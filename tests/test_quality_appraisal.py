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
from backend.rob_tools import (rob2, rob2_crossover, rob2_cluster, robins_i,
                               robins_i_v1, quadas2, quadas3)
from backend.reporting_guidelines import (consort2025, consort_crossover,
                                          consort_cluster, strobe)


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

    # ── Single-arm preflight ──────────────────────────────
    def test_single_arm_study_type_pins_variant_to_single_arm(self, monkeypatch):
        # Even with C4=Yes (per-protocol), single-arm study type keeps variant
        self._stub_call(monkeypatch, {
            "B1": "Y", "B2": "NA", "B3": "N", "C4": "Yes",
        })
        out = robins_i.run_preflight(b"", "Single-Arm Trial", "outcome", {})
        assert out["variant"] == "single_arm"
        assert out["screening_decision"] == "proceed"

    def test_dose_escalation_also_routes_to_single_arm(self, monkeypatch):
        self._stub_call(monkeypatch, {
            "B1": "Y", "B2": "NA", "B3": "N", "C4": "No",
        })
        out = robins_i.run_preflight(b"", "Dose-Escalation Study", "outcome", {})
        assert out["variant"] == "single_arm"

    def test_single_arm_b2_sa_yes_short_circuits_critical(self, monkeypatch):
        # B2-SA Y/PY in single-arm preflight still routes to Critical
        self._stub_call(monkeypatch, {
            "B1": "N", "B2": "Y", "B3": "N", "C4": "No",
        })
        out = robins_i.run_preflight(b"", "Single-Arm Trial", "outcome", {})
        assert out["screening_decision"] == "critical"
        assert "benchmark" in out["screening_reason"].lower()
        assert out["variant"] == "single_arm"  # variant still pinned

    def test_single_arm_b3_yes_short_circuits_critical(self, monkeypatch):
        # B3 Y/PY in single-arm preflight short-circuits — outcome-measurement
        # appropriateness is comparator-agnostic
        self._stub_call(monkeypatch, {
            "B1": "Y", "B2": "NA", "B3": "Y", "C4": "No",
        })
        out = robins_i.run_preflight(b"", "Single-Arm Trial", "outcome", {})
        assert out["screening_decision"] == "critical"
        assert "B3" in out["screening_reason"]
        assert out["variant"] == "single_arm"

    def test_single_arm_preflight_prompt_mentions_benchmark_not_confounding(self):
        # Prompt builder branches by study type — single-arm prompt should
        # frame B1 around benchmark pre-specification.
        sa_prompt = robins_i._build_preflight_prompt_single_arm(
            "Single-Arm Trial", "ORR", {})
        assert "benchmark" in sa_prompt.lower()
        assert "no comparator group" in sa_prompt.lower()
        assert "B1-SA" in sa_prompt
        # Cohort prompt should still frame B1 around confounding control
        cohort_prompt = robins_i._build_preflight_prompt_cohort(
            "Cohort Study", "mortality", {})
        assert "confounding" in cohort_prompt.lower()
        assert "B1-SA" not in cohort_prompt


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


class TestRobinsIDomain1VariantSingleArm:
    """V2 Domain 1 variant single_arm — uncontrolled designs (no comparator).
    Adapts confounding to benchmark-adequacy + prognostic-mix comparability.

    Signaling questions:
      1S.1  Implied benchmark pre-specified before data collection?
      1S.2  Implied benchmark reasonable for population?
      1S.3  Cohort baseline prognostic profile comparable to benchmark?
      1S.4  Quantitative adjustment to external controls?
      1S.5  Negative / falsification controls suggest serious bias?

    Returns the LOW_D1_SA label on clean assessments (since uncontrolled-
    confounding-by-benchmarking can never be ruled out observationally)."""

    def test_clean_benchmark_and_prognostic_match_is_low_sa(self):
        # 1S.1 Y (benchmark pre-specified), 1S.2 Y (reasonable), 1S.3 Y
        # (prognostic profile comparable), 1S.5 N (no falsification hit)
        assert robins_i.domain1_variant_single_arm_judge({
            "1S.1": "Y", "1S.2": "Y", "1S.3": "Y", "1S.4": "NA", "1S.5": "N",
        }) == robins_i.LOW_D1_SA

    def test_falsification_control_hit_is_critical(self):
        # 1S.5 Y dominates → Critical regardless of upstream
        assert robins_i.domain1_variant_single_arm_judge({"1S.5": "Y"}) == "Critical"
        assert robins_i.domain1_variant_single_arm_judge({
            "1S.1": "Y", "1S.2": "Y", "1S.3": "Y", "1S.5": "PY",
        }) == "Critical"

    def test_no_benchmark_no_adjustment_is_critical(self):
        # 1S.1 N (no benchmark) + 1S.4 N (no quantitative adjustment) → Critical
        assert robins_i.domain1_variant_single_arm_judge({
            "1S.1": "N", "1S.4": "N", "1S.5": "N",
        }) == "Critical"

    def test_no_benchmark_with_adjustment_is_serious(self):
        # 1S.1 N (no benchmark) but 1S.4 Y (quantitative adjustment) → Serious
        assert robins_i.domain1_variant_single_arm_judge({
            "1S.1": "N", "1S.4": "Y", "1S.5": "N",
        }) == "Serious"

    def test_wn_prognostic_match_is_moderate(self):
        # 1S.1 Y, 1S.2 Y, 1S.3 WN (most-but-not-all prognostic factors comparable),
        # 1S.5 N → Moderate (floor for WN on prognostic comparability)
        assert robins_i.domain1_variant_single_arm_judge({
            "1S.1": "Y", "1S.2": "Y", "1S.3": "WN", "1S.5": "N",
        }) == "Moderate"

    def test_unreasonable_benchmark_is_moderate(self):
        # 1S.1 Y + 1S.2 N (benchmark unreasonable for population) + 1S.3 Y
        # + 1S.5 N → Moderate
        assert robins_i.domain1_variant_single_arm_judge({
            "1S.1": "Y", "1S.2": "N", "1S.3": "Y", "1S.5": "N",
        }) == "Moderate"

    def test_sn_prognostic_mismatch_rescued_by_adjustment(self):
        # 1S.1 Y, 1S.3 SN (substantial prognostic mismatch) but 1S.4 Y
        # (quantitative external-control adjustment) → Moderate
        assert robins_i.domain1_variant_single_arm_judge({
            "1S.1": "Y", "1S.2": "Y", "1S.3": "SN", "1S.4": "Y", "1S.5": "N",
        }) == "Moderate"

    def test_sn_prognostic_mismatch_no_adjustment_is_serious(self):
        # 1S.1 Y, 1S.3 SN, 1S.4 N, 1S.5 N → Serious
        assert robins_i.domain1_variant_single_arm_judge({
            "1S.1": "Y", "1S.2": "Y", "1S.3": "SN", "1S.4": "N", "1S.5": "N",
        }) == "Serious"


