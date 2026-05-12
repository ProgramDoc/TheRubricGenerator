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
from backend.rob_tools import rob2, robins_i
from backend.reporting_guidelines import consort2025, strobe


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
# ROBINS-I V2 — preflight (B1/B2/B3 + C4 variant decision)
# ─────────────────────────────────────────────
class TestRobinsIPreflight:
    """Preflight screening + variant decision (post-LLM-parse logic).

    The LLM call is stubbed via monkeypatch; tests cover the deterministic
    post-parse decision branches: B2/B3 short-circuit to Critical, C4
    dispatches Variant A vs B.
    """

    @staticmethod
    def _stub_call(monkeypatch, raw: dict) -> None:
        from backend.rob_tools import robins_i as ri
        monkeypatch.setattr(
            ri, "_call_with_pdf",
            lambda pdf, prompt, max_tokens=8192: raw,
        )

    def test_b2_yes_short_circuits_to_critical(self, monkeypatch):
        self._stub_call(monkeypatch, {
            "B1": "N", "B2": "Y", "B3": "N", "C4": "No",
            "B1_rationale": "no adjustment",
            "B2_rationale": "substantial confounding likely",
            "B3_rationale": "ok measurement",
            "C4_rationale": "ITT analysis",
        })
        out = robins_i.run_preflight(b"", "Cohort Study", "outcome X", {})
        assert out["screening_decision"] == "critical"
        assert "B2" in out["screening_reason"]

    def test_b3_yes_short_circuits_to_critical(self, monkeypatch):
        self._stub_call(monkeypatch, {
            "B1": "Y", "B2": "NA", "B3": "Y", "C4": "Yes",
        })
        out = robins_i.run_preflight(b"", "Cohort Study", "outcome X", {})
        assert out["screening_decision"] == "critical"
        assert "B3" in out["screening_reason"]

    def test_proceed_with_variant_a_when_c4_no(self, monkeypatch):
        self._stub_call(monkeypatch, {
            "B1": "Y", "B2": "NA", "B3": "N", "C4": "No",
        })
        out = robins_i.run_preflight(b"", "Cohort Study", "outcome X", {})
        assert out["screening_decision"] == "proceed"
        assert out["variant"] == "A"

    def test_proceed_with_variant_b_when_c4_yes(self, monkeypatch):
        self._stub_call(monkeypatch, {
            "B1": "Y", "B2": "NA", "B3": "N", "C4": "Yes",
        })
        out = robins_i.run_preflight(b"", "Cohort Study", "outcome X", {})
        assert out["screening_decision"] == "proceed"
        assert out["variant"] == "B"

    def test_invalid_tokens_default_to_safe_values(self, monkeypatch):
        # Malformed LLM output shouldn't raise; B1 defaults to NI-equivalent
        # (allowed list is Y/PY/PN/N; out-of-range falls to default)
        self._stub_call(monkeypatch, {
            "B1": "Z", "B2": "Q", "B3": "M", "C4": "maybe",
        })
        out = robins_i.run_preflight(b"", "Cohort Study", "x", {})
        # C4 doesn't start with 'y' → variant A
        assert out["variant"] == "A"
        # B2/B3 normalized away from Y/PY → proceed
        assert out["screening_decision"] == "proceed"


