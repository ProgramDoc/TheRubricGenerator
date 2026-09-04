"""Pure-Python meta-analysis statistics engine for the Synthesis feature.

No DB, no LLM, no I/O — just numpy/scipy. Every public function is
``inspect.getsource``-able so the developer view can show the exact maths that
produced a number (mirrors the pure decision trees in ``backend/rob_tools/``).

Scope (v1):
  * Effect sizes — continuous (MD, SMD/Hedges' g), binary (OR, RR, RD),
    correlation (Fisher z), single-arm (proportion logit / Freeman-Tukey,
    incidence rate).
  * Pooling — inverse-variance fixed effect, Mantel-Haenszel (binary FE),
    random effects with DerSimonian-Laird and REML tau-squared.
  * Heterogeneity — Cochran's Q, I-squared, tau-squared (DL + REML), H, and a
    Q-profile CI for tau-squared.
  * Publication bias — funnel data, Egger's regression test, Duval-Tweedie
    trim-and-fill (L0 estimator).
  * Subgroup analysis + mixed-effects meta-regression.
  * Sensitivity — leave-one-out + influence diagnostics (Cook's distance,
    DFFITS, hat values, studentized residuals).
  * GRADE body-of-evidence certainty combiner.

Conventions
-----------
* ``yi`` is the effect estimate on the *analysis scale*: log for OR/RR/IRR,
  Fisher-z for correlation, logit/arcsine for proportions, raw for MD/SMD/RD.
* ``vi`` is the sampling variance of ``yi``.
* Ratio measures (OR/RR/IRR) and proportions/correlations are back-transformed
  for display by :func:`back_transform`.
* Functions never raise on degenerate input; they return ``None`` (drop a
  study) or a dict carrying a ``status`` flag so the orchestrator can log it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import numpy as np
from scipy import optimize, stats

# ---------------------------------------------------------------------------
# Measure metadata
# ---------------------------------------------------------------------------

# Measures whose analysis scale is logarithmic (back-transform with exp).
LOG_MEASURES = {"OR", "RR", "IRR", "PLOGIT", "HR"}
# Measures back-transformed with tanh (Fisher z).
Z_MEASURES = {"ZCOR"}
# Freeman-Tukey double-arcsine proportion.
FT_MEASURES = {"PFT"}
# Difference measures, plotted on a linear scale around 0.
LINEAR_MEASURES = {"MD", "SMD", "RD"}

# Null (no-effect) value on the *display* scale, per measure.
NULL_VALUE = {
    "MD": 0.0, "SMD": 0.0, "RD": 0.0,
    "OR": 1.0, "RR": 1.0, "IRR": 1.0, "HR": 1.0,
    "ZCOR": 0.0, "PLOGIT": None, "PFT": None,
}

CONTINUOUS_MEASURES = {"MD", "SMD"}
BINARY_MEASURES = {"OR", "RR", "RD"}
CORRELATION_MEASURES = {"ZCOR"}
SINGLE_ARM_MEASURES = {"PLOGIT", "PFT", "IRR"}
TIME_TO_EVENT_MEASURES = {"HR"}


def measure_is_log(measure: str) -> bool:
    return measure in LOG_MEASURES


def display_uses_log_axis(measure: str) -> bool:
    """Forest/funnel x-axis is log-scaled for ratio measures."""
    return measure in {"OR", "RR", "IRR", "HR"}


# ---------------------------------------------------------------------------
# Effect-size layer
# ---------------------------------------------------------------------------

@dataclass
class EffectSize:
    yi: float           # estimate on the analysis scale
    vi: float           # sampling variance of yi
    measure: str
    n: float | None = None          # total sample size (for plots / OIS)
    corrected: bool = False         # continuity correction applied?
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def sei(self) -> float:
        return math.sqrt(self.vi) if self.vi is not None and self.vi > 0 else float("nan")

    def as_dict(self) -> dict[str, Any]:
        return {
            "yi": self.yi, "vi": self.vi, "sei": self.sei, "measure": self.measure,
            "n": self.n, "corrected": self.corrected,
        }


def _finite(*xs: Any) -> bool:
    try:
        return all(x is not None and math.isfinite(float(x)) for x in xs)
    except (TypeError, ValueError):
        return False


# --- continuous -----------------------------------------------------------

def md(m1, sd1, n1, m2, sd2, n2) -> EffectSize | None:
    """Raw mean difference. yi = m1 - m2, vi = sd1^2/n1 + sd2^2/n2."""
    if not _finite(m1, sd1, n1, m2, sd2, n2) or n1 < 1 or n2 < 1:
        return None
    if sd1 < 0 or sd2 < 0:
        return None
    yi = float(m1) - float(m2)
    vi = sd1 ** 2 / n1 + sd2 ** 2 / n2
    if vi <= 0:
        return None
    return EffectSize(yi, vi, "MD", n=n1 + n2,
                      raw={"m1": m1, "sd1": sd1, "n1": n1, "m2": m2, "sd2": sd2, "n2": n2})


def smd_hedges_g(m1, sd1, n1, m2, sd2, n2) -> EffectSize | None:
    """Standardized mean difference with Hedges' small-sample correction.

    sd_pooled = sqrt(((n1-1)sd1^2 + (n2-1)sd2^2)/(n1+n2-2))
    d = (m1-m2)/sd_pooled ; J = 1 - 3/(4*df - 1) ; g = J*d
    var(d) = (n1+n2)/(n1*n2) + d^2/(2*(n1+n2)) ; var(g) = J^2 * var(d)
    """
    if not _finite(m1, sd1, n1, m2, sd2, n2) or n1 < 2 or n2 < 2:
        return None
    df = n1 + n2 - 2
    if df <= 0:
        return None
    sp2 = ((n1 - 1) * sd1 ** 2 + (n2 - 1) * sd2 ** 2) / df
    if sp2 <= 0:
        return None
    sp = math.sqrt(sp2)
    d = (float(m1) - float(m2)) / sp
    j = 1.0 - 3.0 / (4.0 * df - 1.0)
    g = j * d
    var_d = (n1 + n2) / (n1 * n2) + d ** 2 / (2.0 * (n1 + n2))
    vg = j ** 2 * var_d
    if vg <= 0:
        return None
    return EffectSize(g, vg, "SMD", n=n1 + n2,
                      raw={"m1": m1, "sd1": sd1, "n1": n1, "m2": m2, "sd2": sd2, "n2": n2,
                           "d": d, "J": j, "sd_pooled": sp})


# --- binary (2x2) ---------------------------------------------------------

def _cc(a, b, c, d, correction=0.5):
    """Apply a continuity correction to all four cells if any is zero."""
    if min(a, b, c, d) == 0:
        return a + correction, b + correction, c + correction, d + correction, True
    return a, b, c, d, False


def log_or(events1, total1, events2, total2, correction=0.5) -> EffectSize | None:
    """Log odds ratio. arm1=intervention (a/b), arm2=control (c/d)."""
    if not _finite(events1, total1, events2, total2):
        return None
    a, n1, c, n2 = float(events1), float(total1), float(events2), float(total2)
    b, d = n1 - a, n2 - c
    if n1 < 1 or n2 < 1 or a < 0 or c < 0 or b < 0 or d < 0:
        return None
    if a == 0 and c == 0:        # double-zero: no information for OR
        return None
    a, b, c, d, corrected = _cc(a, b, c, d, correction)
    yi = math.log((a * d) / (b * c))
    vi = 1 / a + 1 / b + 1 / c + 1 / d
    return EffectSize(yi, vi, "OR", n=n1 + n2, corrected=corrected,
                      raw={"a": events1, "b": total1 - events1, "c": events2,
                           "d": total2 - events2, "n1": total1, "n2": total2})


def log_rr(events1, total1, events2, total2, correction=0.5) -> EffectSize | None:
    """Log risk ratio."""
    if not _finite(events1, total1, events2, total2):
        return None
    a, n1, c, n2 = float(events1), float(total1), float(events2), float(total2)
    if n1 < 1 or n2 < 1 or a < 0 or c < 0 or a > n1 or c > n2:
        return None
    if a == 0 and c == 0:
        return None
    b, d = n1 - a, n2 - c
    a, b, c, d, corrected = _cc(a, b, c, d, correction)
    n1c, n2c = a + b, c + d
    yi = math.log((a / n1c) / (c / n2c))
    vi = 1 / a - 1 / n1c + 1 / c - 1 / n2c
    if vi <= 0:
        return None
    return EffectSize(yi, vi, "RR", n=n1 + n2, corrected=corrected,
                      raw={"a": events1, "c": events2, "n1": total1, "n2": total2})


def risk_difference(events1, total1, events2, total2) -> EffectSize | None:
    """Risk difference p1 - p2 (no continuity correction needed)."""
    if not _finite(events1, total1, events2, total2):
        return None
    a, n1, c, n2 = float(events1), float(total1), float(events2), float(total2)
    if n1 < 1 or n2 < 1 or a < 0 or c < 0 or a > n1 or c > n2:
        return None
    p1, p2 = a / n1, c / n2
    yi = p1 - p2
    vi = p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2
    if vi <= 0:
        vi = 1e-12
    return EffectSize(yi, vi, "RD", n=n1 + n2,
                      raw={"a": events1, "c": events2, "n1": total1, "n2": total2})


# --- correlation ----------------------------------------------------------

def fisher_z(r, n) -> EffectSize | None:
    """Fisher z transform of Pearson r. vi = 1/(n-3)."""
    if not _finite(r, n) or n <= 3:
        return None
    r = max(-0.999999, min(0.999999, float(r)))
    yi = math.atanh(r)
    vi = 1.0 / (n - 3)
    return EffectSize(yi, vi, "ZCOR", n=n, raw={"r": r, "n": n})


# --- single arm -----------------------------------------------------------

def proportion_logit(events, n, correction=0.5) -> EffectSize | None:
    """Logit-transformed single proportion."""
    if not _finite(events, n) or n < 1 or events < 0 or events > n:
        return None
    e, nn = float(events), float(n)
    corrected = False
    if e == 0 or e == nn:
        e += correction
        nn += 2 * correction
        corrected = True
    p = e / nn
    yi = math.log(p / (1 - p))
    vi = 1.0 / (nn * p) + 1.0 / (nn * (1 - p))
    return EffectSize(yi, vi, "PLOGIT", n=n, corrected=corrected,
                      raw={"events": events, "n": n})


def proportion_double_arcsine(events, n) -> EffectSize | None:
    """Freeman-Tukey double-arcsine transformed proportion."""
    if not _finite(events, n) or n < 1 or events < 0 or events > n:
        return None
    e, nn = float(events), float(n)
    yi = 0.5 * (math.asin(math.sqrt(e / (nn + 1))) + math.asin(math.sqrt((e + 1) / (nn + 1))))
    vi = 1.0 / (4 * nn + 2)
    return EffectSize(yi, vi, "PFT", n=n, raw={"events": events, "n": n})


def incidence_rate_log(events, person_time) -> EffectSize | None:
    """Log incidence rate. yi = ln(events/PT), vi = 1/events."""
    if not _finite(events, person_time) or events <= 0 or person_time <= 0:
        return None
    yi = math.log(events / person_time)
    vi = 1.0 / events
    return EffectSize(yi, vi, "IRR", n=person_time, raw={"events": events, "person_time": person_time})


# --- time-to-event --------------------------------------------------------

def hazard_ratio(hr=None, ci_lower=None, ci_upper=None, *,
                 loghr=None, se=None, o_e=None, v=None) -> EffectSize | None:
    """Log hazard ratio for a time-to-event outcome.

    Accepts, in priority order (a HR is NEVER derived from a 2x2 count table):
      1. log-rank observed-minus-expected ``o_e`` + variance ``v`` (Peto:
         yi = (O-E)/V, vi = 1/V);
      2. a reported log-HR ``loghr`` + its standard error ``se``;
      3. a reported ``hr`` + 95% ``ci_lower``/``ci_upper`` (yi = ln HR, and the
         SE is recovered from the CI width on the log scale).
    """
    if _finite(o_e, v) and float(v) > 0:
        oe, vv = float(o_e), float(v)
        return EffectSize(oe / vv, 1.0 / vv, "HR", raw={"o_e": o_e, "v": v})
    if _finite(loghr, se) and float(se) > 0:
        return EffectSize(float(loghr), float(se) ** 2, "HR", raw={"loghr": loghr, "se": se})
    if _finite(hr, ci_lower, ci_upper) and float(hr) > 0 and float(ci_lower) > 0 and float(ci_upper) > 0:
        lo, hi = sorted((float(ci_lower), float(ci_upper)))
        se_ln = (math.log(hi) - math.log(lo)) / (2.0 * _z_crit())
        if se_ln <= 0:
            return None
        return EffectSize(math.log(float(hr)), se_ln ** 2, "HR",
                          raw={"hr": hr, "ci_lower": ci_lower, "ci_upper": ci_upper})
    return None


# --- dispatch -------------------------------------------------------------

def effect_size(measure: str, row: dict[str, Any], correction: float = 0.5) -> EffectSize | None:
    """Compute an :class:`EffectSize` from a raw data row for ``measure``.

    ``row`` keys (use the subset the measure needs):
      continuous: m1, sd1, n1, m2, sd2, n2
      binary:     events1, total1, events2, total2
      correlation:r, n
      single-arm: events, n  (proportion) | events, person_time (rate)
    """
    g = row.get
    if measure == "MD":
        return md(g("m1"), g("sd1"), g("n1"), g("m2"), g("sd2"), g("n2"))
    if measure == "SMD":
        return smd_hedges_g(g("m1"), g("sd1"), g("n1"), g("m2"), g("sd2"), g("n2"))
    if measure == "OR":
        return log_or(g("events1"), g("total1"), g("events2"), g("total2"), correction)
    if measure == "RR":
        return log_rr(g("events1"), g("total1"), g("events2"), g("total2"), correction)
    if measure == "RD":
        return risk_difference(g("events1"), g("total1"), g("events2"), g("total2"))
    if measure == "ZCOR":
        return fisher_z(g("r"), g("n"))
    if measure == "PLOGIT":
        return proportion_logit(g("events"), g("n"), correction)
    if measure == "PFT":
        return proportion_double_arcsine(g("events"), g("n"))
    if measure == "IRR":
        return incidence_rate_log(g("events"), g("person_time"))
    if measure == "HR":
        return hazard_ratio(g("hr"), g("ci_lower"), g("ci_upper"),
                            loghr=g("loghr"), se=g("se"), o_e=g("o_e"), v=g("v"))
    raise ValueError(f"unknown measure {measure!r}")


def back_transform(yi: float, measure: str, harmonic_n: float | None = None) -> float:
    """Convert an analysis-scale value back to the display scale."""
    if measure in LOG_MEASURES and measure != "PLOGIT":
        return math.exp(yi)
    if measure == "PLOGIT":
        return 1.0 / (1.0 + math.exp(-yi))
    if measure in Z_MEASURES:
        return math.tanh(yi)
    if measure in FT_MEASURES:
        # inverse Freeman-Tukey using the harmonic mean of study n
        if harmonic_n and harmonic_n > 0:
            t = yi
            return 0.5 * (1 - np.sign(math.cos(2 * t)) * math.sqrt(
                max(0.0, 1 - (math.sin(2 * t) + (math.sin(2 * t) - 1 / math.sin(2 * t)) / harmonic_n) ** 2)))
        return math.sin(yi) ** 2
    return yi


# ---------------------------------------------------------------------------
# Pooling layer
# ---------------------------------------------------------------------------

def _z_crit(alpha: float = 0.05) -> float:
    return float(stats.norm.ppf(1 - alpha / 2))


def inverse_variance_pool(yis: Sequence[float], vis: Sequence[float], tau2: float = 0.0,
                          alpha: float = 0.05, ci_method: str = "wald") -> dict[str, Any]:
    """Inverse-variance pooled estimate with optional additive tau^2.

    ``ci_method='knapp_hartung'`` widens the CI using a t-distribution with
    k-1 df and a variance-correction factor (the modern random-effects default).
    """
    yi = np.asarray(yis, float)
    vi = np.asarray(vis, float)
    k = yi.size
    if k == 0:
        return {"status": "no_studies", "k": 0}
    w = 1.0 / (vi + tau2)
    sw = w.sum()
    est = float((w * yi).sum() / sw)
    weights_pct = (w / sw * 100.0).tolist()
    if ci_method == "knapp_hartung" and k >= 2:
        # Hartung-Knapp variance estimator
        q = float((w * (yi - est) ** 2).sum() / (k - 1))
        se = math.sqrt(max(q, 1e-12) / sw)
        crit = float(stats.t.ppf(1 - alpha / 2, k - 1))
        zval = est / se
        pval = float(2 * stats.t.sf(abs(zval), k - 1))
    else:
        se = math.sqrt(1.0 / sw)
        crit = _z_crit(alpha)
        zval = est / se if se > 0 else float("nan")
        pval = float(2 * stats.norm.sf(abs(zval)))
    return {
        "status": "ok", "k": k, "estimate": est, "se": se,
        "ci_low": est - crit * se, "ci_high": est + crit * se,
        "z": zval, "p": pval, "tau2": tau2,
        "weights": w.tolist(), "weights_pct": weights_pct,
        "ci_method": ci_method,
    }


def mantel_haenszel(tables: Sequence[dict], measure: str, alpha: float = 0.05) -> dict[str, Any]:
    """Mantel-Haenszel fixed-effect pool for binary data.

    ``tables``: list of {a, b, c, d} (a=ev int, b=non-ev int, c=ev ctrl,
    d=non-ev ctrl). Returns the pooled estimate on the *analysis* scale
    (log for OR/RR) with the Robins-Breslow-Greenland / Greenland-Robins
    variance. Tolerates single-zero cells without correction.
    """
    A = np.array([t["a"] for t in tables], float)
    B = np.array([t["b"] for t in tables], float)
    C = np.array([t["c"] for t in tables], float)
    D = np.array([t["d"] for t in tables], float)
    N = A + B + C + D
    k = A.size
    if k == 0 or np.any(N <= 0):
        return {"status": "no_studies", "k": k}
    crit = _z_crit(alpha)
    if measure == "OR":
        R = (A * D / N).sum()
        S = (B * C / N).sum()
        if R <= 0 or S <= 0:
            return {"status": "degenerate", "k": k}
        log_est = math.log(R / S)
        P = (A + D) / N
        Q = (B + C) / N
        Ri = A * D / N
        Si = B * C / N
        var = ((P * Ri).sum() / (2 * R ** 2)
               + (P * Si + Q * Ri).sum() / (2 * R * S)
               + (Q * Si).sum() / (2 * S ** 2))
    elif measure == "RR":
        n1 = A + B
        n2 = C + D
        R = (A * n2 / N).sum()
        S = (C * n1 / N).sum()
        if R <= 0 or S <= 0:
            return {"status": "degenerate", "k": k}
        log_est = math.log(R / S)
        num = ((n1 * n2 * (A + C) - A * C * N) / N ** 2).sum()
        var = num / (R * S)
    elif measure == "RD":
        n1 = A + B
        n2 = C + D
        w = n1 * n2 / N
        rd_i = A / n1 - C / n2
        sw = w.sum()
        est = float((w * rd_i).sum() / sw)
        num = ((A * B * n2 ** 3 + C * D * n1 ** 3) / (n1 * n2 * N ** 2)).sum()
        var = num / sw ** 2
        se = math.sqrt(max(var, 1e-18))
        zval = est / se if se > 0 else float("nan")
        return {"status": "ok", "k": k, "estimate": est, "se": se,
                "ci_low": est - crit * se, "ci_high": est + crit * se,
                "z": zval, "p": float(2 * stats.norm.sf(abs(zval))), "method": "MH"}
    else:
        raise ValueError(f"Mantel-Haenszel undefined for measure {measure!r}")
    se = math.sqrt(max(var, 1e-18))
    zval = log_est / se if se > 0 else float("nan")
    return {"status": "ok", "k": k, "estimate": log_est, "se": se,
            "ci_low": log_est - crit * se, "ci_high": log_est + crit * se,
            "z": zval, "p": float(2 * stats.norm.sf(abs(zval))), "method": "MH"}


# ---------------------------------------------------------------------------
# Heterogeneity
# ---------------------------------------------------------------------------

def _q_statistic(yi: np.ndarray, vi: np.ndarray) -> tuple[float, float]:
    """Cochran's Q at the fixed-effect solution + the FE pooled estimate."""
    w = 1.0 / vi
    mu = (w * yi).sum() / w.sum()
    q = float((w * (yi - mu) ** 2).sum())
    return q, float(mu)


