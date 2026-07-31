"""Pooling preparation — absorb per-study extraction outputs and combine to pool.

This is the bridge between **extraction / Table 2** (per-study, multi-outcome) and
the **pooling engine** (`pooling.pool_outcome`, one call = one outcome). Extraction
produces, for each study, a set of ``outcomes[]`` objects (one per outcome ×
comparison × timepoint) carrying either raw arm data or a reported effect + CI.
Pooling needs those regrouped **across studies into bodies of evidence** — one body
per (outcome × comparison × timepoint × design-class) — with a single effect
measure. This module does exactly that regroup-and-route, in pure Python:

    many studies × their outcomes[]  ─►  group into bodies  ─►  pool each body

Two golden rules from the synthesis overview are enforced here, not left to the
caller:

* **Never pool RCTs with non-randomized studies.** Randomized and non-randomized
  designs land in *separate* bodies (GRADE rates them separately). See
  ``_design_class``.
* **Never pool unlike measures.** A body is pooled on one measure; studies whose
  reported metric can't be reconciled to it (an OR where the body pools RR, with no
  raw counts to recompute) are **excluded with a named warning**, never silently
  coerced.

It is pure (no model): it consumes whatever the extraction agents already emitted.
The raw arm numbers come from ``pooling_extract.extract_outcome_data`` (the
model-wired "outcome-data" pass); reported effects come from Table 2's
``outcomes[]``. Either is enough to pool.

Full methodology: ``docs/shareable/pooling_meta_analysis_shareable.md`` (§ "From
extraction to a pooled outcome").
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from .pooling import pool_outcome
from .table2 import _coerce_num, build_study_id, canonicalize_metric

# Study-design → body class. Randomized and non-randomized evidence are pooled as
# separate bodies (GRADE g-rule). Keys match annotator.TYPE_FIELD_IDS / the QA
# STUDY_TYPE_REGISTRY vocabulary.
_RANDOMIZED = {
    "randomized controlled trial", "rct", "crossover trial", "cross-over trial",
    "cluster randomized trial", "cluster randomised trial",
    "randomized trial", "randomised controlled trial",
}
_NON_RANDOMIZED = {
    "cohort study", "cohort", "case-control", "case-control study",
    "non-randomized trial", "non-randomised trial", "cross-sectional",
    "cross-sectional (analytical)", "case-crossover", "single-arm trial",
    "dose-escalation study", "observational", "nrsi",
}

# Raw-arm field names this bridge recognizes on an outcome object or study row.
_BINARY_ARM_FIELDS = ("events_int", "n_int", "events_ctrl", "n_ctrl")
_RATE_ARM_FIELDS = ("events_int", "time_int", "events_ctrl", "time_ctrl")  # person-time -> IRR
_CONTINUOUS_ARM_FIELDS = (
    "mean_int", "sd_int", "n_int", "mean_ctrl", "sd_ctrl", "n_ctrl")


# ---------------------------------------------------------------------------
# 1. Normalization + classification helpers
# ---------------------------------------------------------------------------

def _norm(text: Any) -> str:
    """Lowercase, strip punctuation/whitespace — for matching 'the same' outcome."""
    if text is None:
        return ""
    s = re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()
    return re.sub(r"\s+", " ", s)


def _design_class(design: Any) -> str:
    """Map a study-design label to a pooling body class: rct / nrs / unknown."""
    d = _norm(design)
    if not d:
        return "unknown"
    if d in {_norm(x) for x in _RANDOMIZED} or "randomiz" in d or "randomis" in d:
        return "rct"
    if d in {_norm(x) for x in _NON_RANDOMIZED} or "cohort" in d or "observational" in d:
        return "nrs"
    return "unknown"


def _has_all(d: dict[str, Any], keys: tuple[str, ...]) -> bool:
    return all(_coerce_num(d.get(k)) is not None for k in keys)


# ---------------------------------------------------------------------------
# 1b. Risk of bias — resolved per (study x outcome), carried, never judged
# ---------------------------------------------------------------------------

CANONICAL_KEY = "canonical_outcome"


def resolve_rob(
    study_level: dict[str, Any],
    oc: Optional[dict[str, Any]],
    outcome_key: Optional[str] = None,
) -> tuple[Optional[str], str]:
    """Resolve the risk-of-bias label for one (study x outcome) pair.

    Returns ``(label, source)``. ``outcome_key`` is the body's outcome name — the
    canonical one when harmonization has run. Precedence: the outcome object's own
    label, then the study's ``rob_by_outcome`` map, then a study-level label, then
    nothing. An explicit ``rob_source`` on either dict always wins over the inferred
    value — that is how a risk-of-bias instrument stamps ``"tool"``.

    Risk of bias is per outcome because RoB 2 and ROBINS-I are outcome-specific:
    domain 4 (measurement of the outcome) and domain 5 (selection of the reported
    result) genuinely differ between outcomes, so one trial can be Low for mortality
    and High for an unblinded subjective outcome. Nothing here judges the label —
    mapping labels to severities is the GRADE agent's job.
    """
    oc = oc or {}
    explicit = oc.get("rob_source") or study_level.get("rob_source")

    def _out(label: Any, inferred: str) -> tuple[Optional[str], str]:
        text = str(label).strip() if label is not None else ""
        if not text:
            return None, "missing"
        return text, (explicit or inferred)

    if oc.get("rob"):                                       # 1. outcome object
        return _out(oc["rob"], "user_outcome")

    table = study_level.get("rob_by_outcome") or {}         # 2. per-outcome map
    if table:
        key = (oc.get(CANONICAL_KEY) or outcome_key
               or oc.get("name") or oc.get("outcome_name"))
        # Matched exact-after-normalization, never fuzzily: harmonization is the
        # sanctioned semantic layer and runs once, where a reviewer can inspect it.
        # A wrong label on the wrong body is worse than a conservative "missing".
        hit = {_norm(k): k for k in table}.get(_norm(key))
        if hit is not None and table.get(hit):
            return _out(table[hit], "user_outcome")

    if study_level.get("rob"):                              # 3. study-level
        return _out(study_level["rob"], "user_study")
    return None, "missing"                                  # 4. nothing


def attach_rob(
    studies: list[dict[str, Any]],
    records: Optional[list[dict[str, Any]]],
    id_key: str = "study_id",
) -> list[dict[str, Any]]:
    """Merge risk-of-bias records onto study dicts. Pure — no I/O, no judgement.

    ``records``: ``[{"study_id": ..., "outcome": <str or None>, "rob": <label>,
    "rob_source": ...}, ...]``. A record with an ``outcome`` populates that study's
    ``rob_by_outcome`` map; one without becomes the study-level ``rob``. This is the
    seam an appraisal-database adapter targets.

    **Order matters**: ``attach_rob`` -> ``harmonize_outcomes`` -> ``group_into_bodies``.
    Attaching after harmonization leaves the ``rob_by_outcome`` keys un-canonicalized,
    so every per-outcome lookup misses and a fully-appraised body reads as unappraised.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for r in records or []:
        sid = r.get(id_key) or r.get("study_id")
        if not sid or not r.get("rob"):
            continue
        slot = by_id.setdefault(str(sid), {"rob_by_outcome": {}})
        if r.get("outcome"):
            slot["rob_by_outcome"][r["outcome"]] = r["rob"]
        else:
            slot["rob"] = r["rob"]
        if r.get("rob_source"):
            slot["rob_source"] = r["rob_source"]

    out: list[dict[str, Any]] = []
    for s in studies:
        sid = str(s.get("study_id") or build_study_id(
            s.get("citation_authors"), s.get("citation_year")) or "")
        add = by_id.get(sid)
        if not add:
            out.append(s)
            continue
        s2 = dict(s)
        s2["rob_by_outcome"] = {**(s.get("rob_by_outcome") or {}), **add["rob_by_outcome"]}
        for k in ("rob", "rob_source"):
            if add.get(k) and not s2.get(k):
                s2[k] = add[k]
        out.append(s2)
    return out


