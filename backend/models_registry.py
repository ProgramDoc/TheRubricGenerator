"""Registered model CRUD + team member management for Phase 1.5.

A registered model represents a model under test (e.g. "MyLab-GPT4o-Finetuned v2").
The name is unique across the platform. Team members are registered users linked
by email."""

import sqlite3
from fastapi import HTTPException


def create_registered_model(conn: sqlite3.Connection, creator_user_id: int,
                            name: str, version: str, provider: str = "",
                            git_repo: str = "", organization: str = "",
                            team_member_emails: list[str] | None = None) -> dict:
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

    try:
        with conn:
            cur = conn.execute(
                """INSERT INTO registered_models (name, version, provider, git_repo, organization, created_by)
                   VALUES (?,?,?,?,?,?)""",
                (name, version, provider or None, git_repo or None, organization or None, creator_user_id),
            )
            model_id = cur.lastrowid
            for uid in team_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO registered_model_members (registered_model_id, user_id) VALUES (?,?)",
                    (model_id, uid),
                )
            conn.commit()
    except sqlite3.IntegrityError:
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
        """SELECT u.id, u.email, u.display_name
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
            "INSERT OR IGNORE INTO registered_model_members (registered_model_id, user_id) VALUES (?,?)",
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


def user_can_use_model(conn: sqlite3.Connection, model_id: int, user_id: int) -> bool:
    """Check if a user is a member of a registered model (required to publish tests with it)."""
    row = conn.execute(
        "SELECT 1 FROM registered_model_members WHERE registered_model_id=? AND user_id=?",
        (model_id, user_id),
    ).fetchone()
    return row is not None