def tau2_dersimonian_laird(yi: np.ndarray, vi: np.ndarray) -> float:
    w = 1.0 / vi
    q, _ = _q_statistic(yi, vi)
    k = yi.size
    c = w.sum() - (w ** 2).sum() / w.sum()
    if c <= 0:
        return 0.0
    return max(0.0, (q - (k - 1)) / c)


def _reml_neg_ll(tau2: float, yi: np.ndarray, vi: np.ndarray) -> float:
    w = 1.0 / (vi + tau2)
    sw = w.sum()
    mu = (w * yi).sum() / sw
    ll = -0.5 * (np.log(vi + tau2).sum() + math.log(sw) + (w * (yi - mu) ** 2).sum())
    return -ll


def tau2_reml(yi: np.ndarray, vi: np.ndarray) -> float:
    k = yi.size
    if k < 2:
        return 0.0
    upper = max(10.0 * float(vi.max()), 10.0 * tau2_dersimonian_laird(yi, vi) + 1.0, 10.0)
    res = optimize.minimize_scalar(_reml_neg_ll, bounds=(0.0, upper),
                                   args=(yi, vi), method="bounded",
                                   options={"xatol": 1e-8})
    return max(0.0, float(res.x))


def tau2_paule_mandel(yi: np.ndarray, vi: np.ndarray, *,
                      max_iter: int = 200, tol: float = 1e-7) -> float:
    """Paule-Mandel (empirical-Bayes) tau^2 estimator.

    Solves the generalized-Q estimating equation ``sum w_i (y_i - mu)^2 = k - 1``
    (with ``w_i = 1/(v_i + tau^2)`` and mu the weighted mean) by bisection.
    Clamped at 0; less biased than DL under real heterogeneity.
    """
    k = yi.size
    if k < 2:
        return 0.0

    def gstat(t2: float) -> float:
        w = 1.0 / (vi + t2)
        mu = (w * yi).sum() / w.sum()
        return float((w * (yi - mu) ** 2).sum()) - (k - 1)

    if gstat(0.0) <= 0:
        return 0.0
    lo, hi = 0.0, 10.0 * float(vi.max()) + 10.0
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


