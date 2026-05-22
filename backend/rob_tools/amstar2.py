"""AMSTAR-2 — critical appraisal of systematic reviews.

Source: Shea BJ, Reeves BC, Wells G, Thuku M, Hamel C, Moran J, Moher D,
Tugwell P, Welch V, Kristjansson E, Henry DA. "AMSTAR 2: a critical appraisal
tool for systematic reviews that include randomised or non-randomised studies
of healthcare interventions, or both." BMJ 2017;358:j4008.

AMSTAR-2 is structurally unlike the other Quality Appraisal risk-of-bias tools:

- It assesses **16 checklist items**, not 4-7 bias domains. Each item is rated
  ``Yes`` / ``Partial Yes`` / ``No`` (some items only Yes/No; items 11, 12, 15
  also allow ``No meta-analysis conducted``).
- The headline output is an **overall confidence rating** — High / Moderate /
  Low / Critically low — derived (``amstar2_overall``) from how many *critical*
  vs *non-critical* items are flawed. The 7 critical items are the published
  default set (Shea 2017): items 2, 4, 7, 9, 11, 13, 15.
- GRADE does not apply (AMSTAR-2 rates the review's methodological quality, not
  the certainty of a body of evidence) — the registry entry sets
  ``skip_grade`` so the orchestrator skips indirectness, imprecision, and the
  GRADE computation for systematic-review papers.

Mechanics, consistent with the other tools:

- ``ITEMS`` — the 16 items, each with signaling sub-criteria transcribed from
  the AMSTAR-2 checklist + the AMSTAR-2 guidance document.
- The LLM answers each sub-criterion Y/N; a pure-Python decision tree
  (``amstar2_item_judge``) maps the answers to the item rating. Decision trees
  live in code (not prompts) so the developer view can show the exact logic.
- ``run_preflight`` — one LLM call determining whether the review includes
  RCTs / NRSI / both (items 9 + 11 have design-specific sub-criteria) and
  whether a quantitative synthesis was performed (items 11/12/15 are gated on
  it — when there is no meta-analysis those items are set to "No meta-analysis
  conducted" in code, with no LLM call, mirroring the RoB 2 Cluster NA cascade).
- ``run(pdf_bytes, fields, classification, primary_outcome, progress)`` — the
  orchestrator entry point. Returns ``(item_results, overall_confidence, "NA")``.
"""

from __future__ import annotations

import inspect
import json
import logging
from typing import Any, Callable

from ..annotator import _call_with_pdf

logger = logging.getLogger("rubricgen")


SIGNAL_OPTIONS = ("Y", "N")
ITEM_RATINGS = ("Yes", "Partial Yes", "No", "No meta-analysis conducted")
CONFIDENCE_LEVELS = ("High", "Moderate", "Low", "Critically low")

# The 7 critical domains — published default set (Shea 2017, BMJ 2017;358:j4008).
# AMSTAR-2 invites reviewers to designate their own; v1 hardcodes the default.
CRITICAL_ITEMS = frozenset({2, 4, 7, 9, 11, 13, 15})

# Items conditional on a quantitative synthesis having been performed.
_META_GATED_ITEMS = frozenset({11, 12, 15})

# Rating order for "take the lower of two design ratings" (items 9 / 11, "both").
_RATING_RANK = {"No": 0, "Partial Yes": 1, "Yes": 2}


# ─────────────────────────────────────────────
# Decision trees (pure Python — no LLM)
# ─────────────────────────────────────────────
def _all_yes(signal_ids: list[str], answers: dict[str, str]) -> bool:
    """True when every listed sub-criterion was answered Y."""
    return bool(signal_ids) and all(answers.get(sid) == "Y" for sid in signal_ids)


def _any_yes(signal_ids: list[str], answers: dict[str, str]) -> bool:
    """True when at least one listed sub-criterion was answered Y."""
    return any(answers.get(sid) == "Y" for sid in signal_ids)


def _tiered_judge(item_signals: list[dict[str, Any]],
                  answers: dict[str, str]) -> str:
    """Yes / Partial Yes / No for a tiered item.

    Partial Yes requires every ``tier="partial"`` sub-criterion; Yes requires
    those plus every ``tier="yes"`` sub-criterion (the AMSTAR-2 "For Yes: as
    for partial yes, plus ..." structure).
    """
    partial_ids = [s["id"] for s in item_signals if s.get("tier") == "partial"]
    yes_ids = [s["id"] for s in item_signals if s.get("tier") == "yes"]
    partial_ok = _all_yes(partial_ids, answers)
    yes_ok = _all_yes(yes_ids, answers) if yes_ids else True
    if partial_ok and yes_ok:
        return "Yes"
    if partial_ok:
        return "Partial Yes"
    return "No"


def _min_rating(ratings: list[str]) -> str:
    """Return the lowest (most conservative) rating in the list."""
    if not ratings:
        return "No"
    return min(ratings, key=lambda r: _RATING_RANK.get(r, 0))


