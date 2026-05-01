"""Tests for GRADE imprecision — single-trial assessment.

Covers the pure-Python parts that don't require LLM calls:
- Severity decision tree (count of reds/oranges → severity tier).
- Severity-tier explanation strings.
- Judgement normaliser (LLM output coercion + N/A handling).
- Outcome-type heuristic (binary vs continuous).
- Prompt-builder behaviour with and without thresholds, binary vs continuous.
- prompt_catalog shape.
- compute_grade combining RoB + indirectness + imprecision downgrades.
- flatten_result_row including imprecision columns.
"""

from __future__ import annotations

import pytest

from backend import imprecision as imprec
from backend import quality_appraisal as qa


# ─────────────────────────────────────────────
# Decision tree — _judgement_severity
# ─────────────────────────────────────────────
class TestSeverityTree:
    def test_all_precise_no_downgrade(self):
        sev, levels, _ = imprec._judgement_severity({
            "ci_width":     "precise",
            "sample_size":  "precise",
            "event_count":  "precise",
            "fragility":    "precise",
        })
        assert sev == "none"
        assert levels == 0

    def test_all_probably_precise_no_downgrade(self):
        sev, levels, _ = imprec._judgement_severity({
            sid: "probably_precise" for sid in imprec.SUBDOMAIN_IDS
        })
        assert sev == "none"
        assert levels == 0

    def test_single_borderline_no_downgrade(self):
        # One probably_not_precise alone is "inherent uncertainty" — don't downgrade
        sev, levels, _ = imprec._judgement_severity({
            "ci_width":     "probably_not_precise",
            "sample_size":  "probably_precise",
            "event_count":  "precise",
            "fragility":    "precise",
        })
        assert sev == "none"
        assert levels == 0

    def test_two_oranges_serious(self):
        # Two probably_not_precise → serious (1 level)
        sev, levels, counts = imprec._judgement_severity({
            "ci_width":     "probably_not_precise",
            "sample_size":  "probably_not_precise",
            "event_count":  "precise",
            "fragility":    "precise",
        })
        assert sev == "serious"
        assert levels == 1
        assert counts["oranges"] == 2

    def test_one_red_serious(self):
        # Single not_precise → serious
        sev, levels, _ = imprec._judgement_severity({
            "ci_width":     "precise",
            "sample_size":  "precise",
            "event_count":  "precise",
            "fragility":    "not_precise",
        })
        assert sev == "serious"
        assert levels == 1

    def test_two_reds_very_serious(self):
        sev, levels, _ = imprec._judgement_severity({
            "ci_width":     "not_precise",
            "sample_size":  "not_precise",
            "event_count":  "precise",
            "fragility":    "precise",
        })
        assert sev == "very_serious"
        assert levels == 2

    def test_three_reds_extremely_serious(self):
        sev, levels, _ = imprec._judgement_severity({
            "ci_width":     "not_precise",
            "sample_size":  "not_precise",
            "event_count":  "not_precise",
            "fragility":    "precise",
        })
        assert sev == "extremely_serious"
        assert levels == 3

    def test_four_reds_extremely_serious_capped(self):
        # 4 reds is still extremely_serious (3 levels, not 4) — GRADE caps at -3.
        sev, levels, counts = imprec._judgement_severity({
            "ci_width":     "not_precise",
            "sample_size":  "not_precise",
            "event_count":  "not_precise",
            "fragility":    "not_precise",
        })
        assert sev == "extremely_serious"
        assert levels == 3
        assert counts["reds"] == 4

    def test_one_red_dominates_oranges(self):
        # 1 red + 1 orange → still serious (red rule applies first)
        sev, levels, _ = imprec._judgement_severity({
            "ci_width":     "not_precise",
            "sample_size":  "probably_not_precise",
            "event_count":  "precise",
            "fragility":    "precise",
        })
        assert sev == "serious"
        assert levels == 1

    def test_continuous_outcome_event_count_na_excluded(self):
        # event_count normalized to 'precise' (via the N/A→precise alias)
        # should NOT contribute to reds/oranges, so a single not_precise CI
        # only triggers 'serious'.
        sev, levels, counts = imprec._judgement_severity({
            "ci_width":     "not_precise",
            "sample_size":  "precise",
            "event_count":  "precise",  # would have been n_a from the LLM
            "fragility":    "precise",
        })
        assert sev == "serious"
        assert levels == 1
        assert counts["reds"] == 1


