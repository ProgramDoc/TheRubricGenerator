"""Per-outcome extraction — the list of outcomes a paper can be appraised for.

Risk-of-bias instruments are outcome-specific. RoB 2 domain 4 (measurement of
the outcome) and domain 5 (selection of the reported result) genuinely differ
between outcomes in the same paper, and GRADE rates risk of bias per outcome —
one trial can be Low for mortality and High for an unblinded subjective outcome.
This module produces the candidate list a reviewer picks from, so the appraiser
can run once per selected outcome instead of once per paper.

Deliberately *not* under ``rob_tools/``: the outcome an assessment is scoped to
feeds the risk-of-bias tool, the indirectness assessment, and the imprecision
assessment alike, so it is tool-agnostic by construction. It sits beside
``indirectness.py`` / ``imprecision.py`` for the same reason.

The extraction is one LLM call and mirrors the shape of
``rob_tools/quadas3.py:extract_estimates`` — synthetic ids assigned in Python,
every field coerced to a stripped string, an empty list when the model returns
something unusable. The caller always has a fallback: ``quality_appraisal.py``
falls through to ``pick_primary_outcome`` when this returns nothing.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .annotator import _call_with_pdf

logger = logging.getLogger("rubricgen")

# Caps mirror pick_primary_outcome's 200-char budget — these strings end up
# inside every RoB / indirectness / imprecision prompt, so they stay compact.
_NAME_CAP = 120
_DESCRIPTION_CAP = 200
_MEASURE_CAP = 120
_TIMING_CAP = 80
_LABEL_CAP = 200

# Free text from the model, normalized to the vocabulary imprecision.py reasons
# about. Anything unrecognized becomes "" and the caller falls back to its
# lexical heuristic rather than trusting a guess.
_OUTCOME_TYPES = ("binary", "continuous", "time-to-event")


_OUTCOME_EXTRACTION_PROMPT_HEADER = """Identify every distinct outcome the attached study reports that could be separately appraised for risk of bias.

Risk-of-bias instruments (RoB 2, ROBINS-I) are outcome-specific: domain 4 (measurement of the outcome) and domain 5 (selection of the reported result) genuinely differ between outcomes in the same paper. List the outcomes a reviewer would appraise separately.

For each outcome return:
- ``name`` — short label suitable for a UI checkbox (≤ 80 chars), e.g. "All-cause mortality"
- ``description`` — the outcome as the paper defines it (≤ 200 chars)
- ``measure`` — how it was measured; the instrument, scale, or metric used
- ``timing`` — the timepoint or follow-up window, as reported
- ``outcome_type`` — one of "binary", "continuous", "time-to-event", or "" if unclear
- ``is_primary`` — true only for the paper's stated primary outcome(s)

Rules:
- Do NOT split one outcome into the several statistics reported for it. A hazard ratio, a Kaplan-Meier curve, and a median survival for overall survival are ONE outcome.
- Composite outcomes are one outcome. Do not decompose them into components unless the components are themselves pre-specified outcomes.
- Do NOT list every adverse-event tally as a separate outcome. Include a safety outcome only where the paper pre-specifies a named one.
- List the primary outcome first.
- If the paper states no outcomes at all, return an empty list.

Return ONLY a JSON object of the shape:
{
  "outcomes": [
    {"name": "...", "description": "...", "measure": "...", "timing": "...", "outcome_type": "...", "is_primary": true}
  ]
}
"""


def extract_outcomes(pdf_bytes: bytes,
                     extracted_fields: dict[str, str] | None = None,
                     ) -> list[dict[str, Any]]:
    """Single LLM call returning every appraisable outcome in the paper.

    Used by the run modal's per-paper outcome selector. Each outcome gets a
    synthetic ``id`` (1..N) assigned here so the frontend can check / uncheck
    them without ambiguity, and so the stored result row has a stable per-paper
    outcome key.

    Returns ``[]`` rather than raising when the model returns nothing usable —
    the caller falls back to the auto-picked primary outcome.
    """
    extracted_fields = extracted_fields or {}
    ctx_json = (json.dumps(extracted_fields, indent=2) if extracted_fields
                else "(no pre-extracted fields)")
    prompt = _OUTCOME_EXTRACTION_PROMPT_HEADER + (
        "\n\nContext (fields already extracted from the paper):\n" + ctx_json
    )
    raw = _call_with_pdf(pdf_bytes, prompt, max_tokens=8192)

    outcomes_raw = raw.get("outcomes")
    if not isinstance(outcomes_raw, list):
        return []

    out: list[dict[str, Any]] = []
    for oc in outcomes_raw:
        if not isinstance(oc, dict):
            continue
        # Numbered over the entries we keep, so a malformed entry doesn't leave
        # a gap in the ids that end up stored as outcome_id and exported.
        idx = len(out) + 1
        otype = str(oc.get("outcome_type") or "").strip().lower()
        clean = {
            "id": idx,
            "name": str(oc.get("name") or "").strip()[:_NAME_CAP],
            "description": str(oc.get("description") or "").strip()[:_DESCRIPTION_CAP],
            "measure": str(oc.get("measure") or "").strip()[:_MEASURE_CAP],
            "timing": str(oc.get("timing") or "").strip()[:_TIMING_CAP],
            "outcome_type": otype if otype in _OUTCOME_TYPES else "",
            "is_primary": bool(oc.get("is_primary")),
            "source": "extracted",
        }
        if not clean["name"]:
            # Synthesize a label from whatever the model did give us.
            bits = [b for b in (clean["measure"], clean["timing"]) if b]
            clean["name"] = (clean["description"] or " — ".join(bits)
                             or f"Outcome {idx}")[:_NAME_CAP]
        out.append(clean)
    return out


def outcome_label(outcome: dict[str, Any]) -> str:
    """The single string threaded into the RoB / indirectness / imprecision prompts.

    Composed rather than bare so the assessor knows *which* measurement at
    *which* timepoint it is rating — two outcomes can share a name and differ
    only in follow-up. Note this is the prompt string, not the join key:
    downstream consumers grouping studies by outcome should use the outcome's
    ``name``, which stays a clean short label.
    """
    name = (outcome.get("name") or outcome.get("description") or "").strip()
    bits = [name]
    measure = (outcome.get("measure") or "").strip()
    timing = (outcome.get("timing") or "").strip()
    if measure:
        bits.append(f"measured as {measure}")
    if timing:
        bits.append(f"at {timing}")
    return " — ".join(b for b in bits if b)[:_LABEL_CAP]


def prompt_catalog() -> dict[str, Any]:
    """Developer-view payload: the exact prompt and the output contract."""
    return {
        "name": "Outcome extraction",
        "purpose": ("Lists the outcomes a paper can be separately appraised for, "
                    "so risk of bias, indirectness, and imprecision can be rated "
                    "once per outcome rather than once per paper."),
        "prompt": _OUTCOME_EXTRACTION_PROMPT_HEADER,
        "outcome_types": list(_OUTCOME_TYPES),
        "output_fields": ["id", "name", "description", "measure", "timing",
                          "outcome_type", "is_primary", "source"],
        "label_composition": ("name — measured as {measure} — at {timing}, "
                              f"capped at {_LABEL_CAP} characters"),
    }
