"""Tests for the GRADE body-of-evidence certainty agent (backend/evidence_synthesis/grade.py).

Two layers:

* **Engine unit tests** — call ``grade_body`` with hand-built ``pool_outcome``-shaped
  result dicts (ported from the driscoll ``test_synthesis_stats.py`` GRADE suite,
  rewired to the new pooling contract: natural-scale ``estimate``/``ci_lower``/
  ``ci_upper``, ``heterogeneity.i2``/``q_p``, ``totals``).
* **End-to-end tests** — run a real ``pool_outcome(...)`` → ``grade_body(...)`` so the
  new-contract adaptation is exercised against genuine pooled numbers.

Pure — no LLM, no DB.
"""

import math

import pytest

from backend.evidence_synthesis import pool_outcome
from backend.evidence_synthesis.grade import (
    GRADE_LEVELS,
    absolute_effects,
    grade_body,
    sof_row,
)


def _result(*, measure, k, estimate, ci_lower, ci_upper, i2=5.0, q_p=0.8,
            n_int=1000.0, n_ctrl=1000.0, events_int=None, events_ctrl=None,
            egger=None, trim_fill=None, studies=None, design_class=None):
    """Build a minimal pool_outcome-shaped result dict for the engine."""
    return {
        "measure": measure,
        "k": k,
        "design_class": design_class,
        "pooled": {"estimate": estimate, "ci_lower": ci_lower, "ci_upper": ci_upper},
        "heterogeneity": {"i2": i2, "q_p": q_p, "tau2": 0.0, "prediction_interval": None},
        "publication_bias": {"egger": egger, "trim_fill": trim_fill},
        "studies": studies or [],
        "totals": {"n_int": n_int, "n_ctrl": n_ctrl,
                   "events_int": events_int, "events_ctrl": events_ctrl},
    }


# ---------------------------------------------------------------------------
# Downgrades
# ---------------------------------------------------------------------------

class TestGradeDowngrade:
    def test_no_downgrade(self):
        r = _result(measure="SMD", k=3, estimate=0.4, ci_lower=0.2, ci_upper=0.6,
                    i2=10.0, q_p=0.5, n_int=1000, n_ctrl=1000)
        g = grade_body(r, initial="High", per_study_rob=["Low", "Low", "Low"])
        assert g["final"] == "High"
        assert g["total_downgrade"] == 0

    def test_downgrades_stack_to_very_low(self):
        # RoB(2) + inconsistency(1) + imprecision(crosses null) -> capped at Very low
        r = _result(measure="SMD", k=3, estimate=0.25, ci_lower=-0.1, ci_upper=0.6,
                    i2=80.0, q_p=0.001, n_int=60, n_ctrl=60,
                    egger={"p": 0.02, "adequate_power": True})
        g = grade_body(r, initial="High",
                       per_study_rob=["High", "High", "Some concerns"])
        assert g["final"] == "Very low"
        assert g["total_downgrade"] >= 3

    def test_grade_cap_cannot_go_below_very_low(self):
        r = _result(measure="SMD", k=2, estimate=0.0, ci_lower=-1.0, ci_upper=1.0,
                    i2=90.0, q_p=0.0001, n_int=20, n_ctrl=20)
        g = grade_body(r, initial="Low", per_study_rob=["Critical", "Critical"])
        assert g["final"] == "Very low"

    def test_single_study_no_inconsistency(self):
        r = _result(measure="RR", k=1, estimate=0.8, ci_lower=0.6, ci_upper=0.95,
                    i2=0.0, q_p=None, n_int=800, n_ctrl=800,
                    events_int=200, events_ctrl=260)
        g = grade_body(r, initial="High", per_study_rob=["Low"])
        inc = next(d for d in g["domains"] if d["domain"] == "Inconsistency")
        assert inc["downgrade"] == 0
        assert "single study" in inc["reason"].lower()

    def test_rob_weighted_not_averaged(self):
        # Most weight in a low-RoB study -> no downgrade despite a high-RoB member.
        studies = [{"study_id": "big", "weight_pct": 90.0},
                   {"study_id": "small", "weight_pct": 10.0}]
        r = _result(measure="RR", k=2, estimate=0.7, ci_lower=0.55, ci_upper=0.9,
                    n_int=1500, n_ctrl=1500, events_int=400, events_ctrl=560,
                    studies=studies)
        g = grade_body(r, per_study_rob=["Low", "High"], initial="High")
        rob = next(d for d in g["domains"] if d["domain"] == "Risk of bias")
        assert rob["downgrade"] == 0

    def test_publication_bias_not_assessed_below_10(self):
        r = _result(measure="RR", k=4, estimate=0.7, ci_lower=0.5, ci_upper=0.95,
                    n_int=2000, n_ctrl=2000, events_int=400, events_ctrl=560,
                    egger={"p": 0.01, "adequate_power": False})
        g = grade_body(r, initial="High", per_study_rob=["Low"] * 4)
        pub = next(d for d in g["domains"] if d["domain"] == "Publication bias")
        assert pub["downgrade"] == 0


