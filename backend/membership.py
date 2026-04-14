"""Membership plans — subscription tiers with PDF upload limits and monthly credits.

Plans: Free ($0, 20 PDFs total), Pro ($29/mo, 500 PDFs/mo, 1000 credits/mo),
Enterprise ($99/mo, unlimited PDFs, 5000 credits/mo).
"""

import json
import logging
import os
import sqlite3
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger("rubricgen")

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")

# ─────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────

MEMBERSHIP_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS membership_plans (
    id              SERIAL PRIMARY KEY,
    name            TEXT    NOT NULL UNIQUE,
    price_cents     INTEGER NOT NULL DEFAULT 0,
    interval        TEXT    NOT NULL DEFAULT 'month',
    pdf_limit       INTEGER NOT NULL DEFAULT 20,
    pdf_limit_type  TEXT    NOT NULL DEFAULT 'total' CHECK(pdf_limit_type IN ('total','monthly','unlimited')),
    credits_monthly INTEGER NOT NULL DEFAULT 0,
    max_models      INTEGER NOT NULL DEFAULT 3,
    stripe_price_id TEXT,
    features_json   TEXT,
    active          INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS user_memberships (
    id                   SERIAL PRIMARY KEY,
    user_id              INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_id              INTEGER NOT NULL REFERENCES membership_plans(id),
    status               TEXT    NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled','expired','past_due')),
    stripe_sub_id        TEXT,
    stripe_customer_id   TEXT,
    current_period_start TEXT,
    current_period_end   TEXT,
    pdf_count            INTEGER DEFAULT 0,
    pdf_count_period     TEXT,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id)
);
"""


def seed_plans(conn: sqlite3.Connection) -> None:
    """Seed the three membership plans if not already present."""
    existing = conn.execute("SELECT COUNT(*) AS c FROM membership_plans").fetchone()
    if existing["c"] > 0:
        return

    plans = [
        {
            "name": "Free",
            "price_cents": 0,
            "interval": "none",
            "pdf_limit": 20,
            "pdf_limit_type": "total",
            "credits_monthly": 0,
            "max_models": 3,
            "features_json": json.dumps([
                "Basic literature search",
                "20 PDF uploads (lifetime)",
                "Up to 3 models per challenge",
                "Community library access",
            ]),
        },
        {
            "name": "Pro",
            "price_cents": 2900,
            "interval": "month",
            "pdf_limit": 500,
            "pdf_limit_type": "monthly",
            "credits_monthly": 1000,
            "max_models": 99,
            "features_json": json.dumps([
                "Unlimited search results",
                "500 PDF uploads per month",
                "All models per challenge",
                "1,000 credits/month included",
                "CSV & PDF report export",
                "Priority support",
            ]),
        },
        {
            "name": "Enterprise",
            "price_cents": 9900,
            "interval": "month",
            "pdf_limit": 0,
            "pdf_limit_type": "unlimited",
            "credits_monthly": 5000,
            "max_models": 99,
            "features_json": json.dumps([
                "Everything in Pro",
                "Unlimited PDF uploads",
                "5,000 credits/month included",
                "Organization billing",
                "Competition API access",
                "Custom model registration",
                "Dedicated support",
            ]),
        },
    ]

    with conn:
        for p in plans:
            conn.execute(
                """INSERT INTO membership_plans
                   (name, price_cents, interval, pdf_limit, pdf_limit_type,
                    credits_monthly, max_models, features_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (p["name"], p["price_cents"], p["interval"], p["pdf_limit"],
                 p["pdf_limit_type"], p["credits_monthly"], p["max_models"],
                 p["features_json"]),
            )
        conn.commit()
    logger.info("Seeded 3 membership plans")


# ─────────────────────────────────────────────
# Membership queries
# ─────────────────────────────────────────────

