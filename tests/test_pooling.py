"""Tests for the Pooling (meta-analysis) synthesis agent.

Covers the pure-Python body-of-evidence engine (no model): effect-size computation
from raw arm data + pre-computed estimates, inverse-variance fixed/random-effects
pooling, the three tau2 estimators, heterogeneity, Egger + trim-and-fill, and the
top-level ``pool_outcome`` composer + GRADE hand-off. All values are cross-checked
against hand computation or reference statistical tables.
"""

from __future__ import annotations

import math

import pytest

from backend.evidence_synthesis import pooling as pl
from backend.evidence_synthesis.pooling import (
    eggers_test,
    grade_pooling_inputs,
    pool_outcome,
    study_effect,
    trim_and_fill,
)


# ─────────────────────────────────────────────
# Special-function shims vs reference tables
# ─────────────────────────────────────────────
class TestSpecialFunctions:
    def test_chi2_survival(self):
        assert pl._chi2_sf(3.841, 1) == pytest.approx(0.05, abs=1e-3)
        assert pl._chi2_sf(9.488, 4) == pytest.approx(0.05, abs=1e-3)
        assert pl._chi2_sf(0.0, 3) == 1.0

    def test_student_t_two_sided(self):
        assert pl._student_t_sf2(2.776, 4) == pytest.approx(0.05, abs=1e-3)
        assert pl._student_t_sf2(0.0, 10) == pytest.approx(1.0, abs=1e-9)

    def test_t_quantile_matches_tables(self):
        assert pl._t_quantile(0.975, 3) == pytest.approx(3.182, abs=1e-3)
        assert pl._t_quantile(0.975, 4) == pytest.approx(2.776, abs=1e-3)
        assert pl._t_quantile(0.975, 10) == pytest.approx(2.228, abs=1e-3)

    def test_t_quantile_converges_to_normal(self):
        assert pl._t_quantile(0.975, 1e9) == pytest.approx(1.959964, abs=1e-4)


# ─────────────────────────────────────────────
# Effect-size computation
# ─────────────────────────────────────────────
class TestBinaryEffect:
    def test_odds_ratio(self):
        e = study_effect(
            {"events_int": 15, "n_int": 100, "events_ctrl": 25, "n_ctrl": 100}, "OR")
        assert math.exp(e["yi"]) == pytest.approx(0.5294, abs=1e-3)
        # vi = 1/15 + 1/85 + 1/25 + 1/75
        assert e["vi"] == pytest.approx(0.13176, abs=1e-4)

    def test_risk_ratio(self):
        e = study_effect(
            {"events_int": 15, "n_int": 100, "events_ctrl": 25, "n_ctrl": 100}, "RR")
        assert math.exp(e["yi"]) == pytest.approx(0.6, abs=1e-6)

    def test_risk_difference(self):
        e = study_effect(
            {"events_int": 15, "n_int": 100, "events_ctrl": 25, "n_ctrl": 100}, "RD")
        assert e["yi"] == pytest.approx(-0.10, abs=1e-9)

    def test_continuity_correction_on_zero_cell(self):
        e = study_effect(
            {"events_int": 0, "n_int": 50, "events_ctrl": 5, "n_ctrl": 50}, "OR")
        assert e["note"] == "continuity_correction_0.5"
        assert math.isfinite(e["yi"])

    def test_double_zero_dropped_for_rr(self):
        assert study_effect(
            {"events_int": 0, "n_int": 50, "events_ctrl": 0, "n_ctrl": 50}, "RR") is None


