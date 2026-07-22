# Table 2 (Evidence Table) — Sharable Methodology Reference

A self-contained reference for building the **per-study evidence table** (ASCO "Table 2" / "T2"): one row = **a study × outcome × comparison × timepoint**, transcribing each study's *reported* results (intervention vs comparator, per outcome, with instrument, timepoint, effect + CI/p). Contains:

- The **extraction-tag logic across all three field levels** (universal / type-specific / cross-cutting), with each tag classified as a direct pull, a code derivation, an extraction, prompt context, or unused.
- The **`outcomes[]` output schema** — the one genuinely new build item, since the platform's native fields are single-outcome and evidence tables are multi-outcome.
- The **calculations that are *not* pure extraction**, each mapped to a named function.
- **Sharable JSON prompt logic** — the exact prompt strings + output shapes for the two extraction passes, plus a transport-free prompt-assembly config.
- The **dual-mode contract** — Table 2 builds *with* extraction tags injected from an upstream extraction agent (assembly only, zero model calls) **or** *in isolation* (it runs its own extraction). Extraction and assembly are independently callable.
- A **turnkey single-file Python reference implementation** (`llm_call` injected — no framework, HTTP, or database dependency) and plain-`assert` test sketches.
- **Real-guideline examples** of Table 2 with a column-variability matrix and a product-type × presence taxonomy (§8).
- **A rendering-model recommendation** — extract-once into one canonical dataset, then serve each table as a declarative view preset (dynamic-table UI + a thin pivot/nest projector), with the Layer A vs Layer B boundary (§9).

**Source.** The per-study "Evidence Table" family as it recurs across the ASCO / ASCO-OH(CCO) clinical-practice-guideline corpus (e.g. "Evidence Table for [Intervention / Question N]"), where a study's reported effects per outcome are transcribed with instrument, timepoint, effect estimate, and CI/p. The extraction-and-transcription conventions follow standard systematic-review per-study data-extraction practice (Cochrane Handbook, ch. on data collection). This document is the buildable companion to the strategy notes `Sharable_Table2_evidence_table_strategy.md` and `Sharable_evidence_synthesis_agents_overview.md`.

**Scope.** This covers **only** the per-study evidence table: extracting each study's reported per-outcome results and assembling them into rows. It is **almost entirely direct extraction** — there is no pooling, no meta-analysis, no baseline risk, no MID, no heterogeneity, and no certainty rating here.

Out of scope (different agents / tables):

- **Pooling / meta-analysis / GRADE evidence profile** (the "T5" body-of-evidence table). A GRADE certainty is *computed across the pooled set of studies*, never read off one paper — that is a separate agent.
- **Recommendation table** ("T6").
- **Quality-assessment / risk-of-bias table rendering** ("T3"). Table 2 *consumes* a per-study overall risk-of-bias verdict (`rob_overall`) as one derived column; it does not run or render the RoB tool.
- **Diagnostic-accuracy metric computation** from 2×2 counts (sensitivity/specificity/PPV/NPV). Table 2 transcribes such metrics *as reported*; deriving them is a specialised routine.
- Editing / overriding extracted values in a UI.

**Interpretation callouts.**

- **Transcription, not synthesis.** Table 2 records what a paper *states*. The only non-extraction work is light normalization (concatenation, label mapping, unit-safe display) and reconciliation of *reported* statistics — never a newly computed or pooled effect.
- **Direction depends on the outcome.** A hazard ratio below 1 favours the intervention for an *adverse* outcome (mortality) but the comparator for a *desirable* one (response). Direction inference therefore takes an `outcome_favorable_direction` hint; the default (`"lower"` is better) suits the adverse/symptom-burden outcomes most common in these tables. See §5.3.
- **Reported statistics are preserved faithfully.** `p < 0.001` is carried as `(p_value = 0.001, p_operator = "lt")` — the inequality is preserved, never collapsed to `=`. Missing CI or p stay blank; they are *derived* only when something valid exists to derive them from, and every derived value is flagged. See §5.4–5.5.
- **Quality rating inverts risk of bias.** *Low* risk of bias maps to *High* study quality. Because "High" means opposite things on a bias scale vs an AMSTAR-2 confidence scale, the mapping is tool-routed. See §5.6.
- **Dual-mode by construction.** Extraction (model calls) and assembly (pure Python) are separate stages, so Table 2 runs from injected tags with zero model calls, or extracts in isolation. See §7.

---

## Revision notes

Substantive changes to the methodology in this document, newest-first, so downstream implementations (e.g. forks maintained by other teams) can see what changed and why. Cosmetic / wording-only edits are not logged.

### 2026-07-05 — Added guideline-examples + rendering-model sections

**What changed.** Added §8 "Table 2 in practice: column variability across guidelines" (screenshots of real ASCO guideline Table 2s, a column-variability matrix, and a product-type × Table-2-presence taxonomy) and §9 "Rendering model: extract-once projection vs per-table render agent" (a recommendation with a tradeoff table). The reference-implementation / test / platform-notes sections renumbered to §10–§12.
**Why.** To ground the rendering-architecture decision — a dynamic-table UI serving columns off the extracted data vs a bespoke per-table render agent — in the actual layout variability observed across guideline Table 2s.
**Impact.** Documentation only — no change to any decision logic, prompt, schema, or output shape; no stored/historical results are affected. Adds PNG image assets under `assets/table2/`.
**Sections touched:** new §8, §9; renumbered §10–§12.

### 2026-06-30 — Initial publication

Self-contained Table 2 (per-study evidence table) methodology: the three-level extraction-tag mapping, the `outcomes[]` multi-outcome schema, the enumerated non-extraction calculations, the two extraction prompts + output shapes, the dual-mode (injected vs isolation) contract, and a turnkey Python reference implementation with plain-`assert` tests. The reference module's self-checks pass on CPython 3.13.

---

## 1. Core principle

Table 2 is the **per-study evidence table**. One row is a single reported result:

```
one row = (study × outcome × comparison × timepoint)
```

A study contributes **as many rows as it reports distinct results** — e.g. overall survival and progression-free survival, each at two timepoints, for two arm comparisons, is up to eight rows. Study-level facts (author, design, population, arms) are **denormalized** (repeated) across that study's rows so each row is display-complete.

Everything in a Table 2 row is a **pull** (copied from an extraction field), a **light derivation** (a concatenation, a label map, a composed display string), or the output of the **one new extraction pass** — the `outcomes[]` array. There is no pooled or newly-computed effect anywhere in Table 2.

**Why the `outcomes[]` array is the only new build item.** A typical extraction schema's outcome/result fields are **single-outcome** (`primary_outcome_definition`, `key_findings_effect_estimate`, …). Evidence tables are **multi-outcome**. So Table 2 introduces one array — `outcomes[]`, one object per (outcome × comparison × timepoint) — that generalizes those single-outcome fields to *N* outcomes. Every column other than that array maps onto fields an extractor already produces.

---

## 2. Two stages: extraction (model) and assembly (pure Python)

Table 2 is deliberately split so the two halves are independently useful:

| Stage | Produces | Model calls? |
|---|---|---|
| **Extraction** | study-level tags + the `outcomes[]` array | yes (0–2 passes) |
| **Assembly** | the ordered list of Table 2 rows | **no** — pure Python |

This split is what enables the **dual-mode** operation of §7: when an upstream extraction agent has already produced the tags, assembly runs alone with zero model calls; when nothing is injected, extraction runs first. Both paths converge on the same pure-Python assembler.

---

## 3. Extraction tags → Table 2 logic across all three levels

Each tag is classified as:

- **DIRECT** — pulled into a Table 2 column as-is.
- **DERIVED** — computed in code, no model call (function named).
- **EXTRACT** — needs the multi-outcome `outcomes[]` pass (not in the single-outcome native fields).
- **CONTEXT** — not a column, but fed to the extraction prompt to disambiguate.
- **UNUSED** — considered and deliberately excluded (listed so forkers know it wasn't forgotten).

The target column set: `study_id`, `design`, `population / N`, `eligibility_threshold`, `intervention vs comparator`, `outcome_name`, `outcome_instrument`, `outcome_timing`, `statistical_method`, `result_effect`, `result_ci / p`, `quality_rating`.

### 3.1 Level 1 — Universal fields

| Tag | Table 2 role | Class | Rule / notes |
|-----|--------------|-------|------------|
| `citation_authors` | half of `study_id` | DERIVED | `build_study_id(authors, year)` — first-author surname, "et al." if ≥3, "&" if 2. |
| `citation_year` | half of `study_id` | DERIVED | Consumed by `build_study_id`; also disambiguates same-author studies. |
| `citation_title` | — | CONTEXT | Helps the `outcomes[]` prompt anchor which paper / results section it is reading. |
| `citation_journal` | — | UNUSED | Bibliographic only. |
| `citation_doi` | — | UNUSED | Belongs in the reference list, not the evidence row. |
| `study_objective` | — | CONTEXT | Orients which contrast/outcome is the study's main question (main vs subgroup). |
| `population_participants` | `population / N` | DIRECT | Verbatim population descriptor; denormalized across the study's rows. |
| `population_intervention_exposure` | `intervention`; seeds `outcomes[].comparison` | DIRECT + CONTEXT | Direct into the column; also prompt context so `comparison` names the right arms. |
| `population_comparator` | `comparator`; seeds `outcomes[].comparison` | DIRECT + CONTEXT | Same dual role; disambiguates the contrast per outcome (esp. multi-arm). |
| `population_outcomes` | — | CONTEXT | An outcome checklist fed to the pass so each reported outcome is captured. |
| `sample_size_total` | `population / N` | DIRECT | Feeds `format_n_cell`; denormalized across the study's rows. |
| `sample_size_per_group` | — | CONTEXT | Per-arm N; optionally rendered inside the comparison cell. |
| `power_calculation_reported` | — | UNUSED | Appraisal input, not a T2 column. |
| `setting` | — | UNUSED | Not a T2 column. |
| `country_region` | — | UNUSED | Not a T2 column. |
| `study_period_enrollment_start` | — | UNUSED | Not a T2 column. |
| `study_period_enrollment_end` | — | UNUSED | Not a T2 column. |
| `follow_up_duration` | fallback for `outcome_timing` | CONTEXT / DERIVED | Study-level max follow-up; used as the timing fallback when an outcome states none. |
| `primary_outcome_definition` | `outcome_name` (single-outcome seed) | DIRECT→EXTRACT | Seeds `outcomes[0].name` in injected single-outcome mode; the pass supersedes it in isolation. |
| `primary_outcome_measurement` | `outcome_instrument` (seed) | DIRECT→EXTRACT | Seeds `instrument` (BFI, FACIT-F, EORTC, "months"). |
| `primary_outcome_timing` | `outcome_timing` (seed) | DIRECT→EXTRACT | Seeds `timing`. |
| `secondary_outcomes` | extra `outcomes[]` rows | CONTEXT→EXTRACT | Checklist of further outcomes; expands into additional objects, not one column. |
| `key_findings_effect_estimate` | `result_effect` (seed) | DIRECT→EXTRACT | Seeds `effect_estimate`. |
| `key_findings_metric` | `result_effect` (seed) | DIRECT→EXTRACT | Seeds `effect_metric`; drives the direction null value (1 for ratios, 0 for differences). |
| `key_findings_ci_lower` | `result_ci` (seed) | DIRECT→EXTRACT | Seeds `ci_lower`; blank if absent — never fabricated. |
| `key_findings_ci_upper` | `result_ci` (seed) | DIRECT→EXTRACT | Seeds `ci_upper`; blank if absent. |
| `key_findings_pvalue` | `result_p` (seed) | DIRECT→EXTRACT | Seeds `p_value` + `p_operator` (parsed from any inequality, e.g. "<0.001"). |
| `key_findings_direction` | `result_effect` direction (seed) | DIRECT→DERIVED | Seeds `direction`; if absent, `infer_direction(...)` computes it against the null. |
| `funding_source` | — | UNUSED | Appraisal/conflict input, not a T2 column. |
| `conflicts_of_interest` | — | UNUSED | Appraisal input. |
| `limitations_stated` | — | UNUSED | Appraisal narrative. |
| `protocol_registration` | — | UNUSED at L1 | Registration is captured (optionally) as the Level-3 `registration_number`. |

### 3.2 Level 2 — Type-specific fields (keyed by classified `study_type`)

The dominant Level-2 use is a **single column, `statistical_method`**, whose *source tag changes by study type*. A resolver picks the right tag; designs with **no native analysis field leave the cell blank** (or take it from the isolation-mode study-level pull) rather than mis-borrow an unrelated field:

```python
def resolve_statistical_method(study_type: str, type_fields: dict) -> str | None:
    """Pick the T2 statistical_method cell from the type-specific field that carries
    the analysis / statistics description for this design. Designs with no such field
    return None (the cell is left blank or filled by the study-level pull)."""
    STAT_FIELD_BY_TYPE = {
        "Randomized Controlled Trial": "analysis_framework",
        "Prognostic Factor Study":     "statistical_analysis",
        "Cohort Study":                "adjustment_method",
        "Prediction Model Study":      "model_type",
        "SR with Meta-Analysis":       "pooling_model",
        # Diagnostic Accuracy: NO native analysis field. Do NOT source this from
        # index_test — that names the test being evaluated, not the analysis. Leave
        # the method cell blank / to the study-level pull.
    }
    key = STAT_FIELD_BY_TYPE.get(study_type)
    return type_fields.get(key) if key else type_fields.get("statistical_analysis")
```

| Study type | Tag | Table 2 role | Class | Rule / notes |
|-----------|-----|--------------|-------|------------|
| Randomized Controlled Trial | `analysis_framework` | `statistical_method` | DIRECT | e.g. "ITT, mixed-model repeated measures". |
| Randomized Controlled Trial | `randomization_method` / `allocation_concealment` / `blinding_*` / `attrition_rate` | — | UNUSED (T2) | Risk-of-bias inputs, not evidence columns. |
| Randomized Controlled Trial | `missing_data_handling` | — | CONTEXT | Helps the pass read the *analysed* estimate (ITT vs completer). |
| Randomized Controlled Trial | `outcome_measurement_method` | fallback for `outcomes[].instrument` | CONTEXT | Names the instrument when the outcome text is terse. |
| Prognostic Factor Study | `statistical_analysis` | `statistical_method` | DIRECT | The prognostic-study analysis field. |
| Prognostic Factor Study | `prognostic_factor` | seeds `outcomes[].comparison` | CONTEXT | Factor present-vs-absent / per-unit contrast. |
| Prognostic Factor Study | `outcome_definition` | fallback for `outcomes[].name` | CONTEXT | Disambiguates the predicted outcome. |
| Cohort Study | `adjustment_method` | `statistical_method` | DIRECT | e.g. "Cox, adjusted for age/stage". |
| Cohort Study | `confounders_measured` | — | CONTEXT | Adjusted-vs-crude disambiguation. |
| Cohort Study | `exposure_definition` | seeds `outcomes[].comparison` | CONTEXT | Exposed-vs-unexposed contrast. |
| Cohort Study | `outcome_ascertainment` | fallback for `outcomes[].instrument` | CONTEXT | How the outcome was measured. |
| Diagnostic Accuracy | `index_test` | `intervention` | DIRECT | Names the "intervention" side (the test under evaluation). **Not** the method column. |
| Diagnostic Accuracy | `reference_standard` | `comparator` | DIRECT | The diagnostic comparator. |
| Diagnostic Accuracy | `two_by_two_table` | `result_effect` source | CONTEXT→EXTRACT | The pass transcribes each *reported* accuracy metric (sens/spec/…) as an outcome row. |
| Diagnostic Accuracy | `threshold_effects` | — | CONTEXT | The cutoff each estimate carries. |
| Prediction Model Study | `discrimination` / `calibration` | `outcomes[]` rows | EXTRACT | Reported AUC/C-index / calibration become outcome rows. |
| Prediction Model Study | `model_type` | `statistical_method` | DIRECT | The modelling approach. |
| SR with Meta-Analysis | `pooled_estimate` | `result_effect` | DIRECT | The review's **reported** pooled estimate is transcribed — a pull, not our pooling. |
| SR with Meta-Analysis | `effect_measure` | `outcomes[].effect_metric` | DIRECT | Metric of the pooled estimate. |
| SR with Meta-Analysis | `pooling_model` | `statistical_method` | DIRECT | Fixed/random-effects. |
| SR with Meta-Analysis | `heterogeneity` | appended to `result_effect` / notes | DIRECT | I²/τ² as reported (not recomputed). |
| SR with Meta-Analysis | `included_studies_n` | `population / N` | DIRECT | For an SR row, N is the count of pooled studies — see `format_n_cell` (§5.7). |
| Guideline / Consensus | `recommendations` / `grade_used` | — | UNUSED (T2) | Guidelines are not per-study evidence rows; they belong to the recommendation table. |
| **All other type-specific fields** | — | UNUSED / CONTEXT | — | **General rule:** any type-specific field that is not the design's analysis field, an arm/exposure definition, or a reported-result field is UNUSED unless it disambiguates the outcome/comparison (then CONTEXT). Only the analysis/statistics field — and, for SR/MA, the pooled-result fields — is ever DIRECT. |

### 3.3 Level 3 — Cross-cutting design modifiers

None are core evidence columns; they are optional annotations or prompt context. Listed so each is known to be considered.

| Tag | Table 2 role | Class | Rule / notes |
|-----|--------------|-------|------------|
| `clinical_trial_phase` | annotation on `design` | DERIVED | Optionally appends "(Phase III)" to the design cell. |
| `registration_number` | annotation on `study_id` | DIRECT | Optional footnote on the study row; not a required column. |
| `data_source_type` | — | CONTEXT | Registry vs trial — can steer the prompt to the right result. |
| `target_trial_emulation` | — | CONTEXT | Signals an emulated contrast (helps name arms). |
| `pilot_or_feasibility` | annotation on `design` | DERIVED | Optionally appends "(pilot)"; does not gate the row. |
| `regulatory_context` / `database_name` / `industry_sponsored` / `adaptive_design` / `pragmatic_vs_explanatory` / `trial_framework` | — | UNUSED | Design/appraisal descriptors; not T2 columns. |

### 3.4 Classification + reused appraisal output

| Tag | Table 2 role | Class | Rule / notes |
|-----|--------------|-------|------------|
| `major_category` / `subcategory` | — | CONTEXT | Coarse routing / disambiguation only. |
| `study_type` | `design`; routes `statistical_method` | DIRECT + CONTEXT | Direct into `design`; the key `resolve_statistical_method` uses. |
| `rob_tool` | routes `quality_rating` mapping | CONTEXT | Not a displayed column; tells `map_quality_rating` which scale to apply. |
| `rob_domains` | — | UNUSED | Per-domain judgements are not a T2 column. |
| `rob_overall` | `quality_rating` | DERIVED | `map_quality_rating(rob_overall, rob_tool)` → High / Intermediate / Low (§5.6). |

### 3.5 Single-outcome → `outcomes[]` generalization

When only single-outcome universal tags are injected (no `outcomes[]` array), Table 2 **seeds a 1-element array** from them with **zero model calls**. In isolation, the `outcomes[]` pass replaces this seed and may emit many objects.

| Single-outcome universal tag | → `outcomes[]` field | Notes |
|------------------------------|----------------------|-------|
| `primary_outcome_definition` | `name` | Outcome label. |
| `primary_outcome_measurement` | `instrument` | Scale/instrument or unit. |
| `primary_outcome_timing` | `timing` | Falls back to `follow_up_duration`. |
| *(from intervention + comparator)* | `comparison` | Filled from `population_comparator` at explode time. |
| `key_findings_metric` | `effect_metric` | Ratio (null 1) or difference (null 0). |
| `key_findings_effect_estimate` | `effect_estimate` | As reported. |
| `key_findings_direction` | `direction` | If blank, inferred via `infer_direction`. |
| `key_findings_ci_lower` / `_ci_upper` | `ci_lower` / `ci_upper` | Blank if unreported. |
| `key_findings_pvalue` | `p_value` (+ `p_operator`) | Inequalities preserved. |
| *(new — no single-outcome analog)* | `source_quote` | Verbatim provenance; only from the EXTRACT pass. |
| *(new — no single-outcome analog)* | `confidence` | Per-outcome extraction confidence; only from the EXTRACT pass (seed → `null`). |

---

## 4. The `outcomes[]` output schema

One object per **(outcome × comparison × timepoint)**. This is the only *new* build item; every other column is a pull or a light derivation. Missing numbers are `null` — **never fabricated, never computed**.

```jsonc
{
  "outcomes": [
    {
      // ── Identity of the row ─────────────────────────────────────────────
      "name":            "string",      // Outcome as reported, e.g. "Overall survival". Non-null.
      "instrument":      "string|null", // Instrument / scale / unit: "BFI", "FACIT-F", "months", "mmHg".
      "timing":          "string|null", // Timepoint AS REPORTED: "week 12", "median 18.2 mo". Distinct timing => distinct row.
      "comparison":      "string|null", // The two arms contrasted for THIS effect: "drug A vs placebo". Distinct comparison => distinct row.

      // ── The reported effect ─────────────────────────────────────────────
      "effect_metric":   "enum|null",   // See enum. Use "narrative" for text-only outcomes.
      "effect_estimate": "string|null", // Point estimate AS STATED, verbatim ("0.68", "-2.4", "12.1 months"). For "narrative", the descriptive text.
      "direction":       "enum",        // Interpreted benefit direction — see enum. Non-null.

      // ── Precision, as reported ──────────────────────────────────────────
      "ci_lower":        "number|null", // Lower bound of the reported 95% CI. null if absent. NEVER derive from p.
      "ci_upper":        "number|null", // Upper bound. null if absent.
      "p_value":         "number|null", // Reported p as a number (0.032). For "p<0.001" report 0.001 AND set p_operator="lt". null if absent. NEVER derive from CI.
      "p_operator":      "enum",        // eq | lt | gt | le | ge — the inequality reported with p. "eq" when the paper gives an exact value. Default "eq".

      // ── Provenance & self-assessment ────────────────────────────────────
      "source_quote":    "string",      // VERBATIM sentence/clause stating this effect. Non-null. The audit trail.
      "confidence":      "enum",        // high | moderate | low — extractor confidence in THIS row.

      // ── Optional flags ──────────────────────────────────────────────────
      "is_subgroup":     "boolean",     // true for a subgroup / secondary-population result. Default false.
      "subgroup_label":  "string|null"  // e.g. "PD-L1 >= 50%". null when is_subgroup=false.
    }
  ]
}
```

**Enums**

```jsonc
{
  "effect_metric": [
    // Ratio measures — null value 1, reasoning on the LOG scale:
    "HR",   // hazard ratio (time-to-event)
    "OR",   // odds ratio
    "RR",   // risk ratio / relative risk
    "IRR",  // incidence rate ratio
    // Difference measures — null value 0, reasoning on the NATURAL scale:
    "MD",   // mean difference
    "SMD",  // standardised mean difference
    "RD",   // risk difference  (spelled-out "absolute risk difference" normalizes to RD)
    // Escape hatch for text-only outcomes:
    "narrative"
  ],
  "direction": [
    "favours_intervention",
    "favours_comparator",
    "no_difference",     // null-crossing/touching CI, non-significant, or explicitly no difference
    "not_estimable"      // direction cannot be determined from what is reported
  ],
  "p_operator":  ["eq", "lt", "gt", "le", "ge"],
  "confidence":  ["high", "moderate", "low"]
}
```

> **One canonical vocabulary.** `direction` uses British spelling with underscores (`favours_intervention`), everywhere — in the schema, the prompt, and the Python. `confidence` is the enum `high | moderate | low` (or `null` for a seeded row with no per-outcome confidence). There is exactly one `RD`; "ARD" / "absolute risk difference" normalize to it rather than being a separate choice.

---

## 5. Calculations that are *not* pure extraction

Table 2 is transcription, so the calculation surface is small — but it is not empty. Every item below is deterministic Python (no model call); the function names match the reference module in §10.

| # | Calculation | Function | What it does |
|---|-------------|----------|--------------|
| 5.1 | **Study id** | `build_study_id` | `citation_authors` + `citation_year` → "First-author et al., YYYY" (1 → "Smith", 2 → "Smith & Jones", ≥3 → "Smith et al."). |
| 5.2 | **Metric canonicalization** | `canonicalize_metric` | Synonym → canonical token (hazard ratio→HR, relative risk→RR, standardised mean difference→SMD) + a *family* (`ratio` / `difference` / `time_to_event` / `narrative` / `unknown`). Drives the null value. |
| 5.3 | **Direction inference** | `infer_direction` | Estimate/CI vs the family's null value (ratio 1, difference 0), mapped to `favours_*` using the `outcome_favorable_direction` hint; CI straddling/touching the null → `no_difference`; a confidently parsed reported direction wins. |
| 5.4 | **Effect-cell parsing** | `parse_effect_cell` | `"0.72 (95% CI 0.55–0.94), p=0.01"` → `{estimate, ci_lower, ci_upper, p_value, p_operator}`. Requires an explicit `p<`/`p=` operator (so `grp2` is not misread as `p=2`); handles bracketed and unbracketed CIs; never guesses. |
| 5.5 | **Statistical reconciliation** | `reconcile_stats` | Fills **only** a missing CI (from estimate + exact p) or a missing p (from CI) — log scale for ratios, identity for differences, z = 1.959964. Never overwrites a reported value; never uses a *bounded* p (`<`, `>`) to derive an SE; marks every derived value. |
| 5.6 | **Quality rating** | `map_quality_rating` | `rob_overall` (+ `rob_tool`) → High / Intermediate / Low, **inverting** bias scales (low risk → high quality) but not the AMSTAR-2 confidence scale. |
| 5.7 | **N-cell composition** | `format_n_cell` | Participant N for a primary study; `"k=12 (N=3450)"` for a systematic-review/meta-analysis row (k studies, not participants). |
| 5.8 | **Single-outcome seed** | `seed_outcomes_from_universal` | Builds a 1-element `outcomes[]` from the native single-outcome fields (the zero-model-call path when only single-outcome tags are injected). |
| 5.9 | **Row explosion + denormalization** | `explode_rows` | One flat row per `outcomes[]` object; study-level fields repeated across them; composes `result_effect` / `result_ci_p` display strings; carries the subgroup flag + provenance. |
| 5.10 | **Dedupe** | `dedupe_rows` | Drops only *truly identical* rows; the key includes the subgroup flag/label and the metric, so a subgroup row and the main analysis both survive. |
| 5.11 | **Dual-mode merge** | `merge_injected_and_extracted` | Injected study-level tags win field-by-field; the `outcomes[]` array is taken whole from the highest-priority source (injected → extracted → seed). |

**Guarded, documented limitations** (called out so a forker knows they are intentional, not bugs):

- A **null-valued estimate with only a p** (HR/RR/OR = 1, or MD = 0, plus `p = 0.5` and no CI) is genuinely under-determined — `ln(1) = 0`, so the standard error is `0/z`. `reconcile_stats` leaves the CI blank rather than fabricate one.
- A **CI bound sitting exactly on the null** (ratio CI `1.0–1.5`) is treated as `no_difference` — the standard non-significant reading of a touching CI.
- Direction can only name an arm (`favours_*`) when the outcome's desirable direction is known (the hint, or a reported direction, or the model's `source_quote`); the pure-numeric fallback returns `not_estimable` rather than guess which arm benefits.

