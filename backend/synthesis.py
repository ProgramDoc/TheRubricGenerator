"""Synthesis — systematic review + meta-analysis orchestration.

Mirrors the Quality Appraisal architecture (``backend/quality_appraisal.py``):
a run/results/events table family, a daemon-thread batch worker with
events-polling progress, a credit gate + refund, and a developer-view
``prompt_catalog``. The novel parts live in ``backend/synthesis_stats.py``
(the pure meta-analysis engine) and ``backend/synthesis_codegen.py`` (the
R/Python code emitter).

Pipeline per review: derive eligibility -> screen each paper -> extract
outcome data for included studies -> auto-run risk of bias (reusing the QA
RoB tools via ``quality_appraisal.appraise_rob_only``) -> pool per outcome
(fixed + random) + heterogeneity + publication bias + subgroup +
meta-regression + sensitivity -> body-of-evidence GRADE.
"""

from __future__ import annotations

import inspect
import json
import logging
import threading
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException

from backend import annotator as annotator_mod
from backend import billing as bill_mod
from backend import quality_appraisal as qa_mod
from backend import synthesis_codegen as codegen
from backend import synthesis_stats as stats
from backend.helpers import call_anthropic, parse_json_response

logger = logging.getLogger("rubricgen")


# ─────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────
SYNTHESIS_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS synthesis_reviews (
    id                        SERIAL PRIMARY KEY,
    user_id                   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id                INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    title                     TEXT,
    paper_ids_json            TEXT NOT NULL DEFAULT '[]',
    paper_count               INTEGER NOT NULL DEFAULT 0,
    status                    TEXT NOT NULL DEFAULT 'pending',
    phase                     TEXT,
    pico_json                 TEXT,
    eligibility_criteria_json TEXT,
    run_rob                   INTEGER NOT NULL DEFAULT 1,
    rob_scope                 TEXT,
    prisma_manual_counts_json TEXT NOT NULL DEFAULT '{}',
    credit_cost               INTEGER NOT NULL DEFAULT 0,
    credits_refunded          INTEGER NOT NULL DEFAULT 0,
    error_message             TEXT,
    created_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at              TIMESTAMP,
    deleted_at                TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_synth_reviews_user_created
    ON synthesis_reviews(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS synthesis_studies (
    id                      SERIAL PRIMARY KEY,
    review_id               INTEGER NOT NULL REFERENCES synthesis_reviews(id) ON DELETE CASCADE,
    paper_id                INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    filename                TEXT,
    status                  TEXT NOT NULL DEFAULT 'pending',
    classification_json     TEXT NOT NULL DEFAULT '{}',
    study_type              TEXT,
    screening_decision      TEXT,
    screening_reason        TEXT,
    prisma_exclusion_reason TEXT,
    screening_confidence    TEXT,
    screening_json          TEXT NOT NULL DEFAULT '{}',
    decision_overridden     INTEGER NOT NULL DEFAULT 0,
    rob_tool                TEXT,
    rob_overall             TEXT,
    rob_direction           TEXT,
    rob_domains_json        TEXT NOT NULL DEFAULT '{}',
    qa_run_id               INTEGER,
    error_message           TEXT,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_synth_studies_review ON synthesis_studies(review_id);

CREATE TABLE IF NOT EXISTS synthesis_outcomes (
    id                   SERIAL PRIMARY KEY,
    review_id            INTEGER NOT NULL REFERENCES synthesis_reviews(id) ON DELETE CASCADE,
    name                 TEXT NOT NULL,
    outcome_type         TEXT NOT NULL,
    effect_measure       TEXT NOT NULL,
    model_choice         TEXT NOT NULL DEFAULT 'random',
    tau2_method          TEXT NOT NULL DEFAULT 'REML',
    fe_method            TEXT,
    continuity_correction REAL NOT NULL DEFAULT 0.5,
    re_ci_method         TEXT NOT NULL DEFAULT 'wald',
    subgroup_field       TEXT,
    mid_benefit          TEXT,
    mid_harm             TEXT,
    sort_order           INTEGER NOT NULL DEFAULT 0,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_synth_outcomes_review ON synthesis_outcomes(review_id);

CREATE TABLE IF NOT EXISTS synthesis_data_points (
    id                   SERIAL PRIMARY KEY,
    review_id            INTEGER NOT NULL REFERENCES synthesis_reviews(id) ON DELETE CASCADE,
    outcome_id           INTEGER NOT NULL REFERENCES synthesis_outcomes(id) ON DELETE CASCADE,
    study_id             INTEGER NOT NULL REFERENCES synthesis_studies(id) ON DELETE CASCADE,
    context_label        TEXT,
    included_in_pool     INTEGER NOT NULL DEFAULT 1,
    raw_json             TEXT NOT NULL DEFAULT '{}',
    subgroup_value       TEXT,
    moderator_json       TEXT NOT NULL DEFAULT '{}',
    yi                   REAL,
    vi                   REAL,
    continuity_applied   INTEGER NOT NULL DEFAULT 0,
    extraction_confidence TEXT,
    needs_review         INTEGER NOT NULL DEFAULT 0,
    edited_by_user       INTEGER NOT NULL DEFAULT 0,
    source_quote         TEXT,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_synth_data_outcome ON synthesis_data_points(outcome_id);
CREATE INDEX IF NOT EXISTS idx_synth_data_review ON synthesis_data_points(review_id);

-- One risk-of-bias judgement per (study x outcome). RoB 2 and ROBINS-I are
-- outcome-specific instruments -- missing outcome data, measurement of the outcome,
-- and selection of the reported result genuinely differ between outcomes -- so one
-- trial can be Low for mortality and High for an unblinded symptom score.
--
-- Deliberately not columns on synthesis_data_points: that table holds one row per
-- clinical context (comparison x timepoint x subgroup), so a judgement stored there
-- would be duplicated across rows that can be edited and dropped independently. It
-- also has no row at all when extraction returns nothing, which is exactly when a
-- failed-RoB record still needs somewhere to live.
CREATE TABLE IF NOT EXISTS synthesis_study_rob (
    id               SERIAL PRIMARY KEY,
    review_id        INTEGER NOT NULL REFERENCES synthesis_reviews(id) ON DELETE CASCADE,
    study_id         INTEGER NOT NULL REFERENCES synthesis_studies(id) ON DELETE CASCADE,
    outcome_id       INTEGER NOT NULL REFERENCES synthesis_outcomes(id) ON DELETE CASCADE,
    assessed_outcome TEXT,
    rob_tool         TEXT,
    rob_overall      TEXT,
    rob_direction    TEXT,
    rob_domains_json TEXT NOT NULL DEFAULT '{}',
    status           TEXT NOT NULL DEFAULT 'ok',
    error_message    TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (study_id, outcome_id)
);
CREATE INDEX IF NOT EXISTS idx_synth_study_rob_review  ON synthesis_study_rob(review_id);
CREATE INDEX IF NOT EXISTS idx_synth_study_rob_outcome ON synthesis_study_rob(outcome_id);

CREATE TABLE IF NOT EXISTS synthesis_results (
    id                   SERIAL PRIMARY KEY,
    review_id            INTEGER NOT NULL REFERENCES synthesis_reviews(id) ON DELETE CASCADE,
    outcome_id           INTEGER NOT NULL REFERENCES synthesis_outcomes(id) ON DELETE CASCADE,
    status               TEXT NOT NULL DEFAULT 'pending',
    k_studies            INTEGER,
    fixed_json           TEXT NOT NULL DEFAULT '{}',
    random_json          TEXT NOT NULL DEFAULT '{}',
    heterogeneity_json   TEXT NOT NULL DEFAULT '{}',
    publication_bias_json TEXT NOT NULL DEFAULT '{}',
    subgroup_json        TEXT NOT NULL DEFAULT '{}',
    metaregression_json  TEXT NOT NULL DEFAULT '{}',
    sensitivity_json     TEXT NOT NULL DEFAULT '{}',
    forest_json          TEXT NOT NULL DEFAULT '{}',
    grade_certainty      TEXT,
    grade_json           TEXT NOT NULL DEFAULT '{}',
    grade_explanation    TEXT,
    r_code               TEXT,
    python_code          TEXT,
    code_blocks_json     TEXT NOT NULL DEFAULT '[]',
    error_message        TEXT,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_synth_results_outcome ON synthesis_results(outcome_id);

CREATE TABLE IF NOT EXISTS synthesis_events (
    id          SERIAL PRIMARY KEY,
    review_id   INTEGER NOT NULL REFERENCES synthesis_reviews(id) ON DELETE CASCADE,
    event_type  TEXT NOT NULL,
    message     TEXT NOT NULL,
    detail_json TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_synth_events_review ON synthesis_events(review_id, id);
"""


def migrate_synthesis_columns(conn) -> None:
    """Idempotent post-launch column additions (mirrors
    quality_appraisal.migrate_qa_columns)."""
    from backend.db import column_exists
    if not column_exists(conn, "synthesis_reviews", "rob_scope"):
        with conn:
            conn.execute("ALTER TABLE synthesis_reviews ADD COLUMN rob_scope TEXT")
            # Reviews created before per-outcome risk of bias resolved one judgement
            # per study, framed on the first outcome. Stamping them 'study' is what
            # lets the pooler tell "assessed once, study-wide" apart from "not
            # assessed" -- without it every historical review would re-pool as
            # un-rated.
            conn.execute("UPDATE synthesis_reviews SET rob_scope='study' "
                         "WHERE rob_scope IS NULL")
            conn.commit()


# ─────────────────────────────────────────────
# Credit model
# ─────────────────────────────────────────────
CREDIT_COST_SYNTH_SCREEN = 8        # classify (3) + screen (5) per paper
CREDIT_COST_SYNTH_EXTRACT = 8       # per (included paper x outcome)
# Risk of bias is split because only half of it repeats per outcome: prefill_fields
# is outcome-independent and runs once per paper, while the RoB tool itself runs once
# per (paper x outcome). Charging the old flat 24 per outcome would bill 8 credits an
# outcome for an LLM call that is never made.
CREDIT_COST_SYNTH_ROB_PREFILL = 8   # per included paper
CREDIT_COST_SYNTH_ROB_TOOL = 16     # per (included paper x outcome)
CREDIT_COST_SYNTH_ROB = (           # legacy alias: one paper, one outcome
    CREDIT_COST_SYNTH_ROB_PREFILL + CREDIT_COST_SYNTH_ROB_TOOL)


def billable_units(n_outcomes: int) -> int:
    """Per-paper billing units. Shared by estimate_cost and every refund path so
    the pre-charge and the refunds cannot drift apart."""
    return max(1, n_outcomes)


def rob_charge(n_outcomes: int) -> int:
    """Full risk-of-bias charge for one included paper."""
    return (CREDIT_COST_SYNTH_ROB_PREFILL
            + billable_units(n_outcomes) * CREDIT_COST_SYNTH_ROB_TOOL)


def estimate_cost(paper_count: int, n_outcomes: int, run_rob: bool) -> int:
    """Upfront charge (the maximum, assuming every paper is included).
    Excluded papers refund their extraction + RoB share in the worker."""
    per_paper = (CREDIT_COST_SYNTH_SCREEN
                 + billable_units(n_outcomes) * CREDIT_COST_SYNTH_EXTRACT)
    if run_rob:
        per_paper += rob_charge(n_outcomes)
    return paper_count * per_paper


# ─────────────────────────────────────────────
# Events
# ─────────────────────────────────────────────
def log_event(conn, review_id: int, event_type: str, message: str,
              detail: dict | None = None) -> None:
    """Append a progress event; committed immediately for the frontend poll."""
    try:
        with conn:
            conn.execute(
                """INSERT INTO synthesis_events (review_id, event_type, message, detail_json)
                   VALUES (?, ?, ?, ?)""",
                (review_id, event_type, message, json.dumps(detail) if detail else None),
            )
            conn.commit()
    except Exception as e:
        logger.warning("Failed to log synthesis event (review=%s): %s", review_id, e)


def _set_status(conn, review_id: int, status: str, phase: str | None = None) -> None:
    with conn:
        if phase is not None:
            conn.execute("UPDATE synthesis_reviews SET status=?, phase=? WHERE id=?",
                         (status, phase, review_id))
        else:
            conn.execute("UPDATE synthesis_reviews SET status=? WHERE id=?", (status, review_id))
        conn.commit()


# ─────────────────────────────────────────────
# Measures
# ─────────────────────────────────────────────
# outcome_type -> the effect measures offered for it.
OUTCOME_TYPE_MEASURES = {
    "continuous": ["SMD", "MD"],
    "binary": ["OR", "RR", "RD"],
    "time_to_event": ["HR"],
    "correlation": ["ZCOR"],
    "single_arm": ["PLOGIT", "PFT", "IRR"],
}

MEASURE_LABELS = {
    "MD": "Mean difference", "SMD": "Standardized mean difference (Hedges' g)",
    "OR": "Odds ratio", "RR": "Risk ratio", "RD": "Risk difference",
    "HR": "Hazard ratio",
    "ZCOR": "Correlation (Fisher z)", "PLOGIT": "Proportion (logit)",
    "PFT": "Proportion (Freeman-Tukey)", "IRR": "Incidence rate",
}

# Required numeric keys per measure, for the needs_review flag. HR is reported
# directly (HR + 95% CI) rather than computed from a 2x2 — the engine also
# accepts log-HR + SE or log-rank O-E + V when present.
_REQUIRED_KEYS = {
    "MD": ["m1", "sd1", "n1", "m2", "sd2", "n2"],
    "SMD": ["m1", "sd1", "n1", "m2", "sd2", "n2"],
    "OR": ["events1", "total1", "events2", "total2"],
    "RR": ["events1", "total1", "events2", "total2"],
    "RD": ["events1", "total1", "events2", "total2"],
    "HR": ["hr", "ci_lower", "ci_upper"],
    "ZCOR": ["r", "n"],
    "PLOGIT": ["events", "n"],
    "PFT": ["events", "n"],
    "IRR": ["events", "person_time"],
}


def supported_measures() -> dict[str, Any]:
    return {
        "outcome_types": OUTCOME_TYPE_MEASURES,
        "measure_labels": MEASURE_LABELS,
        "models": ["fixed", "random", "both"],
        "tau2_methods": ["REML", "DL", "PM"],
        "fe_methods": ["IV", "MH"],
        "re_ci_methods": ["wald", "knapp_hartung"],
    }


# ─────────────────────────────────────────────
# LLM step 1 — derive eligibility criteria from PICO (text-only)
# ─────────────────────────────────────────────
_ELIGIBILITY_SYSTEM = (
    "You are a systematic-review methodologist. Given a review's PICO, produce "
    "concise, machine-checkable inclusion and exclusion criteria a screener can "
    "apply to a full-text article. Be specific about population, intervention, "
    "comparator, outcomes, and study design. Respond with JSON only."
)


def derive_eligibility_criteria(pico: dict) -> dict:
    """One text-only LLM call turning a PICO into structured eligibility criteria."""
    prompt = (
        "Review PICO:\n" + json.dumps(pico, indent=2) + "\n\n"
        "Return JSON of this exact shape:\n"
        "{\n"
        '  "inclusion": [{"axis": "population|intervention|comparator|outcome|design|other", "criterion": "..."}],\n'
        '  "exclusion": [{"axis": "...", "criterion": "..."}],\n'
        '  "design_filter": ["Randomized Controlled Trial", "Cohort Study", ...]\n'
        "}\n"
        "design_filter lists the study-design classifications eligible for inclusion "
        "(use the standard names; leave [] to accept any design)."
    )
    raw = call_anthropic([{"role": "user", "content": prompt}],
                         system=_ELIGIBILITY_SYSTEM, max_tokens=2048)
    data = parse_json_response(raw)
    return {
        "inclusion": [c for c in data.get("inclusion", []) if isinstance(c, dict)],
        "exclusion": [c for c in data.get("exclusion", []) if isinstance(c, dict)],
        "design_filter": [str(d) for d in data.get("design_filter", []) if d],
    }


# ─────────────────────────────────────────────
# LLM step 2 — screen one paper
# ─────────────────────────────────────────────
PRISMA_EXCLUSION_REASONS = [
    "wrong population", "wrong intervention", "wrong comparator",
    "wrong outcome", "wrong study design", "duplicate report",
    "full text unavailable", "insufficient data", "other",
]


def _screen_prompt(criteria: dict, classification: dict, pico: dict) -> str:
    return (
        "You are screening a full-text article for inclusion in a systematic review.\n\n"
        "Review PICO:\n" + json.dumps(pico, indent=2) + "\n\n"
        "Eligibility criteria:\n" + json.dumps(criteria, indent=2) + "\n\n"
        f"This study was classified as: {classification.get('study_type', 'unknown')}.\n\n"
        "Decide whether the study meets ALL inclusion criteria and none of the "
        "exclusion criteria. Quote the article where possible. Return JSON:\n"
        "{\n"
        '  "decision": "include" | "exclude",\n'
        '  "confidence": "high" | "moderate" | "low",\n'
        '  "reason": "1-2 sentences citing the paper",\n'
        '  "per_criterion": [{"axis": "population", "met": true, "evidence": "..."}],\n'
        f'  "prisma_exclusion_reason": one of {PRISMA_EXCLUSION_REASONS} (only if excluded, else "")\n'
        "}"
    )


def screen_paper(pdf_bytes: bytes, criteria: dict, classification: dict, pico: dict) -> dict:
    """One LLM call deciding include/exclude. The design filter is enforced in
    Python by the caller; this call judges the remaining PICO axes."""
    raw = annotator_mod._call_with_pdf(
        pdf_bytes, _screen_prompt(criteria, classification, pico), max_tokens=2048)
    decision = str(raw.get("decision", "")).strip().lower()
    if decision not in ("include", "exclude"):
        decision = "exclude"
    reason = str(raw.get("reason", "")).strip()
    excl = str(raw.get("prisma_exclusion_reason", "")).strip().lower()
    if decision == "exclude" and excl not in PRISMA_EXCLUSION_REASONS:
        excl = "other"
    return {
        "decision": decision,
        "confidence": str(raw.get("confidence", "moderate")).strip().lower(),
        "reason": reason,
        "per_criterion": [c for c in raw.get("per_criterion", []) if isinstance(c, dict)],
        "prisma_exclusion_reason": excl if decision == "exclude" else "",
    }


# ─────────────────────────────────────────────
# LLM step 3 — extract effect-size data for one outcome
# ─────────────────────────────────────────────
def _extract_field_spec(measure: str) -> str:
    if measure in ("MD", "SMD"):
        return ('"m1": intervention-arm mean, "sd1": its SD, "n1": its sample size,\n'
                '   "m2": comparator-arm mean, "sd2": its SD, "n2": its sample size')
    if measure in ("OR", "RR", "RD"):
        return ('"events1": intervention-arm event count, "total1": intervention-arm total,\n'
                '   "events2": comparator-arm event count, "total2": comparator-arm total')
    if measure == "HR":
        return ('"hr": the reported hazard ratio (intervention vs comparator), '
                '"ci_lower" and "ci_upper": its 95% confidence-interval bounds. Report '
                'the HR verbatim; if instead the paper gives a log-rank O-E and variance '
                'V, use "o_e" and "v" — never derive a HR from a 2x2 table')
    if measure == "ZCOR":
        return '"r": Pearson correlation, "n": sample size'
    if measure in ("PLOGIT", "PFT"):
        return '"events": event count (single arm), "n": group total'
    if measure == "IRR":
        return '"events": event count, "person_time": person-time at risk'
    return '"value": the reported statistic'


def _extract_prompt(outcome_name: str, measure: str, pico: dict) -> str:
    return (
        "Extract the numeric outcome data needed to compute a meta-analysis "
        f"effect size of type {measure} ({MEASURE_LABELS.get(measure, measure)}) "
        f"for the outcome: \"{outcome_name}\".\n\n"
        "Review PICO (for arm identification):\n" + json.dumps(pico, indent=2) + "\n\n"
        "Rules:\n"
        "- ONE row per distinct clinical context = one comparison x one timepoint "
        "x one subgroup. Group ALL the numbers for one context into ONE row; never "
        "split a single comparison across rows.\n"
        "- Report the numbers verbatim as printed (do not recompute).\n"
        "- Use the intervention arm as arm 1 and the comparator as arm 2.\n"
        "- Leave a field null if the paper does not report it.\n\n"
        "Return JSON:\n"
        "{\n"
        '  "data_points": [\n'
        "    {\n"
        '      "context_label": "e.g. 12-week HbA1c, drug vs placebo",\n'
        '      "timepoint": "...", "subgroup": "overall", "comparison": "...",\n'
        f"      {_extract_field_spec(measure)},\n"
        '      "source_quote": "verbatim numbers as printed",\n'
        '      "extraction_confidence": "high" | "moderate" | "low"\n'
        "    }\n"
        "  ]\n"
        "}"
    )


def _coerce_num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def extract_outcome_data(pdf_bytes: bytes, outcome_name: str, measure: str, pico: dict) -> list[dict]:
    """One LLM call returning every effect-size data row for an outcome.
    Mirrors quadas3.extract_estimates: a single structured object, parsed
    defensively, one row per clinical context."""
    raw = annotator_mod._call_with_pdf(
        pdf_bytes, _extract_prompt(outcome_name, measure, pico), max_tokens=4096)
    rows = raw.get("data_points") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        rows = []
    out = []
    keys = _REQUIRED_KEYS.get(measure, [])
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        numeric = {k: _coerce_num(r.get(k)) for k in keys}
        needs_review = any(numeric.get(k) is None for k in keys)
        out.append({
            "context_label": str(r.get("context_label") or f"Context {i + 1}"),
            "timepoint": str(r.get("timepoint") or ""),
            "subgroup": str(r.get("subgroup") or "overall"),
            "comparison": str(r.get("comparison") or ""),
            "raw": numeric,
            "source_quote": str(r.get("source_quote") or ""),
            "extraction_confidence": str(r.get("extraction_confidence") or "moderate"),
            "needs_review": needs_review,
        })
    return out


# ─────────────────────────────────────────────
# Effect-size computation for a stored data point
# ─────────────────────────────────────────────
def compute_effect_for_point(measure: str, raw: dict, correction: float = 0.5) -> dict:
    """Compute (yi, vi) for a raw data row. Returns a dict (or yi/vi None)."""
    es = stats.effect_size(measure, raw or {}, correction)
    if es is None:
        return {"yi": None, "vi": None, "continuity_applied": False}
    return {"yi": es.yi, "vi": es.vi, "continuity_applied": es.corrected, "n": es.n}


# ─────────────────────────────────────────────
# Pooling one outcome (pure compute — no LLM; re-runnable)
# ─────────────────────────────────────────────
def _initial_grade_for(study_types: list[str]) -> str:
    """Body-of-evidence starting GRADE from the modal included study design."""
    if not study_types:
        return "Low"
    modal = max(set(study_types), key=study_types.count)
    cfg = qa_mod.STUDY_TYPE_REGISTRY.get(modal)
    if cfg and cfg.get("initial_grade"):
        return cfg["initial_grade"]
    return "High" if "Randomized" in modal else "Low"


# Instruments that do not produce a risk-of-bias judgement. AMSTAR-2 rates a
# systematic review's *confidence*, where "High" is GOOD — the opposite polarity to
# every RoB instrument — so feeding its labels to the severity map downgrades the
# best-conducted reviews by two levels. Excluded at the source rather than given
# severity entries: mapping them would legitimize routing systematic reviews into a
# body of primary-study evidence, which is a separate methodological error.
# Cf. synthesis_table2.map_quality_rating, which already treats AMSTAR-2 as
# non-inverted for the same reason.
_NON_ROB_TOOLS = {"amstar2"}


def _resolve_study_rob(study_row: dict, rob_row: dict | None,
                       rob_scope: str) -> tuple[str | None, str]:
    """Resolve one (study x outcome) risk-of-bias label → ``(label|None, source)``.

    Ladder: the per-outcome judgement, then — only for reviews that predate
    per-outcome appraisal — the legacy study-level label, then nothing. ``None``
    means "excluded from the risk-of-bias domain", never "clean": the caller drops
    it and renormalizes the weights rather than scoring it.
    """
    if rob_row:
        if (rob_row.get("rob_tool") or "").strip().lower() in _NON_ROB_TOOLS:
            return None, "excluded_non_rob_tool"
        if rob_row.get("status") == "ok" and (rob_row.get("rob_overall") or "").strip():
            return rob_row["rob_overall"].strip(), "outcome"
        return None, rob_row.get("status") or "missing"
    if rob_scope == "study":
        if (study_row.get("rob_tool") or "").strip().lower() in _NON_ROB_TOOLS:
            return None, "excluded_non_rob_tool"
        if (study_row.get("rob_overall") or "").strip():
            return study_row["rob_overall"].strip(), "study_legacy"
    return None, "missing"


def load_rob_map(conn, review_id: int) -> dict[int, dict[int, dict]]:
    """``{outcome_id: {study_id: rob_row}}`` for a whole review, in one query."""
    out: dict[int, dict[int, dict]] = {}
    for r in conn.execute("SELECT * FROM synthesis_study_rob WHERE review_id=?",
                          (review_id,)).fetchall():
        d = _row_dict(r)
        out.setdefault(d["outcome_id"], {})[d["study_id"]] = d
    return out


def pool_outcome(conn, review_id: int, outcome: dict, points: list[dict],
                 studies_by_id: dict[int, dict],
                 rob_by_study: dict[int, dict] | None = None,
                 rob_scope: str = "outcome") -> dict:
    """Pool one outcome from its (already-computed) data points and persist a
    synthesis_results row. Pure compute — safe to re-run after edits.

    ``rob_by_study`` is this outcome's ``{study_id: rob_row}`` slice of
    :func:`load_rob_map`; ``rob_scope`` comes from ``synthesis_reviews.rob_scope``
    and decides whether a missing per-outcome row may fall back to the legacy
    study-level label.
    """
    measure = outcome["effect_measure"]
    model = outcome.get("model_choice") or "random"
    tau2_method = outcome.get("tau2_method") or "REML"
    fe_method = outcome.get("fe_method") or ("MH" if measure in ("OR", "RR", "RD") else "IV")
    re_ci_method = outcome.get("re_ci_method") or "wald"

    usable = [p for p in points
              if p.get("included_in_pool", 1) and p.get("yi") is not None and p.get("vi")]
    # effects / labels / subgroups / robs and the pooled weights_pct are all built
    # in this one loop, in this one order. `usable` has already dropped points, so a
    # label list assembled anywhere else shifts by one after the first drop — and a
    # misaligned risk-of-bias weighting looks exactly like a correct one.
    effects, labels, subgroups, robs, rob_sources, study_types = [], [], [], [], [], []
    for p in usable:
        st = studies_by_id.get(p["study_id"], {})
        raw = json.loads(p.get("raw_json") or "{}") if isinstance(p.get("raw_json"), str) else (p.get("raw") or {})
        effects.append(stats.EffectSize(yi=float(p["yi"]), vi=float(p["vi"]),
                                        measure=measure, n=raw.get("n"), raw=raw))
        labels.append(st.get("filename") or st.get("label") or p.get("context_label") or "Study")
        subgroups.append(p.get("subgroup_value"))
        rob_label, rob_src = _resolve_study_rob(
            st, (rob_by_study or {}).get(p["study_id"]), rob_scope)
        robs.append(rob_label)
        rob_sources.append(rob_src)
        if st.get("study_type"):
            study_types.append(st["study_type"])

    result: dict[str, Any] = {"outcome_id": outcome["id"], "k_studies": len(effects)}
    if not effects:
        result["status"] = "no_data"
        _write_result_row(conn, review_id, outcome["id"], status="no_data", k=0)
        return result

    pooled = stats.pool(effects, measure, model=model, tau2_method=tau2_method,
                        fe_method=fe_method, re_ci_method=re_ci_method)
    het = pooled["heterogeneity"]
    fixed, random = pooled["fixed"], pooled["random"]
    forest = stats.build_forest(effects, measure, fixed, random, model, het,
                                labels=labels, subgroups=subgroups, robs=robs)

    use = random if model != "fixed" and random.get("status") == "ok" else fixed

    # Publication bias / subgroup / sensitivity — guarded by k.
    pub_bias: dict[str, Any] = {"funnel": stats.funnel_data(effects, use.get("estimate", 0.0))}
    if len(effects) >= 3:
        pub_bias["egger"] = stats.eggers_test(effects)
        pub_bias["trimfill"] = stats.trim_and_fill(effects, measure, model=model, tau2_method=tau2_method)

    subgroup = {}
    if any(s for s in subgroups):
        subgroup = stats.subgroup_analysis(effects, [s or "(unspecified)" for s in subgroups],
                                           measure, model=model, tau2_method=tau2_method)

    sensitivity = {}
    if len(effects) >= 3:
        sensitivity = {
            "leave_one_out": stats.leave_one_out(effects, measure, model=model,
                                                 tau2_method=tau2_method, labels=labels),
            "influence": stats.influence_diagnostics(effects, tau2_method=tau2_method, labels=labels),
        }

    # GRADE body of evidence.
    weights = use.get("weights_pct")
    total_n = sum(e.n for e in effects if e.n) or 0
    grade = stats.grade_body_of_evidence(
        initial=_initial_grade_for(study_types),
        per_study_rob=robs, weights=weights, heterogeneity=het, pooled=use,
        measure=measure, total_n=total_n,
        subgroup=subgroup or None,
        egger=pub_bias.get("egger"), trimfill=pub_bias.get("trimfill"),
        mid_benefit=_coerce_num(outcome.get("mid_benefit")),
        mid_harm=_coerce_num(outcome.get("mid_harm")),
        is_binary=measure in ("OR", "RR", "RD"),
    )
    # Where each pooled study's label came from, so the UI can say *why* a body was
    # not rated (never appraised vs appraisal failed vs a non-RoB instrument).
    grade["rob_sources"] = {s: rob_sources.count(s) for s in sorted(set(rob_sources))}

    # Code generation.
    code_studies = [{"label": labels[i], "raw": effects[i].raw,
                     "yi": effects[i].yi, "vi": effects[i].vi,
                     "subgroup": subgroups[i]} for i in range(len(effects))]
    blocks = codegen.code_blocks(outcome, code_studies)
    r_code = codegen.r_code_for(outcome, code_studies)
    py_code = codegen.python_code_for(outcome, code_studies)

    _write_result_row(
        conn, review_id, outcome["id"], status="ok", k=len(effects),
        fixed=fixed, random=random, heterogeneity=het, publication_bias=pub_bias,
        subgroup=subgroup, metaregression={}, sensitivity=sensitivity, forest=forest,
        grade_certainty=grade["final"], grade=grade, grade_explanation=grade["explanation"],
        r_code=r_code, python_code=py_code, code_blocks=blocks,
    )
    result["status"] = "ok"
    return result


def _write_result_row(conn, review_id, outcome_id, *, status, k,
                      fixed=None, random=None, heterogeneity=None, publication_bias=None,
                      subgroup=None, metaregression=None, sensitivity=None, forest=None,
                      grade_certainty=None, grade=None, grade_explanation=None,
                      r_code=None, python_code=None, code_blocks=None):
    j = lambda x: json.dumps(x or {})
    with conn:
        conn.execute("DELETE FROM synthesis_results WHERE outcome_id=?", (outcome_id,))
        conn.execute(
            """INSERT INTO synthesis_results
                 (review_id, outcome_id, status, k_studies, fixed_json, random_json,
                  heterogeneity_json, publication_bias_json, subgroup_json,
                  metaregression_json, sensitivity_json, forest_json,
                  grade_certainty, grade_json, grade_explanation,
                  r_code, python_code, code_blocks_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (review_id, outcome_id, status, k, j(fixed), j(random), j(heterogeneity),
             j(publication_bias), j(subgroup), j(metaregression), j(sensitivity), j(forest),
             grade_certainty, j(grade), grade_explanation, r_code, python_code,
             json.dumps(code_blocks or [])),
        )
        conn.commit()


def repool_review(conn, review_id: int) -> None:
    """Re-run pooling for every outcome from the current data points. No LLM —
    cheap and synchronous; called by the /pool endpoint after edits."""
    outcomes = [_row_dict(r) for r in conn.execute(
        "SELECT * FROM synthesis_outcomes WHERE review_id=? ORDER BY sort_order, id", (review_id,)).fetchall()]
    studies = {r["id"]: _row_dict(r) for r in conn.execute(
        "SELECT * FROM synthesis_studies WHERE review_id=?", (review_id,)).fetchall()}
    rob_map = load_rob_map(conn, review_id)
    rev = conn.execute("SELECT rob_scope FROM synthesis_reviews WHERE id=?",
                       (review_id,)).fetchone()
    # NULL means the review predates per-outcome appraisal (migrate_synthesis_columns
    # backfills 'study', but a row inserted before the backfill ran can still be NULL).
    rob_scope = (rev["rob_scope"] if rev else None) or "study"
    for oc in outcomes:
        pts = [_row_dict(r) for r in conn.execute(
            "SELECT * FROM synthesis_data_points WHERE outcome_id=?", (oc["id"],)).fetchall()]
        # rehydrate raw + yi/vi from stored columns
        for p in pts:
            p["raw"] = json.loads(p.get("raw_json") or "{}")
        try:
            pool_outcome(conn, review_id, oc, pts, studies,
                         rob_by_study=rob_map.get(oc["id"]), rob_scope=rob_scope)
        except Exception:
            logger.exception("Re-pool failed (review=%s outcome=%s)", review_id, oc["id"])


def _row_dict(row) -> dict:
    try:
        return {k: row[k] for k in row.keys()}
    except Exception:
        return dict(row)


# ─────────────────────────────────────────────
# Full pipeline
# ─────────────────────────────────────────────
def run_synthesis(get_db_fn, papers_dir: Path, user_id: int, is_admin: bool,
                  review_id: int, *, on_study_fields=None) -> None:
    """Screen -> extract -> RoB -> pool -> GRADE. Safe to run on a daemon thread."""
    total_refunded = 0
    conn = get_db_fn()
    try:
        review = conn.execute("SELECT * FROM synthesis_reviews WHERE id=?", (review_id,)).fetchone()
        if not review:
            return
        review = _row_dict(review)
        paper_ids = json.loads(review.get("paper_ids_json") or "[]")
        pico = json.loads(review.get("pico_json") or "{}")
        run_rob = bool(review.get("run_rob", 1))
        outcomes = [_row_dict(r) for r in conn.execute(
            "SELECT * FROM synthesis_outcomes WHERE review_id=? ORDER BY sort_order, id",
            (review_id,)).fetchall()]
        n_outcomes = len(outcomes)

        _set_status(conn, review_id, "screening", "Deriving eligibility criteria")
        log_event(conn, review_id, "info", f"Starting systematic review on {len(paper_ids)} paper(s).")

        # 1. Eligibility criteria
        criteria = json.loads(review.get("eligibility_criteria_json") or "null")
        if not criteria:
            try:
                criteria = derive_eligibility_criteria(pico)
                with conn:
                    conn.execute("UPDATE synthesis_reviews SET eligibility_criteria_json=? WHERE id=?",
                                 (json.dumps(criteria), review_id))
                    conn.commit()
                log_event(conn, review_id, "progress", "Derived eligibility criteria from PICO.")
            except Exception as e:
                logger.exception("Eligibility derivation failed (review=%s)", review_id)
                criteria = {"inclusion": [], "exclusion": [], "design_filter": []}
                log_event(conn, review_id, "warn", "Could not derive criteria automatically; screening on PICO only.")
        design_filter = [d for d in (criteria.get("design_filter") or [])]
    finally:
        conn.close()

    # 2. Screen each paper
    _set_phase(get_db_fn, review_id, "screening", "Screening studies")
    study_ids: dict[int, int] = {}
    for pid in paper_ids:
        c = get_db_fn()
        try:
            try:
                pdf_bytes, filename = annotator_mod.load_paper_pdf(c, papers_dir, pid, user_id, is_admin)
                classification = annotator_mod.classify_study_design(pdf_bytes)
                study_type = classification.get("study_type", "")
                log_event(c, review_id, "progress", f"Screening: {filename[:70]}")
                if design_filter and study_type not in design_filter:
                    decision = {"decision": "exclude", "confidence": "high",
                                "reason": f"Study design '{study_type}' is outside the eligible designs.",
                                "per_criterion": [], "prisma_exclusion_reason": "wrong study design"}
                else:
                    decision = screen_paper(pdf_bytes, criteria, classification, pico)
                sid = _insert_study(c, review_id, pid, filename, classification, decision)
                study_ids[pid] = sid
            except HTTPException as he:
                _insert_study_error(c, review_id, pid, str(he.detail))
                log_event(c, review_id, "warn", f"Screening failed for paper {pid}: {he.detail}")
            except Exception as e:
                logger.exception("Screening failed (review=%s paper=%s)", review_id, pid)
                _insert_study_error(c, review_id, pid, "screening failed")
        finally:
            c.close()

    # 3. Extract + RoB for included studies
    _set_phase(get_db_fn, review_id, "extracting", "Extracting outcome data")
    for pid, sid in study_ids.items():
        c = get_db_fn()
        try:
            srow = c.execute("SELECT * FROM synthesis_studies WHERE id=?", (sid,)).fetchone()
            if not srow:
                continue
            srow = _row_dict(srow)
            if srow.get("screening_decision") != "include":
                # refund extraction + RoB for excluded papers
                if not is_admin:
                    refund = (billable_units(n_outcomes) * CREDIT_COST_SYNTH_EXTRACT
                              + (rob_charge(n_outcomes) if run_rob else 0))
                    if refund:
                        _refund(c, user_id, refund, f"Synthesis review {review_id}: paper {pid} excluded")
                        total_refunded += refund
                continue
            pdf_bytes, _ = annotator_mod.load_paper_pdf(c, papers_dir, pid, user_id, is_admin)
            classification = json.loads(srow.get("classification_json") or "{}")
            study_type = srow.get("study_type") or classification.get("study_type", "")

            for oc in outcomes:
                log_event(c, review_id, "progress",
                          f"Extracting '{oc['name']}' from {srow.get('filename', '')[:60]}")
                try:
                    rows = extract_outcome_data(pdf_bytes, oc["name"], oc["effect_measure"], pico)
                except Exception:
                    logger.exception("Extraction failed (review=%s paper=%s outcome=%s)", review_id, pid, oc["id"])
                    rows = []
                for r in rows:
                    eff = compute_effect_for_point(oc["effect_measure"], r["raw"],
                                                   float(oc.get("continuity_correction") or 0.5))
                    _insert_data_point(c, review_id, oc["id"], sid, r, eff)

            # RoB (reuse QA tool, no re-classify) — once per (study x outcome).
            # RoB 2 / ROBINS-I are outcome-specific instruments, so the same trial
            # can be Low for mortality and High for an unblinded symptom score.
            if run_rob and study_type not in qa_mod.STUDY_TYPE_REGISTRY:
                for oc in outcomes:
                    _upsert_study_rob(c, review_id, sid, oc["id"], status="unsupported",
                                      error_message=f"no RoB tool for study type '{study_type}'")
                log_event(c, review_id, "warn",
                          f"No risk-of-bias tool for '{study_type}'; paper {pid} not appraised.")
                if not is_admin:
                    refund = rob_charge(n_outcomes)
                    _refund(c, user_id, refund,
                            f"Synthesis {review_id}: no RoB tool for {study_type}")
                    total_refunded += refund
            elif run_rob:
                cfg = qa_mod.STUDY_TYPE_REGISTRY[study_type]
                fields = None
                try:
                    # Once per paper — prefill is outcome-independent.
                    fields = annotator_mod.prefill_fields(pdf_bytes, study_type)
                    if on_study_fields is not None:
                        on_study_fields(sid, fields)
                except Exception:
                    logger.exception("RoB prefill failed (review=%s paper=%s)", review_id, pid)
                    log_event(c, review_id, "warn",
                              f"Field extraction failed for paper {pid}; risk of bias skipped.")
                    for oc in outcomes:
                        _upsert_study_rob(c, review_id, sid, oc["id"], status="error",
                                          error_message="field extraction failed")
                    if not is_admin:
                        refund = rob_charge(n_outcomes)
                        _refund(c, user_id, refund,
                                f"Synthesis {review_id}: RoB prefill failed paper {pid}")
                        total_refunded += refund

                if fields is not None:
                    for oc in outcomes:
                        assessed_outcome = ((oc["name"] or "").strip()
                                            or qa_mod.pick_primary_outcome(fields))
                        try:
                            _set_phase_msg(c, review_id, "appraising",
                                           f"Risk of bias — {oc['name'][:28]}: "
                                           f"{srow.get('filename','')[:36]}")
                            rob_domains, rob_overall, rob_direction = qa_mod.appraise_rob_only(
                                pdf_bytes, fields, classification, assessed_outcome, cfg,
                                # Domain 1 note: randomization is a per-trial property,
                                # not a per-outcome one. Without it the same trial can
                                # drift between outcomes on a domain that cannot differ.
                                outcome_is_override=True,
                                # Bind every loop variable — a bare closure would label
                                # each event with the last outcome of the loop.
                                progress=lambda d, _c=c, _o=oc, _t=cfg["rob_tool"]:
                                    log_event(_c, review_id, "progress",
                                              f"RoB {_t} · {_o['name'][:24]} · domain {d}"))
                            _upsert_study_rob(
                                c, review_id, sid, oc["id"],
                                assessed_outcome=assessed_outcome, rob_tool=cfg["rob_tool"],
                                rob_overall=rob_overall, rob_direction=rob_direction,
                                rob_domains=rob_domains, status="ok")
                            log_event(c, review_id, "progress",
                                      f"Risk of bias ({cfg['rob_tool']}) for "
                                      f"'{oc['name']}': {rob_overall}")
                        except Exception:
                            # One outcome failing must not cost the others.
                            logger.exception("RoB failed (review=%s paper=%s outcome=%s)",
                                             review_id, pid, oc["id"])
                            _upsert_study_rob(
                                c, review_id, sid, oc["id"],
                                assessed_outcome=assessed_outcome, rob_tool=cfg["rob_tool"],
                                status="error", error_message="risk-of-bias tool failed")
                            log_event(c, review_id, "warn",
                                      f"Risk-of-bias assessment failed for paper {pid}, "
                                      f"outcome '{oc['name']}'.")
                            if not is_admin:
                                _refund(c, user_id, CREDIT_COST_SYNTH_ROB_TOOL,
                                        f"Synthesis {review_id}: RoB failed paper {pid} "
                                        f"outcome {oc['id']}")
                                total_refunded += CREDIT_COST_SYNTH_ROB_TOOL
        except Exception:
            logger.exception("Extraction/RoB stage failed (review=%s paper=%s)", review_id, pid)
        finally:
            c.close()

    # 4. Pool every outcome + GRADE
    _set_phase(get_db_fn, review_id, "pooling", "Pooling + GRADE")
    c = get_db_fn()
    try:
        log_event(c, review_id, "progress", "Pooling data and computing GRADE.")
        repool_review(c, review_id)
        with c:
            c.execute(
                "UPDATE synthesis_reviews SET status='complete', phase=NULL, credits_refunded=?, completed_at=CURRENT_TIMESTAMP WHERE id=?",
                (total_refunded, review_id))
            c.commit()
        log_event(c, review_id, "info", "Synthesis complete.", {"refunded": total_refunded})
    finally:
        c.close()


def run_synthesis_async(get_db_fn, papers_dir: Path, user_id: int, is_admin: bool,
                        review_id: int) -> None:
    t = threading.Thread(target=run_synthesis,
                         args=(get_db_fn, papers_dir, user_id, is_admin, review_id),
                         daemon=True, name=f"synthesis-{review_id}")
    t.start()


# ─────────────────────────────────────────────
# Small DB helpers
# ─────────────────────────────────────────────
def _set_phase(get_db_fn, review_id, status, phase):
    c = get_db_fn()
    try:
        _set_status(c, review_id, status, phase)
    finally:
        c.close()


def _set_phase_msg(conn, review_id, status, phase):
    with conn:
        conn.execute("UPDATE synthesis_reviews SET status=?, phase=? WHERE id=?", (status, phase, review_id))
        conn.commit()


def _refund(conn, user_id, amount, description):
    try:
        bill_mod.refund_credits(conn, user_id, amount, description)
    except Exception as e:
        logger.warning("Refund failed: %s", e)


def _insert_study(conn, review_id, paper_id, filename, classification, decision) -> int:
    with conn:
        cur = conn.execute(
            """INSERT INTO synthesis_studies
                 (review_id, paper_id, filename, status, classification_json, study_type,
                  screening_decision, screening_reason, prisma_exclusion_reason,
                  screening_confidence, screening_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?) RETURNING id""",
            (review_id, paper_id, filename,
             "included" if decision["decision"] == "include" else "excluded",
             json.dumps(classification), classification.get("study_type", ""),
             decision["decision"], decision["reason"], decision["prisma_exclusion_reason"],
             decision["confidence"], json.dumps(decision.get("per_criterion", []))))
        row = cur.fetchone()
        conn.commit()
    return row["id"] if row else cur.lastrowid


def _insert_study_error(conn, review_id, paper_id, msg) -> None:
    with conn:
        conn.execute(
            """INSERT INTO synthesis_studies (review_id, paper_id, status, error_message)
               VALUES (?,?,?,?)""",
            (review_id, paper_id, "error", msg))
        conn.commit()


def _insert_data_point(conn, review_id, outcome_id, study_id, row, eff) -> None:
    with conn:
        conn.execute(
            """INSERT INTO synthesis_data_points
                 (review_id, outcome_id, study_id, context_label, raw_json, subgroup_value,
                  yi, vi, continuity_applied, extraction_confidence, needs_review, source_quote)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (review_id, outcome_id, study_id, row["context_label"], json.dumps(row["raw"]),
             row.get("subgroup"), eff.get("yi"), eff.get("vi"),
             1 if eff.get("continuity_applied") else 0, row["extraction_confidence"],
             1 if row["needs_review"] else 0, row["source_quote"]))
        conn.commit()


def _upsert_study_rob(conn, review_id: int, study_id: int, outcome_id: int, *,
                      assessed_outcome: str | None = None, rob_tool: str | None = None,
                      rob_overall: str | None = None, rob_direction: str | None = None,
                      rob_domains: dict | None = None, status: str = "ok",
                      error_message: str | None = None) -> None:
    """Write one (study x outcome) risk-of-bias judgement, replacing any prior row.

    DELETE + INSERT rather than ON CONFLICT so the statement is identical on
    PostgreSQL and SQLite, matching _write_result_row. A row is written even for
    failures — that is what makes "appraised and failed" distinguishable from
    "never appraised" when the pooler resolves the domain.
    """
    with conn:
        conn.execute("DELETE FROM synthesis_study_rob WHERE study_id=? AND outcome_id=?",
                     (study_id, outcome_id))
        conn.execute(
            """INSERT INTO synthesis_study_rob
                 (review_id, study_id, outcome_id, assessed_outcome, rob_tool,
                  rob_overall, rob_direction, rob_domains_json, status, error_message)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (review_id, study_id, outcome_id, assessed_outcome, rob_tool, rob_overall,
             rob_direction, json.dumps(rob_domains or {}), status, error_message))
        conn.commit()


# ─────────────────────────────────────────────
# PRISMA flow counts
# ─────────────────────────────────────────────
def compute_prisma_counts(conn, review_id: int) -> dict:
    """Counts for the PRISMA 2020 flow diagram, from screening + manual upstream."""
    review = conn.execute("SELECT prisma_manual_counts_json, paper_count FROM synthesis_reviews WHERE id=?",
                          (review_id,)).fetchone()
    manual = json.loads((review["prisma_manual_counts_json"] if review else None) or "{}")
    studies = [_row_dict(r) for r in conn.execute(
        "SELECT screening_decision, prisma_exclusion_reason, status FROM synthesis_studies WHERE review_id=?",
        (review_id,)).fetchall()]
    assessed = len(studies)
    included = sum(1 for s in studies if s.get("screening_decision") == "include")
    excluded = [s for s in studies if s.get("screening_decision") == "exclude"]
    reasons: dict[str, int] = {}
    for s in excluded:
        r = s.get("prisma_exclusion_reason") or "other"
        reasons[r] = reasons.get(r, 0) + 1
    return {
        "identified_db": int(manual.get("identified_db") or 0),
        "identified_other": int(manual.get("identified_other") or 0),
        "duplicates_removed": int(manual.get("duplicates_removed") or 0),
        "records_screened": int(manual.get("records_screened") or assessed),
        "records_excluded_screen": int(manual.get("records_excluded_screen") or 0),
        "reports_assessed": assessed,
        "reports_excluded": [{"reason": k, "n": v} for k, v in reasons.items()],
        "reports_excluded_total": len(excluded),
        "included": included,
    }


# ─────────────────────────────────────────────
# Developer view
# ─────────────────────────────────────────────
def prompt_catalog() -> dict[str, Any]:
    """All prompts + the exact source of every stats function, for the dev view."""
    stat_fns = ["smd_hedges_g", "log_or", "inverse_variance_pool", "mantel_haenszel",
                "heterogeneity", "tau2_reml", "eggers_test", "trim_and_fill",
                "subgroup_analysis", "meta_regression", "leave_one_out",
                "influence_diagnostics", "grade_body_of_evidence"]
    return {
        "overview": "Synthesis runs a systematic review + meta-analysis: screen -> "
                    "extract -> risk of bias -> pool -> publication bias / subgroup / "
                    "sensitivity -> body-of-evidence GRADE. Computation is pure Python "
                    "(numpy/scipy); R + Python reproduction code is emitted per outcome.",
        "credit_cost": {"screen_per_paper": CREDIT_COST_SYNTH_SCREEN,
                        "extract_per_unit": CREDIT_COST_SYNTH_EXTRACT,
                        "rob_prefill_per_paper": CREDIT_COST_SYNTH_ROB_PREFILL,
                        "rob_tool_per_unit": CREDIT_COST_SYNTH_ROB_TOOL},
        "eligibility_system_prompt": _ELIGIBILITY_SYSTEM,
        "screening_prompt_template": _screen_prompt({"inclusion": "...", "exclusion": "..."},
                                                    {"study_type": "..."}, {"population": "..."}),
        "extraction_prompt_template": _extract_prompt("<outcome>", "SMD", {"population": "..."}),
        "prisma_exclusion_reasons": PRISMA_EXCLUSION_REASONS,
        "supported_measures": supported_measures(),
        "statistics_source": {fn: _safe_getsource(getattr(stats, fn)) for fn in stat_fns},
        "codegen_source": _safe_getsource(codegen.code_blocks),
    }


def _safe_getsource(obj) -> str:
    try:
        return inspect.getsource(obj)
    except Exception:
        return ""


# ─────────────────────────────────────────────
# CSV / XLSX flattening
# ─────────────────────────────────────────────
def flatten_for_export(conn, review_id: int) -> list[dict]:
    """One row per (outcome, study) data point + pooled-summary rows."""
    outcomes = {r["id"]: _row_dict(r) for r in conn.execute(
        "SELECT * FROM synthesis_outcomes WHERE review_id=?", (review_id,)).fetchall()}
    studies = {r["id"]: _row_dict(r) for r in conn.execute(
        "SELECT * FROM synthesis_studies WHERE review_id=?", (review_id,)).fetchall()}
    results = {r["outcome_id"]: _row_dict(r) for r in conn.execute(
        "SELECT * FROM synthesis_results WHERE review_id=?", (review_id,)).fetchall()}
    points = [_row_dict(r) for r in conn.execute(
        "SELECT * FROM synthesis_data_points WHERE review_id=? ORDER BY outcome_id, study_id",
        (review_id,)).fetchall()]
    rob_map = load_rob_map(conn, review_id)
    rev = conn.execute("SELECT rob_scope FROM synthesis_reviews WHERE id=?",
                       (review_id,)).fetchone()
    rob_scope = (rev["rob_scope"] if rev else None) or "study"
    rows = []
    for p in points:
        oc = outcomes.get(p["outcome_id"], {})
        st = studies.get(p["study_id"], {})
        raw = json.loads(p.get("raw_json") or "{}")
        rob_row = rob_map.get(p["outcome_id"], {}).get(p["study_id"])
        rob_label, rob_src = _resolve_study_rob(st, rob_row, rob_scope)
        rows.append({
            "outcome": oc.get("name"), "measure": oc.get("effect_measure"),
            "study": st.get("filename"), "study_type": st.get("study_type"),
            # Per (outcome x study) — these rows are already at that grain.
            "rob_overall": rob_label,
            "rob_tool": (rob_row or {}).get("rob_tool") or st.get("rob_tool"),
            "rob_source": rob_src,
            "included_in_pool": p.get("included_in_pool"),
            **{f"raw_{k}": v for k, v in raw.items()},
            "yi": p.get("yi"), "vi": p.get("vi"),
            "needs_review": p.get("needs_review"),
            "context": p.get("context_label"), "subgroup": p.get("subgroup_value"),
        })
    # pooled summaries
    for oid, res in results.items():
        oc = outcomes.get(oid, {})
        rnd = json.loads(res.get("random_json") or "{}")
        het = json.loads(res.get("heterogeneity_json") or "{}")
        rows.append({
            "outcome": oc.get("name"), "measure": oc.get("effect_measure"),
            "study": "POOLED (random effects)", "k_studies": res.get("k_studies"),
            "pooled_estimate": rnd.get("estimate"), "ci_low": rnd.get("ci_low"),
            "ci_high": rnd.get("ci_high"), "p": rnd.get("p"),
            "I2": het.get("I2"), "tau2": het.get("tau2_REML"),
            "grade_certainty": res.get("grade_certainty"),
        })
    return rows
