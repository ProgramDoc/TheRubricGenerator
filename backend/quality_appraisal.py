"""Quality Appraisal AI — single-study quality-of-evidence assessment.

Given a paper, this module:

1. Classifies the study design (reuses annotator).
2. Extracts structured fields (reuses annotator universal + type + modifier layers).
3. Picks the study's primary outcome.
4. Runs the registered risk-of-bias tool for the study type
   (RoB 2 for RCTs; others stubbed for future work).
5. Runs the registered reporting-guideline checklist
   (CONSORT 2025 for RCTs).
6. Computes an initial GRADE certainty (per study-type default) and
   an updated GRADE after the risk-of-bias judgement.

v1 scope: **Randomized Controlled Trial** only. The architecture is
extensible via ``STUDY_TYPE_REGISTRY`` — adding new study types is a new
entry here plus new tool/guideline modules.
"""

from __future__ import annotations

import inspect
import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException

from . import annotator as annotator_mod
from . import billing as bill_mod
from . import paper_files
from .helpers import call_anthropic, parse_json_response
from .rob_tools import rob2, robins_i
from .reporting_guidelines import consort2025, strobe

logger = logging.getLogger("rubricgen")


# ─────────────────────────────────────────────
# Schema (init_db wires this in)
# ─────────────────────────────────────────────
QUALITY_APPRAISAL_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS quality_appraisal_runs (
    id               SERIAL PRIMARY KEY,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id       INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    paper_ids_json   TEXT    NOT NULL DEFAULT '[]',
    paper_count      INTEGER NOT NULL DEFAULT 0,
    status           TEXT    NOT NULL DEFAULT 'pending',
    credit_cost      INTEGER NOT NULL DEFAULT 0,
    credits_refunded INTEGER NOT NULL DEFAULT 0,
    error_message    TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at     TIMESTAMP,
    deleted_at       TIMESTAMP
);

