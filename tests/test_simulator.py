"""No live model calls. Exercise production orchestration with mocked agent boundaries."""
import copy
import json
import math
import time

import pytest
from pydantic import ValidationError

from backend import benchmark as bm, simulator as sim
from tests.test_synthesis_api import mock_llm, _mk_papers, _admin_id


@pytest.fixture(autouse=True)
def mock_grade_model(monkeypatch):
    from backend.evidence_synthesis import grade_indirectness
    calls = []
    def assess(target, context):
        calls.append((target, context))
        return ({"population": {"judgement": "direct"}}, "not serious", 0, "Test assessment: direct")
    monkeypatch.setattr(grade_indirectness, "assess_body", assess)
    return calls


def reference():
    return {"title": "Test fixture — not published evidence", "version": "1", "topic": "test",
            "citation": "Synthetic integration-test fixture", "source_url": "https://example.org/reference",
            "pico": {"population": "Adults", "intervention": "Drug", "comparator": "Placebo"},
            "outcomes": [{"key": "pain", "name": "pain", "outcome_type": "continuous", "effect_measure": "MD",
                          "source_locator": "Fixture only", "target": {"estimate": -1, "ci_low": -2, "ci_high": -0.5, "grade": "Moderate"}}]}


def test_seed_sources_and_schema():
    data = sim.seeds()
    assert len(data) == 3
    assert sum(len(d["outcomes"]) for d in data.values()) == 6
    assert all(d["curation"] == "published_targets" for d in data.values())
    schema = json.loads((sim.ROOT / "frontend/simulator-reference-schema.json").read_text())
    assert schema == bm.Dataset.model_json_schema()


def test_gold_never_passed_to_agents():
    data = reference()
    data["citation"] = "GOLD_SECRET_CITATION"
    data["outcomes"][0]["target"]["grade"] = "Very low"
    protocol = bm.agent_protocol(data)
    text = json.dumps(protocol)
    for forbidden in ("target", "GOLD_SECRET", "Very low", "ci_low", "tolerance", "source_locator", "method_note"):
        assert forbidden not in text
    assert protocol["outcomes"][0]["name"] == "pain"


@pytest.mark.parametrize("bad", [float('nan'), float('inf'), -float('inf')])
def test_nonfinite_reference_rejected(bad):
    data = reference()
    data["outcomes"][0]["target"]["estimate"] = bad
    with pytest.raises(ValidationError):
        bm.Dataset.model_validate(data)


def test_reference_validation():
    data = reference()
    data["outcomes"][0]["target"]["ci_low"] = 3
    with pytest.raises(ValidationError):
        bm.Dataset.model_validate(data)
    data = reference()
    data["curation"] = "adjudicated"
    with pytest.raises(ValidationError):
        bm.Dataset.model_validate(data)


def test_ratio_scoring_uses_log_scale_and_counts_missing():
    oc = sim.seeds()["seed:probiotics-aad-2021"]["outcomes"][0]
    pred = {**oc["target"], "grade": "High"}
    s = bm.score_outcome(oc, pred)
    assert s["effect_match"] and s["ci_match"]
    assert s["overconfident"] and s["grade_distance"] == 1
    pred["estimate"] *= 1.1
    s2 = bm.score_outcome(oc, pred)
    assert not s2["effect_match"]
    assert s2["analysis_error"] == pytest.approx(math.log(1.1))
    summary = bm.summarize([s, bm.score_outcome(oc, None)])
    assert summary["outcomes"] == summary["grade_targets"] == 2
    assert summary["effect_matches"] == 1 and summary["unrated"] == 1


def test_degenerate_kappa_is_not_perfect_agreement():
    oc = reference()["outcomes"][0]
    s = bm.score_outcome(oc, {**oc["target"]})
    assert bm.summarize([s])["quadratic_weighted_kappa"] is None


def test_auth_required(client):
    for path in ("/api/simulator/datasets", "/api/simulator/runs", "/api/simulator/runs/1", "/simulator"):
        assert client.get(path).status_code in (401, 403)


