"""GRADE agent — HTTP glue + persistence.

The thin layer between the FastAPI routes in ``main.py`` and the pure GRADE
engine. Two responsibilities, kept out of ``main.py`` (which stays a router):

1. **Stateless composition** — ``grade_certainty(payload)`` / ``sof(payload)``:
   accept a request body carrying either a precomputed ``pool_result`` OR raw
   ``studies`` (+ ``measure``), plus the RoB labels + human judgments, and return
   the GRADE record (and, for ``sof``, the Summary-of-Findings row). Mirrors the
   driscoll ``synthesis_agents`` glue, rewired to ``backend.synthesis.grade``.

2. **Persistence** — ``GRADE_TABLES_SQL`` (grade_runs / grade_results /
   grade_events) + the CRUD helpers a run needs. Follows this repo's
   ``quality_appraisal_runs/results/events`` idiom: PostgreSQL DDL, ``?``
   placeholders, ``created_at``/``completed_at`` (never a column named
   ``timestamp``), soft-delete via ``deleted_at``.

The only model call is the optional indirectness auto-assessment, which lives in
``grade_indirectness`` and is triggered by ``grade_assess`` — this module never
calls a model directly.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .grade import grade_body, sof_row
from .pooling import pool_outcome

# Credit cost for a run's optional LLM indirectness auto-assessment, per body that
# actually needs it (reviewer-supplied indirectness is free). Admins bypass.
CREDIT_COST_GRADE_INDIRECTNESS = 2


GRADE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS grade_runs (
    id                 SERIAL PRIMARY KEY,
    user_id            INTEGER NOT NULL,
    project_id         INTEGER,
    name               TEXT,
    target_pico_json   TEXT,
    auto_indirectness  INTEGER NOT NULL DEFAULT 1,
    status             TEXT NOT NULL DEFAULT 'pending',
    n_bodies           INTEGER NOT NULL DEFAULT 0,
    error_message      TEXT,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at       TIMESTAMP,
    deleted_at         TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_grade_runs_user ON grade_runs(user_id);

CREATE TABLE IF NOT EXISTS grade_results (
    id                    SERIAL PRIMARY KEY,
    run_id                INTEGER NOT NULL REFERENCES grade_runs(id) ON DELETE CASCADE,
    outcome_name          TEXT,
    comparison            TEXT,
    timepoint             TEXT,
    design_class          TEXT,
    measure               TEXT,
    k_studies             INTEGER,
    certainty             TEXT,
    initial_certainty     TEXT,
    total_downgrade       INTEGER,
    total_upgrade         INTEGER,
    grade_json            TEXT NOT NULL DEFAULT '{}',
    sof_json              TEXT NOT NULL DEFAULT '{}',
    absolute_effects_json TEXT,
    indirectness_json     TEXT,
    explanation           TEXT,
    error_message         TEXT,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_grade_results_run ON grade_results(run_id);

CREATE TABLE IF NOT EXISTS grade_events (
    id          SERIAL PRIMARY KEY,
    run_id      INTEGER NOT NULL REFERENCES grade_runs(id) ON DELETE CASCADE,
    event_type  TEXT NOT NULL,
    message     TEXT NOT NULL,
    detail_json TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_grade_events_run ON grade_events(run_id);
"""


# ---------------------------------------------------------------------------
# Stateless composition (POST /api/agents/grade, /api/agents/grade-sof)
# ---------------------------------------------------------------------------

def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _pool_result_from_payload(payload: dict) -> dict:
    """Return a pool_outcome result — passed through or computed from raw studies."""
    pr = payload.get("pool_result")
    if pr and isinstance(pr, dict) and pr.get("pooled"):
        return pr
    studies = payload.get("studies")
    measure = payload.get("measure") or (pr or {}).get("measure")
    if not studies or not measure:
        raise ValueError("provide either a pooled 'pool_result' or 'studies' + 'measure'")
    return pool_outcome(
        studies, measure,
        model=payload.get("model", "random"),
        tau2_method=payload.get("tau2_method", "REML"),
        outcome_name=payload.get("outcome_name"),
        favorable_direction=payload.get("favorable_direction", "lower"),
    )


