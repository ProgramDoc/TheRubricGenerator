"""Agent self-improvement loop — autoresearch methodology.

Modeled after karpathy/autoresearch:
- The agent prompt is the "train.py" — the single file being iterated on
- After each daily challenge, enter an EXPERIMENT LOOP
- Each experiment: propose a modification → lightweight eval → keep or discard
- Binary keep/discard: did the metric improve? Keep. Did it not? Revert.
- Log every experiment (like results.tsv)
- Simplicity criterion: simpler prompts that achieve equal results are preferred
- Run up to EXPERIMENT_BUDGET experiments per cycle

Unlike autoresearch's 5-minute GPU runs, our experiments cost API money.
We use a lightweight eval (2 papers, 1 model, 3 questions) instead of
a full challenge to keep costs ~$1/experiment.

See: https://github.com/karpathy/autoresearch
"""

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from .helpers import call_anthropic, call_gemini, parse_json_response, time_ms
from .skills import get_active_skill, list_skill_versions
from .obsidian import (
    write_agent_skill_file,
    write_agent_history_file,
    write_experiment_note,
)

logger = logging.getLogger("rubricgen")


# ─────────────────────────────────────────────
# Seed program.md files (Karpathy autoresearch meta-learner control plane)
# Written once at startup; human-edited thereafter; re-read on every run.
# ─────────────────────────────────────────────

GENERATOR_PROGRAM_MD = """# Generator Skill — Meta-Learner Program

This file is the **human-editable control plane** for the rubric-generator
autoresearch loop. It is re-read on every self-improvement experiment and
injected into the meta-Claude prompt as PROGRAM INSTRUCTIONS. Edit freely.

## Objective
Maximize the quality of benchmark rubrics generated from clinical research
PDFs, measured by: verifiability, specificity, discrimination, clarity.

## Current hypotheses (2026-04)
1. More concrete few-shot examples should improve specificity.
2. Over-long domain guidance degrades focus on numerical extraction.
3. Explicit partial-credit templates in scoring_criteria reduce judge
   validity penalties downstream.

## Search directions (ordered by priority)
- Add/refine ONE concrete good-question exemplar per edit
- Tighten verbose domain descriptions where they repeat each other
- Introduce stricter language about numerical extraction and CI reporting
- Experiment with tighter token budgets on scoring_criteria to force clarity

## Do NOT
- Change the output JSON schema (breaking change for downstream parsers)
- Remove the 11-domain enum — downstream filters depend on the exact keys
- Remove the "exactly 10 questions" count for default challenges
- Make multiple unrelated edits in one proposal (one change at a time)

## Accept criterion
An edit is kept iff lightweight eval quality score strictly improves.
Ties go to the simpler prompt (autoresearch simplicity rule).

## History notes
Append manual observations here after reviewing experiment artifacts in
`experiments/`. The meta-learner will read these as additional context.
"""


JUDGE_PROGRAM_MD = """# Judge Skill — Meta-Learner Program

Human-editable control plane for the rubric-judge autoresearch loop.
Re-read on every self-improvement experiment.

## Objective
Maximize grading consistency and discrimination: the judge should score
clearly-correct answers high and clearly-wrong answers low, with stable
partial-credit decisions under re-grading.

## Current hypotheses (2026-04)
1. Explicit per-domain rubric-interpretation guidance reduces variance.
2. Examples of partial-credit decisions improve cross-run consistency.
3. Overly strict numerical language causes false zeros on rounding.

## Search directions
- Tighten per-domain guidance where variance is highest
- Add one worked-example partial-credit decision per edit
- Refine the rubric_validity flag language so flags are rare but reliable
- Experiment with simpler overall_comments instructions

## Do NOT
- Change the output JSON schema (grades[], total_score, max_score, etc.)
- Remove rubric_validity — downstream generator scoring depends on it
- Collapse per-domain guidance into one block (kills domain-specific signal)
- Make multiple unrelated edits in one proposal

## Accept criterion
An edit is kept iff the lightweight discrimination score strictly
improves (correct_answer_score - wrong_answer_score grows).
Ties go to the simpler prompt.

## History notes
Append observations here; the meta-learner will see them.
"""


