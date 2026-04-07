"""Rubric Generator Agent. Given a set of PDFs and a theme, generates
a structured benchmark rubric with 10 questions. Uses the active generator
skill from agent_skills."""

from ..helpers import call_anthropic, parse_json_response, time_ms


def run_generator_agent(papers_b64: list[dict], theme: str, skill: dict,
                        difficulty: str | None = None,
                        daily_composition: dict | None = None,
                        questions_per_paper: int = 5,
                        max_tokens: int = 16384) -> tuple[dict, int]:
    """
    papers_b64: list of {filename, b64} — the PDFs in the challenge
    theme: the challenge theme (e.g. "RCT methodology in oncology")
    skill: dict with at least 'prompt_text' (from get_active_skill)
    difficulty: optional key into DIFFICULTY_LEVELS for individual tests
    daily_composition: optional dict {easy_breezy: 2, minor_league: 2, professional: 4, jedi: 2}
        for the Daily AI Researcher Challenge

    Returns: (rubric_dict, elapsed_ms)
    """
    if not papers_b64:
        raise ValueError("No papers provided to generator")

    # Lazy import to avoid circular (challenges imports this module)
    from ..challenges import DIFFICULTY_LEVELS

    difficulty_block = ""
    if daily_composition:
        # Daily AI Researcher Challenge: mixed difficulty composition
        comp_lines = "\n".join(f"- {count} {level.replace('_',' ').title()} questions"
                               for level, count in daily_composition.items())
        total_q = sum(daily_composition.values())
        difficulty_block = (
            f"\n\nDAILY AI RESEARCHER CHALLENGE — MIXED DIFFICULTY COMPOSITION\n"
            f"Generate EXACTLY {total_q} questions with this exact breakdown:\n{comp_lines}\n\n"
            f"Each question MUST include a 'difficulty' field in the JSON set to one of: "
            f"'easy_breezy', 'minor_league', 'professional', 'jedi'.\n"
            f"Easy Breezy = simple field extraction (PICO, sample size).\n"
            f"Minor League = study classification and design taxonomy.\n"
            f"Professional = methodological appraisal and validity.\n"
            f"Jedi = adversarial expert appraisal, subtle methodology distinctions."
        )
    elif difficulty and difficulty in DIFFICULTY_LEVELS:
        d = DIFFICULTY_LEVELS[difficulty]
        total_q = len(papers_b64) * questions_per_paper
        difficulty_block = (
            f"\n\nDIFFICULTY LEVEL: {d['label']}\n"
            f"{d['hint']}\n"
            f"Generate exactly {total_q} questions ({questions_per_paper} per paper). "
            f"Each question MUST include a 'paper_ref' field with the filename of the paper it targets. "
            f"The difficulty level controls the COGNITIVE COMPLEXITY of each question."
        )
    else:
        # Default: dynamic question count based on paper count
        if not daily_composition:
            total_q = len(papers_b64) * questions_per_paper
            difficulty_block = (
                f"\n\nGenerate exactly {total_q} questions ({questions_per_paper} per paper). "
                f"Each question MUST include a 'paper_ref' field with the filename of the paper it targets."
            )

    # Phase 8: comparative rubric type (multi-paper synthesis)
    comparative_block = ""
    if not daily_composition and not difficulty and len(papers_b64) >= 2:
        # Check if caller requested comparative type (passed via theme convention)
        if theme and theme.startswith("__comparative__"):
            comparative_block = (
                "\n\nCOMPARATIVE RUBRIC — MULTI-PAPER SYNTHESIS\n"
                "You are evaluating an LLM's ability to synthesize findings across multiple research papers.\n\n"
                "Generate 10 questions that require cross-paper comparison:\n"
                "- 2 questions: Identify contradictions or conflicting findings between papers\n"
                "- 2 questions: Compare methodological approaches (study design, sample size, controls)\n"
                "- 2 questions: Assess population overlap and generalizability across studies\n"
                "- 2 questions: Synthesize outcome measures and effect sizes across papers\n"
                "- 2 questions: Critical appraisal — which study provides stronger evidence and why\n\n"
                "Each question MUST reference at least 2 papers by their filename.\n"
                "Include a 'paper_refs' field in each question JSON listing referenced paper filenames.\n"
                "Set rubric_type to 'comparative' in the output."
            )

    # Build multi-document user message
    content: list[dict] = []
    for i, p in enumerate(papers_b64):
        content.append({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": p["b64"]},
        })
    content.append({
        "type": "text",
        "text": (
            f"Challenge theme: {theme}\n\n"
            f"Papers in this challenge:\n"
            + "\n".join(f"- {p['filename']}" for p in papers_b64)
            + difficulty_block
            + comparative_block
            + "\n\nGenerate the benchmark rubric as specified. Respond with JSON only."
        ),
    })

    messages = [{"role": "user", "content": content}]

    raw, elapsed_ms = time_ms(
        call_anthropic, messages, skill["prompt_text"], max_tokens
    )
    rubric = parse_json_response(raw)

    # Ensure total_max_points is correct
    rubric["total_max_points"] = sum(
        q.get("max_points", 0) for q in rubric.get("questions", [])
    )
    rubric["theme"] = theme
    return rubric, elapsed_ms
