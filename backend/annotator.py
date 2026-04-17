"""OGAI Annotator — ported into The AI Researcher as a Benchmark Lab application.

Lets experts classify a paper's study design (hierarchical taxonomy), auto-extract
structured fields with Claude, manually confirm/correct/flag each field, link PDF
text spans to fields for provenance, and export CSVs.

Reuses the existing ``papers`` table + projects + auth. Adds two new tables:
``annotations`` (one row per paper+reviewer) and ``annotation_spans`` (text→field
links). Optimistic concurrency via the ``annotations.version`` column.
"""

import base64
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .helpers import call_anthropic, parse_json_response

logger = logging.getLogger("rubricgen")


# ─────────────────────────────────────────────
# Schema (init_db wires this in)
# ─────────────────────────────────────────────
ANNOTATOR_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS annotations (
    id                        SERIAL PRIMARY KEY,
    paper_id                  INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    reviewer_id               INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    data_json                 TEXT    DEFAULT '{}',
    updated_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    correction_notes          TEXT,
    corrections_json          TEXT,
    pipeline_predictions_json TEXT,
    field_annotations_json    TEXT,
    status                    TEXT    DEFAULT 'in_progress',
    version                   INTEGER NOT NULL DEFAULT 1,
    UNIQUE(paper_id, reviewer_id)
);