def amstar2_item_judge(item: dict[str, Any], answers: dict[str, str],
                       review_includes: str = "both",
                       meta_analysis: bool = True) -> str:
    """Map signaling-question answers to an AMSTAR-2 item rating.

    ``review_includes`` is one of ``rct`` / ``nrsi`` / ``both`` (items 9 + 11
    have design-specific sub-criteria). ``meta_analysis`` gates items 11/12/15.

    Logic types (``item["logic"]``):
      - ``all_required`` — Yes iff every sub-criterion is Y, else No.
      - ``one_of``       — Yes iff at least one sub-criterion is Y, else No.
      - ``tiered``       — Yes / Partial Yes / No via ``_tiered_judge``.
      - ``rob_design``   — item 9: tiered, evaluated per included design;
                           for "both" the lower of the two ratings is taken.
      - ``meta_design``  — item 11: Yes/No, evaluated per included design;
                           for "both" both design sets must be fully met.
    """
    logic = item["logic"]
    if item.get("meta_gated") and not meta_analysis:
        return "No meta-analysis conducted"

    all_ids = [s["id"] for s in item["signals"]]

    if logic == "all_required":
        return "Yes" if _all_yes(all_ids, answers) else "No"

    if logic == "one_of":
        return "Yes" if _any_yes(all_ids, answers) else "No"

    if logic == "tiered":
        return _tiered_judge(item["signals"], answers)

    if logic == "rob_design":
        ratings: list[str] = []
        for design in ("rct", "nrsi"):
            if review_includes in (design, "both"):
                ratings.append(_tiered_judge(
                    [s for s in item["signals"] if s.get("design") == design],
                    answers))
        if not ratings:  # defensive — unknown review_includes
            ratings.append(_tiered_judge(item["signals"], answers))
        return _min_rating(ratings)

    if logic == "meta_design":
        results: list[bool] = []
        for design in ("rct", "nrsi"):
            if review_includes in (design, "both"):
                ids = [s["id"] for s in item["signals"]
                       if s.get("design") == design]
                results.append(_all_yes(ids, answers))
        if not results:  # defensive
            results.append(_all_yes(all_ids, answers))
        return "Yes" if all(results) else "No"

    return "No"


def amstar2_overall(item_ratings: dict[int, str],
                    critical_ids: frozenset[int] = CRITICAL_ITEMS) -> str:
    """Derive the overall confidence rating (Shea 2017 algorithm).

    A *critical flaw* is a critical item rated "No"; a *non-critical weakness*
    is a non-critical item rated "No". "Partial Yes" and "No meta-analysis
    conducted" are not flaws.

      High            — 0 critical flaws and ≤ 1 non-critical weakness
      Moderate        — 0 critical flaws and  > 1 non-critical weakness
      Low             — exactly 1 critical flaw (± non-critical weaknesses)
      Critically low  — ≥ 2 critical flaws (± non-critical weaknesses)
    """
    critical_flaws = sum(1 for iid, r in item_ratings.items()
                         if iid in critical_ids and r == "No")
    noncritical_weaknesses = sum(1 for iid, r in item_ratings.items()
                                 if iid not in critical_ids and r == "No")
    if critical_flaws == 0:
        return "High" if noncritical_weaknesses <= 1 else "Moderate"
    if critical_flaws == 1:
        return "Low"
    return "Critically low"


