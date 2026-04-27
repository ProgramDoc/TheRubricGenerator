"""Grade-review queue for the 3-judge adjudication pipeline.

When primary, shadow, and third judge grades all disagree on a single
question, the adjudicator flags that question for human review. This
module:

  1. Defines the ``grade_reviews`` table DDL.
  2. Exposes :func:`flag_for_review` / :func:`list_pending_reviews` /
     :func:`resolve_review` CRUD helpers used by the challenge pipeline
     and the admin UI.
  3. Wraps the SMTP notifier so operators get pinged when new items
     land in the queue.

Schema design — important per CLAUDE.md:
  * DDL is PostgreSQL-flavored (``SERIAL PRIMARY KEY``, ``CURRENT_TIMESTAMP``).
    The SqliteConnection wrapper in ``backend/db.py`` rewrites it at
    runtime for local dev.
  * No column is named ``timestamp`` — the SQLite compat wrapper rewrites
    that to ``TEXT`` unconditionally, which would clobber a real column.
  * Use ``created_at`` / ``resolved_at`` per convention.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# DDL — registered from main.py:init_db()
# ─────────────────────────────────────────────────────────────────────

GRADE_REVIEWS_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS grade_reviews (
    id                  SERIAL PRIMARY KEY,
    challenge_id        INTEGER NOT NULL,
    participant_id      INTEGER NOT NULL,    -- model_participants.id
    question_id         TEXT    NOT NULL,
    max_points          REAL    NOT NULL DEFAULT 1,
    primary_score       REAL,
    shadow_score        REAL,
    third_score         REAL,
    primary_reasoning   TEXT,
    shadow_reasoning    TEXT,
    third_reasoning     TEXT,
    question_text       TEXT,
    ideal_answer        TEXT,
    scoring_criteria    TEXT,
    model_answer        TEXT,
    status              TEXT    NOT NULL DEFAULT 'pending',  -- pending | resolved
    final_score         REAL,
    reviewer_user_id    INTEGER,
    reviewer_note       TEXT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at         TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_grade_reviews_status     ON grade_reviews(status);
CREATE INDEX IF NOT EXISTS idx_grade_reviews_challenge  ON grade_reviews(challenge_id);
CREATE INDEX IF NOT EXISTS idx_grade_reviews_created_at ON grade_reviews(created_at);
"""


# ─────────────────────────────────────────────────────────────────────
# Write path
# ─────────────────────────────────────────────────────────────────────

def flag_for_review(conn, *, challenge_id: int, participant_id: int,
                    flagged: list[dict], model_answer_map: dict[str, str]) -> list[int]:
    """Persist 3-way-split questions to the review queue.

    ``flagged`` is the list returned by
    :func:`backend.agents.adjudicator.adjudicate_grades`. Each entry
    carries primary/shadow/third scores and the three reasoning strings.

    ``model_answer_map`` is ``{question_id: answer_text}`` — we snapshot
    the participant's answer onto the review row so the human reviewer
    has everything on one page without a JOIN back to
    ``model_participants.answer_json``.

    Returns the list of newly-created review ids.
    """
    review_ids: list[int] = []
    if not flagged:
        return review_ids

    for f in flagged:
        cur = conn.execute(
            """INSERT INTO grade_reviews (
                   challenge_id, participant_id, question_id, max_points,
                   primary_score, shadow_score, third_score,
                   primary_reasoning, shadow_reasoning, third_reasoning,
                   question_text, ideal_answer, scoring_criteria, model_answer,
                   status
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending')
               RETURNING id""",
            (
                challenge_id, participant_id, f["question_id"],
                float(f.get("max_points") or 1),
                _maybe_float(f.get("primary")),
                _maybe_float(f.get("shadow")),
                _maybe_float(f.get("third")),
                f.get("primary_reasoning") or "",
                f.get("shadow_reasoning")  or "",
                f.get("third_reasoning")   or "",
                f.get("question", "")[:4000],
                _stringify(f.get("ideal_answer"))[:4000],
                f.get("scoring_criteria", "")[:2000],
                (model_answer_map.get(f["question_id"]) or "")[:8000],
            ),
        )
        # Resolve the new id portably:
        #   - PgConnection: RETURNING id feeds cur.fetchone() AND populates lastrowid.
        #   - SqliteConnection: the wrapper strips `RETURNING id` at runtime
        #     (see backend/db.py), so fetchone() returns None and we must
        #     fall back to cursor.lastrowid.
        rid: int | None = None
        try:
            row = cur.fetchone()
        except Exception:
            row = None
        if row is not None:
            try:
                rid = int(row[0] if isinstance(row, (tuple, list)) else row["id"])
            except Exception:
                rid = None
        if rid is None:
            rid = getattr(cur, "lastrowid", None)
        if rid is not None:
            review_ids.append(int(rid))

    conn.commit()
    logger.info(
        "Flagged %d question(s) for human review (challenge=%s participant=%s)",
        len(review_ids), challenge_id, participant_id,
    )
    return review_ids