CREATE TABLE IF NOT EXISTS annotation_spans (
    id          SERIAL PRIMARY KEY,
    paper_id    INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    reviewer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    field_name  TEXT    NOT NULL,
    page        INTEGER,
    text        TEXT,
    x0 REAL, y0 REAL, x1 REAL, y1 REAL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_annspans_paper_reviewer
    ON annotation_spans(paper_id, reviewer_id);
CREATE INDEX IF NOT EXISTS idx_annotations_reviewer
    ON annotations(reviewer_id);
"""


# ─────────────────────────────────────────────
# Field schemas (kept in sync with the frontend)
# ─────────────────────────────────────────────
UNIVERSAL_FIELD_IDS: list[str] = [
    "citation_authors", "citation_year", "citation_title", "citation_journal", "citation_doi",
    "study_objective", "population_participants", "population_intervention_exposure",
    "population_comparator", "population_outcomes", "sample_size_total", "sample_size_per_group",
    "power_calculation_reported", "setting", "country_region",
    "study_period_enrollment_start", "study_period_enrollment_end", "follow_up_duration",
    "primary_outcome_definition", "primary_outcome_measurement", "primary_outcome_timing",
    "secondary_outcomes", "key_findings_effect_estimate", "key_findings_metric",
    "key_findings_ci_lower", "key_findings_ci_upper", "key_findings_pvalue",
    "key_findings_direction", "funding_source", "conflicts_of_interest",
    "limitations_stated", "protocol_registration",
]

TYPE_FIELD_IDS: dict[str, list[str]] = {
    "Randomized Controlled Trial": [
        "randomization_method", "allocation_concealment", "allocation_ratio",
        "stratification_factors", "baseline_balance", "blinding_participants",
        "blinding_personnel", "blinding_outcome_assessors", "protocol_deviations",
        "analysis_framework", "attrition_rate", "missing_data_handling",
        "outcome_measurement_method", "protocol_available", "outcomes_match_protocol",
        "consort_flow_diagram",
    ],
    "Cluster Randomized Trial": [
        "cluster_unit", "n_clusters", "icc_reported",
        "recruitment_after_randomization", "clustering_in_analysis", "contamination_risk",
    ],
    "Stepped-Wedge Cluster RCT": [
        "cluster_unit", "n_clusters", "icc_reported",
        "recruitment_after_randomization", "clustering_in_analysis", "contamination_risk",
    ],
    "Crossover Trial": [
        "washout_period", "carryover_assessment", "period_effects",
        "sequence_order", "paired_analysis",
    ],
    "Non-Randomized Trial": [
        "concurrent_control_confirmed", "allocation_mechanism",
        "baseline_comparability", "confounding_control", "blinding",
    ],
    "Single-Arm Trial": [
        "primary_endpoint_prespecified", "inclusion_exclusion_criteria",
        "comparator_historical_reference", "consecutive_enrolment",
    ],
    "Dose-Escalation Study": [
        "escalation_scheme", "dlt_definition", "dose_levels", "mtd_declared",
        "rp2d", "expansion_cohort", "pk_pd_reported",
    ],
    "Interrupted Time Series": [
        "n_data_points_pre", "n_data_points_post", "intervention_date", "control_series",
        "statistical_method", "level_change", "slope_change",
        "autocorrelation_addressed", "seasonality_adjustment", "concurrent_events",
    ],
    "Uncontrolled Before-After": [
        "pre_measurement", "post_measurement", "secular_trend_risk",
        "regression_to_mean_risk", "concurrent_events",
    ],
    "Difference-in-Differences": [
        "exogenous_event", "parallel_trends_evidence", "n_pre_period_points",
        "interaction_term", "common_shocks", "staggered_adoption",
    ],
    "Regression Discontinuity": [
        "running_variable", "cutoff_value", "sharp_vs_fuzzy",
        "bandwidth_selection", "manipulation_testing", "continuity_plots",
    ],
    "Cohort Study": [
        "exposure_definition", "exposure_measurement", "comparator_group",
        "outcome_ascertainment", "confounders_measured", "adjustment_method",
        "loss_to_follow_up", "immortal_time_bias",
    ],
    "Case-Control": [
        "case_definition", "case_source", "control_selection", "matching",
        "exposure_ascertainment", "recall_bias_risk",
    ],
    "Case-Crossover": [
        "case_definition_ccx", "exposure_definition_ccx", "hazard_period",
        "control_period", "induction_period", "temporal_direction",
        "exposure_variability", "conditional_logistic", "self_selection_bias",
    ],
    "Cross-Sectional (Analytical)": [
        "sampling_method", "response_rate", "exposure_outcome_simultaneity", "adjustment_method",
    ],
    "Mendelian Randomization": [
        "instrument_variants", "f_statistic", "mr_design", "sample_overlap",
        "pleiotropy_tests", "exclusion_restriction",
    ],
    "Diagnostic Accuracy": [
        "index_test", "reference_standard", "blinding_index_to_reference",
        "blinding_reference_to_index", "two_by_two_table", "spectrum_of_patients",
        "verification_bias", "threshold_effects", "flow_and_timing",
    ],
    "Prognostic Factor Study": [
        "prognostic_factor", "outcome_definition", "study_participation",
        "study_attrition", "pf_measurement", "confounding_control", "statistical_analysis",
    ],
    "Prediction Model Study": [
        "predictors_candidate", "predictor_selection_method", "model_type",
        "discrimination", "calibration", "model_presentation", "model_stage",
    ],
    "SR without Meta-Analysis": [
        "search_strategy", "inclusion_criteria", "study_selection", "data_extraction",
        "included_studies_n", "rob_tool_used", "synthesis_method", "grade_assessment",
        "prisma_flow",
    ],
    "SR with Meta-Analysis": [
        "search_strategy", "inclusion_criteria", "included_studies_n", "effect_measure",
        "pooled_estimate", "pooling_model", "heterogeneity", "publication_bias",
        "sensitivity_analyses", "subgroup_analyses", "grade_assessment", "prisma_flow",
    ],
    "Umbrella Review": [
        "search_strategy", "inclusion_criteria", "included_studies_n", "rob_tool_used",
        "synthesis_method", "grade_assessment", "prisma_flow",
    ],
    "Network Meta-Analysis": [
        "search_strategy", "inclusion_criteria", "included_studies_n", "effect_measure",
        "pooled_estimate", "heterogeneity", "publication_bias", "sensitivity_analyses",
        "grade_assessment", "prisma_flow",
    ],
    "Economic Evaluation": [
        "evaluation_type", "perspective", "time_horizon", "discount_rate", "model_type",
        "cost_inputs", "effectiveness_source", "icer", "sensitivity_analysis",
    ],
    "Guideline / Consensus": [
        "guideline_organization", "panel_composition", "evidence_base", "grade_used",
        "recommendations", "updating_plan",
    ],
    "Qualitative Research": [
        "methodology", "data_collection", "sampling_strategy", "data_saturation",
        "reflexivity", "themes",
    ],
}

# ─────────────────────────────────────────────
# Field groups for batch extraction (mirrors the frontend FIELD_GROUPS array)
# ─────────────────────────────────────────────
FIELD_GROUPS: dict[str, list[str]] = {
    "citation":   ["citation_authors", "citation_year", "citation_title",
                   "citation_journal", "citation_doi"],
    "objective":  ["study_objective"],
    "population": ["population_participants", "population_intervention_exposure",
                   "population_comparator", "population_outcomes"],
    "sample":     ["sample_size_total", "sample_size_per_group", "power_calculation_reported"],
    "setting":    ["setting", "country_region",
                   "study_period_enrollment_start", "study_period_enrollment_end",
                   "follow_up_duration"],
    "outcomes":   ["primary_outcome_definition", "primary_outcome_measurement",
                   "primary_outcome_timing", "secondary_outcomes"],
    "results":    ["key_findings_effect_estimate", "key_findings_metric",
                   "key_findings_ci_lower", "key_findings_ci_upper",
                   "key_findings_pvalue", "key_findings_direction"],
    "admin":      ["funding_source", "conflicts_of_interest",
                   "limitations_stated", "protocol_registration"],
}


def filter_universal_by_groups(groups: list[str] | None) -> list[str]:
    """Return the universal field IDs selected by ``groups``.

    ``None`` / empty ``groups`` → all universal fields (full extraction).
    Unknown groups are silently skipped.
    """
    if not groups:
        return list(UNIVERSAL_FIELD_IDS)
    seen: list[str] = []
    for g in groups:
        for fid in FIELD_GROUPS.get(g, []):
            if fid in UNIVERSAL_FIELD_IDS and fid not in seen:
                seen.append(fid)
    return seen


# Extra columns that appear in the CSV beyond the universal + type-specific fields
CLASSIFICATION_COLS: list[str] = [
    "major_category", "subcategory", "study_type",
    "rule1_pass", "rule2_pass", "rule2b_pass", "rule3_pass",
    "natural_experiment_flag", "author_stated_design", "author_label_discordance",
    "reviewer_action",
]

DESIGN_MODIFIER_COLS: list[str] = [
    "clinical_trial_phase", "regulatory_context", "registration_number",
    "industry_sponsored", "data_source_type", "database_name",
    "adaptive_design", "pragmatic_vs_explanatory", "trial_framework",
    "target_trial_emulation", "pilot_or_feasibility",
]

FLAT_CSV_COLS: list[str] = (
    CLASSIFICATION_COLS
    + UNIVERSAL_FIELD_IDS
    + DESIGN_MODIFIER_COLS
    + ["correction_notes", "corrections_json", "pipeline_predictions_json", "field_annotations_json"]
)


# ─────────────────────────────────────────────
# Classification taxonomy + prompts
# ─────────────────────────────────────────────
_TAXONOMY = """
Major categories and their subcategories and study types:

Primary Studies:
  Randomized Controlled → Randomized Controlled Trial, Cluster Randomized Trial, Stepped-Wedge Cluster RCT, Crossover Trial
  Non-Randomized Controlled → Non-Randomized Trial
  Non-Randomized Uncontrolled → Single-Arm Trial, Dose-Escalation Study
  Quasi-Experimental → Interrupted Time Series, Uncontrolled Before-After, Difference-in-Differences, Regression Discontinuity
  Qualitative & Mixed Methods → Qualitative Research, Mixed Methods

Observational Studies:
  Descriptive → Case Report / Series, Cross-Sectional (Descriptive), Ecological Study
  Analytical → Case-Control, Cohort Study, Cross-Sectional (Analytical), Self-Controlled Case Series, Case-Crossover, Mendelian Randomization
  Diagnostic / Prognostic → Diagnostic Accuracy, Prognostic Factor Study, Prediction Model Study

Evidence Synthesis:
  Reviews → SR without Meta-Analysis, SR with Meta-Analysis, Umbrella Review, Network Meta-Analysis, Scoping Review, Narrative Review

Guidance / Consensus:
  Guidelines & Consensus → Guideline / Consensus

Economic & Decision Models:
  Economic Evaluation → Economic Evaluation
""".strip()


def build_classify_prompt() -> str:
    return f"""You are a clinical research methodologist. Read this PDF and classify it using the taxonomy below.

Return ONLY a valid JSON object with exactly these three keys:
- "major_category": one of the major category names
- "subcategory": the subcategory within that major category
- "study_type": the specific study type

Taxonomy:
{_TAXONOMY}

Rules:
- Choose the single best-fitting classification based on the study design described in the paper.
- If the paper explicitly states its design, use that as your primary signal.
- If uncertain between two options, choose the more specific one.
- Return ONLY the JSON object — no explanation, no markdown fences.

Example output:
{{"major_category": "Primary Studies", "subcategory": "Randomized Controlled", "study_type": "Randomized Controlled Trial"}}"""


def build_prefill_prompt(study_type: str,
                         groups: list[str] | None = None,
                         type_fields: list[str] | None = None,
                         modifier_fields: list[str] | None = None) -> str:
    """Assemble the prefill prompt.

    ``groups``          → universal fields (None/empty = all 8 groups)
    ``type_fields``     → subset of ``TYPE_FIELD_IDS[study_type]``.
                          None = all type-specific; [] = none.
    ``modifier_fields`` → subset of ``DESIGN_MODIFIER_COLS``.
                          None = all modifiers; [] = none.
    """
    universal = filter_universal_by_groups(groups)

    all_type = TYPE_FIELD_IDS.get(study_type, [])
    if type_fields is None:
        selected_type = list(all_type)
    else:
        selected_type = [f for f in type_fields if f in all_type]

    if modifier_fields is None:
        selected_modifiers = list(DESIGN_MODIFIER_COLS)
    else:
        selected_modifiers = [f for f in modifier_fields if f in DESIGN_MODIFIER_COLS]

    all_ids = universal + selected_type + selected_modifiers
    field_list = "\n".join(f"  - {f}" for f in all_ids)
    return f"""You are a clinical research data extractor. Extract information from this PDF to fill a structured annotation form for a {study_type} study.

Return ONLY a valid JSON object — no preamble, no markdown fences, no explanation. Keys must be exactly the field IDs below.

Rules:
- Short factual values (1–3 sentences max). Omit fields not found (do not include null or empty string).
- Do not invent values. Extract only what is explicitly stated.
- Numeric fields: return just the number/value as a string.
- DOI: return the DOI string only, without "https://doi.org/".

Fields:
{field_list}

Return only the JSON object."""


# ─────────────────────────────────────────────
# Credit costs (admin-bypass handled by caller)
# ─────────────────────────────────────────────
CREDIT_COST_CLASSIFY = 3    # ~$0.30 at Starter pack rate
CREDIT_COST_PREFILL = 8    # ~$0.80 at Starter pack rate


# ─────────────────────────────────────────────
# Anthropic call with PDF attachment
# ─────────────────────────────────────────────
def _call_with_pdf(pdf_bytes: bytes, prompt: str, max_tokens: int = 4096) -> dict:
    """Send the PDF as a base64 document block alongside the text prompt.

    ``call_anthropic`` in ``helpers.py`` already sets the ``pdfs-2024-09-25`` beta
    header, so we just need to format the message content blocks.
    """
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode()
    messages = [{
        "role": "user",
        "content": [
            {"type": "document",
             "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}},
            {"type": "text", "text": prompt},
        ],
    }]
    raw = call_anthropic(messages, system="", max_tokens=max_tokens)
    return parse_json_response(raw)


def classify_study_design(pdf_bytes: bytes) -> dict:
    result = _call_with_pdf(pdf_bytes, build_classify_prompt())
    allowed = {"major_category", "subcategory", "study_type"}
    filtered = {k: str(v) for k, v in result.items() if k in allowed}
    if "study_type" not in filtered:
        raise HTTPException(502, f"Classification response missing study_type: {result}")
    return filtered


def prefill_fields(pdf_bytes: bytes, study_type: str,
                   groups: list[str] | None = None,
                   type_fields: list[str] | None = None,
                   modifier_fields: list[str] | None = None) -> dict:
    if not study_type:
        raise HTTPException(400, "study_type is required for prefill")
    # 40+ fields comfortably fit in 8k tokens; bump ceiling to be safe for big papers
    prompt = build_prefill_prompt(
        study_type, groups=groups,
        type_fields=type_fields, modifier_fields=modifier_fields,
    )
    result = _call_with_pdf(pdf_bytes, prompt, max_tokens=8192)
    return {k: str(v) for k, v in result.items() if v not in (None, "", [])}


def get_schema() -> dict:
    """Field catalog used by the frontend to render selection UI."""
    return {
        "universal_groups": [
            {"id": g, "field_ids": list(FIELD_GROUPS[g])}
            for g in ("citation", "objective", "population", "sample",
                      "setting", "outcomes", "results", "admin")
            if g in FIELD_GROUPS
        ],
        "type_fields": {st: list(fs) for st, fs in TYPE_FIELD_IDS.items()},
        "modifier_fields": list(DESIGN_MODIFIER_COLS),
    }


# ─────────────────────────────────────────────
# Data-access helpers (caller owns the connection)
# ─────────────────────────────────────────────
def _row_has(row, col: str) -> bool:
    try:
        return col in row.keys()
    except Exception:
        return False


def load_annotation(conn, paper_id: int, reviewer_id: int) -> dict:
    """Return {annotation, spans} for one paper+reviewer pair. Missing = empty."""
    row = conn.execute(
        "SELECT * FROM annotations WHERE paper_id=? AND reviewer_id=?",
        (paper_id, reviewer_id),
    ).fetchone()
    spans_rows = conn.execute(
        "SELECT field_name, page, text, x0, y0, x1, y1 FROM annotation_spans "
        "WHERE paper_id=? AND reviewer_id=?",
        (paper_id, reviewer_id),
    ).fetchall()

    if not row:
        return {"annotation": None, "spans": [dict(s) for s in spans_rows]}

    try:
        data = json.loads(row["data_json"] or "{}")
    except Exception:
        data = {}
    for col in ("correction_notes", "corrections_json",
                "pipeline_predictions_json", "field_annotations_json"):
        if _row_has(row, col):
            val = row[col]
            if val and col not in data:
                data[col] = val

    return {
        "annotation": {
            "id": row["id"],
            "reviewer_id": row["reviewer_id"],
            "timestamp": row["updated_at"],
            "status": row["status"] if _row_has(row, "status") else "in_progress",
            "version": row["version"] if _row_has(row, "version") else 1,
            "data": data,
        },
        "spans": [dict(s) for s in spans_rows],
    }


def save_annotation(conn, paper_id: int, reviewer_id: int, payload: dict) -> dict:
    """UPSERT annotation + replace spans atomically.

    ``payload`` shape::

        {
          "data": {…},
          "field_annotations": {…},
          "spans": [{field_name,page,text,x0,y0,x1,y1}, …],
          "status": "in_progress" | "needs_review" | "flagged" | "complete",
          "version": int | None,        # client's last-seen version
        }

    Raises HTTPException(409) if ``payload["version"]`` is stale.
    """
    data = dict(payload.get("data") or {})
    field_annotations = payload.get("field_annotations") or {}
    spans = payload.get("spans") or []
    status = payload.get("status") or data.get("reviewer_action") or "in_progress"
    client_version = payload.get("version")

    correction_notes = data.get("correction_notes") or ""
    corrections_json = data.get("corrections_json") or ""
    pipeline_predictions_json = data.get("pipeline_predictions_json") or ""
    field_annotations_json = (
        json.dumps(field_annotations) if field_annotations
        else (data.get("field_annotations_json") or "")
    )
    now = datetime.now(timezone.utc).isoformat()

    with conn:
        existing = conn.execute(
            "SELECT version FROM annotations WHERE paper_id=? AND reviewer_id=?",
            (paper_id, reviewer_id),
        ).fetchone()

        if existing and client_version is not None:
            db_version = existing["version"] if _row_has(existing, "version") else 1
            if client_version < db_version:
                raise HTTPException(
                    status_code=409,
                    detail="Conflict: this paper was updated elsewhere. Please reload.",
                )

        new_version = (existing["version"] + 1) if existing else 1

        conn.execute(
            """INSERT INTO annotations
                   (paper_id, reviewer_id, data_json, updated_at,
                    correction_notes, corrections_json, pipeline_predictions_json,
                    field_annotations_json, status, version)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(paper_id, reviewer_id) DO UPDATE SET
                   data_json=excluded.data_json,
                   updated_at=excluded.updated_at,
                   correction_notes=excluded.correction_notes,
                   corrections_json=excluded.corrections_json,
                   pipeline_predictions_json=excluded.pipeline_predictions_json,
                   field_annotations_json=excluded.field_annotations_json,
                   status=excluded.status,
                   version=excluded.version""",
            (paper_id, reviewer_id, json.dumps(data), now,
             correction_notes, corrections_json, pipeline_predictions_json,
             field_annotations_json, status, new_version),
        )

        conn.execute(
            "DELETE FROM annotation_spans WHERE paper_id=? AND reviewer_id=?",
            (paper_id, reviewer_id),
        )
        for s in spans:
            conn.execute(
                """INSERT INTO annotation_spans
                       (paper_id, reviewer_id, field_name, page, text, x0, y0, x1, y1)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (paper_id, reviewer_id, s.get("field_name"), s.get("page"), s.get("text"),
                 s.get("x0"), s.get("y0"), s.get("x1"), s.get("y1")),
            )
        conn.commit()

    return {"status": "ok", "timestamp": now, "version": new_version}


