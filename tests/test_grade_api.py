"""API + persistence tests for the GRADE agent.

Covers the stateless ``/api/agents/grade`` + ``/api/agents/grade-sof`` endpoints and
the persisted ``/api/grade/runs`` lifecycle (create → list → detail → events →
export → delete), plus the hybrid-indirectness behaviour (reviewer value wins;
auto-assessment fills the gap). The LLM auto-assessor is monkeypatched so no
network call happens.
"""

import pytest

RCT_STUDIES = [
    {"study_id": "A", "design": "RCT", "events_int": 50, "n_int": 500, "events_ctrl": 100, "n_ctrl": 500},
    {"study_id": "B", "design": "RCT", "events_int": 40, "n_int": 400, "events_ctrl": 80, "n_ctrl": 400},
    {"study_id": "C", "design": "RCT", "events_int": 45, "n_int": 450, "events_ctrl": 90, "n_ctrl": 450},
]


def _hdr(u):
    return {"Cookie": f"rubricgen_session={u['cookie']}"}


class TestStatelessGrade:
    def test_grade_from_studies(self, client, test_user):
        r = client.post("/api/agents/grade", json={
            "studies": RCT_STUDIES, "measure": "RR",
            "per_study_rob": ["Low", "Low", "Low"],
            "baseline_risk_per_1000": 200,
        }, headers=_hdr(test_user))
        assert r.status_code == 200, r.text
        g = r.json()
        assert g["initial"] == "High"
        assert g["final"] == "High"
        assert g["absolute_effects"]["nnt"] == pytest.approx(10, abs=1)

    def test_grade_sof_row(self, client, test_user):
        r = client.post("/api/agents/grade-sof", json={
            "studies": RCT_STUDIES, "measure": "RR",
            "per_study_rob": ["Low", "Low", "Low"],
            "baseline_risk_per_1000": 200,
            "outcome": {"name": "Mortality", "timeframe": "12mo"},
        }, headers=_hdr(test_user))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["sof_row"]["outcome"] == "Mortality"
        assert body["sof_row"]["certainty"] == "High"
        assert body["grade"]["final"] == "High"

    def test_missing_inputs_400(self, client, test_user):
        r = client.post("/api/agents/grade", json={"per_study_rob": []},
                        headers=_hdr(test_user))
        assert r.status_code == 400

    def test_requires_auth(self, client):
        r = client.post("/api/agents/grade", json={"studies": RCT_STUDIES, "measure": "RR"})
        assert r.status_code in (401, 403)


class TestGradeRuns:
    def test_run_lifecycle_reviewer_indirectness(self, client, test_user):
        payload = {
            "name": "Test run",
            "auto_indirectness": False,
            "rob_by_study": {"A": "Low", "B": "Low", "C": "Some concerns"},
            "bodies": [{
                "studies": RCT_STUDIES, "measure": "RR",
                "outcome_name": "Mortality", "comparison": "Drug vs placebo",
                "baseline_risk_per_1000": 200,
                "indirectness_levels": 0,
            }],
        }
        r = client.post("/api/grade/runs", json=payload, headers=_hdr(test_user))
        assert r.status_code == 200, r.text
        detail = r.json()
        run_id = detail["id"]
        assert detail["status"] == "complete"
        assert detail["n_bodies"] == 1
        assert len(detail["results"]) == 1
        res = detail["results"][0]
        assert res["outcome_name"] == "Mortality"
        assert res["certainty"] == "High"
        assert res["grade"]["final"] == "High"

        # list
        r = client.get("/api/grade/runs", headers=_hdr(test_user))
        assert r.status_code == 200
        assert any(x["id"] == run_id for x in r.json())

        # detail
        r = client.get(f"/api/grade/runs/{run_id}", headers=_hdr(test_user))
        assert r.status_code == 200
        assert r.json()["id"] == run_id

        # events
        r = client.get(f"/api/grade/runs/{run_id}/events", headers=_hdr(test_user))
        assert r.status_code == 200
        types = [e["event_type"] for e in r.json()]
        assert "run_started" in types and "body_done" in types

        # csv export
        r = client.get(f"/api/grade/runs/{run_id}/csv", headers=_hdr(test_user))
        assert r.status_code == 200
        assert "certainty" in r.text and "Mortality" in r.text

        # delete
        r = client.delete(f"/api/grade/runs/{run_id}", headers=_hdr(test_user))
        assert r.status_code == 200
        r = client.get(f"/api/grade/runs/{run_id}", headers=_hdr(test_user))
        assert r.status_code == 404

    def test_hybrid_auto_indirectness(self, client, admin_user, monkeypatch):
        # Stub the LLM auto-assessor to return a fixed serious-indirectness level.
        # Admin bypasses the credit gate on the auto-indirectness pass.
        import backend.evidence_synthesis.grade_indirectness as gi

        def _fake(target_pico, body_ctx):
            return ({"population": {"judgement": "not_direct", "rationale": "surrogate"}},
                    "serious", 1, "Serious indirectness: surrogate outcome")
        monkeypatch.setattr(gi, "assess_body", _fake)

        payload = {
            "name": "Auto indirectness",
            "auto_indirectness": True,
            "target_pico": {"population": "adults", "intervention": "drug",
                            "comparator": "placebo", "outcome": "survival"},
            "rob_by_study": {"A": "Low", "B": "Low", "C": "Low"},
            "bodies": [{
                "studies": RCT_STUDIES, "measure": "RR",
                "outcome_name": "Surrogate marker",
                "baseline_risk_per_1000": 200,
                # no indirectness_levels -> auto path
            }],
        }
        r = client.post("/api/grade/runs", json=payload, headers=_hdr(admin_user))
        assert r.status_code == 200, r.text
        res = r.json()["results"][0]
        # Auto-assessed serious indirectness -> High downgraded to Moderate.
        ind = next(d for d in res["grade"]["domains"] if d["domain"] == "Indirectness")
        assert ind["downgrade"] == 1
        assert res["certainty"] == "Moderate"
        assert res["indirectness"] is not None  # detail persisted

    def test_reviewer_overrides_auto(self, client, test_user, monkeypatch):
        import backend.evidence_synthesis.grade_indirectness as gi

        def _boom(target_pico, body_ctx):  # must NOT be called
            raise AssertionError("auto-assessor should not run when reviewer supplied a level")
        monkeypatch.setattr(gi, "assess_body", _boom)

        payload = {
            "auto_indirectness": True,
            "bodies": [{
                "studies": RCT_STUDIES, "measure": "RR",
                "outcome_name": "Mortality",
                "indirectness_levels": 0,  # reviewer value wins
            }],
        }
        r = client.post("/api/grade/runs", json=payload, headers=_hdr(test_user))
        assert r.status_code == 200, r.text
        assert r.json()["results"][0]["certainty"] == "High"

    def test_empty_bodies_400(self, client, test_user):
        r = client.post("/api/grade/runs", json={"bodies": []}, headers=_hdr(test_user))
        assert r.status_code == 400
