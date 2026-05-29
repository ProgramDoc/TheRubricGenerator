"""ROBINS-I V1 (1 August 2016) — production module for The AI Researcher.

Cribsheet: Sterne JAC, Hernán MA, Reeves BC, Savović J, Berkman ND, Viswanathan M,
Henry D, Altman DG, Ansari MT, Boutron I, Carpenter JR, Chan A-W, Churchill R,
Hróbjartsson A, Kirkham J, Jüni P, Loke YK, Pigott TD, Ramsay CR, Regidor D,
Rothstein HR, Sandhu L, Santaguida PL, Schünemann HJ, Shea B, Shrier I, Tugwell P,
Turner L, Valentine JC, Waddington H, Waters E, Whiting P, Higgins JPT.
*The Risk Of Bias In Non-randomized Studies — of Interventions (ROBINS-I)
assessment tool (version for cohort-type studies).* Version 1 August 2016.
Underlying paper: Sterne JAC et al., BMJ 2016;355:i4919.

Parallel to ``robins_i.py`` (V2). Selected per-run via
``quality_appraisal_runs.robins_i_tool_choice`` — default is V2;
``"robins_i_v1"`` opts in to V1 (used by the OVID team and other ongoing V1
workflows).

**Cohort variant** (the original V1 cribsheet scope) — 7 domains with the
§1.1 aim preflight; D4 is aim-gated.

**Single-arm variant** (project-specific extension; mirrors V2's `single_arm`
adaptation for V1's 5-token vocab) — selected when
``classification["study_type"] in SINGLE_ARM_STUDY_TYPES``. The §1.1 aim
preflight is replaced by a benchmark preflight (B1-SA / B2-SA / B3); D1 is
reframed as benchmark adequacy + prognostic-mix comparability (1S.1–1S.5)
and D2 as intervention fidelity + intent-vs-received cohort definition
(2S.1–2S.3). D4 is marked NA in code with no LLM call (V2 retired this
domain entirely; its concerns are folded into D2-SA's 2S.3). D3, D5, D6,
D7 reuse the cohort signals + judges unchanged. V1's 5-token signal vocab
(Y/PY/PN/N/NI) cannot express V2's WN/SN/WY/SY gradient — the conservative
mapping is documented in ``docs/shareable/robins_i_v1_shareable.md`` §17.

This module ports the self-contained reference implementation in
``docs/shareable/robins_i_v1_shareable.md`` (§15) and adapts it to the
project's tool-runner contract:

  ``run(pdf_bytes, extracted_fields, classification, primary_outcome, *,
        progress=None, target_pico=None) -> (domain_results, overall, direction)``

The §1.1 aim preflight LLM call (cohort path) or benchmark preflight LLM call
(single-arm path) lives inside ``run()`` and its output is stashed in
``domain_results["aim_preflight"]`` (cohort) or ``domain_results["preflight"]``
(single-arm) so the per-paper detail view can surface the rationale (mirrors
V2's ``domain_results["preflight"]`` convention).
"""
from __future__ import annotations

import inspect
import json
import logging
from collections import Counter
from typing import Any, Callable

from ..annotator import _call_with_pdf

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Scales
# ─────────────────────────────────────────────
SIGNAL_OPTIONS = ("Y", "PY", "PN", "N", "NI")
JUDGEMENTS = ("Low", "Moderate", "Serious", "Critical", "No information")
AIMS = ("assignment_to", "starting_and_adhering")

# Single-arm variant — selected at run() entry from classification["study_type"]
# BEFORE the aim preflight (mirrors V2's pattern in robins_i.py:120). When this
# matches, the aim preflight is skipped and replaced by the benchmark preflight
# (B1-SA / B2-SA / B3); D1 + D2 use the SA signal sets + judges; D4 is set to
# NA in code with no LLM call.
SINGLE_ARM_STUDY_TYPES = frozenset({"Single-Arm Trial", "Dose-Escalation Study"})

# Domain 1 'Low' label for the single-arm variant — replaces plain "Low" since
# uncontrolled-confounding-by-indication is N/A; what remains is residual
# uncertainty about whether the implied benchmark / external control truly
# matches the cohort's prognostic profile. Same string V2 uses for parity.
LOW_D1_SA = "Low (except for concerns about uncontrolled benchmarking)"


# Per-question response-option subsets
_BASIC = ("Y", "PY", "PN", "N", "NI")            # standard 5-token
_BASIC_NA = ("NA", "Y", "PY", "PN", "N", "NI")   # gated questions add NA
_BASIC_NO_NI = ("Y", "PY", "PN", "N")            # 1.1 has no NI option


# ─────────────────────────────────────────────
# Helper predicates
# ─────────────────────────────────────────────
def _yes(ans: str) -> bool:
    return ans in ("Y", "PY")


def _no(ans: str) -> bool:
    return ans in ("N", "PN")


def _no_info(ans: str) -> bool:
    return ans == "NI"


# ─────────────────────────────────────────────
# Decision trees — conservative interpretations of cribsheet Tables 1 + 2
# ─────────────────────────────────────────────
def domain1_judge(signals: dict[str, str]) -> str:
    """V1 D1 — Bias due to confounding. Cribsheet Table 1 row."""
    q1_1 = signals.get("1.1", "NI")
    if q1_1 in ("N", "PN"):
        return "Low"  # cribsheet early exit
    if q1_1 == "NI":
        return "No information"

    q1_2 = signals.get("1.2", "NI")
    q1_3 = signals.get("1.3", "NI")

    if q1_2 in ("Y", "PY") and q1_3 in ("Y", "PY"):
        # Time-varying path
        q1_7 = signals.get("1.7", "NI")
        q1_8 = signals.get("1.8", "NI")
        if q1_7 in ("Y", "PY") and q1_8 in ("Y", "PY"):
            return "Moderate"
        if q1_7 in ("N", "PN") or q1_8 in ("N", "PN"):
            return "Serious"
        return "No information"

    # Baseline-only path
    q1_4 = signals.get("1.4", "NI")
    q1_5 = signals.get("1.5", "NI")
    q1_6 = signals.get("1.6", "NI")

    if q1_6 in ("Y", "PY"):
        return "Serious"
    if q1_4 in ("N", "PN") or q1_5 in ("N", "PN"):
        return "Serious"
    if q1_4 in ("Y", "PY") and q1_5 in ("Y", "PY") and q1_6 in ("N", "PN"):
        return "Moderate"
    return "No information"


def domain2_judge(signals: dict[str, str]) -> str:
    """V1 D2 — Bias in selection of participants. Cribsheet Table 1 row."""
    q2_1 = signals.get("2.1", "NI")
    q2_4 = signals.get("2.4", "NI")
    q2_2 = signals.get("2.2", "NI")
    q2_3 = signals.get("2.3", "NI")
    q2_5 = signals.get("2.5", "NI")

    if q2_1 in ("N", "PN") and q2_4 in ("Y", "PY"):
        return "Low"
    if q2_5 in ("Y", "PY"):
        return "Moderate"
    if q2_1 in ("Y", "PY") and q2_2 in ("Y", "PY") and q2_3 in ("Y", "PY"):
        return "Serious"
    if q2_4 in ("N", "PN"):
        return "Serious"
    if q2_1 == "NI" and q2_4 == "NI":
        return "No information"
    return "Moderate"


def domain3_judge(signals: dict[str, str]) -> str:
    """V1 D3 — Bias in classification of interventions. Cribsheet Table 1 row."""
    q3_1 = signals.get("3.1", "NI")
    q3_2 = signals.get("3.2", "NI")
    q3_3 = signals.get("3.3", "NI")

    if q3_1 in ("Y", "PY") and q3_2 in ("Y", "PY") and q3_3 in ("N", "PN"):
        return "Low"
    if q3_1 in ("N", "PN") or q3_3 in ("Y", "PY"):
        return "Serious"
    if q3_1 == "NI":
        return "No information"
    return "Moderate"


def domain4_judge(signals: dict[str, str], aim: str = "assignment_to") -> str:
    """V1 D4 — Bias due to deviations from intended interventions. Table 2 row.

    aim must be "assignment_to" (uses 4.1, 4.2) or
                "starting_and_adhering" (uses 4.3-4.6).
    """
    if aim == "assignment_to":
        q4_1 = signals.get("4.1", "NI")
        q4_2 = signals.get("4.2", "NI")
        if q4_1 in ("N", "PN"):
            return "Low"
        if q4_2 in ("N", "PN"):
            return "Low"
        if q4_2 in ("Y", "PY"):
            return "Serious"
        if q4_1 == "NI" or q4_2 == "NI":
            return "No information"
        return "Moderate"

    if aim == "starting_and_adhering":
        q4_3 = signals.get("4.3", "NI")
        q4_4 = signals.get("4.4", "NI")
        q4_5 = signals.get("4.5", "NI")
        q4_6 = signals.get("4.6", "NI")
        if (q4_3 in ("Y", "PY") and q4_4 in ("Y", "PY") and q4_5 in ("Y", "PY")):
            return "Low"
        if q4_6 in ("Y", "PY"):
            return "Moderate"
        bad = (q4_3 in ("N", "PN") or q4_4 in ("N", "PN") or q4_5 in ("N", "PN"))
        if bad and q4_6 in ("N", "PN", "NI"):
            return "Serious"
        if q4_3 == "NI" and q4_4 == "NI" and q4_5 == "NI":
            return "No information"
        return "Moderate"

    raise ValueError(f"Unknown aim: {aim}")


