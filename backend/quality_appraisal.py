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
from . import indirectness as indir_mod
from . import imprecision as imprec_mod
from .helpers import call_anthropic, parse_json_response
from .rob_tools import rob2, robins_i, quadas3
from .reporting_guidelines import consort2025, strobe, stard

logger = logging.getLogger("rubricgen")


# ─────────────────────────────────────────────
# Schema (init_db wires this in)
# ─────────────────────────────────────────────
QUALITY_APPRAISAL_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS quality_appraisal_runs (
    id                          SERIAL PRIMARY KEY,
    user_id                     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id                  INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    paper_ids_json              TEXT    NOT NULL DEFAULT '[]',
    paper_count                 INTEGER NOT NULL DEFAULT 0,
    status                      TEXT    NOT NULL DEFAULT 'pending',
    credit_cost                 INTEGER NOT NULL DEFAULT 0,
    credits_refunded            INTEGER NOT NULL DEFAULT 0,
    error_message               TEXT,
    target_pico_json            TEXT,
    imprecision_thresholds_json TEXT,
    quadas3_review_context      TEXT,
    paper_estimates_json        TEXT NOT NULL DEFAULT '{}',
    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at                TIMESTAMP,
    deleted_at                  TIMESTAMP
);

CREATE TABLE IF NOT EXISTS quality_appraisal_results (
    id                       SERIAL PRIMARY KEY,
    run_id                   INTEGER NOT NULL REFERENCES quality_appraisal_runs(id) ON DELETE CASCADE,
    paper_id                 INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    status                   TEXT    NOT NULL DEFAULT 'pending',
    error_message            TEXT,
    filename                 TEXT,
    study_type               TEXT,
    rob_tool                 TEXT,
    reporting_guideline      TEXT,
    primary_outcome          TEXT,
    classification_json      TEXT    NOT NULL DEFAULT '{}',
    extracted_fields_json    TEXT    NOT NULL DEFAULT '{}',
    rob_domains_json         TEXT    NOT NULL DEFAULT '{}',
    rob_overall              TEXT,
    rob_direction            TEXT,
    applicability_overall    TEXT,
    estimate_id              INTEGER,
    estimate_json            TEXT    NOT NULL DEFAULT '{}',
    guideline_json           TEXT    NOT NULL DEFAULT '{}',
    guideline_proportion     REAL,
    guideline_adhered        INTEGER,
    guideline_applicable     INTEGER,
    indirectness_json        TEXT    NOT NULL DEFAULT '{}',
    indirectness_overall     TEXT,
    indirectness_levels      INTEGER NOT NULL DEFAULT 0,
    indirectness_explanation TEXT,
    imprecision_json         TEXT    NOT NULL DEFAULT '{}',
    imprecision_overall      TEXT,
    imprecision_levels       INTEGER NOT NULL DEFAULT 0,
    imprecision_explanation  TEXT,
    initial_grade            TEXT,
    updated_grade            TEXT,
    grade_explanation        TEXT,
    created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
STUDY_TYPE_REGISTRY: dict[str, dict[str, Any]] = {
    "Randomized Controlled Trial":  {"rob_tool": "rob2",     "reporting_guideline": "consort2025", "initial_grade": "High"},
    "Cohort Study":                 {"rob_tool": "robins_i", "reporting_guideline": "strobe",      "initial_grade": "Low"},
    "Case-Control":                 {"rob_tool": "robins_i", "reporting_guideline": "strobe",      "initial_grade": "Low"},
    "Non-Randomized Trial":         {"rob_tool": "robins_i", "reporting_guideline": "strobe",      "initial_grade": "Low"},
    "Cross-Sectional (Analytical)": {"rob_tool": "robins_i", "reporting_guideline": "strobe",      "initial_grade": "Low"},
    "Case-Crossover":               {"rob_tool": "robins_i", "reporting_guideline": "strobe",      "initial_grade": "Low"},
    # Single-arm / uncontrolled designs — route to ROBINS-I V2's single_arm
    # variant of D1/D2 (D3-D6 reused unchanged). Initial GRADE is "Very low"
    # since absence of a comparator is a more severe limitation than confounded
    # comparison; compute_grade clamps further downgrades at "Very low".
    # STROBE is reused pragmatically; a phase2_singlearm guideline module
    # may be added in a follow-up. Dose-Escalation shares the single-arm
    # variant; MTD/DLT/RP2D-specific bias considerations are not modeled.
    "Single-Arm Trial":             {"rob_tool": "robins_i", "reporting_guideline": "strobe",      "initial_grade": "Very low"},
    "Dose-Escalation Study":        {"rob_tool": "robins_i", "reporting_guideline": "strobe",      "initial_grade": "Very low"},
    "Diagnostic Accuracy":          {
        "rob_tool":             "quadas3",
        "reporting_guideline":  "stard",
        "initial_grade":        "High",   # GRADE handbook: cross-sectional
                                          # accuracy designs start at High;
                                          # case-control accuracy designs are
                                          # downgraded for participant-selection
                                          # bias inside QUADAS-3 D1 instead.
        "skip_grade_extras":    True,     # Skip indirectness + imprecision —
                                          # those modules assume PICO/treatment
                                          # trials, not PIRT diagnostic accuracy.
        "supports_estimates":   True,     # Multiple sens/spec estimates per
                                          # paper. Each estimate produces a
                                          # separate quality_appraisal_results
                                          # row (same paper_id, distinct
                                          # estimate_id).
    },
    # Future (not wired yet — classification skips + refunds):
    # "Cluster Randomized Trial":    {"rob_tool": "rob2_cluster",    "reporting_guideline": "consort_cluster",    "initial_grade": "High"},
    # "Crossover Trial":             {"rob_tool": "rob2_crossover",  "reporting_guideline": "consort_crossover",  "initial_grade": "High"},
    # "SR with Meta-Analysis":       {"rob_tool": "amstar2",         "reporting_guideline": "prisma2020",         "initial_grade": "High"},
}

_TOOL_RUNNERS: dict[str, Callable] = {
    "rob2":     rob2.run,
    "robins_i": robins_i.run,
    "quadas3":  quadas3.run,
}
_GUIDELINE_RUNNERS: dict[str, Callable] = {
    "consort2025": consort2025.run,
    "strobe":      strobe.run,
    "stard":       stard.run,
}
# Tools that support Phase-4 estimate extraction. Used by the run-create modal
# to pre-populate per-paper estimate selectors. Each value MUST be a callable
# of signature ``(pdf_bytes, extracted_fields) -> list[dict]``.
_ESTIMATE_EXTRACTORS: dict[str, Callable] = {
    "quadas3": quadas3.extract_estimates,
}


def dispatch(study_type: str) -> dict[str, str] | None:
    """Return the appraisal config for a study type, or None if unsupported."""
    return STUDY_TYPE_REGISTRY.get(study_type)


# ─────────────────────────────────────────────
# Credit cost
# ─────────────────────────────────────────────
# Per paper: classify (~3) + prefill (~8) + RoB (~15: 5 × RoB 2 domains for
# RCTs, or 1 preflight + 6 ROBINS-I V2 domains for non-randomized — same
# total LLM call count) + reporting guideline (~4) + indirectness (~3) +
# imprecision (~3) ≈ 36. Matches the estimate surfaced in the UI before a run.
# QUADAS-3 paths use the same 36-credit budget but charge per-estimate
# (cfg.supports_estimates=True) and skip indirectness + imprecision
# (cfg.skip_grade_extras=True).
CREDIT_COST_QA_PER_PAPER = 36


def migrate_qa_columns(conn) -> None:
    """Idempotent ALTER TABLE migration adding indirectness + imprecision +
    target-PICO + imprecision-thresholds columns.

    Safe to call from ``init_db`` after ``QUALITY_APPRAISAL_TABLES_SQL`` runs:
    new installs already have the columns, existing installs get them added.
    """
    from .db import column_exists  # local import to avoid circular at module load
    with conn:
        # quality_appraisal_runs.target_pico_json
        if not column_exists(conn, "quality_appraisal_runs", "target_pico_json"):
            conn.execute("ALTER TABLE quality_appraisal_runs ADD COLUMN target_pico_json TEXT")
        # quality_appraisal_runs.imprecision_thresholds_json
        if not column_exists(conn, "quality_appraisal_runs", "imprecision_thresholds_json"):
            conn.execute("ALTER TABLE quality_appraisal_runs ADD COLUMN imprecision_thresholds_json TEXT")
        # quality_appraisal_results.indirectness_*
        if not column_exists(conn, "quality_appraisal_results", "indirectness_json"):
            conn.execute("ALTER TABLE quality_appraisal_results ADD COLUMN indirectness_json TEXT NOT NULL DEFAULT '{}'")
        if not column_exists(conn, "quality_appraisal_results", "indirectness_overall"):
            conn.execute("ALTER TABLE quality_appraisal_results ADD COLUMN indirectness_overall TEXT")
        if not column_exists(conn, "quality_appraisal_results", "indirectness_levels"):
            conn.execute("ALTER TABLE quality_appraisal_results ADD COLUMN indirectness_levels INTEGER NOT NULL DEFAULT 0")
        if not column_exists(conn, "quality_appraisal_results", "indirectness_explanation"):
            conn.execute("ALTER TABLE quality_appraisal_results ADD COLUMN indirectness_explanation TEXT")
        # quality_appraisal_results.imprecision_*
        if not column_exists(conn, "quality_appraisal_results", "imprecision_json"):
            conn.execute("ALTER TABLE quality_appraisal_results ADD COLUMN imprecision_json TEXT NOT NULL DEFAULT '{}'")
        if not column_exists(conn, "quality_appraisal_results", "imprecision_overall"):
            conn.execute("ALTER TABLE quality_appraisal_results ADD COLUMN imprecision_overall TEXT")
        if not column_exists(conn, "quality_appraisal_results", "imprecision_levels"):
            conn.execute("ALTER TABLE quality_appraisal_results ADD COLUMN imprecision_levels INTEGER NOT NULL DEFAULT 0")
        if not column_exists(conn, "quality_appraisal_results", "imprecision_explanation"):
            conn.execute("ALTER TABLE quality_appraisal_results ADD COLUMN imprecision_explanation TEXT")
        # QUADAS-3 v1.2 — applicability + per-estimate columns
        if not column_exists(conn, "quality_appraisal_runs", "quadas3_review_context"):
            conn.execute("ALTER TABLE quality_appraisal_runs ADD COLUMN quadas3_review_context TEXT")
        if not column_exists(conn, "quality_appraisal_runs", "paper_estimates_json"):
            conn.execute("ALTER TABLE quality_appraisal_runs ADD COLUMN paper_estimates_json TEXT NOT NULL DEFAULT '{}'")
        if not column_exists(conn, "quality_appraisal_results", "applicability_overall"):
            conn.execute("ALTER TABLE quality_appraisal_results ADD COLUMN applicability_overall TEXT")
        if not column_exists(conn, "quality_appraisal_results", "estimate_id"):
            conn.execute("ALTER TABLE quality_appraisal_results ADD COLUMN estimate_id INTEGER")
        if not column_exists(conn, "quality_appraisal_results", "estimate_json"):
            conn.execute("ALTER TABLE quality_appraisal_results ADD COLUMN estimate_json TEXT NOT NULL DEFAULT '{}'")
        conn.commit()


# ─────────────────────────────────────────────
# GRADE logic
# ─────────────────────────────────────────────
GRADE_LEVELS = ["High", "Moderate", "Low", "Very low"]


def _grade_index(level: str) -> int:
    try:
        return GRADE_LEVELS.index(level)
    except ValueError:
        return 0


def _rob_downgrade(rob_overall: str,
                   rob_domain_judgements: list[str] | None = None
                   ) -> tuple[int, str]:
    """Compute RoB-driven downgrade levels and a human-readable reason fragment.

    Returns ``(levels, reason)``. ``levels`` is 0/1/2; ``reason`` is the noun
    phrase that follows "for ..." in the explanation (e.g. "Some concerns in
    risk of bias").

    Vocabulary:
      RoB 2          Low / Some concerns / High
      ROBINS-I V2    Low / Moderate / Serious / Critical (4 levels — V2 dropped
                     the V1 "No information" overall judgement, though stored
                     V1 runs still resolve via the legacy branch below).
      QUADAS-3       Low / High / Insufficient information (3 levels).

    Domain 1's "Low (except for concerns about uncontrolled confounding)" and
    "Low (except for concerns about uncontrolled benchmarking)" labels are
    normalized to plain "Low" by the ROBINS-I V2 overall aggregator before
    they reach this function.
    """
    judgements = rob_domain_judgements or []
    if rob_overall == "Low":
        return 0, "Low risk of bias"

    # RoB 2 branches
    if rob_overall == "Some concerns":
        return 1, "Some concerns in risk of bias"
    if rob_overall == "High":
        high_count = sum(1 for j in judgements if j == "High")
        if high_count >= 2:
            return 2, f"High risk of bias in {high_count} domains"
        return 1, "High risk of bias"

    # ROBINS-I V2 branches
    if rob_overall == "Moderate":
        return 1, "Moderate risk of bias (ROBINS-I V2)"
    if rob_overall == "Serious":
        serious_count = sum(1 for j in judgements if j == "Serious")
        if serious_count >= 2:
            return 2, f"Serious risk of bias in {serious_count} ROBINS-I V2 domains"
        return 1, "Serious risk of bias (ROBINS-I V2)"
    if rob_overall == "Critical":
        return 2, "Critical risk of bias (ROBINS-I V2)"

    # Legacy V1 stored results — V2 no longer produces "No information" overall
    if rob_overall == "No information":
        return 1, "No information in one or more ROBINS-I domains (conservative; legacy V1 result)"

    # QUADAS-3 branches (Low / High / Insufficient information). "Low" is
    # already handled at the top; "High" matches the RoB 2 branch above.
    if rob_overall == "Insufficient information":
        return 1, "Insufficient information in one or more QUADAS-3 domains (conservative)"

    return 1, f"risk of bias ({rob_overall})"


def compute_grade(initial: str,
                  rob_overall: str,
                  rob_domain_judgements: list[str] | None = None,
                  indirectness_levels: int = 0,
                  indirectness_explanation: str = "",
                  imprecision_levels: int = 0,
                  imprecision_explanation: str = "",
                  ) -> tuple[str, str]:
    """Compute updated GRADE + human-readable explanation.

    Downgrades for **risk of bias**, **indirectness**, and **imprecision**.
    Other GRADE domains (inconsistency, publication bias) still require a
    body of evidence and are documented as out of scope in the developer view.

    Per GradePro guidance:
      RoB 2     Low             → 0
                Some concerns   → 1
                High            → 1 (2 if ≥2 domains are High)
      ROBINS-I  Low             → 0
                Moderate        → 1
                Serious         → 1 (2 if ≥2 domains are Serious)
                Critical        → 2 (always)
                No information  → 1 (conservative)
      Indirectness  none / serious / very_serious / extremely_serious → 0/1/2/3
      Imprecision   none / serious / very_serious / extremely_serious → 0/1/2/3

    Total downgrade is the sum of the three, capped at "Very low" (3 levels
    below "High").
    """
    idx = _grade_index(initial)
    rob_levels, rob_reason = _rob_downgrade(rob_overall, rob_domain_judgements)
    indir_levels = max(0, int(indirectness_levels or 0))
    imprec_levels = max(0, int(imprecision_levels or 0))
    total = rob_levels + indir_levels + imprec_levels
    new_idx = min(idx + total, len(GRADE_LEVELS) - 1)
    new_level = GRADE_LEVELS[new_idx]

    if total == 0:
        clean_parts: list[str] = []
        if indir_levels == 0:
            clean_parts.append("no serious indirectness")
        if imprec_levels == 0:
            clean_parts.append("no serious imprecision")
        suffix = " and " + ", ".join(clean_parts) + " detected" if clean_parts else ""
        return new_level, f"No downgrade: overall risk of bias is Low{suffix}."

    parts: list[str] = []
    if rob_levels > 0:
        unit = "level" if rob_levels == 1 else "levels"
        parts.append(f"{rob_levels} {unit} for {rob_reason}")
    if indir_levels > 0:
        sev_label = {1: "serious", 2: "very serious",
                     3: "extremely serious"}.get(indir_levels, f"{indir_levels}-level")
        unit = "level" if indir_levels == 1 else "levels"
        suffix = f" — {indirectness_explanation}" if indirectness_explanation else ""
        parts.append(f"{indir_levels} {unit} for {sev_label} indirectness{suffix}")
    if imprec_levels > 0:
        sev_label = {1: "serious", 2: "very serious",
                     3: "extremely serious"}.get(imprec_levels, f"{imprec_levels}-level")
        unit = "level" if imprec_levels == 1 else "levels"
        suffix = f" — {imprecision_explanation}" if imprecision_explanation else ""
        parts.append(f"{imprec_levels} {unit} for {sev_label} imprecision{suffix}")

    total_unit = "level" if total == 1 else "levels"
    return new_level, f"Downgraded {total} {total_unit}: " + " + ".join(parts) + "."


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


def _appraise_paper_with_estimates(conn, run_id: int, paper_id: int,
                                    filename: str, pdf_bytes: bytes,
                                    fields: dict[str, str],
                                    classification: dict[str, str],
                                    primary_outcome: str,
                                    cfg: dict[str, Any],
                                    *,
                                    paper_estimates: list[dict[str, Any]],
                                    review_context: str | None,
                                    notify: Callable[[str, str], None],
                                    ) -> dict[str, Any]:
    """QUADAS-3 / per-estimate path. Runs the registered reporting guideline
    once per paper, then loops over estimates running the per-estimate RoB +
    applicability + GRADE pass and writing one ``quality_appraisal_results``
    row per (paper, estimate). Indirectness + imprecision are skipped per
    ``cfg.skip_grade_extras``.

    If ``paper_estimates`` is empty, falls back to a single-estimate
    assessment against the paper's primary / headline accuracy estimate.

    Returns a summary dict with aggregate fields across all estimates plus
    ``estimates_done`` / ``estimates_errored`` counts; the caller can refund
    per-estimate unit credits based on ``estimates_errored``.
    """
    study_type = classification.get("study_type", "Diagnostic Accuracy")
    rob_runner = _TOOL_RUNNERS.get(cfg["rob_tool"])
    if rob_runner is None:
        msg = f"RoB tool '{cfg['rob_tool']}' is registered but not yet implemented."
        _write_result(conn, run_id, paper_id, status="skipped",
                       error=msg, filename=filename,
                       study_type=study_type, classification=classification,
                       extracted_fields=fields)
        notify("warn", msg)
        return {"status": "skipped", "error": msg, "estimates_done": 0,
                "estimates_errored": 1}

    # Reporting guideline (once per paper)
    guide_runner = _GUIDELINE_RUNNERS.get(cfg["reporting_guideline"])
    if guide_runner is None:
        guideline = {"items": {}, "adhered": 0, "applicable": 0,
                      "total": 0, "proportion": 0.0,
                      "note": f"Guideline '{cfg['reporting_guideline']}' not implemented."}
    else:
        notify("info", f"Checking reporting guideline ({cfg['reporting_guideline']})")
        try:
            guideline = guide_runner(pdf_bytes, fields, classification)
        except HTTPException as he:
            msg = f"Reporting-guideline check failed: {he.detail}"
            logger.warning("Guideline check failed (run=%s paper=%s): %s",
                           run_id, paper_id, msg)
            guideline = {"items": {}, "adhered": 0, "applicable": 0,
                          "total": 0, "proportion": 0.0, "note": msg}
            notify("warn", msg)
        except Exception:
            logger.exception("Guideline run failed (run=%s paper=%s)", run_id, paper_id)
            guideline = {"items": {}, "adhered": 0, "applicable": 0,
                          "total": 0, "proportion": 0.0,
                          "note": "Reporting-guideline check failed — see server logs."}

    # Estimate list — fallback to single primary-estimate iteration if none supplied
    estimates_to_run: list[dict[str, Any] | None] = list(paper_estimates) if paper_estimates else [None]

    estimates_done = 0
    estimates_errored = 0
    last_summary: dict[str, Any] = {}

    for est in estimates_to_run:
        est_label = (est or {}).get("description", "primary estimate")
        notify("info", f"Running QUADAS-3 ({cfg['rob_tool']}) for: {est_label[:80]}")
        try:
            rob_domains, rob_overall, rob_direction, app_overall = rob_runner(
                pdf_bytes, fields, classification, primary_outcome,
                progress=lambda domain_id, _e=est_label: notify(
                    "progress", f"QUADAS-3 domain {domain_id} ({_e[:50]})"),
                estimate=est, review_context=review_context,
            )
        except HTTPException as he:
            msg = f"QUADAS-3 assessment failed for '{est_label}': {he.detail}"
            _write_result(conn, run_id, paper_id, status="error",
                           error=msg, filename=filename,
                           study_type=study_type, classification=classification,
                           extracted_fields=fields,
                           estimate_id=(est or {}).get("id"),
                           estimate=est)
            notify("error", msg)
            estimates_errored += 1
            continue
        except Exception:
            logger.exception("QUADAS-3 run failed (run=%s paper=%s)", run_id, paper_id)
            msg = f"QUADAS-3 assessment failed for '{est_label}' — see server logs."
            _write_result(conn, run_id, paper_id, status="error",
                           error=msg, filename=filename,
                           study_type=study_type, classification=classification,
                           extracted_fields=fields,
                           estimate_id=(est or {}).get("id"),
                           estimate=est)
            notify("error", msg)
            estimates_errored += 1
            continue

        # GRADE for this estimate (skip indirectness + imprecision per cfg)
        # ROBINS-I V2 stores preflight metadata under rob_domains["preflight"];
        # filter to dicts that actually carry a domain judgement.
        rob_domain_judgements = [
            d.get("judgement", "Low")
            for k, d in rob_domains.items()
            if k != "preflight" and isinstance(d, dict) and "judgement" in d
        ]
        initial_grade = cfg["initial_grade"]
        updated_grade, grade_expl = compute_grade(
            initial_grade, rob_overall, rob_domain_judgements,
            indirectness_levels=0, indirectness_explanation="",
            imprecision_levels=0, imprecision_explanation="")

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
                       applicability_overall=app_overall,
                       estimate_id=(est or {}).get("id"),
                       estimate=est,
                       guideline=guideline,
                       initial_grade=initial_grade,
                       updated_grade=updated_grade,
                       grade_explanation=grade_expl)
        notify("info",
                f"Done: {filename} — {est_label[:50]} — RoB {rob_overall}, "
                f"applicability {app_overall}, GRADE {initial_grade}→{updated_grade}")
        estimates_done += 1
        last_summary = {
            "rob_overall": rob_overall,
            "applicability_overall": app_overall,
            "initial_grade": initial_grade,
            "updated_grade": updated_grade,
        }

    overall_status = "ok" if estimates_done > 0 else "error"
    return {
        "status": overall_status,
        "filename": filename,
        "study_type": study_type,
        "estimates_done": estimates_done,
        "estimates_errored": estimates_errored,
        "guideline_proportion": guideline.get("proportion"),
        **last_summary,
    }