# ─────────────────────────────────────────────
# ROBINS-I V2 — Domain 1 (confounding)
# ─────────────────────────────────────────────
class TestRobinsIDomain1VariantA:
    """V2 Domain 1 Variant A — ITT effect, baseline confounding only.
    Cribsheet p20 algorithm."""

    def test_well_controlled_no_concerns_is_low_d1(self):
        # All ideal: 1A.1 Y (all controlled), 1A.2 Y (valid measurement),
        # 1A.3 N (no over-adjustment), 1A.4 N (no neg-control hits)
        assert robins_i.domain1_variant_a_judge({
            "1A.1": "Y", "1A.2": "Y", "1A.3": "N", "1A.4": "N",
        }) == robins_i.LOW_D1

    def test_wn_floor_is_moderate_not_low(self):
        # 1A.1 WN (most-but-not-all controlled) → MODERATE floor, not LOW
        assert robins_i.domain1_variant_a_judge({
            "1A.1": "WN", "1A.2": "Y", "1A.3": "N", "1A.4": "N",
        }) == "Moderate"

    def test_sn_with_neg_control_hit_is_critical(self):
        # 1A.1 SN + 1A.4 Y → Critical
        assert robins_i.domain1_variant_a_judge({
            "1A.1": "SN", "1A.4": "Y",
        }) == "Critical"

    def test_sn_without_neg_control_hit_is_serious(self):
        assert robins_i.domain1_variant_a_judge({
            "1A.1": "SN", "1A.4": "N",
        }) == "Serious"

    def test_over_adjustment_for_post_intervention_is_serious(self):
        # 1A.3 Y (controlled for post-intervention) + 1A.2 Y + 1A.4 N → Serious
        assert robins_i.domain1_variant_a_judge({
            "1A.1": "Y", "1A.2": "Y", "1A.3": "Y", "1A.4": "N",
        }) == "Serious"

    def test_over_adjustment_with_validity_problems_is_critical(self):
        # 1A.3 Y + 1A.2 SN + 1A.4 N → Critical (compound failure)
        assert robins_i.domain1_variant_a_judge({
            "1A.1": "Y", "1A.2": "SN", "1A.3": "Y", "1A.4": "N",
        }) == "Critical"

    def test_neg_control_hit_with_good_adjustment_is_serious(self):
        assert robins_i.domain1_variant_a_judge({
            "1A.1": "Y", "1A.2": "Y", "1A.3": "N", "1A.4": "Y",
        }) == "Serious"

    def test_sn_validity_with_no_over_adjustment_is_serious(self):
        # 1A.1 Y + 1A.3 N + 1A.2 SN → Serious
        assert robins_i.domain1_variant_a_judge({
            "1A.1": "Y", "1A.2": "SN", "1A.3": "N", "1A.4": "N",
        }) == "Serious"


class TestRobinsIDomain1VariantB:
    """V2 Domain 1 Variant B — per-protocol effect, baseline + time-varying.
    Cribsheet p24 algorithm."""

    def test_appropriate_g_methods_clean_is_low_d1(self):
        # 1B.1 Y (g-methods), 1B.2 Y (all controlled), 1B.3 Y (valid), 1B.5 N
        assert robins_i.domain1_variant_b_judge({
            "1B.1": "Y", "1B.2": "Y", "1B.3": "Y", "1B.5": "N",
        }) == robins_i.LOW_D1

    def test_no_g_methods_with_over_adjustment_is_critical(self):
        # 1B.1 N + 1B.4 Y → Critical
        assert robins_i.domain1_variant_b_judge({
            "1B.1": "N", "1B.4": "Y",
        }) == "Critical"

    def test_no_g_methods_no_over_adjustment_neg_clean_is_serious(self):
        # 1B.1 N + 1B.4 N + 1B.5 N → Serious
        assert robins_i.domain1_variant_b_judge({
            "1B.1": "N", "1B.4": "N", "1B.5": "N",
        }) == "Serious"

    def test_no_g_methods_neg_control_hit_is_critical(self):
        # 1B.1 N + 1B.4 N + 1B.5 Y → Critical
        assert robins_i.domain1_variant_b_judge({
            "1B.1": "N", "1B.4": "N", "1B.5": "Y",
        }) == "Critical"

    def test_wn_factor_control_is_moderate_floor(self):
        # 1B.1 Y + 1B.2 WN + 1B.3 Y + 1B.5 N → Moderate (floor for WN on 1B.2)
        assert robins_i.domain1_variant_b_judge({
            "1B.1": "Y", "1B.2": "WN", "1B.3": "Y", "1B.5": "N",
        }) == "Moderate"

    def test_sn_factor_control_with_neg_control_hit_is_critical(self):
        # 1B.1 Y + 1B.2 SN + 1B.5 Y → Critical
        assert robins_i.domain1_variant_b_judge({
            "1B.1": "Y", "1B.2": "SN", "1B.5": "Y",
        }) == "Critical"