def domain5_judge(signals: dict[str, str]) -> str:
    """V1 D5 — Bias due to missing data. Cribsheet Table 2 row."""
    q5_1 = signals.get("5.1", "NI")
    q5_2 = signals.get("5.2", "NI")
    q5_3 = signals.get("5.3", "NI")
    q5_4 = signals.get("5.4", "NI")
    q5_5 = signals.get("5.5", "NI")

    if (q5_1 in ("Y", "PY") and q5_2 in ("N", "PN") and q5_3 in ("N", "PN")):
        return "Low"

    has_missing = (q5_1 in ("N", "PN") or q5_2 in ("Y", "PY") or q5_3 in ("Y", "PY"))
    if has_missing:
        if q5_4 in ("Y", "PY") or q5_5 in ("Y", "PY"):
            return "Moderate"
        if q5_4 in ("N", "PN") or q5_5 in ("N", "PN"):
            return "Serious"
        if q5_4 == "NI" and q5_5 == "NI":
            return "No information"
        return "Moderate"

    if q5_1 == "NI" and q5_2 == "NI" and q5_3 == "NI":
        return "No information"
    return "Moderate"


def domain6_judge(signals: dict[str, str]) -> str:
    """V1 D6 — Bias in measurement of outcomes. Cribsheet Table 2 row."""
    q6_1 = signals.get("6.1", "NI")
    q6_2 = signals.get("6.2", "NI")
    q6_3 = signals.get("6.3", "NI")
    q6_4 = signals.get("6.4", "NI")

    if (q6_3 in ("Y", "PY")
        and (q6_1 in ("N", "PN") or q6_2 in ("N", "PN"))
        and q6_4 in ("N", "PN")):
        return "Low"
    if q6_3 in ("N", "PN"):
        return "Serious"
    if q6_1 in ("Y", "PY") and q6_2 in ("Y", "PY"):
        return "Serious"
    if q6_4 in ("Y", "PY"):
        return "Serious"
    if q6_3 == "NI" and q6_1 == "NI" and q6_2 == "NI":
        return "No information"
    return "Moderate"


def domain7_judge(signals: dict[str, str]) -> str:
    """V1 D7 — Bias in selection of the reported result. Cribsheet Table 2 row."""
    q7_1 = signals.get("7.1", "NI")
    q7_2 = signals.get("7.2", "NI")
    q7_3 = signals.get("7.3", "NI")

    yes_count = sum(1 for q in (q7_1, q7_2, q7_3) if q in ("Y", "PY"))
    ni_count = sum(1 for q in (q7_1, q7_2, q7_3) if q == "NI")

    if yes_count >= 2:
        return "Critical"
    if yes_count == 1:
        return "Serious"
    if ni_count == 3:
        return "No information"
    if ni_count >= 1:
        return "Moderate"
    return "Low"


def domain1_single_arm_judge(signals: dict[str, str]) -> str:
    """V1 D1 single-arm — Benchmark adequacy + prognostic-mix comparability.

    Project-specific adaptation that mirrors V2's
    ``domain1_variant_single_arm_judge`` (see ``robins_i.py:263``) but uses
    V1's 5-token signal vocabulary (Y/PY/PN/N/NI — no WN/SN gradient) and
    5-level judgement scale.

    Single-arm trials have no internal comparator. Classical
    confounding-by-indication is N/A; the domain instead assesses:

    1. **Benchmark adequacy** — was the implied comparison (historical
       control rate, performance criterion, or null hypothesis decision
       rule) pre-specified and reasonable for the population? (1S.1, 1S.2)
    2. **Prognostic-mix comparability** — is the cohort's measured baseline
       prognostic profile comparable to the benchmark population, and did
       authors address residual differences? (1S.3, 1S.4)

    1S.5 (negative / falsification controls / external-validity) serves the
    same Critical-elevating role as 1A.4 / 1B.5 in V2's cohort variants.

    Signaling questions:
      1S.1  Implied benchmark pre-specified before data collection?
      1S.2  Implied benchmark reasonable for this population?
      1S.3  Cohort's baseline prognostic profile comparable to benchmark?
      1S.4  Quantitative adjustment to external controls / sensitivity analyses?
      1S.5  Negative / falsification controls suggest serious uncontrolled
            selection-prognostic bias?

    V1-vs-V2 vocab note: V2's 1S.3 accepts WN ("most-but-not-all comparable")
    and SN ("materially less comparable"); V1 collapses both into PN/N. The
    tree maps V1's PN → V2's WN-equivalent (Moderate floor) and V1's N →
    V2's SN-equivalent (Serious unless 1S.4 rescues), per the conservative
    interpretation rule in ``docs/shareable/robins_i_v1_shareable.md`` §17.

    Returns Low (with the LOW_D1_SA label), Moderate, Serious, Critical, or
    No information.
    """
    q1 = signals.get("1S.1", "NI")
    q2 = signals.get("1S.2", "NI")
    q3 = signals.get("1S.3", "NI")
    q4 = signals.get("1S.4", "NI")
    q5 = signals.get("1S.5", "NI")

    # 1S.5 dominates: falsification-control hit → Critical regardless
    if _yes(q5):
        return "Critical"

    # 1S.1 N/PN: no pre-specified benchmark
    if _no(q1):
        # 1S.4 N/PN: no quantitative adjustment either → Critical
        if _no(q4):
            return "Critical"
        return "Serious"

    if _no_info(q1):
        return "No information"

    # 1S.1 Y/PY: benchmark pre-specified
    if _yes(q1):
        # 1S.3 prognostic comparability
        if _yes(q3):
            # 1S.2 (benchmark reasonable) decides Low-SA vs Moderate
            if _yes(q2):
                return LOW_D1_SA
            return "Moderate"
        # V1 collapse: PN → V2-WN-equivalent (Moderate floor)
        if q3 == "PN":
            return "Moderate"
        # V1 collapse: N → V2-SN-equivalent (substantial mismatch).
        # NI on 1S.3 — silent on prognostic comparability — treated the same.
        if q3 == "N" or _no_info(q3):
            # 1S.4 (quantitative adjustment) can rescue
            if _yes(q4):
                return "Moderate"
            return "Serious"

    return "Serious"


def domain2_single_arm_judge(signals: dict[str, str]) -> str:
    """V1 D2 single-arm — Intervention fidelity + intent-vs-received cohort.

    Project-specific adaptation that mirrors V2's
    ``domain2_variant_single_arm_judge`` (see ``robins_i.py:384``) but uses
    V1's 5-token signal vocabulary (no SY/WY gradient on 2S.3, no WN/SN
    gradient on 2S.2) and 5-level judgement scale.

    With no comparator, classical differential misclassification by group
    is meaningless. What remains is whether the intervention was
    well-defined, whether dose modifications / discontinuations were
    recorded, and crucially whether the analyzed cohort was defined by
    *intended* treatment (ITT-like) or *received* treatment (per-protocol —
    risks selection toward responders).

    Signaling questions:
      2S.1  Intervention well-defined (dose, schedule, duration,
            dose-modifications protocol) at start of follow-up?
      2S.2  Were dose reductions, holds, and discontinuations recorded
            and reported?
      2S.3  Was the analyzed cohort defined by intended treatment (everyone
            enrolled, ITT-like) or by received treatment (only those
            completing ≥X cycles / responding)?

    V1-vs-V2 vocab note: V2's 2S.3 uses SY/WY/PN/N/NI where SY = "explicitly
    restricted to completers/responders" → Critical and WY = "excludes some
    enrolled but not dominantly for treatment reasons" → Serious. V1
    collapses both into Y/PY. To preserve the V2 severity gradient under V1's
    vocab, the tree treats **Y as the V2-SY-equivalent** (the strongest
    statement available) → Critical, and **PY as the V2-WY-equivalent** →
    Serious. Reviewers should answer 2S.3 with Y only when the paper
    explicitly restricts the primary analysis to completers/responders.

    V2's 2S.2 accepts WN/SN gradient; V1 collapses to PN/N. The tree maps
    PN → V2-WN-equivalent (Moderate) and N → V2-SN-equivalent (Serious).

    Returns Low, Moderate, Serious, Critical, or No information.
    """
    q1 = signals.get("2S.1", "NI")
    q2 = signals.get("2S.2", "NI")
    q3 = signals.get("2S.3", "NI")

    # 2S.3 dominates: cohort-definition selection bias
    if q3 == "Y":
        # V2-SY-equivalent — primary analysis explicitly restricted to
        # completers/responders → Critical
        return "Critical"
    if q3 == "PY" or _no_info(q3):
        # V2-WY-equivalent or unclear → Serious (intervention-definition
        # quality does not rescue cohort-filter bias)
        return "Serious"

    # q3 in (PN, N): cohort defined by intended treatment → low concern here
    if _yes(q1):
        # Well-defined intervention. 2S.2 (recording fidelity) decides.
        if _yes(q2):
            return "Low"
        if q2 == "PN":
            return "Moderate"
        if q2 == "N":
            return "Serious"
        # NI on 2S.2 — measurement-fidelity uncertain
        return "Moderate"

    # 2S.1 N/PN: intervention definition unclear
    if _no(q1):
        return "Serious"
    # NI on 2S.1
    return "No information"


