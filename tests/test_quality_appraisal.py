"""Tests for Quality Appraisal AI.

Covers the pure-Python parts that don't require LLM calls:
- RoB 2 per-domain decision trees (cribsheet algorithms).
- RoB 2 overall aggregation (cribsheet p.24 criteria).
- GRADE downgrade logic.
- Study-type registry dispatch.
- CONSORT proportion math with N/A items.
- Primary-outcome picker fallback chain.
- Export row flattening.
- API auth gates (without hitting real AI).
"""

from __future__ import annotations

import json

import pytest

from backend import quality_appraisal as qa
from backend.rob_tools import rob2
from backend.reporting_guidelines import consort2025


# ─────────────────────────────────────────────
# RoB 2 Domain 1 — randomization process
# ─────────────────────────────────────────────
class TestDomain1:
    """Cribsheet p.6 flowchart."""

    def test_sequence_not_concealed_high(self):
        # 1.2 = N → High, regardless of 1.1 and 1.3
        for q11 in rob2.SIGNAL_OPTIONS:
            for q13 in rob2.SIGNAL_OPTIONS:
                assert rob2.rob2_domain1_judge({
                    "1.1": q11, "1.2": "N", "1.3": q13,
                }) == "High"
                assert rob2.rob2_domain1_judge({
                    "1.1": q11, "1.2": "PN", "1.3": q13,
                }) == "High"

    def test_concealed_but_not_random_high(self):
        # 1.2 Y/PY but 1.1 N/PN → High
        for q12 in ("Y", "PY"):
            for q11 in ("N", "PN"):
                for q13 in rob2.SIGNAL_OPTIONS:
                    assert rob2.rob2_domain1_judge({
                        "1.1": q11, "1.2": q12, "1.3": q13,
                    }) == "High"

    def test_concealed_random_no_baseline_problem_low(self):
        # 1.1 Y/PY/NI, 1.2 Y/PY, 1.3 N/PN/NI → Low
        for q11 in ("Y", "PY", "NI"):
            for q12 in ("Y", "PY"):
                for q13 in ("N", "PN", "NI"):
                    assert rob2.rob2_domain1_judge({
                        "1.1": q11, "1.2": q12, "1.3": q13,
                    }) == "Low"

    def test_concealed_random_baseline_problem_some(self):
        # 1.1 Y/PY/NI, 1.2 Y/PY, 1.3 Y/PY → Some concerns
        for q11 in ("Y", "PY", "NI"):
            for q12 in ("Y", "PY"):
                for q13 in ("Y", "PY"):
                    assert rob2.rob2_domain1_judge({
                        "1.1": q11, "1.2": q12, "1.3": q13,
                    }) == "Some concerns"

    def test_concealment_unknown_but_baseline_problem_high(self):
        # 1.2 NI + 1.3 Y/PY → High
        for q11 in rob2.SIGNAL_OPTIONS:
            assert rob2.rob2_domain1_judge({
                "1.1": q11, "1.2": "NI", "1.3": "Y",
            }) == "High"
            assert rob2.rob2_domain1_judge({
                "1.1": q11, "1.2": "NI", "1.3": "PY",
            }) == "High"

    def test_concealment_unknown_no_baseline_problem_some(self):
        for q11 in rob2.SIGNAL_OPTIONS:
            for q13 in ("N", "PN", "NI"):
                assert rob2.rob2_domain1_judge({
                    "1.1": q11, "1.2": "NI", "1.3": q13,
                }) == "Some concerns"