def q_profile_tau2_ci(yi: np.ndarray, vi: np.ndarray, alpha: float = 0.05) -> tuple[float, float]:
    """Q-profile (generalized Q) confidence interval for tau^2."""
    k = yi.size
    df = k - 1
    if df < 1:
        return (float("nan"), float("nan"))

    def gen_q(tau2):
        w = 1.0 / (vi + tau2)
        mu = (w * yi).sum() / w.sum()
        return float((w * (yi - mu) ** 2).sum())

    lo_t = stats.chi2.ppf(alpha / 2, df)
    hi_t = stats.chi2.ppf(1 - alpha / 2, df)
    upper = max(10.0 * float(vi.max()), 1000.0)

    def solve(target):
        # gen_q is decreasing in tau2
        if gen_q(0.0) <= target:
            return 0.0
        if gen_q(upper) >= target:
            return upper
        return float(optimize.brentq(lambda t: gen_q(t) - target, 0.0, upper))

    # lower bound of tau2 corresponds to the upper Q quantile and vice versa
    ci_low = solve(hi_t)
    ci_high = solve(lo_t)
    return (ci_low, ci_high)


def heterogeneity(yis: Sequence[float], vis: Sequence[float], alpha: float = 0.05) -> dict[str, Any]:
    """Q, I^2, tau^2 (DL + REML), H, and a Q-profile tau^2 CI."""
    yi = np.asarray(yis, float)
    vi = np.asarray(vis, float)
    k = yi.size
    if k < 2:
        return {"status": "insufficient_studies", "k": k,
                "Q": None, "df": max(0, k - 1), "p": None,
                "I2": None, "tau2_DL": 0.0, "tau2_REML": 0.0, "tau2_PM": 0.0, "H": None}
    q, _ = _q_statistic(yi, vi)
    df = k - 1
    i2 = max(0.0, (q - df) / q) * 100.0 if q > 0 else 0.0
    h = math.sqrt(q / df) if df > 0 else float("nan")
    p = float(stats.chi2.sf(q, df))
    t2_dl = tau2_dersimonian_laird(yi, vi)
    t2_reml = tau2_reml(yi, vi)
    t2_pm = tau2_paule_mandel(yi, vi)
    tau2_ci = q_profile_tau2_ci(yi, vi, alpha)
    return {
        "status": "ok", "k": k, "Q": q, "df": df, "p": p,
        "I2": i2, "H": h, "tau": math.sqrt(t2_reml),
        "tau2_DL": t2_dl, "tau2_REML": t2_reml, "tau2_PM": t2_pm,
        "tau2_ci_low": tau2_ci[0], "tau2_ci_high": tau2_ci[1],
        "low_power": k == 2,
    }


