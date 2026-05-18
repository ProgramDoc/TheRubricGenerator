"""Chrome extension pairing + PDF-fetch queue.

The Chrome extension runs inside the user's authenticated browser (institutional
VPN/SSO cookies intact) and pulls paywalled PDFs that our server-side fetchers
can't reach. This module owns:

- The pairing-code lifecycle (one-time short code → permanent ``rg_ext_*`` token)
- The per-user "papers waiting for extension fetch" queue (papers where
  ``pdf_status='extension_pending'``)
- The upload endpoint logic that validates incoming PDF bytes, stores them via
  ``backend/paper_files.write_paper_file``, and atomically upgrades the metadata
  paper row via ``backend/search._upgrade_paper_to_pdf``
- The skip endpoint (mark as ``fetch_failed``, idempotent)
- A thin LLM-resolve endpoint that wraps ``backend.pdf_link_picker`` so the
  extension's content script can fall back to LLM-driven nav when its DOM
  heuristics miss (same prompt + JSON contract as the server-side browser-agent)

Auth model: ``users.extension_token`` (``rg_ext_<urlsafe>``) is checked alongside
``users.api_key`` in ``main.py:_get_user_by_api_key``. One extension per user;
revoke = clear the column. No cross-device coordination in v1.

DB tables: ``extension_pairings`` (short code → user_id, with TTL + consumption
audit) plus two columns on ``users`` (``extension_token``, ``extension_paired_at``).
All migrations are idempotent and run from ``main.py:init_db``.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import paper_files, pdf_fetcher
from .db import column_exists

logger = logging.getLogger("rubricgen")

# Pairing-code policy
PAIRING_CODE_TTL_SECONDS = 10 * 60  # 10 min
# Confusion-resistant alphabet — no 0/O, 1/I/L
_PAIRING_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PAIRING_CODE_LENGTH_PER_GROUP = 4  # → "EX-XXXX-YYYY"

# Token policy
EXTENSION_TOKEN_PREFIX = "rg_ext_"

# Upload policy
MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MB hard cap on incoming PDF bytes

# Queue policy
DEFAULT_QUEUE_LIMIT = 50
MAX_QUEUE_LIMIT = 200


EXTENSION_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS extension_pairings (
    code           TEXT PRIMARY KEY,
    user_id        INTEGER NOT NULL,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at     TIMESTAMP NOT NULL,
    consumed_at    TIMESTAMP,
    consumed_token TEXT
);
CREATE INDEX IF NOT EXISTS idx_extension_pairings_user
    ON extension_pairings(user_id);
CREATE INDEX IF NOT EXISTS idx_extension_pairings_expires
    ON extension_pairings(expires_at);
"""


def migrate_user_columns(conn) -> None:
    """Idempotent ALTER TABLE for users.extension_token + extension_paired_at.
    Called from main.py:init_db()."""
    if not column_exists(conn, "users", "extension_token"):
        try:
            with conn:
                conn.execute("ALTER TABLE users ADD COLUMN extension_token TEXT")
                conn.commit()
        except Exception as e:
            logger.warning("Could not add users.extension_token: %s", e)
    if not column_exists(conn, "users", "extension_paired_at"):
        try:
            with conn:
                conn.execute("ALTER TABLE users ADD COLUMN extension_paired_at TIMESTAMP")
                conn.commit()
        except Exception as e:
            logger.warning("Could not add users.extension_paired_at: %s", e)


# ─────────────────────────────────────────────
# Pairing flow
# ─────────────────────────────────────────────

def _generate_pairing_code() -> str:
    """Return a fresh ``EX-XXXX-YYYY`` code (~38 bits of entropy)."""
    g1 = "".join(secrets.choice(_PAIRING_ALPHABET) for _ in range(PAIRING_CODE_LENGTH_PER_GROUP))
    g2 = "".join(secrets.choice(_PAIRING_ALPHABET) for _ in range(PAIRING_CODE_LENGTH_PER_GROUP))
    return f"EX-{g1}-{g2}"


def mint_pairing_code(conn, user_id: int) -> dict:
    """Create a fresh pairing code for a user. Invalidates any prior unconsumed
    code so there's only one live code at a time per user.

    Returns ``{code, expires_at_iso, ttl_seconds}``.
    """
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=PAIRING_CODE_TTL_SECONDS)
    # Invalidate prior unconsumed codes for this user (set expires_at = now)
    with conn:
        conn.execute(
            """UPDATE extension_pairings SET expires_at = ?
               WHERE user_id = ? AND consumed_at IS NULL AND expires_at > ?""",
            (now.isoformat(), user_id, now.isoformat()),
        )
        # Mint a new code, retrying on the (very unlikely) PK collision
        for _ in range(5):
            code = _generate_pairing_code()
            try:
                conn.execute(
                    """INSERT INTO extension_pairings (code, user_id, expires_at)
                       VALUES (?, ?, ?)""",
                    (code, user_id, expires_at.isoformat()),
                )
                conn.commit()
                return {
                    "code": code,
                    "expires_at": expires_at.isoformat(),
                    "ttl_seconds": PAIRING_CODE_TTL_SECONDS,
                }
            except Exception:
                continue
    raise RuntimeError("Failed to mint pairing code after 5 attempts")


