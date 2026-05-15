"""Cochrane RoB 2 — Risk of Bias for cross-over randomized trials.

Encodes the official Cochrane RoB 2 crossover-trials cribsheet
(Higgins, Eldridge, Li, Sterne — the crossover extension to RoB 2.0):

- ``DOMAINS`` — 6 domains × their signaling questions + verbatim elaborations.
- ``rob2_crossover_domainN_judge(signals)`` — pure-Python decision trees.
- ``rob2_crossover_overall(domain_judgements)`` — aggregate per the cribsheet.
- ``run(pdf_bytes, fields, classification, assessed_outcome, progress)`` —
  per-domain LLM calls via the annotator's ``_call_with_pdf`` pipeline, then
  local decision-tree evaluation.

Six domains (vs. five for parallel-group):

  D1  Bias arising from the randomization process
  D2  Bias due to deviations from intended interventions (effect of assignment)
  DS  Bias arising from period and carryover effects   ← NEW (crossover-only)
  D3  Bias due to missing outcome data
  D4  Bias in measurement of the outcome
  D5  Bias in selection of the reported result          ← has 5.1, 5.2, 5.3, 5.4

Domain 5 gains a fourth signaling question (5.4) covering the situation in
which only first-period data are reported on the basis of a test for
carryover — a form of result selection unique to the crossover design.

Signaling-question answers are Y / PY / PN / N / NI (the cribsheet's options).
Domain judgements are "Low" / "Some concerns" / "High".

Where the crossover cribsheet elaboration says "as for parallel group trials",
the question text and elaboration are reused verbatim from
:mod:`backend.rob_tools.rob2`. Crossover-specific elaborations are transcribed
from the crossover cribsheet (final wording should be cross-checked against
the latest official PDF when shipping a new version).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from fastapi import HTTPException

from ..annotator import _call_with_pdf
from ..helpers import parse_json_response
from . import rob2

logger = logging.getLogger("rubricgen")


SIGNAL_OPTIONS = ("Y", "PY", "PN", "N", "NI")


# ─────────────────────────────────────────────
# Decision trees (pure Python — no LLM)
# ─────────────────────────────────────────────


def _yes(ans: str) -> bool:
    return ans in ("Y", "PY")


def _no(ans: str) -> bool:
    return ans in ("N", "PN")


def rob2_crossover_domain1_judge(signals: dict[str, str]) -> str:
    """Domain 1 (randomization process) — crossover cribsheet.

    Mirrors the parallel-group flowchart; 1.3 is reframed for period-1
    starting characteristics across sequence groups.
    """
    return rob2.rob2_domain1_judge(signals)


def rob2_crossover_domain2_judge(signals: dict[str, str]) -> str:
    """Domain 2 (deviations from intended interventions — effect of assignment).

    Crossover cribsheet follows the same Part 1 / Part 2 structure as the
    parallel-group cribsheet, with elaborations referring to sequence groups
    and per-period adherence.
    """
    return rob2.rob2_domain2_judge(signals)


def rob2_crossover_domainS_judge(signals: dict[str, str]) -> str:
    """Domain S (bias arising from period and carryover effects).

    Inputs: ``{"S.1": ..., "S.2": ..., "S.3": ..., "S.4": ...}``

    Decision logic (per the crossover cribsheet):

      - If carryover effects can be discounted (S.1 Y/PY)
        AND analysis appropriately took the cross-over design into account
        (S.4 Y/PY) → Low.
      - If carryover is plausible (S.1 N/PN) AND there was NO suitable washout
        (S.2 N/PN) AND unbiased data are NOT available (S.3 N/PN) → High.
      - If carryover is plausible (S.1 N/PN) AND analysis was not paired /
        appropriate (S.4 N/PN) → High.
      - Otherwise → Some concerns.
    """
    s1 = signals.get("S.1", "NI")
    s2 = signals.get("S.2", "NI")
    s3 = signals.get("S.3", "NI")
    s4 = signals.get("S.4", "NI")

    # Carryover ruled out + paired/appropriate analysis → Low
    if _yes(s1) and _yes(s4):
        return "Low"

    # Carryover plausible + no washout + no unbiased data → High
    if _no(s1) and _no(s2) and _no(s3):
        return "High"

    # Carryover plausible + analysis didn't take design into account → High
    if _no(s1) and _no(s4):
        return "High"

    # Inappropriate analysis on its own (regardless of carryover ruled-out
    # status) → High, because results aren't usable
    if _no(s4) and not _yes(s1):
        return "High"

    return "Some concerns"


def rob2_crossover_domain3_judge(signals: dict[str, str]) -> str:
    """Domain 3 (missing outcome data) — crossover cribsheet.

    Largely as parallel-group; elaborations adjusted for per-period missingness.
    """
    return rob2.rob2_domain3_judge(signals)


def rob2_crossover_domain4_judge(signals: dict[str, str]) -> str:
    """Domain 4 (measurement of the outcome) — crossover cribsheet.

    Largely as parallel-group.
    """
    return rob2.rob2_domain4_judge(signals)


def rob2_crossover_domain5_judge(signals: dict[str, str]) -> str:
    """Domain 5 (selection of the reported result) — crossover cribsheet.

    Four signaling questions (5.1, 5.2, 5.3, 5.4). 5.4 is crossover-specific:
    it flags selective reporting of first-period-only data based on a test
    for carryover.

      - 5.2 or 5.3 Y/PY → High (multiple eligible measurements / analyses).
      - 5.4 Y/PY        → High (first-period-only reporting on carryover test).
      - 5.2 and 5.3 and 5.4 all N/PN → consult 5.1: Y/PY → Low, else Some concerns.
      - Otherwise (NI mixed with N/PN, no Y/PY) → Some concerns.
    """
    q51 = signals.get("5.1", "NI")
    q52 = signals.get("5.2", "NI")
    q53 = signals.get("5.3", "NI")
    q54 = signals.get("5.4", "NI")

    if _yes(q52) or _yes(q53) or _yes(q54):
        return "High"
    if _no(q52) and _no(q53) and _no(q54):
        return "Low" if _yes(q51) else "Some concerns"
    return "Some concerns"


def rob2_crossover_overall(domain_judgements: list[str]) -> str:
    """Overall RoB judgement, aggregated as for parallel-group RoB 2."""
    return rob2.rob2_overall(domain_judgements)


# Domain ids include the string "S" for the crossover-only period/carryover
# domain. Iteration order matches the cribsheet's narrative order
# (1 → 2 → S → 3 → 4 → 5).
DOMAIN_JUDGES: dict[Any, Callable[[dict[str, str]], str]] = {
    1:   rob2_crossover_domain1_judge,
    2:   rob2_crossover_domain2_judge,
    "S": rob2_crossover_domainS_judge,
    3:   rob2_crossover_domain3_judge,
    4:   rob2_crossover_domain4_judge,
    5:   rob2_crossover_domain5_judge,
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
                             "baseline_balance", "sequence_order"],
        "signals": [
            {
                "id": "1.1",
                "text": "Was the allocation sequence random?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "As for parallel group trials. Answer 'Yes' if a random component was used in the "
                    "sequence generation process (computer-generated random numbers; random-number "
                    "table; coin tossing; shuffling cards or envelopes; throwing dice; drawing lots). "
                    "For cross-over trials the unit of allocation is the sequence of interventions, not "
                    "the intervention itself."
                ),
            },
            {
                "id": "1.2",
                "text": "Was the allocation sequence concealed until participants were enrolled and assigned to intervention sequences?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "As for parallel group trials. Answer 'Yes' if remote/central allocation, or "
                    "opaque, sequentially-numbered, tamper-sealed envelopes were used. Concealment "
                    "in a cross-over trial applies to the allocated sequence of interventions."
                ),
            },
            {
                "id": "1.3",
                "text": "Did baseline differences between intervention groups suggest a problem with the randomization process?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "For cross-over trials, baseline imbalance is assessed across the sequence groups "
                    "(e.g., AB vs BA) at study entry — before the first period begins. Differences "
                    "compatible with chance do not indicate bias. Answer 'Yes' only if imbalances "
                    "indicate a problem with the randomization process. Answer 'No information' when "
                    "no useful sequence-group baseline data are reported."
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
             "elaboration": "As for parallel group trials. In a cross-over trial, participants experience both interventions; awareness during each period is relevant for behavioural co-interventions and reporting bias on participant-reported outcomes."},
            {"id": "2.2",
             "text": "Were carers and people delivering the interventions aware of participants' assigned intervention during the trial?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "As for parallel group trials, evaluated separately for each period."},
            {"id": "2.3",
             "text": "If Y/PY/NI to 2.1 or 2.2: Were there deviations from the intended intervention that arose because of the trial context?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "As for parallel group trials. Examples include unplanned dose adjustments or use of prohibited co-interventions that arose because the trial was being conducted. Per-period adherence may differ between sequence groups; consider both periods."},
            {"id": "2.4",
             "text": "If Y/PY to 2.3: Were these deviations likely to have affected the outcome?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "As for parallel group trials. Deviations only impact the effect estimate if they affect the outcome."},
            {"id": "2.5",
             "text": "If Y/PY/NI to 2.4: Were these deviations from intended intervention balanced between groups?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "For cross-over trials, 'balanced between groups' means balanced between sequence groups across periods. Unbalanced trial-context deviations bias the effect estimate."},
            {"id": "2.6",
             "text": "Was an appropriate analysis used to estimate the effect of assignment to intervention?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "For cross-over trials the appropriate analysis is paired (within-participant) and either explicitly models period and/or carryover effects or demonstrates these are not material. Naïve unpaired analyses comparing arms ignore the within-participant structure and are inappropriate when paired data are available."},
            {"id": "2.7",
             "text": "If N/PN/NI to 2.6: Was there potential for a substantial impact (on the result) of the failure to analyse participants in the group to which they were randomized?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "No precise threshold. In cross-over trials this includes participants dropping out before completing the second period — substantial impact is possible if their period-1 data are mishandled."},
        ],
    },
    {
        "id": "S",
        "name": "Bias arising from period and carryover effects",
        "relevant_fields": ["washout_period", "carryover_assessment",
                             "period_effects", "paired_analysis"],
        "signals": [
            {"id": "S.1",
             "text": "Were carryover effects unlikely to occur in this trial, given the nature of the interventions and the outcome?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Carryover is the persistence of a treatment effect into the subsequent period. Answer 'Yes' if pharmacokinetic/pharmacodynamic reasoning, or the nature of the condition and outcome, make carryover implausible. Answer 'No information' if carryover plausibility cannot be assessed from the report."},
            {"id": "S.2",
             "text": "If carryover effects could occur, was there a suitable washout period between treatment periods?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "A 'suitable' washout is long enough relative to the half-life and pharmacodynamic effect of the interventions for residual effects to dissipate before the next period begins. Answer 'No information' if washout duration is not reported or its adequacy cannot be judged. If S.1 = Y/PY (carryover ruled out), this question can be answered 'NA'-equivalent (answer 'Yes')."},
            {"id": "S.3",
             "text": "For trials with potential for carryover effects, were unbiased data available for the analysis (e.g., from periods unaffected by carryover, or via methods that adjust for carryover)?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Answer 'Yes' if unbiased data are available — for example, only first-period data are used (and there is no other selection bias from doing so), or carryover is statistically modelled. Answer 'No' if carryover is plausible and no adjustment or mitigation was applied. If S.1 = Y/PY, answer 'Yes'."},
            {"id": "S.4",
             "text": "Were the data analysed using an appropriate paired analysis that takes the cross-over design into account?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "An appropriate cross-over analysis uses within-participant comparisons and, where indicated, models period and/or carryover effects. An unpaired analysis comparing period-1 outcomes between sequence groups discards the design's advantages and is generally inappropriate when paired data are available. Linear mixed models with participant random effects and fixed effects for period and treatment are typical."},
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
             "elaboration": "As for parallel group trials. For cross-over trials, 'available' means data are available for the participant in the period(s) contributing to the analysis. Participants missing data in one period only may still contribute to the analysis depending on the analytical approach."},
            {"id": "3.2",
             "text": "If N/PN/NI to 3.1: Is there evidence that the result was not biased by missing outcome data?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "As for parallel group trials. Evidence can come from analysis methods correcting for bias, or sensitivity analyses robust to plausible assumptions about missingness."},
            {"id": "3.3",
             "text": "If N/PN to 3.2: Could missingness in the outcome depend on its true value?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "As for parallel group trials. If loss-to-follow-up or period dropout might be related to participants' health status, missingness could depend on the true outcome value."},
            {"id": "3.4",
             "text": "If Y/PY/NI to 3.3: Is it likely that missingness in the outcome depended on its true value?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "As for parallel group trials."},
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
             "elaboration": "As for parallel group trials. Methods unsuitable for the outcome of interest may produce systematically biased estimates."},
            {"id": "4.2",
             "text": "Could measurement or ascertainment of the outcome have differed between intervention groups?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "For cross-over trials, this addresses whether measurement could differ between periods or between sequence groups. Identical measurement protocols across periods are expected."},
            {"id": "4.3",
             "text": "If N/PN/NI to 4.1 and 4.2: Were outcome assessors aware of the intervention received by study participants?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "As for parallel group trials. For participant-reported outcomes, the outcome assessor IS the study participant."},
            {"id": "4.4",
             "text": "If Y/PY/NI to 4.3: Could assessment of the outcome have been influenced by knowledge of intervention received?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "As for parallel group trials."},
            {"id": "4.5",
             "text": "If Y/PY/NI to 4.4: Is it likely that assessment of the outcome was influenced by knowledge of intervention received?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "As for parallel group trials."},
        ],
    },
    {
        "id": 5,
        "name": "Bias in selection of the reported result",
        "relevant_fields": ["protocol_available", "outcomes_match_protocol",
                             "paired_analysis", "carryover_assessment"],
        "signals": [
            {"id": "5.1",
             "text": "Were the data that produced this result analysed in accordance with a pre-specified analysis plan that was finalized before unblinded outcome data were available for analysis?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "As for parallel group trials."},
            {"id": "5.2",
             "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible outcome measurements (e.g., scales, definitions, time points) within the outcome domain?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "As for parallel group trials."},
            {"id": "5.3",
             "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible analyses of the data?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "Largely as for parallel group trials. It is possible that trial authors might decide between presenting a paired analysis and an unpaired analysis, or between an analysis that does and does not include a period effect, on the basis of the results. The result of an unpaired analysis will generally be less precise, so a decision to present only an unpaired analysis might reflect a desire to minimize evidence of an intervention effect or to suggest equivalence of interventions. Similarly, including period effects in analysis will generally lead to less a precise intervention effect estimate than an analysis that does not include them."},
            {"id": "5.4",
             "text": "Is a result based on data from both periods sought, but unavailable on the basis of carryover having been identified?",
             "options": list(SIGNAL_OPTIONS),
             "elaboration": "This question addresses the situation in which only results from the first period are reported on the basis of a test for carryover. Answer 'N' if data from both periods contribute to the result being assessed for risk of bias."},
        ],
    },
]


# ─────────────────────────────────────────────
# Prompt building + LLM orchestration
# ─────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing risk of bias in a "
    "**cross-over** randomized trial using the Cochrane RoB 2 tool (cross-over "
    "extension). Read the PDF carefully. Answer each signaling question with "
    "one of: Y (yes), PY (probably yes), PN (probably no), N (no), NI (no "
    "information). Provide a 1-2 sentence rationale for each answer, quoting "
    "the paper where possible. Return ONLY a valid JSON object — no preamble, "
    "no markdown fences."
)


def build_domain_prompt(domain: dict[str, Any],
                        study_type: str,
                        assessed_outcome: str,
                        extracted_fields: dict[str, str],
                        outcome_is_override: bool = False) -> str:
    """Per-domain prompt for cross-over RoB 2 signaling-question assessment."""
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

    override_note = ""
    if outcome_is_override and str(domain["id"]) == "1":
        # Same Domain 1 override note used by the parallel-group module — surface
        # the fact that the assessed outcome is a reviewer pick, and remind the
        # LLM that randomization is per-trial (not per-outcome).
        override_note = (
            "\n\nNote: this assessment is for a non-primary outcome chosen by the "
            "reviewer because the paper's primary outcome was unclear. Domain 1 "
            "signaling questions concern the randomization process for the trial "
            "as a whole, not the specific outcome — answer accordingly."
        )

    return f"""Assess **Domain {domain['id']} — {domain['name']}** for the **cross-over** trial described in the attached PDF.

