# Pooling (Meta-Analysis) — Sharable Methodology Reference

A self-contained reference for the **pooling agent** — the body-of-evidence engine that combines per-study effect sizes for **one outcome** into a pooled estimate, heterogeneity summary, and small-study/publication-bias tests. This is "Component B" of the evidence-synthesis pipeline: its outputs are the numeric inputs to a GRADE evidence profile (ASCO "Table 5" / Summary-of-Findings). Contains:

- The **per-study effect-size formulas** for every supported measure (OR / RR / IRR / RD / MD / SMD / HR), from raw arm data (2×2 counts, or mean/SD/N) and from a pre-computed estimate + CI.
- The **inverse-variance fixed- and random-effects pooling** math, with three **τ² (between-study variance) estimators** — DerSimonian-Laird, REML, and Paule-Mandel — as plain Python.
- The **heterogeneity summary** — Cochran's Q, I², H², τ², and the Higgins-Thompson **prediction interval**.
- The **small-study / publication-bias tests** — Egger's regression and Duval-Tweedie **trim-and-fill**.
- The **result schema** and the **GRADE hand-off contract** (raw numbers, no certainty decisions).
- The **extraction → pooled-outcome bridge** — how many studies' per-study extraction outputs are *absorbed and combined*: regrouped into bodies of evidence (one per outcome × comparison × timepoint × design-class), the measure chosen, unreconcilable studies excluded, and each body routed to the pooler. Includes the one **outcome-data extraction prompt** that pulls the raw arm numbers, and the **dual-mode** contract (extraction agent first, self-extract the gaps).
- **Outcome harmonization** — mapping differently-worded outcomes ("All-cause mortality" / "Death from any cause" / "Overall mortality") onto **one canonical outcome** before grouping, so synonyms pool together: a deterministic reviewer-dictionary/alias pass (pure) plus an LLM outcome-name **clustering** fallback (one batch call), with the exact prompt (§9.7).
- **Two reference implementations:** the production style (numpy + `scipy.stats`) and a **dependency-free variant** (χ²/Student-t computed from hand-rolled Numerical-Recipes shims) so a fork can run it even without scipy — plus plain-`assert` test sketches.

**Source.** Standard random-effects meta-analysis as codified in the Cochrane Handbook for Systematic Reviews of Interventions (ch. 10, "Analysing data and undertaking meta-analyses") and the GRADE handbook. Component-specific references: DerSimonian R, Laird N. *Meta-analysis in clinical trials.* Control Clin Trials 1986;7:177–188 (DL τ²). Viechtbauer W. *Bias and efficiency of meta-analytic variance estimators in the random-effects model.* J Educ Behav Stat 2005;30:261–293 (REML). Paule RC, Mandel J. *Consensus values and weighting factors.* J Res Natl Bur Stand 1982;87:377–385 (PM). Higgins JPT, Thompson SG, Spiegelhalter DJ. *A re-evaluation of random-effects meta-analysis.* J R Stat Soc A 2009;172:137–159 (prediction interval). Egger M, Davey Smith G, Schneider M, Minder C. *Bias in meta-analysis detected by a simple, graphical test.* BMJ 1997;315:629–634. Duval S, Tweedie R. *Trim and fill.* Biometrics 2000;56:455–463. Hedges LV, Olkin I. *Statistical Methods for Meta-Analysis.* 1985 (SMD bias correction).

**Scope.** This document covers **only the pooling step**: per-study effect sizes → one pooled estimate + heterogeneity + small-study tests, for a single outcome and a single body of evidence. Out of scope:

- **GRADE certainty rating** (the 5 downgrade + 3 upgrade domains), **absolute effects**, **baseline risk**, and **MIDs** — that is the separate **GRADE agent** (Component D). This module exposes a `grade_pooling_inputs()` hand-off of raw numbers but makes **no certainty decisions**.
- **Effect-size extraction** — the per-study numbers arrive from an upstream extraction / evidence-table agent (see the companion `table2_evidence_table_shareable.md`). The pooling **math never calls a model**; the only model touch in this agent is the optional *outcome-data extraction pass* (§9) that pulls raw arm numbers when they aren't already extracted. The assembly bridge and the pooling engine are pure.
- **Mixing designs.** Do **not** pool RCTs and non-randomized studies into one estimate — GRADE rates them as separate bodies of evidence. Call the pooler once per body.
- **Network / indirect comparisons**, multivariate/multiple-outcome pooling, individual-participant-data meta-analysis, and dose-response meta-regression.

**Interpretation callouts.**

- **Body-of-evidence, never single-paper.** A pooled estimate is computed *across* a set of studies; it is never read off one paper. Pooling is a body-of-evidence agent, structurally unlike per-study extraction.
- **Analysis scale vs natural scale.** Ratio measures (OR/RR/IRR/HR) are pooled on the **log** scale and back-transformed with `exp()` for display; difference measures (MD/SMD/RD) are pooled on the raw scale. τ², H², and Q always live on the **analysis (log) scale** — that is where between-study variance is defined — even when the point estimate and prediction interval are shown back-transformed.
- **Model-free math; one optional extraction prompt.** The pooling engine and the assembly bridge are deterministic arithmetic — no model. The agent's *only* LLM prompt is the optional outcome-data extraction pass (§9), which pulls raw arm numbers from a PDF when they weren't already extracted. If your studies already carry effect data (from Table 2 or elsewhere), the whole agent runs with zero model calls.
- **Libraries are allowed.** The production implementation uses **numpy** for the vectorized numeric core and **`scipy.stats`** for the χ² / Student-t / normal distribution functions (exact and well-tested). An agent here is not restricted to the standard library. For forks that cannot add scipy, §10 also ships a **dependency-free variant** with hand-rolled distribution shims — the two agree to plotting precision.
- **Conservative small-study handling.** Zero cells get the standard +0.5 continuity correction (RR/OR, and IRR from person-time); double-zero-event studies carry no information and are dropped. **IRR is never approximated from a 2×2 count table** — it requires person-time or a pre-computed estimate+CI (§2.2b), else the study is dropped with a named warning. Egger's test is flagged **underpowered below k = 10** per the GRADE publication-bias gate, and trim-and-fill is best-effort (Duval-Tweedie L0). These are surfaced, not silently applied.

---

## Revision notes

Substantive changes to the methodology in this document, newest-first, so downstream implementations (e.g. forks maintained by other teams) can see what changed and why. Cosmetic / wording-only edits are not logged.

### 2026-07-12 — Person-time in the outcome-data extraction prompt (IRR end-to-end)

**What changed.** The outcome-data extraction prompt (§9.4) now captures **person-time**: a new `outcome_type: "rate"`, the fields `time_int`/`time_ctrl` (arm person-time, e.g. person-years) added to the schema, and guidance that an incidence rate ratio needs person-time (events + an arm *size* is "binary", not "rate"). The bridge's study-input mapping (§9.3) is now **target-aware** — an IRR body pulls the person-time arms (`events_*`/`time_*`), never a 2×2; a raw person-time study with no reported metric **infers IRR**. Together with the prior IRR person-time engine change, this makes IRR pool **end-to-end from a self-extracted PDF**, not only from pre-computed IRR+CI.

**Why.** The previous change made the engine *reject* IRR-from-counts but left no way to *supply* person-time through extraction, so self-extracted IRR outcomes couldn't pool. This closes the loop.

**Impact.** Additive — new optional fields (`time_int`/`time_ctrl`, `outcome_type:"rate"`); no change to RR/OR/RD/MD/SMD/HR behaviour or to existing stored results. Studies already carrying person-time now pool as IRR instead of being dropped.

**Sections touched:** §9.3 (target-aware mapping + IRR inference), §9.4 (prompt: "rate" type, person-time fields).

### 2026-07-12 — IRR person-time, conservative unknown-design, direction propagation