def get_user_membership(conn: sqlite3.Connection, user_id: int) -> dict:
    """Get user's current membership with plan details and usage."""
    row = conn.execute(
        """SELECT um.*, mp.name AS plan_name, mp.price_cents, mp.interval,
                  mp.pdf_limit, mp.pdf_limit_type, mp.credits_monthly,
                  mp.max_models, mp.features_json
           FROM user_memberships um
           JOIN membership_plans mp ON mp.id = um.plan_id
           WHERE um.user_id = ?""",
        (user_id,),
    ).fetchone()

    if row:
        result = dict(row)
        try:
            result["features"] = json.loads(result.pop("features_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            result["features"] = []
        return result

    # No membership yet — auto-assign Free plan
    free_plan = conn.execute(
        "SELECT * FROM membership_plans WHERE name = 'Free'"
    ).fetchone()
    if free_plan:
        _assign_free_plan(conn, user_id, free_plan["id"])
        return get_user_membership(conn, user_id)

    # Fallback if plans not seeded yet
    return {
        "user_id": user_id,
        "plan_name": "Free",
        "pdf_limit": 20,
        "pdf_limit_type": "total",
        "pdf_count": 0,
        "credits_monthly": 0,
        "max_models": 3,
        "status": "active",
        "features": [],
    }


def _assign_free_plan(conn: sqlite3.Connection, user_id: int,
                      plan_id: int) -> None:
    """Auto-assign the Free plan to a new user."""
    with conn:
        conn.execute(
            """INSERT INTO user_memberships
               (user_id, plan_id, status, pdf_count)
               VALUES (?, ?, 'active', 0) ON CONFLICT DO NOTHING""",
            (user_id, plan_id),
        )
        conn.commit()


def check_pdf_limit(conn: sqlite3.Connection, user_id: int) -> dict:
    """Check if user can upload more PDFs. Returns {allowed, used, limit, plan}."""
    membership = get_user_membership(conn, user_id)
    limit_type = membership.get("pdf_limit_type", "total")
    limit = membership.get("pdf_limit", 20)
    plan_name = membership.get("plan_name", "Free")

    if limit_type == "unlimited":
        # Count for display but always allow
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM papers WHERE user_id = ?", (user_id,)
        ).fetchone()["c"]
        return {"allowed": True, "used": total, "limit": -1, "plan": plan_name}

    if limit_type == "total":
        # Lifetime total
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM papers WHERE user_id = ?", (user_id,)
        ).fetchone()["c"]
        return {"allowed": total < limit, "used": total, "limit": limit, "plan": plan_name}

    if limit_type == "monthly":
        # Count papers uploaded in current billing period
        period_start = membership.get("current_period_start")
        if period_start:
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM papers WHERE user_id = ? AND created_at >= ?",
                (user_id, period_start),
            ).fetchone()["c"]
        else:
            # No period set — count this calendar month
            count = conn.execute(
                """SELECT COUNT(*) AS c FROM papers WHERE user_id = ?
                   AND created_at >= date_trunc('month', CURRENT_TIMESTAMP)""",
                (user_id,),
            ).fetchone()["c"]
        return {"allowed": count < limit, "used": count, "limit": limit, "plan": plan_name}

    return {"allowed": True, "used": 0, "limit": limit, "plan": plan_name}


def increment_pdf_count(conn: sqlite3.Connection, user_id: int) -> None:
    """Increment the user's PDF count for their membership period."""
    with conn:
        conn.execute(
            """UPDATE user_memberships SET pdf_count = pdf_count + 1
               WHERE user_id = ?""",
            (user_id,),
        )
        conn.commit()


# ─────────────────────────────────────────────
# Membership management
# ─────────────────────────────────────────────

def list_plans(conn: sqlite3.Connection) -> list[dict]:
    """List all active membership plans."""
    rows = conn.execute(
        "SELECT * FROM membership_plans WHERE active = 1 ORDER BY price_cents"
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["features"] = json.loads(d.pop("features_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            d["features"] = []
        result.append(d)
    return result


def create_subscription(conn: sqlite3.Connection, user_id: int,
                        plan_id: int, success_url: str,
                        cancel_url: str) -> dict:
    """Create a Stripe checkout session for a subscription plan."""
    if not STRIPE_SECRET_KEY:
        raise HTTPException(500, "Stripe not configured")

    plan = conn.execute(
        "SELECT * FROM membership_plans WHERE id = ? AND active = 1",
        (plan_id,),
    ).fetchone()
    if not plan:
        raise HTTPException(404, "Plan not found")
    if plan["price_cents"] == 0:
        raise HTTPException(400, "Free plan doesn't require a subscription")

    try:
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY
    except ImportError:
        raise HTTPException(500, "Stripe library not installed")

    # Get or create Stripe customer
    user = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
    existing = conn.execute(
        "SELECT stripe_customer_id FROM user_memberships WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    if existing and existing["stripe_customer_id"]:
        customer_id = existing["stripe_customer_id"]
    else:
        customer = stripe.Customer.create(email=user["email"])
        customer_id = customer.id

    # Create checkout session in subscription mode
    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "usd",
                "product_data": {"name": f"AI Researcher {plan['name']} Membership"},
                "unit_amount": plan["price_cents"],
                "recurring": {"interval": plan["interval"]},
            },
            "quantity": 1,
        }],
        mode="subscription",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={
            "user_id": str(user_id),
            "plan_id": str(plan_id),
            "plan_name": plan["name"],
        },
    )

    # Store customer ID
    with conn:
        conn.execute(
            """INSERT INTO user_memberships (user_id, plan_id, status, stripe_customer_id)
               VALUES (?, ?, 'pending', ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 plan_id = excluded.plan_id,
                 stripe_customer_id = excluded.stripe_customer_id""",
            (user_id, plan_id, customer_id),
        )
        conn.commit()

    return {"checkout_url": session.url}


