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

RoB join contract: **risk of bias arrives on the pooled study records.** Each entry
of ``pooled.studies[]`` carries its own ``rob`` label (``rob_overall`` from the
appraisal instrument: "Low" / "Some concerns" / "Moderate" / "Serious" / "High" /
"Critical" / "Insufficient information" / …) next to its ``weight_pct``, resolved per
(study × outcome) by ``pooling_prep.resolve_rob`` when the body was assembled. That
co-location is the point: the risk-of-bias domain is weight-driven, and the pooler
drops studies without usable data, so the pooled order is not the input order — a
label list supplied alongside would silently shift by one after the first drop.

``rob_by_study`` (study id → label) remains as a legacy study-level override for
callers supplying labels out-of-band; it fills gaps only. Labels are sourced from
``quality_appraisal_results`` by :mod:`grade_rob_source`, which is impure by design so
this module stays so. Studies with no label from either channel default to "Some
concerns" severity via ``grade._ROB_SEVERITY``; a body with no labels at all is
returned un-graded rather than rated as though the domain were clean.
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
                        rob_by_study: Optional[dict[str, str]] = None) -> list[str]:
    """Per-study RoB labels aligned to ``pool_result['studies']`` order.

    The **primary channel is the label already on each study record** — the pooling
    bridge resolves risk of bias per (study × outcome) via ``pooling_prep.resolve_rob``
    and ``pool_outcome`` carries it through next to that study's ``weight_pct``. This
    function only reads it back off in pooled order.

    ``rob_by_study`` is the legacy study-level override, kept for callers that supply
    labels out-of-band (e.g. straight off an HTTP request body). It fills gaps only —
    a label already attached upstream wins, because that one was resolved for *this*
    outcome while a study-level map cannot be.

    Studies with no label from either source yield an empty string, which
    ``grade._ROB_SEVERITY`` treats as "Some concerns" (severity 1) — conservative.
    """
    studies = pool_result.get("studies", []) or []
    if not studies:
        return []
    out = []
    for s in studies:
        label = (s.get("rob") or "").strip()
        if not label and rob_by_study:
            sid = s.get("study_id")
            label = ((rob_by_study.get(sid) if sid else None) or "").strip()
        out.append(label)
    return out


def grade_one_body(body: dict[str, Any], *,
                   rob_by_study: Optional[dict[str, str]] = None,
                   judgments: Optional[dict[str, Any]] = None,
                   require_rob: bool = True,
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

    rob_labels = rob_labels_for_body(pr, rob_by_study)
    if require_rob and not any(r.strip() for r in rob_labels):
        # Rating a body whose risk of bias was never assessed is indistinguishable, in
        # the output, from rating one that was assessed and found clean. Refuse, and
        # say which body — the caller can attach labels or opt out explicitly.
        descriptor["warnings"].append(
            "no risk-of-bias judgements for this body — GRADE not computed; attach "
            "labels via the pooling bridge (attach_rob) or pass require_rob=False")
        return descriptor

    grade = grade_body(
        pr,
        initial=judgments.get("initial"),
        per_study_rob=rob_labels,
        require_rob=require_rob,
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
                 require_rob: bool = True,
                 cfg: Optional[GradeConfig] = None) -> list[dict[str, Any]]:
    """Grade every pooled body → one GRADE descriptor per body.

    ``pooled_bodies`` is the list from ``pooling_prep.pool_extractions`` /
    ``pool_studies``. Risk of bias normally rides on the pooled study records (see
    :func:`rob_labels_for_body`); ``rob_by_study`` is the legacy study-level override.
    ``judgments_by_outcome`` maps an outcome key (``outcome | comparison |
    timepoint`` lower-cased, or just the outcome name) → that outcome's judgment
    dict. Indirectness auto-assessment is *not* done here (pure module) — resolve
    it in :mod:`grade_assess` before calling, or pass ``indirectness_levels``.

    With ``require_rob=True`` (the default) a body carrying no risk-of-bias labels is
    returned un-graded with an explanatory warning rather than rated as though the
    domain were clean.
    """
    out = []
    for body in pooled_bodies:
        out.append(grade_one_body(
            body, rob_by_study=rob_by_study, require_rob=require_rob,
            judgments=_match_judgments(body, judgments_by_outcome), cfg=cfg))
    return out
