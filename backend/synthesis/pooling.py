"""Pooling (meta-analysis) — the body-of-evidence effect-size engine.

One call = **one outcome**. Given the per-study effect data for a single outcome
(events/totals for binary, mean/SD/N for continuous, or a pre-computed
estimate + CI), this module produces the pooled effect and the three statistics
that feed GRADE:

* pooled relative effect + 95% CI (fixed- and random-effects)  -> Imprecision / Large-effect / Absolute effects
* heterogeneity: Cochran Q, I2, H2, tau2, prediction interval    -> Inconsistency
* small-study / publication-bias tests: Egger + trim-and-fill    -> Publication bias

It is the model-free half of the synthesis stack — the analogue of ``table2.py``.
Nothing here calls a model; effect sizes arrive from the Extraction / Table 2
agents. It uses **numpy** for the vectorized numeric core and **scipy.stats** for
the chi-square / Student-t distribution functions (an agent is free to use more
than the standard library — see requirements.txt). Every value placed on a
returned dict is a plain Python ``float`` so results stay JSON-serializable. A
turnkey **dependency-free** variant (numpy/scipy replaced by hand-rolled
Numerical-Recipes shims) lives in the shareable methodology doc for forks that
cannot add scipy.

Golden rule (see ``Sharable_evidence_synthesis_agents_overview.md``): a pooled
estimate is computed ACROSS a set of studies, never read off one paper. This is a
body-of-evidence agent, not extraction. Do **not** pool RCTs with non-randomized
studies into one estimate — GRADE rates them as separate bodies (call this once
per body).

Full methodology + rationale: ``docs/shareable/pooling_meta_analysis_shareable.md``.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Optional

import numpy as np
from scipy import stats

from .table2 import _order_ci, _to_float, canonicalize_metric

# Two-sided 95% normal quantile (shared with table2.reconcile_stats).
_Z_95 = 1.959964

# Measures pooled on the log scale (ratios + hazard ratios); everything else
# (MD / SMD / RD) is pooled on the raw/identity scale.
_LOG_SCALE_MEASURES = frozenset({"OR", "RR", "IRR", "HR"})
_RAW_SCALE_MEASURES = frozenset({"MD", "SMD", "RD"})
# OR / RR / RD are computed from a 2x2 count table. IRR is NOT — it needs
# person-time denominators (see _irr_effect), so it is routed separately.
_BINARY_MEASURES = frozenset({"OR", "RR", "RD"})
_CONTINUOUS_MEASURES = frozenset({"MD", "SMD"})


# ---------------------------------------------------------------------------
# 0. Distribution functions (scipy.stats — exact, well-tested)
# ---------------------------------------------------------------------------

def _chi2_sf(x: float, df: float) -> float:
    """Upper-tail survival function of a chi-square with ``df`` degrees of freedom."""
    if df <= 0 or x <= 0:
        return 1.0
    return float(stats.chi2.sf(x, df))


def _student_t_sf2(t: float, df: float) -> float:
    """Two-sided Student-t p-value P(|T| > |t|)."""
    if df <= 0:
        return float("nan")
    return float(2.0 * stats.t.sf(abs(t), df))


def _t_quantile(p: float, df: float) -> float:
    """Upper-tail Student-t quantile (inverse CDF) with ``df`` degrees of freedom."""
    if df <= 0:
        return float("nan")
    return float(stats.t.ppf(p, df))


# ---------------------------------------------------------------------------
# 1. Per-study effect size:  raw arm data / pre-computed  ->  (yi, vi)
# ---------------------------------------------------------------------------

def study_effect(study: dict[str, Any], measure: str) -> Optional[dict[str, Any]]:
    """Reduce one study to an effect size ``(yi, vi)`` on the pooling scale.

    Accepts three input shapes, in priority order:

    1. **Pre-computed** ``yi`` + ``vi`` (already on the analysis scale) — passed
       straight through. Also accepts ``estimate`` + (``se`` **or** ``ci_lower`` +
       ``ci_upper``) on the natural scale, which we back-solve to ``(yi, vi)``
       (log-transforming for ratio/HR measures). This is the path for hazard
       ratios and any study reporting only a headline effect + CI.
    2. **Binary 2x2** ``events_int / n_int / events_ctrl / n_ctrl`` for
       OR / RR / IRR / RD.
    3. **Continuous** ``mean_int / sd_int / n_int / mean_ctrl / sd_ctrl / n_ctrl``
       for MD / SMD.

    Returns a dict ``{yi, vi, ...arm counts..., note}`` or ``None`` if the study
    lacks usable data (dropped from k, with a warning surfaced by ``pool_outcome``).
    ``yi`` is the intervention-vs-comparator contrast: for ratios/HR it is the log
    effect, for differences the raw difference. A zero cell triggers the standard
    +0.5 continuity correction (RR/OR/IRR); double-zero studies are dropped.
    """
    m = (measure or "").upper()
    is_log = m in _LOG_SCALE_MEASURES

    # --- Path 1: pre-computed effect ---------------------------------------
    yi = _num(study.get("yi"))
    vi = _num(study.get("vi"))
    if yi is not None and vi is not None and vi > 0:
        out = _base_arm_counts(study)
        out.update({"yi": yi, "vi": vi, "note": study.get("note")})
        return out

    est = _num(study.get("estimate"))
    if est is not None:
        se = _num(study.get("se"))
        lo, hi = _order_ci(_num(study.get("ci_lower")), _num(study.get("ci_upper")))
        if is_log:
            if est <= 0:
                return None
            t_est = math.log(est)
            if se is None and lo is not None and hi is not None and lo > 0 and hi > 0:
                se = (math.log(hi) - math.log(lo)) / (2.0 * _Z_95)
        else:
            t_est = est
            if se is None and lo is not None and hi is not None:
                se = (hi - lo) / (2.0 * _Z_95)
        if se is not None and se > 0:
            out = _base_arm_counts(study)
            out.update({"yi": t_est, "vi": se * se, "note": study.get("note")})
            return out
        return None

    # --- Path 2: binary 2x2 -------------------------------------------------
    if m in _BINARY_MEASURES:
        return _binary_effect(study, m)

    # --- Path 2b: incidence rate ratio (needs person-time, not a 2x2) -------
    if m == "IRR":
        return _irr_effect(study)

    # --- Path 3: continuous -------------------------------------------------
    if m in _CONTINUOUS_MEASURES:
        return _continuous_effect(study, m)

    return None


def _binary_effect(study: dict[str, Any], m: str) -> Optional[dict[str, Any]]:
    a = _num(study.get("events_int"))
    n1 = _num(study.get("n_int"))
    c = _num(study.get("events_ctrl"))
    n2 = _num(study.get("n_ctrl"))
    if None in (a, n1, c, n2) or n1 <= 0 or n2 <= 0:
        return None
    if a < 0 or c < 0 or a > n1 or c > n2:
        return None

    note = None
    b = n1 - a
    d = n2 - c

    if m == "RD":                          # risk difference — no correction needed
        p1, p2 = a / n1, c / n2
        yi = p1 - p2
        vi = p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2
        if vi <= 0:
            vi = 1.0 / (n1 + n2)          # guard the all-same-risk degenerate case
        return _with_counts(study, yi, vi, note)

    # Ratio measures: continuity-correct any zero cell; drop double-zero events.
    if m == "RR" and a == 0 and c == 0:
        return None
    if m == "OR" and ((a == 0 and c == 0) or (b == 0 and d == 0)):
        return None
    if 0 in (a, b, c, d):
        a, b, c, d = a + 0.5, b + 0.5, c + 0.5, d + 0.5
        n1c, n2c = a + b, c + d
        note = "continuity_correction_0.5"
    else:
        n1c, n2c = n1, n2

    if m == "RR":
        yi = math.log((a / n1c) / (c / n2c))
        vi = 1.0 / a - 1.0 / n1c + 1.0 / c - 1.0 / n2c
    else:                                  # OR
        yi = math.log((a * d) / (b * c))
        vi = 1.0 / a + 1.0 / b + 1.0 / c + 1.0 / d
    if vi <= 0:
        return None
    return _with_counts(study, yi, vi, note)


def _irr_effect(study: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Incidence rate ratio from events + PERSON-TIME (not a 2x2 count table).

    IRR = (events_int / time_int) / (events_ctrl / time_ctrl); var(log IRR) =
    1/events_int + 1/events_ctrl. Person-time is read from ``time_int``/``time_ctrl``
    (aliases ``pyears_int``/``pyears_ctrl``). Returns None when person-time is absent
    — an IRR must NOT be approximated from sample-size counts (that silently computes
    a risk ratio and ignores differential follow-up). Papers reporting IRR + CI go
    through the pre-computed path instead. Zero events get the +0.5 correction;
    double-zero events are dropped.
    """
    a = _num(study.get("events_int"))
    c = _num(study.get("events_ctrl"))
    t1 = _num(study.get("time_int")) if study.get("time_int") is not None else _num(study.get("pyears_int"))
    t2 = _num(study.get("time_ctrl")) if study.get("time_ctrl") is not None else _num(study.get("pyears_ctrl"))
    if None in (a, c, t1, t2) or t1 <= 0 or t2 <= 0 or a < 0 or c < 0:
        return None
    if a == 0 and c == 0:
        return None
    note = None
    if a == 0 or c == 0:
        a, c = a + 0.5, c + 0.5
        note = "continuity_correction_0.5"
    yi = math.log((a / t1) / (c / t2))
    vi = 1.0 / a + 1.0 / c
    if vi <= 0:
        return None
    return _with_counts(study, yi, vi, note)


