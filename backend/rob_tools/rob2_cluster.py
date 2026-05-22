"""Cochrane RoB 2 — Risk of Bias for cluster-randomized trials (RoB 2 CRT).

Encodes the official Cochrane RoB 2 cluster-randomized-trial extension cribsheet
(``20210318_RoB_2_cribsheet_cluster_trial.pdf`` — version of 18 March 2021):

- ``DOMAINS_ASSIGNMENT`` / ``DOMAINS_ADHERING`` — 6 domains × their signaling
  questions + elaborations, differing only in Domain 2.
- ``rob2_cluster_domain*_judge(signals)`` — pure-Python decision trees
  transcribed verbatim from the cribsheet flowcharts.
- ``rob2_cluster_overall(domain_judgements)`` — aggregate per p.22.
- ``run(pdf_bytes, fields, classification, assessed_outcome, progress, ...)`` —
  per-domain LLM calls via the annotator's ``_call_with_pdf`` pipeline, then
  local decision-tree evaluation.

Six domains (vs. five for parallel-group RoB 2):

  D1a  Bias arising from the randomization process
  D1b  Bias arising from the timing of identification or recruitment of
       participants in a cluster-randomized trial            ← NEW (cluster-only)
  D2   Bias due to deviations from the intended interventions
  D3   Bias due to missing outcome data
  D4   Bias in measurement of the outcome
  D5   Bias in selection of the reported result

**Domain 2 has two variants**, selected per run by the review team's aim:
``assignment`` (the intention-to-treat effect — 8 signals: 2.1a, 2.1b, 2.2-2.7)
and ``adhering`` (the per-protocol effect — 6 signals: 2.1, 2.2-2.6). The
cribsheet flowcharts for the two variants are distinct (pp. 12 and 14).

Signaling-question answers are Y / PY / PN / N / NI, plus **NA** for conditional
questions (the CRT cribsheet lists NA as a response option). Domain judgements
are "Low" / "Some concerns" / "High".

The 2021 CRT flowcharts route several paths differently from the 2019
parallel-group cribsheet in :mod:`backend.rob_tools.rob2` (e.g. p.6 — a
concealed-but-not-random sequence is *Some concerns* here, and D3/D4 split 3.1
and 4.3 into 3.1a/3.1b and 4.3a/4.3b). The decision trees are therefore
transcribed independently. Only Domain 5 and the overall aggregation are
genuinely identical to standard RoB 2 and delegate to that module.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from ..annotator import _call_with_pdf
from . import rob2

logger = logging.getLogger("rubricgen")


# Y / PY / PN / N / NI plus NA — NA is offered for conditional questions and is
# a meaningful routing token in the Domain 2 adhering flowchart.
SIGNAL_OPTIONS = ("Y", "PY", "PN", "N", "NI", "NA")

_BASE_OPTS = ["Y", "PY", "PN", "N", "NI"]
_NA_OPTS = ["NA", "Y", "PY", "PN", "N", "NI"]


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


def _g(signals: dict[str, str], sid: str) -> str:
    """Read a signal answer, mapping a stray 'NA' to 'NI'.

    Used by every tree EXCEPT the Domain 2 adhering tree. In the assignment,
    1a, 1b, 3 and 4 flowcharts a question is consulted only on the branch
    where it is applicable, so a well-formed assessment never sends 'NA' to
    those reads — 'NA' there is an LLM slip, and 'NI' is the safe equivalent.
    The adhering tree reads raw because its flowchart (p.14) groups 'NA' with
    the no-concern branches on purpose.
    """
    a = str(signals.get(sid, "NI")).strip().upper()
    return "NI" if a == "NA" else a


def rob2_cluster_domain1a_judge(signals: dict[str, str]) -> str:
    """Domain 1a (randomization process) — RoB 2 CRT cribsheet p.6.

    Inputs: ``{"1a.1": ..., "1a.2": ..., "1a.3": ...}``.
    Output: "Low" / "Some concerns" / "High".
    """
    q1 = _g(signals, "1a.1")
    q2 = _g(signals, "1a.2")
    q3 = _g(signals, "1a.3")

    if _no(q2):
        return "High"
    if q2 == "NI":
        return "High" if _yes(q3) else "Some concerns"
    # 1a.2 Y/PY (concealed)
    if _no(q1):
        return "Some concerns"
    # 1a.1 Y/PY/NI
    return "Some concerns" if _yes(q3) else "Low"


def rob2_cluster_domain1b_judge(signals: dict[str, str]) -> str:
    """Domain 1b (timing of identification/recruitment of participants) — p.9.

    The cluster-specific domain. Inputs: ``{"1b.1", "1b.2", "1b.3"}``.
    """
    q1 = _g(signals, "1b.1")
    q2 = _g(signals, "1b.2")
    q3 = _g(signals, "1b.3")

    if _yes(q1):
        # All participants identified/recruited before randomization →
        # identification/recruitment bias is not possible.
        return "Low"
    # 1b.1 N/PN/NI → 1b.2
    if _yes(q2):
        return "High"
    if _no(q2):
        return "Some concerns" if _yes(q3) else "Low"
    # 1b.2 NI → 1b.3
    return "High" if _yes(q3) else "Some concerns"


def rob2_cluster_domain2_assignment_judge(signals: dict[str, str]) -> str:
    """Domain 2 — effect of ASSIGNMENT to intervention (ITT) — p.12.

    Two parts combined by the cribsheet criteria:
      Low    iff Part 1 = Low AND Part 2 = Low
      High   iff either part = High
      Some   otherwise
    Part 1 uses 2.1a/2.1b/2.2 then the 2.3 → 2.4 → 2.5 chain; Part 2 uses 2.6-2.7.
    """
    q21a = _g(signals, "2.1a")
    q21b = _g(signals, "2.1b")
    q22 = _g(signals, "2.2")
    q23 = _g(signals, "2.3")
    q24 = _g(signals, "2.4")
    q25 = _g(signals, "2.5")
    q26 = _g(signals, "2.6")
    q27 = _g(signals, "2.7")

    # ── Part 1 — awareness gate, then 2.3 → 2.4 → 2.5 ──
    if _no(q21a):
        # Participants not aware they were in a trial → only carers/deliverers
        # (2.2) can introduce trial-context deviations.
        aware = not _no(q22)
    else:
        # 2.1a Y/PY/NI → consult 2.1b + 2.2; both N/PN means "not aware".
        aware = not (_no(q21b) and _no(q22))

    if not aware:
        part1 = "Low"
    elif _no(q23):
        part1 = "Low"
    elif q23 == "NI":
        part1 = "Some concerns"
    elif _no(q24):
        # 2.3 Y/PY → 2.4 N/PN → deviations did not affect the outcome.
        part1 = "Low"
    else:
        # 2.4 Y/PY/NI → 2.5: balanced → Some concerns, else High.
        part1 = "Some concerns" if _yes(q25) else "High"

    # ── Part 2 — appropriate ITT analysis ──
    if _yes(q26):
        part2 = "Low"
    elif _no(q27):
        part2 = "Some concerns"
    else:
        # 2.7 Y/PY/NI → substantial impact of analysing the wrong groups.
        part2 = "High"

    if part1 == "High" or part2 == "High":
        return "High"
    if part1 == "Low" and part2 == "Low":
        return "Low"
    return "Some concerns"


def rob2_cluster_domain2_adhering_judge(signals: dict[str, str]) -> str:
    """Domain 2 — effect of ADHERING to intervention (per-protocol) — p.14.

    Inputs: ``{"2.1", "2.2", "2.3", "2.4", "2.5", "2.6"}``.

    'NA' is a meaningful routing token here — the p.14 flowchart groups it with
    the no-concern branches ("NA/Y/PY" out of 2.3; "NA/N/PN" out of 2.4/2.5).
    This tree therefore reads raw, unlike the other cluster trees.
    """
    q21 = str(signals.get("2.1", "NI")).strip().upper()
    q22 = str(signals.get("2.2", "NI")).strip().upper()
    q23 = str(signals.get("2.3", "NI")).strip().upper()
    q24 = str(signals.get("2.4", "NI")).strip().upper()
    q25 = str(signals.get("2.5", "NI")).strip().upper()
    q26 = str(signals.get("2.6", "NI")).strip().upper()

    def _node_2425() -> str:
        # Both 2.4 and 2.5 NA/N/PN → Low; otherwise consult 2.6.
        if q24 in ("NA", "N", "PN") and q25 in ("NA", "N", "PN"):
            return "Low"
        return "Some concerns" if _yes(q26) else "High"

    if _no(q21) and _no(q22):
        # Neither participants nor carers/deliverers aware → straight to 2.4/2.5.
        return _node_2425()
    # Either 2.1 or 2.2 Y/PY/NI → consult 2.3.
    if q23 in ("NA", "Y", "PY"):
        return _node_2425()
    # 2.3 N/PN/NI → consult 2.6 directly.
    return "Some concerns" if _yes(q26) else "High"


def rob2_cluster_domain3_judge(signals: dict[str, str]) -> str:
    """Domain 3 (missing outcome data) — cribsheet p.16.

    3.1 is split into 3.1a (data available for all recruiting clusters) and
    3.1b (data available for all/nearly all participants within clusters).
    """
    q31a = _g(signals, "3.1a")
    q31b = _g(signals, "3.1b")
    q32 = _g(signals, "3.2")
    q33 = _g(signals, "3.3")
    q34 = _g(signals, "3.4")

    if _yes(q31a) and _yes(q31b):
        return "Low"
    # Either 3.1a/3.1b N/PN/NI → 3.2 evidence the result is not biased.
    if _yes(q32):
        return "Low"
    # 3.2 N/PN → 3.3 could missingness depend on the true value?
    if _no(q33):
        return "Low"
    # 3.3 Y/PY/NI → 3.4 likely that it depended?
    if _no(q34):
        return "Some concerns"
    return "High"


def rob2_cluster_domain4_judge(signals: dict[str, str]) -> str:
    """Domain 4 (measurement of the outcome) — cribsheet p.19.

    4.3 is split into 4.3a (assessors aware a trial was taking place — relevant
    for participant-reported outcomes) and 4.3b (assessors aware of the
    intervention received).
    """
    q41 = _g(signals, "4.1")
    q42 = _g(signals, "4.2")
    q43a = _g(signals, "4.3a")
    q43b = _g(signals, "4.3b")
    q44 = _g(signals, "4.4")
    q45 = _g(signals, "4.5")

    if _yes(q41):
        return "High"
    if _yes(q42):
        return "High"
    # 4.2 N/PN keeps the Low outcome reachable; 4.2 NI floors the branch at
    # Some concerns (cribsheet p.19 flowchart).
    floor = "Low" if _no(q42) else "Some concerns"
    if _no(q43a):
        return floor
    if _no(q43b):
        return floor
    if _no(q44):
        return floor
    if _no(q45):
        return "Some concerns"
    return "High"


def rob2_cluster_domain5_judge(signals: dict[str, str]) -> str:
    """Domain 5 (selection of the reported result) — cribsheet p.21.

    The CRT cribsheet states 5.1/5.2/5.3 and the flowchart are "as for
    individually-randomized trials", so this delegates to the standard tool.
    """
    return rob2.rob2_domain5_judge(signals)


def rob2_cluster_overall(domain_judgements: list[str]) -> str:
    """Overall RoB judgement — cribsheet p.22. Identical to standard RoB 2."""
    return rob2.rob2_overall(domain_judgements)


DOMAIN_JUDGES_ASSIGNMENT: dict[Any, Callable[[dict[str, str]], str]] = {
    "1a": rob2_cluster_domain1a_judge,
    "1b": rob2_cluster_domain1b_judge,
    2: rob2_cluster_domain2_assignment_judge,
    3: rob2_cluster_domain3_judge,
    4: rob2_cluster_domain4_judge,
    5: rob2_cluster_domain5_judge,
}
DOMAIN_JUDGES_ADHERING: dict[Any, Callable[[dict[str, str]], str]] = {
    **DOMAIN_JUDGES_ASSIGNMENT,
    2: rob2_cluster_domain2_adhering_judge,
}


def judges_for(aim: str) -> dict[Any, Callable[[dict[str, str]], str]]:
    """Return the domain-id → judge map for the chosen analysis aim."""
    return DOMAIN_JUDGES_ADHERING if aim == "adhering" else DOMAIN_JUDGES_ASSIGNMENT


# ─────────────────────────────────────────────
# Domain definitions — signaling questions + elaborations
# ─────────────────────────────────────────────
_DOMAIN_1A: dict[str, Any] = {
    "id": "1a",
    "name": "Bias arising from the randomization process",
    "relevant_fields": ["cluster_unit", "n_clusters", "icc_reported",
                         "allocation_concealment", "baseline_balance"],
    "signals": [
        {"id": "1a.1",
         "text": "Was the allocation sequence random?",
         "options": list(_BASE_OPTS),
         "elaboration": "Considerations are mostly the same as for individually-randomized trials. The unit of allocation is the cluster. Answer 'No' for non-random methods that might be seen in cluster-randomized trials, including those based on geography (e.g., clusters near the main research centre allocated to the intervention and those further away to the control), alternation, or any systematic or judgement-based method."},
        {"id": "1a.2",
         "text": "Was the allocation sequence concealed until clusters were enrolled and assigned to interventions?",
         "options": list(_BASE_OPTS),
         "elaboration": "As for individually-randomized trials, but applied to clusters. Answer 'Yes' if clusters were enrolled and their identities fixed before allocation was revealed (e.g., remote/central allocation, or all clusters identified and enrolled before the sequence was generated/applied). Answer 'No' if those enrolling clusters could foresee assignments."},
        {"id": "1a.3",
         "text": "Did baseline differences between intervention groups suggest a problem with the randomization process?",
         "options": list(_BASE_OPTS),
         "elaboration": "Differences compatible with chance do not lead to a risk of bias. Answer 'No' if observed imbalances are compatible with chance or are likely due to identification/recruitment bias (assessed in Domain 1b). Because most cluster-randomized trials randomize few clusters, substantial chance imbalances in cluster or participant characteristics are more common than in individually-randomized trials. Answer 'Yes' if imbalances indicate a problem with the randomization process: (1) substantial differences between the numbers of clusters per arm vs the intended allocation ratio; (2) a substantial excess of statistically-significant differences in baseline cluster characteristics beyond chance; (3) imbalance in one or more baseline outcome measures very unlikely to be due to chance and large enough to bias the effect estimate; or (4) excessive similarity in baseline characteristics not compatible with chance. Answer 'No information' when no useful baseline data are reported. Do not let the answer here influence 1a.1 or 1a.2 — if adequate randomization methods are reported, answer those on that basis and raise any imbalance concern here."},
    ],
}

_DOMAIN_1B: dict[str, Any] = {
    "id": "1b",
    "name": "Bias arising from the timing of identification or recruitment of participants",
    "relevant_fields": ["recruitment_after_randomization", "contamination_risk",
                         "n_clusters", "cluster_unit"],
    "signals": [
        {"id": "1b.1",
         "text": "Were all the individual participants identified and recruited (if appropriate) before randomization of clusters?",
         "options": list(_BASE_OPTS),
         "elaboration": "Answer 'Yes' if (1) all participants were identified and recruited before the clusters were randomized, or (2) individual participants were not recruited at all but all were identified before randomization — in these cases identification/recruitment bias is not possible. Answer 'No' if (1) some or all participants were identified or recruited after randomization, or (2) there are any clusters in which no participants were recruited (empty clusters)."},
        {"id": "1b.2",
         "text": "Is it likely that selection of individual participants was affected by knowledge of the intervention assigned to the cluster?",
         "options": list(_NA_OPTS),
         "elaboration": "Answer this only if 1b.1 is N/PN/NI (otherwise NA). Answer 'Yes' if: (1) those recruiting individuals were aware of cluster allocation before recruitment and this is likely, consciously or subconsciously, to have affected recruitment differentially between intervention groups; (2) some participants were aware of cluster allocation before their recruitment and this is likely to have affected recruitment differentially; or (3) those identifying potential or actual participants were aware of cluster allocation and are likely to have differentially included individuals in different trial groups. Answer 'No' if all relevant parties — those identifying actual participants, those identifying potential participants, those recruiting, and potential participants — were unaware of cluster allocation at recruitment."},
        {"id": "1b.3",
         "text": "Were there baseline imbalances that suggest differential identification or recruitment of individual participants between intervention groups?",
         "options": list(_BASE_OPTS),
         "elaboration": "As for signalling question 1a.3, imbalances compatible with chance should not be interpreted as suggesting differential identification or recruitment of participants. Such imbalances are more common in cluster-randomized trials than imbalances due to problems with randomization. They can be in the numbers of participants recruited into each group, or in the characteristics of those individuals."},
    ],
}

_DOMAIN_2_ASSIGNMENT: dict[str, Any] = {
    "id": 2,
    "name": "Bias due to deviations from intended interventions (effect of assignment to intervention)",
    "relevant_fields": ["contamination_risk", "clustering_in_analysis",
                         "blinding_participants", "blinding_personnel",
                         "protocol_deviations", "analysis_framework"],
    "signals": [
        {"id": "2.1a",
         "text": "Were participants aware that they were in a trial?",
         "options": list(_BASE_OPTS),
         "elaboration": "In cluster-randomized trials participants may know they are receiving an intervention, or even that they are in a study, without knowing they are in a trial — so they may not know another intervention is being compared with theirs. This makes it impossible for them to cause deviations from the intended interventions that arise because of the trial context. Answer 'No' if participants are not aware they are in a study, or are aware they are in a study but not that they are in a trial. Participants are those on whom investigators seek to measure the outcome — patients, the public, health professionals, or other cluster staff."},
        {"id": "2.1b",
         "text": "Were participants aware of their assigned intervention during the trial?",
         "options": list(_NA_OPTS),
         "elaboration": "Answer this only if 2.1a is Y/PY/NI (otherwise NA). Answer 'Yes' if participants were aware of any part of the assigned intervention during the trial; consider all parts of the assigned intervention."},
        {"id": "2.2",
         "text": "Were carers and people delivering the interventions aware of participants' assigned intervention during the trial?",
         "options": list(_BASE_OPTS),
         "elaboration": "If those caring for participants or delivering the interventions are aware of the assigned intervention, then implementation of the intended intervention, or administration of non-protocol interventions, may differ between groups. Blinding carers and trial personnel, most commonly via a placebo, may prevent such differences but is rare in cluster-randomized trials."},
        {"id": "2.3",
         "text": "Were there deviations from the intended intervention that arose because of the trial context?",
         "options": list(_NA_OPTS),
         "elaboration": "Answer this only if 2.1b or 2.2 is Y/PY/NI (otherwise NA). The guidance mostly applies as for individually-randomized trials. Deviations arising because of the trial context are rarely reported in cluster-randomized trials and may in fact occur rarely — interventions are often aimed at clusters and cluster staff, who may lack the authority or motivation to introduce deviations, and complex interventions make such deviations harder to identify. 'No information' is appropriate in many cases, but use 'Probably yes' if it seems likely that such deviations occurred."},
        {"id": "2.4",
         "text": "Were these deviations likely to have affected the outcome?",
         "options": list(_NA_OPTS),
         "elaboration": "Answer this only if 2.3 is Y/PY (otherwise NA). As for individually-randomized trials — deviations arising from the trial context impact the effect estimate only if they affect the outcome."},
        {"id": "2.5",
         "text": "Were these deviations from intended intervention balanced between groups?",
         "options": list(_NA_OPTS),
         "elaboration": "Answer this only if 2.4 is Y/PY/NI (otherwise NA). As for individually-randomized trials — unbalanced trial-context deviations bias the effect estimate more than balanced ones."},
        {"id": "2.6",
         "text": "Was an appropriate analysis used to estimate the effect of assignment to intervention?",
         "options": list(_BASE_OPTS),
         "elaboration": "Answer 'Yes' if all clusters and individuals were analysed according to the groups to which they were assigned. Where the number of individuals whose assigned group cannot be identified with certainty (or who changed clusters mid-trial) is small and unrelated to assigned group, an analysis that places all individuals in their assigned groups as far as possible is appropriate. Excluding only participants with missing outcome data is appropriate here (missing data are a separate domain). Answer 'No' if participants were analysed by the intervention received rather than assigned, if analyses exclude participants or whole clusters not receiving their assigned intervention, or if a stepped-wedge trial ignores the time trend. Excluding eligible participants after randomization is inappropriate; excluding ineligible participants whose ineligibility was confirmed after randomization and could not have been influenced by assignment can be appropriate."},
        {"id": "2.7",
         "text": "Was there potential for a substantial impact (on the result) of the failure to analyse participants in the group to which they were randomized?",
         "options": list(_NA_OPTS),
         "elaboration": "Answer this only if 2.6 is N/PN/NI (otherwise NA). As for individually-randomized trials, but watch for entire clusters analysed in the wrong intervention group as well as individual participants. There is no precise threshold — substantial impact is possible even with a small proportion misanalysed if the outcome is rare or the misanalysis relates to prognostic factors."},
    ],
}

_DOMAIN_2_ADHERING: dict[str, Any] = {
    "id": 2,
    "name": "Bias due to deviations from intended interventions (effect of adhering to intervention)",
    "relevant_fields": ["contamination_risk", "clustering_in_analysis",
                         "blinding_participants", "blinding_personnel",
                         "protocol_deviations", "analysis_framework"],
    "signals": [
        {"id": "2.1",
         "text": "Were participants aware of their assigned intervention during the trial?",
         "options": list(_BASE_OPTS),
         "elaboration": "If participants are aware of their assigned intervention, health-related behaviours are more likely to differ between the intervention groups. Blinding participants, most commonly via a placebo or sham intervention, may prevent such differences. If participants experienced side effects or toxicities they knew to be specific to one of the interventions, answer 'Yes' or 'Probably yes'."},
        {"id": "2.2",
         "text": "Were carers and people delivering the interventions aware of participants' assigned intervention during the trial?",
         "options": list(_BASE_OPTS),
         "elaboration": "If carers or people delivering the interventions are aware of the assigned intervention, its implementation or the administration of non-protocol interventions may differ between groups. Blinding may prevent such differences. If randomized allocation was not concealed, it is likely that carers and people delivering the interventions were aware of participants' assigned intervention."},
        {"id": "2.3",
         "text": "Were important non-protocol interventions balanced across intervention groups?",
         "options": list(_NA_OPTS),
         "elaboration": "Answer this only if 2.1 or 2.2 is Y/PY/NI, and only if applicable (otherwise NA). Non-protocol interventions are co-interventions received in addition to the protocol-defined intervention. Answer 'Yes' if important non-protocol interventions were balanced across intervention groups. Imbalanced non-protocol interventions — at the individual or the cluster level — can bias the estimated effect of adhering to the assigned intervention. Mark 'NA' if there were no important non-protocol interventions to consider."},
        {"id": "2.4",
         "text": "Were there failures in implementing the intervention that could have affected the outcome?",
         "options": list(_NA_OPTS),
         "elaboration": "Answer only if applicable (otherwise NA). Answer 'Yes' if the intervention was not implemented as intended in a way that could have affected the outcome — incomplete or inconsistent delivery, or departures from the protocol-specified intervention. Implementation failures are especially relevant for complex interventions delivered to whole clusters or cluster staff."},
        {"id": "2.5",
         "text": "Was there non-adherence to the assigned intervention regimen that could have affected participants' outcomes?",
         "options": list(_NA_OPTS),
         "elaboration": "Answer only if applicable (otherwise NA). Mostly as for individually-randomized trials. It is important to consider non-adherence and co-interventions at both the individual and the cluster level."},
        {"id": "2.6",
         "text": "Was an appropriate analysis used to estimate the effect of adhering to the intervention?",
         "options": list(_NA_OPTS),
         "elaboration": "Answer this only if 2.3 is N/PN/NI, or 2.4 or 2.5 is Y/PY/NI (otherwise NA). Mostly as for individually-randomized trials — an appropriate analysis adjusts for prognostic factors and the timing of any non-protocol intervention, non-adherence, or implementation failure. When interventions are multifaceted, consider every intervention for which implementation failures could have affected the outcome, including those aimed at whole clusters and at professionals within clusters as well as at individual patients and the public."},
    ],
}

_DOMAIN_3: dict[str, Any] = {
    "id": 3,
    "name": "Bias due to missing outcome data",
    "relevant_fields": ["clustering_in_analysis", "n_clusters",
                         "attrition_rate", "missing_data_handling"],
    "signals": [
        {"id": "3.1a",
         "text": "Were data for this outcome available for all clusters that recruited participants?",
         "options": list(_BASE_OPTS),
         "elaboration": "In some cluster-randomized trials there may be clusters in which no participants were recruited — this can happen only when participants are recruited after randomization and is handled in Domain 1b. Because a cluster-randomized trial usually has a relatively small number of clusters, there is potential for bias even if only one cluster has no analysable participants. Answer 'No' if any recruiting cluster contributed no outcome data."},
        {"id": "3.1b",
         "text": "Were data for this outcome available for all, or nearly all, participants within clusters?",
         "options": list(_BASE_OPTS),
         "elaboration": "The issues here are broadly as for individually-randomized trials: 'nearly all' means missingness small enough that it could have made no important difference to the result. In cluster-randomized trials there may be particular complexities when clusters merge, split, or disappear."},
        {"id": "3.2",
         "text": "Is there evidence that the result was not biased by missing outcome data?",
         "options": ["NA", "Y", "PY", "PN", "N"],
         "elaboration": "Answer this only if 3.1a or 3.1b is N/PN/NI (otherwise NA). As for individually-randomized trials — evidence can come from an analysis method that corrects for bias from missing data, or from sensitivity analyses showing the result is robust to plausible assumptions about the missingness. A single imputation method (e.g., last-observation-carried-forward) does not by itself establish lack of bias."},
        {"id": "3.3",
         "text": "Could missingness in the outcome depend on its true value?",
         "options": list(_NA_OPTS),
         "elaboration": "Answer this only if 3.2 is N/PN (otherwise NA). As for individually-randomized trials — if loss to follow-up or withdrawal might be related to participants' health status, missingness could depend on the true outcome value."},
        {"id": "3.4",
         "text": "Is it likely that missingness in the outcome depended on its true value?",
         "options": list(_NA_OPTS),
         "elaboration": "Answer this only if 3.3 is Y/PY/NI (otherwise NA). As for individually-randomized trials — distinguishes 'could depend' (Some concerns) from 'likely did depend' (High). Evidence includes differential missingness proportions or reasons between groups, or trial circumstances making such dependence likely."},
    ],
}

_DOMAIN_4: dict[str, Any] = {
    "id": 4,
    "name": "Bias in measurement of the outcome",
    "relevant_fields": ["blinding_outcome_assessors", "outcome_measurement_method",
                         "clustering_in_analysis"],
    "signals": [
        {"id": "4.1",
         "text": "Was the method of measuring the outcome inappropriate?",
         "options": list(_BASE_OPTS),
         "elaboration": "As for individually-randomized trials. Usually 'No' for pre-specified outcomes; answer 'Yes' if the measurement method is unlikely to detect plausible effects or has poor validity."},
        {"id": "4.2",
         "text": "Could measurement or ascertainment of the outcome have differed between intervention groups?",
         "options": list(_BASE_OPTS),
         "elaboration": "As for individually-randomized trials. Differences may arise from detection bias in passive ascertainment, or from intervention-driven extra contacts giving more chances to detect outcome events."},
        {"id": "4.3a",
         "text": "Were outcome assessors aware that a trial was taking place?",
         "options": list(_NA_OPTS),
         "elaboration": "Answer this only if 4.1 and 4.2 are both N/PN/NI (otherwise NA). This question applies to cluster-randomized trials in which participants report their own outcomes (e.g., a questionnaire). If they are not aware they are in a trial, their self-assessment cannot be affected by assignment even if they are aware of the intervention they received."},
        {"id": "4.3b",
         "text": "Were outcome assessors aware of the intervention received by study participants?",
         "options": list(_NA_OPTS),
         "elaboration": "Answer this only if 4.3a is Y/PY/NI (otherwise NA). Answer 'No' if outcome assessors were blinded to intervention status. For participant-reported outcomes the outcome assessor IS the study participant. Where outcomes come from routine data, the individual providing the data and the individual extracting it can both be considered outcome assessors."},
        {"id": "4.4",
         "text": "Could assessment of the outcome have been influenced by knowledge of intervention received?",
         "options": list(_NA_OPTS),
         "elaboration": "Answer this only if 4.3b is Y/PY/NI (otherwise NA). As for individually-randomized trials — knowledge could influence participant-reported outcomes, observer-reported outcomes involving judgement, and intervention-provider decision outcomes; it is unlikely to influence all-cause mortality."},
        {"id": "4.5",
         "text": "Is it likely that assessment of the outcome was influenced by knowledge of intervention received?",
         "options": list(_NA_OPTS),
         "elaboration": "Answer this only if 4.4 is Y/PY/NI (otherwise NA). As for individually-randomized trials — distinguishes 'could have been influenced' (Some concerns) from 'likely was influenced' (High)."},
    ],
}

_DOMAIN_5: dict[str, Any] = {
    "id": 5,
    "name": "Bias in selection of the reported result",
    "relevant_fields": ["protocol_available", "outcomes_match_protocol"],
    "signals": [
        {"id": "5.1",
         "text": "Were the data that produced this result analysed in accordance with a pre-specified analysis plan that was finalized before unblinded outcome data were available for analysis?",
         "options": list(_BASE_OPTS),
         "elaboration": "As for individually-randomized trials. If the trialists' pre-specified intentions are available in sufficient detail, the planned analyses can be compared with what was published. To avoid selection of the reported result, the analysis plan must have been finalized before unblinded outcome data were available."},
        {"id": "5.2",
         "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible outcome measurements (e.g., scales, definitions, time points) within the outcome domain?",
         "options": list(_BASE_OPTS),
         "elaboration": "As for individually-randomized trials. If multiple measurements were made within the outcome domain but only one (or a subset) is fully reported without justification, and the reported result is likely selected on the basis of the results, answer 'Yes'."},
        {"id": "5.3",
         "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible analyses of the data?",
         "options": list(_BASE_OPTS),
         "elaboration": "As for individually-randomized trials. An outcome measurement may be analysed in multiple ways (adjusted vs unadjusted, different covariate sets, different missing-data strategies, different ways of accounting for clustering). If multiple estimates exist but only one is reported on the basis of the results, answer 'Yes'."},
    ],
}


DOMAINS_ASSIGNMENT: list[dict[str, Any]] = [
    _DOMAIN_1A, _DOMAIN_1B, _DOMAIN_2_ASSIGNMENT, _DOMAIN_3, _DOMAIN_4, _DOMAIN_5,
]
DOMAINS_ADHERING: list[dict[str, Any]] = [
    _DOMAIN_1A, _DOMAIN_1B, _DOMAIN_2_ADHERING, _DOMAIN_3, _DOMAIN_4, _DOMAIN_5,
]
# Module-level default (ITT) — used where aim-agnostic iteration is acceptable.
DOMAINS: list[dict[str, Any]] = DOMAINS_ASSIGNMENT


def domains_for_aim(aim: str) -> list[dict[str, Any]]:
    """Return the 6-domain list for the chosen analysis aim.

    ``aim == "adhering"`` → per-protocol Domain 2; anything else → ITT Domain 2.
    """
    return DOMAINS_ADHERING if aim == "adhering" else DOMAINS_ASSIGNMENT


# ─────────────────────────────────────────────
# Prompt building + LLM orchestration
# ─────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing risk of bias in a "
    "**cluster-randomized** trial using the Cochrane RoB 2 tool (cluster-randomized "
    "trial extension, RoB 2 CRT). Read the PDF carefully. Answer each signaling "
    "question with one of the response options listed for that question — Y (yes), "
    "PY (probably yes), PN (probably no), N (no), NI (no information), or NA (not "
    "applicable, only where offered). Provide a 1-2 sentence rationale for each "
    "answer, quoting the paper where possible. Return ONLY a valid JSON object — "
    "no preamble, no markdown fences."
)


def build_domain_prompt(domain: dict[str, Any],
                        study_type: str,
                        assessed_outcome: str,
                        extracted_fields: dict[str, str],
                        outcome_is_override: bool = False) -> str:
    """Per-domain prompt for cluster-randomized RoB 2 signaling-question assessment."""
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
        shape += f'  "{sig["id"]}": "{"|".join(sig["options"])}",\n'
        shape += f'  "{sig["id"]}_rationale": "1-2 sentences quoting the paper",\n'
    shape += '  "direction_of_bias": "NA|Favours experimental|Favours comparator|Towards null|Away from null|Unpredictable"\n'
    shape += "}"

    override_note = ""
    if outcome_is_override and str(domain["id"]) == "1a":
        override_note = (
            "\n\nNote: this assessment is for a non-primary outcome chosen by the "
            "reviewer because the paper's primary outcome was unclear. Domain 1a "
            "signaling questions concern the randomization process for the trial "
            "as a whole, not the specific outcome — answer accordingly."
        )

    return f"""Assess **Domain {domain['id']} — {domain['name']}** for the **cluster-randomized** trial described in the attached PDF.