# ---------------------------------------------------------------------------
# 2. One outcome object -> a pooling study input
# ---------------------------------------------------------------------------

def outcome_to_study_input(
    study_level: dict[str, Any],
    oc: dict[str, Any],
    target_measure: Optional[str],
    outcome_key: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Map one extraction outcome object to a ``pooling.study_effect`` input dict.

    Priority: **raw arm data** — 2×2 counts (OR/RR/RD), events + **person-time**
    (IRR), or mean/SD/N (MD/SMD), searched on the outcome object first then the study
    row — so the pooler computes the effect size with a proper variance + continuity
    correction; **else the reported effect** (``effect_estimate`` +
    ``ci_lower``/``ci_upper``), used only when its canonical metric matches
    ``target_measure``. The raw-data choice is **target-aware**: an IRR body takes
    person-time arms (never a 2×2, which can't make an IRR), a ratio/RD body takes the
    2×2, a continuous body takes mean/SD. Returns None when nothing is poolable.
    """
    study_id = study_level.get("study_id") or build_study_id(
        study_level.get("citation_authors"), study_level.get("citation_year"))
    design = study_level.get("study_type") or study_level.get("design")
    base = {"study_id": study_id, "design": design}
    # Carried through to the pooled record so it stays paired with this study's weight.
    base["rob"], base["rob_source"] = resolve_rob(study_level, oc, outcome_key)
    tgt = (target_measure or "").upper()

    def _grab(src: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
        base.update({k: _coerce_num(src.get(k)) for k in fields})
        return base

    # Raw arm data — target-aware. Check the outcome object, then the study row.
    for src in (oc, study_level):
        if tgt == "IRR":
            if _has_all(src, _RATE_ARM_FIELDS):
                return _grab(src, _RATE_ARM_FIELDS)
        elif tgt in ("OR", "RR", "RD"):
            if _has_all(src, _BINARY_ARM_FIELDS):
                return _grab(src, _BINARY_ARM_FIELDS)
        elif tgt in ("MD", "SMD"):
            if _has_all(src, _CONTINUOUS_ARM_FIELDS):
                return _grab(src, _CONTINUOUS_ARM_FIELDS)
        else:                                   # unknown / None target — accept any shape
            if _has_all(src, _BINARY_ARM_FIELDS):
                return _grab(src, _BINARY_ARM_FIELDS)
            if _has_all(src, _RATE_ARM_FIELDS):
                return _grab(src, _RATE_ARM_FIELDS)
            if _has_all(src, _CONTINUOUS_ARM_FIELDS):
                return _grab(src, _CONTINUOUS_ARM_FIELDS)

    # Reported effect — only if its metric matches the body's target measure.
    metric, _family = canonicalize_metric(oc.get("effect_metric"))
    est = _coerce_num(oc.get("effect_estimate"))
    if est is None or metric is None:
        return None
    if target_measure is not None and metric.upper() != target_measure.upper():
        return None
    base.update({
        "estimate": est,
        "ci_lower": _coerce_num(oc.get("ci_lower")),
        "ci_upper": _coerce_num(oc.get("ci_upper")),
        "se": _coerce_num(oc.get("se")),
    })
    return base


def study_is_poolable(study: dict[str, Any]) -> bool:
    """True if this study already carries at least one outcome with usable pooling
    data (raw arm data or a reported effect estimate).

    This is the dual-mode gate: a study that IS poolable can be pooled from its
    injected extraction elements with zero model calls; one that is NOT poolable
    needs the outcome-data extraction pass to run first (see ``pooling_extract``).
    """
    outcomes = study.get("outcomes")
    if not isinstance(outcomes, list):
        return False
    return any(
        isinstance(oc, dict) and outcome_to_study_input(study, oc, None) is not None
        for oc in outcomes
    )


def _study_input_measure(oc: dict[str, Any]) -> Optional[str]:
    """The canonical measure a single outcome object would contribute, if reported."""
    metric, _family = canonicalize_metric(oc.get("effect_metric"))
    return metric.upper() if metric else None


def _infer_measure_from_arms(oc: dict[str, Any], study_level: dict[str, Any]) -> Optional[str]:
    """Default measure for a raw-arm-data study with no reported metric: RR / IRR / MD.

    Binary counts default to RR; events + person-time to IRR; mean/SD to MD. Binary is
    checked before rate so an events+total table isn't misread as a rate."""
    for src in (oc, study_level):
        if _has_all(src, _BINARY_ARM_FIELDS):
            return "RR"
        if _has_all(src, _RATE_ARM_FIELDS):
            return "IRR"
        if _has_all(src, _CONTINUOUS_ARM_FIELDS):
            return "MD"
    return None


# ---------------------------------------------------------------------------
# 3. Group studies' outcomes into bodies of evidence
# ---------------------------------------------------------------------------

def group_into_bodies(
    studies: Iterable[dict[str, Any]],
    *,
    include_timepoint: bool = True,
) -> list[dict[str, Any]]:
    """Regroup per-study ``outcomes[]`` into bodies of evidence.

    Each input study is a dict of study-level fields plus an ``outcomes`` list (the
    shape Table 2 / extraction emit). One body = all outcome objects sharing a
    normalized (outcome name × comparison × timepoint × design-class) key —
    randomized and non-randomized designs never share a body. ``include_timepoint``
    keeps different follow-up times as separate bodies (default; conservative);
    set False to pool across timepoints.

    Returns a list of body dicts: ``{outcome_name, comparison, timepoint,
    design_class, members: [(study_level, outcome_obj), ...]}`` — display labels are
    taken from the first member (unnormalized).
    """
    bodies: dict[tuple, dict[str, Any]] = {}
    for study in studies:
        outcomes = study.get("outcomes")
        if not isinstance(outcomes, list):
            continue
        dcls = _design_class(study.get("study_type") or study.get("design"))
        for oc in outcomes:
            if not isinstance(oc, dict):
                continue
            # Prefer the harmonized canonical label (set by the harmonization layer)
            # over the study's verbatim wording, so synonymous outcomes group together.
            name = oc.get("canonical_outcome") or oc.get("name") or oc.get("outcome_name")
            comparison = oc.get("comparison") or study.get("population_comparator")
            timing = oc.get("timing") or oc.get("outcome_timing")
            key = (_norm(name), _norm(comparison), _norm(timing) if include_timepoint else "", dcls)
            body = bodies.setdefault(key, {
                "outcome_name": name,
                "comparison": comparison,
                "timepoint": timing,
                "design_class": dcls,
                "members": [],
            })
            body["members"].append((study, oc))
    return list(bodies.values())


# ---------------------------------------------------------------------------
# 4. Choose the body's measure, build inputs, pool
# ---------------------------------------------------------------------------

def _choose_measure(members: list[tuple[dict, dict]], override: Optional[str]) -> Optional[str]:
    """Pick the measure to pool a body on: caller override → majority reported
    metric → default inferred from raw arm data (RR binary / MD continuous)."""
    if override:
        metric, _ = canonicalize_metric(override)
        return metric.upper() if metric else override.upper()
    counts: dict[str, int] = {}
    for study_level, oc in members:
        m = _study_input_measure(oc)
        if m:
            counts[m] = counts.get(m, 0) + 1
    if counts:
        # Most common reported metric (ties broken by first-seen order-insensitively).
        return max(counts.items(), key=lambda kv: kv[1])[0]
    for study_level, oc in members:
        m = _infer_measure_from_arms(oc, study_level)
        if m:
            return m
    return None


def pool_body(
    body: dict[str, Any],
    *,
    measure: Optional[str] = None,
    model: str = "random",
    tau2_method: str = "REML",
) -> dict[str, Any]:
    """Pool one body of evidence. Chooses the measure, builds per-study inputs
    (dropping unreconcilable members with a named exclusion), and calls
    ``pool_outcome``. Returns the body descriptor + ``measure`` + ``pooled`` result
    (or ``pooled=None`` when < 1 study survives) + ``excluded`` labels + ``warnings``
    (e.g. an unclassified study design). ``favorable_direction`` is read from the
    outcome objects and threaded into ``pool_outcome``."""
    members = body.get("members", [])
    target = _choose_measure(members, measure)
    favorable_direction = _choose_favorable_direction(members)
    inputs: list[dict[str, Any]] = []
    excluded: list[str] = []
    for study_level, oc in members:
        si = outcome_to_study_input(study_level, oc, target, body.get("outcome_name"))
        label = (study_level.get("study_id")
                 or build_study_id(study_level.get("citation_authors"),
                                   study_level.get("citation_year")))
        if si is None:
            m = _study_input_measure(oc)
            if (target or "").upper() == "IRR":
                reason = ("IRR needs person-time (time_int/time_ctrl) or a reported "
                          "IRR estimate+CI — a 2×2 count table is not enough")
            elif m and target:
                reason = f"reported {m}, body pools {target}"
            else:
                reason = "no poolable effect or arm data"
            excluded.append(f"{label}: {reason}")
        else:
            inputs.append(si)

    # Conservative unknown-design handling: never merged with rct/nrs bodies (the key
    # already separates them), and flagged so a reviewer knows this body's design
    # provenance is unverified — not a confirmed RCT or non-randomized body.
    warnings: list[str] = []
    if body.get("design_class") == "unknown":
        raw = sorted({str(sl.get("study_type") or sl.get("design") or "unspecified")
                      for sl, _oc in members})
        warnings.append(
            "unclassified study design — pooled as a separate 'unknown' body, not a "
            f"confirmed RCT/NRS body; raw design label(s): {', '.join(raw)}")

    result: dict[str, Any] = {
        "outcome_name": body.get("outcome_name"),
        "comparison": body.get("comparison"),
        "timepoint": body.get("timepoint"),
        "design_class": body.get("design_class"),
        "measure": target,
        "favorable_direction": favorable_direction,
        "k": len(inputs),
        "excluded": excluded,
        "warnings": warnings,
        "pooled": None,
    }
    if target and inputs:
        result["pooled"] = pool_outcome(
            inputs, target, model=model, tau2_method=tau2_method,
            outcome_name=body.get("outcome_name"),
            favorable_direction=favorable_direction,
            design_class=body.get("design_class"))
    return result


def _choose_favorable_direction(members: list[tuple[dict, dict]]) -> str:
    """The outcome's desirable direction ('lower' / 'higher' / 'neutral'), read from
    the outcome objects (or study rows) — 'lower' by default (adverse outcomes like
    mortality). The first explicit value wins; consistent within a body by design."""
    for study_level, oc in members:
        for src in (oc, study_level):
            fd = src.get("favorable_direction") or src.get("favorable")
            if fd:
                return str(fd).strip().lower()
    return "lower"


def pool_extractions(
    studies: Iterable[dict[str, Any]],
    *,
    measures: Optional[dict[str, str]] = None,
    default_measure: Optional[str] = None,
    include_timepoint: bool = True,
    min_studies: int = 1,
    model: str = "random",
    tau2_method: str = "REML",
) -> list[dict[str, Any]]:
    """End-to-end: absorb many studies' extraction outputs → one pooled result per
    body of evidence. Pure Python; NEVER calls a model.

    ``studies`` is a list of per-study dicts, each carrying study-level fields plus
    an ``outcomes`` list (the shape Table 2 / the extraction agents emit). Groups the
    outcomes into bodies, pools each, and returns the list of body results (skipping
    bodies with fewer than ``min_studies`` poolable studies). ``measures`` maps a
    normalized outcome name → forced measure; ``default_measure`` forces the measure
    for every body without an entry. Different designs and (by default) timepoints
    become separate bodies.
    """
    measures = measures or {}
    bodies = group_into_bodies(studies, include_timepoint=include_timepoint)
    out: list[dict[str, Any]] = []
    for body in bodies:
        forced = measures.get(_norm(body.get("outcome_name"))) or default_measure
        res = pool_body(body, measure=forced, model=model, tau2_method=tau2_method)
        if res["k"] >= min_studies:
            out.append(res)
    return out
