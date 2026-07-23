"""Unit tests for the Synthesis meta-analysis engine.

The headline validation is the JSLHR tutorial's worked example (Zhang, Cheng &
Zhang 2022, Figure 2): 22 studies pooled by random-effects REML giving
g = 0.468 (95% CI [0.038, 0.898]), I² = 75.0%, τ² = 0.8240, Q = 83.85 (df=21).
Those are metafor/meta reference numbers, so matching them validates the
pooling + heterogeneity + REML layer end-to-end. The remaining tests use
hand-computable fixtures.
"""

import math

import numpy as np
import pytest

from backend import synthesis_stats as ss


# --- JSLHR golden fixture: (TE, SE_TE) read off Figure 2 --------------------
JSLHR = [
    (0.08, 0.1513), (0.12, 0.4407), (-1.36, 0.4047), (3.63, 0.9258),
    (0.08, 0.3537), (-0.18, 0.2466), (0.29, 0.2852), (1.01, 0.5346),
    (0.26, 0.2930), (0.59, 0.3127), (1.28, 0.4663), (0.38, 0.4253),
    (4.51, 0.7014), (1.19, 1.0960), (0.65, 0.7687), (-0.20, 1.0563),
    (0.24, 0.2083), (-0.03, 0.2569), (-0.12, 0.2571), (0.14, 0.8794),
    (0.00, 0.2709), (0.46, 0.3487),
]


def _jslhr_effects():
    return [ss.EffectSize(yi=te, vi=se ** 2, measure="SMD", n=40) for te, se in JSLHR]


class TestGoldenFixture:
    def test_heterogeneity_matches_metafor(self):
        yi = [te for te, _ in JSLHR]
        vi = [se ** 2 for _, se in JSLHR]
        het = ss.heterogeneity(yi, vi)
        assert het["df"] == 21
        assert het["Q"] == pytest.approx(83.85, abs=0.6)
        assert het["I2"] == pytest.approx(75.0, abs=1.0)
        assert het["H"] == pytest.approx(2.00, abs=0.03)
        assert het["p"] < 0.0001
        assert het["tau2_REML"] == pytest.approx(0.8240, abs=0.05)

    def test_random_effects_pool_matches_metafor(self):
        res = ss.pool(_jslhr_effects(), "SMD", model="random", tau2_method="REML")
        re = res["random"]
        assert re["estimate"] == pytest.approx(0.468, abs=0.01)
        assert re["ci_low"] == pytest.approx(0.038, abs=0.02)
        assert re["ci_high"] == pytest.approx(0.898, abs=0.02)
        assert re["z"] == pytest.approx(2.13, abs=0.05)
        assert re["p"] == pytest.approx(0.0329, abs=0.005)

    def test_tau2_qprofile_ci(self):
        yi = [te for te, _ in JSLHR]
        vi = [se ** 2 for _, se in JSLHR]
        het = ss.heterogeneity(yi, vi)
        # metafor reports tau^2 CI [0.4601, 2.7124]
        assert het["tau2_ci_low"] == pytest.approx(0.4601, abs=0.15)
        assert het["tau2_ci_high"] == pytest.approx(2.7124, abs=0.6)


