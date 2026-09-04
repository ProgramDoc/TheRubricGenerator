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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException

from . import annotator as annotator_mod
from . import billing as bill_mod
from . import paper_files
from . import indirectness as indir_mod
from . import imprecision as imprec_mod
from . import outcomes as outcomes_mod
from .helpers import call_anthropic, parse_json_response
from .rob_tools import rob2, rob2_crossover, rob2_cluster, robins_i, robins_i_v1, quadas3, quadas2, amstar2
from .reporting_guidelines import consort2025, consort_crossover, consort_cluster, strobe, stard, prisma2020

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
    outcome_overrides_json      TEXT NOT NULL DEFAULT '{}',
    paper_outcomes_json         TEXT NOT NULL DEFAULT '{}',
    diagnostic_tool_choice      TEXT,
    robins_i_tool_choice        TEXT,
    rob2_cluster_aim            TEXT,
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
    assessed_outcome         TEXT,
    classification_json      TEXT    NOT NULL DEFAULT '{}',
    extracted_fields_json    TEXT    NOT NULL DEFAULT '{}',
    rob_domains_json         TEXT    NOT NULL DEFAULT '{}',
    rob_overall              TEXT,
    rob_direction            TEXT,
    applicability_overall    TEXT,
    estimate_id              INTEGER,
    estimate_json            TEXT    NOT NULL DEFAULT '{}',
    outcome_id               INTEGER,
    outcome_json             TEXT    NOT NULL DEFAULT '{}',
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
    # Cross-over RCT — RoB 2 cross-over extension (6 domains incl. Domain S
    # for period/carryover effects; Domain 5 has 4 signaling questions
    # including 5.4 for first-period-only reporting on the basis of a
    # carryover test). Reporting guideline is the combined CONSORT 2025 base
    # checklist plus the Dwan et al. 2019 cross-over extension items
    # (washout, period effects, sequence randomization, paired analysis, etc.).
    "Crossover Trial":              {"rob_tool": "rob2_crossover", "reporting_guideline": "consort_crossover", "initial_grade": "High"},
    # Cluster-randomized RCT — RoB 2 CRT extension (6 domains: 1a randomization
    # + 1b identification/recruitment-timing + 2-5; Domain 2 has an ITT and a
    # per-protocol variant selected per run via quality_appraisal_runs.rob2_cluster_aim).
    # Reporting guideline is CONSORT 2025 base plus the Campbell et al. 2012
    # cluster-randomised-trial extension items.
    "Cluster Randomized Trial":     {"rob_tool": "rob2_cluster",   "reporting_guideline": "consort_cluster",   "initial_grade": "High"},
    # Systematic reviews — AMSTAR-2 (Shea 2017) critical appraisal + PRISMA 2020
    # reporting checklist. AMSTAR-2 is structurally unlike the primary-study
    # tools: it scores 16 checklist items and emits an overall *confidence*
    # rating (High / Moderate / Low / Critically low), not a GRADE certainty.
    # ``skip_grade`` tells the orchestrator to skip indirectness, imprecision,
    # and the GRADE computation — those assess a body of evidence for an
    # outcome, not a review's methodological quality. ``initial_grade`` is
    # None (unused). AMSTAR-2 covers reviews with or without meta-analysis
    # (items 11/12/15 handle the "No meta-analysis conducted" case).
    "SR with Meta-Analysis":        {"rob_tool": "amstar2",        "reporting_guideline": "prisma2020",        "initial_grade": None, "skip_grade": True},
    "SR without Meta-Analysis":     {"rob_tool": "amstar2",        "reporting_guideline": "prisma2020",        "initial_grade": None, "skip_grade": True},
}

_TOOL_RUNNERS: dict[str, Callable] = {
    "rob2":           rob2.run,
    "rob2_crossover": rob2_crossover.run,
    "rob2_cluster":   rob2_cluster.run,
    "robins_i":       robins_i.run,       # V2 (20 Nov 2025 cribsheet) — default
    "robins_i_v1":    robins_i_v1.run,    # V1 (1 Aug 2016 cribsheet) — opt-in per run
    "quadas3":        quadas3.run,
    "quadas2":        quadas2.run,
    "amstar2":        amstar2.run,        # AMSTAR-2 — systematic reviews
}
_GUIDELINE_RUNNERS: dict[str, Callable] = {
    "consort2025":       consort2025.run,
    "consort_crossover": consort_crossover.run,
    "consort_cluster":   consort_cluster.run,
    "strobe":            strobe.run,
    "stard":             stard.run,
    "prisma2020":        prisma2020.run,   # PRISMA 2020 — systematic reviews
}
# Tools that support Phase-4 estimate extraction. Used by the run-create modal
# to pre-populate per-paper estimate selectors. Each value MUST be a callable
# of signature ``(pdf_bytes, extracted_fields) -> list[dict]``.
# QUADAS-2 aliases QUADAS-3's extractor — numerical sens/spec extraction is
# RoB-tool-agnostic, so a single shared prompt covers both.
_ESTIMATE_EXTRACTORS: dict[str, Callable] = {
    "quadas3": quadas3.extract_estimates,
    "quadas2": quadas3.extract_estimates,
}


def dispatch(study_type: str) -> dict[str, str] | None:
    """Return the appraisal config for a study type, or None if unsupported.

    Tolerant of surrounding whitespace and case drift in the classifier's
    output — an otherwise-valid study type must never fall through to
    "unsupported" because of formatting noise.
    """
    if not study_type:
        return None
    hit = STUDY_TYPE_REGISTRY.get(study_type)
    if hit is not None:
        return hit
    normalized = str(study_type).strip()
    hit = STUDY_TYPE_REGISTRY.get(normalized)
    if hit is not None:
        return hit
    lowered = normalized.lower()
    for key, cfg in STUDY_TYPE_REGISTRY.items():
        if key.lower() == lowered:
            return cfg
    return None


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
# Marginal cost of appraising one more outcome on the same paper. Classify,
# prefill, and the reporting-guideline check are once-per-paper and already paid
# for in the 36 above; only RoB (~15) + indirectness (~3) + imprecision (~3)
# repeat. Charging another full 36 would over-bill by ~42%.
CREDIT_COST_QA_ADDITIONAL_OUTCOME = 21


def paper_charge(n_estimates: int = 0, n_outcomes: int = 0) -> int:
    """What one paper costs. The single source of truth for the charge.

    Called by the run-create endpoint to charge and by ``run_batch`` to refund —
    they must not drift, which is why the arithmetic lives here rather than
    inline in either caller.

    The two fan-out axes are mutually exclusive (the API rejects a paper
    carrying both): diagnostic-accuracy papers bill a full unit per estimate,
    everything else bills one paper plus the marginal cost of each extra
    outcome.
    """
    if n_estimates > 0:
        return CREDIT_COST_QA_PER_PAPER * n_estimates
    return (CREDIT_COST_QA_PER_PAPER
            + CREDIT_COST_QA_ADDITIONAL_OUTCOME * max(0, n_outcomes - 1))