# ---------------------------------------------------------------------------
# Upgrades (non-randomized evidence only)
# ---------------------------------------------------------------------------

class TestGradeUpgrade:
    def _clean_binary(self, estimate, ci_lower, ci_upper, total_events=800):
        return _result(measure="RR", k=3, estimate=estimate, ci_lower=ci_lower,
                       ci_upper=ci_upper, i2=5.0, q_p=0.8, n_int=2500, n_ctrl=2500,
                       events_int=total_events, events_ctrl=total_events)

    def test_large_effect_upgrades_observational(self):
        r = self._clean_binary(3.0, 2.0, 4.5)
        g = grade_body(r, initial="Low", per_study_rob=["Low", "Low", "Low"])
        assert g["total_downgrade"] == 0
        assert g["total_upgrade"] == 1
        assert g["final"] == "Moderate"

    def test_very_large_effect_two_levels(self):
        r = self._clean_binary(6.0, 2.5, 14.0)
        g = grade_body(r, initial="Low", per_study_rob=["Low", "Low"])
        assert g["total_upgrade"] == 2
        assert g["final"] == "High"

    def test_dose_response_manual_upgrade(self):
        r = self._clean_binary(1.4, 1.1, 1.8)
        g = grade_body(r, initial="Low", per_study_rob=["Low", "Low", "Low"],
                       dose_response=True)
        assert g["total_upgrade"] == 1
        assert g["final"] == "Moderate"

    def test_no_upgrade_when_downgraded(self):
        # Serious imprecision (crosses null + below OIS) blocks rating-up.
        r = _result(measure="RR", k=2, estimate=3.0, ci_lower=0.9, ci_upper=10.0,
                    i2=5.0, q_p=0.8, n_int=40, n_ctrl=40, events_int=30, events_ctrl=10)
        g = grade_body(r, initial="Low", per_study_rob=["Low", "Low"])
        assert g["total_downgrade"] >= 1
        assert g["total_upgrade"] == 0

    def test_rct_never_upgraded(self):
        r = self._clean_binary(6.0, 3.0, 12.0)
        g = grade_body(r, initial="High", per_study_rob=["Low", "Low"])
        assert g["total_upgrade"] == 0
        assert g["final"] == "High"

    def test_override_pins_a_domain(self):
        r = self._clean_binary(1.2, 1.05, 1.4)
        g = grade_body(r, initial="High", per_study_rob=["Low", "Low"],
                       overrides={"indirectness": 2})
        ind = next(d for d in g["domains"] if d["domain"] == "Indirectness")
        assert ind["downgrade"] == 2
        assert g["final"] == "Low"  # High − 2


# ---------------------------------------------------------------------------
# Indirectness (hybrid — engine consumes the integer)
# ---------------------------------------------------------------------------

class TestIndirectness:
    def test_reviewer_level_applied(self):
        r = _result(measure="RR", k=3, estimate=0.7, ci_lower=0.55, ci_upper=0.9,
                    n_int=2000, n_ctrl=2000, events_int=400, events_ctrl=560)
        g = grade_body(r, initial="High", per_study_rob=["Low"] * 3,
                       indirectness_levels=1, indirectness_reason="surrogate outcome")
        ind = next(d for d in g["domains"] if d["domain"] == "Indirectness")
        assert ind["downgrade"] == 1
        assert "surrogate" in ind["reason"].lower()
        assert g["final"] == "Moderate"

    def test_none_defaults_to_zero(self):
        r = _result(measure="RR", k=3, estimate=0.7, ci_lower=0.55, ci_upper=0.9,
                    n_int=2000, n_ctrl=2000, events_int=400, events_ctrl=560)
        g = grade_body(r, initial="High", per_study_rob=["Low"] * 3,
                       indirectness_levels=None)
        ind = next(d for d in g["domains"] if d["domain"] == "Indirectness")
        assert ind["downgrade"] == 0