SEARCH_STRATEGIST_PROGRAM_MD = """# Search Strategist Skill — Meta-Learner Program

Human-editable control plane for the search strategist autoresearch loop.
Re-read on every self-improvement experiment.

## Objective
Maximize search query recall and precision: the strategist should produce
Boolean queries that retrieve relevant papers while minimizing noise,
and should extract accurate PICO elements from research questions.

## Current hypotheses (2026-04)
1. Including both MeSH and free-text synonyms improves recall.
2. Explicit PICO extraction guidance reduces errors in complex questions.
3. Follow-up suggestions that are too generic reduce user engagement.

## Search directions
- Improve MeSH term selection accuracy
- Add guidance for handling multi-concept queries
- Refine follow-up question specificity

## Do NOT
- Change the output JSON schema (text, pico, search_query, follow_up_questions)
- Remove PICO extraction capability
- Make multiple unrelated edits in one proposal
"""


STATISTICIAN_PROGRAM_MD = """# Statistician Skill — Meta-Learner Program

Human-editable control plane for the statistician autoresearch loop.
Re-read on every self-improvement experiment.

## Objective
Maximize statistical rigor and practical utility: the statistician should
recommend appropriate methods, produce correct code, and identify genuine
statistical issues in published work.

## Current hypotheses (2026-04)
1. Method selection trees (design -> test) reduce inappropriate test recommendations.
2. Explicit assumption-checking guidance prevents overlooked violations.
3. Code output with inline comments improves user understanding.

## Search directions
- Improve method-design matching for complex designs (crossover, cluster RCTs)
- Add Bayesian analysis guidance alongside frequentist
- Refine critique severity calibration (avoid false alarms)
- Improve power analysis code templates

## Do NOT
- Change the output JSON schema
- Remove code generation capability
- Conflate statistical significance with clinical significance
- Make multiple unrelated edits in one proposal
"""


STUDY_APPRAISER_PROGRAM_MD = """# Study Appraiser Skill — Meta-Learner Program

Human-editable control plane for the study appraiser autoresearch loop.
Re-read on every self-improvement experiment.

## Objective
Maximize accuracy of quality assessments: the appraiser should correctly
select appraisal tools, justify domain-level judgments with evidence,
and produce actionable quality ratings.

## Current hypotheses (2026-04)
1. Forcing tool selection before assessment prevents tool misapplication.
2. Signaling question checklists improve domain judgment consistency.
3. Explicit direction-of-bias assessment adds clinical value.

## Search directions
- Improve study design recognition for hybrid designs
- Add guidance for appraising stepped-wedge and adaptive trials
- Refine GRADE certainty language for non-specialists
- Improve sensitivity to selective outcome reporting

## Do NOT
- Change the output JSON schema
- Remove domain-level justifications
- Allow overall judgments without domain-by-domain evidence
- Make multiple unrelated edits in one proposal
"""


HYPOTHESIS_GENERATOR_PROGRAM_MD = """# Hypothesis Generator Skill — Meta-Learner Program

Human-editable control plane for the hypothesis generator autoresearch loop.
Re-read on every self-improvement experiment.

## Objective
Maximize hypothesis novelty, testability, and scientific rigor: the generator
should produce hypotheses that are genuinely new, mechanistically grounded,
and feasible to test.

## Current hypotheses (2026-04)
1. Requiring explicit knowledge gap identification improves novelty.
2. Testability scoring with study design suggestions improves feasibility.
3. Cross-domain bridging hypotheses are more novel than incremental ones.

## Search directions
- Improve novelty assessment accuracy
- Add guidance for distinguishing correlation-based vs mechanism-based hypotheses
- Refine feasibility estimation
- Improve scholarly vs non-scholarly source classification

## Do NOT
- Change the output JSON schema
- Generate unfalsifiable hypotheses
- Remove novelty assessment
- Make multiple unrelated edits in one proposal
"""


