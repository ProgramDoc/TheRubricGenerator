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


# ═══════════════════════════════════════════════════════════════════════════
# Stripe layer
# ═══════════════════════════════════════════════════════════════════════════
#
# Model: one Stripe Subscription per organization, with three SubscriptionItems
# (one per seat type). Quantity on each item = the pool size. Stripe is the
# source of truth for quantities; our `enterprise_subscriptions` table is a
# local mirror updated by webhook. API calls here nudge Stripe and return
# optimistic state; durable state lands on the subsequent webhook.

from fastapi import HTTPException  # noqa: E402 — local to stripe section

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
_stripe = None


def _get_stripe():
    """Lazy Stripe import; mirrors the pattern in backend/billing.py."""
    global _stripe
    if _stripe is None:
        if not STRIPE_SECRET_KEY:
            raise HTTPException(500, "Stripe is not configured on this server.")
        try:
            import stripe
            stripe.api_key = STRIPE_SECRET_KEY
            _stripe = stripe
        except ImportError:
            raise HTTPException(500, "Stripe library not installed")
    return _stripe


def _seat_row(conn, code: str) -> dict:
    """Load a seat row from seat_types, raising 503 if the catalog is empty or
    the code is unknown. This is the first check that fails if ENTERPRISE_MODE
    is flipped on without Stripe price IDs being configured."""
    row = conn.execute(
        "SELECT code, stripe_price_id, price_cents, monthly_credits "
        "FROM seat_types WHERE code=? AND active=1",
        (code,),
    ).fetchone()
    if not row:
        raise HTTPException(503, f"Seat type '{code}' is not configured.")
    if not row["stripe_price_id"]:
        raise HTTPException(
            503,
            f"Seat type '{code}' has no Stripe price configured. "
            f"Set {next(s['env_var'] for s in SEAT_TYPES if s['code']==code)} in the environment.",
        )
    return dict(row)


# ─────────────────────────────────────────────
# Validation helpers
# ─────────────────────────────────────────────
def _validate_seat_quantities(admin_qty: int, engineer_qty: int, general_qty: int,
                              *, require_admin: bool = True) -> None:
    """Sanity-check seat counts. Enterprise owners must always hold ≥1 admin
    seat (require_admin=True by default); the caller may relax this only for
    reconciliation paths that accept whatever Stripe reports."""
    for name, qty in [("admin_qty", admin_qty),
                      ("engineer_qty", engineer_qty),
                      ("general_qty", general_qty)]:
        if not isinstance(qty, int) or qty < 0 or qty > 1000:
            raise HTTPException(400, f"{name} must be an integer between 0 and 1000.")
    if require_admin and admin_qty < 1:
        raise HTTPException(400, "At least 1 admin seat is required.")


# ─────────────────────────────────────────────
# Subscription lifecycle
# ─────────────────────────────────────────────
def create_enterprise_checkout(conn, user: dict, *, org_id: int,
                               admin_qty: int, engineer_qty: int,
                               general_qty: int,
                               success_url: str, cancel_url: str) -> str:
    """Create a Stripe Customer + subscription-mode Checkout Session for a
    newly-created org. Returns the Checkout URL the frontend should redirect
    to. The subscription only comes into existence on `checkout.session.
    completed`; we persist the real state from the subsequent
    `customer.subscription.created` webhook.
    """
    stripe = _get_stripe()
    _validate_seat_quantities(admin_qty, engineer_qty, general_qty)

    # Resolve seat prices (raises 503 if any are unconfigured)
    prices = {
        SEAT_TYPE_ADMIN:    _seat_row(conn, SEAT_TYPE_ADMIN)["stripe_price_id"],
        SEAT_TYPE_ENGINEER: _seat_row(conn, SEAT_TYPE_ENGINEER)["stripe_price_id"],
        SEAT_TYPE_GENERAL:  _seat_row(conn, SEAT_TYPE_GENERAL)["stripe_price_id"],
    }

    # One Customer per org. If this org already has a row (e.g. the user bailed
    # from a prior checkout), reuse it rather than stacking duplicate customers.
    existing = conn.execute(
        "SELECT stripe_customer_id FROM enterprise_subscriptions WHERE org_id=?",
        (org_id,),
    ).fetchone()
    if existing and existing["stripe_customer_id"]:
        customer_id = existing["stripe_customer_id"]
    else:
        cust = stripe.Customer.create(
            email=user["email"],
            name=user.get("display_name") or user["email"],
            metadata={"org_id": str(org_id), "user_id": str(user["id"])},
        )
        customer_id = cust.id
        # Insert a placeholder row so repeated checkouts reuse the customer.
        conn.execute(
            """INSERT INTO enterprise_subscriptions
                   (org_id, stripe_customer_id, status,
                    admin_qty, engineer_qty, general_qty)
               VALUES (?, ?, 'incomplete', ?, ?, ?)
               ON CONFLICT(org_id) DO UPDATE SET
                   stripe_customer_id = excluded.stripe_customer_id""",
            (org_id, customer_id, admin_qty, engineer_qty, general_qty),
        )
        conn.commit()

    # Build line_items — only include seats with qty > 0 to keep the Stripe
    # invoice tidy. Zero-qty items can be added via PATCH later.
    line_items = []
    if admin_qty > 0:
        line_items.append({"price": prices[SEAT_TYPE_ADMIN],    "quantity": admin_qty})
    if engineer_qty > 0:
        line_items.append({"price": prices[SEAT_TYPE_ENGINEER], "quantity": engineer_qty})
    if general_qty > 0:
        line_items.append({"price": prices[SEAT_TYPE_GENERAL],  "quantity": general_qty})

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=line_items,
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"org_id": str(org_id), "user_id": str(user["id"]),
                  "kind": "enterprise_subscription"},
        subscription_data={
            "metadata": {"org_id": str(org_id)},
        },
    )
    logger.info("Enterprise: checkout session %s created for org %d", session.id, org_id)
    return session.url