def robins_i_v1_overall(domain_judgements: list[str]) -> str:
    """Overall risk-of-bias per V1 Table 3.

    Single-arm runs mark D4 as ``NA`` in code (V2 retired the deviations
    domain entirely; its concerns are folded into D2-SA's 2S.3). NA
    judgements are excluded from aggregation. The single-arm Domain 1
    label (LOW_D1_SA) is normalized to "Low" for aggregation since the
    caveat in the label is reported in the per-domain detail.
    """
    if not domain_judgements:
        return "No information"

    def _norm(j: str) -> str:
        # LOW_D1_SA = "Low (except for concerns about uncontrolled
        # benchmarking)" — V2's footnote pattern; aggregates as "Low".
        if j.startswith("Low"):
            return "Low"
        return j

    active = [_norm(j) for j in domain_judgements if j and j != "NA"]
    if not active:
        return "No information"
    if any(j == "Critical" for j in active):
        return "Critical"
    if any(j == "Serious" for j in active):
        return "Serious"
    if all(j == "Low" for j in active):
        return "Low"
    if all(j == "No information" for j in active):
        return "No information"
    if all(j in ("Low", "Moderate") for j in active):
        return "Moderate"
    return "No information"


DOMAIN_JUDGES = {
    1: domain1_judge,
    2: domain2_judge,
    3: domain3_judge,
    # 4: dispatched separately because it takes aim= kwarg
    5: domain5_judge,
    6: domain6_judge,
    7: domain7_judge,
}


# Single-arm judge dispatch. D1 and D2 use the SA-specific decision trees;
# D3/D5/D6/D7 reuse the cohort judges unchanged (those domain concerns
# transfer meaningfully to single-arm). D4 is intentionally absent — it's
# set to NA in run() with no LLM call.
DOMAIN_JUDGES_SINGLE_ARM: dict[int, Callable[[dict[str, str]], str]] = {
    1: domain1_single_arm_judge,
    2: domain2_single_arm_judge,
    3: domain3_judge,
    5: domain5_judge,
    6: domain6_judge,
    7: domain7_judge,
}


# ─────────────────────────────────────────────
# Cascade enforcement — rule-based NA handling per the cribsheet's
# cascading-question structure. Called AFTER the LLM responds, before the
# decision tree runs. Overrides LLM answers for gated-out questions to NA.
# ─────────────────────────────────────────────
def enforce_cascade_d1(signals: dict[str, str]) -> dict[str, str]:
    """V1 D1 — confounding. Cascading structure (cribsheet pp 5-6)."""
    out = dict(signals)
    q1_1 = out.get("1.1", "NI")

    if q1_1 in ("N", "PN", "NI"):
        for sid in ("1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8"):
            out[sid] = "NA"
        return out

    q1_2 = out.get("1.2", "NI")
    q1_3 = out.get("1.3", "NI")

    if q1_2 in ("Y", "PY") and q1_3 in ("Y", "PY"):
        # Time-varying path
        for sid in ("1.4", "1.5", "1.6"):
            out[sid] = "NA"
        if out.get("1.7", "NI") not in ("Y", "PY"):
            out["1.8"] = "NA"
    else:
        # Baseline-only path
        for sid in ("1.7", "1.8"):
            out[sid] = "NA"
        if out.get("1.4", "NI") not in ("Y", "PY"):
            out["1.5"] = "NA"
        if q1_2 not in ("Y", "PY"):
            out["1.3"] = "NA"

    return out


def enforce_cascade_d2(signals: dict[str, str]) -> dict[str, str]:
    """V1 D2 — selection. Cascading structure (cribsheet p 7)."""
    out = dict(signals)
    q2_1 = out.get("2.1", "NI")

    if q2_1 not in ("Y", "PY"):
        out["2.2"] = "NA"
        out["2.3"] = "NA"
    else:
        if out.get("2.2", "NI") not in ("Y", "PY"):
            out["2.3"] = "NA"

    cond_22_23 = (out.get("2.2", "NA") in ("Y", "PY")
                  and out.get("2.3", "NA") in ("Y", "PY"))
    cond_24 = out.get("2.4", "NI") in ("N", "PN")
    if not (cond_22_23 or cond_24):
        out["2.5"] = "NA"

    return out


def enforce_cascade_d4(signals: dict[str, str], aim: str) -> dict[str, str]:
    """V1 D4 — deviations. Within-path cascading (cribsheet pp 9-10).

    Aim-gating (which question subset is asked) is handled upstream by
    ``_signals_for_domain``. This function handles WITHIN-PATH gating:
      assignment_to:        4.2 only asked if 4.1 = Y/PY; else NA
      starting_and_adhering: 4.6 only asked if any of 4.3/4.4/4.5 = N/PN; else NA
    """
    out = dict(signals)
    if aim == "assignment_to":
        if out.get("4.1", "NI") not in ("Y", "PY"):
            out["4.2"] = "NA"
    elif aim == "starting_and_adhering":
        any_bad = any(out.get(sid, "NI") in ("N", "PN")
                      for sid in ("4.3", "4.4", "4.5"))
        if not any_bad:
            out["4.6"] = "NA"
    return out


def enforce_cascade_d5(signals: dict[str, str]) -> dict[str, str]:
    """V1 D5 — missing data. Cascading structure (cribsheet p 11)."""
    out = dict(signals)
    trigger = (out.get("5.1", "NI") in ("PN", "N")
               or out.get("5.2", "NI") in ("Y", "PY")
               or out.get("5.3", "NI") in ("Y", "PY"))
    if not trigger:
        out["5.4"] = "NA"
        out["5.5"] = "NA"
    return out


def enforce_cascade(domain_id: int,
                    signals: dict[str, str],
                    aim: str = "assignment_to") -> dict[str, str]:
    """Apply the appropriate per-domain cascade enforcer. D3, D6, D7 have no
    cascading and return signals unchanged."""
    if domain_id == 1:
        return enforce_cascade_d1(signals)
    if domain_id == 2:
        return enforce_cascade_d2(signals)
    if domain_id == 4:
        return enforce_cascade_d4(signals, aim=aim)
    if domain_id == 5:
        return enforce_cascade_d5(signals)
    return signals  # D3, D6, D7 have no cascade


# ─────────────────────────────────────────────
# Signal definitions — verbatim from the V1 cribsheet
# ─────────────────────────────────────────────
DOMAIN1_SIGNALS: list[dict[str, Any]] = [
    {"id": "1.1", "text": "Is there potential for confounding of the effect of intervention in this study?", "options": list(_BASIC_NO_NI), "elaboration": "In rare situations, such as when studying harms that are very unlikely to be related to factors that influence treatment decisions, no confounding is expected and the study can be considered to be at low risk of bias due to confounding, equivalent to a fully randomized trial. There is no NI (No information) option for this signalling question."},
    {"id": "1.2", "text": "Was the analysis based on splitting participants' follow up time according to intervention received?", "options": list(_BASIC_NA), "elaboration": "If participants could switch between intervention groups then associations between intervention and outcome may be biased by time-varying confounding. This occurs when prognostic factors influence switches between intended interventions."},
    {"id": "1.3", "text": "Were intervention discontinuations or switches likely to be related to factors that are prognostic for the outcome?", "options": list(_BASIC_NA), "elaboration": "If intervention switches are unrelated to the outcome, for example when the outcome is an unexpected harm, then time-varying confounding will not be present and only control for baseline confounding is required."},
    {"id": "1.4", "text": "Did the authors use an appropriate analysis method that controlled for all the important confounding domains?", "options": list(_BASIC_NA), "elaboration": "Appropriate methods to control for measured confounders include stratification, regression, matching, standardization, and inverse probability weighting. They may control for individual variables or for the estimated propensity score. Each method depends on the assumption that there is no unmeasured or residual confounding."},
    {"id": "1.5", "text": "If Y/PY to 1.4: Were confounding domains that were controlled for measured validly and reliably by the variables available in this study?", "options": list(_BASIC_NA), "elaboration": "Appropriate control of confounding requires that the variables adjusted for are valid and reliable measures of the confounding domains. Subjective measures (e.g. based on self-report) may have lower validity and reliability than objective measures such as lab findings."},
    {"id": "1.6", "text": "Did the authors control for any post-intervention variables that could have been affected by the intervention?", "options": list(_BASIC_NA), "elaboration": "Controlling for post-intervention variables that are affected by intervention is not appropriate. Controlling for mediating variables estimates the direct effect of intervention and may introduce bias."},
    {"id": "1.7", "text": "Did the authors use an appropriate analysis method that adjusted for all the important confounding domains and for time-varying confounding?", "options": list(_BASIC_NA), "elaboration": "Adjustment for time-varying confounding is necessary to estimate the effect of starting and adhering to intervention. Appropriate methods include those based on inverse probability weighting. Standard regression models that include time-updated confounders may be problematic if time-varying confounding is present."},
    {"id": "1.8", "text": "If Y/PY to 1.7: Were confounding domains that were adjusted for measured validly and reliably by the variables available in this study?", "options": list(_BASIC_NA), "elaboration": "Same measurement-validity question as 1.5 but applied to baseline + time-varying confounders."},
]

