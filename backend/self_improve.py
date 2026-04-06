"""Agent self-improvement loop. After enough challenges accumulate, a
meta-Claude call analyzes recent performance and proposes a modified
prompt. The new version is deployed immediately and monitored; if it
underperforms the previous version, an automatic rollback fires.

Inspired by karpathy/autoresearch: a single mutable file (the agent prompt)
iterated on autonomously. Each iteration: review → propose → deploy → monitor.
"""

import json
import logging
import os
import sqlite3
from pathlib import Path

from .helpers import call_anthropic
from .skills import get_active_skill, list_skill_versions, record_skill_performance
from .obsidian import write_skill_note

logger = logging.getLogger("rubricgen")

IMPROVE_AFTER_N = int(os.environ.get("SKILL_IMPROVE_AFTER_N", "5"))
ROLLBACK_AFTER_M = int(os.environ.get("SKILL_ROLLBACK_AFTER_M", "3"))
ROLLBACK_THRESHOLD = 0.8  # new version must reach 80% of previous avg_performance

# ─────────────────────────────────────────────
# Context gathering
# ─────────────────────────────────────────────

def _gather_generator_context(conn: sqlite3.Connection, skill_id: int,
                               limit: int = 5) -> dict:
    """Collect recent challenge data for the generator skill."""
    challenges = conn.execute(
        """SELECT c.id, c.theme, c.generator_score, c.completed_at,
                  cr.rubric_json, cr.generation_time_ms
           FROM challenges c
           JOIN challenge_rubrics cr ON cr.challenge_id = c.id
           WHERE cr.generator_skill_id=? AND c.status='complete' AND c.kind='daily'
           ORDER BY c.completed_at DESC LIMIT ?""",
        (skill_id, limit),
    ).fetchall()

    context_items = []
    for ch in challenges:
        rubric = {}
        try:
            rubric = json.loads(ch["rubric_json"] or "{}")
        except Exception:
            pass

        # Per-question analysis: which were too easy, too hard, unverifiable?
        participants = conn.execute(
            """SELECT grade_json, accuracy FROM model_participants
               WHERE challenge_id=? AND status='graded'""",
            (ch["id"],),
        ).fetchall()

        question_analysis = []
        questions = rubric.get("questions", [])
        for q in questions:
            qid = q.get("id", "")
            scores_per_model = []
            validity_per_model = []
            for p in participants:
                try:
                    grades = json.loads(p["grade_json"] or "{}")
                    for g in grades.get("grades", []):
                        if g.get("question_id") == qid:
                            scores_per_model.append(g.get("score", 0) / max(1, g.get("max_points", 1)))
                            validity_per_model.append(g.get("rubric_validity", 1))
                except Exception:
                    pass

            avg_score = sum(scores_per_model) / len(scores_per_model) if scores_per_model else 0
            avg_validity = sum(validity_per_model) / len(validity_per_model) if validity_per_model else 1

            classification = "discriminating"
            if avg_score > 0.9:
                classification = "too_easy"
            elif avg_score < 0.1:
                classification = "too_hard"
            if avg_validity < 0.5:
                classification = "unverifiable"

            question_analysis.append({
                "id": qid,
                "domain": q.get("domain", ""),
                "question_preview": q.get("question", "")[:100],
                "avg_model_score": round(avg_score, 3),
                "avg_validity": round(avg_validity, 3),
                "classification": classification,
            })

        context_items.append({
            "challenge_id": ch["id"],
            "theme": ch["theme"],
            "generator_score": ch["generator_score"],
            "generation_time_ms": ch["generation_time_ms"],
            "question_count": len(questions),
            "question_analysis": question_analysis,
        })

    # Aggregate stats
    scores = [c["generator_score"] or 0 for c in context_items]
    too_easy = sum(1 for c in context_items for q in c["question_analysis"] if q["classification"] == "too_easy")
    too_hard = sum(1 for c in context_items for q in c["question_analysis"] if q["classification"] == "too_hard")
    unverifiable = sum(1 for c in context_items for q in c["question_analysis"] if q["classification"] == "unverifiable")
    discriminating = sum(1 for c in context_items for q in c["question_analysis"] if q["classification"] == "discriminating")
    total_qs = sum(c["question_count"] for c in context_items)

    return {
        "challenges_analyzed": len(context_items),
        "avg_generator_score": round(sum(scores) / len(scores), 4) if scores else 0,
        "total_questions": total_qs,
        "too_easy_count": too_easy,
        "too_hard_count": too_hard,
        "unverifiable_count": unverifiable,
        "discriminating_count": discriminating,
        "details": context_items,
    }


