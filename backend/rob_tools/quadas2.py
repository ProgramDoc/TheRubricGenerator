"""QUADAS-2 (2011) — Risk of bias + applicability for diagnostic test accuracy studies.

Source: Whiting PF, Rutjes AWS, Westwood ME, Mallett S, Deeks JJ, Reitsma JB,
Leeflang MMG, Sterne JAC, Bossuyt PMM, and the QUADAS-2 Group.
"QUADAS-2: A Revised Tool for the Quality Assessment of Diagnostic Accuracy
Studies." Ann Intern Med. 2011;155:529-536.

Encodes the QUADAS-2 tool as:

- ``DOMAINS`` — 4 domains × signaling questions (3 + 2 + 2 + 4 = 11 signals).
  Domains 1-3 carry both a Risk of Bias and an Applicability assessment;
  Domain 4 (Flow and Timing) is RoB-only.
- ``quadas2_domain_judge(signals)`` — pure-Python decision tree per
  Phase 4 of the paper ("If all signaling questions are 'yes' then risk of
  bias can be judged 'low'. If any signaling question is answered 'no',
  potential for bias exists.").
- ``quadas2_overall(domain_judgements)`` — aggregate over the 4 domains
  ("If a study is judged 'low' on all domains relating to bias …, then it
  is appropriate to have an overall judgment of 'low risk of bias' …
  If a study is judged 'high' or 'unclear' in 1 or more domains, then it
  may be judged 'at risk of bias' or as having 'concerns regarding
  applicability'.").
- ``run(pdf_bytes, fields, classification, primary_outcome, *, estimate,
  review_context, progress)`` — per-domain LLM calls via the annotator's
  ``_call_with_pdf`` pipeline; each call returns BOTH signal answers (RoB)
  AND applicability concern (where applicable) in a single response.
- ``extract_estimates`` — **not defined here**; the registry aliases
  QUADAS-3's extract_estimates because numerical sens/spec extraction is
  RoB-tool-agnostic.

Signaling-question answers are Y / N / U (yes / no / unclear) — the
classic 3-level QUADAS-2 scale (QUADAS-3 v1.2 uses 5-level Y/PY/PN/N/NI).
Domain RoB judgements are "Low" / "High" / "Unclear".
Applicability judgements are "Low" / "High" / "Unclear".

The QUADAS-2 paper narratively allows reviewers to keep a domain at Low
even with a single "No" if the No is judged immaterial. We take the
conservative interpretation here (any N → High) to keep the decision tree
pure and inspectable; reviewers can read the rationales in the detail
modal to override that judgement in their own write-up.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from fastapi import HTTPException

from ..annotator import _call_with_pdf
from ..helpers import parse_json_response

logger = logging.getLogger("rubricgen")


SIGNAL_OPTIONS = ("Y", "N", "U")
JUDGEMENTS = ("Low", "High", "Unclear")
APPLICABILITY_OPTIONS = ("Low", "High", "Unclear")


# ─────────────────────────────────────────────
# Decision trees (pure Python — no LLM)
# ─────────────────────────────────────────────
def _yes(ans: str) -> bool:
    return ans == "Y"


def _no(ans: str) -> bool:
    return ans == "N"


def quadas2_domain_judge(signals: dict[str, str]) -> str:
    """Map signaling-question answers (Y/N/U) to a domain-level RoB
    judgement (Low / High / Unclear) per the QUADAS-2 Phase 4 narrative.

    Rule (conservative):
      - All signals Y → "Low"
      - Any N → "High"
      - Any U without N → "Unclear"
      - Empty / all-U → "Unclear"
    """
    answered = [v for v in signals.values()]
    if not answered:
        return "Unclear"
    if any(_no(v) for v in answered):
        return "High"
    if all(_yes(v) for v in answered):
        return "Low"
    return "Unclear"


def quadas2_overall(domain_judgements: list[str]) -> str:
    """Aggregate per the QUADAS-2 paper ("Incorporating Assessments" section).

    - Any domain High → "High"
    - All domains Low → "Low"
    - Otherwise (any Unclear, none High) → "Unclear"
    """
    if not domain_judgements:
        return "Unclear"
    if any(j == "High" for j in domain_judgements):
        return "High"
    if all(j == "Low" for j in domain_judgements):
        return "Low"
    return "Unclear"


def quadas2_applicability_overall(judgements: list[str]) -> str:
    """Aggregate applicability judgements per the QUADAS-2 paper (same rule
    as RoB). Only 3 domains carry applicability (Patient Selection, Index
    Test, Reference Standard); Flow and Timing is excluded from the input
    list.
    """
    return quadas2_overall(judgements)


DOMAIN_JUDGES: dict[int, Callable[[dict[str, str]], str]] = {
    1: quadas2_domain_judge,
    2: quadas2_domain_judge,
    3: quadas2_domain_judge,
    4: quadas2_domain_judge,
}


# ─────────────────────────────────────────────
# Domain definitions — signaling questions transcribed verbatim from
# QUADAS-2 (Whiting 2011, Table 1 + section-by-section narrative)
# ─────────────────────────────────────────────
DOMAINS: list[dict[str, Any]] = [
    {
        "id": 1,
        "name": "Patient Selection",
        "has_applicability": True,
        "applicability_question": (
            "Are there concerns that the included patients and setting do "
            "not match the review question?"
        ),
        "applicability_elaboration": (
            "Concerns about applicability may exist if patients included in "
            "the study differ from those targeted by the review question in "
            "terms of severity of the target condition, demographic features, "
            "presence of differential diagnosis or comorbid conditions, "
            "setting of the study, and previous testing protocols."
        ),
        "relevant_fields": [
            "spectrum_of_patients", "verification_bias", "flow_and_timing",
            "population_inclusion", "population_exclusion",
        ],
        "signals": [
            {
                "id": "1.1",
                "text": "Was a consecutive or random sample of patients enrolled?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "A study should ideally enrol a consecutive or random "
                    "sample of eligible patients with suspected disease to "
                    "prevent the potential for bias. Convenience samples or "
                    "selection on test-related criteria → 'No'."
                ),
            },
            {
                "id": "1.2",
                "text": "Was a case-control design avoided?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Studies enrolling participants with known disease and a "
                    "separate control group without the condition may "
                    "exaggerate diagnostic accuracy (spectrum bias). Answer "
                    "'Yes' for single-gate (cohort) designs; 'No' for "
                    "case-control / multi-gate designs."
                ),
            },
            {
                "id": "1.3",
                "text": "Did the study avoid inappropriate exclusions?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Studies that make inappropriate exclusions (e.g. not "
                    "including 'difficult-to-diagnose' patients, or excluding "
                    "patients with 'red flags' for the target condition who "
                    "may be easier to diagnose) may over- or underestimate "
                    "diagnostic accuracy. 'No' if exclusions are likely to "
                    "have distorted the spectrum."
                ),
            },
        ],
    },
    {
        "id": 2,
        "name": "Index Test",
        "has_applicability": True,
        "applicability_question": (
            "Are there concerns that the index test, its conduct, or its "
            "interpretation differ from the review question?"
        ),
        "applicability_elaboration": (
            "Variations in test technology, execution, or interpretation may "
            "affect estimates of the diagnostic accuracy of a test. If index "
            "test methods vary from those specified in the review question, "
            "concerns about applicability may exist."
        ),
        "relevant_fields": [
            "index_test", "blinding_index_to_reference", "threshold_effects",
        ],
        "signals": [
            {
                "id": "2.1",
                "text": "Were the index test results interpreted without knowledge of the results of the reference standard?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Knowledge of the reference standard may influence "
                    "interpretation of index test results (review bias). If "
                    "the index test is always conducted and interpreted "
                    "before the reference standard, this item can be rated "
                    "'Yes'."
                ),
            },
            {
                "id": "2.2",
                "text": "If a threshold was used, was it pre-specified?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Selecting the test threshold to optimize sensitivity "
                    "and/or specificity post-hoc may lead to overestimation "
                    "of test performance. Test performance is likely to be "
                    "poorer in an independent sample of patients in whom the "
                    "same threshold is used. Mark 'Unclear' if no threshold "
                    "was used (e.g. continuous test reported as AUC only)."
                ),
            },
        ],
    },
    {
        "id": 3,
        "name": "Reference Standard",
        "has_applicability": True,
        "applicability_question": (
            "Are there concerns that the target condition as defined by the "
            "reference standard does not match the review question?"
        ),
        "applicability_elaboration": (
            "The reference standard may be free of bias, but the target "
            "condition that it defines may differ from the target condition "
            "specified in the review question. For example, when defining "
            "urinary tract infection, the reference standard is generally "
            "based on specimen culture; however, the threshold above which a "
            "result is considered positive may vary."
        ),
        "relevant_fields": [
            "reference_standard", "blinding_reference_to_index",
            "flow_and_timing",
        ],
        "signals": [
            {
                "id": "3.1",
                "text": "Is the reference standard likely to correctly classify the target condition?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Estimates of test accuracy are based on the assumptions "
                    "that the reference standard is 100% sensitive and that "
                    "any specific disagreements between the reference "
                    "standard and index test result from incorrect "
                    "classification by the index test. 'No' if the reference "
                    "standard is known to be inaccurate or substantially "
                    "different from the accepted diagnostic criterion."
                ),
            },
            {
                "id": "3.2",
                "text": "Were the reference standard results interpreted without knowledge of the results of the index test?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Potential for bias is related to the potential influence "
                    "of previous knowledge of the index test result on the "
                    "interpretation of the reference standard."
                ),
            },
        ],
    },
    {
        "id": 4,
        "name": "Flow and Timing",
        "has_applicability": False,
        "applicability_question": None,
        "applicability_elaboration": None,
        "relevant_fields": [
            "flow_and_timing", "verification_bias", "two_by_two_table",
        ],
        "signals": [
            {
                "id": "4.1",
                "text": "Was there an appropriate interval between the index test and reference standard?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Results of the index test and reference standard are "
                    "ideally collected on the same patients at the same time. "
                    "If a delay occurs or if treatment begins between the "
                    "index test and the reference standard, recovery or "
                    "deterioration of the condition may cause "
                    "misclassification. The appropriate interval is "
                    "condition-specific (hours for stroke, weeks for a "
                    "slow-growing tumour)."
                ),
            },
            {
                "id": "4.2",
                "text": "Did all patients receive a reference standard?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Partial verification — applying the reference standard "
                    "only to a subset (e.g. index-positive participants) — "
                    "biases sensitivity and specificity estimates. 'No' if "
                    "verification was selective."
                ),
            },
            {
                "id": "4.3",
                "text": "Did all patients receive the same reference standard?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Differential verification — different reference "
                    "standards for index-positive vs index-negative patients "
                    "— introduces bias. 'No' if multiple reference standards "
                    "were used non-randomly."
                ),
            },
            {
                "id": "4.4",
                "text": "Were all patients included in the analysis?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "All patients recruited into the study should be included "
                    "in the analysis. A potential for bias exists if the "
                    "number of patients enrolled differs from the number of "
                    "patients included in the 2×2 table of results, because "
                    "patients lost to follow-up differ systematically from "
                    "those who remain."
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
    "accuracy study using the QUADAS-2 tool (Whiting et al., 2011, Ann Intern "
    "Med). For each domain, read the PDF carefully and answer the signaling "
    "questions with one of: Y (yes), N (no), U (unclear). When the domain has "
    "an applicability assessment, also rate concern that the as-conducted "
    "study matches the review question (PIRT: Patient population / Index test "
    "/ Reference standard / Target condition) as: Low / High / Unclear. "
    "Provide a short rationale (1-2 sentences, quoting the paper where "
    "possible) for every answer. Return ONLY a valid JSON object — no "
    "preamble, no markdown fences."
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
    """Render the review-level context (PIRT review question) for the
    prompt header. Empty when not supplied — the LLM falls back to a
    generic intended-use baseline."""
    if not review_context or not review_context.strip():
        return (
            "(no review question supplied — judge applicability against the "
            "generic 'intended-use population' implied by the paper)"
        )
    return review_context.strip()


def build_domain_prompt(domain: dict[str, Any],
                        study_type: str,
                        primary_outcome: str,
                        extracted_fields: dict[str, str],
                        estimate: dict[str, Any] | None = None,
                        review_context: str | None = None) -> str:
    """Per-domain prompt for QUADAS-2 signaling-question + applicability assessment.

    Sends:
      - the estimate being assessed (if user selected one via Phase 4 of the
        per-estimate path) with its descriptors
      - the user's review question (PIRT), if any
      - verbatim signaling questions + elaborations from Whiting 2011
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
        shape_lines.append(f'  "{sig["id"]}": "Y|N|U",')
        shape_lines.append(f'  "{sig["id"]}_rationale": "1-2 sentences quoting the paper",')
    if domain["has_applicability"]:
        shape_lines.append(
            '  "applicability_judgement": "Low|High|Unclear",'
        )
        shape_lines.append(
            '  "applicability_rationale": "1-2 sentences explaining the concern relative to the review question"'
        )
    else:
        if shape_lines[-1].endswith(","):
            shape_lines[-1] = shape_lines[-1][:-1]
    shape_lines.append("}")
    shape = "\n".join(shape_lines)

    applicability_block = ""
    if domain["has_applicability"]:
        applicability_block = (
            "\n\n**Applicability assessment** (rate as Low / High / Unclear):\n"
            f"{domain['applicability_question']}\n"
            f"Elaboration: {domain['applicability_elaboration']}\n"
            "\n**Review question** (PIRT — use this to judge applicability):\n"
            f"{_format_review_context(review_context)}"
        )

    return f"""Assess **Domain {domain['id']} — {domain['name']}** of QUADAS-2 (Whiting 2011) for the diagnostic test accuracy study described in the attached PDF.

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

Answer N only when the paper gives enough information to rule out adherence; answer U only when the paper is silent or the information is ambiguous. Rationales must be short (1-2 sentences) and quote the paper verbatim where possible."""