def update_seat_quantities(conn, org_id: int, *,
                           admin_qty: int | None = None,
                           engineer_qty: int | None = None,
                           general_qty: int | None = None) -> dict:
    """Adjust seat-pool quantities via stripe.Subscription.modify.

    Reduction below the currently-assigned count is rejected with 409 — the
    caller must unassign members first. Durable state update comes via
    `customer.subscription.updated` webhook; this call returns optimistic
    quantities for UI responsiveness.
    """
    stripe = _get_stripe()
    sub = conn.execute(
        "SELECT * FROM enterprise_subscriptions WHERE org_id=?", (org_id,)
    ).fetchone()
    if not sub:
        raise HTTPException(404, "Enterprise subscription not found for this org.")
    if sub["status"] not in ("active", "past_due", "incomplete"):
        raise HTTPException(409, f"Subscription is {sub['status']}; cannot modify seats.")
    if not sub["stripe_subscription_id"]:
        raise HTTPException(409, "Subscription is not active yet; complete checkout first.")

    target = {
        SEAT_TYPE_ADMIN:    sub["admin_qty"]    if admin_qty    is None else admin_qty,
        SEAT_TYPE_ENGINEER: sub["engineer_qty"] if engineer_qty is None else engineer_qty,
        SEAT_TYPE_GENERAL:  sub["general_qty"]  if general_qty  is None else general_qty,
    }
    _validate_seat_quantities(target[SEAT_TYPE_ADMIN], target[SEAT_TYPE_ENGINEER],
                              target[SEAT_TYPE_GENERAL])

    # Guard: can't drop any pool below assigned count.
    assigned = {
        SEAT_TYPE_ADMIN:    0,
        SEAT_TYPE_ENGINEER: 0,
        SEAT_TYPE_GENERAL:  0,
    }
    for r in conn.execute(
        "SELECT role, COUNT(*) AS c FROM org_members WHERE org_id=? GROUP BY role",
        (org_id,),
    ).fetchall():
        if r["role"] in assigned:
            assigned[r["role"]] = r["c"]
    for code, qty in target.items():
        if qty < assigned[code]:
            raise HTTPException(
                409,
                {"error": "pool_reduction_blocked", "seat_type": code,
                 "requested_qty": qty, "assigned_count": assigned[code],
                 "message": f"Cannot reduce {code} seats to {qty}; "
                            f"{assigned[code]} are currently assigned. "
                            "Unassign members first."},
            )

    # Build items payload. Include an item only if (a) it has a stored item_id
    # (Stripe already knows it) OR (b) target qty > 0 (we're adding a new one).
    items = []
    for code in (SEAT_TYPE_ADMIN, SEAT_TYPE_ENGINEER, SEAT_TYPE_GENERAL):
        item_id_col = f"{code}_item_id"
        existing_item_id = sub[item_id_col]
        qty = target[code]
        if existing_item_id:
            # Existing item: update qty (0 deletes it via deleted=True).
            item = {"id": existing_item_id, "quantity": qty}
            if qty == 0:
                item = {"id": existing_item_id, "deleted": True}
            items.append(item)
        elif qty > 0:
            # New item: add with price
            seat = _seat_row(conn, code)
            items.append({"price": seat["stripe_price_id"], "quantity": qty})

    stripe.Subscription.modify(
        sub["stripe_subscription_id"],
        items=items,
        proration_behavior="create_prorations",
    )
    logger.info("Enterprise: subscription %s qty update %s for org %d",
                sub["stripe_subscription_id"], target, org_id)

    # Optimistic local update; the subscription.updated webhook is canonical.
    conn.execute(
        """UPDATE enterprise_subscriptions
              SET admin_qty=?, engineer_qty=?, general_qty=?,
                  updated_at=CURRENT_TIMESTAMP
            WHERE org_id=?""",
        (target[SEAT_TYPE_ADMIN], target[SEAT_TYPE_ENGINEER],
         target[SEAT_TYPE_GENERAL], org_id),
    )
    conn.commit()
    return target