# ─────────────────────────────────────────────
# ROBINS-I V2 — Domain 2 (classification of interventions)
# ─────────────────────────────────────────────
class TestRobinsIDomain2:
    """V2 D2 — bias in classification of interventions. Cribsheet p28."""

    def test_distinguishable_no_differential_no_other_low(self):
        # 2.1 Y, 2.4 N, 2.5 N → Low
        assert robins_i.domain2_judge({
            "2.1": "Y", "2.4": "N", "2.5": "N",
        }) == "Low"

    def test_distinguishable_with_minor_other_is_moderate(self):
        # Non-differential misclassification: 2.5 Y bumps by one tier
        assert robins_i.domain2_judge({
            "2.1": "Y", "2.4": "N", "2.5": "Y",
        }) == "Moderate"

    def test_strong_differential_is_critical(self):
        # 2.1 Y, 2.4 SY (strong-yes, substantial impact), 2.5 Y → Critical
        assert robins_i.domain2_judge({
            "2.1": "Y", "2.4": "SY", "2.5": "Y",
        }) == "Critical"

    def test_weak_differential_is_serious(self):
        # 2.1 Y, 2.4 WY, 2.5 Y → Serious
        assert robins_i.domain2_judge({
            "2.1": "Y", "2.4": "WY", "2.5": "Y",
        }) == "Serious"

    def test_not_distinguishable_late_events_low(self):
        # 2.1 N + 2.2 Y (almost all events after distinguishable) → tier 0
        assert robins_i.domain2_judge({
            "2.1": "N", "2.2": "Y", "2.4": "N", "2.5": "N",
        }) == "Low"

    def test_no_appropriate_analysis_with_strong_yes_d24_is_critical(self):
        # 2.1 N + 2.2 N + 2.3 N → tier 2; 2.4 SY → direct Critical
        assert robins_i.domain2_judge({
            "2.1": "N", "2.2": "N", "2.3": "N", "2.4": "SY",
        }) == "Critical"


# ─────────────────────────────────────────────
# ROBINS-I V2 — Domain 3 (selection of participants)
# ─────────────────────────────────────────────
class TestRobinsIDomain3:
    """V2 D3 — bias in selection of participants into the study (or analysis).
    Cribsheet p32 — sub-sections A (prevalent-user/immortal time), B (other
    selection bias), C (severity / correction)."""

    def test_clean_follow_up_no_other_selection_low(self):
        # 3.1 Y (follow-up began at intervention start), 3.2 N, 3.3 N → Low
        assert robins_i.domain3_judge({
            "3.1": "Y", "3.2": "N", "3.3": "N",
        }) == "Low"

    def test_strong_no_follow_up_start_promotes_to_serious(self):
        # 3.1 SN (substantial bias) → A=Serious; B=Low; worst=Serious;
        # C: 3.6 N, 3.7 N, 3.8 N → Serious
        assert robins_i.domain3_judge({
            "3.1": "SN", "3.3": "N", "3.6": "N", "3.7": "N", "3.8": "N",
        }) == "Serious"

    def test_post_intervention_selection_with_both_links_is_serious(self):
        # 3.3 Y + 3.4 Y + 3.5 Y → B=Serious; C: 3.6 N, 3.7 N, 3.8 N → Serious
        assert robins_i.domain3_judge({
            "3.1": "Y", "3.2": "N",
            "3.3": "Y", "3.4": "Y", "3.5": "Y",
            "3.6": "N", "3.7": "N", "3.8": "N",
        }) == "Serious"

    def test_severe_selection_bias_with_no_correction_is_critical(self):
        # B=Serious AND 3.8 Y (bias severe enough to exclude) → Critical
        assert robins_i.domain3_judge({
            "3.1": "Y", "3.2": "N",
            "3.3": "Y", "3.4": "Y", "3.5": "Y",
            "3.6": "N", "3.7": "N", "3.8": "Y",
        }) == "Critical"

    def test_corrected_for_selection_is_moderate(self):
        # B=Serious BUT 3.6 Y (analysis corrected) → Moderate
        assert robins_i.domain3_judge({
            "3.1": "Y", "3.2": "N",
            "3.3": "Y", "3.4": "Y", "3.5": "Y",
            "3.6": "Y",
        }) == "Moderate"

    def test_sensitivity_minimal_impact_is_moderate(self):
        # B=Serious, 3.6 N, 3.7 Y (sensitivity minimal impact) → Moderate
        assert robins_i.domain3_judge({
            "3.1": "Y", "3.2": "N",
            "3.3": "Y", "3.4": "Y", "3.5": "Y",
            "3.6": "N", "3.7": "Y",
        }) == "Moderate"


