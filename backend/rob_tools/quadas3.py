"""QUADAS-3 v1.2 — Risk of bias + applicability for diagnostic test accuracy studies.

Source: QUADAS-3 v1.2 (University of Bristol; Whiting et al.) — the successor
to QUADAS-2 (2011), restructured around four domains and two parallel
judgements per domain (risk of bias + applicability concerns).

Encodes the v1.2 tool as:

- ``DOMAINS`` — 4 domains × signaling questions + applicability flag
  (3 of 4 domains carry an applicability concern; the Analysis domain is RoB-only).
- ``quadas3_domain_judge(signals)`` — pure-Python decision tree per Phase 5
  ("If all signaling questions are Y/PY → Low; any N/PN → High; otherwise II").
- ``quadas3_overall(domain_judgements)`` — Phase 6 aggregation
  ("Any High → High; All Low → Low; otherwise Insufficient information").
- ``run(pdf_bytes, fields, classification, primary_outcome, *, estimate, review_context, progress)``
  — per-domain LLM calls via the annotator's ``_call_with_pdf`` pipeline; each
  call returns BOTH signal answers (RoB) AND applicability concern (where
  applicable) in a single response, so we pay one LLM call per domain.
- ``extract_estimates(pdf_bytes, fields)`` — single LLM call returning all
  numerical sens/spec estimates the paper reports, used by the run modal's
  step-2 estimate selector (Phase 4 of QUADAS-3).

Signaling-question answers are Y / PY / PN / N / NI.
Domain RoB judgements are "Low" / "High" / "Insufficient information".
Applicability judgements are "Low" / "High" / "Insufficient information".

v1 scope: single-test diagnostic accuracy reviews (one index test vs one
reference standard). QUADAS-C (comparative accuracy) is a separate tool and
is out of scope. Phase 5 of QUADAS-3 explicitly allows reviewer judgement to
keep a domain at Low even when a single signaling question answers N/PN; we
take the conservative interpretation here (any N/PN → High) to keep the
decision tree pure and inspectable. Reviewers can read the rationales in the
detail modal to override that judgement in their own write-up.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from fastapi import HTTPException

from ..annotator import _call_with_pdf
from ..helpers import parse_json_response

logger = logging.getLogger("rubricgen")


SIGNAL_OPTIONS = ("Y", "PY", "PN", "N", "NI")
JUDGEMENTS = ("Low", "High", "Insufficient information")
APPLICABILITY_OPTIONS = ("Low", "High", "Insufficient information")


# ─────────────────────────────────────────────
# Decision trees (pure Python — no LLM)
# ─────────────────────────────────────────────
# QUADAS-3 Phase 5 narrative gives the rule; we encode it conservatively. The
# docx allows reviewer judgement to keep a domain at Low even with one N/PN
# signal, but baking that judgement into a deterministic tree would be
# arbitrary, so we map any N/PN → High and surface the per-signal rationale
# for human review.


def _yes(ans: str) -> bool:
    return ans in ("Y", "PY")


def _no(ans: str) -> bool:
    return ans in ("N", "PN")


def quadas3_domain_judge(signals: dict[str, str]) -> str:
    """Map signaling-question answers (Y/PY/PN/N/NI) to a domain-level RoB
    judgement (Low / High / Insufficient information) per QUADAS-3 Phase 5.

    Rule:
      - All signals Y or PY → "Low"
      - Any N or PN → "High"
      - Any NI without N/PN → "Insufficient information"
      - All NI (or empty) → "Insufficient information"
    """
    answered = [v for v in signals.values()]
    if not answered:
        return "Insufficient information"
    if any(_no(v) for v in answered):
        return "High"
    if all(_yes(v) for v in answered):
        return "Low"
    # Mixed Y/PY + NI → Insufficient information per Phase 5
    return "Insufficient information"


def quadas3_overall(domain_judgements: list[str]) -> str:
    """Aggregate per QUADAS-3 Phase 6.

    - Any domain High → "High"
    - All domains Low → "Low"
    - Otherwise (any II, none High) → "Insufficient information"
    """
    if not domain_judgements:
        return "Insufficient information"
    if any(j == "High" for j in domain_judgements):
        return "High"
    if all(j == "Low" for j in domain_judgements):
        return "Low"
    return "Insufficient information"


def quadas3_applicability_overall(judgements: list[str]) -> str:
    """Aggregate applicability judgements per QUADAS-3 Phase 6 (same rule as RoB).

    Only 3 domains carry applicability (Participants, Index Test, Target
    Condition); Analysis is excluded from the input list.
    """
    return quadas3_overall(judgements)


DOMAIN_JUDGES: dict[int, Callable[[dict[str, str]], str]] = {
    1: quadas3_domain_judge,
    2: quadas3_domain_judge,
    3: quadas3_domain_judge,
    4: quadas3_domain_judge,
}


# ─────────────────────────────────────────────
# Domain definitions — signaling questions transcribed verbatim from QUADAS-3 v1.2
# ─────────────────────────────────────────────
DOMAINS: list[dict[str, Any]] = [
    {
        "id": 1,
        "name": "Participants",
        "has_applicability": True,
        "applicability_question": (
            "Concern that the included participants do not match those in the "
            "ideal test accuracy trial."
        ),
        "applicability_elaboration": (
            "Describe how differences between the included participants (e.g., "
            "presentation, prior testing, setting, intended use of index test) "
            "and the ideal test accuracy trial defined for this review have led "
            "to this judgement."
        ),
        "relevant_fields": [
            "spectrum_of_patients", "verification_bias", "flow_and_timing",
            "population_inclusion", "population_exclusion",
        ],
        "signals": [
            {
                "id": "1.1",
                "text": "Was a single-gate design used?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "A single-gate design enrols one group of participants in "
                    "whom the diagnosis is not yet known (this could include "
                    "multiple groups from different locations, e.g. different "
                    "hospitals). A multi-gate design (case-control) enrols "
                    "participants with known diagnosis and is at higher risk of "
                    "spectrum bias. Answer 'Yes' for single-gate; 'No' for "
                    "multi-gate / case-control."
                ),
            },
            {
                "id": "1.2",
                "text": "Were participants prospectively enrolled?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Prospective enrolment lets investigators standardise the "
                    "test workflow and minimises differential verification. "
                    "Retrospective enrolment from medical records or stored "
                    "samples increases the risk that participants entered the "
                    "two-by-two table for reasons related to the test result."
                ),
            },
            {
                "id": "1.3",
                "text": "Was a consecutive or random sample of participants included?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Consecutive enrolment of all eligible participants in a "
                    "defined window, or random sampling from the eligible pool, "
                    "minimises selection bias. 'No' for convenience samples or "
                    "selection on test-related criteria."
                ),
            },
            {
                "id": "1.4",
                "text": "Is the study group a representative sample of the intended-use population?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Compare the enrolled spectrum (presentation, prior tests, "
                    "comorbidity, prevalence) to the population in which the "
                    "index test would be deployed. 'No' if cases or controls "
                    "are unusually severe / mild / pre-screened relative to the "
                    "intended-use population."
                ),
            },
        ],
    },
    {
        "id": 2,
        "name": "Index Test",
        "has_applicability": True,
        "applicability_question": (
            "Concern that the index test, its conduct, or interpretation does "
            "not match the ideal test accuracy trial."
        ),
        "applicability_elaboration": (
            "Describe how differences between the setting in which the test "
            "was conducted, who conducted and interpreted it, and the ideal "
            "test accuracy trial defined for this review have led to this "
            "judgement."
        ),
        "relevant_fields": [
            "index_test", "blinding_index_to_reference",
            "threshold_effects",
        ],
        "signals": [
            {
                "id": "2.1",
                "text": "Was the index test conducted and interpreted according to the recommended instructions?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Following manufacturer's instructions, published "
                    "protocols, or accepted standard clinical methods. 'No' "
                    "for ad-hoc modifications or undocumented procedure."
                ),
            },
            {
                "id": "2.2",
                "text": "Were the index test results interpreted without knowledge of the reference standard results?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Blinding of the index-test interpreter to the reference "
                    "standard prevents review bias. For automated reads or "
                    "tests interpreted before the reference standard is known, "
                    "this is naturally satisfied."
                ),
            },
            {
                "id": "2.3",
                "text": "Were the index test results interpreted with the same information as would be available when the test is used in practice?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Interpretation should be representative of clinical use. "
                    "'No' if interpreters had extra information (e.g., MRI for "
                    "a screening mammogram) or were deprived of routinely "
                    "available context (e.g., clinical history)."
                ),
            },
            {
                "id": "2.4",
                "text": "If an index test threshold was used, was it standard or pre-specified?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Thresholds derived post-hoc from the data ('data-driven') "
                    "are at high risk of optimistic bias. Pre-specified or "
                    "standard manufacturer thresholds are preferable. Mark "
                    "'NI' if no threshold was used (continuous test reported "
                    "as AUC only)."
                ),
            },
        ],
    },
    {
        "id": 3,
        "name": "Target Condition",
        "has_applicability": True,
        "applicability_question": (
            "Concern that the target condition as defined by the reference "
            "standard does not match the ideal test accuracy trial."
        ),
        "applicability_elaboration": (
            "Describe how any differences between how the target condition "
            "was defined and the ideal test accuracy trial defined for this "
            "review have led to this judgement."
        ),
        "relevant_fields": [
            "reference_standard", "blinding_reference_to_index",
            "flow_and_timing",
        ],
        "signals": [
            {
                "id": "3.1",
                "text": "Does the reference standard adequately identify those with and without the target condition?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "The reference standard should correctly classify all "
                    "participants. 'No' if the reference standard is known to "
                    "be inaccurate (low-sensitivity gold standard, imperfect "
                    "composite) or substantially different from the accepted "
                    "diagnostic criterion."
                ),
            },
            {
                "id": "3.2",
                "text": "Was the target condition assessed in all participants?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Partial verification — applying the reference standard "
                    "only to a subset (e.g., index-positive participants) — "
                    "biases sensitivity and specificity estimates. 'No' if "
                    "verification was selective."
                ),
            },
            {
                "id": "3.3",
                "text": "Was the target condition assessed in the same way in all participants?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Differential verification — different reference "
                    "standards for index-positive vs index-negative — "
                    "introduces bias. 'No' if multiple reference standards "
                    "were used non-randomly."
                ),
            },
            {
                "id": "3.4",
                "text": "Did the reference standard avoid incorporating the index test?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Incorporation bias arises when the index test is part of "
                    "a composite reference standard, inflating apparent "
                    "accuracy. 'No' for any composite reference that uses the "
                    "index test as a component."
                ),
            },
            {
                "id": "3.5",
                "text": "Was the reference standard conducted and interpreted according to the recommended instructions?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Same standard as the index-test version of this question "
                    "but applied to the reference. Followed protocols, "
                    "manufacturer's instructions, or accepted clinical "
                    "criteria → 'Yes'. Ad-hoc reading or undocumented "
                    "procedure → 'No'."
                ),
            },
            {
                "id": "3.6",
                "text": "Were the reference standard results interpreted without knowledge of the index test results?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Blinding of the reference-standard interpreter to the "
                    "index test prevents review bias. For automated or "
                    "labelled-by-default reference standards, this is "
                    "naturally satisfied."
                ),
            },
            {
                "id": "3.7",
                "text": "If a reference standard threshold was used, was it standard or pre-specified?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Same logic as for the index test: data-driven thresholds "
                    "introduce optimistic bias; standard or pre-specified "
                    "thresholds are preferable. 'NI' if no threshold was used."
                ),
            },
            {
                "id": "3.8",
                "text": "Was there an appropriate time interval between index test and reference standard?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "The interval must be short enough that the disease state "
                    "could not have changed between tests. Long delays "
                    "introduce disease-progression bias. The appropriate "
                    "duration is condition-specific (hours for stroke, weeks "
                    "for a slow-growing tumour)."
                ),
            },
        ],
    },
    {
        "id": 4,
        "name": "Analysis",
        "has_applicability": False,
        "applicability_question": None,
        "applicability_elaboration": None,
        "relevant_fields": [
            "two_by_two_table", "verification_bias", "flow_and_timing",
        ],
        "signals": [
            {
                "id": "4.1",
                "text": "Were all participants included in the analysis?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Per the flow diagram (Phase 3): every enrolled "
                    "participant should appear in the two-by-two table or "
                    "have a documented reason for exclusion. 'No' if "
                    "participants disappear from the analysis without "
                    "explanation."
                ),
            },
            {
                "id": "4.2",
                "text": "Were missing data handled appropriately?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Missing index test, missing reference standard, and "
                    "uninterpretable results should be reported and handled "
                    "transparently. Indeterminate results coded as "
                    "test-positive or test-negative without justification "
                    "introduces bias. 'No' if missing data are silently "
                    "dropped or default-coded."
                ),
            },
            {
                "id": "4.3",
                "text": "Does the unit of analysis match the ideal test accuracy trial?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Unit of analysis should match the clinical decision "
                    "(participant, lesion, sample, image, etc.). 'No' if "
                    "the paper reports per-lesion accuracy when per-participant "
                    "would be the clinically relevant unit, or vice versa."
                ),
            },
            {
                "id": "4.4",
                "text": "Were the estimates of sensitivity and specificity calculated appropriately?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Standard 2×2 sensitivity and specificity formulas, "
                    "exact or score CIs, no improper pooling across "
                    "non-independent observations. 'No' for non-standard or "
                    "unjustified statistical methods."
                ),
            },
        ],
    },
]


# ─────────────────────────────────────────────
# Prompt building + LLM orchestration
# ─────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing a diagnostic test "
    "accuracy study using the QUADAS-3 v1.2 tool (Whiting et al., University "
    "of Bristol). For each domain, read the PDF carefully and answer the "
    "signaling questions with one of: Y (yes), PY (probably yes), PN "
    "(probably no), N (no), NI (no information). When the domain has an "
    "applicability assessment, also rate concern that the as-conducted study "
    "matches the ideal test accuracy trial as: Low / High / Insufficient "
    "information. Provide a short rationale (1-2 sentences, quoting the "
    "paper where possible) for every answer. "
    "Return ONLY a valid JSON object — no preamble, no markdown fences."
)


def _format_estimate_block(estimate: dict[str, Any] | None) -> str:
    """Render the estimate context block for the prompt header. Empty when
    no estimate was supplied (single-estimate fallback)."""
    if not estimate:
        return "(assessment is for the paper's primary / headline accuracy estimate)"
    parts = []
    for key in ("description", "subgroup", "index_test", "threshold",
                "reference_standard", "unit_of_analysis", "sensitivity",
                "specificity", "n"):
        val = estimate.get(key)
        if val:
            parts.append(f"- {key.replace('_', ' ').title()}: {val}")
    return "\n".join(parts) if parts else "(assessment is for an estimate but no descriptor fields were supplied)"


def _format_review_context(review_context: str | None) -> str:
    """Render the review-level context (synthesis question + ideal trial) for
    the prompt header. Empty when not supplied — the LLM falls back to a
    generic intended-use baseline."""
    if not review_context or not review_context.strip():
        return (
            "(no review-level context supplied — judge applicability against "
            "the generic 'intended-use population' implied by the paper)"
        )
    return review_context.strip()


def build_domain_prompt(domain: dict[str, Any],
                        study_type: str,
                        primary_outcome: str,
                        extracted_fields: dict[str, str],
                        estimate: dict[str, Any] | None = None,
                        review_context: str | None = None) -> str:
    """Per-domain prompt for QUADAS-3 signaling-question + applicability assessment.

    Sends:
      - the estimate being assessed (one of N from Phase 4) with its descriptors
      - the user's review-level context (synthesis question + ideal trial), if any
      - verbatim signaling questions + elaborations from the QUADAS-3 v1.2 docx
      - relevant pre-extracted fields from the annotator
      - expected JSON shape — signal answers + rationales + (where applicable)
        applicability judgement + applicability rationale
    """
    relevant = {k: extracted_fields[k]
                for k in domain["relevant_fields"] if extracted_fields.get(k)}
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"

    q_lines = []
    for sig in domain["signals"]:
        q_lines.append(
            f"\n**{sig['id']}. {sig['text']}**\n"
            f"Elaboration: {sig['elaboration']}\n"
            f"Response options: {'/'.join(sig['options'])}."
        )
    questions_block = "\n".join(q_lines)

    shape_lines = ["{"]
    for sig in domain["signals"]:
        shape_lines.append(f'  "{sig["id"]}": "Y|PY|PN|N|NI",')
        shape_lines.append(f'  "{sig["id"]}_rationale": "1-2 sentences quoting the paper",')
    if domain["has_applicability"]:
        shape_lines.append(
            '  "applicability_judgement": "Low|High|Insufficient information",'
        )
        shape_lines.append(
            '  "applicability_rationale": "1-2 sentences explaining the concern relative to the ideal test accuracy trial"'
        )
    else:
        # Drop trailing comma from the last signal_rationale line above
        if shape_lines[-1].endswith(","):
            shape_lines[-1] = shape_lines[-1][:-1]
    shape_lines.append("}")
    shape = "\n".join(shape_lines)

    applicability_block = ""
    if domain["has_applicability"]:
        applicability_block = (
            "\n\n**Applicability assessment** (rate as Low / High / Insufficient information):\n"
            f"{domain['applicability_question']}\n"
            f"Elaboration: {domain['applicability_elaboration']}\n"
            "\n**Review-level context** (use this to judge applicability):\n"
            f"{_format_review_context(review_context)}"
        )

    return f"""Assess **Domain {domain['id']} — {domain['name']}** of QUADAS-3 v1.2 for the diagnostic test accuracy study described in the attached PDF.