# ---------------------------------------------------------------------------
# Main pooling entry point
# ---------------------------------------------------------------------------

def pool(effects: Sequence[EffectSize], measure: str, *,
         model: str = "random", tau2_method: str = "REML",
         fe_method: str = "IV", re_ci_method: str = "wald",
         alpha: float = 0.05) -> dict[str, Any]:
    """Pool a list of effect sizes. Returns a dict with fixed/random results,
    heterogeneity, and per-study weight breakdowns ready for a forest plot.

    ``model`` in {fixed, random, both}. ``fe_method`` in {IV, MH} (MH only for
    binary). ``tau2_method`` in {DL, REML, PM}.
    """
    effects = [e for e in effects if e is not None and _finite(e.yi, e.vi) and e.vi > 0]
    k = len(effects)
    if k == 0:
        return {"status": "no_studies", "k": 0}
    yi = np.array([e.yi for e in effects], float)
    vi = np.array([e.vi for e in effects], float)

    het = heterogeneity(yi, vi, alpha)
    tau2 = 0.0
    if k >= 2:
        _tau2_key = {"REML": "tau2_REML", "DL": "tau2_DL", "PM": "tau2_PM"}
        tau2 = het[_tau2_key.get(tau2_method.upper(), "tau2_REML")]

    out: dict[str, Any] = {"status": "ok", "k": k, "measure": measure,
                           "heterogeneity": het, "model": model,
                           "tau2_method": tau2_method, "fe_method": fe_method}

    # Fixed effect
    if fe_method.upper() == "MH" and measure in BINARY_MEASURES:
        tables = [{"a": e.raw["a"], "b": e.raw["n1"] - e.raw["a"],
                   "c": e.raw["c"], "d": e.raw["n2"] - e.raw["c"]} for e in effects]
        fixed = mantel_haenszel(tables, measure, alpha)
        fixed["weights_pct"] = _iv_weight_pct(vi, 0.0)
    else:
        fixed = inverse_variance_pool(yi, vi, 0.0, alpha, "wald")
    out["fixed"] = fixed

    # Random effects
    if k >= 2:
        random = inverse_variance_pool(yi, vi, tau2, alpha, re_ci_method)
        random["tau2"] = tau2
    else:
        random = dict(fixed)
        random["tau2"] = 0.0
        random["note"] = "single study — random == fixed"
    out["random"] = random

    out["prediction_interval"] = _prediction_interval(yi, vi, tau2, alpha) if k >= 3 else None
    out["forest"] = build_forest(effects, measure, fixed, random, model, het)
    return out