DOMAIN2_SIGNALS: list[dict[str, Any]] = [
    {"id": "2.1", "text": "Was selection of participants into the study (or into the analysis) based on participant characteristics observed after the start of intervention?", "options": list(_BASIC), "elaboration": "This domain is concerned only with selection into the study based on participant characteristics observed after the start of intervention. Baseline confounding is addressed in Domain 1, not here."},
    {"id": "2.2", "text": "If Y/PY to 2.1: Were the post-intervention variables that influenced selection likely to be associated with intervention?", "options": list(_BASIC_NA), "elaboration": "Selection bias occurs when selection is related to an effect of either intervention or a cause of intervention AND an effect of either the outcome or a cause of the outcome."},
    {"id": "2.3", "text": "If Y/PY to 2.2: Were the post-intervention variables that influenced selection likely to be influenced by the outcome or a cause of the outcome?", "options": list(_BASIC_NA), "elaboration": "Collider-style selection bias."},
    {"id": "2.4", "text": "Do start of follow-up and start of intervention coincide for most participants?", "options": list(_BASIC), "elaboration": "If participants are not followed from the start of the intervention then a period of follow up has been excluded, and individuals who experienced the outcome soon after intervention will be missing from analyses."},
    {"id": "2.5", "text": "If Y/PY to 2.2 and 2.3, or N/PN to 2.4: Were adjustment techniques used that are likely to correct for the presence of selection biases?", "options": list(_BASIC_NA), "elaboration": "It is in principle possible to correct for selection biases using inverse probability weights or missing-data methods, but such methods are rarely used in practice."},
]

DOMAIN3_SIGNALS: list[dict[str, Any]] = [
    {"id": "3.1", "text": "Were intervention groups clearly defined?", "options": list(_BASIC), "elaboration": "A pre-requisite for an appropriate comparison of interventions is that the interventions are well defined. For individual-level interventions, criteria for considering individuals to have received each intervention should be clear and explicit, covering issues such as type, setting, dose, frequency, intensity and/or timing of intervention."},
    {"id": "3.2", "text": "Was the information used to define intervention groups recorded at the start of the intervention?", "options": list(_BASIC), "elaboration": "If information about interventions received is available from sources that could not have been affected by subsequent outcomes, then differential misclassification of intervention status is unlikely. Collection at the time of intervention makes it easier to avoid such misclassification."},
    {"id": "3.3", "text": "Could classification of intervention status have been affected by knowledge of the outcome or risk of the outcome?", "options": list(_BASIC), "elaboration": "Collection of the information at the time of the intervention may not be sufficient to avoid bias. The way in which the data are collected for the purposes of the NRSI should also avoid misclassification."},
]

DOMAIN4_SIGNALS: list[dict[str, Any]] = [
    # Aim = "assignment_to" path
    {"id": "4.1", "text": "Were there deviations from the intended intervention beyond what would be expected in usual practice?", "options": list(_BASIC), "elaboration": "Deviations that happen in usual practice following the intervention (for example, cessation of a drug intervention because of acute toxicity) are part of the intended intervention and therefore do not lead to bias in the effect of assignment to intervention. Such deviations are not expected in observational studies of individuals in routine care."},
    {"id": "4.2", "text": "If Y/PY to 4.1: Were these deviations from intended intervention unbalanced between groups and likely to have affected the outcome?", "options": list(_BASIC_NA), "elaboration": "Deviations from intended interventions that do not reflect usual practice will be important if they affect the outcome, but not otherwise. Bias will arise only if there is imbalance in the deviations across the two groups."},
    # Aim = "starting_and_adhering" path
    {"id": "4.3", "text": "Were important co-interventions balanced across intervention groups?", "options": list(_BASIC), "elaboration": "Risk of bias will be higher if unplanned co-interventions were implemented in a way that would bias the estimated effect of intervention. Bias will arise only if there is imbalance in such co-interventions between the intervention groups."},
    {"id": "4.4", "text": "Was the intervention implemented successfully for most participants?", "options": list(_BASIC), "elaboration": "Risk of bias will be higher if the intervention was not implemented as intended by, for example, the health care professionals delivering care during the trial."},
    {"id": "4.5", "text": "Did study participants adhere to the assigned intervention regimen?", "options": list(_BASIC), "elaboration": "Risk of bias will be higher if participants did not adhere to the intervention as intended. Lack of adherence includes imperfect compliance, cessation of intervention, crossovers, and switches to another active intervention."},
    {"id": "4.6", "text": "If N/PN to 4.3, 4.4 or 4.5: Was an appropriate analysis used to estimate the effect of starting and adhering to the intervention?", "options": list(_BASIC_NA), "elaboration": "Examples of appropriate analysis strategies include inverse probability weighting or instrumental variable estimation. Specialist advice may be needed to assess studies that used these approaches."},
]

DOMAIN5_SIGNALS: list[dict[str, Any]] = [
    {"id": "5.1", "text": "Were outcome data available for all, or nearly all, participants?", "options": list(_BASIC), "elaboration": "'Nearly all' should be interpreted as 'enough to be confident of the findings'. Availability of data from 95% (or 90%) of participants may be sufficient when events are reasonably common in both intervention groups."},
    {"id": "5.2", "text": "Were participants excluded due to missing data on intervention status?", "options": list(_BASIC), "elaboration": "Missing intervention status may be a problem. This requires that the intended study sample is clear, which it may not be in practice."},
    {"id": "5.3", "text": "Were participants excluded due to missing data on other variables needed for the analysis?", "options": list(_BASIC), "elaboration": "This question relates particularly to participants excluded from the analysis because of missing information on confounders that were controlled for in the analysis."},
    {"id": "5.4", "text": "If PN/N to 5.1, or Y/PY to 5.2 or 5.3: Are the proportion of participants and reasons for missing data similar across interventions?", "options": list(_BASIC_NA), "elaboration": "This aims to elicit whether either differential proportion of missing observations or differences in reasons for missing observations could substantially impact on our ability to answer the question being addressed."},
    {"id": "5.5", "text": "If PN/N to 5.1, or Y/PY to 5.2 or 5.3: Is there evidence that results were robust to the presence of missing data?", "options": list(_BASIC_NA), "elaboration": "Evidence for robustness may come from how missing data were handled and whether sensitivity analyses were performed. Both content knowledge and statistical expertise will often be required for this judgement."},
]

DOMAIN6_SIGNALS: list[dict[str, Any]] = [
    {"id": "6.1", "text": "Could the outcome measure have been influenced by knowledge of the intervention received?", "options": list(_BASIC), "elaboration": "Some outcome measures involve negligible assessor judgment, e.g. all-cause mortality or non-repeatable automated laboratory assessments. Risk of bias due to measurement of these outcomes would be expected to be low."},
    {"id": "6.2", "text": "Were outcome assessors aware of the intervention received by study participants?", "options": list(_BASIC), "elaboration": "N if outcome assessors were blinded. In studies where participants report their outcomes themselves, the outcome assessor is the study participant — in observational studies the answer will usually be 'Yes' when participants report their outcomes themselves."},
    {"id": "6.3", "text": "Were the methods of outcome assessment comparable across intervention groups?", "options": list(_BASIC), "elaboration": "Comparable assessment methods would involve the same outcome detection methods and thresholds, same time point, same definition, and same measurements."},
    {"id": "6.4", "text": "Were any systematic errors in measurement of the outcome related to intervention received?", "options": list(_BASIC), "elaboration": "This refers to differential misclassification of outcomes. Systematic errors in measuring the outcome, if present, could cause bias if they are related to intervention or to a confounder of the intervention-outcome relationship."},
]

DOMAIN7_SIGNALS: list[dict[str, Any]] = [
    {"id": "7.1", "text": "Is the reported effect estimate likely to be selected, on the basis of the results, from multiple outcome measurements within the outcome domain?", "options": list(_BASIC), "elaboration": "For a specified outcome domain, it is possible to generate multiple effect estimates for different measurements. If multiple measurements were made but only one or a subset is reported, there is a risk of selective reporting on the basis of results."},
    {"id": "7.2", "text": "Is the reported effect estimate likely to be selected, on the basis of the results, from multiple analyses of the intervention-outcome relationship?", "options": list(_BASIC), "elaboration": "Examples include unadjusted vs adjusted models; final value vs change from baseline vs ANCOVA; different transformations; different covariate sets; different missing-data strategies. If the analyst does not pre-specify methods and multiple estimates are generated but only one or a subset is reported, there is a risk of selective reporting."},
    {"id": "7.3", "text": "Is the reported effect estimate likely to be selected, on the basis of the results, from different subgroups?", "options": list(_BASIC), "elaboration": "Particularly with large cohorts often available from routine data sources, it is possible to generate multiple effect estimates for different subgroups or simply to omit varying proportions of the original cohort."},
]