def cancel_subscription(conn: sqlite3.Connection, user_id: int) -> dict:
    """Cancel current subscription (reverts to Free at period end)."""
    membership = conn.execute(
        "SELECT stripe_sub_id FROM user_memberships WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not membership or not membership["stripe_sub_id"]:
        raise HTTPException(400, "No active subscription to cancel")

    try:
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY
        stripe.Subscription.modify(
            membership["stripe_sub_id"],
            cancel_at_period_end=True,
        )
    except Exception as e:
        raise HTTPException(500, f"Failed to cancel subscription: {e}")

    with conn:
        conn.execute(
            "UPDATE user_memberships SET status = 'cancelled' WHERE user_id = ?",
            (user_id,),
        )
        conn.commit()

    return {"ok": True, "message": "Subscription will cancel at end of billing period"}


def handle_subscription_webhook(event: dict, get_db_fn) -> None:
    """Handle Stripe subscription lifecycle events."""
    event_type = event.get("type", "")
    obj = event.get("data", {}).get("object", {})

    if event_type == "customer.subscription.created":
        _activate_subscription(obj, get_db_fn)
    elif event_type == "customer.subscription.updated":
        _update_subscription(obj, get_db_fn)
    elif event_type == "customer.subscription.deleted":
        _deactivate_subscription(obj, get_db_fn)
    elif event_type == "invoice.paid":
        _grant_monthly_credits(obj, get_db_fn)


def _activate_subscription(sub: dict, get_db_fn) -> None:
    """Activate subscription when Stripe confirms it."""
    meta = sub.get("metadata", {})
    user_id = int(meta.get("user_id", 0))
    plan_id = int(meta.get("plan_id", 0))
    if not user_id:
        # Try to find by customer ID
        customer_id = sub.get("customer", "")
        conn = get_db_fn()
        try:
            row = conn.execute(
                "SELECT user_id, plan_id FROM user_memberships WHERE stripe_customer_id = ?",
                (customer_id,),
            ).fetchone()
            if row:
                user_id = row["user_id"]
                plan_id = plan_id or row["plan_id"]
        finally:
            conn.close()

    if not user_id:
        logger.warning("Subscription webhook: no user_id found")
        return

    conn = get_db_fn()
    try:
        period_start = sub.get("current_period_start", "")
        period_end = sub.get("current_period_end", "")
        # Convert Unix timestamps to ISO
        from datetime import datetime, timezone
        if isinstance(period_start, int):
            period_start = datetime.fromtimestamp(period_start, tz=timezone.utc).isoformat()
        if isinstance(period_end, int):
            period_end = datetime.fromtimestamp(period_end, tz=timezone.utc).isoformat()

        with conn:
            conn.execute(
                """UPDATE user_memberships
                   SET status = 'active', stripe_sub_id = ?,
                       current_period_start = ?, current_period_end = ?,
                       pdf_count = 0, pdf_count_period = ?
                   WHERE user_id = ?""",
                (sub.get("id"), period_start, period_end, period_start, user_id),
            )
            conn.commit()
        logger.info("Activated subscription for user %d (plan %d)", user_id, plan_id)
    finally:
        conn.close()


def _update_subscription(sub: dict, get_db_fn) -> None:
    """Update subscription status/period."""
    _activate_subscription(sub, get_db_fn)  # Same logic applies


def _deactivate_subscription(sub: dict, get_db_fn) -> None:
    """Revert to Free plan when subscription ends."""
    customer_id = sub.get("customer", "")
    conn = get_db_fn()
    try:
        row = conn.execute(
            "SELECT user_id FROM user_memberships WHERE stripe_customer_id = ?",
            (customer_id,),
        ).fetchone()
        if not row:
            return
        free_plan = conn.execute(
            "SELECT id FROM membership_plans WHERE name = 'Free'"
        ).fetchone()
        if free_plan:
            with conn:
                conn.execute(
                    """UPDATE user_memberships
                       SET plan_id = ?, status = 'active', stripe_sub_id = NULL,
                           current_period_start = NULL, current_period_end = NULL
                       WHERE user_id = ?""",
                    (free_plan["id"], row["user_id"]),
                )
                conn.commit()
            logger.info("Reverted user %d to Free plan", row["user_id"])
    finally:
        conn.close()


def _grant_monthly_credits(invoice: dict, get_db_fn) -> None:
    """Grant monthly credits when invoice is paid."""
    sub_id = invoice.get("subscription", "")
    if not sub_id:
        return
    conn = get_db_fn()
    try:
        row = conn.execute(
            """SELECT um.user_id, mp.credits_monthly
               FROM user_memberships um
               JOIN membership_plans mp ON mp.id = um.plan_id
               WHERE um.stripe_sub_id = ?""",
            (sub_id,),
        ).fetchone()
        if row and row["credits_monthly"] > 0:
            from .billing import credit_from_purchase
            credit_from_purchase(
                conn, row["user_id"], row["credits_monthly"],
                f"membership_invoice_{invoice.get('id', '')}",
            )
            logger.info("Granted %d monthly credits to user %d",
                        row["credits_monthly"], row["user_id"])
    finally:
        conn.close()