class TestIncidenceRateRatio:
    def test_irr_from_person_time(self):
        # IRR = (10/500)/(20/480) = 0.48; var(log IRR) = 1/10 + 1/20 = 0.15
        e = study_effect(
            {"events_int": 10, "time_int": 500, "events_ctrl": 20, "time_ctrl": 480}, "IRR")
        assert math.exp(e["yi"]) == pytest.approx(0.48, abs=1e-3)
        assert e["vi"] == pytest.approx(0.15, abs=1e-6)

    def test_irr_person_time_aliases(self):
        e = study_effect(
            {"events_int": 10, "pyears_int": 500, "events_ctrl": 20, "pyears_ctrl": 480}, "IRR")
        assert e is not None and math.exp(e["yi"]) == pytest.approx(0.48, abs=1e-3)

    def test_irr_from_counts_without_person_time_dropped(self):
        # Must NOT be approximated from a 2x2 count table (that silently yields RR).
        assert study_effect(
            {"events_int": 10, "n_int": 100, "events_ctrl": 20, "n_ctrl": 100}, "IRR") is None

    def test_irr_precomputed_still_works(self):
        e = study_effect({"estimate": 0.48, "ci_lower": 0.30, "ci_upper": 0.77}, "IRR")
        assert e["yi"] == pytest.approx(math.log(0.48), abs=1e-9)

    def test_irr_drop_warning_names_person_time(self):
        r = pool_outcome(
            [{"study_id": "A", "events_int": 10, "n_int": 100, "events_ctrl": 20, "n_ctrl": 100}],
            "IRR")
        assert r["k"] == 0
        assert any("person-time" in w for w in r["warnings"])

    def test_irr_zero_event_continuity(self):
        e = study_effect(
            {"events_int": 0, "time_int": 500, "events_ctrl": 8, "time_ctrl": 480}, "IRR")
        assert e["note"] == "continuity_correction_0.5" and math.isfinite(e["yi"])

    def test_invalid_counts_dropped(self):
        assert study_effect(
            {"events_int": 60, "n_int": 50, "events_ctrl": 5, "n_ctrl": 50}, "OR") is None


class TestContinuousEffect:
    def test_mean_difference(self):
        e = study_effect(
            {"mean_int": 10, "sd_int": 2, "n_int": 30,
             "mean_ctrl": 8, "sd_ctrl": 2.5, "n_ctrl": 30}, "MD")
        assert e["yi"] == pytest.approx(2.0, abs=1e-9)
        # vi = 4/30 + 6.25/30
        assert e["vi"] == pytest.approx(0.34167, abs=1e-4)

    def test_hedges_g_smd(self):
        e = study_effect(
            {"mean_int": 10, "sd_int": 2, "n_int": 30,
             "mean_ctrl": 8, "sd_ctrl": 2.5, "n_ctrl": 30}, "SMD")
        # sp=sqrt(5.125)=2.2638; d=0.8835; J=0.9870; g=0.872
        assert e["yi"] == pytest.approx(0.872, abs=2e-3)
        assert e["note"] == "hedges_g"

    def test_too_small_n_dropped(self):
        assert study_effect(
            {"mean_int": 10, "sd_int": 2, "n_int": 1,
             "mean_ctrl": 8, "sd_ctrl": 2.5, "n_ctrl": 30}, "MD") is None


class TestPrecomputedEffect:
    def test_yi_vi_passthrough(self):
        e = study_effect({"yi": 0.5, "vi": 0.04}, "OR")
        assert e["yi"] == 0.5 and e["vi"] == 0.04

    def test_estimate_plus_ci_log_scale(self):
        # RR = 0.5 (95% CI 0.3-0.83) -> yi = log(0.5)
        e = study_effect({"estimate": 0.5, "ci_lower": 0.3, "ci_upper": 0.833}, "RR")
        assert e["yi"] == pytest.approx(math.log(0.5), abs=1e-9)
        se = (math.log(0.833) - math.log(0.3)) / (2 * pl._Z_95)
        assert e["vi"] == pytest.approx(se * se, abs=1e-9)

    def test_estimate_plus_ci_raw_scale(self):
        e = study_effect({"estimate": 2.0, "ci_lower": 1.0, "ci_upper": 3.0}, "MD")
        assert e["yi"] == 2.0
        se = (3.0 - 1.0) / (2 * pl._Z_95)
        assert e["vi"] == pytest.approx(se * se, abs=1e-9)

    def test_negative_estimate_invalid_for_ratio(self):
        assert study_effect({"estimate": -0.5, "se": 0.1}, "RR") is None


