"""Cochrane RoB 2 — Risk of Bias for randomized trials (parallel-group).

Encodes the 2019 cribsheet (Higgins, Savović, Page, Sterne et al.) as:

- ``DOMAINS`` — 5 domains × their signaling questions + verbatim elaborations.
- ``rob2_domainN_judge(signals)`` — pure-Python decision trees (cribsheet flowcharts).
- ``rob2_overall(domain_judgements)`` — aggregate per p.24.
- ``run(pdf_bytes, fields, classification, primary_outcome, progress)`` —
  per-domain LLM calls via the annotator's ``_call_with_pdf`` pipeline, then
  local decision-tree evaluation.

Signaling-question answers are Y / PY / PN / N / NI (the cribsheet's options).
Domain judgements are "Low" / "Some concerns" / "High".

Signaling-question text + elaborations are transcribed from
``20190814_RoB_2.0_cribsheet_parallel_trial.pdf`` and shown verbatim to the LLM
so it sees the same guidance a human reviewer would.

v1 scope: individually-randomized parallel-group trials assessing the effect of
**assignment** to intervention (intention-to-treat). Cluster-randomized and
cross-over trials use different Domain 1/2 signaling questions and are
registered separately (``rob2_cluster``, ``rob2_crossover``) in future work.
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

# ─────────────────────────────────────────────
# Decision trees (pure Python — no LLM)
# ─────────────────────────────────────────────
# Each domain's flowchart from the cribsheet is translated directly. The LLM
# answers signaling questions; code maps those answers to a judgement. Keeping
# the trees in code (not prompts) makes the developer view honest: we can show
# the exact logic via ``inspect.getsource``.


def _yes(ans: str) -> bool:
    return ans in ("Y", "PY")


def _no(ans: str) -> bool:
    return ans in ("N", "PN")


def rob2_domain1_judge(signals: dict[str, str]) -> str:
    """Domain 1 (randomization process) algorithm — cribsheet p.6.

    Inputs: ``{"1.1": Y/PY/PN/N/NI, "1.2": ..., "1.3": ...}``
    Output: "Low" / "Some concerns" / "High".
    """
    q11 = signals.get("1.1", "NI")
    q12 = signals.get("1.2", "NI")
    q13 = signals.get("1.3", "NI")

    if _no(q12):
        return "High"
    if q12 == "NI":
        return "High" if _yes(q13) else "Some concerns"
    # q12 is Y/PY
    if _no(q11):
        return "High"
    # q11 is Y/PY/NI
    return "Some concerns" if _yes(q13) else "Low"


def rob2_domain2_judge(signals: dict[str, str]) -> str:
    """Domain 2 (deviations from intended interventions — assignment effect)
    algorithm — cribsheet p.10.

    Two parts combined by the criteria on p.10:
      Low    iff Part 1 = Low AND Part 2 = Low
      High   iff either part = High
      Some   otherwise

    Part 1 uses 2.1-2.5, Part 2 uses 2.6-2.7.
    """
    q21 = signals.get("2.1", "NI")
    q22 = signals.get("2.2", "NI")
    q23 = signals.get("2.3", "NI")
    q24 = signals.get("2.4", "NI")
    q25 = signals.get("2.5", "NI")
    q26 = signals.get("2.6", "NI")
    q27 = signals.get("2.7", "NI")

    # Part 1 — 2.1/2.2 awareness then 2.3/2.4/2.5 chain
    aware = (not _no(q21)) or (not _no(q22))  # "Either Y/PY/NI"
    if not aware:
        # Both 2.1 and 2.2 answered N/PN → Low on Part 1
        part1 = "Low"
    else:
        # 2.3 "deviations that arose because of trial context?"
        if _no(q23):
            part1 = "Some concerns"
        elif q23 == "NI":
            part1 = "Some concerns"
        else:
            # 2.3 Y/PY → 2.4 "affect outcome?"
            if _no(q24):
                part1 = "Some concerns"
            else:
                # 2.4 Y/PY/NI → 2.5 "deviations balanced between groups?"
                part1 = "High" if not _yes(q25) else "Some concerns"

    # Part 2 — 2.6 appropriate analysis → 2.7 substantial impact
    if _yes(q26):
        part2 = "Low"
    else:
        # 2.6 N/PN/NI → 2.7
        part2 = "Some concerns" if _no(q27) else "High"

    if part1 == "High" or part2 == "High":
        return "High"
    if part1 == "Low" and part2 == "Low":
        return "Low"
    return "Some concerns"


def rob2_domain3_judge(signals: dict[str, str]) -> str:
    """Domain 3 (missing outcome data) — cribsheet p.16."""
    q31 = signals.get("3.1", "NI")
    q32 = signals.get("3.2", "NI")
    q33 = signals.get("3.3", "NI")
    q34 = signals.get("3.4", "NI")

    if _yes(q31):
        return "Low"
    # 3.1 N/PN/NI → 3.2 evidence result not biased
    if _yes(q32):
        return "Low"
    # 3.2 N/PN → 3.3 could missingness depend on true value?
    if _no(q33):
        return "Low"
    # 3.3 Y/PY/NI → 3.4 likely that it depended?
    if _yes(q34) or q34 == "NI":
        return "High"
    return "Some concerns"


def rob2_domain4_judge(signals: dict[str, str]) -> str:
    """Domain 4 (measurement of the outcome) — cribsheet p.19."""
    q41 = signals.get("4.1", "NI")
    q42 = signals.get("4.2", "NI")
    q43 = signals.get("4.3", "NI")
    q44 = signals.get("4.4", "NI")
    q45 = signals.get("4.5", "NI")

    # 4.1 method inappropriate?
    if _yes(q41):
        return "High"
    # 4.2 measurement differ between groups?
    if _yes(q42):
        return "High"

    def _chain() -> str:
        # 4.3 outcome assessors aware?
        if _no(q43):
            return "Low"
        # Y/PY/NI → 4.4 could assessment be influenced?
        if _no(q44):
            return "Low"
        # Y/PY/NI → 4.5 likely that it was influenced?
        if _yes(q45) or q45 == "NI":
            return "High"
        return "Some concerns"

    base = _chain()
    if q42 == "NI":
        # NI on 4.2 downgrades a Low to Some concerns (flowchart p.19)
        if base == "Low":
            return "Some concerns"
    return base


def rob2_domain5_judge(signals: dict[str, str]) -> str:
    """Domain 5 (selection of the reported result) — cribsheet p.23."""
    q51 = signals.get("5.1", "NI")
    q52 = signals.get("5.2", "NI")
    q53 = signals.get("5.3", "NI")

    if _yes(q52) or _yes(q53):
        return "High"
    if _no(q52) and _no(q53):
        # Results selected-from guardrails both N/PN → consult 5.1
        return "Low" if _yes(q51) else "Some concerns"
    # Remaining case: at least one NI, none Y/PY
    return "Some concerns"


def rob2_overall(domain_judgements: list[str]) -> str:
    """Overall RoB judgement per cribsheet p.24.

    Criteria:
      Low iff all domains Low.
      High iff any domain High OR >=2 domains Some concerns.
      Some concerns otherwise.
    """
    if any(j == "High" for j in domain_judgements):
        return "High"
    some = sum(1 for j in domain_judgements if j == "Some concerns")
    if some >= 2:
        return "High"
    if some >= 1:
        return "Some concerns"
    return "Low"


DOMAIN_JUDGES: dict[int, Callable[[dict[str, str]], str]] = {
    1: rob2_domain1_judge,
    2: rob2_domain2_judge,
    3: rob2_domain3_judge,
    4: rob2_domain4_judge,
    5: rob2_domain5_judge,
}


# ─────────────────────────────────────────────
# Domain definitions — signaling questions + verbatim elaborations
# ─────────────────────────────────────────────
DOMAINS: list[dict[str, Any]] = [
    {
        "id": 1,
        "name": "Bias arising from the randomization process",
        "relevant_fields": ["randomization_method", "allocation_concealment",
                             "allocation_ratio", "stratification_factors",
                             "baseline_balance"],
        "signals": [
            {
                "id": "1.1",
                "text": "Was the allocation sequence random?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Answer 'Yes' if a random component was used in the sequence generation "
                    "process (computer-generated random numbers; random-number table; coin tossing; "
                    "shuffling cards or envelopes; throwing dice; drawing lots). Minimization with a "
                    "random element counts as random. Answer 'No' if no random element was used or the "
                    "sequence is predictable (alternation; dates of birth/admission; patient record "
                    "numbers; clinician/participant decisions; any systematic or haphazard method). "
                    "Answer 'No information' if the only information is a bare statement that the "
                    "study is randomized."
                ),
            },
            {
                "id": "1.2",
                "text": "Was the allocation sequence concealed until participants were enrolled and assigned to interventions?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Answer 'Yes' if remote/central allocation was used (independent central pharmacy, "
                    "telephone or internet-based service). Answer 'Yes' if opaque, sequentially-numbered, "
                    "tamper-sealed envelopes or identical sequentially-numbered drug containers were used "
                    "correctly. Answer 'No' if there is reason to suspect enrolling personnel or the "
                    "participant had knowledge of the forthcoming allocation."
                ),
            },
            {
                "id": "1.3",
                "text": "Did baseline differences between intervention groups suggest a problem with the randomization process?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Differences compatible with chance do not indicate bias. Answer 'Yes' only if "
                    "imbalances indicate a problem: substantial differences in group sizes vs the "
                    "allocation ratio; a substantial excess of statistically-significant baseline "
                    "differences; imbalance in one or more key prognostic factors unlikely to be due to "
                    "chance; or excessive similarity not compatible with chance. Answer 'No information' "
                    "when there are no useful baseline data."
                ),
            },
        ],
    },
    {
        "id": 2,
        "name": "Bias due to deviations from intended interventions (effect of assignment)",
        "relevant_fields": ["blinding_participants", "blinding_personnel",
                             "protocol_deviations", "analysis_framework",
                             "missing_data_handling"],
        "signals": [
            {"id": "2.1",
             "text": "Were participants aware of their assigned intervention during the trial?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Participants aware of assignment may change health-related behaviours. Blinding (placebo/sham) prevents such differences. If participants experienced intervention-specific side effects they knew to be specific, answer 'Yes' or 'Probably yes'."},
            {"id": "2.2",
             "text": "Were carers and people delivering the interventions aware of participants' assigned intervention during the trial?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "If carers/deliverers are aware, implementation may differ (e.g., co-intervention). If allocation was not concealed, awareness is likely. Side effects specific to one intervention also count as awareness."},
            {"id": "2.3",
             "text": "If Y/PY/NI to 2.1 or 2.2: Were there deviations from the intended intervention that arose because of the trial context?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Assess problems from changes inconsistent with protocol that arose because of trial context (recruitment/engagement, securing consent, etc). Answer 'Yes' only if evidence/strong reason to believe such deviations happened. Non-adherence consistent with real-world care = No. Protocol-permitted changes (e.g., drug cessation for acute toxicity) = No."},
            {"id": "2.4",
             "text": "If Y/PY to 2.3: Were these deviations likely to have affected the outcome?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Changes arising from trial context impact the effect estimate only if they affect the outcome."},
            {"id": "2.5",
             "text": "If Y/PY/NI to 2.4: Were these deviations from intended intervention balanced between groups?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Unbalanced trial-context deviations bias the effect estimate more than balanced ones."},
            {"id": "2.6",
             "text": "Was an appropriate analysis used to estimate the effect of assignment to intervention?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "ITT and modified-ITT (excluding missing outcome data only) are appropriate. Naïve per-protocol and as-treated analyses are inappropriate. Post-randomization exclusion of ineligible participants whose eligibility was confirmed after randomization and couldn't have been affected by assignment can be appropriate."},
            {"id": "2.7",
             "text": "If N/PN/NI to 2.6: Was there potential for a substantial impact (on the result) of the failure to analyse participants in the group to which they were randomized?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "No precise threshold. Substantial impact possible even with <5% wrong-group or excluded if the outcome is rare or exclusions relate to prognostic factors."},
        ],
    },
    {
        "id": 3,
        "name": "Bias due to missing outcome data",
        "relevant_fields": ["attrition_rate", "missing_data_handling"],
        "signals": [
            {"id": "3.1",
             "text": "Were data for this outcome available for all, or nearly all, participants randomized?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "ITT analysis requires data from all randomized. 'Nearly all' = missingness small enough that it could have made no important difference. For continuous outcomes, ≥95% is often sufficient; for dichotomous outcomes, the proportion depends on event rate. Imputed data counts as missing here."},
            {"id": "3.2",
             "text": "If N/PN/NI to 3.1: Is there evidence that the result was not biased by missing outcome data?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Evidence can come from (1) analysis methods correcting for bias, or (2) sensitivity analyses robust to plausible assumptions. Single-method imputation (LOCF or intervention-group-only multiple imputation) does NOT establish lack of bias."},
            {"id": "3.3",
             "text": "If N/PN to 3.2: Could missingness in the outcome depend on its true value?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "If loss-to-follow-up/withdrawal might be related to participants' health status, missingness could depend on the true outcome. Documented reasons unrelated to outcome (device failure, routine-collection interruption) → missingness unlikely to depend on true value."},
            {"id": "3.4",
             "text": "If Y/PY/NI to 3.3: Is it likely that missingness in the outcome depended on its true value?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Five reasons to answer Yes: differential missingness proportions between groups; reported reasons provide evidence; reasons differ between groups; trial circumstances make dependence likely (e.g., schizophrenia trials); time-to-event censoring when participants change treatment for outcome-related reasons. Answer No if analysis accounted for participant characteristics likely to explain the relationship."},
        ],
    },
    {
        "id": 4,
        "name": "Bias in measurement of the outcome",
        "relevant_fields": ["blinding_outcome_assessors", "outcome_measurement_method"],
        "signals": [
            {"id": "4.1",
             "text": "Was the method of measuring the outcome inappropriate?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Identifies measurement methods unsuitable for the outcome. Usually 'No' for pre-specified outcomes. 'Yes' if the method is unlikely to detect plausible effects or has poor validity."},
            {"id": "4.2",
             "text": "Could measurement or ascertainment of the outcome have differed between intervention groups?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Differences may arise from diagnostic detection bias in passive ascertainment, or from intervention-driven extra clinician visits giving more chances to detect outcome events."},
            {"id": "4.3",
             "text": "If N/PN/NI to 4.1 and 4.2: Were outcome assessors aware of the intervention received by study participants?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Answer 'No' if assessors were blinded to intervention status. For participant-reported outcomes, the outcome assessor IS the study participant."},
            {"id": "4.4",
             "text": "If Y/PY/NI to 4.3: Could assessment of the outcome have been influenced by knowledge of intervention received?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Knowledge could influence participant-reported outcomes (like pain), observer-reported outcomes involving judgement, and intervention-provider decision outcomes. Unlikely to influence all-cause mortality."},
            {"id": "4.5",
             "text": "If Y/PY/NI to 4.4: Is it likely that assessment of the outcome was influenced by knowledge of intervention received?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Distinguishes 'could have' (Some concerns) from 'likely did' (High). With strong beliefs in benefit/harm, it's more likely assessment was influenced (e.g., homeopathy participant-reported symptoms; physiotherapist-delivered-then-assessed function)."},
        ],
    },
    {
        "id": 5,
        "name": "Bias in selection of the reported result",
        "relevant_fields": ["protocol_available", "outcomes_match_protocol"],
        "signals": [
            {"id": "5.1",
             "text": "Were the data that produced this result analysed in accordance with a pre-specified analysis plan that was finalized before unblinded outcome data were available for analysis?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "If researchers' pre-specified intentions are available in sufficient detail, planned measurements/analyses can be compared to published. To avoid selection of the reported result, analysis-plan finalization must precede availability of unblinded outcome data."},
            {"id": "5.2",
             "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible outcome measurements (e.g., scales, definitions, time points) within the outcome domain?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "An outcome domain may be measured multiple ways. If multiple measurements were made but only one (or a subset) is fully reported without justification and the reported result is likely selected on the basis of results (novelty, significance, confirmation of prior hypothesis), answer 'Yes'."},
            {"id": "5.3",
             "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible analyses of the data?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "An outcome measurement may be analysed multiple ways (unadjusted vs adjusted; final value vs change; different composite definitions; covariate sets; missing-data strategies). If multiple estimates exist but only one is reported on the basis of results, answer 'Yes'."},
        ],
    },
]


# ─────────────────────────────────────────────
# Prompt building + LLM orchestration
# ─────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing risk of bias in a randomized trial "
    "using the Cochrane RoB 2 tool. Read the PDF carefully. Answer each signaling question "
    "with one of: Y (yes), PY (probably yes), PN (probably no), N (no), NI (no information). "
    "Provide a 1-2 sentence rationale for each answer, quoting the paper where possible. "
    "Return ONLY a valid JSON object — no preamble, no markdown fences."
)


def build_domain_prompt(domain: dict[str, Any],
                        study_type: str,
                        primary_outcome: str,
                        extracted_fields: dict[str, str]) -> str:
    """Per-domain prompt for RoB 2 signaling-question assessment.

    Sends:
      - outcome being assessed (RoB 2 is outcome-specific)
      - verbatim signaling questions + elaborations from the cribsheet
      - relevant extracted fields from the annotator (so the LLM sees structured data)
      - expected JSON shape
    """
    # Only surface fields that exist + have a non-empty value
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

    shape = "{\n"
    for sig in domain["signals"]:
        shape += f'  "{sig["id"]}": "Y|PY|PN|N|NI",\n'
        shape += f'  "{sig["id"]}_rationale": "1-2 sentences quoting the paper",\n'
    shape += '  "direction_of_bias": "NA|Favours experimental|Favours comparator|Towards null|Away from null|Unpredictable"\n'
    shape += "}"

    return f"""Assess **Domain {domain['id']} — {domain['name']}** for the study described in the attached PDF.

