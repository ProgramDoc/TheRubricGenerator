# Synthesis — Systematic Review + Meta-Analysis — Sharable Methodology Reference

Contains: the full pair-wise meta-analysis methodology used by the Synthesis feature — the study-screening and effect-size-extraction LLM prompts, every effect-size formula (continuous, binary, time-to-event / hazard ratio, correlation, single-arm), the pooling models (inverse-variance fixed effect, Mantel-Haenszel, random effects with DerSimonian-Laird, REML, and Paule-Mandel τ²), heterogeneity statistics (Cochran's Q, I², τ², H, Q-profile CI), publication-bias methods (funnel data, Egger's regression test, Duval-Tweedie trim-and-fill), subgroup analysis + mixed-effects meta-regression, sensitivity analysis (leave-one-out + influence diagnostics), and the GRADE body-of-evidence certainty combiner — plus a turnkey single-file Python reference implementation and framework-free tests.

**Source.** Standard pair-wise meta-analysis methodology as described in:
- Borenstein M, Hedges LV, Higgins JPT, Rothstein HR. *Introduction to Meta-Analysis.* Wiley, 2009. (Effect sizes, inverse-variance pooling, Q/I²/τ².)
- DerSimonian R, Laird N. "Meta-analysis in clinical trials." *Control Clin Trials* 1986;7:177-188. (Random-effects τ².)
- Viechtbauer W. "Conducting meta-analyses in R with the metafor package." *J Stat Softw* 2010;36(3):1-48. (REML τ², Q-profile CI, influence diagnostics.)
- Higgins JPT, Thompson SG, Deeks JJ, Altman DG. "Measuring inconsistency in meta-analyses." *BMJ* 2003;327:557-560. (I².)
- Egger M, Davey Smith G, Schneider M, Minder C. "Bias in meta-analysis detected by a simple, graphical test." *BMJ* 1997;315:629-634. (Egger's test.)
- Duval S, Tweedie R. "Trim and fill: a simple funnel-plot-based method." *Biometrics* 2000;56:455-463. (Trim-and-fill.)
- Robins J, Breslow N, Greenland S. "Estimators of the Mantel-Haenszel variance…" *Biometrics* 1986;42:311-323. (M-H variance.)
- Schünemann H, Brożek J, Guyatt G, Oxman A (eds). *GRADE Handbook.* (Body-of-evidence certainty.)

**Scope.** This document covers **aggregate-data, pair-wise meta-analysis** of a single outcome at a time, where one row of data = one study × one outcome × one comparison × one timepoint. It covers screening uploaded full-text PDFs against PICO-derived eligibility, extracting the numeric data, computing per-study effect sizes, pooling, heterogeneity, publication bias, subgroup/meta-regression, sensitivity, and a body-of-evidence GRADE rating that consumes per-study risk-of-bias judgements produced by a *separate* RoB tool.

**Out of scope.** Network (multiple-treatments) meta-analysis; individual-participant-data (IPD) meta-analysis; diagnostic-test-accuracy meta-analysis (bivariate / HSROC); dose-response meta-analysis; automated literature *searching* / de-duplication (the PRISMA identification counts are entered by the reviewer, not derived); multi-reviewer double-screening with kappa; prospective power analysis. The risk-of-bias *instruments* themselves (RoB 2, ROBINS-I, etc.) are documented in their own shareable docs — this doc only specifies how a per-study RoB rating is folded into GRADE.

**Conservative-tree note.** Where a methodological choice is a matter of judgement (which τ² estimator, whether to apply Knapp-Hartung, the exact GRADE downgrade thresholds), this reference picks a defensible default and **states it inline**. Forkers may tune the thresholds; the formulas are canonical.

**Computation note (production integration).** In the production platform the numbers are computed **deterministically in pure Python (numpy/scipy)** — never in R — and the platform additionally *emits* runnable R (`meta`/`metafor`) and Python scripts as a reproducibility artifact. This document is the source of truth for the Python computation; the emitted R is a convenience and is not reproduced here.

---

## Revision notes

Substantive changes to the methodology in this document, newest-first, so downstream implementations (e.g. forks maintained by other teams) can see what changed and why. Cosmetic / wording-only edits are not logged.

### 2026-07-22 — Hazard ratio (time-to-event) + Paule-Mandel τ²

**What changed.** Added two engine capabilities. (1) **Hazard ratio (HR)** — a time-to-event effect measure pooled on the log scale (new §3.5). It is **never derived from a 2×2 table**; it takes a reported HR + 95% CI, a reported log-HR + SE, or a log-rank O-E + variance V (Peto). Wired end-to-end: measure metadata + effect-size dispatch, the extraction field spec (`hr`/`ci_lower`/`ci_upper`, plus `o_e`/`v`), the measure registry, and R code export via `metagen(TE, seTE, sm="HR")`. (2) **Paule-Mandel τ²** — a third between-study-variance estimator (new §5 subsection) selectable via `tau2_method="PM"` alongside DL and REML; solved by bisection of the generalized-Q estimating equation `Σ wᵢ(yᵢ−μ)² = k−1`.

**Why.** HR is the standard measure for survival/time-to-event outcomes (oncology, cardiology) and was previously unsupported. PM is less biased than DL under real heterogeneity and needs no likelihood optimizer, giving reviewers a robust third option.

**Impact.** Purely additive — no change to any existing measure, pooling result, or the DL/REML defaults. New optional fields (`tau2_PM` in the heterogeneity dict; the HR measure and its `o_e`/`v`/`loghr`/`se` inputs). No stored results are affected.

**Sections touched:** front matter (Contains), §3.5 (new), §5 (PM subsection), §10 reference implementation (`hazard_ratio`, `tau2_pm`), §11 tests.

### 2026-06-12 — Initial publication

**What changed.** First publication of the Synthesis meta-analysis methodology: screening + extraction prompts (§1–§2), effect-size formulas for all measures (§3), inverse-variance + Mantel-Haenszel + random-effects pooling (§4), heterogeneity (§5), publication bias (§6), subgroup + meta-regression (§7), sensitivity (§8), GRADE body-of-evidence combiner (§9), turnkey reference implementation (§10), and tests (§11).

**Why.** Synthesis complements per-paper Quality Appraisal: QA assesses one study at a time and explicitly defers the GRADE domains that need a *body* of evidence (inconsistency, publication bias). Synthesis pools the body of evidence and therefore completes the GRADE picture.

**Impact.** New methodology; nothing pre-existing changes. The per-study RoB rating consumed by §9 is produced by the platform's existing RoB tools (documented separately) — this doc does not alter them.

**Sections touched:** all (genesis).

---

## 1. Study screening

Screening decides whether an uploaded full-text article should be included in the review, judged against eligibility criteria derived from the review's PICO. Two LLM calls are involved.

### 1.1 Deriving eligibility criteria from PICO (one text-only call)

**System prompt:**

```
You are a systematic-review methodologist. Given a review's PICO, produce
concise, machine-checkable inclusion and exclusion criteria a screener can
apply to a full-text article. Be specific about population, intervention,
comparator, outcomes, and study design. Respond with JSON only.
```

**User prompt** (`{pico_json}` = the PICO as JSON):

```
Review PICO:
{pico_json}

Return JSON of this exact shape:
{
  "inclusion": [{"axis": "population|intervention|comparator|outcome|design|other", "criterion": "..."}],
  "exclusion": [{"axis": "...", "criterion": "..."}],
  "design_filter": ["Randomized Controlled Trial", "Cohort Study", ...]
}
design_filter lists the study-design classifications eligible for inclusion
(use the standard names; leave [] to accept any design).
```

### 1.2 Screening one paper (one call, PDF attached)

The study's design **classification is computed first** (by a separate classifier) and passed in, so the design filter is enforced **deterministically in code** — never trust the LLM to apply the design gate. If `design_filter` is non-empty and the study's type is not in it, the paper is excluded with reason `wrong study design` **without** an LLM call.

**Prompt** (`{criteria_json}`, `{study_type}`, `{pico_json}` interpolated):

```
You are screening a full-text article for inclusion in a systematic review.

Review PICO:
{pico_json}

Eligibility criteria:
{criteria_json}

This study was classified as: {study_type}.

Decide whether the study meets ALL inclusion criteria and none of the
exclusion criteria. Quote the article where possible. Return JSON:
{
  "decision": "include" | "exclude",
  "confidence": "high" | "moderate" | "low",
  "reason": "1-2 sentences citing the paper",
  "per_criterion": [{"axis": "population", "met": true, "evidence": "..."}],
  "prisma_exclusion_reason": one of [...] (only if excluded, else "")
}
```

`prisma_exclusion_reason` must be one of the canonical PRISMA-2020 categories:

```
wrong population, wrong intervention, wrong comparator, wrong outcome,
wrong study design, duplicate report, full text unavailable,
insufficient data, other
```

These categories feed the "records excluded with reasons" boxes of the PRISMA flow diagram directly.

---

## 2. Effect-size data extraction

One LLM call per (paper × outcome). The prompt is **measure-conditioned** — it asks only for the fields the chosen effect measure needs — but the JSON envelope is uniform. The governing principle is identical to QUADAS-3 estimate extraction: **one row = one clinical context = one comparison × one timepoint × one subgroup; group ALL the numbers for one context into ONE row; never split a single comparison across rows.**

**Prompt** (`{outcome_name}`, `{measure}`, `{measure_label}`, `{pico_json}`, `{field_spec}` interpolated):

```
Extract the numeric outcome data needed to compute a meta-analysis
effect size of type {measure} ({measure_label}) for the outcome: "{outcome_name}".

Review PICO (for arm identification):
{pico_json}

Rules:
- ONE row per distinct clinical context = one comparison x one timepoint
  x one subgroup. Group ALL the numbers for one context into ONE row; never
  split a single comparison across rows.
- Report the numbers verbatim as printed (do not recompute).
- Use the intervention arm as arm 1 and the comparator as arm 2.
- Leave a field null if the paper does not report it.

Return JSON:
{
  "data_points": [
    {
      "context_label": "e.g. 12-week HbA1c, drug vs placebo",
      "timepoint": "...", "subgroup": "overall", "comparison": "...",
      {field_spec},
      "source_quote": "verbatim numbers as printed",
      "extraction_confidence": "high" | "moderate" | "low"
    }
  ]
}
```

`{field_spec}` per measure:

| Measure | `field_spec` |
|---|---|
| `MD`, `SMD` | `"m1": intervention-arm mean, "sd1": its SD, "n1": its sample size, "m2": comparator-arm mean, "sd2": its SD, "n2": its sample size` |
| `OR`, `RR`, `RD` | `"events1": intervention-arm event count, "total1": intervention-arm total, "events2": comparator-arm event count, "total2": comparator-arm total` |
| `ZCOR` | `"r": Pearson correlation, "n": sample size` |
| `PLOGIT`, `PFT` | `"events": event count (single arm), "n": group total` |
| `IRR` | `"events": event count, "person_time": person-time at risk` |

A row is flagged `needs_review` (routed to a human before pooling) when any required numeric for the measure is missing.

---

## 3. Effect-size formulas

All functions return `(yi, vi)` where `yi` is on the **analysis scale** (log for OR/RR/IRR, Fisher-z for correlation, logit/arcsine for proportions, raw for MD/SMD/RD) and `vi` is its sampling variance. Ratio measures and transformed measures are back-transformed for display only.

### 3.1 Continuous

**Mean difference (MD):**

```
yi = m1 - m2
vi = sd1²/n1 + sd2²/n2
```

**Standardized mean difference — Hedges' g (SMD):**

```
df       = n1 + n2 - 2
sd_pooled = sqrt(((n1-1)·sd1² + (n2-1)·sd2²) / df)
d         = (m1 - m2) / sd_pooled
J         = 1 - 3 / (4·df - 1)          # small-sample bias correction
g         = J · d
var(d)    = (n1 + n2)/(n1·n2) + d² / (2·(n1 + n2))
vi        = J² · var(d)
yi        = g
```

### 3.2 Binary (2×2: a = events arm 1, n1 = total arm 1; c = events arm 2, n2 = total arm 2; b = n1−a, d = n2−c)

**Continuity correction:** if any of `a, b, c, d` is 0, add 0.5 to all four cells (flag `corrected`). A **double-zero** study (`a = c = 0`) carries no information for OR/RR and is dropped from the inverse-variance path (Mantel-Haenszel tolerates it with zero weight).

**Log odds ratio (OR):**

```
yi = ln((a·d) / (b·c))
vi = 1/a + 1/b + 1/c + 1/d
```

**Log risk ratio (RR):**

```
yi = ln((a/n1) / (c/n2))
vi = 1/a - 1/n1 + 1/c - 1/n2
```

**Risk difference (RD)** (no continuity correction):

```
p1 = a/n1 ;  p2 = c/n2
yi = p1 - p2
vi = p1·(1-p1)/n1 + p2·(1-p2)/n2
```

### 3.3 Correlation — Fisher z (ZCOR)

```
yi = atanh(r)                  # 0.5·ln((1+r)/(1-r)); clamp |r| < 1
vi = 1 / (n - 3)               # requires n > 3
```
Back-transform the pooled estimate and CI bounds with `tanh`.

### 3.4 Single-arm

**Proportion, logit (PLOGIT)** — continuity 0.5 when `events = 0` or `events = n`:

```
p  = events/n
yi = ln(p / (1 - p))
vi = 1/(n·p) + 1/(n·(1-p))
```
Back-transform with the inverse logit `1/(1+e^{-yi})`.

**Proportion, Freeman-Tukey double-arcsine (PFT):**

```
yi = 0.5·(asin(sqrt(e/(n+1))) + asin(sqrt((e+1)/(n+1))))
vi = 1 / (4n + 2)
```

**Incidence rate, log (IRR):**

```
yi = ln(events / person_time)
vi = 1 / events
```

### 3.5 Time-to-event — hazard ratio (HR)

A hazard ratio is pooled on the **log** scale (like OR/RR/IRR) and is **never derived from a 2×2 count table** — it needs the survival-analysis output. Three input forms, in priority order:

```
1. log-rank O-E + variance V (Peto):   yi = (O − E) / V ,      vi = 1 / V
2. reported log-HR + its SE:            yi = ln(HR)_reported ,  vi = SE²
3. reported HR + 95% CI:                yi = ln(HR) ,
                                        vi = ((ln(CI_upper) − ln(CI_lower)) / (2·z_{0.975}))²
```

Back-transform for display with `exp(yi)`; the no-effect value is 1. Most trials report form 3 (HR + CI); the extraction prompt asks for `hr`, `ci_lower`, `ci_upper` (and accepts `o_e`/`v` when a log-rank O-E is given instead).

---

## 4. Pooling

### 4.1 Inverse-variance (fixed effect, and random effect with additive τ²)

```
wᵢ        = 1 / (vᵢ + τ²)            # τ² = 0 for fixed effect
estimate  = Σ wᵢ·yᵢ / Σ wᵢ
SE        = sqrt(1 / Σ wᵢ)
CI        = estimate ± z_{1-α/2}·SE        # z = 1.959964 for 95%
weight_%ᵢ = wᵢ / Σ wᵢ · 100
```

**Knapp-Hartung adjustment (optional, random effects, k ≥ 2):** replace the Wald CI with a t-distribution interval that rescales the SE:

```
q  = Σ wᵢ·(yᵢ - estimate)² / (k - 1)
SE = sqrt(q / Σ wᵢ)
CI = estimate ± t_{1-α/2, k-1}·SE
```

### 4.2 Mantel-Haenszel (binary fixed effect; preferred over IV-FE for sparse data)

For each study `i` with `Nᵢ = aᵢ + bᵢ + cᵢ + dᵢ`:

**OR:** `OR_MH = Σ(aᵢdᵢ/Nᵢ) / Σ(bᵢcᵢ/Nᵢ)`; Robins-Breslow-Greenland variance of `ln(OR_MH)` with `R = Σ aᵢdᵢ/Nᵢ`, `S = Σ bᵢcᵢ/Nᵢ`, `Pᵢ = (aᵢ+dᵢ)/Nᵢ`, `Qᵢ = (bᵢ+cᵢ)/Nᵢ`:

```
Var(ln OR_MH) = Σ(Pᵢ·aᵢdᵢ/Nᵢ)/(2R²)
              + Σ(Pᵢ·bᵢcᵢ/Nᵢ + Qᵢ·aᵢdᵢ/Nᵢ)/(2RS)
              + Σ(Qᵢ·bᵢcᵢ/Nᵢ)/(2S²)
```

**RR:** `RR_MH = Σ(aᵢ·n2ᵢ/Nᵢ) / Σ(cᵢ·n1ᵢ/Nᵢ)` with `R = Σ aᵢn2ᵢ/Nᵢ`, `S = Σ cᵢn1ᵢ/Nᵢ`:

```
Var(ln RR_MH) = Σ((n1ᵢ·n2ᵢ·(aᵢ+cᵢ) - aᵢ·cᵢ·Nᵢ)/Nᵢ²) / (R·S)
```

**RD:** weights `wᵢ = n1ᵢ·n2ᵢ/Nᵢ`, `RD_MH = Σ wᵢ·RDᵢ / Σ wᵢ`; Greenland-Robins variance.

M-H tolerates single-zero cells without correction. It is the **fixed-effect headline for binary outcomes**; random effects always use the inverse-variance path with the per-study log-OR/RR variances of §3.2.

### 4.3 Random effects

Estimate τ² (§5), then re-pool by §4.1 with `wᵢ = 1/(vᵢ + τ²)`. With a single study (k = 1) the random-effects estimate equals the fixed-effect estimate.

---

## 5. Heterogeneity

Computed at the **fixed-effect** solution (`wᵢ = 1/vᵢ`, `μ_FE = Σwᵢyᵢ/Σwᵢ`):

```
Q   = Σ wᵢ·(yᵢ - μ_FE)²
df  = k - 1
I²  = max(0, (Q - df) / Q) · 100        # 0 when Q ≤ df
H   = sqrt(Q / df)
p_Q = 1 - χ²_cdf(Q, df)
```

**DerSimonian-Laird τ²:**

```
C       = Σ wᵢ - Σ wᵢ² / Σ wᵢ
τ²_DL   = max(0, (Q - df) / C)
```

**REML τ²** — maximize the restricted profile log-likelihood over τ² ≥ 0:

```
ℓ(τ²) = -½·[ Σ ln(vᵢ + τ²) + ln(Σ 1/(vᵢ + τ²)) + Σ (yᵢ - μ̂(τ²))²/(vᵢ + τ²) ]
        where μ̂(τ²) = Σ wᵢyᵢ / Σ wᵢ , wᵢ = 1/(vᵢ + τ²)
```
maximized with a bounded 1-D optimizer on `[0, 10·max(vᵢ) + 10]`.

**Paule-Mandel τ²** (empirical Bayes) — solve the generalized-Q estimating equation for the τ² at which the weighted residual sum equals the degrees of freedom:

```
find τ² ≥ 0  such that  Σ wᵢ(τ²)·(yᵢ - μ̂(τ²))² = k - 1 ,   wᵢ(τ²) = 1/(vᵢ + τ²)
```
The left side is monotone-decreasing in τ², so bisect on `[0, large]`; if it is already ≤ k−1 at τ²=0, return 0. PM is less biased than DL under real heterogeneity and, unlike REML, needs no likelihood optimizer. All three estimators are exposed (`tau2_DL` / `tau2_REML` / `tau2_PM`); the pooling model selects one via `tau2_method ∈ {DL, REML, PM}` (REML default).

**Q-profile CI for τ²** (Viechtbauer) — the generalized Q statistic `Q_gen(τ²) = Σ wᵢ(τ²)·(yᵢ - μ̂(τ²))²` is monotone-decreasing in τ²; solve `Q_gen(τ²) = χ²_{1-α/2, df}` for the lower bound and `Q_gen(τ²) = χ²_{α/2, df}` for the upper bound (root-find on `[0, large]`).

**Edge cases:** `k = 1` → heterogeneity is not assessable (return a status flag, τ² = 0). `k = 2` → compute but flag `low_power` (I² is very unstable; skip Egger/trim-and-fill). Negative τ² is clamped to 0.

I² thresholds (Higgins 2003): 25% / 50% / 75% ≈ low / moderate / high.

---

## 6. Publication bias (require k ≥ 3; flag `underpowered` when k < 10)

**Funnel data:** per study `(yᵢ, seᵢ)` with `seᵢ = sqrt(vᵢ)`, plotted as effect (x) vs SE (y, inverted). The 95%/99% pseudo-confidence funnel is `pooled ± 1.96·se` and `± 2.576·se`.

**Egger's regression test:** regress the standard normal deviate `SNDᵢ = yᵢ/seᵢ` on precision `1/seᵢ` by OLS. The **intercept** tests for small-study effects (0 under symmetry):

```
X = [1, 1/seᵢ] ;  fit SNDᵢ = β₀ + β₁·(1/seᵢ)
t  = β₀ / SE(β₀) ;  p = 2·(1 - t_cdf(|t|, k-2))
```

**Duval-Tweedie trim-and-fill (L0 estimator):**
1. Estimate the pooled effect `μ` (random effects).
2. Determine the suppressed side: with `Tn` = sum of ranks of `|yᵢ - μ|` over the studies *above* μ, if `Tn > k(k+1)/4` the right side is over-represented → studies are missing on the **left** (and vice versa). Orient so the over-represented (to-be-trimmed) side is positive.
3. Iterate: trim the `L` most extreme positive studies, re-estimate `μ`, recompute `L = round((4·Tn − k(k+1)) / (2k − 1))` on the trimmed set, until `L` stabilizes.
4. Fill: reflect the `L` trimmed studies about `μ` (same variances), add them, and re-pool → the **adjusted estimate** and `n_imputed = L`.

---

## 7. Subgroup analysis + meta-regression

**Subgroup analysis:** pool within each subgroup level (random or fixed), then a **test for subgroup differences** using the fixed-effect pooled subgroup estimates `μ_g` with weights `W_g = Σ_{i∈g} 1/vᵢ`:

```
grand     = Σ W_g·μ_g / Σ W_g
Q_between = Σ W_g·(μ_g - grand)²
df        = G - 1
p         = 1 - χ²_cdf(Q_between, df)
```

**Mixed-effects meta-regression:** weighted least squares with weights `wᵢ = 1/(vᵢ + τ²)`, where τ² is the *residual* heterogeneity (re-estimated by REML on the model residuals, iterated to convergence). With design matrix `X` (intercept + moderators):

```
β       = (XᵀWX)⁻¹ XᵀW y
cov(β)  = (XᵀWX)⁻¹
z_j     = β_j / sqrt(cov[j,j]) ;  p_j = 2·(1 - Φ(|z_j|))
QE      = residual·diag(1/vᵢ)·residual    (test for residual heterogeneity, df = k-p)
QM      = β_mods·cov_mods⁻¹·β_mods         (omnibus test of moderators, df = p-1)
R²      = max(0, (τ²_total - τ²_resid) / τ²_total)·100
```

---

## 8. Sensitivity analysis (require k ≥ 3)

**Leave-one-out:** for each study, re-pool with that study omitted (re-estimating τ²); report the omitted-study estimate, CI, I², τ².

**Influence diagnostics** (intercept-only random-effects model; `wᵢ` at the full-data τ²):

```
hatᵢ        = wᵢ / Σ wⱼ                         # leverage
std_residᵢ  = (yᵢ - μ_full) / sqrt(vᵢ + τ²_full)
DFFITSᵢ     = (μ_full - μ_{(-i)}) / SE_{(-i)}    # standardized fitted-value change
Cook's Dᵢ   = (μ_full - μ_{(-i)})² / SE_full²
influentialᵢ = |DFFITSᵢ| > 3·sqrt(1/(k-1))  OR  |std_residᵢ| > 1.96
```
(`μ_{(-i)}`, `SE_{(-i)}`, `τ²_{(-i)}` come from the leave-one-out refits.)

---

## 9. GRADE — body-of-evidence certainty

GRADE rates certainty for a **body of evidence** on a four-level scale `["High", "Moderate", "Low", "Very low"]`. The initial level is the modal included design's default (RCT body → High; non-randomized → Low; uncontrolled single-arm → Very low). Five domains can downgrade; the total downgrade is summed and the final level is `initial + total` clamped at "Very low".

This is the meta-analysis-level GRADE — distinct from a per-study GRADE — because it consumes the *pooled* heterogeneity, imprecision, and funnel asymmetry that only exist for a body of evidence.

**Per-study RoB label → severity (0/1/2)** — mirrors the downgrade vocabulary of the RoB instruments:

```
low / low (except …)                       → 0
some concerns / moderate / no information /
  insufficient information / unclear        → 1
high / serious / critical                   → 2
```

**Risk of bias across studies** (weighted by pooled weight `w`, normalized): with `frac_serious` = weight share in severity-≥2 studies and `frac_some` = weight share in severity-≥1 studies:

```
frac_serious ≥ 0.50                         → downgrade 2
frac_serious ≥ 0.25  OR  frac_some ≥ 0.50   → downgrade 1
otherwise                                    → 0
```

**Inconsistency** (from I² and the Q p-value, unless explained by a significant subgroup test):

```
subgroup test p < 0.05                       → 0 (explained)
I² > 75% and p_Q < 0.10                       → 1 (considerable)
I² > 50% and p_Q < 0.10                       → 1 (substantial)
otherwise                                     → 0
```

**Imprecision** (from the pooled CI vs MID thresholds + an optimal-information-size heuristic). With optional MID-benefit/MID-harm on the display scale, and `OIS_fail` = total N below ~300 (binary) / ~400 (continuous):

```
MIDs supplied and CI spans both MIDs          → 2
CI crosses the line of no effect, OIS fails    → 2
CI crosses the line of no effect               → 1
OIS fails                                      → 1
otherwise                                      → 0
```

**Indirectness** — a review-level judgement (0 by default; raised when the as-conducted PICO is narrower than the review question). Passed in as a level (0–3).

**Publication bias:**

```
Egger p < 0.10                                → 1
trim-and-fill imputed ≥ 2 studies              → 1
otherwise                                      → 0
```

```
final_index = min(3, index(initial) + Σ downgrades)
final       = ["High","Moderate","Low","Very low"][final_index]
```

---

## 10. Reference implementation (single self-contained file)

Pure `numpy`/`scipy`; no framework imports. The two LLM steps take an injected `llm_call(prompt, pdf_bytes=None, system="") -> dict` (you supply the model + JSON parsing). Everything else is deterministic and runs offline.

```python
"""meta_analysis.py — self-contained pair-wise meta-analysis engine.
Dependencies: numpy, scipy. LLM calls are injected; the maths is offline.
"""
import math
import numpy as np
from scipy import optimize, stats

Z95 = float(stats.norm.ppf(0.975))
LOG_MEASURES = {"OR", "RR", "IRR", "HR"}

# ── 3. effect sizes ────────────────────────────────────────────────────────
def smd_hedges_g(m1, sd1, n1, m2, sd2, n2):
    df = n1 + n2 - 2
    if df <= 0: return None
    sp2 = ((n1 - 1) * sd1**2 + (n2 - 1) * sd2**2) / df
    if sp2 <= 0: return None
    d = (m1 - m2) / math.sqrt(sp2)
    J = 1 - 3 / (4 * df - 1)
    vd = (n1 + n2) / (n1 * n2) + d**2 / (2 * (n1 + n2))
    return J * d, J**2 * vd

def md(m1, sd1, n1, m2, sd2, n2):
    vi = sd1**2 / n1 + sd2**2 / n2
    return (m1 - m2, vi) if vi > 0 else None

def _cc(a, b, c, d, k=0.5):
    return (a+k, b+k, c+k, d+k, True) if min(a, b, c, d) == 0 else (a, b, c, d, False)

def log_or(e1, t1, e2, t2, k=0.5):
    a, c = e1, e2; b, d = t1 - e1, t2 - e2
    if a == 0 and c == 0: return None
    a, b, c, d, _ = _cc(a, b, c, d, k)
    return math.log((a*d)/(b*c)), 1/a + 1/b + 1/c + 1/d

def log_rr(e1, t1, e2, t2, k=0.5):
    a, c = e1, e2; b, d = t1 - e1, t2 - e2
    if a == 0 and c == 0: return None
    a, b, c, d, _ = _cc(a, b, c, d, k)
    n1c, n2c = a + b, c + d
    return math.log((a/n1c)/(c/n2c)), 1/a - 1/n1c + 1/c - 1/n2c

def risk_difference(e1, t1, e2, t2):
    p1, p2 = e1/t1, e2/t2
    return p1 - p2, p1*(1-p1)/t1 + p2*(1-p2)/t2

def fisher_z(r, n):
    if n <= 3: return None
    r = max(-0.999999, min(0.999999, r))
    return math.atanh(r), 1.0/(n-3)

def proportion_logit(e, n, k=0.5):
    if e == 0 or e == n: e += k; n += 2*k
    p = e/n
    return math.log(p/(1-p)), 1/(n*p) + 1/(n*(1-p))

def proportion_ft(e, n):
    return 0.5*(math.asin(math.sqrt(e/(n+1))) + math.asin(math.sqrt((e+1)/(n+1)))), 1/(4*n+2)

def incidence_rate_log(e, pt):
    return math.log(e/pt), 1.0/e

def hazard_ratio(hr=None, ci_lower=None, ci_upper=None, loghr=None, se=None, o_e=None, v=None):
    if o_e is not None and v is not None and v > 0:      # log-rank Peto
        return o_e / v, 1.0 / v
    if loghr is not None and se is not None and se > 0:  # reported log-HR + SE
        return loghr, se**2
    if hr and ci_lower and ci_upper and hr > 0 and ci_lower > 0 and ci_upper > 0:
        lo, hi = sorted((ci_lower, ci_upper))
        s = (math.log(hi) - math.log(lo)) / (2*Z95)
        return (math.log(hr), s**2) if s > 0 else None
    return None

def back_transform(yi, measure):
    if measure in LOG_MEASURES: return math.exp(yi)
    if measure == "PLOGIT": return 1/(1+math.exp(-yi))
    if measure == "ZCOR": return math.tanh(yi)
    if measure == "PFT": return math.sin(yi)**2
    return yi

# ── 5. heterogeneity ───────────────────────────────────────────────────────
def _q(yi, vi):
    w = 1/vi; mu = (w*yi).sum()/w.sum()
    return float((w*(yi-mu)**2).sum()), float(mu)

def tau2_dl(yi, vi):
    w = 1/vi; q, _ = _q(yi, vi); k = yi.size
    c = w.sum() - (w**2).sum()/w.sum()
    return max(0.0, (q-(k-1))/c) if c > 0 else 0.0

def tau2_reml(yi, vi):
    if yi.size < 2: return 0.0
    def neg_ll(t2):
        w = 1/(vi+t2); mu = (w*yi).sum()/w.sum()
        return 0.5*(np.log(vi+t2).sum() + math.log(w.sum()) + (w*(yi-mu)**2).sum())
    up = max(10*float(vi.max()), 10*tau2_dl(yi, vi)+1, 10)
    return max(0.0, float(optimize.minimize_scalar(neg_ll, bounds=(0, up), method="bounded").x))

def tau2_pm(yi, vi, tol=1e-7, max_iter=200):     # Paule-Mandel (empirical Bayes)
    k = yi.size
    if k < 2: return 0.0
    g = lambda t2: float(((1/(vi+t2)) * (yi - ((1/(vi+t2))*yi).sum()/(1/(vi+t2)).sum())**2).sum()) - (k-1)
    if g(0.0) <= 0: return 0.0
    lo, hi = 0.0, 10*float(vi.max()) + 10
    while g(hi) > 0 and hi < 1e12: hi *= 2
    for _ in range(max_iter):
        mid = 0.5*(lo+hi); gm = g(mid)
        if abs(gm) < tol: return mid
        lo, hi = (mid, hi) if gm > 0 else (lo, mid)
    return 0.5*(lo+hi)

def heterogeneity(yi, vi):
    yi, vi = np.asarray(yi, float), np.asarray(vi, float); k = yi.size
    if k < 2: return {"k": k, "Q": None, "I2": None, "tau2_REML": 0.0, "tau2_PM": 0.0, "df": max(0, k-1)}
    q, _ = _q(yi, vi); df = k-1
    return {"k": k, "Q": q, "df": df, "p": float(stats.chi2.sf(q, df)),
            "I2": max(0.0, (q-df)/q)*100 if q > 0 else 0.0, "H": math.sqrt(q/df),
            "tau2_DL": tau2_dl(yi, vi), "tau2_REML": tau2_reml(yi, vi), "tau2_PM": tau2_pm(yi, vi)}

# ── 4. pooling ─────────────────────────────────────────────────────────────
def iv_pool(yi, vi, tau2=0.0, knapp=False):
    yi, vi = np.asarray(yi, float), np.asarray(vi, float); k = yi.size
    w = 1/(vi+tau2); sw = w.sum(); est = float((w*yi).sum()/sw)
    if knapp and k >= 2:
        qd = float((w*(yi-est)**2).sum()/(k-1)); se = math.sqrt(qd/sw)
        crit = float(stats.t.ppf(0.975, k-1)); p = float(2*stats.t.sf(abs(est/se), k-1))
    else:
        se = math.sqrt(1/sw); crit = Z95; p = float(2*stats.norm.sf(abs(est/se)))
    return {"estimate": est, "se": se, "ci_low": est-crit*se, "ci_high": est+crit*se,
            "p": p, "weights_pct": (w/sw*100).tolist(), "tau2": tau2}

def mantel_haenszel_or(tables):
    A = np.array([t["a"] for t in tables], float); B = np.array([t["b"] for t in tables], float)
    C = np.array([t["c"] for t in tables], float); D = np.array([t["d"] for t in tables], float)
    N = A+B+C+D; R = (A*D/N).sum(); S = (B*C/N).sum()
    P = (A+D)/N; Q = (B+C)/N; Ri = A*D/N; Si = B*C/N
    var = (P*Ri).sum()/(2*R**2) + (P*Si+Q*Ri).sum()/(2*R*S) + (Q*Si).sum()/(2*S**2)
    est = math.log(R/S); se = math.sqrt(var)
    return {"estimate": est, "se": se, "ci_low": est-Z95*se, "ci_high": est+Z95*se,
            "p": float(2*stats.norm.sf(abs(est/se)))}

def pool(yi, vi, model="random", tau2_method="REML", knapp=False):
    het = heterogeneity(yi, vi)
    _key = {"REML": "tau2_REML", "DL": "tau2_DL", "PM": "tau2_PM"}.get(tau2_method.upper(), "tau2_REML")
    tau2 = het[_key] if het["k"] >= 2 else 0.0
    return {"fixed": iv_pool(yi, vi, 0.0), "random": iv_pool(yi, vi, tau2, knapp),
            "heterogeneity": het}

# ── 6. publication bias ────────────────────────────────────────────────────
def eggers_test(yi, vi):
    yi, vi = np.asarray(yi, float), np.asarray(vi, float); k = yi.size
    if k < 3: return None
    se = np.sqrt(vi); snd = yi/se; X = np.column_stack([np.ones(k), 1/se])
    beta, *_ = np.linalg.lstsq(X, snd, rcond=None)
    resid = snd - X@beta; s2 = (resid@resid)/(k-2)
    se_int = math.sqrt(s2*np.linalg.inv(X.T@X)[0, 0]); t = beta[0]/se_int
    return {"intercept": float(beta[0]), "p": float(2*stats.t.sf(abs(t), k-2)), "underpowered": k < 10}

def _signed_rank_t(dev):
    order = np.argsort(np.abs(dev), kind="mergesort")
    ranks = np.empty_like(order, float); ranks[order] = np.arange(1, dev.size+1)
    return float(ranks[dev > 0].sum())

def trim_and_fill(yi, vi, tau2_method="REML", side="auto", max_iter=100):
    yi, vi = np.asarray(yi, float), np.asarray(vi, float); k0 = yi.size
    if k0 < 3: return {"n_imputed": 0}
    est = lambda y, v: (float((1/(v+ (tau2_reml(y, v) if tau2_method=="REML" else tau2_dl(y, v)))*y).sum()
                              / (1/(v+(tau2_reml(y, v) if tau2_method=="REML" else tau2_dl(y, v)))).sum())
                        if y.size >= 2 else float(y[0]))
    mu0 = est(yi, vi)
    if side == "auto":
        side = "left" if _signed_rank_t(yi-mu0) > k0*(k0+1)/4 else "right"
    flip = 1.0 if side == "left" else -1.0
    y = (yi-mu0)*flip; mu = 0.0; L = 0
    for _ in range(max_iter):
        keep = np.ones(y.size, bool)
        if L > 0: keep[np.argsort(y)[-L:]] = False
        yk, vk = y[keep], vi[keep]
        mu = (est(yk+mu0, vk)-mu0) if yk.size else 0.0
        kk = yk.size
        Ln = max(0, round((4*_signed_rank_t(yk-mu) - kk*(kk+1))/(2*kk-1))) if kk > 1 else 0
        if Ln == L: break
        L = Ln
    return {"n_imputed": int(L), "side": side}

# ── 8. sensitivity ─────────────────────────────────────────────────────────
def leave_one_out(yi, vi, tau2_method="REML"):
    yi, vi = np.asarray(yi, float), np.asarray(vi, float); out = []
    for i in range(yi.size):
        k = np.arange(yi.size) != i
        r = pool(yi[k], vi[k], tau2_method=tau2_method)
        out.append({"omitted": i, "estimate": r["random"]["estimate"], "I2": r["heterogeneity"]["I2"]})
    return out

# ── 9. GRADE ───────────────────────────────────────────────────────────────
GRADE = ["High", "Moderate", "Low", "Very low"]
ROB_SEV = {"low": 0, "some concerns": 1, "moderate": 1, "no information": 1,
           "insufficient information": 1, "unclear": 1, "high": 2, "serious": 2, "critical": 2}
def _sev(label): return ROB_SEV.get((label or "").strip().lower().split(" (")[0], 1)

def grade_body(initial, per_study_rob, weights, het, pooled, measure, total_n,
               subgroup_p=None, egger=None, n_imputed=0, indirectness=0,
               mid_benefit=None, mid_harm=None, is_binary=False):
    sev = np.array([_sev(r) for r in per_study_rob], float)
    w = np.asarray(weights, float) if weights and len(weights) == sev.size else np.ones(max(sev.size, 1))
    w = w/w.sum() if w.sum() else w
    fs = float(w[sev >= 2].sum()) if sev.size else 0.0
    fm = float(w[sev >= 1].sum()) if sev.size else 0.0
    d_rob = 2 if fs >= 0.5 else (1 if (fs >= 0.25 or fm >= 0.5) else 0)
    i2, pq = (het.get("I2") or 0.0), het.get("p", 1.0)
    if subgroup_p is not None and subgroup_p < 0.05: d_inc = 0
    elif i2 > 75 and pq < 0.10: d_inc = 1
    elif i2 > 50 and pq < 0.10: d_inc = 1
    else: d_inc = 0
    lo, hi = pooled.get("ci_low"), pooled.get("ci_high")
    crosses = lo is not None and hi is not None and lo <= 0 <= hi
    ois_fail = total_n < (300 if is_binary else 400)
    if mid_benefit is not None and mid_harm is not None and lo is not None:
        dl, dh = back_transform(lo, measure), back_transform(hi, measure)
        d_imp = 2 if (dl <= mid_harm and dh >= mid_benefit) else (1 if crosses or ois_fail else 0)
    else:
        d_imp = 2 if (crosses and ois_fail) else (1 if (crosses or ois_fail) else 0)
    d_pub = 1 if (egger and egger.get("p", 1) < 0.10) or n_imputed >= 2 else 0
    total = d_rob + d_inc + max(0, indirectness) + d_imp + d_pub
    final = GRADE[min(3, GRADE.index(initial) + total)]
    return {"final": final, "total_downgrade": total,
            "domains": {"risk_of_bias": d_rob, "inconsistency": d_inc,
                        "indirectness": indirectness, "imprecision": d_imp, "publication_bias": d_pub}}

# ── 1-2. LLM steps (model injected) ────────────────────────────────────────
def derive_eligibility(pico, llm_call):
    import json
    sys = ("You are a systematic-review methodologist. Given a review's PICO, produce "
           "concise, machine-checkable inclusion and exclusion criteria a screener can "
           "apply to a full-text article. Respond with JSON only.")
    return llm_call("Review PICO:\n" + json.dumps(pico) +
                    '\n\nReturn JSON {"inclusion":[{"axis":"","criterion":""}],'
                    '"exclusion":[...],"design_filter":[...]}', system=sys)
```

---

## 11. Test sketches (framework-free)

```python
import numpy as np, math
import meta_analysis as ma

# JSLHR golden fixture (Zhang, Cheng & Zhang 2022, Fig 2): 22 studies, SMD.
TE = [0.08,0.12,-1.36,3.63,0.08,-0.18,0.29,1.01,0.26,0.59,1.28,0.38,4.51,1.19,
      0.65,-0.20,0.24,-0.03,-0.12,0.14,0.00,0.46]
SE = [0.1513,0.4407,0.4047,0.9258,0.3537,0.2466,0.2852,0.5346,0.2930,0.3127,0.4663,
      0.4253,0.7014,1.0960,0.7687,1.0563,0.2083,0.2569,0.2571,0.8794,0.2709,0.3487]
yi = np.array(TE); vi = np.array(SE)**2

het = ma.heterogeneity(yi, vi)
assert het["df"] == 21
assert abs(het["Q"] - 83.85) < 0.6            # metafor reference
assert abs(het["I2"] - 75.0) < 1.0
assert abs(het["tau2_REML"] - 0.8240) < 0.05

re = ma.pool(yi, vi, tau2_method="REML")["random"]
assert abs(re["estimate"] - 0.468) < 0.01     # metafor: 0.468 [0.038, 0.898]
assert abs(re["ci_low"] - 0.038) < 0.02
assert abs(re["ci_high"] - 0.898) < 0.02

# Hedges' g from raw means
g, vg = ma.smd_hedges_g(10, 2, 50, 8, 2, 50)
assert abs(g - (1 - 3/(4*98 - 1)) * 1.0) < 1e-9   # d = 1.0

# Mantel-Haenszel OR
mh = ma.mantel_haenszel_or([{"a":10,"b":90,"c":5,"d":95},{"a":20,"b":80,"c":10,"d":90}])
assert abs(math.exp(mh["estimate"]) - 2.2) < 1e-6   # 13.75/6.25

# zero-cell continuity + double-zero drop
assert ma.log_or(0, 100, 5, 100) is not None        # single zero -> corrected
assert ma.log_or(0, 100, 0, 100) is None            # double zero -> dropped

# trim-and-fill: symmetric => ~0 imputed; asymmetric => >=1
sym = ma.trim_and_fill(np.array([-0.4,-0.2,0.0,0.2,0.4]), np.array([0.01]*5))
assert sym["n_imputed"] <= 1
asym = ma.trim_and_fill(np.array([0.1,0.2,0.5,0.6,0.9,1.1]),
                        np.array([0.0025,0.0025,0.09,0.1225,0.25,0.3025]))
assert asym["n_imputed"] >= 1

# GRADE: RCT body with high RoB + high I2 + imprecision -> downgraded
g = ma.grade_body("High", ["High","High","Some concerns"], [1,1,1],
                  {"I2":80.0,"p":0.001}, {"ci_low":-0.1,"ci_high":0.6}, "SMD", 120,
                  egger={"p":0.02})
assert g["final"] == "Very low"

# Hazard ratio: reported HR + 95% CI -> log scale; not from a 2x2
hr = ma.hazard_ratio(hr=0.75, ci_lower=0.60, ci_upper=0.94)
assert abs(hr[0] - math.log(0.75)) < 1e-9
assert abs(ma.hazard_ratio(o_e=-5.0, v=20.0)[0] + 0.25) < 1e-9   # Peto (O-E)/V
assert ma.back_transform(hr[0], "HR") == 0.75 or abs(math.exp(hr[0]) - 0.75) < 1e-9

# Paule-Mandel tau^2: > 0 on heterogeneous data, selectable in pool()
yh = np.array([0.1,0.9,0.3,-0.2,0.6]); vh = np.array([0.05,0.04,0.06,0.05,0.03])
assert ma.tau2_pm(yh, vh) > 0
assert abs(ma.pool(yh, vh, tau2_method="PM")["random"]["tau2"] - ma.tau2_pm(yh, vh)) < 1e-9
```

---

## 12. Implementation notes for other platforms

- **Compute in code, not in a prompt.** All of §3–§9 is deterministic arithmetic — never ask the LLM to pool, test heterogeneity, or assign GRADE. The LLM is used only for screening (§1) and extracting the raw numbers (§2).
- **Only two dependencies.** `numpy` for vector arithmetic/linear algebra and `scipy.stats` (norm/chi2/t CDFs+PPFs) + `scipy.optimize.minimize_scalar` (REML) + a root-finder (Q-profile CI, trim-and-fill). If you cannot use scipy, the three distribution functions can be hand-rolled from `math.erf`/`math.lgamma` plus a bisection PPF, and REML via a bounded Brent search — but scipy is strongly recommended.
- **Analysis scale vs display scale.** Keep `yi`/`vi` on the analysis scale throughout (log for ratios, Fisher-z for correlation, logit/arcsine for proportions); back-transform **only** the final point estimate and CI bounds for display. Heterogeneity, Egger, and trim-and-fill all operate on the analysis scale.
- **Continuity correction is a policy.** The 0.5 add-to-all-cells rule is the common default; some reviews prefer treatment-arm-only or no correction. Surface a `corrected` flag and let reviewers see it.
- **τ² estimator is a choice.** REML is the modern default (and what this reference validates against metafor); DerSimonian-Laird is the classic closed form. Expose both. Knapp-Hartung CIs are recommended for small k but change the interval — make it explicit.
- **k-thresholds matter.** Egger's test and trim-and-fill are meaningless for k < 3 and unreliable for k < 10 — return a status flag rather than a misleading number. I² is unstable at k = 2.
- **The RoB rating is external.** §9 consumes a per-study risk-of-bias label produced by a *separate* instrument (RoB 2, ROBINS-I, …). Map your instrument's labels to the 0/1/2 severity in §9; the GRADE thresholds (weight fractions, I² cutoffs, OIS sizes) are tunable policy, not canonical formulas.
- **One outcome at a time.** A "review" has many outcomes; each is an independent meta-analysis with its own measure, model, and forest plot. Do not pool across outcomes.
- **PRISMA upstream counts are reviewer-entered.** When studies arrive as uploaded full-text PDFs, the identification / de-duplication counts cannot be inferred — collect them from the reviewer and render them into the flow diagram alongside the screened/excluded/included counts you *can* derive.
```
