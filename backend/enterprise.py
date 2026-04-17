"""Enterprise-only seat-based billing.

Replaces the legacy individual Free/Pro/Enterprise plans (see `membership.py`,
being deprecated) with a per-seat model inside organizations:

    Admin seat     — $450/mo + 500 monthly credit floor, full org control
    Engineer seat  — $250/mo + 300 monthly credit floor, build/run
    General seat   — $100/mo + 100 monthly credit floor, view/annotate

Usage credits overage is purchased as one-time packs (reuses `billing.py`
credit_packs + Stripe Checkout), credited into the org pool.

This module owns:
- `ENTERPRISE_TABLES_SQL`: schema for seat_types + enterprise_subscriptions
- `SEAT_TYPES`: the seat catalog constants (code, price, credits, rank)
- `SEAT_RANK`: permission hierarchy for `require_active_seat` (added later)
- `seed_seat_types(conn)`: upsert seat catalog from env vars on boot
- `ENTERPRISE_MODE`: env-flag gating the rollout; when False, gating/migration
  are inert and the app behaves exactly as before.

Stripe integration, access gating, and API handlers land in follow-up commits.
"""

import logging
import os

from .db import IntegrityError

logger = logging.getLogger("rubricgen")

# ─────────────────────────────────────────────
# Feature flag
# ─────────────────────────────────────────────
# When ENTERPRISE_MODE=0 (default) the new tables exist but nothing reads from
# them, so the app behaves identically to pre-enterprise. Flip to "1" on
# staging/production only when the full pipeline (phases 1–5) is ready.
ENTERPRISE_MODE = os.environ.get("ENTERPRISE_MODE", "0") == "1"

# ─────────────────────────────────────────────
# Seat catalog
# ─────────────────────────────────────────────
# Single source of truth: seat_types table is seeded from these constants at
# boot. Env vars supply the Stripe price IDs so staging/prod can point at
# different Stripe products without a code change.
SEAT_TYPE_ADMIN    = "admin"
SEAT_TYPE_ENGINEER = "engineer"
SEAT_TYPE_GENERAL  = "general"

SEAT_TYPES: list[dict] = [
    {
        "code": SEAT_TYPE_ADMIN,
        "display_name": "Admin",
        "price_cents": 45000,        # $450/mo
        "monthly_credits": 500,
        "env_var": "STRIPE_PRICE_SEAT_ADMIN",
        "rank": 3,
    },
    {
        "code": SEAT_TYPE_ENGINEER,
        "display_name": "Engineer",
        "price_cents": 25000,        # $250/mo
        "monthly_credits": 300,
        "env_var": "STRIPE_PRICE_SEAT_ENGINEER",
        "rank": 2,
    },
    {
        "code": SEAT_TYPE_GENERAL,
        "display_name": "General User",
        "price_cents": 10000,        # $100/mo
        "monthly_credits": 100,
        "env_var": "STRIPE_PRICE_SEAT_GENERAL",
        "rank": 1,
    },
]

# Permission hierarchy for `require_active_seat`. Wired up in a later commit.
SEAT_RANK: dict[str, int] = {s["code"]: s["rank"] for s in SEAT_TYPES}

# ─────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────
# Executed from `main.py:init_db()` via `conn.executescript(ENTERPRISE_TABLES_SQL)`.
# Postgres DDL; the SqliteConnection wrapper in backend/db.py converts at runtime.
ENTERPRISE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS seat_types (
    code             TEXT PRIMARY KEY,
    display_name     TEXT    NOT NULL,
    price_cents      INTEGER NOT NULL,
    monthly_credits  INTEGER NOT NULL,
    stripe_price_id  TEXT    NOT NULL,
    rank             INTEGER NOT NULL,
    active           INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS enterprise_subscriptions (
    org_id                 INTEGER PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
    stripe_customer_id     TEXT NOT NULL,
    stripe_subscription_id TEXT UNIQUE,
    status                 TEXT NOT NULL DEFAULT 'incomplete'
        CHECK(status IN ('incomplete','active','past_due','canceled','unpaid')),
    admin_qty              INTEGER NOT NULL DEFAULT 0,
    engineer_qty           INTEGER NOT NULL DEFAULT 0,
    general_qty            INTEGER NOT NULL DEFAULT 0,
    admin_item_id          TEXT,
    engineer_item_id       TEXT,
    general_item_id        TEXT,
    current_period_start   TEXT,
    current_period_end     TEXT,
    created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_es_status ON enterprise_subscriptions(status);
CREATE INDEX IF NOT EXISTS idx_es_customer ON enterprise_subscriptions(stripe_customer_id);
"""


# ─────────────────────────────────────────────
# Seeding
# ─────────────────────────────────────────────
def seed_seat_types(conn) -> None:
    """Upsert each seat from SEAT_TYPES into the seat_types table.

    Missing Stripe price env vars are logged but non-fatal — the app still
    boots, and attempts to create enterprises will fail with a clear error
    until the env is configured. This mirrors `billing.py:seed_credit_packs`.
    """
    try:
        for seat in SEAT_TYPES:
            price_id = os.environ.get(seat["env_var"], "").strip()
            if not price_id:
                logger.warning(
                    "Enterprise: %s is unset; seat '%s' will have empty stripe_price_id. "
                    "Set it before enabling ENTERPRISE_MODE=1.",
                    seat["env_var"], seat["code"],
                )
            # ON CONFLICT DO UPDATE keeps catalog in sync with code/env changes
            # without wiping on redeploys.
            conn.execute(
                """INSERT INTO seat_types
                       (code, display_name, price_cents, monthly_credits,
                        stripe_price_id, rank, active)
                   VALUES (?, ?, ?, ?, ?, ?, 1)
                   ON CONFLICT(code) DO UPDATE SET
                       display_name    = excluded.display_name,
                       price_cents     = excluded.price_cents,
                       monthly_credits = excluded.monthly_credits,
                       stripe_price_id = excluded.stripe_price_id,
                       rank            = excluded.rank""",
                (seat["code"], seat["display_name"], seat["price_cents"],
                 seat["monthly_credits"], price_id, seat["rank"]),
            )
        conn.commit()
    except IntegrityError as e:
        logger.error("Enterprise: failed to seed seat_types: %s", e)
        # Non-fatal: boot continues. If table is empty, enterprise endpoints
        # will return 503 when they're added in the Stripe-integration commit.


def get_seat(code: str) -> dict | None:
    """Return the static seat definition for a given code, or None."""
    for s in SEAT_TYPES:
        if s["code"] == code:
            return s
    return None