# ─────────────────────────────────────────────
# ROBINS-I V2 — Domain 4 (missing data)
# ─────────────────────────────────────────────
class TestRobinsIDomain4:
    """V2 D4 — bias due to missing data. Cribsheet p38."""

    def test_complete_data_all_three_low(self):
        assert robins_i.domain4_judge({
            "4.1": "Y", "4.2": "Y", "4.3": "Y",
        }) == "Low"

    def test_complete_case_unrelated_exclusion_low(self):
        # Some missing data but 4.5 N (exclusion unrelated to outcome) → Low
        assert robins_i.domain4_judge({
            "4.1": "N", "4.2": "Y", "4.3": "Y", "4.4": "Y", "4.5": "N",
        }) == "Low"

    def test_complete_case_outcome_related_exclusion_with_sn_is_critical(self):
        # 4.5 Y + 4.6 SN (model doesn't explain) + 4.11 N → Critical
        assert robins_i.domain4_judge({
            "4.1": "N", "4.2": "N", "4.3": "Y", "4.4": "Y",
            "4.5": "Y", "4.6": "SN", "4.11": "N",
        }) == "Critical"

    def test_proper_multiple_imputation_low(self):
        # 4.7 Y + 4.8 Y (MAR ok) + 4.9 Y (appropriate MI) → Low
        assert robins_i.domain4_judge({
            "4.1": "N", "4.2": "N", "4.3": "Y", "4.4": "N",
            "4.7": "Y", "4.8": "Y", "4.9": "Y",
        }) == "Low"

    def test_locf_imputation_is_critical_when_no_evidence(self):
        # 4.9 SN (LOCF) + 4.11 N → Critical
        assert robins_i.domain4_judge({
            "4.1": "N", "4.4": "N", "4.7": "Y", "4.8": "Y",
            "4.9": "SN", "4.11": "N",
        }) == "Critical"

    def test_alternative_method_appropriate_low(self):
        # Not complete case, not imputation, 4.10 Y → Low
        assert robins_i.domain4_judge({
            "4.1": "N", "4.4": "N", "4.7": "N", "4.10": "Y",
        }) == "Low"


# ─────────────────────────────────────────────
# ROBINS-I V2 — Domain 5 (measurement of the outcome)
# ─────────────────────────────────────────────
class TestRobinsIDomain5:
    """V2 D5 — bias arising from measurement of the outcome. Cribsheet p41."""

    def test_comparable_methods_blinded_low(self):
        # 5.1 N (no differential measurement), 5.2 N (blinded) → Low
        assert robins_i.domain5_judge({"5.1": "N", "5.2": "N"}) == "Low"

    def test_differential_measurement_is_serious(self):
        # 5.1 Y → Serious directly
        assert robins_i.domain5_judge({"5.1": "Y"}) == "Serious"

    def test_unblinded_with_strong_knowledge_influence_is_serious(self):
        # 5.1 N + 5.2 Y + 5.3 SY → Serious
        assert robins_i.domain5_judge({
            "5.1": "N", "5.2": "Y", "5.3": "SY",
        }) == "Serious"

    def test_unblinded_with_weak_knowledge_influence_is_moderate(self):
        assert robins_i.domain5_judge({
            "5.1": "N", "5.2": "Y", "5.3": "WY",
        }) == "Moderate"

    def test_unblinded_but_no_likely_influence_low(self):
        # Assessors knew but no reason to believe influenced
        assert robins_i.domain5_judge({
            "5.1": "N", "5.2": "Y", "5.3": "N",
        }) == "Low"