def sync_from_stripe(conn, org_id: int) -> dict:
    """Reconcile `enterprise_subscriptions` from Stripe's live state.

    Used as a self-heal endpoint when a webhook is dropped mid-flight. Safe
    to call repeatedly; always overwrites local state with Stripe's view.
    """
    stripe = _get_stripe()
    sub = conn.execute(
        "SELECT stripe_subscription_id FROM enterprise_subscriptions WHERE org_id=?",
        (org_id,),
    ).fetchone()
    if not sub or not sub["stripe_subscription_id"]:
        raise HTTPException(404, "No Stripe subscription recorded for this org.")
    live = stripe.Subscription.retrieve(sub["stripe_subscription_id"],
                                        expand=["items"])
    _apply_subscription_to_db(conn, org_id, live)
    return {"status": live.status, "org_id": org_id}


# ─────────────────────────────────────────────
# Webhook handling
# ─────────────────────────────────────────────
def _apply_subscription_to_db(conn, org_id: int, sub_obj) -> None:
    """Write quantities + status + period dates from a Stripe Subscription
    object into `enterprise_subscriptions`. Handles both webhook-delivered
    events and Subscription.retrieve() return values."""
    qty_by_code = {SEAT_TYPE_ADMIN: 0, SEAT_TYPE_ENGINEER: 0, SEAT_TYPE_GENERAL: 0}
    item_id_by_code = {SEAT_TYPE_ADMIN: None, SEAT_TYPE_ENGINEER: None,
                       SEAT_TYPE_GENERAL: None}
    # `items` may be a ListObject or already a dict depending on source.
    items_data = getattr(sub_obj, "items", None) or sub_obj.get("items", {})
    data = getattr(items_data, "data", None) or items_data.get("data", [])
    price_to_code = {}
    for r in conn.execute(
        "SELECT code, stripe_price_id FROM seat_types WHERE stripe_price_id<>''"
    ).fetchall():
        price_to_code[r["stripe_price_id"]] = r["code"]

    for item in data:
        price_id = item["price"]["id"] if isinstance(item, dict) else item.price.id
        code = price_to_code.get(price_id)
        if not code:
            logger.warning("Enterprise webhook: unknown price %s on sub item", price_id)
            continue
        qty = item["quantity"] if isinstance(item, dict) else item.quantity
        item_id = item["id"] if isinstance(item, dict) else item.id
        qty_by_code[code] = qty
        item_id_by_code[code] = item_id

    status = sub_obj["status"] if isinstance(sub_obj, dict) else sub_obj.status
    sub_id = sub_obj["id"] if isinstance(sub_obj, dict) else sub_obj.id
    period_start = sub_obj.get("current_period_start") if isinstance(sub_obj, dict) else getattr(sub_obj, "current_period_start", None)
    period_end   = sub_obj.get("current_period_end")   if isinstance(sub_obj, dict) else getattr(sub_obj, "current_period_end", None)

    conn.execute(
        """UPDATE enterprise_subscriptions
              SET stripe_subscription_id=?,
                  status=?,
                  admin_qty=?, engineer_qty=?, general_qty=?,
                  admin_item_id=?, engineer_item_id=?, general_item_id=?,
                  current_period_start=?, current_period_end=?,
                  updated_at=CURRENT_TIMESTAMP
            WHERE org_id=?""",
        (sub_id, status,
         qty_by_code[SEAT_TYPE_ADMIN], qty_by_code[SEAT_TYPE_ENGINEER],
         qty_by_code[SEAT_TYPE_GENERAL],
         item_id_by_code[SEAT_TYPE_ADMIN], item_id_by_code[SEAT_TYPE_ENGINEER],
         item_id_by_code[SEAT_TYPE_GENERAL],
         str(period_start) if period_start else None,
         str(period_end)   if period_end   else None,
         org_id),
    )
    conn.commit()


