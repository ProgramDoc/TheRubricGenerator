"""Participant model runners. Routes a challenge's rubric + PDFs to a
frontier model (Claude/GPT/Gemini/Kimi/custom) and returns structured answers.

Phase 3 refactor: routing uses the SUPPORTED_MODELS dict from challenges.py.
Custom models (registered by users) are called via call_openai_compatible
with the stored base URL and decrypted API key.
"""

from ..helpers import (
    call_anthropic, call_gemini, call_openai_compatible,
    parse_json_response, time_ms,
    OPENAI_API_KEY, MOONSHOT_API_KEY,
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


# Map provider → env-var API key for built-in models
_PROVIDER_KEYS = {
    "openai":    lambda: OPENAI_API_KEY,
    "moonshot":  lambda: MOONSHOT_API_KEY,
}


def _build_question_block(rubric: dict) -> str:
    questions = rubric.get("questions", [])
    return "\n\n".join(
        f"Question {i+1} (ID: {q['id']}, Domain: {q.get('domain','')}, Max points: {q.get('max_points',1)}):\n{q['question']}"
        for i, q in enumerate(questions)
    )


def run_participant_model(model_id: str, rubric: dict, papers_b64: list[dict],
                          max_tokens: int = 16384,
                          custom_base_url: str | None = None,
                          custom_api_key: str | None = None) -> tuple[dict, int]:
    """
    model_id: built-in key from SUPPORTED_MODELS or a custom model name.
    rubric: the challenge rubric dict.
    papers_b64: list of {filename, b64}.
    custom_base_url/custom_api_key: for registered custom models.

    Returns: (answers_dict, elapsed_ms)
    """
    q_block = _build_question_block(rubric)
    user_msg = (
        f"Please answer the following {len(rubric.get('questions', []))} questions "
        f"about the attached research papers. Respond with the JSON object as instructed.\n\n{q_block}"
    )

    # Lazy import to avoid circular dependency (challenges imports participants)
    from ..challenges import SUPPORTED_MODELS
    model_spec = SUPPORTED_MODELS.get(model_id)

    # Determine caller type
    if custom_base_url and custom_api_key:
        caller = "openai_compat"
        base_url = custom_base_url
        api_key = custom_api_key
        provider_label = f"Custom ({model_id})"
    elif model_spec:
        caller = model_spec["caller"]
        base_url = model_spec.get("base_url", "")
        provider = model_spec["provider"]
        provider_label = provider.title()
        api_key = _PROVIDER_KEYS.get(provider, lambda: "")()
    else:
        raise ValueError(f"Unknown model: {model_id} (not in SUPPORTED_MODELS and no custom credentials)")

    if caller == "gemini":
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

    elif caller == "openai_compat":
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
        raw, elapsed_ms = time_ms(
            call_openai_compatible, base_url, api_key, model_id, messages, max_tokens, provider_label
        )

    elif caller == "anthropic":
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

    else:
        raise ValueError(f"Unknown caller type: {caller}")

    try:
        answers = parse_json_response(raw)
    except Exception:
        answers = {
            "responses": [
                {"question_id": q["id"], "answer": raw[:500]}
                for q in rubric.get("questions", [])
            ]
        }
    return answers, elapsed_ms