def _grade_kwargs(payload: dict) -> dict:
    return dict(
        initial=payload.get("initial"),
        per_study_rob=payload.get("per_study_rob") or [],
        indirectness_levels=(None if payload.get("indirectness_levels") is None
                             else int(payload["indirectness_levels"])),
        indirectness_reason=payload.get("indirectness_reason", ""),
        mid_benefit=_num(payload.get("mid_benefit")),
        mid_harm=_num(payload.get("mid_harm")),
        baseline_risk_per_1000=_num(payload.get("baseline_risk_per_1000")),
        dose_response=payload.get("dose_response"),
        opposing_confounding=bool(payload.get("opposing_confounding")),
        subgroup=payload.get("subgroup"),
        metaregression=payload.get("metaregression"),
        overrides=payload.get("overrides") or None,
    )


def grade_certainty(payload: dict) -> dict:
    """Rate a single pooled body of evidence. Stateless (no persistence, no model).

    Accepts a precomputed ``pool_result`` or raw ``studies`` + ``measure``, plus
    ``per_study_rob`` and the human judgments. Returns the ``grade_body`` dict.
    Note: when ``indirectness_levels`` is omitted this does NOT auto-assess (that
    needs a model call) — it defaults to 0; use the run endpoint for hybrid
    auto-indirectness.
    """
    pr = _pool_result_from_payload(payload)
    return grade_body(pr, **_grade_kwargs(payload))


def sof(payload: dict) -> dict:
    """Convenience: grade one body and assemble its Summary-of-Findings row.

    Returns ``{pool, grade, sof_row}``.
    """
    pr = _pool_result_from_payload(payload)
    g = grade_body(pr, **_grade_kwargs(payload))
    row = sof_row(pr, g, outcome=payload.get("outcome"))
    return {"pool": pr, "grade": g, "sof_row": row}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def create_run(conn, user_id: int, *, name: Optional[str], project_id: Optional[int],
               target_pico: Optional[dict], auto_indirectness: bool, n_bodies: int) -> int:
    cur = conn.execute(
        """INSERT INTO grade_runs (user_id, project_id, name, target_pico_json,
                                   auto_indirectness, status, n_bodies)
           VALUES (?, ?, ?, ?, ?, 'running', ?) RETURNING id""",
        (user_id, project_id, name,
         json.dumps(target_pico) if target_pico else None,
         1 if auto_indirectness else 0, n_bodies),
    )
    row = cur.fetchone()
    conn.commit()
    return row["id"] if row else cur.lastrowid


def log_event(conn, run_id: int, event_type: str, message: str, **detail) -> None:
    conn.execute(
        """INSERT INTO grade_events (run_id, event_type, message, detail_json)
           VALUES (?, ?, ?, ?)""",
        (run_id, event_type, message, json.dumps(detail) if detail else None),
    )
    conn.commit()


