"""Registered model CRUD + team member management for Phase 1.5.

A registered model represents a model under test (e.g. "MyLab-GPT4o-Finetuned v2").
The name is unique across the platform. Team members are registered users linked
by email."""

import secrets
import sqlite3
from fastapi import HTTPException

from .db import IntegrityError


def create_registered_model(conn: sqlite3.Connection, creator_user_id: int,
                            name: str, version: str, provider: str = "",
                            git_repo: str = "", organization: str = "",
                            team_member_emails: list[str] | None = None,
                            team_members_with_perms: list[dict] | None = None,
                            org_id: int | None = None) -> dict:
    """
    Validates:
    - name unique (409)
    - version non-empty (400)
    - all team_member_emails exist as registered users (400 with list of unknown)

    Auto-adds the creator to members.
    """
    name = (name or "").strip()
    version = (version or "").strip()
    if not name:
        raise HTTPException(400, "Model name is required")
    if not version:
        raise HTTPException(400, "Version is required")
    if len(name) > 100:
        raise HTTPException(400, "Model name must be 100 characters or fewer")
    if len(version) > 50:
        raise HTTPException(400, "Version must be 50 characters or fewer")

    # Resolve team member emails to user IDs (excluding creator)
    team_ids: set[int] = set()
    if team_member_emails:
        unknown: list[str] = []
        for raw in team_member_emails:
            email = (raw or "").strip().lower()
            if not email:
                continue
            row = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
            if not row:
                unknown.append(email)
            else:
                team_ids.add(row["id"])
        if unknown:
            raise HTTPException(
                400,
                "Unknown team member emails (must be registered users): " + ", ".join(unknown),
            )

    team_ids.add(creator_user_id)

    # Build can_run permissions map from team_members_with_perms if provided
    can_run_map: dict[int, int] = {creator_user_id: 1}  # creator always has can_run
    if team_members_with_perms:
        for tm in team_members_with_perms:
            em = (tm.get("email") or "").strip().lower()
            if not em:
                continue
            row = conn.execute("SELECT id FROM users WHERE email=?", (em,)).fetchone()
            if row:
                can_run_map[row["id"]] = 1 if tm.get("can_run") else 0

    # Generate a unique API key for this model (used for competition API auth)
    model_api_key = f"rg_model_{secrets.token_urlsafe(32)}"

    try:
        with conn:
            cur = conn.execute(
                """INSERT INTO registered_models (name, version, provider, git_repo, organization, created_by, model_api_key, org_id)
                   VALUES (?,?,?,?,?,?,?,?) RETURNING id""",
                (name, version, provider or None, git_repo or None, organization or None, creator_user_id, model_api_key, org_id),
            )
            model_id = cur.lastrowid
            for uid in team_ids:
                conn.execute(
                    "INSERT INTO registered_model_members (registered_model_id, user_id, can_run) VALUES (?,?,?) ON CONFLICT DO NOTHING",
                    (model_id, uid, can_run_map.get(uid, 0)),
                )
            conn.commit()
    except IntegrityError:
        raise HTTPException(409, f"Model name '{name}' is already taken")

    return get_registered_model(conn, model_id)


