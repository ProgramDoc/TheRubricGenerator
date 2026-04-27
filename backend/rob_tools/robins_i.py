"""ROBINS-I — Risk Of Bias In Non-randomised Studies of Interventions.

Source: Sterne JA, Hernán MA, Reeves BC, Savović J, Berkman ND, Viswanathan M,
et al. "ROBINS-I: a tool for assessing risk of bias in non-randomised studies
of interventions." BMJ 2016; 355:i4919. https://doi.org/10.1136/bmj.i4919

Applies to non-randomized studies of interventions: Cohort, Case-Control,
Case-Crossover, Non-Randomized Trial, and analytical Cross-Sectional studies.
Quasi-experimental designs (before-after, ITS, DiD, regression discontinuity)
need their own confounding-prompt adaptations and are deferred.

v1 scope: **effect-of-assignment** interpretation of D4 only (effect-of-adherence
variant deferred). Uses the 7-domain structure from the tool:

  D1  Confounding
  D2  Selection of participants into the study
  D3  Classification of interventions
  D4  Deviations from intended interventions (effect of assignment)
  D5  Missing data
  D6  Measurement of outcomes
  D7  Selection of the reported result

Signaling-question answers are Y / PY / PN / N / NI (same vocabulary as RoB 2).
Domain judgements are 5-level: **Low / Moderate / Serious / Critical / No information**.
Overall judgement = worst single-domain judgement per the tool's aggregation rule.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from ..annotator import _call_with_pdf

logger = logging.getLogger("rubricgen")


SIGNAL_OPTIONS = ("Y", "PY", "PN", "N", "NI")
JUDGEMENTS = ("Low", "Moderate", "Serious", "Critical", "No information")


# ─────────────────────────────────────────────
# Decision trees (pure Python — no LLM)
# ─────────────────────────────────────────────
# Each domain's algorithm is a direct translation of the ROBINS-I guidance
# document. The LLM answers signaling questions; code maps those answers to a
# judgement. Keeping the trees in code (not prompts) makes the developer view
# honest: reviewers can see the exact logic via inspect.getsource.


def _yes(ans: str) -> bool:
    return ans in ("Y", "PY")


def _no(ans: str) -> bool:
    return ans in ("N", "PN")


def robins_i_domain1_judge(signals: dict[str, str]) -> str:
    """Domain 1 (confounding) — ROBINS-I guidance §4.2.

    1.1 potential for confounding    → if N/PN, result = Low (no confounding expected).
    1.4 appropriate analysis         → if N/PN, result = Serious/Critical.
    1.5 confounders measured validly → downgrades 1.4-Y to Serious if poor.
    1.6 adjustment for post-intervention variables → if Y/PY, result = Serious
                                                     (adjustment on causal pathway biases the estimate).
    1.8 time-varying confounding     → if Y/PY unaddressed, worst-case Serious.
    """
    q11 = signals.get("1.1", "NI")
    q14 = signals.get("1.4", "NI")
    q15 = signals.get("1.5", "NI")
    q16 = signals.get("1.6", "NI")
    q18 = signals.get("1.8", "NI")

    # No potential for confounding → Low (rare for observational data)
    if _no(q11):
        return "Low"
    # Adjusted for a post-intervention variable → causal-pathway bias → Serious
    if _yes(q16):
        return "Serious"
    # No adjustment attempted at all → Critical
    if _no(q14):
        return "Critical"
    if q14 == "NI":
        return "No information"
    # 1.4 Y/PY: analysis attempted. Quality of measurement matters.
    if _no(q15):
        return "Serious"
    if q15 == "NI":
        return "Moderate"
    # Time-varying confounding unaddressed → bump to Serious
    if _yes(q18):
        return "Serious"
    # Best case for a well-adjusted observational study
    return "Moderate"


def robins_i_domain2_judge(signals: dict[str, str]) -> str:
    """Domain 2 (selection of participants) — ROBINS-I guidance §4.3.

    2.1 selection based on post-intervention characteristics?
    2.2 post-intervention selection associated with intervention?
    2.3 post-intervention selection influenced by outcome?
    2.4 follow-up start coincides with intervention start?
    2.5 adjustment methods used that correct selection bias?
    """
    q21 = signals.get("2.1", "NI")
    q22 = signals.get("2.2", "NI")
    q23 = signals.get("2.3", "NI")
    q24 = signals.get("2.4", "NI")
    q25 = signals.get("2.5", "NI")

    # Clean case: no post-intervention selection AND follow-up aligns
    if _no(q21) and _yes(q24):
        return "Low"
    # Post-intervention selection linked to both intervention and outcome
    if _yes(q21) and _yes(q22) and _yes(q23):
        return "Moderate" if _yes(q25) else "Serious"
    # Post-intervention selection linked to intervention only
    if _yes(q21) and _yes(q22):
        return "Moderate" if _yes(q25) else "Serious"
    # Immortal-time-bias risk (follow-up doesn't coincide)
    if _no(q24):
        return "Moderate" if _yes(q25) else "Serious"
    return "Moderate"


def robins_i_domain3_judge(signals: dict[str, str]) -> str:
    """Domain 3 (classification of interventions) — ROBINS-I guidance §4.4.

    3.1 intervention groups clearly defined?
    3.2 information recorded at the start of the intervention?
    3.3 classification affected by knowledge of the outcome?
    """
    q31 = signals.get("3.1", "NI")
    q32 = signals.get("3.2", "NI")
    q33 = signals.get("3.3", "NI")

    if _yes(q33):
        return "Serious"
    if _no(q31) or _no(q32):
        return "Moderate"
    if q31 == "NI" or q32 == "NI":
        return "No information"
    return "Low"


def robins_i_domain4_judge(signals: dict[str, str]) -> str:
    """Domain 4 (deviations from intended interventions, effect-of-assignment
    variant) — ROBINS-I guidance §4.5.

    4.1 deviations beyond usual care?
    4.2 unbalanced deviations likely to have affected outcome?
    4.3 important co-interventions balanced between groups?
    4.4 intervention implemented correctly?
    4.5 appropriate analysis for effect of assignment?
    """
    q41 = signals.get("4.1", "NI")
    q42 = signals.get("4.2", "NI")
    q43 = signals.get("4.3", "NI")
    q44 = signals.get("4.4", "NI")
    q45 = signals.get("4.5", "NI")

    # Best case: no unusual deviations, correct implementation, balanced co-interventions
    if _no(q41) and _yes(q44) and _yes(q43):
        return "Low"
    # Unbalanced deviations likely to affect outcome
    if _yes(q41) and _yes(q42):
        return "Moderate" if _yes(q45) else "Serious"
    # Intervention not correctly implemented → Serious
    if _no(q44):
        return "Serious"
    # Co-interventions unbalanced → Moderate at least
    if _no(q43):
        return "Moderate"
    return "Moderate"


def robins_i_domain5_judge(signals: dict[str, str]) -> str:
    """Domain 5 (missing data) — ROBINS-I guidance §4.6.

    5.1 outcome data available for all/nearly all participants?
    5.2 participants excluded due to missing intervention status?
    5.3 participants excluded due to missing data on other variables?
    """
    q51 = signals.get("5.1", "NI")
    q52 = signals.get("5.2", "NI")
    q53 = signals.get("5.3", "NI")

    if _yes(q51) and _no(q52) and _no(q53):
        return "Low"
    if _no(q51) and (_yes(q52) or _yes(q53)):
        return "Serious"
    if _no(q51):
        return "Moderate"
    if q51 == "NI":
        return "No information"
    return "Moderate"


def robins_i_domain6_judge(signals: dict[str, str]) -> str:
    """Domain 6 (measurement of outcomes) — ROBINS-I guidance §4.7.

    6.1 could outcome measure be influenced by intervention knowledge?
    6.2 were outcome assessors aware of intervention received?
    6.3 were outcome methods comparable across groups?
    6.4 were systematic measurement errors related to intervention?
    6.5 likely that measurement was influenced by intervention knowledge?
    """
    q61 = signals.get("6.1", "NI")
    q62 = signals.get("6.2", "NI")
    q63 = signals.get("6.3", "NI")
    q64 = signals.get("6.4", "NI")
    q65 = signals.get("6.5", "NI")

    if _yes(q64) or _yes(q65):
        return "Serious"
    if _no(q63):
        return "Serious"
    # Objective measurement OR blinded assessors → Low
    if _no(q61) and _no(q62):
        return "Low"
    # Some NI — honest "no information"
    if q61 == "NI" and q62 == "NI":
        return "No information"
    return "Moderate"


def robins_i_domain7_judge(signals: dict[str, str]) -> str:
    """Domain 7 (selection of reported result) — ROBINS-I guidance §4.8.

    7.1 selected from multiple eligible outcome measurements?
    7.2 selected from multiple eligible analyses?
    7.3 selected from different subgroups on basis of results?
    """
    q71 = signals.get("7.1", "NI")
    q72 = signals.get("7.2", "NI")
    q73 = signals.get("7.3", "NI")

    if _yes(q71) or _yes(q72) or _yes(q73):
        return "Serious"
    if q71 == "NI" and q72 == "NI" and q73 == "NI":
        return "No information"
    return "Low"


def robins_i_overall(domain_judgements: list[str]) -> str:
    """Overall judgement — worst-domain aggregation per ROBINS-I guidance p.10.

    Order of severity (worst → best):
      Critical > Serious > Moderate > No information > Low.

    A single Critical domain makes the study Critical overall. A single Serious
    domain makes it Serious. "No information" means at least one domain could
    not be judged but no Moderate/Serious/Critical domains were found.
    """
    if any(j == "Critical" for j in domain_judgements):
        return "Critical"
    if any(j == "Serious" for j in domain_judgements):
        return "Serious"
    if any(j == "Moderate" for j in domain_judgements):
        return "Moderate"
    if any(j == "No information" for j in domain_judgements):
        return "No information"
    return "Low"


DOMAIN_JUDGES: dict[int, Callable[[dict[str, str]], str]] = {
    1: robins_i_domain1_judge,
    2: robins_i_domain2_judge,
    3: robins_i_domain3_judge,
    4: robins_i_domain4_judge,
    5: robins_i_domain5_judge,
    6: robins_i_domain6_judge,
    7: robins_i_domain7_judge,
}


# ─────────────────────────────────────────────
# Domain definitions — signaling questions + verbatim elaborations
# ─────────────────────────────────────────────
DOMAINS: list[dict[str, Any]] = [
    {
        "id": 1,
        "name": "Bias due to confounding",
        "relevant_fields": ["confounders_measured", "adjustment_method",
                             "exposure_definition", "comparator_group",
                             "immortal_time_bias", "confounding_control"],
        "signals": [
            {"id": "1.1",
             "text": "Is there potential for confounding of the effect of intervention in this study?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Confounding is expected in almost all non-randomized studies. Answer 'No' or 'Probably no' only when randomization or strong quasi-experimental design rules it out (e.g., if the intervention is truly unrelated to participant characteristics)."},
            {"id": "1.2",
             "text": "Was the analysis based on splitting participants' follow up time according to intervention received?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Time-split analyses compare time on vs off treatment within participants. They avoid some selection issues but require handling of time-varying confounding."},
            {"id": "1.3",
             "text": "If Y/PY to 1.2: Were intervention discontinuations or switches likely to be related to factors that are prognostic for the outcome?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "If people stopped/switched intervention for reasons related to outcome prognosis (e.g., side effects, worsening disease), the time-split comparison is confounded."},
            {"id": "1.4",
             "text": "Did the authors use an appropriate analysis method that controlled for all the important confounding domains?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "The paper should identify important confounding domains (baseline characteristics associated with both intervention and outcome) and use multivariable adjustment, stratification, matching, propensity scores, or similar. 'No' if no adjustment is attempted or key domains are omitted."},
            {"id": "1.5",
             "text": "If Y/PY to 1.4: Were confounding domains that were controlled for measured validly and reliably by the variables available in this study?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Adjustment only helps if the confounders were measured well. Self-report of important variables, missing data on confounders, or measurement at the wrong time point weakens adjustment."},
            {"id": "1.6",
             "text": "Did the authors control for any post-intervention variables that could have been affected by the intervention?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Adjusting for variables on the causal pathway between intervention and outcome biases the effect estimate. Classic example: adjusting for a biomarker that the intervention changes."},
            {"id": "1.7",
             "text": "Did the authors use an appropriate analysis method that controlled for time-varying confounding?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Applies when confounders change over time and intervention decisions depend on those time-varying values (e.g., clinicians adjusting dose based on disease progression). Methods like marginal structural models or g-estimation are appropriate; standard regression is not."},
            {"id": "1.8",
             "text": "Were there important time-varying confounding effects that the analysis did not account for?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Answer 'Yes' only if you have clear evidence of unaddressed time-varying confounding; 'No' when not relevant or appropriately handled."},
        ],
    },
    {
        "id": 2,
        "name": "Bias in selection of participants into the study",
        "relevant_fields": ["case_source", "control_selection", "matching",
                             "sampling_method", "loss_to_follow_up",
                             "immortal_time_bias"],
        "signals": [
            {"id": "2.1",
             "text": "Was selection of participants into the study (or analysis) based on participant characteristics observed after the start of intervention?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Examples: restricting the analytic sample to people who completed treatment; selecting cases based on events observed during follow-up; excluding early deaths."},
            {"id": "2.2",
             "text": "If Y/PY to 2.1: Were the post-intervention variables that influenced selection likely to be associated with intervention?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "If selection variables are associated with intervention (e.g., completers differ between arms), selection can bias the effect estimate."},
            {"id": "2.3",
             "text": "If Y/PY to 2.1 and 2.2: Were the post-intervention variables that influenced selection likely to be influenced by the outcome or a cause of the outcome?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Selection linked to intervention AND to outcome (or its causes) produces collider/selection bias."},
            {"id": "2.4",
             "text": "Do start of follow-up and start of intervention coincide for most participants?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Answer 'No' if participants accrue person-time before intervention start (immortal time) or after (lag). Mis-timed follow-up usually inflates apparent protective effects of treatment."},
            {"id": "2.5",
             "text": "Were adjustment techniques used that are likely to correct for the presence of selection biases?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Inverse-probability-of-selection weighting, exclusion of the immortal period, or landmark analysis can correct selection bias. Simple multivariable adjustment typically does not."},
        ],
    },
    {
        "id": 3,
        "name": "Bias in classification of interventions",
        "relevant_fields": ["exposure_definition", "exposure_measurement",
                             "exposure_ascertainment"],
        "signals": [
            {"id": "3.1",
             "text": "Were intervention groups clearly defined?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Clear definitions specify the intervention(s) under study, its dose/duration/route, and an explicit comparator."},
            {"id": "3.2",
             "text": "Was the information used to define intervention groups recorded at the start of the intervention?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Prospective recording of intervention status prevents recall/memory-based misclassification. Retrospective ascertainment from medical records at a later time is weaker."},
            {"id": "3.3",
             "text": "Could classification of intervention status have been affected by knowledge of the outcome or risk of the outcome?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Differential misclassification — e.g., exposure recorded after the event occurred, case-control studies reconstructing exposure history — inflates or distorts effect estimates."},
        ],
    },
    {
        "id": 4,
        "name": "Bias due to deviations from intended interventions (effect of assignment)",
        "relevant_fields": ["blinding", "allocation_mechanism",
                             "baseline_comparability", "analysis_framework"],
        "signals": [
            {"id": "4.1",
             "text": "Were there deviations from the intended intervention beyond what would be expected in usual practice?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Usual-practice deviations (treatment stopped for side effects, patient preference changes) are acceptable. Trial-context deviations (unblinded providers altering care, protocol-mandated changes not planned for routine use) are concerning."},
            {"id": "4.2",
             "text": "If Y/PY to 4.1: Were these deviations from intended intervention unbalanced between groups and likely to have affected the outcome?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Balanced deviations bias less than unbalanced. Judge substantial effect on the outcome of interest."},
            {"id": "4.3",
             "text": "Were important co-interventions balanced between intervention groups?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Co-interventions (other treatments received alongside the intervention) that differ between groups can confound the effect estimate."},
            {"id": "4.4",
             "text": "Was the intervention implemented as intended, with fidelity?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Was each participant in the intervention group actually exposed to (a dose of) the intended intervention? Poor fidelity (low adherence, wrong dose) attenuates observed effects."},
            {"id": "4.5",
             "text": "Was an appropriate analysis used to estimate the effect of assignment to intervention?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "ITT or intention-equivalent analyses (using the intervention as assigned rather than as received) estimate the effect of assignment. Per-protocol or as-treated analyses estimate something different and can be biased."},
        ],
    },
    {
        "id": 5,
        "name": "Bias due to missing data",
        "relevant_fields": ["loss_to_follow_up", "missing_data_handling",
                             "attrition_rate"],
        "signals": [
            {"id": "5.1",
             "text": "Were outcome data available for all, or nearly all, participants?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "'Nearly all' means missingness is small enough that it could not meaningfully change the effect estimate. Judge by proportion AND by whether missing participants differ from available ones."},
            {"id": "5.2",
             "text": "Were participants excluded from the analysis due to missing data on the intervention status?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Excluding people with unknown intervention status can introduce bias if their outcomes differ systematically."},
            {"id": "5.3",
             "text": "Were participants excluded from the analysis due to missing data on other variables needed for the analysis?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Complete-case analysis on confounders/effect modifiers may bias the estimate when missingness is not completely at random."},
        ],
    },
    {
        "id": 6,
        "name": "Bias in measurement of outcomes",
        "relevant_fields": ["outcome_ascertainment", "outcome_definition"],
        "signals": [
            {"id": "6.1",
             "text": "Could the outcome measure have been influenced by knowledge of the intervention received?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Subjective outcomes (pain, quality of life, clinician judgement) can be influenced by intervention knowledge. Objective outcomes (all-cause mortality, linked-registry events) usually cannot."},
            {"id": "6.2",
             "text": "Were outcome assessors aware of the intervention received by study participants?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Blinded assessors eliminate knowledge-driven measurement bias. In observational studies blinding is rarely formal — assess whether assessors had effective access to intervention status when scoring the outcome."},
            {"id": "6.3",
             "text": "Were the methods of outcome assessment comparable across intervention groups?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Differential follow-up frequency, different diagnostic workups, or different case-finding between groups create detection bias."},
            {"id": "6.4",
             "text": "Were any systematic errors in measurement of the outcome related to the intervention received?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Yes if intervention modifies the measured quantity (e.g., treatment that changes a biomarker used to define the outcome) without truly changing the underlying clinical state."},
            {"id": "6.5",
             "text": "Is it likely that assessment of the outcome was influenced by knowledge of the intervention received?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Distinguishes 'could have been' (some concerns) from 'likely was' (serious). Knowledge influence is more likely with strong beliefs about intervention benefit/harm."},
        ],
    },
    {
        "id": 7,
        "name": "Bias in selection of the reported result",
        "relevant_fields": ["outcome_definition", "statistical_analysis"],
        "signals": [
            {"id": "7.1",
             "text": "Is the reported effect estimate likely to be selected, on the basis of the results, from multiple outcome measurements within the outcome domain?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Outcome domain may be measured multiple ways (scales, time points, definitions). If only the most favorable measurement is reported without prespecification, answer 'Yes'."},
            {"id": "7.2",
             "text": "Is the reported effect estimate likely to be selected, on the basis of the results, from multiple analyses of the intervention-outcome relationship?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Multiple modeling choices (unadjusted vs adjusted, alternative covariate sets, different missing-data strategies) can produce different estimates. Selection on favorable results is concerning."},
            {"id": "7.3",
             "text": "Is the reported effect estimate likely to be selected, on the basis of the results, from different subgroups?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Post-hoc subgroup reporting driven by where effects look largest is a form of result-driven selection."},
        ],
    },
]


# ─────────────────────────────────────────────
# Prompt building + LLM orchestration
# ─────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing risk of bias in a "
    "non-randomized study of an intervention using the Cochrane ROBINS-I tool. "
    "Read the PDF carefully. Answer each signaling question with one of: "
    "Y (yes), PY (probably yes), PN (probably no), N (no), NI (no information). "
    "Provide a 1-2 sentence rationale for each answer, quoting the paper where "
    "possible. Return ONLY a valid JSON object — no preamble, no markdown fences."
)


def build_domain_prompt(domain: dict[str, Any],
                        study_type: str,
                        primary_outcome: str,
                        extracted_fields: dict[str, str]) -> str:
    """Per-domain prompt for ROBINS-I signaling-question assessment."""
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

ROBINS-I answers carry different meaning than RoB 2: the judgement scale is Low / Moderate / Serious / Critical / No information (code maps your signal answers to this judgement). Answer N (or PN) when the paper gives enough information to rule out the problem, and NI only when the paper is silent. Rationales must be short (1-2 sentences) and quote the paper verbatim where possible."""