def _maybe_float(x) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _stringify(x) -> str:
    if isinstance(x, (dict, list)):
        return json.dumps(x, ensure_ascii=False)
    return "" if x is None else str(x)


# ─────────────────────────────────────────────────────────────────────
# Read path
# ─────────────────────────────────────────────────────────────────────

def list_pending_reviews(conn, limit: int = 200) -> list[dict]:
    """Return pending reviews, newest first. Used by the admin inbox."""
    rows = conn.execute(
        """SELECT r.*, c.title AS challenge_title, mp.model_id AS model_id
             FROM grade_reviews r
             LEFT JOIN challenges         c  ON c.id  = r.challenge_id
             LEFT JOIN model_participants mp ON mp.id = r.participant_id
            WHERE r.status = 'pending'
            ORDER BY r.created_at DESC
            LIMIT ?""",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def get_review(conn, review_id: int) -> Optional[dict]:
    row = conn.execute(
        """SELECT r.*, c.title AS challenge_title, mp.model_id AS model_id
             FROM grade_reviews r
             LEFT JOIN challenges         c  ON c.id  = r.challenge_id
             LEFT JOIN model_participants mp ON mp.id = r.participant_id
            WHERE r.id = ?""",
        (int(review_id),),
    ).fetchone()
    return dict(row) if row else None


# ─────────────────────────────────────────────────────────────────────
# Resolve
# ─────────────────────────────────────────────────────────────────────

def resolve_review(conn, *, review_id: int, final_score: float,
                   reviewer_user_id: int, reviewer_note: str = "") -> dict:
    """Persist the human adjudicator's decision and rewrite the
    participant's ``grade_json`` + denormalized score columns so the
    leaderboard reflects the final grade.

    Returns the updated review row. Raises ``ValueError`` if the review
    has already been resolved or if the score is outside
    [0, max_points].
    """
    review = get_review(conn, review_id)
    if not review:
        raise ValueError(f"review {review_id} not found")
    if review["status"] != "pending":
        raise ValueError(f"review {review_id} already resolved")

    max_pts = float(review.get("max_points") or 1)
    score = float(final_score)
    if score < 0 or score > max_pts:
        raise ValueError(
            f"final_score {score} outside [0, {max_pts}] for review {review_id}"
        )

    conn.execute(
        """UPDATE grade_reviews
              SET status='resolved',
                  final_score=?,
                  reviewer_user_id=?,
                  reviewer_note=?,
                  resolved_at=CURRENT_TIMESTAMP
            WHERE id=?""",
        (score, int(reviewer_user_id), reviewer_note[:2000], int(review_id)),
    )

    # Rewrite the participant's grade_json so the leaderboard reflects
    # the adjudicated score. This is the reason grade_reviews stores
    # question_id + participant_id — we can do a targeted mutation
    # without re-running the judge.
    _apply_resolution_to_participant(
        conn,
        participant_id=int(review["participant_id"]),
        question_id=str(review["question_id"]),
        final_score=score,
        reviewer_note=reviewer_note,
    )

    conn.commit()
    return get_review(conn, review_id) or {}


def _apply_resolution_to_participant(conn, *, participant_id: int,
                                      question_id: str, final_score: float,
                                      reviewer_note: str) -> None:
    """Patch the per-participant grade_json + denormalized score columns.

    Rounds the new accuracy to 4 decimals the same way
    ``backend/challenges.py:score_participant`` does, so the two rows
    stay byte-identical after resolve.
    """
    row = conn.execute(
        "SELECT grade_json, speed_bonus FROM model_participants WHERE id=?",
        (participant_id,),
    ).fetchone()
    if not row:
        logger.warning("resolve_review: participant %s not found", participant_id)
        return

    try:
        grades = json.loads(row["grade_json"] or "{}")
    except Exception:
        grades = {}

    updated = False
    total_pts = 0.0
    max_pts = 0.0
    for g in grades.get("grades", []):
        if g.get("question_id") == question_id:
            g["score"] = final_score
            adj = g.setdefault("adjudication", {})
            adj["reviewed"] = True
            adj["status"] = "human"
            adj["final_score"] = final_score
            adj["reviewer_note"] = reviewer_note
            adj.pop("provisional", None)
            updated = True
        total_pts += float(g.get("score", 0) or 0)
        max_pts += float(g.get("max_points", 1) or 1)

    if not updated:
        logger.warning(
            "resolve_review: question %s not found in participant %s grade_json",
            question_id, participant_id,
        )
        return

    accuracy = round(total_pts / max_pts, 4) if max_pts else 0.0
    speed_bonus = float(row["speed_bonus"] or 0)
    total_score = round(accuracy * speed_bonus, 4)

    conn.execute(
        """UPDATE model_participants
              SET grade_json=?, accuracy=?, total_score=?
            WHERE id=?""",
        (json.dumps(grades), accuracy, total_score, participant_id),
    )


# ─────────────────────────────────────────────────────────────────────
# Notification
# ─────────────────────────────────────────────────────────────────────

def notify_admins(conn, review_ids: list[int]) -> None:
    """Email every admin user about fresh review items. Silent on any
    SMTP misconfiguration — email is a nicety, not a blocker."""
    if not review_ids:
        return

    host = os.environ.get("SMTP_HOST", "")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    pw   = os.environ.get("SMTP_PASS", "")
    sender = os.environ.get("SMTP_FROM", user or "noreply@rubricgen.local")
    app_base_url = os.environ.get("APP_BASE_URL", "http://localhost:8000")

    if not host or not user:
        logger.info("SMTP not configured; skipping review notification for %d item(s)",
                    len(review_ids))
        return

    admins = conn.execute(
        "SELECT email FROM users WHERE role='admin' AND email IS NOT NULL"
    ).fetchall()
    if not admins:
        return

    subject = f"[The AI Researcher] {len(review_ids)} grading disagreement(s) need review"
    body = (
        f"The 3-judge adjudicator flagged {len(review_ids)} question(s) where "
        f"primary, shadow, and third judges all disagreed.\n\n"
        f"Review queue: {app_base_url}/review\n\n"
        f"Review IDs: {', '.join(str(r) for r in review_ids)}\n"
    )

    for admin in admins:
        addr = admin["email"]
        try:
            msg = EmailMessage()
            msg["From"] = sender
            msg["To"]   = addr
            msg["Subject"] = subject
            msg.set_content(body)
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.starttls()
                s.login(user, pw)
                s.send_message(msg)
            logger.info("Sent review notification to %s", addr)
        except Exception as e:
            logger.error("Review-notification send failed for %s: %s", addr, e)
