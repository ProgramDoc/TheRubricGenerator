"""GRADE preparation — the pure bridge from pooled bodies to GRADE ratings.

This is the composition step: it takes the list of pooled bodies that
:func:`pooling_prep.pool_extractions` / :func:`pooling_prep.pool_body` emit, joins
the per-study risk-of-bias labels (from the platform's quality-appraisal), pulls
the per-outcome human judgments, and calls :func:`grade.grade_body` for each body.
The analogue of ``pooling_prep`` for the certainty layer.

It is **pure** (no model, no I/O): the LLM indirectness auto-assessment lives in
:mod:`grade_indirectness` and is resolved one level up in :mod:`grade_assess`, so
this module stays fast to unit-test. When ``judgments_by_outcome`` already carries
an ``indirectness_levels`` (reviewer-supplied or pre-resolved), it is passed
straight through.

RoB join contract: ``rob_by_study`` maps a study id → its overall RoB label
(``quality_appraisal_results.rob_overall``: "Low" / "Some concerns" / "Moderate" /
"Serious" / "High" / "Critical" / "Insufficient information" / …). Each pooled
body carries ``pooled.studies[]`` with ``study_id`` + ``weight_pct``; we align the
RoB labels to that order so the risk-of-bias domain is properly weighted. Studies
with no RoB entry default to "Some concerns" severity via ``grade._ROB_SEVERITY``.
"""

from __future__ import annotations

from typing import Any, Optional

from .grade import GradeConfig, grade_body, sof_row


def _body_outcome_key(body: dict[str, Any]) -> str:
    """Normalized key used to look a body up in ``judgments_by_outcome``."""
    return " | ".join(str(body.get(k) or "").strip().lower()
                      for k in ("outcome_name", "comparison", "timepoint"))


def _match_judgments(body: dict[str, Any],
                     judgments_by_outcome: Optional[dict[str, dict]]) -> dict[str, Any]:
    """Find the per-outcome judgment dict for a body (exact key, then outcome-name)."""
    if not judgments_by_outcome:
        return {}
    key = _body_outcome_key(body)
    if key in judgments_by_outcome:
        return judgments_by_outcome[key] or {}
    name = str(body.get("outcome_name") or "").strip().lower()
    for k, v in judgments_by_outcome.items():
        if k == name or k.split(" | ")[0] == name:
            return v or {}
    return {}


def rob_labels_for_body(pool_result: dict[str, Any],
                        rob_by_study: Optional[dict[str, str]]) -> list[str]:
    """Per-study RoB labels aligned to ``pool_result['studies']`` order.

    Studies absent from ``rob_by_study`` yield an empty string, which
    ``grade._ROB_SEVERITY`` treats as "Some concerns" (severity 1) — conservative.
    """
    if not rob_by_study:
        return []
    out = []
    for s in pool_result.get("studies", []) or []:
        sid = s.get("study_id")
        out.append((rob_by_study.get(sid) if sid else None) or "")
    return out


def grade_one_body(body: dict[str, Any], *,
                   rob_by_study: Optional[dict[str, str]] = None,
                   judgments: Optional[dict[str, Any]] = None,
                   cfg: Optional[GradeConfig] = None) -> dict[str, Any]:
    """Grade a single pooled body → ``{...body descriptor, grade, sof_row}``.

    ``body`` is a ``pool_body`` result (carries ``pooled`` + ``design_class`` +
    ``measure`` + display labels). ``judgments`` is that outcome's judgment dict:
    ``baseline_risk_per_1000``, ``mid_benefit``, ``mid_harm``, ``indirectness_levels``
    (+ ``indirectness_reason``), ``dose_response``, ``opposing_confounding``,
    ``overrides``, and an optional ``initial`` override.
    """
    judgments = judgments or {}
    pool_result = body.get("pooled")
    descriptor = {
        "outcome_name": body.get("outcome_name"),
        "comparison": body.get("comparison"),
        "timepoint": body.get("timepoint"),
        "design_class": body.get("design_class"),
        "measure": body.get("measure"),
        "k": body.get("k"),
        "grade": None,
        "sof_row": None,
        "warnings": list(body.get("warnings") or []),
    }
    if not pool_result or not pool_result.get("pooled"):
        descriptor["warnings"].append("no pooled estimate — GRADE not computed")
        return descriptor

    # The pool_body result nests the outcome labels one level up; mirror them onto
    # the pool_outcome result so grade.sof_row can read them.
    pr = dict(pool_result)
    pr.setdefault("outcome_name", body.get("outcome_name"))
    pr.setdefault("comparison", body.get("comparison"))
    pr.setdefault("timepoint", body.get("timepoint"))
    pr.setdefault("design_class", body.get("design_class"))

    grade = grade_body(
        pr,
        initial=judgments.get("initial"),
        per_study_rob=rob_labels_for_body(pr, rob_by_study),
        indirectness_levels=judgments.get("indirectness_levels"),
        indirectness_reason=judgments.get("indirectness_reason", ""),
        mid_benefit=judgments.get("mid_benefit"),
        mid_harm=judgments.get("mid_harm"),
        baseline_risk_per_1000=judgments.get("baseline_risk_per_1000"),
        dose_response=judgments.get("dose_response"),
        opposing_confounding=bool(judgments.get("opposing_confounding")),
        subgroup=judgments.get("subgroup"),
        metaregression=judgments.get("metaregression"),
        overrides=judgments.get("overrides"),
        cfg=cfg,
    )
    descriptor["grade"] = grade
    descriptor["sof_row"] = sof_row(pr, grade, outcome=judgments.get("outcome"))
    return descriptor


def grade_bodies(pooled_bodies: list[dict[str, Any]], *,
                 rob_by_study: Optional[dict[str, str]] = None,
                 judgments_by_outcome: Optional[dict[str, dict]] = None,
                 cfg: Optional[GradeConfig] = None) -> list[dict[str, Any]]:
    """Grade every pooled body → one GRADE descriptor per body.

    ``pooled_bodies`` is the list from ``pooling_prep.pool_extractions`` /
    ``pool_studies``. ``rob_by_study`` is the study-id → RoB-label map;
    ``judgments_by_outcome`` maps an outcome key (``outcome | comparison |
    timepoint`` lower-cased, or just the outcome name) → that outcome's judgment
    dict. Indirectness auto-assessment is *not* done here (pure module) — resolve
    it in :mod:`grade_assess` before calling, or pass ``indirectness_levels``.
    """
    out = []
    for body in pooled_bodies:
        out.append(grade_one_body(
            body, rob_by_study=rob_by_study,
            judgments=_match_judgments(body, judgments_by_outcome), cfg=cfg))
    return out
