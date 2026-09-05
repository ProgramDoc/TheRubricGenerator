"""Benchmark runner for the production synthesis agents. References stay in the evaluator.

Jobs and immutable output snapshots live in PostgreSQL (SQLite for tests). One DB
lease limits the simulator to one active run across web processes. Expired work
is marked interrupted, never silently replayed against paid model APIs.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Cookie, Header, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import Field

from backend import benchmark as bm, synthesis as syn
from backend.evidence_synthesis import grade_agent

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
SIMULATOR_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS simulator_datasets (
 id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 title TEXT NOT NULL, revision TEXT NOT NULL, data_json TEXT NOT NULL,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS simulator_runs (
 id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 experiment_id TEXT NOT NULL, replicate INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued', dataset_json TEXT NOT NULL,
 dataset_revision TEXT NOT NULL, input_json TEXT NOT NULL, config_json TEXT NOT NULL,
 review_id INTEGER REFERENCES synthesis_reviews(id),
 output_json TEXT, metrics_json TEXT, error_message TEXT,
 credit_cost INTEGER NOT NULL DEFAULT 0,
 queued_epoch DOUBLE PRECISION NOT NULL, started_epoch DOUBLE PRECISION, completed_epoch DOUBLE PRECISION,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_simulator_runs_user ON simulator_runs(user_id, id);
CREATE INDEX IF NOT EXISTS idx_simulator_runs_queue ON simulator_runs(status, id);
CREATE TABLE IF NOT EXISTS simulator_worker_lease (
 id INTEGER PRIMARY KEY, owner TEXT, expires_epoch DOUBLE PRECISION NOT NULL DEFAULT 0
);
INSERT INTO simulator_worker_lease (id, expires_epoch) VALUES (1, 0) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS simulator_launch_requests (
 user_id INTEGER NOT NULL REFERENCES users(id), request_id TEXT NOT NULL,
 payload_hash TEXT NOT NULL, response_json TEXT,
 PRIMARY KEY (user_id, request_id)
);
"""
router = APIRouter()


def _main():
    import main
    return main


def _user(cookie, key, *, write=False):
    m = _main()
    u = m.require_user(cookie, key)
    m.require_active_seat(u, "engineer" if write else "general")
    return u


def seeds():
    data = json.loads((ROOT / "data/benchmarks/published-v1.json").read_text())
    data.update(json.loads((ROOT / "data/benchmarks/cochrane-v1.json").read_text()))
    return {f"seed:{key}": bm.Dataset.model_validate(value).model_dump() for key, value in data.items()}


def _dataset(conn, key, uid):
    if key.startswith("seed:"):
        data = seeds().get(key)
    else:
        try:
            ident = int(key.removeprefix("custom:"))
        except ValueError:
            raise HTTPException(404, "Benchmark not found")
        row = conn.execute("SELECT data_json FROM simulator_datasets WHERE id=? AND user_id=?", (ident, uid)).fetchone()
        data = json.loads(row["data_json"]) if row else None
    if data is None:
        raise HTTPException(404, "Benchmark not found")
    return data


def _owned_run(conn, ident, uid):
    r = conn.execute("SELECT * FROM simulator_runs WHERE id=? AND user_id=?", (ident, uid)).fetchone()
    if not r:
        raise HTTPException(404, "Simulator run not found")
    return dict(r)


def fingerprint():
    from backend.helpers import ANTHROPIC_MODEL
    paths = [ROOT / "backend/annotator.py", ROOT / "backend/helpers.py", ROOT / "backend/synthesis.py",
             ROOT / "backend/synthesis_stats.py", ROOT / "backend/quality_appraisal.py", ROOT / "backend/benchmark.py",
             ROOT / "backend/simulator.py", ROOT / "backend/simulator_grade.py", ROOT / "backend/indirectness.py"]
    paths += sorted((ROOT / "backend/rob_tools").glob("*.py"))
    paths += sorted((ROOT / "backend/evidence_synthesis").glob("*.py"))
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    return {"engine": "production-synthesis-plus-grade-v1", "model": ANTHROPIC_MODEL,
            "code_revision": os.environ.get("RENDER_GIT_COMMIT", "local"),
            "implementation_hash": bm.digest({"files": hashes, "configured_model": ANTHROPIC_MODEL}), "file_hashes": hashes,
            "evaluator_version": "1", "provider_seed_control": False,
            "grade_scope": "Production synthesis followed by the GRADE agent with automatic indirectness; original synthesis GRADE retained"}


