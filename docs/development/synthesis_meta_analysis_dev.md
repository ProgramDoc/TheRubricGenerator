# Synthesis Meta-Analysis — Development Notes

Internal companion to [`../shareable/synthesis_meta_analysis_shareable.md`](../shareable/synthesis_meta_analysis_shareable.md).
The shareable document is the methodology, framework-free and history-free, for readers implementing
on another stack. This document holds what is specific to *this* codebase: where the code lives, what
state it is in, and the revision history.

**Backing modules**

- `backend/synthesis.py` — orchestrator: screening / extraction prompts, `run_synthesis`, the
  `synthesis_*` DDL, `pool_outcome` / `repool_review`, PRISMA counts, credit model,
  `_resolve_study_rob` / `load_rob_map` / `_NON_ROB_TOOLS`
- `backend/synthesis_stats.py` — the pure numpy/scipy engine: effect sizes, pooling, heterogeneity,
  publication bias, subgroup / meta-regression, sensitivity, `grade_body_of_evidence` +
  `_rob_across_studies` + `_ROB_SEVERITY`
- `backend/synthesis_codegen.py` — emits runnable R (`meta`/`metafor`) + Python per calculation
- `frontend/synthesis.html` — the 8-tab workspace
- Risk-of-bias instruments are reused from `backend/rob_tools/` via
  `quality_appraisal.appraise_rob_only(...)`

**Implementation status.** Shipped. Per-outcome risk of bias implemented on branch
`rob-per-outcome-integration`.

---

## Revision notes

Substantive changes to the methodology, newest-first, so downstream implementations (e.g. forks
maintained by other teams) can see what changed and why. Cosmetic / wording-only edits are not logged.

### 2026-09-04 — RoB coverage guard (direction-aware); indirectness reported as not assessed

**What changed.** Two certainty-inflation fixes in `synthesis_stats` (`backend/synthesis_stats.py`), prompted by an external adversarial review; mirrored in the evidence-synthesis GRADE agent and its shareable docs.

1. **`_rob_across_studies` coverage guard.** Dropping unassessed studies and renormalizing could rate a body whose labels covered a sliver of the pooled weight (reproduced: High certainty with 99% of the weight unassessed and 1% assessed Low). Direction-aware fix: a downgrade computed from the assessed sliver stands at any coverage (unassessed studies could only add concerns), but a **clean** (0-downgrade) result with under 50% assessed weight now returns `assessed=False`, so `grade_body_of_evidence` reports `status="not_rated"` — the same refusal as a body with no labels at all. The exclusion note now names the excluded weight share, and the not-rated explanation carries the specific reason.
2. **Indirectness is an input, and an unsupplied one is flagged.** `grade_body_of_evidence` accepted `indirectness_levels` but the Synthesis orchestrator never supplies it, so every pooled body claimed "no serious indirectness". The domain now reports `downgrade: null` / `assessable: false` with a "not assessed" reason, a `warnings` entry, and an explanation caveat when no assessment (or explicit `indirectness_assessed=True`) is provided. Contribution to the total stays 0 — the fix is honesty, not an invented downgrade. Wiring a real per-body indirectness assessment (e.g. from the review PICO) remains open work.

**Impact.** Breaking for bodies with <50% assessed weight that previously rated clean — those are now `not_rated`, which is the point. All downgrades and majority-coverage ratings are unchanged. The grade JSON gains `warnings` on the rated path and `assessable`/nullable `downgrade` on the Indirectness row (the Synthesis UI already renders `downgrade==null` as "n/a").

### 2026-07-31 — Risk of bias is per (study × outcome); unassessed studies no longer scored

**What changed.** Three linked corrections to the GRADE risk-of-bias domain (§9).

1. **Grain.** The risk-of-bias instrument now runs once per **(study × outcome)** instead of once per
   study, and each pooled body reads the label assessed for *its* outcome. A resolution ladder was
   added: per-outcome judgement → study-level label (legacy bodies only) → unassessed. Only the
   instrument call repeats per outcome; field extraction feeding the prompts stays once per study.
2. **Unassessed studies are dropped, not defaulted.** The weighted aggregation now restricts to
   studies carrying a label and renormalizes the weights over them, and reports how many were
   excluded.
3. **A body with no labels at all is not rated** — `final = None`, `status = "not_rated"`, with a
   warning — rather than receiving a certainty computed from a 0 downgrade.

Separately, **AMSTAR-2 is excluded from the domain at the source**, rather than being given severity
entries.

**Why.** RoB 2 and ROBINS-I are outcome-specific instruments: "missing outcome data", "measurement of
the outcome", and "selection of the reported result" genuinely differ between outcomes in the same
paper, so one trial can be *Low* for all-cause mortality and *High* for an unblinded symptom score.
The previous implementation appraised each paper once against whichever outcome happened to be first
in the review and replayed that single label into every outcome's GRADE — so a review of
`[overall survival, PFS, grade 3-4 adverse events]` rated the adverse-event body of evidence using the
overall-survival appraisal.

