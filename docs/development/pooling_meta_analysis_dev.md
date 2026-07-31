# Pooling / Meta-Analysis Agent — Development Notes

Internal companion to [`../shareable/pooling_meta_analysis_shareable.md`](../shareable/pooling_meta_analysis_shareable.md). The shareable
document is the methodology, framework-free and history-free, for readers implementing on another
stack. This document holds what is specific to *this* codebase: where the code lives, what state it
is in, and the revision history.

**Backing modules**

- `backend/evidence_synthesis/pooling.py` — `pool_outcome()`, effect sizes, τ² estimators, heterogeneity, Egger, trim-and-fill, `grade_pooling_inputs()`
- `backend/evidence_synthesis/pooling_prep.py` — `group_into_bodies()`, `outcome_to_study_input()`, `resolve_rob()`, `attach_rob()`, `pool_body()`, `pool_extractions()`
- `backend/evidence_synthesis/pooling_extract.py` — `pool_studies()`, the dual-mode extraction bridge
- `backend/evidence_synthesis/pooling_harmonize.py` — outcome-label harmonization
- `backend/evidence_synthesis/grade_prep.py` — `rob_labels_for_body()`, the pooled-order alignment, and the `quality_appraisal_results` sourcing adapter
- `tests/test_pooling.py`, `tests/test_pooling_prep.py`, `tests/test_pooling_harmonize.py`