Study type: {study_type}
Primary outcome (target condition): {primary_outcome}

Estimate being assessed:
{_format_estimate_block(estimate)}

Context (fields already extracted from the paper):
{ctx_json}

Signaling questions:
{questions_block}{applicability_block}

Return a JSON object with exactly this shape:
{shape}

Answer N (or PN) only when the paper gives enough information to rule out adherence; answer NI only when the paper is silent. Rationales must be short (1-2 sentences) and quote the paper verbatim where possible."""


def _assess_domain(pdf_bytes: bytes, domain: dict[str, Any],
                   study_type: str, primary_outcome: str,
                   extracted_fields: dict[str, str],
                   estimate: dict[str, Any] | None = None,
                   review_context: str | None = None) -> dict[str, Any]:
    """LLM-assess one domain. Returns
    ``{signals, rationales, judgement, applicability_judgement, applicability_rationale}``
    (the last two are absent for the Analysis domain)."""
    prompt = build_domain_prompt(domain, study_type, primary_outcome,
                                  extracted_fields, estimate=estimate,
                                  review_context=review_context)
    raw = _call_with_pdf(pdf_bytes, prompt, max_tokens=8192)

    signals: dict[str, str] = {}
    rationales: dict[str, str] = {}
    for sig in domain["signals"]:
        sid = sig["id"]
        ans = str(raw.get(sid, "NI")).strip().upper()
        if ans not in SIGNAL_OPTIONS:
            logger.warning("QUADAS-3 domain %s question %s: invalid answer %r — defaulting to NI",
                            domain["id"], sid, ans)
            ans = "NI"
        signals[sid] = ans
        rationales[sid] = str(raw.get(f"{sid}_rationale", "")).strip()

    judgement = DOMAIN_JUDGES[domain["id"]](signals)

    out: dict[str, Any] = {
        "signals": signals,
        "rationales": rationales,
        "judgement": judgement,
    }

    if domain["has_applicability"]:
        app = str(raw.get("applicability_judgement", "Insufficient information")).strip()
        # Normalise common abbreviations
        norm = app.lower()
        if norm in ("ii", "insufficient", "insufficient information", "no information",
                    "no info", "ni", "unclear"):
            app = "Insufficient information"
        elif norm in ("low", "low concern", "low concerns"):
            app = "Low"
        elif norm in ("high", "high concern", "high concerns"):
            app = "High"
        else:
            app = "Insufficient information"
        out["applicability_judgement"] = app
        out["applicability_rationale"] = str(
            raw.get("applicability_rationale", "")).strip()

    return out


def run(pdf_bytes: bytes,
        extracted_fields: dict[str, str],
        classification: dict[str, str],
        primary_outcome: str,
        progress: Callable[[int], None] | None = None,
        *,
        estimate: dict[str, Any] | None = None,
        review_context: str | None = None,
        ) -> tuple[dict[str, Any], str, str, str]:
    """Run QUADAS-3 v1.2 against a diagnostic test accuracy study.

    Returns ``(domain_results, overall_rob, overall_direction, overall_applicability)``.

    - ``domain_results`` is keyed by domain id (``"1"`` … ``"4"``), each with
      ``{name, signals, rationales, judgement, applicability_judgement,
      applicability_rationale}`` (the last two only for domains 1–3).
    - ``overall_rob`` is "Low" / "High" / "Insufficient information".
    - ``overall_direction`` is always ``"NA"`` for diagnostic accuracy
      (RoB direction-of-effect is a treatment-trial concept).
    - ``overall_applicability`` is "Low" / "High" / "Insufficient information",
      aggregated over the 3 applicability-bearing domains only.

    Optional kwargs:
      - ``estimate``: a Phase-4 estimate descriptor dict (subgroup, threshold,
        reference standard, sens/spec, etc.) — threaded into prompts.
      - ``review_context``: free-text review-level context (Phases 1+2:
        synthesis question + ideal test accuracy trial) — threaded into
        applicability prompts.
    """
    study_type = classification.get("study_type", "Diagnostic Accuracy")

    domain_results: dict[str, Any] = {}
    for domain in DOMAINS:
        if progress:
            try:
                progress(domain["id"])
            except Exception:
                pass
        result = _assess_domain(pdf_bytes, domain, study_type,
                                 primary_outcome, extracted_fields,
                                 estimate=estimate,
                                 review_context=review_context)
        result["id"] = domain["id"]
        result["name"] = domain["name"]
        result["has_applicability"] = domain["has_applicability"]
        domain_results[str(domain["id"])] = result

    rob_overall = quadas3_overall(
        [domain_results[str(d["id"])]["judgement"] for d in DOMAINS])
    app_overall = quadas3_applicability_overall(
        [domain_results[str(d["id"])]["applicability_judgement"]
         for d in DOMAINS if d["has_applicability"]])

    return domain_results, rob_overall, "NA", app_overall


# ─────────────────────────────────────────────
# Phase 4 — estimate extraction
# ─────────────────────────────────────────────
_ESTIMATE_EXTRACTION_PROMPT_HEADER = """Identify every numerical accuracy estimate (sensitivity / specificity pair, or 2×2 table) the attached diagnostic test accuracy paper reports that could be selected for QUADAS-3 Phase 4 assessment.

