"""Tests for AMSTAR-2 (systematic-review critical appraisal) + PRISMA 2020.

Covers the pure-Python parts that don't require LLM calls:
- The 16 AMSTAR-2 items + the 7 critical-item set.
- Per-item decision trees (all_required / one_of / tiered / rob_design / meta_design).
- The overall-confidence algorithm (High / Moderate / Low / Critically low).
- Meta-analysis NA gating for items 11/12/15.
- Study-type registry dispatch for the two SR types + the skip_grade flag.
- PRISMA 2020 checklist shape + prompt coverage.
- Export row flattening for an AMSTAR-2 result row.
- Developer-view prompt-catalog wiring.
"""

from __future__ import annotations

import pytest

from backend import quality_appraisal as qa
from backend.rob_tools import amstar2
from backend.reporting_guidelines import prisma2020


# ─────────────────────────────────────────────
# AMSTAR-2 item catalogue
# ─────────────────────────────────────────────
class TestItems:
    def test_sixteen_items(self):
        assert len(amstar2.ITEMS) == 16
        assert [it["id"] for it in amstar2.ITEMS] == list(range(1, 17))

    def test_critical_items_are_the_published_default_seven(self):
        assert amstar2.CRITICAL_ITEMS == frozenset({2, 4, 7, 9, 11, 13, 15})
        for it in amstar2.ITEMS:
            assert it["critical"] == (it["id"] in amstar2.CRITICAL_ITEMS)

    def test_meta_gated_items(self):
        gated = {it["id"] for it in amstar2.ITEMS if it.get("meta_gated")}
        assert gated == {11, 12, 15}

    def test_every_item_has_signals_and_logic(self):
        valid_logic = {"all_required", "one_of", "tiered", "rob_design", "meta_design"}
        for it in amstar2.ITEMS:
            assert it["logic"] in valid_logic
            assert it["signals"], f"item {it['id']} has no signals"
            for s in it["signals"]:
                assert s["id"] and s["text"]

    def test_tiered_items_have_a_partial_tier(self):
        for it in amstar2.ITEMS:
            if it["logic"] == "tiered":
                tiers = {s.get("tier") for s in it["signals"]}
                assert "partial" in tiers, f"item {it['id']} tiered but no partial tier"

    def test_design_aware_items_tag_each_signal(self):
        for it in amstar2.ITEMS:
            if it["logic"] in ("rob_design", "meta_design"):
                for s in it["signals"]:
                    assert s.get("design") in ("rct", "nrsi"), \
                        f"item {it['id']} signal {s['id']} missing design tag"


# ─────────────────────────────────────────────
# Decision tree — all_required / one_of
# ─────────────────────────────────────────────
class TestSimpleLogic:
    def test_all_required_needs_every_signal(self):
        item1 = amstar2._ITEMS_BY_ID[1]  # PICO — 4 signals, all required
        assert amstar2.amstar2_item_judge(
            item1, {"1.1": "Y", "1.2": "Y", "1.3": "Y", "1.4": "Y"}) == "Yes"
        assert amstar2.amstar2_item_judge(
            item1, {"1.1": "Y", "1.2": "Y", "1.3": "Y", "1.4": "N"}) == "No"
        assert amstar2.amstar2_item_judge(item1, {}) == "No"

    def test_one_of_needs_a_single_signal(self):
        item3 = amstar2._ITEMS_BY_ID[3]  # study-design explanation — one_of
        assert amstar2.amstar2_item_judge(
            item3, {"3.1": "N", "3.2": "Y", "3.3": "N"}) == "Yes"
        assert amstar2.amstar2_item_judge(
            item3, {"3.1": "N", "3.2": "N", "3.3": "N"}) == "No"
        assert amstar2.amstar2_item_judge(item3, {}) == "No"


# ─────────────────────────────────────────────
# Decision tree — tiered (Yes / Partial Yes / No)
# ─────────────────────────────────────────────
class TestTieredLogic:
    def test_item2_full_yes(self):
        item2 = amstar2._ITEMS_BY_ID[2]
        all_yes = {s["id"]: "Y" for s in item2["signals"]}
        assert amstar2.amstar2_item_judge(item2, all_yes) == "Yes"

    def test_item2_partial_yes(self):
        item2 = amstar2._ITEMS_BY_ID[2]
        # partial tier satisfied, yes tier not
        ans = {s["id"]: ("Y" if s["tier"] == "partial" else "N")
               for s in item2["signals"]}
        assert amstar2.amstar2_item_judge(item2, ans) == "Partial Yes"

    def test_item2_no_when_partial_incomplete(self):
        item2 = amstar2._ITEMS_BY_ID[2]
        ans = {s["id"]: "Y" for s in item2["signals"]}
        ans["2.1"] = "N"  # one partial-tier criterion missing
        assert amstar2.amstar2_item_judge(item2, ans) == "No"

    def test_item7_two_tier(self):
        item7 = amstar2._ITEMS_BY_ID[7]  # 7.1 partial, 7.2 yes
        assert amstar2.amstar2_item_judge(item7, {"7.1": "Y", "7.2": "Y"}) == "Yes"
        assert amstar2.amstar2_item_judge(item7, {"7.1": "Y", "7.2": "N"}) == "Partial Yes"
        assert amstar2.amstar2_item_judge(item7, {"7.1": "N", "7.2": "Y"}) == "No"


