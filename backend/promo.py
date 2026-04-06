"""Promo code management. Admin creates codes for tech partners.

Two types:
- 'free': user pays nothing for tests while promo is active
- 'breakeven': user pays only the platform break-even cost (no margin)

Auto-approved for 48 hours after activation. After that, admin must approve
for continued use.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException


PROMO_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS promo_codes (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    code                 TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    type                 TEXT    NOT NULL CHECK(type IN ('free','breakeven')),
    discount_pct         INTEGER NOT NULL DEFAULT 100,
    created_by           INTEGER REFERENCES users(id),
    max_uses             INTEGER DEFAULT 0,
    times_used           INTEGER DEFAULT 0,
    valid_from           TEXT    DEFAULT (datetime('now')),
    valid_until          TEXT,
    auto_approve_hours   INTEGER NOT NULL DEFAULT 48,
    active               INTEGER NOT NULL DEFAULT 1,
    created_at           TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_promo_activations (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id               INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    promo_code_id         INTEGER NOT NULL REFERENCES promo_codes(id) ON DELETE CASCADE,
    activated_at          TEXT    DEFAULT (datetime('now')),
    auto_approved_until   TEXT    NOT NULL,
    admin_approved        INTEGER NOT NULL DEFAULT 0,
    admin_approved_at     TEXT,
    active                INTEGER NOT NULL DEFAULT 1,
    UNIQUE(user_id, promo_code_id)
);
"""


def create_promo_code(conn: sqlite3.Connection, code: str, promo_type: str,
                      created_by: int, max_uses: int = 0,
                      discount_pct: int = 100,
                      valid_until: str | None = None,
                      auto_approve_hours: int = 48) -> dict:
    code = code.strip().upper()
    if not code:
        raise HTTPException(400, "Code is required")
    if promo_type not in ("free", "breakeven"):
        raise HTTPException(400, "Type must be 'free' or 'breakeven'")
    try:
        with conn:
            cur = conn.execute(
                """INSERT INTO promo_codes
                   (code, type, discount_pct, created_by, max_uses, valid_until, auto_approve_hours)
                   VALUES (?,?,?,?,?,?,?)""",
                (code, promo_type, discount_pct, created_by, max_uses, valid_until, auto_approve_hours),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Promo code '{code}' already exists")
    return get_promo_code(conn, cur.lastrowid)


def get_promo_code(conn: sqlite3.Connection, promo_id: int) -> dict:
    row = conn.execute("SELECT * FROM promo_codes WHERE id=?", (promo_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Promo code not found")
    return dict(row)


def list_promo_codes(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM promo_codes ORDER BY created_at DESC").fetchall()
    result = []
    for r in rows:
        d = dict(r)
        activations = conn.execute(
            """SELECT upa.*, u.display_name, u.email
               FROM user_promo_activations upa
               JOIN users u ON u.id = upa.user_id
               WHERE upa.promo_code_id=?""",
            (d["id"],),
        ).fetchall()
        d["activations"] = [dict(a) for a in activations]
        result.append(d)
    return result


def apply_promo_code(conn: sqlite3.Connection, user_id: int, code: str) -> dict:
    """User activates a promo code. Auto-approved for the configured hours."""
    code = code.strip().upper()
    promo = conn.execute("SELECT * FROM promo_codes WHERE code=? AND active=1", (code,)).fetchone()
    if not promo:
        raise HTTPException(404, "Invalid or inactive promo code")
    promo = dict(promo)

    now = datetime.now(timezone.utc)
    # Check validity window
    if promo.get("valid_until"):
        until = datetime.fromisoformat(promo["valid_until"]).replace(tzinfo=timezone.utc)
        if now > until:
            raise HTTPException(400, "This promo code has expired")
    # Check max uses
    if promo["max_uses"] > 0 and promo["times_used"] >= promo["max_uses"]:
        raise HTTPException(400, "This promo code has reached its maximum uses")
    # Check if already activated
    existing = conn.execute(
        "SELECT id FROM user_promo_activations WHERE user_id=? AND promo_code_id=?",
        (user_id, promo["id"]),
    ).fetchone()
    if existing:
        raise HTTPException(409, "You have already activated this promo code")

    auto_until = (now + timedelta(hours=promo.get("auto_approve_hours", 48))).isoformat()

    with conn:
        conn.execute(
            """INSERT INTO user_promo_activations
               (user_id, promo_code_id, auto_approved_until)
               VALUES (?,?,?)""",
            (user_id, promo["id"], auto_until),
        )
        conn.execute(
            "UPDATE promo_codes SET times_used = times_used + 1 WHERE id=?",
            (promo["id"],),
        )
        conn.commit()
    return {"ok": True, "promo_type": promo["type"], "auto_approved_until": auto_until}


def get_user_promo_status(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """Return user's active promo activations with current validity."""
    rows = conn.execute(
        """SELECT upa.*, pc.code, pc.type, pc.discount_pct
           FROM user_promo_activations upa
           JOIN promo_codes pc ON pc.id = upa.promo_code_id
           WHERE upa.user_id=? AND upa.active=1""",
        (user_id,),
    ).fetchall()
    now = datetime.now(timezone.utc).isoformat()
    result = []
    for r in rows:
        d = dict(r)
        auto_until = d.get("auto_approved_until", "")
        d["currently_valid"] = (auto_until > now) or bool(d.get("admin_approved"))
        d["needs_admin_approval"] = (auto_until <= now) and not bool(d.get("admin_approved"))
        result.append(d)
    return result


def user_has_active_promo(conn: sqlite3.Connection, user_id: int) -> dict | None:
    """Check if user has any currently active promo. Returns the best one (free > breakeven) or None."""
    promos = get_user_promo_status(conn, user_id)
    valid = [p for p in promos if p.get("currently_valid")]
    if not valid:
        return None
    # Prefer 'free' over 'breakeven'
    free = [p for p in valid if p.get("type") == "free"]
    if free:
        return free[0]
    return valid[0]


def admin_approve_user_promo(conn: sqlite3.Connection, activation_id: int) -> dict:
    """Admin approves a user's promo activation for indefinite use."""
    row = conn.execute("SELECT * FROM user_promo_activations WHERE id=?", (activation_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Activation not found")
    with conn:
        conn.execute(
            "UPDATE user_promo_activations SET admin_approved=1, admin_approved_at=datetime('now') WHERE id=?",
            (activation_id,),
        )
        conn.commit()
    return {"ok": True}