def _iv_weight_pct(vi: np.ndarray, tau2: float) -> list[float]:
    w = 1.0 / (vi + tau2)
    return (w / w.sum() * 100.0).tolist()


def _prediction_interval(yi: np.ndarray, vi: np.ndarray, tau2: float, alpha: float) -> dict[str, float]:
    k = yi.size
    w = 1.0 / (vi + tau2)
    mu = (w * yi).sum() / w.sum()
    se_mu = math.sqrt(1.0 / w.sum())
    t = float(stats.t.ppf(1 - alpha / 2, k - 2))
    spread = t * math.sqrt(tau2 + se_mu ** 2)
    return {"low": mu - spread, "high": mu + spread}


# ---------------------------------------------------------------------------
# Forest-plot data
# ---------------------------------------------------------------------------

def build_forest(effects: Sequence[EffectSize], measure: str,
                 fixed: dict, random: dict, model: str, het: dict,
                 labels: Sequence[str] | None = None,
                 subgroups: Sequence[str] | None = None,
                 robs: Sequence[str] | None = None,
                 alpha: float = 0.05) -> dict[str, Any]:
    """Assemble a frontend-ready forest payload (display scale)."""
    crit = _z_crit(alpha)
    log_axis = display_uses_log_axis(measure)
    use = random if model != "fixed" and random.get("status") == "ok" else fixed
    wpct = use.get("weights_pct") or [100.0 / len(effects)] * len(effects)
    studies = []
    for i, e in enumerate(effects):
        lo = e.yi - crit * e.sei
        hi = e.yi + crit * e.sei
        studies.append({
            "label": labels[i] if labels else f"Study {i + 1}",
            "es": back_transform(e.yi, measure),
            "lo": back_transform(lo, measure),
            "hi": back_transform(hi, measure),
            "es_raw": e.yi, "se": e.sei,
            "weight": (wpct[i] / 100.0) if i < len(wpct) else 1.0 / len(effects),
            "weight_pct": wpct[i] if i < len(wpct) else 100.0 / len(effects),
            "n": e.n, "subgroup": subgroups[i] if subgroups else None,
            "rob": robs[i] if robs else None,
        })
    pooled = {}
    for name, res in (("fixed", fixed), ("random", random)):
        if res.get("status") == "ok":
            pooled[name] = {
                "es": back_transform(res["estimate"], measure),
                "lo": back_transform(res["ci_low"], measure),
                "hi": back_transform(res["ci_high"], measure),
                "p": res.get("p"),
            }
    het_line = _format_het_line(het)
    return {
        "measure": measure, "log_axis": log_axis,
        "null_value": NULL_VALUE.get(measure),
        "studies": studies, "pooled": pooled,
        "model": model, "het_line": het_line,
    }


def _format_het_line(het: dict) -> str:
    if het.get("status") != "ok":
        return ""
    return (f"I²={het['I2']:.0f}% · τ²={het['tau2_REML']:.3f} · "
            f"Q={het['Q']:.2f} (df={het['df']}, p={het['p']:.3f})")


# ---------------------------------------------------------------------------
# Publication bias
# ---------------------------------------------------------------------------

def funnel_data(effects: Sequence[EffectSize], pooled_estimate: float) -> dict[str, Any]:
    """Per-study (es, se) plus the 95%/99% pseudo-CI funnel bounds."""
    pts = [{"es": e.yi, "se": e.sei, "imputed": False} for e in effects]
    se_max = max((e.sei for e in effects), default=0.0)
    return {"points": pts, "pooled": pooled_estimate, "se_max": se_max,
            "ci95": 1.959963985, "ci99": 2.575829304}


def eggers_test(effects: Sequence[EffectSize]) -> dict[str, Any]:
    """Egger's regression test for funnel asymmetry.

    Regress the standard normal deviate (yi/sei) on precision (1/sei) by OLS.
    The intercept tests for small-study effects (0 under symmetry).
    """
    k = len(effects)
    if k < 3:
        return {"status": "insufficient_studies", "k": k}
    snd = np.array([e.yi / e.sei for e in effects], float)
    precision = np.array([1.0 / e.sei for e in effects], float)
    X = np.column_stack([np.ones(k), precision])
    beta, *_ = np.linalg.lstsq(X, snd, rcond=None)
    resid = snd - X @ beta
    dof = k - 2
    sigma2 = (resid @ resid) / dof
    cov = sigma2 * np.linalg.inv(X.T @ X)
    intercept = float(beta[0])
    se_int = math.sqrt(cov[0, 0])
    t = intercept / se_int if se_int > 0 else float("nan")
    p = float(2 * stats.t.sf(abs(t), dof))
    return {"status": "ok", "k": k, "intercept": intercept, "se": se_int,
            "t": t, "p": p, "df": dof, "slope": float(beta[1]),
            "underpowered": k < 10}


def _signed_rank_t(dev: np.ndarray) -> float:
    """Tweedie's Tn: sum of ranks of |dev| for the positive deviations."""
    order = np.argsort(np.abs(dev), kind="mergesort")
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, dev.size + 1)
    return float(ranks[dev > 0].sum())


def trim_and_fill(effects: Sequence[EffectSize], measure: str, *,
                  model: str = "random", tau2_method: str = "REML",
                  side: str = "auto", max_iter: int = 100) -> dict[str, Any]:
    """Duval & Tweedie iterative trim-and-fill using the L0 estimator."""
    k0 = len(effects)
    if k0 < 3:
        return {"status": "insufficient_studies", "k": k0, "n_imputed": 0}
    yi = np.array([e.yi for e in effects], float)
    vi = np.array([e.vi for e in effects], float)

    def re_estimate(y, v):
        if y.size < 2:
            return float(y[0]) if y.size else float("nan")
        t2 = tau2_reml(y, v) if tau2_method.upper() == "REML" else tau2_dersimonian_laird(y, v)
        w = 1.0 / (v + t2)
        return float((w * y).sum() / w.sum())

    # decide the suppressed side from the initial estimate
    mu0 = re_estimate(yi, vi)
    if side == "auto":
        # Tn = rank sum of right-side deviations; large Tn => right is
        # over-represented => studies suppressed on the LEFT.
        side = "left" if _signed_rank_t(yi - mu0) > (k0 * (k0 + 1)) / 4 else "right"

    # Orient so the OVER-represented (trimmed) side is positive: missing-left
    # means we trim the extreme right (flip=+1); missing-right trims left.
    flip = 1.0 if side == "left" else -1.0
    y = (yi - mu0) * flip  # work on the side where extremes are positive
    v = vi.copy()
    mu = 0.0
    L = 0
    for _ in range(max_iter):
        # trim L most extreme positive studies, re-estimate
        keep = np.ones(y.size, bool)
        if L > 0:
            extreme_idx = np.argsort(y)[-L:]
            keep[extreme_idx] = False
        yk, vk = y[keep], v[keep]
        mu = re_estimate(yk + mu0, vk) - mu0 if yk.size else 0.0
        dev = yk - mu
        Tn = _signed_rank_t(dev)
        kk = yk.size
        L_new = max(0, int(round((4 * Tn - kk * (kk + 1)) / (2 * kk - 1)))) if kk > 1 else 0
        if L_new == L:
            break
        L = L_new

    # fill: reflect the L most extreme studies about the trimmed estimate
    imputed = []
    if L > 0:
        extreme_idx = np.argsort(y)[-L:]
        for idx in extreme_idx:
            reflected = (2 * mu - y[idx]) * flip + mu0
            imputed.append({"yi": float(reflected), "vi": float(v[idx])})
    y_full = np.concatenate([yi, np.array([p["yi"] for p in imputed], float)]) if imputed else yi
    v_full = np.concatenate([vi, np.array([p["vi"] for p in imputed], float)]) if imputed else vi
    adj = re_estimate(y_full, v_full)
    return {
        "status": "ok", "k": k0, "n_imputed": L, "side": side,
        "adjusted_estimate": adj,
        "adjusted_estimate_display": back_transform(adj, measure),
        "imputed_points": [{"es": p["yi"], "se": math.sqrt(p["vi"]), "imputed": True} for p in imputed],
    }