# ─────────────────────────────────────────────
# Decision tree — design-aware items 9 + 11
# ─────────────────────────────────────────────
class TestDesignAwareLogic:
    def test_item9_rct_review(self):
        item9 = amstar2._ITEMS_BY_ID[9]
        all_rct_yes = {"9.1": "Y", "9.2": "Y", "9.3": "Y", "9.4": "Y"}
        assert amstar2.amstar2_item_judge(
            item9, all_rct_yes, review_includes="rct") == "Yes"
        # partial tier only (9.1/9.2) satisfied
        assert amstar2.amstar2_item_judge(
            item9, {"9.1": "Y", "9.2": "Y", "9.3": "N", "9.4": "N"},
            review_includes="rct") == "Partial Yes"

    def test_item9_nrsi_review_uses_nrsi_signals(self):
        item9 = amstar2._ITEMS_BY_ID[9]
        all_nrsi_yes = {"9.5": "Y", "9.6": "Y", "9.7": "Y", "9.8": "Y"}
        assert amstar2.amstar2_item_judge(
            item9, all_nrsi_yes, review_includes="nrsi") == "Yes"

    def test_item9_both_takes_lower_rating(self):
        item9 = amstar2._ITEMS_BY_ID[9]
        # RCT set → Yes, NRSI set → Partial Yes ⇒ overall Partial Yes
        signals = {"9.1": "Y", "9.2": "Y", "9.3": "Y", "9.4": "Y",
                   "9.5": "Y", "9.6": "Y", "9.7": "N", "9.8": "N"}
        assert amstar2.amstar2_item_judge(
            item9, signals, review_includes="both") == "Partial Yes"

    def test_item11_meta_design_yes_no(self):
        item11 = amstar2._ITEMS_BY_ID[11]
        rct_yes = {"11.1": "Y", "11.2": "Y", "11.3": "Y"}
        assert amstar2.amstar2_item_judge(
            item11, rct_yes, review_includes="rct", meta_analysis=True) == "Yes"
        rct_partial = {"11.1": "Y", "11.2": "Y", "11.3": "N"}
        assert amstar2.amstar2_item_judge(
            item11, rct_partial, review_includes="rct", meta_analysis=True) == "No"


# ─────────────────────────────────────────────
# Meta-analysis NA gating (items 11, 12, 15)
# ─────────────────────────────────────────────
class TestMetaGating:
    def test_meta_gated_items_return_na_without_meta_analysis(self):
        for iid in (11, 12, 15):
            item = amstar2._ITEMS_BY_ID[iid]
            assert amstar2.amstar2_item_judge(
                item, {}, meta_analysis=False) == "No meta-analysis conducted"

    def test_non_gated_item_unaffected_by_meta_flag(self):
        item1 = amstar2._ITEMS_BY_ID[1]
        ans = {"1.1": "Y", "1.2": "Y", "1.3": "Y", "1.4": "Y"}
        assert amstar2.amstar2_item_judge(item1, ans, meta_analysis=False) == "Yes"

    def test_item15_all_required_with_meta_analysis(self):
        item15 = amstar2._ITEMS_BY_ID[15]
        assert amstar2.amstar2_item_judge(
            item15, {"15.1": "Y", "15.2": "Y"}, meta_analysis=True) == "Yes"
        assert amstar2.amstar2_item_judge(
            item15, {"15.1": "Y", "15.2": "N"}, meta_analysis=True) == "No"