# ─────────────────────────────────────────────
# The 16 AMSTAR-2 items — sub-criteria transcribed from the AMSTAR-2 checklist
# (Shea 2017) and the AMSTAR-2 guidance document.
# ─────────────────────────────────────────────
ITEMS: list[dict[str, Any]] = [
    {
        "id": 1,
        "name": "PICO components in research question",
        "question": ("Did the research questions and inclusion criteria for "
                      "the review include the components of PICO?"),
        "critical": False,
        "logic": "all_required",
        "ratings": ["Yes", "No"],
        "relevant_fields": ["inclusion_criteria", "objective"],
        "elaboration": (
            "PICO (Population, Intervention, Comparator group, Outcome) should "
            "be the organising framework for the review question. The four "
            "elements need not be stated explicitly but must be discernible "
            "from a careful reading of the abstract, introduction, or methods. "
            "A timeframe for follow-up is optional/recommended and is not "
            "scored here."),
        "signals": [
            {"id": "1.1", "tier": "yes",
             "text": "The review specifies the Population."},
            {"id": "1.2", "tier": "yes",
             "text": "The review specifies the Intervention."},
            {"id": "1.3", "tier": "yes",
             "text": "The review specifies the Comparator group."},
            {"id": "1.4", "tier": "yes",
             "text": "The review specifies the Outcome(s)."},
        ],
    },
    {
        "id": 2,
        "name": "Protocol established before the review",
        "question": ("Did the report of the review contain an explicit "
                      "statement that the review methods were established "
                      "prior to the conduct of the review and did the report "
                      "justify any significant deviations from the protocol?"),
        "critical": True,
        "logic": "tiered",
        "ratings": ["Yes", "Partial Yes", "No"],
        "relevant_fields": ["search_strategy", "inclusion_criteria",
                            "synthesis_method"],
        "elaboration": (
            "Systematic reviews are observational research; their methods "
            "should be planned before the review begins. Partial Yes: the "
            "authors state they worked from a written protocol/guide covering "
            "the review questions, a search strategy, inclusion/exclusion "
            "criteria, and a risk-of-bias assessment. Yes: as Partial Yes, "
            "plus the protocol was registered (e.g. PROSPERO) and additionally "
            "pre-specified a meta-analysis/synthesis plan (if appropriate), a "
            "plan for investigating heterogeneity, and a justification for any "
            "deviations from the protocol."),
        "signals": [
            {"id": "2.1", "tier": "partial",
             "text": "A written protocol or guide stated the review question(s)."},
            {"id": "2.2", "tier": "partial",
             "text": "The written protocol/guide included a search strategy."},
            {"id": "2.3", "tier": "partial",
             "text": "The written protocol/guide included inclusion/exclusion criteria."},
            {"id": "2.4", "tier": "partial",
             "text": "The written protocol/guide included a risk-of-bias assessment."},
            {"id": "2.5", "tier": "yes",
             "text": "The protocol was registered before the review was conducted."},
            {"id": "2.6", "tier": "yes",
             "text": ("The protocol specified a meta-analysis/synthesis plan, "
                      "where appropriate.")},
            {"id": "2.7", "tier": "yes",
             "text": "The protocol specified a plan for investigating causes of heterogeneity."},
            {"id": "2.8", "tier": "yes",
             "text": "The review justifies any deviations from the protocol."},
        ],
    },
    {
        "id": 3,
        "name": "Explanation of study-design selection",
        "question": ("Did the review authors explain their selection of the "
                      "study designs for inclusion in the review?"),
        "critical": False,
        "logic": "one_of",
        "ratings": ["Yes", "No"],
        "relevant_fields": ["inclusion_criteria", "objective"],
        "elaboration": (
            "The selection of study types should not be arbitrary. The review "
            "should satisfy at least one of: an explanation for including only "
            "RCTs, an explanation for including only NRSI (non-randomised "
            "studies of interventions), or an explanation for including both."),
        "signals": [
            {"id": "3.1", "tier": "yes",
             "text": "The review explains its decision to include only RCTs."},
            {"id": "3.2", "tier": "yes",
             "text": "The review explains its decision to include only NRSI."},
            {"id": "3.3", "tier": "yes",
             "text": "The review explains its decision to include both RCTs and NRSI."},
        ],
    },
    {
        "id": 4,
        "name": "Comprehensive literature search",
        "question": "Did the review authors use a comprehensive literature search strategy?",
        "critical": True,
        "logic": "tiered",
        "ratings": ["Yes", "Partial Yes", "No"],
        "relevant_fields": ["search_strategy"],
        "elaboration": (
            "Partial Yes: searched at least 2 databases relevant to the "
            "question, provided key words and/or the search strategy, and "
            "justified any publication restrictions (e.g. language). Yes: as "
            "Partial Yes, plus searched the reference lists of included "
            "studies, searched trial/study registries, consulted content "
            "experts, searched for grey literature where relevant, and ran the "
            "search within 24 months of completion of the review."),
        "signals": [
            {"id": "4.1", "tier": "partial",
             "text": "Searched at least 2 databases relevant to the research question."},
            {"id": "4.2", "tier": "partial",
             "text": "Provided key words and/or the full search strategy."},
            {"id": "4.3", "tier": "partial",
             "text": "Justified publication restrictions (e.g. language)."},
            {"id": "4.4", "tier": "yes",
             "text": "Searched the reference lists / bibliographies of included studies."},
            {"id": "4.5", "tier": "yes",
             "text": "Searched trial / study registries."},
            {"id": "4.6", "tier": "yes",
             "text": "Included or consulted content experts in the field."},
            {"id": "4.7", "tier": "yes",
             "text": "Where relevant, searched for grey literature."},
            {"id": "4.8", "tier": "yes",
             "text": "Conducted the search within 24 months of completion of the review."},
        ],
    },
    {
        "id": 5,
        "name": "Study selection in duplicate",
        "question": "Did the review authors perform study selection in duplicate?",
        "critical": False,
        "logic": "one_of",
        "ratings": ["Yes", "No"],
        "relevant_fields": ["study_selection"],
        "elaboration": (
            "Best practice requires two reviewers to determine eligibility. "
            "Yes if either: at least two reviewers independently selected the "
            "eligible studies and reached consensus; OR two reviewers selected "
            "a sample and achieved good agreement (kappa ≥ 0.80 / ≥ 80%), with "
            "the remainder selected by one reviewer."),
        "signals": [
            {"id": "5.1", "tier": "yes",
             "text": ("At least two reviewers independently selected eligible "
                      "studies and reached consensus.")},
            {"id": "5.2", "tier": "yes",
             "text": ("Two reviewers selected a sample with ≥ 80% agreement, "
                      "the remainder selected by one reviewer.")},
        ],
    },
    {
        "id": 6,
        "name": "Data extraction in duplicate",
        "question": "Did the review authors perform data extraction in duplicate?",
        "critical": False,
        "logic": "one_of",
        "ratings": ["Yes", "No"],
        "relevant_fields": ["data_extraction"],
        "elaboration": (
            "As item 5, applied to data extraction. Yes if either: at least "
            "two reviewers reached consensus on the data to extract; OR two "
            "reviewers extracted from a sample with ≥ 80% agreement, the "
            "remainder by one reviewer."),
        "signals": [
            {"id": "6.1", "tier": "yes",
             "text": ("At least two reviewers reached consensus on which data "
                      "to extract.")},
            {"id": "6.2", "tier": "yes",
             "text": ("Two reviewers extracted data from a sample with ≥ 80% "
                      "agreement, the remainder by one reviewer.")},
        ],
    },
    {
        "id": 7,
        "name": "List of excluded studies with justification",
        "question": ("Did the review authors provide a list of excluded "
                      "studies and justify the exclusions?"),
        "critical": True,
        "logic": "tiered",
        "ratings": ["Yes", "Partial Yes", "No"],
        "relevant_fields": ["study_selection", "prisma_flow"],
        "elaboration": (
            "Partial Yes: provided a list of all potentially relevant studies "
            "read in full text but excluded. Yes: as Partial Yes, plus "
            "justified the exclusion of each such study. Exclusion should not "
            "be based on risk of bias, which is handled separately."),
        "signals": [
            {"id": "7.1", "tier": "partial",
             "text": ("Provided a list of all potentially relevant studies "
                      "read in full text but excluded.")},
            {"id": "7.2", "tier": "yes",
             "text": ("Justified the exclusion of each potentially relevant "
                      "study.")},
        ],
    },
    {
        "id": 8,
        "name": "Adequate description of included studies",
        "question": "Did the review authors describe the included studies in adequate detail?",
        "critical": False,
        "logic": "tiered",
        "ratings": ["Yes", "Partial Yes", "No"],
        "relevant_fields": ["included_studies_n", "inclusion_criteria"],
        "elaboration": (
            "Partial Yes: described the populations, interventions, "
            "comparators, outcomes, and research designs of the included "
            "studies. Yes: as Partial Yes, plus described the population, "
            "intervention (with doses where relevant), and comparator (with "
            "doses where relevant) in detail, and described each study's "
            "setting and timeframe for follow-up."),
        "signals": [
            {"id": "8.1", "tier": "partial",
             "text": "Described the populations of the included studies."},
            {"id": "8.2", "tier": "partial",
             "text": "Described the interventions of the included studies."},
            {"id": "8.3", "tier": "partial",
             "text": "Described the comparators of the included studies."},
            {"id": "8.4", "tier": "partial",
             "text": "Described the outcomes of the included studies."},
            {"id": "8.5", "tier": "partial",
             "text": "Described the research designs of the included studies."},
            {"id": "8.6", "tier": "yes",
             "text": "Described the population in detail."},
            {"id": "8.7", "tier": "yes",
             "text": "Described the intervention in detail (including doses where relevant)."},
            {"id": "8.8", "tier": "yes",
             "text": "Described the comparator in detail (including doses where relevant)."},
            {"id": "8.9", "tier": "yes",
             "text": "Described each study's setting."},
            {"id": "8.10", "tier": "yes",
             "text": "Described the timeframe for follow-up."},
        ],
    },
    {
        "id": 9,
        "name": "Satisfactory risk-of-bias technique",
        "question": ("Did the review authors use a satisfactory technique for "
                      "assessing the risk of bias (RoB) in individual studies "
                      "that were included in the review?"),
        "critical": True,
        "logic": "rob_design",
        "ratings": ["Yes", "Partial Yes", "No"],
        "relevant_fields": ["rob_tool_used"],
        "elaboration": (
            "Whether the review used a satisfactory RoB technique for its "
            "included studies. For reviews of RCTs — Partial Yes: assessed RoB "
            "from unconcealed allocation and from lack of blinding of patients "
            "and assessors for outcome assessment (blinding is unnecessary for "
            "objective outcomes such as all-cause mortality); Yes: also "
            "assessed RoB from a non-random allocation sequence and from "
            "selective reporting of results. For reviews of NRSI — Partial "
            "Yes: assessed RoB from confounding and from selection bias; Yes: "
            "also assessed RoB from the methods used to ascertain exposures "
            "and outcomes and from selective reporting of results."),
        "signals": [
            {"id": "9.1", "tier": "partial", "design": "rct",
             "text": "(RCTs) Assessed RoB from unconcealed allocation."},
            {"id": "9.2", "tier": "partial", "design": "rct",
             "text": ("(RCTs) Assessed RoB from lack of blinding of patients "
                      "and assessors when assessing outcomes (unnecessary for "
                      "objective outcomes such as all-cause mortality).")},
            {"id": "9.3", "tier": "yes", "design": "rct",
             "text": "(RCTs) Assessed RoB from an allocation sequence that was not truly random."},
            {"id": "9.4", "tier": "yes", "design": "rct",
             "text": ("(RCTs) Assessed RoB from selection of the reported "
                      "result from among multiple measurements or analyses.")},
            {"id": "9.5", "tier": "partial", "design": "nrsi",
             "text": "(NRSI) Assessed RoB from confounding."},
            {"id": "9.6", "tier": "partial", "design": "nrsi",
             "text": "(NRSI) Assessed RoB from selection bias."},
            {"id": "9.7", "tier": "yes", "design": "nrsi",
             "text": "(NRSI) Assessed RoB from the methods used to ascertain exposures and outcomes."},
            {"id": "9.8", "tier": "yes", "design": "nrsi",
             "text": ("(NRSI) Assessed RoB from selection of the reported "
                      "result from among multiple measurements or analyses.")},
        ],
    },
    {
        "id": 10,
        "name": "Funding sources of included studies",
        "question": ("Did the review authors report on the sources of funding "
                      "for the studies included in the review?"),
        "critical": False,
        "logic": "all_required",
        "ratings": ["Yes", "No"],
        "relevant_fields": ["included_studies_n"],
        "elaboration": (
            "The review should report the sources of funding for the "
            "individual included studies. Reporting that the reviewers looked "
            "for this information but it was not reported by the study authors "
            "also qualifies as Yes."),
        "signals": [
            {"id": "10.1", "tier": "yes",
             "text": ("Reported the sources of funding for the individual "
                      "included studies (or noted the information was sought "
                      "but not reported).")},
        ],
    },
    {
        "id": 11,
        "name": "Appropriate meta-analysis methods",
        "question": ("If meta-analysis was performed, did the review authors "
                      "use appropriate methods for statistical combination of "
                      "results?"),
        "critical": True,
        "logic": "meta_design",
        "meta_gated": True,
        "ratings": ["Yes", "No", "No meta-analysis conducted"],
        "relevant_fields": ["synthesis_method", "pooling_model", "effect_measure",
                            "pooled_estimate", "heterogeneity"],
        "elaboration": (
            "For reviews of RCTs — Yes: the authors justified combining the "
            "data, used an appropriate weighted technique adjusting for "
            "heterogeneity if present, and investigated the causes of any "
            "heterogeneity. For reviews of NRSI — Yes: the authors justified "
            "combining the data, used an appropriate weighted technique "
            "adjusting for heterogeneity, statistically combined "
            "confounding-adjusted effect estimates (or justified combining raw "
            "data), and reported separate summary estimates for RCTs and NRSI "
            "when both were included."),
        "signals": [
            {"id": "11.1", "design": "rct",
             "text": "(RCTs) The authors justified combining the data in a meta-analysis."},
            {"id": "11.2", "design": "rct",
             "text": ("(RCTs) Used an appropriate weighted technique to combine "
                      "results and adjusted for heterogeneity if present.")},
            {"id": "11.3", "design": "rct",
             "text": "(RCTs) Investigated the causes of any heterogeneity."},
            {"id": "11.4", "design": "nrsi",
             "text": "(NRSI) The authors justified combining the data in a meta-analysis."},
            {"id": "11.5", "design": "nrsi",
             "text": ("(NRSI) Used an appropriate weighted technique to combine "
                      "results, adjusting for heterogeneity if present.")},
            {"id": "11.6", "design": "nrsi",
             "text": ("(NRSI) Statistically combined confounding-adjusted "
                      "effect estimates, or justified combining raw data when "
                      "adjusted estimates were unavailable.")},
            {"id": "11.7", "design": "nrsi",
             "text": ("(NRSI) Reported separate summary estimates for RCTs and "
                      "NRSI when both were included in the review.")},
        ],
    },
    {
        "id": 12,
        "name": "Impact of RoB on the meta-analysis",
        "question": ("If meta-analysis was performed, did the review authors "
                      "assess the potential impact of RoB in individual "
                      "studies on the results of the meta-analysis or other "
                      "evidence synthesis?"),
        "critical": False,
        "logic": "one_of",
        "meta_gated": True,
        "ratings": ["Yes", "No", "No meta-analysis conducted"],
        "relevant_fields": ["rob_tool_used", "sensitivity_analyses"],
        "elaboration": (
            "Yes if either: the pooled estimate was restricted to low "
            "risk-of-bias RCTs; OR, where studies of variable RoB were pooled, "
            "the authors performed analyses (e.g. sensitivity or "
            "meta-regression) to investigate the possible impact of RoB on the "
            "summary estimates of effect."),
        "signals": [
            {"id": "12.1", "tier": "yes",
             "text": "The pooled estimate included only low risk-of-bias RCTs."},
            {"id": "12.2", "tier": "yes",
             "text": ("The authors performed analyses to investigate the "
                      "possible impact of RoB on the summary estimates of effect.")},
        ],
    },
    {
        "id": 13,
        "name": "Accounting for RoB when interpreting results",
        "question": ("Did the review authors account for RoB in individual "
                      "studies when interpreting / discussing the results of "
                      "the review?"),
        "critical": True,
        "logic": "one_of",
        "ratings": ["Yes", "No"],
        "relevant_fields": ["rob_tool_used", "grade_assessment"],
        "elaboration": (
            "Yes if either: the review included only low risk-of-bias RCTs; "
            "OR, where RCTs with moderate or high RoB, or NRSI, were included, "
            "the review provided a discussion of the likely impact of RoB on "
            "the results. This applies even when no meta-analysis was done."),
        "signals": [
            {"id": "13.1", "tier": "yes",
             "text": "The review included only low risk-of-bias RCTs."},
            {"id": "13.2", "tier": "yes",
             "text": ("The review discusses the likely impact of RoB on the "
                      "results (where moderate/high-RoB RCTs or NRSI were included).")},
        ],
    },
    {
        "id": 14,
        "name": "Explanation and discussion of heterogeneity",
        "question": ("Did the review authors provide a satisfactory "
                      "explanation for, and discussion of, any heterogeneity "
                      "observed in the results of the review?"),
        "critical": False,
        "logic": "one_of",
        "ratings": ["Yes", "No"],
        "relevant_fields": ["heterogeneity", "subgroup_analyses"],
        "elaboration": (
            "Yes if either: there was no significant heterogeneity in the "
            "results; OR, where heterogeneity was present, the authors "
            "investigated its sources and discussed the impact on the review's "
            "results."),
        "signals": [
            {"id": "14.1", "tier": "yes",
             "text": "There was no significant heterogeneity in the results."},
            {"id": "14.2", "tier": "yes",
             "text": ("The authors investigated the sources of heterogeneity "
                      "and discussed its impact on the results.")},
        ],
    },
    {
        "id": 15,
        "name": "Investigation of publication bias",
        "question": ("If they performed quantitative synthesis, did the review "
                      "authors carry out an adequate investigation of "
                      "publication bias (small-study bias) and discuss its "
                      "likely impact on the results of the review?"),
        "critical": True,
        "logic": "all_required",
        "meta_gated": True,
        "ratings": ["Yes", "No", "No meta-analysis conducted"],
        "relevant_fields": ["publication_bias"],
        "elaboration": (
            "Yes requires both: graphical or statistical tests for publication "
            "bias were performed, and the likelihood and magnitude of its "
            "impact on the review's results were discussed."),
        "signals": [
            {"id": "15.1", "tier": "yes",
             "text": "Performed graphical or statistical tests for publication bias."},
            {"id": "15.2", "tier": "yes",
             "text": ("Discussed the likelihood and magnitude of the impact of "
                      "publication bias on the results.")},
        ],
    },
    {
        "id": 16,
        "name": "Conflicts of interest of the review",
        "question": ("Did the review authors report any potential sources of "
                      "conflict of interest, including any funding they "
                      "received for conducting the review?"),
        "critical": False,
        "logic": "one_of",
        "ratings": ["Yes", "No"],
        "relevant_fields": [],
        "elaboration": (
            "Yes if either: the authors reported no competing interests; OR "
            "the authors described their funding sources and how they managed "
            "any potential conflicts of interest."),
        "signals": [
            {"id": "16.1", "tier": "yes",
             "text": "The authors reported they had no competing interests."},
            {"id": "16.2", "tier": "yes",
             "text": ("The authors described their funding sources and how "
                      "they managed potential conflicts of interest.")},
        ],
    },
]

