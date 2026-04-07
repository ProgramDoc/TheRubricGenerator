"""Judge Agent. Grades a competing model's answers against the rubric."""

import json

from ..helpers import call_anthropic, parse_json_response, time_ms


def run_judge_agent(rubric: dict, model_answers: dict, skill: dict,
                    max_tokens: int = 16384) -> tuple[dict, int]:
    """
    rubric: the full rubric dict (with questions[].ideal_answer, scoring_criteria, max_points)
    model_answers: dict with shape {"responses": [{"question_id": "q1", "answer": "..."}]}
    skill: active judge skill dict

    Returns: (grades_dict, elapsed_ms)
    """
    questions = {q["id"]: q for q in rubric.get("questions", [])}
    response_map = {r["question_id"]: r["answer"] for r in model_answers.get("responses", [])}

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

    user_msg = f"Grade these evaluation responses strictly against the scoring criteria:\n\n{json.dumps(grading_items, indent=2)}"

    raw, elapsed_ms = time_ms(
        call_anthropic,
        [{"role": "user", "content": user_msg}],
        skill["prompt_text"],
        max_tokens,
    )
    grades = parse_json_response(raw)
    return grades, elapsed_ms


def shadow_regrade(rubric: dict, model_answers: dict, skill: dict) -> dict:
    """Run the judge agent a second time for consistency scoring.
    Same inputs, independent call. The difference between this and the
    primary grade informs the judge's consistency score."""
    grades, _ = run_judge_agent(rubric, model_answers, skill)
    return grades