# ─────────────────────────────────────────────
# RoB 2 Domain 2 — deviations (assignment effect)
# ─────────────────────────────────────────────
class TestDomain2:
    def test_both_blinded_appropriate_analysis_low(self):
        # 2.1 N and 2.2 N → Part 1 Low; 2.6 Y → Part 2 Low → Low
        signals = {"2.1": "N", "2.2": "N", "2.3": "NI", "2.4": "NI",
                   "2.5": "NI", "2.6": "Y", "2.7": "NI"}
        assert rob2.rob2_domain2_judge(signals) == "Low"

    def test_unblinded_deviations_unbalanced_high(self):
        # 2.1 Y → aware → 2.3 Y → 2.4 Y → 2.5 N (unbalanced) → Part 1 High
        signals = {"2.1": "Y", "2.2": "Y", "2.3": "Y", "2.4": "Y",
                   "2.5": "N", "2.6": "Y", "2.7": "NI"}
        assert rob2.rob2_domain2_judge(signals) == "High"

    def test_inappropriate_analysis_large_impact_high(self):
        # 2.6 N, 2.7 Y → Part 2 High
        signals = {"2.1": "N", "2.2": "N", "2.3": "NI", "2.4": "NI",
                   "2.5": "NI", "2.6": "N", "2.7": "Y"}
        assert rob2.rob2_domain2_judge(signals) == "High"

    def test_inappropriate_analysis_small_impact_some(self):
        # 2.6 N, 2.7 N → Part 2 Some concerns
        signals = {"2.1": "N", "2.2": "N", "2.3": "NI", "2.4": "NI",
                   "2.5": "NI", "2.6": "N", "2.7": "N"}
        assert rob2.rob2_domain2_judge(signals) == "Some concerns"


# ─────────────────────────────────────────────
# RoB 2 Domain 3 — missing outcome data
# ─────────────────────────────────────────────
class TestDomain3:
    def test_complete_data_low(self):
        assert rob2.rob2_domain3_judge({
            "3.1": "Y", "3.2": "NI", "3.3": "NI", "3.4": "NI",
        }) == "Low"

    def test_evidence_unbiased_low(self):
        assert rob2.rob2_domain3_judge({
            "3.1": "N", "3.2": "Y", "3.3": "NI", "3.4": "NI",
        }) == "Low"

    def test_could_not_depend_on_true_value_low(self):
        assert rob2.rob2_domain3_judge({
            "3.1": "N", "3.2": "N", "3.3": "N", "3.4": "NI",
        }) == "Low"

    def test_likely_depended_high(self):
        assert rob2.rob2_domain3_judge({
            "3.1": "N", "3.2": "N", "3.3": "Y", "3.4": "Y",
        }) == "High"

    def test_could_depend_not_likely_some(self):
        assert rob2.rob2_domain3_judge({
            "3.1": "N", "3.2": "N", "3.3": "Y", "3.4": "N",
        }) == "Some concerns"


# ─────────────────────────────────────────────
# RoB 2 Domain 4 — measurement of the outcome
# ─────────────────────────────────────────────
class TestDomain4:
    def test_inappropriate_method_high(self):
        assert rob2.rob2_domain4_judge({
            "4.1": "Y", "4.2": "N", "4.3": "N", "4.4": "N", "4.5": "N",
        }) == "High"

    def test_differs_between_groups_high(self):
        assert rob2.rob2_domain4_judge({
            "4.1": "N", "4.2": "Y", "4.3": "N", "4.4": "N", "4.5": "N",
        }) == "High"

    def test_blinded_assessor_low(self):
        assert rob2.rob2_domain4_judge({
            "4.1": "N", "4.2": "N", "4.3": "N", "4.4": "NI", "4.5": "NI",
        }) == "Low"

    def test_unblinded_likely_biased_high(self):
        assert rob2.rob2_domain4_judge({
            "4.1": "N", "4.2": "N", "4.3": "Y", "4.4": "Y", "4.5": "Y",
        }) == "High"

    def test_unblinded_could_but_unlikely_some(self):
        assert rob2.rob2_domain4_judge({
            "4.1": "N", "4.2": "N", "4.3": "Y", "4.4": "Y", "4.5": "N",
        }) == "Some concerns"


# ─────────────────────────────────────────────
# RoB 2 Domain 5 — selection of the reported result
# ─────────────────────────────────────────────
class TestDomain5:
    def test_multiple_outcome_measurements_high(self):
        assert rob2.rob2_domain5_judge({
            "5.1": "Y", "5.2": "Y", "5.3": "N",
        }) == "High"

    def test_multiple_analyses_high(self):
        assert rob2.rob2_domain5_judge({
            "5.1": "Y", "5.2": "N", "5.3": "Y",
        }) == "High"

    def test_prespecified_no_selection_low(self):
        assert rob2.rob2_domain5_judge({
            "5.1": "Y", "5.2": "N", "5.3": "N",
        }) == "Low"

    def test_no_prespecification_some(self):
        assert rob2.rob2_domain5_judge({
            "5.1": "N", "5.2": "N", "5.3": "N",
        }) == "Some concerns"

    def test_ni_on_selection_some(self):
        assert rob2.rob2_domain5_judge({
            "5.1": "Y", "5.2": "NI", "5.3": "N",
        }) == "Some concerns"