# ---------------------------------------------------------------------------
# Absolute effects
# ---------------------------------------------------------------------------

class TestAbsoluteEffects:
    def test_rr_absolute_effects_per_1000(self):
        ae = absolute_effects("RR", 0.5, 0.35, 0.7, baseline_per_1000=200.0)
        assert ae["intervention_per_1000"] == 100.0      # 200 × 0.5
        assert ae["risk_difference_per_1000"] == -100.0
        assert ae["nnt"] == 10
        assert ae["favours"] == "intervention"

    def test_or_absolute_effects_via_odds(self):
        ae = absolute_effects("OR", 2.0, 1.5, 2.6, baseline_per_1000=100.0)
        # odds = 0.1/0.9 × 2 = 0.2222 -> risk = 0.1818 -> 181.8 per 1000
        assert ae["intervention_per_1000"] == pytest.approx(181.8, abs=0.5)
        assert ae["favours"] == "comparator"

    def test_none_for_continuous_or_missing_baseline(self):
        assert absolute_effects("SMD", 0.3, 0.1, 0.5, 200.0) is None
        assert absolute_effects("RR", 0.5, 0.3, 0.7, None) is None


# ---------------------------------------------------------------------------
# Design -> starting certainty
# ---------------------------------------------------------------------------

class TestStartingCertainty:
    def test_rct_high(self):
        r = _result(measure="RR", k=2, estimate=0.7, ci_lower=0.5, ci_upper=0.95,
                    n_int=2000, n_ctrl=2000, events_int=400, events_ctrl=560,
                    design_class="rct")
        assert grade_body(r, per_study_rob=["Low", "Low"])["initial"] == "High"

    def test_nrs_low(self):
        r = _result(measure="RR", k=2, estimate=0.7, ci_lower=0.5, ci_upper=0.95,
                    n_int=2000, n_ctrl=2000, events_int=400, events_ctrl=560,
                    design_class="nrs")
        assert grade_body(r, per_study_rob=["Low", "Low"])["initial"] == "Low"

    def test_single_arm_very_low(self):
        studies = [{"study_id": "A", "design": "Single-Arm Trial", "weight_pct": 100.0}]
        r = _result(measure="RR", k=1, estimate=0.7, ci_lower=0.5, ci_upper=0.95,
                    n_int=100, n_ctrl=0, events_int=30, events_ctrl=0, studies=studies)
        assert grade_body(r, per_study_rob=["Low"])["initial"] == "Very low"


# ---------------------------------------------------------------------------
# End-to-end: real pool_outcome -> grade_body
# ---------------------------------------------------------------------------

