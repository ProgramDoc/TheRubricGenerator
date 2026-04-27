"""Judge Agent. Grades a competing model's answers against the rubric.

Three independent judges are used in sequence:

  * Judge 1 — Anthropic (Claude).  :func:`run_judge_agent`
  * Judge 2 — OpenAI (GPT-*).       :func:`run_second_judge` (with Claude fallback)
  * Judge 3 — Gemini.               :func:`backend.agents.adjudicator.run_third_judge`

Judges 2 and 3 are only invoked when the previous judges disagree
(see :mod:`backend.agents.adjudicator`). The ``shadow_regrade`` name is
kept as a thin alias for backwards compatibility with call sites that
haven't migrated yet — it now runs an OpenAI judge, not a second Claude
call.
"""

from __future__ import annotations

import json
import logging

from ..helpers import (
    call_anthropic,
    call_openai,
    parse_json_response,
    time_ms,
    OPENAI_API_KEY,
)

logger = logging.getLogger(__name__)

# Default OpenAI model for the second judge. Matches the pattern used in
# backend/agents/participants.py for OpenAI runs.
_DEFAULT_OPENAI_MODEL = "gpt-4o"


def _build_grading_items(rubric: dict, model_answers: dict) -> list[dict]:
    """Shared payload builder — identical across the three judges so they
    all grade against the same scoring criteria."""
    questions = {q["id"]: q for q in rubric.get("questions", [])}
    response_map = {r["question_id"]: r["answer"]
                    for r in model_answers.get("responses", [])}
    items = []
    for qid, q in questions.items():
        items.append({
            "question_id": qid,
            "domain": q.get("domain", ""),
            "paper_ref": q.get("paper_ref", ""),
            "question": q["question"],
            "ideal_answer": q.get("ideal_answer", ""),
            "scoring_criteria": q.get("scoring_criteria", ""),
            "max_points": q.get("max_points", 1),
            "llm_answer": response_map.get(qid, "(no answer provided)"),
        })
    return items


def run_judge_agent(rubric: dict, model_answers: dict, skill: dict,
                    max_tokens: int = 16384) -> tuple[dict, int]:
    """Judge 1 — Claude. First-pass grade against the rubric.

    rubric: full rubric dict (questions[].ideal_answer, scoring_criteria, max_points)
    model_answers: {"responses": [{"question_id": "q1", "answer": "..."}]}
    skill: active judge skill dict

    Returns: (grades_dict, elapsed_ms)
    """
    grading_items = _build_grading_items(rubric, model_answers)
    user_msg = (
        "Grade these evaluation responses strictly against the scoring "
        f"criteria:\n\n{json.dumps(grading_items, indent=2)}"
    )

    raw, elapsed_ms = time_ms(
        call_anthropic,
        [{"role": "user", "content": user_msg}],
        skill["prompt_text"],
        max_tokens,
    )
    grades = parse_json_response(raw)
    return grades, elapsed_ms


def run_second_judge(rubric: dict, model_answers: dict, skill: dict,
                     max_tokens: int = 16384,
                     model: str = _DEFAULT_OPENAI_MODEL) -> tuple[dict, int]:
    """Judge 2 — OpenAI. Independent grade from a different vendor.

    Reuses the judge skill prompt verbatim so grading criteria are
    identical to judge 1. Only the LLM backend differs — this
    decorrelates vendor-specific scoring quirks from judge 1.

    Fallback behavior: if OPENAI_API_KEY is missing we fall back to a
    second Claude call so the pipeline still produces a "second opinion"
    in local/dev environments. The fallback is logged as degraded.

    Returns: (grades_dict, elapsed_ms)
    """
    grading_items = _build_grading_items(rubric, model_answers)
    user_msg = (
        "Grade these evaluation responses strictly against the scoring "
        f"criteria. Return JSON only.\n\n{json.dumps(grading_items, indent=2)}"
    )

    if not OPENAI_API_KEY:
        logger.warning(
            "Second judge degraded: OPENAI_API_KEY not set, falling back to Claude."
        )
        return run_judge_agent(rubric, model_answers, skill, max_tokens)

    messages = [{"role": "user", "content": user_msg}]
    # OpenAI uses a `system` role inline rather than a separate parameter.
    messages_with_system = (
        [{"role": "system", "content": skill["prompt_text"]}] + messages
    )
    raw, elapsed_ms = time_ms(
        call_openai,
        messages_with_system,
        model,
        max_tokens,
    )
    grades = parse_json_response(raw)
    return grades, elapsed_ms


# ─── Backwards compatibility ────────────────────────────────────────
# Old call sites imported ``shadow_regrade``. Keep the name alive as a
# thin alias for one release; new code should call ``run_second_judge``.

def shadow_regrade(rubric: dict, model_answers: dict, skill: dict) -> dict:
    """Deprecated alias. Runs judge 2 (OpenAI, with Claude fallback).
    Kept for backwards compatibility; prefer :func:`run_second_judge`."""
    grades, _ = run_second_judge(rubric, model_answers, skill)
    return grades
