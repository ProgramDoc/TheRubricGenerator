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

    # Outcome-aware so tests can make one outcome Low and another High, and can
    # assert how many times the instrument actually ran.
    rob_calls: list[str] = []
    rob_by_outcome: dict[str, str] = {}

    def fake_rob(pdf_bytes, fields, classification, assessed_outcome, cfg, **kw):
        rob_calls.append(assessed_outcome)
        verdict = rob_by_outcome.get(assessed_outcome, "Low")
        if isinstance(verdict, Exception):
            raise verdict
        return ({"1": {"judgement": verdict}}, verdict, "NA")

    monkeypatch.setattr(syn.qa_mod, "appraise_rob_only", fake_rob)
    syn._test_rob_calls = rob_calls          # noqa: SLF001 — test handles
    syn._test_rob_by_outcome = rob_by_outcome
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
        # Risk of bias is per (study x outcome) now, not a column on the study.
        assert len(detail["study_rob"]) == 1
        assert detail["study_rob"][0]["rob_overall"] == "Low"
        assert detail["study_rob"][0]["outcome_id"] == detail["outcomes"][0]["id"]
        assert len(detail["data_points"]) == 1
        assert detail["data_points"][0]["yi"] is not None
        assert detail["results"][0]["k_studies"] == 1
        assert detail["prisma"]["included"] == 1

    def _run_two_outcome_review(self, run_rob=True, n_papers=1, user_id=None,
                                is_admin=True):
        """Create + run a 2-outcome review synchronously. Returns its review id."""
        from main import get_db, PAPERS_DIR
        from backend import synthesis as syn
        uid = user_id if user_id is not None else _admin_id()
        pids = _mk_papers(n_papers, uid)
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO synthesis_reviews (user_id, title, paper_ids_json, pico_json, "
                "run_rob, rob_scope, status) VALUES (?,?,?,?,?, 'outcome', 'pending')",
                (uid, "t", json.dumps(pids), json.dumps({"population": "adults"}),
                 1 if run_rob else 0))
            rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            for name in ("overall survival", "grade 3-4 adverse events"):
                conn.execute(
                    "INSERT INTO synthesis_outcomes (review_id, name, outcome_type, "
                    "effect_measure, model_choice, tau2_method) VALUES (?,?,?,?,?,?)",
                    (rid, name, "continuous", "SMD", "random", "REML"))
            conn.commit()
        finally:
            conn.close()
        syn.run_synthesis(get_db, PAPERS_DIR, uid, is_admin, rid)
        return rid

    def test_rob_runs_once_per_outcome(self, client, admin_user, mock_llm):
        """The instrument runs per (study x outcome); prefill stays once per paper."""
        prefills = []
        orig = mock_llm.annotator_mod.prefill_fields
        mock_llm.annotator_mod.prefill_fields = lambda *a, **k: (
            prefills.append(1) or orig(*a, **k))
        rid = self._run_two_outcome_review()
        detail = client.get(f"/api/synthesis/reviews/{rid}",
                            cookies={"rubricgen_session": admin_user["cookie"]}).json()
        assert sorted(mock_llm._test_rob_calls) == ["grade 3-4 adverse events",
                                                    "overall survival"]
        assert len(prefills) == 1, "prefill_fields is outcome-independent"
        assert len(detail["study_rob"]) == 2
        assert all(r["status"] == "ok" for r in detail["study_rob"])

    def test_per_outcome_rob_drives_grade(self, client, admin_user, mock_llm):
        """The regression guard: one trial Low for one outcome and High for another
        must produce different risk-of-bias downgrades, not one label copied."""
        mock_llm._test_rob_by_outcome.update({
            "overall survival": "Low",
            "grade 3-4 adverse events": "High",
        })
        rid = self._run_two_outcome_review()
        detail = client.get(f"/api/synthesis/reviews/{rid}",
                            cookies={"rubricgen_session": admin_user["cookie"]}).json()
        by_name = {o["id"]: o["name"] for o in detail["outcomes"]}
        downgrades = {}
        for res in detail["results"]:
            rob = [d for d in res["grade"]["domains"] if d["domain"] == "Risk of bias"][0]
            downgrades[by_name[res["outcome_id"]]] = rob["downgrade"]
        assert downgrades["overall survival"] == 0
        assert downgrades["grade 3-4 adverse events"] == 2

    def test_one_outcome_rob_failure_is_isolated(self, client, admin_user, mock_llm):
        mock_llm._test_rob_by_outcome["grade 3-4 adverse events"] = RuntimeError("boom")
        rid = self._run_two_outcome_review()
        detail = client.get(f"/api/synthesis/reviews/{rid}",
                            cookies={"rubricgen_session": admin_user["cookie"]}).json()
        assert detail["status"] == "complete"
        by_status = {r["status"] for r in detail["study_rob"]}
        assert by_status == {"ok", "error"}, "one failure must not lose the other outcome"

    def test_rob_disabled_is_not_rated_not_downgraded(self, client, admin_user, mock_llm):
        """run_rob=False used to score every study 'some concerns' and silently
        downgrade one level. It must come back unrated instead."""
        rid = self._run_two_outcome_review(run_rob=False)
        detail = client.get(f"/api/synthesis/reviews/{rid}",
                            cookies={"rubricgen_session": admin_user["cookie"]}).json()
        assert detail["study_rob"] == []
        for res in detail["results"]:
            assert res["grade_certainty"] is None
            assert res["grade"]["status"] == "not_rated"
            assert res["grade"]["warnings"]

    def test_repool_legacy_review_uses_study_level_rob(self, client, admin_user, mock_llm):
        """A review appraised before per-outcome RoB must still grade on re-pool."""
        from main import get_db
        from backend import synthesis as syn
        rid = self._run_two_outcome_review()
        conn = get_db()
        try:
            # Emulate a pre-migration review: study-level label, no per-outcome rows.
            conn.execute("DELETE FROM synthesis_study_rob WHERE review_id=?", (rid,))
            conn.execute("UPDATE synthesis_reviews SET rob_scope='study' WHERE id=?", (rid,))
            conn.execute("UPDATE synthesis_studies SET rob_overall='Low', rob_tool='rob2' "
                         "WHERE review_id=?", (rid,))
            conn.commit()
            syn.repool_review(conn, rid)
            conn.commit()
        finally:
            conn.close()
        detail = client.get(f"/api/synthesis/reviews/{rid}",
                            cookies={"rubricgen_session": admin_user["cookie"]}).json()
        for res in detail["results"]:
            assert res["grade_certainty"] is not None
            assert res["grade"]["rob_sources"].get("study_legacy")

    def test_amstar2_excluded_from_rob_domain(self, client, admin_user, mock_llm):
        """AMSTAR-2 rates confidence, where High is good — it must not be scored
        as a risk label (which would downgrade a good review two levels)."""
        from main import get_db
        from backend import synthesis as syn
        rid = self._run_two_outcome_review()
        conn = get_db()
        try:
            conn.execute("UPDATE synthesis_study_rob SET rob_tool='amstar2', "
                         "rob_overall='High' WHERE review_id=?", (rid,))
            conn.commit()
            syn.repool_review(conn, rid)
            conn.commit()
        finally:
            conn.close()
        detail = client.get(f"/api/synthesis/reviews/{rid}",
                            cookies={"rubricgen_session": admin_user["cookie"]}).json()
        for res in detail["results"]:
            assert res["grade"]["status"] == "not_rated"
            assert res["grade"]["rob_sources"].get("excluded_non_rob_tool")

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


