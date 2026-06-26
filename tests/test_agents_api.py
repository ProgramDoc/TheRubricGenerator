"""HTTP-level tests for the independently-servable synthesis agents.

Exercises the real FastAPI routes (auth + JSON in/out) for /api/agents/pool,
/api/agents/grade, /api/agents/sof — no LLM, no network.
"""
from __future__ import annotations

import pytest


def _auth(admin_user):
    return {"rubricgen_session": admin_user["cookie"]}


def _binary_rows():
    # intervention (arm1) with fewer events -> protective RR (<1 on display scale)
    return [
        {"raw": {"events1": 10, "total1": 100, "events2": 20, "total2": 100}, "label": "A"},
        {"raw": {"events1": 12, "total1": 110, "events2": 24, "total2": 108}, "label": "B"},
        {"raw": {"events1": 8, "total1": 90, "events2": 18, "total2": 95}, "label": "C"},
    ]


class TestAgentsAuth:
    def test_pool_requires_auth(self, client):
        r = client.post("/api/agents/pool", json={"measure": "RR", "effects": []})
        assert r.status_code in (401, 403)


class TestAgentsPool:
    def test_pool_ok(self, client, admin_user):
        r = client.post("/api/agents/pool", json={"measure": "RR", "effects": _binary_rows()},
                        cookies=_auth(admin_user))
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok" and body["k"] == 3
        assert body["random"]["estimate"] < 0
        assert body["total_n"] and body["total_n"] > 0

    def test_pool_bad_measure_400(self, client, admin_user):
        r = client.post("/api/agents/pool", json={"measure": "NOPE", "effects": []},
                        cookies=_auth(admin_user))
        assert r.status_code == 400


class TestAgentsGradeAndSof:
    def test_grade_chains_from_pool(self, client, admin_user):
        pr = client.post("/api/agents/pool", json={"measure": "RR", "effects": _binary_rows()},
                         cookies=_auth(admin_user)).json()
        r = client.post("/api/agents/grade",
                        json={"pool_result": pr, "design": "Randomized Controlled Trial",
                              "per_study_rob": ["Low", "Low", "Low"], "baseline_risk_per_1000": 200},
                        cookies=_auth(admin_user))
        assert r.status_code == 200
        g = r.json()
        assert g["initial"] == "High"
        assert g["final"] in ("High", "Moderate", "Low", "Very low")

    def test_grade_missing_pooled_400(self, client, admin_user):
        r = client.post("/api/agents/grade", json={"measure": "RR", "per_study_rob": ["Low"]},
                        cookies=_auth(admin_user))
        assert r.status_code == 400

    def test_sof_produces_table5_row(self, client, admin_user):
        r = client.post("/api/agents/sof",
                        json={"measure": "RR", "effects": _binary_rows(),
                              "design": "Randomized Controlled Trial",
                              "per_study_rob": ["Low", "Low", "Low"],
                              "baseline_risk_per_1000": 200,
                              "outcome": {"name": "Mortality", "follow_up": "12 months"}},
                        cookies=_auth(admin_user))
        assert r.status_code == 200
        row = r.json()["sof_row"]
        assert row["outcome"] == "Mortality" and row["n_studies"] == 3
        assert 0 < row["relative_effect"]["estimate"] < 1
        assert row["certainty"] in ("High", "Moderate", "Low", "Very low")
        assert row["absolute_effects"]["intervention_per_1000"] is not None


@pytest.fixture
def mock_paper_llm(monkeypatch):
    """Patch the PDF-load + LLM boundary for the appraise/extract agents."""
    import main
    monkeypatch.setattr(main.annotator_mod, "load_paper_pdf",
                        lambda *a, **k: (b"%PDF-1.4 test", "study.pdf"))
    monkeypatch.setattr(main.annotator_mod, "classify_study_design",
                        lambda *a, **k: {"study_type": "Randomized Controlled Trial",
                                         "major_category": "Interventional", "subcategory": "RCT"})
    monkeypatch.setattr(main.annotator_mod, "prefill_fields",
                        lambda *a, **k: {"primary_outcome_definition": "pain score at 12 weeks"})
    monkeypatch.setattr(main.qa_mod, "appraise_rob_only",
                        lambda *a, **k: ({"1": {"judgement": "Low", "name": "Randomization"}}, "Low", "NA"))
    monkeypatch.setattr(main.synthesis_mod, "extract_outcome_data",
                        lambda *a, **k: [{"context_label": "12wk", "timepoint": "12 weeks",
                                          "subgroup": "overall", "comparison": "drug vs placebo",
                                          "raw": {"events1": 10, "total1": 100, "events2": 20, "total2": 100},
                                          "source_quote": "table 2", "extraction_confidence": "high",
                                          "needs_review": False}])


