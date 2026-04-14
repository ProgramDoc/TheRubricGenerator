"""AI Researcher Lab — unified multi-agent conversation module.

Provides session-based conversations for multiple AI agent types:
- search_strategist: literature search (delegates to search.py)
- statistician: statistical analysis planning and critique
- study_appraiser: study quality appraisal
- hypothesis_generator: novel hypothesis generation
- literature_reviewer: literature review synthesis

Each agent type uses versioned system prompts from agent_skills table
and saves conversations as markdown in the Obsidian vault.
"""

import json
import logging
import sqlite3
from typing import Any

from fastapi import HTTPException

from .helpers import call_anthropic, strip_markdown_fences
from .skills import get_active_skill

logger = logging.getLogger("rubricgen")

# Valid agent types for the lab
AGENT_TYPES = (
    "search_strategist",
    "statistician",
    "study_appraiser",
    "hypothesis_generator",
    "literature_reviewer",
)

# ─────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────

LAB_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS lab_sessions (
    id            SERIAL PRIMARY KEY,
    title         TEXT    NOT NULL DEFAULT 'New Conversation',
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id    INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    agent_type    TEXT    NOT NULL DEFAULT 'search_strategist',
    pico_json     TEXT,
    metadata_json TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ls_user ON lab_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_ls_agent ON lab_sessions(user_id, agent_type);

CREATE TABLE IF NOT EXISTS lab_messages (
    id            SERIAL PRIMARY KEY,
    session_id    INTEGER NOT NULL REFERENCES lab_sessions(id) ON DELETE CASCADE,
    role          TEXT    NOT NULL CHECK(role IN ('user','assistant','system')),
    content       TEXT    NOT NULL,
    metadata_json TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_lm_session ON lab_messages(session_id);

CREATE TABLE IF NOT EXISTS lab_attachments (
    id          SERIAL PRIMARY KEY,
    message_id  INTEGER NOT NULL REFERENCES lab_messages(id) ON DELETE CASCADE,
    filename    TEXT    NOT NULL,
    file_type   TEXT    NOT NULL DEFAULT 'pdf',
    file_path   TEXT,
    file_hash   TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_la_message ON lab_attachments(message_id);

CREATE TABLE IF NOT EXISTS lab_exports (
    id          SERIAL PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES lab_sessions(id) ON DELETE CASCADE,
    export_type TEXT    NOT NULL,
    filename    TEXT    NOT NULL,
    file_path   TEXT    NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_le_session ON lab_exports(session_id);
"""


# ─────────────────────────────────────────────
# Session CRUD
# ─────────────────────────────────────────────

def create_session(conn, user_id: int, agent_type: str = "search_strategist",
                   title: str = "New Conversation") -> dict:
    if agent_type not in AGENT_TYPES:
        raise HTTPException(400, f"Invalid agent_type: {agent_type}")
    with conn:
        cur = conn.execute(
            "INSERT INTO lab_sessions (title, user_id, agent_type) VALUES (?, ?, ?) RETURNING id",
            (title, user_id, agent_type),
        )
        conn.commit()
    row = conn.execute("SELECT * FROM lab_sessions WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def list_sessions(conn, user_id: int, agent_type: str | None = None) -> list[dict]:
    if agent_type:
        rows = conn.execute(
            """SELECT s.*, COUNT(m.id) AS message_count
               FROM lab_sessions s LEFT JOIN lab_messages m ON m.session_id = s.id
               WHERE s.user_id = ? AND s.agent_type = ?
               GROUP BY s.id ORDER BY s.updated_at DESC""",
            (user_id, agent_type),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT s.*, COUNT(m.id) AS message_count
               FROM lab_sessions s LEFT JOIN lab_messages m ON m.session_id = s.id
               WHERE s.user_id = ?
               GROUP BY s.id ORDER BY s.updated_at DESC""",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_session(conn, session_id: int, user_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM lab_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Session not found")
    session = dict(row)
    session["messages"] = get_messages(conn, session_id)
    try:
        session["pico"] = json.loads(session.get("pico_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        session["pico"] = {}
    return session


def delete_session(conn, session_id: int, user_id: int) -> None:
    row = conn.execute(
        "SELECT id FROM lab_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Session not found")
    with conn:
        conn.execute("DELETE FROM lab_sessions WHERE id = ?", (session_id,))
        conn.commit()


def update_session(conn, session_id: int, user_id: int,
                   title: str | None = None,
                   project_id: int | None = None,
                   remove_from_project: bool = False) -> dict:
    row = conn.execute(
        "SELECT * FROM lab_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Session not found")
    with conn:
        if title is not None:
            conn.execute("UPDATE lab_sessions SET title = ? WHERE id = ?", (title, session_id))
        if remove_from_project:
            conn.execute("UPDATE lab_sessions SET project_id = NULL WHERE id = ?", (session_id,))
        elif project_id is not None:
            conn.execute("UPDATE lab_sessions SET project_id = ? WHERE id = ?", (project_id, session_id))
        conn.commit()
    return dict(conn.execute("SELECT * FROM lab_sessions WHERE id = ?", (session_id,)).fetchone())


# ─────────────────────────────────────────────
# Messages
# ─────────────────────────────────────────────

def add_message(conn, session_id: int, role: str,
                content: str, metadata: dict | None = None) -> dict:
    meta_json = json.dumps(metadata) if metadata else None
    with conn:
        cur = conn.execute(
            "INSERT INTO lab_messages (session_id, role, content, metadata_json) VALUES (?, ?, ?, ?) RETURNING id",
            (session_id, role, content, meta_json),
        )
        conn.execute(
            "UPDATE lab_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (session_id,),
        )
        conn.commit()
    row = conn.execute("SELECT * FROM lab_messages WHERE id = ?", (cur.lastrowid,)).fetchone()
    result = dict(row)
    try:
        result["metadata"] = json.loads(result.pop("metadata_json", None) or "{}")
    except (json.JSONDecodeError, TypeError):
        result["metadata"] = {}
    return result


def get_messages(conn, session_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM lab_messages WHERE session_id = ? ORDER BY created_at ASC",
        (session_id,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["metadata"] = json.loads(d.pop("metadata_json", None) or "{}")
        except (json.JSONDecodeError, TypeError):
            d["metadata"] = {}
        result.append(d)
    return result


def _build_chat_messages(conn, session_id: int) -> list[dict]:
    """Format message history for Anthropic API."""
    rows = conn.execute(
        "SELECT role, content FROM lab_messages WHERE session_id = ? ORDER BY created_at ASC",
        (session_id,),
    ).fetchall()
    messages = []
    for r in rows:
        if r["role"] in ("user", "assistant"):
            messages.append({"role": r["role"], "content": r["content"]})
    return messages


def _generate_session_title(user_message: str) -> str:
    """Generate a short title from the first user message."""
    text = user_message.strip()
    for prefix in [
        "Help me", "Can you", "I need", "I want", "Please",
        "Analyze", "Review", "Generate", "Create", "Develop",
        "What is", "How does", "Tell me about",
    ]:
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].strip()
            break
    for sep in ['.', '?', '\n']:
        idx = text.find(sep)
        if 0 < idx < 80:
            text = text[:idx]
            break
    title = text[:60].strip()
    if len(text) > 60:
        title += "..."
    return title or "New Conversation"


def _parse_ai_response(raw: str) -> dict:
    """Parse AI response. Expects JSON; graceful fallback to plain text."""
    cleaned = strip_markdown_fences(raw)
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return {
            "text": raw,
            "follow_up_questions": [],
        }


# ─────────────────────────────────────────────
# Chat orchestration
# ─────────────────────────────────────────────

def chat(conn, session_id: int, user_id: int,
         user_message: str, agent_type: str = "search_strategist") -> dict:
    """Orchestrate a chat turn for any lab agent type."""

    # Validate session ownership + agent type
    sess = conn.execute(
        "SELECT * FROM lab_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if not sess:
        raise HTTPException(404, "Session not found")

    # Save user message
    add_message(conn, session_id, "user", user_message)

    # Auto-title on first user message
    msg_count = conn.execute(
        "SELECT COUNT(*) AS c FROM lab_messages WHERE session_id = ? AND role = 'user'",
        (session_id,),
    ).fetchone()["c"]
    if msg_count == 1:
        title = _generate_session_title(user_message)
        with conn:
            conn.execute(
                "UPDATE lab_sessions SET title = ? WHERE id = ?",
                (title, session_id),
            )
            conn.commit()

    # For search_strategist, delegate to the search module
    if agent_type == "search_strategist":
        return _chat_search_strategist(conn, session_id, user_id, user_message)

    # For other agents, use the lab agent system
    return _chat_lab_agent(conn, session_id, user_id, user_message, agent_type)


def _chat_search_strategist(conn, session_id: int, user_id: int,
                             user_message: str) -> dict:
    """Handle search strategist using skill-based prompt."""
    skill = get_active_skill(conn, "search_strategist")
    messages = _build_chat_messages(conn, session_id)
    raw = call_anthropic(messages, skill["prompt_text"], max_tokens=4096)
    parsed = _parse_ai_response(raw)

    # Update PICO if present
    if parsed.get("pico"):
        with conn:
            conn.execute(
                "UPDATE lab_sessions SET pico_json = ? WHERE id = ?",
                (json.dumps(parsed["pico"]), session_id),
            )
            conn.commit()

    metadata = {}
    if parsed.get("pico"):
        metadata["pico"] = parsed["pico"]
    if parsed.get("search_query"):
        metadata["search_query"] = parsed["search_query"]
    if parsed.get("follow_up_questions"):
        metadata["follow_ups"] = parsed["follow_up_questions"]

    assistant_msg = add_message(
        conn, session_id, "assistant", parsed.get("text", ""), metadata or None
    )

    return {
        "session_id": session_id,
        "message": assistant_msg,
        "pico": parsed.get("pico"),
        "search_query": parsed.get("search_query"),
        "follow_up_questions": parsed.get("follow_up_questions", []),
    }


def _chat_lab_agent(conn, session_id: int, user_id: int,
                    user_message: str, agent_type: str) -> dict:
    """Handle statistician, appraiser, hypothesis, and literature agents."""
    skill = get_active_skill(conn, agent_type)
    messages = _build_chat_messages(conn, session_id)
    raw = call_anthropic(messages, skill["prompt_text"], max_tokens=8192)
    parsed = _parse_ai_response(raw)

    metadata = {}
    if parsed.get("follow_up_questions"):
        metadata["follow_ups"] = parsed["follow_up_questions"]
    # Agent-specific metadata
    for key in ("analysis_plan", "critique", "appraisal", "overall_rating",
                "hypotheses", "outline", "citation_list", "code_blocks",
                "images"):
        if parsed.get(key):
            metadata[key] = parsed[key]

    assistant_msg = add_message(
        conn, session_id, "assistant", parsed.get("text", ""), metadata or None
    )

    return {
        "session_id": session_id,
        "message": assistant_msg,
        "follow_up_questions": parsed.get("follow_up_questions", []),
        "agent_type": agent_type,
        **{k: v for k, v in metadata.items() if k != "follow_ups"},
    }