def refund_for(summary: dict[str, Any], charge: int, n_estimates: int = 0) -> tuple[int, str]:
    """How much of ``charge`` to give back, and why. Pure — no DB, no billing.

    Returns ``(amount, reason)``; ``amount`` is never more than ``charge``.
    """
    if n_estimates > 0 and "estimates_errored" in summary:
        # Diagnostic-accuracy path, unchanged: a flat unit per failed estimate.
        failed = int(summary.get("estimates_errored") or 0)
        return (CREDIT_COST_QA_PER_PAPER * failed,
                f"{failed} failed estimate(s)") if failed else (0, "")
    if "units_errored" in summary:
        failed = int(summary.get("units_errored") or 0)
        planned = int(summary.get("units_planned") or 0)
        skipped = int(summary.get("units_skipped") or 0)
        if planned and failed >= planned:
            # Nothing usable came out of this paper. Refunding the whole charge
            # preserves today's policy for the single-unit case.
            return charge, "no usable result"
        amount = CREDIT_COST_QA_ADDITIONAL_OUTCOME * (failed + skipped)
        if not amount:
            return 0, ""
        bits = []
        if failed:
            bits.append(f"{failed} failed")
        if skipped:
            bits.append(f"{skipped} not applicable")
        return min(amount, charge), f"{' + '.join(bits)} outcome unit(s)"
    if summary.get("status") in ("error", "skipped"):
        # Failed before the unit loop (load / classify / prefill / unsupported
        # study type). Nothing ran, so the whole charge goes back — a flat
        # per-paper refund would under-refund a multi-outcome paper.
        return charge, str(summary.get("status"))
    return 0, ""


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
        # Outcome override (reviewer can override the auto-picked primary outcome
        # — e.g., when the paper's stated primary is ambiguous).
        if not column_exists(conn, "quality_appraisal_runs", "outcome_overrides_json"):
            conn.execute("ALTER TABLE quality_appraisal_runs ADD COLUMN outcome_overrides_json TEXT NOT NULL DEFAULT '{}'")
        if not column_exists(conn, "quality_appraisal_results", "assessed_outcome"):
            conn.execute("ALTER TABLE quality_appraisal_results ADD COLUMN assessed_outcome TEXT")
        # QUADAS-2 alongside QUADAS-3 — per-run tool selection
        # (NULL = QUADAS-3 default for back-compat with existing rows)
        if not column_exists(conn, "quality_appraisal_runs", "diagnostic_tool_choice"):
            conn.execute("ALTER TABLE quality_appraisal_runs ADD COLUMN diagnostic_tool_choice TEXT")
        # ROBINS-I V1 alongside V2 — per-run tool selection
        # (NULL = V2 default for back-compat; "robins_i_v1" opts into V1 cribsheet
        #  for cohort/case-control/NRSI/cross-sectional/case-crossover papers.
        #  Single-arm study types ignore this — V1's cribsheet is cohort-only,
        #  so they stay on V2's single_arm variant regardless.)
        if not column_exists(conn, "quality_appraisal_runs", "robins_i_tool_choice"):
            conn.execute("ALTER TABLE quality_appraisal_runs ADD COLUMN robins_i_tool_choice TEXT")
        # RoB 2 Cluster per-run analysis aim — 'assignment' (intention-to-treat,
        # default) or 'adhering' (per-protocol). Selects the Domain 2 variant.
        # NULL = 'assignment' for back-compat with existing rows.
        if not column_exists(conn, "quality_appraisal_runs", "rob2_cluster_aim"):
            conn.execute("ALTER TABLE quality_appraisal_runs ADD COLUMN rob2_cluster_aim TEXT")
        # Per-(paper × outcome) fan-out. Risk-of-bias instruments are
        # outcome-specific, so a reviewer can select several outcomes per paper
        # and each produces its own result row. Empty map = the legacy
        # single-outcome behaviour (auto-pick, or the string override above).
        if not column_exists(conn, "quality_appraisal_runs", "paper_outcomes_json"):
            conn.execute("ALTER TABLE quality_appraisal_runs ADD COLUMN paper_outcomes_json TEXT NOT NULL DEFAULT '{}'")
        if not column_exists(conn, "quality_appraisal_results", "outcome_id"):
            conn.execute("ALTER TABLE quality_appraisal_results ADD COLUMN outcome_id INTEGER")
        if not column_exists(conn, "quality_appraisal_results", "outcome_json"):
            conn.execute("ALTER TABLE quality_appraisal_results ADD COLUMN outcome_json TEXT NOT NULL DEFAULT '{}'")
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

    # QUADAS-2 branches (Low / High / Unclear). "Low" and "High" share the
    # branches above; "Unclear" is QUADAS-2-specific.
    if rob_overall == "Unclear":
        return 1, "Unclear risk of bias in one or more QUADAS-2 domains (conservative)"

    return 1, f"risk of bias ({rob_overall})"