class TestEffectSizes:
    def test_md(self):
        e = ss.md(10, 2, 50, 8, 3, 50)
        assert e.yi == pytest.approx(2.0)
        assert e.vi == pytest.approx(4 / 50 + 9 / 50)

    def test_smd_hedges_g(self):
        e = ss.smd_hedges_g(10, 2, 50, 8, 2, 50)
        sp = math.sqrt((49 * 4 + 49 * 4) / 98)  # = 2
        d = 2.0 / sp
        j = 1 - 3 / (4 * 98 - 1)
        assert e.raw["sd_pooled"] == pytest.approx(sp)
        assert e.yi == pytest.approx(j * d, rel=1e-9)
        assert e.measure == "SMD"

    def test_log_or_and_continuity(self):
        e = ss.log_or(10, 100, 5, 100)  # a=10,b=90,c=5,d=95
        assert e.yi == pytest.approx(math.log((10 * 95) / (90 * 5)))
        assert e.vi == pytest.approx(1 / 10 + 1 / 90 + 1 / 5 + 1 / 95)
        assert not e.corrected
        z = ss.log_or(0, 100, 5, 100)  # zero cell -> corrected
        assert z.corrected

    def test_log_or_double_zero_dropped(self):
        assert ss.log_or(0, 100, 0, 100) is None

    def test_log_rr(self):
        e = ss.log_rr(10, 100, 5, 100)
        assert e.yi == pytest.approx(math.log((10 / 100) / (5 / 100)))

    def test_risk_difference(self):
        e = ss.risk_difference(10, 100, 5, 100)
        assert e.yi == pytest.approx(0.05)
        assert e.vi == pytest.approx(0.1 * 0.9 / 100 + 0.05 * 0.95 / 100)

    def test_fisher_z(self):
        e = ss.fisher_z(0.5, 30)
        assert e.yi == pytest.approx(math.atanh(0.5))
        assert e.vi == pytest.approx(1 / 27)
        assert ss.back_transform(e.yi, "ZCOR") == pytest.approx(0.5)

    def test_proportion_logit(self):
        e = ss.proportion_logit(20, 100)
        assert e.yi == pytest.approx(math.log(0.2 / 0.8))
        assert e.vi == pytest.approx(1 / (100 * 0.2) + 1 / (100 * 0.8))
        assert ss.back_transform(e.yi, "PLOGIT") == pytest.approx(0.2)

    def test_incidence_rate(self):
        e = ss.incidence_rate_log(15, 1000)
        assert e.yi == pytest.approx(math.log(15 / 1000))
        assert e.vi == pytest.approx(1 / 15)

    def test_hazard_ratio_from_ci(self):
        # HR + 95% CI -> yi = ln(HR), SE recovered from the CI width on the log scale.
        e = ss.effect_size("HR", {"hr": 0.75, "ci_lower": 0.60, "ci_upper": 0.94})
        assert e.measure == "HR"
        assert e.yi == pytest.approx(math.log(0.75))
        se = (math.log(0.94) - math.log(0.60)) / (2 * ss._z_crit())
        assert e.vi == pytest.approx(se ** 2)
        assert ss.back_transform(e.yi, "HR") == pytest.approx(0.75)

    def test_hazard_ratio_peto_oe_v(self):
        e = ss.hazard_ratio(o_e=-5.0, v=20.0)
        assert e.yi == pytest.approx(-0.25) and e.vi == pytest.approx(0.05)

    def test_hazard_ratio_loghr_se(self):
        e = ss.hazard_ratio(loghr=-0.3, se=0.1)
        assert e.yi == pytest.approx(-0.3) and e.vi == pytest.approx(0.01)

    def test_hazard_ratio_not_from_2x2(self):
        # A 2x2 count table alone yields no HR.
        assert ss.effect_size("HR", {"events1": 10, "total1": 100,
                                     "events2": 20, "total2": 100}) is None

    def test_hr_is_log_and_ratio_axis(self):
        assert ss.measure_is_log("HR") and ss.display_uses_log_axis("HR")
        assert ss.NULL_VALUE["HR"] == 1.0