LITERATURE_REVIEWER_PROGRAM_MD = """# Literature Reviewer Skill — Meta-Learner Program

Human-editable control plane for the literature reviewer autoresearch loop.
Re-read on every self-improvement experiment.

## Objective
Maximize review quality and comprehensiveness: the reviewer should produce
well-structured syntheses with accurate citations, balanced coverage,
and clear identification of evidence gaps.

## Current hypotheses (2026-04)
1. Thematic organization is more useful than chronological for most topics.
2. Explicit evidence gap identification improves review utility.
3. Citation precision (author-year, PMID) reduces verification burden.

## Search directions
- Improve thematic clustering of diverse study types
- Add guidance for handling conflicting evidence
- Refine gap identification specificity
- Improve integration with search strategist results

## Do NOT
- Change the output JSON schema
- Produce purely descriptive (study-by-study) reviews
- Remove citation requirements
- Make multiple unrelated edits in one proposal
"""

RESEARCH_CHAT_PROGRAM_MD = """# Research Chat Skill — Meta-Learner Program

Human-editable control plane for the research chat autoresearch loop.
Re-read on every self-improvement experiment.

## Objective
Maximize helpfulness and accuracy as a general research assistant:
provide clear, well-reasoned answers that connect to the user's
specific context and guide them toward the right specialized tools.

## Current hypotheses (2026-04)
1. Proactive suggestions to switch to specialized agents improves outcomes.
2. Asking clarifying questions before giving advice reduces misunderstandings.
3. Contextual citations increase user trust in responses.

## Search directions
- Improve ability to triage questions to the right specialized agent
- Add domain-specific reasoning when discussing clinical research
- Refine follow-up question quality

## Do NOT
- Change the output JSON schema
- Provide overly hedged or vague answers
- Replace specialized agent functionality
- Make multiple unrelated edits in one proposal
"""

STUDY_BUILDER_PROGRAM_MD = """# Study Builder Skill — Meta-Learner Program

Human-editable control plane for the study builder autoresearch loop.
Re-read on every self-improvement experiment.

## Objective
Maximize the quality and completeness of study protocol outputs:
designs should be rigorous, feasible, and compliant with SPIRIT 2013
and relevant regulatory guidelines.

## Current hypotheses (2026-04)
1. Structured protocol section output improves iterative refinement.
2. Explicit design decision tracking helps researchers justify choices.
3. Feasibility warnings prevent impractical protocol submissions.

## Search directions
- Improve adaptive design recommendations
- Add cost/timeline estimation heuristics
- Refine inclusion/exclusion criteria generation
- Better integration with protocol evaluator for self-checking

## Do NOT
- Change the output JSON schema
- Skip feasibility considerations for methodological purity
- Produce incomplete protocol sections without flagging gaps
- Make multiple unrelated edits in one proposal
"""

PROTOCOL_EVALUATOR_PROGRAM_MD = """# Protocol Evaluator Skill — Meta-Learner Program

Human-editable control plane for the protocol evaluator autoresearch loop.
Re-read on every self-improvement experiment.

## Objective
Maximize the accuracy and thoroughness of protocol evaluations:
reviews should identify genuine issues, rate severity appropriately,
and provide actionable improvement recommendations.

## Current hypotheses (2026-04)
1. Structured checklist scoring alongside narrative critique is most useful.
2. Distinguishing critical vs. minor issues prevents alarm fatigue.
3. Specific fix suggestions (not just problem identification) improve uptake.

## Search directions
- Improve calibration of overall_rating assignment
- Add regulatory-specific evaluation modes (FDA IND, EMA CTA)
- Refine feasibility assessment heuristics
- Better handling of non-standard study designs

## Do NOT
- Change the output JSON schema
- Be purely negative — always acknowledge strengths
- Conflate methodological preferences with genuine flaws
- Make multiple unrelated edits in one proposal
"""


def _read_program_file(vault_dir: Path, agent_type: str) -> str:
    """Read the agent's program.md meta-learner guidance.

    Falls back to an empty-ish string if missing so callers can substitute
    a default hint block. Never raises."""
    try:
        p = vault_dir / "skills" / agent_type / "program.md"
        if p.exists():
            return p.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to read program.md for %s: %s", agent_type, e)
    return ""

EXPERIMENT_BUDGET = int(os.environ.get("SKILL_EXPERIMENT_BUDGET", "5"))
IMPROVEMENT_ENABLED = os.environ.get("SKILL_IMPROVEMENT_ENABLED", "true").lower() in ("true", "1", "yes")