# ─────────────────────────────────────────────
# Explanation strings
# ─────────────────────────────────────────────
class TestSeverityExplanation:
    def test_none_explanation(self):
        msg = imprec.severity_explanation("none", {"reds": 0, "oranges": 0}, {})
        assert "No serious imprecision" in msg

    def test_serious_lists_drivers(self):
        per_sub = {
            "ci_width":     "not_precise",
            "sample_size":  "precise",
            "event_count":  "precise",
            "fragility":    "precise",
        }
        msg = imprec.severity_explanation("serious", {"reds": 1, "oranges": 0}, per_sub)
        assert "Serious" in msg
        assert "Confidence-interval width" in msg

    def test_very_serious_lists_two_drivers(self):
        per_sub = {
            "ci_width":     "not_precise",
            "sample_size":  "not_precise",
            "event_count":  "precise",
            "fragility":    "precise",
        }
        msg = imprec.severity_explanation("very_serious", {"reds": 2, "oranges": 0}, per_sub)
        assert "Very serious" in msg
        assert "Confidence-interval width" in msg
        assert "Sample-size adequacy" in msg


# ─────────────────────────────────────────────
# Judgement normaliser
# ─────────────────────────────────────────────
class TestNormalizeJudgement:
    @pytest.mark.parametrize("raw,expected", [
        ("precise", "precise"),
        ("PRECISE", "precise"),
        ("probably_precise", "probably_precise"),
        ("Probably Precise", "probably_precise"),
        ("probably-not-precise", "probably_not_precise"),
        ("not_precise", "not_precise"),
        ("sufficiently_precise", "precise"),
        ("probably_sufficiently_precise", "probably_precise"),
        ("not_sufficiently_precise", "not_precise"),
        ("yes", "precise"),
        ("no", "not_precise"),
        # N/A aliases — used for event_count on continuous outcomes.
        # All map to 'precise' so they don't contribute to severity counting.
        ("n_a", "precise"),
        ("na", "precise"),
        ("not_applicable", "precise"),
        ("N/A", "precise"),
    ])
    def test_aliases(self, raw, expected):
        assert imprec._normalize_judgement(raw) == expected

    def test_unknown_defaults_to_probably_precise(self):
        # Unknown values fall back to "probably_precise" (conservative — don't
        # invent a downgrade from garbage output).
        assert imprec._normalize_judgement("maybe") == "probably_precise"
        assert imprec._normalize_judgement("") == "probably_precise"


# ─────────────────────────────────────────────
# Outcome-type heuristic
# ─────────────────────────────────────────────
class TestInferOutcomeIsBinary:
    def test_explicit_binary_field(self):
        assert imprec.infer_outcome_is_binary(
            {"primary_outcome_type": "binary"}, "")

    def test_explicit_dichotomous_field(self):
        assert imprec.infer_outcome_is_binary(
            {"primary_outcome_type": "dichotomous"}, "")

    def test_explicit_continuous_field(self):
        assert imprec.infer_outcome_is_binary(
            {"primary_outcome_type": "continuous"}, "") is False

    def test_mortality_keyword_in_outcome_name(self):
        assert imprec.infer_outcome_is_binary({}, "all-cause mortality")

    def test_mean_keyword_continuous(self):
        assert imprec.infer_outcome_is_binary(
            {}, "mean change in HbA1c at 12 weeks") is False

    def test_uncertain_returns_none(self):
        assert imprec.infer_outcome_is_binary({}, "primary efficacy outcome") is None


# ─────────────────────────────────────────────
# Prompt builder
# ─────────────────────────────────────────────
class TestPromptBuilder:
    def test_no_thresholds_falls_back(self):
        prompt = imprec.build_prompt(
            None, "Randomized Controlled Trial",
            "all-cause mortality at 90 days",
            {},
            outcome_is_binary=True,
        )
        assert "No MID thresholds supplied" in prompt
        assert "line of no effect" in prompt

    def test_thresholds_render_in_prompt(self):
        thresholds = {
            "mid_benefit": "5% absolute risk reduction",
            "mid_harm":    "5% absolute risk increase",
        }
        prompt = imprec.build_prompt(
            thresholds, "Randomized Controlled Trial",
            "MACE at 24 months",
            {"effect_size": "RR 0.78, 95% CI 0.62-0.98"},
            outcome_is_binary=True,
        )
        assert "5% absolute risk reduction" in prompt
        assert "5% absolute risk increase" in prompt
        assert "RR 0.78" in prompt  # extracted-fields context surfaced

    def test_binary_outcome_drives_event_count_language(self):
        prompt = imprec.build_prompt(
            None, "Randomized Controlled Trial",
            "MACE", {}, outcome_is_binary=True,
        )
        assert "BINARY" in prompt
        assert "rule-of-thumb thresholds" in prompt

    def test_continuous_outcome_event_count_na(self):
        prompt = imprec.build_prompt(
            None, "Randomized Controlled Trial",
            "mean HbA1c change", {}, outcome_is_binary=False,
        )
        assert "CONTINUOUS" in prompt
        assert "n_a" in prompt
        assert "excluded from severity" in prompt

    def test_partial_thresholds_marks_unspecified(self):
        thresholds = {"mid_benefit": "5% absolute risk reduction"}  # only benefit
        prompt = imprec.build_prompt(
            thresholds, "Randomized Controlled Trial", "outcome", {},
            outcome_is_binary=True,
        )
        assert "5% absolute risk reduction" in prompt
        assert "unspecified" in prompt

    def test_prompt_lists_all_subdomains(self):
        prompt = imprec.build_prompt(None, "RCT", "outcome", {})
        for sub in imprec.SUBDOMAINS:
            assert sub["label"] in prompt
            assert sub["id"] in prompt


