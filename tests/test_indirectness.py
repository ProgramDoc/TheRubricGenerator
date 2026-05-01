"""Tests for GRADE indirectness — single-trial PICO assessment.

Covers the pure-Python parts that don't require LLM calls:
- Severity decision tree (count of reds/oranges → severity tier).
- Severity-tier explanation strings.
- Judgement normaliser (LLM output coercion).
- Prompt-builder behaviour with and without a target PICO.
- prompt_catalog shape.
- compute_grade combining RoB + indirectness downgrades.
- flatten_result_row including indirectness columns.
"""

from __future__ import annotations

import pytest

from backend import indirectness as indir
from backend import quality_appraisal as qa


# ─────────────────────────────────────────────
# Decision tree — _judgement_severity
# ─────────────────────────────────────────────
class TestSeverityTree:
    def test_all_direct_no_downgrade(self):
        sev, levels, _ = indir._judgement_severity({
            "population":   "direct",
            "intervention": "direct",
            "comparator":   "direct",
            "outcome":      "direct",
        })
        assert sev == "none"
        assert levels == 0

    def test_all_probably_direct_no_downgrade(self):
        sev, levels, _ = indir._judgement_severity({
            sid: "probably_direct" for sid in indir.SUBDOMAIN_IDS
        })
        assert sev == "none"
        assert levels == 0

    def test_single_borderline_no_downgrade(self):
        # One probably_not_direct alone is "inherent indirectness" — don't downgrade
        sev, levels, _ = indir._judgement_severity({
            "population":   "probably_not_direct",
            "intervention": "probably_direct",
            "comparator":   "direct",
            "outcome":      "direct",
        })
        assert sev == "none"
        assert levels == 0

    def test_two_oranges_serious(self):
        # Two probably_not_direct → serious (1 level)
        sev, levels, counts = indir._judgement_severity({
            "population":   "probably_not_direct",
            "intervention": "probably_not_direct",
            "comparator":   "direct",
            "outcome":      "direct",
        })
        assert sev == "serious"
        assert levels == 1
        assert counts["oranges"] == 2

    def test_one_red_serious(self):
        # Single not_direct → serious
        sev, levels, _ = indir._judgement_severity({
            "population":   "direct",
            "intervention": "direct",
            "comparator":   "direct",
            "outcome":      "not_direct",
        })
        assert sev == "serious"
        assert levels == 1

    def test_two_reds_very_serious(self):
        sev, levels, _ = indir._judgement_severity({
            "population":   "not_direct",
            "intervention": "not_direct",
            "comparator":   "direct",
            "outcome":      "direct",
        })
        assert sev == "very_serious"
        assert levels == 2

    def test_three_reds_extremely_serious(self):
        sev, levels, _ = indir._judgement_severity({
            "population":   "not_direct",
            "intervention": "not_direct",
            "comparator":   "not_direct",
            "outcome":      "direct",
        })
        assert sev == "extremely_serious"
        assert levels == 3

    def test_four_reds_extremely_serious_capped(self):
        # 4 reds is still extremely_serious (3 levels, not 4) — GRADE caps at -3.
        sev, levels, counts = indir._judgement_severity({
            "population":   "not_direct",
            "intervention": "not_direct",
            "comparator":   "not_direct",
            "outcome":      "not_direct",
        })
        assert sev == "extremely_serious"
        assert levels == 3
        assert counts["reds"] == 4

    def test_one_red_dominates_oranges(self):
        # 1 red + 1 orange → still serious (red rule applies first)
        sev, levels, _ = indir._judgement_severity({
            "population":   "not_direct",
            "intervention": "probably_not_direct",
            "comparator":   "direct",
            "outcome":      "direct",
        })
        assert sev == "serious"
        assert levels == 1


# ─────────────────────────────────────────────
# Explanation strings
# ─────────────────────────────────────────────
class TestSeverityExplanation:
    def test_none_explanation(self):
        msg = indir.severity_explanation("none", {"reds": 0, "oranges": 0}, {})
        assert "No serious indirectness" in msg

    def test_serious_lists_drivers(self):
        per_sub = {
            "population":   "not_direct",
            "intervention": "direct",
            "comparator":   "direct",
            "outcome":      "direct",
        }
        msg = indir.severity_explanation("serious", {"reds": 1, "oranges": 0}, per_sub)
        assert "Serious" in msg
        assert "Population" in msg

    def test_very_serious_lists_two_drivers(self):
        per_sub = {
            "population":   "not_direct",
            "intervention": "not_direct",
            "comparator":   "direct",
            "outcome":      "direct",
        }
        msg = indir.severity_explanation("very_serious", {"reds": 2, "oranges": 0}, per_sub)
        assert "Very serious" in msg
        assert "Population" in msg
        assert "Intervention" in msg


