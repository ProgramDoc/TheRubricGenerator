"""Integration tests for the Synthesis API + pipeline.

Mocks the LLM boundary (classify / screen / extract / prefill / RoB) so the
orchestration, persistence, pooling, GRADE, and endpoint wiring are exercised
without any network calls.
"""

from __future__ import annotations

import json

import pytest


def _mk_papers(n: int, owner_id: int) -> list[int]:
    from main import get_db
    conn = get_db()
    ids = []
    try:
        for i in range(n):
            conn.execute(
                "INSERT INTO papers (filename, disk_filename, sha256, user_id) VALUES (?,?,?,?)",
                (f"study{i}.pdf", f"study{i}.pdf", f"hash{i}", owner_id))
            ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.commit()
    finally:
        conn.close()
    return ids


def _admin_id() -> int:
    from main import get_db
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
        return row["id"]
    finally:
        conn.close()


@pytest.fixture
def mock_llm(monkeypatch):
    """Patch every LLM call in the synthesis pipeline with deterministic stubs."""
    from backend import synthesis as syn

    # three studies with means that yield a clear effect
    means = {0: (5.0, 1.0, 50, 6.5, 1.1, 50),
             1: (4.8, 1.0, 60, 6.2, 1.2, 61),
             2: (5.2, 1.1, 40, 6.0, 1.0, 42)}
    counter = {"i": 0}

    monkeypatch.setattr(syn.annotator_mod, "load_paper_pdf",
                        lambda *a, **k: (b"%PDF-1.4 test", "study.pdf"))
    monkeypatch.setattr(syn.annotator_mod, "classify_study_design",
                        lambda *a, **k: {"study_type": "Randomized Controlled Trial",
                                         "major_category": "Interventional", "subcategory": "RCT"})
    monkeypatch.setattr(syn.annotator_mod, "prefill_fields", lambda *a, **k: {"primary_outcome_definition": "pain score"})
    monkeypatch.setattr(syn, "derive_eligibility_criteria",
                        lambda pico: {"inclusion": [{"axis": "population", "criterion": "adults"}],
                                      "exclusion": [], "design_filter": ["Randomized Controlled Trial"]})
    monkeypatch.setattr(syn, "screen_paper",
                        lambda *a, **k: {"decision": "include", "confidence": "high",
                                         "reason": "matches PICO", "per_criterion": [],
                                         "prisma_exclusion_reason": ""})

    def fake_extract(pdf_bytes, outcome_name, measure, pico):
        i = counter["i"] % 3
        counter["i"] += 1
        m1, sd1, n1, m2, sd2, n2 = means[i]
        return [{"context_label": "12wk", "timepoint": "12 weeks", "subgroup": "overall",
                 "comparison": "drug vs placebo",
                 "raw": {"m1": m1, "sd1": sd1, "n1": n1, "m2": m2, "sd2": sd2, "n2": n2},
                 "source_quote": "table 2", "extraction_confidence": "high", "needs_review": False}]

    monkeypatch.setattr(syn, "extract_outcome_data", fake_extract)
    monkeypatch.setattr(syn.qa_mod, "appraise_rob_only",
                        lambda *a, **k: ({"1": {"judgement": "Low"}}, "Low", "NA"))
    return syn


class TestSynthesisAuth:
    def test_supported_measures_requires_auth(self, client):
        assert client.get("/api/synthesis/supported-measures").status_code in (401, 403)

    def test_supported_measures_ok(self, client, admin_user):
        r = client.get("/api/synthesis/supported-measures",
                       cookies={"rubricgen_session": admin_user["cookie"]})
        assert r.status_code == 200
        body = r.json()
        assert "SMD" in body["outcome_types"]["continuous"]
        assert "OR" in body["outcome_types"]["binary"]

    def test_prompts_dev_view(self, client, admin_user):
        r = client.get("/api/synthesis/prompts",
                       cookies={"rubricgen_session": admin_user["cookie"]})
        assert r.status_code == 200
        cat = r.json()
        assert "statistics_source" in cat
        assert "grade_body_of_evidence" in cat["statistics_source"]