# ─────────────────────────────────────────────
# Experiment tracking table
# ─────────────────────────────────────────────
EXPERIMENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS skill_experiments (
    id               SERIAL PRIMARY KEY,
    agent_type       TEXT    NOT NULL,
    skill_version    INTEGER NOT NULL,
    metric_before    REAL,
    metric_after     REAL,
    status           TEXT    NOT NULL CHECK(status IN ('keep','discard','crash')),
    description      TEXT,
    prompt_preview   TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_skill_exp_type ON skill_experiments(agent_type);
"""


def _log_experiment(conn: sqlite3.Connection, agent_type: str, version: int,
                    metric_before: float, metric_after: float,
                    status: str, description: str, prompt_preview: str = "") -> None:
    """Log an experiment result, like autoresearch's results.tsv."""
    with conn:
        conn.execute(
            """INSERT INTO skill_experiments
               (agent_type, skill_version, metric_before, metric_after, status, description, prompt_preview)
               VALUES (?,?,?,?,?,?,?)""",
            (agent_type, version, metric_before, metric_after, status, description, prompt_preview[:500]),
        )
        conn.commit()


# ─────────────────────────────────────────────
# Lightweight evaluation (cheap experiment run)
# ─────────────────────────────────────────────

def _lightweight_eval(prompt_text: str, papers_b64: list[dict], theme: str,
                      agent_type: str) -> tuple[float, str]:
    """Run a quick evaluation of a candidate prompt.

    For generators: generate a mini-rubric (3 questions), assess quality heuristically.
    For judges: grade a known rubric with the candidate prompt, measure internal consistency.

    Returns (metric, description) where higher metric = better.
    Cost: ~$0.50-1.00 per call.
    """
    if agent_type == "generator":
        return _eval_generator_prompt(prompt_text, papers_b64, theme)
    else:
        return _eval_judge_prompt(prompt_text, papers_b64, theme)


def _eval_generator_prompt(prompt_text: str, papers_b64: list[dict],
                           theme: str) -> tuple[float, str]:
    """Evaluate a generator prompt by:
    1. Generate a 3-question rubric
    2. Use a second Claude call to assess rubric quality (verifiable? discriminating? specific?)
    Returns (quality_score 0-1, description)
    """
    # Build content with first 2 papers only (keep cheap)
    content: list[dict] = []
    for p in papers_b64[:2]:
        content.append({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": p["b64"]},
        })
    content.append({
        "type": "text",
        "text": (
            f"Challenge theme: {theme}\n"
            f"Papers: {', '.join(p['filename'] for p in papers_b64[:2])}\n\n"
            f"Generate EXACTLY 3 benchmark questions (not 10 — this is a quick evaluation run). "
            f"Follow all other instructions in your system prompt. Respond with JSON only."
        ),
    })

    try:
        raw, elapsed_ms = time_ms(
            call_anthropic,
            [{"role": "user", "content": content}],
            prompt_text,
            4096,
        )
        rubric = parse_json_response(raw)
    except Exception as e:
        return 0.0, f"crash: {e}"

    questions = rubric.get("questions", [])
    if not questions:
        return 0.0, "crash: no questions generated"

    # Quality assessment via a second Claude call
    quality_prompt = f"""Rate this evaluation rubric on a scale of 0.0 to 1.0 across these criteria:
1. Verifiability: Can each question's ideal_answer be verified from the paper? (0=no, 1=yes)
2. Specificity: Are answers specific with actual values/names, not vague? (0=vague, 1=specific)
3. Discrimination: Would these questions distinguish a strong reader from a weak one? (0=trivial, 1=discriminating)
4. Clarity: Are scoring_criteria unambiguous? (0=ambiguous, 1=clear)

Rubric:
{json.dumps(questions, indent=2)}

Respond with ONLY a JSON object: {{"verifiability": 0.X, "specificity": 0.X, "discrimination": 0.X, "clarity": 0.X, "overall": 0.X, "reasoning": "..."}}"""

    try:
        quality_raw = call_anthropic(
            [{"role": "user", "content": quality_prompt}],
            "You are a rubric quality assessor. Return only JSON.",
            1024,
        )
        quality = parse_json_response(quality_raw)
        score = float(quality.get("overall", 0.5))
        reasoning = quality.get("reasoning", "")[:200]
        return score, f"quality={score:.3f} ({reasoning})"
    except Exception as e:
        # Fallback: count heuristics
        has_ideal = sum(1 for q in questions if q.get("ideal_answer", "").strip())
        has_criteria = sum(1 for q in questions if q.get("scoring_criteria", "").strip())
        score = (has_ideal + has_criteria) / (2 * max(1, len(questions)))
        return score, f"heuristic_quality={score:.3f} ({has_ideal}/{len(questions)} ideal answers)"


def _eval_judge_prompt(prompt_text: str, papers_b64: list[dict],
                       theme: str) -> tuple[float, str]:
    """Evaluate a judge prompt by:
    1. Create a simple rubric with known answers
    2. Grade a known-correct response AND a known-wrong response
    3. Measure if the judge correctly discriminates (correct scores high, wrong scores low)
    Returns (discrimination_score 0-1, description)
    """
    # Synthetic test: provide a rubric question with a clearly correct and clearly wrong answer
    test_question = {
        "question_id": "test_q1",
        "domain": "Methods",
        "question": "What study design was used?",
        "ideal_answer": "A randomized controlled trial with parallel groups.",
        "scoring_criteria": "Full credit: identifies RCT with parallel groups. Partial: identifies RCT only. Zero: wrong design or vague.",
        "max_points": 3,
    }

    correct_answer = {"question_id": "test_q1", "answer": "The study used a randomized controlled trial with parallel group allocation."}
    wrong_answer = {"question_id": "test_q1", "answer": "This was a retrospective cohort study using administrative databases."}

    grading_items_correct = [{**test_question, "llm_answer": correct_answer["answer"]}]
    grading_items_wrong = [{**test_question, "llm_answer": wrong_answer["answer"]}]

    try:
        # Grade the correct answer
        raw_c = call_anthropic(
            [{"role": "user", "content": f"Grade this response:\n{json.dumps(grading_items_correct, indent=2)}"}],
            prompt_text, 1024,
        )
        grades_c = parse_json_response(raw_c)
        score_c = grades_c.get("grades", [{}])[0].get("score", 0) if grades_c.get("grades") else 0

        # Grade the wrong answer
        raw_w = call_anthropic(
            [{"role": "user", "content": f"Grade this response:\n{json.dumps(grading_items_wrong, indent=2)}"}],
            prompt_text, 1024,
        )
        grades_w = parse_json_response(raw_w)
        score_w = grades_w.get("grades", [{}])[0].get("score", 0) if grades_w.get("grades") else 0

        # Discrimination: correct should score high (3), wrong should score low (0-1)
        max_pts = test_question["max_points"]
        correct_ratio = score_c / max_pts  # should be ~1.0
        wrong_ratio = score_w / max_pts    # should be ~0.0
        discrimination = max(0, correct_ratio - wrong_ratio)  # 1.0 = perfect discrimination

        return discrimination, f"discrimination={discrimination:.3f} (correct={score_c}/{max_pts}, wrong={score_w}/{max_pts})"
    except Exception as e:
        return 0.0, f"crash: {e}"


# ─────────────────────────────────────────────
# Meta-Claude: propose prompt modifications
# ─────────────────────────────────────────────

META_PROMPT = """You are iterating on an AI agent's system prompt, like modifying train.py in an ML experiment loop.

AGENT TYPE: {agent_type}
CURRENT VERSION: v{version}

PROGRAM INSTRUCTIONS (human-editable guidance, re-read on every run):
{program_instructions}

EXPERIMENT HISTORY (most recent first):
{experiment_log}

CURRENT PROMPT:
```
{current_prompt}
```

YOUR TASK:
Propose a SINGLE focused modification to the prompt. Like a researcher modifying one hyperparameter at a time:
- Change ONE thing (not five things at once)
- If the last experiment improved the metric, try a similar direction (momentum)
- If the last experiment hurt, revert that direction and try something different
- If you're stuck, try something more radical

SIMPLICITY CRITERION (from autoresearch):
"All else being equal, simpler is better. A small improvement that adds ugly complexity is not worth it.
Removing something and getting equal or better results is a great outcome — that's a simplification win."

WHAT TO MODIFY: {modification_hints}

Return ONLY the complete revised prompt text. No explanation, no preamble, no markdown fences.
Just the prompt, ready to be used as-is."""


GENERATOR_HINTS = """Consider:
- Adding/removing specific question-design instructions
- Changing how you describe what makes a "good" vs "bad" question
- Adjusting the emphasis on verifiability vs difficulty vs discrimination
- Simplifying verbose instructions that may confuse the model
- Adding concrete examples of good questions (few-shot)
- Removing instructions that don't seem to help"""

JUDGE_HINTS = """Consider:
- Making scoring criteria interpretation more specific
- Adjusting strictness level (too strict = low scores for good answers; too lenient = high scores for bad)
- Changing how partial credit is described
- Simplifying the grading rubric if it's overspecified
- Adding examples of what constitutes full/partial/zero credit
- Adjusting the rubric_validity assessment instructions"""


def _get_experiment_log(conn: sqlite3.Connection, agent_type: str,
                        limit: int = 10) -> str:
    """Format recent experiments as a readable log (like results.tsv)."""
    rows = conn.execute(
        """SELECT skill_version, metric_before, metric_after, status, description
           FROM skill_experiments WHERE agent_type=?
           ORDER BY created_at DESC LIMIT ?""",
        (agent_type, limit),
    ).fetchall()
    if not rows:
        return "(no previous experiments)"
    lines = ["version | before | after  | status  | description"]
    lines.append("--------|--------|--------|---------|------------")
    for r in rows:
        lines.append(
            f"v{r['skill_version']:>5} | {(r['metric_before'] or 0):.4f} | {(r['metric_after'] or 0):.4f} | "
            f"{r['status']:<7} | {(r['description'] or '')[:60]}"
        )
    return "\n".join(lines)


def _propose_modification(conn: sqlite3.Connection, agent_type: str,
                          current_prompt: str, version: int,
                          vault_dir: Path | None = None) -> str | None:
    """Ask meta-Claude to propose a single focused modification.

    Injects the human-editable program.md content (if present) into the
    meta-prompt so operator guidance steers the loop."""
    experiment_log = _get_experiment_log(conn, agent_type)
    hints = GENERATOR_HINTS if agent_type == "generator" else JUDGE_HINTS

    program_instructions = ""
    if vault_dir is not None:
        program_instructions = _read_program_file(vault_dir, agent_type).strip()
    if not program_instructions:
        program_instructions = "(none — use default hints)"

    prompt = META_PROMPT.format(
        agent_type=agent_type,
        version=version,
        program_instructions=program_instructions,
        experiment_log=experiment_log,
        current_prompt=current_prompt,
        modification_hints=hints,
    )

    try:
        new_prompt = call_anthropic(
            [{"role": "user", "content": prompt}],
            "You are an expert prompt engineer. Return only the revised prompt text.",
            4096,
        )
        new_prompt = new_prompt.strip()
        if len(new_prompt) < 100:
            logger.warning("Meta-Claude returned suspiciously short prompt (%d chars)", len(new_prompt))
            return None
        return new_prompt
    except Exception as e:
        logger.error("Prompt proposal failed: %s", e)
        return None


# ─────────────────────────────────────────────
# The experiment loop (autoresearch-style)
# ─────────────────────────────────────────────

def run_experiment_loop(get_db_fn, agent_type: str, papers_b64: list[dict],
                        theme: str, vault_dir: Path) -> dict:
    """
    The autoresearch-style experiment loop. Called after each daily challenge.

    LOOP (up to EXPERIMENT_BUDGET times):
      1. Get current prompt (the "train.py")
      2. Measure baseline metric with current prompt
      3. Meta-Claude proposes a modification
      4. Measure new metric with modified prompt
      5. If improved → KEEP (update DB, advance version)
         If not → DISCARD (revert, log, move on)
      6. Log the experiment
      7. Repeat

    Returns summary dict with experiment results.
    """
    if not IMPROVEMENT_ENABLED:
        return {"status": "disabled"}
    if not papers_b64:
        return {"status": "no_papers"}

    conn = get_db_fn()
    results = []

    try:
        for experiment_num in range(EXPERIMENT_BUDGET):
            skill = get_active_skill(conn, agent_type)
            current_prompt = skill["prompt_text"]
            current_version = skill["version"]

            logger.info(
                "Experiment %d/%d for %s (current: v%d)",
                experiment_num + 1, EXPERIMENT_BUDGET, agent_type, current_version,
            )

            # Step 1: Baseline metric with current prompt
            baseline_metric, baseline_desc = _lightweight_eval(
                current_prompt, papers_b64, theme, agent_type,
            )
            logger.info("  Baseline: %.4f (%s)", baseline_metric, baseline_desc[:80])

            # Step 2: Propose modification (reads program.md from vault)
            new_prompt = _propose_modification(
                conn, agent_type, current_prompt, current_version, vault_dir=vault_dir,
            )
            if not new_prompt:
                _log_experiment(conn, agent_type, current_version, baseline_metric, 0, "crash",
                                "meta-Claude returned no proposal")
                results.append({"experiment": experiment_num + 1, "status": "crash", "reason": "no proposal"})
                # Per-experiment note for crash
                try:
                    write_experiment_note(vault_dir, agent_type, {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "status": "crash",
                        "skill_version": current_version,
                        "metric_before": baseline_metric,
                        "metric_after": 0,
                        "agent_type": agent_type,
                        "baseline_prompt": current_prompt,
                        "candidate_prompt": "",
                        "description": "meta-Claude returned no proposal",
                        "eval_description": baseline_desc,
                        "keep_reason": "crash: no proposal",
                        "challenge_id": None,
                    })
                except Exception as e:
                    logger.warning("Failed to write crash experiment note: %s", e)
                continue

            # Step 3: Evaluate modified prompt
            new_metric, new_desc = _lightweight_eval(
                new_prompt, papers_b64, theme, agent_type,
            )
            logger.info("  Candidate: %.4f (%s)", new_metric, new_desc[:80])

            # Step 4: Keep or discard (binary, like autoresearch)
            if new_metric > baseline_metric:
                # KEEP — deploy the new version
                versions = list_skill_versions(conn, agent_type)
                max_ver = max((v["version"] for v in versions), default=0)
                new_version = max_ver + 1

                with conn:
                    conn.execute("UPDATE agent_skills SET active=0 WHERE agent_type=?", (agent_type,))
                    conn.execute(
                        "INSERT INTO agent_skills (agent_type, version, prompt_text, active) VALUES (?,?,?,1)",
                        (agent_type, new_version, new_prompt),
                    )
                    conn.commit()

                improvement = new_metric - baseline_metric
                desc = f"KEEP: {baseline_metric:.4f} → {new_metric:.4f} (+{improvement:.4f}). {new_desc[:100]}"
                _log_experiment(conn, agent_type, new_version, baseline_metric, new_metric, "keep", desc, new_prompt[:500])
                results.append({"experiment": experiment_num + 1, "status": "keep",
                                "before": baseline_metric, "after": new_metric, "version": new_version})
                logger.info("  → KEEP (v%d, +%.4f)", new_version, improvement)

                # Per-experiment autoresearch artifact
                try:
                    write_experiment_note(vault_dir, agent_type, {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "status": "keep",
                        "skill_version": new_version,
                        "metric_before": baseline_metric,
                        "metric_after": new_metric,
                        "agent_type": agent_type,
                        "baseline_prompt": current_prompt,
                        "candidate_prompt": new_prompt,
                        "description": desc,
                        "eval_description": new_desc,
                        "keep_reason": f"improvement +{improvement:.4f}",
                        "challenge_id": None,
                    })
                except Exception as e:
                    logger.warning("Failed to write keep experiment note: %s", e)

            else:
                # DISCARD — revert, don't change anything
                desc = f"DISCARD: {baseline_metric:.4f} → {new_metric:.4f}. {new_desc[:100]}"
                _log_experiment(conn, agent_type, current_version, baseline_metric, new_metric, "discard", desc, new_prompt[:500])
                results.append({"experiment": experiment_num + 1, "status": "discard",
                                "before": baseline_metric, "after": new_metric})
                logger.info("  → DISCARD (no improvement)")

                # Per-experiment autoresearch artifact
                try:
                    write_experiment_note(vault_dir, agent_type, {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "status": "discard",
                        "skill_version": current_version,
                        "metric_before": baseline_metric,
                        "metric_after": new_metric,
                        "agent_type": agent_type,
                        "baseline_prompt": current_prompt,
                        "candidate_prompt": new_prompt,
                        "description": desc,
                        "eval_description": new_desc,
                        "keep_reason": f"no gain ({new_metric - baseline_metric:+.4f})",
                        "challenge_id": None,
                    })
                except Exception as e:
                    logger.warning("Failed to write discard experiment note: %s", e)

        # Refresh SKILL.md + history.md (new Anthropic-format vault structure)
        try:
            active = get_active_skill(conn, agent_type)
            versions = list_skill_versions(conn, agent_type)
            write_agent_skill_file(vault_dir, agent_type, active)
            write_agent_history_file(vault_dir, agent_type, versions)
        except Exception as e:
            logger.error("Obsidian skill file write failed: %s", e)

    except Exception as e:
        logger.error("Experiment loop failed for %s: %s", agent_type, e, exc_info=True)
        results.append({"status": "error", "error": str(e)})
    finally:
        conn.close()

    kept = sum(1 for r in results if r.get("status") == "keep")
    discarded = sum(1 for r in results if r.get("status") == "discard")
    logger.info(
        "Experiment loop complete for %s: %d experiments, %d kept, %d discarded",
        agent_type, len(results), kept, discarded,
    )
    return {"agent_type": agent_type, "experiments": results, "kept": kept, "discarded": discarded}


def maybe_improve_after_challenge(get_db_fn, agent_type: str,
                                  vault_dir: Path,
                                  papers_b64: list[dict] | None = None,
                                  theme: str = "") -> None:
    """Entry point called after each daily challenge completes.
    If papers_b64 is provided, runs the experiment loop immediately.
    Otherwise, loads papers from the most recent daily challenge."""
    if not IMPROVEMENT_ENABLED:
        return

    if not papers_b64:
        # Load papers from the most recent completed daily challenge
        import base64
        conn = get_db_fn()
        try:
            latest = conn.execute(
                """SELECT c.id, c.theme FROM challenges c
                   WHERE c.kind='daily' AND c.status='complete'
                   ORDER BY c.completed_at DESC LIMIT 1"""
            ).fetchone()
            if not latest:
                return
            theme = latest["theme"] or ""
            paper_rows = conn.execute(
                """SELECT p.filename, p.disk_filename
                   FROM papers p JOIN challenge_papers cp ON cp.paper_id = p.id
                   WHERE cp.challenge_id=? LIMIT 3""",
                (latest["id"],),
            ).fetchall()
            # We need the PAPERS_DIR — import from config
            import os
            from pathlib import Path as _P
            data_dir = _P(os.environ.get("RENDER_DATA_DIR", _P(__file__).parent.parent))
            papers_dir = data_dir / "papers"
            papers_b64 = []
            for r in paper_rows:
                path = papers_dir / (r["disk_filename"] or "")
                if path.exists():
                    b64 = base64.b64encode(path.read_bytes()).decode()
                    papers_b64.append({"filename": r["filename"], "b64": b64})
        finally:
            conn.close()

    if not papers_b64:
        return

    run_experiment_loop(get_db_fn, agent_type, papers_b64, theme, vault_dir)


# ─────────────────────────────────────────────
# Admin status
# ─────────────────────────────────────────────

def get_improvement_status(conn: sqlite3.Connection, agent_type: str) -> dict:
    """Return current improvement status for the admin panel."""
    skill = get_active_skill(conn, agent_type)
    versions = list_skill_versions(conn, agent_type)

    # Recent experiments
    experiments = conn.execute(
        """SELECT skill_version, metric_before, metric_after, status, description, created_at
           FROM skill_experiments WHERE agent_type=?
           ORDER BY created_at DESC LIMIT 20""",
        (agent_type,),
    ).fetchall()

    kept = sum(1 for e in experiments if e["status"] == "keep")
    discarded = sum(1 for e in experiments if e["status"] == "discard")
    crashed = sum(1 for e in experiments if e["status"] == "crash")

    return {
        "agent_type": agent_type,
        "active_version": skill["version"],
        "total_versions": len(versions),
        "experiment_budget": EXPERIMENT_BUDGET,
        "improvement_enabled": IMPROVEMENT_ENABLED,
        "recent_experiments": [dict(e) for e in experiments],
        "experiment_summary": {"total": len(experiments), "kept": kept, "discarded": discarded, "crashed": crashed},
        "versions": versions,
    }
