"""Deterministic R + Python code generation for the Synthesis Analysis tab.

For each meta-analysis calculation we emit a *runnable* R snippet (using the
``meta`` / ``metafor`` packages) and an equivalent Python snippet (numpy/scipy)
that reproduce the backend numbers. This is a transparency artifact — the code
is shown/downloaded, never executed in production (mirrors the Quality
Appraisal developer-view philosophy). No LLM; pure string templating.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from backend.exports import export_python_script, export_r_script

# meta() call configuration per measure.
_META_FN = {
    "MD": ("metacont", 'sm = "MD"'),
    "SMD": ("metacont", 'sm = "SMD", method.smd = "Hedges"'),
    "OR": ("metabin", 'sm = "OR"'),
    "RR": ("metabin", 'sm = "RR"'),
    "RD": ("metabin", 'sm = "RD"'),
    "ZCOR": ("metacor", 'sm = "ZCOR"'),
    "PLOGIT": ("metaprop", 'sm = "PLOGIT"'),
    "PFT": ("metaprop", 'sm = "PFT"'),
    "IRR": ("metarate", 'sm = "IRLN"'),
    "HR": ("metagen", 'sm = "HR"'),
}

R_PREAMBLE = [
    "# Requires: install.packages(c('meta', 'metafor'))",
    "library(meta)",
    "library(metafor)",
]
PY_PREAMBLE = [
    "# Requires: pip install numpy scipy",
    "import numpy as np",
    "from scipy import optimize, stats",
]


# --- small formatting helpers ---------------------------------------------

def _num(x: Any) -> str:
    if x is None or x == "":
        return "NA"
    try:
        return repr(float(x))
    except (TypeError, ValueError):
        return "NA"


def _se(vi: Any) -> Any:
    """Standard error from a sampling variance (for the metagen TE/seTE columns)."""
    try:
        v = float(vi)
        return v ** 0.5 if v > 0 else None
    except (TypeError, ValueError):
        return None


def _rvec(values: Sequence[Any]) -> str:
    return "c(" + ", ".join(_num(v) for v in values) + ")"


def _rstrvec(values: Sequence[Any]) -> str:
    return "c(" + ", ".join(json.dumps(str(v)) for v in values) + ")"


def _pynum(x: Any) -> str:
    if x is None or x == "":
        return "np.nan"
    try:
        return repr(float(x))
    except (TypeError, ValueError):
        return "np.nan"


def _pylist(values: Sequence[Any]) -> str:
    return "[" + ", ".join(_pynum(v) for v in values) + "]"


# --- R data frame per measure ---------------------------------------------

def _r_dataframe(measure: str, studies: list[dict]) -> str:
    labels = [s.get("label", f"Study {i+1}") for i, s in enumerate(studies)]
    raw = [s.get("raw", {}) for s in studies]
    cols = [f"  study = {_rstrvec(labels)}"]
    if measure in ("MD", "SMD"):
        cols += [
            f"  n.e = {_rvec([r.get('n1') for r in raw])}",
            f"  mean.e = {_rvec([r.get('m1') for r in raw])}",
            f"  sd.e = {_rvec([r.get('sd1') for r in raw])}",
            f"  n.c = {_rvec([r.get('n2') for r in raw])}",
            f"  mean.c = {_rvec([r.get('m2') for r in raw])}",
            f"  sd.c = {_rvec([r.get('sd2') for r in raw])}",
        ]
    elif measure in ("OR", "RR", "RD"):
        cols += [
            f"  event.e = {_rvec([r.get('events1', r.get('a')) for r in raw])}",
            f"  n.e = {_rvec([r.get('total1', r.get('n1')) for r in raw])}",
            f"  event.c = {_rvec([r.get('events2', r.get('c')) for r in raw])}",
            f"  n.c = {_rvec([r.get('total2', r.get('n2')) for r in raw])}",
        ]
    elif measure == "ZCOR":
        cols += [
            f"  cor = {_rvec([r.get('r') for r in raw])}",
            f"  n = {_rvec([r.get('n') for r in raw])}",
        ]
    elif measure in ("PLOGIT", "PFT"):
        cols += [
            f"  event = {_rvec([r.get('events') for r in raw])}",
            f"  n = {_rvec([r.get('n') for r in raw])}",
        ]
    elif measure == "IRR":
        cols += [
            f"  event = {_rvec([r.get('events') for r in raw])}",
            f"  time = {_rvec([r.get('person_time') for r in raw])}",
        ]
    elif measure == "HR":
        # Generic inverse-variance: metagen consumes the log-HR (TE) + its SE.
        cols += [
            f"  TE = {_rvec([s.get('yi') for s in studies])}",
            f"  seTE = {_rvec([_se(s.get('vi')) for s in studies])}",
        ]
    sub = [s.get("subgroup") for s in studies]
    if any(v is not None for v in sub):
        cols.append(f"  subgroup = {_rstrvec(sub)}")
    mod = [s.get("moderator") for s in studies]
    if any(v is not None for v in mod):
        cols.append(f"  moderator = {_rvec(mod)}")
    return "dat <- data.frame(\n" + ",\n".join(cols) + "\n)"


def _r_meta_call(measure: str, model: str, tau2_method: str, fe_method: str) -> str:
    fn, sm = _META_FN[measure]
    common = "TRUE" if model in ("fixed", "both") else "FALSE"
    random = "TRUE" if model in ("random", "both") else "FALSE"
    args = []
    if fn == "metacont":
        args = ["n.e = dat$n.e", "mean.e = dat$mean.e", "sd.e = dat$sd.e",
                "n.c = dat$n.c", "mean.c = dat$mean.c", "sd.c = dat$sd.c"]
    elif fn == "metabin":
        args = ["event.e = dat$event.e", "n.e = dat$n.e",
                "event.c = dat$event.c", "n.c = dat$n.c"]
        sm += ', method = "%s"' % ("MH" if fe_method.upper() == "MH" else "Inverse")
    elif fn == "metacor":
        args = ["cor = dat$cor", "n = dat$n"]
    elif fn == "metaprop":
        args = ["event = dat$event", "n = dat$n"]
    elif fn == "metarate":
        args = ["event = dat$event", "time = dat$time"]
    elif fn == "metagen":
        args = ["TE = dat$TE", "seTE = dat$seTE"]
    args += ["studlab = dat$study", sm,
             f'method.tau = "{tau2_method.upper()}"',
             f"common = {common}", f"random = {random}"]
    return f"m <- {fn}(\n  " + ",\n  ".join(args) + "\n)\nsummary(m)"


# --- Python reproduction ---------------------------------------------------

def _py_data_block(yis, vis, labels) -> str:
    return (f"yi = np.array({_pylist(yis)})   # effect sizes (analysis scale)\n"
            f"vi = np.array({_pylist(vis)})   # sampling variances\n"
            f"labels = {json.dumps([str(l) for l in labels])}")


_PY_HELPERS = '''def tau2_reml(yi, vi):
    def neg_ll(t2):
        w = 1.0 / (vi + t2)
        mu = (w * yi).sum() / w.sum()
        return 0.5 * (np.log(vi + t2).sum() + np.log(w.sum()) + (w * (yi - mu) ** 2).sum())
    r = optimize.minimize_scalar(neg_ll, bounds=(0, 10 * vi.max() + 10), method="bounded")
    return max(0.0, float(r.x))

def pool(yi, vi, tau2=0.0):
    w = 1.0 / (vi + tau2)
    est = (w * yi).sum() / w.sum()
    se = (1.0 / w.sum()) ** 0.5
    z = est / se
    return est, se, est - 1.96 * se, est + 1.96 * se, 2 * stats.norm.sf(abs(z))'''


# --- public API ------------------------------------------------------------

def code_blocks(outcome: dict, studies: list[dict]) -> list[dict[str, str]]:
    """Per-calculation {key, title, r_code, py_code} blocks for the Analysis tab.

    ``outcome`` has measure, model, tau2_method, fe_method, subgroup_field.
    ``studies`` is a list of {label, raw, yi, vi, subgroup, moderator}.
    """
    measure = outcome["effect_measure"]
    model = outcome.get("model_choice", "random")
    tau2_method = outcome.get("tau2_method", "REML")
    fe_method = outcome.get("fe_method") or ("MH" if measure in ("OR", "RR", "RD") else "IV")
    has_subgroup = any(s.get("subgroup") is not None for s in studies)
    has_moderator = any(s.get("moderator") is not None for s in studies)

    yis = [s.get("yi") for s in studies]
    vis = [s.get("vi") for s in studies]
    labels = [s.get("label", f"Study {i+1}") for i, s in enumerate(studies)]

    blocks: list[dict[str, str]] = []

    blocks.append({
        "key": "data", "title": "1. Study data",
        "r_code": _r_dataframe(measure, studies),
        "py_code": _py_data_block(yis, vis, labels),
    })

    blocks.append({
        "key": "model", "title": "2. Effect sizes + pooled model",
        "r_code": _r_meta_call(measure, model, tau2_method, fe_method),
        "py_code": (_PY_HELPERS + "\n\n"
                    f"tau2 = tau2_reml(yi, vi)   # method = {tau2_method}\n"
                    "fe = pool(yi, vi, 0.0)\n"
                    "re = pool(yi, vi, tau2)\n"
                    'print("Fixed:", fe)\nprint("Random:", re)'),
    })

    blocks.append({
        "key": "heterogeneity", "title": "3. Heterogeneity (Q, I², τ²)",
        "r_code": "# Q, I^2, tau^2 are in the model summary:\n"
                  'cat("Q =", m$Q, " df =", m$df.Q, " p =", m$pval.Q, "\\n")\n'
                  'cat("I^2 =", m$I2, " tau^2 =", m$tau2, "\\n")',
        "py_code": ("w = 1.0 / vi\nmu_fe = (w * yi).sum() / w.sum()\n"
                    "Q = (w * (yi - mu_fe) ** 2).sum()\ndf = len(yi) - 1\n"
                    "I2 = max(0.0, (Q - df) / Q) * 100\n"
                    'print(f"Q={Q:.3f} df={df} I2={I2:.1f}% tau2={tau2:.4f}")'),
    })

    blocks.append({
        "key": "forest", "title": "4. Forest plot",
        "r_code": "forest(m, layout = \"meta\", prediction = TRUE)",
        "py_code": "# Forest plots are rendered as SVG in the Synthesis UI from the\n"
                   "# per-study (es, ci_low, ci_high, weight) values above.",
    })

    blocks.append({
        "key": "publication_bias", "title": "5. Publication bias (Egger + trim-and-fill)",
        "r_code": ('metabias(m, method.bias = "linreg")   # Egger\'s test\n'
                   "tf <- trimfill(m)\nsummary(tf)"),
        "py_code": ("k = len(yi)\nsei = np.sqrt(vi)\n"
                    "snd = yi / sei\nprec = 1.0 / sei\n"
                    "X = np.column_stack([np.ones(k), prec])\n"
                    "beta, *_ = np.linalg.lstsq(X, snd, rcond=None)\n"
                    "resid = snd - X @ beta\n"
                    "se_int = np.sqrt(((resid @ resid) / (k - 2)) * np.linalg.inv(X.T @ X)[0, 0])\n"
                    "t = beta[0] / se_int\n"
                    'print("Egger intercept =", beta[0], " p =", 2 * stats.t.sf(abs(t), k - 2))'),
    })

    if has_subgroup:
        blocks.append({
            "key": "subgroup", "title": "6. Subgroup analysis",
            "r_code": "msub <- update(m, subgroup = dat$subgroup)\nsummary(msub)",
            "py_code": "# Subgroup pooling repeats the pool() above within each\n"
                       "# subgroup level; Q_between tests for differences.",
        })

    if has_moderator:
        blocks.append({
            "key": "meta_regression", "title": "7. Meta-regression",
            "r_code": "mreg <- metareg(m, ~ moderator)\nsummary(mreg)",
            "py_code": "# Mixed-effects WLS with weights 1/(vi+tau2); see\n"
                       "# synthesis_stats.meta_regression for the exact maths.",
        })

    blocks.append({
        "key": "leave_one_out", "title": "8. Sensitivity (leave-one-out)",
        "r_code": 'metainf(m, pooled = "random")',
        "py_code": ("for i in range(len(yi)):\n"
                    "    keep = np.arange(len(yi)) != i\n"
                    "    t2 = tau2_reml(yi[keep], vi[keep])\n"
                    "    print(labels[i], pool(yi[keep], vi[keep], t2)[0])"),
    })

    return blocks


def r_code_for(outcome: dict, studies: list[dict]) -> str:
    """Full runnable .R script (concatenated blocks under the meta/metafor preamble)."""
    blocks = [{"description": b["title"], "code": b["r_code"]} for b in code_blocks(outcome, studies)]
    title = f"Meta-analysis: {outcome.get('name', 'outcome')} ({outcome['effect_measure']})"
    return export_r_script(blocks, preamble=R_PREAMBLE, title=title)


def python_code_for(outcome: dict, studies: list[dict]) -> str:
    """Full runnable .py script (concatenated blocks under the numpy/scipy preamble)."""
    blocks = [{"description": b["title"], "code": b["py_code"]} for b in code_blocks(outcome, studies)]
    title = f"Meta-analysis: {outcome.get('name', 'outcome')} ({outcome['effect_measure']})"
    return export_python_script(blocks, preamble=PY_PREAMBLE, title=title)