def _continuous_effect(study: dict[str, Any], m: str) -> Optional[dict[str, Any]]:
    m1 = _num(study.get("mean_int"))
    s1 = _num(study.get("sd_int"))
    n1 = _num(study.get("n_int"))
    m2 = _num(study.get("mean_ctrl"))
    s2 = _num(study.get("sd_ctrl"))
    n2 = _num(study.get("n_ctrl"))
    if None in (m1, s1, n1, m2, s2, n2) or n1 <= 1 or n2 <= 1 or s1 < 0 or s2 < 0:
        return None

    if m == "MD":
        yi = m1 - m2
        vi = s1 * s1 / n1 + s2 * s2 / n2
        if vi <= 0:
            return None
        return _with_counts(study, yi, vi, None)

    # SMD — Hedges' g (small-sample-corrected Cohen's d).
    dfree = n1 + n2 - 2
    sp2 = ((n1 - 1) * s1 * s1 + (n2 - 1) * s2 * s2) / dfree
    if sp2 <= 0:
        return None
    d = (m1 - m2) / math.sqrt(sp2)
    j = 1.0 - 3.0 / (4.0 * (n1 + n2) - 9.0)          # Hedges bias correction
    g = j * d
    var_d = (n1 + n2) / (n1 * n2) + d * d / (2.0 * dfree)
    vi = j * j * var_d
    if vi <= 0:
        return None
    return _with_counts(study, g, vi, "hedges_g")