class TestSynthesisCreditModel:
    """Pre-charge and refunds must decompose into the same units, or a partly
    failed review silently over- or under-charges."""

    @pytest.mark.parametrize("n_outcomes", [0, 1, 3])
    @pytest.mark.parametrize("run_rob", [True, False])
    def test_estimate_cost_matches_worker_units(self, n_outcomes, run_rob):
        from backend import synthesis as syn
        k = syn.billable_units(n_outcomes)
        expected_per_paper = syn.CREDIT_COST_SYNTH_SCREEN + k * syn.CREDIT_COST_SYNTH_EXTRACT
        if run_rob:
            expected_per_paper += (syn.CREDIT_COST_SYNTH_ROB_PREFILL
                                   + k * syn.CREDIT_COST_SYNTH_ROB_TOOL)
        assert syn.estimate_cost(4, n_outcomes, run_rob) == 4 * expected_per_paper

    def test_rob_charge_scales_only_the_tool_half(self):
        from backend import synthesis as syn
        # prefill is outcome-independent: 3 outcomes must not bill 3 prefills.
        assert (syn.rob_charge(3) - syn.rob_charge(1)
                == 2 * syn.CREDIT_COST_SYNTH_ROB_TOOL)

    def test_single_outcome_rob_charge_unchanged(self):
        from backend import synthesis as syn
        assert syn.rob_charge(1) == 24  # the pre-split flat rate


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