class Binding(bm.StrictModel):
    paper_id: int = Field(gt=0)
    study_key: str = Field(default="", max_length=100)


class Launch(bm.StrictModel):
    request_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    dataset_id: str = Field(max_length=100)
    papers: list[Binding] = Field(min_length=1, max_length=100)
    repeats: int = Field(default=1, ge=1, le=5)
    max_credits: int = Field(ge=0)
    corpus_complete: bool = False
    primary_reports_only: bool = False


@router.get("/simulator", include_in_schema=False)
def simulator_page(rubricgen_session: str | None = Cookie(default=None)):
    _user(rubricgen_session, None)
    return FileResponse(str(ROOT / "frontend/simulator.html"), media_type="text/html")


@router.get("/api/simulator/datasets")
def datasets(rubricgen_session: str | None = Cookie(default=None), x_api_key: str | None = Header(default=None)):
    u = _user(rubricgen_session, x_api_key)
    conn = _main().get_db()
    try:
        data = seeds()
        for row in conn.execute("SELECT id, data_json FROM simulator_datasets WHERE user_id=? ORDER BY id DESC", (u["id"],)).fetchall():
            data[f"custom:{row['id']}"] = json.loads(row["data_json"])
        return [{"id": key, "revision": bm.digest(value), **value} for key, value in data.items()]
    finally:
        conn.close()


@router.post("/api/simulator/datasets", status_code=201)
def import_dataset(body: bm.Dataset, rubricgen_session: str | None = Cookie(default=None), x_api_key: str | None = Header(default=None)):
    u = _user(rubricgen_session, x_api_key, write=True)
    data = body.model_dump()
    conn = _main().get_db()
    try:
        cur = conn.execute("INSERT INTO simulator_datasets (user_id,title,revision,data_json) VALUES (?,?,?,?) RETURNING id",
                           (u["id"], body.title, bm.digest(data), json.dumps(data)))
        conn.commit()
        return {"id": f"custom:{cur.lastrowid}", "revision": bm.digest(data)}
    finally:
        conn.close()


def _validate_launch(conn, body, u):
    d = _dataset(conn, body.dataset_id, u["id"])
    ids = [p.paper_id for p in body.papers]
    if len(set(ids)) != len(ids):
        raise HTTPException(400, "Select each paper only once")
    if not body.primary_reports_only:
        raise HTTPException(400, "Confirm that only primary study reports will be sent to agents")
    rows = conn.execute("SELECT id,filename,sha256 FROM papers WHERE user_id=? AND id IN (" + ",".join("?" * len(ids)) + ")", (u["id"], *ids)).fetchall()
    by_id = {r["id"]: dict(r) for r in rows}
    if set(by_id) != set(ids):
        raise HTTPException(400, "One or more selected papers are unavailable")
    hashes = [r["sha256"] for r in rows if r["sha256"]]
    if len(set(hashes)) != len(hashes):
        raise HTTPException(400, "Duplicate PDF contents cannot be included as separate studies")
    keys = [p.study_key for p in body.papers if p.study_key]
    if len(keys) != len(set(keys)):
        raise HTTPException(400, "Multiple reports for one study must be reconciled before benchmarking")
    reference_keys = {s["key"] for s in d["studies"]}
    if set(keys) - reference_keys:
        raise HTTPException(400, "Paper mapping contains a study absent from the benchmark manifest")
    if d["curation"] == "adjudicated" and (set(keys) != reference_keys or not body.corpus_complete):
        raise HTTPException(400, "An adjudicated run requires the complete mapped benchmark study manifest")
    inputs = {"papers": [{**by_id[p.paper_id], "study_key": p.study_key} for p in body.papers],
              "corpus_complete": body.corpus_complete, "primary_reports_only": True}
    cost = (syn.estimate_cost(len(ids), len(d["outcomes"]), True)
            + len(d["outcomes"]) * grade_agent.CREDIT_COST_GRADE_INDIRECTNESS) * body.repeats
    return d, inputs, cost