def _normalise_answer(raw_value: Any) -> str:
    """Normalise an LLM answer string to one of Y / N / U.

    Accepts Y / Yes / yes / YES → Y;
            N / No / no / NO → N;
            anything else (U / Unclear / NI / ?) → U.
    """
    s = str(raw_value or "").strip().lower()
    if s in ("y", "yes"):
        return "Y"
    if s in ("n", "no"):
        return "N"
    return "U"


def _assess_domain(pdf_bytes: bytes, domain: dict[str, Any],
                   study_type: str, primary_outcome: str,
                   extracted_fields: dict[str, str],
                   estimate: dict[str, Any] | None = None,
                   review_context: str | None = None) -> dict[str, Any]:
    """LLM-assess one domain. Returns
    ``{signals, rationales, judgement, applicability_judgement, applicability_rationale}``
    (the last two are absent for the Flow and Timing domain)."""
    prompt = build_domain_prompt(domain, study_type, primary_outcome,
                                  extracted_fields, estimate=estimate,
                                  review_context=review_context)
    raw = _call_with_pdf(pdf_bytes, prompt, max_tokens=8192)

    signals: dict[str, str] = {}
    rationales: dict[str, str] = {}
    for sig in domain["signals"]:
        sid = sig["id"]
        ans = _normalise_answer(raw.get(sid))
        if ans not in SIGNAL_OPTIONS:
            logger.warning("QUADAS-2 domain %s question %s: invalid answer %r — defaulting to U",
                            domain["id"], sid, raw.get(sid))
            ans = "U"
        signals[sid] = ans
        rationales[sid] = str(raw.get(f"{sid}_rationale", "")).strip()

    judgement = DOMAIN_JUDGES[domain["id"]](signals)

    out: dict[str, Any] = {
        "signals": signals,
        "rationales": rationales,
        "judgement": judgement,
    }

    if domain["has_applicability"]:
        app = str(raw.get("applicability_judgement", "Unclear")).strip()
        norm = app.lower()
        if norm in ("u", "unclear", "?", "insufficient", "insufficient information",
                    "no information", "ni"):
            app = "Unclear"
        elif norm in ("low", "low concern", "low concerns"):
            app = "Low"
        elif norm in ("high", "high concern", "high concerns"):
            app = "High"
        else:
            app = "Unclear"
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
    """Run QUADAS-2 against a diagnostic test accuracy study.

    Returns ``(domain_results, overall_rob, overall_direction, overall_applicability)``.

    - ``domain_results`` is keyed by domain id (``"1"`` … ``"4"``), each with
      ``{name, signals, rationales, judgement, applicability_judgement,
      applicability_rationale}`` (the last two only for domains 1-3).
    - ``overall_rob`` is "Low" / "High" / "Unclear".
    - ``overall_direction`` is always ``"NA"`` for diagnostic accuracy
      (RoB direction-of-effect is a treatment-trial concept).
    - ``overall_applicability`` is "Low" / "High" / "Unclear", aggregated
      over the 3 applicability-bearing domains only.

    Optional kwargs (kept compatible with quadas3.run for transparent dispatch):
      - ``estimate``: a Phase-4 estimate descriptor dict (subgroup, threshold,
        reference standard, sens/spec, etc.) — threaded into prompts.
      - ``review_context``: free-text review question (PIRT) — threaded into
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

    rob_overall = quadas2_overall(
        [domain_results[str(d["id"])]["judgement"] for d in DOMAINS])
    app_overall = quadas2_applicability_overall(
        [domain_results[str(d["id"])]["applicability_judgement"]
         for d in DOMAINS if d["has_applicability"]])

    return domain_results, rob_overall, "NA", app_overall


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
            "decision_tree_code": inspect.getsource(quadas2_domain_judge),
        })
    return {
        "tool": "QUADAS-2 (Whiting 2011) — Diagnostic test accuracy",
        "system_prompt": _SYSTEM_PROMPT,
        "signal_options": list(SIGNAL_OPTIONS),
        "judgements": list(JUDGEMENTS),
        "applicability_options": list(APPLICABILITY_OPTIONS),
        "domains": domain_entries,
        "overall_algorithm_code": inspect.getsource(quadas2_overall),
        "applicability_algorithm_code": inspect.getsource(quadas2_applicability_overall),
        "extract_estimates_prompt": (
            "QUADAS-2 reuses QUADAS-3's numerical sens/spec extractor "
            "(tool-agnostic). See the quadas3 entry for the prompt."
        ),
        "v1_limitations": [
            "QUADAS-2 narratively allows reviewer judgement to keep a domain "
            "at Low even with one or more 'No' signals; the decision tree "
            "maps any N to High conservatively (rationale text preserved).",
            "Review-specific tailoring of signaling questions (Phase 2 of "
            "Whiting 2011) is not implemented — we use the canonical core "
            "questions for every review.",
            "Indirectness + imprecision are skipped for diagnostic-accuracy "
            "papers (existing modules assume PICO/treatment trials, not PIRT).",
            "Per-estimate iteration is supported via the shared QUADAS-3 "
            "Phase-4 estimate extractor (Whiting 2011 originally assumed "
            "single-estimate-per-study, but per-estimate use is methodologically "
            "defensible and widely adopted).",
            "QUADAS-C (comparative accuracy) is a separate tool and is not "
            "implemented.",
        ],
    }