DOMAIN1_SIGNALS_SINGLE_ARM: list[dict[str, Any]] = [
    {"id": "1S.1", "text": "Was the implied benchmark (historical control rate, pre-specified performance criterion, or null hypothesis with a quantitative decision rule) pre-specified before data collection?", "options": list(_BASIC_NO_NI), "elaboration": "Single-arm trials have no internal comparator. They are interpreted against an implicit benchmark — usually a historical-control response rate, a regulatory performance criterion (e.g. ORR > 30% to support accelerated approval), or a null hypothesis with a pre-specified statistical decision rule (e.g. Simon's two-stage design). Answer Y/PY if a numeric benchmark + decision rule was clearly stated in the protocol / SAP / methods, BEFORE the data were collected. Answer N/PN if no benchmark is identifiable, or if the benchmark looks chosen post-hoc to match the observed result. There is no NI option — commit to one of Y/PY/PN/N (matches V1's 1.1 convention)."},
    {"id": "1S.2", "text": "Is the implied benchmark reasonable given current standard of care and the patient population being studied?", "options": list(_BASIC), "elaboration": "A pre-specified benchmark is only useful if it reflects a clinically meaningful threshold for this population. Answer Y/PY if the benchmark is consistent with contemporary published control-arm rates in comparable patients (similar disease stage, prior therapy, biomarker status). Answer N/PN if the benchmark is implausibly low (inflates apparent benefit) or implausibly high (forces a near-impossible bar). NI if no contemporary comparable estimate exists."},
    {"id": "1S.3", "text": "Is the cohort's measured baseline prognostic profile (stage, prior lines, ECOG/performance status, biomarker status, key comorbidities) comparable to that of the benchmark population?", "options": list(_BASIC_NA), "elaboration": "The single-arm proportion is biased upward if the enrolled cohort is more prognostically favourable than the benchmark population (e.g. younger, less heavily pre-treated, biomarker-enriched). Answer Y/PY when measured baseline prognostic factors are comparable. **PN when most-but-not-all prognostic factors look comparable** (V1 collapse of V2's WN). **N when at least one important prognostic factor is materially more favourable in this cohort** (V1 collapse of V2's SN). NA only when no benchmark was identified at 1S.1."},
    {"id": "1S.4", "text": "Did the authors address residual prognostic-mix differences quantitatively (sensitivity analyses, propensity-score adjustment to external controls, prognostic-score stratification, or similar)?", "options": list(_BASIC_NA), "elaboration": "Even when 1S.3 raises concerns, quantitative external-control adjustment can rescue interpretability. Examples include propensity-score weighting against an external real-world cohort, prognostic-score stratification, MAIC, or pre-specified sensitivity analyses showing the conclusion is robust to plausible prognostic differences. Answer Y/PY when such methods were used and reported. N/PN when not addressed. NA only when no benchmark was identified at 1S.1."},
    {"id": "1S.5", "text": "Do negative / falsification controls, external-validity considerations, or other quantitative bias analyses suggest serious uncontrolled selection-prognostic bias?", "options": list(_BASIC_NO_NI), "elaboration": "Analogous to V2's 1A.4 / 1B.5. Answer Y/PY if a falsification analysis (e.g. testing the intervention against an outcome it shouldn't affect) suggested residual bias, or if external-validity checks revealed serious cohort-vs-benchmark mismatch. Answer N if no falsification analysis was performed and no other consideration suggests substantial uncontrolled bias — this is the typical answer."},
]

DOMAIN2_SIGNALS_SINGLE_ARM: list[dict[str, Any]] = [
    {"id": "2S.1", "text": "Was the intervention well-defined (dose, schedule, duration, dose-modifications protocol) at the start of follow-up?", "options": list(_BASIC), "elaboration": "In a single-arm trial there is no comparator misclassification, but the single intervention must be specified precisely enough that the reported result corresponds to a reproducible regimen. Answer Y/PY when dose, schedule, duration, and dose-modification rules (reductions, holds, criteria for discontinuation) are fully reported. Answer N/PN when the intervention is described only at high level (e.g. 'standard chemotherapy')."},
    {"id": "2S.2", "text": "Were dose reductions, holds, and discontinuations recorded and reported?", "options": list(_BASIC), "elaboration": "Recording of treatment delivery is essential for interpreting the single-arm result. Answer Y/PY when reductions/holds/discontinuations are tabulated or otherwise reported. **PN when most exposure modifications were recorded but some detail is missing** (V1 collapse of V2's WN). **N when material exposure detail is missing such that the analyzed 'intervention' is effectively undefined** (V1 collapse of V2's SN)."},
    {"id": "2S.3", "text": "Was the analyzed cohort defined by intended treatment (everyone enrolled, ITT-like) or by received treatment (only those completing ≥X cycles / responding to treatment)?", "options": list(_BASIC), "elaboration": "Defining the analyzed cohort by *received* treatment (per-protocol completers, 'evaluable population') selects for patients who tolerated the intervention well enough to keep receiving it — a strong selection toward responders that inflates the single-arm proportion. **Answer Y only when the primary analysis is explicitly restricted to completers / responders / evaluable population** (V1 collapse of V2's SY — Critical-routing). **Answer PY when the analyzed cohort excludes some enrolled patients for treatment-related reasons but not dominantly** (V1 collapse of V2's WY — Serious-routing). Answer N/PN when all enrolled (or all who received any dose of intervention — modified ITT) are analyzed."},
]


DOMAINS: list[dict[str, Any]] = [
    {"id": 1, "name": "Bias due to confounding", "signals": DOMAIN1_SIGNALS, "signals_single_arm": DOMAIN1_SIGNALS_SINGLE_ARM, "direction_options": ("NA", "Favours experimental", "Favours comparator", "Unpredictable"), "relevant_fields": ["confounders_measured", "adjustment_method", "exposure_definition", "comparator_group", "comparator_historical_reference", "primary_endpoint_prespecified", "immortal_time_bias", "confounding_control", "consecutive_enrolment"]},
    {"id": 2, "name": "Bias in selection of participants into the study", "signals": DOMAIN2_SIGNALS, "signals_single_arm": DOMAIN2_SIGNALS_SINGLE_ARM, "direction_options": ("NA", "Favours experimental", "Favours comparator", "Towards null", "Away from null", "Unpredictable"), "relevant_fields": ["case_source", "control_selection", "sampling_method", "loss_to_follow_up", "immortal_time_bias", "exposure_definition", "intervention_classification", "escalation_scheme", "dose_levels", "expansion_cohort"]},
    {"id": 3, "name": "Bias in classification of interventions", "signals": DOMAIN3_SIGNALS, "direction_options": ("NA", "Favours experimental", "Favours comparator", "Towards null", "Away from null", "Unpredictable"), "relevant_fields": ["exposure_definition", "exposure_measurement", "exposure_ascertainment", "intervention_classification"]},
    {"id": 4, "name": "Bias due to deviations from intended interventions", "signals": DOMAIN4_SIGNALS, "aim_gated": True, "direction_options": ("NA", "Favours experimental", "Favours comparator", "Towards null", "Away from null", "Unpredictable"), "relevant_fields": ["intervention_classification", "loss_to_follow_up", "co_interventions", "adherence"]},
    {"id": 5, "name": "Bias due to missing data", "signals": DOMAIN5_SIGNALS, "direction_options": ("NA", "Favours experimental", "Favours comparator", "Towards null", "Away from null", "Unpredictable"), "relevant_fields": ["loss_to_follow_up", "missing_data_handling", "attrition_rate"]},
    {"id": 6, "name": "Bias in measurement of outcomes", "signals": DOMAIN6_SIGNALS, "direction_options": ("NA", "Favours experimental", "Favours comparator", "Towards null", "Away from null", "Unpredictable"), "relevant_fields": ["outcome_ascertainment", "outcome_definition", "assessor_blinding"]},
    {"id": 7, "name": "Bias in selection of the reported result", "signals": DOMAIN7_SIGNALS, "direction_options": ("NA", "Favours experimental", "Favours comparator", "Towards null", "Away from null", "Unpredictable"), "relevant_fields": ["outcome_definition", "statistical_analysis", "pre_registered_protocol"]},
]


# Single-arm DOMAINS view (used by the SA dispatch in run() and by
# prompt_catalog()). D4 is intentionally absent; the orchestrator marks D4
# as NA in code. D1 and D2 use the SA signal sets; D3, D5, D6, D7 inherit
# the cohort signals unchanged.
DOMAINS_SINGLE_ARM: list[dict[str, Any]] = [
    {"id": 1, "name": "Bias due to confounding — benchmark adequacy + prognostic-mix comparability (single-arm)", "signals": DOMAIN1_SIGNALS_SINGLE_ARM, "direction_options": ("NA", "Favours experimental", "Favours benchmark", "Unpredictable"), "relevant_fields": ["confounders_measured", "adjustment_method", "exposure_definition", "comparator_historical_reference", "primary_endpoint_prespecified", "consecutive_enrolment"]},
    {"id": 2, "name": "Bias in classification of interventions — intervention fidelity + intent-vs-received cohort (single-arm)", "signals": DOMAIN2_SIGNALS_SINGLE_ARM, "direction_options": ("NA", "Favours experimental", "Towards null", "Away from null", "Unpredictable"), "relevant_fields": ["exposure_definition", "exposure_measurement", "intervention_classification", "escalation_scheme", "dose_levels", "expansion_cohort", "adherence"]},
    {"id": 3, "name": "Bias in classification of interventions", "signals": DOMAIN3_SIGNALS, "direction_options": ("NA", "Favours experimental", "Towards null", "Away from null", "Unpredictable"), "relevant_fields": ["exposure_definition", "exposure_measurement", "exposure_ascertainment", "intervention_classification"]},
    {"id": 5, "name": "Bias due to missing data", "signals": DOMAIN5_SIGNALS, "direction_options": ("NA", "Favours experimental", "Towards null", "Away from null", "Unpredictable"), "relevant_fields": ["loss_to_follow_up", "missing_data_handling", "attrition_rate"]},
    {"id": 6, "name": "Bias in measurement of outcomes", "signals": DOMAIN6_SIGNALS, "direction_options": ("NA", "Favours experimental", "Towards null", "Away from null", "Unpredictable"), "relevant_fields": ["outcome_ascertainment", "outcome_definition", "assessor_blinding"]},
    {"id": 7, "name": "Bias in selection of the reported result", "signals": DOMAIN7_SIGNALS, "direction_options": ("NA", "Favours experimental", "Towards null", "Away from null", "Unpredictable"), "relevant_fields": ["outcome_definition", "statistical_analysis", "pre_registered_protocol"]},
]