_ITEMS_BY_ID: dict[int, dict[str, Any]] = {it["id"]: it for it in ITEMS}


# ─────────────────────────────────────────────
# Prompt building + LLM orchestration
# ─────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist applying the AMSTAR-2 "
    "critical-appraisal tool (Shea et al., BMJ 2017;358:j4008) to a systematic "
    "review. Read the attached PDF carefully. For each signaling sub-criterion "
    "answer Y (yes — the review clearly reports or meets it) or N (no — not "
    "reported, or not met). Be strict but fair: answer Y only when the review "
    "actually provides the information; if it is silent or ambiguous, answer "
    "N. Provide a short rationale (1-2 sentences, quoting the review where "
    "possible) for every answer. Return ONLY a valid JSON object — no "
    "preamble, no markdown fences."
)

_PREFLIGHT_PROMPT = """You are applying the AMSTAR-2 tool to the systematic review in the attached PDF.

Before scoring the 16 items, determine two things that change how items 9 and 11 are assessed:

1. **review_includes** — does the systematic review include:
   - "rct"  — only randomised controlled trials
   - "nrsi" — only non-randomised studies of interventions (cohort, case-control, etc.)
   - "both" — both RCTs and NRSI
2. **meta_analysis** — did the review perform a meta-analysis or other quantitative
   synthesis (statistical pooling of results)? true or false.

Return a JSON object with exactly this shape:
{
  "review_includes": "rct" | "nrsi" | "both",
  "meta_analysis": true | false,
  "rationale": "1-2 sentences quoting the review (study designs included; whether results were statistically pooled)"
}

Return only the JSON object."""