# ─────────────────────────────────────────────
# Fixed / random pooling + tau2
# ─────────────────────────────────────────────
class TestPooling:
    STUDIES = [
        {"study_id": "A", "yi": math.log(0.5), "vi": 0.1},
        {"study_id": "B", "yi": math.log(0.8), "vi": 0.05},
        {"study_id": "C", "yi": math.log(0.6), "vi": 0.08},
    ]

    def test_fixed_effect_matches_manual(self):
        r = pool_outcome(self.STUDIES, "OR", tau2_method="DL")
        w = [1 / 0.1, 1 / 0.05, 1 / 0.08]
        y = [math.log(0.5), math.log(0.8), math.log(0.6)]
        est = math.exp(sum(wi * yi for wi, yi in zip(w, y)) / sum(w))
        assert r["fixed"]["estimate"] == pytest.approx(est, abs=1e-6)

    def test_weights_sum_to_100(self):
        r = pool_outcome(self.STUDIES, "OR", tau2_method="DL")
        assert sum(s["weight_pct"] for s in r["studies"]) == pytest.approx(100.0, abs=1e-6)

    def test_tau2_zero_when_homogeneous(self):
        # Q < df here -> tau2 clamps to 0 -> random == fixed.
        r = pool_outcome(self.STUDIES, "OR", tau2_method="DL")
        assert r["heterogeneity"]["tau2"] == 0.0
        assert r["random"]["estimate"] == pytest.approx(r["fixed"]["estimate"], abs=1e-9)

    def test_ratio_back_transformed(self):
        r = pool_outcome(self.STUDIES, "OR")
        assert r["scale"] == "log"
        assert 0 < r["pooled"]["estimate"] < 1     # protective OR, on natural scale

    def test_dropped_study_named_in_warnings(self):
        studies = self.STUDIES + [{"study_id": "BAD", "estimate": None}]
        r = pool_outcome(studies, "OR")
        assert r["k"] == 3
        assert any("BAD" in w for w in r["warnings"])

    def test_empty_input(self):
        r = pool_outcome([], "OR")
        assert r["k"] == 0 and r["pooled"] is None


class TestTau2Estimators:
    HET = ([0.1, 0.9, 0.3, -0.2, 0.6], [0.05, 0.04, 0.06, 0.05, 0.03])

    def test_dl_closed_form_positive(self):
        tau2 = pl._tau2_dersimonian_laird(*self.HET)
        assert tau2 > 0

    def test_reml_and_pm_converge_near_dl(self):
        y, v = self.HET
        dl = pl._tau2_dersimonian_laird(y, v)
        reml = pl._tau2_reml(y, v)
        pm = pl._tau2_paule_mandel(y, v)
        # Different estimators, same order of magnitude on a clearly heterogeneous set.
        assert reml > 0 and pm > 0
        assert abs(reml - dl) < 0.2 and abs(pm - dl) < 0.2

    def test_all_estimators_zero_when_homogeneous(self):
        y, v = [0.2, 0.2, 0.2], [0.05, 0.05, 0.05]
        assert pl._tau2_dersimonian_laird(y, v) == 0.0
        assert pl._tau2_reml(y, v) == 0.0
        assert pl._tau2_paule_mandel(y, v) == 0.0


# ─────────────────────────────────────────────
# Heterogeneity
# ─────────────────────────────────────────────
class TestHeterogeneity:
    def test_i2_and_q_on_heterogeneous_binary(self):
        studies = [
            {"study_id": "S1", "events_int": 12, "n_int": 100, "events_ctrl": 20, "n_ctrl": 100},
            {"study_id": "S2", "events_int": 8, "n_int": 80, "events_ctrl": 25, "n_ctrl": 90},
            {"study_id": "S3", "events_int": 30, "n_int": 120, "events_ctrl": 28, "n_ctrl": 110},
            {"study_id": "S4", "events_int": 5, "n_int": 60, "events_ctrl": 18, "n_ctrl": 65},
        ]
        h = pool_outcome(studies, "RR", tau2_method="DL")["heterogeneity"]
        assert h["q"] == pytest.approx(8.477, abs=0.05)
        assert h["i2"] == pytest.approx(64.6, abs=1.0)
        assert 0 < h["q_p"] < 0.05

    def test_prediction_interval_requires_three_studies(self):
        two = [{"yi": 0.1, "vi": 0.05}, {"yi": 0.3, "vi": 0.05}]
        assert pool_outcome(two, "MD")["heterogeneity"]["prediction_interval"] is None

    def test_prediction_interval_back_transformed_for_ratio(self):
        studies = [{"yi": v, "vi": 0.05} for v in (0.1, 0.5, 0.3, -0.2, 0.6)]
        h = pool_outcome(studies, "RR")["heterogeneity"]
        pi = h["prediction_interval"]
        assert pi is not None and pi["lower"] > 0    # exp() -> strictly positive