**What changed.** Three correctness/robustness fixes from an implementation review, plus two doc caveats. (1) **IRR** is no longer computed from a 2×2 count table — it needs **person-time** (`time_int`/`time_ctrl`, alias `pyears_*`): `yᵢ=ln((a/T₁)/(c/T₂))`, `vᵢ=1/a+1/c`. A study requesting IRR from bare counts is dropped with a named warning; the pre-computed IRR+CI path is unaffected (§2.2b). Previously IRR reused the risk-ratio-from-counts formula, silently ignoring differential follow-up. (2) **Unknown study designs** (labels the mapper doesn't recognize) are kept in their own `unknown` body — never merged with an `rct`/`nrs` body — and the body result now carries a `warnings` entry naming the raw label. (3) **`favorable_direction`** is read from the outcome objects and propagated through `pool_body` into `pool_outcome` (was defaulting to `"lower"` at the bridge). Doc adds: an explicit IRR-needs-person-time caveat and a note that **comparison harmonization is lexical only** (unlike outcomes).

**Why.** Implementation-review feedback: IRR from counts is statistically wrong (person-time not modeled); unknown designs should be handled conservatively rather than silently pooled as a clean body; and per-outcome direction must survive the extraction→pool bridge for correct downstream interpretation.

**Impact.** IRR is the one behaviour change that can alter results: an IRR body previously (incorrectly) pooled from counts will now either use person-time / pre-computed CIs or drop those studies — re-run any IRR-from-counts pooling. RR/OR/RD/MD/SMD/HR pooling is unchanged. The unknown-design and direction changes are additive (new `warnings`/`favorable_direction` fields on the body result). New body-result keys: `favorable_direction`, `warnings`.

**Sections touched:** §2.2 (IRR removed from the 2×2 table), new §2.2b, §9.1–9.3 (unknown-design + direction), §10 turnkey (`_irr`), §11 tests, §12 platform notes, front-matter callout.

### 2026-07-12 — Added outcome harmonization (§9.7)

**What changed.** Added §9.7 "Outcome harmonization" — a layer that maps differently-worded outcomes onto one canonical outcome *before* grouping, so synonyms ("All-cause mortality" / "Death from any cause" / "Overall mortality") pool into one body instead of three. Two layered modes: a **deterministic reviewer-dictionary/alias pass** (pure, zero model calls — exact-normalized then conservative token-subset / Jaccard fuzzy matching, with a report of unmatched names) and an **LLM outcome-name clustering** fallback (one batch call over the distinct still-unresolved names, constrained to reviewer target labels, forbidden from merging distinct constructs). Grouping (§9.1) now keys on `canonical_outcome` when present. Wired into `pool_studies(outcome_targets=..., harmonize_llm=...)`. §9.2 updated to point at §9.7 for the synonym case; new §9.7 includes the clustering prompt + a pure dictionary-mode reference.

**Why.** The §9.1 grouping key is lexical (normalized name) — it merges case/punctuation variants but not synonyms, so the same clinical outcome worded differently across studies would not pool. Harmonization closes that gap while guarding against the opposite error (mis-merging genuinely different outcomes).

**Impact.** Additive and opt-in — with no `outcome_targets` and `harmonize_llm=False`, behaviour is unchanged (grouping on normalized verbatim names). New pure module `pooling_harmonize.py`; LLM clusterer in `pooling_extract.py`. No stored results affected.

**Sections touched:** front matter (Contains), §9.2 (synonym note), new §9.7.

### 2026-07-12 — Added the extraction → pooled-outcome bridge + dual-mode (§9)

**What changed.** Added §9 "From extraction to a pooled outcome (the assembly bridge)" documenting how many studies' per-study extraction outputs are absorbed and combined into pooled results: the `group_into_bodies` → `pool_body` → `pool_extractions` flow, the two enforced grouping rules (design-class separation so RCTs and non-randomized studies never share a body; single-measure-per-body with named exclusion of unreconcilable metrics), the raw-data-preferred study-input mapping, the one **outcome-data extraction prompt** that pulls raw arm numbers, and a pure-Python bridge reference implementation. Added §9.5 **dual-mode**: the pooling data comes from the extraction agent when present (zero model calls), and the pooling agent **self-extracts from the PDF as a fallback** when a study's extraction elements are missing (`study_is_poolable` gate → `prepare_study` → `pool_studies`), mirroring Table 2's injected-vs-isolation contract. Renumbered the old §9/§10/§11 (reference impl / tests / platform notes) to §10/§11/§12.

**Why.** The pooling engine pools one already-assembled body; nothing previously documented how per-study extractions get regrouped into bodies and routed to the pooler — the "combine them to pool" step — nor how the agent should behave when the extraction elements are unavailable. This closes both gaps.

**Impact.** Additive — no change to the pooling math, formulas, decision logic, result schema, or GRADE hand-off; no stored results affected. New modules `pooling_prep.py` (pure) + `pooling_extract.py` (dual-mode orchestrator + one outcome-data model call) in the production code.

**Sections touched:** front matter (Contains + two callouts + scope), new §9 (incl. §9.5 dual-mode), renumbered §10–§12.

### 2026-07-12 — Production module adopts numpy + scipy

**What changed.** The production module now uses **numpy** for the vectorized numeric core and **`scipy.stats`** for the χ² / Student-t / normal distribution functions, replacing the hand-rolled Numerical-Recipes shims. The methodology, formulas, decision logic, result schema, and GRADE hand-off are **unchanged** — this is an implementation/accuracy change only (e.g. the prediction-interval t-multiplier is now exact rather than a Cornish-Fisher approximation with ~3e-3 error at df=3). The stdlib shims are retained in §10 as a **dependency-free variant** for forks that cannot add scipy; the two agree to plotting precision.

**Why.** To lift an artificial "standard-library-only" constraint: an agent may use more than the stdlib, and exact, well-tested distribution functions are more trustworthy and maintainable than hand-rolled ones.

**Impact.** No change to any pooled estimate, heterogeneity value, or output shape beyond floating-point-level precision differences in tail p-values / the df=3 prediction interval. No stored results need re-running. Adds `numpy` + `scipy` to the platform's requirements.

**Sections touched:** front matter (Contains + callouts), §10 (intro + scipy mapping table + variant framing), §11.

### 2026-07-12 — Initial publication

Self-contained pooling (meta-analysis) methodology: per-study effect-size formulas for OR/RR/IRR/RD/MD/SMD/HR (from 2×2 counts, mean/SD/N, or pre-computed estimate+CI); inverse-variance fixed- and random-effects pooling with DL / REML / PM τ² estimators; the heterogeneity summary (Q, I², H², τ², prediction interval); Egger's regression and Duval-Tweedie trim-and-fill; the result schema and GRADE hand-off contract; a turnkey single-file Python reference implementation and plain-`assert` tests. The reference module's self-checks pass on CPython 3.12.

---

## 1. Core principle

One call to the pooler = **one outcome**, **one body of evidence**. It takes a list of per-study effect data and produces a single result object:

```
pool_outcome(studies, measure) -> {
    fixed / random pooled estimate (+ 95% CI, back-transformed for ratios),
    per-study weights,
    heterogeneity (Q, df, Q-p, I², H², τ², prediction interval),
    publication_bias (Egger, trim-and-fill),
    arm totals, warnings
}
```

Everything downstream — imprecision, inconsistency, publication-bias, large-effect upgrade, and absolute effects — reads off this object. The pooler makes **no GRADE decision**; it only computes the numbers GRADE consumes.

Two stages, both pure Python:

| Stage | Input | Output |
|---|---|---|
| **Effect size** | one study's raw arm data OR a pre-computed estimate+CI | `(yᵢ, vᵢ)` on the analysis scale |
| **Pool** | the set of `(yᵢ, vᵢ)` | pooled effect + heterogeneity + small-study tests |

---

## 2. Per-study effect size: `(yᵢ, vᵢ)`

`yᵢ` is the intervention-vs-comparator contrast on the analysis scale (log for ratios/HR, raw for differences); `vᵢ` is its variance. The pooler accepts three input shapes, in priority order.

### 2.1 Pre-computed (any measure, incl. HR)

If a study already reports `yᵢ` + `vᵢ`, they pass through. Otherwise, given a natural-scale `estimate` plus either `se` or `ci_lower` + `ci_upper`:

- **Ratio/HR (log scale):** `yᵢ = ln(estimate)`; if no SE, `se = (ln(ci_upper) − ln(ci_lower)) / (2·z)`; `vᵢ = se²`. (`estimate ≤ 0` is invalid.)
- **Difference (raw scale):** `yᵢ = estimate`; if no SE, `se = (ci_upper − ci_lower) / (2·z)`; `vᵢ = se²`.

`z = 1.959964` (two-sided 95% normal quantile). This is the only path for hazard ratios and for any study reporting just a headline effect + CI.

### 2.2 Binary 2×2 (OR / RR / RD)

Arms: intervention `a` events of `n₁`; comparator `c` events of `n₂`. Let `b = n₁ − a`, `d = n₂ − c`.

| Measure | `yᵢ` | `vᵢ` |
|---|---|---|
| **RR** | `ln((a/n₁)/(c/n₂))` | `1/a − 1/n₁ + 1/c − 1/n₂` |
| **OR** | `ln((a·d)/(b·c))` | `1/a + 1/b + 1/c + 1/d` |
| **RD** | `a/n₁ − c/n₂` | `a(n₁−a)/n₁³ + c(n₂−c)/n₂³` |

**Continuity correction.** If any cell is zero for RR/OR, add **0.5 to all four cells** (flag `continuity_correction_0.5`). **Double-zero drop:** a study with zero events in both arms (RR), or zero events in both arms *or* full events in both arms (OR), carries no information and is dropped. RD needs no correction.

> **⚠ IRR is NOT computed from a 2×2 count table.** An incidence rate ratio needs **person-time** denominators, not sample sizes — computing it from counts silently produces a risk ratio and ignores differential follow-up. IRR is therefore handled separately (§2.2b); a study requesting IRR from a bare 2×2 is **dropped with a named warning**, never approximated.

### 2.2b Incidence rate ratio (IRR — needs person-time)

Arms: `a` events over person-time `T₁` (`time_int`, alias `pyears_int`); `c` events over `T₂` (`time_ctrl`, alias `pyears_ctrl`).

```
yᵢ = ln( (a/T₁) / (c/T₂) )
vᵢ = 1/a + 1/c
```

Zero events get the +0.5 correction; double-zero-event studies are dropped. When a paper reports IRR + CI directly (the common case), it goes through the pre-computed path (§2.1) instead — no person-time needed there.

### 2.3 Continuous (MD / SMD)

Arms: `mean_int, sd_int, n₁` and `mean_ctrl, sd_ctrl, n₂`.

- **MD:** `yᵢ = m₁ − m₂`; `vᵢ = sd₁²/n₁ + sd₂²/n₂`.
- **SMD — Hedges' g** (small-sample-corrected Cohen's d):

  ```
  s_pooled = sqrt(((n₁−1)·sd₁² + (n₂−1)·sd₂²) / (n₁+n₂−2))
  d = (m₁ − m₂) / s_pooled
  J = 1 − 3 / (4·(n₁+n₂) − 9)          # Hedges bias correction
  g = J · d                              # yᵢ
  var_d = (n₁+n₂)/(n₁·n₂) + d²/(2·(n₁+n₂−2))
  vᵢ = J² · var_d
  ```

---

## 3. Inverse-variance pooling

### 3.1 Fixed effect (common effect)

```
wᵢ = 1/vᵢ
μ_FE = Σ wᵢ yᵢ / Σ wᵢ
var(μ_FE) = 1 / Σ wᵢ ,   se = sqrt(var)
```

### 3.2 Random effects

Weights incorporate the between-study variance τ² (§4):

```
wᵢ* = 1/(vᵢ + τ²)
μ_RE = Σ wᵢ* yᵢ / Σ wᵢ*
var(μ_RE) = 1 / Σ wᵢ* ,   se = sqrt(var)
```

95% CI on the analysis scale: `μ ± z·se`. For ratio/HR measures, back-transform the estimate and both CI limits with `exp()`. Per-study **weight %** is `100·wᵢ / Σwᵢ` from whichever model is chosen (`random` by default).