# ─────────────────────────────────────────────
# Overall confidence rating (Shea 2017 algorithm)
# ─────────────────────────────────────────────
class TestOverallConfidence:
    def test_no_flaws_is_high(self):
        ratings = {iid: "Yes" for iid in range(1, 17)}
        assert amstar2.amstar2_overall(ratings) == "High"

    def test_one_noncritical_weakness_still_high(self):
        ratings = {iid: "Yes" for iid in range(1, 17)}
        ratings[1] = "No"  # item 1 is non-critical
        assert amstar2.amstar2_overall(ratings) == "High"

    def test_two_noncritical_weaknesses_is_moderate(self):
        ratings = {iid: "Yes" for iid in range(1, 17)}
        ratings[1] = "No"
        ratings[3] = "No"  # items 1 and 3 both non-critical
        assert amstar2.amstar2_overall(ratings) == "Moderate"

    def test_one_critical_flaw_is_low(self):
        ratings = {iid: "Yes" for iid in range(1, 17)}
        ratings[4] = "No"  # item 4 is critical
        assert amstar2.amstar2_overall(ratings) == "Low"

    def test_one_critical_flaw_with_noncritical_still_low(self):
        ratings = {iid: "Yes" for iid in range(1, 17)}
        ratings[4] = "No"   # critical
        ratings[1] = "No"   # non-critical
        ratings[3] = "No"   # non-critical
        assert amstar2.amstar2_overall(ratings) == "Low"

    def test_two_critical_flaws_is_critically_low(self):
        ratings = {iid: "Yes" for iid in range(1, 17)}
        ratings[2] = "No"
        ratings[9] = "No"  # two critical items
        assert amstar2.amstar2_overall(ratings) == "Critically low"

    def test_partial_yes_is_not_a_flaw(self):
        ratings = {iid: "Yes" for iid in range(1, 17)}
        ratings[2] = "Partial Yes"   # critical item — Partial Yes ≠ flaw
        ratings[4] = "Partial Yes"
        assert amstar2.amstar2_overall(ratings) == "High"

    def test_no_meta_analysis_is_not_a_flaw(self):
        ratings = {iid: "Yes" for iid in range(1, 17)}
        ratings[11] = "No meta-analysis conducted"  # critical item, but NA
        ratings[15] = "No meta-analysis conducted"
        assert amstar2.amstar2_overall(ratings) == "High"

    def test_empty_ratings_is_high(self):
        assert amstar2.amstar2_overall({}) == "High"


# ─────────────────────────────────────────────
# Answer normalisation
# ─────────────────────────────────────────────
class TestNormalise:
    def test_affirmatives_become_y(self):
        for v in ("Y", "y", "Yes", "yes", "YES", "true", "1"):
            assert amstar2._normalise_answer(v) == "Y"

    def test_everything_else_becomes_n(self):
        for v in ("N", "no", "", None, "maybe", "unclear", "?"):
            assert amstar2._normalise_answer(v) == "N"


# ─────────────────────────────────────────────
# Registry dispatch + skip_grade flag
# ─────────────────────────────────────────────
class TestRegistry:
    def test_sr_with_meta_analysis_registered(self):
        cfg = qa.dispatch("SR with Meta-Analysis")
        assert cfg is not None
        assert cfg["rob_tool"] == "amstar2"
        assert cfg["reporting_guideline"] == "prisma2020"
        assert cfg["skip_grade"] is True
        assert cfg["initial_grade"] is None

    def test_sr_without_meta_analysis_registered(self):
        cfg = qa.dispatch("SR without Meta-Analysis")
        assert cfg is not None
        assert cfg["rob_tool"] == "amstar2"
        assert cfg["reporting_guideline"] == "prisma2020"
        assert cfg["skip_grade"] is True

    def test_amstar2_runner_registered(self):
        assert qa._TOOL_RUNNERS["amstar2"] is amstar2.run

    def test_prisma2020_runner_registered(self):
        assert qa._GUIDELINE_RUNNERS["prisma2020"] is prisma2020.run

    def test_registry_keys_still_match_annotator_types(self):
        from backend import annotator as ann
        for key in qa.STUDY_TYPE_REGISTRY:
            assert key in ann.TYPE_FIELD_IDS


# ─────────────────────────────────────────────
# PRISMA 2020 checklist
# ─────────────────────────────────────────────
class TestPrisma2020:
    def test_item_count(self):
        # 27 numbered items with a/b/c/d sub-items → 42 entries.
        assert len(prisma2020.ITEMS) == 42

    def test_required_ids_present(self):
        ids = {it["id"] for it in prisma2020.ITEMS}
        for must in ("1", "2", "10a", "10b", "13a", "13f", "16a", "16b",
                     "20a", "20d", "23a", "23d", "24a", "24c", "27"):
            assert must in ids, f"PRISMA 2020 item {must} missing"

    def test_sections_cover_the_whole_checklist(self):
        sections = {it["section"] for it in prisma2020.ITEMS}
        assert sections == {"Title", "Abstract", "Introduction", "Methods",
                            "Results", "Discussion", "Other information"}

    def test_prompt_contains_every_item(self):
        prompt = prisma2020.build_prompt({"study_type": "SR with Meta-Analysis"})
        for it in prisma2020.ITEMS:
            assert f'**{it["id"]}**' in prompt

    def test_prompt_catalog_shape(self):
        cat = prisma2020.prompt_catalog()
        assert cat["guideline"] == "PRISMA 2020"
        assert len(cat["items"]) == 42
        assert "scoring_code" in cat


