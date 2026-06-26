"""Unit tests for the pure synthesis agents (backend/agents.py).

Run isolated from the FastAPI conftest:
    pytest tests/test_agents.py --noconftest -q
"""
import pytest

from backend import synthesis_agents as agents


def _binary_rows():
    # Three 2x2 tables, intervention (arm1) with fewer events -> protective RR.
    return [
        {"raw": {"events1": 10, "total1": 100, "events2": 20, "total2": 100}, "label": "A"},
        {"raw": {"events1": 12, "total1": 110, "events2": 24, "total2": 108}, "label": "B"},
        {"raw": {"events1": 8, "total1": 90, "events2": 18, "total2": 95}, "label": "C"},
    ]


class TestPoolAgent:
    def test_pool_rr_from_raw(self):
        out = agents.pool_effects({"measure": "RR", "effects": _binary_rows()})
        assert out["status"] == "ok" and out["k"] == 3
        assert out["random"]["estimate"] < 0          # log RR < 0 -> protective
        assert out["total_n"] and out["total_n"] > 0

    def test_pool_precomputed_yivi(self):
        rows = [{"yi": 0.2, "vi": 0.04}, {"yi": 0.3, "vi": 0.05}, {"yi": 0.25, "vi": 0.03}]
        out = agents.pool_effects({"measure": "SMD", "effects": rows})
        assert out["status"] == "ok" and out["k"] == 3

    def test_unknown_measure_raises(self):
        with pytest.raises(ValueError):
            agents.pool_effects({"measure": "WAT", "effects": []})

    def test_bad_rows_reported_not_raised(self):
        out = agents.pool_effects({"measure": "SMD",
                                   "effects": [{"yi": 0.2, "vi": 0.04}, {"raw": {}}]})
        assert out["k"] == 1
        assert len(out["problems"]) == 1


class TestGradeAgent:
    def test_grade_chains_from_pool(self):
        pr = agents.pool_effects({"measure": "RR", "effects": _binary_rows()})
        g = agents.grade_certainty({
            "pool_result": pr, "design": "Randomized Controlled Trial",
            "per_study_rob": ["Low", "Low", "Low"], "baseline_risk_per_1000": 200,
        })
        assert g["final"] in ("High", "Moderate", "Low", "Very low")
        assert g["initial"] == "High"            # design routed to RCT
        assert "domains" in g

    def test_grade_requires_pooled(self):
        with pytest.raises(ValueError):
            agents.grade_certainty({"measure": "RR", "per_study_rob": ["Low"]})

    def test_observational_design_starts_low(self):
        pr = agents.pool_effects({"measure": "RR", "effects": _binary_rows()})
        g = agents.grade_certainty({
            "pool_result": pr, "design": "Cohort Study",
            "per_study_rob": ["Low", "Low", "Low"]})
        assert g["initial"] == "Low"


class TestSofAgent:
    def test_sof_row_assembled(self):
        res = agents.sof({
            "measure": "RR", "effects": _binary_rows(),
            "design": "Randomized Controlled Trial",
            "per_study_rob": ["Low", "Low", "Low"],
            "baseline_risk_per_1000": 200,
            "outcome": {"name": "Mortality", "follow_up": "12 months"},
        })
        row = res["sof_row"]
        assert row["outcome"] == "Mortality"
        assert row["n_studies"] == 3
        assert row["measure"] == "RR"
        # relative effect back-transformed to a display ratio < 1 (protective)
        assert 0 < row["relative_effect"]["estimate"] < 1
        assert row["certainty"] in ("High", "Moderate", "Low", "Very low")
        assert row["absolute_effects"]["intervention_per_1000"] is not None