def _with_counts(study: dict[str, Any], yi: float, vi: float, note: Optional[str]) -> dict[str, Any]:
    out = _base_arm_counts(study)
    out.update({"yi": yi, "vi": vi, "note": note})
    return out


def _base_arm_counts(study: dict[str, Any]) -> dict[str, Any]:
    return {
        "study_id": study.get("study_id") or study.get("id") or study.get("name"),
        "design": study.get("design") or study.get("study_type"),
        "n_int": _num(study.get("n_int")),
        "n_ctrl": _num(study.get("n_ctrl")),
        "events_int": _num(study.get("events_int")),
        "events_ctrl": _num(study.get("events_ctrl")),
    }


# ---------------------------------------------------------------------------
# 2. Inverse-variance pooling (fixed + random) and tau2 estimators
# ---------------------------------------------------------------------------

def _iv_pool(y: np.ndarray, v: np.ndarray, tau2: float = 0.0) -> dict[str, Any]:
    """Inverse-variance pool with total variance (v + tau2). tau2=0 -> fixed effect.

    Returns plain-``float`` scalars plus the weight vector as a numpy array (used
    internally for per-study percentages; converted to float at the result boundary).
    """
    w = 1.0 / (v + tau2)
    sw = float(w.sum())
    est = float((w * y).sum() / sw)
    var = 1.0 / sw
    return {"estimate": est, "var": var, "se": math.sqrt(var), "weights": w, "sum_w": sw}