# ─────────────────────────────────────────────
# Judgement normaliser
# ─────────────────────────────────────────────
class TestNormalizeJudgement:
    @pytest.mark.parametrize("raw,expected", [
        ("direct", "direct"),
        ("DIRECT", "direct"),
        ("probably_direct", "probably_direct"),
        ("Probably Direct", "probably_direct"),
        ("probably-not-direct", "probably_not_direct"),
        ("not_direct", "not_direct"),
        ("sufficiently_direct", "direct"),
        ("probably_sufficiently_direct", "probably_direct"),
        ("not_sufficiently_direct", "not_direct"),
        ("yes", "direct"),
        ("no", "not_direct"),
    ])
    def test_aliases(self, raw, expected):
        assert indir._normalize_judgement(raw) == expected

    def test_unknown_defaults_to_probably_direct(self):
        # Unknown values fall back to "probably_direct" (conservative — don't
        # invent a downgrade from garbage output).
        assert indir._normalize_judgement("maybe") == "probably_direct"
        assert indir._normalize_judgement("") == "probably_direct"


# ─────────────────────────────────────────────
# Prompt builder
# ─────────────────────────────────────────────
class TestPromptBuilder:
    def test_no_target_pico_falls_back_to_as_conducted(self):
        prompt = indir.build_prompt(
            None, "Randomized Controlled Trial",
            "all-cause mortality at 90 days",
            {},
        )
        assert "No target PICO supplied" in prompt
        assert "as-conducted PICO" in prompt
        # Surrogate-outcome guidance must appear when no target PICO is given —
        # it's the one PICO subdomain we can meaningfully judge cold.
        assert "surrogate" in prompt.lower()

    def test_target_pico_renders_in_prompt(self):
        target = {
            "population":   "adults with type 2 diabetes",
            "intervention": "GLP-1 agonist",
            "comparator":   "placebo",
            "outcome":      "MACE",
        }
        prompt = indir.build_prompt(
            target, "Randomized Controlled Trial",
            "HbA1c reduction",
            {"population_description": "T2DM cohort, age 45-70"},
        )
        assert "adults with type 2 diabetes" in prompt
        assert "GLP-1 agonist" in prompt
        assert "MACE" in prompt
        assert "T2DM cohort" in prompt  # extracted-fields context surfaced

    def test_blank_target_pico_treated_as_none(self):
        prompt = indir.build_prompt(
            {"population": "", "intervention": "", "comparator": "", "outcome": ""},
            "Randomized Controlled Trial", "outcome", {},
        )
        assert "No target PICO supplied" in prompt

    def test_partial_target_pico_marks_unspecified(self):
        target = {"outcome": "MACE"}  # only outcome supplied
        prompt = indir.build_prompt(
            target, "Randomized Controlled Trial", "outcome", {},
        )
        assert "MACE" in prompt
        assert "unspecified" in prompt

    def test_prompt_lists_all_subdomains(self):
        prompt = indir.build_prompt(None, "RCT", "outcome", {})
        for sub in indir.SUBDOMAINS:
            assert sub["label"] in prompt
            assert sub["id"] in prompt


# ─────────────────────────────────────────────
# prompt_catalog
# ─────────────────────────────────────────────
class TestPromptCatalog:
    def test_catalog_shape(self):
        cat = indir.prompt_catalog()
        assert cat["tool"].startswith("GRADE Indirectness")
        assert set(cat["judgement_options"]) == set(indir.JUDGEMENT_OPTIONS)
        assert set(cat["severity_levels"]) == set(indir.SEVERITY_LEVELS)
        assert len(cat["subdomains"]) == 4
        # Both prompt templates must exist (with and without target PICO)
        assert cat["prompt_template_with_target_pico"]
        assert cat["prompt_template_no_target_pico"]
        # Decision-tree source must be present (devs need to see the algorithm)
        assert "def _judgement_severity" in cat["severity_decision_tree_code"]
        # Downgrade table covers all 4 severity tiers
        assert set(cat["downgrade_table"].keys()) == set(indir.SEVERITY_LEVELS)


