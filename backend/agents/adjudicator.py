"""Adjudicator Agent.

Resolves disagreement between judges 1 (Anthropic) and 2 (OpenAI) by
running judge 3 (Gemini), then takes the majority-of-three vote across
the per-question scores. When no two of the three agree on the score
(3-way split), the question is flagged for human review via
:mod:`backend.review`.

Vendor lineup (see backend/agents/judge.py for judges 1 + 2):

  * Judge 1 — Anthropic Claude
  * Judge 2 — OpenAI GPT-*
  * Judge 3 — Google Gemini

Public API:
  * :func:`run_third_judge`   — Gemini judge call (with Claude fallback).
  * :func:`adjudicate_grades` — merge three grade dicts into a single
    "final" grade dict, returning the list of questions that require
    human review.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from ..helpers import (
    call_anthropic,
    call_gemini,
    parse_json_response,
    time_ms,
    GEMINI_API_KEY,
)

logger = logging.getLogger(__name__)

# Default Gemini model for cross-vendor adjudication. Matches the pattern
# used in backend/agents/participants.py for Gemini runs.
_DEFAULT_GEMINI_MODEL = "gemini-2.5-pro"


def run_third_judge(rubric: dict, model_answers: dict, judge_skill: dict,
                    max_tokens: int = 16384,
                    model: str = _DEFAULT_GEMINI_MODEL) -> tuple[dict, int]:
    """Run judge 3 (Gemini) — the third independent vendor.

    Reuses the judge skill's system prompt verbatim so grading criteria are
    identical to judges 1 and 2. The only variable is the LLM backend,
    which decorrelates vendor-specific scoring quirks across the three
    passes.

    Fallback behavior: if GEMINI_API_KEY is missing we fall back to a
    Claude call so the pipeline still produces a tiebreaker in local/dev
    environments. The fallback is logged as degraded. Callers can still
    detect the no-Gemini case up front via :func:`third_judge_available`
    and record a ``no_third`` status if they prefer skipping the tiebreak
    entirely.

    Returns the parsed grades dict and elapsed_ms (mirrors
    :func:`backend.agents.judge.run_judge_agent`).
    """
    questions = {q["id"]: q for q in rubric.get("questions", [])}
    response_map = {r["question_id"]: r["answer"]
                    for r in model_answers.get("responses", [])}

    grading_items = []
    for qid, q in questions.items():
        grading_items.append({
            "question_id": qid,
            "domain": q.get("domain", ""),
            "paper_ref": q.get("paper_ref", ""),
            "question": q["question"],
            "ideal_answer": q.get("ideal_answer", ""),
            "scoring_criteria": q.get("scoring_criteria", ""),
            "max_points": q.get("max_points", 1),
            "llm_answer": response_map.get(qid, "(no answer provided)"),
        })

    user_msg = (
        "Grade these evaluation responses strictly against the scoring "
        "criteria. Return JSON only.\n\n"
        f"{json.dumps(grading_items, indent=2)}"
    )

    if not GEMINI_API_KEY:
        logger.warning(
            "Third judge degraded: GEMINI_API_KEY not set, falling back to Claude."
        )
        raw, elapsed_ms = time_ms(
            call_anthropic,
            [{"role": "user", "content": user_msg}],
            judge_skill["prompt_text"],
            max_tokens,
        )
    else:
        raw, elapsed_ms = time_ms(
            call_gemini,
            judge_skill["prompt_text"],
            user_msg,
            model,
            None,                # pdf_b64 not needed — grading is text-only
            max_tokens,
        )
    grades = parse_json_response(raw)
    return grades, elapsed_ms


# ─────────────────────────────────────────────────────────────────────
# Adjudication logic — pure function, no I/O
# ─────────────────────────────────────────────────────────────────────

def _index_grades(grades: dict) -> dict[str, dict]:
    """Return {question_id: grade_row} from a judge grades dict."""
    return {g["question_id"]: g for g in grades.get("grades", [])}


def _majority_score(primary: float, shadow: float,
                    third: Optional[float]) -> tuple[Optional[float], str]:
    """Resolve three (or two) judge scores into a single adjudicated score.

    The three score inputs come from judges 1 / 2 / 3 respectively
    (``primary`` = Anthropic, ``shadow`` = OpenAI, ``third`` = Gemini).
    The historical "shadow" parameter name predates the rewiring to
    OpenAI; it now simply means "the second judge".

    Returns (final_score, status) where status is one of:
      * "agree"    — judges 1 and 2 matched; no tiebreak needed
      * "majority" — two of three agreed; final is that value
      * "split"    — all three differ; final is None, needs human review
      * "no_third" — third judge call was skipped / unavailable; primary
                     wins by default and the gap is logged

    Floats are compared with a small epsilon so rubrics that emit
    fractional scores (e.g. 0.5 for partial credit) don't all cascade
    into the split branch.
    """
    eps = 1e-6

    def eq(a: float, b: float) -> bool:
        return abs((a or 0) - (b or 0)) < eps

    if eq(primary, shadow):
        return primary, "agree"

    if third is None:
        return primary, "no_third"

    # Two-of-three majority
    if eq(primary, third):
        return primary, "majority"
    if eq(shadow, third):
        return shadow, "majority"

    # All three distinct — human review required
    return None, "split"


def adjudicate_grades(primary_grades: dict,
                      shadow_grades: dict,
                      third_grades: Optional[dict]) -> tuple[dict, list[dict]]:
    """Merge three grade dicts into a single adjudicated grade dict.

    Strategy (per question):
      1. If primary and shadow agree, keep primary score. No adjudication.
      2. If they disagree and the third grade exists, apply majority-of-3.
      3. If all three differ, leave the primary score in place for
         provisional leaderboard purposes AND return the question in the
         ``needs_review`` list so :mod:`backend.review` can persist it and
         notify a human adjudicator.

    Returns (final_grades, needs_review). ``final_grades`` has the same
    shape as the input grades dicts — callers can drop it straight into
    ``model_participants.grade_json``. Each per-question entry gains an
    ``adjudication`` sub-dict documenting what happened:

        {
          "status": "agree" | "majority" | "split" | "no_third",
          "primary_score": 1.0,
          "shadow_score":  0.0,
          "third_score":   1.0,    # may be missing if no_third
          "reviewed":      false,
        }

    ``needs_review`` is a list of dicts with keys ``question_id``,
    ``primary``, ``shadow``, ``third``, ``max_points`` — exactly what
    :func:`backend.review.flag_for_review` expects.
    """
    primary_idx = _index_grades(primary_grades)
    shadow_idx = _index_grades(shadow_grades)
    third_idx = _index_grades(third_grades) if third_grades else {}

    final_rows: list[dict] = []
    needs_review: list[dict] = []

    for qid, p in primary_idx.items():
        s = shadow_idx.get(qid) or {}
        t = third_idx.get(qid) if third_idx else None

        p_score = p.get("score", 0) or 0
        s_score = s.get("score", 0) or 0
        t_score = t.get("score") if t else None

        final_score, status = _majority_score(p_score, s_score, t_score)

        row = dict(p)  # preserve reasoning, max_points, ideal_answer, etc.
        row["adjudication"] = {
            "status": status,
            "primary_score": p_score,
            "shadow_score": s_score,
            "third_score": t_score,
            "reviewed": False,
        }
        if final_score is None:
            # Provisional: leave primary score in place; human will overwrite.
            row["adjudication"]["provisional"] = True
            needs_review.append({
                "question_id": qid,
                "primary": p_score,
                "shadow": s_score,
                "third":  t_score,
                "max_points": p.get("max_points", 1),
                "question": p.get("question", ""),
                "ideal_answer": p.get("ideal_answer", ""),
                "scoring_criteria": p.get("scoring_criteria", ""),
                "primary_reasoning": p.get("reasoning", ""),
                "shadow_reasoning":  s.get("reasoning", ""),
                "third_reasoning":   (t or {}).get("reasoning", ""),
            })
        else:
            row["score"] = final_score

        final_rows.append(row)

    final = dict(primary_grades)  # preserve avg_rubric_validity etc.
    final["grades"] = final_rows
    final["adjudication_summary"] = {
        "total":    len(final_rows),
        "agree":    sum(1 for r in final_rows if r["adjudication"]["status"] == "agree"),
        "majority": sum(1 for r in final_rows if r["adjudication"]["status"] == "majority"),
        "split":    sum(1 for r in final_rows if r["adjudication"]["status"] == "split"),
        "no_third": sum(1 for r in final_rows if r["adjudication"]["status"] == "no_third"),
    }
    return final, needs_review


def needs_adjudication(primary_grades: dict, shadow_grades: dict) -> bool:
    """Cheap pre-check: is any per-question score different?

    Lets the caller skip the expensive third-judge call when primary and
    shadow are identical (the common case for easy questions).
    """
    eps = 1e-6
    primary_idx = _index_grades(primary_grades)
    shadow_idx = _index_grades(shadow_grades)
    for qid, p in primary_idx.items():
        s = shadow_idx.get(qid) or {}
        if abs((p.get("score", 0) or 0) - (s.get("score", 0) or 0)) > eps:
            return True
    return False


def third_judge_available() -> bool:
    """True iff GEMINI_API_KEY is configured. When False the adjudicator
    degrades gracefully — primary grade wins on any disagreement and the
    ``no_third`` status is recorded so operators can spot the gap."""
    return bool(GEMINI_API_KEY)