def consume_pairing_code(conn, code: str) -> dict:
    """Exchange a pairing code for a permanent extension token.

    Raises ``ValueError`` with a status hint:
        - ``not_found`` (404) — unknown code
        - ``expired`` (410) — TTL elapsed
        - ``already_consumed`` (409) — code already used

    Returns ``{token, user_id, paired_at}`` on success. The token is
    ``rg_ext_<token_urlsafe(32)>`` and is also written to ``users.extension_token``.
    """
    code = (code or "").strip().upper()
    if not code:
        raise ValueError("not_found")
    row = conn.execute(
        """SELECT code, user_id, expires_at, consumed_at
           FROM extension_pairings WHERE code = ?""",
        (code,),
    ).fetchone()
    if not row:
        raise ValueError("not_found")
    if row["consumed_at"]:
        raise ValueError("already_consumed")
    # Compare expiry
    now = datetime.now(timezone.utc)
    try:
        exp = datetime.fromisoformat(row["expires_at"])
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
    except Exception:
        # Bad row — treat as expired
        raise ValueError("expired")
    if exp < now:
        raise ValueError("expired")

    # Mint the long token + set on users
    token = EXTENSION_TOKEN_PREFIX + secrets.token_urlsafe(32)
    paired_at = now.isoformat()
    user_id = row["user_id"]
    with conn:
        conn.execute(
            """UPDATE users SET extension_token = ?, extension_paired_at = ?
               WHERE id = ?""",
            (token, paired_at, user_id),
        )
        conn.execute(
            """UPDATE extension_pairings
               SET consumed_at = ?, consumed_token = ?
               WHERE code = ?""",
            (paired_at, token, code),
        )
        conn.commit()
    return {"token": token, "user_id": user_id, "paired_at": paired_at}


def revoke_extension_token(conn, user_id: int) -> None:
    """Clear the extension token for a user. Idempotent."""
    with conn:
        conn.execute(
            """UPDATE users SET extension_token = NULL, extension_paired_at = NULL
               WHERE id = ?""",
            (user_id,),
        )
        conn.commit()


def get_user_by_extension_token(conn, token: str) -> dict | None:
    """Auth lookup for ``rg_ext_*`` tokens. Returns the same shape as
    ``main.py:_get_user_by_api_key`` so callers can use either interchangeably."""
    if not token or not token.startswith(EXTENSION_TOKEN_PREFIX):
        return None
    row = conn.execute(
        "SELECT id, email, display_name, role FROM users WHERE extension_token = ?",
        (token,),
    ).fetchone()
    return dict(row) if row else None


