"""ROBINS-I V2 — Risk Of Bias In Non-randomised Studies of Interventions, Version 2.

Source: ROBINS-I V2 cribsheet (20 November 2025). ROBINS-I V2 development group:
Sterne JA, Brandt Mathur M, Elbers R, Hróbjartsson A, McAleenan A, Reeves B,
Shrier I, Tilling K, Armstrong R, Berkman N, Boutron I, Carpenter J, Chan AW,
Deeks J, Golder S, Henry D, Jüni P, Kirkham J, Konstantinidis M, Lasserson T,
Loke Y, McGuinness L, Page M, Savović J, Shea B, Mawdsley D, Shepperd S,
Tugwell P, Valentine J, Viswanathan M, Waddington HS, Wells G, Hernán M, Higgins J.

V2 is published explicitly for **follow-up (cohort) studies**. The 6-domain
structure removes V1's "Bias due to deviations from intended intervention"
domain; protocol-deviation issues are folded into Domain 1 Variant B (time-
varying confounding). Other non-randomized designs (Case-Control, Case-
Crossover, Cross-Sectional) still dispatch to this tool from the registry
in :mod:`backend.quality_appraisal` — V2 is applied as the best-available
approximation; a methodologically pure assessment for those designs would
require a different tool (V1 ROBINS-I, or a design-specific tool).

The 6 domains:

  D1  Risk of bias due to confounding (two variants — see below)
  D2  Risk of bias in classification of interventions
  D3  Risk of bias in selection of participants into the study (or analysis)
  D4  Risk of bias due to missing data
  D5  Risk of bias arising from measurement of the outcome
  D6  Risk of bias in selection of the reported result

**Preflight** — one LLM call answers four preliminary considerations:
  B1  Did the authors attempt to control for confounding?
  B2  (If N/PN to B1) Is there sufficient potential for confounding that an
      unadjusted result should not be considered further?
  B3  Was the method of measuring the outcome inappropriate?
  C4  Did the analysis account for switches / protocol deviations during
      follow-up? (No → ITT effect → Variant A; Yes → per-protocol → Variant B)

If B2=Y/PY or B3=Y/PY, the result is **Critical** with no further assessment
(saves 6 domain LLM calls). Otherwise C4 dispatches Domain 1 to Variant A
(4 signaling questions, baseline confounding only) or Variant B (5 signaling
questions, baseline + time-varying confounding).

**Signal vocabulary:**
  Y / PY / PN / N / NI   — universal (yes / probably yes / probably no / no /
                            no information)
  WN / SN                 — weak no / strong no (some confounding + missing-
                            data questions)
  WY / SY                 — weak yes / strong yes (some misclassification +
                            measurement questions)

Different questions accept different response-option subsets — declared per
signal entry. Y/PY answers for low-RoB-marker questions point toward Low risk
of bias; the algorithms map signal answers (including the weak/strong tokens)
to a 4-level domain judgement.

**Domain judgement scale (4-level):**
  Low / Moderate / Serious / Critical

V1's separate "No information" judgement is gone in V2 — NI is still a valid
signal answer, but the algorithms route NI through the decision trees rather
than producing a distinct "No information" judgement. For Domain 1, "Low" is
labelled "Low (except for concerns about uncontrolled confounding)" per the
cribsheet footnote on page 4 — confounding cannot be eliminated in an
observational study, so the best achievable confidence is "Low except…".

**Overall:** worst-domain aggregation. Critical > Serious > Moderate > Low.
"""

from __future__ import annotations

import inspect
import json
import logging
from typing import Any, Callable

from ..annotator import _call_with_pdf

logger = logging.getLogger("rubricgen")


# ─────────────────────────────────────────────
# Signal vocabulary + judgement scale
# ─────────────────────────────────────────────
SIGNAL_OPTIONS_ALL = ("Y", "PY", "PN", "N", "NI", "WN", "SN", "WY", "SY")
"""All legal signal tokens across V2. Per-question subsets are declared on
each signal entry."""

JUDGEMENTS = ("Low", "Moderate", "Serious", "Critical")
"""Domain-level judgements. Note Domain 1 substitutes
'Low (except for concerns about uncontrolled confounding)' for plain Low —
that substitution happens after the tree returns."""

LOW_D1 = "Low (except for concerns about uncontrolled confounding)"


def _yes(ans: str) -> bool:
    """Truthy: any 'yes-flavoured' answer including weak / strong yes."""
    return ans in ("Y", "PY", "WY", "SY")


def _no(ans: str) -> bool:
    """Truthy: any 'no-flavoured' answer including weak / strong no."""
    return ans in ("N", "PN", "WN", "SN")


def _strict_yes(ans: str) -> bool:
    return ans in ("Y", "PY")


def _strict_no(ans: str) -> bool:
    return ans in ("N", "PN")


def _weak_no(ans: str) -> bool:
    return ans == "WN"


def _strong_no(ans: str) -> bool:
    return ans == "SN"


def _weak_yes(ans: str) -> bool:
    return ans == "WY"


def _strong_yes(ans: str) -> bool:
    return ans == "SY"


def _no_info(ans: str) -> bool:
    return ans == "NI"


# ─────────────────────────────────────────────
# Decision trees — pure Python (no LLM)
# ─────────────────────────────────────────────
# Each tree is a direct translation of the algorithm diagrams in the ROBINS-I
# V2 cribsheet (20 November 2025). Page references point to the source PDF.
# Keeping the trees in code (not prompts) lets reviewers see the exact
# scoring logic via inspect.getsource — surfaced in the developer view.


def domain1_variant_a_judge(signals: dict[str, str]) -> str:
    """D1 Variant A (ITT effect, baseline confounding only). Cribsheet p20.

    Signaling questions:
      1A.1  Controlled for all important confounding factors?
      1A.2  Confounding factors measured validly and reliably?
      1A.3  Controlled for any post-intervention variables?
      1A.4  Negative controls suggest serious uncontrolled confounding?
    """
    q1 = signals.get("1A.1", "NI")
    q2 = signals.get("1A.2", "NI")
    q3 = signals.get("1A.3", "NI")
    q4 = signals.get("1A.4", "NI")

    # 1.1 SN or NI: nothing further to redeem the result
    if _strong_no(q1) or _no_info(q1):
        return "Critical" if _yes(q4) else "Serious"

    # 1.1 Y/PY: well-controlled
    if _strict_yes(q1):
        if _yes(q3):  # over-adjusted for post-intervention vars → causal-pathway bias
            if _yes(q4):
                return "Critical"
            # 1.4 No: severity depends on 1.2 validity
            if _strict_yes(q2):
                return "Serious"
            return "Critical"
        # 1.3 N/PN/NI: no over-adjustment
        if _strict_yes(q2) or _weak_no(q2):
            return "Serious" if _yes(q4) else LOW_D1
        # 1.2 SN/NI: validity concerns plus possibly uncontrolled confounding
        return "Serious"

    # 1.1 WN: most-but-not-all controlled (floor is Moderate, not Low)
    if _weak_no(q1):
        if _yes(q3):
            if _yes(q4):
                return "Critical"
            if _strict_yes(q2):
                return "Serious"
            return "Critical"
        # 1.3 N/PN/NI
        if _strict_yes(q2) or _weak_no(q2):
            return "Serious" if _yes(q4) else "Moderate"
        return "Serious"

    # Fallthrough — shouldn't be reachable with a valid signal token
    return "Serious"