def _gather_judge_context(conn: sqlite3.Connection, skill_id: int,
                           limit: int = 5) -> dict:
    """Collect recent judge performance data."""
    challenges = conn.execute(
        """SELECT c.id, c.theme, c.judge_score, c.completed_at
           FROM challenges c
           WHERE c.judge_skill_id=? AND c.status='complete' AND c.kind='daily'
           ORDER BY c.completed_at DESC LIMIT ?""",
        (skill_id, limit),
    ).fetchall()

    context_items = []
    for ch in challenges:
        participants = conn.execute(
            """SELECT model_id, grade_json, judge_time_ms
               FROM model_participants
               WHERE challenge_id=? AND status='graded'""",
            (ch["id"],),
        ).fetchall()

        # Look for inconsistencies in grading
        grade_patterns = []
        for p in participants:
            try:
                grades = json.loads(p["grade_json"] or "{}")
                for g in grades.get("grades", []):
                    grade_patterns.append({
                        "question_id": g.get("question_id"),
                        "score": g.get("score", 0),
                        "max_points": g.get("max_points", 1),
                        "reasoning_length": len(g.get("reasoning", "")),
                    })
            except Exception:
                pass

        context_items.append({
            "challenge_id": ch["id"],
            "theme": ch["theme"],
            "judge_score": ch["judge_score"],
            "participant_count": len(participants),
            "total_grades": len(grade_patterns),
        })

    scores = [c["judge_score"] or 0 for c in context_items]
    return {
        "challenges_analyzed": len(context_items),
        "avg_judge_score": round(sum(scores) / len(scores), 4) if scores else 0,
        "details": context_items,
    }


# ─────────────────────────────────────────────
# Improvement logic
# ─────────────────────────────────────────────

def should_improve(conn: sqlite3.Connection, agent_type: str) -> bool:
    """Check if the current active skill has been used for >= IMPROVE_AFTER_N challenges."""
    skill = get_active_skill(conn, agent_type)
    return skill.get("times_used", 0) >= IMPROVE_AFTER_N and IMPROVE_AFTER_N > 0


META_PROMPT = """You are a prompt engineer improving an AI agent's system prompt based on
real performance data from a clinical research model-benchmarking platform.

AGENT TYPE: {agent_type}

CURRENT PROMPT (version {version}):
{current_prompt}

RECENT PERFORMANCE DATA:
{context_json}

INSTRUCTIONS:
1. Analyze which aspects of the current prompt produce high-quality results.
2. Identify weaknesses based on the performance data:
   - For generators: questions classified as "too_easy" (all models got right),
     "too_hard" (none got right), or "unverifiable" (judge flagged as not verifiable
     from the paper) are problems. "discriminating" questions are good.
   - For judges: low consistency scores indicate the grading criteria in the prompt
     are ambiguous. High scores indicate clear, reproducible grading.
3. Propose a REVISED version of the prompt that:
   - Preserves what works well (don't change things that are performing)
   - Addresses identified weaknesses with SPECIFIC instruction changes
   - Maintains the EXACT SAME JSON output format (do not change the schema)
   - Is a COMPLETE replacement prompt (not a diff or patch)
4. If performance is already excellent (avg score > 0.8), make only minor tweaks.
   Don't fix what isn't broken.

Return ONLY the revised prompt text. No preamble, no explanation, no markdown fences."""


def propose_improved_skill(conn: sqlite3.Connection, agent_type: str) -> str | None:
    """Call meta-Claude to propose a modified skill prompt.
    Returns the new prompt text, or None on failure."""
    skill = get_active_skill(conn, agent_type)

    if agent_type == "generator":
        context = _gather_generator_context(conn, skill["id"])
    else:
        context = _gather_judge_context(conn, skill["id"])

    prompt = META_PROMPT.format(
        agent_type=agent_type,
        version=skill["version"],
        current_prompt=skill["prompt_text"],
        context_json=json.dumps(context, indent=2),
    )

    try:
        new_prompt = call_anthropic(
            messages=[{"role": "user", "content": prompt}],
            system="You are an expert prompt engineer. Return only the revised prompt text.",
            max_tokens=4096,
        )
        new_prompt = new_prompt.strip()
        if len(new_prompt) < 100:
            logger.warning("Meta-Claude returned suspiciously short prompt (%d chars)", len(new_prompt))
            return None
        return new_prompt
    except Exception as e:
        logger.error("propose_improved_skill failed: %s", e)
        return None


def deploy_new_skill(conn: sqlite3.Connection, agent_type: str,
                     new_prompt: str) -> int:
    """Insert a new skill version and set it as active. Deactivates previous."""
    versions = list_skill_versions(conn, agent_type)
    max_version = max((v["version"] for v in versions), default=0)
    new_version = max_version + 1

    with conn:
        # Deactivate all existing versions for this agent
        conn.execute(
            "UPDATE agent_skills SET active=0 WHERE agent_type=?",
            (agent_type,),
        )
        # Insert new version as active
        cur = conn.execute(
            """INSERT INTO agent_skills (agent_type, version, prompt_text, active)
               VALUES (?,?,?,1)""",
            (agent_type, new_version, new_prompt),
        )
        conn.commit()

    new_id = cur.lastrowid
    logger.info(
        "Deployed new %s skill v%d (id=%d). Previous versions deactivated.",
        agent_type, new_version, new_id,
    )
    return new_id