# ─────────────────────────────────────────────
# Overall aggregation (cribsheet p.24)
# ─────────────────────────────────────────────
class TestOverall:
    def test_all_low_is_low(self):
        assert rob2.rob2_overall(["Low"] * 5) == "Low"

    def test_any_high_is_high(self):
        for i in range(5):
            doms = ["Low"] * 5
            doms[i] = "High"
            assert rob2.rob2_overall(doms) == "High"

    def test_one_some_is_some(self):
        doms = ["Low", "Low", "Some concerns", "Low", "Low"]
        assert rob2.rob2_overall(doms) == "Some concerns"

    def test_two_some_is_high(self):
        """Documented convention: 'multiple' Some concerns → High."""
        doms = ["Low", "Some concerns", "Some concerns", "Low", "Low"]
        assert rob2.rob2_overall(doms) == "High"

    def test_mixed_high_wins(self):
        doms = ["Low", "Some concerns", "High", "Some concerns", "Low"]
        assert rob2.rob2_overall(doms) == "High"

    def test_empty_input_low(self):
        # Degenerate case — no domains means nothing to downgrade
        assert rob2.rob2_overall([]) == "Low"


# ─────────────────────────────────────────────
# GRADE logic
# ─────────────────────────────────────────────
class TestGrade:
    def test_high_low_rob_stays_high(self):
        level, expl = qa.compute_grade("High", "Low", ["Low"] * 5)
        assert level == "High"
        assert "No downgrade" in expl

    def test_high_some_concerns_moderate(self):
        level, _ = qa.compute_grade("High", "Some concerns", ["Some concerns"] * 2)
        assert level == "Moderate"

    def test_high_high_rob_moderate_when_one_high_domain(self):
        level, _ = qa.compute_grade("High", "High", ["High", "Low", "Low", "Low", "Low"])
        assert level == "Moderate"

    def test_high_high_rob_downgrade_two_when_two_domains_high(self):
        level, expl = qa.compute_grade("High", "High",
                                        ["High", "High", "Low", "Low", "Low"])
        assert level == "Low"
        assert "2 levels" in expl

    def test_moderate_high_rob_low(self):
        level, _ = qa.compute_grade("Moderate", "High", ["High"])
        assert level == "Low"

    def test_low_high_rob_verylow(self):
        level, _ = qa.compute_grade("Low", "High", ["High"])
        assert level == "Very low"

    def test_verylow_stays_verylow(self):
        level, _ = qa.compute_grade("Very low", "High", ["High"])
        assert level == "Very low"

    def test_verylow_high_rob_two_domains_still_clamps(self):
        level, _ = qa.compute_grade("Very low", "High", ["High", "High"])
        assert level == "Very low"


# ─────────────────────────────────────────────
# Study-type registry
# ─────────────────────────────────────────────
class TestDispatch:
    def test_rct_is_registered(self):
        cfg = qa.dispatch("Randomized Controlled Trial")
        assert cfg is not None
        assert cfg["rob_tool"] == "rob2"
        assert cfg["reporting_guideline"] == "consort2025"
        assert cfg["initial_grade"] == "High"

    def test_unsupported_returns_none(self):
        assert qa.dispatch("Case-Control") is None
        assert qa.dispatch("Cohort Study") is None
        assert qa.dispatch("") is None
        assert qa.dispatch("Not A Real Study Type") is None

    def test_registry_keys_match_annotator_types(self):
        """Registry keys must be valid annotator study types so classification
        output drops straight into dispatch()."""
        from backend import annotator as ann
        for key in qa.STUDY_TYPE_REGISTRY:
            assert key in ann.TYPE_FIELD_IDS, (
                f"Registry key {key!r} is not in annotator.TYPE_FIELD_IDS; "
                "classification will never produce it and dispatch will always skip.")


