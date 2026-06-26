"""Independently-servable synthesis agents (pure — no LLM, no DB, no I/O).

Thin glue over :mod:`backend.synthesis_stats` so the meta-analysis pooling and
the GRADE certainty engine can each be called as a standalone endpoint
(``POST /api/agents/pool``, ``/api/agents/grade``, ``/api/agents/sof``) — on
their own or chained — in addition to the orchestrated review pipeline
(:mod:`backend.synthesis`). Every entry point takes/returns plain JSON-able
dicts and raises ``ValueError`` on bad input (the route maps that to HTTP 400).

The three agents map onto the ASCO table spec:
  * ``pool_effects``  -> Table 5 relative-effect + heterogeneity columns
  * ``grade_certainty`` -> Table 5 certainty + per-domain reasons (+ absolute effects)
  * ``build_sof_row`` -> one assembled Table 5 (Summary-of-Findings) row
"""
from __future__ import annotations

from typing import Any

from backend import synthesis_stats as stats


def _num(x: Any) -> float | None:
    try:
        return None if x is None or x == "" else float(x)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Pooling agent
# ---------------------------------------------------------------------------

def _build_effects(measure: str, rows: list[dict] | None, correction: float = 0.5):
    """Turn request rows into :class:`EffectSize` objects.

    Each row is either pre-computed ``{yi, vi, n?, label?, subgroup?}`` or raw
    ``{raw: {...}, label?, subgroup?}`` (cell counts / means -> ``effect_size``).
    Returns ``(effects, labels, subgroups, problems)`` where ``problems`` lists
    rows that could not be used (kept, never raised, so a bad row is visible).
    """
    effects, labels, subgroups, problems = [], [], [], []
    for i, row in enumerate(rows or []):
        label = str(row.get("label") or f"Study {i + 1}")
        es = None
        if row.get("yi") is not None and row.get("vi") is not None:
            try:
                es = stats.EffectSize(float(row["yi"]), float(row["vi"]), measure,
                                      n=row.get("n"), raw=row.get("raw") or {})
            except (TypeError, ValueError):
                es = None
        elif row.get("raw"):
            es = stats.effect_size(measure, row["raw"], correction)
        if es is None or not (es.vi and es.vi > 0):
            problems.append({"index": i, "label": label, "reason": "missing or invalid yi/vi"})
            continue
        effects.append(es)
        labels.append(label)
        subgroups.append(row.get("subgroup"))
    return effects, labels, subgroups, problems


def pool_effects(payload: dict) -> dict:
    """Pool a set of effect sizes for one outcome under a PICO.

    Body: ``{measure, effects:[...], model?, tau2_method?, fe_method?,
    re_ci_method?, alpha?, continuity_correction?, publication_bias?,
    sensitivity?}``. Returns the :func:`synthesis_stats.pool` result plus
    optional publication-bias, subgroup, and sensitivity blocks and ``total_n``.
    """
    measure = payload.get("measure")
    if measure not in stats.NULL_VALUE:
        raise ValueError(f"unknown or unsupported measure: {measure!r}")
    model = payload.get("model", "random")
    tau2_method = payload.get("tau2_method", "REML")
    fe_method = payload.get("fe_method", "IV")
    re_ci_method = payload.get("re_ci_method", "wald")
    alpha = float(payload.get("alpha", 0.05))
    correction = float(payload.get("continuity_correction", 0.5))

    effects, labels, subgroups, problems = _build_effects(measure, payload.get("effects"), correction)
    if not effects:
        return {"status": "no_studies", "k": 0, "problems": problems}

    out = stats.pool(effects, measure, model=model, tau2_method=tau2_method,
                     fe_method=fe_method, re_ci_method=re_ci_method, alpha=alpha)
    out["labels"] = labels
    out["problems"] = problems
    out["total_n"] = sum(e.n for e in effects if e.n) or None

    use = out.get("random") or out.get("fixed") or {}

    want_pubbias = payload.get("publication_bias", len(effects) >= 3)
    if want_pubbias and len(effects) >= 3:
        out["publication_bias"] = {
            "funnel": stats.funnel_data(effects, use.get("estimate", 0.0)),
            "egger": stats.eggers_test(effects),
            "trimfill": stats.trim_and_fill(effects, measure, model=model, tau2_method=tau2_method),
        }

    if any(s is not None for s in subgroups):
        out["subgroup"] = stats.subgroup_analysis(
            effects, [s or "(unspecified)" for s in subgroups], measure,
            model=model, tau2_method=tau2_method, alpha=alpha)

    if len(effects) >= 3 and payload.get("sensitivity", True):
        out["sensitivity"] = {
            "leave_one_out": stats.leave_one_out(effects, measure, model=model,
                                                 tau2_method=tau2_method, labels=labels),
            "influence": stats.influence_diagnostics(effects, tau2_method=tau2_method, labels=labels),
        }
    return out


# ---------------------------------------------------------------------------
# GRADE certainty agent
# ---------------------------------------------------------------------------