**Implementation status.** Merged to `main`, in `backend/evidence_synthesis/` (renamed from `backend/synthesis/` so the package coexists with the Synthesis review app's `backend/synthesis.py` module).

The shareable document is byte-identical to the copy distributed in the `synthesis-` repo — keep the two in step. `main` holds the canonical copies of `pooling_meta_analysis_shareable.md` and `grade_certainty_shareable.md`; the branch's checked-in copies were re-synced from them when the risk-of-bias pass-through code landed (they had drifted, still carrying the pre-convention inline `## Revision notes` section, and the GRADE copy predated the `require_rob` / `studies[].rob` contract). If the two diverge again, `main` wins.

---

## Sourcing risk of bias from `quality_appraisal_results`

The shareable document describes the label *contract* (§7 vocabulary, §9.3 precedence, `attach_rob` as the injection seam) framework-free. This is the repo-specific half: how our own per-paper appraisal feeds it.

One row per (paper × appraised outcome), turned into `attach_rob` records:

```sql
SELECT r.paper_id,
       r.outcome_id,
       -- The clean short label, and the join key. Fall back to the composed
       -- assessed_outcome only for rows written before the per-outcome fan-out.
       COALESCE(
           NULLIF(TRIM(json_extract(r.outcome_json, '$.name')), ''),
           NULLIF(TRIM(r.assessed_outcome), ''),
           r.primary_outcome) AS outcome,
       r.rob_overall AS rob,
       r.rob_tool,
       r.study_type
  FROM quality_appraisal_results r
 WHERE r.run_id = ?
   AND r.status = 'ok'
   AND r.rob_overall IS NOT NULL
   AND r.rob_tool <> 'amstar2'   -- inverted CONFIDENCE scale, not risk of bias
```

- **Key on `outcome_json.name`, not `assessed_outcome`.** This is the one that bites. `assessed_outcome` is *composed* for prompt quality — `"Quality of life — measured as KCCQ total symptom score — at 8 months"` — and will not match a body's outcome name after normalization. `outcome_json.name` is the clean short label (`"Quality of life"`) the outcome extractor produced, which is what a body key looks like. Getting this backwards makes every per-outcome lookup miss, and §9.3 of the shareable doc explains why that is worse than not harmonizing at all: the body reads as unappraised and gets downgraded for it. The CSV/XLSX export surfaces both, as `outcome_label` and `assessed_outcome`.
- **`rob_source` is always `'tool'`** for rows from this query, whether or not `outcome` resolved. The enum value records *who produced* the label, not what scope it has.
- **The AMSTAR-2 exclusion is load-bearing, twice over.** `STUDY_TYPE_REGISTRY` routes `SR with Meta-Analysis` / `SR without Meta-Analysis` to `amstar2.run`, whose `rob_overall` is `High / Moderate / Low / Critically low` with **`High` = good** — which is why that registry entry carries `skip_grade=True`. Without the `WHERE`, a well-conducted review lands as `High` → severity 2 → a two-level downgrade for being good. Separately, `"Critically low"` is absent from the consuming severity map and would silently resolve to severity 1 — a second, opposite error in the same row. **Fix by exclusion at the source, not by adding a severity entry**: mapping `"critically low"` downstream would legitimize routing systematic reviews into a body of primary-study evidence, which is a different methodological error.
- **Rows predating the fan-out carry no `outcome_json`** — `outcome_id` is NULL and `outcome_json` is `'{}'`. The `COALESCE` above handles them; when every candidate is empty, emit the record with `outcome: None` so `attach_rob` files it as the study-level label (tier 3 of the §9.3 ladder).
- **`paper_id` → `study_id` is the caller's join.** Nothing in the pooling inputs carries `paper_id`; `outcome_to_study_input` derives `study_id` from `study_id or citation_authors`. Whoever assembles the study list must carry the paper id through, or supply a mapping. Note one paper now yields several rows, so `result_id` (exported as its own column) is the row key, not `paper_id`.

**Per-outcome appraisal is opt-in.** `appraise_paper` fans out one `quality_appraisal_results` row per assessment unit — one per selected outcome for treatment designs, one per Phase-4 estimate for diagnostic accuracy, and exactly one when the reviewer supplies neither. So a run only produces multi-outcome rows when someone asked for them; the default is still the auto-picked primary outcome and a single row, and the tier-2 hit rate for secondary bodies depends on reviewers actually selecting those outcomes at run-create time. The two fan-out axes are mutually exclusive per paper (the API 400s on a paper carrying both), and AMSTAR-2 papers collapse back to one unit because the tool rates the review rather than an outcome.

---

## Revision notes

Substantive changes to the methodology, newest-first, so downstream implementations (e.g. forks maintained by other teams) can see what changed and why. Cosmetic / wording-only edits are not logged.

This history lives here rather than in the shareable document: the shareable document is the methodology as a reader on another stack should implement it, with no history and no repo-internal references. Everything about *this* codebase — where the code lives, what state it is in, what changed when — belongs on this side.

### 2026-07-31 — Appraisal fans out per (paper × outcome); the RoB join key changes

**What changed.** The quality-appraisal orchestrator now writes one result row per *assessment unit* rather than one per paper. A reviewer selects the outcomes to appraise (extracted by a new tool-agnostic `extract_outcomes` pass, or typed in), and each produces its own risk-of-bias + indirectness + imprecision + GRADE row carrying `outcome_id` and `outcome_json`. Classification, field extraction, and the reporting-guideline check stay once per paper. The diagnostic-accuracy per-estimate fan-out is unchanged and mutually exclusive with the outcome axis.

**Why.** Risk-of-bias instruments are outcome-specific — RoB 2 domain 4 (measurement of the outcome) and domain 5 (selection of the reported result) genuinely differ between outcomes, and GRADE rates risk of bias per outcome. One trial can be Low for mortality and High for an unblinded subjective outcome. The instruments already threaded the assessed outcome into every domain prompt; only the orchestrator collapsed it to one, which capped the per-(study × outcome) resolution described above at "primary outcome only, everything else `missing`".

**Impact on this document's contract.** **The `rob_by_outcome` key source changes** — key on `outcome_json.name`, not `assessed_outcome`. `assessed_outcome` is now composed for prompt quality (`"Quality of life — measured as … — at 8 months"`) and will not match a body outcome name after normalization; `outcome_json.name` is the clean short label. The sourcing SQL above is updated accordingly. Nothing about the *shareable* input contract changes shape — `rob`, `rob_source`, and `rob_by_outcome` are as they were — so there is no inline breaking-change note to look for in the shareable document.

Stored rows are untouched (three additive nullable/defaulted columns, no backfill), and single-outcome runs behave exactly as before. New multi-outcome runs raise the tier-2 hit rate for secondary bodies from zero to whatever reviewers select. Exports gain a `result_id` column, because one paper now yields several rows and `paper_id` is no longer a row key.

**Sections touched:** none of the shareable doc — §9.3's caveat already covered this case ("Either appraise per outcome, or supply a study-level `rob`"); no wording change was needed.

### 2026-07-31 — Risk of bias rides on the pooled study records, per (study × outcome)

**What changed.** The pooler now carries a per-study risk-of-bias label through to its output. Each per-study input may supply `rob` and `rob_source`; `_counts` preserves both (so every effect-size path — 2×2, person-time, continuous, pre-computed — carries them), and `pool_outcome` emits them on each `studies[]` record next to the `weight_pct` it computed. The pooler applies no severity mapping, no threshold, and no downgrade; it carries the label and nothing else.

Resolution is per **(study × outcome)**, at the assembly bridge, via a new `resolve_rob(study_level, oc, outcome_key)` with a four-tier precedence ladder: the outcome object's own `rob` (`user_outcome`) → the study's `rob_by_outcome[<body outcome>]` (`user_outcome`) → a study-level `rob` (`user_study`) → nothing (`missing`). An explicitly supplied `rob_source` always wins over the inferred value, which is how an instrument stamps `tool`. A new `attach_rob(studies, records)` is the pure injection seam an appraisal-database adapter targets. Outcome harmonization now canonicalizes `rob_by_outcome` keys alongside outcome names, and feeds them into the distinct-name set it clusters.

**Why.** The GRADE agent's contract requires `studies[].rob` on every study record and raises on a body with no labels. The pooler had no risk-of-bias concept at all and, worse, actively destroyed the field: `_counts` rebuilt each record from a fixed key list, so a caller could attach a label and watch it vanish without a warning. A spec-compliant pooler feeding a spec-compliant GRADE agent therefore failed on **every** body. Risk of bias is attached to the record rather than supplied as a parallel list because the pooler drops studies without usable data — a positional list misaligns after the first drop, and every label past it shifts by one, silently and indistinguishably from a correct result.

Risk of bias is per outcome, not per study, because RoB 2 and ROBINS-I are outcome-specific instruments: Domain 4 (measurement of the outcome) and Domain 5 (selection of the reported result) genuinely differ between outcomes, and GRADE rates risk of bias per outcome. One trial can be Low for mortality and High for an unblinded subjective outcome.

**Impact.** **Breaking** on the `studies[]` output shape — every entry gains `rob` and `rob_source`. Additive on the input side: studies without labels resolve `missing` and are counted conservatively downstream. No pooled estimate, heterogeneity value, or small-study test changes, so stored *numbers* need no recomputation. Certainty ratings **can** change: a body previously handed to GRADE now supplies a risk-of-bias domain that was previously absent. Harmonization now rewrites `rob_by_outcome` keys, so callers must attach labels **before** harmonizing (`attach_rob` → `harmonize_by_targets` → `group_into_bodies`); attaching afterwards leaves alias keys, every lookup misses, and an appraised body reads as unappraised.

**Sections touched:** §1, §7, §8, §9.3, §9.6, §9.7, §10, §11, §12 (of the shareable doc).

### 2026-07-31 — Pooling ↔ GRADE hand-off corrections

**What changed.** Four contract defects found while wiring risk of bias through.

1. **`design_class` now appears on the `pool_outcome` result**, not only on the body wrapper one level above. `pool_outcome` takes it as a keyword and `pool_body` passes the class its own grouping rule enforced.
2. **The hand-off object is the whole `pool_outcome(...)` result dict**, not `grade_pooling_inputs(result)`. The two documents previously named two different objects; the GRADE agent indexes `studies[]`, `heterogeneity`, `publication_bias`, and `totals`, none of which the flat helper carries. `grade_pooling_inputs` is retained and re-documented as a flattened convenience view for forest plots, summary tables, and exports.
3. **`grade_pooling_inputs.total_n` now sums both arms.** It previously returned `totals["n_int"]` alone.
4. **Reference-block drift repaired.** `group_into_bodies` now reads `canonical_outcome` (two places in the prose already said it did, so §9.7's harmonization was inert in the reference implementation); `pool_body` now propagates `favorable_direction`, emits the unknown-design `warnings` entry, and passes `outcome_name` down — all three documented in §9.1/§9.2 but absent from the code block. The production modules on the branch already did (1) and (4) correctly; this was a shareable-doc-only defect.

**Why.** (1) let the GRADE agent re-derive the design class by substring-matching study labels — a second, independent classifier that can disagree with the enforced grouping rule that built the body. (2) starved a doc-faithful implementation of the risk-of-bias, inconsistency, publication-bias, and OIS inputs. (3) halved the Optimal Information Size, converting a met threshold into an unmet one and manufacturing an imprecision downgrade. (4) meant a reader implementing from the reference block got no outcome harmonization at all, which is a prerequisite for per-outcome risk-of-bias matching.

**Impact.** (2) and (3) are **breaking** for any implementation wired through `grade_pooling_inputs` — such an implementation was already broken, and after this change it is broken visibly rather than silently. (1) and (4) are additive. No stored pooled numbers change; imprecision ratings computed via `grade_pooling_inputs.total_n` were wrong and should be recomputed.

**Sections touched:** §7, §8, §9.6, §10, §11, §12 (of the shareable doc).

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