---

## 6. Sharable JSON prompt logic

Two prompts, sent as literal strings with `{placeholder}` markers. A caller on any stack fills the placeholders, sends them through an injected `llm_call`, and parses the returned JSON. No framework, transport, or storage dependency.

### 6.1 The `outcomes[]` extraction prompt

**SYSTEM string**

```text
You are a clinical-evidence extraction service building a per-study evidence table
(one row per outcome × comparison × timepoint). You transcribe results EXACTLY AS
REPORTED. You never pool, average, re-analyse, or compute a new effect. You never
invent numbers.

Hard rules:
1. One object per (outcome × comparison × timepoint). Same outcome at 3 timepoints
   => 3 objects. Two arm comparisons for one outcome => 2 objects.
2. Report effect_estimate, ci_lower, ci_upper, p_value AS STATED. If a value is not
   reported, set it to null. NEVER derive a CI from a p-value or a p-value from a CI.
   NEVER fabricate a plausible number.
3. Preserve p-value inequalities. For "p<0.001" set p_value=0.001 AND p_operator="lt".
   For an exact "p=0.03" set p_value=0.03 AND p_operator="eq". Operators: eq, lt, gt,
   le, ge.
4. Do not compute or pool anything. If the paper itself reports a pooled/meta-analytic
   estimate (e.g. this study IS a meta-analysis), transcribe that reported pooled
   number — but never create one.
5. source_quote MUST be a verbatim span copied from the paper stating the effect. Do
   not paraphrase. If you cannot find a verbatim statement, do not emit the row.
6. Narrative-only outcomes (words, no numeric effect): set effect_metric="narrative",
   put the statement in effect_estimate, leave ci_lower/ci_upper/p_value null, set
   direction from the text.
7. Subgroup / secondary-population results: set is_subgroup=true and fill
   subgroup_label. Keep the main (all-participants) analysis as its own row.
8. effect_metric is one of: HR, OR, RR, IRR, MD, SMD, RD, narrative. Ratio metrics
   (HR/OR/RR/IRR) have null value 1; difference metrics (MD/SMD/RD) have null value 0.
9. direction is one of: favours_intervention, favours_comparator, no_difference,
   not_estimable. "favours_intervention" means the effect favours the intervention arm
   named in `comparison`. Remember that for an adverse outcome (mortality, relapse) a
   value below the null favours the intervention, but for a desirable outcome
   (survival, response) a value above the null favours the intervention.
10. confidence (high|moderate|low) is YOUR confidence that this row faithfully
    transcribes the paper.

Return ONLY a single JSON object of the exact shape requested. No prose, no markdown.
```

**TASK / USER string**

```text
Study context: {study_context}
Intervention arm(s): {intervention}
Comparator arm(s): {comparator}
Outcomes of interest (extract ALL reported; this list is guidance, not a limit):
{outcomes_of_interest}

Read the attached study and extract every reported outcome result as one object per
(outcome × comparison × timepoint). Follow every rule in the system message.

Return exactly this JSON shape:

{
  "outcomes": [
    {
      "name": "",
      "instrument": null,
      "timing": null,
      "comparison": "",
      "effect_metric": null,
      "effect_estimate": null,
      "direction": "not_estimable",
      "ci_lower": null,
      "ci_upper": null,
      "p_value": null,
      "p_operator": "eq",
      "source_quote": "",
      "confidence": "low",
      "is_subgroup": false,
      "subgroup_label": null
    }
  ]
}
```

`{study_context}` = a short descriptor (title + design + population); `{intervention}` / `{comparator}` come from `population_intervention_exposure` / `population_comparator` when tags were injected, else from the study-level pull in §6.2; `{outcomes_of_interest}` = `population_outcomes` + `primary_outcome_definition` + `secondary_outcomes` when available, else `"all reported outcomes"`.

### 6.2 The study-level characteristics pull prompt

Used **only in isolation mode** (no injected tags) to fill `study_id` / `design` / `population / N` / `intervention vs comparator` / `eligibility_threshold`. One call per study, independent of the `outcomes[]` call.

**SYSTEM string**

```text
You are a clinical-evidence extraction service. You extract study-level
characteristics for a per-study evidence table. You transcribe what the paper states
and never fabricate. If a field is not reported, return null. You do not classify risk
of bias and you do not extract per-outcome results here.

Return ONLY a single JSON object of the exact shape requested. No prose, no markdown.
```

**TASK / USER string**

```text
Read the attached study and return its top-level characteristics.

Field guidance:
- citation_authors: author list as printed (used with year to form the study_id).
- citation_year: publication year (integer).
- study_type: the design label best supported by the text (e.g. "Randomized
  Controlled Trial", "Cohort Study", "Diagnostic Accuracy", "SR with Meta-Analysis").
- population_participants: one-line description of who was studied.
- sample_size_total: total N analysed (integer). null if not reported.
- included_studies_n: for a systematic review, the number of included studies. null otherwise.
- population_intervention_exposure: the intervention/exposure arm(s), as reported.
- population_comparator: the comparator/control arm(s), as reported.
- eligibility_threshold: any numeric eligibility cutoff for enrolment
  (e.g. "BFI >= 36", "eGFR < 30"). null if none.
- statistical_method: the primary analysis framework / statistical method
  (e.g. "Cox proportional hazards", "mixed-effects model", "log-rank"). null if none.

Return exactly this JSON shape:

{
  "citation_authors": null,
  "citation_year": null,
  "study_type": null,
  "population_participants": null,
  "sample_size_total": null,
  "included_studies_n": null,
  "population_intervention_exposure": null,
  "population_comparator": null,
  "eligibility_threshold": null,
  "statistical_method": null
}
```

Notes: `study_id` is derived in Python (§5.1), not asked of the model. `eligibility_threshold` has no native single-outcome field, so it is populated **only** in isolation mode (or under the enrich toggle) — in injected mode without an extraction pass, expect it blank. `statistical_method` is pulled here as a fallback; in injected mode it comes from the type-specific field via `resolve_statistical_method`.

### 6.3 Prompt assembly as JSON

A transport-free config describing the two prompts, their placeholders, determinism guidance, and merge points. A caller wires these to its own `llm_call`; nothing here names an endpoint, framework, or vendor.

```jsonc
{
  "agent": "table2_evidence_table",
  "determinism": {
    "temperature": 0.0,
    "note": "Lowest available temperature. This is transcription, not generation — variance is a defect. Parse the response as JSON; strip any markdown fences before parsing; on parse failure retry once, then fail the row."
  },
  "prompts": {
    "outcomes_pass": {
      "purpose": "Extract outcomes[] — one object per (outcome × comparison × timepoint).",
      "system_ref": "§6.1 SYSTEM string",
      "task_ref":   "§6.1 TASK/USER string",
      "attach_document": true,
      "placeholders": {
        "study_context":        "title + design + population",
        "intervention":         "population_intervention_exposure (injected) OR study-level pull",
        "comparator":           "population_comparator (injected) OR study-level pull",
        "outcomes_of_interest": "population_outcomes + primary_outcome_definition + secondary_outcomes, else 'all reported outcomes'"
      },
      "returns":  "{ \"outcomes\": [ <object per §4 schema> ] }",
      "run_when": "mode == 'isolation'  OR  (mode == 'injected' AND enrich AND no injected outcomes[])"
    },
    "study_level_pull": {
      "purpose": "Fill study_id / design / population / arms / eligibility_threshold when no tags were injected.",
      "system_ref": "§6.2 SYSTEM string",
      "task_ref":   "§6.2 TASK/USER string",
      "attach_document": true,
      "returns":  "{ citation_authors, citation_year, study_type, population_participants, sample_size_total, included_studies_n, population_intervention_exposure, population_comparator, eligibility_threshold, statistical_method }",
      "run_when": "mode == 'isolation'"
    }
  },
  "merge_points": {
    "study_id":                   "build_study_id(citation_authors, citation_year)  // Python, no model",
    "design":                     "study_type  // classifier tag, else study_level_pull",
    "population_N":               "population_participants + format_n_cell(study_type, sample_size_total, included_studies_n)",
    "intervention_vs_comparator": "population_intervention_exposure / population_comparator",
    "statistical_method":         "resolve_statistical_method(study_type, type_fields) else study_level_pull",
    "quality_rating":             "map_quality_rating(rob_overall, rob_tool)  // reused appraisal output, Python, no model",
    "outcome_rows":               "one row per outcomes[] element; study-level fields denormalized across the study's rows"
  },
  "invariants": [
    "Missing CI or p is null, never fabricated.",
    "p-value inequalities are preserved via p_operator ('p<0.001' stays a bound).",
    "No pooling or newly-computed effects; report AS STATED.",
    "source_quote is verbatim provenance for every extracted outcome row.",
    "Subgroup rows are kept but flagged (is_subgroup=true); the main analysis stays the primary row.",
    "An SR-with-meta row transcribes the review's OWN pooled estimate — a pull, not our pooling."
  ]
}
```

---

## 7. Dual-mode operation

Table 2 runs in two modes that converge on the same pure-Python assembler.

- **Mode A — with injected extraction tags.** An upstream extraction agent already produced Level-1/2/3 tags (and possibly the `outcomes[]` array and a risk-of-bias verdict). Table 2 *assembles*. If `outcomes[]` was injected → **zero model calls**. If only single-outcome tags were injected → seed a 1-element array (§5.8) → still **zero model calls**. Optional `enrich_injected=True` runs *only* the `outcomes[]` pass to discover secondary outcomes.
- **Mode B — in isolation.** Table 2 receives only the paper + a classified `study_type`. It runs **its own extraction** (study-level pull + `outcomes[]` pass), then assembles.

### 7.1 Decision logic — when is the model called?

| Input situation | study-level pull | `outcomes[]` pass | Model calls |
|---|---|---|---|
| Injected + `outcomes[]` present | no | no | **0** |
| Injected, no `outcomes[]`, default (seed) | no | no (seed 1-elem) | **0** |
| Injected, no `outcomes[]`, `enrich_injected=True` | no | yes | 1 |
| Isolation (no tags) | yes | yes | 2 |

Default for "injected without `outcomes[]`" is **seed, don't enrich** — trust the upstream single-outcome extraction; enrichment is opt-in because it costs a call and can surface outcomes the reviewer didn't intend to include.

### 7.2 Precedence (injected and freshly-extracted coexist)

**Injected study-level scalars win** field-by-field (they are facts the upstream extractor committed to); a self-extracted value fills a gap only where the injected tag is blank. The **`outcomes[]` array is taken whole** from the highest-priority source (injected → extracted → seed) — two independently produced arrays have no shared row identity, so element-wise merging is unsafe. The reused appraisal verdict (`rob_overall` / `rob_tool`) is carried through as-is.

### 7.3 Output contract — one Table 2 row

```jsonc
{
  "study_id":              "Doe & Roe, 2019",
  "design":                "Randomized Controlled Trial",
  "population":            "Adults with cancer-related fatigue",
  "n":                     "240",                       // format_n_cell(...)
  "eligibility_threshold": "BFI >= 36",                 // blank in injected mode w/o extraction
  "intervention":          "Exercise programme",
  "comparator":            "Usual care",
  "outcome_name":          "Fatigue",
  "outcome_instrument":    "BFI",
  "outcome_timing":        "12 weeks",
  "comparison":            "Exercise programme vs Usual care",
  "statistical_method":    "Mixed-effects model",
  "effect_metric":         "MD",
  "effect_estimate":       -3.4,
  "ci_lower":              -5.1,
  "ci_upper":              -1.7,
  "p_value":               0.001,
  "p_operator":            "lt",
  "direction":             "favours_intervention",
  "derived_stats":         [],                          // which of ci/p were computed
  "result_effect":         "MD -3.4 (favours intervention)",
  "result_ci_p":           "95% CI -5.1–-1.7; p<0.001",
  "quality_rating":        "High",                      // Low risk of bias -> High quality
  "is_subgroup":           false,
  "subgroup_label":        null,
  "source_quote":          "…mean difference −3.4…",
  "confidence":            "moderate",                  // enum, or null for a seeded row
  "provenance":            "seeded"                     // injected | extracted | enriched | seeded
}
```

### 7.4 Flow — both paths converge on pure-Python assembly

```
   Mode A                                Mode B
 injected tags                          isolation (paper + study_type)
      │                                        │
      ▼                                        ▼
  inspect input (no model call)         _extract_study_level(paper)   ← model
      │                                  _extract_outcomes(paper)      ← model
 ┌────┴─────────────┐                           │
 │                  │                           │
 outcomes[]      no outcomes[]                  │
 present         (default: seed;                │
 ZERO calls       enrich → 1 call)              │
 │                  │                           │
 └────────┬─────────┴───────────────────────────┘
          ▼
   merge_injected_and_extracted(injected, extracted)
          ▼   (pure Python — NO model)
   assemble_table2(merged)
     • one row per (study × outcome × comparison × timepoint)
     • denormalize study-level fields onto each row
     • reconcile stats; infer direction; compose result_effect / result_ci_p
     • map_quality_rating(rob_overall, rob_tool)
     • stamp provenance + carry source_quote / confidence
          ▼
   ordered list of Table 2 rows
```

### 7.5 Two independently usable stages