def domain1_variant_b_judge(signals: dict[str, str]) -> str:
    """D1 Variant B (per-protocol effect, baseline + time-varying). Cribsheet p24.

    Signaling questions:
      1B.1  Appropriate analysis method for time-varying confounding?
      1B.2  Controlled for all important baseline + time-varying factors?
      1B.3  Confounding factors measured validly and reliably?
      1B.4  Controlled for variables measured after intervention start?
      1B.5  Negative controls suggest serious uncontrolled confounding?
    """
    q1 = signals.get("1B.1", "NI")
    q2 = signals.get("1B.2", "NI")
    q3 = signals.get("1B.3", "NI")
    q4 = signals.get("1B.4", "NI")
    q5 = signals.get("1B.5", "NI")

    # 1.1 N/PN/NI: wrong analysis method (e.g. plain regression with time-varying
    #               confounders) — bias risk dominated by 1.4 and 1.5
    if _strict_no(q1) or _no_info(q1):
        if _yes(q4):
            return "Critical"
        return "Critical" if _yes(q5) else "Serious"

    # 1.1 Y/PY: appropriate g-methods etc. used
    if _strict_yes(q1):
        # 1.2: all important factors controlled?
        if _strict_yes(q2):
            # 1.3: validity of measurement
            if _strict_yes(q3) or _weak_no(q3):
                return "Serious" if _yes(q5) else LOW_D1
            return "Serious"  # SN / NI on 1.3
        if _weak_no(q2):
            if _strict_yes(q3) or _weak_no(q3):
                return "Serious" if _yes(q5) else "Moderate"
            return "Serious"
        # 1.2 SN/NI: not all important factors controlled
        return "Critical" if _yes(q5) else "Serious"

    return "Serious"


def domain2_judge(signals: dict[str, str]) -> str:
    """D2 Bias in classification of interventions. Cribsheet p28.

    Signaling questions:
      2.1  Intervention strategies distinguishable at start of follow-up?
      2.2  Did almost all outcome events occur after strategies became
           distinguishable?
      2.3  Did the analysis avoid problems from non-distinguishable strategies?
      2.4  Classification of intervention status influenced by knowledge of
           outcome / risk of outcome? (differential misclassification)
      2.5  Further classification errors (non-differential) likely?
    """
    q1 = signals.get("2.1", "NI")
    q2 = signals.get("2.2", "NI")
    q3 = signals.get("2.3", "NI")
    q4 = signals.get("2.4", "NI")
    q5 = signals.get("2.5", "NI")

    # Upstream tier: 2.1 → 2.2 → 2.3 cascade decides which matrix row to use
    if _yes(q1) or _yes(q2):
        tier = 0  # best — top matrix
    elif _strong_yes(q3):
        tier = 1  # middle matrix (per cribsheet: SY routes to middle 2.4)
    elif _weak_yes(q3) or _no_info(q3):
        tier = 1
    else:
        tier = 2  # worst — 2.3 N/PN: analysis did not address non-distinguishable strategies

    # 2.4 differential misclassification bump
    if _strict_no(q4):
        bump4 = 0
    elif _weak_yes(q4) or _no_info(q4):
        bump4 = 1
    elif _strong_yes(q4):
        bump4 = 2
    else:
        bump4 = 1  # defensive fallback

    # 2.5 non-differential misclassification bump
    if _strict_no(q5):
        bump5 = 0
    else:  # Y / PY / NI all bump
        bump5 = 1

    # Tier 2 has a direct CRITICAL for 2.4 SY/WY/NI per the diagram
    if tier == 2 and (_yes(q4) or _no_info(q4)):
        return "Critical"

    idx = min(tier + bump4 + bump5, 3)
    return JUDGEMENTS[idx]


def domain3_judge(signals: dict[str, str]) -> str:
    """D3 Bias in selection of participants. Cribsheet p32.

    Signaling questions:
      A. Prevalent-user bias and immortal time
        3.1  Follow-up began at start of intervention strategies?
        3.2  (If Y/PY to 3.1) Were outcome events after intervention start
             excluded from the analysis?
      B. Other selection bias
        3.3  Selection based on participant characteristics observed AFTER
             intervention start?
        3.4  (If Y/PY to 3.3) Were post-intervention variables that influenced
             selection associated with intervention?
        3.5  (If Y/PY to 3.4) Were those variables influenced by outcome
             (or cause of outcome)?
      C. Analysis / sensitivity / severity (if A or B raises concerns)
        3.6  Did the analysis correct for selection biases identified above?
        3.7  Did sensitivity analyses demonstrate minimal impact?
        3.8  Were selection biases severe enough to exclude from synthesis?
    """
    q1 = signals.get("3.1", "NI")
    q2 = signals.get("3.2", "NI")
    q3 = signals.get("3.3", "NI")
    q4 = signals.get("3.4", "NI")
    q5 = signals.get("3.5", "NI")
    q6 = signals.get("3.6", "NI")
    q7 = signals.get("3.7", "NI")
    q8 = signals.get("3.8", "NI")

    # Subsection A: prevalent-user bias and immortal time
    if _strict_yes(q1):
        a_judgement = "Low" if _strict_no(q2) or _no_info(q2) else "Moderate"
    elif _weak_no(q1) or _no_info(q1):
        a_judgement = "Moderate"
    elif _strong_no(q1):
        a_judgement = "Serious"
    else:
        a_judgement = "Moderate"

    # Subsection B: other selection bias
    if _strict_no(q3):
        b_judgement = "Low"
    elif _yes(q3):
        if _strict_no(q4) or _no_info(q4):
            b_judgement = "Low"
        elif _yes(q4):
            if _yes(q5):
                b_judgement = "Serious"
            else:  # N/PN/NI to 3.5
                b_judgement = "Moderate"
        else:
            b_judgement = "Moderate"
    else:  # NI to 3.3
        b_judgement = "Moderate"

    # Combine A and B per the across-tier matrix (cribsheet p32)
    rank = {"Low": 0, "Moderate": 1, "Serious": 2, "Critical": 3}
    worst = max(rank[a_judgement], rank[b_judgement])

    if worst == 0:
        return "Low"
    if worst == 1:
        return "Moderate"

    # Subsection C only applies when A or B is Serious
    # 3.6: analysis corrected for biases?
    if _yes(q6):
        return "Moderate"
    # 3.7: sensitivity analyses minimal impact?
    if _yes(q7):
        return "Moderate"
    # 3.8: biases severe enough to exclude from synthesis?
    if _yes(q8):
        return "Critical"
    return "Serious"