def appraise_paper(conn, papers_dir: Path, user_id: int, is_admin: bool,
                   run_id: int, paper_id: int,
                   on_progress: Callable[[str, str], None] | None = None,
                   target_pico: dict[str, str] | None = None,
                   imprecision_thresholds: dict[str, str] | None = None,
                   paper_estimates: list[dict[str, Any]] | None = None,
                   quadas3_review_context: str | None = None,
                   ) -> dict[str, Any]:
    """Appraise a single paper end-to-end. Writes one row to
    ``quality_appraisal_results`` per estimate (QUADAS-3 supports multiple
    estimates per paper; everything else writes exactly one row).

    Returns a summary dict for tests / callers. Raises nothing for
    paper-level failures — the failure is recorded on the row; the caller
    looks at ``status`` to decide whether to refund.

    ``target_pico`` is the user-supplied target PICO for indirectness
    assessment; ``None`` falls back to as-conducted PICO (outcome-surrogacy
    only is meaningful in that case).

    ``imprecision_thresholds`` is the user-supplied 2-threshold MID set for
    imprecision assessment; ``None`` falls back to line-of-no-effect +
    LLM-judged clinical importance.

    ``paper_estimates`` is the user-selected list of QUADAS-3 Phase-4
    estimate descriptors for this paper. Only consulted when the dispatched
    tool is ``quadas3``. Empty / None falls back to a single-estimate
    assessment against the paper's primary / headline accuracy estimate.

    ``quadas3_review_context`` is the user-supplied review-level context
    (Phases 1+2 — synthesis question + ideal test accuracy trial) for
    diagnostic-accuracy applicability assessment. Threaded into prompts.
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

    # 3.5 QUADAS-3 / per-estimate path. Diagnostic-accuracy papers can have
    # multiple Phase-4 estimates and produce one result row per estimate.
    # Indirectness + imprecision are skipped per cfg.skip_grade_extras (the
    # existing modules assume PICO/treatment trials, not PIRT diagnostic
    # accuracy).
    if cfg.get("supports_estimates"):
        return _appraise_paper_with_estimates(
            conn, run_id, paper_id, filename, pdf_bytes, fields,
            classification, primary_outcome, cfg,
            paper_estimates=paper_estimates or [],
            review_context=quadas3_review_context,
            notify=_notify)

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

    # 6. Indirectness (GRADE PICO assessment for this single trial)
    _notify("info", "Assessing GRADE indirectness")
    indirectness: dict[str, Any] = {}
    indirectness_overall = "none"
    indirectness_levels = 0
    indirectness_expl = ""
    try:
        indirectness, indirectness_overall, indirectness_levels, indirectness_expl = (
            indir_mod.run(pdf_bytes, fields, classification, primary_outcome,
                          target_pico=target_pico)
        )
    except HTTPException as he:
        msg = f"Indirectness assessment failed: {he.detail}"
        logger.warning("Indirectness failed (run=%s paper=%s): %s",
                       run_id, paper_id, msg)
        _notify("warn", msg)
        indirectness = {"error": msg}
    except Exception as e:
        logger.exception("Indirectness run failed (run=%s paper=%s)", run_id, paper_id)
        _notify("warn", "Indirectness assessment failed — see server logs.")
        indirectness = {"error": "Indirectness assessment failed."}

    # 6.5 Imprecision (GRADE single-trial assessment: CI / N / events / fragility)
    _notify("info", "Assessing GRADE imprecision")
    imprecision: dict[str, Any] = {}
    imprecision_overall = "none"
    imprecision_levels = 0
    imprecision_expl = ""
    try:
        imprecision, imprecision_overall, imprecision_levels, imprecision_expl = (
            imprec_mod.run(pdf_bytes, fields, classification, primary_outcome,
                            thresholds=imprecision_thresholds)
        )
    except HTTPException as he:
        msg = f"Imprecision assessment failed: {he.detail}"
        logger.warning("Imprecision failed (run=%s paper=%s): %s",
                       run_id, paper_id, msg)
        _notify("warn", msg)
        imprecision = {"error": msg}
    except Exception as e:
        logger.exception("Imprecision run failed (run=%s paper=%s)", run_id, paper_id)
        _notify("warn", "Imprecision assessment failed — see server logs.")
        imprecision = {"error": "Imprecision assessment failed."}

    # 7. GRADE — combines RoB + indirectness + imprecision downgrades
    # ROBINS-I V2 stores preflight metadata under rob_domains["preflight"];
    # filter to dicts that actually carry a domain judgement.
    rob_domain_judgements = [
        d.get("judgement", "Low")
        for k, d in rob_domains.items()
        if k != "preflight" and isinstance(d, dict) and "judgement" in d
    ]
    initial_grade = cfg["initial_grade"]
    updated_grade, grade_expl = compute_grade(
        initial_grade, rob_overall, rob_domain_judgements,
        indirectness_levels=indirectness_levels,
        indirectness_explanation=indirectness_expl,
        imprecision_levels=imprecision_levels,
        imprecision_explanation=imprecision_expl)

    # 8. Persist
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
                   indirectness=indirectness,
                   indirectness_overall=indirectness_overall,
                   indirectness_levels=indirectness_levels,
                   indirectness_explanation=indirectness_expl,
                   imprecision=imprecision,
                   imprecision_overall=imprecision_overall,
                   imprecision_levels=imprecision_levels,
                   imprecision_explanation=imprecision_expl,
                   initial_grade=initial_grade,
                   updated_grade=updated_grade,
                   grade_explanation=grade_expl)
    _notify("info", f"Done: {filename} — RoB {rob_overall}, indirectness {indirectness_overall}, imprecision {imprecision_overall}, GRADE {initial_grade}→{updated_grade}")
    return {
        "status": "ok",
        "filename": filename,
        "study_type": study_type,
        "rob_overall": rob_overall,
        "guideline_proportion": guideline.get("proportion"),
        "indirectness_overall": indirectness_overall,
        "indirectness_levels": indirectness_levels,
        "imprecision_overall": imprecision_overall,
        "imprecision_levels": imprecision_levels,
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
                  applicability_overall: str | None = None,
                  estimate_id: int | None = None,
                  estimate: dict | None = None,
                  guideline: dict | None = None,
                  indirectness: dict | None = None,
                  indirectness_overall: str | None = None,
                  indirectness_levels: int = 0,
                  indirectness_explanation: str | None = None,
                  imprecision: dict | None = None,
                  imprecision_overall: str | None = None,
                  imprecision_levels: int = 0,
                  imprecision_explanation: str | None = None,
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
                     applicability_overall, estimate_id, estimate_json,
                     guideline_json, guideline_proportion,
                     guideline_adhered, guideline_applicable,
                     indirectness_json, indirectness_overall,
                     indirectness_levels, indirectness_explanation,
                     imprecision_json, imprecision_overall,
                     imprecision_levels, imprecision_explanation,
                     initial_grade, updated_grade, grade_explanation)
               VALUES (?, ?, ?, ?, ?,  ?, ?, ?, ?,  ?, ?,  ?, ?, ?,  ?, ?, ?,  ?, ?,  ?, ?,  ?, ?, ?, ?,  ?, ?, ?, ?,  ?, ?, ?)""",
            (run_id, paper_id, status, error, filename,
             study_type, rob_tool, reporting_guideline, primary_outcome,
             json.dumps(classification or {}),
             json.dumps(extracted_fields or {}),
             json.dumps(rob_domains or {}),
             rob_overall, rob_direction,
             applicability_overall,
             estimate_id,
             json.dumps(estimate or {}),
             json.dumps(guideline or {}), proportion,
             guideline.get("adhered"), guideline.get("applicable"),
             json.dumps(indirectness or {}),
             indirectness_overall,
             int(indirectness_levels or 0),
             indirectness_explanation,
             json.dumps(imprecision or {}),
             imprecision_overall,
             int(imprecision_levels or 0),
             imprecision_explanation,
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

    # Mark run running and load the run-level params once.
    target_pico: dict[str, str] | None = None
    imprecision_thresholds: dict[str, str] | None = None
    quadas3_review_context: str | None = None
    paper_estimates_map: dict[str, list[dict[str, Any]]] = {}
    setup_conn = get_db_fn()
    try:
        with setup_conn:
            setup_conn.execute(
                "UPDATE quality_appraisal_runs SET status='running' WHERE id=?",
                (run_id,),
            )
            setup_conn.commit()
        try:
            row = setup_conn.execute(
                "SELECT target_pico_json, imprecision_thresholds_json, "
                "       quadas3_review_context, paper_estimates_json "
                "FROM quality_appraisal_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            if row and row["target_pico_json"]:
                tp = json.loads(row["target_pico_json"])
                if isinstance(tp, dict) and any(tp.get(k) for k in ("population", "intervention", "comparator", "outcome")):
                    target_pico = tp
            if row and row["imprecision_thresholds_json"]:
                it = json.loads(row["imprecision_thresholds_json"])
                if isinstance(it, dict) and any(it.get(k) for k in ("mid_benefit", "mid_harm")):
                    imprecision_thresholds = it
            if row and row["quadas3_review_context"]:
                rc = (row["quadas3_review_context"] or "").strip()
                if rc:
                    quadas3_review_context = rc
            if row and row["paper_estimates_json"]:
                try:
                    pe = json.loads(row["paper_estimates_json"])
                    if isinstance(pe, dict):
                        # Normalise to {str(paper_id): [estimate_dict, ...]}
                        for pid_key, est_list in pe.items():
                            if isinstance(est_list, list):
                                paper_estimates_map[str(pid_key)] = [
                                    e for e in est_list if isinstance(e, dict)]
                except Exception as e:
                    logger.warning("Failed to parse paper_estimates_json for run %s: %s", run_id, e)
        except Exception as e:
            logger.warning("Failed to load run params for run %s: %s", run_id, e)
        log_event(setup_conn, run_id, "info",
                   f"Started quality appraisal on {len(paper_ids)} paper(s).")
        if target_pico:
            log_event(setup_conn, run_id, "info",
                       "Indirectness will be assessed against the supplied target PICO.")
        if imprecision_thresholds:
            log_event(setup_conn, run_id, "info",
                       "Imprecision will be assessed against the supplied MID thresholds.")
        if quadas3_review_context:
            log_event(setup_conn, run_id, "info",
                       "QUADAS-3 applicability will be judged against the supplied review context.")
        if paper_estimates_map:
            total_estimates = sum(len(v) for v in paper_estimates_map.values())
            log_event(setup_conn, run_id, "info",
                       f"QUADAS-3 will run against {total_estimates} estimate(s) across {len(paper_estimates_map)} paper(s).")
    finally:
        setup_conn.close()

    for pid in paper_ids:
        pconn = get_db_fn()
        try:
            paper_estimates = paper_estimates_map.get(str(pid)) or []
            try:
                summary = appraise_paper(
                    pconn, papers_dir, user_id, is_admin, run_id, pid,
                    on_progress=lambda level, msg, _c=pconn: log_event(
                        _c, run_id, level, msg),
                    target_pico=target_pico,
                    imprecision_thresholds=imprecision_thresholds,
                    paper_estimates=paper_estimates,
                    quadas3_review_context=quadas3_review_context,
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

            # Refund: 1 unit-cost per failed/skipped (paper, estimate) tuple.
            # Non-QUADAS papers have at most 1 unit; QUADAS-3 papers have one
            # unit per estimate. The per_paper_cost is the unit cost.
            if not is_admin:
                if "estimates_errored" in summary:
                    # QUADAS-3 path — refund per failed estimate
                    failed = int(summary.get("estimates_errored") or 0)
                    if failed > 0:
                        try:
                            refund_amt = per_paper_cost * failed
                            bill_mod.refund_credits(
                                pconn, user_id, refund_amt,
                                f"Refund: QA run {run_id} paper {pid} ({failed} failed estimate(s))",
                            )
                            total_refunded += refund_amt
                            log_event(pconn, run_id, "info",
                                       f"Refunded {refund_amt} credits for paper {pid} ({failed} failed estimate(s)).")
                        except Exception as e:
                            logger.warning("Refund failed (run=%s paper=%s): %s", run_id, pid, e)
                elif summary.get("status") in ("error", "skipped"):
                    # Non-QUADAS path — refund the single per-paper cost
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
                "Run the GRADE indirectness assessment (per-trial PICO judgement)",
                "Run the GRADE imprecision assessment (CI width / sample size / events / fragility)",
                "Compute initial GRADE (from study type) and updated GRADE (downgrade for RoB + indirectness + imprecision)",
            ],
        },
        "registry": STUDY_TYPE_REGISTRY,
        "credit_cost_per_paper": CREDIT_COST_QA_PER_PAPER,
        "rob_tools": {
            "rob2":     rob2.prompt_catalog(),
            "robins_i": robins_i.prompt_catalog(),
            "quadas3":  quadas3.prompt_catalog(),
        },
        "reporting_guidelines": {
            "consort2025": consort2025.prompt_catalog(),
            "strobe":      strobe.prompt_catalog(),
            "stard":       stard.prompt_catalog(),
        },
        "indirectness": indir_mod.prompt_catalog(),
        "imprecision": imprec_mod.prompt_catalog(),
        "grade": {
            "levels": GRADE_LEVELS,
            "description": (
                "Downgrades for risk of bias, indirectness (per-trial PICO "
                "assessment, see Figure 1 of the GRADE handbook indirectness "
                "chapter), and imprecision (single-trial CI / sample size / "
                "event count / fragility, per the GRADE handbook imprecision "
                "chapter). Other GRADE domains (inconsistency, publication "
                "bias) still require a body of evidence and are out of scope "
                "for this single-study tool."
            ),
            "logic_code": inspect.getsource(compute_grade),
            "rob_downgrade_code": inspect.getsource(_rob_downgrade),
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
    indirectness = result_row.get("indirectness") or {}
    imprecision = result_row.get("imprecision") or {}

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
        "indirectness_overall": result_row.get("indirectness_overall"),
        "indirectness_levels": result_row.get("indirectness_levels"),
        "indirectness_explanation": result_row.get("indirectness_explanation"),
        "indirectness_population":  (indirectness.get("population")  or {}).get("judgement", ""),
        "indirectness_intervention":(indirectness.get("intervention")or {}).get("judgement", ""),
        "indirectness_comparator":  (indirectness.get("comparator")  or {}).get("judgement", ""),
        "indirectness_outcome":     (indirectness.get("outcome")     or {}).get("judgement", ""),
        "primary_outcome_is_surrogate": indirectness.get("primary_outcome_is_surrogate", ""),
        "imprecision_overall": result_row.get("imprecision_overall"),
        "imprecision_levels": result_row.get("imprecision_levels"),
        "imprecision_explanation": result_row.get("imprecision_explanation"),
        "imprecision_ci_width":     (imprecision.get("ci_width")     or {}).get("judgement", ""),
        "imprecision_sample_size":  (imprecision.get("sample_size")  or {}).get("judgement", ""),
        "imprecision_event_count":  (imprecision.get("event_count")  or {}).get("judgement", ""),
        "imprecision_fragility":    (imprecision.get("fragility")    or {}).get("judgement", ""),
        "imprecision_outcome_is_binary": imprecision.get("outcome_is_binary", ""),
        "initial_grade": result_row.get("initial_grade"),
        "updated_grade": result_row.get("updated_grade"),
        "grade_explanation": result_row.get("grade_explanation"),
    }
    # QUADAS-3-specific columns. Empty strings for non-QUADAS rows.
    estimate = result_row.get("estimate") or {}
    row["estimate_id"] = result_row.get("estimate_id")
    row["estimate_description"] = estimate.get("description", "")
    row["estimate_subgroup"] = estimate.get("subgroup", "")
    row["estimate_index_test"] = estimate.get("index_test", "")
    row["estimate_threshold"] = estimate.get("threshold", "")
    row["estimate_reference_standard"] = estimate.get("reference_standard", "")
    row["estimate_unit_of_analysis"] = estimate.get("unit_of_analysis", "")
    row["estimate_sensitivity"] = estimate.get("sensitivity", "")
    row["estimate_specificity"] = estimate.get("specificity", "")
    row["estimate_n"] = estimate.get("n", "")
    row["applicability_overall"] = result_row.get("applicability_overall") or ""

    # Per-domain judgements + all signaling-question answers. Dispatch the
    # DOMAINS list by rob_tool so ROBINS-I V2 rows get 6 domains, QUADAS-3
    # rows get 4, and the rest fall back to RoB 2's 5.
    tool = result_row.get("rob_tool")
    if tool == "robins_i":
        domains_for_tool = robins_i.DOMAINS
    elif tool == "quadas3":
        domains_for_tool = quadas3.DOMAINS
    else:
        domains_for_tool = rob2.DOMAINS

    # ROBINS-I V2 preflight columns (B1/B2/B3/C4 + variant + screening
    # decision). Empty for RoB 2 / QUADAS-3 rows.
    if tool == "robins_i":
        preflight = rob_domains.get("preflight") or {}
        row["robins_b1"] = preflight.get("B1", "")
        row["robins_b2"] = preflight.get("B2", "")
        row["robins_b3"] = preflight.get("B3", "")
        row["robins_c4"] = preflight.get("C4", "")
        row["robins_variant"] = preflight.get("variant") or (rob_domains.get("1") or {}).get("variant", "")
        row["robins_screening_decision"] = preflight.get("screening_decision", "")
        row["robins_screening_reason"] = preflight.get("screening_reason", "")

    for dom in domains_for_tool:
        d = rob_domains.get(str(dom["id"])) or {}
        row[f"rob_d{dom['id']}_judgement"] = d.get("judgement", "")
        # QUADAS-3 — also dump per-domain applicability (3 of 4 domains have it)
        if tool == "quadas3" and dom.get("has_applicability"):
            row[f"rob_d{dom['id']}_applicability"] = d.get("applicability_judgement", "")
        for sig in dom["signals"]:
            row[f"rob_{sig['id']}"] = (d.get("signals") or {}).get(sig["id"], "")
    return row