class TestEndToEnd:
    RCT_STUDIES = [
        {"study_id": "A", "design": "RCT", "events_int": 50, "n_int": 500, "events_ctrl": 100, "n_ctrl": 500},
        {"study_id": "B", "design": "RCT", "events_int": 40, "n_int": 400, "events_ctrl": 80, "n_ctrl": 400},
        {"study_id": "C", "design": "RCT", "events_int": 45, "n_int": 450, "events_ctrl": 90, "n_ctrl": 450},
    ]

    def test_rct_rr_half_absolute_effects(self):
        r = pool_outcome(self.RCT_STUDIES, "RR")
        assert r["pooled"]["estimate"] == pytest.approx(0.5, abs=0.02)
        g = grade_body(r, per_study_rob=["Low", "Low", "Low"], baseline_risk_per_1000=200)
        assert g["initial"] == "High"
        assert g["final"] == "High"
        ae = g["absolute_effects"]
        assert ae["intervention_per_1000"] == pytest.approx(100.0, abs=2.0)
        assert ae["nnt"] == pytest.approx(10, abs=1)

    def test_end_to_end_high_heterogeneity_downgrades(self):
        het_studies = [
            {"study_id": "A", "design": "RCT", "events_int": 10, "n_int": 500, "events_ctrl": 100, "n_ctrl": 500},
            {"study_id": "B", "design": "RCT", "events_int": 120, "n_int": 500, "events_ctrl": 90, "n_ctrl": 500},
            {"study_id": "C", "design": "RCT", "events_int": 30, "n_int": 500, "events_ctrl": 110, "n_ctrl": 500},
        ]
        r = pool_outcome(het_studies, "RR")
        assert r["heterogeneity"]["i2"] > 75
        g = grade_body(r, per_study_rob=["Low", "Low", "Low"])
        inc = next(d for d in g["domains"] if d["domain"] == "Inconsistency")
        assert inc["downgrade"] >= 1

    def test_sof_row_shape(self):
        r = pool_outcome(self.RCT_STUDIES, "RR")
        g = grade_body(r, per_study_rob=["Low", "Low", "Low"], baseline_risk_per_1000=200)
        row = sof_row(r, g, outcome={"name": "Mortality", "timeframe": "12 months"})
        assert row["outcome"] == "Mortality"
        assert row["n_studies"] == 3
        assert row["measure"] == "RR"
        assert row["certainty"] == "High"
        assert row["relative_effect"]["estimate"] == pytest.approx(0.5, abs=0.02)
        assert row["absolute_effects"]["nnt"] == pytest.approx(10, abs=1)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_pool_result(self):
        r = {"measure": "RR", "k": 0, "pooled": {}, "heterogeneity": {},
             "publication_bias": {}, "studies": [], "totals": {}}
        g = grade_body(r, per_study_rob=[])
        assert g["final"] in GRADE_LEVELS
        assert g["absolute_effects"] is None

    def test_missing_rob_raises_by_default(self):
        # A body of real studies with no RoB labels must not be rated as though the
        # domain were clean -- that is indistinguishable from an assessed-clean body.
        r = _result(measure="RR", k=2, estimate=0.7, ci_lower=0.5, ci_upper=0.95,
                    n_int=2000, n_ctrl=2000, events_int=400, events_ctrl=560,
                    studies=[{"study_id": "a", "weight_pct": 50.0},
                             {"study_id": "b", "weight_pct": 50.0}])
        with pytest.raises(ValueError, match="no risk-of-bias judgements"):
            grade_body(r, initial="High", per_study_rob=[])

    def test_missing_rob_conservative_when_opted_out(self):
        # Explicit opt-out keeps the old behaviour: reports "no judgements", no crash.
        r = _result(measure="RR", k=2, estimate=0.7, ci_lower=0.5, ci_upper=0.95,
                    n_int=2000, n_ctrl=2000, events_int=400, events_ctrl=560,
                    studies=[{"study_id": "a", "weight_pct": 50.0},
                             {"study_id": "b", "weight_pct": 50.0}])
        g = grade_body(r, initial="High", per_study_rob=[], require_rob=False)
        assert g["final"] in GRADE_LEVELS
        rob = next(d for d in g["domains"] if d["domain"] == "Risk of bias")
        assert "no risk-of-bias judgements" in rob["reason"]

    def test_rob_read_off_study_records(self):
        # The primary channel: labels ride on studies[], paired with their weights,
        # so no per_study_rob argument is needed at all.
        r = _result(measure="RR", k=2, estimate=0.7, ci_lower=0.5, ci_upper=0.95,
                    n_int=2000, n_ctrl=2000, events_int=400, events_ctrl=560,
                    studies=[{"study_id": "a", "weight_pct": 50.0, "rob": "Critical"},
                             {"study_id": "b", "weight_pct": 50.0, "rob": "Critical"}])
        g = grade_body(r, initial="High")
        rob = next(d for d in g["domains"] if d["domain"] == "Risk of bias")
        assert rob["downgrade"] == 2

    def test_rob_on_records_weighted_like_a_positional_list(self):
        # Same weighting rule as test_rob_weighted_not_averaged, via the record channel.
        r = _result(measure="RR", k=2, estimate=0.7, ci_lower=0.55, ci_upper=0.9,
                    n_int=1500, n_ctrl=1500, events_int=400, events_ctrl=560,
                    studies=[{"study_id": "big", "weight_pct": 90.0, "rob": "Low"},
                             {"study_id": "small", "weight_pct": 10.0, "rob": "High"}])
        g = grade_body(r, initial="High")
        rob = next(d for d in g["domains"] if d["domain"] == "Risk of bias")
        assert rob["downgrade"] == 0

    def test_rob_length_mismatch_raises(self):
        # Silently falling back to equal weights produced an unweighted judgement
        # indistinguishable from a weighted one. Refuse instead.
        r = _result(measure="RR", k=2, estimate=0.7, ci_lower=0.5, ci_upper=0.95,
                    n_int=2000, n_ctrl=2000, events_int=400, events_ctrl=560,
                    studies=[{"study_id": "a", "weight_pct": 50.0},
                             {"study_id": "b", "weight_pct": 50.0}])
        with pytest.raises(ValueError, match="match the pooled studies"):
            grade_body(r, initial="High", per_study_rob=["Low"])