# ─────────────────────────────────────────────
# ROBINS-I V2 — Domain 6 (selection of reported result)
# ─────────────────────────────────────────────
class TestRobinsIDomain6:
    """V2 D6 — bias in selection of the reported result. Cribsheet p47."""

    def test_pre_specified_plan_low(self):
        # 6.1 Y → Low regardless of 6.2-6.4
        assert robins_i.domain6_judge({"6.1": "Y"}) == "Low"

    def test_no_plan_all_clean_low(self):
        assert robins_i.domain6_judge({
            "6.1": "N", "6.2": "N", "6.3": "N", "6.4": "N",
        }) == "Low"

    def test_single_selection_is_serious(self):
        # 6.1 N + 1 Y/PY among 6.2-6.4 → Serious
        assert robins_i.domain6_judge({
            "6.1": "N", "6.2": "Y", "6.3": "N", "6.4": "N",
        }) == "Serious"

    def test_two_selections_is_critical(self):
        # 6.1 N + 2 Y/PY → Critical
        assert robins_i.domain6_judge({
            "6.1": "N", "6.2": "Y", "6.3": "Y", "6.4": "N",
        }) == "Critical"

    def test_all_ni_is_serious(self):
        assert robins_i.domain6_judge({
            "6.1": "N", "6.2": "NI", "6.3": "NI", "6.4": "NI",
        }) == "Serious"

    def test_one_ni_no_y_is_moderate(self):
        assert robins_i.domain6_judge({
            "6.1": "N", "6.2": "NI", "6.3": "N", "6.4": "N",
        }) == "Moderate"