def _fixed_effect(yi, vi) -> dict[str, Any]:
    """Inverse-variance fixed-effect (common-effect) pool."""
    return _iv_pool(np.asarray(yi, dtype=float), np.asarray(vi, dtype=float), 0.0)


def _random_effect(yi, vi, tau2: float) -> dict[str, Any]:
    """Inverse-variance random-effects pool with a fixed tau2."""
    return _iv_pool(np.asarray(yi, dtype=float), np.asarray(vi, dtype=float), float(tau2))


def _cochran_q(yi, vi, fe_est: float) -> float:
    y = np.asarray(yi, dtype=float)
    v = np.asarray(vi, dtype=float)
    return float(((1.0 / v) * (y - fe_est) ** 2).sum())


def _tau2_dersimonian_laird(yi, vi) -> float:
    """Closed-form DerSimonian-Laird moment estimator of tau2 (clamped at 0)."""
    y = np.asarray(yi, dtype=float)
    v = np.asarray(vi, dtype=float)
    k = y.size
    if k < 2:
        return 0.0
    w = 1.0 / v
    sw = float(w.sum())
    fe_est = float((w * y).sum() / sw)
    q = float((w * (y - fe_est) ** 2).sum())
    denom = sw - float((w * w).sum()) / sw
    if denom <= 0:
        return 0.0
    return max(0.0, (q - (k - 1)) / denom)


def _tau2_reml(yi, vi, *, max_iter: int = 200, tol: float = 1e-7) -> float:
    """REML tau2 via the standard fixed-point iteration, seeded from DL.

    w_i = 1/(v_i + tau2); mu = weighted mean;
    tau2_new = [ sum w_i^2 ((y_i - mu)^2 - v_i) ] / sum w_i^2  +  1/sum w_i
    (the ML update plus the REML correction term for estimating mu). Clamped at 0;
    falls back to DL if it fails to converge.
    """
    y = np.asarray(yi, dtype=float)
    v = np.asarray(vi, dtype=float)
    if y.size < 2:
        return 0.0
    tau2 = _tau2_dersimonian_laird(y, v)
    for _ in range(max_iter):
        w = 1.0 / (v + tau2)
        sw = float(w.sum())
        mu = float((w * y).sum() / sw)
        sw2 = float((w * w).sum())
        num = float((w * w * ((y - mu) ** 2 - v)).sum())
        tau2_new = max(0.0, num / sw2 + 1.0 / sw)
        if abs(tau2_new - tau2) < tol:
            return tau2_new
        tau2 = tau2_new
    return _tau2_dersimonian_laird(y, v)


def _tau2_paule_mandel(yi, vi, *, max_iter: int = 200, tol: float = 1e-7) -> float:
    """Paule-Mandel (empirical-Bayes) tau2 — solves sum w_i (y_i-mu)^2 = k-1 by bisection."""
    y = np.asarray(yi, dtype=float)
    v = np.asarray(vi, dtype=float)
    k = y.size
    if k < 2:
        return 0.0

    def gstat(tau2: float) -> float:
        w = 1.0 / (v + tau2)
        mu = float((w * y).sum() / w.sum())
        return float((w * (y - mu) ** 2).sum()) - (k - 1)

    if gstat(0.0) <= 0:
        return 0.0
    lo, hi = 0.0, float(v.max()) + 1.0
    while gstat(hi) > 0 and hi < 1e12:
        hi *= 2.0
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        g = gstat(mid)
        if abs(g) < tol:
            return mid
        if g > 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


