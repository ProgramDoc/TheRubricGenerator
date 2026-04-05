"""Challenge orchestration: create, run (background), score, and leaderboard refresh.

A challenge is: a set of PDFs + a theme + a generated rubric + N participating
models + judge grades + scores. The run is synchronous on a background thread so
HTTP requests can return immediately while orchestration continues.
"""

import base64
import json
import logging
import sqlite3
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .agents.generator import run_generator_agent
from .agents.judge import run_judge_agent, shadow_regrade
from .agents.participants import provider_for, run_participant_model
from .obsidian import write_challenge_note, write_skill_note
from .skills import (
    get_active_skill, list_skill_versions, record_skill_performance,
)

logger = logging.getLogger("rubricgen")

SUPPORTED_MODELS = {
    "gpt-4o":                   "openai",
    "gpt-4-turbo":              "openai",
    "gpt-4o-mini":              "openai",
    "claude-sonnet-4-20250514": "anthropic",
    "gemini-2.5-pro":           "google",
    "gemini-2.0-flash":         "google",
}

# ─────────────────────────────────────────────
# Scoring formulas
# ─────────────────────────────────────────────

def speed_bonus(elapsed_ms: int, target_seconds: float) -> float:
    """1 / (1 + elapsed_s / target_s). 1.0 at instant, 0.5 at target, approaches 0 slowly."""
    elapsed_s = max(0, elapsed_ms) / 1000.0
    return 1.0 / (1.0 + elapsed_s / target_seconds)


def score_model_run(grades: dict, answer_time_ms: int) -> dict:
    total = float(grades.get("total_score", 0) or 0)
    max_s = float(grades.get("max_score", 0) or 0)
    accuracy = (total / max_s) if max_s > 0 else 0.0
    sb = speed_bonus(answer_time_ms, target_seconds=120)
    return {
        "accuracy": round(accuracy, 4),
        "speed_bonus": round(sb, 4),
        "total_score": round(accuracy * sb, 4),
    }


def score_generator(participants: list[dict], generation_time_ms: int,
                    avg_rubric_validity: float) -> float:
    """
    difficulty = mean across questions of (1 - mean model accuracy on that question)
    We approximate: difficulty = 1 - mean(accuracy across participating models)
    validity = avg_rubric_validity from judge (0..1)
    speed = speed_bonus(generation_time_ms, 60s target)
    """
    if not participants:
        return 0.0
    accs = [p.get("accuracy", 0) or 0 for p in participants]
    mean_acc = sum(accs) / len(accs)
    difficulty = max(0.0, 1.0 - mean_acc)
    validity = max(0.0, min(1.0, avg_rubric_validity))
    sb = speed_bonus(generation_time_ms, target_seconds=60)
    return round(difficulty * validity * sb, 4)


def score_judge(primary_grades: dict, shadow_grades: dict, judge_time_ms: int) -> float:
    """Consistency = 1 - mean absolute score diff across questions / max_points.
    Speed bonus on judge time."""
    try:
        primary = {g["question_id"]: g for g in primary_grades.get("grades", [])}
        shadow  = {g["question_id"]: g for g in shadow_grades.get("grades", [])}
        diffs = []
        for qid, p in primary.items():
            s = shadow.get(qid, {})
            max_pts = max(1, p.get("max_points", 1))
            diff = abs((p.get("score", 0) or 0) - (s.get("score", 0) or 0)) / max_pts
            diffs.append(diff)
        consistency = 1.0 - (sum(diffs) / len(diffs) if diffs else 0)
    except Exception:
        consistency = 0.5
    sb = speed_bonus(judge_time_ms, target_seconds=60)
    return round(max(0.0, consistency) * sb, 4)


# ─────────────────────────────────────────────
# DB helpers (use a passed-in get_db_fn to avoid circular imports with main.py)
# ─────────────────────────────────────────────

def create_challenge(get_db_fn, user_id: int, title: str, theme: str,
                     paper_ids: list[int], participant_models: list[str]) -> int:
    if not paper_ids:
        raise ValueError("At least one paper required")
    if len(paper_ids) > 10:
        raise ValueError("Maximum 10 papers per challenge")
    if not participant_models:
        raise ValueError("At least one participant model required")
    for m in participant_models:
        if m not in SUPPORTED_MODELS:
            raise ValueError(f"Unsupported model: {m}")

    conn = get_db_fn()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO challenges (title, theme, kind, status, created_by) VALUES (?,?,?,?,?)",
                (title, theme, "manual", "pending", user_id),
            )
            cid = cur.lastrowid
            for pid in paper_ids:
                conn.execute(
                    "INSERT INTO challenge_papers (challenge_id, paper_id) VALUES (?,?)",
                    (cid, pid),
                )
            for m in participant_models:
                conn.execute(
                    "INSERT INTO model_participants (challenge_id, model_id, provider, status) VALUES (?,?,?,?)",
                    (cid, m, SUPPORTED_MODELS[m], "pending"),
                )
            conn.commit()
    finally:
        conn.close()
    return cid


