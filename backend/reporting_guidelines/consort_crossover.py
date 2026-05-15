"""CONSORT cross-over extension — reporting-guideline checklist for cross-over RCTs.

Sources combined:

- Hopewell S, Chan AW, Collins GS, Hróbjartsson A, Moher D, Schulz KF, et al.
  "CONSORT 2025 Statement: updated guideline for reporting randomised trials."
  BMJ. 2025; 388:e081123. https://dx.doi.org/10.1136/bmj-2024-081123
  (Base 30 items reused from :mod:`backend.reporting_guidelines.consort2025`.)
- Dwan K, Li T, Altman DG, Elbourne D. "CONSORT 2010 statement: extension to
  randomised crossover trials." BMJ 2019; 366: l4378.
  https://doi.org/10.1136/bmj.l4378
  (Cross-over-specific extension items.)

The cross-over extension to CONSORT 2010 is the authoritative reporting
guideline for cross-over trials. A CONSORT 2025 cross-over extension has not
yet been published; we combine the 2025 base checklist with the cross-over
extension items, framing the latter in the same checklist-item shape so they
fit the same scoring pipeline (one LLM call → per-item adhered/evidence).

Items legitimately not applicable to a given trial return ``adhered=null`` so
they don't inflate or deflate the proportion.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..annotator import _call_with_pdf
from . import consort2025

logger = logging.getLogger("rubricgen")


# Cross-over-specific extension items, adapted from Dwan et al. 2019.
# Numbered with an "X-" prefix so they sort visibly distinct from base items
# (and won't ever collide with future CONSORT 2025 item IDs).
CROSSOVER_EXTENSION_ITEMS: list[dict[str, str]] = [
    {"id": "X-1", "section": "Title and abstract", "topic": "Crossover design identification",
     "description": "Identification as a cross-over trial in the title and/or abstract"},
    {"id": "X-2", "section": "Methods", "topic": "Trial design — periods and sequences",
     "description": "Description of the cross-over design (number of periods, number of treatment sequences, allocation of participants to sequences)"},
    {"id": "X-3", "section": "Methods", "topic": "Washout period",
     "description": "Length and nature of the washout period(s) between treatment periods, including the rationale for the chosen washout length"},
    {"id": "X-4", "section": "Methods", "topic": "Carryover effects",
     "description": "Whether and how the possibility of carryover effects between treatment periods was considered in the design and analysis"},
    {"id": "X-5", "section": "Methods", "topic": "Period effects",
     "description": "Whether period effects (e.g., time-related changes in the outcome) were considered in the design and analysis"},
    {"id": "X-6", "section": "Methods", "topic": "Outcomes — per period",
     "description": "Pre-specified primary and secondary outcomes including how each outcome was measured in each period"},
    {"id": "X-7", "section": "Methods", "topic": "Sample size — paired design",
     "description": "Sample size calculation accounting for the paired (within-participant) nature of the cross-over design"},
    {"id": "X-8", "section": "Methods", "topic": "Sequence randomization",
     "description": "Method used to randomise participants to the sequence of treatments (not just to a single intervention)"},
    {"id": "X-9", "section": "Methods", "topic": "Statistical methods — paired analysis",
     "description": "Statistical methods used for the cross-over analysis — paired comparison (within-participant), and how period and carryover effects were assessed and/or modelled"},
    {"id": "X-10", "section": "Methods", "topic": "First-period-only contingency",
     "description": "Pre-specified plan for handling the analysis if carryover is detected (e.g., reporting first-period-only results)"},
    {"id": "X-11", "section": "Results", "topic": "Flow diagram — per period",
     "description": "Flow diagram showing the number of participants in each sequence, completing each period, and contributing to each comparison"},
    {"id": "X-12", "section": "Results", "topic": "Period-specific losses",
     "description": "Losses and exclusions after randomisation, broken down by period and/or sequence"},
    {"id": "X-13", "section": "Results", "topic": "Baseline data per sequence",
     "description": "Baseline characteristics presented by sequence group (not just by intervention) so any sequence imbalance is visible"},
    {"id": "X-14", "section": "Results", "topic": "Period-specific outcome data",
     "description": "For each period and sequence, the number of participants analysed and the outcome data"},
    {"id": "X-15", "section": "Results", "topic": "Paired effect estimate",
     "description": "Effect estimate with 95% CI from the paired (within-participant) analysis"},
    {"id": "X-16", "section": "Results", "topic": "Carryover assessment",
     "description": "Results of any test for carryover or period effects, and the implications for the primary analysis"},
]


# Combined item list: base CONSORT 2025 items followed by cross-over extension items.
ITEMS: list[dict[str, str]] = consort2025.ITEMS + CROSSOVER_EXTENSION_ITEMS


_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing adherence of a "
    "**cross-over** randomised trial report to a combined checklist: the "
    "CONSORT 2025 base items plus the CONSORT 2010 cross-over extension items "
    "(Dwan et al. 2019). Read the PDF carefully. For each checklist item, "
    "decide whether the trial report reports the required information. Be "
    "strict but fair: an item is adhered only if the information is actually "
    "present (not merely referenced as 'available elsewhere' unless the paper "
    "provides a usable pointer). If an item is genuinely not applicable to "
    "this trial, mark it N/A. Return ONLY a valid JSON object — no preamble, "
    "no markdown fences."
)


def build_prompt(classification: dict[str, str],
                 extracted_fields: dict[str, str] | None = None) -> str:
    """Assemble the single-call CONSORT prompt covering base + cross-over items."""
    study_type = classification.get("study_type", "Crossover Trial")
    ctx_json = json.dumps(extracted_fields or {}, indent=2) if extracted_fields else "(no pre-extracted fields)"

    base_lines = []
    for it in consort2025.ITEMS:
        base_lines.append(
            f"- **{it['id']}** ({it['section']} — {it['topic']}): {it['description']}"
        )
    ext_lines = []
    for it in CROSSOVER_EXTENSION_ITEMS:
        ext_lines.append(
            f"- **{it['id']}** ({it['section']} — {it['topic']}): {it['description']}"
        )

    shape_entries = []
    for it in ITEMS:
        shape_entries.append(
            f'  "{it["id"]}": {{"adhered": true|false|null, "evidence": "short quote or ... \'N/A\' if not applicable"}}'
        )
    shape = "{\n" + ",\n".join(shape_entries) + "\n}"

    return f"""Assess this **{study_type}** report against the combined CONSORT 2025 + cross-over extension checklist.