Multiple estimates within a single paper occur due to differences in: study subgroups, index test versions, target-condition definitions, reference standards, units of analysis, and thresholds.

For each estimate, return the descriptor as compactly as possible:
- ``description`` — one-line label suitable for a UI checkbox (≤ 80 chars)
- ``subgroup`` — population subgroup (or 'overall' if not subgrouped)
- ``index_test`` — name + version / threshold of the index test
- ``threshold`` — numerical threshold if applicable
- ``reference_standard`` — gold-standard test
- ``unit_of_analysis`` — participant / lesion / sample / image / etc.
- ``sensitivity`` — point estimate as reported (e.g. "84% (95% CI 79–88)")
- ``specificity`` — point estimate as reported
- ``n`` — total participants contributing to this 2×2

Return ONLY a JSON object of the shape:
{
  "estimates": [
    {"description": "...", "subgroup": "...", "index_test": "...", "threshold": "...", "reference_standard": "...", "unit_of_analysis": "...", "sensitivity": "...", "specificity": "...", "n": "..."}
  ]
}

If the paper reports only a single primary estimate, return a list of length 1. If no numerical accuracy estimates can be found, return an empty list.
"""


def extract_estimates(pdf_bytes: bytes,
                      extracted_fields: dict[str, str] | None = None,
                      ) -> list[dict[str, Any]]:
    """Single LLM call returning all candidate accuracy estimates from the paper.

    Used by the run modal's step-2 estimate selector. Each returned estimate
    gets a synthetic ``id`` (1..N) added in Python so the frontend can
    checkbox / unselect them without ambiguity.
    """
    extracted_fields = extracted_fields or {}
    ctx_json = json.dumps(extracted_fields, indent=2) if extracted_fields else "(no pre-extracted fields)"
    prompt = _ESTIMATE_EXTRACTION_PROMPT_HEADER + (
        "\n\nContext (fields already extracted from the paper):\n" + ctx_json
    )
    raw = _call_with_pdf(pdf_bytes, prompt, max_tokens=8192)

    estimates_raw = raw.get("estimates")
    if not isinstance(estimates_raw, list):
        return []

    out: list[dict[str, Any]] = []
    for idx, est in enumerate(estimates_raw, start=1):
        if not isinstance(est, dict):
            continue
        clean = {
            "id": idx,
            "description": str(est.get("description") or "").strip(),
            "subgroup": str(est.get("subgroup") or "").strip(),
            "index_test": str(est.get("index_test") or "").strip(),
            "threshold": str(est.get("threshold") or "").strip(),
            "reference_standard": str(est.get("reference_standard") or "").strip(),
            "unit_of_analysis": str(est.get("unit_of_analysis") or "").strip(),
            "sensitivity": str(est.get("sensitivity") or "").strip(),
            "specificity": str(est.get("specificity") or "").strip(),
            "n": str(est.get("n") or "").strip(),
        }
        if not clean["description"]:
            # Synthesize a description from subgroup + threshold
            bits = [b for b in (clean["subgroup"], clean["index_test"],
                                 clean["threshold"]) if b]
            clean["description"] = " — ".join(bits) or f"Estimate {idx}"
        out.append(clean)
    return out


# ─────────────────────────────────────────────
# Developer-view exposure
# ─────────────────────────────────────────────
def prompt_catalog() -> dict[str, Any]:
    """Return the prompts + decision-tree source for the developer icon."""
    import inspect
    domain_entries = []
    for domain in DOMAINS:
        sample_fields = {k: "<extracted value>" for k in domain["relevant_fields"]}
        domain_entries.append({
            "id": domain["id"],
            "name": domain["name"],
            "has_applicability": domain["has_applicability"],
            "applicability_question": domain["applicability_question"],
            "applicability_elaboration": domain["applicability_elaboration"],
            "signals": domain["signals"],
            "relevant_fields": domain["relevant_fields"],
            "prompt_template": build_domain_prompt(
                domain, "Diagnostic Accuracy",
                "<primary outcome here>", sample_fields,
                estimate=None, review_context=None,
            ),
            "decision_tree_code": inspect.getsource(quadas3_domain_judge),
        })
    return {
        "tool": "QUADAS-3 v1.2 — Diagnostic test accuracy",
        "system_prompt": _SYSTEM_PROMPT,
        "signal_options": list(SIGNAL_OPTIONS),
        "judgements": list(JUDGEMENTS),
        "applicability_options": list(APPLICABILITY_OPTIONS),
        "domains": domain_entries,
        "overall_algorithm_code": inspect.getsource(quadas3_overall),
        "applicability_algorithm_code": inspect.getsource(quadas3_applicability_overall),
        "extract_estimates_prompt": _ESTIMATE_EXTRACTION_PROMPT_HEADER,
        "v1_limitations": [
            "Phase 5 narrative allows reviewer judgement to keep a domain at "
            "Low even with one or more N/PN signals; the decision tree maps "
            "any N/PN to High conservatively (rationale text preserved).",
            "Per-estimate domain-difference shortcut from Phase 5 footnote "
            "is not implemented — every estimate runs all 4 domains.",
            "Indirectness + imprecision are skipped for diagnostic-accuracy "
            "papers (existing modules assume PICO/treatment trials, not PIRT).",
            "Phases 1+2 (synthesis question + ideal trial design) are "
            "collected as a single free-text textarea, not the structured "
            "tables in the QUADAS-3 docx.",
            "QUADAS-C (comparative accuracy) is a separate tool and is not "
            "implemented.",
        ],
    }
