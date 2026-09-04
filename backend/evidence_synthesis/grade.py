"""GRADE — the body-of-evidence certainty engine.

One call = **one body of evidence** (one outcome × comparison × timepoint ×
design-class). Given a pooled result from :func:`pooling.pool_outcome` (or the
``pooled`` block a :func:`pooling_prep.pool_body` returns), the per-study
risk-of-bias labels, and a few human judgments, this module produces:

* the **GRADE certainty** rating — starts by design (RCT=High / NRS=Low /
  single-arm=Very low), rates DOWN across the five core domains (risk of bias,
  inconsistency, indirectness, imprecision, publication bias) for any design, and
  — for non-randomized evidence with no rate-down factors — rates UP for large
  effect, dose-response gradient, and plausible opposing confounding; then clamps
  to [Very low, High].
* the anticipated **absolute effects** per 1000 (assumed risk → risk with
  intervention → risk difference → NNT) for the Summary-of-Findings row (ASCO T5).

It is the *decision* half of the synthesis stack — the analogue of the pooling
engine's *math* half. It makes the up/down-grade calls that pooling deliberately
does not. **Pure Python, stdlib only** (no numpy/scipy): it consumes the plain
floats the pooling engine emits, so it stays importable without the LLM / RoB-tool
dependency chain (fast unit tests). Every returned value is JSON-serializable.

Golden rule (see ``Sharable_evidence_synthesis_agents_overview.md``): a GRADE
rating is computed ACROSS a body of studies, never read off one paper. Do **not**
rate RCTs and non-randomized studies as one body — the pooling ``design_class``
already separates them; grade each body on its own.

Contract with pooling: this reads a ``pool_outcome`` result dict — ``measure``,
``k``, ``pooled`` (natural-scale ``estimate`` / ``ci_lower`` / ``ci_upper`` +
analysis-scale ``yi``), ``heterogeneity`` (``i2`` / ``q_p`` / ``tau2`` /
``prediction_interval``), ``publication_bias`` (``egger`` / ``trim_fill``),
``studies[]`` (per-study ``study_id`` / ``design`` / ``weight_pct``), and
``totals``. It never re-computes the meta-analysis.

Full methodology + rationale: ``docs/shareable/grade_certainty_shareable.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

# The four GRADE certainty levels, high→low (index 0..3).
GRADE_LEVELS = ["High", "Moderate", "Low", "Very low"]

# Per-study RoB label -> a 0..2 severity, mirroring
# quality_appraisal._rob_downgrade. Labels are lower-cased before lookup; the
# Domain-1 "Low (except for concerns about uncontrolled confounding/benchmarking)"
# ROBINS-I variants normalize to Low here.
_ROB_SEVERITY = {
    "low": 0,
    "low (except for concerns about uncontrolled confounding)": 0,
    "low (except for concerns about uncontrolled benchmarking)": 0,
    "some concerns": 1,
    "moderate": 1,
    "high": 2,
    "serious": 2,
    "critical": 2,
    "no information": 1,
    "insufficient information": 1,
    "unclear": 1,
}

# Ratio / hazard measures live on the log scale internally; their natural-scale
# null is 1.0. Difference measures (MD / SMD / RD) have a null of 0.0.
_RATIO_MEASURES = frozenset({"OR", "RR", "IRR", "HR"})
_BINARY_MEASURES = frozenset({"OR", "RR", "RD", "IRR"})
# Absolute effects are only defined for binary measures with a baseline risk.
_ABSOLUTE_MEASURES = frozenset({"RR", "OR", "RD", "IRR"})


@dataclass
class GradeConfig:
    """Tunable operating points for the GRADE certainty engine.

    Defaults follow core GRADE (GRADE Handbook, Schünemann 2013; JCE GRADE
    guidelines 1–8 and 11). Exposed as a dataclass so a methodologist / the UI can
    adjust thresholds without editing the engine.
    """
    # Imprecision — Optimal Information Size rules of thumb.
    ois_binary_events: int = 300        # binary: total N/events < 300 -> OIS unmet
    ois_total_n: int = 400              # continuous: < 400 participants -> OIS unmet
    # Inconsistency — I^2 / Cochran-Q.
    i2_serious: float = 50.0            # I^2 > 50% (with Q p<threshold) -> -1
    i2_very_serious: float = 75.0       # I^2 > 75% -> considerable
    q_p_threshold: float = 0.10
    # Risk of bias — share of pooled weight in studies with concerns.
    rob_high_weight_2: float = 0.50     # >=50% weight at high/serious -> -2
    rob_high_weight_1: float = 0.25     # >=25% high OR >=50% some -> -1
    rob_some_weight_1: float = 0.50
    # Publication bias — only meaningful with enough studies.
    pubbias_min_studies: int = 10       # core GRADE: do not assess below ~10 studies
    egger_p: float = 0.10
    trimfill_min_imputed: int = 2
    # Upgrade (non-randomized / observational evidence only).
    large_effect_1: float = 2.0         # RR/OR >=2 or <=0.5 -> +1
    large_effect_2: float = 5.0         # RR/OR >=5 or <=0.2 -> +2
    require_ci_for_large_effect: bool = True   # CI must also exclude the null
    upgrade_requires_no_downgrade: bool = True # rate up only absent rate-down factors


_DEFAULT_GRADE_CONFIG = GradeConfig()


def _grade_index(level: str) -> int:
    try:
        return GRADE_LEVELS.index(level)
    except ValueError:
        return 0


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


# ---------------------------------------------------------------------------
# Downgrade domains (all designs)
# ---------------------------------------------------------------------------

def _rob_across_studies(per_study_rob: Sequence[str], weights: Optional[Sequence[float]],
                        cfg: GradeConfig = _DEFAULT_GRADE_CONFIG) -> tuple[int, str]:
    """Weighted risk-of-bias-across-studies downgrade (0/1/2).

    Weights the per-study RoB severities by pooled weight (falls back to equal
    weight). GRADE g4: do not average — most of the weight sitting in high/serious
    studies drives the downgrade.

    Studies with no label are **dropped** and the weights renormalized over the
    rest, matching ``synthesis_stats._rob_across_studies``. Scoring them "some
    concerns" asserts a finding about a study nobody appraised and — because
    unappraised studies are common — pushes ``frac_some`` past its threshold on its
    own; scoring them "low" inflates certainty. A body where *nothing* is appraised
    is caught earlier by ``require_rob``.
    """
    labelled = [(i, r) for i, r in enumerate(per_study_rob) if (r or "").strip()]
    if not labelled:
        return 0, "no risk-of-bias judgements available"
    sev = [_ROB_SEVERITY.get(r.strip().lower(), 1) for _, r in labelled]
    if weights is not None and len(weights) == len(per_study_rob):
        w = [float(weights[i]) for i, _ in labelled]
    else:
        w = [1.0] * len(sev)
    total = sum(w) or 1.0
    frac_serious = sum(wi for wi, s in zip(w, sev) if s >= 2) / total
    frac_some = sum(wi for wi, s in zip(w, sev) if s >= 1) / total
    n_missing = len(per_study_rob) - len(labelled)
    gap = ("" if not n_missing else
           f"; {n_missing} of {len(per_study_rob)} pooled studies had no assessable "
           "judgement and are excluded from this domain")
    if frac_serious >= cfg.rob_high_weight_2:
        return 2, f"most of the weight ({frac_serious:.0%}) is in studies at high/serious risk of bias{gap}"
    if frac_serious >= cfg.rob_high_weight_1 or frac_some >= cfg.rob_some_weight_1:
        return 1, f"a substantial share of weight ({frac_some:.0%}) is in studies with risk-of-bias concerns{gap}"
    return 0, f"most weight is in low risk-of-bias studies{gap}"


def _inconsistency_downgrade(k: int, i2: Optional[float], q_p: Optional[float],
                             subgroup: Optional[dict],
                             cfg: GradeConfig = _DEFAULT_GRADE_CONFIG) -> tuple[int, str]:
    """Inconsistency from heterogeneity (I² + Cochran-Q p), subgroup-explained aware.

    Single study (k<2) → not assessable. Subgroup differences that explain the
    heterogeneity (Q-between p<0.05) suppress the downgrade (GRADE g7).
    """
    if k < 2:
        return 0, "single study — inconsistency not assessable"
    i2 = i2 or 0.0
    p = 1.0 if q_p is None else q_p
    explained = bool(subgroup and subgroup.get("p_between") is not None
                     and subgroup["p_between"] < 0.05)
    if explained:
        return 0, f"heterogeneity (I²={i2:.0f}%) explained by subgroup differences"
    if i2 > cfg.i2_very_serious and p < cfg.q_p_threshold:
        return 1, f"considerable heterogeneity (I²={i2:.0f}%, p={p:.3f})"
    if i2 > cfg.i2_serious and p < cfg.q_p_threshold:
        return 1, f"substantial unexplained heterogeneity (I²={i2:.0f}%)"
    return 0, f"acceptable consistency (I²={i2:.0f}%)"


def _imprecision_downgrade(measure: str, ci_lower: Optional[float], ci_upper: Optional[float],
                           total_n: Optional[float], mid_benefit: Optional[float],
                           mid_harm: Optional[float], is_binary: bool,
                           cfg: GradeConfig = _DEFAULT_GRADE_CONFIG) -> tuple[int, str]:
    """Imprecision from the pooled 95% CI vs decision thresholds + OIS (g6).

    ``ci_lower`` / ``ci_upper`` are on the **natural (display) scale** — the pooling
    engine already back-transformed ratio measures. Null is 1.0 for ratio/HR
    measures and 0.0 for difference measures. MIDs are on the same display scale.
    """
    null = 1.0 if measure in _RATIO_MEASURES else 0.0
    crosses_null = (ci_lower is not None and ci_upper is not None
                    and ci_lower <= null <= ci_upper)
    ois_cap = cfg.ois_binary_events if is_binary else cfg.ois_total_n
    ois_fail = total_n is not None and total_n < ois_cap
    if (mid_benefit is not None and mid_harm is not None
            and ci_lower is not None and ci_upper is not None):
        lo, hi = min(mid_benefit, mid_harm), max(mid_benefit, mid_harm)
        # CI spans both the benefit and harm thresholds -> very serious.
        if ci_lower <= lo and ci_upper >= hi:
            return 2, "CI spans both the benefit and harm thresholds"
        if crosses_null:
            return 1, "CI crosses the line of no effect"
        if ois_fail:
            return 1, "sample size below the optimal information size"
        return 0, "CI excludes clinically important effects in one direction"
    if crosses_null and ois_fail:
        return 2, "wide CI crossing no effect with sample size below OIS"
    if crosses_null:
        return 1, "CI crosses the line of no effect"
    if ois_fail:
        return 1, "sample size below the optimal information size"
    return 0, "adequately precise"


def _pubbias_downgrade(k: int, egger: Optional[dict], trim_fill: Optional[dict],
                       cfg: GradeConfig = _DEFAULT_GRADE_CONFIG) -> tuple[int, str]:
    """Publication bias from small-study tests (g5). Not assessed below ~10 studies."""
    adequate = None if egger is None else egger.get("adequate_power")
    if adequate is False or (adequate is None and k and k < cfg.pubbias_min_studies):
        return 0, f"not formally assessed (<{cfg.pubbias_min_studies} studies)"
    ep = None if egger is None else _num(egger.get("p"))
    if ep is not None and ep < cfg.egger_p:
        return 1, f"funnel asymmetry (Egger p={ep:.3f})"
    n_imp = 0 if trim_fill is None else int(trim_fill.get("n_imputed") or 0)
    if n_imp >= cfg.trimfill_min_imputed:
        return 1, f"trim-and-fill imputed {n_imp} missing studies"
    return 0, "no strong evidence of publication bias"


# ---------------------------------------------------------------------------
# Upgrade domains — non-randomized / observational evidence only
# ---------------------------------------------------------------------------

def _large_effect_upgrade(measure: str, estimate: Optional[float], ci_lower: Optional[float],
                          ci_upper: Optional[float],
                          cfg: GradeConfig = _DEFAULT_GRADE_CONFIG) -> tuple[int, str]:
    """Large magnitude of effect (ratio measures only) -> 0 / +1 / +2 (g9).

    ``estimate`` / ``ci_lower`` / ``ci_upper`` are natural-scale ratios. Normalises
    to a ratio ≥ 1 and, when ``require_ci_for_large_effect`` is set, requires the CI
    bound nearer the null (1.0) to also exclude no-effect.
    """
    if measure not in _RATIO_MEASURES:
        return 0, "large-effect criterion applies to ratio measures (OR/RR/HR/IRR)"
    if estimate is None or estimate <= 0:
        return 0, "no positive pooled ratio"
    if estimate >= 1.0:
        r, near = estimate, ci_lower
    else:
        r = 1.0 / estimate
        near = (1.0 / ci_upper) if (ci_upper and ci_upper > 0) else None

    def clears(threshold: float) -> bool:
        if not cfg.require_ci_for_large_effect:
            return r >= threshold
        return r >= threshold and near is not None and near >= 1.0

    if clears(cfg.large_effect_2):
        return 2, f"very large effect ({measure}≈{r:.1f}); CI excludes no-effect"
    if clears(cfg.large_effect_1):
        return 1, f"large effect ({measure}≈{r:.1f})"
    return 0, f"effect magnitude below the large-effect threshold ({measure}≈{r:.1f})"


def _dose_response_upgrade(dose_response: Optional[bool], metaregression: Optional[dict],
                           moderator_name: str = "dose") -> tuple[int, str]:
    """Dose-response gradient -> 0 / +1 (g9).

    ``dose_response`` True/False lets the assessor assert directly; otherwise
    auto-detect from a meta-regression on a dose moderator (slope p<0.05).
    """
    if dose_response is not None:
        return (1, "dose-response gradient (assessor judgement)") if dose_response \
            else (0, "no dose-response gradient")
    if not metaregression:
        return 0, "no dose moderator modelled"
    coefs = metaregression.get("coefficients", []) or []
    target = next((c for c in coefs
                   if (c.get("name") or "").lower() in (moderator_name.lower(), "dose", "dose_level")), None)
    if target is None:
        target = next((c for c in coefs if (c.get("name") or "") != "intercept"
                       and _num(c.get("p")) is not None and _num(c.get("p")) < 0.05), None)
    if target and _num(target.get("p")) is not None and _num(target.get("p")) < 0.05:
        return 1, f"significant dose-response gradient ({target.get('name')}, p={_num(target.get('p')):.3f})"
    return 0, "no significant dose-response gradient"


def _opposing_confounding_upgrade(opposing_confounding: bool) -> tuple[int, str]:
    """All plausible residual confounding would reduce the observed effect -> 0 / +1.

    An assessor judgement; there is no automatable signal for it.
    """
    if opposing_confounding:
        return 1, "plausible residual confounding would only attenuate the observed effect"
    return 0, "not applicable"


# ---------------------------------------------------------------------------
# Anticipated absolute effects (Summary-of-Findings / ASCO T5)
# ---------------------------------------------------------------------------

def absolute_effects(measure: str, estimate: Optional[float], ci_lower: Optional[float],
                     ci_upper: Optional[float], baseline_per_1000: Optional[float]) -> Optional[dict]:
    """Anticipated absolute effects per 1000 for a dichotomous outcome (g12).

    ``estimate`` / ``ci_lower`` / ``ci_upper`` are the **natural-scale** pooled
    relative effect + CI (RR/OR/IRR ratios or an absolute RD). ``baseline_per_1000``
    is the assumed comparator (control) risk per 1000. Returns the intervention risk,
    the risk difference (CI propagated from the relative-effect CI), and NNT.
    Returns ``None`` when not computable.
    """
    b = _num(baseline_per_1000)
    if b is None or measure not in _ABSOLUTE_MEASURES:
        return None
    acr = b / 1000.0
    if measure != "RD" and not (0.0 < acr < 1.0):
        return None

    def apply(rel: Optional[float]) -> Optional[float]:
        if rel is None:
            return None
        if measure == "RD":
            return acr + rel                       # RD is already absolute
        if measure in ("RR", "IRR"):
            return acr * rel
        odds = acr / (1.0 - acr) * rel             # OR -> absolute via odds
        return odds / (1.0 + odds)

    est = apply(estimate)
    lo = apply(ci_lower)
    hi = apply(ci_upper)
    if est is None:
        return None
    rd = est - acr
    rd_lo = None if lo is None else lo - acr
    rd_hi = None if hi is None else hi - acr
    return {
        "baseline_per_1000": round(b, 1),
        "intervention_per_1000": round(est * 1000, 1),
        "risk_difference_per_1000": round(rd * 1000, 1),
        "rd_ci_per_1000": [None if rd_lo is None else round(rd_lo * 1000, 1),
                           None if rd_hi is None else round(rd_hi * 1000, 1)],
        "nnt": None if rd == 0 else round(1.0 / abs(rd)),
        "favours": "intervention" if rd < 0 else ("comparator" if rd > 0 else "neither"),
    }


# ---------------------------------------------------------------------------
# Design -> starting certainty
# ---------------------------------------------------------------------------

def _randomized_design(design_class: Optional[str],
                       studies: Optional[Sequence[dict]]) -> bool:
    """Whether the body's *design* is randomized — from ``design_class`` when the
    pooler supplies one, else inferred conservatively from the study design labels.

    Deliberately independent of the starting certainty: GRADE 9 restricts rating
    up to non-randomized evidence *by design*, so an RCT body whose caller pinned
    ``initial="Low"`` must still be barred from the upgrade domains.
    """
    dc = (design_class or "").lower()
    if dc == "rct":
        return True
    if dc == "nrs":
        return False
    designs = " ".join(str((s or {}).get("design") or "") for s in (studies or [])).lower()
    non_random = "non-random" in designs or "nonrandom" in designs
    randomized = ("randomized" in designs or "randomised" in designs or "rct" in designs)
    return randomized and not non_random


def _initial_from_design(design_class: Optional[str], measure: Optional[str],
                         studies: Optional[Sequence[dict]]) -> str:
    """Starting certainty by design (g3): RCT=High, NRS=Low, single-arm=Very low."""
    designs = " ".join(str((s or {}).get("design") or "") for s in (studies or [])).lower()
    if "single-arm" in designs or "single arm" in designs or "dose-escalation" in designs:
        return "Very low"
    return "High" if _randomized_design(design_class, studies) else "Low"


# ---------------------------------------------------------------------------
# Certainty combiner — the single public entry point
# ---------------------------------------------------------------------------

def grade_body(pool_result: dict[str, Any], *,
               initial: Optional[str] = None,
               per_study_rob: Optional[Sequence[str]] = None,
               weights: Optional[Sequence[float]] = None,
               require_rob: bool = True,
               indirectness_levels: Optional[int] = None,
               indirectness_reason: str = "",
               mid_benefit: Optional[float] = None,
               mid_harm: Optional[float] = None,
               baseline_risk_per_1000: Optional[float] = None,
               dose_response: Optional[bool] = None,
               opposing_confounding: bool = False,
               subgroup: Optional[dict] = None,
               metaregression: Optional[dict] = None,
               overrides: Optional[dict] = None,
               cfg: Optional[GradeConfig] = None) -> dict[str, Any]:
    """Rate the certainty of one pooled body of evidence on the GRADE scale.

    ``pool_result`` is a :func:`pooling.pool_outcome` result dict. Reads its
    ``measure`` / ``k`` / ``pooled`` / ``heterogeneity`` / ``publication_bias`` /
    ``studies`` / ``totals`` — never re-computes the meta-analysis. Starts at
    ``initial`` (defaults from the body's design), rates DOWN for the five core
    domains for any design, and — for non-randomized evidence with no rate-down
    factors — rates UP for large effect, dose-response, and opposing confounding.

    **Risk of bias is read off the study records** — each ``studies[]`` entry carries
    its own ``rob`` label next to its ``weight_pct``, because the risk-of-bias domain
    is weight-driven and the pooled order is not the input order (studies without
    usable data are dropped). ``per_study_rob`` remains as an explicit positional
    override, but it must now match ``studies[]`` exactly: a length mismatch raises
    rather than silently falling back to equal weights, which previously produced an
    unweighted judgement indistinguishable from a weighted one. With
    ``require_rob=True`` (the default) a body carrying no labels at all raises, rather
    than being rated as though risk of bias were clean. ``weights`` defaults to the
    per-study ``weight_pct``. ``indirectness_levels`` (0/1/2) is the reviewer's rating — when
    ``None`` it defaults to 0 (the orchestrator supplies an auto-assessed value
    upstream when a target PICO is available). ``overrides`` pins any domain by key
    (e.g. ``{"imprecision": 2}``). Returns the full GRADE record.
    """
    cfg = cfg or _DEFAULT_GRADE_CONFIG
    overrides = overrides or {}

    measure = (pool_result.get("measure") or "").upper()
    k = int(pool_result.get("k") or 0)
    pooled = pool_result.get("pooled") or {}
    het = pool_result.get("heterogeneity") or {}
    pb = pool_result.get("publication_bias") or {}
    studies = pool_result.get("studies") or []
    totals = pool_result.get("totals") or {}

    estimate = _num(pooled.get("estimate"))
    ci_lower = _num(pooled.get("ci_lower"))
    ci_upper = _num(pooled.get("ci_upper"))
    i2 = _num(het.get("i2"))
    q_p = _num(het.get("q_p"))
    egger = pb.get("egger")
    trim_fill = pb.get("trim_fill")

    # Total sample for the OIS check: prefer summed arm N; for binary, events give
    # the tighter GRADE signal but fall back to N when events are absent.
    is_binary = measure in _BINARY_MEASURES
    n_int = _num(totals.get("n_int"))
    n_ctrl = _num(totals.get("n_ctrl"))
    total_n = (n_int or 0.0) + (n_ctrl or 0.0) or None
    if is_binary:
        e_int = _num(totals.get("events_int"))
        e_ctrl = _num(totals.get("events_ctrl"))
        total_events = None if (e_int is None and e_ctrl is None) else (e_int or 0.0) + (e_ctrl or 0.0)
        ois_metric = total_events if total_events is not None else total_n
    else:
        ois_metric = total_n

    if initial is None:
        initial = _initial_from_design(pool_result.get("design_class"), measure, studies)
    # Upgrade eligibility keys on the DESIGN, not on the starting certainty: an RCT
    # body whose caller pinned initial="Low" is still randomized evidence and GRADE 9
    # never rates it up.
    is_randomized = _randomized_design(pool_result.get("design_class"), studies)

    # Risk of bias arrives ON the study records, not as a parallel list: the pooler
    # drops studies without usable data, so the pooled order is not the input order and
    # a positional list silently shifts by one after the first drop.
    if per_study_rob is None:
        per_study_rob = [(s.get("rob") or "") for s in studies]
    else:
        per_study_rob = list(per_study_rob)
        if per_study_rob and studies and len(per_study_rob) != len(studies):
            # Falling back to equal weights here produced an unweighted judgement
            # indistinguishable from a weighted one. Refuse instead.
            raise ValueError("per_study_rob must match the pooled studies exactly")
        if not per_study_rob:
            per_study_rob = [(s.get("rob") or "") for s in studies]

    if not any((r or "").strip() for r in per_study_rob):
        # Scoped to bodies that actually have studies: the guard exists to stop a body
        # of real studies being rated as though bias were clean. With no studies there
        # is nothing to be biased, and this error would misdescribe the real problem.
        if require_rob and studies:
            raise ValueError("no risk-of-bias judgements for this body of evidence")
        per_study_rob = []

    if weights is None:
        weights = [s.get("weight_pct") for s in studies if s.get("weight_pct") is not None]
        if len(weights) != len(per_study_rob):
            weights = None

    # Coverage guard — the same refusal as the no-labels case above, extended to
    # severely incomplete appraisal. Direction-aware: a downgrade computed from
    # the labelled sliver stands whatever the coverage (unlabelled studies could
    # only add concerns), but a *clean* result on under half the pooled weight
    # would rate a body 99% unassessed with one small Low-risk study as though
    # bias were clean — refuse that instead of inflating certainty.
    if require_rob and studies and per_study_rob:
        _lab_idx = [i for i, r in enumerate(per_study_rob) if (r or "").strip()]
        if weights is not None and len(weights) == len(per_study_rob):
            _total_w = sum(float(w or 0.0) for w in weights) or 1.0
            _coverage = sum(float(weights[i] or 0.0) for i in _lab_idx) / _total_w
        else:
            _coverage = len(_lab_idx) / len(per_study_rob)
        if _coverage < 0.5:
            _lv, _ = _rob_across_studies(per_study_rob, weights, cfg)
            if _lv == 0:
                raise ValueError(
                    f"risk-of-bias labels cover only {_coverage:.0%} of the pooled "
                    "weight and show no concerns on the labelled sliver — "
                    "insufficient coverage to rate this body of evidence")

    def _pin(domain_key: str, levels: int, reason: str) -> tuple[int, str]:
        if domain_key in overrides:
            return max(0, int(overrides[domain_key])), (reason + " [overridden]").strip()
        return levels, reason

    # --- downgrades (all designs) ---
    rob_lv, rob_reason = _pin("risk_of_bias", *_rob_across_studies(per_study_rob, weights, cfg))
    inc_lv, inc_reason = _pin("inconsistency", *_inconsistency_downgrade(k, i2, q_p, subgroup, cfg))
    imp_lv, imp_reason = _pin("imprecision", *_imprecision_downgrade(
        measure, ci_lower, ci_upper, ois_metric, _num(mid_benefit), _num(mid_harm), is_binary, cfg))
    pub_lv, pub_reason = _pin("publication_bias", *_pubbias_downgrade(k, egger, trim_fill, cfg))
    ind_input = 0 if indirectness_levels is None else max(0, int(indirectness_levels))
    if indirectness_levels is None and not indirectness_reason:
        # No assessment reached us at all (the orchestrator normally supplies a
        # reviewer rating or the hybrid auto-assessment). 0 downgrade levels —
        # we never invent a finding — but the reason must say "not assessed",
        # never "no serious indirectness" about something nobody assessed.
        ind_default = ("not assessed — no indirectness assessment supplied; "
                       "the rating does not account for this domain")
    else:
        ind_default = ("no serious indirectness" if ind_input == 0
                       else "indirectness concerns")
    ind_lv, ind_reason = _pin("indirectness", ind_input,
                              indirectness_reason or ind_default)

    domains = [
        {"domain": "Risk of bias", "kind": "downgrade", "downgrade": rob_lv, "upgrade": 0, "reason": rob_reason},
        {"domain": "Inconsistency", "kind": "downgrade", "downgrade": inc_lv, "upgrade": 0, "reason": inc_reason},
        {"domain": "Indirectness", "kind": "downgrade", "downgrade": ind_lv, "upgrade": 0, "reason": ind_reason},
        {"domain": "Imprecision", "kind": "downgrade", "downgrade": imp_lv, "upgrade": 0, "reason": imp_reason},
        {"domain": "Publication bias", "kind": "downgrade", "downgrade": pub_lv, "upgrade": 0, "reason": pub_reason},
    ]
    total_down = sum(d["downgrade"] for d in domains)

    # --- upgrades (non-randomized evidence only, and only absent rate-down factors) ---
    total_up = 0
    can_upgrade = (not is_randomized and initial == "Low"
                   and (total_down == 0 or not cfg.upgrade_requires_no_downgrade))
    if can_upgrade:
        le_lv, le_reason = _pin("large_effect", *_large_effect_upgrade(measure, estimate, ci_lower, ci_upper, cfg))
        dr_lv, dr_reason = _pin("dose_response", *_dose_response_upgrade(dose_response, metaregression))
        oc_lv, oc_reason = _pin("opposing_confounding", *_opposing_confounding_upgrade(opposing_confounding))
        for name, lv, reason in (("Large effect", le_lv, le_reason),
                                 ("Dose-response gradient", dr_lv, dr_reason),
                                 ("Opposing plausible confounding", oc_lv, oc_reason)):
            domains.append({"domain": name, "kind": "upgrade", "downgrade": 0, "upgrade": lv, "reason": reason})
            total_up += lv

    start = _grade_index(initial)
    final_idx = max(0, min(len(GRADE_LEVELS) - 1, start + total_down - total_up))
    final = GRADE_LEVELS[final_idx]

    fired = [f"{d['domain'].lower()} (−{d['downgrade']}: {d['reason']})"
             for d in domains if d["downgrade"] > 0]
    raised = [f"{d['domain'].lower()} (+{d['upgrade']}: {d['reason']})"
              for d in domains if d["upgrade"] > 0]
    parts = [f"Initial certainty {initial}"]
    if fired:
        parts.append(f"downgraded {total_down} level(s) for " + "; ".join(fired))
    if raised:
        parts.append(f"upgraded {total_up} level(s) for " + "; ".join(raised))
    if not fired and not raised:
        parts.append("no serious concerns across GRADE domains")
    explanation = ". ".join(parts) + f". Final certainty: {final}."

    return {
        "initial": initial,
        "final": final,
        "total_downgrade": total_down,
        "total_upgrade": total_up,
        "domains": domains,
        "explanation": explanation,
        "absolute_effects": absolute_effects(measure, estimate, ci_lower, ci_upper, baseline_risk_per_1000),
    }


# ---------------------------------------------------------------------------
# Summary-of-Findings row (ASCO T5)
# ---------------------------------------------------------------------------

def sof_row(pool_result: dict[str, Any], grade_result: dict[str, Any],
            *, outcome: Optional[dict] = None) -> dict[str, Any]:
    """Assemble one ASCO Table-5 (GRADE Summary-of-Findings) row.

    Combines the pooled relative effect (natural scale, already back-transformed by
    the pooling engine) with the GRADE certainty + absolute effects. ``outcome`` is
    an optional label block ``{name, timeframe, n_participants}``.
    """
    outcome = outcome or {}
    pooled = pool_result.get("pooled") or {}
    totals = pool_result.get("totals") or {}
    n_int = _num(totals.get("n_int")) or 0.0
    n_ctrl = _num(totals.get("n_ctrl")) or 0.0
    reasons = []
    for d in grade_result.get("domains", []):
        mag = d.get("downgrade", 0) or d.get("upgrade", 0)
        if mag:
            reasons.append({"domain": d["domain"], "direction": d.get("kind"),
                            "levels": mag, "reason": d.get("reason")})
    return {
        "outcome": outcome.get("name") or pool_result.get("outcome_name"),
        "timeframe": outcome.get("timeframe") or pool_result.get("timepoint"),
        "comparison": pool_result.get("comparison"),
        "n_studies": pool_result.get("k"),
        "n_participants": outcome.get("n_participants") or (int(n_int + n_ctrl) or None),
        "n_intervention": int(n_int) or None,
        "n_control": int(n_ctrl) or None,
        "measure": pool_result.get("measure"),
        "relative_effect": {
            "estimate": _num(pooled.get("estimate")),
            "ci_low": _num(pooled.get("ci_lower")),
            "ci_high": _num(pooled.get("ci_upper")),
        },
        "absolute_effects": grade_result.get("absolute_effects"),
        "certainty": grade_result.get("final"),
        "certainty_reasons": reasons,
        "explanation": grade_result.get("explanation"),
    }