# ─────────────────────────────────────────────
# GRADE — RoB + indirectness combination
# ─────────────────────────────────────────────
class TestGradeWithIndirectness:
    def test_low_rob_no_indirectness_no_downgrade(self):
        level, expl = qa.compute_grade("High", "Low", ["Low"] * 5,
                                        indirectness_levels=0)
        assert level == "High"
        assert "No downgrade" in expl
        assert "no serious indirectness" in expl

    def test_low_rob_serious_indirectness_one_downgrade(self):
        level, expl = qa.compute_grade(
            "High", "Low", ["Low"] * 5,
            indirectness_levels=1,
            indirectness_explanation="surrogate primary outcome (HbA1c)",
        )
        assert level == "Moderate"
        assert "1 level" in expl
        assert "serious indirectness" in expl
        assert "surrogate" in expl

    def test_high_rob_plus_serious_indirectness_two_levels(self):
        # Some concerns RoB (-1) + serious indirectness (-1) = -2
        level, expl = qa.compute_grade(
            "High", "Some concerns", ["Some concerns"] * 2,
            indirectness_levels=1,
            indirectness_explanation="population mismatch",
        )
        assert level == "Low"
        assert "2 levels" in expl
        assert "Some concerns" in expl
        assert "indirectness" in expl

    def test_very_serious_indirectness_two_levels(self):
        level, expl = qa.compute_grade(
            "High", "Low", ["Low"] * 5,
            indirectness_levels=2,
            indirectness_explanation="population + intervention mismatch",
        )
        assert level == "Low"
        assert "2 levels" in expl
        assert "very serious indirectness" in expl

    def test_extremely_serious_indirectness_three_levels(self):
        level, expl = qa.compute_grade(
            "High", "Low", ["Low"] * 5,
            indirectness_levels=3,
        )
        assert level == "Very low"
        assert "3 levels" in expl
        assert "extremely serious indirectness" in expl

    def test_combined_caps_at_very_low(self):
        # High RoB (2 levels) + very serious indirectness (2 levels) = 4 levels,
        # but capped at Very low (3 levels below High).
        level, _ = qa.compute_grade(
            "High", "High", ["High", "High"],
            indirectness_levels=2,
        )
        assert level == "Very low"

    def test_robins_i_critical_plus_indirectness(self):
        # ROBINS-I Critical (2) + serious indirectness (1) = 3 levels,
        # capped at Very low.
        level, expl = qa.compute_grade(
            "Low", "Critical", ["Critical"],
            indirectness_levels=1,
        )
        assert level == "Very low"
        assert "Critical" in expl
        assert "indirectness" in expl

    def test_default_indirectness_is_zero_back_compat(self):
        # Old call sites that don't pass indirectness still work — and the
        # explanation must NOT mention "no serious indirectness" when the RoB
        # path itself produces a downgrade (we only mention indirectness when
        # the result is "no downgrade").
        level, expl = qa.compute_grade("High", "Some concerns", ["Some concerns"] * 2)
        assert level == "Moderate"
        assert "1 level" in expl


# ─────────────────────────────────────────────
# Flatten — indirectness columns appear in export
# ─────────────────────────────────────────────
class TestFlattenWithIndirectness:
    def _row(self, indirectness=None, **overrides):
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
            "indirectness": indirectness or {},
            "indirectness_overall": "none",
            "indirectness_levels": 0,
            "indirectness_explanation": "",
            "initial_grade": "High",
            "updated_grade": "High",
            "grade_explanation": "No downgrade",
        }
        base.update(overrides)
        return base

    def test_flatten_includes_indirectness_columns(self):
        row = self._row(
            indirectness={
                "population":   {"judgement": "direct"},
                "intervention": {"judgement": "probably_direct"},
                "comparator":   {"judgement": "probably_not_direct"},
                "outcome":      {"judgement": "not_direct"},
                "primary_outcome_is_surrogate": True,
            },
            indirectness_overall="serious",
            indirectness_levels=1,
            indirectness_explanation="surrogate outcome",
        )
        flat = qa.flatten_result_row(row)
        assert flat["indirectness_overall"] == "serious"
        assert flat["indirectness_levels"] == 1
        assert flat["indirectness_explanation"] == "surrogate outcome"
        assert flat["indirectness_population"] == "direct"
        assert flat["indirectness_intervention"] == "probably_direct"
        assert flat["indirectness_comparator"] == "probably_not_direct"
        assert flat["indirectness_outcome"] == "not_direct"
        assert flat["primary_outcome_is_surrogate"] is True

    def test_flatten_handles_missing_indirectness(self):
        # Old rows with no indirectness dict shouldn't crash the export
        row = self._row()
        flat = qa.flatten_result_row(row)
        assert flat["indirectness_overall"] == "none"
        assert flat["indirectness_population"] == ""
        assert flat["indirectness_outcome"] == ""


# ─────────────────────────────────────────────
# Registry sanity — indirectness module is wired
# ─────────────────────────────────────────────
def test_indirectness_in_qa_prompt_catalog():
    cat = qa.prompt_catalog()
    assert "indirectness" in cat
    assert cat["indirectness"]["tool"].startswith("GRADE Indirectness")
    # Pipeline overview mentions indirectness explicitly
    steps = " ".join(cat["overview"]["pipeline_steps"]).lower()
    assert "indirectness" in steps
    # GRADE description has been updated
    assert "indirectness" in cat["grade"]["description"].lower()


def test_credit_cost_bumped_for_indirectness():
    # Credit cost includes the indirectness LLM call (was 30, now 33).
    assert qa.CREDIT_COST_QA_PER_PAPER >= 33