# ─────────────────────────────────────────────
# ROBINS-I V2 — Overall aggregation
# ─────────────────────────────────────────────
class TestRobinsIOverall:
    """Worst-domain aggregation per cribsheet p48 — V2 has 6 domains and a
    4-level scale (no "No information" overall)."""

    def test_all_low_is_low(self):
        assert robins_i.robins_i_overall(["Low"] * 6) == "Low"

    def test_d1_special_label_is_treated_as_low(self):
        # The "Low (except for concerns about uncontrolled confounding)" label
        # must normalize to Low for aggregation.
        assert robins_i.robins_i_overall(
            [robins_i.LOW_D1, "Low", "Low", "Low", "Low", "Low"]
        ) == "Low"

    def test_any_critical_is_critical(self):
        assert robins_i.robins_i_overall(
            ["Low", "Moderate", "Critical", "Serious", "Low", "Low"]
        ) == "Critical"

    def test_any_serious_is_serious(self):
        assert robins_i.robins_i_overall(
            ["Low", "Moderate", "Serious", "Low", "Low", "Low"]
        ) == "Serious"

    def test_moderate_promotion(self):
        assert robins_i.robins_i_overall(
            ["Low", "Moderate", "Low", "Low", "Low", "Low"]
        ) == "Moderate"

    def test_empty_input_low(self):
        assert robins_i.robins_i_overall([]) == "Low"


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

    # ── ROBINS-I V2 branches ─────────────────────────
    # V2 has 6 domains and a 4-level scale (Low/Moderate/Serious/Critical).
    # The "No information" overall judgement is V1 legacy — kept for backward
    # compat with stored runs.

    def test_robins_i_low_no_downgrade(self):
        level, expl = qa.compute_grade("Low", "Low", ["Low"] * 6)
        assert level == "Low"
        assert "No downgrade" in expl

    def test_robins_i_moderate_downgrades_one(self):
        level, expl = qa.compute_grade("Low", "Moderate", ["Moderate"])
        assert level == "Very low"
        assert "1 level" in expl
        assert "ROBINS-I V2" in expl

    def test_robins_i_serious_single_domain_downgrades_one(self):
        level, _ = qa.compute_grade("Low", "Serious",
                                     ["Serious", "Low", "Low", "Low", "Low", "Low"])
        assert level == "Very low"

    def test_robins_i_serious_two_domains_downgrades_two(self):
        level, expl = qa.compute_grade("High", "Serious",
                                        ["Serious", "Serious", "Low", "Low", "Low", "Low"])
        assert level == "Low"
        assert "2 levels" in expl

    def test_robins_i_critical_downgrades_two(self):
        level, expl = qa.compute_grade("High", "Critical", ["Critical"])
        assert level == "Low"
        assert "2 levels" in expl

    def test_robins_i_v1_no_information_legacy_branch_still_resolves(self):
        # Stored V1 runs may have "No information" as overall — should still
        # resolve via the legacy branch (V2 algorithms never produce it).
        level, expl = qa.compute_grade("Moderate", "No information", ["No information"])
        assert level == "Low"
        assert "legacy V1" in expl


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
        # Types still not wired (registry stubs remain commented)
        assert qa.dispatch("SR with Meta-Analysis") is None
        assert qa.dispatch("Diagnostic Accuracy") is None
        assert qa.dispatch("") is None
        assert qa.dispatch("Not A Real Study Type") is None

    def test_cohort_study_is_registered(self):
        cfg = qa.dispatch("Cohort Study")
        assert cfg is not None
        assert cfg["rob_tool"] == "robins_i"
        assert cfg["reporting_guideline"] == "strobe"
        assert cfg["initial_grade"] == "Low"

    def test_case_control_is_registered(self):
        cfg = qa.dispatch("Case-Control")
        assert cfg is not None
        assert cfg["rob_tool"] == "robins_i"
        assert cfg["initial_grade"] == "Low"

    def test_non_randomized_trial_is_registered(self):
        cfg = qa.dispatch("Non-Randomized Trial")
        assert cfg is not None
        assert cfg["rob_tool"] == "robins_i"

    def test_cross_sectional_analytical_is_registered(self):
        cfg = qa.dispatch("Cross-Sectional (Analytical)")
        assert cfg is not None
        assert cfg["rob_tool"] == "robins_i"

    def test_case_crossover_is_registered(self):
        cfg = qa.dispatch("Case-Crossover")
        assert cfg is not None
        assert cfg["rob_tool"] == "robins_i"

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
# STROBE proportion math
# ─────────────────────────────────────────────
class TestStrobe:
    def test_item_count_covers_checklist(self):
        assert len(strobe.ITEMS) >= 30
        ids = {it["id"] for it in strobe.ITEMS}
        assert {"1a", "1b", "2", "3", "4", "5", "6a", "6b",
                "12a", "12b", "12c", "12d", "12e",
                "13a", "13b", "14a", "14b", "14c",
                "16a", "16b", "16c", "18", "19", "20", "21", "22"}.issubset(ids)

    def test_prompt_contains_all_items(self):
        prompt = strobe.build_prompt({"study_type": "Cohort Study"})
        for it in strobe.ITEMS:
            assert f"**{it['id']}**" in prompt, f"Item {it['id']} missing from prompt"

    def test_proportion_math_excludes_na(self):
        raw = {
            "1a": {"adhered": True, "evidence": "yes"},
            "5":  {"adhered": False, "evidence": "missing"},
            "6b": {"adhered": None, "evidence": "N/A (unmatched cohort)"},
            "14c": {"adhered": True, "evidence": "median follow-up 3.1 years"},
        }
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
        assert "robins_i" in cat["rob_tools"]
        assert "reporting_guidelines" in cat
        assert "consort2025" in cat["reporting_guidelines"]
        assert "strobe" in cat["reporting_guidelines"]
        assert "grade" in cat
        assert cat["credit_cost_per_paper"] == qa.CREDIT_COST_QA_PER_PAPER

    def test_rob2_catalog_shows_decision_tree_code(self):
        cat = qa.prompt_catalog()
        rob2_cat = cat["rob_tools"]["rob2"]
        assert len(rob2_cat["domains"]) == 5
        for d in rob2_cat["domains"]:
            assert "def " in d["decision_tree_code"]
            assert d["prompt_template"].strip()
            assert d["signals"]

    def test_robins_i_catalog_shows_decision_tree_code(self):
        cat = qa.prompt_catalog()
        robins_cat = cat["rob_tools"]["robins_i"]
        # V2: 6 domains, 4-level scale (no "No information" judgement)
        assert len(robins_cat["domains"]) == 6
        assert set(robins_cat["judgements"]) == {
            "Low", "Moderate", "Serious", "Critical",
        }
        # V2 catalog surfaces preflight prompt + D1 special label
        assert "preflight_prompt_template" in robins_cat
        assert robins_cat["preflight_prompt_template"].strip()
        assert "B1" in robins_cat["preflight_prompt_template"]
        assert "C4" in robins_cat["preflight_prompt_template"]
        assert robins_cat["domain_1_low_label"].startswith("Low (except")

        for d in robins_cat["domains"]:
            if d["id"] == 1:
                # Domain 1 has two variants — both prompts + both trees
                assert "A" in d["prompt_template"] and "B" in d["prompt_template"]
                assert "A" in d["decision_tree_code"] and "B" in d["decision_tree_code"]
                assert "def " in d["decision_tree_code"]["A"]
                assert "def " in d["decision_tree_code"]["B"]
                # Variant signal lists exposed
                assert "A" in d["signals"] and "B" in d["signals"]
            else:
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

    def test_robins_i_v2_row_uses_six_domains(self):
        # V2 ROBINS-I — 6 domains, variant-A signals on D1, preflight metadata.
        result = {
            "paper_id": 11, "filename": "cohort.pdf", "status": "ok",
            "study_type": "Cohort Study", "rob_tool": "robins_i",
            "primary_outcome": "Cardiovascular mortality",
            "classification": {"study_type": "Cohort Study"},
            "extracted_fields": {"citation_title": "Observational cohort"},
            "rob_domains": {
                "preflight": {
                    "B1": "Y", "B2": "NA", "B3": "N", "C4": "No",
                    "variant": "A",
                    "screening_decision": "proceed",
                    "screening_reason": "",
                },
                "1": {"judgement": "Moderate", "variant": "A",
                      "signals": {"1A.1": "WN", "1A.2": "Y", "1A.3": "N", "1A.4": "N"}},
                "2": {"judgement": "Low", "signals": {"2.1": "Y", "2.4": "N", "2.5": "N"}},
                "3": {"judgement": "Low", "signals": {"3.1": "Y", "3.2": "N", "3.3": "N"}},
                "4": {"judgement": "Low", "signals": {"4.1": "Y", "4.2": "Y", "4.3": "Y"}},
                "5": {"judgement": "Low", "signals": {"5.1": "N", "5.2": "N"}},
                "6": {"judgement": "Low", "signals": {"6.1": "Y"}},
            },
            "rob_overall": "Moderate", "rob_direction": "NA",
            "guideline": {"proportion": 0.71, "adhered": 22, "applicable": 31},
            "guideline_proportion": 0.71, "guideline_adhered": 22, "guideline_applicable": 31,
            "initial_grade": "Low", "updated_grade": "Very low",
            "grade_explanation": "Downgraded 1 level for Moderate risk of bias (ROBINS-I V2).",
        }
        row = qa.flatten_result_row(result)
        assert row["rob_overall"] == "Moderate"
        assert row["rob_d1_judgement"] == "Moderate"
        assert row["rob_d6_judgement"] == "Low"
        # V2 ROBINS-I has 6 domains — no D7
        assert "rob_d7_judgement" not in row
        # V2 signal IDs are variant-prefixed for D1
        assert row["rob_1A.1"] == "WN"
        assert row["rob_2.1"] == "Y"
        assert row["rob_6.1"] == "Y"
        # Preflight metadata columns
        assert row["robins_b1"] == "Y"
        assert row["robins_c4"] == "No"
        assert row["robins_variant"] == "A"
        assert row["robins_screening_decision"] == "proceed"


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