# ─────────────────────────────────────────────
# CONSORT proportion math
# ─────────────────────────────────────────────
class TestConsort:
    def test_item_count_has_all_subitems(self):
        # 30 numbered items with several a/b/c/d sub-items — should be > 30.
        assert len(consort2025.ITEMS) >= 30
        assert len(consort2025.ITEMS) <= 50   # sanity upper bound
        # A few must-haves
        ids = {it["id"] for it in consort2025.ITEMS}
        assert {"1a", "1b", "2", "5a", "5b", "12a", "16a", "16b",
                "20a", "20b", "21a", "21b", "21c", "21d",
                "22a", "22b", "26", "27", "29", "30"}.issubset(ids)

    def test_prompt_contains_all_items(self):
        prompt = consort2025.build_prompt({"study_type": "Randomized Controlled Trial"})
        for it in consort2025.ITEMS:
            assert f"**{it['id']}**" in prompt, f"Item {it['id']} missing from prompt"

    def test_proportion_math_excludes_na(self):
        """run()'s post-processing should exclude adhered=None from the denominator."""
        # Simulate what run() does post-LLM
        raw = {
            "1a": {"adhered": True, "evidence": "foo"},
            "1b": {"adhered": True, "evidence": "foo"},
            "2":  {"adhered": False, "evidence": "bar"},
            "3":  {"adhered": None, "evidence": "N/A"},
        }
        # Mimic the counting logic
        applicable = [v for v in raw.values() if v["adhered"] is not None]
        adhered = sum(1 for v in applicable if v["adhered"] is True)
        assert len(applicable) == 3
        assert adhered == 2
        assert round(adhered / len(applicable), 3) == 0.667


# ─────────────────────────────────────────────
# Primary-outcome picker
# ─────────────────────────────────────────────
class TestPrimaryOutcome:
    def test_prefers_definition(self):
        fields = {
            "primary_outcome_definition": "Overall survival at 5 years.",
            "primary_outcome_measurement": "Kaplan-Meier estimate",
            "population_outcomes": "OS; PFS; adverse events",
        }
        out = qa.pick_primary_outcome(fields)
        assert "Overall survival" in out

    def test_falls_back_to_measurement(self):
        fields = {
            "primary_outcome_measurement": "HbA1c at 12 weeks",
        }
        assert "HbA1c" in qa.pick_primary_outcome(fields)

    def test_empty_returns_placeholder(self):
        out = qa.pick_primary_outcome({})
        assert "not specified" in out.lower()

    def test_trims_long_values(self):
        long_text = " ".join(["Overall"] * 100) + "."
        fields = {"primary_outcome_definition": long_text}
        assert len(qa.pick_primary_outcome(fields)) <= 200


# ─────────────────────────────────────────────
# Prompt catalog
# ─────────────────────────────────────────────
class TestPromptCatalog:
    def test_catalog_has_sections(self):
        cat = qa.prompt_catalog()
        assert "overview" in cat
        assert "rob_tools" in cat
        assert "rob2" in cat["rob_tools"]
        assert "reporting_guidelines" in cat
        assert "consort2025" in cat["reporting_guidelines"]
        assert "grade" in cat
        assert cat["credit_cost_per_paper"] == qa.CREDIT_COST_QA_PER_PAPER

    def test_rob2_catalog_shows_decision_tree_code(self):
        cat = qa.prompt_catalog()
        rob2_cat = cat["rob_tools"]["rob2"]
        assert len(rob2_cat["domains"]) == 5
        # Every domain must include its decision-tree source + prompt template.
        for d in rob2_cat["domains"]:
            assert "def " in d["decision_tree_code"]
            assert d["prompt_template"].strip()
            assert d["signals"]