def list_papers_with_status(conn, user_id: int) -> list[dict]:
    """All the user's PDFs with per-paper annotation status, for the left sidebar."""
    rows = conn.execute(
        """SELECT p.id, p.filename, p.project_id, p.created_at,
                  a.status       AS ann_status,
                  a.updated_at   AS ann_timestamp,
                  a.version      AS ann_version
             FROM papers p
        LEFT JOIN annotations a
               ON a.paper_id = p.id AND a.reviewer_id = ?
            WHERE p.user_id = ?
         ORDER BY p.created_at DESC""",
        (user_id, user_id),
    ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────
# CSV export (with formula-injection protection)
# ─────────────────────────────────────────────
_FORMULA_CHARS = set("=+-@\t\r")


def _csv_row(vals: list) -> str:
    def _esc(v):
        v = "" if v is None else str(v)
        if v and v[0] in _FORMULA_CHARS:
            v = "\t" + v
        v = v.replace('"', '""')
        return f'"{v}"' if any(c in v for c in (',', '"', '\n', '\r', '\t')) else v
    return ",".join(_esc(v) for v in vals) + "\r\n"


def build_export_rows(
    conn,
    user_id: int,
    paper_id: int | None = None,
    project_id: int | None = None,
) -> tuple[list[str], str]:
    papers_q = "SELECT id, filename, project_id FROM papers WHERE user_id=?"
    params: list = [user_id]
    if paper_id is not None:
        papers_q += " AND id=?"
        params.append(paper_id)
    elif project_id is not None:
        papers_q += " AND project_id=?"
        params.append(project_id)

    papers = {p["id"]: dict(p) for p in conn.execute(papers_q, params).fetchall()}
    proj_rows = conn.execute(
        "SELECT id, name FROM projects WHERE user_id=?", (user_id,)
    ).fetchall()
    proj_names = {p["id"]: p["name"] for p in proj_rows}

    paper_ids = list(papers.keys())
    annotations: list = []
    if paper_ids:
        placeholders = ",".join("?" * len(paper_ids))
        annotations = conn.execute(
            f"SELECT * FROM annotations WHERE paper_id IN ({placeholders}) "
            f"  AND reviewer_id=?",
            (*paper_ids, user_id),
        ).fetchall()

    all_ann_fields: set[str] = set()
    parsed_fa: list[dict] = []
    for ann in annotations:
        try:
            fa = json.loads(
                (ann["field_annotations_json"] if _row_has(ann, "field_annotations_json") else None) or "{}"
            )
        except Exception:
            fa = {}
        all_ann_fields.update(fa.keys())
        parsed_fa.append(fa)

    sorted_fields = sorted(all_ann_fields)
    ann_extra_cols: list[str] = []
    for fid in sorted_fields:
        ann_extra_cols += [
            f"{fid}__ann_status",
            f"{fid}__ai_value",
            f"{fid}__corrected_value",
            f"{fid}__flagged",
            f"{fid}__flag_note",
        ]

    header = ["filename", "project", "reviewer_id", "timestamp"] + FLAT_CSV_COLS + ann_extra_cols
    rows: list[str] = [_csv_row(header)]

    for ann, fa in zip(annotations, parsed_fa):
        try:
            data = json.loads(ann["data_json"] or "{}")
        except Exception:
            data = {}
        for col in ("correction_notes", "corrections_json",
                    "pipeline_predictions_json", "field_annotations_json"):
            if _row_has(ann, col):
                val = ann[col]
                if val:
                    data[col] = val

        paper = papers.get(ann["paper_id"], {})
        proj_name = proj_names.get(paper.get("project_id"), "") if paper.get("project_id") else ""
        base = [paper.get("filename", ""), proj_name, ann["reviewer_id"], ann["updated_at"]]
        base += [data.get(c, "") for c in FLAT_CSV_COLS]

        for fid in sorted_fields:
            fann = fa.get(fid, {}) or {}
            base += [
                fann.get("status", ""),
                fann.get("ai_value", ""),
                fann.get("corrected_value", ""),
                "Yes" if fann.get("flagged") else "",
                fann.get("flag_note", ""),
            ]

        rows.append(_csv_row(base))

    if paper_id is not None and paper_ids:
        fn = f"annotator_{Path(papers[paper_ids[0]]['filename']).stem}.csv"
    elif project_id is not None:
        fn = f"annotator_project_{proj_names.get(project_id, str(project_id))}.csv".replace(" ", "_")
    else:
        fn = "annotator_annotations.csv"
    return rows, fn


# ─────────────────────────────────────────────
# PDF bytes from the shared papers table
# ─────────────────────────────────────────────
def load_paper_pdf(conn, papers_dir: Path, paper_id: int, user_id: int,
                   is_admin: bool = False) -> tuple[bytes, str]:
    """Read a PDF off disk, enforcing ownership (admins bypass)."""
    row = conn.execute(
        "SELECT disk_filename, filename, user_id FROM papers WHERE id=?", (paper_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Paper not found")
    if row["user_id"] != user_id and not is_admin:
        raise HTTPException(403, "Access denied")

    disk_name = row["disk_filename"] or f"{row['filename']}.pdf"
    path = papers_dir / disk_name
    if not path.exists():
        raise HTTPException(404, "PDF file not found on disk")
    return path.read_bytes(), row["filename"]
