"""Tests for sourcing risk-of-bias labels out of the quality-appraisal tables.

This is the join that makes the RoB tools' judgements reach the body-of-evidence
GRADE rating. Runs against the real ``quality_appraisal_results`` schema (conftest's
autouse fixture calls ``main.init_db()``), so a schema drift breaks these.
"""

from __future__ import annotations

import pytest

from backend.db import get_db
from backend.evidence_synthesis import pooling_prep as pp
from backend.evidence_synthesis.grade_rob_source import rob_records_for_run


@pytest.fixture
def qa_run(test_user):
    """A real quality-appraisal run with real papers — the results table has FKs onto
    ``quality_appraisal_runs`` and ``papers``, so stub ids will not insert. The user is
    registered through the real signup path rather than a hand-built INSERT, so this
    does not have to track the ``users`` NOT NULL columns.

    Returns ``(run_id, [paper_id, ...])`` with four papers available.
    """
    conn = get_db()
    try:
        user_id = conn.execute(
            "SELECT id FROM users WHERE email=?", (test_user["email"],)).fetchone()["id"]
        paper_ids = []
        for i in range(4):
            cur = conn.execute(
                "INSERT INTO papers (filename, sha256, user_id) VALUES (?, ?, ?) "
                "RETURNING id", (f"p{i}.pdf", f"sha-rob-source-{i}", user_id))
            paper_ids.append(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO quality_appraisal_runs (user_id, paper_count) "
            "VALUES (?, ?) RETURNING id", (user_id, len(paper_ids)))
        run_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return run_id, paper_ids