---

## 4. Between-study variance τ²

All three estimators clamp at 0 (a negative moment estimate means "no detectable heterogeneity" → random collapses to fixed).

### 4.1 DerSimonian-Laird (closed form)

```
Q  = Σ wᵢ (yᵢ − μ_FE)²          # wᵢ = 1/vᵢ  (fixed-effect weights)
df = k − 1
τ²_DL = max(0, (Q − df) / (Σwᵢ − Σwᵢ²/Σwᵢ))
```

### 4.2 REML (iterative, the default)

Fixed-point iteration, seeded from DL. With `wᵢ = 1/(vᵢ+τ²)` and `μ` the weighted mean:

```
τ²_new = [ Σ wᵢ² ((yᵢ − μ)² − vᵢ) ] / Σ wᵢ²  +  1 / Σ wᵢ
```

(The `Σwᵢ²((yᵢ−μ)²−vᵢ)/Σwᵢ²` term is the ML update; the `+ 1/Σwᵢ` correction is the REML adjustment for estimating μ.) Iterate to convergence; clamp at 0; fall back to DL if it fails to converge.

### 4.3 Paule-Mandel (iterative, empirical Bayes)

Solve, by bisection, the τ² for which the weighted residual sum equals the degrees of freedom:

```
Σ wᵢ (yᵢ − μ)² = k − 1 ,   with wᵢ = 1/(vᵢ+τ²), μ = weighted mean
```

If the statistic at τ²=0 is already ≤ 0, return 0.

---

## 5. Heterogeneity summary

```
Q      = Σ (1/vᵢ)(yᵢ − μ_FE)²
df     = k − 1
Q_p    = χ²-survival(Q, df)
I²     = max(0, (Q − df)/Q) · 100 %      # 0 if Q ≤ df
H²     = Q / df
τ²     = (§4)
```

**Prediction interval** (Higgins-Thompson; needs k ≥ 3), on the analysis scale, back-transformed for ratios:

```
PI = μ_RE ± t(0.975, k−2) · sqrt(τ² + var(μ_RE))
```

Uses the Student-t quantile with **k − 2** degrees of freedom (§0 shim).

---

## 6. Small-study / publication-bias tests

### 6.1 Egger's regression test

Regress the standard normal deviate on precision (unweighted OLS):

```
SNDᵢ = yᵢ / sᵢ ,   precisionᵢ = 1 / sᵢ ,   sᵢ = sqrt(vᵢ)
SNDᵢ = intercept + slope · precisionᵢ
```

The **intercept** measures funnel asymmetry; test it against 0 with a Student-t (df = k − 2). Returns `None` for k < 3 or when all variances are identical (precision has no spread → regression undefined). Flag `adequate_power = (k ≥ 10)` per the GRADE gate — the test is underpowered below 10 studies.

### 6.2 Duval-Tweedie trim-and-fill (L0)

1. Pool → μ. Rank the absolute residuals `|yᵢ − μ|`; the over-represented tail is the side with the larger signed-rank sum.
2. Estimate the number of suppressed studies with the L0 estimator: `L0 = round((4·Tₙ − n(n+1)) / (2n − 1))`, where `Tₙ` is the rank sum on the over-represented side; clamp to `[0, n−1]`.
3. Trim the `L0` most-extreme studies on that side, re-pool, re-estimate `L0`; iterate to a fixed point.
4. **Fill:** mirror the `L0` most-extreme studies about the trimmed μ (`y_fill = 2μ − yᵢ`, same `vᵢ`), re-pool the augmented set.

Returns the deficient side, `n_imputed`, and the adjusted random-effects estimate + CI (back-transformed for ratios in the assembled result). Best-effort; most informative at k ≥ 10.

---

## 7. Result schema

`pool_outcome(...)` returns:

```json
{
  "measure": "RR", "scale": "log", "model": "random",
  "outcome_name": null, "favorable_direction": "lower",
  "k": 4,
  "warnings": ["continuity correction (+0.5) applied: Smith 2019", "..."],
  "studies": [
    {"study_id": "...", "design": "...", "yi": -0.61, "vi": 0.05, "se": 0.22,
     "estimate": 0.54, "ci_lower": 0.35, "ci_upper": 0.84,
     "weight_pct": 27.3, "note": null}
  ],
  "fixed":  {"yi": -0.52, "se": 0.11, "z": -4.7, "p": 2.6e-6,
             "estimate": 0.59, "ci_lower": 0.48, "ci_upper": 0.74},
  "random": {"...": "...", "tau2": 0.206},
  "pooled": {"...": "the chosen model, duplicated for convenience"},
  "heterogeneity": {
    "k": 4, "q": 8.48, "df": 3, "q_p": 0.037,
    "i2": 64.6, "h2": 2.83, "tau2": 0.206, "tau": 0.454,
    "tau2_method": "REML", "tau2_dl": 0.19,
    "prediction_interval": {"lower": 0.06, "upper": 1.69, "t_df": 2}
  },
  "publication_bias": {
    "egger": {"intercept": 1.2, "se": 0.6, "t": 2.0, "df": 2, "p": 0.18,
              "k": 4, "adequate_power": false, "slope": -0.3},
    "trim_fill": {"side": "left", "n_imputed": 1,
                  "estimate": -0.7, "se": 0.2, "ci_lower": -1.1, "ci_upper": -0.3,
                  "adjusted_estimate": 0.50, "adjusted_ci_lower": 0.33, "adjusted_ci_upper": 0.74}
  },
  "totals": {"n_int": 360, "n_ctrl": 365, "events_int": 55, "events_ctrl": 91}
}
```

Studies without usable data are dropped from `k` and named in `warnings` (never silently discarded).

---

## 8. GRADE hand-off contract

`grade_pooling_inputs(result)` surfaces exactly the numbers the GRADE agent reads — **no decisions**:

| Field | Feeds GRADE domain |
|---|---|
| `pooled_estimate`, `ci_lower`, `ci_upper` | Imprecision; Large-effect upgrade; Absolute effects |
| `i2`, `tau2`, `q_p`, `prediction_interval` | Inconsistency |
| `total_n`, `events_int`, `events_ctrl` | Imprecision (OIS); Absolute effects |
| `egger_p`, `egger_adequate_power`, `trim_fill_n_imputed`, `trim_fill_adjusted_estimate` | Publication bias |

The GRADE agent applies thresholds and makes the up/down-grade calls itself. This keeps pooling and GRADE sharing one contract without pooling ever "deciding" certainty.

---

## 9. From extraction to a pooled outcome (the assembly bridge)

`pool_outcome` pools **one** already-assembled body. But extraction produces **many studies, each with many outcomes** — so something has to *absorb* those per-study outputs and *combine* them into bodies before pooling. That bridge (`pooling_prep.py`, pure Python) is what turns "a pile of extractions" into "a pooled result per outcome". The pooling data **comes from the extraction agent**; when a study's extraction elements are missing, the agent **self-extracts from the PDF** as a fallback (§9.5, dual-mode).

### 9.1 The flow

```
studies (each: study-level fields + outcomes[])
        │  group_into_bodies
        ▼
bodies  (one per  outcome × comparison × timepoint × design-class)
        │  pool_body  (choose measure → build inputs → pool_outcome)
        ▼
one pooled result per body   (+ measure, favorable_direction, excluded studies, warnings)
```

Each input study is the shape Table 2 / extraction already emit: study-level fields (`citation_authors`, `study_type`, `population_comparator`, …) plus an `outcomes` list, one object per (outcome × comparison × timepoint) carrying **either** raw arm data **or** a reported effect + CI + metric.

### 9.2 The two grouping rules (enforced, not left to the caller)

- **Never pool RCTs with non-randomized studies.** The body key includes a *design class* (`rct` / `nrs` / `unknown`); randomized and non-randomized designs land in separate bodies, because GRADE rates them as separate bodies of evidence. Design → class mapping is by study-type label (RCT / crossover / cluster → `rct`; cohort / case-control / single-arm / observational → `nrs`). A study-type label the mapper doesn't recognize is **`unknown`** — it is kept in its **own** body (never merged into an `rct` or `nrs` body) and the body result carries a `warnings` entry naming the raw label, so a reviewer knows its design provenance is unverified. Classify designs upstream to avoid `unknown` bodies.
- **Never pool unlike measures.** A body pools on **one** measure. It is chosen as: caller override → the **majority reported metric** among the body's outcomes → a default inferred from raw arm data (RR for binary, MD for continuous). A study whose reported metric differs from the body's measure **and** has no raw counts to recompute from is **excluded with a named reason** (e.g. `"Kim, 2022: reported OR, body pools RR"`) — never silently coerced. (Converting OR↔RR needs a baseline risk and is out of scope.)

Outcome names are matched after normalization (lowercase, punctuation-stripped), so "All-cause mortality", "all cause mortality", and "All cause Mortality" group together — but this is **lexical, not semantic**: synonyms worded differently ("Death from any cause", "Overall mortality") would land in separate bodies. The **harmonization layer (§9.7)** resolves that by mapping synonyms onto one canonical outcome before grouping.

> **⚠ Comparison harmonization is lexical only.** The `comparison` component of the key is normalized the same way but has **no synonym/harmonization layer** — "drug vs placebo" and "intervention vs control" would form separate bodies even for the same contrast. If your studies word the comparison differently, normalize it upstream in extraction (e.g. to a canonical "<intervention> vs <comparator>" string), or the §9.7 canonical-map mechanism can be applied to comparison strings by the caller. Only *outcomes* are harmonized automatically.

Timepoints are separate bodies by default (don't pool 6-month with 12-month); pass `include_timepoint=False` to pool across them. `favorable_direction` (the outcome's desirable direction — e.g. `"higher"` for survival, the default `"lower"` for adverse outcomes like mortality) is read from the outcome objects and **propagated through the body into `pool_outcome`**, so downstream direction interpretation is correct per outcome.