CREATE TABLE IF NOT EXISTS quality_appraisal_results (
    id                    SERIAL PRIMARY KEY,
    run_id                INTEGER NOT NULL REFERENCES quality_appraisal_runs(id) ON DELETE CASCADE,
    paper_id              INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    status                TEXT    NOT NULL DEFAULT 'pending',
    error_message         TEXT,
    filename              TEXT,
    study_type            TEXT,
    rob_tool              TEXT,
    reporting_guideline   TEXT,
    primary_outcome       TEXT,
    classification_json   TEXT    NOT NULL DEFAULT '{}',
    extracted_fields_json TEXT    NOT NULL DEFAULT '{}',
    rob_domains_json      TEXT    NOT NULL DEFAULT '{}',
    rob_overall           TEXT,
    rob_direction         TEXT,
    guideline_json        TEXT    NOT NULL DEFAULT '{}',
    guideline_proportion  REAL,
    guideline_adhered     INTEGER,
    guideline_applicable  INTEGER,
    initial_grade         TEXT,
    updated_grade         TEXT,
    grade_explanation     TEXT,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS quality_appraisal_events (
    id          SERIAL PRIMARY KEY,
    run_id      INTEGER NOT NULL REFERENCES quality_appraisal_runs(id) ON DELETE CASCADE,
    event_type  TEXT    NOT NULL,
    message     TEXT    NOT NULL,
    detail_json TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_qa_runs_user_created ON quality_appraisal_runs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qa_results_run ON quality_appraisal_results(run_id);
CREATE INDEX IF NOT EXISTS idx_qa_events_run ON quality_appraisal_events(run_id, id);
"""


# ─────────────────────────────────────────────
# Study-type registry (extensibility contract)
# ─────────────────────────────────────────────
# Keys MUST match annotator.TYPE_FIELD_IDS keys so classification results
# drop straight in. Unsupported types return None from dispatch() → result
# gets status='skipped' and the per-paper charge is refunded.
STUDY_TYPE_REGISTRY: dict[str, dict[str, str]] = {
    "Randomized Controlled Trial":  {"rob_tool": "rob2",     "reporting_guideline": "consort2025", "initial_grade": "High"},
    "Cohort Study":                 {"rob_tool": "robins_i", "reporting_guideline": "strobe",      "initial_grade": "Low"},
    "Case-Control":                 {"rob_tool": "robins_i", "reporting_guideline": "strobe",      "initial_grade": "Low"},
    "Non-Randomized Trial":         {"rob_tool": "robins_i", "reporting_guideline": "strobe",      "initial_grade": "Low"},
    "Cross-Sectional (Analytical)": {"rob_tool": "robins_i", "reporting_guideline": "strobe",      "initial_grade": "Low"},
    "Case-Crossover":               {"rob_tool": "robins_i", "reporting_guideline": "strobe",      "initial_grade": "Low"},
    # Future (not wired yet — classification skips + refunds):
    # "Cluster Randomized Trial":    {"rob_tool": "rob2_cluster",    "reporting_guideline": "consort_cluster",    "initial_grade": "High"},
    # "Crossover Trial":             {"rob_tool": "rob2_crossover",  "reporting_guideline": "consort_crossover",  "initial_grade": "High"},
    # "SR with Meta-Analysis":       {"rob_tool": "amstar2",         "reporting_guideline": "prisma2020",         "initial_grade": "High"},
    # "Diagnostic Accuracy":         {"rob_tool": "quadas2",         "reporting_guideline": "stard",              "initial_grade": "Low"},
}

_TOOL_RUNNERS: dict[str, Callable] = {
    "rob2":     rob2.run,
    "robins_i": robins_i.run,
}
_GUIDELINE_RUNNERS: dict[str, Callable] = {
    "consort2025": consort2025.run,
    "strobe":      strobe.run,
}


def dispatch(study_type: str) -> dict[str, str] | None:
    """Return the appraisal config for a study type, or None if unsupported."""
    return STUDY_TYPE_REGISTRY.get(study_type)


# ─────────────────────────────────────────────
# Credit cost
# ─────────────────────────────────────────────
# Per paper: classify (~3) + prefill (~8) + 5 × RoB 2 domain (~3 each) + CONSORT (~4) ≈ 30.
# Matches the estimate surfaced in the UI before a run.
CREDIT_COST_QA_PER_PAPER = 30


# ─────────────────────────────────────────────
# GRADE logic
# ─────────────────────────────────────────────
GRADE_LEVELS = ["High", "Moderate", "Low", "Very low"]


def _grade_index(level: str) -> int:
    try:
        return GRADE_LEVELS.index(level)
    except ValueError:
        return 0


def compute_grade(initial: str,
                  rob_overall: str,
                  rob_domain_judgements: list[str] | None = None
                  ) -> tuple[str, str]:
    """Compute updated GRADE + human-readable explanation.

    v1 downgrades for risk of bias only. Other GRADE domains (inconsistency,
    indirectness, imprecision, publication bias) require a body of evidence
    and are documented as out of scope in the developer view.

    Per GradePro guidance:
      RoB 2     Low             → 0
                Some concerns   → 1
                High            → 1 (2 if ≥2 domains are High)
      ROBINS-I  Low             → 0
                Moderate        → 1
                Serious         → 1 (2 if ≥2 domains are Serious)
                Critical        → 2 (always)
                No information  → 1 (conservative)
    """
    idx = _grade_index(initial)
    judgements = rob_domain_judgements or []

    if rob_overall == "Low":
        return initial, "No downgrade: overall risk of bias is Low."

    # RoB 2 branches
    if rob_overall == "Some concerns":
        new_idx = min(idx + 1, len(GRADE_LEVELS) - 1)
        return GRADE_LEVELS[new_idx], "Downgraded 1 level for Some concerns in risk of bias."
    if rob_overall == "High":
        high_count = sum(1 for j in judgements if j == "High")
        if high_count >= 2:
            new_idx = min(idx + 2, len(GRADE_LEVELS) - 1)
            return GRADE_LEVELS[new_idx], (
                f"Downgraded 2 levels for High risk of bias in {high_count} domains."
            )
        new_idx = min(idx + 1, len(GRADE_LEVELS) - 1)
        return GRADE_LEVELS[new_idx], "Downgraded 1 level for High risk of bias."

    # ROBINS-I branches
    if rob_overall == "Moderate":
        new_idx = min(idx + 1, len(GRADE_LEVELS) - 1)
        return GRADE_LEVELS[new_idx], "Downgraded 1 level for Moderate risk of bias (ROBINS-I)."
    if rob_overall == "Serious":
        serious_count = sum(1 for j in judgements if j == "Serious")
        if serious_count >= 2:
            new_idx = min(idx + 2, len(GRADE_LEVELS) - 1)
            return GRADE_LEVELS[new_idx], (
                f"Downgraded 2 levels for Serious risk of bias in {serious_count} ROBINS-I domains."
            )
        new_idx = min(idx + 1, len(GRADE_LEVELS) - 1)
        return GRADE_LEVELS[new_idx], "Downgraded 1 level for Serious risk of bias (ROBINS-I)."
    if rob_overall == "Critical":
        new_idx = min(idx + 2, len(GRADE_LEVELS) - 1)
        return GRADE_LEVELS[new_idx], "Downgraded 2 levels for Critical risk of bias (ROBINS-I)."
    if rob_overall == "No information":
        new_idx = min(idx + 1, len(GRADE_LEVELS) - 1)
        return GRADE_LEVELS[new_idx], "Downgraded 1 level for No information in one or more ROBINS-I domains (conservative)."

    # Fallback for any unexpected vocabulary
    new_idx = min(idx + 1, len(GRADE_LEVELS) - 1)
    return GRADE_LEVELS[new_idx], f"Downgraded 1 level for risk of bias ({rob_overall})."


# ─────────────────────────────────────────────
# Event logging
# ─────────────────────────────────────────────
def log_event(conn, run_id: int, event_type: str, message: str,
              detail: dict | None = None) -> None:
    """Log a progress event; committed immediately so the frontend poll sees it."""
    try:
        with conn:
            conn.execute(
                """INSERT INTO quality_appraisal_events
                        (run_id, event_type, message, detail_json)
                   VALUES (?, ?, ?, ?)""",
                (run_id, event_type, message,
                 json.dumps(detail) if detail else None),
            )
            conn.commit()
    except Exception as e:
        logger.warning("Failed to log QA event (run=%s): %s", run_id, e)


# ─────────────────────────────────────────────
# Primary-outcome picker
# ─────────────────────────────────────────────
_PRIMARY_OUTCOME_KEYS = [
    "primary_outcome_definition",
    "primary_outcome_measurement",
    "population_outcomes",
]


def pick_primary_outcome(fields: dict[str, str]) -> str:
    """Pick the study's primary outcome name from extracted fields.

    Prefers ``primary_outcome_definition``; falls back through related keys.
    Short-trims excessive prose so it fits comfortably in RoB 2 prompt headers.
    """
    for key in _PRIMARY_OUTCOME_KEYS:
        val = (fields.get(key) or "").strip()
        if val:
            # Trim to first sentence / 200 chars for prompt compactness
            short = re.split(r"(?<=[.;])\s", val, maxsplit=1)[0]
            return short[:200]
    return "(primary outcome not specified in the extracted fields)"


# ─────────────────────────────────────────────
# Paper-level orchestrator
# ─────────────────────────────────────────────
def _row_to_dict(row) -> dict[str, Any]:
    """Turn a DB row into a plain dict (sqlite3.Row and psycopg both support keys())."""
    try:
        return {k: row[k] for k in row.keys()}
    except Exception:
        return dict(row)


def appraise_paper(conn, papers_dir: Path, user_id: int, is_admin: bool,
                   run_id: int, paper_id: int,
                   on_progress: Callable[[str, str], None] | None = None
                   ) -> dict[str, Any]:
    """Appraise a single paper end-to-end. Writes one row to
    ``quality_appraisal_results``.

    Returns a summary dict for tests / callers. Raises nothing for
    paper-level failures — the failure is recorded on the row; the caller
    looks at ``status`` to decide whether to refund.
    """
    def _notify(level: str, msg: str) -> None:
        if on_progress:
            try:
                on_progress(level, msg)
            except Exception:
                pass
        logger.info("QA run %s paper %s — %s", run_id, paper_id, msg)

    # 1. Load paper
    try:
        pdf_bytes, filename = annotator_mod.load_paper_pdf(
            conn, papers_dir, paper_id, user_id, is_admin=is_admin,
        )
    except HTTPException as he:
        msg = f"Could not open paper: {he.detail}"
        _write_result(conn, run_id, paper_id, status="error",
                       error=msg, filename=None)
        _notify("error", msg)
        return {"status": "error", "error": msg}

    _notify("info", f"Classifying '{filename}'")

    # 2. Classify
    try:
        classification = annotator_mod.classify_study_design(pdf_bytes)
    except HTTPException as he:
        msg = f"Classification failed: {he.detail}"
        _write_result(conn, run_id, paper_id, status="error",
                       error=msg, filename=filename)
        _notify("error", msg)
        return {"status": "error", "error": msg}
    except Exception as e:
        logger.exception("Classification failed (run=%s paper=%s)", run_id, paper_id)
        msg = "Classification failed — see server logs."
        _write_result(conn, run_id, paper_id, status="error",
                       error=msg, filename=filename)
        _notify("error", msg)
        return {"status": "error", "error": msg}

    study_type = classification.get("study_type", "")
    cfg = dispatch(study_type)
    if cfg is None:
        msg = (
            f"Study type '{study_type}' is not yet supported by Quality Appraisal. "
            "v1 currently supports Randomized Controlled Trial only."
        )
        _write_result(conn, run_id, paper_id, status="skipped",
                       error=msg, filename=filename,
                       study_type=study_type,
                       classification=classification)
        _notify("warn", msg)
        return {"status": "skipped", "study_type": study_type, "error": msg}

    # 3. Extract fields (all universal groups + all type-specific + all modifiers)
    _notify("info", f"Extracting fields for {study_type}")
    try:
        fields = annotator_mod.prefill_fields(
            pdf_bytes, study_type,
            groups=None,           # all 8 groups
            type_fields=None,      # all type-specific
            modifier_fields=None,  # all modifiers
        )
    except HTTPException as he:
        msg = f"Field extraction failed: {he.detail}"
        _write_result(conn, run_id, paper_id, status="error",
                       error=msg, filename=filename,
                       study_type=study_type,
                       classification=classification)
        _notify("error", msg)
        return {"status": "error", "error": msg}
    except Exception as e:
        logger.exception("Prefill failed (run=%s paper=%s)", run_id, paper_id)
        msg = "Field extraction failed — see server logs."
        _write_result(conn, run_id, paper_id, status="error",
                       error=msg, filename=filename,
                       study_type=study_type,
                       classification=classification)
        _notify("error", msg)
        return {"status": "error", "error": msg}

    primary_outcome = pick_primary_outcome(fields)
    _notify("info", f"Primary outcome: {primary_outcome[:80]}")

    # 4. Risk of bias
    rob_runner = _TOOL_RUNNERS.get(cfg["rob_tool"])
    if rob_runner is None:
        msg = f"RoB tool '{cfg['rob_tool']}' is registered but not yet implemented."
        _write_result(conn, run_id, paper_id, status="skipped",
                       error=msg, filename=filename,
                       study_type=study_type, classification=classification,
                       extracted_fields=fields)
        _notify("warn", msg)
        return {"status": "skipped", "error": msg}

    _notify("info", f"Running risk-of-bias assessment ({cfg['rob_tool']})")
    try:
        rob_domains, rob_overall, rob_direction = rob_runner(
            pdf_bytes, fields, classification, primary_outcome,
            progress=lambda domain_id: _notify(
                "progress", f"RoB {cfg['rob_tool']} domain {domain_id}"),
        )
    except HTTPException as he:
        msg = f"Risk-of-bias assessment failed: {he.detail}"
        _write_result(conn, run_id, paper_id, status="error",
                       error=msg, filename=filename,
                       study_type=study_type, classification=classification,
                       extracted_fields=fields)
        _notify("error", msg)
        return {"status": "error", "error": msg}
    except Exception as e:
        logger.exception("RoB run failed (run=%s paper=%s)", run_id, paper_id)
        msg = "Risk-of-bias assessment failed — see server logs."
        _write_result(conn, run_id, paper_id, status="error",
                       error=msg, filename=filename,
                       study_type=study_type, classification=classification,
                       extracted_fields=fields)
        _notify("error", msg)
        return {"status": "error", "error": msg}

    # 5. Reporting guideline
    guide_runner = _GUIDELINE_RUNNERS.get(cfg["reporting_guideline"])
    if guide_runner is None:
        guideline = {"items": {}, "adhered": 0, "applicable": 0,
                      "total": 0, "proportion": 0.0,
                      "note": f"Guideline '{cfg['reporting_guideline']}' not implemented."}
    else:
        _notify("info", f"Checking reporting guideline ({cfg['reporting_guideline']})")
        try:
            guideline = guide_runner(pdf_bytes, fields, classification)
        except HTTPException as he:
            msg = f"Reporting-guideline check failed: {he.detail}"
            logger.warning("Guideline check failed (run=%s paper=%s): %s",
                           run_id, paper_id, msg)
            guideline = {"items": {}, "adhered": 0, "applicable": 0,
                          "total": 0, "proportion": 0.0,
                          "note": msg}
            _notify("warn", msg)
        except Exception as e:
            logger.exception("Guideline run failed (run=%s paper=%s)", run_id, paper_id)
            guideline = {"items": {}, "adhered": 0, "applicable": 0,
                          "total": 0, "proportion": 0.0,
                          "note": "Reporting-guideline check failed — see server logs."}

    # 6. GRADE
    rob_domain_judgements = [d.get("judgement", "Low")
                              for d in rob_domains.values()]
    initial_grade = cfg["initial_grade"]
    updated_grade, grade_expl = compute_grade(
        initial_grade, rob_overall, rob_domain_judgements)

    # 7. Persist
    _write_result(conn, run_id, paper_id, status="ok", filename=filename,
                   study_type=study_type,
                   rob_tool=cfg["rob_tool"],
                   reporting_guideline=cfg["reporting_guideline"],
                   primary_outcome=primary_outcome,
                   classification=classification,
                   extracted_fields=fields,
                   rob_domains=rob_domains,
                   rob_overall=rob_overall,
                   rob_direction=rob_direction,
                   guideline=guideline,
                   initial_grade=initial_grade,
                   updated_grade=updated_grade,
                   grade_explanation=grade_expl)
    _notify("info", f"Done: {filename} — RoB {rob_overall}, GRADE {initial_grade}→{updated_grade}")
    return {
        "status": "ok",
        "filename": filename,
        "study_type": study_type,
        "rob_overall": rob_overall,
        "guideline_proportion": guideline.get("proportion"),
        "initial_grade": initial_grade,
        "updated_grade": updated_grade,
    }


def _write_result(conn, run_id: int, paper_id: int, *,
                  status: str, error: str | None = None,
                  filename: str | None = None,
                  study_type: str | None = None,
                  rob_tool: str | None = None,
                  reporting_guideline: str | None = None,
                  primary_outcome: str | None = None,
                  classification: dict | None = None,
                  extracted_fields: dict | None = None,
                  rob_domains: dict | None = None,
                  rob_overall: str | None = None,
                  rob_direction: str | None = None,
                  guideline: dict | None = None,
                  initial_grade: str | None = None,
                  updated_grade: str | None = None,
                  grade_explanation: str | None = None) -> None:
    """Insert one ``quality_appraisal_results`` row."""
    guideline = guideline or {}
    proportion = guideline.get("proportion")
    if proportion is not None:
        try:
            proportion = float(proportion)
        except Exception:
            proportion = None

    with conn:
        conn.execute(
            """INSERT INTO quality_appraisal_results
                    (run_id, paper_id, status, error_message, filename,
                     study_type, rob_tool, reporting_guideline, primary_outcome,
                     classification_json, extracted_fields_json,
                     rob_domains_json, rob_overall, rob_direction,
                     guideline_json, guideline_proportion,
                     guideline_adhered, guideline_applicable,
                     initial_grade, updated_grade, grade_explanation)
               VALUES (?, ?, ?, ?, ?,  ?, ?, ?, ?,  ?, ?,  ?, ?, ?,  ?, ?,  ?, ?,  ?, ?, ?)""",
            (run_id, paper_id, status, error, filename,
             study_type, rob_tool, reporting_guideline, primary_outcome,
             json.dumps(classification or {}),
             json.dumps(extracted_fields or {}),
             json.dumps(rob_domains or {}),
             rob_overall, rob_direction,
             json.dumps(guideline or {}), proportion,
             guideline.get("adhered"), guideline.get("applicable"),
             initial_grade, updated_grade, grade_explanation),
        )
        conn.commit()


# ─────────────────────────────────────────────
# Batch orchestrator + thread entry
# ─────────────────────────────────────────────
def run_batch(get_db_fn, papers_dir: Path, user_id: int, is_admin: bool,
              run_id: int, paper_ids: list[int]) -> None:
    """Execute a run over multiple papers. Safe to call from a background thread.

    Per-paper isolation: a failed or skipped paper refunds its credits and
    the run continues. The top-level run status moves to 'complete' on exit.
    Skipped papers (unsupported study type) also refund.
    """
    per_paper_cost = CREDIT_COST_QA_PER_PAPER
    total_refunded = 0

    # Mark run running
    setup_conn = get_db_fn()
    try:
        with setup_conn:
            setup_conn.execute(
                "UPDATE quality_appraisal_runs SET status='running' WHERE id=?",
                (run_id,),
            )
            setup_conn.commit()
        log_event(setup_conn, run_id, "info",
                   f"Started quality appraisal on {len(paper_ids)} paper(s).")
    finally:
        setup_conn.close()

    for pid in paper_ids:
        pconn = get_db_fn()
        try:
            try:
                summary = appraise_paper(
                    pconn, papers_dir, user_id, is_admin, run_id, pid,
                    on_progress=lambda level, msg, _c=pconn: log_event(
                        _c, run_id, level, msg),
                )
            except Exception as e:
                logger.exception("Unhandled error in appraise_paper (run=%s paper=%s)",
                                 run_id, pid)
                summary = {"status": "error", "error": str(e)}
                # Ensure a row exists even on catastrophic failure
                try:
                    _write_result(pconn, run_id, pid,
                                   status="error", error=str(e))
                except Exception:
                    pass

            # Refund per-paper on error or skipped
            if summary.get("status") in ("error", "skipped") and not is_admin:
                try:
                    bill_mod.refund_credits(
                        pconn, user_id, per_paper_cost,
                        f"Refund: Quality Appraisal run {run_id} paper {pid}",
                    )
                    total_refunded += per_paper_cost
                    log_event(pconn, run_id, "info",
                               f"Refunded {per_paper_cost} credits for paper {pid} ({summary.get('status')}).")
                except Exception as e:
                    logger.warning("Refund failed (run=%s paper=%s): %s", run_id, pid, e)
        finally:
            pconn.close()

    # Finalize run
    final_conn = get_db_fn()
    try:
        with final_conn:
            final_conn.execute(
                """UPDATE quality_appraisal_runs
                      SET status='complete',
                          credits_refunded=?,
                          completed_at=CURRENT_TIMESTAMP
                    WHERE id=?""",
                (total_refunded, run_id),
            )
            final_conn.commit()
        log_event(final_conn, run_id, "info",
                   "Quality appraisal complete.",
                   {"refunded": total_refunded})
    finally:
        final_conn.close()


def run_batch_async(get_db_fn, papers_dir: Path, user_id: int, is_admin: bool,
                    run_id: int, paper_ids: list[int]) -> None:
    """Fire-and-forget batch runner on a daemon thread."""
    t = threading.Thread(
        target=run_batch,
        args=(get_db_fn, papers_dir, user_id, is_admin, run_id, paper_ids),
        daemon=True,
        name=f"quality-appraisal-{run_id}",
    )
    t.start()


# ─────────────────────────────────────────────
# Developer-view exposure
# ─────────────────────────────────────────────
def prompt_catalog() -> dict[str, Any]:
    """Return all prompts + scoring logic for the developer icon.

    Available to every signed-in user so the tool's reasoning is inspectable.
    """
    return {
        "overview": {
            "description": (
                "Quality Appraisal AI assesses single-study quality of evidence in three "
                "passes: classify → extract fields → apply validated RoB tool + reporting "
                "guideline + GRADE."
            ),
            "pipeline_steps": [
                "Classify study design (reuses OGAI Annotator)",
                "Extract universal + type-specific + modifier fields",
                "Auto-pick the primary outcome",
                "Run the registered risk-of-bias tool (per-domain LLM calls + pure-Python decision trees)",
                "Run the registered reporting-guideline checklist (single LLM call over all items)",
                "Compute initial GRADE (from study type) and updated GRADE (downgrade for RoB)",
            ],
        },
        "registry": STUDY_TYPE_REGISTRY,
        "credit_cost_per_paper": CREDIT_COST_QA_PER_PAPER,
        "rob_tools": {
            "rob2":     rob2.prompt_catalog(),
            "robins_i": robins_i.prompt_catalog(),
        },
        "reporting_guidelines": {
            "consort2025": consort2025.prompt_catalog(),
            "strobe":      strobe.prompt_catalog(),
        },
        "grade": {
            "levels": GRADE_LEVELS,
            "description": (
                "v1 downgrades for risk of bias only. Other GRADE domains "
                "(inconsistency, indirectness, imprecision, publication bias) require a "
                "body of evidence, not a single study."
            ),
            "logic_code": inspect.getsource(compute_grade),
        },
        "primary_outcome_picker": {
            "description": "Reads primary_outcome_definition → primary_outcome_measurement → population_outcomes from extracted fields.",
            "code": inspect.getsource(pick_primary_outcome),
        },
    }


# ─────────────────────────────────────────────
# Flattening for CSV / XLSX export
# ─────────────────────────────────────────────
def flatten_result_row(result_row: dict[str, Any],
                        paper_row: dict[str, Any] | None = None) -> dict[str, Any]:
    """Flatten a ``quality_appraisal_results`` row (with JSON strings already
    parsed) into a single dict suitable for CSV / XLSX export.

    The column order matches the frontend results grid: study info → classification →
    RoB overall → CONSORT proportion → initial/updated GRADE → GRADE explanation →
    RoB 2 per-domain judgements + signal answers.
    """
    classification = result_row.get("classification") or {}
    fields = result_row.get("extracted_fields") or {}
    rob_domains = result_row.get("rob_domains") or {}
    guideline = result_row.get("guideline") or {}

    # Study info (column 1) — compact stack of citation details
    title = fields.get("citation_title") or (paper_row or {}).get("filename", "")
    authors = fields.get("citation_authors", "")
    journal = fields.get("citation_journal", "")
    year = fields.get("citation_year", "")

    row: dict[str, Any] = {
        "paper_id": result_row.get("paper_id"),
        "filename": result_row.get("filename") or (paper_row or {}).get("filename"),
        "title": title, "authors": authors, "journal": journal, "year": year,
        "status": result_row.get("status"),
        "error_message": result_row.get("error_message"),
        "study_type": result_row.get("study_type"),
        "major_category": classification.get("major_category", ""),
        "subcategory": classification.get("subcategory", ""),
        "clinical_trial_phase": fields.get("clinical_trial_phase", ""),
        "industry_sponsored": fields.get("industry_sponsored", ""),
        "adaptive_design": fields.get("adaptive_design", ""),
        "pragmatic_vs_explanatory": fields.get("pragmatic_vs_explanatory", ""),
        "primary_outcome": result_row.get("primary_outcome"),
        "rob_overall": result_row.get("rob_overall"),
        "rob_direction": result_row.get("rob_direction"),
        "consort_proportion": result_row.get("guideline_proportion"),
        "consort_adhered": result_row.get("guideline_adhered"),
        "consort_applicable": result_row.get("guideline_applicable"),
        "initial_grade": result_row.get("initial_grade"),
        "updated_grade": result_row.get("updated_grade"),
        "grade_explanation": result_row.get("grade_explanation"),
    }
    # Per-domain judgements + all signaling-question answers. Dispatch the
    # DOMAINS list by rob_tool so ROBINS-I rows get 7 domains (not 5).
    tool = result_row.get("rob_tool")
    domains_for_tool = robins_i.DOMAINS if tool == "robins_i" else rob2.DOMAINS
    for dom in domains_for_tool:
        d = rob_domains.get(str(dom["id"])) or {}
        row[f"rob_d{dom['id']}_judgement"] = d.get("judgement", "")
        for sig in dom["signals"]:
            row[f"rob_{sig['id']}"] = (d.get("signals") or {}).get(sig["id"], "")
    return row
