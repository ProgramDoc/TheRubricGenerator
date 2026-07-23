"""GRADE indirectness — body-of-evidence auto-assessor (hybrid path).

The GRADE engine (:mod:`grade`) treats indirectness as a plain integer input
(0/1/2). Usually the reviewer supplies it; when they don't, this module
auto-judges it for a **body of evidence** with one model call and hands the level
back to the engine. Reviewer input always wins — the orchestrator only calls this
when ``indirectness_levels is None``.

It deliberately reuses the single-study indirectness machinery in
:mod:`backend.indirectness` — the same four PICO subdomains, the same 4-level
judgement vocabulary, and the **same count-based severity decision tree**
(``_judgement_severity``) — so the two surfaces can never drift. The only
difference is the input: the single-study tool reads one PDF; a body of evidence
has many studies and no single PDF, so this judges a **text description** of the
pooled body (outcome name, comparison, favourable direction, and any aggregated
study-level PICO context) against the reviewer's target PICO.

Pure-severity logic lives in ``backend.indirectness``; this file only builds the
body-level prompt and marshals the model call. Import is lazy so ``grade`` /
``grade_prep`` stay importable without the model/RoB dependency chain.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger("grade_indirectness")


_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing the GRADE "
    "indirectness domain for a BODY OF EVIDENCE (a pooled meta-analytic estimate "
    "across several studies for one outcome), not a single study. For each of the "
    "four PICO subdomains (Population, Intervention, Comparison, Outcome), judge "
    "how directly the pooled evidence applies to the specified target question on "
    "a 4-level scale: 'direct', 'probably_direct', 'probably_not_direct', or "
    "'not_direct'. Per GRADE guidance, do NOT rate down unless the mismatch is "
    "likely to lead to meaningful, systematic differences in the effect estimate. "
    "Surrogate outcomes (HbA1c, LDL, bone density, progression-free survival, "
    "etc.) should be 'probably_not_direct' or worse unless a strong, "
    "well-established correlation with patient-important outcomes is documented. "
    "Also flag whether the outcome is a surrogate. Return ONLY a valid JSON "
    "object — no preamble, no markdown fences."
)


def _format_target_pico(target_pico: Optional[dict]) -> str:
    from backend.indirectness import _format_target_pico as _f
    return _f(target_pico)


def build_body_prompt(target_pico: Optional[dict], body_context: dict[str, Any]) -> str:
    """Build the body-level indirectness prompt.

    ``body_context`` describes the pooled body: ``outcome_name``, ``comparison``,
    ``measure``, ``favorable_direction``, ``k``, and optional ``study_context`` (a
    list/blob of per-study population/intervention notes the caller aggregated).
    """
    from backend.indirectness import SUBDOMAINS

    target_block = _format_target_pico(target_pico)
    ctx = {k: body_context.get(k) for k in
           ("outcome_name", "comparison", "measure", "favorable_direction", "k")
           if body_context.get(k) is not None}
    study_ctx = body_context.get("study_context")
    ctx_json = json.dumps(ctx, indent=2)
    study_block = ""
    if study_ctx:
        study_block = ("\nPer-study PICO context (aggregated across the body):\n"
                       + json.dumps(study_ctx, indent=2)[:6000])

    sub_lines = [f"\n**{s['label']} ({s['id']})**\nGuidance: {s['guidance']}" for s in SUBDOMAINS]
    shape = "{\n"
    for s in SUBDOMAINS:
        shape += f'  "{s["id"]}": "direct|probably_direct|probably_not_direct|not_direct",\n'
        shape += f'  "{s["id"]}_rationale": "1-2 sentences",\n'
    shape += '  "primary_outcome_is_surrogate": true|false,\n'
    shape += '  "surrogate_rationale": "If the outcome is a surrogate, briefly explain."\n}'

    return f"""Assess **GRADE indirectness** for this pooled body of evidence.

Body of evidence:
{ctx_json}{study_block}

{target_block}

Subdomains to judge:
{"".join(sub_lines)}

Return a JSON object with exactly this shape:
{shape}

Default to 'probably_direct' rather than 'direct' when there is meaningful uncertainty; reserve 'not_direct' for clear, substantial mismatches."""


def assess_body(target_pico: Optional[dict], body_context: dict[str, Any]
                ) -> tuple[dict[str, Any], str, int, str]:
    """Auto-assess indirectness for one body. One model call.

    Returns ``(per_subdomain, severity_label, downgrade_levels, explanation)`` — the
    same shape the single-study ``indirectness.run`` returns, so callers can render
    it identically. ``downgrade_levels`` is the integer the GRADE engine consumes.
    """
    from backend import helpers as helpers_mod
    from backend.indirectness import (
        SUBDOMAINS, _judgement_severity, _normalize_judgement, severity_explanation)

    prompt = build_body_prompt(target_pico, body_context)
    raw_text = helpers_mod.call_anthropic(
        [{"role": "user", "content": prompt}], system=_SYSTEM_PROMPT, max_tokens=2048)
    raw = helpers_mod.parse_json_response(raw_text)

    per_sub: dict[str, Any] = {}
    judgements: dict[str, str] = {}
    for sub in SUBDOMAINS:
        sid = sub["id"]
        j = _normalize_judgement(str(raw.get(sid, "")))
        per_sub[sid] = {"judgement": j, "rationale": str(raw.get(f"{sid}_rationale", "")).strip(),
                        "label": sub["label"]}
        judgements[sid] = j

    per_sub["primary_outcome_is_surrogate"] = bool(raw.get("primary_outcome_is_surrogate", False))
    per_sub["surrogate_rationale"] = str(raw.get("surrogate_rationale", "")).strip()

    severity, levels, counts = _judgement_severity(judgements)
    per_sub["counts"] = counts
    explanation = severity_explanation(severity, counts,
                                       {s["id"]: judgements[s["id"]] for s in SUBDOMAINS})
    return per_sub, severity, levels, explanation