@router.post("/api/simulator/estimate")
def estimate(body: Launch, rubricgen_session: str | None = Cookie(default=None), x_api_key: str | None = Header(default=None)):
    u = _user(rubricgen_session, x_api_key, write=True)
    conn = _main().get_db()
    try:
        _, _, cost = _validate_launch(conn, body, u)
        return {"credits": cost, "charged_credits": 0 if u.get("role") == "admin" else cost,
                "repeats": body.repeats, "worker_enabled": os.environ.get("SIMULATOR_WORKER_ENABLED", "1") == "1"}
    finally:
        conn.close()


@router.post("/api/simulator/runs", status_code=202)
def launch(body: Launch, rubricgen_session: str | None = Cookie(default=None), x_api_key: str | None = Header(default=None)):
    u = _user(rubricgen_session, x_api_key, write=True)
    conn = _main().get_db()
    try:
        if os.environ.get("SIMULATOR_WORKER_ENABLED", "1") != "1":
            raise HTTPException(503, "Simulator worker is disabled")
        payload_hash = bm.digest(body.model_dump(mode="json", exclude={"request_id"}))
        request_key = (u["id"], str(body.request_id))
        inserted = conn.execute("INSERT INTO simulator_launch_requests (user_id,request_id,payload_hash) VALUES (?,?,?) ON CONFLICT DO NOTHING",
                                (*request_key, payload_hash)).rowcount
        if not inserted:
            previous = conn.execute("SELECT payload_hash,response_json FROM simulator_launch_requests WHERE user_id=? AND request_id=?", request_key).fetchone()
            if previous["payload_hash"] != payload_hash:
                raise HTTPException(409, "This launch identifier was already used with different inputs")
            return json.loads(previous["response_json"])
        d, inputs, cost = _validate_launch(conn, body, u)
        if cost > body.max_credits:
            raise HTTPException(400, "Run exceeds the confirmed credit limit; request a fresh estimate")
        # Serialize launches for this user before checking the queue bound.
        conn.execute("INSERT INTO user_credits (user_id,balance) VALUES (?,0) ON CONFLICT DO NOTHING", (u["id"],))
        conn.execute("UPDATE user_credits SET balance=balance WHERE user_id=?", (u["id"],))
        active = conn.execute("SELECT COUNT(*) AS n FROM simulator_runs WHERE user_id=? AND status IN ('queued','running')", (u["id"],)).fetchone()["n"]
        if active + body.repeats > 10:
            raise HTTPException(409, "At most 10 queued or running simulator jobs per user")
        config = {**fingerprint(), "is_admin": u.get("role") == "admin"}
        # Queue creation and debit share one transaction, including retry deduplication.
        # The existing billing helper commits internally, so use an atomic balance guard here.
        if not config["is_admin"]:
            changed = conn.execute("UPDATE user_credits SET balance=balance-?,last_updated=CURRENT_TIMESTAMP WHERE user_id=? AND balance>=?",
                                   (cost, u["id"], cost)).rowcount
            if changed != 1:
                raise HTTPException(402, "Insufficient credits for the confirmed run")
            conn.execute("INSERT INTO credit_transactions (user_id,amount,type,description) VALUES (?,?,'test_charge',?)",
                         (u["id"], -cost, f"Simulator benchmark {body.request_id}"))
        experiment_id, ids = str(uuid.uuid4()), []
        for rep in range(1, body.repeats + 1):
            cur = conn.execute("""INSERT INTO simulator_runs
                (user_id,experiment_id,replicate,dataset_json,dataset_revision,input_json,config_json,credit_cost,queued_epoch)
                VALUES (?,?,?,?,?,?,?,?,?) RETURNING id""",
                (u["id"], experiment_id, rep, json.dumps(d), bm.digest(d), json.dumps(inputs),
                 json.dumps(config), cost // body.repeats, time.time()))
            ids.append(cur.lastrowid)
        response = {"experiment_id": experiment_id, "run_ids": ids, "credits": cost, "status": "queued"}
        conn.execute("UPDATE simulator_launch_requests SET response_json=? WHERE user_id=? AND request_id=?",
                     (json.dumps(response), *request_key))
        conn.commit()
        return response
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@router.get("/api/simulator/runs")
def runs(rubricgen_session: str | None = Cookie(default=None), x_api_key: str | None = Header(default=None)):
    u = _user(rubricgen_session, x_api_key)
    conn = _main().get_db()
    try:
        rows = conn.execute("""SELECT id, experiment_id, replicate, status, dataset_json, dataset_revision,
            config_json, metrics_json, error_message, review_id, credit_cost, queued_epoch, started_epoch, completed_epoch
            FROM simulator_runs WHERE user_id=? ORDER BY id DESC LIMIT 200""", (u["id"],)).fetchall()
        out = []
        for r in rows:
            v = dict(r)
            d = json.loads(v.pop("dataset_json"))
            cfg = json.loads(v.pop("config_json"))
            v.update(title=d["title"], topic=d["topic"], split=d["split"], curation=d["curation"],
                     target_outcomes=len(d["outcomes"]), grade_targets=sum(bool(o["target"].get("grade")) for o in d["outcomes"]),
                     metrics=json.loads(v.pop("metrics_json") or "null"), model=cfg["model"],
                     implementation_hash=cfg["implementation_hash"], code_revision=cfg["code_revision"])
            out.append(v)
        return out
    finally:
        conn.close()


@router.get("/api/simulator/runs/{run_id}")
def detail(run_id: int, rubricgen_session: str | None = Cookie(default=None), x_api_key: str | None = Header(default=None)):
    u = _user(rubricgen_session, x_api_key)
    conn = _main().get_db()
    try:
        r = _owned_run(conn, run_id, u["id"])
        for key in ("dataset", "input", "config", "output", "metrics"):
            r[key] = json.loads(r.pop(key + "_json") or "null")
        if r["review_id"]:
            r["events"] = [dict(x) for x in conn.execute("SELECT id,event_type,message,created_at FROM synthesis_events WHERE review_id=? ORDER BY id DESC LIMIT 100", (r["review_id"],)).fetchall()][::-1]
        else:
            r["events"] = []
        return r
    finally:
        conn.close()


@router.get("/api/simulator/runs/{run_id}/export")
def export(run_id: int, rubricgen_session: str | None = Cookie(default=None), x_api_key: str | None = Header(default=None)):
    d = detail(run_id, rubricgen_session, x_api_key)
    return Response(json.dumps(d, indent=2, default=str), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="simulator-run-{run_id}.json"'})


def _create_review(conn, job, dataset, inputs):
    protocol = bm.agent_protocol(dataset)
    ids = [p["id"] for p in inputs["papers"]]
    cur = conn.execute("""INSERT INTO synthesis_reviews
        (user_id,title,paper_ids_json,paper_count,status,pico_json,run_rob,rob_scope,credit_cost)
        VALUES (?,?,?,?,'pending',?,1,'outcome',?) RETURNING id""",
        (job["user_id"], f"Simulator run {job['id']}", json.dumps(ids), len(ids), json.dumps(protocol["pico"]), job["credit_cost"]))
    rid = cur.lastrowid
    for i, oc in enumerate(protocol["outcomes"]):
        conn.execute("""INSERT INTO synthesis_outcomes
            (review_id,name,outcome_type,effect_measure,model_choice,tau2_method,fe_method,re_ci_method,continuity_correction,sort_order,mid_benefit,mid_harm)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (rid, oc["name"], oc["outcome_type"], oc["effect_measure"], oc["model_choice"],
                                             oc["tau2_method"], oc["fe_method"], oc["re_ci_method"], oc["continuity_correction"], i,
                                             oc["mid_benefit"], oc["mid_harm"]))
    conn.execute("UPDATE simulator_runs SET review_id=? WHERE id=?", (rid, job["id"]))
    conn.commit()
    return rid


def capture(conn, rid, dataset, inputs, *, grade_results=None, snapshot=None):
    """Capture agent outputs BEFORE scoring; all lookups join stable DB identifiers."""
    m = _main()
    snapshot = snapshot or m._synth_detail(conn, rid)  # ordinary synthesis output, no reference data
    refs = dataset["outcomes"]
    predictions = []
    flags = []
    if not inputs["corpus_complete"]:
        flags.append("Uploaded corpus is not declared complete; pooled comparison is exploratory")
    if dataset["curation"] != "adjudicated":
        flags.append("Reference targets are published; study-level benchmark has not been independently adjudicated")
    # Known limitation of the exact production path being evaluated; do not silently fix its output.
    flags.append("Original synthesis reports indirectness as unassessed; the final benchmark uses the separate GRADE agent")
    if any(s.get("decision_overridden") for s in snapshot.get("studies", [])) or any(p.get("edited_by_user") for p in snapshot.get("data_points", [])):
        flags.append("Review inputs were edited during execution; this is an assisted run")
    outcomes = sorted(snapshot["outcomes"], key=lambda o: (o.get("sort_order", 0), o["id"]))
    results = {r["outcome_id"]: r for r in snapshot["results"]}
    robs = {(r["study_id"], r["outcome_id"]): r for r in snapshot.get("study_rob", [])}
    points = snapshot.get("data_points", [])
    for index, oc in enumerate(outcomes):
        row = results.get(oc["id"], {})
        pool = row.get("random" if oc["model_choice"] == "random" else "fixed") or {}
        pred = None
        if row.get("status") == "ok" and pool.get("status") == "ok":
            measure = oc["effect_measure"]
            pred = {k: syn.stats.back_transform(pool[k], measure) for k in ("estimate", "ci_low", "ci_high")}
            pts = [p for p in points if p["outcome_id"] == oc["id"] and p.get("included_in_pool") and p.get("yi") is not None and p.get("vi")]
            unique = {p["study_id"] for p in pts}
            assessed = sum(robs.get((sid, oc["id"]), {}).get("status") == "ok" for sid in unique)
            if len(pts) != len(unique):
                flags.append(f"{oc['name']}: multiple pooled rows from one study; independence needs review")
            if assessed < len(unique):
                flags.append(f"{oc['name']}: only {assessed}/{len(unique)} pooled studies have successful RoB assessments")
            pred.update(grade=row.get("grade_certainty"), k=len(unique), data_points=len(pts),
                        i2=(row.get("heterogeneity") or {}).get("I2"), rob_assessed=assessed,
                        domains={d["domain"]: d.get("downgrade") for d in (row.get("grade") or {}).get("domains", [])})
        gd = (grade_results or {}).get(oc["id"], {})
        if pred is not None and grade_results is not None:
            final_grade = gd.get("grade") or {}
            pred.update(legacy_grade=pred["grade"], grade=final_grade.get("final"),
                        domains={d["domain"]: d.get("downgrade") for d in final_grade.get("domains", [])})
            flags.extend(f"{oc['name']}: {warning}" for warning in gd.get("warnings", []))
        if index < len(refs):
            predictions.append(bm.score_outcome(refs[index], pred))
    # All requested outcomes belong in the denominator, including absent rows.
    predictions.extend(bm.score_outcome(ref, None) for ref in refs[len(outcomes):])
    stage = stage_metrics(dataset, inputs, snapshot)
    return {"synthesis": snapshot, "grade_agent": grade_results or {}, "scores": predictions,
            "flags": sorted(set(flags)), "stages": stage}


def stage_metrics(dataset, inputs, snapshot):
    bindings = {p["id"]: p["study_key"] for p in inputs["papers"] if p.get("study_key")}
    expected = {s["key"]: s for s in dataset["studies"]}
    actual = {r["paper_id"]: r for r in snapshot["studies"]}
    classification_n = classification_ok = included_gold = included_found = tp = fp = 0
    screening_n = screening_ok = 0
    extraction_n = extraction_ok = rob_n = rob_ok = 0
    outcome_ids = {ref["key"]: oc["id"] for ref, oc in zip(dataset["outcomes"], snapshot["outcomes"])}
    points = snapshot.get("data_points", [])
    robs = {(r["study_id"], r["outcome_id"]): r for r in snapshot.get("study_rob", [])}
    for pid, key in bindings.items():
        ref, pred = expected[key], actual.get(pid, {})
        if ref.get("study_type"):
            classification_n += 1
            classification_ok += pred.get("study_type") == ref["study_type"]
        want = ref["expected_included"]
        got = pred.get("screening_decision") == "include"
        screening_n += 1
        screening_ok += pred.get("screening_decision") == ("include" if want else "exclude")
        included_gold += want
        included_found += got
        tp += want and got
        fp += not want and got
        for key, target in ref.get("outcomes", {}).items():
            oid = outcome_ids.get(key)
            matches = [p for p in points if p["study_id"] == pred.get("id") and p["outcome_id"] == oid]
            # Ambiguous context/timepoint extraction is not silently matched to a convenient row.
            raw = matches[0].get("raw", {}) if len(matches) == 1 else {}
            for field, value in target.get("raw", {}).items():
                extraction_n += 1
                try:
                    extraction_ok += abs(float(raw[field])-value) <= max(1e-6, abs(value)*1e-4)
                except (KeyError, TypeError, ValueError):
                    pass
            if target.get("rob"):
                rob_n += 1
                rob_ok += robs.get((pred.get("id"), oid), {}).get("rob_overall") == target["rob"]
    return {"classification_targets": classification_n, "classification_matches": classification_ok,
            "screening_targets": screening_n, "screening_matches": screening_ok,
            "screening_precision": tp/included_found if included_found else None,
            "screening_recall": tp/included_gold if included_gold else None,
            "mapped_studies": len(bindings), "reference_studies": len(expected),
            "extraction_targets": extraction_n, "extraction_matches": extraction_ok,
            "rob_targets": rob_n, "rob_matches": rob_ok,
            "agent_errors": sum(s.get("status") == "error" for s in actual.values())}


def process_job(get_db, papers_dir, job):
    dataset, inputs, cfg = (json.loads(job[k]) for k in ("dataset_json", "input_json", "config_json"))
    conn = get_db()
    try:
        current = fingerprint()
        if current["implementation_hash"] != cfg["implementation_hash"] or current["model"] != cfg["model"]:
            raise ValueError("Code changed while queued. Start a new run to record the new implementation.")
        for p in inputs["papers"]:
            row = conn.execute("SELECT sha256 FROM papers WHERE id=? AND user_id=?", (p["id"], job["user_id"])).fetchone()
            if not row or row["sha256"] != p["sha256"]:
                raise ValueError("An input paper changed after launch")
        rid = _create_review(conn, job, dataset, inputs)
    finally:
        conn.close()
    from backend.simulator_grade import collect_context, run_grade
    contexts = {}
    syn.run_synthesis(get_db, papers_dir, job["user_id"], cfg["is_admin"], rid,
                      on_study_fields=lambda sid, fields: contexts.update({sid: collect_context(fields)}))
    conn = get_db()
    try:
        snapshot = _main()._synth_detail(conn, rid)
        verify_unedited(snapshot, bm.agent_protocol(dataset))
        syn.log_event(conn, rid, "progress", "Running the GRADE agent with body-level indirectness")
        grade_results = run_grade(conn, snapshot, contexts, job["user_id"], cfg["is_admin"], bm.agent_protocol(dataset))
        for p in inputs["papers"]:
            row = conn.execute("SELECT sha256 FROM papers WHERE id=? AND user_id=?", (p["id"], job["user_id"])).fetchone()
            if not row or row["sha256"] != p["sha256"]:
                raise ValueError("An input paper changed during execution; results cannot be benchmarked")
        output = capture(conn, rid, dataset, inputs, grade_results=grade_results, snapshot=snapshot)
        metrics = bm.summarize(output["scores"])
        metrics["outcome_results"] = [{"key": s["key"], "name": s["name"],
                                        "estimate": (s.get("prediction") or {}).get("estimate") if s["status"] == "compared" else None,
                                        "grade": (s.get("prediction") or {}).get("grade")}
                                       for s in output["scores"]]
        all_graded = all((r.get("grade") or {}).get("final") and r.get("indirectness_detail") for r in grade_results.values())
        status = "complete" if metrics["compared"] == metrics["outcomes"] and all_graded else "partial"
        conn.execute("UPDATE simulator_runs SET status=?,output_json=?,metrics_json=?,completed_epoch=? WHERE id=? AND status='running'",
                     (status, json.dumps(json_safe(output), default=str, allow_nan=False), json.dumps(metrics), time.time(), job["id"]))
        conn.commit()
    finally:
        conn.close()


def verify_unedited(snapshot, protocol):
    if any(s.get("decision_overridden") for s in snapshot["studies"]) or any(p.get("edited_by_user") for p in snapshot["data_points"]):
        raise ValueError("Review was edited during execution; launch a fresh unassisted benchmark")
    if snapshot["pico"] != protocol["pico"] or len(snapshot["outcomes"]) != len(protocol["outcomes"]):
        raise ValueError("Review protocol changed during execution")
    for actual, expected in zip(snapshot["outcomes"], protocol["outcomes"]):
        for key in ("name", "outcome_type", "effect_measure", "model_choice", "tau2_method", "fe_method", "re_ci_method"):
            if actual.get(key) != expected[key]:
                raise ValueError("Review protocol changed during execution")
        for key in ("continuity_correction", "mid_benefit", "mid_harm"):
            val = actual.get(key)
            if (float(val) if val is not None else None) != expected[key]:
                raise ValueError("Review protocol changed during execution")


def fail_job(conn, job):
    """Refund an unstarted run atomically; preserve partial execution for review."""
    row = conn.execute("SELECT review_id,status FROM simulator_runs WHERE id=?", (job["id"],)).fetchone()
    if not row or row["status"] != "running":
        return
    unstarted = row["review_id"] is None
    text = "Simulation failed; review the stage events and server logs. No automatic retry was performed."
    if unstarted:
        text = "Simulation could not start because its inputs or implementation changed. No models were called; charged credits were refunded."
    changed = conn.execute("UPDATE simulator_runs SET status='failed',error_message=?,completed_epoch=? WHERE id=? AND status='running'",
                           (text, time.time(), job["id"])).rowcount
    if changed and unstarted and not json.loads(job["config_json"])["is_admin"]:
        conn.execute("UPDATE user_credits SET balance=balance+?,last_updated=CURRENT_TIMESTAMP WHERE user_id=?",
                     (job["credit_cost"], job["user_id"]))
        conn.execute("INSERT INTO credit_transactions (user_id,amount,type,description) VALUES (?,?,'refund',?)",
                     (job["user_id"], job["credit_cost"], f"Simulator run {job['id']}: failed before execution"))
    conn.commit()


def json_safe(value):
    """Statistical diagnostics may contain NaN when inestimable; export them as null."""
    import math
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def claim_job(conn, owner):
    now = time.time()
    cur = conn.execute("UPDATE simulator_worker_lease SET owner=?,expires_epoch=? WHERE id=1 AND expires_epoch<?", (owner, now+120, now))
    conn.commit()
    if cur.rowcount != 1:
        return None
    # A previous owner died. Never replay that run and incur hidden extra model costs.
    conn.execute("UPDATE simulator_runs SET status='interrupted',error_message=?,completed_epoch=? WHERE status='running'",
                 ("Worker interrupted. Partial synthesis output is retained; rerun explicitly. Review credits before retrying.", now))
    row = conn.execute("SELECT * FROM simulator_runs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
    if not row:
        conn.execute("UPDATE simulator_worker_lease SET expires_epoch=0 WHERE id=1 AND owner=?", (owner,))
        conn.commit()
        return None
    job = dict(row)
    conn.execute("UPDATE simulator_runs SET status='running',started_epoch=? WHERE id=? AND status='queued'", (now, job["id"]))
    conn.commit()
    return job


def start_worker(get_db, papers_dir):
    stop = threading.Event()
    if os.environ.get("SIMULATOR_WORKER_ENABLED", "1") != "1":
        return stop
    owner = str(uuid.uuid4())

    def heartbeat(done):
        while not done.wait(20):
            try:
                c = get_db()
                try:
                    c.execute("UPDATE simulator_worker_lease SET expires_epoch=? WHERE id=1 AND owner=?", (time.time()+120, owner))
                    c.commit()
                finally:
                    c.close()
            except Exception:
                logger.exception("Simulator heartbeat failed")

    def loop():
        while not stop.is_set():
            job = None
            done = threading.Event()
            try:
                c = get_db()
                try:
                    job = claim_job(c, owner)
                finally:
                    c.close()
                if job:
                    thread = threading.Thread(target=heartbeat, args=(done,), daemon=True)
                    thread.start()
                    process_job(get_db, papers_dir, job)
            except Exception:
                logger.exception("Simulator job failed")
                if job:
                    c = get_db()
                    try:
                        fail_job(c, job)
                    finally:
                        c.close()
            finally:
                done.set()
                if job:
                    c = get_db()
                    try:
                        c.execute("UPDATE simulator_worker_lease SET expires_epoch=0 WHERE id=1 AND owner=?", (owner,))
                        c.commit()
                    finally:
                        c.close()
            stop.wait(3)
    threading.Thread(target=loop, name="simulator-worker", daemon=True).start()
    return stop