# ─────────────────────────────────────────────
# Prompts + orchestration
# ─────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing risk of bias in a "
    "non-randomized study of an intervention using the Cochrane ROBINS-I tool "
    "(Version 1, 1 August 2016 cribsheet — Sterne JAC et al., BMJ 2016;355:i4919). "
    "Read the PDF carefully. Answer each signaling question with one of: "
    "Y (yes), PY (probably yes), PN (probably no), N (no), NI (no information). "
    "Some questions are gated on prior answers and additionally allow NA (not "
    "applicable). Provide a 1-2 sentence rationale for each answer, quoting "
    "the paper where possible. Return ONLY a valid JSON object — no preamble, "
    "no markdown fences."
)


def _signals_for_domain(domain: dict[str, Any], aim: str) -> list[dict[str, Any]]:
    """For D4, return only the aim-relevant signals. For other domains, return all signals."""
    if domain.get("aim_gated"):
        if aim == "assignment_to":
            return [s for s in domain["signals"] if s["id"] in ("4.1", "4.2")]
        if aim == "starting_and_adhering":
            return [s for s in domain["signals"] if s["id"] in ("4.3", "4.4", "4.5", "4.6")]
    return domain["signals"]


def _signals_for_domain_single_arm(domain: dict[str, Any]) -> list[dict[str, Any]]:
    """For the single-arm variant: D1 + D2 swap to their SA signal sets;
    D3, D5, D6, D7 keep cohort signals unchanged. D4 is not asked (NA in
    code; never reaches this helper)."""
    if domain.get("id") == 1 and domain.get("signals_single_arm"):
        return domain["signals_single_arm"]
    if domain.get("id") == 2 and domain.get("signals_single_arm"):
        return domain["signals_single_arm"]
    return domain["signals"]


# ─────────────────────────────────────────────
# Aim preflight (§1.1 of the shareable doc) — one LLM call, auto-determines
# the Stage-II aim. Mechanically equivalent to V2's C4 question; only the
# output mapping differs (V1 sets AIMS value, V2 routes Variant A/B).
# ─────────────────────────────────────────────
_AIM_PREFLIGHT_RELEVANT_KEYS = (
    "analysis_framework",
    "primary_outcome_measurement",
    "outcome_definition",
    "outcome_ascertainment",
)


def _build_aim_preflight_prompt(primary_outcome: str,
                                extracted_fields: dict[str, str]) -> str:
    relevant = {k: extracted_fields[k] for k in _AIM_PREFLIGHT_RELEVANT_KEYS
                if extracted_fields.get(k)}
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"

    return f"""You are determining the **aim of study** for a ROBINS-I V1 risk-of-bias assessment of a non-randomized study.

Outcome being assessed: {primary_outcome}

Context (fields already extracted from the paper):
{ctx_json}

ROBINS-I V1 assesses risk of bias against a target estimand. The Stage-II aim of study commits to which estimand the appraisal targets:

- **"assignment_to"** — the analysis estimates the effect of *assignment to* intervention (intention-to-treat). Participants are analysed in the group they were originally assigned to; switches, crossovers, and non-adherence are ignored. This aim is set when the paper reports an as-randomized / as-assigned / mITT analysis as its primary estimate.
- **"starting_and_adhering"** — the analysis estimates the effect of *starting and adhering to* intervention (per-protocol). The analysis is restricted to (or weights toward) participants who actually started and adhered to the assigned intervention, and protocol deviations are accounted for via censoring, IPCW, g-methods, marginal structural models, or instrumental-variable estimation. This aim is set when the paper reports a per-protocol / as-treated / completer analysis as its primary estimate.

**Question.** Which aim does the primary analysis of the paper target for the outcome being assessed?

Elaboration:
- If the paper reports both an ITT and a per-protocol analysis, pick the aim that matches the **headline / primary estimate** for this outcome, not the sensitivity analysis.
- Observational cohort studies typically map to **"starting_and_adhering"** because exposure is defined by who actually started the treatment — unless the analysis explicitly uses an ITT-like exposure definition (e.g. first prescription regardless of refill).
- If the analysis section is genuinely silent on whether protocol deviations are accounted for, default to **"assignment_to"** and note the ambiguity in the rationale.

Return JSON with exactly this shape:
{{
  "aim": "assignment_to|starting_and_adhering",
  "rationale": "1-2 sentences quoting or paraphrasing the analysis-section text that supports the choice"
}}"""


def determine_aim(pdf_bytes: bytes,
                  primary_outcome: str,
                  extracted_fields: dict[str, str],
                  ) -> dict[str, Any]:
    """V1 aim preflight — single LLM call returns the aim payload.

    Returns ``{"aim": "...", "rationale": "..."}``. The aim is one of
    ``AIMS = ("assignment_to", "starting_and_adhering")``.
    Falls back to "assignment_to" when the LLM returns an unrecognized value
    (matches the cribsheet's ambiguous-methods guidance).
    """
    prompt = _build_aim_preflight_prompt(primary_outcome, extracted_fields)
    raw = _call_with_pdf(pdf_bytes, prompt, max_tokens=512)
    aim_raw = str(raw.get("aim", "")).strip().lower()
    if aim_raw not in AIMS:
        logger.warning("ROBINS-I V1 aim preflight: invalid LLM answer %r — defaulting to 'assignment_to'", aim_raw)
        aim_raw = "assignment_to"
    rationale = str(raw.get("rationale", "")).strip()
    return {"aim": aim_raw, "rationale": rationale}


# ─────────────────────────────────────────────
# Benchmark preflight (single-arm path) — replaces the aim preflight when
# the variant is single_arm. Mirrors V2's _build_preflight_prompt_single_arm
# (robins_i.py:1431) but stays in V1's 4-token preflight scale (Y/PY/PN/N —
# preflight has no NI to match V1's 1.1 convention).
# ─────────────────────────────────────────────
_BENCHMARK_PREFLIGHT_RELEVANT_KEYS = (
    "primary_endpoint_prespecified",
    "inclusion_exclusion_criteria",
    "comparator_historical_reference",
    "consecutive_enrolment",
    "outcome_definition",
    "outcome_ascertainment",
    "primary_outcome_measurement",
    "analysis_framework",
)


def _build_benchmark_preflight_prompt(study_type: str,
                                      primary_outcome: str,
                                      extracted_fields: dict[str, str]) -> str:
    relevant = {k: extracted_fields[k] for k in _BENCHMARK_PREFLIGHT_RELEVANT_KEYS
                if extracted_fields.get(k)}
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"

    return f"""You are performing the **Preliminary Considerations** screen of ROBINS-I V1 (adapted for single-arm / uncontrolled designs) on an uncontrolled clinical study.

Study type: {study_type}
Outcome being assessed: {primary_outcome}

Context (fields already extracted from the paper):
{ctx_json}

This study has **no comparator group** — every participant received the intervention. Risk of bias is therefore not about confounding-by-indication (which requires a comparator) but about whether the implied benchmark for interpretation (historical control rate, performance criterion, or null hypothesis with a decision rule) was pre-specified and reasonable.

Answer four preliminary-consideration questions:

**B1-SA. Did the authors pre-specify a quantitative benchmark (historical control rate, performance criterion, or null hypothesis with a statistical decision rule) against which the single-arm result is being judged?**
Options: Y / PY / PN / N
Elaboration: Examples of pre-specified benchmarks include: a Simon two-stage design with a numeric response-rate threshold; an FDA accelerated-approval ORR threshold cited in the protocol; a published historical control rate that the trial was powered against. Answer Y/PY if such a benchmark is clearly identifiable in the protocol/SAP/methods. Answer N/PN if no benchmark is stated, or if the benchmark looks post-hoc.

**B2-SA. (Only if N/PN to B1-SA) Is the absence of any pre-specified benchmark severe enough that the single-arm proportion is uninterpretable for causal inference?**
Options: Y / PY / PN / N
Elaboration: Y/PY when the result is reported as a bare proportion with no reference point at all, such that any interpretation depends entirely on post-hoc comparisons. This short-circuits to Critical risk of bias.

**B3. Was the method of measuring the outcome inappropriate?**
Options: Y / PY / PN / N
Elaboration: Identify methods of outcome measurement unsuitable for the outcome they evaluate. Answer Y/PY if (1) important outcome values fall outside levels detectable by the method; (2) the instrument has demonstrated poor reliability/validity; or (3) measurement methods are not interpretable for the question. In most circumstances answer N/PN.

**C4. Did the analysis account for protocol deviations during follow-up (e.g. participants who discontinued the intervention or switched to another therapy)?**
Options: No (the analysis is an intention-to-treat / modified-ITT analysis of all enrolled) / Yes (the analysis is a per-protocol analysis restricted to those who completed treatment / responded)
Note: For single-arm studies this answer is recorded as metadata only. It informs interpretation of D2-SA question 2S.3 but does NOT swap any variant — single-arm papers always use the single_arm variant of D1 and D2.

Return JSON with exactly this shape:
{{
  "B1": "Y|PY|PN|N",
  "B1_rationale": "1-2 sentences quoting the paper (answer to B1-SA)",
  "B2": "Y|PY|PN|N|NA",
  "B2_rationale": "1-2 sentences (or 'NA' if B1 was Y/PY)",
  "B3": "Y|PY|PN|N",
  "B3_rationale": "1-2 sentences quoting the paper",
  "C4": "No|Yes",
  "C4_rationale": "1-2 sentences explaining whether the analysis is ITT-like or per-protocol-like"
}}"""


