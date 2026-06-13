"""Paper collections — lightweight "folders" of papers inside a project.

Two kinds:
  - ``manual``   — a folder the user curates by hand (add/remove papers).
  - ``selected`` — an *auto* folder, owned by a Synthesis review, that mirrors
                   the studies that review included. Re-synced whenever the
                   review finishes screening or a reviewer overrides a decision,
                   so it always reflects the live set of selected papers.

A collection's membership rows carry a ``source`` (``auto`` | ``manual``).
``sync_review_selected`` only ever rewrites the ``auto`` rows, so any papers a
user later pins by hand survive a re-sync — this is the upgrade path to the
"Both" behaviour without a schema change.

This module deliberately depends only on ``backend.db`` so it can be imported
from both ``main.py`` and ``backend/synthesis.py`` without circular imports.
"""
from __future__ import annotations

import logging

from backend.db import get_db  # noqa: F401  (re-exported for convenience)

logger = logging.getLogger("collections")


COLLECTIONS_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS paper_collections (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id  INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'manual',   -- 'manual' | 'selected'
    review_id   INTEGER,                          -- set for kind='selected'
    description TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_paper_collections_user ON paper_collections(user_id);
CREATE INDEX IF NOT EXISTS idx_paper_collections_proj ON paper_collections(project_id);
CREATE INDEX IF NOT EXISTS idx_paper_collections_review ON paper_collections(review_id);

CREATE TABLE IF NOT EXISTS paper_collection_members (
    collection_id INTEGER NOT NULL REFERENCES paper_collections(id) ON DELETE CASCADE,
    paper_id      INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    source        TEXT NOT NULL DEFAULT 'manual',  -- 'auto' | 'manual'
    added_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (collection_id, paper_id)
);
CREATE INDEX IF NOT EXISTS idx_pcm_paper ON paper_collection_members(paper_id);
"""


def _row(r) -> dict:
    return dict(r) if r is not None else {}


# ── Queries ─────────────────────────────────────────────────────────────────

def list_collections(conn, user_id: int, project_id: int | None = None) -> list[dict]:
    """All collections for a user (optionally scoped to a project), with counts."""
    if project_id is not None:
        rows = conn.execute(
            "SELECT * FROM paper_collections WHERE user_id=? AND project_id=? "
            "ORDER BY kind DESC, name ASC",
            (user_id, project_id),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM paper_collections WHERE user_id=? ORDER BY kind DESC, name ASC",
            (user_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = _row(r)
        cnt = conn.execute(
            "SELECT COUNT(*) AS n FROM paper_collection_members WHERE collection_id=?",
            (d["id"],),
        ).fetchone()
        d["paper_count"] = int(cnt["n"] if cnt else 0)
        out.append(d)
    return out


def get_collection(conn, collection_id: int, user_id: int) -> dict | None:
    r = conn.execute(
        "SELECT * FROM paper_collections WHERE id=? AND user_id=?",
        (collection_id, user_id),
    ).fetchone()
    return _row(r) if r else None


def collection_paper_ids(conn, collection_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT paper_id FROM paper_collection_members WHERE collection_id=? ORDER BY added_at",
        (collection_id,),
    ).fetchall()
    return [int(r["paper_id"]) for r in rows]


# ── Mutations ───────────────────────────────────────────────────────────────

def create_collection(
    conn,
    user_id: int,
    project_id: int | None,
    name: str,
    kind: str = "manual",
    review_id: int | None = None,
    description: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO paper_collections (user_id, project_id, name, kind, review_id, description) "
        "VALUES (?,?,?,?,?,?) RETURNING id",
        (user_id, project_id, name, kind, review_id, description),
    )
    cid = cur.lastrowid
    conn.commit()
    return int(cid)


def rename_collection(conn, collection_id: int, name: str) -> None:
    conn.execute(
        "UPDATE paper_collections SET name=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (name, collection_id),
    )
    conn.commit()


def delete_collection(conn, collection_id: int) -> None:
    conn.execute("DELETE FROM paper_collections WHERE id=?", (collection_id,))
    conn.commit()


def add_paper(conn, collection_id: int, paper_id: int, source: str = "manual") -> None:
    conn.execute(
        "INSERT INTO paper_collection_members (collection_id, paper_id, source) "
        "VALUES (?,?,?) ON CONFLICT DO NOTHING",
        (collection_id, paper_id, source),
    )
    conn.execute(
        "UPDATE paper_collections SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (collection_id,),
    )
    conn.commit()


def remove_paper(conn, collection_id: int, paper_id: int) -> None:
    conn.execute(
        "DELETE FROM paper_collection_members WHERE collection_id=? AND paper_id=?",
        (collection_id, paper_id),
    )
    conn.execute(
        "UPDATE paper_collections SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (collection_id,),
    )
    conn.commit()


# ── Auto-folder sync from a Synthesis review ─────────────────────────────────

def sync_review_selected(conn, review_id: int) -> int | None:
    """Upsert the auto 'selected' collection for a Synthesis review and refresh
    its ``auto`` membership to the review's currently-included studies.

    Returns the collection id, or None if the review no longer exists.
    Manually-pinned members (source='manual') are preserved.
    """
    review = conn.execute(
        "SELECT id, user_id, project_id, title FROM synthesis_reviews "
        "WHERE id=? AND deleted_at IS NULL",
        (review_id,),
    ).fetchone()
    if not review:
        return None
    review = _row(review)
    user_id = review["user_id"]
    project_id = review.get("project_id")
    title = (review.get("title") or f"Review {review_id}").strip()
    coll_name = f"Selected — {title}"[:160]

    # The included papers: studies the review kept (final decision = include).
    included = conn.execute(
        "SELECT paper_id FROM synthesis_studies "
        "WHERE review_id=? AND screening_decision='include' AND status='included'",
        (review_id,),
    ).fetchall()
    paper_ids = [int(r["paper_id"]) for r in included]

    # Find or create the review's selected collection.
    existing = conn.execute(
        "SELECT id FROM paper_collections WHERE review_id=? AND kind='selected'",
        (review_id,),
    ).fetchone()
    if existing:
        cid = int(existing["id"])
        conn.execute(
            "UPDATE paper_collections SET name=?, project_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (coll_name, project_id, cid),
        )
        # Drop only the auto rows; keep any manually-pinned papers.
        conn.execute(
            "DELETE FROM paper_collection_members WHERE collection_id=? AND source='auto'",
            (cid,),
        )
    else:
        cur = conn.execute(
            "INSERT INTO paper_collections (user_id, project_id, name, kind, review_id, description) "
            "VALUES (?,?,?, 'selected', ?, ?) RETURNING id",
            (user_id, project_id, coll_name, review_id,
             "Auto-updated: papers included by this systematic review."),
        )
        cid = int(cur.lastrowid)

    for pid in paper_ids:
        conn.execute(
            "INSERT INTO paper_collection_members (collection_id, paper_id, source) "
            "VALUES (?,?, 'auto') ON CONFLICT DO NOTHING",
            (cid, pid),
        )
    conn.commit()
    return cid