def get_extension_status(conn, user_id: int) -> dict:
    """Return ``{paired, paired_at, queue_count}`` for the developers-page UI."""
    user = conn.execute(
        "SELECT extension_token, extension_paired_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    paired = bool(user and user["extension_token"])
    paired_at = user["extension_paired_at"] if user else None
    queue_count = 0
    if paired:
        row = conn.execute(
            """SELECT COUNT(*) AS c FROM papers
               WHERE user_id = ? AND pdf_status = 'extension_pending'""",
            (user_id,),
        ).fetchone()
        queue_count = (row["c"] if row else 0) or 0
    return {"paired": paired, "paired_at": paired_at, "queue_count": queue_count}


# ─────────────────────────────────────────────
# Queue
# ─────────────────────────────────────────────

def get_queue(conn, user_id: int, limit: int = DEFAULT_QUEUE_LIMIT) -> list[dict]:
    """Return papers awaiting extension fetch for this user, oldest-first.

    Each entry includes a ``landing_url`` the extension can navigate to —
    that's ``papers.external_url`` (set by the search-import flow)."""
    limit = max(1, min(int(limit or DEFAULT_QUEUE_LIMIT), MAX_QUEUE_LIMIT))
    rows = conn.execute(
        """SELECT p.id AS paper_id, p.filename, p.external_url,
                  p.created_at,
                  sr.title, sr.doi, sr.pmid, sr.pmcid
           FROM papers p
           LEFT JOIN search_results sr ON sr.paper_id = p.id
           WHERE p.user_id = ? AND p.pdf_status = 'extension_pending'
           ORDER BY p.id ASC
           LIMIT ?""",
        (user_id, limit),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # Title falls back to the filename stub if no search_result joined
        if not d.get("title"):
            d["title"] = (d.get("filename") or "Untitled").replace(".pdf", "").replace("_", " ")[:120]
        d["landing_url"] = d.pop("external_url", None) or _fallback_landing_url(d)
        out.append(d)
    return out


def _fallback_landing_url(d: dict) -> str | None:
    """If a row has no external_url, synthesize one from doi/pmid/pmcid."""
    if d.get("doi"):
        return f"https://doi.org/{d['doi']}"
    if d.get("pmcid"):
        return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{d['pmcid']}/"
    if d.get("pmid"):
        return f"https://pubmed.ncbi.nlm.nih.gov/{d['pmid']}/"
    return None


# ─────────────────────────────────────────────
# Upload
# ─────────────────────────────────────────────

def upload_pdf_for_paper(conn, user_id: int, paper_id: int,
                         pdf_bytes: bytes) -> dict:
    """Validate + store + upgrade. Raises ``ValueError`` with a status hint
    on validation failure:
        - ``too_large`` (413)
        - ``not_pdf`` (415)
        - ``not_found`` (404) — paper missing or wrong owner
        - ``already_present`` (409) — paper already has a PDF
        - ``upgrade_failed`` (500)

    Returns ``{ok: True, paper_id, sha256, storage_path}`` on success.
    """
    if not pdf_bytes:
        raise ValueError("not_pdf")
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise ValueError("too_large")
    if not pdf_fetcher._is_pdf_bytes(pdf_bytes):
        raise ValueError("not_pdf")

    # Look up the paper + verify ownership.
    paper = conn.execute(
        """SELECT p.id, p.user_id, p.pdf_status,
                  sr.id AS search_result_id, sr.pmcid, sr.title, sr.doi, sr.pmid
           FROM papers p
           LEFT JOIN search_results sr ON sr.paper_id = p.id
           WHERE p.id = ?""",
        (paper_id,),
    ).fetchone()
    if not paper or paper["user_id"] != user_id:
        raise ValueError("not_found")
    if paper["pdf_status"] == "present":
        raise ValueError("already_present")

    sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    filename = f"{sha256}.pdf"
    try:
        storage_path = paper_files.write_paper_file(pdf_bytes, filename)
    except Exception as e:
        logger.error("Extension upload: storage write failed: %s", e)
        raise ValueError("upgrade_failed")

    # Reuse the existing atomic upgrade path. _upgrade_paper_to_pdf wants a
    # row-like object with .pmcid / .title — synthesize a minimal one when the
    # paper isn't backed by a search_result row.
    from . import search as search_mod

    upgrade_row = {
        "pmcid": paper["pmcid"] or "",
        "title": paper["title"] or "",
        "doi": paper["doi"] or "",
        "pmid": paper["pmid"] or "",
    }
    pdf_result = {
        "sha256": sha256,
        "filename": filename,
        "storage_path": storage_path,
    }
    upgraded_id = search_mod._upgrade_paper_to_pdf(
        conn, paper_id, upgrade_row, pdf_result,
    )
    if not upgraded_id:
        raise ValueError("upgrade_failed")

    return {
        "ok": True,
        "paper_id": paper_id,
        "sha256": sha256,
        "storage_path": storage_path,
        "filename": filename,
    }


def skip_paper(conn, user_id: int, paper_id: int) -> dict:
    """Mark an extension-queued paper as ``fetch_failed`` (the user's browser
    couldn't pull it either). Idempotent: returns ``{ok: True, status}``.

    Raises ``ValueError("not_found")`` if the paper doesn't exist or isn't
    owned by the user.
    """
    paper = conn.execute(
        "SELECT id, user_id, pdf_status FROM papers WHERE id = ?",
        (paper_id,),
    ).fetchone()
    if not paper or paper["user_id"] != user_id:
        raise ValueError("not_found")
    if paper["pdf_status"] != "extension_pending":
        # Idempotent — already terminal
        return {"ok": True, "status": paper["pdf_status"]}
    with conn:
        conn.execute(
            "UPDATE papers SET pdf_status = 'fetch_failed' WHERE id = ?",
            (paper_id,),
        )
        conn.commit()
    return {"ok": True, "status": "fetch_failed"}


# ─────────────────────────────────────────────
# Cleanup (called from a periodic task or migration)
# ─────────────────────────────────────────────

def purge_expired_pairings(conn, max_age_days: int = 7) -> int:
    """Delete pairing rows older than ``max_age_days`` (consumed or expired).
    Returns the row count. Best-effort, never raises."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
        with conn:
            cur = conn.execute(
                "DELETE FROM extension_pairings WHERE created_at < ?",
                (cutoff,),
            )
            conn.commit()
        return cur.rowcount or 0
    except Exception as e:
        logger.warning("purge_expired_pairings failed: %s", e)
        return 0