# ---------------------------------------------------------------------------
# Subgroup analysis + meta-regression
# ---------------------------------------------------------------------------

def subgroup_analysis(effects: Sequence[EffectSize], groups: Sequence[str], measure: str, *,
                      model: str = "random", tau2_method: str = "REML",
                      alpha: float = 0.05) -> dict[str, Any]:
    """Pool within each subgroup + a Q-test for subgroup differences."""
    pairs = [(e, g) for e, g in zip(effects, groups) if e is not None]
    levels = sorted({g for _, g in pairs if g is not None})
    if len(levels) < 2:
        return {"status": "insufficient_groups", "levels": levels}
    sub_results = {}
    q_within_total = 0.0
    pooled_ests, pooled_ws = [], []
    for lv in levels:
        es = [e for e, g in pairs if g == lv]
        res = pool(es, measure, model=model, tau2_method=tau2_method, alpha=alpha)
        sub_results[lv] = {
            "k": res["k"],
            "estimate": res[("random" if model != "fixed" else "fixed")].get("estimate"),
            "ci_low": res[("random" if model != "fixed" else "fixed")].get("ci_low"),
            "ci_high": res[("random" if model != "fixed" else "fixed")].get("ci_high"),
            "I2": res["heterogeneity"].get("I2"),
            "display": back_transform(res[("random" if model != "fixed" else "fixed")]["estimate"], measure),
        }
        yi = np.array([e.yi for e in es], float)
        vi = np.array([e.vi for e in es], float)
        if yi.size >= 2:
            q_within_total += _q_statistic(yi, vi)[0]
        # group-level pooled for the between test (fixed-effect weights)
        w = 1.0 / vi
        pooled_ests.append((w * yi).sum() / w.sum())
        pooled_ws.append(w.sum())
    # Q-between via the fixed-effect pooled subgroup estimates
    pe = np.array(pooled_ests, float)
    pw = np.array(pooled_ws, float)
    grand = (pw * pe).sum() / pw.sum()
    q_between = float((pw * (pe - grand) ** 2).sum())
    df_between = len(levels) - 1
    p_between = float(stats.chi2.sf(q_between, df_between))
    return {"status": "ok", "levels": levels, "subgroups": sub_results,
            "q_between": q_between, "df_between": df_between, "p_between": p_between,
            "q_within": q_within_total}


def meta_regression(effects: Sequence[EffectSize], moderators: np.ndarray, *,
                    col_names: Sequence[str] | None = None,
                    tau2_method: str = "REML", alpha: float = 0.05,
                    max_iter: int = 100) -> dict[str, Any]:
    """Mixed-effects (random-effects) meta-regression by iterative WLS.

    ``moderators`` is a (k, p-1) design matrix WITHOUT the intercept column
    (it is added). Returns coefficients, SEs, z/p, residual heterogeneity and
    an R^2 analog.
    """
    yi = np.array([e.yi for e in effects], float)
    vi = np.array([e.vi for e in effects], float)
    k = yi.size
    Xmod = np.asarray(moderators, float).reshape(k, -1)
    X = np.column_stack([np.ones(k), Xmod])
    p = X.shape[1]
    if k <= p:
        return {"status": "insufficient_studies", "k": k, "p": p}

    tau2 = 0.0
    for _ in range(max_iter):
        W = np.diag(1.0 / (vi + tau2))
        XtWX = X.T @ W @ X
        beta = np.linalg.solve(XtWX, X.T @ W @ yi)
        resid = yi - X @ beta
        # REML estimate of residual tau^2 (method-of-moments / Viechtbauer step)
        P = W - W @ X @ np.linalg.inv(XtWX) @ X.T @ W
        trace_term = np.trace(P @ np.diag(vi))
        rss = float(resid @ np.diag(1.0 / (vi + tau2)) @ resid)
        new_tau2 = max(0.0, tau2 + (rss - (k - p)) / np.trace(P))
        if abs(new_tau2 - tau2) < 1e-7:
            tau2 = new_tau2
            break
        tau2 = new_tau2

    W = np.diag(1.0 / (vi + tau2))
    XtWX = X.T @ W @ X
    cov = np.linalg.inv(XtWX)
    beta = cov @ X.T @ W @ yi
    se = np.sqrt(np.diag(cov))
    z = beta / se
    pvals = 2 * stats.norm.sf(np.abs(z))
    resid = yi - X @ beta
    qe = float(resid @ np.diag(1.0 / vi) @ resid)
    qe_p = float(stats.chi2.sf(qe, k - p))
    # omnibus test of moderators (excluding intercept)
    bm = beta[1:]
    cov_m = cov[1:, 1:]
    qm = float(bm @ np.linalg.solve(cov_m, bm)) if p > 1 else 0.0
    qm_p = float(stats.chi2.sf(qm, p - 1)) if p > 1 else 1.0
    tau2_total = tau2_reml(yi, vi)
    r2 = max(0.0, (tau2_total - tau2) / tau2_total) * 100.0 if tau2_total > 0 else 0.0
    names = ["intercept"] + list(col_names or [f"x{i}" for i in range(1, p)])
    return {
        "status": "ok", "k": k,
        "coefficients": [
            {"name": names[i], "estimate": float(beta[i]), "se": float(se[i]),
             "z": float(z[i]), "p": float(pvals[i]),
             "ci_low": float(beta[i] - _z_crit(alpha) * se[i]),
             "ci_high": float(beta[i] + _z_crit(alpha) * se[i])}
            for i in range(p)
        ],
        "tau2_residual": tau2, "QE": qe, "QE_p": qe_p,
        "QM": qm, "QM_df": p - 1, "QM_p": qm_p, "R2": r2,
    }


# ---------------------------------------------------------------------------
# Sensitivity / influence
# ---------------------------------------------------------------------------