def grade_certainty(payload: dict) -> dict:
    """Rate a pooled body of evidence on the GRADE scale.

    Accepts either explicit ``pooled``/``heterogeneity`` fields or a
    ``pool_result`` (the output of :func:`pool_effects`) to chain from, plus the
    GRADE-specific inputs (``per_study_rob``, design/``initial``, indirectness,
    MIDs, baseline risk, and the NRS upgrade flags). Delegates to
    :func:`synthesis_stats.grade_body_of_evidence`.
    """
    pr = payload.get("pool_result") or {}
    measure = payload.get("measure") or pr.get("measure")
    if not measure:
        raise ValueError("measure is required")

    pooled = payload.get("pooled") or pr.get("random") or pr.get("fixed")
    if not pooled or (pooled.get("estimate") is None and pooled.get("ci_low") is None):
        raise ValueError("a pooled estimate (estimate / ci_low / ci_high) is required")

    het = payload.get("heterogeneity") or pr.get("heterogeneity") or {}
    pubbias = pr.get("publication_bias") or {}
    egger = payload.get("egger") or pubbias.get("egger")
    trimfill = payload.get("trimfill") or pubbias.get("trimfill")
    subgroup = payload.get("subgroup") or pr.get("subgroup")
    weights = payload.get("weights") or pooled.get("weights_pct")

    # Initial certainty / design.
    initial = payload.get("initial")
    is_rand = payload.get("is_randomized")
    if initial is None:
        if is_rand is None:
            design = (payload.get("design") or "").lower()
            is_rand = ("random" in design) and ("non-random" not in design and "nonrandom" not in design)
        initial = "High" if is_rand else "Low"

    is_binary = payload.get("is_binary")
    if is_binary is None:
        is_binary = measure in ("OR", "RR", "RD")
    total_n = payload.get("total_n")
    if total_n is None:
        total_n = pr.get("total_n")

    return stats.grade_body_of_evidence(
        initial=initial,
        per_study_rob=payload.get("per_study_rob") or [],
        weights=weights, heterogeneity=het, pooled=pooled, measure=measure,
        total_n=total_n, subgroup=subgroup, egger=egger, trimfill=trimfill,
        indirectness_levels=int(payload.get("indirectness_levels") or 0),
        indirectness_reason=payload.get("indirectness_reason", ""),
        mid_benefit=_num(payload.get("mid_benefit")),
        mid_harm=_num(payload.get("mid_harm")),
        is_binary=bool(is_binary),
        is_randomized=is_rand,
        dose_response=payload.get("dose_response"),
        opposing_confounding=bool(payload.get("opposing_confounding")),
        metaregression=payload.get("metaregression"),
        baseline_risk_per_1000=_num(payload.get("baseline_risk_per_1000")),
        overrides=payload.get("overrides") or None,
    )


# ---------------------------------------------------------------------------
# Table 5 — Summary-of-Findings row
# ---------------------------------------------------------------------------

def build_sof_row(outcome: dict, pool_result: dict, grade_result: dict) -> dict:
    """Assemble one ASCO Table-5 (GRADE Summary-of-Findings) row.

    Relative effect is back-transformed from the analysis scale to the display
    scale (e.g. RR/OR), and the certainty reasons keep the per-domain breakdown.
    """
    measure = pool_result.get("measure") or outcome.get("effect_measure")
    pooled = pool_result.get("random") or pool_result.get("fixed") or {}

    def disp(v):
        if v is None or measure is None:
            return None
        return round(stats.back_transform(v, measure), 3)

    reasons = []
    for d in grade_result.get("domains", []):
        mag = d.get("downgrade", 0) or d.get("upgrade", 0)
        if mag:
            reasons.append({"domain": d["domain"], "direction": d.get("kind"),
                            "levels": mag, "reason": d.get("reason")})

    return {
        "outcome": outcome.get("name"),
        "timeframe": outcome.get("timeframe") or outcome.get("follow_up"),
        "n_studies": pool_result.get("k"),
        "n_participants": outcome.get("n_participants") or pool_result.get("total_n"),
        "measure": measure,
        "relative_effect": {"estimate": disp(pooled.get("estimate")),
                            "ci_low": disp(pooled.get("ci_low")),
                            "ci_high": disp(pooled.get("ci_high"))},
        "absolute_effects": grade_result.get("absolute_effects"),
        "certainty": grade_result.get("final"),
        "certainty_reasons": reasons,
        "explanation": grade_result.get("explanation"),
    }


def sof(payload: dict) -> dict:
    """Convenience Table-5 producer: pool -> grade -> assemble one SoF row.

    Body = the union of :func:`pool_effects` and :func:`grade_certainty` inputs,
    plus an optional ``outcome`` block ``{name, timeframe/follow_up,
    n_participants, effect_measure}``. Returns ``{pool, grade, sof_row}``.
    """
    pooled_out = pool_effects(payload)
    if pooled_out.get("status") != "ok":
        return {"pool": pooled_out, "grade": None, "sof_row": None}
    grade_payload = dict(payload)
    grade_payload["pool_result"] = pooled_out
    grade_out = grade_certainty(grade_payload)
    outcome = payload.get("outcome") or {"name": payload.get("outcome_name"),
                                         "effect_measure": payload.get("measure")}
    return {"pool": pooled_out, "grade": grade_out,
            "sof_row": build_sof_row(outcome, pooled_out, grade_out)}