_TAU2_ESTIMATORS = {
    "DL": _tau2_dersimonian_laird,
    "REML": _tau2_reml,
    "PM": _tau2_paule_mandel,
}


# ---------------------------------------------------------------------------
# 3. Heterogeneity summary
# ---------------------------------------------------------------------------

def _heterogeneity(yi: list[float], vi: list[float], tau2: float, tau2_method: str,
                   re_est: float, re_var: float) -> dict[str, Any]:
    k = len(yi)
    df = k - 1
    fe = _fixed_effect(yi, vi)
    q = _cochran_q(yi, vi, fe["estimate"])
    het: dict[str, Any] = {
        "k": k,
        "q": q,
        "df": df,
        "q_p": _chi2_sf(q, df) if df > 0 else None,
        "i2": max(0.0, (q - df) / q) * 100.0 if (df > 0 and q > 0) else 0.0,
        "h2": (q / df) if df > 0 else None,
        "tau2": tau2,
        "tau": math.sqrt(tau2),
        "tau2_method": tau2_method,
        "tau2_dl": _tau2_dersimonian_laird(yi, vi),
        "prediction_interval": None,
    }
    # Prediction interval (Higgins-Thompson): needs >= 3 studies + a Student-t tail.
    if k >= 3:
        t = _t_quantile(0.975, k - 2)
        spread = math.sqrt(tau2 + re_var)
        het["prediction_interval"] = {
            "lower": re_est - t * spread,
            "upper": re_est + t * spread,
            "t_df": k - 2,
        }
    return het


# ---------------------------------------------------------------------------
# 4. Small-study effects: Egger's regression + Duval-Tweedie trim-and-fill
# ---------------------------------------------------------------------------

def eggers_test(yi: list[float], vi: list[float]) -> Optional[dict[str, Any]]:
    """Egger's linear regression test for funnel-plot asymmetry.

    Regresses the standard normal deviate ``y_i / s_i`` on precision ``1 / s_i``
    (unweighted OLS); the intercept measures asymmetry and is tested against 0 with
    a Student-t (df = k - 2). Returns None for k < 3. Underpowered below k = 10 —
    the ``adequate_power`` flag reflects GRADE's k >= 10 gate.
    """
    y_arr = np.asarray(yi, dtype=float)
    v_arr = np.asarray(vi, dtype=float)
    k = y_arr.size
    if k < 3:
        return None
    s = np.sqrt(v_arr)
    x = 1.0 / s                             # precision
    y = y_arr / s                           # standard normal deviate
    n = float(k)
    mx = float(x.mean())
    my = float(y.mean())
    sxx = float(((x - mx) ** 2).sum())
    sxy = float(((x - mx) * (y - my)).sum())
    if sxx <= 0:
        return None
    slope = sxy / sxx
    intercept = my - slope * mx
    resid = y - (intercept + slope * x)
    sigma2 = float((resid * resid).sum()) / (k - 2)
    se_intercept = math.sqrt(sigma2 * (1.0 / n + mx * mx / sxx))
    if se_intercept <= 0:
        return None
    t = intercept / se_intercept
    return {
        "intercept": intercept,
        "se": se_intercept,
        "t": t,
        "df": k - 2,
        "p": _student_t_sf2(t, k - 2),
        "k": k,
        "adequate_power": k >= 10,
        "slope": slope,
    }