def domain4_judge(signals: dict[str, str]) -> str:
    """D4 Bias due to missing data. Cribsheet p38.

    Signaling questions:
      4.1  Complete data on intervention status for all/nearly all?
      4.2  Complete data on outcome for all/nearly all?
      4.3  Complete data on confounders for all/nearly all?
      4.4  Is the result based on a complete case analysis?
      4.5  (If complete case) Was exclusion related to true outcome value?
      4.6  (If yes to 4.5) Is outcome-missingness relationship explained by
           variables in the analysis model?
      4.7  Was the analysis based on imputing missing values?
      4.8  (If imputed) Is MAR/MCAR reasonable?
      4.9  (If 4.8 reasonable) Was imputation performed appropriately?
      4.10 (If neither complete case nor imputed) Was an alternative
           appropriate method used?
      4.11 (If 4.5 raises concerns, or 4.9/4.10 weak) Is there evidence the
           result was not biased?
    """
    q1 = signals.get("4.1", "NI")
    q2 = signals.get("4.2", "NI")
    q3 = signals.get("4.3", "NI")
    q4 = signals.get("4.4", "NI")
    q5 = signals.get("4.5", "NI")
    q6 = signals.get("4.6", "NI")
    q7 = signals.get("4.7", "NI")
    q8 = signals.get("4.8", "NI")
    q9 = signals.get("4.9", "NI")
    q10 = signals.get("4.10", "NI")
    q11 = signals.get("4.11", "NI")

    # Best case: complete data on all three variables → Low directly
    if _strict_yes(q1) and _strict_yes(q2) and _strict_yes(q3):
        return "Low"

    # Complete case analysis path
    if _strict_yes(q4) or _no_info(q4):
        # 4.5: was exclusion related to outcome?
        if _strict_no(q5):  # N / PN: exclusion not related → Low
            return "Low"
        # Y / PY / NI to 4.5: concerning exclusion
        # 4.6: outcome-missingness explained by analysis model?
        if _strict_yes(q6):
            # Bias is plausibly addressed in the model; check 4.11
            if _strict_yes(q11):
                return "Moderate"
            return "Serious"
        if _weak_no(q6) or _no_info(q6):
            # Some concerns about whether the model captures the relationship
            if _strict_yes(q11):
                return "Moderate"
            return "Serious"
        # SN: bias likely substantial
        return "Critical" if _strict_no(q11) else "Serious"

    # Imputation path
    if _strict_yes(q7):
        if _strict_yes(q8):
            # 4.9: imputation appropriate?
            if _strict_yes(q9):
                return "Low"
            if _weak_no(q9) or _no_info(q9):
                return "Moderate" if _strict_yes(q11) else "Serious"
            # SN
            return "Critical" if _strict_no(q11) else "Serious"
        # 4.8 N/PN/NI: MAR/MCAR not reasonable → bias likely
        return "Critical" if _strict_no(q11) else "Serious"

    # Alternative method path (neither complete case nor imputation)
    if _strict_yes(q10):
        return "Low"
    if _weak_no(q10) or _no_info(q10):
        return "Moderate" if _strict_yes(q11) else "Serious"
    # SN to 4.10
    return "Critical" if _strict_no(q11) else "Serious"


def domain5_judge(signals: dict[str, str]) -> str:
    """D5 Bias arising from measurement of the outcome. Cribsheet p41.

    Signaling questions:
      5.1  Could measurement/ascertainment of outcome have differed between
           intervention groups?
      5.2  Were outcome assessors aware of intervention received?
      5.3  (If yes to 5.2) Could assessment have been influenced by knowledge
           of intervention?
    """
    q1 = signals.get("5.1", "NI")
    q2 = signals.get("5.2", "NI")
    q3 = signals.get("5.3", "NI")

    # 5.1 Y/PY: differential measurement → Serious directly
    if _yes(q1):
        return "Serious"

    # 5.1 N/PN: comparable measurement methods
    if _strict_no(q1):
        if _strict_no(q2):
            return "Low"
        # 5.2 Y/PY/NI: assessors knew → 5.3 (impact of knowledge)
        if _strong_yes(q3):
            return "Serious"
        if _weak_yes(q3) or _no_info(q3):
            return "Moderate"
        # PN / N on 5.3: knowledge unlikely to influence assessment
        return "Low"

    # 5.1 NI: unclear whether measurement differed
    if _strict_no(q2):
        return "Moderate"
    if _strong_yes(q3):
        return "Serious"
    return "Moderate"


def domain6_judge(signals: dict[str, str]) -> str:
    """D6 Bias in selection of the reported result. Cribsheet p47.

    Signaling questions:
      6.1  Result reported in accordance with pre-determined analysis plan?
      6.2  Result selected from multiple outcome measurements?
      6.3  Result selected from multiple analyses?
      6.4  Result selected from multiple subgroups?
    """
    q1 = signals.get("6.1", "NI")
    q2 = signals.get("6.2", "NI")
    q3 = signals.get("6.3", "NI")
    q4 = signals.get("6.4", "NI")

    if _strict_yes(q1):
        return "Low"

    yes_count = sum(1 for q in (q2, q3, q4) if _yes(q))
    ni_count = sum(1 for q in (q2, q3, q4) if _no_info(q))

    if yes_count >= 2:
        return "Critical"
    if yes_count == 1:
        return "Serious"
    # No Y/PY among 6.2-6.4
    if ni_count == 3:
        return "Serious"
    if ni_count >= 1:
        return "Moderate"
    return "Low"


DOMAIN_JUDGES_VARIANT_A: dict[int, Callable[[dict[str, str]], str]] = {
    1: domain1_variant_a_judge,
    2: domain2_judge,
    3: domain3_judge,
    4: domain4_judge,
    5: domain5_judge,
    6: domain6_judge,
}

DOMAIN_JUDGES_VARIANT_B: dict[int, Callable[[dict[str, str]], str]] = {
    1: domain1_variant_b_judge,
    2: domain2_judge,
    3: domain3_judge,
    4: domain4_judge,
    5: domain5_judge,
    6: domain6_judge,
}


def robins_i_overall(domain_judgements: list[str]) -> str:
    """Overall judgement — worst-domain aggregation per cribsheet p48.

    The user may override upward when multiple Serious domains compound, but
    this code returns the algorithm default. Domain 1's special
    "Low (except for concerns about uncontrolled confounding)" is treated as
    Low for aggregation purposes.
    """
    rank = {LOW_D1: 0, "Low": 0, "Moderate": 1, "Serious": 2, "Critical": 3}
    worst = max((rank.get(j, 1) for j in domain_judgements), default=0)
    if worst == 0:
        return "Low"
    return JUDGEMENTS[worst]