def _normalise_answer(raw_value: Any) -> str:
    """Normalise an LLM sub-criterion answer to Y or N.

    Anything that is not an affirmative (Y / Yes) is treated as N — for
    AMSTAR-2 a sub-criterion counts only when the review clearly reports it.
    """
    s = str(raw_value or "").strip().lower()
    return "Y" if s in ("y", "yes", "true", "1") else "N"


def _signals_for(item: dict[str, Any], review_includes: str) -> list[dict[str, Any]]:
    """The sub-criteria to send to the LLM for an item.

    Design-aware items (9, 11) only send the sub-criteria for the design(s)
    the review actually includes; all other items send every sub-criterion.
    """
    if item["logic"] not in ("rob_design", "meta_design"):
        return item["signals"]
    if review_includes == "rct":
        return [s for s in item["signals"] if s.get("design") == "rct"]
    if review_includes == "nrsi":
        return [s for s in item["signals"] if s.get("design") == "nrsi"]
    return item["signals"]


def build_preflight_prompt() -> str:
    """The preflight prompt (constant — exposed for the developer view)."""
    return _PREFLIGHT_PROMPT


def build_item_prompt(item: dict[str, Any], study_type: str,
                      extracted_fields: dict[str, str],
                      review_includes: str = "both",
                      meta_analysis: bool = True) -> str:
    """Per-item prompt for an AMSTAR-2 signaling-question assessment."""
    relevant = {k: extracted_fields[k]
                for k in item["relevant_fields"] if extracted_fields.get(k)}
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"

    signals = _signals_for(item, review_includes)
    q_lines = [f"\n**{s['id']}** — {s['text']}" for s in signals]
    questions_block = "\n".join(q_lines)

    shape_lines = ["{"]
    for s in signals:
        shape_lines.append(f'  "{s["id"]}": "Y|N",')
        shape_lines.append(f'  "{s["id"]}_rationale": "1-2 sentences quoting the review",')
    shape_lines[-1] = shape_lines[-1].rstrip(",")
    shape_lines.append("}")
    shape = "\n".join(shape_lines)

    design_note = ""
    if item["logic"] in ("rob_design", "meta_design"):
        design_note = (
            f"\nThis review includes: {review_includes.upper()}. Only the "
            "sub-criteria relevant to that design set are listed below.\n")

    return f"""Assess **AMSTAR-2 Item {item['id']} — {item['name']}** for the systematic review in the attached PDF.

Item {item['id']}: {item['question']}

Study type: {study_type}
Guidance: {item['elaboration']}
{design_note}
Context (fields already extracted from the review):
{ctx_json}

Signaling sub-criteria — answer each Y or N:
{questions_block}

Return a JSON object with exactly this shape:
{shape}

Answer Y only when the review clearly reports or meets the sub-criterion; answer N when it is silent, ambiguous, or does not meet it. Rationales must be short (1-2 sentences) and quote the review verbatim where possible."""


