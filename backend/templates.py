"""Phase 8 — Advanced Rubric Types.

Rubric templates (reusable, versioned), community library (publish/fork/rate),
living template stats (per-question performance tracking), and ground-truth
annotation import from The AI Researcher Annotator.
"""

import json
import logging
import sqlite3
from typing import Any

from fastapi import HTTPException

from .db import IntegrityError

logger = logging.getLogger("rubricgen")

# ─────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────

TEMPLATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS rubric_templates (
    id            SERIAL PRIMARY KEY,
    name          TEXT    NOT NULL,
    description   TEXT,
    rubric_type   TEXT    NOT NULL DEFAULT 'custom',
    template_json TEXT    NOT NULL,
    version       INTEGER NOT NULL DEFAULT 1,
    parent_id     INTEGER REFERENCES rubric_templates(id) ON DELETE SET NULL,
    created_by    INTEGER NOT NULL REFERENCES users(id),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_rt_user ON rubric_templates(created_by);
CREATE INDEX IF NOT EXISTS idx_rt_type ON rubric_templates(rubric_type);

CREATE TABLE IF NOT EXISTS template_question_stats (
    id            SERIAL PRIMARY KEY,
    template_id   INTEGER NOT NULL REFERENCES rubric_templates(id) ON DELETE CASCADE,
    question_id   TEXT    NOT NULL,
    times_used    INTEGER DEFAULT 0,
    avg_score     REAL    DEFAULT 0,
    min_score     REAL    DEFAULT 0,
    max_score     REAL    DEFAULT 1,
    flagged       TEXT,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(template_id, question_id)
);
CREATE INDEX IF NOT EXISTS idx_tqs_template ON template_question_stats(template_id);

CREATE TABLE IF NOT EXISTS community_templates (
    id             SERIAL PRIMARY KEY,
    template_id    INTEGER NOT NULL REFERENCES rubric_templates(id) ON DELETE CASCADE,
    published_by   INTEGER NOT NULL REFERENCES users(id),
    title          TEXT    NOT NULL,
    description    TEXT,
    rubric_type    TEXT    NOT NULL,
    question_count INTEGER DEFAULT 0,
    fork_count     INTEGER DEFAULT 0,
    rating_sum     REAL    DEFAULT 0,
    rating_count   INTEGER DEFAULT 0,
    published_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(template_id)
);
CREATE INDEX IF NOT EXISTS idx_ct_type ON community_templates(rubric_type);
CREATE INDEX IF NOT EXISTS idx_ct_published ON community_templates(published_at);

CREATE TABLE IF NOT EXISTS community_ratings (
    template_id   INTEGER NOT NULL REFERENCES community_templates(id) ON DELETE CASCADE,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rating        INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (template_id, user_id)
);

CREATE TABLE IF NOT EXISTS ground_truth_annotations (
    id              SERIAL PRIMARY KEY,
    rubric_id       INTEGER REFERENCES rubrics(id) ON DELETE CASCADE,
    challenge_id    INTEGER REFERENCES challenges(id) ON DELETE CASCADE,
    question_id     TEXT    NOT NULL,
    expert_answer   TEXT    NOT NULL,
    expert_score    REAL,
    annotator_email TEXT,
    annotation_id   TEXT,
    imported_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_gta_rubric ON ground_truth_annotations(rubric_id);
CREATE INDEX IF NOT EXISTS idx_gta_challenge ON ground_truth_annotations(challenge_id);
"""


# ─────────────────────────────────────────────
# Template CRUD
# ─────────────────────────────────────────────

def create_template(conn: sqlite3.Connection, user_id: int, name: str,
                    description: str = "", rubric_type: str = "custom",
                    template_json: str = "{}",
                    parent_id: int | None = None) -> dict:
    """Create a new rubric template."""
    name = name.strip()
    if not name:
        raise HTTPException(400, "Template name is required")
    if len(name) > 200:
        raise HTTPException(400, "Template name must be 200 characters or less")

    # Validate JSON
    try:
        parsed = json.loads(template_json) if isinstance(template_json, str) else template_json
        if isinstance(parsed, dict):
            template_json = json.dumps(parsed)
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(400, "Invalid template JSON")

    with conn:
        cur = conn.execute(
            """INSERT INTO rubric_templates
               (name, description, rubric_type, template_json, version, parent_id, created_by)
               VALUES (?, ?, ?, ?, 1, ?, ?) RETURNING id""",
            (name, description, rubric_type, template_json, parent_id, user_id),
        )
        conn.commit()
    return get_template(conn, cur.lastrowid)


def get_template(conn: sqlite3.Connection, template_id: int) -> dict:
    """Get template with question stats."""
    row = conn.execute(
        """SELECT rt.*, u.display_name AS creator_name, u.email AS creator_email
           FROM rubric_templates rt
           LEFT JOIN users u ON u.id = rt.created_by
           WHERE rt.id = ?""",
        (template_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Template not found")

    result = dict(row)
    try:
        result["template_data"] = json.loads(result.pop("template_json", "{}"))
    except (json.JSONDecodeError, TypeError):
        result["template_data"] = {}

    # Question stats
    stats = conn.execute(
        "SELECT * FROM template_question_stats WHERE template_id = ? ORDER BY question_id",
        (template_id,),
    ).fetchall()
    result["question_stats"] = [dict(s) for s in stats]

    # Community info if published
    ct = conn.execute(
        "SELECT * FROM community_templates WHERE template_id = ?",
        (template_id,),
    ).fetchone()
    result["published"] = dict(ct) if ct else None

    return result


def list_user_templates(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """List templates created by a user."""
    rows = conn.execute(
        """SELECT rt.id, rt.name, rt.description, rt.rubric_type, rt.version,
                  rt.parent_id, rt.created_at, rt.updated_at,
                  (SELECT COUNT(*) FROM template_question_stats WHERE template_id = rt.id) AS stats_count
           FROM rubric_templates rt
           WHERE rt.created_by = ?
           ORDER BY rt.updated_at DESC""",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def update_template(conn: sqlite3.Connection, template_id: int, user_id: int,
                    name: str | None = None, description: str | None = None,
                    template_json: str | None = None) -> dict:
    """Update a template. Bumps version on content changes."""
    row = conn.execute(
        "SELECT created_by, version FROM rubric_templates WHERE id = ?",
        (template_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Template not found")
    if row["created_by"] != user_id:
        raise HTTPException(403, "Only the creator can edit this template")

    updates: list[str] = ["updated_at = CURRENT_TIMESTAMP"]
    params: list[Any] = []

    if name is not None:
        name = name.strip()
        if not name:
            raise HTTPException(400, "Name cannot be empty")
        updates.append("name = ?")
        params.append(name)
    if description is not None:
        updates.append("description = ?")
        params.append(description)
    if template_json is not None:
        try:
            parsed = json.loads(template_json) if isinstance(template_json, str) else template_json
            template_json = json.dumps(parsed) if isinstance(parsed, dict) else template_json
        except (json.JSONDecodeError, TypeError):
            raise HTTPException(400, "Invalid template JSON")
        updates.append("template_json = ?")
        params.append(template_json)
        updates.append("version = version + 1")

    params.append(template_id)
    with conn:
        conn.execute(
            f"UPDATE rubric_templates SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()
    return get_template(conn, template_id)


def delete_template(conn: sqlite3.Connection, template_id: int,
                    user_id: int) -> None:
    """Delete a template. Must be the creator."""
    row = conn.execute(
        "SELECT created_by FROM rubric_templates WHERE id = ?",
        (template_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Template not found")
    if row["created_by"] != user_id:
        raise HTTPException(403, "Only the creator can delete this template")
    with conn:
        conn.execute("DELETE FROM rubric_templates WHERE id = ?", (template_id,))
        conn.commit()


def fork_template(conn: sqlite3.Connection, source_template_id: int,
                  user_id: int) -> dict:
    """Fork (copy) a template to the user's account."""
    src = conn.execute(
        "SELECT * FROM rubric_templates WHERE id = ?",
        (source_template_id,),
    ).fetchone()
    if not src:
        raise HTTPException(404, "Source template not found")

    result = create_template(
        conn, user_id,
        name=f"{src['name']} (fork)",
        description=src["description"] or "",
        rubric_type=src["rubric_type"],
        template_json=src["template_json"],
        parent_id=source_template_id,
    )

    # Increment fork count on community template if published
    with conn:
        conn.execute(
            """UPDATE community_templates SET fork_count = fork_count + 1
               WHERE template_id = ?""",
            (source_template_id,),
        )
        conn.commit()

    return result


def create_template_from_rubric(conn: sqlite3.Connection, rubric_id: int,
                                user_id: int, name: str,
                                description: str = "") -> dict:
    """Create a template from an existing rubric."""
    rubric = conn.execute(
        "SELECT * FROM rubrics WHERE id = ? AND user_id = ?",
        (rubric_id, user_id),
    ).fetchone()
    if not rubric:
        raise HTTPException(404, "Rubric not found")

    return create_template(
        conn, user_id, name, description,
        rubric_type=rubric["rubric_type"],
        template_json=rubric["rubric_json"],
    )


# ─────────────────────────────────────────────
# Living template stats
# ─────────────────────────────────────────────

def update_question_stats(conn: sqlite3.Connection, template_id: int,
                          grades: dict) -> None:
    """Update per-question stats after an evaluation.
    grades: the grade_json dict with {grades: [{question_id, score, max_points}]}
    """
    for g in grades.get("grades", []):
        qid = g.get("question_id", "")
        score = g.get("score", 0)
        max_pts = g.get("max_points", 1)
        normalized = score / max_pts if max_pts > 0 else 0

        existing = conn.execute(
            "SELECT times_used, avg_score, min_score, max_score FROM template_question_stats WHERE template_id=? AND question_id=?",
            (template_id, qid),
        ).fetchone()

        if existing:
            n = existing["times_used"]
            new_avg = (existing["avg_score"] * n + normalized) / (n + 1)
            new_min = min(existing["min_score"], normalized)
            new_max = max(existing["max_score"], normalized)

            # Flag logic
            flagged = None
            if n + 1 >= 5:  # need at least 5 uses
                if new_avg > 0.95:
                    flagged = "too_easy"
                elif new_avg < 0.05:
                    flagged = "broken"

            with conn:
                conn.execute(
                    """UPDATE template_question_stats
                       SET times_used = times_used + 1, avg_score = ?, min_score = ?,
                           max_score = ?, flagged = ?, updated_at = CURRENT_TIMESTAMP
                       WHERE template_id = ? AND question_id = ?""",
                    (round(new_avg, 4), new_min, new_max, flagged, template_id, qid),
                )
                conn.commit()
        else:
            with conn:
                conn.execute(
                    """INSERT INTO template_question_stats
                       (template_id, question_id, times_used, avg_score, min_score, max_score)
                       VALUES (?, ?, 1, ?, ?, ?)""",
                    (template_id, qid, round(normalized, 4), normalized, normalized),
                )
                conn.commit()


def get_flagged_questions(conn: sqlite3.Connection,
                          template_id: int) -> list[dict]:
    """Return questions flagged as too_easy or broken."""
    rows = conn.execute(
        """SELECT * FROM template_question_stats
           WHERE template_id = ? AND flagged IS NOT NULL
           ORDER BY question_id""",
        (template_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────
# Community library
# ─────────────────────────────────────────────

def publish_template(conn: sqlite3.Connection, template_id: int,
                     user_id: int, title: str,
                     description: str = "") -> dict:
    """Publish a template to the community library."""
    tmpl = conn.execute(
        "SELECT * FROM rubric_templates WHERE id = ?",
        (template_id,),
    ).fetchone()
    if not tmpl:
        raise HTTPException(404, "Template not found")
    if tmpl["created_by"] != user_id:
        raise HTTPException(403, "Only the creator can publish this template")

    title = title.strip()
    if not title:
        raise HTTPException(400, "Title is required")

    # Count questions
    try:
        data = json.loads(tmpl["template_json"] or "{}")
        qcount = len(data.get("questions", []))
    except (json.JSONDecodeError, TypeError):
        qcount = 0

    try:
        with conn:
            conn.execute(
                """INSERT INTO community_templates
                   (template_id, published_by, title, description, rubric_type, question_count)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (template_id, user_id, title, description, tmpl["rubric_type"], qcount),
            )
            conn.commit()
    except IntegrityError:
        raise HTTPException(409, "This template is already published")

    ct = conn.execute(
        "SELECT * FROM community_templates WHERE template_id = ?",
        (template_id,),
    ).fetchone()
    return dict(ct)


def unpublish_template(conn: sqlite3.Connection, template_id: int,
                       user_id: int) -> None:
    """Remove a template from the community library."""
    ct = conn.execute(
        "SELECT published_by FROM community_templates WHERE template_id = ?",
        (template_id,),
    ).fetchone()
    if not ct:
        raise HTTPException(404, "Template not published")
    if ct["published_by"] != user_id:
        raise HTTPException(403, "Only the publisher can unpublish")
    with conn:
        conn.execute(
            "DELETE FROM community_templates WHERE template_id = ?",
            (template_id,),
        )
        conn.commit()


def list_community_templates(conn: sqlite3.Connection,
                             rubric_type: str | None = None,
                             sort: str = "recent",
                             search: str | None = None,
                             limit: int = 20,
                             offset: int = 0) -> dict:
    """Browse the community library. Returns {items, total}."""
    where_clauses = []
    params: list[Any] = []

    if rubric_type:
        where_clauses.append("ct.rubric_type = ?")
        params.append(rubric_type)
    if search:
        where_clauses.append("(ct.title ILIKE ? OR ct.description ILIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    where_sql = " AND ".join(where_clauses)
    if where_sql:
        where_sql = "WHERE " + where_sql

    order_map = {
        "recent": "ct.published_at DESC",
        "rating": "(CASE WHEN ct.rating_count > 0 THEN ct.rating_sum / ct.rating_count ELSE 0 END) DESC",
        "popular": "ct.fork_count DESC",
    }
    order = order_map.get(sort, "ct.published_at DESC")

    total_row = conn.execute(
        f"SELECT COUNT(*) AS c FROM community_templates ct {where_sql}", params
    ).fetchone()
    total = total_row["c"]

    rows = conn.execute(
        f"""SELECT ct.*, u.display_name AS publisher_name,
                   (CASE WHEN ct.rating_count > 0 THEN ROUND(ct.rating_sum / ct.rating_count, 1) ELSE 0 END) AS avg_rating
            FROM community_templates ct
            LEFT JOIN users u ON u.id = ct.published_by
            {where_sql}
            ORDER BY {order}
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    ).fetchall()

    return {"items": [dict(r) for r in rows], "total": total}


def get_community_template(conn: sqlite3.Connection,
                           community_template_id: int) -> dict:
    """Get a community template with its rubric data for preview."""
    ct = conn.execute(
        """SELECT ct.*, u.display_name AS publisher_name,
                  (CASE WHEN ct.rating_count > 0 THEN ROUND(ct.rating_sum / ct.rating_count, 1) ELSE 0 END) AS avg_rating
           FROM community_templates ct
           LEFT JOIN users u ON u.id = ct.published_by
           WHERE ct.id = ?""",
        (community_template_id,),
    ).fetchone()
    if not ct:
        raise HTTPException(404, "Community template not found")

    result = dict(ct)
    tmpl = conn.execute(
        "SELECT template_json, rubric_type FROM rubric_templates WHERE id = ?",
        (ct["template_id"],),
    ).fetchone()
    if tmpl:
        try:
            result["template_data"] = json.loads(tmpl["template_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            result["template_data"] = {}
    return result


def rate_template(conn: sqlite3.Connection, community_template_id: int,
                  user_id: int, rating: int) -> dict:
    """Rate a community template (1-5). Upserts."""
    if rating < 1 or rating > 5:
        raise HTTPException(400, "Rating must be between 1 and 5")

    ct = conn.execute(
        "SELECT id FROM community_templates WHERE id = ?",
        (community_template_id,),
    ).fetchone()
    if not ct:
        raise HTTPException(404, "Community template not found")

    existing = conn.execute(
        "SELECT rating FROM community_ratings WHERE template_id = ? AND user_id = ?",
        (community_template_id, user_id),
    ).fetchone()

    with conn:
        if existing:
            old_rating = existing["rating"]
            conn.execute(
                "UPDATE community_ratings SET rating = ?, created_at = CURRENT_TIMESTAMP WHERE template_id = ? AND user_id = ?",
                (rating, community_template_id, user_id),
            )
            conn.execute(
                """UPDATE community_templates
                   SET rating_sum = rating_sum - ? + ?,
                       rating_count = rating_count
                   WHERE id = ?""",
                (old_rating, rating, community_template_id),
            )
        else:
            conn.execute(
                "INSERT INTO community_ratings (template_id, user_id, rating) VALUES (?, ?, ?)",
                (community_template_id, user_id, rating),
            )
            conn.execute(
                """UPDATE community_templates
                   SET rating_sum = rating_sum + ?, rating_count = rating_count + 1
                   WHERE id = ?""",
                (rating, community_template_id),
            )
        conn.commit()

    return {"ok": True}


def fork_community_template(conn: sqlite3.Connection,
                            community_template_id: int,
                            user_id: int) -> dict:
    """Fork a community template to user's account."""
    ct = conn.execute(
        "SELECT template_id FROM community_templates WHERE id = ?",
        (community_template_id,),
    ).fetchone()
    if not ct:
        raise HTTPException(404, "Community template not found")
    return fork_template(conn, ct["template_id"], user_id)


# ─────────────────────────────────────────────
# Ground truth / Annotator integration
# ─────────────────────────────────────────────

def import_ground_truth(conn: sqlite3.Connection,
                        annotations: list[dict],
                        rubric_id: int | None = None,
                        challenge_id: int | None = None) -> int:
    """Import ground truth annotations from The AI Researcher Annotator.
    Each annotation: {question_id, expert_answer, expert_score?, annotator_email?, annotation_id?}
    Returns count of imported annotations.
    """
    if not annotations:
        raise HTTPException(400, "No annotations provided")
    if not rubric_id and not challenge_id:
        raise HTTPException(400, "Either rubric_id or challenge_id is required")

    count = 0
    with conn:
        for a in annotations:
            qid = a.get("question_id", "")
            answer = a.get("expert_answer", "")
            if not qid or not answer:
                continue
            conn.execute(
                """INSERT INTO ground_truth_annotations
                   (rubric_id, challenge_id, question_id, expert_answer,
                    expert_score, annotator_email, annotation_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    rubric_id, challenge_id, qid, answer,
                    a.get("expert_score"), a.get("annotator_email"),
                    a.get("annotation_id"),
                ),
            )
            count += 1
        conn.commit()
    logger.info("Imported %d ground truth annotations (rubric=%s, challenge=%s)",
                count, rubric_id, challenge_id)
    return count


def get_ground_truth(conn: sqlite3.Connection,
                     rubric_id: int | None = None,
                     challenge_id: int | None = None) -> list[dict]:
    """Retrieve ground truth annotations."""
    if rubric_id:
        rows = conn.execute(
            "SELECT * FROM ground_truth_annotations WHERE rubric_id = ? ORDER BY question_id",
            (rubric_id,),
        ).fetchall()
    elif challenge_id:
        rows = conn.execute(
            "SELECT * FROM ground_truth_annotations WHERE challenge_id = ? ORDER BY question_id",
            (challenge_id,),
        ).fetchall()
    else:
        return []
    return [dict(r) for r in rows]


def compare_judge_to_ground_truth(conn: sqlite3.Connection,
                                  evaluation_id: int) -> dict:
    """Compare an evaluation's judge grades to ground truth.
    Returns accuracy metrics showing how well the AI judge matches experts.
    """
    ev = conn.execute(
        "SELECT rubric_id, graded_json FROM evaluations WHERE id = ?",
        (evaluation_id,),
    ).fetchone()
    if not ev:
        raise HTTPException(404, "Evaluation not found")

    gt_rows = get_ground_truth(conn, rubric_id=ev["rubric_id"])
    if not gt_rows:
        raise HTTPException(404, "No ground truth annotations for this rubric")

    gt_map = {a["question_id"]: a for a in gt_rows}

    try:
        graded = json.loads(ev["graded_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(400, "Invalid graded JSON")

    comparisons = []
    total_agreement = 0
    total_compared = 0

    for g in graded.get("grades", []):
        qid = g.get("question_id", "")
        gt = gt_map.get(qid)
        if not gt:
            continue

        judge_score = g.get("score", 0)
        max_pts = g.get("max_points", 1)
        judge_normalized = judge_score / max_pts if max_pts > 0 else 0

        expert_score = gt.get("expert_score")
        if expert_score is not None:
            agreement = 1.0 - abs(judge_normalized - expert_score)
        else:
            agreement = None

        comparisons.append({
            "question_id": qid,
            "judge_score": judge_normalized,
            "expert_score": expert_score,
            "expert_answer": gt["expert_answer"],
            "judge_reasoning": g.get("reasoning", ""),
            "agreement": round(agreement, 4) if agreement is not None else None,
        })

        if agreement is not None:
            total_agreement += agreement
            total_compared += 1

    avg_agreement = round(total_agreement / total_compared, 4) if total_compared else None

    return {
        "evaluation_id": evaluation_id,
        "comparisons": comparisons,
        "total_compared": total_compared,
        "avg_agreement": avg_agreement,
    }