def check_rollback(conn: sqlite3.Connection, agent_type: str) -> dict:
    """Check if the current active skill should be rolled back.
    Returns {action: 'keep'|'rollback'|'wait', reason: str}."""
    versions = list_skill_versions(conn, agent_type)
    if len(versions) < 2:
        return {"action": "keep", "reason": "only one version exists"}

    current = next((v for v in versions if v["active"]), None)
    if not current:
        return {"action": "keep", "reason": "no active version"}

    # Has the current version been used enough to evaluate?
    if current["times_used"] < ROLLBACK_AFTER_M:
        return {"action": "wait", "reason": f"need {ROLLBACK_AFTER_M - current['times_used']} more challenges before evaluation"}

    # Find the previous version (highest version that isn't current)
    previous = next((v for v in versions if v["version"] < current["version"] and v["times_used"] > 0), None)
    if not previous:
        return {"action": "keep", "reason": "no previous version to compare against"}

    # Compare performance
    if previous["avg_performance"] <= 0:
        return {"action": "keep", "reason": "previous version has no performance data"}

    ratio = current["avg_performance"] / previous["avg_performance"] if previous["avg_performance"] > 0 else 1.0

    if ratio < ROLLBACK_THRESHOLD:
        return {
            "action": "rollback",
            "reason": f"v{current['version']} avg_performance={current['avg_performance']:.4f} is {ratio:.1%} of v{previous['version']} ({previous['avg_performance']:.4f}), below {ROLLBACK_THRESHOLD:.0%} threshold",
            "current_version": current["version"],
            "previous_version": previous["version"],
        }

    return {
        "action": "keep",
        "reason": f"v{current['version']} ({current['avg_performance']:.4f}) at {ratio:.1%} of v{previous['version']} ({previous['avg_performance']:.4f}), above threshold",
    }


def rollback_skill(conn: sqlite3.Connection, agent_type: str,
                   target_version: int) -> None:
    """Rollback to a specific version."""
    with conn:
        conn.execute("UPDATE agent_skills SET active=0 WHERE agent_type=?", (agent_type,))
        conn.execute(
            "UPDATE agent_skills SET active=1 WHERE agent_type=? AND version=?",
            (agent_type, target_version),
        )
        conn.commit()
    logger.info("Rolled back %s skill to v%d", agent_type, target_version)


# ─────────────────────────────────────────────
# Main entry point (called after daily challenge completes)
# ─────────────────────────────────────────────

def maybe_improve_after_challenge(get_db_fn, agent_type: str,
                                  vault_dir: Path) -> None:
    """Check if improvement or rollback is needed. Called after each daily challenge."""
    conn = get_db_fn()
    try:
        # Step 1: Check for rollback of recently deployed skill
        rb = check_rollback(conn, agent_type)
        if rb["action"] == "rollback":
            logger.info("ROLLBACK triggered for %s: %s", agent_type, rb["reason"])
            rollback_skill(conn, agent_type, rb["previous_version"])
            _write_vault(conn, agent_type, vault_dir)
            return
        elif rb["action"] == "wait":
            logger.info("Skill %s evaluation pending: %s", agent_type, rb["reason"])
            return

        # Step 2: Check if improvement is due
        if not should_improve(conn, agent_type):
            return

        logger.info("Triggering self-improvement for %s skill", agent_type)

        # Step 3: Propose new skill
        new_prompt = propose_improved_skill(conn, agent_type)
        if not new_prompt:
            logger.warning("No improvement proposed for %s — meta-Claude returned nothing", agent_type)
            return

        # Step 4: Deploy
        new_id = deploy_new_skill(conn, agent_type, new_prompt)
        logger.info("Deployed improved %s skill (id=%d). Monitoring begins.", agent_type, new_id)

        # Step 5: Write to Obsidian
        _write_vault(conn, agent_type, vault_dir)

    except Exception as e:
        logger.error("Self-improvement failed for %s: %s", agent_type, e, exc_info=True)
    finally:
        conn.close()


def _write_vault(conn: sqlite3.Connection, agent_type: str, vault_dir: Path) -> None:
    """Update the Obsidian skill note."""
    try:
        active = get_active_skill(conn, agent_type)
        versions = list_skill_versions(conn, agent_type)
        write_skill_note(vault_dir, agent_type, active, versions)
    except Exception as e:
        logger.error("Failed to write skill vault note: %s", e)


def get_improvement_status(conn: sqlite3.Connection, agent_type: str) -> dict:
    """Return current improvement status for the admin panel."""
    skill = get_active_skill(conn, agent_type)
    versions = list_skill_versions(conn, agent_type)
    rb = check_rollback(conn, agent_type)

    return {
        "agent_type": agent_type,
        "active_version": skill["version"],
        "active_skill_id": skill["id"],
        "total_versions": len(versions),
        "times_used_current": next((v["times_used"] for v in versions if v["active"]), 0),
        "avg_performance_current": next((v["avg_performance"] for v in versions if v["active"]), 0),
        "improve_threshold": IMPROVE_AFTER_N,
        "rollback_threshold": ROLLBACK_AFTER_M,
        "rollback_status": rb,
        "improvement_due": should_improve(conn, agent_type),
        "versions": versions,
    }