def _insert(conn, run_id, paper_id, *, rob, tool="rob2",
            assessed=None, primary=None, status="ok"):
    conn.execute(
        """INSERT INTO quality_appraisal_results
             (run_id, paper_id, status, rob_tool, rob_overall,
              assessed_outcome, primary_outcome, study_type)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, paper_id, status, tool, rob, assessed, primary,
         "Randomized Controlled Trial"))
    conn.commit()


class TestRobRecordsForRun:
    def test_assessed_outcome_becomes_the_per_outcome_key(self, qa_run):
        run_id, papers = qa_run
        conn = get_db()
        try:
            _insert(conn, run_id, papers[0], rob="Low", assessed="All-cause mortality")
            recs = rob_records_for_run(conn, run_id)
        finally:
            conn.close()
        assert len(recs) == 1
        assert recs[0]["outcome"] == "All-cause mortality"
        assert recs[0]["rob"] == "Low"
        assert recs[0]["rob_source"] == "tool"

    def test_falls_back_to_primary_outcome(self, qa_run):
        # Rows predating assessed_outcome carry only the auto-picked primary.
        run_id, papers = qa_run
        conn = get_db()
        try:
            _insert(conn, run_id, papers[0], rob="Serious", assessed=None,
                    primary="Mortality")
            recs = rob_records_for_run(conn, run_id)
        finally:
            conn.close()
        assert recs[0]["outcome"] == "Mortality"

    def test_no_outcome_at_all_becomes_a_study_level_label(self, qa_run):
        run_id, papers = qa_run
        conn = get_db()
        try:
            _insert(conn, run_id, papers[0], rob="High", assessed="  ", primary=None)
            recs = rob_records_for_run(conn, run_id)
        finally:
            conn.close()
        assert recs[0]["outcome"] is None            # -> tier 3 in resolve_rob

    def test_amstar2_rows_are_excluded(self, qa_run):
        # AMSTAR-2's "High" means high CONFIDENCE (good) -- the opposite polarity to
        # every RoB instrument. Including it would downgrade the best reviews.
        run_id, papers = qa_run
        conn = get_db()
        try:
            _insert(conn, run_id, papers[0], rob="High", tool="amstar2",
                    assessed="Mortality")
            _insert(conn, run_id, papers[1], rob="Low", tool="rob2",
                    assessed="Mortality")
            recs = rob_records_for_run(conn, run_id)
        finally:
            conn.close()
        assert [r["paper_id"] for r in recs] == [papers[1]]
        assert all(r["rob_tool"] != "amstar2" for r in recs)

    def test_critically_low_amstar2_is_excluded_too(self, qa_run):
        # Not in the severity map either -- it would quietly land at severity 1.
        run_id, papers = qa_run
        conn = get_db()
        try:
            _insert(conn, run_id, papers[0], rob="Critically low", tool="amstar2",
                    assessed="M")
            recs = rob_records_for_run(conn, run_id)
        finally:
            conn.close()
        assert recs == []

    def test_errored_and_labelless_rows_are_skipped(self, qa_run):
        run_id, papers = qa_run
        conn = get_db()
        try:
            _insert(conn, run_id, papers[0], rob="Low", assessed="M", status="error")
            _insert(conn, run_id, papers[1], rob=None, assessed="M")
            _insert(conn, run_id, papers[2], rob="", assessed="M")
            recs = rob_records_for_run(conn, run_id)
        finally:
            conn.close()
        assert recs == []

    def test_paper_id_maps_to_the_pooling_study_id(self, qa_run):
        run_id, papers = qa_run
        conn = get_db()
        try:
            _insert(conn, run_id, papers[0], rob="Moderate", assessed="Mortality")
            recs = rob_records_for_run(
                conn, run_id, study_id_by_paper={papers[0]: "Smith 2019"})
        finally:
            conn.close()
        assert recs[0]["study_id"] == "Smith 2019"

    def test_one_paper_can_carry_several_outcomes(self, qa_run):
        # Physically representable today (no uniqueness constraint on run+paper) even
        # though the appraisal orchestrator writes one row per paper per run.
        run_id, papers = qa_run
        conn = get_db()
        try:
            _insert(conn, run_id, papers[0], rob="Low", assessed="Mortality")
            _insert(conn, run_id, papers[0], rob="High", assessed="Quality of life")
            recs = rob_records_for_run(conn, run_id,
                                       study_id_by_paper={papers[0]: "S1"})
        finally:
            conn.close()
        merged = pp.attach_rob([{"study_id": "S1"}], recs)[0]
        assert merged["rob_by_outcome"] == {"Mortality": "Low", "Quality of life": "High"}


def _mortality_studies():
    return [
        {"study_id": "S1", "study_type": "RCT",
         "outcomes": [{"name": "Mortality", "comparison": "d vs p", "timing": "12m",
                       "events_int": 50, "n_int": 500,
                       "events_ctrl": 100, "n_ctrl": 500}]},
        {"study_id": "S2", "study_type": "RCT",
         "outcomes": [{"name": "Mortality", "comparison": "d vs p", "timing": "12m",
                       "events_int": 40, "n_int": 400,
                       "events_ctrl": 80, "n_ctrl": 400}]},
    ]


class TestEndToEndIntoTheBody:
    def test_appraisal_labels_reach_the_pooled_study_records(self, qa_run):
        """The whole point: a RoB tool's judgement lands on the pooled record,
        per outcome, paired with that study's weight."""
        run_id, papers = qa_run
        conn = get_db()
        try:
            _insert(conn, run_id, papers[0], rob="Low", assessed="Mortality")
            _insert(conn, run_id, papers[1], rob="High", assessed="Mortality")
            recs = rob_records_for_run(
                conn, run_id, study_id_by_paper={papers[0]: "S1", papers[1]: "S2"})
        finally:
            conn.close()

        bodies = pp.pool_extractions(pp.attach_rob(_mortality_studies(), recs))
        pooled = bodies[0]["pooled"]["studies"]
        assert {s["study_id"]: s["rob"] for s in pooled} == {"S1": "Low", "S2": "High"}
        assert all(s["rob_source"] == "tool" for s in pooled)
        # and the label is paired with a real weight, which is what GRADE weights on
        assert all(s["weight_pct"] > 0 for s in pooled)

    def test_body_grades_end_to_end_without_a_rob_by_study_map(self, qa_run):
        from backend.evidence_synthesis.grade_prep import grade_bodies
        run_id, papers = qa_run
        conn = get_db()
        try:
            _insert(conn, run_id, papers[0], rob="Low", assessed="Mortality")
            _insert(conn, run_id, papers[1], rob="Low", assessed="Mortality")
            recs = rob_records_for_run(
                conn, run_id, study_id_by_paper={papers[0]: "S1", papers[1]: "S2"})
        finally:
            conn.close()
        bodies = pp.pool_extractions(pp.attach_rob(_mortality_studies(), recs))
        # require_rob defaults True; this only rates because the labels arrived.
        grade = grade_bodies(bodies)[0]["grade"]
        assert grade is not None
        rob = next(d for d in grade["domains"] if d["domain"] == "Risk of bias")
        assert rob["downgrade"] == 0

    def test_same_body_is_not_graded_without_the_appraisal_join(self):
        # The control for the test above: identical studies, no RoB records attached.
        from backend.evidence_synthesis.grade_prep import grade_bodies
        bodies = pp.pool_extractions(_mortality_studies())
        res = grade_bodies(bodies)[0]
        assert res["grade"] is None
        assert any("risk-of-bias" in w for w in res["warnings"])