class TestPooling:
    def test_fixed_inverse_variance(self):
        effects = [ss.EffectSize(0.2, 0.04, "MD"), ss.EffectSize(0.4, 0.01, "MD")]
        res = ss.inverse_variance_pool([e.yi for e in effects], [e.vi for e in effects])
        # weights 25 and 100 -> pooled = (25*0.2 + 100*0.4)/125 = 0.36
        assert res["estimate"] == pytest.approx(0.36)
        assert res["se"] == pytest.approx(math.sqrt(1 / 125))

    def test_mantel_haenszel_or(self):
        tables = [{"a": 10, "b": 90, "c": 5, "d": 95},
                  {"a": 20, "b": 80, "c": 10, "d": 90}]
        res = ss.mantel_haenszel(tables, "OR")
        assert math.exp(res["estimate"]) == pytest.approx(2.2)  # 13.75/6.25
        assert res["method"] == "MH"

    def test_single_study(self):
        res = ss.pool([ss.EffectSize(0.5, 0.1, "MD")], "MD")
        assert res["k"] == 1
        assert res["heterogeneity"]["status"] == "insufficient_studies"
        assert res["random"]["estimate"] == pytest.approx(0.5)


class TestEngineExtras:
    """Paule-Mandel tau^2 + hazard-ratio pooling."""

    HET = (np.array([0.1, 0.9, 0.3, -0.2, 0.6]), np.array([0.05, 0.04, 0.06, 0.05, 0.03]))

    def test_tau2_paule_mandel_positive(self):
        yi, vi = self.HET
        pm = ss.tau2_paule_mandel(yi, vi)
        assert pm > 0
        # PM sits in the same ballpark as DL/REML on a clearly heterogeneous set.
        assert abs(pm - ss.tau2_dersimonian_laird(yi, vi)) < 0.2

    def test_tau2_paule_mandel_zero_when_homogeneous(self):
        yi = np.array([0.2, 0.2, 0.2]); vi = np.array([0.05, 0.05, 0.05])
        assert ss.tau2_paule_mandel(yi, vi) == 0.0

    def test_heterogeneity_exposes_pm(self):
        yi, vi = self.HET
        het = ss.heterogeneity(yi, vi)
        assert "tau2_PM" in het and het["tau2_PM"] > 0

    def test_pool_selects_pm(self):
        yi, vi = self.HET
        eff = [ss.EffectSize(float(y), float(v), "MD") for y, v in zip(yi, vi)]
        res = ss.pool(eff, "MD", tau2_method="PM")
        assert res["random"]["tau2"] == pytest.approx(ss.tau2_paule_mandel(yi, vi))

    def test_pool_hr_back_transforms_to_ratio(self):
        effects = [ss.effect_size("HR", {"hr": h, "ci_lower": lo, "ci_upper": hi})
                   for h, lo, hi in [(0.75, 0.60, 0.94), (0.80, 0.65, 0.98), (0.70, 0.52, 0.95)]]
        res = ss.pool(effects, "HR", model="random")
        assert res["forest"]["log_axis"] is True
        # Every forest study renders on the ratio (display) scale.
        assert res["forest"]["studies"][0]["es"] == pytest.approx(0.75, abs=1e-6)
        assert 0.6 < ss.back_transform(res["random"]["estimate"], "HR") < 0.9


class TestPublicationBias:
    def test_eggers_runs(self):
        eff = _jslhr_effects()
        eg = ss.eggers_test(eff)
        assert eg["status"] == "ok"
        assert eg["df"] == len(eff) - 2
        assert "p" in eg

    def test_eggers_insufficient(self):
        eg = ss.eggers_test([ss.EffectSize(0.1, 0.1, "MD")])
        assert eg["status"] == "insufficient_studies"

    def test_trimfill_symmetric(self):
        # symmetric set centered at 0 -> few/no imputed
        effects = [ss.EffectSize(v, 0.1, "MD") for v in (-0.4, -0.2, 0.0, 0.2, 0.4)]
        tf = ss.trim_and_fill(effects, "MD")
        assert tf["status"] == "ok"
        assert tf["n_imputed"] <= 1

    def test_trimfill_asymmetric(self):
        # suppressed small negative studies -> should impute on the left
        effects = [ss.EffectSize(v, s, "MD") for v, s in
                   [(0.1, 0.05), (0.2, 0.05), (0.5, 0.3), (0.6, 0.35), (0.9, 0.5), (1.1, 0.55)]]
        tf = ss.trim_and_fill(effects, "MD")
        assert tf["status"] == "ok"
        assert tf["n_imputed"] >= 1