def trim_and_fill(yi: list[float], vi: list[float], *, tau2_method: str = "DL",
                  max_iter: int = 100) -> Optional[dict[str, Any]]:
    """Duval-Tweedie trim-and-fill (L0 estimator) for publication-bias adjustment.

    Detects the deficient funnel side, estimates the number of suppressed studies
    ``L0``, mirrors them about the trimmed pooled effect, and re-pools with the
    imputed studies. Returns the number imputed + the adjusted random-effects
    estimate/CI (back-transformation is left to the caller). None for k < 3.
    """
    k = len(yi)
    if k < 3:
        return None
    pairs = sorted(zip(yi, vi), key=lambda p: p[0])
    ys = [p[0] for p in pairs]
    vs = [p[1] for p in pairs]

    def pooled_mean(yv: list[float], vv: list[float]) -> float:
        tau2 = _TAU2_ESTIMATORS.get(tau2_method, _tau2_dersimonian_laird)(yv, vv)
        return _random_effect(yv, vv, tau2)["estimate"]

    l0 = 0
    n = k
    cur_y, cur_v = list(ys), list(vs)
    for _ in range(max_iter):
        mu = pooled_mean(cur_y, cur_v)
        centered = [y - mu for y in cur_y]
        ranks = _signed_ranks([abs(c) for c in centered])
        # Right side (positive residual) is the candidate over-represented tail;
        # orient so the estimator measures suppression on the left.
        tn_right = sum(r for c, r in zip(centered, ranks) if c > 0)
        tn_left = sum(r for c, r in zip(centered, ranks) if c < 0)
        if tn_right >= tn_left:
            side, tn = "left", tn_right     # missing studies are on the LEFT
        else:
            side, tn = "right", tn_left     # missing on the RIGHT
        nn = len(cur_y)
        l0_new = int(round((4.0 * tn - nn * (nn + 1)) / (2.0 * nn - 1.0)))
        l0_new = max(0, min(l0_new, nn - 1))
        if l0_new == l0:
            break
        l0 = l0_new
        # Trim the l0 most extreme studies on the OVER-represented side.
        if side == "left":                  # over-represented tail is the right
            trimmed = sorted(range(n), key=lambda i: ys[i])[: n - l0]
        else:
            trimmed = sorted(range(n), key=lambda i: ys[i])[l0:]
        cur_y = [ys[i] for i in trimmed]
        cur_v = [vs[i] for i in trimmed]
        if len(cur_y) < 2:
            break

    if l0 <= 0:
        tau2 = _TAU2_ESTIMATORS.get(tau2_method, _tau2_dersimonian_laird)(ys, vs)
        re = _random_effect(ys, vs, tau2)
        return {"side": None, "n_imputed": 0,
                "estimate": re["estimate"], "se": re["se"],
                "ci_lower": re["estimate"] - _Z_95 * re["se"],
                "ci_upper": re["estimate"] + _Z_95 * re["se"]}

    mu = pooled_mean(cur_y, cur_v)
    # Mirror the l0 most extreme studies of the over-represented side about mu.
    if side == "left":                      # over-represented on the right -> reflect the top l0
        extreme_idx = sorted(range(n), key=lambda i: ys[i])[n - l0:]
    else:
        extreme_idx = sorted(range(n), key=lambda i: ys[i])[:l0]
    fill_y = [2.0 * mu - ys[i] for i in extreme_idx]
    fill_v = [vs[i] for i in extreme_idx]
    aug_y = ys + fill_y
    aug_v = vs + fill_v
    tau2 = _TAU2_ESTIMATORS.get(tau2_method, _tau2_dersimonian_laird)(aug_y, aug_v)
    re = _random_effect(aug_y, aug_v, tau2)
    return {
        "side": side,
        "n_imputed": l0,
        "estimate": re["estimate"],
        "se": re["se"],
        "ci_lower": re["estimate"] - _Z_95 * re["se"],
        "ci_upper": re["estimate"] + _Z_95 * re["se"],
    }


