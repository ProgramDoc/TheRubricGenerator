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
from .agents.participants import run_participant_model
from .obsidian import write_challenge_note, write_skill_note
from .skills import (
    get_active_skill, list_skill_versions, record_skill_performance,
)

logger = logging.getLogger("rubricgen")

# Frontier models — updated April 2026
# Each entry maps model_id → {provider, caller, cost_credits, ...}
SUPPORTED_MODELS = {
    "claude-opus-4-20250514":   {"provider": "anthropic", "caller": "anthropic",     "cost_credits": 15},
    "gpt-5.4":                  {"provider": "openai",    "caller": "openai_compat",  "cost_credits": 12, "base_url": "https://api.openai.com/v1"},
    "gemini-3.1":               {"provider": "google",    "caller": "gemini",          "cost_credits": 8},
    "gemini-3.1-pro":           {"provider": "google",    "caller": "gemini",          "cost_credits": 10},
    "kimi-k2-thinking":         {"provider": "moonshot",  "caller": "openai_compat",  "cost_credits": 8,  "base_url": "https://api.moonshot.cn/v1"},
    # Legacy models (still supported for existing challenges)
    "gpt-4o":                   {"provider": "openai",    "caller": "openai_compat",  "cost_credits": 10, "base_url": "https://api.openai.com/v1"},
    "claude-sonnet-4-20250514": {"provider": "anthropic", "caller": "anthropic",      "cost_credits": 12},
    "gemini-2.5-pro":           {"provider": "google",    "caller": "gemini",          "cost_credits": 8},
}

# Convenience: map model_id → provider string (for backward compat)
def provider_for_model(model_id: str) -> str:
    m = SUPPORTED_MODELS.get(model_id)
    return m["provider"] if m else "unknown"

# ─────────────────────────────────────────────
# Points system
# ─────────────────────────────────────────────
# Individual test points per correct answer
INDIVIDUAL_POINTS = {
    "easy_breezy":  1,
    "minor_league": 2,
    "professional": 5,
    "jedi":         10,
}

# Daily AI Researcher Challenge: 10x Jedi rate = 100 pts per correct answer
DAILY_POINTS_PER_CORRECT = 100

# Daily bonus: 2 additional Jedi questions worth 20 pts each if all 10 base correct
DAILY_BONUS_QUESTION_COUNT = 2
DAILY_BONUS_POINTS_EACH = 20

# Daily test composition: 2 easy, 2 minor, 4 professional, 2 jedi = 10 questions
DAILY_COMPOSITION = {
    "easy_breezy": 2,
    "minor_league": 2,
    "professional": 4,
    "jedi": 2,
}

# Daily challenge pricing in credits
DAILY_PRICE_PER_CHALLENGE = 300   # $3
DAILY_PRICE_PER_WEEK = 1000       # $10


def calculate_points(difficulty: str | None, correct_count: int, total: int,
                     is_daily: bool = False, bonus_correct: int = 0) -> int:
    """Calculate points earned for a challenge run."""
    if is_daily:
        base = correct_count * DAILY_POINTS_PER_CORRECT
        bonus = bonus_correct * DAILY_BONUS_POINTS_EACH
        return base + bonus
    pts_per = INDIVIDUAL_POINTS.get(difficulty, 1)
    return correct_count * pts_per