# ─────────────────────────────────────────────
# Export flattening
# ─────────────────────────────────────────────
class TestFlattenForExport:
    def test_basic_row_shape(self):
        result = {
            "paper_id": 7, "filename": "trial.pdf", "status": "ok",
            "study_type": "Randomized Controlled Trial",
            "primary_outcome": "Overall survival",
            "classification": {"major_category": "Primary Studies",
                                "subcategory": "Randomized Controlled",
                                "study_type": "Randomized Controlled Trial"},
            "extracted_fields": {"citation_title": "Test trial",
                                  "citation_authors": "Smith et al.",
                                  "citation_journal": "NEJM",
                                  "citation_year": "2024",
                                  "clinical_trial_phase": "Phase 3",
                                  "industry_sponsored": "Yes"},
            "rob_domains": {
                "1": {"judgement": "Low", "signals": {"1.1": "Y", "1.2": "Y", "1.3": "N"}},
                "2": {"judgement": "Some concerns", "signals": {"2.1": "Y", "2.2": "Y"}},
                "3": {"judgement": "Low", "signals": {"3.1": "Y"}},
                "4": {"judgement": "Low", "signals": {"4.1": "N"}},
                "5": {"judgement": "Low", "signals": {"5.1": "Y"}},
            },
            "rob_overall": "Some concerns", "rob_direction": "NA",
            "guideline": {"proportion": 0.87, "adhered": 35, "applicable": 40},
            "guideline_proportion": 0.87, "guideline_adhered": 35, "guideline_applicable": 40,
            "initial_grade": "High", "updated_grade": "Moderate",
            "grade_explanation": "Downgraded 1 level for Some concerns in risk of bias.",
        }
        row = qa.flatten_result_row(result)
        assert row["title"] == "Test trial"
        assert row["authors"] == "Smith et al."
        assert row["rob_overall"] == "Some concerns"
        assert row["consort_proportion"] == 0.87
        assert row["initial_grade"] == "High"
        assert row["updated_grade"] == "Moderate"
        assert row["clinical_trial_phase"] == "Phase 3"
        assert row["rob_d1_judgement"] == "Low"
        assert row["rob_1.1"] == "Y"
        assert row["rob_2.1"] == "Y"

    def test_error_row_preserves_filename(self):
        result = {
            "paper_id": 7, "filename": "trial.pdf", "status": "error",
            "error_message": "Classification failed",
            "classification": {}, "extracted_fields": {}, "rob_domains": {},
            "guideline": {},
        }
        row = qa.flatten_result_row(result)
        assert row["filename"] == "trial.pdf"
        assert row["status"] == "error"
        assert row["error_message"] == "Classification failed"


# ─────────────────────────────────────────────
# API — auth gates (without hitting AI)
# ─────────────────────────────────────────────
class TestApiAuth:
    def test_page_redirects_when_not_authenticated(self, client):
        r = client.get("/quality-appraisal", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/login"

    def test_supported_types_requires_auth(self, client):
        r = client.get("/api/quality-appraisal/supported-types")
        assert r.status_code == 401

    def test_runs_list_requires_auth(self, client):
        r = client.get("/api/quality-appraisal/runs")
        assert r.status_code == 401

    def test_supported_types_ok_for_user(self, client, test_user):
        r = client.get("/api/quality-appraisal/supported-types",
                       cookies={"rubricgen_session": test_user["cookie"]})
        assert r.status_code == 200
        types = r.json()
        assert any(t["study_type"] == "Randomized Controlled Trial" for t in types)

    def test_prompts_endpoint_ok_for_user(self, client, test_user):
        r = client.get("/api/quality-appraisal/prompts",
                       cookies={"rubricgen_session": test_user["cookie"]})
        assert r.status_code == 200
        cat = r.json()
        assert "rob_tools" in cat
        assert "rob2" in cat["rob_tools"]

    def test_runs_list_empty_for_new_user(self, client, test_user):
        r = client.get("/api/quality-appraisal/runs",
                       cookies={"rubricgen_session": test_user["cookie"]})
        assert r.status_code == 200
        assert r.json() == []

    def test_run_create_rejects_empty_paper_ids(self, client, test_user):
        r = client.post("/api/quality-appraisal/runs",
                        json={"paper_ids": []},
                        cookies={"rubricgen_session": test_user["cookie"]})
        assert r.status_code == 400

    def test_run_create_rejects_unowned_papers(self, client, test_user):
        r = client.post("/api/quality-appraisal/runs",
                        json={"paper_ids": [999999]},
                        cookies={"rubricgen_session": test_user["cookie"]})
        assert r.status_code == 400
        assert "unknown or unowned" in r.text

    def test_run_get_404_for_missing(self, client, test_user):
        r = client.get("/api/quality-appraisal/runs/99999",
                       cookies={"rubricgen_session": test_user["cookie"]})
        assert r.status_code == 404