def leave_one_out(effects: Sequence[EffectSize], measure: str, *,
                  model: str = "random", tau2_method: str = "REML",
                  labels: Sequence[str] | None = None,
                  alpha: float = 0.05) -> list[dict[str, Any]]:
    """Re-pool with each study omitted in turn."""
    out = []
    n = len(effects)
    for i in range(n):
        subset = [e for j, e in enumerate(effects) if j != i]
        res = pool(subset, measure, model=model, tau2_method=tau2_method, alpha=alpha)
        use = res["random"] if model != "fixed" and res["random"].get("status") == "ok" else res["fixed"]
        out.append({
            "omitted": labels[i] if labels else f"Study {i + 1}",
            "estimate": use.get("estimate"),
            "ci_low": use.get("ci_low"), "ci_high": use.get("ci_high"),
            "es": back_transform(use["estimate"], measure) if use.get("estimate") is not None else None,
            "lo": back_transform(use["ci_low"], measure) if use.get("ci_low") is not None else None,
            "hi": back_transform(use["ci_high"], measure) if use.get("ci_high") is not None else None,
            "I2": res["heterogeneity"].get("I2"),
            "tau2": res["heterogeneity"].get("tau2_REML"),
        })
    return out


def influence_diagnostics(effects: Sequence[EffectSize], *,
                          tau2_method: str = "REML",
                          labels: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """Per-study influence diagnostics for the intercept-only RE model.

    Reports hat, weight, leave-one-out estimate, tau2.del, DFFITS, Cook's
    distance, studentized residual, and an ``influential`` flag.
    """
    yi = np.array([e.yi for e in effects], float)
    vi = np.array([e.vi for e in effects], float)
    k = yi.size
    if k < 3:
        return [{"label": (labels[i] if labels else f"Study {i + 1}"),
                 "status": "insufficient_studies"} for i in range(k)]

    def fit(y, v):
        t2 = tau2_reml(y, v) if tau2_method.upper() == "REML" else tau2_dersimonian_laird(y, v)
        w = 1.0 / (v + t2)
        mu = (w * y).sum() / w.sum()
        se = math.sqrt(1.0 / w.sum())
        return mu, se, t2, w

    mu_full, se_full, t2_full, w_full = fit(yi, vi)
    sw = w_full.sum()
    out = []
    cutoff = 3 * math.sqrt(1.0 / (k - 1))
    for i in range(k):
        keep = np.arange(k) != i
        mu_i, se_i, t2_i, _ = fit(yi[keep], vi[keep])
        hat = float(w_full[i] / sw)
        resid = yi[i] - mu_full
        std_resid = resid / math.sqrt(vi[i] + t2_full)
        dffits = (mu_full - mu_i) / se_i if se_i > 0 else float("nan")
        cook = ((mu_full - mu_i) ** 2) / (se_full ** 2) if se_full > 0 else float("nan")
        out.append({
            "label": labels[i] if labels else f"Study {i + 1}",
            "hat": hat, "weight_pct": float(w_full[i] / sw * 100),
            "estimate_del": float(mu_i), "tau2_del": float(t2_i),
            "std_residual": float(std_resid), "dffits": float(dffits),
            "cooks_distance": float(cook),
            "influential": bool(abs(dffits) > cutoff or abs(std_resid) > 1.96),
        })
    return out


# ---------------------------------------------------------------------------
# GRADE body-of-evidence certainty
# ---------------------------------------------------------------------------

# Mirrors quality_appraisal.GRADE_LEVELS — kept local so this module stays
# importable without the LLM/RoB-tool dependency chain (fast unit tests).
GRADE_LEVELS = ["High", "Moderate", "Low", "Very low"]

# Per-study RoB label -> a 0..2 severity, mirroring quality_appraisal._rob_downgrade.
#
# Every key here is a *risk* label, so "high" means high RISK and scores 2. AMSTAR-2
# labels must never reach this map: it rates a systematic review's *confidence*, where
# "High" is GOOD, so a well-conducted review would score 2 and downgrade twice.
# "Critically low" is deliberately absent for the same reason — adding it would make
# the inverted reading look supported. AMSTAR-2 studies are excluded from the
# risk-of-bias domain at the source instead; see `_NON_ROB_TOOLS` in backend/synthesis.py.
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


def _grade_index(level: str) -> int:
    try:
        return GRADE_LEVELS.index(level)
    except ValueError:
        return 0


def _rob_across_studies(per_study_rob: Sequence[str],
                        weights: Sequence[float] | None) -> tuple[int, str, bool]:
    """Weighted RoB-across-studies downgrade. Returns ``(downgrade, reason, assessed)``.

    Blank/None entries are studies with no assessable judgement — never appraised,
    the appraisal failed, or the instrument does not produce a risk-of-bias label.
    They are dropped and the weights renormalized over the remainder. Scoring them
    as "some concerns" invents a finding about studies nobody looked at, and scoring
    them "low" inflates certainty; both are fabrications, so neither is used.

    ``assessed=False`` means the domain could not be rated at all. That is distinct
    from a rating of 0: the caller must not add it into a certainty total.

    Coverage guard (direction-aware): dropping unassessed studies and
    renormalizing is only sound while the result cannot inflate certainty. A
    downgrade computed from the assessed sliver stands whatever the coverage —
    unassessed studies could only add concerns, never remove them. A *clean*
    (0-downgrade) result, though, is only trustworthy when the assessed studies
    carry at least half the pooled weight: a body 99% unassessed with one small
    Low-risk study must not read as "most weight is in low risk-of-bias
    studies", so below 50% assessed weight a clean result is reported as not
    assessable instead.
    """
    labels = list(per_study_rob or [])
    w_all = (np.asarray(weights, float)
             if weights is not None and len(weights) == len(labels)
             else np.ones(len(labels), float))
    keep = [i for i, r in enumerate(labels) if (r or "").strip()]
    if not keep:
        return 0, "no risk-of-bias judgement is available for any pooled study", False

    total_w = float(w_all.sum())
    coverage = float(w_all[keep].sum()) / total_w if total_w > 0 else 0.0
    sev = np.array([_ROB_SEVERITY.get(labels[i].strip().lower(), 1) for i in keep], float)
    w = w_all[keep]
    w = w / w.sum()
    gap = ("" if len(keep) == len(labels) else
           f"; {len(labels) - len(keep)} of {len(labels)} pooled studies "
           f"({1 - coverage:.0%} of pooled weight) had no assessable "
           "judgement and are excluded from this domain")
    frac_serious = float(w[sev >= 2].sum())
    frac_some = float(w[sev >= 1].sum())
    if frac_serious >= 0.5:
        return 2, f"most of the weight ({frac_serious:.0%}) is in studies at high/serious risk of bias{gap}", True
    if frac_serious >= 0.25 or frac_some >= 0.5:
        return 1, f"a substantial share of weight ({frac_some:.0%}) is in studies with risk-of-bias concerns{gap}", True
    if coverage < 0.5:
        return 0, (f"risk of bias was assessed for only {coverage:.0%} of the pooled "
                   f"weight ({len(keep)} of {len(labels)} studies) — insufficient "
                   "coverage to accept a clean (no-downgrade) rating for this domain"), False
    return 0, f"most weight is in low risk-of-bias studies{gap}", True


def _inconsistency_downgrade(het: dict, subgroup: dict | None) -> tuple[int, str]:
    if het.get("status") != "ok":
        return 0, "single study — inconsistency not assessable"
    i2 = het.get("I2", 0.0) or 0.0
    p = het.get("p", 1.0)
    explained = bool(subgroup and subgroup.get("status") == "ok" and subgroup.get("p_between", 1.0) < 0.05)
    if explained:
        return 0, f"heterogeneity (I²={i2:.0f}%) explained by subgroup differences"
    if i2 > 75 and p < 0.10:
        return 1, f"considerable heterogeneity (I²={i2:.0f}%, p={p:.3f})"
    if i2 > 50 and p < 0.10:
        return 1, f"substantial unexplained heterogeneity (I²={i2:.0f}%)"
    return 0, f"acceptable consistency (I²={i2:.0f}%)"


def _imprecision_downgrade(pooled: dict, measure: str, total_n: float,
                           mid_benefit: float | None, mid_harm: float | None,
                           is_binary: bool) -> tuple[int, str]:
    lo, hi = pooled.get("ci_low"), pooled.get("ci_high")
    null = 0.0 if measure not in LOG_MEASURES else 0.0  # log scale null = 0
    crosses_null = lo is not None and hi is not None and lo <= null <= hi
    # optimal information size heuristic
    ois_fail = total_n is not None and total_n < (300 if is_binary else 400)
    if mid_benefit is not None and mid_harm is not None and lo is not None and hi is not None:
        # CI crosses both MIDs -> very serious
        d_lo = back_transform(lo, measure)
        d_hi = back_transform(hi, measure)
        if d_lo <= mid_harm and d_hi >= mid_benefit:
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


def _pubbias_downgrade(egger: dict | None, trimfill: dict | None) -> tuple[int, str]:
    if egger and egger.get("status") == "ok" and egger.get("p", 1.0) < 0.10:
        return 1, f"funnel asymmetry (Egger p={egger['p']:.3f})"
    if trimfill and trimfill.get("status") == "ok" and trimfill.get("n_imputed", 0) >= 2:
        return 1, f"trim-and-fill imputed {trimfill['n_imputed']} missing studies"
    return 0, "no strong evidence of publication bias"


def grade_body_of_evidence(*, initial: str, per_study_rob: Sequence[str],
                           weights: Sequence[float] | None, heterogeneity: dict,
                           pooled: dict, measure: str, total_n: float | None,
                           subgroup: dict | None = None, egger: dict | None = None,
                           trimfill: dict | None = None,
                           indirectness_levels: int = 0,
                           indirectness_reason: str = "",
                           indirectness_assessed: bool | None = None,
                           mid_benefit: float | None = None,
                           mid_harm: float | None = None,
                           is_binary: bool = False) -> dict[str, Any]:
    """Combine the five downgradeable GRADE domains into a body-of-evidence
    certainty rating. This completes the domains the per-paper Quality
    Appraisal explicitly defers (inconsistency, publication bias).

    Returns ``status="rated"`` with a ``final`` certainty, or ``status="not_rated"``
    with ``final=None`` when risk of bias — a required GRADE domain — could not be
    assessed for any pooled study (or only for a minority of the pooled weight).
    Rating such a body would be indistinguishable, in the output, from rating one
    that was assessed and found clean.

    Indirectness is an *input*, not something this calculator can judge: whether
    the included evidence matches the review question needs a PICO assessment.
    ``indirectness_assessed`` says whether one was performed; when omitted it is
    inferred (a supplied reason or a non-zero downgrade counts as assessed). An
    unassessed indirectness domain contributes 0 downgrade levels but is marked
    ``assessable=False`` with a warning, never reported as "no serious
    indirectness".
    """
    rob_lv, rob_reason, rob_assessed = _rob_across_studies(per_study_rob, weights)
    inc_lv, inc_reason = _inconsistency_downgrade(heterogeneity, subgroup)
    imp_lv, imp_reason = _imprecision_downgrade(pooled, measure, total_n or 0.0,
                                                mid_benefit, mid_harm, is_binary)
    pub_lv, pub_reason = _pubbias_downgrade(egger, trimfill)
    ind_lv = max(0, int(indirectness_levels))
    ind_assessed = (indirectness_assessed if indirectness_assessed is not None
                    else bool(indirectness_reason) or ind_lv > 0)
    if ind_assessed:
        ind_reason = indirectness_reason or (
            "no serious indirectness" if ind_lv == 0 else "indirectness concerns")
    else:
        ind_reason = ("not assessed — no indirectness assessment was supplied "
                      "for this body of evidence")

    domains = [
        {"domain": "Risk of bias",
         "downgrade": rob_lv if rob_assessed else None,
         "assessable": rob_assessed, "reason": rob_reason},
        {"domain": "Inconsistency", "downgrade": inc_lv, "reason": inc_reason},
        {"domain": "Indirectness",
         "downgrade": ind_lv if ind_assessed else None,
         "assessable": ind_assessed, "reason": ind_reason},
        {"domain": "Imprecision", "downgrade": imp_lv, "reason": imp_reason},
        {"domain": "Publication bias", "downgrade": pub_lv, "reason": pub_reason},
    ]

    if not rob_assessed:
        # The pooled estimate, heterogeneity and publication-bias results are all
        # risk-of-bias-independent and remain valid; only certainty is withheld.
        return {
            "initial": initial, "final": None, "status": "not_rated",
            "total_downgrade": None, "domains": domains,
            "warnings": [f"Certainty not rated: {rob_reason}. Risk of bias is a "
                         "required GRADE domain."],
            "explanation": (
                f"Certainty could not be rated for this outcome — {rob_reason}. "
                "Risk of bias is a required GRADE domain. The pooled estimate, "
                "heterogeneity and publication-bias results are unaffected."),
        }

    total = sum(d["downgrade"] for d in domains if d["downgrade"] is not None)
    start = _grade_index(initial)
    final_idx = min(len(GRADE_LEVELS) - 1, start + total)
    final = GRADE_LEVELS[final_idx]
    warnings: list[str] = []
    if not ind_assessed:
        warnings.append(
            "Indirectness was not assessed for this body of evidence; the "
            "certainty rating does not account for it and may overstate certainty.")
    caveat = (" Indirectness was not assessed; the rating may overstate certainty."
              if not ind_assessed else "")
    fired = [f"{d['domain'].lower()} (−{d['downgrade']}: {d['reason']})"
             for d in domains if d["downgrade"]]
    if fired:
        explanation = f"Initial certainty {initial}; downgraded {total} level(s) for " + "; ".join(fired) + f". Final certainty: {final}.{caveat}"
    else:
        explanation = f"Initial certainty {initial}; no serious concerns across the assessed GRADE domains. Final certainty: {final}.{caveat}"
    return {"initial": initial, "final": final, "status": "rated",
            "total_downgrade": total, "domains": domains,
            "warnings": warnings, "explanation": explanation}
