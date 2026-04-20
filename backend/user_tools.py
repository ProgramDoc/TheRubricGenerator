"""User-authored tools that layer on top of every Lab chat:

- **Briefing** — one free-form text block per user, prepended to every system
  prompt so the agent always knows the user's working style / domain /
  conventions.
- **User methods** — user-authored structured procedure cards (name,
  when-to-use, instructions, active toggle). Every *active* method is
  appended to the system prompt.

Research-workflow terminology (Sources / Briefing / Methods) is chosen to
avoid clashing with Claude / Anthropic's own Files / Instructions / Skills
features while staying intuitive for new users.

Schema is additive; existing Lab behavior is unaffected until the prompt
composer in `backend/lab.py` starts consuming these tables.
"""
import logging

from fastapi import HTTPException

from .db import IntegrityError

logger = logging.getLogger("rubricgen")

# ─────────────────────────────────────────────
# Limits
# ─────────────────────────────────────────────
BRIEFING_MAX_CHARS = 4_000
METHOD_INSTRUCTIONS_MAX_CHARS = 8_000
METHOD_NAME_MAX_CHARS = 120
METHOD_WHEN_MAX_CHARS = 400

# ─────────────────────────────────────────────
# Schema (wired into init_db())
# ─────────────────────────────────────────────
USER_TOOLS_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS user_briefing (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    text        TEXT    NOT NULL DEFAULT '',
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_methods (
    id           SERIAL  PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT    NOT NULL,
    when_to_use  TEXT    NOT NULL DEFAULT '',
    instructions TEXT    NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_user_methods_user ON user_methods(user_id, active);
"""


# ─────────────────────────────────────────────
# Briefing helpers
# ─────────────────────────────────────────────
def get_briefing(conn, user_id: int) -> dict:
    """Return `{text, updated_at}`. Empty strings for a user who never set one."""
    row = conn.execute(
        "SELECT text, updated_at FROM user_briefing WHERE user_id=?",
        (user_id,),
    ).fetchone()
    if not row:
        return {"text": "", "updated_at": None}
    return {"text": row["text"] or "", "updated_at": row["updated_at"]}


def upsert_briefing(conn, user_id: int, text: str) -> dict:
    """Save the user's briefing. Enforces BRIEFING_MAX_CHARS."""
    text = (text or "").strip()
    if len(text) > BRIEFING_MAX_CHARS:
        raise HTTPException(
            413,
            f"Briefing is too long ({len(text)} chars) — cap is {BRIEFING_MAX_CHARS}. "
            "Trim it down or move specifics into a Method.",
        )
    with conn:
        conn.execute(
            """INSERT INTO user_briefing (user_id, text, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id) DO UPDATE SET
                   text = excluded.text,
                   updated_at = CURRENT_TIMESTAMP""",
            (user_id, text),
        )
        conn.commit()
    return get_briefing(conn, user_id)


# ─────────────────────────────────────────────
# User-methods helpers
# ─────────────────────────────────────────────
def _validate_method_fields(name: str, when_to_use: str, instructions: str) -> tuple[str, str, str]:
    name = (name or "").strip()
    when_to_use = (when_to_use or "").strip()
    instructions = (instructions or "").strip()
    if not name:
        raise HTTPException(400, "Method needs a name.")
    if not instructions:
        raise HTTPException(400, "Method needs instructions.")
    if len(name) > METHOD_NAME_MAX_CHARS:
        raise HTTPException(413, f"Name too long (max {METHOD_NAME_MAX_CHARS} chars).")
    if len(when_to_use) > METHOD_WHEN_MAX_CHARS:
        raise HTTPException(413, f"'When to use' too long (max {METHOD_WHEN_MAX_CHARS} chars).")
    if len(instructions) > METHOD_INSTRUCTIONS_MAX_CHARS:
        raise HTTPException(
            413,
            f"Instructions too long ({len(instructions)} chars) — cap is "
            f"{METHOD_INSTRUCTIONS_MAX_CHARS}. Split into multiple methods if needed.",
        )
    return name, when_to_use, instructions


def list_methods(conn, user_id: int) -> list[dict]:
    """Return every method belonging to this user, newest-created first."""
    rows = conn.execute(
        """SELECT id, name, when_to_use, instructions, active, created_at, updated_at
             FROM user_methods
            WHERE user_id=?
            ORDER BY created_at DESC""",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_active_methods(conn, user_id: int) -> list[dict]:
    """Only the methods the user has toggled on. Used by the prompt composer."""
    rows = conn.execute(
        """SELECT id, name, when_to_use, instructions
             FROM user_methods
            WHERE user_id=? AND active=1
            ORDER BY created_at ASC""",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def create_method(conn, user_id: int, name: str, when_to_use: str, instructions: str) -> dict:
    name, when_to_use, instructions = _validate_method_fields(name, when_to_use, instructions)
    with conn:
        cur = conn.execute(
            """INSERT INTO user_methods (user_id, name, when_to_use, instructions, active)
                   VALUES (?, ?, ?, ?, 1) RETURNING id""",
            (user_id, name, when_to_use, instructions),
        )
        mid = cur.lastrowid
        conn.commit()
    return get_method(conn, user_id, mid)


def get_method(conn, user_id: int, method_id: int) -> dict:
    row = conn.execute(
        """SELECT id, name, when_to_use, instructions, active, created_at, updated_at
             FROM user_methods WHERE id=? AND user_id=?""",
        (method_id, user_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Method not found.")
    return dict(row)


def update_method(conn, user_id: int, method_id: int, *,
                  name: str | None = None,
                  when_to_use: str | None = None,
                  instructions: str | None = None,
                  active: bool | None = None) -> dict:
    """Partial update. Any field left None is left unchanged."""
    # Load current for validation + to fill in unset fields.
    current = get_method(conn, user_id, method_id)
    new_name = current["name"] if name is None else name
    new_when = current["when_to_use"] if when_to_use is None else when_to_use
    new_instr = current["instructions"] if instructions is None else instructions
    new_active = 1 if current["active"] else 0
    if active is not None:
        new_active = 1 if active else 0
    new_name, new_when, new_instr = _validate_method_fields(new_name, new_when, new_instr)
    with conn:
        conn.execute(
            """UPDATE user_methods
                  SET name=?, when_to_use=?, instructions=?, active=?,
                      updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND user_id=?""",
            (new_name, new_when, new_instr, new_active, method_id, user_id),
        )
        conn.commit()
    return get_method(conn, user_id, method_id)


def delete_method(conn, user_id: int, method_id: int) -> None:
    # Existence check so deleting a non-owned method 404s rather than silently no-ops.
    get_method(conn, user_id, method_id)
    with conn:
        conn.execute(
            "DELETE FROM user_methods WHERE id=? AND user_id=?",
            (method_id, user_id),
        )
        conn.commit()


# ─────────────────────────────────────────────
# Prompt composition
# ─────────────────────────────────────────────
def compose_overlay(conn, user_id: int) -> str:
    """Build the briefing + active-methods overlay that gets prepended onto
    every Lab agent's system prompt. Returns an empty string if the user has
    no briefing and no active methods — caller should then skip appending it
    entirely to avoid bloating small prompts."""
    briefing = get_briefing(conn, user_id)["text"]
    methods = get_active_methods(conn, user_id)
    if not briefing and not methods:
        return ""

    parts: list[str] = []
    if briefing:
        parts.append("# User briefing\n" + briefing)
    if methods:
        blocks = []
        for m in methods:
            b = f"## {m['name']}"
            if m["when_to_use"]:
                b += f"\nWhen to use: {m['when_to_use']}"
            b += f"\n{m['instructions']}"
            blocks.append(b)
        parts.append("# Active methods\n" + "\n\n".join(blocks))
    return "\n\n".join(parts)