class TestAppraiseAgent:
    def test_appraise_rob(self, client, admin_user, mock_paper_llm):
        r = client.post("/api/agents/appraise", json={"paper_id": 1}, cookies=_auth(admin_user))
        assert r.status_code == 200
        b = r.json()
        assert b["study_type"] == "Randomized Controlled Trial"
        assert b["rob_tool"] == "rob2"          # design-routed
        assert b["overall"] == "Low"

    def test_appraise_requires_paper_id(self, client, admin_user, mock_paper_llm):
        assert client.post("/api/agents/appraise", json={}, cookies=_auth(admin_user)).status_code == 400


class TestExtractAgent:
    def test_extract_characteristics(self, client, admin_user, mock_paper_llm):
        r = client.post("/api/agents/extract", json={"paper_id": 1, "mode": "characteristics"},
                        cookies=_auth(admin_user))
        assert r.status_code == 200
        b = r.json()
        assert b["study_type"] == "Randomized Controlled Trial"
        assert "primary_outcome_definition" in b["fields"]

    def test_extract_outcome_data_computes_yivi(self, client, admin_user, mock_paper_llm):
        r = client.post("/api/agents/extract",
                        json={"paper_id": 1, "mode": "outcome-data",
                              "outcome": {"name": "Mortality", "measure": "RR"}},
                        cookies=_auth(admin_user))
        assert r.status_code == 200
        dps = r.json()["data_points"]
        assert len(dps) == 1
        assert dps[0]["yi"] is not None and dps[0]["vi"] is not None

    def test_extract_outcome_data_requires_outcome(self, client, admin_user, mock_paper_llm):
        r = client.post("/api/agents/extract", json={"paper_id": 1, "mode": "outcome-data"},
                        cookies=_auth(admin_user))
        assert r.status_code == 400

    def test_full_chain_extract_then_pool(self, client, admin_user, mock_paper_llm):
        ex = client.post("/api/agents/extract",
                         json={"paper_id": 1, "mode": "outcome-data",
                               "outcome": {"name": "Mortality", "measure": "RR"}},
                         cookies=_auth(admin_user)).json()
        effects = [{"yi": dp["yi"], "vi": dp["vi"], "label": "study1"} for dp in ex["data_points"]]
        pr = client.post("/api/agents/pool", json={"measure": "RR", "effects": effects},
                         cookies=_auth(admin_user)).json()
        assert pr["status"] == "ok" and pr["k"] == 1


class TestRenderAgents:
    def test_render_table5_from_sof(self, client, admin_user):
        sof = client.post("/api/agents/sof",
                          json={"measure": "RR", "effects": _binary_rows(),
                                "design": "Randomized Controlled Trial",
                                "per_study_rob": ["Low", "Low", "Low"],
                                "baseline_risk_per_1000": 200,
                                "outcome": {"name": "Mortality"}},
                          cookies=_auth(admin_user)).json()
        r = client.post("/api/agents/render/table5",
                        json={"sof_rows": [sof["sof_row"]], "pico": {"population": "adults"}},
                        cookies=_auth(admin_user))
        assert r.status_code == 200
        out = r.json()
        assert out["rows"][0]["outcome"].startswith("Mortality")
        assert 'class="asco-t5"' in out["html"]
        assert out["csv"].startswith("Outcome")

    def test_render_table3_from_appraise(self, client, admin_user, mock_paper_llm):
        ap = client.post("/api/agents/appraise", json={"paper_id": 1}, cookies=_auth(admin_user)).json()
        r = client.post("/api/agents/render/table3", json={"appraisals": [ap]}, cookies=_auth(admin_user))
        assert r.status_code == 200
        out = r.json()
        assert out["groups"][0]["tool"] == "rob2"
        assert "asco-t3" in out["html"]