Study type: {study_type}
Outcome being assessed: {assessed_outcome}{override_note}

Context (fields already extracted from the paper):
{ctx_json}

Signaling questions:
{questions_block}

Return a JSON object with exactly this shape:
{shape}

Answer N (or PN) when the paper gives enough information to rule out the problem, and NI only when the paper is silent. Use NA only for a question whose stated precondition is not met. Rationales must be short (1-2 sentences) and quote the paper verbatim where possible."""


def _assess_domain(pdf_bytes: bytes, domain: dict[str, Any],
                   judges: dict[Any, Callable[[dict[str, str]], str]],
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
            logger.warning("RoB 2 CRT domain %s question %s: invalid answer %r — defaulting to NI",
                            domain["id"], sid, ans)
            ans = "NI"
        signals[sid] = ans
        rationales[sid] = str(raw.get(f"{sid}_rationale", "")).strip()

    judgement = judges[domain["id"]](signals)
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
        outcome_is_override: bool = False,
        aim: str = "assignment") -> tuple[dict[str, Any], str, str]:
    """Run RoB 2 (cluster-randomized extension) against a cluster-randomized trial.

    Returns ``(domain_results, overall_judgement, overall_direction)``.

    - ``domain_results`` is keyed by string domain id (``"1a"``, ``"1b"``,
      ``"2"`` … ``"5"``), each with ``{id, name, signals, rationales,
      judgement, direction}``; plus a string entry ``domain_results["aim"]``
      recording which Domain 2 variant was used.
    - ``aim`` selects the Domain 2 variant: ``"adhering"`` → per-protocol,
      anything else → ``"assignment"`` (intention-to-treat, the default).
    """
    aim = "adhering" if str(aim or "").strip().lower() == "adhering" else "assignment"
    study_type = classification.get("study_type", "Cluster Randomized Trial")
    domains = domains_for_aim(aim)
    judges = judges_for(aim)

    domain_results: dict[str, Any] = {}
    for domain in domains:
        if progress:
            try:
                progress(domain["id"])
            except Exception:
                pass
        result = _assess_domain(pdf_bytes, domain, judges, study_type,
                                 assessed_outcome, extracted_fields,
                                 outcome_is_override=outcome_is_override)
        result["id"] = domain["id"]
        result["name"] = domain["name"]
        domain_results[str(domain["id"])] = result

    overall = rob2_cluster_overall(
        [domain_results[str(d["id"])]["judgement"] for d in domains])

    dirs = [domain_results[str(d["id"])]["direction"] for d in domains
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

    # Record the analysis aim so the CSV/XLSX export and detail UI can recover
    # which Domain 2 variant scored this row. compute_grade ignores this
    # non-dict entry via its ``isinstance(d, dict)`` filter.
    domain_results["aim"] = aim
    return domain_results, overall, overall_direction


# ─────────────────────────────────────────────
# Developer-view exposure
# ─────────────────────────────────────────────
def prompt_catalog() -> dict[str, Any]:
    """Return the prompts + decision-tree source for the developer icon."""
    import inspect

    def _entry(domain: dict[str, Any],
               judges: dict[Any, Callable[[dict[str, str]], str]]) -> dict[str, Any]:
        sample_fields = {k: "<extracted value>" for k in domain["relevant_fields"]}
        return {
            "id": domain["id"],
            "name": domain["name"],
            "signals": domain["signals"],
            "relevant_fields": domain["relevant_fields"],
            "prompt_template": build_domain_prompt(
                domain, "Cluster Randomized Trial",
                "<assessed outcome here>", sample_fields,
            ),
            "decision_tree_code": inspect.getsource(judges[domain["id"]]),
        }

    return {
        "tool": "Cochrane RoB 2 — cluster-randomized trials extension (RoB 2 CRT)",
        "system_prompt": _SYSTEM_PROMPT,
        "signal_options": list(SIGNAL_OPTIONS),
        "judgements": ["Low", "Some concerns", "High"],
        "domains": [_entry(d, DOMAIN_JUDGES_ASSIGNMENT) for d in DOMAINS_ASSIGNMENT],
        "domain2_adhering": _entry(_DOMAIN_2_ADHERING, DOMAIN_JUDGES_ADHERING),
        "overall_algorithm_code": inspect.getsource(rob2_cluster_overall),
        "aim_note": (
            "Domain 2 has two variants selected per run by the review team's "
            "aim. The 'domains' list shows the effect-of-assignment (ITT) "
            "variant; 'domain2_adhering' shows the effect-of-adhering "
            "(per-protocol) Domain 2."
        ),
    }