def _launch(client, admin_user, monkeypatch, n=2, repeats=1, dataset=None):
    headers = {"Cookie": f"rubricgen_session={admin_user['cookie']}"}
    response = client.post('/api/simulator/datasets', json=dataset or reference(), headers=headers)
    assert response.status_code == 201, response.text
    ids = _mk_papers(n, _admin_id())
    body = {"dataset_id": response.json()["id"], "papers": [{"paper_id": p} for p in ids],
            "repeats": repeats, "primary_reports_only": True, "max_credits": 100000}
    # Startup has already run with worker disabled; manually drive the queue in this test.
    monkeypatch.setenv("SIMULATOR_WORKER_ENABLED", "1")
    response = client.post('/api/simulator/runs', json=body, headers=headers)
    assert response.status_code == 202, response.text
    return headers, body, response.json()["run_ids"]


def test_pipeline_snapshot_and_ownership(client, admin_user, test_user, monkeypatch, mock_llm, mock_grade_model):
    import main
    headers, body, ids = _launch(client, admin_user, monkeypatch)
    conn = main.get_db()
    job = sim.claim_job(conn, 'test-worker')
    conn.close()
    sim.process_job(main.get_db, main.PAPERS_DIR, job)
    r = client.get(f'/api/simulator/runs/{ids[0]}', headers=headers)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "complete"
    assert d["metrics"]["compared"] == 1
    pred = d["output"]["scores"][0]["prediction"]
    assert -1.51 < pred["estimate"] < -1.39
    assert len(d["output"]["synthesis"]["studies"]) == 2
    assert len(mock_llm._test_rob_calls) == 2
    assert len(mock_grade_model) == 1
    assert mock_grade_model[0][0]["outcome"] == "pain"
    assert "target" not in json.dumps(mock_grade_model[0][1])
    assert pred["grade"] in bm.GRADES
    assert d["output"]["grade_agent"]
    assert d["config"]["implementation_hash"]
    # Later review edits cannot alter the frozen simulator score or export.
    conn = main.get_db()
    conn.execute("UPDATE synthesis_results SET grade_certainty='Very low' WHERE review_id=?", (d["review_id"],))
    conn.commit()
    conn.close()
    assert client.get(f'/api/simulator/runs/{ids[0]}', headers=headers).json()["output"] == d["output"]
    other = {"Cookie": f"rubricgen_session={test_user['cookie']}"}
    assert client.get(f'/api/simulator/runs/{ids[0]}', headers=other).status_code == 404
    assert client.get(f'/api/simulator/runs/{ids[0]}/export', headers=other).status_code == 404
    assert client.get('/api/simulator/runs', headers=other).json() == []


def test_duplicate_papers_budget_and_worker_gate(client, admin_user, monkeypatch):
    headers, body, ids = _launch(client, admin_user, monkeypatch, n=1)
    bad = copy.deepcopy(body)
    bad["papers"] *= 2
    assert client.post('/api/simulator/runs', json=bad, headers=headers).status_code == 400
    bad = {**body, "max_credits": 0}
    assert client.post('/api/simulator/runs', json=bad, headers=headers).status_code == 400
    bad = {**body, "primary_reports_only": False}
    assert client.post('/api/simulator/runs', json=bad, headers=headers).status_code == 400
    monkeypatch.setenv("SIMULATOR_WORKER_ENABLED", "0")
    assert client.post('/api/simulator/runs', json=body, headers=headers).status_code == 503


def test_queue_lease_and_restart_do_not_replay(client, admin_user, monkeypatch):
    import main
    _, _, ids = _launch(client, admin_user, monkeypatch, n=1, repeats=3)
    conn = main.get_db()
    first = sim.claim_job(conn, 'one')
    assert first["id"] == ids[0]
    assert sim.claim_job(conn, 'two') is None
    conn.execute("UPDATE simulator_worker_lease SET expires_epoch=0")
    conn.commit()
    second = sim.claim_job(conn, 'two')
    assert second["id"] == ids[1]
    assert conn.execute("SELECT status FROM simulator_runs WHERE id=?", (ids[0],)).fetchone()["status"] == 'interrupted'
    conn.close()