- **Extraction in isolation.** `_extract_outcomes(...)` and `_extract_study_level(...)` are standalone callables taking an injected `llm_call` and returning plain dicts/lists — usable by any agent or a bare script to get an `outcomes[]` array without touching assembly.
- **Assembly in isolation.** `assemble_table2(...)` is pure Python with no model dependency. Handed a fully-populated tags dict (with `outcomes[]`), it emits Table 2 rows with zero model calls — Mode A's fast path.

---

## 8. Table 2 in practice: column variability across guidelines

There is **no single canonical Table 2 schema**. "T2" is a recurring *family*; each guideline lays out the same kind of per-study/per-outcome data differently. The examples below — real ASCO guideline tables — make that variability concrete, and it is the reason the rendering model in §9 matters. *(Excerpts reproduced for internal methodology reference; each figure remains © its publisher, J Clin Oncol / ASCO.)*

### 8.1 Four layouts of the same data

**Wide — one column per outcome.** Gaillard 2025 (ovarian) gives each outcome its own column (`PFS, Months, HR (CI)` and `OS, Months, HR (CI)`) and folds harms in as an outcome column (`Grade 3-4 Postoperative Complications, %`). One row = one study; the outcomes spread across columns.

![Gaillard 2025 — wide layout: one column per outcome plus harms-as-outcome](assets/table2/gaillard-2025-ovarian-table1-wide.png)
*Gaillard et al., ASCO neoadjuvant-ovarian guideline, Table 1 — J Clin Oncol 2025;43(7):875. Per-outcome columns (PFS HR, OS HR); harms as an outcome column; no quality column.*

**Nested-cell — outcomes packed into a text cell.** Bower 2024 (fatigue) nests every instrument + timepoint in one `Fatigue Outcomes` cell and the effect + CI + direction in one prose `Results` cell, with a dedicated `Risk of Bias Assessment` column.

![Bower 2024 — nested multi-outcome cells plus a risk-of-bias column](assets/table2/bower-2024-fatigue-evidence-nested.png)
*Bower et al., ASCO cancer-related-fatigue guideline update, Data Supplement 1, Table 1 — J Clin Oncol 2024. Instruments/timepoints nested in one cell; results as prose; RoB as a Table-2 column.*

**Long + Layer-A/B fusion.** Shah 2026 (gastroesophageal) uses one row per outcome and *fuses* the per-study HR with **pooled** per-1000 absolute effects, plus a `Quality of evidence` column and a plain-language `Summary`, stratified by PD-L1 subgroup.

![Shah 2026 — long pooled rows with per-1000 absolute effects, quality, and summary](assets/table2/shah-2026-ge-pooled-quality.png)
*Shah et al., ASCO advanced-gastroesophageal guideline update, Data Supplement, Tables 1–2 — J Clin Oncol 2026;44(12):1145-1165 (DOI 10.1200/JCO-25-02958). Table 1 (per-study characteristics) and Table 2 (pooled per-1000 + quality + summary) sit on one page — the T1/T2/T5 boundary blurred.*

**Characteristics-only.** Yu 2025 (prostate) labels a pure trial-characteristics table (transposed key/value: `Trial ID`, `NCT`, `Study type`, `Experimental agent`, `Comparator`, …) "Table 2" and puts outcomes in a *separate* table.

![Yu 2025 — characteristics-only, no outcome columns](assets/table2/yu-2025-prostate-characteristics.png)
*Yu et al., ASCO prostate genomic-testing guideline, Data Supplement, Table 2 — J Clin Oncol 2025. Pure trial characteristics, no outcome columns; outcomes live in a separate Table 4.*

**Per-outcome certainty pairing.** Hicks 2026 (myeloma) pairs each outcome column with its own certainty column (`OS Certainty`, `PFS Certainty`, `Important AEs Certainty`) and points to separate evidence profiles for the full accounting.

![Hicks 2026 — each outcome column paired with a certainty column](assets/table2/hicks-2026-myeloma-stratified.png)
*Hicks et al., ASCO multiple-myeloma living guideline, Table 3 — J Clin Oncol 2026. Each outcome (OS, PFS, AEs) paired with a per-outcome certainty column; the caption points to Evidence Profiles 1.1–1.3.*

### 8.2 Column-variability matrix

The same per-study facts, laid out differently. Layout is the axis that varies most — no two guidelines share one.

| Guideline (product type) | Outcome layout | Intervention × comparator | Effect format | Quality / certainty | Harms | Follow-up |
|---|---|---|---|---|---|---|
| Bower 2024 fatigue (update) | **nested cell** | merged (one column) | prose in a `Results` cell | in-T2 column (`Risk of Bias`) | n/a (fatigue) | nested (timepoints in cell) |
| Gaillard 2025 ovarian (update) | **wide** (column per outcome) | per-arm rows | `HR (CI)` in the column header | absent from these tables | outcome column (`Grade 3-4 %`) | column (in Table 3) |
| Shah 2026 GE (update) | **long** (row per outcome, pooled) | split (two columns) | HR cell **+ per-1000** columns | in-T2 column (`Quality of evidence`) | separate table | timeframe in row |
| Hicks 2026 myeloma (living) | **wide** (column per outcome) | split (two columns) | `HR (CI) [N]` cell | **per-outcome** columns (in-T2) | outcome column (`AEs RR`) | absent |
| Yu 2025 prostate (de novo) | **none** (characteristics only) | split (two columns) | n/a (outcomes in Table 4) | separate table (Table 3) | separate table (Table 5) | absent |
| Scott 2024 rectal (de novo) | **long** (pooled, supplement) | split | RR cell **+ per-1000** columns | in-T2 column (`Evidence quality`) | outcome (pCR etc.) | timeframe in row |

Nothing is universal: no guideline carries a `Statistical method` column of its own; follow-up is a column / nested / absent; quality is in-T2 / a separate table / absent / per-outcome; harms are an outcome column or a separate table.

### 8.3 Product type predicts *presence*; topic predicts *shape*

A Table 2's **presence** is a deterministic function of the ASCO product type; its **shape** is not (two updates — Bower and Gaillard — have opposite layouts).

| Product type | Carries a Table 2? | Evidence strategy |
|---|---|---|
| De novo guideline | **Yes** | full per-study evidence tables (body and/or supplement) |
| Guideline update | **Yes** | full per-study evidence tables |
| Living guideline | **Yes** | per-study tables + a continuous-update mechanism |
| **Rapid recommendation update** | **No** | recommendations only; evidence compressed to prose + a link to externally-hosted evidence tables |

Spot-check of three rapid updates — Korde 2022 (pembrolizumab, 4 pp), Burstein 2023 (ESR1, 5 pp), Freedman 2024 (CDK4/6, 5 pp) — **0/3 carry any evidence table**; each cites its trials in prose and delegates the tabular evidence to `asco.org` guideline pages.

**Pipeline implication.** The orchestrator should **detect product type and gate evidence-table generation** (see §9.4): a rapid update runs only the recommendation path; it should not fail trying to build a Table 2 the product deliberately omits.

### 8.4 Why this matters

The same extracted per-study facts appear as nested cells (Bower), wide per-outcome columns (Gaillard, Hicks), and long pooled rows (Shah, Scott). **The data is identical; only the shape differs.** So layout must be a property of the *view*, not of extraction and not of a table-specific builder. §9 develops what that implies.

---

## 9. Rendering model: extract-once projection vs per-table render agent

**Thesis.** Extract once into a canonical dataset; make each table a **declarative view** over it. "Serve columns" is *almost* right — column selection reproduces the **long** layout and any subset of it, but the **wide** and **nested** layouts of §8 are grain transforms it cannot express, so the honest model is *serve columns **+ a small set of deterministic pivot/aggregate presets***. And none of this needs a model at render time — the real choice is **generic dynamic-table + declarative presets** vs **bespoke per-table code**, not "UI vs agent".

### 9.1 The distinction, precisely

Decompose "render a guideline's Table 2" into operations and ask which a column-picker over the extracted data can do:

| Operation | What it is | Column-selection alone? |
|---|---|---|
| **SELECT** | choose / reorder / relabel attributes | **Yes** |
| **FILTER** | choose which rows appear (drop subgroups, one comparison) | **Yes** |
| **LONG render** | one row per outcome, as extracted | **Yes** — it *is* the base grain |
| **PIVOT (long → wide)** | one **column** per outcome/timepoint (Gaillard, Hicks) | **No** — changes the grain from row-per-outcome to row-per-study |
| **NEST (aggregate → cell)** | fold N outcome rows into one composed text cell (Bower) | **No** — group + synthesize a new cell value from a *set* of rows |

The user framing is correct — SELECT / FILTER / LONG are free over the extracted data — with one correction of emphasis: PIVOT and NEST are *not* column picks (they change the row count), but they are also **not** bespoke per-table code. Each is one small, closed, table-agnostic transform: a pivot keyed on a chosen dimension, and a group-and-compose keyed on a chosen dimension. They belong in the presentation layer as **two reusable operators**. So the accurate boundary is: **serve columns + filter + a pivot preset + an aggregate-into-cell preset.**

### 9.2 Neither side is an LLM at render time

Every beyond-extraction derivation is deterministic Python (see §5 and the reference module): `build_study_id`, `canonicalize_metric`, `infer_direction`, `reconcile_stats`, `map_quality_rating`, `explode_rows`, `dedupe_rows`, and `assemble_table2` — whose contract is *"pure Python; never calls the model."* The only model touch in the whole pipeline is the `outcomes[]` extraction pass. So a "render agent" is **not** an AI artifact — it is bespoke code; and a "dynamic-table UI" is **not** avoiding intelligence — it runs the same deterministic projections behind interactive controls. The real axis is **generic-and-declarative** vs **bespoke-and-imperative**, both deterministic.