# ─────────────────────────────────────────────
# Per-question response option subsets
# ─────────────────────────────────────────────
# Each signaling question declares the legal answer tokens for that question.
# Tokens are: Y / PY / PN / N / NI / WN / SN / WY / SY (subsets vary).
_BASIC = ("Y", "PY", "PN", "N", "NI")
_BASIC_NA = ("NA", "Y", "PY", "PN", "N", "NI")
_WITH_WN_SN = ("Y", "PY", "WN", "SN", "NI")
_NA_WITH_WN_SN = ("NA", "Y", "PY", "WN", "SN", "NI")
_WITH_WY_SY = ("Y", "PY", "WY", "SY", "PN", "N", "NI")
_DIFFERENTIAL = ("SY", "WY", "PN", "N", "NI")
_NA_DIFFERENTIAL = ("NA", "SY", "WY", "PN", "N", "NI")


# ─────────────────────────────────────────────
# Signal definitions — verbatim text from the cribsheet
# ─────────────────────────────────────────────
DOMAIN1_VARIANT_A_SIGNALS: list[dict[str, Any]] = [
    {
        "id": "1A.1",
        "text": "Did the authors control for all the important confounding factors for which this was necessary?",
        "options": list(_WITH_WN_SN),
        "elaboration": (
            "Answer Y/PY if all important confounding factors identified in the "
            "preliminary consideration were appropriately controlled for "
            "(stratification, regression, matching, standardization, propensity "
            "scores, IPTW). Answer WN if most were controlled and uncontrolled "
            "confounding was probably not substantial. Answer SN if at least one "
            "important confounder should have been controlled but was not, and "
            "the failure is likely to have a material impact."
        ),
    },
    {
        "id": "1A.2",
        "text": "Were confounding factors that were controlled for (and for which control was necessary) measured validly and reliably by the variables available in this study?",
        "options": list(_NA_WITH_WN_SN),
        "elaboration": (
            "Adjustment helps only if confounders were measured well. Answer "
            "WN if measurement error was probably not substantial; SN if there "
            "was at least one important confounder measured poorly enough that "
            "the extent of measurement error in confounders was probably "
            "substantial."
        ),
    },
    {
        "id": "1A.3",
        "text": "Did the authors control for any post-intervention variables that could have been affected by the intervention?",
        "options": list(_BASIC_NA),
        "elaboration": (
            "Controlling for variables on the causal pathway between intervention "
            "and outcome (over-adjustment) biases the effect estimate. Classic "
            "example: adjusting for a biomarker that the intervention changes."
        ),
    },
    {
        "id": "1A.4",
        "text": "Did the use of negative controls, quantitative bias analysis, or other considerations suggest serious uncontrolled confounding?",
        "options": list(_BASIC[:4]),  # Y / PY / PN / N
        "elaboration": (
            "If the study did not use negative controls and no other considerations "
            "suggest uncontrolled confounding, answer N. Answer Y/PY if negative "
            "controls indicate the result being assessed suffers from material "
            "bias due to confounding."
        ),
    },
]

DOMAIN1_VARIANT_B_SIGNALS: list[dict[str, Any]] = [
    {
        "id": "1B.1",
        "text": "Did the authors use an analysis method that was appropriate to control for time-varying as well as baseline confounding?",
        "options": list(_BASIC),
        "elaboration": (
            "Appropriate methods to control for time-varying confounding "
            "('g-methods') include inverse probability weighting based on "
            "baseline- and time-varying confounding factors, with adjustment "
            "for the censoring weights. Standard regression models including "
            "time-varying confounders may be problematic when those "
            "confounders are affected by prior intervention (treatment-"
            "confounder feedback)."
        ),
    },
    {
        "id": "1B.2",
        "text": "Did the authors control for all the important baseline and time-varying confounding factors for which this was necessary?",
        "options": list(_NA_WITH_WN_SN),
        "elaboration": (
            "Per-protocol analyses must control for both baseline and time-"
            "varying confounding factors that predict changes to intervention "
            "received. Same WN / SN semantics as Variant A 1.1."
        ),
    },
    {
        "id": "1B.3",
        "text": "Were confounding factors that were controlled for measured validly and reliably by the variables available in this study?",
        "options": list(_NA_WITH_WN_SN),
        "elaboration": (
            "Same measurement-validity question as Variant A 1.2 but applied "
            "to baseline + time-varying confounders."
        ),
    },
    {
        "id": "1B.4",
        "text": "Did the authors control for time-varying factors or other variables measured after the start of intervention?",
        "options": list(_BASIC_NA),
        "elaboration": (
            "Asked when an inappropriate analysis method (1B.1 N/PN/NI) has "
            "been used. Conditioning on time-varying factors measured after "
            "the start of intervention is likely to lead to bias when those "
            "factors are also on the causal pathway from intervention to "
            "outcome."
        ),
    },
    {
        "id": "1B.5",
        "text": "Did the use of negative controls, or other considerations, suggest serious uncontrolled confounding?",
        "options": list(_BASIC[:4]),
        "elaboration": "Same as Variant A 1.4.",
    },
]

DOMAIN2_SIGNALS: list[dict[str, Any]] = [
    {
        "id": "2.1",
        "text": "Were the intervention strategies distinguishable at the time when follow-up would have started in the target trial?",
        "options": list(_BASIC),
        "elaboration": (
            "In most non-randomized studies, participants are classified to "
            "intervention strategies based on information about interventions "
            "prescribed or received. Some strategies (e.g. 'surgery within 6 "
            "months of diagnosis' vs 'delay surgery until clinical progression') "
            "cannot be distinguished at follow-up start, creating a period of "
            "'immortal time' during which the outcome cannot occur for some "
            "groups."
        ),
    },
    {
        "id": "2.2",
        "text": "Did all or nearly all outcome events occur after the intervention and comparator strategies could be distinguished?",
        "options": list(_BASIC_NA),
        "elaboration": (
            "Asked only if 2.1 was N/PN/NI. If the indistinguishable period is "
            "short relative to total follow-up, the proportion of outcome "
            "events during that period may be low and the misclassification "
            "bias correspondingly small."
        ),
    },
    {
        "id": "2.3",
        "text": "Did the analysis avoid problems arising from intervention strategies that are not distinguishable at the start of follow-up?",
        "options": list(_NA_DIFFERENTIAL),
        "elaboration": (
            "Answer SY (strong yes, fully) if predictors of treatment during "
            "follow-up were measured and used appropriately to derive inverse-"
            "probability weights (e.g. clone-censor-weighting, g-formula), or "
            "if the study used a 'landmark' analysis. WY (partially) if "
            "appropriate but unlikely to have fully adjusted for prognostic "
            "factors predicting treatment after start of follow-up."
        ),
    },
    {
        "id": "2.4",
        "text": "Was classification of intervention status influenced by knowledge of the outcome or risk of the outcome?",
        "options": list(_DIFFERENTIAL),
        "elaboration": (
            "Differential misclassification arises when the outcome (or its "
            "causes, other than the intervention) influences how interventions "
            "are classified. SY = yes, and the impact was substantial; WY = "
            "yes, but the impact was not substantial."
        ),
    },
    {
        "id": "2.5",
        "text": "Were further classification errors (not influenced by knowledge of the outcome or risk of the outcome) likely?",
        "options": list(_BASIC),
        "elaboration": (
            "Non-differential misclassification — receipt of intervention not "
            "recorded for some participants. Usually biases towards the null. "
            "'Nearly all' should be interpreted as 'enough to be confident of "
            "the findings'."
        ),
    },
]

