"""Table 2 — outcomes extraction wiring.

Connects the Table 2 extraction prompts to the platform's PDF-aware model caller
(``annotator._call_with_pdf`` — same 3-stage oversize fallback the annotator and the
quality-appraisal pipeline reuse) and composes the results into Table 2 rows via the
pure-Python core in ``table2.py``.

Two extraction passes (each a standalone callable, so extraction is usable in
isolation):

* ``extract_outcomes(pdf_bytes, ...)`` -> the ``outcomes[]`` array (one object per
  outcome x comparison x timepoint) — the one genuinely new build item.
* ``extract_study_level(pdf_bytes)`` -> the study-level characteristics (used only in
  isolation mode, when no upstream tags were injected).

``build_table2_from_pdf(pdf_bytes, injected=...)`` is the dual-mode orchestrator:
with injected tags it assembles with zero model calls (seeding a 1-element
``outcomes[]`` from single-outcome fields when needed); in isolation it runs both
passes then assembles.

Prompt text + output schema are the framework-free contract in
``docs/shareable/table2_evidence_table_shareable.md`` (§6). This module is the only
place a model is touched; all derivation lives in ``table2.py``.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .. import annotator as annotator_mod
from .table2 import assemble_table2, merge_injected_and_extracted

logger = logging.getLogger("rubricgen")

# max_tokens: an evidence-rich paper can report many outcomes x comparisons x
# timepoints; 8192 keeps the JSON from truncating (mirrors prefill_fields).
_OUTCOMES_MAX_TOKENS = 8192
_STUDY_LEVEL_MAX_TOKENS = 4096


# ---------------------------------------------------------------------------
# Prompts — the exact contract from the shareable doc, combined into a single
# string because _call_with_pdf sends system="" (all instruction in the user turn).
# ---------------------------------------------------------------------------

_OUTCOMES_PROMPT = """\
You are a clinical-evidence extraction service building a per-study evidence table
(one row per outcome x comparison x timepoint). You transcribe results EXACTLY AS
REPORTED. You never pool, average, re-analyse, or compute a new effect. You never
invent numbers.

Hard rules:
1. One object per (outcome x comparison x timepoint). Same outcome at 3 timepoints
   => 3 objects. Two arm comparisons for one outcome => 2 objects.
2. Report effect_estimate, ci_lower, ci_upper, p_value AS STATED. If a value is not
   reported, set it to null. NEVER derive a CI from a p-value or a p-value from a CI.
   NEVER fabricate a plausible number.
3. Preserve p-value inequalities. For "p<0.001" set p_value=0.001 AND p_operator="lt".
   For an exact "p=0.03" set p_value=0.03 AND p_operator="eq". Operators: eq, lt, gt,
   le, ge.
4. Do not compute or pool anything. If the paper itself reports a pooled/meta-analytic
   estimate (e.g. this study IS a meta-analysis), transcribe that reported pooled
   number - but never create one.
5. source_quote MUST be a verbatim span copied from the paper stating the effect. Do
   not paraphrase. If you cannot find a verbatim statement, do not emit the row.
6. Narrative-only outcomes (words, no numeric effect): set effect_metric="narrative",
   put the statement in effect_estimate, leave ci_lower/ci_upper/p_value null, set
   direction from the text.
7. Subgroup / secondary-population results: set is_subgroup=true and fill
   subgroup_label. Keep the main (all-participants) analysis as its own row.
8. effect_metric is one of: HR, OR, RR, IRR, MD, SMD, RD, narrative. Ratio metrics
   (HR/OR/RR/IRR) have null value 1; difference metrics (MD/SMD/RD) have null value 0.
9. direction is one of: favours_intervention, favours_comparator, no_difference,
   not_estimable. "favours_intervention" means the effect favours the intervention arm
   named in `comparison`. For an adverse outcome (mortality, relapse) a value below the
   null favours the intervention; for a desirable outcome (survival, response) a value
   above the null favours the intervention.