# ─────────────────────────────────────────────
# Publication bias
# ─────────────────────────────────────────────
class TestPublicationBias:
    def test_egger_symmetric_near_zero_intercept(self):
        y = [-0.4, -0.2, 0.0, 0.2, 0.4]
        v = [0.02, 0.06, 0.1, 0.06, 0.02]
        eg = eggers_test(y, v)
        assert abs(eg["intercept"]) < 1e-6
        assert eg["adequate_power"] is False        # k < 10

    def test_egger_none_when_no_precision_spread(self):
        # All identical variances -> regression on precision undefined.
        assert eggers_test([-0.3, 0.0, 0.3], [0.05, 0.05, 0.05]) is None

    def test_egger_power_flag_at_ten(self):
        y = [0.1 * i for i in range(10)]
        v = [0.02 + 0.01 * i for i in range(10)]
        assert eggers_test(y, v)["adequate_power"] is True

    def test_trim_fill_symmetric_imputes_none(self):
        y = [-0.4, -0.2, 0.0, 0.2, 0.4]
        v = [0.02, 0.06, 0.1, 0.06, 0.02]
        assert trim_and_fill(y, v)["n_imputed"] == 0

    def test_trim_fill_asymmetric_imputes_on_deficient_side(self):
        y = [0.1, 0.2, 0.25, 0.5, 0.7, 0.9]
        v = [0.02, 0.03, 0.05, 0.1, 0.15, 0.2]
        tf = trim_and_fill(y, v)
        assert tf["n_imputed"] >= 1
        assert tf["side"] == "left"
        # Filling the deficient left side pulls the estimate below the naive mean.
        assert tf["estimate"] < sum(y) / len(y)

    def test_trim_fill_adjusted_back_transformed_in_pool(self):
        y = [0.1, 0.2, 0.25, 0.5, 0.7, 0.9]
        v = [0.02, 0.03, 0.05, 0.1, 0.15, 0.2]
        studies = [{"yi": yi, "vi": vi} for yi, vi in zip(y, v)]
        tf = pool_outcome(studies, "OR")["publication_bias"]["trim_fill"]
        assert tf["adjusted_estimate"] == pytest.approx(math.exp(tf["estimate"]), abs=1e-9)


# ─────────────────────────────────────────────
# End-to-end + GRADE hand-off
# ─────────────────────────────────────────────
class TestEndToEnd:
    def test_binary_pool_totals(self):
        studies = [
            {"study_id": "S1", "events_int": 12, "n_int": 100, "events_ctrl": 20, "n_ctrl": 100},
            {"study_id": "S2", "events_int": 8, "n_int": 80, "events_ctrl": 25, "n_ctrl": 90},
        ]
        r = pool_outcome(studies, "RR")
        assert r["totals"]["n_int"] == 180
        assert r["totals"]["events_ctrl"] == 45

    def test_grade_inputs_shape(self):
        studies = [{"yi": v, "vi": 0.05} for v in (0.1, 0.5, 0.3, -0.2, 0.6)]
        gi = grade_pooling_inputs(pool_outcome(studies, "RR"))
        for key in ("k", "measure", "pooled_estimate", "ci_lower", "ci_upper",
                    "i2", "tau2", "q_p", "egger_p", "trim_fill_n_imputed"):
            assert key in gi
        assert gi["k"] == 5 and gi["measure"] == "RR"

    def test_hr_pooled_on_log_scale(self):
        studies = [
            {"estimate": 0.7, "ci_lower": 0.5, "ci_upper": 0.98},
            {"estimate": 0.8, "ci_lower": 0.6, "ci_upper": 1.07},
        ]
        r = pool_outcome(studies, "HR")
        assert r["scale"] == "log"
        assert 0.6 < r["pooled"]["estimate"] < 0.9

    def test_measure_synonym_canonicalized(self):
        r = pool_outcome([{"yi": 0.1, "vi": 0.05}, {"yi": 0.2, "vi": 0.05}], "hazard ratio")
        assert r["measure"] == "HR" and r["scale"] == "log"
