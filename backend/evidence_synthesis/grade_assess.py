"""GRADE orchestration — the model-wired entry points.

The analogue of ``pooling_extract`` for the certainty layer: the thin layer that
composes the pure pieces and adds the one optional model call (hybrid indirectness
auto-assessment). Everything numeric is delegated to :mod:`grade` /
:mod:`grade_prep`; the only LLM touch is :mod:`grade_indirectness`, and only when a
body's ``indirectness_levels`` was not supplied by the reviewer.

Two entry points:

* :func:`grade_from_pooled` — you already ran the pooling agent; grade the bodies.
* :func:`grade_from_studies` — convenience: run ``pooling_extract.pool_studies``
  first (injected-first / self-extract), then grade.

Hybrid indirectness: for each body, if the reviewer supplied ``indirectness_levels``
it is used verbatim; otherwise, when ``auto_indirectness`` is on and a target PICO
is available, one model call fills it in and the per-subdomain detail is attached to
the body descriptor under ``indirectness_detail``.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .grade_prep import _match_judgments, grade_bodies

logger = logging.getLogger("grade_assess")


def _resolve_indirectness(pooled_bodies: list[dict[str, Any]],
                          judgments_by_outcome: Optional[dict[str, dict]],
                          target_pico: Optional[dict],
                          auto_indirectness: bool) -> tuple[dict[str, dict], dict[str, dict]]:
    """Fill in missing indirectness levels via the LLM auto-assessor.

    Returns ``(resolved_judgments_by_outcome, indirectness_detail_by_key)``. The
    reviewer's value always wins; auto only fills gaps. ``target_pico`` may be blank
    (the assessor then judges surrogate-outcome directness only).
    """
    from .grade_prep import _body_outcome_key

    resolved: dict[str, dict] = {}
    detail: dict[str, dict] = {}
    for body in pooled_bodies:
        key = _body_outcome_key(body)
        j = dict(_match_judgments(body, judgments_by_outcome))
        if j.get("indirectness_levels") is None and auto_indirectness:
            pr = body.get("pooled") or {}
            body_ctx = {
                "outcome_name": body.get("outcome_name"),
                "comparison": body.get("comparison"),
                "measure": body.get("measure"),
                "favorable_direction": (pr or {}).get("favorable_direction"),
                "k": body.get("k"),
                "study_context": j.get("study_context"),
            }
            try:
                from . import grade_indirectness
                per_sub, severity, levels, expl = grade_indirectness.assess_body(target_pico, body_ctx)
                j["indirectness_levels"] = levels
                j["indirectness_reason"] = expl
                detail[key] = {"per_subdomain": per_sub, "severity": severity, "levels": levels}
            except Exception:  # noqa: BLE001 — a failed auto-assessment must not abort the batch
                logger.exception("grade: indirectness auto-assessment failed for %s", key)
                j.setdefault("indirectness_levels", 0)
                j.setdefault("indirectness_reason", "indirectness not assessed (auto-assessment unavailable)")
        resolved[key] = j
    return resolved, detail


def grade_from_pooled(pooled_bodies: list[dict[str, Any]], *,
                      rob_by_study: Optional[dict[str, str]] = None,
                      judgments_by_outcome: Optional[dict[str, dict]] = None,
                      target_pico: Optional[dict] = None,
                      auto_indirectness: bool = True,
                      cfg=None) -> list[dict[str, Any]]:
    """Grade a list of already-pooled bodies. Resolves hybrid indirectness first.

    ``pooled_bodies`` is the output of ``pooling_prep.pool_extractions`` /
    ``pooling_extract.pool_studies``. Attaches ``indirectness_detail`` to each body
    descriptor when the level was auto-assessed.
    """
    from .grade_prep import _body_outcome_key

    resolved, detail = _resolve_indirectness(
        pooled_bodies, judgments_by_outcome, target_pico, auto_indirectness)
    results = grade_bodies(pooled_bodies, rob_by_study=rob_by_study,
                           judgments_by_outcome=resolved, cfg=cfg)
    # Re-attach the auto-assessment detail keyed by the same body order.
    for body, res in zip(pooled_bodies, results):
        d = detail.get(_body_outcome_key(body))
        if d is not None:
            res["indirectness_detail"] = d
    return results


def grade_from_studies(items: list[dict[str, Any]], *,
                       rob_by_study: Optional[dict[str, str]] = None,
                       judgments_by_outcome: Optional[dict[str, dict]] = None,
                       target_pico: Optional[dict] = None,
                       auto_indirectness: bool = True,
                       measures: Optional[dict[str, str]] = None,
                       default_measure: Optional[str] = None,
                       include_timepoint: bool = True,
                       model: str = "random",
                       tau2_method: str = "REML",
                       cfg=None) -> list[dict[str, Any]]:
    """Convenience: pool a mixed batch of studies, then grade the bodies.

    ``items`` is the ``pooling_extract.pool_studies`` input shape (flat study dicts
    with optional ``outcomes`` / ``pdf_bytes``). Returns one GRADE descriptor per
    pooled body.
    """
    from .pooling_extract import pool_studies

    bodies = pool_studies(
        items, measures=measures, default_measure=default_measure,
        include_timepoint=include_timepoint, model=model, tau2_method=tau2_method)
    return grade_from_pooled(
        bodies, rob_by_study=rob_by_study, judgments_by_outcome=judgments_by_outcome,
        target_pico=target_pico, auto_indirectness=auto_indirectness, cfg=cfg)