DOMAIN3_SIGNALS: list[dict[str, Any]] = [
    {
        "id": "3.1",
        "text": "Did follow-up in the analysis begin at the start of the intervention strategies being compared?",
        "options": list(_WITH_WN_SN),
        "elaboration": (
            "A. Prevalent-user bias and immortal time. Answer Y/PY if all "
            "outcome events and follow-up time after the start of the "
            "interventions were included in the analysis. WN if not "
            "substantial; SN if leading to a substantial risk of bias."
        ),
    },
    {
        "id": "3.2",
        "text": "Were outcome events during a period of follow-up after the start of the interventions excluded from the analysis?",
        "options": list(_BASIC),
        "elaboration": (
            "Only asked if 3.1 was Y/PY. Such exclusion creates 'immortal time' "
            "during which events cannot occur and biases the effect estimate."
        ),
    },
    {
        "id": "3.3",
        "text": "Was selection of participants into the study (or into the analysis) based on participant characteristics observed after the start of intervention, additional to the situations addressed in 3.1 and 3.2?",
        "options": list(_BASIC),
        "elaboration": (
            "B. Other selection bias. Answer Y/PY if selection into the study "
            "was based on post-intervention characteristics. N/PN if selection "
            "was based only on pre-intervention characteristics — baseline "
            "confounding is addressed in Domain 1, not here."
        ),
    },
    {
        "id": "3.4",
        "text": "Were the post-intervention variables that influenced selection likely to be associated with intervention?",
        "options": list(_BASIC_NA),
        "elaboration": (
            "Only asked if 3.3 was Y/PY. Selection bias occurs when selection "
            "is related to an effect of either intervention or a cause of "
            "intervention AND an effect of either the outcome or a cause of "
            "the outcome."
        ),
    },
    {
        "id": "3.5",
        "text": "Were the post-intervention variables that influenced selection likely to be influenced by the outcome or a cause of the outcome?",
        "options": list(_BASIC_NA),
        "elaboration": "Only asked if 3.4 was Y/PY. Collider-style selection bias.",
    },
    {
        "id": "3.6",
        "text": "Is it likely that the analysis corrected for all of the potential selection biases identified above?",
        "options": list(_BASIC_NA),
        "elaboration": (
            "C. Analysis / sensitivity / severity. Only asked if A or B raised "
            "concerns. Inverse probability weights can create a pseudo-"
            "population without the selection bias if assumptions are justified."
        ),
    },
    {
        "id": "3.7",
        "text": "Did sensitivity analyses demonstrate that the likely impact of the potential selection biases identified above was minimal?",
        "options": list(_BASIC_NA),
        "elaboration": "Only asked if 3.6 was N/PN/NI.",
    },
    {
        "id": "3.8",
        "text": "Were potential selection biases identified above sufficiently severe that the result should not be included in a quantitative synthesis?",
        "options": list(_BASIC_NA),
        "elaboration": (
            "Distinguishes 'Serious' from 'Critical' risk of selection bias. "
            "Answer N/PN/NI unless there is clear evidence that the selection "
            "biases identified were severe."
        ),
    },
]

DOMAIN4_SIGNALS: list[dict[str, Any]] = [
    {
        "id": "4.1",
        "text": "Were complete data on intervention status available for all, or nearly all, participants?",
        "options": list(_BASIC),
        "elaboration": (
            "'Nearly all' should be interpreted as the number excluded due to "
            "missing intervention data is so small it could not have made an "
            "important difference to the estimated effect. NI usually leads "
            "to a high risk-of-bias judgement."
        ),
    },
    {
        "id": "4.2",
        "text": "Were complete data on the outcome available for all, or nearly all, participants?",
        "options": list(_BASIC),
        "elaboration": (
            "For continuous outcomes, complete data for 95% (or 90%) is often "
            "sufficient. For dichotomous outcomes, the proportion required is "
            "directly linked to the risk of the outcome event."
        ),
    },
    {
        "id": "4.3",
        "text": "Were complete data on important confounding variables available for all, or nearly all, participants?",
        "options": list(_BASIC),
        "elaboration": "Same 'nearly all' interpretation as 4.1 and 4.2.",
    },
    {
        "id": "4.4",
        "text": "Is the result based on a complete case analysis?",
        "options": list(_BASIC_NA),
        "elaboration": (
            "A complete case analysis is restricted to participants with "
            "complete data on all of the intervention, outcome and confounding "
            "variables."
        ),
    },
    {
        "id": "4.5",
        "text": "Was exclusion from the analysis because of missing data (in intervention, confounders or the outcome) likely to be related to the true value of the outcome?",
        "options": list(_BASIC_NA),
        "elaboration": (
            "Y/PY if e.g. (1) differences between intervention groups in "
            "proportions excluded; (2) reported reasons indicate missingness "
            "depends on the true outcome; (3) the outcome's nature makes "
            "missingness likely (severe depression participants missing "
            "appointments)."
        ),
    },
    {
        "id": "4.6",
        "text": "Is the relationship between the outcome and missingness likely to be explained by the variables in the analysis model?",
        "options": list(_NA_WITH_WN_SN),
        "elaboration": (
            "If all variables that plausibly explain the outcome-missingness "
            "relationship are included in the complete-case analysis, bias "
            "due to missing data will be low. WN if not substantial; SN if "
            "bias is likely substantial."
        ),
    },
    {
        "id": "4.7",
        "text": "Was the analysis based on imputing missing values?",
        "options": list(_BASIC_NA),
        "elaboration": "Y/PY if the analysis used either single or multiple imputation.",
    },
    {
        "id": "4.8",
        "text": "Is it reasonable to assume data were 'missing at random' (MAR) or 'missing completely at random' (MCAR)?",
        "options": list(_BASIC_NA),
        "elaboration": (
            "Multiple imputation avoids bias provided incomplete variables "
            "are MAR or MCAR but not if MNAR (missing not at random). N/PN "
            "if there is reason to believe data are MNAR."
        ),
    },
    {
        "id": "4.9",
        "text": "Was imputation performed appropriately?",
        "options": list(_NA_WITH_WN_SN),
        "elaboration": (
            "WN / SN if simple methods (LOCF, mean imputation) were used; "
            "Y/PY if multiple imputation included all predictors of "
            "missingness and all variables in the main analysis model."
        ),
    },
    {
        "id": "4.10",
        "text": "Was an appropriate alternative method used to correct for bias due to missing data?",
        "options": list(_NA_WITH_WN_SN),
        "elaboration": (
            "Asked when the analysis was neither a complete case analysis "
            "nor based on imputation. Examples include inverse probability "
            "weighting and full information maximum likelihood."
        ),
    },
    {
        "id": "4.11",
        "text": "Is there evidence that the result was not biased by missing data?",
        "options": list(_BASIC_NA),
        "elaboration": (
            "Evidence may come from (1) analysis methods that would not be "
            "biased under plausible assumptions about missingness, or "
            "(2) sensitivity analyses showing results change little under "
            "plausible assumptions."
        ),
    },
]