Study type: {study_type}
Outcome being assessed: {primary_outcome}

Context (fields already extracted from the paper):
{ctx_json}

Signaling questions:
{questions_block}

Return a JSON object with exactly this shape:
{shape}

Answer N (or PN) when the paper gives enough information to rule out the problem, and NI only when the paper is silent. Rationales must be short (1-2 sentences) and quote the paper verbatim where possible."""


def _assess_domain(pdf_bytes: bytes, domain: dict[str, Any],
                   study_type: str, primary_outcome: str,
                   extracted_fields: dict[str, str]) -> dict[str, Any]:
    """LLM-assess one domain and return {signals, rationales, judgement, direction}."""
    prompt = build_domain_prompt(domain, study_type, primary_outcome, extracted_fields)
    # 8k tokens is generous for 3-7 signal answers + rationales
    raw = _call_with_pdf(pdf_bytes, prompt, max_tokens=8192)

    signals: dict[str, str] = {}
    rationales: dict[str, str] = {}
    for sig in domain["signals"]:
        sid = sig["id"]
        ans = str(raw.get(sid, "NI")).strip().upper()
        if ans not in SIGNAL_OPTIONS:
            logger.warning("RoB 2 domain %s question %s: invalid answer %r — defaulting to NI",
                            domain["id"], sid, ans)
            ans = "NI"
        signals[sid] = ans
        rationales[sid] = str(raw.get(f"{sid}_rationale", "")).strip()

    judgement = DOMAIN_JUDGES[domain["id"]](signals)
    direction = str(raw.get("direction_of_bias", "NA")).strip() or "NA"
    return {
        "signals": signals,
        "rationales": rationales,
        "judgement": judgement,
        "direction": direction,
    }


def run(pdf_bytes: bytes,
        extracted_fields: dict[str, str],
        classification: dict[str, str],
        primary_outcome: str,
        progress: Callable[[int], None] | None = None) -> tuple[dict[str, Any], str, str]:
    """Run RoB 2 against a parallel-group RCT.

    Returns ``(domain_results, overall_judgement, overall_direction)``.

    - ``domain_results`` is keyed by domain id (``"1"`` … ``"5"``), each with
      ``{name, signals, rationales, judgement, direction}``.
    - ``overall_direction`` uses the most-common non-NA direction across domains
      (or "NA" if all domains report NA).
    """
    study_type = classification.get("study_type", "Randomized Controlled Trial")

    domain_results: dict[str, Any] = {}
    for domain in DOMAINS:
        if progress:
            try:
                progress(domain["id"])
            except Exception:
                pass
        result = _assess_domain(pdf_bytes, domain, study_type,
                                 primary_outcome, extracted_fields)
        result["id"] = domain["id"]
        result["name"] = domain["name"]
        domain_results[str(domain["id"])] = result

    overall = rob2_overall([domain_results[str(d["id"])]["judgement"] for d in DOMAINS])

    # Aggregate direction: take the single most-common non-NA direction;
    # if tied or all NA, fall back to "NA".
    dirs = [domain_results[str(d["id"])]["direction"] for d in DOMAINS
            if domain_results[str(d["id"])]["direction"] not in ("", "NA")]
    if not dirs:
        overall_direction = "NA"
    else:
        from collections import Counter
        counts = Counter(dirs).most_common()
        if len(counts) > 1 and counts[0][1] == counts[1][1]:
            overall_direction = "Unpredictable"
        else:
            overall_direction = counts[0][0]

    return domain_results, overall, overall_direction


# ─────────────────────────────────────────────
# Developer-view exposure
# ─────────────────────────────────────────────
def prompt_catalog() -> dict[str, Any]:
    """Return the prompts + decision-tree source for the developer icon."""
    import inspect
    domain_entries = []
    for domain in DOMAINS:
        judge_fn = DOMAIN_JUDGES[domain["id"]]
        sample_fields = {k: "<extracted value>" for k in domain["relevant_fields"]}
        domain_entries.append({
            "id": domain["id"],
            "name": domain["name"],
            "signals": domain["signals"],
            "relevant_fields": domain["relevant_fields"],
            "prompt_template": build_domain_prompt(
                domain, "Randomized Controlled Trial",
                "<primary outcome here>", sample_fields,
            ),
            "decision_tree_code": inspect.getsource(judge_fn),
        })
    return {
        "tool": "Cochrane RoB 2 (2019) — parallel-group trials",
        "system_prompt": _SYSTEM_PROMPT,
        "signal_options": list(SIGNAL_OPTIONS),
        "judgements": ["Low", "Some concerns", "High"],
        "domains": domain_entries,
        "overall_algorithm_code": inspect.getsource(rob2_overall),
    }