*(An LLM legitimately helps at two points that are not render-time: suggesting which preset best mimics a target guideline's layout — a one-time authoring convenience — and the pooling / GRADE computation of §9.4. Rendering itself stays deterministic.)*

### 9.3 Recommended architecture — one dataset, many views

**(1) Materialize once.** The extraction passes + `assemble_table2` already emit ONE canonical **long** relation — a `list[dict]`, one row per `(study × outcome × comparison × timepoint)` (the finest grain is the `dedupe_rows` key) — with every derivation precomputed, `result_effect` / `result_ci_p` pre-composed, and `source_quote` / `derived_stats` / `provenance` stamped. This is the single source of truth; nothing downstream recomputes an effect.

**(2) Tables are declarative view presets.** A preset is *data*, not code:

```jsonc
// LONG (default) — served directly by the UI and the existing exporters
{ "name": "long_default", "shape": "long",
  "columns": ["study_id","design","n","intervention","comparator",
              "outcome_name","outcome_instrument","outcome_timing",
              "result_effect","result_ci_p","quality_rating"] }

// WIDE — one column per outcome (Gaillard / Hicks)
{ "name": "wide_by_outcome", "shape": "wide",
  "filter": "is_subgroup == false",
  "row_key": ["study_id"],
  "pivot": { "on": ["outcome_name"], "measures": ["result_effect","result_ci_p"] },
  "columns": ["study_id","design","n","intervention","comparator","<pivoted outcome columns>"],
  "labels": { "quality_rating": "Certainty" } }

// NESTED — outcomes folded into one cell per study (Bower)
{ "name": "nested_outcomes", "shape": "nested",
  "group": { "by": ["study_id"], "into": "outcomes_cell",
             "template": "{outcome_instrument} {outcome_timing}: {result_effect}; {result_ci_p}" },
  "columns": ["study_id","population","intervention","comparator",
              "outcomes_cell","statistical_method","quality_rating"],
  "labels": { "quality_rating": "Risk of Bias Assessment", "outcomes_cell": "Fatigue Outcomes" } }
```

A preset specifies: `columns` (+ per-view `labels`, so `quality_rating` renders as "Quality of evidence" for Shah or "Risk of Bias Assessment" for Bower), a `filter`, a `shape` (`long` / `wide` / `nested`), the `pivot` dimension + measures (wide), the `group` key + cell `template` (nested), optional merged-vs-split columns, and formatting.

**(3) Two consumers of presets.**
- A **dynamic-table UI** serves interactive SELECT / FILTER / sort and the LONG view straight off the relation — the "serve columns" story, fully realized, for free.
- A **thin deterministic projector** (`pivot_long_to_wide`, `group_into_cells` — roughly two functions) applies a preset's PIVOT / NEST and feeds the result to the existing exporters. Because `assemble_table2` already emits `list[dict]` and `export_csv` / `export_xlsx` already consume `list[dict]` keyed off `data[0].keys()`, a **long** preset flows through today's exporters unchanged, and a **wide** / **nested** preset just hands them the *projected* `list[dict]`. A nested Word/CSV export is "run the projector, hand the result to the existing exporter."

```
outcomes[] extract ─┐
study-level pull ───┴─► assemble_table2 ─► CANONICAL LONG relation (list[dict])
                                              │
                        ┌─────────────────────┼─────────────────────────┐
                        ▼                      ▼                         ▼
               dynamic-table UI      projector: PIVOT / NEST        long as-is
              (select/filter/sort)   per view preset                    │
                        │                      │                         │
                        ▼                      ▼                         ▼
                interactive screen     export_docx / export_xlsx / export_csv
```

### 9.4 Layer A vs Layer B — project vs compute (and product-type gating)

- **Layer A — per-study tables (T1–T4) are projections of the one relation.** Characteristics (T1), evidence (T2), RoB-as-column (T3-in-T2), diagnostic/prognostic (T4) are all SELECT / FILTER / PIVOT / NEST views over the same long relation. (Yu's characteristics-only "Table 2" is just a preset that selects the study-level columns and zero outcome columns — not a different mechanism.)
- **Layer B — body-of-evidence tables (T5 GRADE, T6 recommendations) are COMPUTED across studies and cannot come from column selection.** A pooled per-1000 effect, a GRADE certainty, or a recommendation strength is a function of the *whole set* of studies, not an attribute of any extracted row — no SELECT/PIVOT/NEST can synthesize it. Those need the pooling / GRADE agents. **The line:** Layer A projects the extracted relation; Layer B computes new relations, which can then themselves be projected.
- **The fusion trap.** Shah's printed "Table 2" fuses per-study HRs (Layer A) with **pooled** per-1000 effects (Layer B). Under this architecture that one printed table is a **composition of two presets over two relations** (a per-study view + a body-of-evidence view), stitched at the view — *not* evidence that Layer A must pool. Keep the relations separate; compose at render.
- **Product-type gating.** Rapid updates carry no evidence table (§8.3), so the orchestrator detects product type and **skips the Layer A evidence-table presets** for a rapid update while still running the recommendation path.

### 9.5 Tradeoffs

Recommendation = dynamic-table + declarative presets; alternative = bespoke per-table render code.

| Dimension | Dynamic-table + declarative presets (recommended) | Bespoke per-table render code |
|---|---|---|
| Interactive reviewer flexibility | High — select/filter/reorder/sort live off the relation | Low — each table is a fixed artifact; change = code |
| Handling column variability | High — a new column subset is a new preset (data) | Medium — every variant is a code branch |
| Reproducing a guideline's *exact* layout | Medium–High — long/wide/nested + merged-columns + labels cover the observed corpus; pixel-exact quirks may need a preset extension | High — bespoke code matches anything, at per-table cost |
| Engineering cost / scaling to N tables | Low, sublinear — build the projector once; each table is a config block | High, ~linear — each table is code to write, test, maintain |
| Static export fidelity (Word/CSV) | High and cheap — long flows through existing exporters; wide/nested run the projector then the same exporters | High, but re-implemented per table |
| UI-platform lock-in | Isolated — presets + projector are plain Python/JSON and own PIVOT/NEST, so **static export never depends on the UI**; the UI is a swappable consumer | Low external dependency, but high bespoke-code surface |
| Auditability / provenance | High — every cell traces to long rows carrying `source_quote` / `derived_stats` / `provenance` | Depends on each builder's discipline |
| Failure blast radius | A preset bug affects one view; the relation is intact | A builder bug can silently miscompose a table |

**Verdict:** extract-once → one canonical long relation → declarative presets, with a thin deterministic projector owning PIVOT and NEST so the wide/nested/static outputs never depend on the interactive platform.

### 9.6 Capabilities a dynamic-table layer must have (vendor-neutral)

A platform — bought or built — is a fit iff it exposes:
1. **Column select / reorder / relabel.**
2. **Row filter / sort.**
3. **Pivot (long → wide)** — promote a chosen dimension to columns (required for the Gaillard/Hicks per-outcome columns).
4. **Group + aggregate-into-cell** — fold a group's rows into one composed cell (required for the Bower nested cells).
5. **Merged-vs-split columns.**
6. **Standalone static export** — deterministic Word/CSV/xlsx from the projected `list[dict]`, runnable *without* the interactive layer.

Explicit non-requirements: **no compute-across-studies** capability (that is Layer B), and **no render-time model**.

---

## 10. Reference implementation — single self-contained Python module

`llm_call` is injected as a parameter; there are **no framework, HTTP, or database imports**. Standard library only. Copy this block and run it; the `__main__` self-checks pass on CPython 3.11+ (verified on 3.13).

```python
"""
table2_reference.py — Reference implementation for the "Table 2" (per-study
evidence table) build.

Table 2 is the PER-STUDY EVIDENCE TABLE: one row = (study x outcome x comparison x
timepoint), transcribing each study's REPORTED results (intervention vs comparator,
per outcome, with instrument, timepoint, effect + CI/p). It is almost entirely DIRECT
EXTRACTION. Pooling / GRADE / meta-analysis belong to a different table and are
explicitly OUT OF SCOPE here.

This module implements every part of Table 2 that is NOT pure extraction:
  * building the study_id label,
  * canonicalizing effect-measure names + their families / null values,
  * inferring the direction of effect from an estimate + CI,
  * parsing a free-text "effect cell" into structured stats (with a faithful p-operator),
  * filling ONLY the missing stat from the reported ones (never overwriting/fabricating),
  * mapping a risk-of-bias overall label onto a 3-level quality band (tool-routed),
  * seeding a 1-element outcomes[] from single-outcome universal fields,
  * exploding + de-duplicating rows,
  * the dual-mode merge of injected vs self-extracted tags,
  * the pure-Python top-level assembler (NO model call), and
  * a tiny orchestrator showing isolation-mode vs injected-mode.

Design rules honored throughout:
  - NEVER fabricate: absent parts stay None; we only DERIVE a stat when there is
    something valid to derive it from, and we record every derived value in a
    `derived` set so a renderer can show it differently.
  - NEVER distort a reported value: "p<0.001" is carried as (p_value=0.001,
    p_operator="lt") — the inequality is preserved, not collapsed to "=".
  - Direction semantics depend on whether the outcome is desirable (survival,
    response) or adverse (mortality, symptom burden). We take an
    `outcome_favorable_direction` hint and document the default explicitly.
  - Standard library only. Any LLM use is an injected `llm_call` callable — but the
    vast majority of this module is pure Python with no model calls at all.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Iterable, Optional


# ---------------------------------------------------------------------------
# 1. Study id
# ---------------------------------------------------------------------------

def build_study_id(authors: Any, year: Any) -> str:
    """Build a "First-author et al., YYYY" study label.

    Handles author input as a list or a delimited string. Rules:
      * 1 author           -> "Smith, YYYY"
      * 2 authors          -> "Smith & Jones, YYYY"
      * 3+ authors         -> "Smith et al., YYYY"
      * missing/blank year -> the year (and its comma) is omitted, e.g. "Smith et al."
      * no usable authors   -> "Unknown study" (+ year if present)

    We only ever use each author's SURNAME. This is a display convenience, not a
    citation parser — it must never fabricate an author who is not present.
    """
    names = _normalize_authors(authors)
    yr = _clean_year(year)
    yr_suffix = f", {yr}" if yr else ""

    if not names:
        return f"Unknown study{yr_suffix}"
    if len(names) == 1:
        core = _surname(names[0])
    elif len(names) == 2:
        core = f"{_surname(names[0])} & {_surname(names[1])}"
    else:
        core = f"{_surname(names[0])} et al."
    return f"{core}{yr_suffix}"


def _normalize_authors(authors: Any) -> list[str]:
    """Coerce list/str author input into a clean list of non-empty name strings.

    Delimiter precedence avoids the Vancouver double-count trap: a "Family, Given;
    Family, Given" list must split on ';' (or ' and '/'&'), NOT on the commas that
    separate each surname from its initials. We only fall back to comma-splitting
    when no stronger delimiter is present; a lone "Smith, JQ" then yields one author
    because the initials fragment is filtered out.
    """
    if authors is None:
        return []
    if isinstance(authors, (list, tuple)):
        raw_list = [str(a) for a in authors]
    else:
        s = str(authors)
        if ";" in s:
            raw_list = re.split(r"\s*;\s*", s)
        elif re.search(r"\band\b|&", s):
            raw_list = re.split(r"\s*(?:\band\b|&)\s*", s)
        else:
            raw_list = re.split(r"\s*,\s*", s)
    out: list[str] = []
    for a in raw_list:
        a = a.strip()
        # Drop initials-only fragments left over from splitting a "Family, Initials"
        # string (e.g. the "JQ" in "Smith, JQ").
        if a and not re.fullmatch(r"[A-Z]\.?(?:\s*[A-Z]\.?)*", a):
            out.append(a)
    return out


def _surname(name: str) -> str:
    """Best-effort surname extraction from a single author string."""
    name = name.strip().strip(".")
    if not name:
        return ""
    if "," in name:                       # "Family, Given/Initials" -> family before comma
        return name.split(",")[0].strip()
    parts = name.split()
    if len(parts) >= 2 and re.fullmatch(r"[A-Z]\.?(?:[A-Z]\.?)*", parts[-1]):
        return parts[0]                   # "Smith JQ" -> "Smith"
    return parts[-1]                       # "Jane Q. Smith" -> "Smith"


def _clean_year(year: Any) -> str:
    """Return a 4-digit year string if one can be recovered, else ""."""
    if year is None:
        return ""
    m = re.search(r"(1[89]\d{2}|20\d{2})", str(year))
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# 2. Effect-measure families + canonicalization
# ---------------------------------------------------------------------------

# family in {"ratio", "difference", "time_to_event", "narrative", "unknown"}.
# time_to_event (HR) is mathematically a ratio; we keep it distinct only so callers
# can label it, and null_value_for treats it as a ratio (null = 1, log scale).
METRIC_FAMILIES: dict[str, str] = {
    "HR": "time_to_event",
    "OR": "ratio",
    "RR": "ratio",
    "IRR": "ratio",
    "MD": "difference",
    "SMD": "difference",
    "RD": "difference",
}

# Synonym -> canonical token. Matched case-insensitively after whitespace collapse
# and apostrophe normalization. "ARD"/"absolute risk difference" collapse to RD.
_METRIC_SYNONYMS: dict[str, str] = {
    "hr": "HR", "hazard ratio": "HR",
    "or": "OR", "odds ratio": "OR",
    "rr": "RR", "risk ratio": "RR", "relative risk": "RR",
    "irr": "IRR", "rate ratio": "IRR", "incidence rate ratio": "IRR",
    "md": "MD", "mean difference": "MD",
    "wmd": "MD", "weighted mean difference": "MD",
    "smd": "SMD",
    "standardized mean difference": "SMD", "standardised mean difference": "SMD",
    "cohen's d": "SMD", "cohens d": "SMD",
    "hedges g": "SMD", "hedges' g": "SMD", "hedges's g": "SMD",
    "rd": "RD", "risk difference": "RD",
    "absolute risk difference": "RD", "absolute difference": "RD", "ard": "RD",
}


def canonicalize_metric(raw: Any) -> tuple[Optional[str], str]:
    """Map a raw effect-measure name onto (canonical_metric, family).

      * Recognized synonym -> ("HR"/"OR"/"RR"/"IRR"/"MD"/"SMD"/"RD", family).
      * A narrative marker ("narrative", "NR", "not reported", "qualitative") ->
        (None, "narrative") so the renderer knows there is no numeric effect.
      * Empty / None        -> (None, "unknown").
      * Anything else       -> (raw_stripped, "unknown").
    """
    if raw is None:
        return None, "unknown"
    s = re.sub(r"\s+", " ", str(raw)).strip()
    if not s:
        return None, "unknown"

    low = s.lower().strip(".").replace("’", "'")   # normalize curly apostrophes

    narrative_markers = {
        "narrative", "qualitative", "descriptive", "nr", "not reported",
        "not estimable", "ne", "n/a", "na",
    }
    if low in narrative_markers:
        return None, "narrative"
    if low in _METRIC_SYNONYMS:
        canon = _METRIC_SYNONYMS[low]
        return canon, METRIC_FAMILIES[canon]

    upper = s.upper()
    if upper in METRIC_FAMILIES:            # a bare canonical token, any case
        return upper, METRIC_FAMILIES[upper]
    return s, "unknown"


def null_value_for(family: str) -> Optional[float]:
    """No-effect null value: ratio/time_to_event -> 1.0; difference -> 0.0; else None."""
    if family in ("ratio", "time_to_event"):
        return 1.0
    if family == "difference":
        return 0.0
    return None


# ---------------------------------------------------------------------------
# 3. Direction of effect
# ---------------------------------------------------------------------------

FAVOURS_INTERVENTION = "favours_intervention"
FAVOURS_COMPARATOR = "favours_comparator"
NO_DIFFERENCE = "no_difference"
NOT_ESTIMABLE = "not_estimable"

DIRECTIONS = (FAVOURS_INTERVENTION, FAVOURS_COMPARATOR, NO_DIFFERENCE, NOT_ESTIMABLE)


def infer_direction(
    family: str,
    estimate: Optional[float],
    ci_lower: Optional[float],
    ci_upper: Optional[float],
    reported_direction: Optional[str] = None,
    outcome_favorable_direction: str = "lower",
) -> str:
    """Infer which arm an effect favours. Returns one of DIRECTIONS.

    DIRECTION SEMANTICS DEPEND ON THE OUTCOME.
    ------------------------------------------
    A value below the null does not universally mean "intervention is better"; it
    depends on whether the outcome is DESIRABLE or ADVERSE:

      * outcome_favorable_direction="lower"  (DEFAULT): a SMALLER value is good for
        the intervention — the right default for the ADVERSE / symptom-burden
        outcomes commonly tabulated (mortality, relapse, fatigue score, pain,
        progression), where HR/OR/RR < 1 or MD < 0 => favours_intervention.
      * outcome_favorable_direction="higher": a LARGER value is good for the
        intervention (survival probability, response rate, QoL score). An estimate
        ABOVE the null then favours the intervention.
      * "neutral"/None: desirability unknown. We can still detect whether the CI
        excludes the null, but we do NOT guess which arm wins — we return
        not_estimable and defer to reported_direction / the model's source_quote.

    Boundary rule (documented convention): a CI bound sitting exactly ON the null
    (ratio CI 1.0-1.5, difference CI 0.0-0.5) is treated as no_difference — touching
    the null is the standard non-significant reading.

    Reconciliation: a confidently parsed reported_direction WINS (authors know their
    own sign conventions); otherwise the CI/estimate computation is used; otherwise
    not_estimable.
    """
    reported = _parse_reported_direction(reported_direction)

    null = null_value_for(family)
    if family in ("narrative", "unknown") or null is None:
        return reported or NOT_ESTIMABLE

    lo, hi = _order_ci(ci_lower, ci_upper)
    computed: Optional[str] = None
    if lo is not None and hi is not None:
        if lo > null and hi > null:
            computed = _side_to_favour("above", outcome_favorable_direction)
        elif lo < null and hi < null:
            computed = _side_to_favour("below", outcome_favorable_direction)
        else:
            computed = NO_DIFFERENCE          # CI includes/touches null => not significant
    elif estimate is not None:
        if estimate > null:
            computed = _side_to_favour("above", outcome_favorable_direction)
        elif estimate < null:
            computed = _side_to_favour("below", outcome_favorable_direction)
        else:
            computed = NO_DIFFERENCE

    if reported is not None:
        return reported
    return computed if computed is not None else NOT_ESTIMABLE


def _side_to_favour(side: str, favorable_direction: Optional[str]) -> str:
    """Translate 'above'/'below' the null into which arm it favours."""
    fd = (favorable_direction or "").strip().lower()
    if fd == "lower":
        return FAVOURS_INTERVENTION if side == "below" else FAVOURS_COMPARATOR
    if fd == "higher":
        return FAVOURS_INTERVENTION if side == "above" else FAVOURS_COMPARATOR
    return NOT_ESTIMABLE                       # desirability unknown -> cannot assign an arm


def _parse_reported_direction(reported: Optional[str]) -> Optional[str]:
    """Normalize a free-text reported direction into a canonical constant, or None.

    Short tokens ("ns", "ne", "nr", "na", "null") are matched only as WHOLE WORDS —
    substring matching would mislabel benign words ("consistent" contains "ns",
    "generated" contains "ne"), and because reported-direction wins, that would
    silently flip a significant result to no_difference.
    """
    if not reported:
        return None
    r = str(reported).strip().lower()
    if r in DIRECTIONS:
        return r
    # Unambiguous multi-word phrases: substring is safe.
    if any(k in r for k in ("favours intervention", "favors intervention",
                            "favour treatment", "favor treatment",
                            "intervention better", "in favour of intervention",
                            "in favor of intervention")):
        return FAVOURS_INTERVENTION
    if any(k in r for k in ("favours comparator", "favors comparator",
                            "favours control", "favors control",
                            "control better", "comparator better", "placebo better")):
        return FAVOURS_COMPARATOR
    if any(k in r for k in ("no difference", "no significant",
                            "non-significant", "not significant")):
        return NO_DIFFERENCE
    if any(k in r for k in ("not estimable", "not reported", "cannot be estimated")):
        return NOT_ESTIMABLE
    # Short tokens only as whole words.
    tokens = set(re.findall(r"[a-z']+", r))
    if tokens & {"ns", "null"}:
        return NO_DIFFERENCE
    if tokens & {"ne", "nr", "na"}:
        return NOT_ESTIMABLE
    return None


def _order_ci(lo: Optional[float], hi: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    """Return CI bounds in (lower, upper) order, tolerating swapped inputs."""
    if lo is not None and hi is not None and lo > hi:
        return hi, lo
    return lo, hi


# ---------------------------------------------------------------------------
# 4. Parsing a free-text effect cell
# ---------------------------------------------------------------------------

_NUM = r"[-+]?\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?|[-+]?\d*\.\d+|[-+]?\d+"
_CI_SEP = r"\s*(?:–|—|-|to|,)\s*"    # en/em dash, hyphen, "to", comma
_P_OP = {"<": "lt", "<=": "le", "≤": "le", ">": "gt", ">=": "ge", "≥": "ge", "=": "eq"}


def parse_effect_cell(text: Any) -> Optional[dict[str, Any]]:
    """Parse a reported effect string into
    {estimate, ci_lower, ci_upper, p_value, p_operator}.

    Handles, e.g.:
      "0.72 (95% CI 0.55-0.94), p=0.01"        -> est .72, CI .55-.94, p=0.01 (eq)
      "HR 0.72 [0.55, 0.94]"                    -> est .72, CI .55-.94
      "MD -3.4 (-5.1 to -1.7); p < 0.001"       -> est -3.4, CI, p=0.001 (lt)
      "HR 0.68, 95% CI 0.55 to 0.94"            -> unbracketed CI fallback
      "p<0.001"                                 -> only p populated (operator lt)

    Rules:
      * Absent parts stay None. We NEVER guess a missing bound or p.
      * The p-value requires an explicit comparator ('p=' / 'p<' / 'p<='); a bare
        'p 0.7' or an embedded 'grp2'/'group1' is NOT read as a p-value, and any
        captured value outside (0, 1] is rejected.
      * Thousands separators are stripped; en/em-dash / hyphen / "to" / comma are
        all accepted CI separators.
      * A leading metric token (HR/OR/RR/...) is ignored here — canonicalize_metric
        owns the metric name; this function only extracts numbers.
      * Returns None if the text contains no parseable value at all.
    """
    if text is None:
        return None
    s = re.sub(r"\s+", " ", str(text)).strip()
    if not s:
        return None

    result: dict[str, Any] = {
        "estimate": None, "ci_lower": None, "ci_upper": None,
        "p_value": None, "p_operator": None,
    }
    work = s

    # --- p-value: boundary before 'p', MANDATORY comparator, value in (0, 1].
    p_match = re.search(
        r"(?:^|[^A-Za-z])[pP]\s*(<=|>=|≤|≥|<|>|=)\s*(" + _NUM + r")", s
    )
    if p_match:
        pv = _to_float(p_match.group(2))
        if pv is not None and 0.0 < pv <= 1.0:
            result["p_value"] = pv
            result["p_operator"] = _P_OP.get(p_match.group(1), "eq")
            work = (work[: p_match.start()] + " " + work[p_match.end():]).strip()

    # --- CI: a bracketed pair "(lo <sep> hi)" or "[lo <sep> hi]".
    ci_match = re.search(
        r"[\(\[]\s*(?:\d{1,3}%?\s*(?:CI|confidence interval)[:\s]*)?"
        r"(" + _NUM + r")" + _CI_SEP + r"(" + _NUM + r")\s*[\)\]]",
        work, flags=re.IGNORECASE,
    )
    if not ci_match:
        # Fallback: unbracketed "95% CI a to b" — requires the CI keyword as an anchor.
        ci_match = re.search(
            r"(?:\d{1,3}\s*%?\s*)?(?:CI|confidence interval)[:\s]*"
            r"(" + _NUM + r")" + _CI_SEP + r"(" + _NUM + r")",
            work, flags=re.IGNORECASE,
        )
    if ci_match:
        lo = _to_float(ci_match.group(1))
        hi = _to_float(ci_match.group(2))
        result["ci_lower"], result["ci_upper"] = _order_ci(lo, hi)
        work = (work[: ci_match.start()] + " " + work[ci_match.end():]).strip()

    # --- estimate: the first standalone number left in the (metric-stripped) text.
    est_match = re.search(_NUM, work)
    if est_match:
        result["estimate"] = _to_float(est_match.group(0))

    if all(v is None for v in result.values()):
        return None
    return result


def _to_float(token: Any) -> Optional[float]:
    """Parse a numeric token (optional thousands separators) into float, or None."""
    if token is None:
        return None
    t = str(token).replace(",", "").strip()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _split_p(value: Any) -> tuple[Optional[float], Optional[str]]:
    """Split a reported p value (which may carry an inequality) into (float, operator).

    "<0.001" -> (0.001, "lt");  "0.03" -> (0.03, "eq");  "p<=0.05" -> (0.05, "le");
    "NS"/"" -> (None, None). Values outside (0, 1] are rejected.
    """
    if value is None:
        return None, None
    if isinstance(value, (int, float)):
        v = float(value)
        return (v, "eq") if 0.0 < v <= 1.0 else (None, None)
    s = str(value).strip()
    m = re.search(r"(<=|>=|≤|≥|<|>|=)?\s*(" + _NUM + r")", s)
    if not m:
        return None, None
    v = _to_float(m.group(2))
    if v is None or not (0.0 < v <= 1.0):
        return None, None
    return v, _P_OP.get(m.group(1) or "=", "eq")


# ---------------------------------------------------------------------------
# 5. Statistical reconciliation (fill ONLY missing values)
# ---------------------------------------------------------------------------

_Z_95 = 1.959964    # two-sided 95% normal quantile


def _phi(x: float) -> float:
    """Standard normal CDF Phi(x) via the error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _inv_phi(p: float) -> Optional[float]:
    """Inverse standard normal CDF via Acklam's rational approximation (no scipy)."""
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
    p_low, p_high = 0.02425, 1.0 - 0.02425
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    if p <= p_high:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
               (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1.0)
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
            ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)


def _z_from_p(p: float) -> Optional[float]:
    """Two-sided p -> |Z|. Solves p = 2*(1 - Phi(z)); None for p outside (0, 1)."""
    if p is None or p <= 0.0 or p >= 1.0:
        return None
    return _inv_phi(1.0 - p / 2.0)


def reconcile_stats(
    estimate: Optional[float],
    ci_lower: Optional[float],
    ci_upper: Optional[float],
    p_value: Optional[float],
    family: str,
    p_operator: Optional[str] = "eq",
) -> dict[str, Any]:
    """Fill ONLY missing stats from the reported ones. Never overwrite a reported value.

    Returns {estimate, ci_lower, ci_upper, p_value, p_operator, se, derived} where
    `derived` names which of {ci_lower, ci_upper, p_value} we computed.

    Math (z = 1.959964 for two-sided 95%):
      * ratio / time_to_event -> LOG scale:
          - CI present:            SE = (ln(hi) - ln(lo)) / (2z)
          - CI missing, est + EXACT p:  SE = |ln(est)| / z_from_p(p);
                                        CI = exp(ln(est) +/- z*SE)
          - p missing, CI/SE present:   Z = ln(est) / SE; p = 2*(1 - Phi(|Z|))
      * difference -> identical arithmetic with the identity function instead of ln.

    Guards (skip that derivation, inputs unchanged):
      * ratio/time_to_event with non-positive estimate or CI bound -> cannot take a log.
      * p missing/<=0/>=1, or p is a BOUND (operator lt/gt/le/ge) -> cannot invert to
        an exact Z, so p is not used to derive an SE.
      * a null-valued estimate (HR/RR/OR = 1, MD = 0) with only a p -> genuinely
        under-determined (ln(1)=0, so SE = 0/z); we intentionally leave the CI blank
        rather than fabricate. This is a documented limitation, not an oversight.
      * nothing to derive from -> no fabrication.
    """
    is_log = family in ("ratio", "time_to_event")
    is_diff = family == "difference"
    derived: set[str] = set()
    out: dict[str, Any] = {
        "estimate": estimate, "ci_lower": ci_lower, "ci_upper": ci_upper,
        "p_value": p_value, "p_operator": p_operator if p_value is not None else None,
        "se": None, "derived": derived,
    }
    if not (is_log or is_diff):
        return out                              # narrative/unknown: no arithmetic

    ci_lower, ci_upper = _order_ci(ci_lower, ci_upper)
    out["ci_lower"], out["ci_upper"] = ci_lower, ci_upper

    if is_log:
        def fwd(v: Optional[float]) -> Optional[float]:
            return math.log(v) if (v is not None and v > 0) else None
        inv = math.exp
    else:
        def fwd(v: Optional[float]) -> Optional[float]:
            return v
        def inv(v: float) -> float:
            return v

    t_est = fwd(estimate) if estimate is not None else None
    t_lo = fwd(ci_lower) if ci_lower is not None else None
    t_hi = fwd(ci_upper) if ci_upper is not None else None

    if is_log:
        if estimate is not None and t_est is None:
            return out                          # non-positive estimate on log scale
        if (ci_lower is not None and t_lo is None) or (ci_upper is not None and t_hi is None):
            t_lo = t_hi = None                  # non-positive CI bound -> drop CI reasoning

    se: Optional[float] = None
    if t_lo is not None and t_hi is not None:                       # (a) SE from CI
        se = (t_hi - t_lo) / (2.0 * _Z_95)
    if se is None and t_est is not None and p_value is not None and p_operator == "eq":
        z_p = _z_from_p(p_value)                                    # (b) SE from est + EXACT p
        if z_p and z_p != 0 and abs(t_est) > 0:
            se = abs(t_est) / z_p
    out["se"] = se

    if se is not None and t_est is not None and ci_lower is None and ci_upper is None:
        lo, hi = _order_ci(inv(t_est - _Z_95 * se), inv(t_est + _Z_95 * se))
        out["ci_lower"], out["ci_upper"] = lo, hi
        derived.update({"ci_lower", "ci_upper"})

    if p_value is None and se is not None and se > 0 and t_est is not None:
        z = t_est / se
        p = 2.0 * (1.0 - _phi(abs(z)))
        out["p_value"] = min(max(p, 1e-300), 1.0 - 1e-16)          # never exactly 0/1
        out["p_operator"] = "eq"
        derived.add("p_value")

    return out


# ---------------------------------------------------------------------------
# 6. Quality rating (risk-of-bias overall -> 3-level quality band)
# ---------------------------------------------------------------------------

_QUALITY_HIGH = "High"
_QUALITY_INTERMEDIATE = "Intermediate"
_QUALITY_LOW = "Low"


def map_quality_rating(rob_overall: Any, rob_tool: Optional[str] = None) -> Optional[str]:
    """Map a risk-of-bias overall label onto a 3-level study-quality band.

    Returns "High" | "Intermediate" | "Low" | None.

    THE MAPPING IS AN INVERSION for risk-of-bias tools: LOW risk of bias => HIGH
    quality. AMSTAR-2 is NOT inverted — it reports a *confidence* rating that already
    runs in the quality direction (High confidence => High quality). Because "High"
    and "Low" mean opposite things across the two scales, `rob_tool` is required to
    resolve a bare "High"/"Low"; without it, only unambiguous labels resolve and a
    bare "High"/"Low" returns None rather than risk a silent inversion.

      RoB 2:        Low->High; "Some concerns"->Intermediate; High->Low
      ROBINS-I:     Low->High; Moderate->Intermediate; Serious/Critical->Low
      QUADAS(-2/3): Low->High; Unclear/"Insufficient information"->Intermediate; High->Low
      AMSTAR-2:     High->High; Moderate->Intermediate; Low/"Critically low"->Low
    """
    if rob_overall is None:
        return None
    label = re.sub(r"\s+", " ", str(rob_overall)).strip().lower()
    tool = (rob_tool or "").strip().lower()

    if label == "moderate":                     # Intermediate on every scale — safe
        return _QUALITY_INTERMEDIATE

    if tool:
        if "amstar" in tool:                    # confidence scale (not inverted)
            if label == "high":
                return _QUALITY_HIGH
            if label in ("low", "critically low"):
                return _QUALITY_LOW
            return None
        # RoB 2 / ROBINS-I / QUADAS — inverted bias scale
        if label == "low":
            return _QUALITY_HIGH
        if label in ("some concerns", "some concern", "unclear", "insufficient information"):
            return _QUALITY_INTERMEDIATE
        if label in ("high", "serious", "critical"):
            return _QUALITY_LOW
        return None

    # No tool: only unambiguous labels resolve.
    if label == "critically low":               # exists only on the AMSTAR-2 scale
        return _QUALITY_LOW
    if label in ("some concerns", "some concern", "unclear", "insufficient information"):
        return _QUALITY_INTERMEDIATE
    if label in ("serious", "critical"):        # exist only on the ROBINS-I scale
        return _QUALITY_LOW
    return None                                  # bare high/low without a tool -> ambiguous


# ---------------------------------------------------------------------------
# 7. Seeding outcomes[] from single-outcome universal fields
# ---------------------------------------------------------------------------

def seed_outcomes_from_universal(tags: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a 1-element outcomes[] from single-outcome universal fields (no model call).

      primary_outcome_definition   -> name
      primary_outcome_measurement  -> instrument
      primary_outcome_timing       -> timing   (falls back to follow_up_duration)
      key_findings_effect_estimate -> effect_estimate
      key_findings_metric          -> effect_metric
      key_findings_ci_lower/upper  -> ci_lower/ci_upper
      key_findings_pvalue          -> p_value (+ p_operator, parsed from any inequality)
      key_findings_direction       -> direction (reported; else inferred at explode time)

    Returns [] when there is no primary-outcome signal at all (no name AND no effect).
    `comparison` is left None here — it is filled from population_comparator at explode.
    Seeded rows carry no verbatim source_quote and no per-outcome confidence (both
    None): those only exist once the extraction pass has run.
    """
    name = _get(tags, "primary_outcome_definition")
    metric = _get(tags, "key_findings_metric")
    estimate = _coerce_num(_get(tags, "key_findings_effect_estimate"))
    if not name and estimate is None and not metric:
        return []

    p_val, p_op = _split_p(_get(tags, "key_findings_pvalue"))
    return [{
        "name": name,
        "instrument": _get(tags, "primary_outcome_measurement"),
        "timing": _get(tags, "primary_outcome_timing") or _get(tags, "follow_up_duration"),
        "comparison": None,
        "effect_metric": metric,
        "effect_estimate": estimate,
        "direction": _get(tags, "key_findings_direction"),
        "ci_lower": _coerce_num(_get(tags, "key_findings_ci_lower")),
        "ci_upper": _coerce_num(_get(tags, "key_findings_ci_upper")),
        "p_value": p_val,
        "p_operator": p_op,
        "source_quote": None,
        "confidence": None,
        "is_subgroup": False,
        "subgroup_label": None,
    }]


# ---------------------------------------------------------------------------
# 8. N cell + display composers
# ---------------------------------------------------------------------------

def format_n_cell(study_type: Any, sample_size_total: Any, included_studies_n: Any) -> str:
    """Compose the "N" sub-cell, whose meaning depends on the design.

    For a systematic review / meta-analysis "study" row, N is a count of pooled
    STUDIES (k), optionally with pooled participants: "k=12 (N=3450)". For a primary
    study it is the participant total. This keeps the reader from confusing k with N.
    """
    st = (str(study_type) or "").lower()
    k = _coerce_num(included_studies_n)
    n = _coerce_num(sample_size_total)
    is_review = "systematic review" in st or "meta-analysis" in st or "meta analysis" in st
    if is_review and k is not None:
        return f"k={_fmt_num(k)} (N={_fmt_num(n)})" if n is not None else f"k={_fmt_num(k)}"
    if n is not None:
        return _fmt_num(n)
    return f"k={_fmt_num(k)}" if k is not None else ""


# ---------------------------------------------------------------------------
# 9. Exploding rows + dedupe
# ---------------------------------------------------------------------------

def explode_rows(
    study_level: dict[str, Any],
    outcomes: Iterable[dict[str, Any]],
    provenance: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Explode one study into flat Table 2 rows — one per outcome object.

    Study-level fields are DENORMALIZED (repeated) across every outcome row so each
    row is display-complete. For each outcome we canonicalize the metric + family,
    reconcile stats (filling only the missing CI or p), infer direction, compose the
    `result_effect` / `result_ci_p` display strings, and carry the subgroup flag +
    provenance. Effects are reported AS REPORTED — never pooled. Narrative-only
    outcomes (no numeric estimate) fall back to their text; CI/p stay blank.
    """
    study_id = study_level.get("study_id") or build_study_id(
        study_level.get("citation_authors"), study_level.get("citation_year"))
    default_comparison = study_level.get("population_comparator")
    n_cell = format_n_cell(
        study_level.get("study_type") or study_level.get("design"),
        study_level.get("sample_size_total"),
        study_level.get("included_studies_n"),
    )

    rows: list[dict[str, Any]] = []
    for oc in outcomes:
        canon_metric, family = canonicalize_metric(oc.get("effect_metric"))
        comparison = oc.get("comparison") or default_comparison
        p_val, p_op = _read_p(oc)

        rec = reconcile_stats(
            _coerce_num(oc.get("effect_estimate")),
            _coerce_num(oc.get("ci_lower")),
            _coerce_num(oc.get("ci_upper")),
            p_val, family, p_op,
        )
        direction = infer_direction(
            family, rec["estimate"], rec["ci_lower"], rec["ci_upper"],
            reported_direction=oc.get("direction"),
            outcome_favorable_direction=oc.get("favorable_direction", "lower"),
        )
        rows.append({
            # study-level (denormalized)
            "study_id": study_id,
            "design": study_level.get("study_type") or study_level.get("design"),
            "population": study_level.get("population_participants"),
            "n": n_cell,
            "eligibility_threshold": study_level.get("eligibility_threshold"),
            "intervention": study_level.get("population_intervention_exposure"),
            "comparator": comparison,
            "statistical_method": study_level.get("statistical_method"),
            "quality_rating": study_level.get("quality_rating"),
            # outcome-level
            "outcome_name": oc.get("name"),
            "outcome_instrument": oc.get("instrument"),
            "outcome_timing": oc.get("timing"),
            "comparison": comparison,
            "effect_metric": canon_metric,
            "effect_family": family,
            "effect_estimate": rec["estimate"] if rec["estimate"] is not None
                               else oc.get("effect_estimate"),
            "ci_lower": rec["ci_lower"],
            "ci_upper": rec["ci_upper"],
            "p_value": rec["p_value"],
            "p_operator": rec["p_operator"],
            "direction": direction,
            "derived_stats": sorted(rec["derived"]),
            "is_subgroup": bool(oc.get("is_subgroup") or oc.get("subgroup")),
            "subgroup_label": oc.get("subgroup_label") or oc.get("subgroup"),
            "source_quote": oc.get("source_quote"),
            "confidence": oc.get("confidence"),
            "provenance": provenance,
            # composed display strings
            "result_effect": _compose_effect(canon_metric, rec["estimate"], direction, oc),
            "result_ci_p": _compose_ci_p(rec["ci_lower"], rec["ci_upper"],
                                         rec["p_value"], rec["p_operator"]),
        })
    return rows


def _read_p(oc: dict[str, Any]) -> tuple[Optional[float], Optional[str]]:
    """Read (p_value, p_operator) from an outcome object, tolerating a string p."""
    if oc.get("p_operator") and oc.get("p_value") is not None:
        return _coerce_num(oc.get("p_value")), oc.get("p_operator")
    return _split_p(oc.get("p_value"))


def _compose_effect(metric: Optional[str], estimate: Optional[float],
                    direction: str, oc: dict[str, Any]) -> str:
    """Compose `result_effect`, e.g. 'HR 0.72 (favours intervention)'.

    Narrative-only outcomes (no numeric estimate) fall back to their source text.
    """
    if estimate is None:
        return (oc.get("source_quote") or oc.get("narrative")
                or (str(oc.get("effect_estimate")) if oc.get("effect_estimate") else "")
                or oc.get("name") or "").strip() or "Not estimable"
    metric_str = f"{metric} " if metric else ""
    dir_str = {
        FAVOURS_INTERVENTION: "favours intervention",
        FAVOURS_COMPARATOR: "favours comparator",
        NO_DIFFERENCE: "no difference",
        NOT_ESTIMABLE: "",
    }.get(direction, "")
    tail = f" ({dir_str})" if dir_str else ""
    return f"{metric_str}{_fmt_num(estimate)}{tail}".strip()


def _compose_ci_p(lo: Optional[float], hi: Optional[float],
                  p: Optional[float], p_op: Optional[str]) -> str:
    """Compose `result_ci_p`, e.g. '95% CI 0.55-0.94; p<0.001'. Blank parts omitted."""
    pieces: list[str] = []
    if lo is not None and hi is not None:
        pieces.append(f"95% CI {_fmt_num(lo)}–{_fmt_num(hi)}")
    if p is not None:
        pieces.append(_fmt_p(p, p_op or "eq"))
    return "; ".join(pieces)


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop only TRULY identical rows. Keeps the first occurrence (stable).

    The key includes the subgroup flag/label and the effect metric so legitimately
    distinct analyses of one outcome survive: a subgroup row vs the main analysis,
    an adjusted vs unadjusted estimate (distinct comparison), or two metrics for one
    outcome are all kept.
    """
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        key = (r.get("study_id"), r.get("outcome_name"), r.get("comparison"),
               r.get("outcome_timing"), bool(r.get("is_subgroup")),
               r.get("subgroup_label"), r.get("effect_metric"))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# 10. Dual-mode merge
# ---------------------------------------------------------------------------

def merge_injected_and_extracted(
    injected: Optional[dict[str, Any]],
    extracted: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Merge injected (upstream) tags with self-extracted tags. Dual-mode entry point.

    PRECEDENCE:
      * Study-level scalar tags: INJECTED WINS field-by-field; a self-extracted value
        fills a gap only where the injected tag is missing/blank. (Upstream extraction
        is authoritative when present; the isolation-mode pull only fills gaps.)
      * outcomes[]: chosen as a whole, in priority order:
          1. injected["outcomes"] if a non-empty list,
          2. else extracted["outcomes"] if non-empty,
          3. else seed_outcomes_from_universal(merged).
        We never element-wise merge two arrays — two independently produced arrays
        have no shared row identity, so the higher-priority array is taken intact.
    """
    injected = injected or {}
    extracted = extracted or {}
    merged: dict[str, Any] = dict(extracted)
    for k, v in injected.items():
        if k == "outcomes":
            continue
        if _present(v):
            merged[k] = v

    inj, ext = injected.get("outcomes"), extracted.get("outcomes")
    if isinstance(inj, list) and inj:
        merged["outcomes"] = inj
    elif isinstance(ext, list) and ext:
        merged["outcomes"] = ext
    else:
        merged["outcomes"] = seed_outcomes_from_universal(merged)
    return merged


# ---------------------------------------------------------------------------
# 11. Top-level assembler (pure Python — NO LLM call)
# ---------------------------------------------------------------------------

def assemble_table2(
    study_level_tags: dict[str, Any],
    outcomes: Optional[list[dict[str, Any]]] = None,
    rob: Optional[dict[str, Any]] = None,
    provenance: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Compose the Table 2 rows for one study. Pure Python; NEVER calls the model.

    Single top-level entry point used in BOTH modes:
      1. Resolve outcomes[]: passed `outcomes`, else tags["outcomes"], else seed a
         1-element array from the universal single-outcome fields.
      2. Derive study_id (if absent) and quality_rating (from `rob`, tool-routed).
      3. explode_rows() -> one flat row per outcome, denormalizing study-level fields.
      4. dedupe_rows() -> drop only truly identical rows.
    """
    tags = dict(study_level_tags or {})
    if outcomes is None:
        outcomes = tags.get("outcomes")
    if not (isinstance(outcomes, list) and outcomes):
        outcomes = seed_outcomes_from_universal(tags)

    if not tags.get("study_id"):
        tags["study_id"] = build_study_id(tags.get("citation_authors"), tags.get("citation_year"))
    if rob:
        tags["quality_rating"] = map_quality_rating(rob.get("rob_overall"), rob.get("rob_tool"))

    return dedupe_rows(explode_rows(tags, outcomes, provenance=provenance))


# ---------------------------------------------------------------------------
# 12. Tiny orchestrator: isolation-mode vs injected-mode
# ---------------------------------------------------------------------------

def build_table2(
    paper: dict[str, Any],
    injected: Optional[dict[str, Any]] = None,
    llm_call: Optional[Callable[..., Any]] = None,
    *,
    enrich_injected: bool = False,
) -> list[dict[str, Any]]:
    """Produce Table 2 rows for one paper in either mode. Thin dispatch layer.

    INJECTED MODE (injected is not None):
        Upstream extraction already produced tags (and possibly an outcomes[] array).
        Default: ZERO model calls — assemble directly (seeding a 1-element outcomes[]
        from single-outcome universal fields if no array was injected). Opt-in
        `enrich_injected=True` runs ONLY the outcomes[] pass to discover secondary
        outcomes; injected study-level tags still win.

    ISOLATION MODE (injected is None):
        No upstream tags. Runs its OWN extraction via `llm_call`: a study-level
        characteristics pull + the outcomes[] pass. Both are pure EXTRACTION prompts
        (out of scope for this non-extraction module); their JSON funnels into the
        same assemble_table2.
    """
    if injected is not None:
        extracted: dict[str, Any] = {}
        provenance = "injected"
        has_injected_outcomes = isinstance(injected.get("outcomes"), list) and injected["outcomes"]
        if enrich_injected and not has_injected_outcomes:
            if llm_call is None:
                raise ValueError("enrich_injected=True requires an `llm_call`.")
            extracted = {"outcomes": _extract_outcomes(paper, llm_call)}
            provenance = "enriched"
        elif not has_injected_outcomes:
            provenance = "seeded"
        merged = merge_injected_and_extracted(injected, extracted)
        rob = {"rob_overall": merged.get("rob_overall"), "rob_tool": merged.get("rob_tool")}
        return assemble_table2(merged, outcomes=merged.get("outcomes"), rob=rob, provenance=provenance)

    if llm_call is None:
        raise ValueError("Isolation mode requires an `llm_call` callable to run extraction.")
    study_level = _extract_study_level(paper, llm_call)
    outcomes = _extract_outcomes(paper, llm_call)
    rob = {"rob_overall": study_level.get("rob_overall"), "rob_tool": study_level.get("rob_tool")}
    return assemble_table2(study_level, outcomes=outcomes, rob=rob, provenance="extracted")


# --- Isolation-mode extraction shims (EXTRACTION — the ONLY places a model is touched).
# Their bodies are pure extraction prompts (see the prompt section of the companion doc);
# these stubs show the contract and where llm_call plugs in. No derivation happens here.

def _extract_study_level(paper: dict[str, Any], llm_call: Callable[..., Any]) -> dict[str, Any]:
    """Study-level characteristics pull (study_id/design/population/arms/method)."""
    return llm_call(task="table2_study_level", paper=paper) or {}


def _extract_outcomes(paper: dict[str, Any], llm_call: Callable[..., Any]) -> list[dict[str, Any]]:
    """The outcomes[] pass — one object per (outcome x comparison x timepoint)."""
    result = llm_call(task="table2_outcomes", paper=paper)
    return result if isinstance(result, list) else (result or {}).get("outcomes", []) or []


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

def _get(d: dict[str, Any], key: str) -> Any:
    """dict.get that also treats empty/whitespace strings as absent (None)."""
    v = d.get(key)
    return None if isinstance(v, str) and not v.strip() else v


def _present(v: Any) -> bool:
    """True if a value counts as supplied (not None, not a blank string)."""
    return not (v is None or (isinstance(v, str) and not v.strip()))


def _coerce_num(v: Any) -> Optional[float]:
    """Coerce a value to float when possible (parsing a numeric string), else None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return _to_float(v)


def _fmt_num(v: Optional[float]) -> str:
    """Format a numeric value compactly. inf/NaN -> blank (never crashes)."""
    if v is None:
        return ""
    if not math.isfinite(v):
        return ""
    if v == int(v):
        return str(int(v))
    return f"{v:.3g}"


def _fmt_p(p: Optional[float], op: str = "eq") -> str:
    """Format a p-value faithfully, preserving any inequality operator."""
    if p is None:
        return ""
    sym = {"lt": "<", "le": "≤", "gt": ">", "ge": "≥", "eq": "="}.get(op, "=")
    if op == "eq" and p < 0.001:
        return "p<0.001"
    if p < 0.001:
        return f"p{sym}{p:.1g}"
    return f"p{sym}{p:.3f}"


# ---------------------------------------------------------------------------
# Self-check (illustrative; not a test framework). Run: python table2_reference.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # study_id — list, 2 authors, Vancouver comma list, and the single-author comma case.
    assert build_study_id(["Smith JQ", "Jones A", "Lee K"], 2021) == "Smith et al., 2021"
    assert build_study_id(["Smith JQ", "Jones A"], "2020") == "Smith & Jones, 2020"
    assert build_study_id("Jane Q. Smith", None) == "Smith"
    assert build_study_id("Smith JQ, Jones A, Lee K", 2019) == "Smith et al., 2019"   # not "et al." of 6
    assert build_study_id("Smith, JQ", 2018) == "Smith, 2018"                          # one author
    assert build_study_id("Smith, John and Jones, Amy", 2017) == "Smith & Jones, 2017"

    # metric canonicalization + null value + apostrophe normalization
    assert canonicalize_metric("hazard ratio") == ("HR", "time_to_event")
    assert canonicalize_metric("relative risk") == ("RR", "ratio")
    assert canonicalize_metric("standardised mean difference") == ("SMD", "difference")
    assert canonicalize_metric("Hedges’ g") == ("SMD", "difference")             # curly apostrophe
    assert canonicalize_metric("absolute risk difference") == ("RD", "difference")
    assert null_value_for("ratio") == 1.0 and null_value_for("difference") == 0.0

    # parse_effect_cell — bracketed, unbracketed, p-only, and the 'grp2'/'group1' trap
    assert parse_effect_cell("HR 0.72 (95% CI 0.55-0.94), p=0.01") == {
        "estimate": 0.72, "ci_lower": 0.55, "ci_upper": 0.94, "p_value": 0.01, "p_operator": "eq"}
    assert parse_effect_cell("HR 0.68, 95% CI 0.55 to 0.94")["ci_lower"] == 0.55       # unbracketed
    pp = parse_effect_cell("p<0.001")
    assert pp["p_value"] == 0.001 and pp["p_operator"] == "lt"                          # faithful bound
    assert parse_effect_cell("grp2 HR 0.7 (0.5-0.9)")["p_value"] is None               # not 'p=2'
    assert parse_effect_cell("group1 mean 5.0")["p_value"] is None

    # reported-direction whole-word safety — 'consistent'/'generated' must NOT match ns/ne
    assert _parse_reported_direction("results were consistent") is None
    assert _parse_reported_direction("effect was generated by the model") is None
    assert _parse_reported_direction("NS") == NO_DIFFERENCE

    # direction — adverse-outcome default (lower is good); CI straddle; desirable outcome
    assert infer_direction("time_to_event", 0.72, 0.55, 0.94) == FAVOURS_INTERVENTION
    assert infer_direction("ratio", 1.1, 0.8, 1.5) == NO_DIFFERENCE                     # straddles null
    assert infer_direction("ratio", 1.0, 1.0, 1.5) == NO_DIFFERENCE                     # touches null
    assert infer_direction("ratio", 1.4, 1.1, 1.8, outcome_favorable_direction="higher") == FAVOURS_INTERVENTION
    assert infer_direction("ratio", 0.5, 0.3, 0.8, outcome_favorable_direction="neutral") == NOT_ESTIMABLE

    # reconcile — derive p from HR + CI (round-trip), and preserve a reported '<' p as a bound
    rec = reconcile_stats(0.72, 0.55, 0.94, None, "time_to_event")
    assert "p_value" in rec["derived"] and 0.0 < rec["p_value"] < 0.05
    rec2 = reconcile_stats(0.72, None, None, 0.001, "time_to_event", p_operator="lt")
    assert rec2["ci_lower"] is None and "ci_lower" not in rec2["derived"]               # bound -> no SE
    rec3 = reconcile_stats(0.72, None, None, 0.01, "time_to_event", p_operator="eq")
    assert "ci_lower" in rec3["derived"] and rec3["ci_lower"] is not None               # exact p -> CI

    # quality mapping — tool-routed inversion; AMSTAR not inverted; bare High needs a tool
    assert map_quality_rating("Low", "rob2") == "High"
    assert map_quality_rating("Some concerns", "rob2") == "Intermediate"
    assert map_quality_rating("High", "robins_i") == "Low"
    assert map_quality_rating("High", "amstar2") == "High"                              # not inverted
    assert map_quality_rating("Critically low", "amstar2") == "Low"
    assert map_quality_rating("Critically low") == "Low"                               # unambiguous w/o tool
    assert map_quality_rating("High") is None                                          # ambiguous w/o tool

    # dedupe keeps a subgroup row alongside the main analysis
    base = {"study_id": "X", "outcome_name": "OS", "comparison": "A vs B",
            "outcome_timing": "12m", "effect_metric": "HR"}
    kept = dedupe_rows([
        {**base, "is_subgroup": False, "subgroup_label": None},
        {**base, "is_subgroup": True, "subgroup_label": "PD-L1>=50%"},
        {**base, "is_subgroup": False, "subgroup_label": None},   # true duplicate -> dropped
    ])
    assert len(kept) == 2

    # SR/MA N cell shows k (studies) with pooled N
    assert format_n_cell("SR with Meta-Analysis", 3450, 12) == "k=12 (N=3450)"
    assert format_n_cell("Randomized Controlled Trial", 240, None) == "240"

    # _fmt_num tolerates inf/NaN
    assert _fmt_num(float("inf")) == "" and _fmt_num(float("nan")) == ""

    # end-to-end assemble (injected mode, single universal outcome, p as '<' bound preserved)
    tags = {
        "citation_authors": ["Doe J", "Roe R"], "citation_year": 2019,
        "study_type": "Randomized Controlled Trial",
        "population_participants": "Adults with cancer-related fatigue",
        "sample_size_total": 240,
        "population_intervention_exposure": "Exercise programme",
        "population_comparator": "Usual care",
        "statistical_method": "Mixed-effects model",
        "primary_outcome_definition": "Fatigue", "primary_outcome_measurement": "BFI",
        "primary_outcome_timing": "12 weeks",
        "key_findings_metric": "MD", "key_findings_effect_estimate": -3.4,
        "key_findings_ci_lower": -5.1, "key_findings_ci_upper": -1.7,
        "key_findings_pvalue": "<0.001",
    }
    table = assemble_table2(tags, rob={"rob_overall": "Low", "rob_tool": "rob2"}, provenance="seeded")
    assert len(table) == 1
    row = table[0]
    assert row["study_id"] == "Doe & Roe, 2019"
    assert row["direction"] == FAVOURS_INTERVENTION       # MD < 0, adverse-outcome default
    assert row["quality_rating"] == "High"                # Low risk of bias -> High quality
    assert row["p_operator"] == "lt" and "p<0.001" in row["result_ci_p"]   # faithful bound
    assert row["provenance"] == "seeded"

    print("All self-checks passed.")
```

---

## 11. Test sketches (framework-free)

The `__main__` block above is the runnable test set — plain `assert`s, no pytest, runnable anywhere. It exercises the study-id rules (including the Vancouver double-count trap), metric canonicalization, the effect-cell parser (including the `grp2` → false-`p` trap and the unbracketed-CI fallback), the whole-word reported-direction safety, direction inference across favourable-direction hints and the CI-straddle/touch boundary, statistical reconciliation (derive-p round-trip; bounded-`p` skip; exact-`p` → CI), the tool-routed quality inversion (and the "bare High needs a tool" refusal), subgroup-preserving dedupe, the SR-vs-primary N cell, `inf`/`NaN` formatting safety, and an end-to-end injected-mode assemble with a faithful `p<0.001`.

---

## 12. Implementation notes for other platforms

- **No repo dependency.** The reference module imports only `math`, `re`, and `typing`. `llm_call` is injected. Copy §10 and it runs.
- **Where your extractor plugs in.** You supply two extraction routines (the §6 prompts): the `outcomes[]` pass and the study-level pull. Everything else — study-id, metric canonicalization, direction, stat reconciliation, quality mapping, row explosion, dedupe, dual-mode merge, assembly — is pure Python you can adopt as-is or reimplement in your language.
- **Field names are the contract.** The tag names in §3 (`citation_authors`, `population_intervention_exposure`, `key_findings_*`, `analysis_framework`, `rob_overall`, …) are the join keys. If your extractor uses different names, remap them into these keys before calling `assemble_table2`, or rename consistently in the module.
- **The quality rating needs the tool.** Always pass `rob_tool` alongside `rob_overall`; a bare `"High"`/`"Low"` without a tool is ambiguous (bias scale vs confidence scale) and returns `None` by design rather than risk a silent inversion.
- **Reconciliation is optional and conservative.** `reconcile_stats` only *fills* a missing CI or p and never overwrites a reported value. If your policy is "display exactly what the paper printed, blanks and all", skip it — nothing downstream requires a derived value. If you keep it, render `derived_stats` cells distinctly (e.g. greyed / daggered) so a reviewer sees what was computed.
- **Direction is a display convenience, not a significance test.** It uses a normal-approximation reading of the reported CI/estimate. It does not re-test significance or recompute anything; for the authoritative arm it defers to the reported direction / the model's `source_quote`.
- **This agent never pools.** If you find yourself computing a pooled effect, a baseline risk, an absolute difference per 1000, or a GRADE certainty, you have crossed into the body-of-evidence table — a different agent. Keep Table 2 to transcription.
