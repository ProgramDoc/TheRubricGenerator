"""Unit tests for the pure ASCO table renderers (backend/table_render.py).

Run isolated:  pytest tests/test_table_render.py --noconftest -q
"""
from backend import table_render as tr


def _sof_row():
    return {
        "outcome": "Mortality", "timeframe": "12 months",
        "n_studies": 3, "n_participants": 603, "measure": "RR",
        "relative_effect": {"estimate": 0.5, "ci_low": 0.35, "ci_high": 0.7},
        "absolute_effects": {"baseline_per_1000": 200.0, "intervention_per_1000": 100.0,
                             "risk_difference_per_1000": -100.0, "rd_ci_per_1000": [-130.0, -60.0],
                             "nnt": 10, "favours": "intervention"},
        "certainty": "Moderate",
        "certainty_reasons": [{"domain": "Imprecision", "direction": "downgrade",
                               "levels": 1, "reason": "wide CI"}],
        "explanation": "RR 0.50; downgraded for imprecision.",
    }


class TestTable5:
    def test_rows_html_csv(self):
        out = tr.render_table5([_sof_row()], pico={"population": "adults", "intervention": "drug"})
        assert out["columns"][0] == "Outcome"
        row = out["rows"][0]
        assert row["outcome"] == "Mortality (12 months)"
        assert "3 studies" in row["participants"] and "603" in row["participants"]
        assert row["relative_effect"] == "RR 0.5 (0.35 to 0.7)"
        assert row["assumed_risk"] == "200.0 per 1000"
        assert row["risk_difference"] == "-100.0 (-130.0 to -60.0)"
        assert row["certainty_symbol"] == tr.GRADE_SYMBOL["Moderate"]
        assert 'class="asco-t5"' in out["html"]
        assert "Mortality" in out["html"]
        assert "population: adults" in out["html"].lower()
        assert "Mortality (12 months)" in out["csv"]

    def test_continuous_outcome_has_no_absolute(self):
        s = {"outcome": "Pain", "n_studies": 1, "measure": "SMD",
             "relative_effect": {"estimate": -0.4, "ci_low": -0.7, "ci_high": -0.1},
             "absolute_effects": None, "certainty": "High", "certainty_reasons": []}
        row = tr.render_table5([s])["rows"][0]
        assert row["assumed_risk"] == "—"
        assert row["risk_difference"] == "—"
        assert row["relative_effect"] == "SMD -0.4 (-0.7 to -0.1)"
        assert "1 study" in row["participants"]


def _appraisal(paper_id, tool, overall, doms):
    return {"paper_id": paper_id, "study_id": f"Author {paper_id}", "study_type": "RCT",
            "rob_tool": tool, "overall": overall,
            "domains": {str(i + 1): {"judgement": j, "name": n} for i, (n, j) in enumerate(doms)}}


class TestTable3:
    def test_groups_by_tool(self):
        apps = [
            _appraisal(1, "rob2", "Low", [("Randomization", "Low"), ("Deviations", "Low")]),
            _appraisal(2, "rob2", "Some concerns", [("Randomization", "Low"), ("Deviations", "Some concerns")]),
            _appraisal(3, "robins_i", "Serious", [("Confounding", "Serious"), ("Selection", "Low")]),
        ]
        out = tr.render_table3(apps)
        groups = {g["tool"]: g for g in out["groups"]}
        assert set(groups) == {"rob2", "robins_i"}
        assert groups["rob2"]["domain_columns"] == ["Randomization", "Deviations"]
        assert len(groups["rob2"]["rows"]) == 2
        assert groups["rob2"]["rows"][0]["overall"] == "Low"
        assert groups["rob2"]["rows"][0]["study"] == "Author 1"
        assert 'class="asco-t3"' in out["html"]
        assert "Confounding" in out["html"]
        assert "Tool: rob2" in out["csv"]
