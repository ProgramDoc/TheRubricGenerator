"""Prepaid credit billing via Stripe. Users buy credit packs, tests debit credits.

Tables: credit_packs, user_credits, credit_transactions
External: Stripe Checkout Sessions for purchases, webhooks for payment confirmation.
"""

import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

logger = logging.getLogger("rubricgen")

STRIPE_SECRET_KEY     = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

# Lazy Stripe import — only load if STRIPE_SECRET_KEY is set
_stripe = None


def _get_stripe():
    global _stripe
    if _stripe is None:
        try:
            import stripe
            stripe.api_key = STRIPE_SECRET_KEY
            _stripe = stripe
        except ImportError:
            raise HTTPException(500, "Stripe library not installed")
    return _stripe


# ─────────────────────────────────────────────
# Schema (called from init_db)
# ─────────────────────────────────────────────
BILLING_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS credit_packs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    credits         INTEGER NOT NULL,
    price_cents     INTEGER NOT NULL,
    stripe_price_id TEXT,
    active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS user_credits (
    user_id      INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    balance      INTEGER NOT NULL DEFAULT 0,
    last_updated TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS credit_transactions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount            INTEGER NOT NULL,
    type              TEXT    NOT NULL CHECK(type IN ('purchase','test_charge','promo','refund','daily_fee')),
    description       TEXT,
    challenge_id      INTEGER,
    stripe_session_id TEXT,
    promo_code_id     INTEGER,
    created_at        TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_txn_user ON credit_transactions(user_id);
"""


def seed_credit_packs(conn: sqlite3.Connection) -> None:
    existing = conn.execute("SELECT COUNT(*) AS c FROM credit_packs").fetchone()
    if existing["c"] > 0:
        return
    packs = [
        ("Starter",    100, 1000),   # $10 = 100 credits
        ("Researcher", 300, 2500),   # $25 = 300 credits
        ("Lab",        750, 5000),   # $50 = 750 credits
    ]
    with conn:
        for name, credits, price_cents in packs:
            conn.execute(
                "INSERT INTO credit_packs (name, credits, price_cents) VALUES (?,?,?)",
                (name, credits, price_cents),
            )
        conn.commit()


# ─────────────────────────────────────────────
# Balance operations
# ─────────────────────────────────────────────
def get_balance(conn: sqlite3.Connection, user_id: int) -> int:
    row = conn.execute("SELECT balance FROM user_credits WHERE user_id=?", (user_id,)).fetchone()
    return row["balance"] if row else 0


def _ensure_user_credits(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO user_credits (user_id, balance) VALUES (?,0)",
        (user_id,),
    )


def debit_credits(conn: sqlite3.Connection, user_id: int, amount: int,
                  description: str, challenge_id: int | None = None) -> bool:
    """Debit credits from user. Returns False if insufficient balance."""
    _ensure_user_credits(conn, user_id)
    balance = get_balance(conn, user_id)
    if balance < amount:
        return False
    with conn:
        conn.execute(
            "UPDATE user_credits SET balance = balance - ?, last_updated = datetime('now') WHERE user_id=?",
            (amount, user_id),
        )
        conn.execute(
            """INSERT INTO credit_transactions (user_id, amount, type, description, challenge_id)
               VALUES (?,?,?,?,?)""",
            (user_id, -amount, "test_charge", description, challenge_id),
        )
        conn.commit()
    return True


def credit_from_purchase(conn: sqlite3.Connection, user_id: int, amount: int,
                         stripe_session_id: str) -> None:
    _ensure_user_credits(conn, user_id)
    with conn:
        conn.execute(
            "UPDATE user_credits SET balance = balance + ?, last_updated = datetime('now') WHERE user_id=?",
            (amount, user_id),
        )
        conn.execute(
            """INSERT INTO credit_transactions (user_id, amount, type, description, stripe_session_id)
               VALUES (?,?,?,?,?)""",
            (user_id, amount, "purchase", f"Purchased {amount} credits", stripe_session_id),
        )
        conn.commit()


def credit_from_promo(conn: sqlite3.Connection, user_id: int, amount: int,
                      promo_code_id: int) -> None:
    _ensure_user_credits(conn, user_id)
    with conn:
        conn.execute(
            "UPDATE user_credits SET balance = balance + ?, last_updated = datetime('now') WHERE user_id=?",
            (amount, user_id),
        )
        conn.execute(
            """INSERT INTO credit_transactions (user_id, amount, type, description, promo_code_id)
               VALUES (?,?,?,?,?)""",
            (user_id, amount, "promo", f"Promo credit: {amount}", promo_code_id),
        )
        conn.commit()


def list_transactions(conn: sqlite3.Connection, user_id: int, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        """SELECT id, amount, type, description, challenge_id, created_at
           FROM credit_transactions WHERE user_id=?
           ORDER BY created_at DESC LIMIT ?""",
        (user_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def list_packs(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM credit_packs WHERE active=1 ORDER BY credits"
    ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────
# Cost calculation
# ─────────────────────────────────────────────
def calculate_test_cost(model_ids: list[str], conn: sqlite3.Connection | None = None) -> int:
    """Calculate total credits needed for a test with the given models.
    Frontier models use SUPPORTED_MODELS costs; custom models use registered price."""
    from .challenges import SUPPORTED_MODELS
    total = 0
    for mid in model_ids:
        spec = SUPPORTED_MODELS.get(mid)
        if spec:
            total += spec.get("cost_credits", 10)
        elif conn and mid.startswith("custom:"):
            # custom:<registered_model_id>
            try:
                rm_id = int(mid.split(":", 1)[1])
                row = conn.execute(
                    "SELECT price_per_test_credits FROM registered_models WHERE id=?", (rm_id,)
                ).fetchone()
                if row:
                    total += row["price_per_test_credits"] or 0
            except (ValueError, IndexError):
                pass
        else:
            total += 10  # fallback
    return total


# ─────────────────────────────────────────────
# Stripe checkout
# ─────────────────────────────────────────────
def create_checkout_session(conn: sqlite3.Connection, user_id: int,
                            pack_id: int, success_url: str,
                            cancel_url: str) -> str:
    """Create a Stripe Checkout Session for purchasing a credit pack.
    Returns the checkout URL to redirect to."""
    if not STRIPE_SECRET_KEY:
        raise HTTPException(500, "Stripe is not configured")
    stripe = _get_stripe()
    pack = conn.execute("SELECT * FROM credit_packs WHERE id=? AND active=1", (pack_id,)).fetchone()
    if not pack:
        raise HTTPException(404, "Credit pack not found")

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "usd",
                "product_data": {"name": f"OGAI Credits — {pack['name']} ({pack['credits']} credits)"},
                "unit_amount": pack["price_cents"],
            },
            "quantity": 1,
        }],
        mode="payment",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={
            "user_id": str(user_id),
            "pack_id": str(pack_id),
            "credits": str(pack["credits"]),
        },
    )
    return session.url


def handle_stripe_webhook(payload: bytes, sig_header: str,
                          get_db_fn) -> dict:
    """Process incoming Stripe webhook. Credits the user on successful payment."""
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(500, "Stripe webhook secret not configured")
    stripe = _get_stripe()
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        raise HTTPException(400, f"Webhook verification failed: {e}")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        meta = session.get("metadata", {})
        user_id = int(meta.get("user_id", 0))
        credits = int(meta.get("credits", 0))
        session_id = session.get("id", "")

        if user_id and credits:
            conn = get_db_fn()
            try:
                credit_from_purchase(conn, user_id, credits, session_id)
                logger.info("Stripe: credited %d to user %d (session=%s)", credits, user_id, session_id)
            finally:
                conn.close()
    return {"received": True}