The defaulting rule was a second, independent error. Every pooled study contributed an entry to the
label list, so a review run *without* risk of bias arrived as `[None, None, None]` rather than an
empty list; each `None` fell to the "some concerns" default, `frac_some` reached 1.0, and the outcome
was silently downgraded one level with the reason "a substantial share of weight (100%) is in studies
with risk-of-bias concerns" — a statement about studies nobody had appraised.

AMSTAR-2 rates a systematic review's *confidence*, where "High" is **good** — the opposite polarity to
every risk-of-bias scale. Its labels hit the `high → 2` entry, so a high-confidence review downgraded
two levels, while "Critically low" was absent from the map and fell to the severity-1 default:
inverted twice in the same row. Excluding it at the source was chosen over adding severity entries
because mapping those labels would legitimize pooling systematic reviews into a body of primary-study
evidence, which is a separate methodological error.

**Impact.** **Stored results change on re-pool**, in three ways, and this is a logic change rather
than a prompt-only one:

- Multi-outcome bodies may now receive *different* risk-of-bias downgrades per outcome where they
  previously shared one. This is the intended correction.
- Bodies whose studies were never appraised move from a 1-level downgrade to *not rated*. Any caller
  reading `grade["final"]` must handle `None`, and `domains[].downgrade` is `None` for an unrateable
  domain.
- Bodies of AMSTAR-2-appraised studies move from a 2-level downgrade to *not rated*.

Already-completed rows are untouched until something re-pools them. Bodies appraised before the change
keep their existing rating via the study-level rung of the ladder, which requires the implementation to
record that a body was appraised study-wide (here: a `rob_scope` marker on the review, backfilled to
`'study'` for existing rows). **Without that marker every historical body re-pools as unrated.**

**Sections touched:** §9 (restructured into §9.1 grain / §9.2 severity map + AMSTAR-2 exclusion /
§9.3 weighted aggregation / §9.4 not-rated), §10 reference implementation
(`resolve_study_rob`, `rob_across_studies`, `NON_ROB_TOOLS`, `grade_body`), §11 tests.

### 2026-07-22 — Hazard ratio (time-to-event) + Paule-Mandel τ²

**What changed.** Added two engine capabilities. (1) **Hazard ratio (HR)** — a time-to-event effect
measure pooled on the log scale (new §3.5). It is **never derived from a 2×2 table**; it takes a
reported HR + 95% CI, a reported log-HR + SE, or a log-rank O-E + variance V (Peto). Wired end-to-end:
measure metadata + effect-size dispatch, the extraction field spec (`hr`/`ci_lower`/`ci_upper`, plus
`o_e`/`v`), the measure registry, and R code export via `metagen(TE, seTE, sm="HR")`.
(2) **Paule-Mandel τ²** — a third between-study-variance estimator (new §5 subsection) selectable via
`tau2_method="PM"` alongside DL and REML; solved by bisection of the generalized-Q estimating equation
`Σ wᵢ(yᵢ−μ)² = k−1`.

**Why.** HR is the standard measure for survival/time-to-event outcomes (oncology, cardiology) and was
previously unsupported. PM is less biased than DL under real heterogeneity and needs no likelihood
optimizer, giving reviewers a robust third option.

**Impact.** Purely additive — no change to any existing measure, pooling result, or the DL/REML
defaults. New optional fields (`tau2_PM` in the heterogeneity dict; the HR measure and its
`o_e`/`v`/`loghr`/`se` inputs). No stored results are affected.

**Sections touched:** front matter (Contains), §3.5 (new), §5 (PM subsection), §10 reference
implementation (`hazard_ratio`, `tau2_pm`), §11 tests.

### 2026-06-12 — Initial publication

**What changed.** First publication of the Synthesis meta-analysis methodology: screening + extraction
prompts (§1–§2), effect-size formulas for all measures (§3), inverse-variance + Mantel-Haenszel +
random-effects pooling (§4), heterogeneity (§5), publication bias (§6), subgroup + meta-regression
(§7), sensitivity (§8), GRADE body-of-evidence combiner (§9), turnkey reference implementation (§10),
and tests (§11).

**Why.** Synthesis complements per-paper Quality Appraisal: QA assesses one study at a time and
explicitly defers the GRADE domains that need a *body* of evidence (inconsistency, publication bias).
Synthesis pools the body of evidence and therefore completes the GRADE picture.

**Impact.** New methodology; nothing pre-existing changes. The per-study RoB rating consumed by §9 is
produced by the platform's existing RoB tools (documented separately) — this doc does not alter them.

**Sections touched:** all (genesis).