Study type: {study_type}
Outcome being assessed: {assessed_outcome}{override_note}

Context (fields already extracted from the paper):
{ctx_json}

Signaling questions:
{questions_block}

Return a JSON object with exactly this shape:
{shape}

Answer N (or PN) when the paper gives enough information to rule out the problem, and NI only when the paper is silent. Rationales must be short (1-2 sentences) and quote the paper verbatim where possible."""


def _assess_domain(pdf_bytes: bytes, domain: dict[str, Any],
                   study_type: str, assessed_outcome: str,
                   extracted_fields: dict[str, str],
                   outcome_is_override: bool = False) -> dict[str, Any]:
    """LLM-assess one domain and return {signals, rationales, judgement, direction}."""
    prompt = build_domain_prompt(domain, study_type, assessed_outcome,
                                  extracted_fields, outcome_is_override)
    raw = _call_with_pdf(pdf_bytes, prompt, max_tokens=8192)

    signals: dict[str, str] = {}
    rationales: dict[str, str] = {}
    for sig in domain["signals"]:
        sid = sig["id"]
        ans = str(raw.get(sid, "NI")).strip().upper()
        if ans not in SIGNAL_OPTIONS:
            logger.warning("RoB 2 crossover domain %s question %s: invalid answer %r — defaulting to NI",
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
        assessed_outcome: str,
        progress: Callable[[Any], None] | None = None,
        outcome_is_override: bool = False) -> tuple[dict[str, Any], str, str]:
    """Run RoB 2 (cross-over extension) against a cross-over RCT.

    Returns ``(domain_results, overall_judgement, overall_direction)``.

    ``domain_results`` is keyed by string domain id (``"1"`` … ``"5"`` plus ``"S"``).
    """
    study_type = classification.get("study_type", "Crossover Trial")

    domain_results: dict[str, Any] = {}
    for domain in DOMAINS:
        if progress:
            try:
                progress(domain["id"])
            except Exception:
                pass
        result = _assess_domain(pdf_bytes, domain, study_type,
                                 assessed_outcome, extracted_fields,
                                 outcome_is_override=outcome_is_override)
        result["id"] = domain["id"]
        result["name"] = domain["name"]
        domain_results[str(domain["id"])] = result

    overall = rob2_crossover_overall(
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
                domain, "Crossover Trial",
                "<assessed outcome here>", sample_fields,
            ),
            "decision_tree_code": inspect.getsource(judge_fn),
        })
    return {
        "tool": "Cochrane RoB 2 — cross-over trials extension",
        "system_prompt": _SYSTEM_PROMPT,
        "signal_options": list(SIGNAL_OPTIONS),
        "judgements": ["Low", "Some concerns", "High"],
        "domains": domain_entries,
        "overall_algorithm_code": inspect.getsource(rob2_crossover_overall),
    }