def _grant_monthly_credits(conn, org_id: int) -> int:
    """Credit the org pool with the bundled floor based on current seat
    counts. Called on `invoice.paid`. Returns credits granted."""
    sub = conn.execute(
        "SELECT admin_qty, engineer_qty, general_qty FROM enterprise_subscriptions "
        "WHERE org_id=?", (org_id,),
    ).fetchone()
    if not sub:
        logger.warning("Monthly credit grant: no subscription row for org %d", org_id)
        return 0
    seat_map = {s["code"]: s["monthly_credits"] for s in SEAT_TYPES}
    credits = (sub["admin_qty"]    * seat_map[SEAT_TYPE_ADMIN]
             + sub["engineer_qty"] * seat_map[SEAT_TYPE_ENGINEER]
             + sub["general_qty"]  * seat_map[SEAT_TYPE_GENERAL])
    if credits <= 0:
        return 0

    # Atomic: bump org balance + record the transaction.
    conn.execute(
        """INSERT INTO org_credits (org_id, balance) VALUES (?, ?)
           ON CONFLICT(org_id) DO UPDATE SET
               balance = org_credits.balance + excluded.balance,
               last_updated = CURRENT_TIMESTAMP""",
        (org_id, credits),
    )
    conn.execute(
        """INSERT INTO org_credit_transactions
               (org_id, amount, type, description)
           VALUES (?, ?, 'seat_grant', ?)""",
        (org_id, credits,
         f"Monthly seat credits: {sub['admin_qty']} admin "
         f"+ {sub['engineer_qty']} engineer + {sub['general_qty']} general"),
    )
    conn.commit()
    logger.info("Enterprise: granted %d credits to org %d", credits, org_id)
    return credits


def handle_subscription_event(conn, event: dict) -> bool:
    """Dispatch a Stripe subscription/invoice event to the right updater.

    Returns True if we handled the event, False if it's not ours (caller can
    delegate to other handlers). Called from `billing.py:handle_stripe_webhook`
    after the generic checkout.session.completed branch.
    """
    et = event.get("type", "")
    obj = event.get("data", {}).get("object", {})

    # Resolve org_id from the event's metadata.
    md = obj.get("metadata") or {}
    org_id = md.get("org_id")
    if not org_id and "subscription" in obj:
        # Invoice events carry the sub id; look up the org from there.
        sub_id = obj.get("subscription")
        if sub_id:
            row = conn.execute(
                "SELECT org_id FROM enterprise_subscriptions "
                "WHERE stripe_subscription_id=?",
                (sub_id,),
            ).fetchone()
            if row:
                org_id = row["org_id"]
    if not org_id:
        # Not an enterprise event.
        return False
    try:
        org_id = int(org_id)
    except (TypeError, ValueError):
        return False

    if et in ("customer.subscription.created", "customer.subscription.updated"):
        _apply_subscription_to_db(conn, org_id, obj)
        logger.info("Enterprise: applied %s for org %d", et, org_id)
        return True

    if et == "customer.subscription.deleted":
        conn.execute(
            """UPDATE enterprise_subscriptions
                  SET status='canceled', updated_at=CURRENT_TIMESTAMP
                WHERE org_id=?""",
            (org_id,),
        )
        conn.commit()
        logger.info("Enterprise: marked org %d canceled", org_id)
        return True

    if et == "invoice.paid":
        _grant_monthly_credits(conn, org_id)
        return True

    if et == "invoice.payment_failed":
        conn.execute(
            """UPDATE enterprise_subscriptions
                  SET status='past_due', updated_at=CURRENT_TIMESTAMP
                WHERE org_id=?""",
            (org_id,),
        )
        conn.commit()
        logger.warning("Enterprise: org %d marked past_due", org_id)
        return True

    return False