Context (fields already extracted from the paper):
{ctx_json}

CONSORT 2025 base items:
{chr(10).join(base_lines)}

CONSORT cross-over extension items (Dwan et al. 2019):
{chr(10).join(ext_lines)}

For each item, return:
- ``adhered = true`` if the paper reports the required information,
- ``adhered = false`` if the paper should report it but does not,
- ``adhered = null`` if the item is legitimately not applicable to this trial.
- ``evidence`` is a brief quote (≤ 25 words) from the paper, or a one-line
  reason for a false/null judgement.

Return a JSON object with exactly this shape:
{shape}

Return only the JSON object."""


def run(pdf_bytes: bytes,
        extracted_fields: dict[str, str],
        classification: dict[str, str]) -> dict[str, Any]:
    """Run combined CONSORT 2025 + cross-over extension adherence check.

    Returns ``{items: {id: {adhered, evidence}}, adhered, applicable,
    proportion, total}``.
    """
    prompt = build_prompt(classification, extracted_fields)
    raw = _call_with_pdf(pdf_bytes, prompt, max_tokens=12288)

    items_out: dict[str, dict[str, Any]] = {}
    for it in ITEMS:
        entry = raw.get(it["id"]) or {}
        adhered = entry.get("adhered")
        if isinstance(adhered, str):
            low = adhered.strip().lower()
            if low in ("true", "yes", "y", "1"):
                adhered = True
            elif low in ("false", "no", "n", "0"):
                adhered = False
            elif low in ("na", "n/a", "null", "none", ""):
                adhered = None
            else:
                adhered = None
        evidence = str(entry.get("evidence") or "").strip()
        items_out[it["id"]] = {"adhered": adhered, "evidence": evidence,
                                "section": it["section"], "topic": it["topic"],
                                "description": it["description"]}

    applicable = [v for v in items_out.values() if v["adhered"] is not None]
    adhered_count = sum(1 for v in applicable if v["adhered"] is True)
    applicable_count = len(applicable)
    proportion = (adhered_count / applicable_count) if applicable_count else 0.0

    return {
        "items": items_out,
        "adhered": adhered_count,
        "applicable": applicable_count,
        "total": len(ITEMS),
        "proportion": round(proportion, 3),
    }


def prompt_catalog() -> dict[str, Any]:
    """Return the prompt template + items table for the developer icon."""
    import inspect
    return {
        "guideline": "CONSORT 2025 + cross-over extension (Dwan et al. 2019)",
        "citation": (
            "Hopewell S et al. BMJ 2025; 388:e081123 (base); "
            "Dwan K, Li T, Altman DG, Elbourne D. BMJ 2019; 366: l4378 (cross-over extension)."
        ),
        "system_prompt": _SYSTEM_PROMPT,
        "base_items": consort2025.ITEMS,
        "crossover_extension_items": CROSSOVER_EXTENSION_ITEMS,
        "items": ITEMS,
        "prompt_template": build_prompt(
            {"study_type": "Crossover Trial"},
            {"(example field)": "<value>"},
        ),
        "scoring_code": inspect.getsource(run),
    }