def _load_papers_b64(conn: sqlite3.Connection, challenge_id: int,
                     papers_dir: Path) -> tuple[list[dict], list[dict]]:
    """Returns (papers_b64_list, papers_meta_list)."""
    rows = conn.execute(
        """SELECT p.id, p.filename, p.disk_filename
           FROM papers p JOIN challenge_papers cp ON cp.paper_id = p.id
           WHERE cp.challenge_id=? ORDER BY p.id""",
        (challenge_id,),
    ).fetchall()
    papers_b64: list[dict] = []
    papers_meta: list[dict] = []
    for r in rows:
        path = papers_dir / (r["disk_filename"] or f"{r['filename']}.pdf")
        if not path.exists():
            logger.error("PDF missing on disk: %s", path)
            continue
        b64 = base64.b64encode(path.read_bytes()).decode()
        papers_b64.append({"id": r["id"], "filename": r["filename"], "b64": b64})
        papers_meta.append({"id": r["id"], "filename": r["filename"]})
    return papers_b64, papers_meta


def run_challenge(get_db_fn, challenge_id: int, papers_dir: Path,
                  vault_dir: Path) -> None:
    """Orchestrate a full challenge run. Intended to be invoked on a background thread.
    Updates challenge status as it progresses; on any exception, marks 'failed' and
    records the error."""
    conn = get_db_fn()
    try:
        challenge_row = conn.execute(
            "SELECT * FROM challenges WHERE id=?", (challenge_id,)
        ).fetchone()
        if not challenge_row:
            logger.error("run_challenge: challenge %s not found", challenge_id)
            return
        challenge = dict(challenge_row)

        with conn:
            conn.execute(
                "UPDATE challenges SET status='running', started_at=datetime('now') WHERE id=?",
                (challenge_id,),
            )
            conn.commit()

        # 1. Load papers
        papers_b64, papers_meta = _load_papers_b64(conn, challenge_id, papers_dir)
        if not papers_b64:
            raise RuntimeError("No valid papers for challenge")

        # 2. Generator agent
        gen_skill = get_active_skill(conn, "generator")
        rubric, gen_ms = run_generator_agent(papers_b64, challenge.get("theme") or "", gen_skill)

        with conn:
            conn.execute(
                """INSERT INTO challenge_rubrics
                   (challenge_id, rubric_json, generation_time_ms, generator_skill_id)
                   VALUES (?,?,?,?)""",
                (challenge_id, json.dumps(rubric), gen_ms, gen_skill["id"]),
            )
            conn.execute(
                "UPDATE challenges SET generator_skill_id=? WHERE id=?",
                (gen_skill["id"], challenge_id),
            )
            conn.commit()

        # 3. Participant models
        participant_rows = conn.execute(
            "SELECT * FROM model_participants WHERE challenge_id=?",
            (challenge_id,),
        ).fetchall()

        judge_skill = get_active_skill(conn, "judge")

        for mp_row in participant_rows:
            mp = dict(mp_row)
            model_id = mp["model_id"]
            try:
                with conn:
                    conn.execute(
                        "UPDATE model_participants SET status='answering' WHERE id=?",
                        (mp["id"],),
                    )
                    conn.commit()

                answers, answer_ms = run_participant_model(model_id, rubric, papers_b64)

                with conn:
                    conn.execute(
                        "UPDATE model_participants SET status='answered', answer_json=?, answer_time_ms=? WHERE id=?",
                        (json.dumps(answers), answer_ms, mp["id"]),
                    )
                    conn.commit()

                # 4. Judge this model
                grades, judge_ms = run_judge_agent(rubric, answers, judge_skill)

                run_score = score_model_run(grades, answer_ms)

                with conn:
                    conn.execute(
                        """UPDATE model_participants
                           SET grade_json=?, judge_time_ms=?,
                               accuracy=?, speed_bonus=?, total_score=?,
                               status='graded'
                           WHERE id=?""",
                        (
                            json.dumps(grades), judge_ms,
                            run_score["accuracy"], run_score["speed_bonus"], run_score["total_score"],
                            mp["id"],
                        ),
                    )
                    conn.commit()

            except Exception as e:
                logger.error("Participant %s failed: %s\n%s", model_id, e, traceback.format_exc())
                with conn:
                    conn.execute(
                        "UPDATE model_participants SET status='failed', error_message=? WHERE id=?",
                        (str(e)[:500], mp["id"]),
                    )
                    conn.commit()

        # 5. Score the generator and judge
        graded_participants = [
            dict(r) for r in conn.execute(
                "SELECT * FROM model_participants WHERE challenge_id=? AND status='graded'",
                (challenge_id,),
            ).fetchall()
        ]
        # Average rubric validity across all graded participants
        validity_values: list[float] = []
        for p in graded_participants:
            try:
                g = json.loads(p.get("grade_json") or "{}")
                v = g.get("avg_rubric_validity")
                if v is not None:
                    validity_values.append(float(v))
            except Exception:
                pass
        avg_validity = sum(validity_values) / len(validity_values) if validity_values else 0.5

        gen_score = score_generator(graded_participants, gen_ms, avg_validity)

        # Judge score: use a shadow regrade on the first successful participant
        judge_score_val = 0.0
        if graded_participants:
            first = graded_participants[0]
            try:
                primary_grades = json.loads(first.get("grade_json") or "{}")
                primary_answers = json.loads(first.get("answer_json") or "{}")
                shadow = shadow_regrade(rubric, primary_answers, judge_skill)
                judge_time = int(first.get("judge_time_ms") or 0)
                judge_score_val = score_judge(primary_grades, shadow, judge_time)
            except Exception as e:
                logger.error("Shadow regrade failed: %s", e)

        with conn:
            conn.execute(
                """UPDATE challenges
                   SET status='complete', completed_at=datetime('now'),
                       generator_score=?, judge_score=?, judge_skill_id=?
                   WHERE id=?""",
                (gen_score, judge_score_val, judge_skill["id"], challenge_id),
            )
            conn.commit()

        # 6. Update skill running averages
        record_skill_performance(conn, gen_skill["id"], gen_score)
        record_skill_performance(conn, judge_skill["id"], judge_score_val)

        # 7. Refresh leaderboard cache
        refresh_leaderboard(conn)

        # 8. Write Obsidian notes
        challenge_row = conn.execute(
            "SELECT * FROM challenges WHERE id=?", (challenge_id,)
        ).fetchone()
        try:
            write_challenge_note(
                vault_dir,
                dict(challenge_row),
                rubric,
                graded_participants,
                papers_meta,
            )
            write_skill_note(
                vault_dir, "generator",
                get_active_skill(conn, "generator"),
                list_skill_versions(conn, "generator"),
            )
            write_skill_note(
                vault_dir, "judge",
                get_active_skill(conn, "judge"),
                list_skill_versions(conn, "judge"),
            )
        except Exception as e:
            logger.error("Obsidian write failed: %s", e)

        logger.info("Challenge %s complete: gen=%s judge=%s", challenge_id, gen_score, judge_score_val)

    except Exception as e:
        logger.error("run_challenge failed: %s\n%s", e, traceback.format_exc())
        try:
            with conn:
                conn.execute(
                    "UPDATE challenges SET status='failed', error_message=?, completed_at=datetime('now') WHERE id=?",
                    (str(e)[:500], challenge_id),
                )
                conn.commit()
        except Exception:
            pass
    finally:
        conn.close()