def run_preflight(pdf_bytes: bytes,
                  extracted_fields: dict[str, str],
                  classification: dict[str, str]) -> dict[str, Any]:
    """One LLM call determining review composition + whether a quantitative
    synthesis was performed. Returns ``{review_includes, meta_analysis,
    rationale}``. Falls back to permissive defaults on a malformed response."""
    study_type = classification.get("study_type", "SR with Meta-Analysis")
    raw = _call_with_pdf(pdf_bytes, _PREFLIGHT_PROMPT, max_tokens=2048)

    ri = str(raw.get("review_includes", "")).strip().lower()
    if ri not in ("rct", "nrsi", "both"):
        ri = "both"  # conservative — sends all sub-criteria for items 9 + 11

    ma_raw = raw.get("meta_analysis")
    if isinstance(ma_raw, bool):
        meta_analysis = ma_raw
    elif isinstance(ma_raw, str):
        meta_analysis = ma_raw.strip().lower() in ("true", "yes", "y", "1")
    else:
        # Fall back to the classification — "SR with Meta-Analysis" implies one.
        meta_analysis = study_type == "SR with Meta-Analysis"

    return {
        "review_includes": ri,
        "meta_analysis": bool(meta_analysis),
        "rationale": str(raw.get("rationale", "")).strip(),
    }