class TestSubgroupAndMetaReg:
    def test_subgroup(self):
        effects = [ss.EffectSize(0.2, 0.05, "SMD"), ss.EffectSize(0.3, 0.05, "SMD"),
                   ss.EffectSize(1.0, 0.05, "SMD"), ss.EffectSize(1.1, 0.05, "SMD")]
        groups = ["A", "A", "B", "B"]
        res = ss.subgroup_analysis(effects, groups, "SMD")
        assert res["status"] == "ok"
        assert set(res["levels"]) == {"A", "B"}
        assert res["p_between"] < 0.01  # groups clearly differ

    def test_meta_regression(self):
        import numpy as np
        # effect increases with moderator
        xs = [1, 2, 3, 4, 5, 6]
        effects = [ss.EffectSize(0.1 * x, 0.02, "SMD") for x in xs]
        res = ss.meta_regression(effects, np.array(xs), col_names=["dose"])
        assert res["status"] == "ok"
        slope = [c for c in res["coefficients"] if c["name"] == "dose"][0]
        assert slope["estimate"] == pytest.approx(0.1, abs=0.03)


class TestSensitivity:
    def test_leave_one_out(self):
        loo = ss.leave_one_out(_jslhr_effects(), "SMD")
        assert len(loo) == len(JSLHR)
        for row in loo:
            assert row["estimate"] is not None

    def test_influence(self):
        infl = ss.influence_diagnostics(_jslhr_effects())
        assert len(infl) == len(JSLHR)
        # Shehata 2013 (index 12, TE 4.51) is a known outlier
        assert infl[12]["influential"] is True


class TestGrade:
    def test_no_downgrade(self):
        het = {"status": "ok", "I2": 10.0, "p": 0.5, "tau2_REML": 0.0}
        pooled = {"ci_low": 0.2, "ci_high": 0.6}
        g = ss.grade_body_of_evidence(
            initial="High", per_study_rob=["Low", "Low", "Low"],
            weights=[1, 1, 1], heterogeneity=het, pooled=pooled,
            measure="SMD", total_n=2000)
        assert g["final"] == "High"
        assert g["total_downgrade"] == 0

    def test_downgrades_stack(self):
        het = {"status": "ok", "I2": 80.0, "p": 0.001, "tau2_REML": 0.5}
        pooled = {"ci_low": -0.1, "ci_high": 0.6}  # crosses null
        g = ss.grade_body_of_evidence(
            initial="High", per_study_rob=["High", "High", "Some concerns"],
            weights=[1, 1, 1], heterogeneity=het, pooled=pooled,
            measure="SMD", total_n=120,
            egger={"status": "ok", "p": 0.02})
        # RoB(2) + inconsistency(1) + imprecision + pubbias -> capped at Very low
        assert g["final"] == "Very low"
        assert g["total_downgrade"] >= 3

    def test_grade_cap(self):
        het = {"status": "ok", "I2": 90.0, "p": 0.0001, "tau2_REML": 1.0}
        pooled = {"ci_low": -1.0, "ci_high": 1.0}
        g = ss.grade_body_of_evidence(
            initial="Low", per_study_rob=["Critical", "Critical"],
            weights=[1, 1], heterogeneity=het, pooled=pooled,
            measure="SMD", total_n=40)
        assert g["final"] == "Very low"  # cannot go below


class TestEdgeCases:
    def test_empty(self):
        assert ss.pool([], "MD")["status"] == "no_studies"

    def test_two_studies_low_power(self):
        het = ss.heterogeneity([0.2, 0.4], [0.05, 0.05])
        assert het["low_power"] is True

    def test_invalid_inputs_return_none(self):
        assert ss.md(None, 2, 50, 8, 3, 50) is None
        assert ss.smd_hedges_g(10, 2, 1, 8, 2, 1) is None
        assert ss.fisher_z(0.5, 2) is None