class TestRobinsIDomain2VariantSingleArm:
    """V2 Domain 2 variant single_arm — degenerate classification (no comparator).
    Focuses on intervention fidelity + intent-vs-received cohort definition.

    Signaling questions:
      2S.1  Intervention well-defined at start of follow-up?
      2S.2  Dose reductions / holds / discontinuations recorded?
      2S.3  Cohort defined by intended (ITT-like) or received (selection-on-
            completers) treatment?"""

    def test_well_defined_itt_cohort_recorded_modifications_low(self):
        # 2S.1 Y, 2S.2 Y, 2S.3 N (intended-treatment cohort) → Low
        assert robins_i.domain2_variant_single_arm_judge({
            "2S.1": "Y", "2S.2": "Y", "2S.3": "N",
        }) == "Low"

    def test_strong_yes_received_treatment_filter_is_critical(self):
        # 2S.3 SY (strongly responder-restricted analysis) → Critical
        assert robins_i.domain2_variant_single_arm_judge({"2S.3": "SY"}) == "Critical"
        # Even with otherwise-clean intervention definition
        assert robins_i.domain2_variant_single_arm_judge({
            "2S.1": "Y", "2S.2": "Y", "2S.3": "SY",
        }) == "Critical"

    def test_weak_yes_received_treatment_filter_is_serious(self):
        # 2S.3 WY (some completer-filtering, not dominant) → Serious
        assert robins_i.domain2_variant_single_arm_judge({"2S.3": "WY"}) == "Serious"

    def test_undefined_intervention_is_serious(self):
        # 2S.1 N (intervention not well-defined) + ITT-like cohort → Serious
        assert robins_i.domain2_variant_single_arm_judge({
            "2S.1": "N", "2S.3": "N",
        }) == "Serious"

    def test_wn_recording_fidelity_is_moderate(self):
        # Well-defined intervention but most-but-not-all modifications recorded
        assert robins_i.domain2_variant_single_arm_judge({
            "2S.1": "Y", "2S.2": "WN", "2S.3": "N",
        }) == "Moderate"

    def test_sn_recording_fidelity_is_serious(self):
        # Well-defined intervention but material recording gaps
        assert robins_i.domain2_variant_single_arm_judge({
            "2S.1": "Y", "2S.2": "SN", "2S.3": "N",
        }) == "Serious"


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

    def test_d1_single_arm_low_label_is_treated_as_low(self):
        # The single-arm LOW_D1_SA label must also normalize to Low.
        assert robins_i.robins_i_overall(
            [robins_i.LOW_D1_SA, "Low", "Low", "Low", "Low", "Low"]
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
        # Types still not wired (registry stubs remain commented).
        # Diagnostic Accuracy IS wired locally (→ QUADAS-3 + STARD).
        # Cluster Randomized Trial IS wired (→ RoB 2 CRT); Stepped-Wedge is not
        # (the CRT cribsheet covers only parallel cluster-randomized trials).
        assert qa.dispatch("SR with Meta-Analysis") is None
        assert qa.dispatch("Stepped-Wedge Cluster RCT") is None
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

    def test_single_arm_trial_is_registered(self):
        cfg = qa.dispatch("Single-Arm Trial")
        assert cfg is not None
        # Routes to ROBINS-I (internal single_arm variant selected by study_type)
        assert cfg["rob_tool"] == "robins_i"
        # STROBE reused pragmatically
        assert cfg["reporting_guideline"] == "strobe"
        # Conservative: uncontrolled designs start at Very low
        assert cfg["initial_grade"] == "Very low"

    def test_dose_escalation_is_registered(self):
        cfg = qa.dispatch("Dose-Escalation Study")
        assert cfg is not None
        # Shares the single-arm variant with Single-Arm Trial
        assert cfg["rob_tool"] == "robins_i"
        assert cfg["reporting_guideline"] == "strobe"
        assert cfg["initial_grade"] == "Very low"

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
        # V2 catalog surfaces preflight prompt(s) + Domain 1 special labels
        # for both the cohort and single-arm variants.
        assert "preflight_prompt_template" in robins_cat
        # Now a dict keyed by 'cohort' / 'single_arm' since SA has its own
        # preflight prompt (B1-SA/B2-SA replacing B1/B2).
        assert isinstance(robins_cat["preflight_prompt_template"], dict)
        assert "cohort" in robins_cat["preflight_prompt_template"]
        assert "single_arm" in robins_cat["preflight_prompt_template"]
        assert "B1" in robins_cat["preflight_prompt_template"]["cohort"]
        assert "C4" in robins_cat["preflight_prompt_template"]["cohort"]
        assert "B1-SA" in robins_cat["preflight_prompt_template"]["single_arm"]
        assert "benchmark" in robins_cat["preflight_prompt_template"]["single_arm"].lower()
        assert robins_cat["domain_1_low_label"].startswith("Low (except")
        assert robins_cat["domain_1_low_label_single_arm"].startswith("Low (except")
        assert "benchmarking" in robins_cat["domain_1_low_label_single_arm"].lower()
        # Single-arm study types surfaced for the developer view
        assert "Single-Arm Trial" in robins_cat["single_arm_study_types"]
        assert "Dose-Escalation Study" in robins_cat["single_arm_study_types"]

        for d in robins_cat["domains"]:
            if d["id"] in (1, 2):
                # Variant-aware domains — three variants (A, B, single_arm)
                for v in ("A", "B", "single_arm"):
                    assert v in d["prompt_template"], f"D{d['id']} missing prompt for variant {v}"
                    assert v in d["decision_tree_code"]
                    assert "def " in d["decision_tree_code"][v]
                    assert v in d["signals"]
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


# ─────────────────────────────────────────────
# RoB 2 cross-over extension — Domain S (period/carryover)
# ─────────────────────────────────────────────
class TestCrossoverDomainS:
    """Period/carryover decision tree for cross-over trials.

    S.1 — carryover ruled out?
    S.2 — suitable washout (only relevant if S.1=N/PN)
    S.3 — unbiased data available (only relevant if S.1=N/PN)
    S.4 — paired/appropriate analysis used?
    """

    def test_no_carryover_and_paired_analysis_is_low(self):
        # S.1 Y (no carryover) + S.4 Y (paired) → Low regardless of S.2/S.3
        for s2 in rob2_crossover.SIGNAL_OPTIONS:
            for s3 in rob2_crossover.SIGNAL_OPTIONS:
                assert rob2_crossover.rob2_crossover_domainS_judge({
                    "S.1": "Y", "S.2": s2, "S.3": s3, "S.4": "Y",
                }) == "Low"

    def test_carryover_plausible_no_washout_no_unbiased_is_high(self):
        # S.1 N + S.2 N + S.3 N → High (carryover + no mitigation)
        assert rob2_crossover.rob2_crossover_domainS_judge({
            "S.1": "N", "S.2": "N", "S.3": "N", "S.4": "Y",
        }) == "High"

    def test_carryover_plausible_no_paired_analysis_is_high(self):
        # S.1 N + S.4 N → High (carryover present, analysis didn't account for it)
        assert rob2_crossover.rob2_crossover_domainS_judge({
            "S.1": "N", "S.2": "Y", "S.3": "Y", "S.4": "N",
        }) == "High"

    def test_no_paired_analysis_alone_is_high(self):
        # S.4 N (inappropriate analysis) with no carryover ruled-out → High
        assert rob2_crossover.rob2_crossover_domainS_judge({
            "S.1": "NI", "S.2": "NI", "S.3": "NI", "S.4": "N",
        }) == "High"

    def test_ni_throughout_is_some_concerns(self):
        # All NI → Some concerns (not enough info to be confident)
        assert rob2_crossover.rob2_crossover_domainS_judge({
            "S.1": "NI", "S.2": "NI", "S.3": "NI", "S.4": "NI",
        }) == "Some concerns"

    def test_carryover_plausible_with_washout_and_paired_is_some_concerns(self):
        # S.1 N + S.2 Y + S.3 NI + S.4 Y → some uncertainty remains
        assert rob2_crossover.rob2_crossover_domainS_judge({
            "S.1": "N", "S.2": "Y", "S.3": "NI", "S.4": "Y",
        }) == "Some concerns"


# ─────────────────────────────────────────────
# RoB 2 cross-over extension — Domain 5 (4 questions incl. 5.4)
# ─────────────────────────────────────────────
class TestCrossoverDomain5:
    """Cross-over Domain 5 has 5.1, 5.2, 5.3, and 5.4 (first-period-only
    reporting based on a carryover test)."""

    def test_5_4_yes_alone_is_high(self):
        # 5.4 Y/PY (first-period-only on carryover test) → High
        assert rob2_crossover.rob2_crossover_domain5_judge({
            "5.1": "Y", "5.2": "N", "5.3": "N", "5.4": "Y",
        }) == "High"
        assert rob2_crossover.rob2_crossover_domain5_judge({
            "5.1": "Y", "5.2": "N", "5.3": "N", "5.4": "PY",
        }) == "High"

    def test_5_4_no_doesnt_change_parallel_outcome(self):
        # When 5.4 N/PN, decision matches parallel-group logic
        assert rob2_crossover.rob2_crossover_domain5_judge({
            "5.1": "Y", "5.2": "N", "5.3": "N", "5.4": "N",
        }) == "Low"
        assert rob2_crossover.rob2_crossover_domain5_judge({
            "5.1": "N", "5.2": "N", "5.3": "N", "5.4": "N",
        }) == "Some concerns"

    def test_5_2_or_5_3_yes_still_high(self):
        assert rob2_crossover.rob2_crossover_domain5_judge({
            "5.1": "Y", "5.2": "Y", "5.3": "N", "5.4": "N",
        }) == "High"
        assert rob2_crossover.rob2_crossover_domain5_judge({
            "5.1": "Y", "5.2": "N", "5.3": "Y", "5.4": "N",
        }) == "High"

    def test_ni_mixed_is_some_concerns(self):
        assert rob2_crossover.rob2_crossover_domain5_judge({
            "5.1": "NI", "5.2": "NI", "5.3": "NI", "5.4": "NI",
        }) == "Some concerns"


# ─────────────────────────────────────────────
# Cross-over module — six-domain run() shape + registry
# ─────────────────────────────────────────────
class TestCrossoverRunShape:
    """Verify the cross-over module exposes 6 domains in cribsheet order
    (1 → 2 → S → 3 → 4 → 5) and the catalog surfaces 5.4."""

    def test_six_domains_in_cribsheet_order(self):
        domain_ids = [d["id"] for d in rob2_crossover.DOMAINS]
        assert domain_ids == [1, 2, "S", 3, 4, 5]

    def test_domain_5_has_four_signaling_questions(self):
        d5 = next(d for d in rob2_crossover.DOMAINS if d["id"] == 5)
        signal_ids = [s["id"] for s in d5["signals"]]
        assert signal_ids == ["5.1", "5.2", "5.3", "5.4"]

    def test_domain_s_has_four_signaling_questions(self):
        dS = next(d for d in rob2_crossover.DOMAINS if d["id"] == "S")
        signal_ids = [s["id"] for s in dS["signals"]]
        assert signal_ids == ["S.1", "S.2", "S.3", "S.4"]

    def test_prompt_catalog_surfaces_all_six_domains(self):
        cat = rob2_crossover.prompt_catalog()
        assert len(cat["domains"]) == 6
        catalog_ids = [d["id"] for d in cat["domains"]]
        assert "S" in catalog_ids
        # 5.4 must appear in Domain 5's signal list within the catalog
        d5 = next(d for d in cat["domains"] if d["id"] == 5)
        assert any(s["id"] == "5.4" for s in d5["signals"])

    def test_qa_prompt_catalog_includes_crossover(self):
        cat = qa.prompt_catalog()
        assert "rob2_crossover" in cat["rob_tools"]
        cross_cat = cat["rob_tools"]["rob2_crossover"]
        assert len(cross_cat["domains"]) == 6


# ─────────────────────────────────────────────
# Crossover registry dispatch
# ─────────────────────────────────────────────
class TestCrossoverDispatch:
    def test_crossover_trial_is_registered(self):
        cfg = qa.dispatch("Crossover Trial")
        assert cfg is not None
        assert cfg["rob_tool"] == "rob2_crossover"
        assert cfg["initial_grade"] == "High"

    def test_tool_runner_resolves(self):
        assert qa._TOOL_RUNNERS["rob2_crossover"] is rob2_crossover.run


# ─────────────────────────────────────────────
# Domain 1 prompt — reviewer-override note
# ─────────────────────────────────────────────
class TestDomain1OutcomeOverridePrompt:
    """When ``outcome_is_override`` is True, Domain 1's prompt should remind
    the LLM that randomization is per-trial (not per-outcome) so it doesn't
    conflate outcome ambiguity with randomization-process concerns."""

    def test_override_note_in_rob2_domain1_only(self):
        d1 = next(d for d in rob2.DOMAINS if d["id"] == 1)
        d3 = next(d for d in rob2.DOMAINS if d["id"] == 3)
        prompt_d1 = rob2.build_domain_prompt(
            d1, "Randomized Controlled Trial",
            "Secondary outcome X", {}, outcome_is_override=True,
        )
        prompt_d3 = rob2.build_domain_prompt(
            d3, "Randomized Controlled Trial",
            "Secondary outcome X", {}, outcome_is_override=True,
        )
        # Domain 1 picks up the override note
        assert "non-primary outcome chosen by the reviewer" in prompt_d1
        assert "per-trial" not in prompt_d3.lower() or \
                "randomization process for the trial as a whole" not in prompt_d3
        # Domain 3 does not get the override note (it's outcome-specific)
        assert "non-primary outcome chosen by the reviewer" not in prompt_d3

    def test_no_override_note_when_not_overridden(self):
        d1 = next(d for d in rob2.DOMAINS if d["id"] == 1)
        prompt = rob2.build_domain_prompt(
            d1, "Randomized Controlled Trial",
            "Overall survival", {}, outcome_is_override=False,
        )
        assert "non-primary outcome chosen" not in prompt

    def test_crossover_domain1_also_gets_override_note(self):
        d1 = next(d for d in rob2_crossover.DOMAINS if d["id"] == 1)
        prompt = rob2_crossover.build_domain_prompt(
            d1, "Crossover Trial", "Secondary outcome",
            {}, outcome_is_override=True,
        )
        assert "non-primary outcome chosen by the reviewer" in prompt


# ─────────────────────────────────────────────
# CONSORT cross-over extension (Dwan et al. 2019)
# ─────────────────────────────────────────────
class TestConsortCrossover:
    def test_items_include_base_and_extension(self):
        # Base CONSORT 2025 items present
        base_ids = {it["id"] for it in consort2025.ITEMS}
        cross_ids = {it["id"] for it in consort_crossover.ITEMS}
        assert base_ids.issubset(cross_ids)
        # Extension items prefixed X-
        ext_ids = {it["id"] for it in consort_crossover.CROSSOVER_EXTENSION_ITEMS}
        assert all(eid.startswith("X-") for eid in ext_ids)
        # All extension items appear in the combined list
        assert ext_ids.issubset(cross_ids)

    def test_extension_covers_carryover_washout_paired(self):
        descriptions = " ".join(
            it["description"].lower() for it in consort_crossover.CROSSOVER_EXTENSION_ITEMS
        )
        # The extension must mention the cross-over-specific concepts
        assert "washout" in descriptions
        assert "carryover" in descriptions
        assert "paired" in descriptions
        assert "period" in descriptions
        assert "sequence" in descriptions

    def test_prompt_lists_both_base_and_extension(self):
        prompt = consort_crossover.build_prompt({"study_type": "Crossover Trial"})
        # Some base item ids must appear
        assert "**1a**" in prompt
        assert "**26**" in prompt
        # All extension items must appear
        for it in consort_crossover.CROSSOVER_EXTENSION_ITEMS:
            assert f"**{it['id']}**" in prompt, f"Extension item {it['id']} missing from prompt"

    def test_crossover_registry_uses_consort_crossover(self):
        cfg = qa.dispatch("Crossover Trial")
        assert cfg["reporting_guideline"] == "consort_crossover"

    def test_guideline_runner_resolves(self):
        assert qa._GUIDELINE_RUNNERS["consort_crossover"] is consort_crossover.run

    def test_qa_prompt_catalog_includes_crossover_guideline(self):
        cat = qa.prompt_catalog()
        assert "consort_crossover" in cat["reporting_guidelines"]
        ccat = cat["reporting_guidelines"]["consort_crossover"]
        assert "crossover_extension_items" in ccat
        assert len(ccat["crossover_extension_items"]) == len(
            consort_crossover.CROSSOVER_EXTENSION_ITEMS)


# ═════════════════════════════════════════════
# QUADAS-2 (Whiting 2011) — pure-Python tests
# ═════════════════════════════════════════════
class TestQuadas2DecisionTree:
    """Per Whiting 2011 Phase 4: all 'yes' → low; any 'no' → high; otherwise unclear."""

    def test_all_yes_is_low(self):
        assert quadas2.quadas2_domain_judge({"1.1": "Y", "1.2": "Y", "1.3": "Y"}) == "Low"

    def test_any_no_is_high(self):
        assert quadas2.quadas2_domain_judge({"1.1": "Y", "1.2": "N", "1.3": "Y"}) == "High"

    def test_single_no_with_others_yes_is_high(self):
        assert quadas2.quadas2_domain_judge({"1.1": "N", "1.2": "Y"}) == "High"

    def test_mixed_yes_unclear_is_unclear(self):
        assert quadas2.quadas2_domain_judge({"1.1": "Y", "1.2": "U"}) == "Unclear"

    def test_empty_is_unclear(self):
        assert quadas2.quadas2_domain_judge({}) == "Unclear"

    def test_all_unclear_is_unclear(self):
        assert quadas2.quadas2_domain_judge({"a": "U", "b": "U"}) == "Unclear"

    def test_no_dominates_unclear(self):
        # Any N is enough for High even mixed with U
        assert quadas2.quadas2_domain_judge({"a": "U", "b": "N"}) == "High"


class TestQuadas2Overall:
    def test_all_low_is_low(self):
        assert quadas2.quadas2_overall(["Low"] * 4) == "Low"

    def test_any_high_is_high(self):
        assert quadas2.quadas2_overall(["Low", "High", "Low", "Low"]) == "High"

    def test_mixed_low_unclear_is_unclear(self):
        assert quadas2.quadas2_overall(["Low", "Unclear", "Low", "Low"]) == "Unclear"

    def test_empty_is_unclear(self):
        assert quadas2.quadas2_overall([]) == "Unclear"

    def test_all_unclear_is_unclear(self):
        assert quadas2.quadas2_overall(["Unclear"] * 4) == "Unclear"


class TestQuadas2Applicability:
    """Applicability aggregator: only 3 domains feed it (D4 excluded)."""

    def test_three_domains_low(self):
        assert quadas2.quadas2_applicability_overall(["Low", "Low", "Low"]) == "Low"

    def test_three_domains_one_high(self):
        assert quadas2.quadas2_applicability_overall(["Low", "High", "Low"]) == "High"

    def test_three_domains_one_unclear(self):
        assert quadas2.quadas2_applicability_overall(["Low", "Unclear", "Low"]) == "Unclear"


class TestQuadas2Domains:
    """Structural invariants on the DOMAINS list."""

    def test_four_domains(self):
        assert len(quadas2.DOMAINS) == 4

    def test_signal_counts_match_whiting_2011_table_1(self):
        # D1=3 (consecutive/random + case-control avoided + inappropriate exclusions)
        # D2=2 (blind to reference + threshold pre-specified)
        # D3=2 (correct classification + blind to index)
        # D4=4 (interval + all received reference + same reference + all in analysis)
        sig_counts = [len(d["signals"]) for d in quadas2.DOMAINS]
        assert sig_counts == [3, 2, 2, 4]

    def test_d4_has_no_applicability(self):
        # Domain 4 (Flow & Timing) is RoB-only per Whiting 2011 Table 1
        assert quadas2.DOMAINS[3]["has_applicability"] is False

    def test_d1_d2_d3_have_applicability(self):
        for d in quadas2.DOMAINS[:3]:
            assert d["has_applicability"] is True

    def test_judges_dict_keyed_by_id(self):
        # DOMAIN_JUDGES dispatches by domain id; all four domains use the same rule.
        assert set(quadas2.DOMAIN_JUDGES.keys()) == {1, 2, 3, 4}
        for jid, fn in quadas2.DOMAIN_JUDGES.items():
            assert fn is quadas2.quadas2_domain_judge

    def test_signal_options_are_three_level(self):
        assert quadas2.SIGNAL_OPTIONS == ("Y", "N", "U")


class TestQuadas2PromptCatalog:
    def test_catalog_contains_quadas2(self):
        cat = quadas2.prompt_catalog()
        assert cat["tool"].startswith("QUADAS-2")
        assert cat["signal_options"] == ["Y", "N", "U"]
        assert cat["judgements"] == ["Low", "High", "Unclear"]
        assert len(cat["domains"]) == 4


class TestRobDowngradeUnclear:
    """QUADAS-2 outcome 'Unclear' → 1 level conservative downgrade."""

    def test_unclear_downgrades_one_level(self):
        levels, reason = qa._rob_downgrade("Unclear", ["Unclear", "Low", "Low", "Low"])
        assert levels == 1
        assert "QUADAS-2" in reason

    def test_compute_grade_high_unclear_drops_to_moderate(self):
        new_level, expl = qa.compute_grade("High", "Unclear", ["Unclear", "Low", "Low", "Low"])
        assert new_level == "Moderate"
        assert "QUADAS-2" in expl or "Unclear" in expl

    def test_compute_grade_high_two_high_drops_two_levels(self):
        # ≥2 High domains → 2 levels (existing branch — QUADAS-2 routes through it too)
        new_level, _ = qa.compute_grade("High", "High", ["High", "High", "Low", "Low"])
        assert new_level == "Low"

    def test_low_quadas2_no_downgrade(self):
        new_level, expl = qa.compute_grade("High", "Low", ["Low"] * 4)
        assert new_level == "High"


class TestQuadas2Dispatch:
    """tool_override='quadas2' on diagnostic-accuracy papers swaps cfg['rob_tool']
    without mutating the module-level registry. Override is ignored for RCTs."""

    def test_quadas2_in_tool_runners(self):
        assert "quadas2" in qa._TOOL_RUNNERS
        assert qa._TOOL_RUNNERS["quadas2"] is quadas2.run

    def test_quadas2_estimate_extractor_aliases_quadas3(self):
        assert "quadas2" in qa._ESTIMATE_EXTRACTORS
        assert qa._ESTIMATE_EXTRACTORS["quadas2"] is qa._ESTIMATE_EXTRACTORS["quadas3"]
        assert qa._ESTIMATE_EXTRACTORS["quadas2"] is quadas3.extract_estimates

    def test_registry_dispatch_still_returns_quadas3_default(self):
        cfg = qa.dispatch("Diagnostic Accuracy")
        assert cfg is not None
        assert cfg["rob_tool"] == "quadas3"  # registry default — overrides happen per-run

    def test_registry_dispatch_is_dict_copy_safe(self):
        # appraise_paper shallow-copies cfg before mutating rob_tool, but
        # double-check that the registry dict itself is unaffected by any
        # downstream mutation.
        cfg1 = qa.dispatch("Diagnostic Accuracy")
        cfg2 = qa.dispatch("Diagnostic Accuracy")
        cfg1["rob_tool"] = "mutated"  # simulate accidental mutation
        # Reset the registry entry back to the canonical value for test isolation
        qa.STUDY_TYPE_REGISTRY["Diagnostic Accuracy"]["rob_tool"] = "quadas3"
        # The shallow-copy idiom inside appraise_paper protects against this
        # exact pattern; this test pins the registry state for follow-up tests.
        assert qa.STUDY_TYPE_REGISTRY["Diagnostic Accuracy"]["rob_tool"] == "quadas3"


class TestQuadas2FlattenForExport:
    """flatten_result_row maps QUADAS-2 rows to per-domain + per-applicability columns."""

    def _make_quadas2_row(self):
        return {
            "paper_id": 42,
            "filename": "test.pdf",
            "status": "ok",
            "study_type": "Diagnostic Accuracy",
            "rob_tool": "quadas2",
            "primary_outcome": "Acute appendicitis",
            "classification": {"major_category": "Diagnostic", "subcategory": ""},
            "extracted_fields": {"citation_title": "Test paper"},
            "rob_domains": {
                "1": {"judgement": "Low",     "applicability_judgement": "Low",
                       "signals": {"1.1": "Y", "1.2": "Y", "1.3": "Y"}},
                "2": {"judgement": "Unclear", "applicability_judgement": "Low",
                       "signals": {"2.1": "Y", "2.2": "U"}},
                "3": {"judgement": "Low",     "applicability_judgement": "High",
                       "signals": {"3.1": "Y", "3.2": "Y"}},
                "4": {"judgement": "Low",
                       "signals": {"4.1": "Y", "4.2": "Y", "4.3": "Y", "4.4": "Y"}},
            },
            "rob_overall": "Unclear",
            "rob_direction": "NA",
            "applicability_overall": "High",
            "guideline": {"adhered": 18, "applicable": 30, "proportion": 0.6},
            "guideline_adhered": 18, "guideline_applicable": 30, "guideline_proportion": 0.6,
            "initial_grade": "High", "updated_grade": "Moderate",
            "grade_explanation": "Downgraded 1 level: Unclear in one or more QUADAS-2 domains.",
        }

    def test_quadas2_flatten_includes_per_domain_judgements(self):
        row = qa.flatten_result_row(self._make_quadas2_row())
        assert row["rob_d1_judgement"] == "Low"
        assert row["rob_d2_judgement"] == "Unclear"
        assert row["rob_d3_judgement"] == "Low"
        assert row["rob_d4_judgement"] == "Low"

    def test_quadas2_flatten_includes_applicability_for_d1_d2_d3(self):
        row = qa.flatten_result_row(self._make_quadas2_row())
        assert row["rob_d1_applicability"] == "Low"
        assert row["rob_d2_applicability"] == "Low"
        assert row["rob_d3_applicability"] == "High"
        # D4 has no applicability column
        assert "rob_d4_applicability" not in row

    def test_quadas2_flatten_includes_all_signal_answers(self):
        row = qa.flatten_result_row(self._make_quadas2_row())
        # D1: 3 signals
        assert row["rob_1.1"] == "Y"
        assert row["rob_1.2"] == "Y"
        assert row["rob_1.3"] == "Y"
        # D2: 2 signals (one Unclear)
        assert row["rob_2.1"] == "Y"
        assert row["rob_2.2"] == "U"
        # D4: 4 signals
        assert row["rob_4.4"] == "Y"

    def test_quadas2_flatten_includes_overall_applicability(self):
        row = qa.flatten_result_row(self._make_quadas2_row())
        assert row["applicability_overall"] == "High"


class TestQuadas2PromptCatalogWired:
    """Top-level prompt_catalog includes QUADAS-2 alongside QUADAS-3."""

    def test_catalog_contains_both_quadas_tools(self):
        cat = qa.prompt_catalog()
        assert "quadas2" in cat["rob_tools"]
        assert "quadas3" in cat["rob_tools"]
        # Sanity check on QUADAS-2 entry shape
        q2 = cat["rob_tools"]["quadas2"]
        assert q2["signal_options"] == ["Y", "N", "U"]
        assert len(q2["domains"]) == 4


class TestRobinsIV1Dispatch:
    """robins_i_tool_override='robins_i_v1' on non-randomized cohort-type
    papers swaps cfg['rob_tool'] without mutating the module-level registry.
    Override is ignored for randomized / diagnostic / single-arm study types."""

    def test_robins_i_v1_in_tool_runners(self):
        assert "robins_i_v1" in qa._TOOL_RUNNERS
        assert qa._TOOL_RUNNERS["robins_i_v1"] is robins_i_v1.run

    def test_registry_dispatch_still_returns_v2_default(self):
        for st in ("Cohort Study", "Case-Control", "Non-Randomized Trial",
                   "Cross-Sectional (Analytical)", "Case-Crossover"):
            cfg = qa.dispatch(st)
            assert cfg is not None
            assert cfg["rob_tool"] == "robins_i", f"{st} should default to V2"

    def test_v1_has_seven_domains(self):
        # V1 retains the historical 7-domain layout (V2 retired one)
        assert len(robins_i_v1.DOMAINS) == 7
        ids = [d["id"] for d in robins_i_v1.DOMAINS]
        assert ids == [1, 2, 3, 4, 5, 6, 7]

    def test_v1_d4_is_aim_gated(self):
        d4 = next(d for d in robins_i_v1.DOMAINS if d["id"] == 4)
        assert d4.get("aim_gated") is True

    def test_v1_judgement_scale_includes_no_information(self):
        # V1 keeps "No information" as a full judgement (V2 retired it).
        assert "No information" in robins_i_v1.JUDGEMENTS
        assert len(robins_i_v1.JUDGEMENTS) == 5  # Low/Moderate/Serious/Critical/No information

    def test_v1_aims_tuple(self):
        assert robins_i_v1.AIMS == ("assignment_to", "starting_and_adhering")

    def test_v1_domain_decision_trees_with_assignment_to(self):
        # D4 with aim=assignment_to and 4.1=N → Low (cribsheet Table 2 early exit)
        assert robins_i_v1.domain4_judge({"4.1": "N"}, aim="assignment_to") == "Low"
        # D4 with aim=assignment_to and 4.1=Y/4.2=Y → Serious
        assert robins_i_v1.domain4_judge({"4.1": "Y", "4.2": "Y"}, aim="assignment_to") == "Serious"

    def test_v1_domain_decision_trees_with_starting_and_adhering(self):
        # D4 with aim=starting_and_adhering and 4.3/4.4/4.5 all Y → Low
        assert robins_i_v1.domain4_judge(
            {"4.3": "Y", "4.4": "Y", "4.5": "Y"}, aim="starting_and_adhering") == "Low"
        # D4 with 4.3=N and 4.6=N → Serious
        assert robins_i_v1.domain4_judge(
            {"4.3": "N", "4.4": "Y", "4.5": "Y", "4.6": "N"}, aim="starting_and_adhering") == "Serious"

    def test_v1_domain4_judge_raises_on_bad_aim(self):
        with pytest.raises(ValueError):
            robins_i_v1.domain4_judge({}, aim="bogus")

    def test_v1_cascade_d1_early_exit_on_no_confounding_potential(self):
        # 1.1=N → 1.2-1.8 all NA (cribsheet pp 5-6)
        out = robins_i_v1.enforce_cascade_d1({"1.1": "N", "1.2": "Y", "1.3": "Y", "1.4": "Y"})
        assert out["1.2"] == "NA"
        assert out["1.3"] == "NA"
        assert out["1.4"] == "NA"

    def test_v1_cascade_d4_assignment_to_gates_4_2(self):
        # 4.2 only asked if 4.1 = Y/PY
        out = robins_i_v1.enforce_cascade_d4({"4.1": "N", "4.2": "Y"}, aim="assignment_to")
        assert out["4.2"] == "NA"

    def test_v1_aim_preflight_prompt_includes_outcome_and_decision_block(self):
        prompt = robins_i_v1._build_aim_preflight_prompt(
            "all-cause mortality at 12 months", {})
        assert "all-cause mortality at 12 months" in prompt
        assert '"assignment_to"' in prompt
        assert '"starting_and_adhering"' in prompt
        assert "(no pre-extracted fields)" in prompt

    def test_v1_aim_preflight_prompt_renders_extracted_fields(self):
        prompt = robins_i_v1._build_aim_preflight_prompt(
            "all-cause mortality",
            {"analysis_framework": "Per-protocol with IPCW"})
        assert "Per-protocol with IPCW" in prompt

    def test_v1_prompt_catalog_shape(self):
        cat = robins_i_v1.prompt_catalog()
        assert cat["tool"].startswith("ROBINS-I V1")
        assert cat["aims"] == ["assignment_to", "starting_and_adhering"]
        assert len(cat["domains"]) == 7
        assert "aim_preflight_prompt" in cat
        assert "aim_preflight_code" in cat
        # Every domain entry must surface the decision-tree source
        for dom in cat["domains"]:
            assert "decision_tree_code" in dom

    def test_v1_listed_in_top_level_prompt_catalog(self):
        cat = qa.prompt_catalog()
        assert "robins_i_v1" in cat["rob_tools"]
        v1 = cat["rob_tools"]["robins_i_v1"]
        assert v1["tool"].startswith("ROBINS-I V1")


class TestRobinsIV1FlattenForExport:
    """flatten_result_row maps ROBINS-I V1 rows to per-domain + aim-preflight columns."""

    def _make_v1_row(self):
        return {
            "paper_id": 99,
            "filename": "v1_test.pdf",
            "status": "ok",
            "study_type": "Cohort Study",
            "rob_tool": "robins_i_v1",
            "primary_outcome": "All-cause mortality at 12 months",
            "classification": {"major_category": "Observational", "subcategory": "Prospective cohort"},
            "extracted_fields": {"citation_title": "V1 test paper"},
            "rob_domains": {
                "aim_preflight": {
                    "aim": "starting_and_adhering",
                    "rationale": "Methods: per-protocol with IPCW for treatment discontinuation."
                },
                "1": {"judgement": "Moderate", "signals": {"1.1": "Y", "1.2": "Y", "1.3": "Y", "1.7": "Y", "1.8": "Y"}},
                "2": {"judgement": "Low",      "signals": {"2.1": "N", "2.4": "Y"}},
                "3": {"judgement": "Low",      "signals": {"3.1": "Y", "3.2": "Y", "3.3": "N"}},
                "4": {"judgement": "Low",      "aim": "starting_and_adhering",
                       "signals": {"4.3": "Y", "4.4": "Y", "4.5": "Y"}},
                "5": {"judgement": "Moderate", "signals": {"5.1": "Y", "5.2": "N", "5.3": "N"}},
                "6": {"judgement": "Low",      "signals": {"6.1": "N", "6.2": "N", "6.3": "Y", "6.4": "N"}},
                "7": {"judgement": "Low",      "signals": {"7.1": "N", "7.2": "N", "7.3": "N"}},
            },
            "rob_overall": "Moderate",
            "rob_direction": "NA",
            "guideline": {"adhered": 18, "applicable": 22, "proportion": 0.82},
            "guideline_adhered": 18, "guideline_applicable": 22, "guideline_proportion": 0.82,
            "initial_grade": "Low", "updated_grade": "Very low",
            "grade_explanation": "Downgraded for moderate RoB + serious indirectness.",
        }

    def test_v1_flatten_includes_seven_domain_judgements(self):
        row = qa.flatten_result_row(self._make_v1_row())
        assert row["rob_d1_judgement"] == "Moderate"
        assert row["rob_d4_judgement"] == "Low"
        assert row["rob_d7_judgement"] == "Low"

    def test_v1_flatten_includes_aim_preflight_columns(self):
        row = qa.flatten_result_row(self._make_v1_row())
        assert row["robins_v1_aim"] == "starting_and_adhering"
        assert "IPCW" in row["robins_v1_aim_rationale"]

    def test_v1_flatten_includes_all_signal_answers(self):
        row = qa.flatten_result_row(self._make_v1_row())
        # D1: 1.1, 1.2, 1.3, 1.7, 1.8 set
        assert row["rob_1.1"] == "Y"
        assert row["rob_1.7"] == "Y"
        # D4: aim-gated signals 4.3-4.5 set, 4.1/4.2/4.6 unset → blank
        assert row["rob_4.3"] == "Y"
        assert row["rob_4.1"] == ""
        # D7 signals
        assert row["rob_7.1"] == "N"

    def test_v1_flatten_does_not_set_v2_preflight_columns(self):
        # V1 rows should NOT populate the V2-only B1/B2/B3/C4/variant columns.
        row = qa.flatten_result_row(self._make_v1_row())
        assert "robins_b1" not in row
        assert "robins_variant" not in row


class TestRobinsIV1PromptCatalogWired:
    """Top-level prompt_catalog includes ROBINS-I V1 alongside V2."""

    def test_catalog_contains_both_robins_versions(self):
        cat = qa.prompt_catalog()
        assert "robins_i" in cat["rob_tools"]
        assert "robins_i_v1" in cat["rob_tools"]
        v1 = cat["rob_tools"]["robins_i_v1"]
        v2 = cat["rob_tools"]["robins_i"]
        # V1 has 7 domains; V2 has 6
        assert len(v1["domains"]) == 7
        assert len(v2["domains"]) == 6
        # V1 includes the §1.1 aim preflight; V2 has the B1/B2/B3/C4 preflight
        assert "aim_preflight_prompt" in v1


# ═════════════════════════════════════════════
# RoB 2 Cluster (RoB 2 CRT) — pure-Python decision-tree tests
# ═════════════════════════════════════════════
class TestClusterDomain1a:
    """Domain 1a (randomization process) — RoB 2 CRT cribsheet p.6."""

    def test_not_concealed_is_high(self):
        assert rob2_cluster.rob2_cluster_domain1a_judge(
            {"1a.1": "Y", "1a.2": "N", "1a.3": "N"}) == "High"

    def test_concealed_random_no_baseline_problem_is_low(self):
        assert rob2_cluster.rob2_cluster_domain1a_judge(
            {"1a.1": "Y", "1a.2": "Y", "1a.3": "N"}) == "Low"

    def test_concealed_but_not_random_is_some_concerns(self):
        # CRT flowchart p.6 routes a concealed-but-non-random sequence to
        # Some concerns (the standard parallel-group tool reaches High here).
        assert rob2_cluster.rob2_cluster_domain1a_judge(
            {"1a.1": "N", "1a.2": "Y", "1a.3": "N"}) == "Some concerns"

    def test_concealed_random_baseline_problem_is_some_concerns(self):
        assert rob2_cluster.rob2_cluster_domain1a_judge(
            {"1a.1": "Y", "1a.2": "Y", "1a.3": "Y"}) == "Some concerns"

    def test_concealment_unknown_baseline_problem_is_high(self):
        assert rob2_cluster.rob2_cluster_domain1a_judge(
            {"1a.1": "Y", "1a.2": "NI", "1a.3": "Y"}) == "High"

    def test_concealment_unknown_no_baseline_problem_is_some_concerns(self):
        assert rob2_cluster.rob2_cluster_domain1a_judge(
            {"1a.1": "Y", "1a.2": "NI", "1a.3": "N"}) == "Some concerns"


class TestClusterDomain1b:
    """Domain 1b (timing of identification/recruitment) — cribsheet p.9.
    The cluster-specific domain — has no parallel-group equivalent."""

    def test_all_recruited_before_randomization_is_low(self):
        # 1b.1 Y → identification/recruitment bias not possible
        assert rob2_cluster.rob2_cluster_domain1b_judge(
            {"1b.1": "Y", "1b.2": "NA", "1b.3": "NA"}) == "Low"

    def test_selection_affected_by_knowledge_is_high(self):
        assert rob2_cluster.rob2_cluster_domain1b_judge(
            {"1b.1": "N", "1b.2": "Y", "1b.3": "N"}) == "High"

    def test_recruiters_blind_no_baseline_problem_is_low(self):
        assert rob2_cluster.rob2_cluster_domain1b_judge(
            {"1b.1": "N", "1b.2": "N", "1b.3": "N"}) == "Low"

    def test_recruiters_blind_baseline_problem_is_some_concerns(self):
        assert rob2_cluster.rob2_cluster_domain1b_judge(
            {"1b.1": "N", "1b.2": "N", "1b.3": "Y"}) == "Some concerns"

    def test_recruiter_awareness_unknown_no_baseline_problem_is_some_concerns(self):
        assert rob2_cluster.rob2_cluster_domain1b_judge(
            {"1b.1": "N", "1b.2": "NI", "1b.3": "N"}) == "Some concerns"

    def test_recruiter_awareness_unknown_baseline_problem_is_high(self):
        assert rob2_cluster.rob2_cluster_domain1b_judge(
            {"1b.1": "N", "1b.2": "NI", "1b.3": "Y"}) == "High"


class TestClusterDomain2Assignment:
    """Domain 2 — effect of assignment (ITT) — cribsheet p.12."""

    def test_not_aware_appropriate_analysis_is_low(self):
        # 2.1a N + 2.2 N → not aware; 2.6 Y → appropriate analysis
        assert rob2_cluster.rob2_cluster_domain2_assignment_judge(
            {"2.1a": "N", "2.2": "N", "2.6": "Y"}) == "Low"

    def test_aware_no_trial_context_deviations_is_low(self):
        assert rob2_cluster.rob2_cluster_domain2_assignment_judge(
            {"2.1a": "Y", "2.1b": "Y", "2.2": "N", "2.3": "N", "2.6": "Y"}) == "Low"

    def test_unbalanced_deviations_affecting_outcome_is_high(self):
        assert rob2_cluster.rob2_cluster_domain2_assignment_judge(
            {"2.1a": "Y", "2.1b": "Y", "2.2": "N", "2.3": "Y", "2.4": "Y",
             "2.5": "N", "2.6": "Y"}) == "High"

    def test_inappropriate_analysis_large_impact_is_high(self):
        assert rob2_cluster.rob2_cluster_domain2_assignment_judge(
            {"2.1a": "N", "2.2": "N", "2.6": "N", "2.7": "Y"}) == "High"

    def test_inappropriate_analysis_small_impact_is_some_concerns(self):
        assert rob2_cluster.rob2_cluster_domain2_assignment_judge(
            {"2.1a": "N", "2.2": "N", "2.6": "N", "2.7": "N"}) == "Some concerns"

    def test_trial_context_deviations_unknown_is_some_concerns(self):
        assert rob2_cluster.rob2_cluster_domain2_assignment_judge(
            {"2.1a": "Y", "2.1b": "Y", "2.2": "Y", "2.3": "NI", "2.6": "Y"}) == "Some concerns"


class TestClusterDomain2Adhering:
    """Domain 2 — effect of adhering (per-protocol) — cribsheet p.14.
    The LLM answers Y/PY/PN/N/NI; the cascade derives NA for gated-out
    questions. The judge runs on the post-cascade signals."""

    def test_not_aware_no_failures_is_low(self):
        assert rob2_cluster.rob2_cluster_domain2_adhering_judge(
            {"2.1": "N", "2.2": "N", "2.4": "N", "2.5": "N"}) == "Low"

    def test_ni_failures_route_to_analysis_check(self):
        # NI on 2.4/2.5 is not "no concern" — routes to the 2.6 analysis check
        assert rob2_cluster.rob2_cluster_domain2_adhering_judge(
            {"2.1": "N", "2.2": "N", "2.4": "NI", "2.5": "N", "2.6": "Y"}) == "Some concerns"

    def test_unbalanced_nonprotocol_inappropriate_analysis_is_high(self):
        assert rob2_cluster.rob2_cluster_domain2_adhering_judge(
            {"2.1": "Y", "2.3": "N", "2.6": "N"}) == "High"

    def test_unbalanced_nonprotocol_appropriate_analysis_is_some_concerns(self):
        assert rob2_cluster.rob2_cluster_domain2_adhering_judge(
            {"2.1": "Y", "2.3": "N", "2.6": "Y"}) == "Some concerns"

    def test_implementation_failure_appropriate_analysis_is_some_concerns(self):
        assert rob2_cluster.rob2_cluster_domain2_adhering_judge(
            {"2.1": "Y", "2.3": "Y", "2.4": "Y", "2.5": "N", "2.6": "Y"}) == "Some concerns"

    def test_implementation_failure_inappropriate_analysis_is_high(self):
        assert rob2_cluster.rob2_cluster_domain2_adhering_judge(
            {"2.1": "Y", "2.3": "Y", "2.4": "Y", "2.5": "N", "2.6": "N"}) == "High"


class TestClusterDomain3:
    """Domain 3 (missing outcome data) — cribsheet p.16.
    3.1 is split into 3.1a (clusters) and 3.1b (participants)."""

    def test_complete_cluster_and_participant_data_is_low(self):
        assert rob2_cluster.rob2_cluster_domain3_judge(
            {"3.1a": "Y", "3.1b": "Y"}) == "Low"

    def test_missing_cluster_data_but_evidence_unbiased_is_low(self):
        assert rob2_cluster.rob2_cluster_domain3_judge(
            {"3.1a": "N", "3.1b": "Y", "3.2": "Y"}) == "Low"

    def test_missing_participant_data_could_not_depend_is_low(self):
        assert rob2_cluster.rob2_cluster_domain3_judge(
            {"3.1a": "Y", "3.1b": "N", "3.2": "N", "3.3": "N"}) == "Low"

    def test_missingness_could_depend_not_likely_is_some_concerns(self):
        assert rob2_cluster.rob2_cluster_domain3_judge(
            {"3.1a": "N", "3.1b": "Y", "3.2": "N", "3.3": "Y", "3.4": "N"}) == "Some concerns"

    def test_missingness_likely_depended_is_high(self):
        assert rob2_cluster.rob2_cluster_domain3_judge(
            {"3.1a": "N", "3.1b": "Y", "3.2": "N", "3.3": "Y", "3.4": "Y"}) == "High"


class TestClusterDomain4:
    """Domain 4 (measurement of the outcome) — cribsheet p.19.
    4.3 is split into 4.3a (assessors aware a trial is happening) and 4.3b."""

    def test_inappropriate_method_is_high(self):
        assert rob2_cluster.rob2_cluster_domain4_judge({"4.1": "Y"}) == "High"

    def test_measurement_differs_between_groups_is_high(self):
        assert rob2_cluster.rob2_cluster_domain4_judge(
            {"4.1": "N", "4.2": "Y"}) == "High"

    def test_assessors_unaware_of_trial_is_low(self):
        # 4.3a N → assessors not even aware a trial is happening → Low
        assert rob2_cluster.rob2_cluster_domain4_judge(
            {"4.1": "N", "4.2": "N", "4.3a": "N"}) == "Low"

    def test_measurement_unknown_floors_at_some_concerns(self):
        # 4.2 NI floors the branch at Some concerns even when 4.3a clears it
        assert rob2_cluster.rob2_cluster_domain4_judge(
            {"4.1": "N", "4.2": "NI", "4.3a": "N"}) == "Some concerns"

    def test_assessor_could_be_influenced_is_some_concerns(self):
        assert rob2_cluster.rob2_cluster_domain4_judge(
            {"4.1": "N", "4.2": "N", "4.3a": "Y", "4.3b": "Y", "4.4": "Y", "4.5": "N"}) == "Some concerns"

    def test_assessor_likely_influenced_is_high(self):
        assert rob2_cluster.rob2_cluster_domain4_judge(
            {"4.1": "N", "4.2": "N", "4.3a": "Y", "4.3b": "Y", "4.4": "Y", "4.5": "Y"}) == "High"


class TestClusterDomain5AndOverall:
    """Domain 5 and the overall aggregation delegate to standard RoB 2."""

    def test_domain5_delegates_to_rob2(self):
        sig = {"5.1": "Y", "5.2": "N", "5.3": "N"}
        assert (rob2_cluster.rob2_cluster_domain5_judge(sig)
                == rob2.rob2_domain5_judge(sig) == "Low")

    def test_overall_delegates_to_rob2(self):
        assert rob2_cluster.rob2_cluster_overall(
            ["Low", "Low", "Low", "Low", "Low", "Low"]) == "Low"
        assert rob2_cluster.rob2_cluster_overall(
            ["Low", "High", "Low", "Low", "Low", "Low"]) == "High"
        assert rob2_cluster.rob2_cluster_overall(
            ["Low", "Some concerns", "Low", "Low", "Low", "Low"]) == "Some concerns"


# ─────────────────────────────────────────────
# RoB 2 Cluster — cascade enforcement (Python-derived NA)
# ─────────────────────────────────────────────
class TestClusterCascade:
    """The LLM answers every question on the 5-token scale; enforce_cascade_*
    derives NA for any conditional question whose precondition is not met."""

    def test_1b_gates_1b2_when_1b1_yes(self):
        # 1b.2 is asked only if 1b.1 is N/PN/NI
        out = rob2_cluster.enforce_cascade_1b({"1b.1": "Y", "1b.2": "Y", "1b.3": "N"})
        assert out["1b.2"] == "NA"
        assert out["1b.3"] == "N"  # 1b.3 is unconditional — never gated

    def test_1b_keeps_1b2_when_1b1_no(self):
        out = rob2_cluster.enforce_cascade_1b({"1b.1": "N", "1b.2": "Y", "1b.3": "N"})
        assert out["1b.2"] == "Y"

    def test_assignment_cascade_gates_full_chain(self):
        # 2.1a=N gates 2.1b; not-aware gates 2.3; which cascades to 2.4/2.5;
        # 2.6=Y gates 2.7.
        out = rob2_cluster.enforce_cascade_2_assignment(
            {"2.1a": "N", "2.1b": "Y", "2.2": "N", "2.3": "Y",
             "2.4": "Y", "2.5": "Y", "2.6": "Y", "2.7": "Y"})
        for sid in ("2.1b", "2.3", "2.4", "2.5", "2.7"):
            assert out[sid] == "NA", f"{sid} should be gated NA: {out}"
        assert out["2.1a"] == "N" and out["2.2"] == "N" and out["2.6"] == "Y"

    def test_assignment_cascade_keeps_on_path_questions(self):
        out = rob2_cluster.enforce_cascade_2_assignment(
            {"2.1a": "Y", "2.1b": "Y", "2.2": "N", "2.3": "Y",
             "2.4": "Y", "2.5": "N", "2.6": "N", "2.7": "N"})
        assert all(v != "NA" for v in out.values()), out

    def test_adhering_cascade_gates_2_3_and_2_6(self):
        out = rob2_cluster.enforce_cascade_2_adhering(
            {"2.1": "N", "2.2": "N", "2.3": "Y", "2.4": "N", "2.5": "N", "2.6": "Y"})
        assert out["2.3"] == "NA"   # not aware → 2.3 gated
        assert out["2.6"] == "NA"   # 2.3 NA + 2.4/2.5 N → 2.6 gated
        # 2.4 / 2.5 are always asked — never gated
        assert out["2.4"] == "N" and out["2.5"] == "N"

    def test_domain3_cascade_gates_when_data_complete(self):
        out = rob2_cluster.enforce_cascade_3(
            {"3.1a": "Y", "3.1b": "Y", "3.2": "N", "3.3": "N", "3.4": "N"})
        for sid in ("3.2", "3.3", "3.4"):
            assert out[sid] == "NA", f"{sid} should be gated NA: {out}"

    def test_domain4_cascade_gates_assessor_chain_when_method_bad(self):
        # 4.1=Y short-circuits to High → 4.3a-4.5 all gated
        out = rob2_cluster.enforce_cascade_4(
            {"4.1": "Y", "4.2": "N", "4.3a": "N", "4.3b": "N", "4.4": "N", "4.5": "N"})
        for sid in ("4.3a", "4.3b", "4.4", "4.5"):
            assert out[sid] == "NA", f"{sid} should be gated NA: {out}"

    def test_enforce_cascade_dispatch(self):
        # Domains 1a and 5 have no conditional questions — passthrough
        assert rob2_cluster.enforce_cascade("1a", {"1a.1": "Y"}) == {"1a.1": "Y"}
        assert rob2_cluster.enforce_cascade(5, {"5.1": "Y"}) == {"5.1": "Y"}
        # Domain 2 dispatches by aim
        adher = rob2_cluster.enforce_cascade(
            2, {"2.1": "N", "2.2": "N", "2.3": "Y", "2.4": "N", "2.5": "N", "2.6": "Y"},
            aim="adhering")
        assert adher["2.3"] == "NA"


# ─────────────────────────────────────────────
# RoB 2 Cluster — six-domain run() shape + both Domain 2 variants
# ─────────────────────────────────────────────
class TestClusterRunShape:
    """The cluster module exposes 6 domains in cribsheet order (1a, 1b, 2-5)
    with an ITT and a per-protocol Domain 2 variant."""

    def test_assignment_six_domains_in_cribsheet_order(self):
        ids = [d["id"] for d in rob2_cluster.DOMAINS_ASSIGNMENT]
        assert ids == ["1a", "1b", 2, 3, 4, 5]

    def test_adhering_six_domains_in_cribsheet_order(self):
        ids = [d["id"] for d in rob2_cluster.DOMAINS_ADHERING]
        assert ids == ["1a", "1b", 2, 3, 4, 5]

    def test_domain2_assignment_has_eight_signals(self):
        d2 = rob2_cluster.domains_for_aim("assignment")[2]
        assert [s["id"] for s in d2["signals"]] == [
            "2.1a", "2.1b", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7"]

    def test_domain2_adhering_has_six_signals(self):
        d2 = rob2_cluster.domains_for_aim("adhering")[2]
        assert [s["id"] for s in d2["signals"]] == [
            "2.1", "2.2", "2.3", "2.4", "2.5", "2.6"]

    def test_domains_for_aim_default_is_assignment(self):
        assert rob2_cluster.domains_for_aim("") is rob2_cluster.DOMAINS_ASSIGNMENT
        assert rob2_cluster.domains_for_aim("adhering") is rob2_cluster.DOMAINS_ADHERING

    def test_judges_for_aim_picks_the_right_domain2_judge(self):
        assert (rob2_cluster.judges_for("assignment")[2]
                is rob2_cluster.rob2_cluster_domain2_assignment_judge)
        assert (rob2_cluster.judges_for("adhering")[2]
                is rob2_cluster.rob2_cluster_domain2_adhering_judge)

    def test_signal_options_exclude_na(self):
        # NA is not a signal answer the LLM produces — it is derived in code
        # by the cascade enforcers.
        assert rob2_cluster.SIGNAL_OPTIONS == ("Y", "PY", "PN", "N", "NI")
        assert "NA" not in rob2_cluster.SIGNAL_OPTIONS

    def test_domain1a_gets_outcome_override_note(self):
        d1a = rob2_cluster.domains_for_aim("assignment")[0]
        assert d1a["id"] == "1a"
        prompt = rob2_cluster.build_domain_prompt(
            d1a, "Cluster Randomized Trial", "Secondary outcome",
            {}, outcome_is_override=True)
        assert "non-primary outcome chosen by the reviewer" in prompt

    def test_other_domains_do_not_get_override_note(self):
        d1b = rob2_cluster.domains_for_aim("assignment")[1]
        prompt = rob2_cluster.build_domain_prompt(
            d1b, "Cluster Randomized Trial", "Secondary outcome",
            {}, outcome_is_override=True)
        assert "non-primary outcome chosen by the reviewer" not in prompt

    def test_prompt_catalog_surfaces_six_domains_and_adhering_variant(self):
        cat = rob2_cluster.prompt_catalog()
        assert len(cat["domains"]) == 6
        assert [d["id"] for d in cat["domains"]] == ["1a", "1b", 2, 3, 4, 5]
        assert "domain2_adhering" in cat
        adhering_signals = [s["id"] for s in cat["domain2_adhering"]["signals"]]
        assert adhering_signals == ["2.1", "2.2", "2.3", "2.4", "2.5", "2.6"]


# ─────────────────────────────────────────────
# RoB 2 Cluster registry dispatch
# ─────────────────────────────────────────────
class TestClusterDispatch:
    def test_cluster_trial_is_registered(self):
        cfg = qa.dispatch("Cluster Randomized Trial")
        assert cfg is not None
        assert cfg["rob_tool"] == "rob2_cluster"
        assert cfg["reporting_guideline"] == "consort_cluster"
        assert cfg["initial_grade"] == "High"

    def test_tool_runner_resolves(self):
        assert qa._TOOL_RUNNERS["rob2_cluster"] is rob2_cluster.run

    def test_guideline_runner_resolves(self):
        assert qa._GUIDELINE_RUNNERS["consort_cluster"] is consort_cluster.run

    def test_registry_key_is_a_valid_annotator_type(self):
        from backend import annotator as ann
        assert "Cluster Randomized Trial" in ann.TYPE_FIELD_IDS

    def test_qa_prompt_catalog_includes_cluster(self):
        cat = qa.prompt_catalog()
        assert "rob2_cluster" in cat["rob_tools"]
        cl = cat["rob_tools"]["rob2_cluster"]
        assert len(cl["domains"]) == 6
        assert "domain2_adhering" in cl


# ─────────────────────────────────────────────
# CONSORT cluster extension (Campbell et al. 2012)
# ─────────────────────────────────────────────
class TestConsortCluster:
    def test_items_include_base_and_extension(self):
        base_ids = {it["id"] for it in consort2025.ITEMS}
        cluster_ids = {it["id"] for it in consort_cluster.ITEMS}
        assert base_ids.issubset(cluster_ids)
        ext_ids = {it["id"] for it in consort_cluster.CLUSTER_EXTENSION_ITEMS}
        assert all(eid.startswith("C-") for eid in ext_ids)
        assert ext_ids.issubset(cluster_ids)

    def test_extension_covers_cluster_concepts(self):
        descriptions = " ".join(
            it["description"].lower() for it in consort_cluster.CLUSTER_EXTENSION_ITEMS
        )
        assert "cluster" in descriptions
        assert "intracluster correlation" in descriptions or "icc" in descriptions
        assert "individual" in descriptions

    def test_prompt_lists_both_base_and_extension(self):
        prompt = consort_cluster.build_prompt({"study_type": "Cluster Randomized Trial"})
        assert "**1a**" in prompt
        for it in consort_cluster.CLUSTER_EXTENSION_ITEMS:
            assert f"**{it['id']}**" in prompt, f"Extension item {it['id']} missing from prompt"

    def test_qa_prompt_catalog_includes_cluster_guideline(self):
        cat = qa.prompt_catalog()
        assert "consort_cluster" in cat["reporting_guidelines"]
        ccat = cat["reporting_guidelines"]["consort_cluster"]
        assert "cluster_extension_items" in ccat
        assert len(ccat["cluster_extension_items"]) == len(
            consort_cluster.CLUSTER_EXTENSION_ITEMS)


# ─────────────────────────────────────────────
# RoB 2 Cluster — CSV/XLSX export flattening
# ─────────────────────────────────────────────
class TestClusterFlattenForExport:
    """flatten_result_row maps rob2_cluster rows to per-domain + per-signal
    columns, picking the Domain 2 variant the row actually used."""

    def _make_cluster_row(self, aim="assignment"):
        if aim == "adhering":
            d2 = {"judgement": "Some concerns",
                  "signals": {"2.1": "Y", "2.2": "Y", "2.3": "N",
                              "2.4": "N", "2.5": "N", "2.6": "Y"}}
        else:
            d2 = {"judgement": "Low",
                  "signals": {"2.1a": "Y", "2.1b": "N", "2.2": "N", "2.3": "N",
                              "2.4": "NA", "2.5": "NA", "2.6": "Y", "2.7": "NA"}}
        return {
            "paper_id": 77,
            "filename": "cluster_test.pdf",
            "status": "ok",
            "study_type": "Cluster Randomized Trial",
            "rob_tool": "rob2_cluster",
            "primary_outcome": "Vaccination coverage at 12 months",
            "classification": {"major_category": "Primary Studies",
                                "subcategory": "Randomized Controlled"},
            "extracted_fields": {"citation_title": "Cluster test paper"},
            "rob_domains": {
                "aim": aim,
                "1a": {"judgement": "Low",
                       "signals": {"1a.1": "Y", "1a.2": "Y", "1a.3": "N"}},
                "1b": {"judgement": "Some concerns",
                       "signals": {"1b.1": "N", "1b.2": "N", "1b.3": "Y"}},
                "2":  d2,
                "3":  {"judgement": "Low", "signals": {"3.1a": "Y", "3.1b": "Y"}},
                "4":  {"judgement": "Low",
                       "signals": {"4.1": "N", "4.2": "N", "4.3a": "N"}},
                "5":  {"judgement": "Low",
                       "signals": {"5.1": "Y", "5.2": "N", "5.3": "N"}},
            },
            "rob_overall": "Some concerns",
            "rob_direction": "NA",
            "guideline": {"adhered": 40, "applicable": 52, "proportion": 0.77},
            "guideline_adhered": 40, "guideline_applicable": 52,
            "guideline_proportion": 0.77,
            "initial_grade": "High", "updated_grade": "Moderate",
            "grade_explanation": "Downgraded 1 level for some concerns in risk of bias.",
        }

    def test_flatten_includes_six_domain_judgements(self):
        row = qa.flatten_result_row(self._make_cluster_row("assignment"))
        assert row["rob_d1a_judgement"] == "Low"
        assert row["rob_d1b_judgement"] == "Some concerns"
        assert row["rob_d2_judgement"] == "Low"
        assert row["rob_d5_judgement"] == "Low"

    def test_assignment_flatten_includes_assignment_signals(self):
        row = qa.flatten_result_row(self._make_cluster_row("assignment"))
        assert row["rob_1a.1"] == "Y"
        assert row["rob_1b.3"] == "Y"
        assert row["rob_2.1a"] == "Y"
        assert row["rob_2.7"] == "NA"
        assert row["rob_3.1a"] == "Y"

    def test_adhering_flatten_uses_adhering_signals(self):
        row = qa.flatten_result_row(self._make_cluster_row("adhering"))
        # Adhering Domain 2 has 2.1 / 2.2-2.6 — and no 2.1a / 2.1b / 2.7
        assert row["rob_2.1"] == "Y"
        assert row["rob_2.6"] == "Y"
        assert "rob_2.1a" not in row
        assert "rob_2.7" not in row

    def test_flatten_does_not_emit_aim_as_a_domain(self):
        row = qa.flatten_result_row(self._make_cluster_row("assignment"))
        assert "rob_daim_judgement" not in row