def _assess_item(pdf_bytes: bytes, item: dict[str, Any], study_type: str,
                 extracted_fields: dict[str, str],
                 review_includes: str, meta_analysis: bool) -> dict[str, Any]:
    """LLM-assess one AMSTAR-2 item. Returns
    ``{id, name, critical, signals, rationales, judgement}``."""
    prompt = build_item_prompt(item, study_type, extracted_fields,
                               review_includes, meta_analysis)
    raw = _call_with_pdf(pdf_bytes, prompt, max_tokens=4096)

    signals: dict[str, str] = {}
    rationales: dict[str, str] = {}
    for s in _signals_for(item, review_includes):
        sid = s["id"]
        signals[sid] = _normalise_answer(raw.get(sid))
        rationales[sid] = str(raw.get(f"{sid}_rationale", "")).strip()

    judgement = amstar2_item_judge(item, signals, review_includes, meta_analysis)
    return {
        "id": item["id"],
        "name": item["name"],
        "critical": item["critical"],
        "signals": signals,
        "rationales": rationales,
        "judgement": judgement,
    }


def run(pdf_bytes: bytes,
        extracted_fields: dict[str, str],
        classification: dict[str, str],
        primary_outcome: str,
        progress: Callable[[int], None] | None = None,
        ) -> tuple[dict[str, Any], str, str]:
    """Run AMSTAR-2 against a systematic review.

    Returns ``(item_results, overall_confidence, "NA")``.

    - ``item_results`` is keyed by item id (``"1"`` … ``"16"``), each with
      ``{id, name, critical, signals, rationales, judgement}``. The preflight
      output is stored under ``item_results["preflight"]``.
    - ``overall_confidence`` is "High" / "Moderate" / "Low" / "Critically low".
    - the third element is always ``"NA"`` — direction-of-effect is a
      treatment-trial concept and does not apply to AMSTAR-2.

    ``primary_outcome`` is part of the shared RoB-tool signature; AMSTAR-2
    appraises the review as a whole and does not use it.
    """
    study_type = classification.get("study_type", "SR with Meta-Analysis")

    preflight = run_preflight(pdf_bytes, extracted_fields, classification)
    review_includes = preflight["review_includes"]
    meta_analysis = preflight["meta_analysis"]

    item_results: dict[str, Any] = {"preflight": preflight}
    item_ratings: dict[int, str] = {}

    for item in ITEMS:
        if progress:
            try:
                progress(item["id"])
            except Exception:
                pass

        # Meta-gated items with no quantitative synthesis: derive the
        # "No meta-analysis conducted" rating in code — no LLM call needed.
        if item.get("meta_gated") and not meta_analysis:
            result = {
                "id": item["id"],
                "name": item["name"],
                "critical": item["critical"],
                "signals": {},
                "rationales": {},
                "judgement": "No meta-analysis conducted",
                "na_derived": True,
            }
        else:
            result = _assess_item(pdf_bytes, item, study_type,
                                  extracted_fields, review_includes,
                                  meta_analysis)

        item_results[str(item["id"])] = result
        item_ratings[item["id"]] = result["judgement"]

    overall = amstar2_overall(item_ratings)
    return item_results, overall, "NA"