10. confidence (high|moderate|low) is YOUR confidence that this row faithfully
    transcribes the paper.

Study context: {study_context}
Intervention arm(s): {intervention}
Comparator arm(s): {comparator}
Outcomes of interest (extract ALL reported; this list is guidance, not a limit):
{outcomes_of_interest}

Read the attached study and extract every reported outcome result as one object per
(outcome x comparison x timepoint). Follow every rule above.

Return ONLY a single JSON object of exactly this shape. No prose, no markdown:

{
  "outcomes": [
    {
      "name": "",
      "instrument": null,
      "timing": null,
      "comparison": "",
      "effect_metric": null,
      "effect_estimate": null,
      "direction": "not_estimable",
      "ci_lower": null,
      "ci_upper": null,
      "p_value": null,
      "p_operator": "eq",
      "source_quote": "",
      "confidence": "low",
      "is_subgroup": false,
      "subgroup_label": null
    }
  ]
}
"""

_STUDY_LEVEL_PROMPT = """\
You are a clinical-evidence extraction service. You extract study-level
characteristics for a per-study evidence table. You transcribe what the paper states
and never fabricate. If a field is not reported, return null. You do not classify risk
of bias and you do not extract per-outcome results here.

Field guidance:
- citation_authors: author list as printed (used with year to form the study id).
- citation_year: publication year (integer).
- citation_title: the paper's title.
- study_type: the design label best supported by the text (e.g. "Randomized
  Controlled Trial", "Cohort Study", "Diagnostic Accuracy", "SR with Meta-Analysis").
- population_participants: one-line description of who was studied.
- sample_size_total: total N analysed (integer). null if not reported.
- included_studies_n: for a systematic review, the number of included studies. null otherwise.
- population_intervention_exposure: the intervention/exposure arm(s), as reported.
- population_comparator: the comparator/control arm(s), as reported.
- population_outcomes: the outcomes the study assessed, as a short list.
- eligibility_threshold: any numeric eligibility cutoff for enrolment
  (e.g. "BFI >= 36", "eGFR < 30"). null if none.
- statistical_method: the primary analysis framework / statistical method
  (e.g. "Cox proportional hazards", "mixed-effects model", "log-rank"). null if none.

Read the attached study and return ONLY a single JSON object of exactly this shape.
No prose, no markdown:

{
  "citation_authors": null,
  "citation_year": null,
  "citation_title": null,
  "study_type": null,
  "population_participants": null,
  "sample_size_total": null,
  "included_studies_n": null,
  "population_intervention_exposure": null,
  "population_comparator": null,
  "population_outcomes": null,
  "eligibility_threshold": null,
  "statistical_method": null
}
"""


def _fill(template: str, **kwargs: str) -> str:
    """Substitute {placeholder} markers without disturbing the literal JSON braces."""
    out = template
    for key, val in kwargs.items():
        out = out.replace("{" + key + "}", val)
    return out


# ---------------------------------------------------------------------------
# Extraction passes (the ONLY model calls)
# ---------------------------------------------------------------------------

def extract_outcomes(
    pdf_bytes: bytes,
    *,
    study_context: str = "",
    intervention: str = "",
    comparator: str = "",
    outcomes_of_interest: str = "all reported outcomes",
) -> list[dict[str, Any]]:
    """Run the outcomes[] pass — one model call. Returns a list of outcome objects.

    Never raises on an empty/oddly-shaped response — returns [] so the caller can
    still emit study-level rows. Oversized-PDF handling is inherited from
    ``_call_with_pdf``. Note: the chunked-text fallback for very large PDFs merges
    per-field and keeps only the first chunk's ``outcomes`` array (a known v1
    limitation); the fast PDF-as-document path handles the large majority of papers.
    """
    prompt = _fill(
        _OUTCOMES_PROMPT,
        study_context=study_context or "not provided",
        intervention=intervention or "as reported in the paper",
        comparator=comparator or "as reported in the paper",
        outcomes_of_interest=outcomes_of_interest or "all reported outcomes",
    )
    result = annotator_mod._call_with_pdf(pdf_bytes, prompt, max_tokens=_OUTCOMES_MAX_TOKENS)
    return _coerce_outcomes(result)


def extract_study_level(pdf_bytes: bytes) -> dict[str, Any]:
    """Run the study-level characteristics pull — one model call. Returns a flat dict.

    Used only in isolation mode (no injected tags). Never raises on an odd response —
    returns {} so assembly can proceed on whatever else is available.
    """
    result = annotator_mod._call_with_pdf(
        pdf_bytes, _STUDY_LEVEL_PROMPT, max_tokens=_STUDY_LEVEL_MAX_TOKENS)
    return result if isinstance(result, dict) else {}


def _coerce_outcomes(result: Any) -> list[dict[str, Any]]:
    """Pull the outcomes list out of whatever the model returned, tolerantly."""
    if isinstance(result, list):
        outcomes = result
    elif isinstance(result, dict):
        outcomes = result.get("outcomes", [])
    else:
        outcomes = []
    return [o for o in outcomes if isinstance(o, dict)]


# ---------------------------------------------------------------------------
# Context helper + dual-mode orchestrator
# ---------------------------------------------------------------------------

def _ctx_from_tags(tags: dict[str, Any]) -> dict[str, str]:
    """Build the outcomes-pass prompt context from study-level tags."""
    tags = tags or {}
    bits = [str(tags[k]) for k in ("citation_title", "study_type", "population_participants")
            if tags.get(k)]
    ooi_parts = []
    for k in ("population_outcomes", "primary_outcome_definition", "secondary_outcomes"):
        v = tags.get(k)
        if v:
            ooi_parts.append(", ".join(v) if isinstance(v, (list, tuple)) else str(v))
    return {
        "study_context": " — ".join(bits),
        "intervention": str(tags.get("population_intervention_exposure") or ""),
        "comparator": str(tags.get("population_comparator") or ""),
        "outcomes_of_interest": "; ".join(ooi_parts) or "all reported outcomes",
    }


def build_table2_from_pdf(
    pdf_bytes: bytes,
    injected: Optional[dict[str, Any]] = None,
    *,
    enrich: bool = False,
    rob: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Produce Table 2 rows for one paper, wired to the platform's model caller.

    INJECTED MODE (``injected`` provided): assemble with ZERO model calls (seeding a
    1-element outcomes[] from single-outcome fields when no array was injected). Opt-in
    ``enrich=True`` runs only the outcomes[] pass to discover secondary outcomes;
    injected study-level tags still win. ``rob`` (``{rob_overall, rob_tool}``) may be
    passed explicitly or ride along on ``injected``.

    ISOLATION MODE (``injected`` is None): runs the study-level pull + the outcomes[]
    pass (threading the pulled arms into the outcomes context), then assembles.
    """
    if injected is not None:
        has_outcomes = isinstance(injected.get("outcomes"), list) and injected["outcomes"]
        extracted: dict[str, Any] = {}
        provenance = "injected" if has_outcomes else "seeded"
        if enrich and not has_outcomes:
            extracted = {"outcomes": extract_outcomes(pdf_bytes, **_ctx_from_tags(injected))}
            provenance = "enriched"
        merged = merge_injected_and_extracted(injected, extracted)
        rob = rob or {"rob_overall": merged.get("rob_overall"), "rob_tool": merged.get("rob_tool")}
        return assemble_table2(merged, outcomes=merged.get("outcomes"), rob=rob, provenance=provenance)

    # Isolation mode: extract, then assemble.
    study_level = extract_study_level(pdf_bytes)
    outcomes = extract_outcomes(pdf_bytes, **_ctx_from_tags(study_level))
    rob = rob or {"rob_overall": study_level.get("rob_overall"), "rob_tool": study_level.get("rob_tool")}
    return assemble_table2(study_level, outcomes=outcomes, rob=rob, provenance="extracted")