# ─────────────────────────────────────────────
# Business logic: enterprise CRUD + member seat management
# ─────────────────────────────────────────────
def get_enterprise_state(conn, org_id: int) -> dict:
    """Return the consolidated view: org, subscription, seat-pool usage,
    credit balance. Used by `GET /api/enterprise/{org_id}` and the dashboard.
    """
    org = conn.execute("SELECT * FROM organizations WHERE id=?", (org_id,)).fetchone()
    if not org:
        raise HTTPException(404, "Organization not found.")
    sub = conn.execute(
        "SELECT * FROM enterprise_subscriptions WHERE org_id=?", (org_id,)
    ).fetchone()
    credits = conn.execute(
        "SELECT balance FROM org_credits WHERE org_id=?", (org_id,)
    ).fetchone()

    # Assigned count per seat type
    assigned = {SEAT_TYPE_ADMIN: 0, SEAT_TYPE_ENGINEER: 0, SEAT_TYPE_GENERAL: 0}
    for r in conn.execute(
        "SELECT role, COUNT(*) AS c FROM org_members WHERE org_id=? GROUP BY role",
        (org_id,),
    ).fetchall():
        if r["role"] in assigned:
            assigned[r["role"]] = r["c"]

    purchased = {SEAT_TYPE_ADMIN: 0, SEAT_TYPE_ENGINEER: 0, SEAT_TYPE_GENERAL: 0}
    if sub:
        purchased = {SEAT_TYPE_ADMIN: sub["admin_qty"],
                     SEAT_TYPE_ENGINEER: sub["engineer_qty"],
                     SEAT_TYPE_GENERAL: sub["general_qty"]}

    return {
        "org": {"id": org["id"], "name": org["name"], "slug": org["slug"],
                "description": org["description"], "domain": org["domain"]},
        "subscription": {
            "status": sub["status"] if sub else "none",
            "stripe_subscription_id": sub["stripe_subscription_id"] if sub else None,
            "current_period_start": sub["current_period_start"] if sub else None,
            "current_period_end": sub["current_period_end"] if sub else None,
        } if sub else {"status": "none"},
        "seats": {
            code: {"purchased": purchased[code], "assigned": assigned[code]}
            for code in (SEAT_TYPE_ADMIN, SEAT_TYPE_ENGINEER, SEAT_TYPE_GENERAL)
        },
        "credits": {"balance": credits["balance"] if credits else 0},
    }


def assign_seat(conn, org_id: int, user_id: int, seat_type: str,
                assigned_by: int) -> dict:
    """Invite/assign a user to a specific seat. 409 if the pool is full."""
    if seat_type not in SEAT_RANK:
        raise HTTPException(400, f"Invalid seat_type '{seat_type}'.")
    sub = conn.execute(
        "SELECT admin_qty, engineer_qty, general_qty FROM enterprise_subscriptions "
        "WHERE org_id=?", (org_id,),
    ).fetchone()
    if not sub:
        raise HTTPException(404, "No enterprise subscription for this org.")
    qty_col = f"{seat_type}_qty"
    purchased = sub[qty_col]
    assigned = conn.execute(
        "SELECT COUNT(*) AS c FROM org_members WHERE org_id=? AND role=?",
        (org_id, seat_type),
    ).fetchone()["c"]
    if assigned >= purchased:
        raise HTTPException(
            409,
            {"error": "pool_full", "seat_type": seat_type,
             "purchased": purchased, "assigned": assigned,
             "message": f"No {seat_type} seats available ({assigned}/{purchased})."}
        )
    existing = conn.execute(
        "SELECT role, is_owner FROM org_members WHERE org_id=? AND user_id=?",
        (org_id, user_id),
    ).fetchone()
    if existing:
        if existing["is_owner"]:
            raise HTTPException(403, "Cannot change the enterprise owner's seat.")
        conn.execute(
            "UPDATE org_members SET role=? WHERE org_id=? AND user_id=?",
            (seat_type, org_id, user_id),
        )
    else:
        conn.execute(
            "INSERT INTO org_members (org_id, user_id, role, is_owner) VALUES (?, ?, ?, 0)",
            (org_id, user_id, seat_type),
        )
    conn.commit()
    return {"org_id": org_id, "user_id": user_id, "seat_type": seat_type}


def unassign_seat(conn, org_id: int, user_id: int) -> None:
    """Remove a user from the org. Rejects removal of the owner."""
    row = conn.execute(
        "SELECT is_owner FROM org_members WHERE org_id=? AND user_id=?",
        (org_id, user_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Member not found in this org.")
    if row["is_owner"]:
        raise HTTPException(403,
                            "Cannot remove the enterprise owner. "
                            "Transfer ownership first.")
    conn.execute(
        "DELETE FROM org_members WHERE org_id=? AND user_id=?",
        (org_id, user_id),
    )
    conn.commit()