DOMAIN5_SIGNALS: list[dict[str, Any]] = [
    {
        "id": "5.1",
        "text": "Could measurement or ascertainment of the outcome have differed between intervention groups?",
        "options": list(_BASIC),
        "elaboration": (
            "Comparable methods involve the same measurement methods and "
            "thresholds, used at comparable time points. Differences can arise "
            "through 'diagnostic detection bias' or extra visits for "
            "intervention participants."
        ),
    },
    {
        "id": "5.2",
        "text": "Were outcome assessors aware of the intervention received by study participants?",
        "options": list(_BASIC),
        "elaboration": (
            "N if outcome assessors were blinded, or if participants self-"
            "report and were themselves blinded. In observational studies, the "
            "answer will usually be Y when participants report their outcomes "
            "themselves."
        ),
    },
    {
        "id": "5.3",
        "text": "Could assessment of the outcome have been influenced by knowledge of the intervention received?",
        "options": list(_NA_DIFFERENTIAL),
        "elaboration": (
            "Only asked if 5.2 was Y/PY/NI. SY (yes, to a large extent) for "
            "patient-reported symptoms in homeopathy studies, or assessments "
            "of recovery by physiotherapists. WY (yes, to a small extent) when "
            "knowledge could have influenced assessment but no strong reason "
            "to believe it did."
        ),
    },
]

DOMAIN6_SIGNALS: list[dict[str, Any]] = [
    {
        "id": "6.1",
        "text": "Was the result reported in accordance with an available, pre-determined analysis plan?",
        "options": list(_BASIC),
        "elaboration": (
            "Analysis plans are rarely publicly available for non-randomized "
            "studies, so most papers will not be assessed as Low risk of bias "
            "for this domain on the basis of 6.1 alone."
        ),
    },
    {
        "id": "6.2",
        "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple outcome measurements within the outcome domain?",
        "options": list(_BASIC),
        "elaboration": (
            "Pain may be measured via VAS, McGill Pain Questionnaire, etc, at "
            "multiple time points. If only the most favourable is reported "
            "without justification, answer Y/PY."
        ),
    },
    {
        "id": "6.3",
        "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple analyses of the data?",
        "options": list(_BASIC),
        "elaboration": (
            "Multiple analytic choices (unadjusted vs adjusted, alternative "
            "covariate sets, missing-data strategies) generate multiple "
            "estimates. Selection on favourable results is concerning."
        ),
    },
    {
        "id": "6.4",
        "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple subgroups?",
        "options": list(_BASIC),
        "elaboration": (
            "Particularly with large cohorts from routine data, multiple "
            "subgroup estimates can be generated. Selection of the most "
            "interesting subgroup result is selective reporting."
        ),
    },
]


# ─────────────────────────────────────────────
# Canonical DOMAINS list for the orchestrator + flattener
# ─────────────────────────────────────────────
# Domain 1 carries the union of Variant A + Variant B signals so CSV/XLSX
# exports get columns for every possible question. Per-paper, only the chosen
# variant's signals are populated.
DOMAINS: list[dict[str, Any]] = [
    {
        "id": 1,
        "name": "Bias due to confounding",
        "variants": ["A", "B"],
        "variant_signals": {
            "A": DOMAIN1_VARIANT_A_SIGNALS,
            "B": DOMAIN1_VARIANT_B_SIGNALS,
        },
        "signals": DOMAIN1_VARIANT_A_SIGNALS + DOMAIN1_VARIANT_B_SIGNALS,
        "relevant_fields": [
            "confounders_measured", "adjustment_method", "exposure_definition",
            "comparator_group", "immortal_time_bias", "confounding_control",
        ],
    },
    {
        "id": 2,
        "name": "Bias in classification of interventions",
        "signals": DOMAIN2_SIGNALS,
        "relevant_fields": [
            "exposure_definition", "exposure_measurement",
            "exposure_ascertainment", "intervention_classification",
        ],
    },
    {
        "id": 3,
        "name": "Bias in selection of participants into the study (or analysis)",
        "signals": DOMAIN3_SIGNALS,
        "relevant_fields": [
            "case_source", "control_selection", "sampling_method",
            "loss_to_follow_up", "immortal_time_bias",
        ],
    },
    {
        "id": 4,
        "name": "Bias due to missing data",
        "signals": DOMAIN4_SIGNALS,
        "relevant_fields": [
            "loss_to_follow_up", "missing_data_handling", "attrition_rate",
        ],
    },
    {
        "id": 5,
        "name": "Bias arising from measurement of the outcome",
        "signals": DOMAIN5_SIGNALS,
        "relevant_fields": ["outcome_ascertainment", "outcome_definition"],
    },
    {
        "id": 6,
        "name": "Bias in selection of the reported result",
        "signals": DOMAIN6_SIGNALS,
        "relevant_fields": ["outcome_definition", "statistical_analysis"],
    },
]


# ─────────────────────────────────────────────
# Preflight (B1 / B2 / B3 screening + C4 variant)
# ─────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing risk of bias in a "
    "non-randomized study of an intervention using the Cochrane ROBINS-I V2 "
    "tool (20 November 2025 cribsheet). Read the PDF carefully. Answer each "
    "signaling question with one of the allowed tokens for that question — "
    "Y (yes), PY (probably yes), PN (probably no), N (no), NI (no information), "
    "and where indicated WN (weak no), SN (strong no), WY (weak yes), "
    "SY (strong yes). Provide a 1-2 sentence rationale for each answer, "
    "quoting the paper where possible. Return ONLY a valid JSON object — no "
    "preamble, no markdown fences."
)