def run_benchmark_preflight(pdf_bytes: bytes,
                            study_type: str,
                            primary_outcome: str,
                            extracted_fields: dict[str, str]) -> dict[str, Any]:
    """Run the V1 single-arm benchmark preflight. Mirrors V2's
    ``run_preflight`` return shape so the orchestrator + frontend code can
    consume both uniformly.

    Returns a dict with::

        {
          "B1": "Y|PY|PN|N",
          "B2": "Y|PY|PN|N|NA",
          "B3": "Y|PY|PN|N",
          "C4": "No|Yes",
          "rationales": {"B1": ..., "B2": ..., "B3": ..., "C4": ...},
          "screening_decision": "proceed" | "critical",
          "screening_reason": str,
          "variant": "single_arm",
        }
    """
    prompt = _build_benchmark_preflight_prompt(
        study_type, primary_outcome, extracted_fields)
    raw = _call_with_pdf(pdf_bytes, prompt, max_tokens=2048)

    def _opt(key: str, default: str = "N", allowed: tuple = ("Y", "PY", "PN", "N")) -> str:
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

    if b2 in ("Y", "PY"):
        return {
            "B1": b1, "B2": b2, "B3": b3, "C4": c4,
            "rationales": rationales,
            "screening_decision": "critical",
            "screening_reason": (
                "B2-SA: Absence of any pre-specified benchmark is severe "
                "enough that the single-arm proportion is uninterpretable "
                "for causal inference."
            ),
            "variant": "single_arm",
        }
    if b3 in ("Y", "PY"):
        return {
            "B1": b1, "B2": b2, "B3": b3, "C4": c4,
            "rationales": rationales,
            "screening_decision": "critical",
            "screening_reason": (
                "B3: The method of measuring the outcome is inappropriate."
            ),
            "variant": "single_arm",
        }

    return {
        "B1": b1, "B2": b2, "B3": b3, "C4": c4,
        "rationales": rationales,
        "screening_decision": "proceed",
        "screening_reason": "",
        "variant": "single_arm",
    }


def build_domain_prompt(domain: dict[str, Any],
                        study_type: str,
                        primary_outcome: str,
                        extracted_fields: dict[str, str],
                        aim: str = "assignment_to",
                        target_pico: dict[str, str] | None = None,
                        variant: str = "cohort") -> str:
    """Per-domain prompt for ROBINS-I V1.

    ``variant``: ``"cohort"`` (default — original V1 7-domain layout) or
    ``"single_arm"`` (D1/D2 use the SA signal sets; aim is irrelevant since
    D4 is skipped in code).
    """
    if variant == "single_arm":
        signals = _signals_for_domain_single_arm(domain)
    else:
        signals = _signals_for_domain(domain, aim)

    relevant = {k: extracted_fields[k] for k in domain.get("relevant_fields", [])
                if extracted_fields.get(k)}
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"

    aim_block = ""
    if variant != "single_arm" and domain.get("aim_gated"):
        aim_block = (
            f"\nAim of study: {aim}\n"
            '- "assignment_to" → answer signaling questions 4.1 and 4.2 only.\n'
            '- "starting_and_adhering" → answer signaling questions 4.3 through 4.6 only.\n'
        )

    pico_block = ""
    if target_pico:
        pico_block = "\nTarget PICO (user-supplied):\n" + json.dumps(target_pico, indent=2) + "\n"

    variant_block = ""
    if variant == "single_arm":
        variant_block = (
            "\nVariant: single_arm — this is an uncontrolled / single-arm design with no comparator. "
            "Domain 1 is reframed as benchmark adequacy + prognostic-mix comparability; Domain 2 is reframed as "
            "intervention fidelity + intent-vs-received cohort definition. Domain 4 (Deviations from intended "
            "interventions) is NOT assessed for single-arm — it has no meaning without a comparator group.\n"
        )

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
    direction_options = domain.get("direction_options", ("NA",))
    shape += f'  "direction_of_bias": "{"|".join(direction_options)}"\n'
    shape += "}"

    return f"""Assess **Domain {domain['id']} — {domain['name']}** for the study described in the attached PDF using the ROBINS-I V1 tool (1 August 2016 cribsheet).

Study type: {study_type}
Outcome being assessed: {primary_outcome}
{aim_block}{variant_block}{pico_block}
Context (fields already extracted from the paper):
{ctx_json}

Signaling questions:
{questions_block}

Return a JSON object with exactly this shape:
{shape}

Notes on ROBINS-I V1:
- The signal vocabulary is **Y / PY / PN / N / NI** (5 tokens). For each question, answer based on what the paper says about that specific question — **do NOT try to determine whether a question is gated out by the cribsheet's cascading structure**. Python applies the cascade rules after you answer and will set `NA` for any question that should be gated out. Just answer each question independently based on its own text.
- The judgement scale is **Low / Moderate / Serious / Critical / No information** (5 levels). The code maps your signal answers to a judgement — answer the signaling questions only.
- Answer N (or PN) when the paper gives enough information to rule out the problem; NI only when the paper is silent.
- Rationales must be short (1-2 sentences) and quote the paper verbatim where possible."""


def _assess_domain(pdf_bytes: bytes, domain: dict[str, Any],
                   study_type: str, primary_outcome: str,
                   extracted_fields: dict[str, str],
                   aim: str = "assignment_to",
                   target_pico: dict[str, str] | None = None,
                   variant: str = "cohort") -> dict[str, Any]:
    prompt = build_domain_prompt(
        domain, study_type, primary_outcome, extracted_fields, aim, target_pico,
        variant=variant)
    raw = _call_with_pdf(pdf_bytes, prompt, max_tokens=8192)

    if variant == "single_arm":
        signals_for_this = _signals_for_domain_single_arm(domain)
    else:
        signals_for_this = _signals_for_domain(domain, aim)
    signals: dict[str, str] = {}
    rationales: dict[str, str] = {}
    for sig in signals_for_this:
        sid = sig["id"]
        ans = str(raw.get(sid, "NI")).strip().upper()
        allowed = set(sig["options"])
        if ans not in allowed:
            logger.warning("ROBINS-I V1 domain %s question %s: invalid answer %r — defaulting to NI",
                           domain["id"], sid, ans)
            ans = "NI" if "NI" in allowed else next(iter(allowed))
        signals[sid] = ans
        rationales[sid] = str(raw.get(f"{sid}_rationale", "")).strip()

    # Python-side cascade enforcement. The cohort cascades (D1/D2/D4/D5) only
    # apply when variant == "cohort"; the SA D1/D2 signal sets have no
    # cribsheet cascade (every SA signal is independently answered).
    if variant != "single_arm":
        pre_cascade = dict(signals)
        signals = enforce_cascade(domain["id"], signals, aim=aim)
        overrides = {sid: (pre_cascade[sid], signals[sid])
                     for sid in signals
                     if sid in pre_cascade and pre_cascade[sid] != signals[sid]}
        if overrides:
            logger.debug("ROBINS-I V1 D%s cascade enforcement overrode LLM answers: %r",
                         domain["id"], overrides)

    if variant == "single_arm":
        judgement = DOMAIN_JUDGES_SINGLE_ARM[domain["id"]](signals)
    elif domain["id"] == 4:
        judgement = domain4_judge(signals, aim=aim)
    else:
        judgement = DOMAIN_JUDGES[domain["id"]](signals)

    direction = str(raw.get("direction_of_bias", "NA")).strip() or "NA"

    out: dict[str, Any] = {
        "signals": signals,
        "rationales": rationales,
        "judgement": judgement,
        "direction": direction,
    }
    if variant != "single_arm" and domain.get("aim_gated"):
        out["aim"] = aim
    if variant == "single_arm" and domain["id"] in (1, 2):
        out["variant"] = "single_arm"
    return out