### 9.3 Study-input mapping (raw data wins)

For each outcome object, the bridge builds a `pool_outcome` input, **preferring raw arm data** and falling back to the reported `effect_estimate` + `ci_lower`/`ci_upper` only when its metric matches the body's measure. The raw-data choice is **target-aware**: a 2×2 body (OR/RR/RD) takes `events_int/n_int/events_ctrl/n_ctrl`; an **IRR body takes person-time** (`events_int/time_int/events_ctrl/time_ctrl`) — a 2×2 count table is *not* accepted for IRR (it can't make one), so a would-be IRR study with only counts is excluded with a person-time-naming reason; a continuous body takes `mean/SD/N`. With no reported metric, a raw-data study's measure is inferred (2×2 → RR, person-time → IRR, mean/SD → MD). This is why raw arm numbers are worth extracting even when a paper also prints an effect.

### 9.4 The outcome-data extraction prompt (the one model call)

Table 2's outcomes pass captures *reported* effects; pooling from scratch wants the **raw arm numbers**. This is the single model prompt in the pooling agent — one call per paper — that pulls them. `{placeholder}` markers are filled from any upstream study-level tags.

```
You are a clinical-evidence extraction service pulling the RAW ARM-LEVEL NUMBERS
needed to meta-analyse a study. You transcribe exactly what the paper reports and
never fabricate. You never pool, average, or compute a new effect.

For EACH distinct (outcome × comparison × timepoint) reported, emit one object with:
- name: the outcome name.
- comparison: the two arms compared, "<intervention> vs <comparator>".
- timing: the timepoint/follow-up, or null.
- design: the study design label (e.g. "Randomized Controlled Trial", "Cohort Study").
- outcome_type: "binary" (events/total), "rate" (events/person-time),
  "continuous" (mean/SD), or "other".
- BINARY outcomes — fill: events_int, n_int, events_ctrl, n_ctrl (integers). These are
  the number of events and the arm total in the intervention and comparator arms.
- RATE outcomes (an incidence rate / rate ratio, i.e. events per person-time) — fill:
  events_int, time_int, events_ctrl, time_ctrl, where time_* is the ARM'S PERSON-TIME
  (e.g. person-years, patient-years at risk) as reported. Only use "rate" when the
  paper gives a person-time denominator; if it reports events and an arm SIZE (not
  person-time), that is "binary" instead. An incidence rate ratio CANNOT be computed
  from counts alone, so person-time is required here.
- CONTINUOUS outcomes — fill: mean_int, sd_int, n_int, mean_ctrl, sd_ctrl, n_ctrl. If
  only an SE is reported, convert to SD = SE × sqrt(n) and note it; if only a CI is
  reported for the mean, leave sd null.
- effect_metric + effect_estimate + ci_lower + ci_upper + p_value + p_operator: the
  paper's REPORTED effect for this outcome, AS STATED, or null. effect_metric is one of
  HR, OR, RR, IRR, MD, SMD, RD, narrative. Preserve p inequalities (p<0.001 →
  p_value=0.001, p_operator="lt").
- source_quote: a verbatim span stating these numbers. If you cannot find one, omit the row.

Hard rules:
1. Report numbers AS STATED. Set any value you cannot find to null. NEVER derive a CI
   from a p, a p from a CI, or arm counts from a percentage unless the denominator is
   explicit.
2. Prefer raw arm data (counts / person-time / mean+SD+N). Also capture the reported
   effect when present.
3. One object per (outcome × comparison × timepoint). Do not merge timepoints or arms.
4. If the study is single-arm (no comparator), set comparator fields to null and
   design accordingly — such rows are not poolable but should still be reported.

Study context: {study_context}
Intervention arm(s): {intervention}
Comparator arm(s): {comparator}
Outcomes of interest (extract ALL reported; guidance, not a limit): {outcomes_of_interest}

Return ONLY a single JSON object of exactly this shape. No prose, no markdown:

{ "study_type": null, "citation_authors": null, "citation_year": null,
  "population_comparator": null,
  "outcomes": [ { "name": "", "comparison": "", "timing": null, "design": null,
    "outcome_type": "binary",
    "events_int": null, "n_int": null, "events_ctrl": null, "n_ctrl": null,
    "time_int": null, "time_ctrl": null,
    "mean_int": null, "sd_int": null, "mean_ctrl": null, "sd_ctrl": null,
    "effect_metric": null, "effect_estimate": null, "ci_lower": null, "ci_upper": null,
    "p_value": null, "p_operator": "eq", "source_quote": "" } ] }
```

### 9.5 Dual-mode: extraction agent first, self-extract the gaps

The pooling data **should come from the extraction agent** — when a study already carries poolable `outcomes[]`, pooling runs from them with **zero model calls**. But the pooling agent must not be stranded when those elements are missing: it **falls back to running the §9.4 extraction itself**. This is the same injected-vs-isolation contract Table 2 uses.

Per study, the orchestrator (`pool_studies`) decides:

| Study carries… | Action | Model calls |
|---|---|---|
| poolable `outcomes[]` (from the extraction agent) | use as-is | **0** |
| no poolable outcomes, but a `pdf_bytes` | self-extract via §9.4 (primed with any study-level tags), then use | 1 |
| neither | contributes nothing (surfaced, not fatal) | 0 |

A study is "poolable" when at least one of its outcomes yields a study input (raw arm data **or** a reported estimate) — `study_is_poolable`. `force_extract=True` re-extracts even when injected outcomes exist (e.g. to pull raw counts when only a headline effect was injected). So the end-to-end path is: **studies (mixed injected / PDF-only) → per-study prepare (inject-or-extract) → group into bodies → pool** — the extraction agent is the source of record, and the agent self-extracts only what's missing.

```python
def study_is_poolable(study):
    outs = study.get("outcomes")
    return isinstance(outs, list) and any(
        isinstance(oc, dict) and outcome_to_study_input(study, oc, None) is not None
        for oc in outs)

def prepare_study(item, extract_outcome_data, force_extract=False):
    """item: flat study dict, may carry outcomes[] and/or pdf_bytes.
    extract_outcome_data(pdf_bytes, injected=...) is the §9.4 model pass, injected."""
    study = {k: v for k, v in item.items() if k != "pdf_bytes"}
    if not force_extract and study_is_poolable(study):
        return study                                    # from the extraction agent — no model call
    if item.get("pdf_bytes"):
        priming = {k: v for k, v in study.items() if k != "outcomes"} or None
        return extract_outcome_data(item["pdf_bytes"], injected=priming)   # self-extract fallback
    return study

def pool_studies(items, extract_outcome_data, *, force_extract=False, **kw):
    studies = [prepare_study(it, extract_outcome_data, force_extract) for it in items]
    return pool_extractions(studies, **kw)
```

### 9.6 Bridge reference (pure Python, standard library)

```python
import re

_RANDOMIZED = {"randomized controlled trial", "rct", "crossover trial",
    "cross-over trial", "cluster randomized trial", "cluster randomised trial",
    "randomized trial", "randomised controlled trial"}
_NON_RANDOMIZED = {"cohort study", "cohort", "case-control", "case-control study",
    "non-randomized trial", "cross-sectional", "case-crossover", "single-arm trial",
    "dose-escalation study", "observational", "nrsi"}
_BIN = ("events_int", "n_int", "events_ctrl", "n_ctrl")
_CON = ("mean_int", "sd_int", "n_int", "mean_ctrl", "sd_ctrl", "n_ctrl")


def _norm(x):
    if x is None:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(x).lower()).strip())


def _design_class(design):
    d = _norm(design)
    if not d:
        return "unknown"
    if d in {_norm(x) for x in _RANDOMIZED} or "randomiz" in d or "randomis" in d:
        return "rct"
    if d in {_norm(x) for x in _NON_RANDOMIZED} or "cohort" in d or "observational" in d:
        return "nrs"
    return "unknown"


def _has(d, keys):
    return all(_num(d.get(k)) is not None for k in keys)


def _num(v):  # tolerant float
    try:
        return float(v) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _canon(measure):  # reuse the pooling reference's _canon / study_effect / pool_outcome
    return (measure or "").upper()


def outcome_to_study_input(study_level, oc, target_measure):
    base = {"study_id": study_level.get("study_id") or study_level.get("citation_authors"),
            "design": study_level.get("study_type") or study_level.get("design")}
    for src in (oc, study_level):
        if _has(src, _BIN):
            base.update({k: _num(src.get(k)) for k in _BIN}); return base
        if _has(src, _CON):
            base.update({k: _num(src.get(k)) for k in _CON}); return base
    metric = _canon(oc.get("effect_metric")); est = _num(oc.get("effect_estimate"))
    if est is None or not metric:
        return None
    if target_measure and metric != _canon(target_measure):
        return None
    base.update({"estimate": est, "ci_lower": _num(oc.get("ci_lower")),
                 "ci_upper": _num(oc.get("ci_upper")), "se": _num(oc.get("se"))})
    return base


def group_into_bodies(studies, include_timepoint=True):
    bodies = {}
    for s in studies:
        outs = s.get("outcomes")
        if not isinstance(outs, list):
            continue
        dcls = _design_class(s.get("study_type") or s.get("design"))
        for oc in outs:
            if not isinstance(oc, dict):
                continue
            name = oc.get("name") or oc.get("outcome_name")
            comp = oc.get("comparison") or s.get("population_comparator")
            tim = oc.get("timing") or oc.get("outcome_timing")
            key = (_norm(name), _norm(comp), _norm(tim) if include_timepoint else "", dcls)
            b = bodies.setdefault(key, {"outcome_name": name, "comparison": comp,
                "timepoint": tim, "design_class": dcls, "members": []})
            b["members"].append((s, oc))
    return list(bodies.values())


def _choose_measure(members, override):
    if override:
        return _canon(override)
    counts = {}
    for _s, oc in members:
        m = _canon(oc.get("effect_metric"))
        if m:
            counts[m] = counts.get(m, 0) + 1
    if counts:
        return max(counts.items(), key=lambda kv: kv[1])[0]
    for _s, oc in members:  # default from raw data
        for src in (oc, _s):
            if _has(src, _BIN):
                return "RR"
            if _has(src, _CON):
                return "MD"
    return None


def pool_body(body, measure=None, model="random", tau2_method="REML"):
    target = _choose_measure(body["members"], measure)
    inputs, excluded = [], []
    for s, oc in body["members"]:
        si = outcome_to_study_input(s, oc, target)
        label = s.get("study_id") or s.get("citation_authors")
        if si is None:
            m = _canon(oc.get("effect_metric"))
            excluded.append(f"{label}: reported {m}, body pools {target}" if m and target
                            else f"{label}: no poolable effect or arm data")
        else:
            inputs.append(si)
    out = {"outcome_name": body["outcome_name"], "comparison": body["comparison"],
           "timepoint": body["timepoint"], "design_class": body["design_class"],
           "measure": target, "k": len(inputs), "excluded": excluded, "pooled": None}
    if target and inputs:
        out["pooled"] = pool_outcome(inputs, target, model=model, tau2_method=tau2_method)
    return out


def pool_extractions(studies, measures=None, default_measure=None,
                     include_timepoint=True, min_studies=1, model="random", tau2_method="REML"):
    measures = measures or {}
    out = []
    for body in group_into_bodies(studies, include_timepoint):
        forced = measures.get(_norm(body["outcome_name"])) or default_measure
        res = pool_body(body, forced, model, tau2_method)
        if res["k"] >= min_studies:
            out.append(res)
    return out
```