# ─────────────────────────────────────────────
# Export row flattening
# ─────────────────────────────────────────────
class TestAmstar2FlattenForExport:
    """flatten_result_row maps an AMSTAR-2 row to per-item + preflight columns."""

    def _make_amstar2_row(self):
        # Two critical "No" flaws (items 4 and 9) → Critically low.
        rob_domains = {
            "preflight": {"review_includes": "both", "meta_analysis": True,
                          "rationale": "Includes RCTs and cohort studies; random-effects meta-analysis."},
        }
        for it in amstar2.ITEMS:
            judgement = "Yes"
            if it["id"] in (4, 9):
                judgement = "No"
            elif it["id"] == 1:
                judgement = "No"  # non-critical weakness
            rob_domains[str(it["id"])] = {
                "id": it["id"], "name": it["name"], "critical": it["critical"],
                "signals": {s["id"]: "Y" for s in it["signals"]},
                "rationales": {}, "judgement": judgement,
            }
        return {
            "paper_id": 77,
            "filename": "review.pdf",
            "status": "ok",
            "study_type": "SR with Meta-Analysis",
            "rob_tool": "amstar2",
            "reporting_guideline": "prisma2020",
            "classification": {"major_category": "Synthesis", "subcategory": ""},
            "extracted_fields": {"citation_title": "A systematic review"},
            "rob_domains": rob_domains,
            "rob_overall": "Critically low",
            "rob_direction": "NA",
            "guideline": {"adhered": 30, "applicable": 40, "proportion": 0.75},
            "guideline_adhered": 30, "guideline_applicable": 40,
            "guideline_proportion": 0.75,
            "initial_grade": None, "updated_grade": None, "grade_explanation": None,
        }

    def test_flatten_includes_sixteen_item_judgements(self):
        row = qa.flatten_result_row(self._make_amstar2_row())
        for iid in range(1, 17):
            assert f"rob_d{iid}_judgement" in row
        assert row["rob_d4_judgement"] == "No"
        assert row["rob_d9_judgement"] == "No"
        assert row["rob_d2_judgement"] == "Yes"

    def test_flatten_includes_confidence_and_preflight(self):
        row = qa.flatten_result_row(self._make_amstar2_row())
        assert row["amstar2_confidence"] == "Critically low"
        assert row["amstar2_review_includes"] == "both"
        assert row["amstar2_meta_analysis"] is True

    def test_flatten_counts_critical_and_noncritical_flaws(self):
        row = qa.flatten_result_row(self._make_amstar2_row())
        assert row["amstar2_critical_flaws"] == 2          # items 4, 9
        assert row["amstar2_noncritical_weaknesses"] == 1  # item 1

    def test_flatten_includes_signal_answers(self):
        row = qa.flatten_result_row(self._make_amstar2_row())
        assert row["rob_1.1"] == "Y"
        assert row["rob_2.1"] == "Y"


# ─────────────────────────────────────────────
# Developer-view prompt catalogue
# ─────────────────────────────────────────────
class TestPromptCatalogWired:
    def test_catalog_contains_amstar2(self):
        cat = qa.prompt_catalog()
        assert "amstar2" in cat["rob_tools"]
        a2 = cat["rob_tools"]["amstar2"]
        assert len(a2["items"]) == 16
        assert a2["critical_items"] == [2, 4, 7, 9, 11, 13, 15]
        assert "item_decision_tree_code" in a2
        assert "overall_algorithm_code" in a2

    def test_catalog_contains_prisma2020(self):
        cat = qa.prompt_catalog()
        assert "prisma2020" in cat["reporting_guidelines"]
        assert cat["reporting_guidelines"]["prisma2020"]["guideline"] == "PRISMA 2020"

    def test_amstar2_prompt_catalog_self_consistent(self):
        cat = amstar2.prompt_catalog()
        assert cat["confidence_levels"] == ["High", "Moderate", "Low", "Critically low"]
        # every item has a rendered prompt template
        for entry in cat["items"]:
            assert entry["prompt_template"]
        assert cat["preflight_prompt"]