def test_missing_extractions_remain_in_denominator(client, admin_user, monkeypatch, mock_llm):
    import main
    headers, _, ids = _launch(client, admin_user, monkeypatch, n=1)
    monkeypatch.setattr(mock_llm, 'extract_outcome_data', lambda *a, **k: [])
    conn = main.get_db()
    job = sim.claim_job(conn, 'worker')
    conn.close()
    sim.process_job(main.get_db, main.PAPERS_DIR, job)
    d = client.get(f'/api/simulator/runs/{ids[0]}', headers=headers).json()
    assert d['status'] == 'partial'
    assert d['metrics']['outcomes'] == 1 and d['metrics']['effect_matches'] == 0


def test_stage_metrics_do_not_match_ambiguous_extraction():
    d = reference()
    d['studies'] = [{'key': 'trial', 'expected_included': True, 'study_type': 'RCT',
                     'outcomes': {'pain': {'raw': {'n1': 50}, 'rob': 'Low'}}}]
    inputs = {'papers': [{'id': 1, 'study_key': 'trial'}]}
    snap = {'studies': [{'id': 10, 'paper_id': 1, 'study_type': 'RCT', 'screening_decision': 'include'}],
            'outcomes': [{'id': 20}], 'study_rob': [{'study_id': 10, 'outcome_id': 20, 'rob_overall': 'Low'}],
            'data_points': [{'study_id': 10, 'outcome_id': 20, 'raw': {'n1': 50}}]*2}
    s = sim.stage_metrics(d, inputs, snap)
    assert s['extraction_targets'] == 1 and s['extraction_matches'] == 0
    assert s['rob_matches'] == 1


def test_changed_input_rejected_before_model_calls(client, admin_user, monkeypatch, mock_llm):
    import main
    _, body, _ = _launch(client, admin_user, monkeypatch, n=1)
    conn = main.get_db()
    job = sim.claim_job(conn, 'worker')
    conn.execute("UPDATE papers SET sha256='changed' WHERE id=?", (body['papers'][0]['paper_id'],))
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match='changed'):
        sim.process_job(main.get_db, main.PAPERS_DIR, job)
    assert not mock_llm._test_rob_calls


def test_paid_launch_retry_is_atomic_and_unstarted_refund_once(client, test_user, monkeypatch):
    import main
    import uuid
    headers = {'Cookie': f"rubricgen_session={test_user['cookie']}"}
    conn = main.get_db()
    uid = conn.execute('SELECT id FROM users WHERE email=?', (test_user['email'],)).fetchone()['id']
    conn.execute('INSERT INTO user_credits (user_id,balance) VALUES (?,10000) ON CONFLICT (user_id) DO UPDATE SET balance=10000', (uid,))
    conn.commit()
    conn.close()
    ds = client.post('/api/simulator/datasets', json=reference(), headers=headers).json()['id']
    pid = _mk_papers(1, uid)[0]
    body = {'request_id': str(uuid.uuid4()), 'dataset_id': ds, 'papers': [{'paper_id': pid}],
            'primary_reports_only': True, 'max_credits': 10000}
    monkeypatch.setenv('SIMULATOR_WORKER_ENABLED', '1')
    first = client.post('/api/simulator/runs', json=body, headers=headers)
    assert first.status_code == 202, first.text
    assert client.post('/api/simulator/runs', json=body, headers=headers).json() == first.json()
    assert client.post('/api/simulator/runs', json={**body, 'repeats': 2}, headers=headers).status_code == 409
    conn = main.get_db()
    assert main.bill.get_balance(conn, uid) == 10000-first.json()['credits']
    job = sim.claim_job(conn, 'refund-test')
    sim.fail_job(conn, job)
    sim.fail_job(conn, job)
    assert main.bill.get_balance(conn, uid) == 10000
    assert conn.execute('SELECT COUNT(*) AS n FROM simulator_runs WHERE user_id=?', (uid,)).fetchone()['n'] == 1
    conn.close()