def _build_preflight_prompt(study_type: str,
                            primary_outcome: str,
                            extracted_fields: dict[str, str]) -> str:
    """Prompt for the combined B1/B2/B3 + C4 preflight pass."""
    relevant_keys = [
        "confounders_measured", "adjustment_method", "outcome_definition",
        "outcome_ascertainment", "analysis_framework", "primary_outcome_measurement",
    ]
    relevant = {k: extracted_fields[k] for k in relevant_keys
                if extracted_fields.get(k)}
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"

    return f"""You are performing the **Preliminary Considerations** screen of ROBINS-I V2 on a non-randomized study.

Study type: {study_type}
Outcome being assessed: {primary_outcome}

Context (fields already extracted from the paper):
{ctx_json}

Answer four preliminary-consideration questions:

**B1. Did the authors make any attempt to control for confounding in the result being assessed?**
Options: Y / PY / PN / N
Elaboration: Confounding is a substantial problem in most non-randomized studies. Answer Y/PY if the analysis includes multivariable adjustment, matching, stratification, propensity-score methods, or inverse probability weighting.

**B2. (Only if N/PN to B1) Is there sufficient potential for confounding that an unadjusted result should not be considered further?**
Options: Y / PY / PN / N
Elaboration: If there is sufficient potential for confounding that an unadjusted result should not be considered, the result is at Critical risk of bias.

**B3. Was the method of measuring the outcome inappropriate?**
Options: Y / PY / PN / N
Elaboration: Identify methods of outcome measurement unsuitable for the outcome they evaluate. Answer Y/PY if (1) important outcome values fall outside levels detectable by the method; (2) the instrument has demonstrated poor reliability/validity; or (3) measurement differed substantially between intervention and comparator groups so that group differences are not interpretable. In most circumstances answer N/PN.

**C4. Did the analysis account for switches during follow-up between the intervention strategies being compared, or for other protocol deviations during follow-up?**
Options: No (the analysis is estimating the intention-to-treat effect — Variant A) / Yes (the analysis is estimating the per-protocol effect — Variant B)

Return JSON with exactly this shape:
{{
  "B1": "Y|PY|PN|N",
  "B1_rationale": "1-2 sentences quoting the paper",
  "B2": "Y|PY|PN|N|NA",
  "B2_rationale": "1-2 sentences (or 'NA' if B1 was Y/PY)",
  "B3": "Y|PY|PN|N",
  "B3_rationale": "1-2 sentences quoting the paper",
  "C4": "No|Yes",
  "C4_rationale": "1-2 sentences explaining whether the analysis estimates ITT or per-protocol"
}}"""


def run_preflight(pdf_bytes: bytes,
                  study_type: str,
                  primary_outcome: str,
                  extracted_fields: dict[str, str]) -> dict[str, Any]:
    """Run the preflight LLM call. Returns the parsed answers + a decision.

    Returns a dict with::

        {
          "B1": "Y|PY|PN|N",
          "B2": "Y|PY|PN|N|NA",
          "B3": "Y|PY|PN|N",
          "C4": "No|Yes",
          "rationales": {"B1": ..., "B2": ..., "B3": ..., "C4": ...},
          "screening_decision": "proceed" | "critical",
          "screening_reason": str,
          "variant": "A" | "B",
        }
    """
    prompt = _build_preflight_prompt(study_type, primary_outcome, extracted_fields)
    raw = _call_with_pdf(pdf_bytes, prompt, max_tokens=2048)

    def _opt(key: str, default: str = "NI", allowed: tuple = ("Y", "PY", "PN", "N")) -> str:
        v = str(raw.get(key, default)).strip().upper()
        if v not in allowed:
            return default
        return v

    b1 = _opt("B1")
    b2 = _opt("B2", default="NA", allowed=("Y", "PY", "PN", "N", "NA"))
    b3 = _opt("B3")
    c4_raw = str(raw.get("C4", "No")).strip().lower()
    c4 = "Yes" if c4_raw.startswith("y") else "No"

    rationales = {
        "B1": str(raw.get("B1_rationale", "")).strip(),
        "B2": str(raw.get("B2_rationale", "")).strip(),
        "B3": str(raw.get("B3_rationale", "")).strip(),
        "C4": str(raw.get("C4_rationale", "")).strip(),
    }

    # Screening decision per cribsheet p9: B2 Y/PY or B3 Y/PY → Critical
    if b2 in ("Y", "PY"):
        return {
            "B1": b1, "B2": b2, "B3": b3, "C4": c4,
            "rationales": rationales,
            "screening_decision": "critical",
            "screening_reason": (
                "B2: Sufficient potential for confounding that the unadjusted "
                "result should not be considered further."
            ),
            "variant": "A" if c4 == "No" else "B",
        }
    if b3 in ("Y", "PY"):
        return {
            "B1": b1, "B2": b2, "B3": b3, "C4": c4,
            "rationales": rationales,
            "screening_decision": "critical",
            "screening_reason": (
                "B3: The method of measuring the outcome is inappropriate."
            ),
            "variant": "A" if c4 == "No" else "B",
        }

    return {
        "B1": b1, "B2": b2, "B3": b3, "C4": c4,
        "rationales": rationales,
        "screening_decision": "proceed",
        "screening_reason": "",
        "variant": "A" if c4 == "No" else "B",
    }


# ─────────────────────────────────────────────
# Per-domain prompt building + LLM orchestration
# ─────────────────────────────────────────────
def _signals_for_domain(domain: dict[str, Any], variant: str) -> list[dict[str, Any]]:
    """Return the active signal list — variant-specific for Domain 1."""
    if domain.get("variant_signals"):
        return domain["variant_signals"][variant]
    return domain["signals"]


def build_domain_prompt(domain: dict[str, Any],
                        variant: str,
                        study_type: str,
                        primary_outcome: str,
                        extracted_fields: dict[str, str],
                        target_pico: dict[str, str] | None = None) -> str:
    """Per-domain prompt builder. For D1, the signal list is variant-specific."""
    signals = _signals_for_domain(domain, variant)

    relevant = {k: extracted_fields[k]
                for k in domain.get("relevant_fields", []) if extracted_fields.get(k)}
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"

    pico_block = ""
    if target_pico:
        pico_block = "\nTarget PICO (user-supplied):\n" + json.dumps(target_pico, indent=2) + "\n"

    domain_header = f"Domain {domain['id']} — {domain['name']}"
    if domain["id"] == 1:
        domain_header += f" (Variant {variant})"

    q_lines = []
    for sig in signals:
        q_lines.append(
            f"\n**{sig['id']}. {sig['text']}**\n"
            f"Elaboration: {sig['elaboration']}\n"
            f"Response options: {'/'.join(sig['options'])}."
        )
    questions_block = "\n".join(q_lines)

    shape = "{\n"
    for sig in signals:
        opt_string = "|".join(sig["options"])
        shape += f'  "{sig["id"]}": "{opt_string}",\n'
        shape += f'  "{sig["id"]}_rationale": "1-2 sentences quoting the paper",\n'
    shape += '  "direction_of_bias": "NA|Favours intervention|Favours comparator|Towards null|Away from null|Unpredictable"\n'
    shape += "}"

    return f"""Assess **{domain_header}** for the study described in the attached PDF using the ROBINS-I V2 tool.

Study type: {study_type}
Outcome being assessed: {primary_outcome}
{pico_block}
Context (fields already extracted from the paper):
{ctx_json}

Signaling questions:
{questions_block}

Return a JSON object with exactly this shape:
{shape}

Notes on ROBINS-I V2:
- The judgement scale is **Low / Moderate / Serious / Critical** (4 levels). Code maps your signal answers to the judgement — answer the signaling questions only.
- Some questions allow **WN / SN** (weak / strong no) or **WY / SY** (weak / strong yes). Use the strong version only when the magnitude is clearly substantial; use the weak version when the direction is right but the magnitude is uncertain.
- Answer N (or PN) when the paper gives enough information to rule out the problem; NI only when the paper is silent.
- Rationales must be short (1-2 sentences) and quote the paper verbatim where possible."""