def _assess_domain(pdf_bytes: bytes, domain: dict[str, Any],
                   study_type: str, primary_outcome: str,
                   extracted_fields: dict[str, str]) -> dict[str, Any]:
    """LLM-assess one domain and return {signals, rationales, judgement, direction}."""
    prompt = build_domain_prompt(domain, study_type, primary_outcome, extracted_fields)
    raw = _call_with_pdf(pdf_bytes, prompt, max_tokens=8192)

    signals: dict[str, str] = {}
    rationales: dict[str, str] = {}
    for sig in domain["signals"]:
        sid = sig["id"]
        ans = str(raw.get(sid, "NI")).strip().upper()
        if ans not in SIGNAL_OPTIONS:
            logger.warning("ROBINS-I domain %s question %s: invalid answer %r — defaulting to NI",
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
    """Run ROBINS-I against a non-randomized study of an intervention.

    Returns ``(domain_results, overall_judgement, overall_direction)``.
    """
    study_type = classification.get("study_type", "Cohort Study")

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

    overall = robins_i_overall(
        [domain_results[str(d["id"])]["judgement"] for d in DOMAINS])

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
                domain, "Cohort Study",
                "<primary outcome here>", sample_fields,
            ),
            "decision_tree_code": inspect.getsource(judge_fn),
        })
    return {
        "tool": "ROBINS-I (Sterne et al. 2016) — non-randomized studies of interventions",
        "system_prompt": _SYSTEM_PROMPT,
        "signal_options": list(SIGNAL_OPTIONS),
        "judgements": list(JUDGEMENTS),
        "domains": domain_entries,
        "overall_algorithm_code": inspect.getsource(robins_i_overall),
    }