def test_grade_failure_is_partial_and_visible(client, admin_user, monkeypatch, mock_llm):
    import main
    from backend.evidence_synthesis import grade_indirectness
    def fail(*args, **kwargs):
        raise RuntimeError('Injected unavailable assessment')
    monkeypatch.setattr(grade_indirectness, 'assess_body', fail)
    headers, _, ids = _launch(client, admin_user, monkeypatch)
    conn = main.get_db()
    job = sim.claim_job(conn, 'failure-test')
    conn.close()
    sim.process_job(main.get_db, main.PAPERS_DIR, job)
    result = client.get(f'/api/simulator/runs/{ids[0]}', headers=headers).json()
    assert result['status'] == 'partial'
    assert result['metrics']['compared'] == 1
    assert any('auto-assessment failed' in f for f in result['output']['flags'])


@pytest.mark.parametrize('bad', ['invalid', float('nan'), float('inf'), True])
def test_invalid_predictions_do_not_crash_or_improve_metrics(bad):
    oc = reference()['outcomes'][0]
    score = bm.score_outcome(oc, {**oc['target'], 'estimate': bad})
    assert score['status'] == 'invalid'
    metrics = bm.summarize([score])
    assert metrics['effect_matches'] == metrics['grade_matches'] == metrics['kappa_pairs'] == 0


def test_grade_adapter_preserves_scales_weights_and_binary_counts():
    from backend.simulator_grade import prepare_body
    oc = {'id': 10, 'name': 'Mortality', 'effect_measure': 'RR', 'model_choice': 'random'}
    snap = {'results': [{'outcome_id': 10, 'status': 'ok',
                         'random': {'status': 'ok', 'estimate': math.log(.63), 'ci_low': math.log(.54),
                                    'ci_high': math.log(.73), 'weights_pct': [20, 80]},
                         'heterogeneity': {'I2': 55, 'p': .03}}],
            'studies': [{'id': i, 'study_type': 'Randomized Controlled Trial'} for i in (1, 2)],
            'study_rob': [{'study_id': i, 'outcome_id': 10, 'rob_tool': 'rob2', 'rob_overall': r, 'status': 'ok'}
                          for i, r in ((1, 'High'), (2, 'Low'))],
            'data_points': [{'study_id': i, 'outcome_id': 10, 'included_in_pool': 1, 'yi': -.5, 'vi': .1,
                             'raw': {'events1': 10, 'total1': 100, 'events2': 20, 'total2': 100}}
                            for i in (1, 2)]}
    body, _ = prepare_body(snap, oc, {})
    pr = body['pooled']
    assert pr['pooled']['estimate'] == pytest.approx(.63)
    assert pr['pooled']['ci_lower'] == pytest.approx(.54)
    assert [s['weight_pct'] for s in pr['studies']] == [20, 80]
    assert [s['rob'] for s in pr['studies']] == ['High', 'Low']
    assert pr['totals'] == {'n_int': 200, 'n_ctrl': 200, 'events_int': 20, 'events_ctrl': 40}
    assert pr['heterogeneity']['i2'] == 55
    snap['data_points'].append(snap['data_points'][0])
    assert prepare_body(snap, oc, {})[0] is None


def test_manual_review_changes_are_rejected():
    protocol = bm.agent_protocol(reference())
    snap = {'studies': [], 'data_points': [], 'pico': protocol['pico'], 'outcomes': copy.deepcopy(protocol['outcomes'])}
    sim.verify_unedited(snap, protocol)
    snap['outcomes'][0]['model_choice'] = 'fixed'
    with pytest.raises(ValueError, match='protocol changed'):
        sim.verify_unedited(snap, protocol)