class TestSynthesisPipeline:
    def test_single_paper_inline(self, client, admin_user, mock_llm):
        pids = _mk_papers(1, _admin_id())
        r = client.post("/api/synthesis/reviews",
                        json={"paper_ids": pids, "title": "Pain RCTs",
                              "pico": {"population": "adults", "intervention": "drug",
                                       "comparator": "placebo", "outcomes": ["pain"]},
                              "outcomes": [{"name": "pain", "outcome_type": "continuous",
                                            "effect_measure": "SMD"}],
                              "run_rob": True},
                        cookies={"rubricgen_session": admin_user["cookie"]})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "complete"
        rid = r.json()["review_id"]
        detail = client.get(f"/api/synthesis/reviews/{rid}",
                            cookies={"rubricgen_session": admin_user["cookie"]}).json()
        assert detail["status"] == "complete"
        assert len(detail["studies"]) == 1
        assert detail["studies"][0]["screening_decision"] == "include"
        assert detail["studies"][0]["rob_overall"] == "Low"
        assert len(detail["data_points"]) == 1
        assert detail["data_points"][0]["yi"] is not None
        assert detail["results"][0]["k_studies"] == 1
        assert detail["prisma"]["included"] == 1

    def test_multi_study_pool_via_direct_run(self, client, admin_user, mock_llm):
        # 3 papers -> run the pipeline synchronously to avoid thread flakiness
        from main import get_db, PAPERS_DIR
        from backend import synthesis as syn
        pids = _mk_papers(3, _admin_id())
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO synthesis_reviews (user_id, title, paper_ids_json, pico_json, run_rob, status) "
                "VALUES (?,?,?,?,?, 'pending')",
                (_admin_id(), "t", json.dumps(pids), json.dumps({"population": "adults"}), 1))
            rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO synthesis_outcomes (review_id, name, outcome_type, effect_measure, model_choice, tau2_method) "
                "VALUES (?,?,?,?,?,?)", (rid, "pain", "continuous", "SMD", "random", "REML"))
            conn.commit()
        finally:
            conn.close()
        syn.run_synthesis(get_db, PAPERS_DIR, _admin_id(), True, rid)
        detail = client.get(f"/api/synthesis/reviews/{rid}",
                            cookies={"rubricgen_session": admin_user["cookie"]}).json()
        assert detail["status"] == "complete"
        assert len([s for s in detail["studies"] if s["screening_decision"] == "include"]) == 3
        res = detail["results"][0]
        assert res["k_studies"] == 3
        assert res["status"] == "ok"
        assert res["grade_certainty"] in ("High", "Moderate", "Low", "Very low")
        assert res["forest"]["studies"]
        assert res["random"]["estimate"] is not None
        # generated code present
        assert res["code_blocks"]
        assert any(b["key"] == "model" for b in res["code_blocks"])

    def test_edit_datapoint_and_repool(self, client, admin_user, mock_llm):
        pids = _mk_papers(1, _admin_id())
        rid = client.post("/api/synthesis/reviews",
                          json={"paper_ids": pids,
                                "outcomes": [{"name": "pain", "outcome_type": "continuous",
                                              "effect_measure": "MD"}], "run_rob": False},
                          cookies={"rubricgen_session": admin_user["cookie"]}).json()["review_id"]
        detail = client.get(f"/api/synthesis/reviews/{rid}",
                            cookies={"rubricgen_session": admin_user["cookie"]}).json()
        dp = detail["data_points"][0]
        # edit the intervention mean
        r = client.patch(f"/api/synthesis/data-points/{dp['id']}",
                         json={"raw": {"m1": 10.0}},
                         cookies={"rubricgen_session": admin_user["cookie"]})
        assert r.status_code == 200
        assert r.json()["yi"] is not None
        # re-pool
        r2 = client.post(f"/api/synthesis/reviews/{rid}/pool",
                         cookies={"rubricgen_session": admin_user["cookie"]})
        assert r2.status_code == 200

    def test_screening_override(self, client, admin_user, mock_llm):
        pids = _mk_papers(1, _admin_id())
        rid = client.post("/api/synthesis/reviews",
                          json={"paper_ids": pids,
                                "outcomes": [{"name": "pain", "outcome_type": "continuous",
                                              "effect_measure": "SMD"}], "run_rob": False},
                          cookies={"rubricgen_session": admin_user["cookie"]}).json()["review_id"]
        detail = client.get(f"/api/synthesis/reviews/{rid}",
                            cookies={"rubricgen_session": admin_user["cookie"]}).json()
        sid = detail["studies"][0]["id"]
        r = client.patch(f"/api/synthesis/studies/{sid}",
                         json={"decision": "exclude", "exclude_reason": "wrong population"},
                         cookies={"rubricgen_session": admin_user["cookie"]})
        assert r.status_code == 200
        prisma = client.get(f"/api/synthesis/reviews/{rid}/prisma",
                           cookies={"rubricgen_session": admin_user["cookie"]}).json()
        assert prisma["included"] == 0

    def test_csv_export(self, client, admin_user, mock_llm):
        pids = _mk_papers(1, _admin_id())
        rid = client.post("/api/synthesis/reviews",
                          json={"paper_ids": pids,
                                "outcomes": [{"name": "pain", "outcome_type": "continuous",
                                              "effect_measure": "SMD"}], "run_rob": False},
                          cookies={"rubricgen_session": admin_user["cookie"]}).json()["review_id"]
        r = client.get(f"/api/synthesis/reviews/{rid}.csv",
                       cookies={"rubricgen_session": admin_user["cookie"]})
        assert r.status_code == 200
        assert "outcome" in r.text

    def test_code_download(self, client, admin_user, mock_llm):
        pids = _mk_papers(1, _admin_id())
        rid = client.post("/api/synthesis/reviews",
                          json={"paper_ids": pids,
                                "outcomes": [{"name": "pain", "outcome_type": "continuous",
                                              "effect_measure": "SMD"}], "run_rob": False},
                          cookies={"rubricgen_session": admin_user["cookie"]}).json()["review_id"]
        detail = client.get(f"/api/synthesis/reviews/{rid}",
                            cookies={"rubricgen_session": admin_user["cookie"]}).json()
        oid = detail["outcomes"][0]["id"]
        r = client.get(f"/api/synthesis/reviews/{rid}/code/{oid}.R",
                       cookies={"rubricgen_session": admin_user["cookie"]})
        assert r.status_code == 200
        assert "metacont" in r.text or "library(meta)" in r.text


class TestSynthesisValidation:
    def test_rejects_bad_measure(self, client, admin_user):
        pids = _mk_papers(1, _admin_id())
        r = client.post("/api/synthesis/reviews",
                        json={"paper_ids": pids,
                              "outcomes": [{"name": "x", "outcome_type": "continuous",
                                            "effect_measure": "OR"}]},
                        cookies={"rubricgen_session": admin_user["cookie"]})
        assert r.status_code == 400

    def test_rejects_empty_outcomes(self, client, admin_user):
        pids = _mk_papers(1, _admin_id())
        r = client.post("/api/synthesis/reviews",
                        json={"paper_ids": pids, "outcomes": []},
                        cookies={"rubricgen_session": admin_user["cookie"]})
        assert r.status_code == 400  # explicit "at least one outcome" check