def save_result(conn, run_id: int, descriptor: dict) -> None:
    """Persist one graded body (a grade_prep descriptor + optional indirectness_detail)."""
    grade = descriptor.get("grade") or {}
    sof = descriptor.get("sof_row") or {}
    ind = descriptor.get("indirectness_detail")
    err = None if grade else "; ".join(descriptor.get("warnings") or []) or "not graded"
    conn.execute(
        """INSERT INTO grade_results
             (run_id, outcome_name, comparison, timepoint, design_class, measure,
              k_studies, certainty, initial_certainty, total_downgrade, total_upgrade,
              grade_json, sof_json, absolute_effects_json, indirectness_json,
              explanation, error_message)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, descriptor.get("outcome_name"), descriptor.get("comparison"),
         descriptor.get("timepoint"), descriptor.get("design_class"), descriptor.get("measure"),
         descriptor.get("k"), grade.get("final"), grade.get("initial"),
         grade.get("total_downgrade"), grade.get("total_upgrade"),
         json.dumps(grade), json.dumps(sof),
         json.dumps(grade.get("absolute_effects")) if grade.get("absolute_effects") else None,
         json.dumps(ind) if ind else None,
         grade.get("explanation"), err),
    )
    conn.commit()


def finalize_run(conn, run_id: int, status: str = "complete", error: Optional[str] = None) -> None:
    conn.execute(
        "UPDATE grade_runs SET status=?, error_message=?, completed_at=CURRENT_TIMESTAMP WHERE id=?",
        (status, error, run_id),
    )
    conn.commit()


def _load_run(conn, run_id: int, user_id: int, is_admin: bool) -> dict:
    row = conn.execute("SELECT * FROM grade_runs WHERE id=? AND deleted_at IS NULL", (run_id,)).fetchone()
    if not row:
        return None
    if not is_admin and row["user_id"] != user_id:
        return None
    return dict(row)


def get_run_detail(conn, run_id: int, user_id: int, is_admin: bool) -> Optional[dict]:
    run = _load_run(conn, run_id, user_id, is_admin)
    if not run:
        return None
    results = conn.execute(
        "SELECT * FROM grade_results WHERE run_id=? ORDER BY id ASC", (run_id,)).fetchall()
    out_results = []
    for r in results:
        d = dict(r)
        for k in ("grade_json", "sof_json", "absolute_effects_json", "indirectness_json"):
            if d.get(k):
                try:
                    d[k[:-5]] = json.loads(d[k])
                except Exception:
                    d[k[:-5]] = None
            d.pop(k, None)
        out_results.append(d)
    tp = None
    if run.get("target_pico_json"):
        try:
            tp = json.loads(run["target_pico_json"])
        except Exception:
            tp = None
    run["target_pico"] = tp
    run.pop("target_pico_json", None)
    run["results"] = out_results
    return run


def list_runs(conn, user_id: int, is_admin: bool, limit: int = 100) -> list[dict]:
    if is_admin:
        rows = conn.execute(
            """SELECT id, name, status, n_bodies, created_at, completed_at, user_id
                 FROM grade_runs WHERE deleted_at IS NULL
                ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, name, status, n_bodies, created_at, completed_at, user_id
                 FROM grade_runs WHERE user_id=? AND deleted_at IS NULL
                ORDER BY id DESC LIMIT ?""", (user_id, limit)).fetchall()
    return [dict(r) for r in rows]


def soft_delete_run(conn, run_id: int, user_id: int, is_admin: bool) -> bool:
    run = _load_run(conn, run_id, user_id, is_admin)
    if not run:
        return False
    conn.execute("UPDATE grade_runs SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (run_id,))
    conn.commit()
    return True


def flatten_for_export(detail: dict) -> list[dict]:
    """One flat row per graded body for CSV/XLSX export."""
    rows = []
    for r in detail.get("results", []):
        grade = r.get("grade") or {}
        ae = r.get("absolute_effects") or {}
        sof = r.get("sof") or {}
        rel = sof.get("relative_effect") or {}
        domains = {d["domain"]: (d.get("downgrade", 0) or -d.get("upgrade", 0))
                   for d in grade.get("domains", [])}
        rows.append({
            "outcome": r.get("outcome_name"),
            "comparison": r.get("comparison"),
            "timepoint": r.get("timepoint"),
            "design_class": r.get("design_class"),
            "measure": r.get("measure"),
            "k_studies": r.get("k_studies"),
            "n_participants": sof.get("n_participants"),
            "relative_effect": rel.get("estimate"),
            "ci_low": rel.get("ci_low"),
            "ci_high": rel.get("ci_high"),
            "baseline_per_1000": ae.get("baseline_per_1000"),
            "intervention_per_1000": ae.get("intervention_per_1000"),
            "risk_difference_per_1000": ae.get("risk_difference_per_1000"),
            "nnt": ae.get("nnt"),
            "initial_certainty": r.get("initial_certainty"),
            "certainty": r.get("certainty"),
            "downgrade_risk_of_bias": domains.get("Risk of bias"),
            "downgrade_inconsistency": domains.get("Inconsistency"),
            "downgrade_indirectness": domains.get("Indirectness"),
            "downgrade_imprecision": domains.get("Imprecision"),
            "downgrade_publication_bias": domains.get("Publication bias"),
            "explanation": r.get("explanation"),
        })
    return rows