def compute_grade(initial: str,
                  rob_overall: str,
                  rob_domain_judgements: list[str] | None = None,
                  indirectness_levels: int = 0,
                  indirectness_explanation: str = "",
                  imprecision_levels: int = 0,
                  imprecision_explanation: str = "",
                  indirectness_assessed: bool = True,
                  imprecision_assessed: bool = True,
                  unassessed_note: str = "assessment failed",
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

    ``indirectness_assessed`` / ``imprecision_assessed`` say whether those
    domains were actually rated. An unassessed domain (the LLM call failed, or
    the tool defers the domain — e.g. diagnostic accuracy) contributes 0
    downgrade levels but the explanation carries an explicit caveat instead of
    claiming "no serious concerns" about something nobody assessed.
    ``unassessed_note`` names the reason in that caveat.
    """
    idx = _grade_index(initial)
    rob_levels, rob_reason = _rob_downgrade(rob_overall, rob_domain_judgements)
    indir_levels = max(0, int(indirectness_levels or 0))
    imprec_levels = max(0, int(imprecision_levels or 0))
    total = rob_levels + indir_levels + imprec_levels
    new_idx = min(idx + total, len(GRADE_LEVELS) - 1)
    new_level = GRADE_LEVELS[new_idx]

    missing = ([] if indirectness_assessed else ["indirectness"]) + \
              ([] if imprecision_assessed else ["imprecision"])
    caveat = ""
    if missing:
        verb = "were" if len(missing) > 1 else "was"
        pron = "them" if len(missing) > 1 else "it"
        caveat = (f" Note: {' and '.join(missing)} {verb} not assessed "
                  f"({unassessed_note}); this rating does not account for {pron} "
                  "and may overstate certainty.")

    if total == 0:
        clean_parts: list[str] = []
        if indir_levels == 0 and indirectness_assessed:
            clean_parts.append("no serious indirectness")
        if imprec_levels == 0 and imprecision_assessed:
            clean_parts.append("no serious imprecision")
        suffix = " and " + ", ".join(clean_parts) + " detected" if clean_parts else ""
        return new_level, f"No downgrade: overall risk of bias is Low{suffix}.{caveat}"

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
    return new_level, f"Downgraded {total} {total_unit}: " + " + ".join(parts) + f".{caveat}"


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


@dataclass(frozen=True)
class _Unit:
    """One assessment unit: everything the RoB + GRADE pass needs to run once.

    A paper fans out into several units on one of two mutually exclusive axes.
    Diagnostic-accuracy papers fan out per *estimate* (one estimate = one 2×2
    table); every other design fans out per *outcome*, because risk-of-bias
    instruments are outcome-specific. Both axes reduce to this descriptor, so
    the loop below is the same either way.
    """
    kind: str                    # "estimate" | "outcome"
    unit_id: int | None          # estimate_id / outcome_id (None = auto-pick fallback)
    payload: dict | None         # the estimate / outcome dict, stored on the row
    assessed_outcome: str        # threaded into every prompt
    is_override: bool            # not the paper's auto-picked primary outcome
    label: str                   # for the event log

    def columns(self) -> dict[str, Any]:
        """The ``_write_result`` kwargs identifying this unit's row."""
        if self.kind == "estimate":
            return {"estimate_id": self.unit_id, "estimate": self.payload}
        return {"outcome_id": self.unit_id, "outcome": self.payload}


def build_outcome_units(paper_outcomes: list[dict[str, Any]] | None,
                        outcome_override: str | None,
                        primary_outcome: str) -> list[_Unit]:
    """Resolve the outcome axis for one paper into units.

    Precedence: the reviewer's structured outcome list, then the legacy
    single-string override, then the auto-picked primary outcome. The last case
    is exactly today's behaviour and produces exactly one unit, so a run that
    supplies nothing is unchanged.
    """
    structured = [o for o in (paper_outcomes or []) if isinstance(o, dict)]
    if structured:
        units = []
        for oc in structured:
            label = outcomes_mod.outcome_label(oc) or primary_outcome
            units.append(_Unit(
                kind="outcome",
                unit_id=oc.get("id"),
                # Copied: run_batch_async is threaded and the map is shared
                # across papers, so units must never alias the caller's dicts.
                payload=dict(oc),
                assessed_outcome=label,
                is_override=label != primary_outcome,
                label=label,
            ))
        return units

    override_str = (outcome_override or "").strip()
    assessed = override_str or primary_outcome
    return [_Unit(kind="outcome", unit_id=None, payload=None,
                  assessed_outcome=assessed,
                  is_override=bool(override_str) and override_str != primary_outcome,
                  label=assessed)]


def build_estimate_units(paper_estimates: list[dict[str, Any]] | None,
                         outcome_override: str | None,
                         primary_outcome: str) -> list[_Unit]:
    """Resolve the diagnostic-accuracy estimate axis for one paper into units.

    One estimate = one 2×2 table = one unit. An empty list falls back to a
    single unit assessed against the paper's headline estimate.

    Every unit shares the paper's assessed outcome — the estimate descriptor,
    not the outcome, is what distinguishes them. The reviewer's outcome override
    is honoured here; earlier versions passed the auto-picked primary outcome
    instead, so an override on a diagnostic-accuracy paper did nothing.
    """
    override_str = (outcome_override or "").strip()
    assessed = override_str or primary_outcome
    is_override = bool(override_str) and override_str != primary_outcome
    estimates = [e for e in (paper_estimates or []) if isinstance(e, dict)]
    return [
        _Unit(kind="estimate",
              unit_id=(e or {}).get("id"),
              payload=dict(e) if e else None,
              assessed_outcome=assessed,
              is_override=is_override,
              label=(e or {}).get("description") or "primary estimate")
        for e in (estimates or [None])
    ]


def _run_guideline(cfg: dict[str, Any], pdf_bytes: bytes,
                   fields: dict[str, str], classification: dict[str, str],
                   run_id: int, paper_id: int,
                   notify: Callable[[str, str], None]) -> dict[str, Any]:
    """The reporting-guideline check. Never raises — a failure becomes a note."""
    guide_runner = _GUIDELINE_RUNNERS.get(cfg["reporting_guideline"])
    empty = {"items": {}, "adhered": 0, "applicable": 0, "total": 0, "proportion": 0.0}
    if guide_runner is None:
        return {**empty,
                "note": f"Guideline '{cfg['reporting_guideline']}' not implemented."}
    notify("info", f"Checking reporting guideline ({cfg['reporting_guideline']})")
    try:
        return guide_runner(pdf_bytes, fields, classification)
    except HTTPException as he:
        msg = f"Reporting-guideline check failed: {he.detail}"
        logger.warning("Guideline check failed (run=%s paper=%s): %s",
                       run_id, paper_id, msg)
        notify("warn", msg)
        return {**empty, "note": msg}
    except Exception:
        logger.exception("Guideline run failed (run=%s paper=%s)", run_id, paper_id)
        return {**empty,
                "note": "Reporting-guideline check failed — see server logs."}


# Tools returning (domains, overall, direction, applicability) rather than a
# 3-tuple, and the tools that accept the outcome-override framing kwarg.
_FOUR_TUPLE_TOOLS = ("quadas2", "quadas3")
_OVERRIDE_AWARE_TOOLS = ("rob2", "rob2_crossover", "rob2_cluster")


def _invoke_rob(rob_runner: Callable, tool: str, pdf_bytes: bytes,
                fields: dict[str, str], classification: dict[str, str],
                unit: _Unit, *, review_context: str | None,
                rob2_cluster_aim: str | None,
                notify: Callable[[str, str], None]) -> tuple:
    """Call a RoB tool and normalize its return to a 4-tuple.

    Absorbs the per-tool signature differences: the RoB 2 family takes the
    outcome-override framing flag (so Domain 1 can note that randomization is
    per-trial, not per-outcome); RoB 2 Cluster additionally takes the analysis
    aim; the QUADAS tools take the estimate descriptor and review context, and
    return applicability as a fourth element.
    """
    kwargs: dict[str, Any] = {
        "progress": lambda domain_id, _l=unit.label: notify(
            "progress", f"RoB {tool} domain {domain_id} ({_l[:50]})"),
    }
    if tool in _OVERRIDE_AWARE_TOOLS:
        kwargs["outcome_is_override"] = unit.is_override
    if tool == "rob2_cluster":
        kwargs["aim"] = ("adhering"
                         if str(rob2_cluster_aim or "").strip().lower() == "adhering"
                         else "assignment")
    if tool in _FOUR_TUPLE_TOOLS:
        kwargs["estimate"] = unit.payload if unit.kind == "estimate" else None
        kwargs["review_context"] = review_context

    out = rob_runner(pdf_bytes, fields, classification, unit.assessed_outcome,
                     **kwargs)
    return out if len(out) == 4 else (*out, None)


def _appraise_units(conn, run_id: int, paper_id: int, filename: str,
                    pdf_bytes: bytes, fields: dict[str, str],
                    classification: dict[str, str], primary_outcome: str,
                    cfg: dict[str, Any], units: list[_Unit], *,
                    target_pico: dict[str, str] | None,
                    imprecision_thresholds: dict[str, str] | None,
                    review_context: str | None,
                    rob2_cluster_aim: str | None,
                    units_skipped: int = 0,
                    notify: Callable[[str, str], None]) -> dict[str, Any]:
    """Run the RoB + GRADE pass once per unit, writing one result row each.

    Classification, field extraction, and the reporting-guideline check are
    once-per-paper (the guideline lazily, so a paper whose every RoB call fails
    never burns it). Risk of bias, indirectness, imprecision, and the GRADE
    computation run per unit.

    A unit that errors writes its own error row and the loop continues — one
    bad outcome must not discard the others. Returns ``units_planned`` /
    ``units_done`` / ``units_errored`` for the caller's refund arithmetic,
    plus the legacy ``estimates_*`` aliases on the estimate axis.
    """
    study_type = classification.get("study_type", "")
    tool = cfg["rob_tool"]
    rob_runner = _TOOL_RUNNERS.get(tool)
    if rob_runner is None:
        msg = f"RoB tool '{tool}' is registered but not yet implemented."
        _write_result(conn, run_id, paper_id, status="skipped",
                       error=msg, filename=filename,
                       study_type=study_type, classification=classification,
                       extracted_fields=fields)
        notify("warn", msg)
        return {"status": "skipped", "error": msg, "filename": filename,
                "study_type": study_type,
                "units_planned": len(units), "units_done": 0,
                "units_errored": len(units), "units_skipped": units_skipped}

    # Lazy so a paper whose RoB calls all fail doesn't pay for the guideline.
    _guideline_cache: list[dict[str, Any]] = []

    def guideline() -> dict[str, Any]:
        if not _guideline_cache:
            _guideline_cache.append(_run_guideline(
                cfg, pdf_bytes, fields, classification, run_id, paper_id, notify))
        return _guideline_cache[0]

    skip_grade = bool(cfg.get("skip_grade"))
    skip_extras = bool(cfg.get("skip_grade_extras"))
    done = errored = 0
    last_summary: dict[str, Any] = {}

    for unit in units:
        notify("info", f"Running risk-of-bias assessment ({tool}) for: {unit.label[:80]}")
        try:
            rob_domains, rob_overall, rob_direction, app_overall = _invoke_rob(
                rob_runner, tool, pdf_bytes, fields, classification, unit,
                review_context=review_context,
                rob2_cluster_aim=rob2_cluster_aim, notify=notify)
        except HTTPException as he:
            msg = f"Risk-of-bias assessment failed for '{unit.label[:60]}': {he.detail}"
            _write_result(conn, run_id, paper_id, status="error", error=msg,
                           filename=filename, study_type=study_type,
                           classification=classification, extracted_fields=fields,
                           **unit.columns())
            notify("error", msg)
            errored += 1
            continue
        except Exception:
            logger.exception("RoB run failed (run=%s paper=%s)", run_id, paper_id)
            msg = (f"Risk-of-bias assessment failed for '{unit.label[:60]}' "
                   "— see server logs.")
            _write_result(conn, run_id, paper_id, status="error", error=msg,
                           filename=filename, study_type=study_type,
                           classification=classification, extracted_fields=fields,
                           **unit.columns())
            notify("error", msg)
            errored += 1
            continue

        # GRADE pillar. Skipped wholesale for tools setting cfg["skip_grade"]
        # (AMSTAR-2): a systematic review's methodological quality is not a
        # GRADE certainty rating. skip_grade_extras (the QUADAS tools) keeps
        # GRADE but drops indirectness + imprecision, whose modules assume a
        # treatment-trial PICO rather than diagnostic-accuracy PIRT.
        indirectness: dict[str, Any] = {}
        indirectness_overall, indirectness_levels, indirectness_expl = "none", 0, ""
        imprecision: dict[str, Any] = {}
        imprecision_overall, imprecision_levels, imprecision_expl = "none", 0, ""
        # A failed (or skipped) assessment must never masquerade as a clean
        # "no serious concerns" pass: it stays at 0 downgrade levels (we never
        # invent a finding), but the overall is labelled "not_assessed" and the
        # GRADE explanation carries an explicit caveat instead of a reassurance.
        indirectness_assessed = imprecision_assessed = False
        initial_grade = updated_grade = grade_expl = None

        if not skip_grade and not skip_extras:
            notify("info", f"Assessing GRADE indirectness ({unit.label[:50]})")
            try:
                (indirectness, indirectness_overall, indirectness_levels,
                 indirectness_expl) = indir_mod.run(
                    pdf_bytes, fields, classification, unit.assessed_outcome,
                    target_pico=target_pico)
                indirectness_assessed = True
            except HTTPException as he:
                msg = f"Indirectness assessment failed: {he.detail}"
                logger.warning("Indirectness failed (run=%s paper=%s): %s",
                               run_id, paper_id, msg)
                notify("warn", msg)
                indirectness = {"error": msg}
                indirectness_overall = "not_assessed"
                indirectness_expl = ("Indirectness assessment failed — this "
                                     "domain was not rated.")
            except Exception:
                logger.exception("Indirectness run failed (run=%s paper=%s)",
                                 run_id, paper_id)
                notify("warn", "Indirectness assessment failed — see server logs.")
                indirectness = {"error": "Indirectness assessment failed."}
                indirectness_overall = "not_assessed"
                indirectness_expl = ("Indirectness assessment failed — this "
                                     "domain was not rated.")

            notify("info", f"Assessing GRADE imprecision ({unit.label[:50]})")
            try:
                (imprecision, imprecision_overall, imprecision_levels,
                 imprecision_expl) = imprec_mod.run(
                    pdf_bytes, fields, classification, unit.assessed_outcome,
                    thresholds=imprecision_thresholds,
                    # The paper-level primary_outcome_* fields describe the
                    # primary outcome only; consulting them for a secondary
                    # mis-types it (see imprecision.infer_outcome_is_binary).
                    outcome_is_primary=not unit.is_override,
                    outcome_type=(unit.payload or {}).get("outcome_type", ""))
                imprecision_assessed = True
            except HTTPException as he:
                msg = f"Imprecision assessment failed: {he.detail}"
                logger.warning("Imprecision failed (run=%s paper=%s): %s",
                               run_id, paper_id, msg)
                notify("warn", msg)
                imprecision = {"error": msg}
                imprecision_overall = "not_assessed"
                imprecision_expl = ("Imprecision assessment failed — this "
                                    "domain was not rated.")
            except Exception:
                logger.exception("Imprecision run failed (run=%s paper=%s)",
                                 run_id, paper_id)
                notify("warn", "Imprecision assessment failed — see server logs.")
                imprecision = {"error": "Imprecision assessment failed."}
                imprecision_overall = "not_assessed"
                imprecision_expl = ("Imprecision assessment failed — this "
                                    "domain was not rated.")

        if not skip_grade:
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
                imprecision_explanation=imprecision_expl,
                indirectness_assessed=indirectness_assessed,
                imprecision_assessed=imprecision_assessed,
                unassessed_note=(
                    "deferred for diagnostic accuracy — the indirectness and "
                    "imprecision modules assume a treatment-trial PICO"
                    if skip_extras else "assessment failed"))

        _write_result(conn, run_id, paper_id, status="ok", filename=filename,
                       study_type=study_type,
                       rob_tool=tool,
                       reporting_guideline=cfg["reporting_guideline"],
                       primary_outcome=primary_outcome,
                       assessed_outcome=unit.assessed_outcome,
                       classification=classification,
                       extracted_fields=fields,
                       rob_domains=rob_domains,
                       rob_overall=rob_overall,
                       rob_direction=rob_direction,
                       applicability_overall=app_overall,
                       guideline=guideline(),
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
                       grade_explanation=grade_expl,
                       **unit.columns())

        if skip_grade:
            notify("info", f"Done: {filename} — {tool} {rob_overall}, "
                           f"reporting-guideline adherence {guideline().get('proportion')}")
        elif skip_extras:
            notify("info", f"Done: {filename} — {unit.label[:50]} — RoB {rob_overall}, "
                           f"applicability {app_overall}, "
                           f"GRADE {initial_grade}→{updated_grade}")
        else:
            notify("info", f"Done: {filename} — {unit.label[:50]} — RoB {rob_overall}, "
                           f"indirectness {indirectness_overall}, "
                           f"imprecision {imprecision_overall}, "
                           f"GRADE {initial_grade}→{updated_grade}")
        done += 1
        last_summary = {
            "rob_overall": rob_overall,
            "indirectness_overall": indirectness_overall,
            "indirectness_levels": indirectness_levels,
            "imprecision_overall": imprecision_overall,
            "imprecision_levels": imprecision_levels,
            "initial_grade": initial_grade,
            "updated_grade": updated_grade,
        }
        if app_overall is not None:
            last_summary["applicability_overall"] = app_overall

    summary = {
        "status": "ok" if done > 0 else "error",
        "filename": filename,
        "study_type": study_type,
        "units_planned": len(units),
        "units_done": done,
        "units_errored": errored,
        "units_skipped": units_skipped,
        "guideline_proportion": (_guideline_cache[0].get("proportion")
                                 if _guideline_cache else None),
        **last_summary,
    }
    if units and units[0].kind == "estimate":
        # Legacy aliases — the estimate axis predates the unified loop.
        summary["estimates_done"] = done
        summary["estimates_errored"] = errored
    return summary