### 9.7 Outcome harmonization (clustering synonyms into one canonical outcome)

The grouping key in §9.1 is the **normalized outcome name**, which merges case /
punctuation / whitespace variants but **not synonyms**: "All-cause mortality", "Death
from any cause", and "Overall mortality" produce three keys → three bodies, though a
reviewer would pool them. Harmonization is the layer that maps such names onto **one
canonical outcome** *before* grouping, by annotating each outcome object with a
`canonical_outcome` label (which §9.1's key-builder then prefers over the raw name).

It is deliberately **not** the extraction step and **not** an LLM free-for-all — mis-merging distinct outcomes (pooling "overall survival" with "progression-free survival") is a serious methodological error. So it is layered **deterministic-first**, mirroring the dual-mode philosophy:

| Mode | Driven by | Model calls | Authority |
|---|---|---|---|
| **Dictionary / alias** | reviewer-defined target outcomes + alias lists | **0** | authoritative, reproducible |
| **LLM clustering** | one batch call over the *distinct, still-unresolved* names | 1 | fallback for names no dictionary covered |

**Dictionary mode** (`harmonize_by_targets`, pure): the reviewer supplies
`[{"canonical": "...", "aliases": ["...", ...]}, ...]`; each extracted name is matched
to a canonical by exact-normalized lookup, then a **conservative** fuzzy match
(token-subset either way, or Jaccard ≥ 0.6). It only cleans variants — it will not
equate different constructs (that requires an explicit alias or the LLM). Unmatched
names keep their own raw name (still poolable among themselves) and are returned in a
**report** for reviewer inspection — never silently dropped.

**LLM mode** (`cluster_outcome_names_map`, one batch call): the *distinct* names
across all studies (not per study — one call for the whole review) are clustered into
canonical outcomes, constrained to the reviewer's target labels when supplied. The
prompt forbids merging genuinely different outcomes and puts uncertain names in their
own single-member cluster. Best-effort: on any failure it returns an empty map and
pooling falls back to raw names.

The two compose — dictionary first, LLM only for the gaps — via `pool_studies(...,
outcome_targets=[...], harmonize_llm=True)`. With neither, grouping uses normalized
verbatim names (the §9.1 default).

**The LLM clustering prompt** (system + user; `{names}` is the newline-joined distinct
names, `{targets_clause}` names the reviewer's canonical labels when provided):

```
[system] You harmonize outcome names from multiple studies in a systematic review.
You cluster names that denote the SAME clinical outcome and give each cluster one
canonical label. You never merge genuinely different outcomes.

[user]
Below are distinct outcome names extracted verbatim from different studies. Some are
the same outcome worded differently (e.g. "All-cause mortality", "Death from any
cause", "Overall mortality" are one outcome). Cluster names that refer to the SAME
outcome/construct and give each cluster a single canonical label.

Rules:
- Only merge names that measure the SAME thing. Do NOT merge different outcomes —
  e.g. "overall survival" vs "progression-free survival", or "systolic BP" vs
  "diastolic BP", are DIFFERENT and must stay in separate clusters.
- Preserve distinctions in what is measured (different scales/instruments for the
  same construct MAY cluster; different constructs may not).
- A name you are unsure about goes in its own single-member cluster.
- Every input name must appear in exactly one cluster's "members".
{targets_clause}
Outcome names:
{names}

Return ONLY a single JSON object of exactly this shape. No prose, no markdown:

{ "clusters": [ { "canonical": "", "members": ["", ""] } ] }
```

**Harmonization reference (pure — dictionary/alias + apply):**

```python
CANONICAL_KEY = "canonical_outcome"


def build_alias_index(targets):
    index = {}
    for t in targets or []:
        if isinstance(t, str):
            canon, aliases = t, []
        elif isinstance(t, dict):
            canon = t.get("canonical") or t.get("name") or t.get("outcome")
            aliases = t.get("aliases") or t.get("synonyms") or []
        else:
            continue
        if not canon:
            continue
        index[_norm(canon)] = canon
        for a in aliases:
            if a:
                index[_norm(a)] = canon
    return index


def match_outcome_name(name, index, fuzzy=True, min_jaccard=0.6):
    n = _norm(name)
    if not n:
        return None
    if n in index:
        return index[n]
    if not fuzzy:
        return None
    nt = set(n.split())
    best, best_score = None, 0.0
    for alias_norm, canon in index.items():
        at = set(alias_norm.split())
        inter = len(nt & at)
        if not at or inter == 0:
            continue
        subset = at <= nt or nt <= at
        jac = inter / len(nt | at)
        if subset or jac >= min_jaccard:
            score = 1.0 if subset else jac
            if score > best_score:
                best, best_score = canon, score
    return best


def apply_canonical_map(studies, name_to_canonical):
    out = []
    for s in studies:
        s2 = dict(s)
        if isinstance(s.get("outcomes"), list):
            new = []
            for oc in s["outcomes"]:
                if isinstance(oc, dict) and not oc.get(CANONICAL_KEY):
                    nm = oc.get("name") or oc.get("outcome_name")
                    canon = name_to_canonical.get(_norm(nm)) if nm else None
                    if canon:
                        oc = {**oc, CANONICAL_KEY: canon}
                new.append(oc)
            s2["outcomes"] = new
        out.append(s2)
    return out


def clusters_to_map(clusters):          # LLM output [{canonical, members}] -> name->canonical
    m = {}
    for cl in clusters or []:
        canon = cl.get("canonical")
        if not canon:
            continue
        for mem in cl.get("members") or []:
            if mem:
                m[_norm(mem)] = canon
        m.setdefault(_norm(canon), canon)
    return m


def harmonize_by_targets(studies, targets, fuzzy=True):
    index = build_alias_index(targets)
    names, mapping, report = {}, {}, []
    for s in studies:                    # distinct names (skip already-canonical)
        for oc in s.get("outcomes") or []:
            if isinstance(oc, dict) and not oc.get(CANONICAL_KEY):
                nm = oc.get("name") or oc.get("outcome_name")
                if nm:
                    names[nm] = names.get(nm, 0) + 1
    for nm, cnt in names.items():
        canon = match_outcome_name(nm, index, fuzzy)
        if canon:
            mapping[_norm(nm)] = canon
        report.append({"name": nm, "canonical": canon, "count": cnt})
    return apply_canonical_map(studies, mapping), report
```

Then grouping (§9.1) is unchanged except its key-builder reads
`oc.get("canonical_outcome") or oc.get("name")`.

---

## 10. Turnkey reference implementation

The **production** module (`backend/synthesis/pooling.py`) uses **numpy** for the numeric core and **`scipy.stats`** for the distribution functions — that is the recommended implementation on any stack that can `pip install numpy scipy`. The distribution calls map directly:

| Purpose | `scipy.stats` call |
|---|---|
| χ² upper tail (Cochran Q p) | `float(chi2.sf(Q, df))` |
| Student-t two-sided p (Egger) | `float(2 * t.sf(abs(t), df))` |
| Student-t quantile (prediction interval) | `float(t.ppf(0.975, df))` |
| Normal two-sided p (pooled z) | `float(2 * norm.sf(abs(z)))` |

Everything else (effect-size formulas, pooling, τ² estimators, trim-and-fill) is plain arithmetic — swap the per-study loops for numpy array ops as you like; the math is identical.

Below is the **dependency-free variant**: identical behaviour with the four distribution functions replaced by hand-rolled Numerical-Recipes shims (incomplete gamma / incomplete beta, Acklam normal quantile, Cornish-Fisher t-quantile). Use it when a fork cannot add scipy. Standard library only — copy this one block and run it.

```python
"""Pooling (meta-analysis) — dependency-free turnkey reference. Standard library only."""
from __future__ import annotations
import math
from typing import Any, Iterable, Optional

_Z = 1.959964  # two-sided 95% normal quantile
_LOG = frozenset({"OR", "RR", "IRR", "HR"})
_BINARY = frozenset({"OR", "RR", "RD"})   # IRR is NOT a 2x2 measure — see _irr
_CONT = frozenset({"MD", "SMD"})
_SYN = {"hazard ratio": "HR", "odds ratio": "OR", "risk ratio": "RR",
        "relative risk": "RR", "rate ratio": "IRR", "incidence rate ratio": "IRR",
        "mean difference": "MD", "standardized mean difference": "SMD",
        "standardised mean difference": "SMD", "risk difference": "RD"}


def _canon(measure: str) -> str:
    s = str(measure or "").strip()
    return _SYN.get(s.lower(), s.upper())


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


# --- special functions (no scipy) -----------------------------------------
def _inv_phi(p: float) -> Optional[float]:
    if p is None or p <= 0.0 or p >= 1.0:
        return None
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plo, phi = 0.02425, 1.0 - 0.02425
    if p < plo:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    if p <= phi:
        q = p - 0.5; r = q*q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
               (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1.0)
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
            ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)


def _gser(a, x):
    ap, s, dlt = a, 1.0/a, 1.0/a
    for _ in range(1000):
        ap += 1.0; dlt *= x/ap; s += dlt
        if abs(dlt) < abs(s)*1e-15:
            break
    return s*math.exp(-x + a*math.log(x) - math.lgamma(a))


def _gcf(a, x):
    tiny = 1e-30; b = x+1.0-a; c = 1.0/tiny; d = 1.0/b; h = d
    for i in range(1, 1000):
        an = -i*(i-a); b += 2.0; d = an*d+b
        if abs(d) < tiny:
            d = tiny
        c = b+an/c
        if abs(c) < tiny:
            c = tiny
        d = 1.0/d; dl = d*c; h *= dl
        if abs(dl-1.0) < 1e-15:
            break
    return math.exp(-x + a*math.log(x) - math.lgamma(a))*h


def _gammq(a, x):
    if x <= 0.0 or a <= 0.0:
        return 1.0
    return 1.0-_gser(a, x) if x < a+1.0 else _gcf(a, x)


def _chi2_sf(x, df):
    return 1.0 if (df <= 0 or x <= 0) else _gammq(df/2.0, x/2.0)


def _betacf(a, b, x):
    tiny = 1e-30; qab, qap, qam = a+b, a+1.0, a-1.0
    c = 1.0; d = 1.0-qab*x/qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0/d; h = d
    for m in range(1, 300):
        m2 = 2*m
        aa = m*(b-m)*x/((qam+m2)*(a+m2))
        d = 1.0+aa*d; d = tiny if abs(d) < tiny else d
        c = 1.0+aa/c; c = tiny if abs(c) < tiny else c
        d = 1.0/d; h *= d*c
        aa = -(a+m)*(qab+m)*x/((a+m2)*(qap+m2))
        d = 1.0+aa*d; d = tiny if abs(d) < tiny else d
        c = 1.0+aa/c; c = tiny if abs(c) < tiny else c
        d = 1.0/d; dl = d*c; h *= dl
        if abs(dl-1.0) < 1e-14:
            break
    return h


def _betai(a, b, x):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lb = math.lgamma(a+b)-math.lgamma(a)-math.lgamma(b)
    front = math.exp(lb + a*math.log(x) + b*math.log(1.0-x))
    return front*_betacf(a, b, x)/a if x < (a+1.0)/(a+b+2.0) \
        else 1.0-front*_betacf(b, a, 1.0-x)/b


def _t_sf2(t, df):
    t = abs(t)
    return _betai(df/2.0, 0.5, df/(df+t*t))


def _t_quantile(p, df):
    z = _inv_phi(p)
    if z is None:
        return float("nan")
    if not math.isfinite(df) or df > 1e6:
        return z
    g1 = (z**3+z)/4.0
    g2 = (5*z**5+16*z**3+3*z)/96.0
    g3 = (3*z**7+19*z**5+17*z**3-15*z)/384.0
    g4 = (79*z**9+776*z**7+1482*z**5-1920*z**3-945*z)/92160.0
    return z + g1/df + g2/df**2 + g3/df**3 + g4/df**4


def _ncdf(x):
    return 0.5*(1.0+math.erf(x/math.sqrt(2.0)))


def _order(lo, hi):
    return (hi, lo) if (lo is not None and hi is not None and lo > hi) else (lo, hi)


# --- effect size -----------------------------------------------------------
def study_effect(study: dict, measure: str) -> Optional[dict]:
    m = _canon(measure); is_log = m in _LOG
    yi, vi = _num(study.get("yi")), _num(study.get("vi"))
    if yi is not None and vi is not None and vi > 0:
        return _counts(study, yi, vi, study.get("note"))
    est = _num(study.get("estimate"))
    if est is not None:
        se = _num(study.get("se"))
        lo, hi = _order(_num(study.get("ci_lower")), _num(study.get("ci_upper")))
        if is_log:
            if est <= 0:
                return None
            t = math.log(est)
            if se is None and lo and hi and lo > 0 and hi > 0:
                se = (math.log(hi)-math.log(lo))/(2*_Z)
        else:
            t = est
            if se is None and lo is not None and hi is not None:
                se = (hi-lo)/(2*_Z)
        return _counts(study, t, se*se, study.get("note")) if (se and se > 0) else None
    if m in _BINARY:
        return _binary(study, m)
    if m == "IRR":
        return _irr(study)
    if m in _CONT:
        return _cont(study, m)
    return None


def _binary(s, m):
    a, n1, c, n2 = (_num(s.get(k)) for k in ("events_int", "n_int", "events_ctrl", "n_ctrl"))
    if None in (a, n1, c, n2) or n1 <= 0 or n2 <= 0 or a < 0 or c < 0 or a > n1 or c > n2:
        return None
    b, d = n1-a, n2-c; note = None
    if m == "RD":
        p1, p2 = a/n1, c/n2
        vi = p1*(1-p1)/n1 + p2*(1-p2)/n2 or 1.0/(n1+n2)
        return _counts(s, p1-p2, vi, None)
    if m == "RR" and a == 0 and c == 0:
        return None
    if m == "OR" and ((a == 0 and c == 0) or (b == 0 and d == 0)):
        return None
    if 0 in (a, b, c, d):
        a, b, c, d = a+0.5, b+0.5, c+0.5, d+0.5
        n1c, n2c = a+b, c+d; note = "continuity_correction_0.5"
    else:
        n1c, n2c = n1, n2
    if m == "RR":
        yi = math.log((a/n1c)/(c/n2c)); vi = 1/a-1/n1c+1/c-1/n2c
    else:
        yi = math.log((a*d)/(b*c)); vi = 1/a+1/b+1/c+1/d
    return _counts(s, yi, vi, note) if vi > 0 else None


def _irr(s):  # incidence rate ratio needs person-time, NOT a 2x2 count table
    a, c = _num(s.get("events_int")), _num(s.get("events_ctrl"))
    t1 = _num(s.get("time_int")) if s.get("time_int") is not None else _num(s.get("pyears_int"))
    t2 = _num(s.get("time_ctrl")) if s.get("time_ctrl") is not None else _num(s.get("pyears_ctrl"))
    if None in (a, c, t1, t2) or t1 <= 0 or t2 <= 0 or a < 0 or c < 0:
        return None
    if a == 0 and c == 0:
        return None
    note = None
    if a == 0 or c == 0:
        a, c = a+0.5, c+0.5; note = "continuity_correction_0.5"
    return _counts(s, math.log((a/t1)/(c/t2)), 1/a + 1/c, note)


def _cont(s, m):
    m1, s1, n1, m2, s2, n2 = (_num(s.get(k)) for k in
        ("mean_int", "sd_int", "n_int", "mean_ctrl", "sd_ctrl", "n_ctrl"))
    if None in (m1, s1, n1, m2, s2, n2) or n1 <= 1 or n2 <= 1 or s1 < 0 or s2 < 0:
        return None
    if m == "MD":
        vi = s1*s1/n1 + s2*s2/n2
        return _counts(s, m1-m2, vi, None) if vi > 0 else None
    df = n1+n2-2
    sp2 = ((n1-1)*s1*s1 + (n2-1)*s2*s2)/df
    if sp2 <= 0:
        return None
    d = (m1-m2)/math.sqrt(sp2)
    j = 1.0 - 3.0/(4.0*(n1+n2)-9.0); g = j*d
    vi = j*j*((n1+n2)/(n1*n2) + d*d/(2.0*df))
    return _counts(s, g, vi, "hedges_g") if vi > 0 else None


def _counts(s, yi, vi, note):
    return {"study_id": s.get("study_id") or s.get("id") or s.get("name"),
            "design": s.get("design") or s.get("study_type"),
            "n_int": _num(s.get("n_int")), "n_ctrl": _num(s.get("n_ctrl")),
            "events_int": _num(s.get("events_int")), "events_ctrl": _num(s.get("events_ctrl")),
            "yi": yi, "vi": vi, "note": note}


# --- pooling + tau2 --------------------------------------------------------
def _fe(y, v):
    w = [1.0/vi for vi in v]; sw = sum(w)
    est = sum(wi*yi for wi, yi in zip(w, y))/sw
    return {"estimate": est, "var": 1.0/sw, "se": math.sqrt(1.0/sw), "weights": w, "sum_w": sw}


def _re(y, v, tau2):
    w = [1.0/(vi+tau2) for vi in v]; sw = sum(w)
    est = sum(wi*yi for wi, yi in zip(w, y))/sw
    return {"estimate": est, "var": 1.0/sw, "se": math.sqrt(1.0/sw), "weights": w, "sum_w": sw}


def _Q(y, v, fe):
    return sum((1.0/vi)*(yi-fe)**2 for yi, vi in zip(y, v))


def tau2_dl(y, v):
    k = len(y)
    if k < 2:
        return 0.0
    fe = _fe(y, v); q = _Q(y, v, fe["estimate"])
    sw, sw2 = fe["sum_w"], sum(wi*wi for wi in fe["weights"])
    denom = sw - sw2/sw
    return max(0.0, (q-(k-1))/denom) if denom > 0 else 0.0


def tau2_reml(y, v, max_iter=200, tol=1e-7):
    k = len(y)
    if k < 2:
        return 0.0
    tau2 = tau2_dl(y, v)
    for _ in range(max_iter):
        w = [1.0/(vi+tau2) for vi in v]; sw = sum(w)
        mu = sum(wi*yi for wi, yi in zip(w, y))/sw
        sw2 = sum(wi*wi for wi in w)
        num = sum(wi*wi*((yi-mu)**2-vi) for wi, yi, vi in zip(w, y, v))
        nt = max(0.0, num/sw2 + 1.0/sw)
        if abs(nt-tau2) < tol:
            return nt
        tau2 = nt
    return tau2_dl(y, v)


def tau2_pm(y, v, max_iter=200, tol=1e-7):
    k = len(y)
    if k < 2:
        return 0.0

    def g(tau2):
        w = [1.0/(vi+tau2) for vi in v]; sw = sum(w)
        mu = sum(wi*yi for wi, yi in zip(w, y))/sw
        return sum(wi*(yi-mu)**2 for wi, yi in zip(w, y)) - (k-1)
    if g(0.0) <= 0:
        return 0.0
    lo, hi = 0.0, max(v)+1.0
    while g(hi) > 0 and hi < 1e12:
        hi *= 2.0
    for _ in range(max_iter):
        mid = 0.5*(lo+hi); gm = g(mid)
        if abs(gm) < tol:
            return mid
        lo, hi = (mid, hi) if gm > 0 else (lo, mid)
    return 0.5*(lo+hi)


_TAU2 = {"DL": tau2_dl, "REML": tau2_reml, "PM": tau2_pm}


# --- publication bias ------------------------------------------------------
def eggers_test(y, v):
    k = len(y)
    if k < 3:
        return None
    s = [math.sqrt(vi) for vi in v]
    x = [1.0/si for si in s]; yy = [yi/si for yi, si in zip(y, s)]
    n = float(k); mx = sum(x)/n; my = sum(yy)/n
    sxx = sum((xi-mx)**2 for xi in x)
    sxy = sum((xi-mx)*(yi-my) for xi, yi in zip(x, yy))
    if sxx <= 0:
        return None
    slope = sxy/sxx; icpt = my-slope*mx
    resid = [yi-(icpt+slope*xi) for xi, yi in zip(x, yy)]
    sig2 = sum(r*r for r in resid)/(k-2)
    se = math.sqrt(sig2*(1.0/n + mx*mx/sxx))
    if se <= 0:
        return None
    t = icpt/se
    return {"intercept": icpt, "se": se, "t": t, "df": k-2,
            "p": _t_sf2(t, k-2), "k": k, "adequate_power": k >= 10, "slope": slope}


def _signed_ranks(av):
    order = sorted(range(len(av)), key=lambda i: av[i]); r = [0.0]*len(av); i = 0
    while i < len(order):
        j = i
        while j+1 < len(order) and av[order[j+1]] == av[order[i]]:
            j += 1
        avg = (i+j)/2.0 + 1.0
        for t in range(i, j+1):
            r[order[t]] = avg
        i = j+1
    return r


def trim_and_fill(y, v, tau2_method="DL", max_iter=100):
    k = len(y)
    if k < 3:
        return None
    pairs = sorted(zip(y, v), key=lambda p: p[0])
    ys = [p[0] for p in pairs]; vs = [p[1] for p in pairs]
    est = _TAU2.get(tau2_method, tau2_dl)

    def pm(yy, vv):
        return _re(yy, vv, est(yy, vv))["estimate"]
    l0, n = 0, k; cy, cv = list(ys), list(vs); side = "left"
    for _ in range(max_iter):
        mu = pm(cy, cv); cen = [yi-mu for yi in cy]
        rk = _signed_ranks([abs(c) for c in cen])
        tr = sum(r for c, r in zip(cen, rk) if c > 0)
        tl = sum(r for c, r in zip(cen, rk) if c < 0)
        side, tn = ("left", tr) if tr >= tl else ("right", tl)
        nn = len(cy)
        ln = int(round((4.0*tn - nn*(nn+1))/(2.0*nn-1.0)))
        ln = max(0, min(ln, nn-1))
        if ln == l0:
            break
        l0 = ln
        idx = sorted(range(n), key=lambda i: ys[i])
        keep = idx[:n-l0] if side == "left" else idx[l0:]
        cy = [ys[i] for i in keep]; cv = [vs[i] for i in keep]
        if len(cy) < 2:
            break
    if l0 <= 0:
        re = _re(ys, vs, est(ys, vs))
        return {"side": None, "n_imputed": 0, "estimate": re["estimate"], "se": re["se"],
                "ci_lower": re["estimate"]-_Z*re["se"], "ci_upper": re["estimate"]+_Z*re["se"]}
    mu = pm(cy, cv); idx = sorted(range(n), key=lambda i: ys[i])
    ex = idx[n-l0:] if side == "left" else idx[:l0]
    ay = ys + [2.0*mu-ys[i] for i in ex]; av = vs + [vs[i] for i in ex]
    re = _re(ay, av, est(ay, av))
    return {"side": side, "n_imputed": l0, "estimate": re["estimate"], "se": re["se"],
            "ci_lower": re["estimate"]-_Z*re["se"], "ci_upper": re["estimate"]+_Z*re["se"]}


# --- top-level composer ----------------------------------------------------
def _block(t, se, is_log):
    lo, hi = t-_Z*se, t+_Z*se
    z = t/se if se > 0 else float("nan")
    p = 2.0*(1.0-_ncdf(abs(z))) if math.isfinite(z) else None
    return {"yi": t, "se": se, "z": z, "p": (min(max(p, 1e-300), 1.0) if p is not None else None),
            "estimate": math.exp(t) if is_log else t,
            "ci_lower": math.exp(lo) if is_log else lo,
            "ci_upper": math.exp(hi) if is_log else hi}


def pool_outcome(studies: Iterable[dict], measure: str, *, model="random",
                 tau2_method="REML", outcome_name=None, favorable_direction="lower"):
    m = _canon(measure); is_log = m in _LOG
    prep, warn = [], []
    for i, s in enumerate(studies):
        e = study_effect(s, m)
        lab = s.get("study_id") or s.get("id") or s.get("name") or f"study[{i}]"
        if e is None or not math.isfinite(e["yi"]) or not (e["vi"] > 0):
            warn.append(f"dropped (no usable {m} data): {lab}"); continue
        e["study_id"] = e.get("study_id") or lab
        if e.get("note") == "continuity_correction_0.5":
            warn.append(f"continuity correction (+0.5) applied: {e['study_id']}")
        prep.append(e)
    res = {"measure": m, "scale": "log" if is_log else "raw", "model": model,
           "outcome_name": outcome_name, "favorable_direction": favorable_direction,
           "k": len(prep), "warnings": warn, "studies": [], "fixed": None, "random": None,
           "pooled": None, "heterogeneity": None, "publication_bias": None,
           "totals": _totals(prep)}
    if not prep:
        res["warnings"].append("no poolable studies"); return res
    y = [e["yi"] for e in prep]; v = [e["vi"] for e in prep]
    fe = _fe(y, v)
    tau2 = _TAU2.get((tau2_method or "REML").upper(), tau2_reml)(y, v) if len(y) >= 2 else 0.0
    re = _re(y, v, tau2)
    res["fixed"] = _block(fe["estimate"], fe["se"], is_log)
    res["random"] = _block(re["estimate"], re["se"], is_log); res["random"]["tau2"] = tau2
    chosen = re if model == "random" else fe
    res["pooled"] = _block(chosen["estimate"], chosen["se"], is_log)
    sw = chosen["sum_w"]
    for e, w in zip(prep, chosen["weights"]):
        se = math.sqrt(e["vi"])
        res["studies"].append({"study_id": e["study_id"], "design": e.get("design"),
            "yi": e["yi"], "vi": e["vi"], "se": se,
            "estimate": math.exp(e["yi"]) if is_log else e["yi"],
            "ci_lower": math.exp(e["yi"]-_Z*se) if is_log else e["yi"]-_Z*se,
            "ci_upper": math.exp(e["yi"]+_Z*se) if is_log else e["yi"]+_Z*se,
            "weight_pct": 100.0*w/sw, "note": e.get("note")})
    df = len(y)-1; q = _Q(y, v, fe["estimate"])
    het = {"k": len(y), "q": q, "df": df, "q_p": _chi2_sf(q, df) if df > 0 else None,
           "i2": max(0.0, (q-df)/q)*100.0 if (df > 0 and q > 0) else 0.0,
           "h2": (q/df) if df > 0 else None, "tau2": tau2, "tau": math.sqrt(tau2),
           "tau2_method": (tau2_method or "REML").upper(), "tau2_dl": tau2_dl(y, v),
           "prediction_interval": None}
    if len(y) >= 3:
        t = _t_quantile(0.975, len(y)-2); sp = math.sqrt(tau2+re["var"])
        lo, hi = re["estimate"]-t*sp, re["estimate"]+t*sp
        het["prediction_interval"] = {
            "lower": math.exp(lo) if is_log else lo,
            "upper": math.exp(hi) if is_log else hi, "t_df": len(y)-2}
    res["heterogeneity"] = het
    tf = trim_and_fill(y, v, tau2_method=(tau2_method or "REML").upper())
    if tf is not None:
        tf = dict(tf)
        tf["adjusted_estimate"] = math.exp(tf["estimate"]) if is_log else tf["estimate"]
        tf["adjusted_ci_lower"] = math.exp(tf["ci_lower"]) if is_log else tf["ci_lower"]
        tf["adjusted_ci_upper"] = math.exp(tf["ci_upper"]) if is_log else tf["ci_upper"]
    res["publication_bias"] = {"egger": eggers_test(y, v), "trim_fill": tf}
    return res


def _totals(prep):
    def s(k):
        vals = [e[k] for e in prep if e.get(k) is not None]
        return sum(vals) if vals else None
    return {"n_int": s("n_int"), "n_ctrl": s("n_ctrl"),
            "events_int": s("events_int"), "events_ctrl": s("events_ctrl")}


def grade_pooling_inputs(result: dict) -> dict:
    p = result.get("pooled") or {}; h = result.get("heterogeneity") or {}
    pb = result.get("publication_bias") or {}; eg = pb.get("egger") or {}; tf = pb.get("trim_fill") or {}
    t = result.get("totals") or {}
    return {"k": result.get("k"), "measure": result.get("measure"),
            "pooled_estimate": p.get("estimate"), "ci_lower": p.get("ci_lower"),
            "ci_upper": p.get("ci_upper"), "i2": h.get("i2"), "tau2": h.get("tau2"),
            "q_p": h.get("q_p"), "prediction_interval": h.get("prediction_interval"),
            "total_n": t.get("n_int"), "events_int": t.get("events_int"),
            "events_ctrl": t.get("events_ctrl"), "egger_p": eg.get("p"),
            "egger_adequate_power": eg.get("adequate_power"),
            "trim_fill_n_imputed": tf.get("n_imputed"),
            "trim_fill_adjusted_estimate": tf.get("adjusted_estimate")}
```

---

## 11. Quick test sketches (plain `assert`, no framework)

```python
import math

# --- special functions vs reference tables ---
assert abs(_chi2_sf(3.841, 1) - 0.05) < 1e-3
assert abs(_t_sf2(2.776, 4) - 0.05) < 1e-3
assert abs(_t_quantile(0.975, 10) - 2.228) < 1e-3

# --- effect sizes ---
e = study_effect({"events_int": 15, "n_int": 100, "events_ctrl": 25, "n_ctrl": 100}, "OR")
assert abs(math.exp(e["yi"]) - 0.5294) < 1e-3 and abs(e["vi"] - 0.13176) < 1e-4
e = study_effect({"events_int": 15, "n_int": 100, "events_ctrl": 25, "n_ctrl": 100}, "RR")
assert abs(math.exp(e["yi"]) - 0.6) < 1e-6
e = study_effect({"mean_int": 10, "sd_int": 2, "n_int": 30,
                  "mean_ctrl": 8, "sd_ctrl": 2.5, "n_ctrl": 30}, "SMD")
assert abs(e["yi"] - 0.872) < 2e-3 and e["note"] == "hedges_g"

# --- IRR: from person-time only, never from a 2x2 count table ---
assert study_effect({"events_int": 10, "n_int": 100,                        # counts, no person-time
                     "events_ctrl": 20, "n_ctrl": 100}, "IRR") is None
e = study_effect({"events_int": 10, "time_int": 500,
                  "events_ctrl": 20, "time_ctrl": 480}, "IRR")               # IRR=0.48, vi=0.15
assert abs(math.exp(e["yi"]) - 0.48) < 1e-3 and abs(e["vi"] - 0.15) < 1e-6
assert study_effect({"events_int": 0, "n_int": 50, "events_ctrl": 0, "n_ctrl": 50}, "RR") is None  # double-zero
assert study_effect({"events_int": 0, "n_int": 50, "events_ctrl": 5, "n_ctrl": 50}, "OR")["note"] \
       == "continuity_correction_0.5"

# --- pooling: FE matches manual, weights sum to 100, tau2=0 when homogeneous ---
S = [{"yi": math.log(0.5), "vi": 0.1}, {"yi": math.log(0.8), "vi": 0.05}, {"yi": math.log(0.6), "vi": 0.08}]
r = pool_outcome(S, "OR", tau2_method="DL")
w = [1/0.1, 1/0.05, 1/0.08]; ylg = [math.log(0.5), math.log(0.8), math.log(0.6)]
assert abs(r["fixed"]["estimate"] - math.exp(sum(a*b for a, b in zip(w, ylg))/sum(w))) < 1e-6
assert abs(sum(s["weight_pct"] for s in r["studies"]) - 100.0) < 1e-6
assert r["heterogeneity"]["tau2"] == 0.0  # Q < df

# --- tau2 estimators agree in order of magnitude on a heterogeneous set ---
yh = [0.1, 0.9, 0.3, -0.2, 0.6]; vh = [0.05, 0.04, 0.06, 0.05, 0.03]
assert tau2_dl(yh, vh) > 0 and tau2_reml(yh, vh) > 0 and tau2_pm(yh, vh) > 0

# --- heterogeneous binary RR: Q / I2 ---
studies = [
    {"events_int": 12, "n_int": 100, "events_ctrl": 20, "n_ctrl": 100},
    {"events_int": 8,  "n_int": 80,  "events_ctrl": 25, "n_ctrl": 90},
    {"events_int": 30, "n_int": 120, "events_ctrl": 28, "n_ctrl": 110},
    {"events_int": 5,  "n_int": 60,  "events_ctrl": 18, "n_ctrl": 65}]
h = pool_outcome(studies, "RR", tau2_method="DL")["heterogeneity"]
assert abs(h["q"] - 8.477) < 0.05 and abs(h["i2"] - 64.6) < 1.0

# --- publication bias ---
ys = [-0.4, -0.2, 0.0, 0.2, 0.4]; vs = [0.02, 0.06, 0.1, 0.06, 0.02]
assert abs(eggers_test(ys, vs)["intercept"]) < 1e-6           # symmetric
assert trim_and_fill(ys, vs)["n_imputed"] == 0
ay = [0.1, 0.2, 0.25, 0.5, 0.7, 0.9]; av = [0.02, 0.03, 0.05, 0.1, 0.15, 0.2]
tf = trim_and_fill(ay, av)
assert tf["n_imputed"] >= 1 and tf["side"] == "left"

# --- extraction -> pool bridge (§9): design + measure separation ---
def _rct(a, oc):
    return {"citation_authors": a, "study_type": "RCT",
            "population_comparator": "placebo", "outcomes": [oc]}

studies = [
    _rct("Smith", {"name": "All-cause mortality", "comparison": "d vs p", "timing": "12m",
                   "events_int": 12, "n_int": 100, "events_ctrl": 20, "n_ctrl": 100}),
    _rct("Jones", {"name": "all cause mortality", "comparison": "d vs p", "timing": "12m",
                   "effect_metric": "RR", "effect_estimate": 0.6, "ci_lower": 0.4, "ci_upper": 0.9}),
    _rct("Kim",   {"name": "all-cause mortality", "comparison": "d vs p", "timing": "12m",
                   "effect_metric": "OR", "effect_estimate": 0.55}),  # excluded from RR body
    {"citation_authors": "Park", "study_type": "Cohort Study", "population_comparator": "placebo",
     "outcomes": [{"name": "All cause mortality", "comparison": "d vs p", "timing": "12m",
                   "effect_metric": "RR", "effect_estimate": 0.7}]},   # separate (nrs) body
]
bodies = pool_extractions(studies)
rct = [b for b in bodies if b["design_class"] == "rct"][0]
assert rct["measure"] == "RR" and rct["k"] == 2          # raw + reported RR; OR excluded
assert any("Kim" in e for e in rct["excluded"])
assert {b["design_class"] for b in bodies} == {"rct", "nrs"}  # designs never share a body

print("all pooling self-checks passed")
```

---

## 12. Implementation notes for other platforms

- **Libraries vs dependency-free.** The production module uses **numpy + `scipy.stats`** (the recommended path — exact, well-tested distributions). The §10 dependency-free variant reproduces the same numbers from stdlib shims: χ²/Student-t tails from the incomplete-gamma / incomplete-beta functions (Numerical Recipes `gammq` / `betai`), the normal quantile from Acklam's rational approximation, and the t-quantile from a Cornish-Fisher expansion (≈3e-3 error at df=3, negligible above). Both agree to plotting precision. Pick the variant your deployment allows — nothing else in the methodology changes.
- **Analysis scale vs display scale.** Keep pooling on the log scale for ratios; back-transform only for display. A common fork bug is pooling ratios on the raw scale (wrong) or reporting τ²/H²/Q on the natural scale (they are always analysis-scale). The reference keeps `yi`/`se`/`tau2` on the analysis scale and adds back-transformed `estimate`/`ci_*`/`prediction_interval` fields.
- **τ² default.** The reference defaults to **REML**; DL is the closed-form alternative and is always also reported as `heterogeneity.tau2_dl` for sensitivity. Cochrane historically used DL; REML/PM are less biased under heterogeneity. Pick per your review's SAP and record which you used.
- **Continuity correction is a modelling choice.** The +0.5 all-cells correction is the Cochrane default but distorts sparse-data OR/RR. For rare events prefer a Peto OR or a Mantel-Haenszel / exact method — not implemented here; document the deviation if you swap it in.
- **IRR requires person-time.** An incidence rate ratio is `(a/T₁)/(c/T₂)` with `var(log IRR)=1/a+1/c`; it is **not** a function of sample sizes. This engine computes IRR only from person-time (`time_int`/`time_ctrl`) or a pre-computed estimate+CI, and **drops** an IRR study given only a 2×2 count table (with a named warning) rather than silently returning a risk ratio. If your extraction can't capture person-time, prefer reporting IRR + CI directly.
- **Unknown study designs are quarantined, not guessed.** A design label the mapper doesn't recognize becomes an `unknown` body — never merged with an `rct` or `nrs` body — and is flagged in the body's `warnings`. This is deliberately conservative: mis-assigning a design corrupts both the pooling separation and the downstream GRADE starting certainty. Classify designs upstream.
- **Comparison strings are matched lexically only.** Only outcome names are harmonized (§9.7); the `comparison` key component is normalized but not synonym-mapped. Canonicalize comparison wording upstream if studies phrase the same contrast differently.
- **Trim-and-fill is best-effort.** The L0 estimator is sensitive to the pooling model used inside the loop and is unreliable under real heterogeneity; treat `n_imputed` as a sensitivity signal, not a correction. Egger's test is underpowered below k = 10 (the `adequate_power` flag encodes the GRADE gate).
- **One call per body of evidence.** Do not pass mixed RCT + non-randomized studies. GRADE rates them separately; the design split happens upstream (in extraction/classification), and you call the pooler once per body.
- **Separation of concerns.** This agent computes numbers only. Certainty rating, absolute effects, baseline risk, and MIDs belong to the downstream GRADE agent, which reads `grade_pooling_inputs(result)`. Keeping the boundary clean is what lets pooling be reused for non-GRADE displays (forest plots, effect tables).
