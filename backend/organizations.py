"""Phase 7 — Multi-Tenant Teams & Organizations.

Organization CRUD, membership management, role-based access control,
invite-link joining, and email-domain auto-join.
"""

import logging
import re
import secrets
import sqlite3
from typing import Any

from fastapi import HTTPException

from .db import IntegrityError, column_exists, is_postgres

logger = logging.getLogger("rubricgen")

# ─────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────

ORG_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS organizations (
    id           SERIAL PRIMARY KEY,
    name         TEXT    NOT NULL UNIQUE,
    slug         TEXT    NOT NULL UNIQUE,
    description  TEXT,
    domain       TEXT,
    invite_code  TEXT    UNIQUE,
    created_by   INTEGER NOT NULL REFERENCES users(id),
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_org_slug ON organizations(slug);
CREATE INDEX IF NOT EXISTS idx_org_domain ON organizations(domain);

CREATE TABLE IF NOT EXISTS org_members (
    org_id    INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role      TEXT    NOT NULL DEFAULT 'general' CHECK(role IN ('general','engineer','admin')),
    is_owner  INTEGER NOT NULL DEFAULT 0,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (org_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_om_user ON org_members(user_id);

CREATE TABLE IF NOT EXISTS org_credits (
    org_id       INTEGER PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
    balance      INTEGER NOT NULL DEFAULT 0,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS org_credit_transactions (
    id                SERIAL PRIMARY KEY,
    org_id            INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id           INTEGER,
    amount            INTEGER NOT NULL,
    type              TEXT    NOT NULL CHECK(type IN ('purchase','test_charge','promo','refund','transfer_in')),
    description       TEXT,
    challenge_id      INTEGER,
    stripe_session_id TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_oct_org ON org_credit_transactions(org_id);

CREATE TABLE IF NOT EXISTS org_leaderboard_cache (
    org_id           INTEGER PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
    total_models     INTEGER DEFAULT 0,
    total_challenges INTEGER DEFAULT 0,
    total_points     INTEGER DEFAULT 0,
    daily_points     INTEGER DEFAULT 0,
    avg_accuracy     REAL    DEFAULT 0,
    best_model_id    TEXT,
    last_updated     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ─────────────────────────────────────────────
# Role hierarchy
# ─────────────────────────────────────────────

ROLE_RANK = {"general": 1, "engineer": 2, "admin": 3}
VALID_ROLES = set(ROLE_RANK.keys())

# Legacy → seat vocabulary. Used during migration and for any stale DB row
# that slipped through (e.g. a fresh import from a backup). Keep until the
# Phase-5 cutover is deployed everywhere.
LEGACY_ROLE_MAP = {"viewer": "general", "contributor": "engineer", "admin": "admin"}


def migrate_to_seat_vocab(conn) -> None:
    """Phase 1b migration: move org_members from the legacy viewer/contributor/
    admin vocabulary to the seat-based general/engineer/admin vocabulary, and
    add the ``is_owner`` column (backfilled from organizations.created_by).

    Idempotent: guarded on the presence of the ``is_owner`` column, so re-running
    on an already-migrated DB is a no-op.

    Portability: handles the CHECK-constraint swap differently on PG vs SQLite.
      - PostgreSQL: DROP+ADD the named CHECK constraint.
      - SQLite: rebuild the table (SQLite doesn't allow altering CHECK in-place).

    Callers: invoked from ``main.py:init_db()`` right after ``ORG_TABLES_SQL``
    executes. Safe to run on boot; the guard skips the heavy work after the
    first successful run.
    """
    # Idempotency guard — if is_owner already exists we've already migrated.
    if column_exists(conn, "org_members", "is_owner"):
        return

    logger.info("Enterprise 1b migration: normalizing org_members role vocabulary…")

    try:
        # Ordering matters: the UPDATE to new values must run under the NEW
        # CHECK, so on each backend we swap the constraint first, then update.
        if is_postgres():
            # Postgres: add column in place, swap CHECK constraint, then update
            # values. `IF EXISTS` makes the DROP safe across environments where
            # the constraint name may differ by history.
            conn.execute(
                "ALTER TABLE org_members ADD COLUMN is_owner INTEGER NOT NULL DEFAULT 0"
            )
            conn.execute(
                "ALTER TABLE org_members "
                "DROP CONSTRAINT IF EXISTS org_members_role_check"
            )
            conn.execute(
                "ALTER TABLE org_members "
                "ADD CONSTRAINT org_members_role_check "
                "CHECK (role IN ('viewer','contributor','admin','general','engineer'))"
            )
            # Translate legacy values — allowed because the interim CHECK above
            # accepts both vocabularies.
            conn.execute("UPDATE org_members SET role='general'  WHERE role='viewer'")
            conn.execute("UPDATE org_members SET role='engineer' WHERE role='contributor'")
            # Tighten the CHECK to the new vocabulary only.
            conn.execute(
                "ALTER TABLE org_members DROP CONSTRAINT org_members_role_check"
            )
            conn.execute(
                "ALTER TABLE org_members "
                "ADD CONSTRAINT org_members_role_check "
                "CHECK (role IN ('general','engineer','admin'))"
            )
            # Backfill is_owner from organizations.created_by.
            conn.execute(
                """UPDATE org_members SET is_owner=1
                     WHERE (org_id, user_id) IN
                           (SELECT id, created_by FROM organizations)"""
            )
        else:
            # SQLite: CHECK constraints can't be altered in place — rebuild the
            # table with the new CHECK and translate the role values inside the
            # INSERT SELECT so they satisfy the new constraint on write. Row
            # count is tiny (one per (org,user) pair), copy is cheap.
            conn.executescript(
                """
                CREATE TABLE org_members_new (
                    org_id    INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    role      TEXT    NOT NULL DEFAULT 'general'
                              CHECK(role IN ('general','engineer','admin')),
                    is_owner  INTEGER NOT NULL DEFAULT 0,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (org_id, user_id)
                );
                INSERT INTO org_members_new (org_id, user_id, role, is_owner, joined_at)
                  SELECT om.org_id,
                         om.user_id,
                         CASE om.role
                             WHEN 'viewer'      THEN 'general'
                             WHEN 'contributor' THEN 'engineer'
                             ELSE om.role                       -- 'admin' stays 'admin'
                         END,
                         CASE WHEN o.created_by = om.user_id THEN 1 ELSE 0 END,
                         om.joined_at
                    FROM org_members om
                    LEFT JOIN organizations o ON o.id = om.org_id;
                DROP TABLE org_members;
                ALTER TABLE org_members_new RENAME TO org_members;
                CREATE INDEX IF NOT EXISTS idx_om_user ON org_members(user_id);
                """
            )

        conn.commit()
        logger.info("Enterprise 1b migration: complete.")
    except Exception as e:
        logger.error("Enterprise 1b migration failed: %s", e)
        raise


def require_org_role(conn: sqlite3.Connection, org_id: int, user_id: int,
                     min_role: str) -> dict:
    """Check user has at least `min_role` in org. Returns the org_members row.
    Raises 403 if insufficient, 404 if org doesn't exist."""
    org = conn.execute("SELECT id FROM organizations WHERE id=?", (org_id,)).fetchone()
    if not org:
        raise HTTPException(404, "Organization not found")
    row = conn.execute(
        "SELECT * FROM org_members WHERE org_id=? AND user_id=?",
        (org_id, user_id),
    ).fetchone()
    if not row:
        raise HTTPException(403, "You are not a member of this organization")
    if ROLE_RANK.get(row["role"], -1) < ROLE_RANK.get(min_role, 99):
        raise HTTPException(403, f"Requires {min_role} role or higher")
    return dict(row)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _slugify(name: str) -> str:
    """Convert org name to URL-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-")
    return slug[:64] or "org"


def _generate_invite_code() -> str:
    return secrets.token_urlsafe(16)


# ─────────────────────────────────────────────
# Organization CRUD
# ─────────────────────────────────────────────

def create_organization(conn: sqlite3.Connection, creator_user_id: int,
                        name: str, description: str = "",
                        domain: str = "") -> dict:
    """Create an organization. The creator is auto-added as admin."""
    name = name.strip()
    if not name:
        raise HTTPException(400, "Organization name is required")
    if len(name) > 100:
        raise HTTPException(400, "Organization name must be 100 characters or less")

    slug = _slugify(name)
    invite_code = _generate_invite_code()
    domain = domain.strip().lower() if domain else None

    try:
        with conn:
            cur = conn.execute(
                """INSERT INTO organizations (name, slug, description, domain, invite_code, created_by)
                   VALUES (?, ?, ?, ?, ?, ?) RETURNING id""",
                (name, slug, description, domain, invite_code, creator_user_id),
            )
            org_id = cur.lastrowid
            # Auto-add creator as admin + enterprise owner (is_owner=1).
            # Exactly one owner per org; migrations / seat-swap logic rely on
            # this invariant.
            conn.execute(
                "INSERT INTO org_members (org_id, user_id, role, is_owner) "
                "VALUES (?, ?, 'admin', 1)",
                (org_id, creator_user_id),
            )
            # Initialize org credits
            conn.execute(
                "INSERT INTO org_credits (org_id, balance) VALUES (?, 0)",
                (org_id,),
            )
            conn.commit()
    except IntegrityError:
        raise HTTPException(409, "An organization with that name already exists")

    return get_organization(conn, org_id)


def get_organization(conn: sqlite3.Connection, org_id: int) -> dict:
    """Get org details with member list."""
    org = conn.execute("SELECT * FROM organizations WHERE id=?", (org_id,)).fetchone()
    if not org:
        raise HTTPException(404, "Organization not found")
    result = dict(org)

    members = conn.execute(
        """SELECT om.user_id, om.role, om.joined_at,
                  u.email, u.display_name
           FROM org_members om
           JOIN users u ON u.id = om.user_id
           WHERE om.org_id = ?
           ORDER BY om.role DESC, om.joined_at ASC""",
        (org_id,),
    ).fetchall()
    result["members"] = [dict(m) for m in members]

    balance_row = conn.execute(
        "SELECT balance FROM org_credits WHERE org_id=?", (org_id,)
    ).fetchone()
    result["credit_balance"] = balance_row["balance"] if balance_row else 0

    return result


def list_user_organizations(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """All orgs where user is a member."""
    rows = conn.execute(
        """SELECT o.id, o.name, o.slug, o.description, o.domain, o.created_at,
                  om.role, om.joined_at,
                  (SELECT COUNT(*) FROM org_members WHERE org_id = o.id) AS member_count,
                  (SELECT balance FROM org_credits WHERE org_id = o.id) AS credit_balance
           FROM organizations o
           JOIN org_members om ON om.org_id = o.id
           WHERE om.user_id = ?
           ORDER BY o.name""",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def update_organization(conn: sqlite3.Connection, org_id: int,
                        requester_user_id: int,
                        name: str | None = None,
                        description: str | None = None,
                        domain: str | None = None) -> dict:
    """Update org settings. Requires admin role."""
    require_org_role(conn, org_id, requester_user_id, "admin")

    updates: list[str] = []
    params: list[Any] = []
    if name is not None:
        name = name.strip()
        if not name:
            raise HTTPException(400, "Name cannot be empty")
        updates.append("name = ?")
        params.append(name)
        updates.append("slug = ?")
        params.append(_slugify(name))
    if description is not None:
        updates.append("description = ?")
        params.append(description)
    if domain is not None:
        updates.append("domain = ?")
        params.append(domain.strip().lower() or None)

    if not updates:
        raise HTTPException(400, "Nothing to update")

    params.append(org_id)
    try:
        with conn:
            conn.execute(
                f"UPDATE organizations SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            conn.commit()
    except IntegrityError:
        raise HTTPException(409, "An organization with that name already exists")

    return get_organization(conn, org_id)


def delete_organization(conn: sqlite3.Connection, org_id: int,
                        requester_user_id: int) -> None:
    """Delete org. Requires admin role."""
    require_org_role(conn, org_id, requester_user_id, "admin")
    with conn:
        conn.execute("DELETE FROM organizations WHERE id = ?", (org_id,))
        conn.commit()
    logger.info("Organization %d deleted by user %d", org_id, requester_user_id)


# ─────────────────────────────────────────────
# Membership management
# ─────────────────────────────────────────────

def add_member(conn: sqlite3.Connection, org_id: int, requester_user_id: int,
               email: str, role: str = "general") -> dict:
    """Add a member by email. Requires admin role."""
    require_org_role(conn, org_id, requester_user_id, "admin")

    if role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role. Must be one of: {', '.join(sorted(VALID_ROLES))}")

    email = email.strip().lower()
    user = conn.execute("SELECT id, email, display_name FROM users WHERE email=?", (email,)).fetchone()
    if not user:
        raise HTTPException(404, f"No user found with email: {email}")

    existing = conn.execute(
        "SELECT 1 FROM org_members WHERE org_id=? AND user_id=?",
        (org_id, user["id"]),
    ).fetchone()
    if existing:
        raise HTTPException(409, f"{email} is already a member of this organization")

    with conn:
        conn.execute(
            "INSERT INTO org_members (org_id, user_id, role) VALUES (?, ?, ?)",
            (org_id, user["id"], role),
        )
        conn.commit()

    logger.info("Added user %s to org %d as %s", email, org_id, role)
    return {"user_id": user["id"], "email": user["email"],
            "display_name": user["display_name"], "role": role}


def remove_member(conn: sqlite3.Connection, org_id: int, requester_user_id: int,
                  member_user_id: int) -> None:
    """Remove a member. Admin can remove anyone; members can remove themselves (leave)."""
    if requester_user_id != member_user_id:
        require_org_role(conn, org_id, requester_user_id, "admin")

    # Prevent removing the last admin
    member = conn.execute(
        "SELECT role FROM org_members WHERE org_id=? AND user_id=?",
        (org_id, member_user_id),
    ).fetchone()
    if not member:
        raise HTTPException(404, "Member not found in this organization")

    if member["role"] == "admin":
        admin_count = conn.execute(
            "SELECT COUNT(*) AS c FROM org_members WHERE org_id=? AND role='admin'",
            (org_id,),
        ).fetchone()["c"]
        if admin_count <= 1:
            raise HTTPException(400, "Cannot remove the last admin. Transfer admin role first or delete the organization.")

    with conn:
        conn.execute(
            "DELETE FROM org_members WHERE org_id=? AND user_id=?",
            (org_id, member_user_id),
        )
        conn.commit()

    logger.info("Removed user %d from org %d", member_user_id, org_id)


def update_member_role(conn: sqlite3.Connection, org_id: int,
                       requester_user_id: int, member_user_id: int,
                       new_role: str) -> None:
    """Change a member's role. Requires admin role."""
    require_org_role(conn, org_id, requester_user_id, "admin")

    if new_role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role. Must be one of: {', '.join(sorted(VALID_ROLES))}")

    member = conn.execute(
        "SELECT role FROM org_members WHERE org_id=? AND user_id=?",
        (org_id, member_user_id),
    ).fetchone()
    if not member:
        raise HTTPException(404, "Member not found in this organization")

    # Prevent demoting the last admin
    if member["role"] == "admin" and new_role != "admin":
        admin_count = conn.execute(
            "SELECT COUNT(*) AS c FROM org_members WHERE org_id=? AND role='admin'",
            (org_id,),
        ).fetchone()["c"]
        if admin_count <= 1:
            raise HTTPException(400, "Cannot demote the last admin")

    with conn:
        conn.execute(
            "UPDATE org_members SET role=? WHERE org_id=? AND user_id=?",
            (new_role, org_id, member_user_id),
        )
        conn.commit()

    logger.info("Updated user %d role to %s in org %d", member_user_id, new_role, org_id)


# ─────────────────────────────────────────────
# Invite & domain join
# ─────────────────────────────────────────────

def join_by_invite(conn: sqlite3.Connection, user_id: int,
                   invite_code: str) -> dict:
    """Join an organization via invite code. Added as viewer."""
    org = conn.execute(
        "SELECT id, name FROM organizations WHERE invite_code=?",
        (invite_code.strip(),),
    ).fetchone()
    if not org:
        raise HTTPException(404, "Invalid invite code")

    existing = conn.execute(
        "SELECT 1 FROM org_members WHERE org_id=? AND user_id=?",
        (org["id"], user_id),
    ).fetchone()
    if existing:
        raise HTTPException(409, "You are already a member of this organization")

    with conn:
        conn.execute(
            "INSERT INTO org_members (org_id, user_id, role) VALUES (?, ?, 'general')",
            (org["id"], user_id),
        )
        conn.commit()

    logger.info("User %d joined org %d via invite code", user_id, org["id"])
    return {"org_id": org["id"], "org_name": org["name"], "role": "general"}


def join_by_domain(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """Auto-join orgs whose domain matches the user's email domain.
    Called during registration. Returns list of orgs joined."""
    user = conn.execute("SELECT email FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        return []

    email_domain = user["email"].rsplit("@", 1)[-1].lower() if "@" in user["email"] else ""
    if not email_domain:
        return []

    orgs = conn.execute(
        "SELECT id, name FROM organizations WHERE domain=?",
        (email_domain,),
    ).fetchall()

    joined = []
    for org in orgs:
        existing = conn.execute(
            "SELECT 1 FROM org_members WHERE org_id=? AND user_id=?",
            (org["id"], user_id),
        ).fetchone()
        if not existing:
            with conn:
                conn.execute(
                    "INSERT INTO org_members (org_id, user_id, role) VALUES (?, ?, 'general')",
                    (org["id"], user_id),
                )
                conn.commit()
            joined.append({"org_id": org["id"], "org_name": org["name"], "role": "general"})
            logger.info("User %d auto-joined org %d via domain %s", user_id, org["id"], email_domain)

    return joined


def regenerate_invite_code(conn: sqlite3.Connection, org_id: int,
                           requester_user_id: int) -> str:
    """Generate a new invite code, invalidating the old one. Requires admin."""
    require_org_role(conn, org_id, requester_user_id, "admin")
    new_code = _generate_invite_code()
    with conn:
        conn.execute(
            "UPDATE organizations SET invite_code=? WHERE id=?",
            (new_code, org_id),
        )
        conn.commit()
    return new_code