# ─────────────────────────────────────────────
# Developer-view exposure
# ─────────────────────────────────────────────
def prompt_catalog() -> dict[str, Any]:
    """Return the prompts + decision-tree source for the developer icon."""
    sample_fields = {"search_strategy": "<extracted value>"}
    item_entries = []
    for item in ITEMS:
        item_entries.append({
            "id": item["id"],
            "name": item["name"],
            "question": item["question"],
            "critical": item["critical"],
            "logic": item["logic"],
            "ratings": item["ratings"],
            "meta_gated": bool(item.get("meta_gated")),
            "elaboration": item["elaboration"],
            "signals": item["signals"],
            "relevant_fields": item["relevant_fields"],
            "prompt_template": build_item_prompt(
                item, "SR with Meta-Analysis", sample_fields,
                review_includes="both", meta_analysis=True),
        })
    return {
        "tool": "AMSTAR-2 (Shea 2017) — critical appraisal of systematic reviews",
        "citation": ("Shea BJ, Reeves BC, Wells G, et al. AMSTAR 2: a critical "
                     "appraisal tool for systematic reviews. BMJ 2017;358:j4008."),
        "system_prompt": _SYSTEM_PROMPT,
        "signal_options": list(SIGNAL_OPTIONS),
        "item_ratings": list(ITEM_RATINGS),
        "confidence_levels": list(CONFIDENCE_LEVELS),
        "critical_items": sorted(CRITICAL_ITEMS),
        "preflight_prompt": _PREFLIGHT_PROMPT,
        "items": item_entries,
        "item_decision_tree_code": inspect.getsource(amstar2_item_judge),
        "tiered_helper_code": inspect.getsource(_tiered_judge),
        "overall_algorithm_code": inspect.getsource(amstar2_overall),
        "v1_limitations": [
            "The 7 critical items are the published default set (2, 4, 7, 9, "
            "11, 13, 15); AMSTAR-2 invites reviewers to designate their own, "
            "but v1 hardcodes the default.",
            "'Partial Yes' and 'No meta-analysis conducted' are not counted as "
            "flaws in the overall-confidence algorithm (standard AMSTAR-2 "
            "interpretation).",
            "For reviews including both RCTs and NRSI, items 9 and 11 take the "
            "lower of the two design-specific ratings.",
            "Items 11/12/15 are set to 'No meta-analysis conducted' in code "
            "(no LLM call) when the preflight reports no quantitative synthesis.",
            "AMSTAR-2 rates the review's methodological quality, not the "
            "certainty of a body of evidence — GRADE, indirectness, and "
            "imprecision are skipped for systematic-review papers.",
        ],
    }