def appraise_rob_only(pdf_bytes: bytes, fields: dict, classification: dict,
                      assessed_outcome: str, cfg: dict, *,
                      outcome_is_override: bool = False,
                      rob2_cluster_aim: str | None = None,
                      progress: Callable[[Any], None] | None = None):
    """Run *only* the registered risk-of-bias tool for an already-classified
    paper, returning ``(rob_domains, rob_overall, rob_direction)``.

    This is the reusable RoB dispatch shared by :func:`appraise_paper` and the
    Synthesis pipeline (``backend/synthesis.py``), so risk-of-bias methodology
    stays single-sourced through ``STUDY_TYPE_REGISTRY`` + ``_TOOL_RUNNERS``.
    The caller supplies the already-computed ``classification`` and ``fields``
    (no re-classification / no double charge). Raises ``ValueError`` if the
    tool is registered but not implemented; propagates tool exceptions.
    """
    rob_runner = _TOOL_RUNNERS.get(cfg["rob_tool"])
    if rob_runner is None:
        raise ValueError(
            f"RoB tool '{cfg['rob_tool']}' is registered but not yet implemented.")
    rob_kwargs: dict[str, Any] = {}
    if progress is not None:
        rob_kwargs["progress"] = progress
    # rob2 / rob2_crossover / rob2_cluster accept outcome_is_override so Domain 1
    # can be framed around per-trial randomization for a reviewer-picked outcome.
    if cfg["rob_tool"] in ("rob2", "rob2_crossover", "rob2_cluster"):
        rob_kwargs["outcome_is_override"] = outcome_is_override
    # RoB 2 Cluster also takes the per-run analysis aim (ITT vs per-protocol).
    if cfg["rob_tool"] == "rob2_cluster":
        rob_kwargs["aim"] = (
            "adhering" if str(rob2_cluster_aim or "").strip().lower() == "adhering"
            else "assignment")
    return rob_runner(pdf_bytes, fields, classification, assessed_outcome, **rob_kwargs)