def get_registered_model(conn: sqlite3.Connection, model_id: int) -> dict:
    row = conn.execute(
        """SELECT rm.*, u.display_name AS creator_name, u.email AS creator_email
           FROM registered_models rm
           LEFT JOIN users u ON u.id = rm.created_by
           WHERE rm.id=?""",
        (model_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Model not found")
    model = dict(row)
    members = conn.execute(
        """SELECT u.id, u.email, u.display_name, rmm.can_run
           FROM registered_model_members rmm
           JOIN users u ON u.id = rmm.user_id
           WHERE rmm.registered_model_id=?
           ORDER BY u.display_name""",
        (model_id,),
    ).fetchall()
    model["members"] = [dict(m) for m in members]
    return model


def list_registered_models(conn: sqlite3.Connection, user_id: int | None = None) -> list[dict]:
    """List all models, optionally filtering to ones where user_id is a member."""
    if user_id is not None:
        rows = conn.execute(
            """SELECT rm.*, u.display_name AS creator_name
               FROM registered_models rm
               LEFT JOIN users u ON u.id = rm.created_by
               WHERE rm.id IN (SELECT registered_model_id FROM registered_model_members WHERE user_id=?)
               ORDER BY rm.created_at DESC""",
            (user_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT rm.*, u.display_name AS creator_name
               FROM registered_models rm
               LEFT JOIN users u ON u.id = rm.created_by
               ORDER BY rm.created_at DESC"""
        ).fetchall()
    result = []
    for r in rows:
        m = dict(r)
        members = conn.execute(
            """SELECT u.id, u.email, u.display_name
               FROM registered_model_members rmm
               JOIN users u ON u.id = rmm.user_id
               WHERE rmm.registered_model_id=?""",
            (m["id"],),
        ).fetchall()
        m["members"] = [dict(x) for x in members]
        result.append(m)
    return result


def delete_registered_model(conn: sqlite3.Connection, model_id: int, user_id: int) -> None:
    row = conn.execute(
        "SELECT created_by FROM registered_models WHERE id=?", (model_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Model not found")
    if row["created_by"] != user_id:
        raise HTTPException(403, "Only the creator can delete a registered model")
    with conn:
        conn.execute("DELETE FROM registered_models WHERE id=?", (model_id,))
        conn.commit()


def add_member(conn: sqlite3.Connection, model_id: int, requester_user_id: int,
               email: str) -> dict:
    row = conn.execute("SELECT created_by FROM registered_models WHERE id=?", (model_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Model not found")
    if row["created_by"] != requester_user_id:
        raise HTTPException(403, "Only the creator can add members")
    email = (email or "").strip().lower()
    user_row = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if not user_row:
        raise HTTPException(400, f"No registered user with email {email}")
    with conn:
        conn.execute(
            "INSERT INTO registered_model_members (registered_model_id, user_id) VALUES (?,?) ON CONFLICT DO NOTHING",
            (model_id, user_row["id"]),
        )
        conn.commit()
    return get_registered_model(conn, model_id)


def remove_member(conn: sqlite3.Connection, model_id: int, requester_user_id: int,
                  member_user_id: int) -> dict:
    row = conn.execute("SELECT created_by FROM registered_models WHERE id=?", (model_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Model not found")
    if row["created_by"] != requester_user_id:
        raise HTTPException(403, "Only the creator can remove members")
    # Can't remove the creator (themselves)
    if member_user_id == row["created_by"]:
        raise HTTPException(400, "Cannot remove the creator from their own model")
    with conn:
        conn.execute(
            "DELETE FROM registered_model_members WHERE registered_model_id=? AND user_id=?",
            (model_id, member_user_id),
        )
        conn.commit()
    return get_registered_model(conn, model_id)


def get_model_by_api_key(conn: sqlite3.Connection, api_key: str) -> dict | None:
    """Look up a registered model by its API key (for competition API auth)."""
    row = conn.execute(
        """SELECT rm.*, u.display_name AS creator_name, u.email AS creator_email
           FROM registered_models rm
           LEFT JOIN users u ON u.id = rm.created_by
           WHERE rm.model_api_key=?""",
        (api_key,),
    ).fetchone()
    return dict(row) if row else None


def regenerate_api_key(conn: sqlite3.Connection, model_id: int,
                       requester_user_id: int) -> str:
    """Regenerate the API key for a model. Creator only."""
    row = conn.execute("SELECT created_by FROM registered_models WHERE id=?", (model_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Model not found")
    if row["created_by"] != requester_user_id:
        raise HTTPException(403, "Only the creator can regenerate the API key")
    new_key = f"rg_model_{secrets.token_urlsafe(32)}"
    with conn:
        conn.execute("UPDATE registered_models SET model_api_key=? WHERE id=?", (new_key, model_id))
        conn.commit()
    return new_key


def user_can_use_model(conn: sqlite3.Connection, model_id: int, user_id: int) -> bool:
    """Check if a user is a member of a registered model (required to publish tests with it)."""
    row = conn.execute(
        "SELECT 1 FROM registered_model_members WHERE registered_model_id=? AND user_id=?",
        (model_id, user_id),
    ).fetchone()
    return row is not None


def user_can_run_model(conn: sqlite3.Connection, model_id: int, user_id: int) -> bool:
    """Check if a user has can_run permission on a model (required to use it in challenges).
    The creator always has can_run."""
    # Creator always allowed
    creator = conn.execute(
        "SELECT created_by FROM registered_models WHERE id=?", (model_id,)
    ).fetchone()
    if creator and creator["created_by"] == user_id:
        return True
    row = conn.execute(
        "SELECT can_run FROM registered_model_members WHERE registered_model_id=? AND user_id=?",
        (model_id, user_id),
    ).fetchone()
    return row is not None and bool(row["can_run"])


def update_member_permission(conn: sqlite3.Connection, model_id: int,
                             requester_user_id: int, member_user_id: int,
                             can_run: bool) -> dict:
    """Update a member's can_run permission. Creator only."""
    row = conn.execute(
        "SELECT created_by FROM registered_models WHERE id=?", (model_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Model not found")
    if row["created_by"] != requester_user_id:
        raise HTTPException(403, "Only the creator can change member permissions")
    with conn:
        conn.execute(
            "UPDATE registered_model_members SET can_run=? WHERE registered_model_id=? AND user_id=?",
            (1 if can_run else 0, model_id, member_user_id),
        )
        conn.commit()
    return get_registered_model(conn, model_id)


def update_model(conn: sqlite3.Connection, model_id: int, user_id: int,
                  name: str | None = None, version: str | None = None,
                  provider: str | None = None, git_repo: str | None = None,
                  organization: str | None = None, changelog: str | None = None) -> dict:
    """Update model metadata. Creator only. If version changes, log to model_versions."""
    row = conn.execute(
        "SELECT * FROM registered_models WHERE id=?", (model_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Model not found")
    if row["created_by"] != user_id:
        raise HTTPException(403, "Only the creator can update a registered model")

    updates = []
    params = []

    if name is not None:
        name = name.strip()
        if not name:
            raise HTTPException(400, "Model name cannot be empty")
        if len(name) > 100:
            raise HTTPException(400, "Model name must be 100 characters or fewer")
        if name != row["name"]:
            # Check uniqueness
            dup = conn.execute(
                "SELECT id FROM registered_models WHERE LOWER(name)=LOWER(?) AND id!=?",
                (name, model_id),
            ).fetchone()
            if dup:
                raise HTTPException(409, f"Model name '{name}' is already taken")
            updates.append("name=?")
            params.append(name)

    version_changed = False
    if version is not None:
        version = version.strip()
        if not version:
            raise HTTPException(400, "Version cannot be empty")
        if len(version) > 50:
            raise HTTPException(400, "Version must be 50 characters or fewer")
        if version != row["version"]:
            updates.append("version=?")
            params.append(version)
            version_changed = True

    if provider is not None:
        updates.append("provider=?")
        params.append(provider or None)
    if git_repo is not None:
        updates.append("git_repo=?")
        params.append(git_repo or None)
    if organization is not None:
        updates.append("organization=?")
        params.append(organization or None)

    if not updates:
        return get_registered_model(conn, model_id)

    updates.append("updated_at=CURRENT_TIMESTAMP")
    params.append(model_id)

    with conn:
        conn.execute(
            f"UPDATE registered_models SET {', '.join(updates)} WHERE id=?",
            tuple(params),
        )
        # Log version change to history
        if version_changed:
            conn.execute(
                "INSERT INTO model_versions (registered_model_id, version, changelog, created_by) VALUES (?,?,?,?)",
                (model_id, version, changelog or None, user_id),
            )
        conn.commit()

    return get_registered_model(conn, model_id)


def get_version_history(conn: sqlite3.Connection, model_id: int) -> list[dict]:
    """Get version history for a model, most recent first."""
    row = conn.execute("SELECT id FROM registered_models WHERE id=?", (model_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Model not found")
    rows = conn.execute(
        """SELECT mv.*, u.display_name AS author_name, u.email AS author_email
           FROM model_versions mv
           LEFT JOIN users u ON u.id = mv.created_by
           WHERE mv.registered_model_id=?
           ORDER BY mv.created_at DESC""",
        (model_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_org_models(conn: sqlite3.Connection, org_id: int) -> list[dict]:
    """List all models belonging to an organization."""
    rows = conn.execute(
        """SELECT rm.*, u.display_name AS creator_name
           FROM registered_models rm
           LEFT JOIN users u ON u.id = rm.created_by
           WHERE rm.org_id = ?
           ORDER BY rm.created_at DESC""",
        (org_id,),
    ).fetchall()
    return [dict(r) for r in rows]