def _assess_domain(pdf_bytes: bytes,
                   domain: dict[str, Any],
                   variant: str,
                   study_type: str,
                   primary_outcome: str,
                   extracted_fields: dict[str, str],
                   target_pico: dict[str, str] | None = None,
                   ) -> dict[str, Any]:
    """LLM-assess one domain. Returns {signals, rationales, judgement, direction, variant?}."""
    prompt = build_domain_prompt(
        domain, variant, study_type, primary_outcome, extracted_fields, target_pico,
    )
    raw = _call_with_pdf(pdf_bytes, prompt, max_tokens=8192)

    signals_for_this = _signals_for_domain(domain, variant)
    signals: dict[str, str] = {}
    rationales: dict[str, str] = {}
    for sig in signals_for_this:
        sid = sig["id"]
        ans = str(raw.get(sid, "NI")).strip().upper()
        # Normalize "NA" tokens not in this question's allowed set → NI
        allowed = set(sig["options"])
        if ans not in allowed:
            logger.warning(
                "ROBINS-I V2 domain %s question %s: invalid answer %r (allowed %s) — defaulting to NI",
                domain["id"], sid, ans, sorted(allowed),
            )
            ans = "NI" if "NI" in allowed else next(iter(allowed))
        signals[sid] = ans
        rationales[sid] = str(raw.get(f"{sid}_rationale", "")).strip()

    judges = DOMAIN_JUDGES_VARIANT_A if variant == "A" else DOMAIN_JUDGES_VARIANT_B
    judgement = judges[domain["id"]](signals)
    direction = str(raw.get("direction_of_bias", "NA")).strip() or "NA"

    result: dict[str, Any] = {
        "signals": signals,
        "rationales": rationales,
        "judgement": judgement,
        "direction": direction,
    }
    if domain["id"] == 1:
        result["variant"] = variant
    return result


def run(pdf_bytes: bytes,
        extracted_fields: dict[str, str],
        classification: dict[str, str],
        primary_outcome: str,
        progress: Callable[[int], None] | None = None,
        target_pico: dict[str, str] | None = None,
        ) -> tuple[dict[str, Any], str, str]:
    """Run ROBINS-I V2 against a non-randomized study.

    Pipeline:
      1. Preflight (B1/B2/B3 + C4) — single LLM call.
      2. If B2=Y/PY or B3=Y/PY → return Critical immediately (skip domains).
      3. Otherwise per-domain assessments (Domain 1 dispatched by Variant).

    Returns ``(domain_results, overall_judgement, overall_direction)``.

    ``domain_results["preflight"]`` carries the B1/B2/B3/C4 answers +
    rationales + variant + screening decision. When a screening short-circuit
    fires, the per-domain entries are absent except for the Critical-routing
    metadata.
    """
    study_type = classification.get("study_type", "Cohort Study")

    # Stage 1 — preflight
    if progress:
        try:
            progress(0)
        except Exception:
            pass

    preflight = run_preflight(pdf_bytes, study_type, primary_outcome, extracted_fields)
    domain_results: dict[str, Any] = {"preflight": preflight}

    # Stage 2 — screening short-circuit
    if preflight["screening_decision"] == "critical":
        return domain_results, "Critical", "Unpredictable"

    # Stage 3 — six domains
    variant = preflight["variant"]
    for domain in DOMAINS:
        if progress:
            try:
                progress(domain["id"])
            except Exception:
                pass
        result = _assess_domain(
            pdf_bytes, domain, variant, study_type, primary_outcome,
            extracted_fields, target_pico,
        )
        result["id"] = domain["id"]
        result["name"] = domain["name"]
        domain_results[str(domain["id"])] = result

    # Aggregate overall judgement
    domain_judgements = [domain_results[str(d["id"])]["judgement"] for d in DOMAINS]
    overall = robins_i_overall(domain_judgements)

    # Direction of bias — modal across domains; ties → Unpredictable
    dirs = [domain_results[str(d["id"])]["direction"]
            for d in DOMAINS
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
    """Return prompts + decision-tree source for the developer icon.

    The returned structure surfaces:
      - The system prompt + preflight prompt template
      - Each domain's signal definitions (verbatim from the cribsheet)
      - Each domain's pure-Python decision tree via inspect.getsource
      - The overall-aggregation algorithm
    """
    domain_entries = []
    for domain in DOMAINS:
        sample_fields = {k: "<extracted value>"
                         for k in domain.get("relevant_fields", [])}
        if domain.get("variant_signals"):
            # Domain 1 — emit prompts for both variants
            prompts = {
                "A": build_domain_prompt(
                    domain, "A", "Cohort Study",
                    "<primary outcome here>", sample_fields,
                ),
                "B": build_domain_prompt(
                    domain, "B", "Cohort Study",
                    "<primary outcome here>", sample_fields,
                ),
            }
            judge_code = {
                "A": inspect.getsource(domain1_variant_a_judge),
                "B": inspect.getsource(domain1_variant_b_judge),
            }
            signals_payload = {
                "A": DOMAIN1_VARIANT_A_SIGNALS,
                "B": DOMAIN1_VARIANT_B_SIGNALS,
            }
        else:
            prompts = build_domain_prompt(
                domain, "A", "Cohort Study",
                "<primary outcome here>", sample_fields,
            )
            judge_fn = DOMAIN_JUDGES_VARIANT_A[domain["id"]]
            judge_code = inspect.getsource(judge_fn)
            signals_payload = domain["signals"]

        domain_entries.append({
            "id": domain["id"],
            "name": domain["name"],
            "signals": signals_payload,
            "relevant_fields": domain.get("relevant_fields", []),
            "prompt_template": prompts,
            "decision_tree_code": judge_code,
        })

    return {
        "tool": (
            "ROBINS-I V2 (20 November 2025 cribsheet) — non-randomized "
            "studies of interventions, published scope: follow-up (cohort) "
            "studies. The tool is also applied to other non-randomized "
            "designs by the quality-appraisal dispatcher as the "
            "best-available approximation."
        ),
        "system_prompt": _SYSTEM_PROMPT,
        "signal_options_all": list(SIGNAL_OPTIONS_ALL),
        "judgements": list(JUDGEMENTS),
        "domain_1_low_label": LOW_D1,
        "preflight_prompt_template": _build_preflight_prompt(
            "Cohort Study", "<primary outcome here>", {},
        ),
        "domains": domain_entries,
        "overall_algorithm_code": inspect.getsource(robins_i_overall),
    }