# ─────────────────────────────────────────────
# prompt_catalog
# ─────────────────────────────────────────────
class TestPromptCatalog:
    def test_catalog_shape(self):
        cat = imprec.prompt_catalog()
        assert cat["tool"].startswith("GRADE Imprecision")
        assert set(cat["judgement_options"]) == set(imprec.JUDGEMENT_OPTIONS)
        assert set(cat["severity_levels"]) == set(imprec.SEVERITY_LEVELS)
        assert len(cat["subdomains"]) == 4
        # Both prompt templates must exist (with and without thresholds)
        assert cat["prompt_template_with_thresholds"]
        assert cat["prompt_template_no_thresholds"]
        # Decision-tree source must be present (devs need to see the algorithm)
        assert "def _judgement_severity" in cat["severity_decision_tree_code"]
        # Outcome-type heuristic source must be present too
        assert "def infer_outcome_is_binary" in cat["outcome_type_heuristic_code"]
        # Downgrade table covers all 4 severity tiers
        assert set(cat["downgrade_table"].keys()) == set(imprec.SEVERITY_LEVELS)
        assert len(cat["out_of_scope"]) >= 4


# ─────────────────────────────────────────────
# GRADE — RoB + indirectness + imprecision combination
# ─────────────────────────────────────────────
class TestGradeWithImprecision:
    def test_low_rob_no_downgrades_clean(self):
        level, expl = qa.compute_grade(
            "High", "Low", ["Low"] * 5,
            indirectness_levels=0, imprecision_levels=0,
        )
        assert level == "High"
        assert "No downgrade" in expl
        assert "no serious indirectness" in expl
        assert "no serious imprecision" in expl

    def test_low_rob_serious_imprecision_only(self):
        level, expl = qa.compute_grade(
            "High", "Low", ["Low"] * 5,
            imprecision_levels=1,
            imprecision_explanation="wide CI crossing line of no effect",
        )
        assert level == "Moderate"
        assert "1 level" in expl
        assert "serious imprecision" in expl
        assert "wide CI" in expl

    def test_combined_rob_indirectness_imprecision(self):
        # Some concerns RoB (-1) + serious indirectness (-1) + serious imprecision (-1) = -3
        level, expl = qa.compute_grade(
            "High", "Some concerns", ["Some concerns"] * 2,
            indirectness_levels=1,
            indirectness_explanation="population mismatch",
            imprecision_levels=1,
            imprecision_explanation="few events (n=42)",
        )
        assert level == "Very low"
        assert "3 levels" in expl
        assert "Some concerns" in expl
        assert "indirectness" in expl
        assert "imprecision" in expl

    def test_very_serious_imprecision_two_levels(self):
        level, expl = qa.compute_grade(
            "High", "Low", ["Low"] * 5,
            imprecision_levels=2,
            imprecision_explanation="CI crosses both MID thresholds; few events",
        )
        assert level == "Low"
        assert "2 levels" in expl
        assert "very serious imprecision" in expl

    def test_extremely_serious_imprecision_three_levels(self):
        level, expl = qa.compute_grade(
            "High", "Low", ["Low"] * 5,
            imprecision_levels=3,
        )
        assert level == "Very low"
        assert "3 levels" in expl
        assert "extremely serious imprecision" in expl

    def test_combined_caps_at_very_low(self):
        # High RoB (2) + very serious indirectness (2) + very serious imprecision (2)
        # = 6, capped at Very low (3).
        level, _ = qa.compute_grade(
            "High", "High", ["High", "High"],
            indirectness_levels=2, imprecision_levels=2,
        )
        assert level == "Very low"

    def test_default_imprecision_zero_back_compat(self):
        # Existing call sites that pass only indirectness still work — and
        # when there's a downgrade, no "no serious imprecision" appears.
        level, expl = qa.compute_grade(
            "High", "Some concerns", ["Some concerns"] * 2,
            indirectness_levels=1,
            indirectness_explanation="surrogate outcome",
        )
        assert level == "Low"
        assert "2 levels" in expl
        assert "imprecision" not in expl  # not surfaced when imprec_levels=0

    def test_default_all_zero_back_compat(self):
        # The very oldest call sites (no indirectness, no imprecision) still work.
        level, expl = qa.compute_grade("High", "Some concerns", ["Some concerns"] * 2)
        assert level == "Moderate"
        assert "1 level" in expl