def appraise_paper(conn, papers_dir: Path, user_id: int, is_admin: bool,
                   run_id: int, paper_id: int,
                   on_progress: Callable[[str, str], None] | None = None,
                   target_pico: dict[str, str] | None = None,
                   imprecision_thresholds: dict[str, str] | None = None,
                   paper_estimates: list[dict[str, Any]] | None = None,
                   quadas3_review_context: str | None = None,
                   outcome_override: str | None = None,
                   paper_outcomes: list[dict[str, Any]] | None = None,
                   tool_override: str | None = None,
                   robins_i_tool_override: str | None = None,
                   rob2_cluster_aim: str | None = None,
                   ) -> dict[str, Any]:
    """Appraise a single paper end-to-end. Writes one ``quality_appraisal_results``
    row per assessment unit — one per Phase-4 estimate for diagnostic accuracy,
    one per selected outcome otherwise, and exactly one when neither is supplied.

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
    for diagnostic-accuracy applicability assessment (Phases 1+2 — synthesis
    question + ideal test accuracy trial when using QUADAS-3; PIRT review
    question when using QUADAS-2). Threaded into prompts.

    ``outcome_override`` lets the reviewer specify the outcome to assess when
    the paper's auto-picked primary outcome is unclear or ambiguous. When
    supplied, it replaces the auto-pick as the ``assessed_outcome`` passed to
    the RoB tool's prompts. The auto-pick is still recorded as
    ``primary_outcome`` for audit.

    ``paper_outcomes`` is the reviewer-selected list of outcomes to appraise
    separately, each producing its own result row. Risk-of-bias instruments are
    outcome-specific — RoB 2 domain 4 (measurement of the outcome) and domain 5
    (selection of the reported result) genuinely differ between outcomes — so
    one trial can be Low for mortality and High for an unblinded subjective
    outcome. Takes precedence over ``outcome_override``; empty / None keeps the
    single-outcome behaviour. Ignored for diagnostic accuracy, where the
    estimate is the outcome axis, and for tools that rate a whole review rather
    than an outcome (AMSTAR-2), which collapse back to one unit.

    ``tool_override`` selects between QUADAS-2 and QUADAS-3 for
    diagnostic-accuracy papers (``'quadas2'`` or ``'quadas3'``). When unset
    or set to anything other than a registered tool, the registry default
    (currently QUADAS-3) is used. Override is ignored for non-diagnostic
    study types so a stray param can't reroute an RCT to a QUADAS tool.

    ``robins_i_tool_override`` selects between ROBINS-I V2 and V1 for any
    ROBINS-I-dispatched paper (``'robins_i'`` for V2 (default) or
    ``'robins_i_v1'`` for V1). Applies to both cohort and single-arm study
    types — V1 ships its own single_arm variant (project-specific extension
    mirroring V2's single_arm adaptation for V1's 5-token vocab). Ignored
    for randomized / diagnostic study types.

    ``rob2_cluster_aim`` selects the RoB 2 CRT Domain 2 variant for
    cluster-randomized trials (``'assignment'`` for the intention-to-treat
    effect (default) or ``'adhering'`` for the per-protocol effect). Only
    consulted when the dispatched RoB tool is ``rob2_cluster``.
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

    # Canonicalize the classifier's study type against the registry vocabulary
    # (strip whitespace, repair case drift) BEFORE any routing: dispatch,
    # tool overrides, and the RoB tools' own study-type checks (e.g. the
    # ROBINS-I single-arm variant) all compare exactly.
    study_type = str(classification.get("study_type") or "").strip()
    for _key in STUDY_TYPE_REGISTRY:
        if _key.lower() == study_type.lower():
            study_type = _key
            break
    classification["study_type"] = study_type
    cfg = dispatch(study_type)
    # Per-run tool override for diagnostic-accuracy papers (QUADAS-2 vs
    # QUADAS-3). Shallow-copy the cfg so we never mutate the module-level
    # registry — critical because run_batch_async is multi-threaded.
    if cfg is not None and tool_override and study_type == "Diagnostic Accuracy":
        if tool_override in _TOOL_RUNNERS:
            cfg = {**cfg, "rob_tool": tool_override}
        else:
            logger.warning("Unknown tool_override %r for paper %s — falling back to %s",
                            tool_override, paper_id, cfg["rob_tool"])
    # Per-run tool override for ROBINS-I papers (V2 vs V1). Applies to both
    # cohort and single-arm study types — V1 now ships its own single-arm
    # variant (project-specific extension; see backend/rob_tools/robins_i_v1.py
    # SINGLE_ARM_STUDY_TYPES) that mirrors V2's pattern for V1's 5-token vocab.
    if (cfg is not None and robins_i_tool_override
            and cfg.get("rob_tool") == "robins_i"):
        if robins_i_tool_override in ("robins_i", "robins_i_v1"):
            cfg = {**cfg, "rob_tool": robins_i_tool_override}
        else:
            logger.warning("Unknown robins_i_tool_override %r for paper %s — keeping %s",
                            robins_i_tool_override, paper_id, cfg["rob_tool"])
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

    # 3.5 Build the assessment units. A paper fans out on exactly one axis:
    # diagnostic-accuracy papers per Phase-4 estimate (one estimate = one 2×2
    # table), everything else per outcome. Supplying neither yields one unit and
    # exactly today's behaviour.
    units_skipped = 0
    if cfg.get("supports_estimates"):
        units = build_estimate_units(paper_estimates, outcome_override,
                                     primary_outcome)
    elif cfg.get("skip_grade"):
        # AMSTAR-2: the tool scores the review as a whole, not an outcome, so
        # the outcome axis does not apply. Collapse to one unit; the extra
        # units the reviewer was charged for are reported back for refund.
        units = build_outcome_units(None, outcome_override, primary_outcome)
        structured = [o for o in (paper_outcomes or []) if isinstance(o, dict)]
        units_skipped = max(0, len(structured) - 1)
        if units_skipped:
            _notify("info", f"{cfg['rob_tool']} rates the review as a whole — "
                            f"{units_skipped} extra outcome(s) not assessed")
    else:
        units = build_outcome_units(paper_outcomes, outcome_override, primary_outcome)

    if len(units) > 1:
        _notify("info", f"Assessing {len(units)} outcomes for this paper")
    elif units and units[0].is_override:
        _notify("info",
                 f"Assessed outcome (reviewer override): {units[0].assessed_outcome[:80]}")
    else:
        _notify("info", f"Primary outcome: {primary_outcome[:80]}")

    return _appraise_units(
        conn, run_id, paper_id, filename, pdf_bytes, fields,
        classification, primary_outcome, cfg, units,
        target_pico=target_pico,
        imprecision_thresholds=imprecision_thresholds,
        review_context=quadas3_review_context,
        rob2_cluster_aim=rob2_cluster_aim,
        units_skipped=units_skipped,
        notify=_notify)



def _write_result(conn, run_id: int, paper_id: int, *,
                  status: str, error: str | None = None,
                  filename: str | None = None,
                  study_type: str | None = None,
                  rob_tool: str | None = None,
                  reporting_guideline: str | None = None,
                  primary_outcome: str | None = None,
                  assessed_outcome: str | None = None,
                  classification: dict | None = None,
                  extracted_fields: dict | None = None,
                  rob_domains: dict | None = None,
                  rob_overall: str | None = None,
                  rob_direction: str | None = None,
                  applicability_overall: str | None = None,
                  estimate_id: int | None = None,
                  estimate: dict | None = None,
                  outcome_id: int | None = None,
                  outcome: dict | None = None,
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
                     assessed_outcome,
                     classification_json, extracted_fields_json,
                     rob_domains_json, rob_overall, rob_direction,
                     applicability_overall, estimate_id, estimate_json,
                     outcome_id, outcome_json,
                     guideline_json, guideline_proportion,
                     guideline_adhered, guideline_applicable,
                     indirectness_json, indirectness_overall,
                     indirectness_levels, indirectness_explanation,
                     imprecision_json, imprecision_overall,
                     imprecision_levels, imprecision_explanation,
                     initial_grade, updated_grade, grade_explanation)
               VALUES (?, ?, ?, ?, ?,  ?, ?, ?, ?, ?,  ?, ?,  ?, ?, ?,  ?, ?, ?,  ?, ?,  ?, ?,  ?, ?,  ?, ?, ?, ?,  ?, ?, ?, ?,  ?, ?, ?)""",
            (run_id, paper_id, status, error, filename,
             study_type, rob_tool, reporting_guideline, primary_outcome,
             assessed_outcome,
             json.dumps(classification or {}),
             json.dumps(extracted_fields or {}),
             json.dumps(rob_domains or {}),
             rob_overall, rob_direction,
             applicability_overall,
             estimate_id,
             json.dumps(estimate or {}),
             outcome_id,
             json.dumps(outcome or {}),
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
    total_refunded = 0

    # Mark run running and load the run-level params once.
    target_pico: dict[str, str] | None = None
    imprecision_thresholds: dict[str, str] | None = None
    quadas3_review_context: str | None = None
    diagnostic_tool_choice: str | None = None
    robins_i_tool_choice: str | None = None
    rob2_cluster_aim: str | None = None
    paper_estimates_map: dict[str, list[dict[str, Any]]] = {}
    paper_outcomes_map: dict[str, list[dict[str, Any]]] = {}
    outcome_overrides_map: dict[str, str] = {}
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
                "       quadas3_review_context, paper_estimates_json, "
                "       paper_outcomes_json, "
                "       outcome_overrides_json, diagnostic_tool_choice, "
                "       robins_i_tool_choice, rob2_cluster_aim "
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
            if row and row["diagnostic_tool_choice"]:
                dtc = (row["diagnostic_tool_choice"] or "").strip().lower()
                if dtc in ("quadas2", "quadas3"):
                    diagnostic_tool_choice = dtc
            if row and row["robins_i_tool_choice"]:
                rtc = (row["robins_i_tool_choice"] or "").strip().lower()
                if rtc in ("robins_i", "robins_i_v1"):
                    robins_i_tool_choice = rtc
            if row and row["rob2_cluster_aim"]:
                rca = (row["rob2_cluster_aim"] or "").strip().lower()
                if rca in ("assignment", "adhering"):
                    rob2_cluster_aim = rca
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
            if row and row["paper_outcomes_json"]:
                try:
                    po = json.loads(row["paper_outcomes_json"])
                    if isinstance(po, dict):
                        # Normalise to {str(paper_id): [outcome_dict, ...]}
                        for pid_key, oc_list in po.items():
                            if isinstance(oc_list, list):
                                paper_outcomes_map[str(pid_key)] = [
                                    o for o in oc_list if isinstance(o, dict)]
                except Exception as e:
                    logger.warning("Failed to parse paper_outcomes_json for run %s: %s", run_id, e)
            if row and row["outcome_overrides_json"]:
                try:
                    oo = json.loads(row["outcome_overrides_json"])
                    if isinstance(oo, dict):
                        for pid_key, val in oo.items():
                            if isinstance(val, str) and val.strip():
                                outcome_overrides_map[str(pid_key)] = val.strip()
                except Exception as e:
                    logger.warning("Failed to parse outcome_overrides_json for run %s: %s", run_id, e)
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
                       "Diagnostic-accuracy applicability will be judged against the supplied review context.")
        if diagnostic_tool_choice:
            log_event(setup_conn, run_id, "info",
                       f"Diagnostic-accuracy papers will use {diagnostic_tool_choice.upper()}.")
        if robins_i_tool_choice:
            label = "ROBINS-I V1 (1 Aug 2016 cribsheet)" if robins_i_tool_choice == "robins_i_v1" else "ROBINS-I V2 (20 Nov 2025 cribsheet)"
            log_event(setup_conn, run_id, "info",
                       f"Non-randomized cohort-type papers will use {label}.")
        if rob2_cluster_aim:
            aim_label = ("the per-protocol effect (effect of adhering to intervention)"
                         if rob2_cluster_aim == "adhering"
                         else "the intention-to-treat effect (effect of assignment to intervention)")
            log_event(setup_conn, run_id, "info",
                       f"Cluster-randomized trials will assess {aim_label}.")
        if paper_estimates_map:
            total_estimates = sum(len(v) for v in paper_estimates_map.values())
            log_event(setup_conn, run_id, "info",
                       f"QUADAS-3 will run against {total_estimates} estimate(s) across {len(paper_estimates_map)} paper(s).")
        if paper_outcomes_map:
            total_outcomes = sum(len(v) for v in paper_outcomes_map.values())
            log_event(setup_conn, run_id, "info",
                       f"Appraising {total_outcomes} outcome(s) across "
                       f"{len(paper_outcomes_map)} paper(s) — one result row each.")
        if outcome_overrides_map:
            log_event(setup_conn, run_id, "info",
                       f"Outcome overrides supplied for {len(outcome_overrides_map)} paper(s).")
    finally:
        setup_conn.close()

    for pid in paper_ids:
        pconn = get_db_fn()
        try:
            paper_estimates = paper_estimates_map.get(str(pid)) or []
            paper_outcomes = paper_outcomes_map.get(str(pid)) or []
            outcome_override = outcome_overrides_map.get(str(pid))
            charge = paper_charge(n_estimates=len(paper_estimates),
                                  n_outcomes=len(paper_outcomes))
            try:
                summary = appraise_paper(
                    pconn, papers_dir, user_id, is_admin, run_id, pid,
                    on_progress=lambda level, msg, _c=pconn: log_event(
                        _c, run_id, level, msg),
                    target_pico=target_pico,
                    imprecision_thresholds=imprecision_thresholds,
                    paper_estimates=paper_estimates,
                    quadas3_review_context=quadas3_review_context,
                    outcome_override=outcome_override,
                    paper_outcomes=paper_outcomes,
                    tool_override=diagnostic_tool_choice,
                    robins_i_tool_override=robins_i_tool_choice,
                    rob2_cluster_aim=rob2_cluster_aim,
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

            # Refund whatever part of this paper's charge produced no result.
            # The arithmetic lives in refund_for / paper_charge so it cannot
            # drift from what the run-create endpoint charged.
            if not is_admin:
                refund_amt, reason = refund_for(
                    summary, charge, n_estimates=len(paper_estimates))
                if refund_amt > 0:
                    try:
                        bill_mod.refund_credits(
                            pconn, user_id, refund_amt,
                            f"Refund: Quality Appraisal run {run_id} paper {pid} ({reason})",
                        )
                        total_refunded += refund_amt
                        log_event(pconn, run_id, "info",
                                   f"Refunded {refund_amt} credits for paper {pid} ({reason}).")
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
            "rob2":           rob2.prompt_catalog(),
            "rob2_crossover": rob2_crossover.prompt_catalog(),
            "rob2_cluster":   rob2_cluster.prompt_catalog(),
            "robins_i":       robins_i.prompt_catalog(),
            "robins_i_v1":    robins_i_v1.prompt_catalog(),
            "quadas3":        quadas3.prompt_catalog(),
            "quadas2":        quadas2.prompt_catalog(),
            "amstar2":        amstar2.prompt_catalog(),
        },
        "reporting_guidelines": {
            "consort2025":       consort2025.prompt_catalog(),
            "consort_crossover": consort_crossover.prompt_catalog(),
            "consort_cluster":   consort_cluster.prompt_catalog(),
            "strobe":            strobe.prompt_catalog(),
            "stard":             stard.prompt_catalog(),
            "prisma2020":        prisma2020.prompt_catalog(),
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
        # One paper can produce several rows (one per outcome or per estimate),
        # so paper_id alone is not a key for downstream joins.
        "result_id": result_row.get("id"),
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
        "assessed_outcome": result_row.get("assessed_outcome"),
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

    # Per-outcome columns. Emitted unconditionally (empty for single-outcome and
    # diagnostic rows) because csv.DictWriter takes its header from the first row
    # and raises on any later row carrying extra keys.
    outcome = result_row.get("outcome") or {}
    row["outcome_id"] = result_row.get("outcome_id")
    # The clean short label downstream consumers group studies by. Falls back to
    # assessed_outcome so legacy and diagnostic rows still carry an outcome
    # string, though that one is composed and less suited to matching.
    row["outcome_label"] = (outcome.get("name", "")
                            or result_row.get("assessed_outcome") or "")
    row["outcome_description"] = outcome.get("description", "")
    row["outcome_measure"] = outcome.get("measure", "")
    row["outcome_timing"] = outcome.get("timing", "")
    row["outcome_type"] = outcome.get("outcome_type", "")
    row["outcome_is_primary"] = outcome.get("is_primary", "")
    row["outcome_source"] = outcome.get("source", "")

    # Per-domain judgements + all signaling-question answers. Dispatch the
    # DOMAINS list by rob_tool so ROBINS-I V2 rows get 6 domains, QUADAS-2
    # rows get 4 (different signal IDs from QUADAS-3), QUADAS-3 rows get 4,
    # and the rest fall back to RoB 2's 5.
    tool = result_row.get("rob_tool")
    if tool == "robins_i":
        domains_for_tool = robins_i.DOMAINS
    elif tool == "robins_i_v1":
        # Pick the cohort DOMAINS list as the base, then append SA-only
        # signals for D1 + D2 so a CSV that includes both cohort and SA rows
        # gets the union of columns (matches the V2 pattern in robins_i.py
        # where DOMAIN1 signals = A + B + single_arm union).
        domains_for_tool = []
        for d in robins_i_v1.DOMAINS:
            if d["id"] == 1 and d.get("signals_single_arm"):
                domains_for_tool.append({
                    **d,
                    "signals": d["signals"] + d["signals_single_arm"],
                })
            elif d["id"] == 2 and d.get("signals_single_arm"):
                domains_for_tool.append({
                    **d,
                    "signals": d["signals"] + d["signals_single_arm"],
                })
            else:
                domains_for_tool.append(d)
    elif tool == "quadas3":
        domains_for_tool = quadas3.DOMAINS
    elif tool == "quadas2":
        domains_for_tool = quadas2.DOMAINS
    elif tool == "rob2_crossover":
        domains_for_tool = rob2_crossover.DOMAINS
    elif tool == "rob2_cluster":
        # Domain 2 signal IDs differ by analysis aim — pick the variant the
        # row actually used (run() records it under rob_domains["aim"]).
        domains_for_tool = rob2_cluster.domains_for_aim(
            rob_domains.get("aim") or "assignment")
    elif tool == "amstar2":
        # AMSTAR-2 — the 16 checklist items (each emits rob_d{id}_judgement +
        # rob_{signal} columns through the generic loop below).
        domains_for_tool = amstar2.ITEMS
    else:
        domains_for_tool = rob2.DOMAINS

    # ROBINS-I V2 preflight columns (B1/B2/B3/C4 + variant + screening
    # decision). Empty for RoB 2 / QUADAS rows.
    if tool == "robins_i":
        preflight = rob_domains.get("preflight") or {}
        row["robins_b1"] = preflight.get("B1", "")
        row["robins_b2"] = preflight.get("B2", "")
        row["robins_b3"] = preflight.get("B3", "")
        row["robins_c4"] = preflight.get("C4", "")
        row["robins_variant"] = preflight.get("variant") or (rob_domains.get("1") or {}).get("variant", "")
        row["robins_screening_decision"] = preflight.get("screening_decision", "")
        row["robins_screening_reason"] = preflight.get("screening_reason", "")
    # ROBINS-I V1 aim-preflight columns (§1.1 — auto-determined Stage-II aim
    # of study + rationale). Empty for V2 / RoB 2 / QUADAS rows. The
    # single-arm path of V1 stashes aim_preflight = None and uses preflight
    # instead (mirroring V2) — also emit the SA preflight columns so a single
    # CSV can carry both V1 cohort + V1 SA rows.
    if tool == "robins_i_v1":
        aim_pf = rob_domains.get("aim_preflight") or {}
        row["robins_v1_aim"] = aim_pf.get("aim", "")
        row["robins_v1_aim_rationale"] = aim_pf.get("rationale", "")
        preflight = rob_domains.get("preflight") or {}
        row["robins_v1_b1"] = preflight.get("B1", "")
        row["robins_v1_b2"] = preflight.get("B2", "")
        row["robins_v1_b3"] = preflight.get("B3", "")
        row["robins_v1_c4"] = preflight.get("C4", "")
        row["robins_v1_variant"] = preflight.get("variant") or ("cohort" if aim_pf else "")
        row["robins_v1_screening_decision"] = preflight.get("screening_decision", "")
        row["robins_v1_screening_reason"] = preflight.get("screening_reason", "")
    # AMSTAR-2 preflight columns (review composition + meta-analysis) + the
    # overall confidence rating + critical / non-critical flaw counts. Empty
    # for non-systematic-review rows.
    if tool == "amstar2":
        preflight = rob_domains.get("preflight") or {}
        row["amstar2_confidence"] = result_row.get("rob_overall") or ""
        row["amstar2_review_includes"] = preflight.get("review_includes", "")
        row["amstar2_meta_analysis"] = preflight.get("meta_analysis", "")
        row["amstar2_critical_flaws"] = sum(
            1 for it in amstar2.ITEMS if it["critical"]
            and (rob_domains.get(str(it["id"])) or {}).get("judgement") == "No")
        row["amstar2_noncritical_weaknesses"] = sum(
            1 for it in amstar2.ITEMS if not it["critical"]
            and (rob_domains.get(str(it["id"])) or {}).get("judgement") == "No")

    for dom in domains_for_tool:
        d = rob_domains.get(str(dom["id"])) or {}
        row[f"rob_d{dom['id']}_judgement"] = d.get("judgement", "")
        # QUADAS-2 / QUADAS-3 — also dump per-domain applicability (3 of 4
        # domains have it; Flow & Timing / Analysis are RoB-only).
        if tool in ("quadas2", "quadas3") and dom.get("has_applicability"):
            row[f"rob_d{dom['id']}_applicability"] = d.get("applicability_judgement", "")
        for sig in dom["signals"]:
            row[f"rob_{sig['id']}"] = (d.get("signals") or {}).get(sig["id"], "")
    return row
