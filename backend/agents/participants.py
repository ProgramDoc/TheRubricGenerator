"""Participant model runners. Routes a challenge's rubric + PDFs to a
frontier model (Claude/GPT/Gemini) and returns structured answers."""

from ..helpers import (
    call_anthropic, call_gemini, call_openai,
    parse_json_response, time_ms,
)


PARTICIPANT_SYSTEM = """You are a clinical research expert participating in a benchmark challenge.
You will be given a set of research papers and a list of questions about them.
Answer each question thoroughly and specifically, citing evidence from the papers.
Be precise with numbers, names, and methodology. Do not speculate beyond what is written in the papers.

Format your response as JSON only, no preamble, no markdown fences:

{
  "responses": [
    {"question_id": "q1", "answer": "<your detailed answer>"},
    {"question_id": "q2", "answer": "<your detailed answer>"}
  ]
}"""


PROVIDER_BY_PREFIX = {
    "claude": "anthropic",
    "gpt":    "openai",
    "gemini": "google",
}


def provider_for(model_id: str) -> str:
    for prefix, provider in PROVIDER_BY_PREFIX.items():
        if model_id.startswith(prefix):
            return provider
    return "unknown"


def _build_question_block(rubric: dict) -> str:
    questions = rubric.get("questions", [])
    return "\n\n".join(
        f"Question {i+1} (ID: {q['id']}, Domain: {q.get('domain','')}, Max points: {q.get('max_points',1)}):\n{q['question']}"
        for i, q in enumerate(questions)
    )


def run_participant_model(model_id: str, rubric: dict, papers_b64: list[dict],
                          max_tokens: int = 4096) -> tuple[dict, int]:
    """
    model_id: 'gpt-4o', 'gemini-2.5-pro', 'claude-sonnet-4-20250514', etc.
    rubric: the challenge rubric dict
    papers_b64: list of {filename, b64}

    Returns: (answers_dict, elapsed_ms) — answers_dict has {"responses": [...]}
    """
    q_block = _build_question_block(rubric)
    user_msg = (
        f"Please answer the following {len(rubric.get('questions', []))} questions "
        f"about the attached research papers. Respond with the JSON object as instructed.\n\n{q_block}"
    )

    provider = provider_for(model_id)

    if provider == "google":
        # Gemini natively accepts inline PDFs. Only one PDF supported per call in our
        # current helper; combine multiple PDFs by concatenating base64 won't work.
        # For Phase 1 with multiple PDFs, we pass the first PDF inline and reference
        # all by filename in the prompt.
        pdf_b64 = papers_b64[0]["b64"] if papers_b64 else None
        if len(papers_b64) > 1:
            user_msg = (
                f"[Note: {len(papers_b64)} papers in this challenge: "
                f"{', '.join(p['filename'] for p in papers_b64)}. "
                f"Primary attached; refer to filenames in paper_ref when answering.]\n\n"
                + user_msg
            )
        raw, elapsed_ms = time_ms(
            call_gemini, PARTICIPANT_SYSTEM, user_msg, model_id, pdf_b64, max_tokens
        )

    elif provider == "openai":
        # OpenAI chat-completions doesn't accept PDF base64. Text-only prompt.
        messages = [
            {"role": "system", "content": PARTICIPANT_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"[Note: {len(papers_b64)} papers in this challenge: "
                    f"{', '.join(p['filename'] for p in papers_b64)}. "
                    f"PDFs are not directly attached; answer based on paper identifiers in paper_ref.]\n\n"
                    + user_msg
                ),
            },
        ]
        raw, elapsed_ms = time_ms(call_openai, messages, model_id, max_tokens)

    else:
        # Anthropic: supports multiple inline PDFs
        content: list[dict] = []
        for p in papers_b64:
            content.append({
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": p["b64"]},
            })
        content.append({"type": "text", "text": user_msg})

        raw, elapsed_ms = time_ms(
            call_anthropic,
            [{"role": "user", "content": content}],
            PARTICIPANT_SYSTEM,
            max_tokens,
            model_id,
        )

    try:
        answers = parse_json_response(raw)
    except Exception:
        # Fallback: wrap the raw text so scoring still works, just with 0 credit
        answers = {
            "responses": [
                {"question_id": q["id"], "answer": raw[:500]}
                for q in rubric.get("questions", [])
            ]
        }
    return answers, elapsed_ms