# ─────────────────────────────────────────────
# Flatten — imprecision columns appear in export
# ─────────────────────────────────────────────
class TestFlattenWithImprecision:
    def _row(self, imprecision=None, **overrides):
        base = {
            "paper_id": 1,
            "filename": "x.pdf",
            "status": "ok",
            "study_type": "Randomized Controlled Trial",
            "rob_tool": "rob2",
            "reporting_guideline": "consort2025",
            "primary_outcome": "MACE",
            "classification": {},
            "extracted_fields": {},
            "rob_domains": {},
            "rob_overall": "Low",
            "guideline": {},
            "guideline_proportion": 0.8,
            "indirectness": {},
            "indirectness_overall": "none",
            "indirectness_levels": 0,
            "indirectness_explanation": "",
            "imprecision": imprecision or {},
            "imprecision_overall": "none",
            "imprecision_levels": 0,
            "imprecision_explanation": "",
            "initial_grade": "High",
            "updated_grade": "High",
            "grade_explanation": "No downgrade",
        }
        base.update(overrides)
        return base

    def test_flatten_includes_imprecision_columns(self):
        row = self._row(
            imprecision={
                "ci_width":    {"judgement": "not_precise"},
                "sample_size": {"judgement": "probably_not_precise"},
                "event_count": {"judgement": "probably_precise"},
                "fragility":   {"judgement": "precise"},
                "outcome_is_binary": True,
                "sample_size_total": 142,
                "events_total": 18,
                "ci_summary": "RR 0.78, 95% CI 0.30-2.05",
            },
            imprecision_overall="serious",
            imprecision_levels=1,
            imprecision_explanation="CI crosses both decision thresholds",
        )
        flat = qa.flatten_result_row(row)
        assert flat["imprecision_overall"] == "serious"
        assert flat["imprecision_levels"] == 1
        assert flat["imprecision_explanation"] == "CI crosses both decision thresholds"
        assert flat["imprecision_ci_width"] == "not_precise"
        assert flat["imprecision_sample_size"] == "probably_not_precise"
        assert flat["imprecision_event_count"] == "probably_precise"
        assert flat["imprecision_fragility"] == "precise"
        assert flat["imprecision_outcome_is_binary"] is True

    def test_flatten_handles_missing_imprecision(self):
        # Old rows with no imprecision dict shouldn't crash the export
        row = self._row()
        flat = qa.flatten_result_row(row)
        assert flat["imprecision_overall"] == "none"
        assert flat["imprecision_ci_width"] == ""
        assert flat["imprecision_fragility"] == ""

    def test_flatten_continuous_outcome(self):
        # Continuous outcome: outcome_is_binary=False, event_count subdomain
        # should still flatten cleanly.
        row = self._row(
            imprecision={
                "ci_width":    {"judgement": "precise"},
                "sample_size": {"judgement": "precise"},
                "event_count": {"judgement": "precise"},  # mapped from n_a
                "fragility":   {"judgement": "precise"},
                "outcome_is_binary": False,
            },
        )
        flat = qa.flatten_result_row(row)
        assert flat["imprecision_outcome_is_binary"] is False
        assert flat["imprecision_event_count"] == "precise"


# ─────────────────────────────────────────────
# Registry sanity — imprecision module is wired
# ─────────────────────────────────────────────
def test_imprecision_in_qa_prompt_catalog():
    cat = qa.prompt_catalog()
    assert "imprecision" in cat
    assert cat["imprecision"]["tool"].startswith("GRADE Imprecision")
    # Pipeline overview mentions imprecision explicitly
    steps = " ".join(cat["overview"]["pipeline_steps"]).lower()
    assert "imprecision" in steps
    # GRADE description has been updated
    grade_desc = cat["grade"]["description"].lower()
    assert "imprecision" in grade_desc
    # imprecision is no longer in the deferred-domains list — it's now active.
    # The deferred list should mention only inconsistency + publication bias.
    assert "inconsistency" in grade_desc
    assert "publication bias" in grade_desc


def test_credit_cost_bumped_for_imprecision():
    # Credit cost includes the imprecision LLM call (was 33 with indirectness, now 36).
    assert qa.CREDIT_COST_QA_PER_PAPER >= 36