def run_challenge_async(get_db_fn, challenge_id: int, papers_dir: Path,
                        vault_dir: Path) -> None:
    """Fire-and-forget: run a challenge on a daemon thread so the HTTP handler returns fast."""
    t = threading.Thread(
        target=run_challenge,
        args=(get_db_fn, challenge_id, papers_dir, vault_dir),
        daemon=True,
        name=f"challenge-{challenge_id}",
    )
    t.start()


# ─────────────────────────────────────────────
# Leaderboard
# ─────────────────────────────────────────────

def refresh_leaderboard(conn: sqlite3.Connection) -> None:
    """Recompute leaderboard_cache from model_participants."""
    rows = conn.execute(
        """SELECT model_id, provider,
                  COUNT(*) AS total_challenges,
                  SUM(total_score) AS cumulative_score,
                  AVG(accuracy) AS avg_accuracy,
                  AVG(speed_bonus) AS avg_speed_bonus
           FROM model_participants
           WHERE status='graded'
           GROUP BY model_id, provider"""
    ).fetchall()

    with conn:
        conn.execute("DELETE FROM leaderboard_cache")
        for r in rows:
            conn.execute(
                """INSERT INTO leaderboard_cache
                   (model_id, provider, total_challenges, cumulative_score,
                    avg_accuracy, avg_speed_bonus, last_updated)
                   VALUES (?,?,?,?,?,?,datetime('now'))""",
                (
                    r["model_id"], r["provider"], r["total_challenges"],
                    r["cumulative_score"] or 0,
                    r["avg_accuracy"] or 0,
                    r["avg_speed_bonus"] or 0,
                ),
            )
        conn.commit()