def _signed_ranks(absvals: list[float]) -> list[float]:
    """Average (tie-corrected) ranks of the absolute values, 1-based."""
    order = sorted(range(len(absvals)), key=lambda i: absvals[i])
    ranks = [0.0] * len(absvals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and absvals[order[j + 1]] == absvals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0            # average of 1-based positions i+1..j+1
        for t in range(i, j + 1):
            ranks[order[t]] = avg
        i = j + 1
    return ranks


# ---------------------------------------------------------------------------
# 5. Top-level composer:  studies + measure  ->  full pooled result
# ---------------------------------------------------------------------------

def pool_outcome(
    studies: Iterable[dict[str, Any]],
    measure: str,
    *,
    model: str = "random",
    tau2_method: str = "REML",
    outcome_name: Optional[str] = None,
    favorable_direction: str = "lower",
) -> dict[str, Any]:
    """Pool one outcome across studies. Pure Python — NEVER calls a model.

    ``studies`` is an iterable of per-study dicts accepted by :func:`study_effect`
    (raw binary, raw continuous, or pre-computed estimate + CI). ``measure`` is one
    of OR / RR / IRR / HR / MD / SMD / RD. Returns a single result dict carrying the
    fixed- and random-effects pooled estimates (back-transformed to the natural
    scale for ratio/HR measures), per-study weights, the heterogeneity summary, and
    the small-study / publication-bias tests — i.e. everything the GRADE agent
    consumes. Studies without usable data are dropped from ``k`` and named in
    ``warnings``.
    """
    canon, _family = canonicalize_metric(measure)
    m = (canon or str(measure)).upper()
    is_log = m in _LOG_SCALE_MEASURES

    prepared: list[dict[str, Any]] = []
    warnings: list[str] = []
    for idx, s in enumerate(studies):
        eff = study_effect(s, m)
        label = s.get("study_id") or s.get("id") or s.get("name") or f"study[{idx}]"
        if eff is None or not math.isfinite(eff["yi"]) or not (eff["vi"] > 0):
            hint = ("; IRR needs person-time (time_int/time_ctrl) or a pre-computed "
                    "estimate+CI — it is not computed from 2x2 counts" if m == "IRR" else "")
            warnings.append(f"dropped (no usable {m} data){hint}: {label}")
            continue
        eff["study_id"] = eff.get("study_id") or label
        if eff.get("note") == "continuity_correction_0.5":
            warnings.append(f"continuity correction (+0.5) applied: {eff['study_id']}")
        prepared.append(eff)

    result: dict[str, Any] = {
        "measure": m,
        "scale": "log" if is_log else "raw",
        "model": model,
        "outcome_name": outcome_name,
        "favorable_direction": favorable_direction,
        "k": len(prepared),
        "warnings": warnings,
        "studies": [],
        "fixed": None,
        "random": None,
        "pooled": None,
        "heterogeneity": None,
        "publication_bias": None,
        "totals": _totals(prepared),
    }
    if not prepared:
        result["warnings"].append("no poolable studies — nothing to pool")
        return result

    yi = [e["yi"] for e in prepared]
    vi = [e["vi"] for e in prepared]

    fe = _fixed_effect(yi, vi)
    tau2_fn = _TAU2_ESTIMATORS.get((tau2_method or "REML").upper(), _tau2_reml)
    tau2 = tau2_fn(yi, vi) if len(yi) >= 2 else 0.0
    re = _random_effect(yi, vi, tau2)

    result["fixed"] = _effect_block(fe["estimate"], fe["se"], is_log)
    result["random"] = _effect_block(re["estimate"], re["se"], is_log)
    result["random"]["tau2"] = tau2
    chosen = re if model == "random" else fe
    result["pooled"] = _effect_block(chosen["estimate"], chosen["se"], is_log)

    # Per-study weights from the chosen model.
    sw = chosen["sum_w"]
    for e, w in zip(prepared, chosen["weights"]):
        result["studies"].append({
            "study_id": e["study_id"],
            "design": e.get("design"),
            "yi": e["yi"],
            "vi": e["vi"],
            "se": math.sqrt(e["vi"]),
            "estimate": math.exp(e["yi"]) if is_log else e["yi"],
            "ci_lower": (math.exp(e["yi"] - _Z_95 * math.sqrt(e["vi"])) if is_log
                         else e["yi"] - _Z_95 * math.sqrt(e["vi"])),
            "ci_upper": (math.exp(e["yi"] + _Z_95 * math.sqrt(e["vi"])) if is_log
                         else e["yi"] + _Z_95 * math.sqrt(e["vi"])),
            "weight_pct": float(100.0 * w / sw),
            "note": e.get("note"),
        })

    result["heterogeneity"] = _heterogeneity(
        yi, vi, tau2, (tau2_method or "REML").upper(), re["estimate"], re["var"])
    # Back-transform the prediction interval to the natural scale for ratio/HR
    # measures, so it reads on the same scale as the pooled estimate. tau2/H2 stay
    # on the analysis (log) scale — that is where between-study variance lives.
    pi = result["heterogeneity"].get("prediction_interval")
    if is_log and pi is not None:
        pi["lower"] = math.exp(pi["lower"])
        pi["upper"] = math.exp(pi["upper"])

    egger = eggers_test(yi, vi)
    tf = trim_and_fill(yi, vi, tau2_method=(tau2_method or "REML").upper())
    if tf is not None:
        tf = dict(tf)
        tf["adjusted_estimate"] = math.exp(tf["estimate"]) if is_log else tf["estimate"]
        tf["adjusted_ci_lower"] = math.exp(tf["ci_lower"]) if is_log else tf["ci_lower"]
        tf["adjusted_ci_upper"] = math.exp(tf["ci_upper"]) if is_log else tf["ci_upper"]
    result["publication_bias"] = {"egger": egger, "trim_fill": tf}
    return result


def _effect_block(t_est: float, se: float, is_log: bool) -> dict[str, Any]:
    """Build a natural-scale effect block from an analysis-scale estimate + SE."""
    lo = t_est - _Z_95 * se
    hi = t_est + _Z_95 * se
    z = t_est / se if se > 0 else float("nan")
    p = float(2.0 * stats.norm.sf(abs(z))) if math.isfinite(z) else None
    block = {
        "yi": t_est,                        # analysis scale (log for ratios)
        "se": se,
        "z": z,
        "p": min(max(p, 1e-300), 1.0) if p is not None else None,
        "estimate": math.exp(t_est) if is_log else t_est,
        "ci_lower": math.exp(lo) if is_log else lo,
        "ci_upper": math.exp(hi) if is_log else hi,
    }
    return block


def _totals(prepared: list[dict[str, Any]]) -> dict[str, Optional[float]]:
    def _sum(key: str) -> Optional[float]:
        vals = [e[key] for e in prepared if e.get(key) is not None]
        return sum(vals) if vals else None
    return {
        "n_int": _sum("n_int"),
        "n_ctrl": _sum("n_ctrl"),
        "events_int": _sum("events_int"),
        "events_ctrl": _sum("events_ctrl"),
    }


# ---------------------------------------------------------------------------
# 6. GRADE hand-off — the raw numbers the GRADE agent reads (no decisions here)
# ---------------------------------------------------------------------------

def grade_pooling_inputs(result: dict[str, Any]) -> dict[str, Any]:
    """Extract the pooled statistics the GRADE agent consumes — numbers, not verdicts.

    GRADE makes the up/down-grade calls itself; this only surfaces the inputs from a
    :func:`pool_outcome` result so the two agents share one contract. Absolute
    effects + certainty are computed downstream (they also need baseline risk + MIDs).
    """
    pooled = result.get("pooled") or {}
    het = result.get("heterogeneity") or {}
    pb = result.get("publication_bias") or {}
    egger = pb.get("egger") or {}
    tf = pb.get("trim_fill") or {}
    return {
        "k": result.get("k"),
        "measure": result.get("measure"),
        "pooled_estimate": pooled.get("estimate"),
        "ci_lower": pooled.get("ci_lower"),
        "ci_upper": pooled.get("ci_upper"),
        # Inconsistency
        "i2": het.get("i2"),
        "tau2": het.get("tau2"),
        "q_p": het.get("q_p"),
        "prediction_interval": het.get("prediction_interval"),
        # Imprecision / large-effect / absolute effects
        "total_n": (result.get("totals") or {}).get("n_int"),
        "events_int": (result.get("totals") or {}).get("events_int"),
        "events_ctrl": (result.get("totals") or {}).get("events_ctrl"),
        # Publication bias
        "egger_p": egger.get("p"),
        "egger_adequate_power": egger.get("adequate_power"),
        "trim_fill_n_imputed": tf.get("n_imputed"),
        "trim_fill_adjusted_estimate": tf.get("adjusted_estimate"),
    }


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    return _to_float(v)