# Phase 1.5: user-facing difficulty tiers for user-designed public tests.
# Difficulty is defined by the COGNITIVE COMPLEXITY of the questions, not
# the number of questions. All levels produce ~10 questions. The level
# steers the generator agent toward different kinds of reasoning:
#
#   easy_breezy  — simple extraction: field facts a structured data-extraction
#                  tool would capture (PICO, sample size, intervention name,
#                  primary outcome value, etc.)
#   minor_league — study classification: design type, taxonomy, research
#                  question type, allocation method, choice of comparator
#   professional — methodological appraisal: limitations, statistical
#                  approach, internal validity, risk of bias judgments,
#                  adequacy of outcome measurement
#   jedi         — adversarial expert appraisal: subtle methodological
#                  distinctions, edge cases in bias assessment, reconciling
#                  conflicting evidence, questions that discriminate expert
#                  from novice readers
#
# The Generator Agent's Claude skill will refine these definitions over
# successive iterations (Phase 3 self-improvement loop) based on which
# questions actually discriminate models at each tier.
DIFFICULTY_LEVELS = {
    "easy_breezy": {
        "label": "Easy Breezy",
        "hint": (
            "SIMPLE EXTRACTION. Ask questions that a structured data-extraction tool would answer: "
            "specific PICO elements, sample size, intervention name/dose, primary outcome name and value, "
            "comparator, setting, follow-up duration. Answers should be single facts directly stated in the paper. "
            "Avoid reasoning chains or interpretation."
        ),
    },
    "minor_league": {
        "label": "Minor League",
        "hint": (
            "STUDY CLASSIFICATION. Ask questions about study design, taxonomy, and methodological categorization: "
            "RCT vs observational, parallel vs crossover, superiority vs non-inferiority, prospective vs retrospective, "
            "research question type (etiologic/prognostic/diagnostic/interventional), allocation method, "
            "type of comparator, applicable risk-of-bias tool. Requires some inference from methods text but "
            "each answer is a defensible categorical judgment."
        ),
    },
    "professional": {
        "label": "Professional",
        "hint": (
            "METHODOLOGICAL APPRAISAL. Ask rigorous questions about quality and validity: "
            "adequacy of blinding, handling of missing data, statistical methods appropriateness, "
            "outcome measurement validity, threats to internal validity, conflicts of interest, "
            "reporting bias indicators, risk-of-bias domain judgments with justification. "
            "Requires multi-step reasoning and integration across intro/methods/results."
        ),
    },
    "jedi": {
        "label": "Jedi",
        "hint": (
            "ADVERSARIAL EXPERT APPRAISAL. Ask questions that discriminate expert from novice: "
            "subtle methodological distinctions (per-protocol vs ITT implications, competing risks, "
            "regression to the mean, immortal time bias), reconciling conflicting results across tables "
            "and text, detecting underreported limitations, applying primacy-gate rules to ambiguous designs, "
            "identifying when a stated design does not match the methodology-driven classification. "
            "A novice would miss these; a methodologist would catch them."
        ),
    },
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
                     paper_ids: list[int], participant_models: list[str],
                     project_id: int | None = None,
                     visibility: str = "private",
                     difficulty: str | None = None,
                     registered_model_id: int | None = None,
                     kind: str = "manual") -> int:
    if kind not in ("manual", "daily", "dry_run"):
        raise ValueError(f"kind must be 'manual', 'daily', or 'dry_run', got {kind!r}")
    if not paper_ids:
        raise ValueError("At least one paper required")
    if len(paper_ids) > 10:
        raise ValueError("Maximum 10 papers per challenge")
    if not participant_models:
        raise ValueError("At least one participant model required")
    for m in participant_models:
        if m not in SUPPORTED_MODELS:
            raise ValueError(f"Unsupported model: {m}")

    if visibility not in ("private", "public"):
        raise ValueError("visibility must be 'private' or 'public'")
    if difficulty is not None and difficulty not in DIFFICULTY_LEVELS:
        raise ValueError(f"Unknown difficulty '{difficulty}'")

    # Public publishing rules: difficulty + registered_model_id required
    if visibility == "public":
        if not difficulty:
            raise ValueError("Public challenges must specify a difficulty level")
        if not registered_model_id:
            raise ValueError("Public challenges must specify a registered model")

    conn = get_db_fn()
    try:
        # Validate project ownership if set
        if project_id is not None:
            row = conn.execute(
                "SELECT user_id FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            if not row or row["user_id"] != user_id:
                raise ValueError("Project not found or not owned by user")

        # Validate registered model exists and user is a member
        if registered_model_id is not None:
            row = conn.execute(
                "SELECT id FROM registered_models WHERE id=?", (registered_model_id,)
            ).fetchone()
            if not row:
                raise ValueError("Registered model not found")
            member = conn.execute(
                "SELECT 1 FROM registered_model_members WHERE registered_model_id=? AND user_id=?",
                (registered_model_id, user_id),
            ).fetchone()
            if not member:
                raise ValueError("You must be a member of the registered model to use it")

        with conn:
            cur = conn.execute(
                """INSERT INTO challenges
                   (title, theme, kind, status, created_by, project_id, visibility, difficulty, registered_model_id)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (title, theme, kind, "pending", user_id,
                 project_id, visibility, difficulty, registered_model_id),
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
                    (cid, m, SUPPORTED_MODELS[m]["provider"], "pending"),
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
        is_daily = (challenge.get("kind") == "daily")
        rubric, gen_ms = run_generator_agent(
            papers_b64,
            challenge.get("theme") or "",
            gen_skill,
            difficulty=challenge.get("difficulty"),
            daily_composition=DAILY_COMPOSITION if is_daily else None,
        )

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

                # Calculate points
                correct_count = sum(
                    1 for g in grades.get("grades", [])
                    if g.get("score", 0) >= g.get("max_points", 1)
                )
                total_q = len(grades.get("grades", []))
                bonus_correct = 0

                # Daily bonus round: if all base questions correct, generate 2 more Jedi questions
                if is_daily and correct_count == total_q and total_q > 0:
                    logger.info("Model %s got %d/%d — triggering bonus round", model_id, correct_count, total_q)
                    try:
                        bonus_rubric, _ = run_generator_agent(
                            papers_b64,
                            challenge.get("theme") or "",
                            gen_skill,
                            difficulty="jedi",
                        )
                        # Override to exactly 2 questions
                        bonus_qs = (bonus_rubric.get("questions") or [])[:DAILY_BONUS_QUESTION_COUNT]
                        if bonus_qs:
                            bonus_rubric_trimmed = dict(bonus_rubric, questions=bonus_qs)
                            bonus_answers, _ = run_participant_model(model_id, bonus_rubric_trimmed, papers_b64)
                            bonus_grades, _ = run_judge_agent(bonus_rubric_trimmed, bonus_answers, judge_skill)
                            bonus_correct = sum(
                                1 for g in bonus_grades.get("grades", [])
                                if g.get("score", 0) >= g.get("max_points", 1)
                            )
                            # Append bonus to grades
                            grades["bonus_grades"] = bonus_grades.get("grades", [])
                            grades["bonus_correct"] = bonus_correct
                    except Exception as e:
                        logger.error("Bonus round failed for %s: %s", model_id, e)

                pts = calculate_points(
                    challenge.get("difficulty"), correct_count, total_q,
                    is_daily=is_daily, bonus_correct=bonus_correct,
                )

                with conn:
                    conn.execute(
                        """UPDATE model_participants
                           SET grade_json=?, judge_time_ms=?,
                               accuracy=?, speed_bonus=?, total_score=?,
                               points=?, status='graded'
                           WHERE id=?""",
                        (
                            json.dumps(grades), judge_ms,
                            run_score["accuracy"], run_score["speed_bonus"], run_score["total_score"],
                            pts, mp["id"],
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

        # 9. Self-improvement experiment loop (daily challenges only)
        #    Uses the daily challenge's papers as the test bed — no extra PubMed fetch.
        #    Runs up to EXPERIMENT_BUDGET experiments per agent (~$5 total).
        if is_daily:
            try:
                from .self_improve import run_experiment_loop
                # Use first 2 papers from this challenge for lightweight eval
                eval_papers = papers_b64[:2] if papers_b64 else []
                eval_theme = challenge.get("theme") or ""
                if eval_papers:
                    logger.info("Starting self-improvement experiment loop")
                    run_experiment_loop(get_db_fn, "generator", eval_papers, eval_theme, vault_dir)
                    run_experiment_loop(get_db_fn, "judge", eval_papers, eval_theme, vault_dir)
            except Exception as e:
                logger.error("Self-improvement experiment loop failed: %s", e)

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
    """Recompute leaderboard_cache. Overall leaderboard = total_points from
    ALL public individual tests + daily challenges. Daily leaderboard =
    daily_points from kind='daily' only."""

    # Overall: all graded challenges that earn points (daily + public individual)
    rows = conn.execute(
        """SELECT mp.model_id, mp.provider,
                  COUNT(*) AS total_challenges,
                  SUM(mp.total_score) AS cumulative_score,
                  AVG(mp.accuracy) AS avg_accuracy,
                  AVG(mp.speed_bonus) AS avg_speed_bonus,
                  SUM(mp.points) AS total_points,
                  SUM(CASE WHEN c.kind='daily' THEN mp.points ELSE 0 END) AS daily_points
           FROM model_participants mp
           JOIN challenges c ON c.id = mp.challenge_id
           WHERE mp.status='graded' AND (c.kind = 'daily' OR c.visibility = 'public')
           GROUP BY mp.model_id, mp.provider"""
    ).fetchall()

    # Daily streak: count consecutive days a model has held #1 rank
    # Get the most recent daily challenges in order
    daily_results = conn.execute(
        """SELECT c.id, c.completed_at, mp.model_id, mp.points
           FROM model_participants mp
           JOIN challenges c ON c.id = mp.challenge_id
           WHERE mp.status='graded' AND c.kind='daily' AND c.status='complete'
           ORDER BY c.completed_at DESC"""
    ).fetchall()

    # Group by challenge to find daily winners
    from collections import defaultdict
    challenge_winners: dict[int, tuple[str, int]] = {}  # challenge_id -> (model_id, points)
    for r in daily_results:
        cid = r["id"]
        if cid not in challenge_winners or r["points"] > challenge_winners[cid][1]:
            challenge_winners[cid] = (r["model_id"], r["points"])

    # Get ordered list of winners (most recent first)
    ordered_challenges = sorted(challenge_winners.keys(), reverse=True)
    winner_sequence = [challenge_winners[cid][0] for cid in ordered_challenges]

    # Calculate streaks per model
    streaks: dict[str, int] = defaultdict(int)
    if winner_sequence:
        current_winner = winner_sequence[0]
        streak = 1
        for w in winner_sequence[1:]:
            if w == current_winner:
                streak += 1
            else:
                break
        streaks[current_winner] = streak

    # Previous rank for movement calculation
    prev_ranks: dict[str, int] = {}
    prev = conn.execute(
        "SELECT model_id, RANK() OVER (ORDER BY total_points DESC) AS rank FROM leaderboard_cache"
    ).fetchall()
    for r in prev:
        prev_ranks[r["model_id"]] = r["rank"]

    with conn:
        conn.execute("DELETE FROM leaderboard_cache")
        # Sort by total_points for new rank
        sorted_rows = sorted(rows, key=lambda r: -(r["total_points"] or 0))
        for i, r in enumerate(sorted_rows):
            new_rank = i + 1
            old_rank = prev_ranks.get(r["model_id"], new_rank)
            rank_change = old_rank - new_rank  # positive = moved up
            conn.execute(
                """INSERT INTO leaderboard_cache
                   (model_id, provider, total_challenges, cumulative_score,
                    avg_accuracy, avg_speed_bonus, total_points, daily_points,
                    daily_streak, daily_rank_change, last_updated)
                   VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
                (
                    r["model_id"], r["provider"], r["total_challenges"],
                    r["cumulative_score"] or 0,
                    r["avg_accuracy"] or 0,
                    r["avg_speed_bonus"] or 0,
                    r["total_points"] or 0,
                    r["daily_points"] or 0,
                    streaks.get(r["model_id"], 0),
                    rank_change,
                ),
            )
        conn.commit()


def get_daily_results(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    """Get recent daily challenge results for the Daily AI Researcher Challenge leaderboard.
    Each row = one day's results with winner, 2nd, 3rd place."""
    challenges = conn.execute(
        """SELECT c.id, c.title, c.theme, c.completed_at, c.generator_score,
                  cr.rubric_json
           FROM challenges c
           LEFT JOIN challenge_rubrics cr ON cr.challenge_id = c.id
           WHERE c.kind='daily' AND c.status='complete'
           ORDER BY c.completed_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()

    results = []
    for ch in challenges:
        participants = conn.execute(
            """SELECT model_id, provider, points, accuracy, answer_time_ms,
                      judge_time_ms, grade_json, answer_json
               FROM model_participants
               WHERE challenge_id=? AND status='graded'
               ORDER BY points DESC""",
            (ch["id"],),
        ).fetchall()
        results.append({
            "challenge_id": ch["id"],
            "title": ch["title"],
            "theme": ch["theme"],
            "completed_at": ch["completed_at"],
            "participants": [dict(p) for p in participants],
        })
    return results
