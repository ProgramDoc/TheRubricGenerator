"""Tests for QUADAS-3 v1.2 — diagnostic test accuracy.

Covers the pure-Python parts that don't require LLM calls:
- Per-domain decision tree (Phase 5 mapping of signaling answers → judgement).
- Overall RoB aggregation (Phase 6 rule).
- Overall applicability aggregation (3 of 4 domains).
- Estimate normalization in `extract_estimates` (mocked LLM).
- Study-type registry membership (Diagnostic Accuracy → quadas3 + stard + skip_grade_extras).
- GRADE downgrade for QUADAS-3 outcome labels.
- STARD checklist coverage.
- Annotator-type compatibility (registry key matches `annotator.TYPE_FIELD_IDS`).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend import quality_appraisal as qa
from backend.rob_tools import quadas3
from backend.reporting_guidelines import stard


# ─────────────────────────────────────────────
# Per-domain decision tree (Phase 5)
# ─────────────────────────────────────────────
class TestQuadas3DomainJudge:
    """Phase 5 rule: all Y/PY → Low; any N/PN → High; otherwise II."""

    def test_all_yes_low(self):
        for ans in ("Y", "PY"):
            assert quadas3.quadas3_domain_judge({
                "1.1": ans, "1.2": ans, "1.3": ans, "1.4": ans,
            }) == "Low"

    def test_mixed_yes_py_low(self):
        assert quadas3.quadas3_domain_judge({
            "1.1": "Y", "1.2": "PY", "1.3": "Y", "1.4": "PY",
        }) == "Low"

    def test_any_no_high(self):
        # Single N anywhere → High
        for sid in ("1.1", "1.2", "1.3", "1.4"):
            sigs = {"1.1": "Y", "1.2": "Y", "1.3": "Y", "1.4": "Y"}
            sigs[sid] = "N"
            assert quadas3.quadas3_domain_judge(sigs) == "High", (
                f"Expected High when {sid}=N, got otherwise")

    def test_any_pn_high(self):
        for sid in ("1.1", "1.2", "1.3", "1.4"):
            sigs = {"1.1": "Y", "1.2": "Y", "1.3": "Y", "1.4": "Y"}
            sigs[sid] = "PN"
            assert quadas3.quadas3_domain_judge(sigs) == "High"

    def test_all_ni_insufficient(self):
        assert quadas3.quadas3_domain_judge({
            "1.1": "NI", "1.2": "NI", "1.3": "NI", "1.4": "NI",
        }) == "Insufficient information"

    def test_mixed_yes_ni_insufficient(self):
        # Y/PY + at least one NI → "Insufficient information" (Phase 5)
        assert quadas3.quadas3_domain_judge({
            "1.1": "Y", "1.2": "NI", "1.3": "PY", "1.4": "Y",
        }) == "Insufficient information"

    def test_no_overrides_ni(self):
        # N/PN takes priority over NI — any flagged signal → High
        assert quadas3.quadas3_domain_judge({
            "1.1": "NI", "1.2": "N", "1.3": "NI", "1.4": "NI",
        }) == "High"

    def test_empty_signals_insufficient(self):
        assert quadas3.quadas3_domain_judge({}) == "Insufficient information"


# ─────────────────────────────────────────────
# Overall aggregation (Phase 6)
# ─────────────────────────────────────────────
class TestQuadas3Overall:
    """Phase 6 rule: any High → High; all Low → Low; otherwise II."""

    def test_all_low(self):
        assert quadas3.quadas3_overall(["Low", "Low", "Low", "Low"]) == "Low"

    def test_any_high(self):
        for slot in range(4):
            judgements = ["Low"] * 4
            judgements[slot] = "High"
            assert quadas3.quadas3_overall(judgements) == "High"

    def test_high_takes_priority_over_ii(self):
        assert quadas3.quadas3_overall(
            ["Low", "Insufficient information", "High", "Low"]
        ) == "High"

    def test_any_ii_no_high(self):
        assert quadas3.quadas3_overall(
            ["Low", "Insufficient information", "Low", "Low"]
        ) == "Insufficient information"

    def test_all_ii(self):
        assert quadas3.quadas3_overall(["Insufficient information"] * 4) == \
            "Insufficient information"

    def test_empty(self):
        assert quadas3.quadas3_overall([]) == "Insufficient information"


# ─────────────────────────────────────────────
# Applicability aggregation (3 domains only — Analysis is excluded)
# ─────────────────────────────────────────────
class TestQuadas3ApplicabilityOverall:
    def test_three_low_all_low(self):
        assert quadas3.quadas3_applicability_overall(["Low", "Low", "Low"]) == "Low"

    def test_three_high_any_high(self):
        assert quadas3.quadas3_applicability_overall(["Low", "High", "Low"]) == "High"

    def test_three_ii(self):
        assert quadas3.quadas3_applicability_overall(
            ["Low", "Insufficient information", "Low"]
        ) == "Insufficient information"


# ─────────────────────────────────────────────
# DOMAINS structure invariants
# ─────────────────────────────────────────────
class TestQuadas3Domains:
    def test_four_domains(self):
        assert len(quadas3.DOMAINS) == 4

    def test_domain_ids_sequential(self):
        assert [d["id"] for d in quadas3.DOMAINS] == [1, 2, 3, 4]

    def test_first_three_have_applicability_fourth_does_not(self):
        for d in quadas3.DOMAINS[:3]:
            assert d["has_applicability"] is True, f"D{d['id']} should have applicability"
        assert quadas3.DOMAINS[3]["has_applicability"] is False, "D4 (Analysis) is RoB-only"

    def test_signal_counts_match_docx(self):
        # Verbatim from QUADAS-3 v1.2 docx Tables 6-9:
        #   D1 (Participants):     4 signals
        #   D2 (Index Test):       4 signals
        #   D3 (Target Condition): 8 signals
        #   D4 (Analysis):         4 signals
        expected = {1: 4, 2: 4, 3: 8, 4: 4}
        for d in quadas3.DOMAINS:
            assert len(d["signals"]) == expected[d["id"]], (
                f"D{d['id']} expected {expected[d['id']]} signals, got {len(d['signals'])}")

    def test_signal_options_match_docx(self):
        # Y / PY / PN / N / NI per Phase 5
        for d in quadas3.DOMAINS:
            for sig in d["signals"]:
                assert sig["options"] == list(quadas3.SIGNAL_OPTIONS), (
                    f"Signal {sig['id']} has unexpected options {sig['options']}")

    def test_signal_ids_match_domain(self):
        # Signal ids are dotted: D{n}.{m}
        for d in quadas3.DOMAINS:
            for sig in d["signals"]:
                assert sig["id"].startswith(f"{d['id']}."), (
                    f"Signal {sig['id']} doesn't match domain {d['id']}")


# ─────────────────────────────────────────────
# Estimate extraction — output normalisation
# ─────────────────────────────────────────────
class TestExtractEstimates:
    """`extract_estimates` mocks `_call_with_pdf` to verify the post-processing."""

    def test_returns_list(self):
        with patch("backend.rob_tools.quadas3._call_with_pdf",
                   return_value={"estimates": [
                       {"description": "PCR vs gold standard",
                        "subgroup": "overall",
                        "index_test": "qPCR",
                        "threshold": "Ct < 35",
                        "reference_standard": "viral culture",
                        "unit_of_analysis": "participant",
                        "sensitivity": "84% (95% CI 79–88)",
                        "specificity": "92% (95% CI 88–95)",
                        "n": "412"}]}):
            result = quadas3.extract_estimates(b"\x00fake-pdf", {})
        assert len(result) == 1
        assert result[0]["id"] == 1
        assert result[0]["description"] == "PCR vs gold standard"
        assert result[0]["sensitivity"].startswith("84%")

    def test_assigns_synthetic_ids(self):
        with patch("backend.rob_tools.quadas3._call_with_pdf",
                   return_value={"estimates": [
                       {"description": "A"},
                       {"description": "B"},
                       {"description": "C"},
                   ]}):
            result = quadas3.extract_estimates(b"\x00fake-pdf", {})
        assert [e["id"] for e in result] == [1, 2, 3]

    def test_synthesizes_description_from_subgroup(self):
        with patch("backend.rob_tools.quadas3._call_with_pdf",
                   return_value={"estimates": [
                       {"subgroup": "<2yo children", "threshold": "Ct < 35"},
                   ]}):
            result = quadas3.extract_estimates(b"\x00fake-pdf", {})
        assert result[0]["description"] != ""
        # Description is built from non-empty descriptor fields
        assert "Ct < 35" in result[0]["description"] or \
               "<2yo" in result[0]["description"]

    def test_empty_estimates_returns_empty(self):
        with patch("backend.rob_tools.quadas3._call_with_pdf",
                   return_value={"estimates": []}):
            assert quadas3.extract_estimates(b"\x00", {}) == []

    def test_missing_estimates_key_returns_empty(self):
        with patch("backend.rob_tools.quadas3._call_with_pdf",
                   return_value={"some_other_key": []}):
            assert quadas3.extract_estimates(b"\x00", {}) == []

    def test_drops_non_dict_entries(self):
        with patch("backend.rob_tools.quadas3._call_with_pdf",
                   return_value={"estimates": [
                       {"description": "A"},
                       "garbage",
                       42,
                       {"description": "B"},
                   ]}):
            result = quadas3.extract_estimates(b"\x00", {})
        assert [e["description"] for e in result] == ["A", "B"]


# ─────────────────────────────────────────────
# Study-type registry — Diagnostic Accuracy
# ─────────────────────────────────────────────
class TestQuadas3Registry:
    def test_diagnostic_accuracy_registered(self):
        cfg = qa.dispatch("Diagnostic Accuracy")
        assert cfg is not None
        assert cfg["rob_tool"] == "quadas3"
        assert cfg["reporting_guideline"] == "stard"
        assert cfg["initial_grade"] == "High"
        assert cfg.get("skip_grade_extras") is True
        assert cfg.get("supports_estimates") is True

    def test_quadas3_runner_registered(self):
        assert qa._TOOL_RUNNERS["quadas3"] is quadas3.run

    def test_stard_runner_registered(self):
        assert qa._GUIDELINE_RUNNERS["stard"] is stard.run

    def test_estimate_extractor_registered(self):
        assert qa._ESTIMATE_EXTRACTORS["quadas3"] is quadas3.extract_estimates

    def test_registry_key_is_valid_annotator_type(self):
        """Diagnostic Accuracy must be in annotator.TYPE_FIELD_IDS so
        classification output drops straight into dispatch()."""
        from backend import annotator as ann
        assert "Diagnostic Accuracy" in ann.TYPE_FIELD_IDS


# ─────────────────────────────────────────────
# GRADE downgrade — QUADAS-3 outcome labels
# ─────────────────────────────────────────────
class TestQuadas3GradeDowngrade:
    def test_quadas3_low_no_downgrade(self):
        level, expl = qa.compute_grade("High", "Low", ["Low"] * 4,
                                        indirectness_levels=0,
                                        imprecision_levels=0)
        assert level == "High"
        assert "No downgrade" in expl

    def test_quadas3_high_single_domain_downgrades_one(self):
        level, expl = qa.compute_grade(
            "High", "High", ["High", "Low", "Low", "Low"],
            indirectness_levels=0, imprecision_levels=0)
        assert level == "Moderate"
        assert "1 level" in expl

    def test_quadas3_high_two_domains_downgrades_two(self):
        level, expl = qa.compute_grade(
            "High", "High", ["High", "High", "Low", "Low"],
            indirectness_levels=0, imprecision_levels=0)
        assert level == "Low"
        assert "2 levels" in expl

    def test_quadas3_insufficient_information_downgrades_one(self):
        level, expl = qa.compute_grade(
            "High", "Insufficient information",
            ["Insufficient information", "Low", "Low", "Low"],
            indirectness_levels=0, imprecision_levels=0)
        assert level == "Moderate"
        assert "1 level" in expl
        assert "QUADAS-3" in expl or "Insufficient" in expl

    def test_rob_downgrade_returns_zero_for_low(self):
        levels, _reason = qa._rob_downgrade("Low", ["Low"] * 4)
        assert levels == 0

    def test_rob_downgrade_returns_one_for_insufficient(self):
        levels, reason = qa._rob_downgrade("Insufficient information",
                                            ["Insufficient information"])
        assert levels == 1
        assert "QUADAS-3" in reason or "Insufficient" in reason


# ─────────────────────────────────────────────
# STARD checklist
# ─────────────────────────────────────────────
class TestStard:
    def test_item_count(self):
        # STARD 2015 has 30 numbered items with a/b sub-items at 10, 12, 13,
        # 21 — totalling 30 - 4 + 8 = 34 entries.
        assert len(stard.ITEMS) == 34

    def test_required_ids_present(self):
        ids = {it["id"] for it in stard.ITEMS}
        # A few must-haves from the BMJ 2015 publication
        required = {"1", "2", "3", "4", "5", "6", "7", "8", "9",
                    "10a", "10b", "11", "12a", "12b", "13a", "13b",
                    "14", "15", "16", "17", "18", "19", "20",
                    "21a", "21b", "22", "23", "24", "25",
                    "26", "27", "28", "29", "30"}
        assert required.issubset(ids), f"Missing STARD ids: {required - ids}"

    def test_prompt_contains_all_items(self):
        prompt = stard.build_prompt({"study_type": "Diagnostic Accuracy"})
        for it in stard.ITEMS:
            assert f"**{it['id']}**" in prompt, f"Item {it['id']} missing from prompt"

    def test_proportion_math_excludes_na(self):
        raw = {
            "1": {"adhered": True, "evidence": "yes"},
            "5": {"adhered": False, "evidence": "missing"},
            "25": {"adhered": None, "evidence": "N/A — non-invasive imaging"},
            "23": {"adhered": True, "evidence": "2x2 reported"},
        }
        applicable = [v for v in raw.values() if v["adhered"] is not None]
        adhered = sum(1 for v in applicable if v["adhered"] is True)
        assert len(applicable) == 3
        assert adhered == 2
        assert round(adhered / len(applicable), 3) == 0.667


# ─────────────────────────────────────────────
# Prompt catalog — developer view exposure
# ─────────────────────────────────────────────
class TestQuadas3PromptCatalog:
    def test_returns_full_structure(self):
        cat = quadas3.prompt_catalog()
        assert cat["tool"].startswith("QUADAS-3")
        assert cat["judgements"] == ["Low", "High", "Insufficient information"]
        assert cat["applicability_options"] == ["Low", "High", "Insufficient information"]
        assert len(cat["domains"]) == 4

    def test_includes_decision_tree_source(self):
        cat = quadas3.prompt_catalog()
        # Each domain entry exposes the decision-tree source via inspect.getsource
        for d in cat["domains"]:
            assert "def " in d["decision_tree_code"]

    def test_main_prompt_catalog_includes_quadas3(self):
        cat = qa.prompt_catalog()
        assert "quadas3" in cat["rob_tools"]
        assert cat["rob_tools"]["quadas3"]["tool"].startswith("QUADAS-3")

    def test_main_prompt_catalog_includes_stard(self):
        cat = qa.prompt_catalog()
        assert "stard" in cat["reporting_guidelines"]
        assert "STARD" in cat["reporting_guidelines"]["stard"]["guideline"]


# ─────────────────────────────────────────────
# DOMAIN_JUDGES registry
# ─────────────────────────────────────────────
class TestDomainJudges:
    def test_has_four_judges(self):
        assert set(quadas3.DOMAIN_JUDGES.keys()) == {1, 2, 3, 4}

    def test_all_judges_callable(self):
        for k, fn in quadas3.DOMAIN_JUDGES.items():
            assert callable(fn), f"Judge {k} is not callable"

    def test_all_judges_use_same_logic(self):
        # Phase 5 is uniform across QUADAS-3 domains, so all four registry
        # entries point at the same conservative function.
        signals_low = {"x.1": "Y", "x.2": "Y"}
        signals_high = {"x.1": "Y", "x.2": "N"}
        for fn in quadas3.DOMAIN_JUDGES.values():
            assert fn(signals_low) == "Low"
            assert fn(signals_high) == "High"


# ─────────────────────────────────────────────
# Run() integration with mocked LLM — verifies wiring + return shape
# ─────────────────────────────────────────────
class TestQuadas3Run:
    """End-to-end mocked-LLM smoke test: 4 domain calls + applicability
    aggregation, validates `run()` returns the 4-tuple."""

    def _domain_response(self, domain_id, all_yes=True, applicability="Low"):
        from backend.rob_tools.quadas3 import DOMAINS
        d = next(d for d in DOMAINS if d["id"] == domain_id)
        ans = "Y" if all_yes else "N"
        out = {}
        for sig in d["signals"]:
            out[sig["id"]] = ans
            out[f"{sig['id']}_rationale"] = f"rationale for {sig['id']}"
        if d["has_applicability"]:
            out["applicability_judgement"] = applicability
            out["applicability_rationale"] = f"applicability rationale (D{domain_id})"
        return out

    def test_run_returns_four_tuple_all_low(self):
        # _assess_domain calls _call_with_pdf once per domain. We patch the
        # module-level reference so each call returns the appropriate
        # domain-specific response.
        responses = [self._domain_response(1, all_yes=True),
                      self._domain_response(2, all_yes=True),
                      self._domain_response(3, all_yes=True),
                      self._domain_response(4, all_yes=True)]
        with patch("backend.rob_tools.quadas3._call_with_pdf",
                   side_effect=responses):
            domains, rob, direction, app = quadas3.run(
                b"\x00fake-pdf",
                extracted_fields={},
                classification={"study_type": "Diagnostic Accuracy"},
                primary_outcome="acute appendicitis",
            )
        assert rob == "Low"
        assert direction == "NA"
        assert app == "Low"
        assert set(domains.keys()) == {"1", "2", "3", "4"}
        for did in ("1", "2", "3"):
            assert "applicability_judgement" in domains[did]
        assert "applicability_judgement" not in domains["4"]

    def test_run_high_when_any_domain_high(self):
        responses = [self._domain_response(1, all_yes=False),  # → High
                      self._domain_response(2, all_yes=True),
                      self._domain_response(3, all_yes=True),
                      self._domain_response(4, all_yes=True)]
        with patch("backend.rob_tools.quadas3._call_with_pdf",
                   side_effect=responses):
            _, rob, _, _ = quadas3.run(
                b"\x00", extracted_fields={},
                classification={"study_type": "Diagnostic Accuracy"},
                primary_outcome="x")
        assert rob == "High"

    def test_run_threads_review_context_into_prompts(self):
        captured_prompts = []

        def _spy(_pdf, prompt, **_):
            captured_prompts.append(prompt)
            return self._domain_response(len(captured_prompts), all_yes=True)

        with patch("backend.rob_tools.quadas3._call_with_pdf", side_effect=_spy):
            quadas3.run(
                b"\x00", extracted_fields={},
                classification={"study_type": "Diagnostic Accuracy"},
                primary_outcome="x",
                review_context="ED patients with abdominal pain; ideal trial: ...")

        # Review context must surface in the first 3 (applicability-bearing)
        # prompts. The 4th (Analysis) doesn't have an applicability section.
        for p in captured_prompts[:3]:
            assert "ED patients with abdominal pain" in p, (
                "review_context not threaded into applicability prompt")