def run(pdf_bytes: bytes,
        extracted_fields: dict[str, str],
        classification: dict[str, str],
        primary_outcome: str,
        progress: Callable[[int], None] | None = None,
        target_pico: dict[str, str] | None = None,
        aim: str | None = None,
        ) -> tuple[dict[str, Any], str, str]:
    """Run ROBINS-I V1 against a non-randomized study.

    Pipeline (cohort variant — default):
      1. Aim preflight (§1.1) — single LLM call, auto-determines the Stage-II
         aim from the paper. Skipped when ``aim`` is explicitly supplied.
      2. Per-domain assessments — 7 domains, one LLM call each.

    Pipeline (single-arm variant — when
    ``classification["study_type"] in SINGLE_ARM_STUDY_TYPES``):
      1. Benchmark preflight (B1-SA/B2-SA/B3) — single LLM call. If
         B2-SA=Y/PY or B3=Y/PY, short-circuit to Critical (D1-D7 skipped).
      2. Per-domain assessments — 6 active domains (D1, D2, D3, D5, D6, D7);
         D4 is marked NA in code with no LLM call. D1 + D2 use the SA signal
         sets + SA judges. D3, D5, D6, D7 reuse cohort signals + judges.

    Returns ``(domain_results, overall_judgement, overall_direction)``.

    For the cohort variant, ``domain_results["aim_preflight"]`` carries
    ``{"aim", "rationale"}``. For the single-arm variant,
    ``domain_results["preflight"]`` carries the full benchmark preflight
    payload (B1/B2/B3/C4 + rationales + variant + screening decision +
    reason) — same shape V2 uses, so the frontend detail-modal path is
    shared.
    """
    study_type = classification.get("study_type", "Cohort Study")
    is_single_arm = study_type in SINGLE_ARM_STUDY_TYPES

    if progress:
        try:
            progress(0)
        except Exception:
            pass

    # ─── Single-arm path ─────────────────────────────────────────────
    if is_single_arm:
        if aim is not None:
            logger.info("ROBINS-I V1: ignoring aim=%r for single-arm study type %r "
                        "(aim is irrelevant without a comparator)", aim, study_type)
        preflight = run_benchmark_preflight(
            pdf_bytes, study_type, primary_outcome, extracted_fields)
        domain_results: dict[str, Any] = {"preflight": preflight, "aim_preflight": None}

        # Stage 2 — screening short-circuit (mirrors V2's pattern)
        if preflight["screening_decision"] == "critical":
            return domain_results, "Critical", "Unpredictable"

        # Stage 3 — six active domains; D4 set to NA in code
        for domain in DOMAINS_SINGLE_ARM:
            if progress:
                try:
                    progress(domain["id"])
                except Exception:
                    pass
            result = _assess_domain(
                pdf_bytes, domain, study_type, primary_outcome,
                extracted_fields, aim="assignment_to", target_pico=target_pico,
                variant="single_arm",
            )
            result["id"] = domain["id"]
            result["name"] = domain["name"]
            domain_results[str(domain["id"])] = result

        # D4 = NA — V2 retired the deviations domain entirely; its concerns
        # are folded into D2-SA's 2S.3 (intent-vs-received cohort definition).
        domain_results["4"] = {
            "id": 4,
            "name": "Bias due to deviations from intended interventions",
            "signals": {},
            "rationales": {},
            "judgement": "NA",
            "direction": "NA",
            "reason": (
                "Not applicable to single-arm trials — V2 retired this "
                "domain entirely; intent-vs-received cohort definition is "
                "assessed in Domain 2-SA (question 2S.3)."
            ),
        }

        domain_judgements = [domain_results[str(d["id"])]["judgement"]
                             for d in DOMAINS_SINGLE_ARM]
        overall = robins_i_v1_overall(domain_judgements)

        dirs = [domain_results[str(d["id"])]["direction"]
                for d in DOMAINS_SINGLE_ARM
                if domain_results[str(d["id"])]["direction"] not in ("", "NA")]
        if not dirs:
            overall_direction = "NA"
        else:
            counts = Counter(dirs).most_common()
            if len(counts) > 1 and counts[0][1] == counts[1][1]:
                overall_direction = "Unpredictable"
            else:
                overall_direction = counts[0][0]

        return domain_results, overall, overall_direction

    # ─── Cohort path ─────────────────────────────────────────────────
    # Stage 1 — aim preflight
    if aim is None:
        aim_payload = determine_aim(pdf_bytes, primary_outcome, extracted_fields)
        aim = aim_payload["aim"]
    else:
        if aim not in AIMS:
            raise ValueError(f"aim must be None or one of {AIMS}; got {aim!r}")
        aim_payload = {"aim": aim, "rationale": "Manually supplied; preflight skipped."}

    domain_results = {"aim_preflight": aim_payload}

    # Stage 2 — seven domains
    for domain in DOMAINS:
        if progress:
            try:
                progress(domain["id"])
            except Exception:
                pass
        result = _assess_domain(
            pdf_bytes, domain, study_type, primary_outcome,
            extracted_fields, aim=aim, target_pico=target_pico,
        )
        result["id"] = domain["id"]
        result["name"] = domain["name"]
        domain_results[str(domain["id"])] = result

    # Aggregate overall judgement
    domain_judgements = [domain_results[str(d["id"])]["judgement"] for d in DOMAINS]
    overall = robins_i_v1_overall(domain_judgements)

    # Direction of bias — modal across domains; ties → Unpredictable
    dirs = [domain_results[str(d["id"])]["direction"]
            for d in DOMAINS
            if domain_results[str(d["id"])]["direction"] not in ("", "NA")]
    if not dirs:
        overall_direction = "NA"
    else:
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
    """Return prompts + decision-tree source for the developer icon."""
    judge_funcs = {
        1: domain1_judge, 2: domain2_judge, 3: domain3_judge,
        4: domain4_judge, 5: domain5_judge, 6: domain6_judge, 7: domain7_judge,
    }
    cascade_funcs = {
        1: enforce_cascade_d1, 2: enforce_cascade_d2,
        4: enforce_cascade_d4, 5: enforce_cascade_d5,
    }
    domains_meta: list[dict[str, Any]] = []
    for dom in DOMAINS:
        # Build a representative per-domain prompt with placeholder context
        # for the developer view. Aim only matters for D4; default to ITT
        # which gives the smaller (2-question) view.
        prompt_template = build_domain_prompt(
            dom, "Cohort Study", "<primary_outcome>", {}, aim="assignment_to",
        )
        entry = {
            "id": dom["id"],
            "name": dom["name"],
            "signals": dom["signals"],
            "relevant_fields": dom.get("relevant_fields", []),
            "direction_options": list(dom.get("direction_options", ("NA",))),
            "aim_gated": dom.get("aim_gated", False),
            "prompt_template": prompt_template,
            "decision_tree_code": inspect.getsource(judge_funcs[dom["id"]]),
        }
        if dom["id"] in cascade_funcs:
            entry["cascade_code"] = inspect.getsource(cascade_funcs[dom["id"]])
        domains_meta.append(entry)

    # Single-arm variant developer-view payload — mirrors the cohort entry
    # shape but uses the SA signal sets + SA judges for D1 + D2 (D3, D5, D6,
    # D7 reuse cohort signals + judges; D4 is set to NA in code).
    sa_judge_funcs = {
        1: domain1_single_arm_judge,
        2: domain2_single_arm_judge,
        3: domain3_judge,
        5: domain5_judge,
        6: domain6_judge,
        7: domain7_judge,
    }
    sa_domains_meta: list[dict[str, Any]] = []
    for dom in DOMAINS_SINGLE_ARM:
        prompt_template = build_domain_prompt(
            dom, "Single-Arm Trial", "<primary_outcome>", {},
            aim="assignment_to", variant="single_arm",
        )
        sa_domains_meta.append({
            "id": dom["id"],
            "name": dom["name"],
            "signals": dom["signals"],
            "relevant_fields": dom.get("relevant_fields", []),
            "direction_options": list(dom.get("direction_options", ("NA",))),
            "prompt_template": prompt_template,
            "decision_tree_code": inspect.getsource(sa_judge_funcs[dom["id"]]),
        })
    sa_domains_meta.append({
        "id": 4,
        "name": "Bias due to deviations from intended interventions",
        "signals": [],
        "relevant_fields": [],
        "direction_options": ["NA"],
        "prompt_template": None,
        "decision_tree_code": (
            "# D4 = NA for single-arm runs (no LLM call). V2 retired this\n"
            "# domain entirely; intent-vs-received cohort definition is\n"
            "# folded into D2-SA's 2S.3."
        ),
        "na_in_code": True,
    })

    return {
        "tool": "ROBINS-I V1 (1 August 2016)",
        "system_prompt": _SYSTEM_PROMPT,
        "signal_options": list(SIGNAL_OPTIONS),
        "judgements": list(JUDGEMENTS),
        "aims": list(AIMS),
        "aim_preflight_prompt": _build_aim_preflight_prompt(
            "<primary_outcome>", {}),
        "aim_preflight_code": inspect.getsource(determine_aim),
        "domains": domains_meta,
        "overall_algorithm_code": inspect.getsource(robins_i_v1_overall),
        # Single-arm variant — project-specific extension mirroring V2's
        # single_arm adaptation for V1's 5-token vocab. Developer-view UI
        # detects these keys and renders a parallel single-arm section.
        "single_arm_study_types": sorted(SINGLE_ARM_STUDY_TYPES),
        "domain_1_low_label_single_arm": LOW_D1_SA,
        "benchmark_preflight_prompt": _build_benchmark_preflight_prompt(
            "Single-Arm Trial", "<primary_outcome>", {}),
        "benchmark_preflight_code": inspect.getsource(run_benchmark_preflight),
        "domains_single_arm": sa_domains_meta,
    }
