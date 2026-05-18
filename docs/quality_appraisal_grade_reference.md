# Quality Appraisal — GRADE Indirectness + Imprecision Reference

Reference for the LLM prompts and Python decision-tree rules used by The Rubric Generator's Quality Appraisal AI for the **GRADE indirectness** and **GRADE imprecision** domains. Transcribed verbatim from source on 2026-05-01.

**Sources:**
- **GRADE Indirectness** — Schünemann H, Brożek J, Guyatt G, Oxman A, eds. *GRADE Handbook for Grading Quality of Evidence and Strength of Recommendations*, indirectness chapter. <https://book.gradepro.org/guideline/indirectness>. Figure 1 of that chapter explicitly supports per-trial indirectness tables, so the per-study assessment in this module is methodologically sound.
- **GRADE Imprecision** — Murad MH, Neumann I, Brożek J, Langendam M, Dahm P, Schünemann HJ. *GRADE Handbook for Grading Quality of Evidence and Strength of Recommendations*, imprecision chapter. <https://book.gradepro.org/guideline/imprecision>. GRADE imprecision is conventionally a body-of-evidence rating; this module assesses it per-trial via CI width vs. decision thresholds + sample/event adequacy + fragility.

**Source files in repo:**
- [backend/indirectness.py](../backend/indirectness.py)
- [backend/imprecision.py](../backend/imprecision.py)
- [backend/quality_appraisal.py](../backend/quality_appraisal.py) — `compute_grade()` combiner

**Module contract** (mirrors the RoB-tool pattern):

> Each module exposes:
> - a `SUBDOMAINS` list describing per-subdomain guidance,
> - constants for `JUDGEMENT_OPTIONS` and `SEVERITY_LEVELS`,
> - `build_prompt(...)` that constructs the per-paper prompt,
> - `_normalize_judgement(raw)` that coerces output to the 4 allowed values,
> - `_judgement_severity(judgements)` — pure-Python decision tree counting reds + oranges → severity,
> - `severity_explanation(severity, counts, per_subdomain)` — one-sentence rationale for the chosen tier,
> - `run(pdf_bytes, fields, classification, primary_outcome, …)` — the orchestrator entry point,
> - `prompt_catalog()` — for the developer-view UI (transparency: any signed-in user can inspect the prompts and decision trees behind a judgement).

Both severity decision trees use the **same shape** — count `not_*` (red) and `probably_not_*` (orange) judgements across subdomains and map to GRADE downgrade levels:

```
reds=0, oranges<=1                  → none              (0 levels)
reds=0, oranges>=2                  → serious           (1 level)
reds=1 (regardless of oranges)      → serious           (1 level)
reds=2                              → very_serious      (2 levels)
reds>=3                             → extremely_serious (3 levels)
```

Per GRADE guidance: do not rate down unless concerns are likely to lead to meaningful, systematic differences — a single borderline ('probably not …') subdomain is treated as inherent uncertainty, not a reason to downgrade.

---

# Section 1 — GRADE Indirectness (single-trial PICO assessment)

## 1.1 Overview

- **Scope:** single-trial assessment of the four PICO components (Population, Intervention, Comparator, Outcome) for the target review question.
- **Subdomains:** 4 (one per PICO component).
- **Judgement scale:** 4-level (matches the GRADE-handbook green/yellow/orange/red colour scheme).
- **Severity tiers → GRADE downgrade:** 0 / 1 / 2 / 3 levels.
- **Optional run-level input:** `target_pico = {population, intervention, comparator, outcome}` — when provided, the prompt asks for judgement *against* the user's review question. When blank, the prompt falls back to assessing outcome-surrogacy against the as-conducted PICO and defaults the other three subdomains to `probably_direct` unless the as-conducted PICO is unusually narrow.

## 1.2 Judgement options + severity tiers

```python
JUDGEMENT_OPTIONS = ("direct", "probably_direct", "probably_not_direct", "not_direct")
SEVERITY_LEVELS   = ("none", "serious", "very_serious", "extremely_serious")
```

Mapping:
- `direct` — sufficiently direct
- `probably_direct` — probably sufficiently direct
- `probably_not_direct` — probably not sufficiently direct
- `not_direct` — not sufficiently direct

## 1.3 System prompt

```text
You are an evidence-synthesis methodologist assessing the GRADE indirectness domain for a single study. Read the PDF carefully. For each of the four PICO subdomains (Population, Intervention, Comparison, Outcome), judge how directly the study's evidence applies to the specified target question on a 4-level scale: 'direct' (sufficiently direct), 'probably_direct' (probably sufficiently direct), 'probably_not_direct' (probably not sufficiently direct), or 'not_direct' (not sufficiently direct). Provide a 1-2 sentence rationale per subdomain, quoting the paper where possible. Per GRADE guidance, do NOT rate down unless there are compelling reasons to believe the mismatch would lead to meaningful, systematic differences in the effect estimate. Surrogate outcomes (HbA1c, LDL, bone density, progression-free survival, etc.) should be rated 'probably_not_direct' or worse unless a strong, well-established correlation with patient-important outcomes is documented in the paper. Return ONLY a valid JSON object — no preamble, no markdown fences.
```

## 1.4 Per-paper prompt template

Built by `build_prompt(target_pico, study_type, primary_outcome, extracted_fields)`:

```text
Assess **GRADE indirectness** for the study described in the attached PDF.

Study type: {study_type}
As-conducted primary outcome: {primary_outcome}

{target_block}

Context (fields already extracted from the paper):
{ctx_json}

Subdomains to judge:
{subdomains_block}

Return a JSON object with exactly this shape:
{shape}

For each subdomain, weigh whether the mismatch is likely to produce systematic differences in effect estimates. Default to 'probably_direct' rather than 'direct' when there is any meaningful uncertainty. Reserve 'not_direct' for clear, substantial mismatches. Rationales must quote the paper verbatim where possible.
```

### `target_block` — when target PICO is supplied

```text
Target question (PICO):
  Population: {population_or_unspecified}
  Intervention: {intervention_or_unspecified}
  Comparator: {comparator_or_unspecified}
  Outcome: {outcome_or_unspecified}
```

### `target_block` — fallback when no target PICO is supplied

```text
(No target PICO supplied — assess against the as-conducted PICO of the study itself. Focus the OUTCOME judgement on whether the primary outcome is a surrogate vs. a patient-important outcome. For Population, Intervention, and Comparator, default to 'probably_direct' unless the study's selection is unusually narrow or atypical for routine clinical use.)
```

### `ctx_json` — extracted-field context

The prompt includes only the keys listed below from the pre-extracted fields, JSON-pretty-printed:

```text
population_description, population_age, population_sex,
population_comorbidities, population_setting, geography,
inclusion_criteria, exclusion_criteria,
intervention_description, intervention_dose,
intervention_duration, intervention_provider,
comparator_description, comparator_type,
primary_outcome_definition, primary_outcome_measurement,
follow_up_duration
```

## 1.5 Per-subdomain guidance (rendered into `subdomains_block`)

Each subdomain entry is rendered as:

```text
**{label} ({id})**
Guidance: {guidance}
```

### 1.5.1 `population` — Population

> Assess how closely the study population matches the population of interest in the target question (age, sex, comorbidities, severity, setting, geographic context). Highly selected, narrow, or atypical populations limit generalisability and warrant 'probably not sufficiently direct' or stronger.

### 1.5.2 `intervention` — Intervention

> Assess how closely the studied intervention matches the intervention of interest (dose, formulation, mode of delivery, intensity, duration, provider type). Substantial differences in delivery context ("too ideal" trial conditions, specialised provider, non-translatable infrastructure) warrant downgrading.

### 1.5.3 `comparator` — Comparator

> Assess how closely the studied comparator matches the comparator of interest. Active comparators that include potentially effective co-interventions, or 'usual care' that varies markedly across settings, warrant downgrading. Placebo controls when the question is about head-to-head comparison are not direct.

### 1.5.4 `outcome` — Outcomes

> Assess whether outcome measures capture what matters to patients. Per the GRADE handbook: 'surrogate outcomes should be rated down for indirectness unless there is a strong and well-established correlation with meaningful, patient-important outcomes — a criterion that is rarely fulfilled.' Examples of surrogates: HbA1c (diabetes complications), LDL cholesterol (cardiovascular events), bone mineral density (fractures), progression-free survival (overall survival), tumour response rate (survival).

## 1.6 Output JSON shape

The model is told to return exactly this shape:

```json
{
  "population":   "direct|probably_direct|probably_not_direct|not_direct",
  "population_rationale":   "1-2 sentences quoting the paper",
  "intervention": "direct|probably_direct|probably_not_direct|not_direct",
  "intervention_rationale": "1-2 sentences quoting the paper",
  "comparator":   "direct|probably_direct|probably_not_direct|not_direct",
  "comparator_rationale":   "1-2 sentences quoting the paper",
  "outcome":      "direct|probably_direct|probably_not_direct|not_direct",
  "outcome_rationale":      "1-2 sentences quoting the paper",
  "primary_outcome_is_surrogate": true,
  "surrogate_rationale": "If outcome is a surrogate, briefly explain (e.g., \"HbA1c is a surrogate for diabetes complications\")."
}
```

## 1.7 Judgement normaliser

`_normalize_judgement(raw)` coerces the model's output to one of the four allowed values; defaults to `probably_direct` for unknown / empty values (conservative — don't invent a downgrade from garbage output).

```python
JUDGEMENT_OPTIONS = ("direct", "probably_direct", "probably_not_direct", "not_direct")

aliases = {
    "sufficiently_direct":          "direct",
    "probably_sufficiently_direct": "probably_direct",
    "probably_not_sufficiently_direct": "probably_not_direct",
    "not_sufficiently_direct":      "not_direct",
    "yes": "direct",
    "no":  "not_direct",
}
```

Pre-normalisation: lowercase, strip, replace spaces and hyphens with underscores. Unknown values → `"probably_direct"`.

## 1.8 Severity decision tree (pure-Python)

```python
def _judgement_severity(judgements: dict[str, str]) -> tuple[str, int, dict[str, int]]:
    """Aggregate per-subdomain judgements into an overall severity tier.

    Returns (severity_label, downgrade_levels, counts).

    Rule:
      reds=0 and oranges<=1                              → none (0 levels)
      reds=0 and oranges>=2                              → serious (1)
      reds=1 (regardless of oranges)                     → serious (1)
      reds=2                                             → very_serious (2)
      reds>=3                                            → extremely_serious (3)

    "reds" = not_direct; "oranges" = probably_not_direct.
    """
    reds = sum(1 for v in judgements.values() if v == "not_direct")
    oranges = sum(1 for v in judgements.values() if v == "probably_not_direct")
    counts = {"reds": reds, "oranges": oranges}

    if reds >= 3:
        return "extremely_serious", 3, counts
    if reds == 2:
        return "very_serious", 2, counts
    if reds == 1 or oranges >= 2:
        return "serious", 1, counts
    return "none", 0, counts
```

## 1.9 Severity-tier explanation

`severity_explanation(severity, counts, per_subdomain)` produces a one-sentence rationale naming the driver subdomains:

- **none** → `"No serious indirectness: PICO components are sufficiently direct for the target question."`
- **serious** → `"Serious indirectness: concerns in {drivers} ({reds} not-direct, {oranges} probably-not-direct)."`
- **very_serious** → `"Very serious indirectness: 2 PICO components not sufficiently direct ({drivers})."`
- **extremely_serious** → `"Extremely serious indirectness: {reds} PICO components not sufficiently direct ({drivers})."`

`{drivers}` lists the labels of subdomains that were judged `not_direct` or `probably_not_direct`.

## 1.10 Downgrade table (developer-view)

| Severity | GRADE downgrade | Rule |
|---|---|---|
| `none` | 0 levels | All subdomains direct or probably_direct (≤1 borderline allowed) |
| `serious` | 1 level | Exactly 1 not_direct, OR ≥2 probably_not_direct |
| `very_serious` | 2 levels | 2 not_direct subdomains |
| `extremely_serious` | 3 levels | 3 or more not_direct subdomains |

## 1.11 Out of scope (deferred)

- Indirect comparisons / network meta-analysis — body-of-evidence only, not applicable to a single study.
- Baseline-risk indirectness — needs external longitudinal data to model alternative baselines.
- ICEMAN credibility check for subgroup effects.

---

# Section 2 — GRADE Imprecision (single-trial assessment)

## 2.1 Overview

- **Scope:** single-trial assessment of imprecision for the primary outcome via four subdomains (CI width / sample size / event count / fragility).
- **Subdomains:** 4 (CI width is the primary GRADE tool; the other three are secondary checks for fragility / adequacy).
- **Judgement scale:** 4-level (cyan→red gradient in the UI, parallel to indirectness's green→red).
- **Severity tiers → GRADE downgrade:** 0 / 1 / 2 / 3 levels.
- **Optional run-level input:** `thresholds = {mid_benefit, mid_harm}` — the GRADE 2-threshold framing. When provided, the prompt asks the assessor to judge CI width against the user's a-priori MID thresholds. When blank, the assessor falls back to line-of-no-effect + clinical-importance reasoning, defaulting to `probably_precise` rather than `precise` when CI width is uncertain.

## 2.2 Judgement options + severity tiers

```python
JUDGEMENT_OPTIONS = ("precise", "probably_precise", "probably_not_precise", "not_precise")
SEVERITY_LEVELS   = ("none", "serious", "very_serious", "extremely_serious")
```

Mapping:
- `precise` — sufficiently precise for decision-making
- `probably_precise` — probably sufficiently precise
- `probably_not_precise` — probably not sufficiently precise
- `not_precise` — not sufficiently precise

## 2.3 System prompt

```text
You are an evidence-synthesis methodologist assessing the GRADE imprecision domain for a single trial. Read the PDF carefully. For each of the four subdomains (CI width, sample size, event count, fragility), judge how precise the primary-outcome evidence is on a 4-level scale: 'precise' (sufficiently precise), 'probably_precise' (probably sufficiently precise), 'probably_not_precise' (probably not sufficiently precise), or 'not_precise' (not sufficiently precise). Provide a 1-2 sentence rationale per subdomain, quoting the paper where possible. Per the GRADE handbook, the primary tool is whether the 95% CI for the absolute effect crosses decision thresholds — the line of no effect, plus minimal important difference (MID) thresholds for benefit and harm if supplied. Mark event_count as 'n_a' for continuous outcomes (it will be excluded from severity counting). Be alert to single-trial fragility: large relative effects on few events may appear precise but be unreliable. If baseline risk is very low (<5%) and the absolute-risk CI is narrow despite a wide relative-risk CI, briefly note this in rationale rather than rating down. Return ONLY a valid JSON object — no preamble, no markdown fences.
```

## 2.4 Per-paper prompt template

Built by `build_prompt(thresholds, study_type, primary_outcome, extracted_fields, outcome_is_binary)`:

```text
Assess **GRADE imprecision** for the trial described in the attached PDF.

Study type: {study_type}
As-conducted primary outcome: {primary_outcome}

{outcome_block}

{threshold_block}

Context (fields already extracted from the paper):
{ctx_json}

Subdomains to judge:
{subdomains_block}

Return a JSON object with exactly this shape:
{shape}

For each subdomain, weigh whether imprecision is likely to leave the truth uncertain for clinical decision-making. Default to 'probably_precise' rather than 'precise' when there is meaningful uncertainty. Reserve 'not_precise' for clear, substantial imprecision concerns. Rationales must quote the paper verbatim where possible (effect estimates, CIs, sample sizes, event counts).
```

### `outcome_block` — branched on the inferred outcome type

**Binary:**
```text
Outcome type (inferred): BINARY. Judge event_count using the rule-of-thumb thresholds in the guidance.
```

**Continuous:**
```text
Outcome type (inferred): CONTINUOUS. Mark event_count as 'n_a' (it will be excluded from severity counting). Judge fragility from sample size and observed variance instead.
```

**Uncertain:**
```text
Outcome type (inferred): UNCERTAIN. Determine binary vs continuous from the paper; if continuous, mark event_count as 'n_a'.
```

### `threshold_block` — when MIDs are supplied

```text
Decision thresholds (a priori):
  MID for benefit: {mid_benefit_or_unspecified}
  MID for harm:    {mid_harm_or_unspecified}
```

### `threshold_block` — fallback when no thresholds are supplied

```text
(No MID thresholds supplied — assess CI width against the line of no effect plus your judgement of clinically important effect sizes for this outcome. Default to 'probably_precise' rather than 'precise' when CI width is uncertain.)
```

### `ctx_json` — extracted-field context

The prompt includes only the keys listed below from the pre-extracted fields, JSON-pretty-printed:

```text
primary_outcome_definition, primary_outcome_measurement,
primary_outcome_type,
effect_size, effect_estimate, confidence_interval,
p_value, statistical_test,
sample_size, sample_size_intervention, sample_size_comparator,
events_intervention, events_comparator,
follow_up_duration, baseline_risk,
population_outcomes
```

## 2.5 Per-subdomain guidance (rendered into `subdomains_block`)

Each subdomain entry is rendered as:

```text
**{label} ({id})**
Guidance: {guidance}
```

### 2.5.1 `ci_width` — Confidence-interval width

> Per the GRADE handbook, imprecision is judged primarily by whether the 95% confidence interval around the absolute effect estimate crosses clinical-decision thresholds. Default thresholds are the line of no effect plus the minimal important difference (MID) for benefit and harm if supplied. A CI that does not cross any threshold → 'precise'. A CI crossing one threshold → 'probably_not_precise' (1-level concern). A CI crossing two or more thresholds → 'not_precise'. If no effect estimate / CI is reported, return 'probably_not_precise' and explain in rationale.

### 2.5.2 `sample_size` — Sample-size adequacy

> Is the enrolled N large enough that the result is unlikely to flip with a few more participants? This is a single-trial surrogate for the GRADE Optimal Information Size (we do not compute formal RIS). Rule-of-thumb thresholds for a clinically important effect: <100 total participants → 'not_precise'; 100–300 → 'probably_not_precise'; 300–1000 → 'probably_precise'; >1000 → 'precise'. Adjust for outcome type and observed effect size; underpowered trials with extreme effects warrant concern.

### 2.5.3 `event_count` — Event count (binary outcomes)

> For binary primary outcomes: are there enough events to support the observed effect? Rule-of-thumb: <100 total events across arms → 'not_precise'; 100–300 → 'probably_not_precise'; 300–1000 → 'probably_precise'; >1000 → 'precise'. Pay extra attention to the smaller arm — significance driven by ≤10 events in one arm is fragile. **Mark this subdomain N/A for continuous outcomes** (return `"n_a"` or `"not_applicable"`); the normalizer treats N/A as 'precise' so it never contributes to severity counting.

### 2.5.4 `fragility` — Fragility / robustness

> Could a small number of additional events change the conclusion? Per the GRADE handbook: small studies that produce large relative effects on dichotomous outcomes can appear precise via narrow CIs but be fragile because CIs for odds ratios / relative risks tend to narrow as effects grow. Flag: extreme effect sizes from few events, p-values barely under 0.05 with small N, single-event-driven significance, or large relative effects (RRR > 50%) with sparse data. Continuous outcomes: judge robustness from observed variance + sample size.

## 2.6 Output JSON shape

The model is told to return exactly this shape:

```json
{
  "ci_width":     "precise|probably_precise|probably_not_precise|not_precise",
  "ci_width_rationale":    "1-2 sentences quoting the paper",
  "sample_size": "precise|probably_precise|probably_not_precise|not_precise",
  "sample_size_rationale": "1-2 sentences quoting the paper",
  "event_count": "precise|probably_precise|probably_not_precise|not_precise|n_a",
  "event_count_rationale": "1-2 sentences quoting the paper",
  "fragility":   "precise|probably_precise|probably_not_precise|not_precise",
  "fragility_rationale":   "1-2 sentences quoting the paper",
  "outcome_is_binary": true,
  "sample_size_total": 142,
  "events_total": 18,
  "ci_summary": "Brief description of the reported 95% CI for the primary outcome (e.g., \"RR 0.78, 95% CI 0.62 to 0.98\"), or null if not reported."
}
```

`sample_size_total` and `events_total` may be `null` if not reported. `ci_summary` is a free-text string or `null`.

## 2.7 Outcome-type heuristic

`infer_outcome_is_binary(extracted_fields, primary_outcome)` is a best-effort guess used to set the `outcome_block` in the prompt and to drive the UI's "N/A — continuous outcome" rendering for the event-count cell. The model can override the heuristic via `outcome_is_binary` in its response.

```python
_BINARY_HINTS = (
    "binary", "dichotom", "event", "incidence", "mortalit", "death",
    "rate", "proportion", "frequency", "occurrence",
)
_CONTINUOUS_HINTS = (
    "continuous", "mean", "score", "scale", "concentration",
    "level", "change from baseline",
)


def infer_outcome_is_binary(extracted_fields: dict[str, str],
                              primary_outcome: str) -> bool | None:
    """Best-effort guess at whether the primary outcome is binary.

    Returns True for binary, False for continuous, None if indeterminate.
    """
    explicit = (extracted_fields.get("primary_outcome_type") or "").lower()
    if any(h in explicit for h in ("binary", "dichotom")):
        return True
    if "continuous" in explicit:
        return False

    measurement = (extracted_fields.get("primary_outcome_measurement") or "")
    definition = (extracted_fields.get("primary_outcome_definition") or "")
    haystack = " ".join([primary_outcome or "", measurement, definition]).lower()
    if any(h in haystack for h in _BINARY_HINTS):
        return True
    if any(h in haystack for h in _CONTINUOUS_HINTS):
        return False
    return None
```

## 2.8 Judgement normaliser (with N/A handling)

`_normalize_judgement(raw)` coerces the model's output to one of the four allowed values; defaults to `probably_precise` for unknown / empty values. **N/A aliases map to `precise`** so they don't contribute to severity counting — used for `event_count` on continuous outcomes.

```python
JUDGEMENT_OPTIONS = ("precise", "probably_precise", "probably_not_precise", "not_precise")

aliases = {
    "sufficiently_precise":          "precise",
    "probably_sufficiently_precise": "probably_precise",
    "probably_not_sufficiently_precise": "probably_not_precise",
    "not_sufficiently_precise":      "not_precise",
    "yes": "precise",
    "no":  "not_precise",
    # N/A aliases — used for event_count on continuous outcomes:
    "n_a":            "precise",
    "na":             "precise",
    "not_applicable": "precise",
    "n/a":            "precise",
}
```

Pre-normalisation: lowercase, strip, replace spaces and hyphens with underscores. Unknown values → `"probably_precise"`.

## 2.9 Severity decision tree (pure-Python)

Identical structure to indirectness, with `not_precise` / `probably_not_precise` substituted for the indirectness labels:

```python
def _judgement_severity(judgements: dict[str, str]) -> tuple[str, int, dict[str, int]]:
    """Aggregate per-subdomain judgements into an overall severity tier.

    Returns (severity_label, downgrade_levels, counts).

    Rule:
      reds=0 and oranges<=1                              → none (0 levels)
      reds=0 and oranges>=2                              → serious (1)
      reds=1 (regardless of oranges)                     → serious (1)
      reds=2                                             → very_serious (2)
      reds>=3                                            → extremely_serious (3)

    "reds" = not_precise; "oranges" = probably_not_precise.
    N/A subdomains (e.g. event_count for continuous outcomes) are normalized
    to 'precise' upstream so they never contribute to reds/oranges.
    """
    reds = sum(1 for v in judgements.values() if v == "not_precise")
    oranges = sum(1 for v in judgements.values() if v == "probably_not_precise")
    counts = {"reds": reds, "oranges": oranges}

    if reds >= 3:
        return "extremely_serious", 3, counts
    if reds == 2:
        return "very_serious", 2, counts
    if reds == 1 or oranges >= 2:
        return "serious", 1, counts
    return "none", 0, counts
```

## 2.10 Severity-tier explanation

`severity_explanation(severity, counts, per_subdomain)` produces a one-sentence rationale naming the driver subdomains:

- **none** → `"No serious imprecision: confidence intervals, sample size, and event counts are sufficient for the target question."`
- **serious** → `"Serious imprecision: concerns in {drivers} ({reds} not-precise, {oranges} probably-not-precise)."`
- **very_serious** → `"Very serious imprecision: 2 subdomains not sufficiently precise ({drivers})."`
- **extremely_serious** → `"Extremely serious imprecision: {reds} subdomains not sufficiently precise ({drivers})."`

`{drivers}` lists the labels of subdomains that were judged `not_precise` or `probably_not_precise`.

## 2.11 Downgrade table (developer-view)

| Severity | GRADE downgrade | Rule |
|---|---|---|
| `none` | 0 levels | All subdomains precise or probably_precise (≤1 borderline allowed) |
| `serious` | 1 level | Exactly 1 not_precise, OR ≥2 probably_not_precise |
| `very_serious` | 2 levels | 2 not_precise subdomains |
| `extremely_serious` | 3 levels | 3 or more not_precise subdomains |

## 2.12 Out of scope (deferred)

- Six-threshold EtD framing — only 2-threshold MID-benefit + MID-harm in v1.
- Machine-readable threshold-crossing arithmetic — the assessor judges qualitatively (no numeric CI parsing).
- Formal Optimal Information Size / Review Information Size computation.
- Walsh fragility-index computation.
- Very-low-baseline-risk auto-override — guardrail in the rationale only, not a separate downgrade short-circuit.
- Random-effects double-counting caveat — meta-analysis only; this module is single-trial.

---

# Section 3 — GRADE combination (`compute_grade`)

The orchestrator combines RoB + indirectness + imprecision via [`backend/quality_appraisal.py:compute_grade`](../backend/quality_appraisal.py).

## 3.1 GRADE levels

```python
GRADE_LEVELS = ["High", "Moderate", "Low", "Very low"]
```

## 3.2 Combiner signature

```python
def compute_grade(initial: str,
                  rob_overall: str,
                  rob_domain_judgements: list[str] | None = None,
                  indirectness_levels: int = 0,
                  indirectness_explanation: str = "",
                  imprecision_levels: int = 0,
                  imprecision_explanation: str = "",
                  ) -> tuple[str, str]:
    """Compute updated GRADE + human-readable explanation."""
```

## 3.3 RoB downgrade rules (`_rob_downgrade` helper)

```text
RoB 2     Low             → 0
          Some concerns   → 1
          High            → 1 (2 if ≥2 domains are High)
ROBINS-I  Low             → 0
          Moderate        → 1
          Serious         → 1 (2 if ≥2 domains are Serious)
          Critical        → 2 (always)
          No information  → 1 (conservative)
```

## 3.4 Combination logic

```python
idx          = _grade_index(initial)
rob_levels, rob_reason = _rob_downgrade(rob_overall, rob_domain_judgements)
indir_levels = max(0, int(indirectness_levels or 0))
imprec_levels = max(0, int(imprecision_levels or 0))
total        = rob_levels + indir_levels + imprec_levels
new_idx      = min(idx + total, len(GRADE_LEVELS) - 1)   # cap at "Very low"
new_level    = GRADE_LEVELS[new_idx]
```

The total downgrade is the **sum** of the three contributors, capped at 3 levels below the initial certainty (i.e. "Very low" is the floor when starting from "High"). Other GRADE domains (inconsistency, publication bias) still require a body of evidence and are out of scope for this single-study tool.

## 3.5 Explanation text

The returned explanation lists every contributor that fired:

- **Total = 0**: `"No downgrade: overall risk of bias is Low and no serious indirectness, no serious imprecision detected."` (only mentions clean indirectness / imprecision when each `_levels` is zero).
- **Total > 0**: `"Downgraded {total} level(s): {rob_part} + {indirectness_part} + {imprecision_part}."` with each contributing part included only when its level count is > 0.

Contributing-part templates:

```text
{rob_levels} level(s) for {rob_reason}
{indir_levels} level(s) for {sev_label} indirectness — {indirectness_explanation}
{imprec_levels} level(s) for {sev_label} imprecision — {imprecision_explanation}
```

`{sev_label}` is `"serious"` for 1 level, `"very serious"` for 2, `"extremely serious"` for 3.

Example (RoB Some concerns + serious indirectness + serious imprecision):

> Downgraded 3 levels: 1 level for Some concerns in risk of bias + 1 level for serious indirectness — surrogate primary outcome (HbA1c) + 1 level for serious imprecision — wide CI crossing line of no effect.

---

# Section 4 — Persistence + transparency

## 4.1 DB columns

`quality_appraisal_runs`:
- `target_pico_json` — JSON of `{population, intervention, comparator, outcome}` (or NULL).
- `imprecision_thresholds_json` — JSON of `{mid_benefit, mid_harm}` (or NULL).

`quality_appraisal_results`:
- `indirectness_json` — full per-subdomain output (judgements + rationales + surrogate flag + counts).
- `indirectness_overall` — severity tier label.
- `indirectness_levels` — 0 / 1 / 2 / 3.
- `indirectness_explanation` — one-sentence rationale.
- `imprecision_json` — full per-subdomain output (judgements + rationales + outcome_is_binary + sample_size_total + events_total + ci_summary + counts).
- `imprecision_overall` — severity tier label.
- `imprecision_levels` — 0 / 1 / 2 / 3.
- `imprecision_explanation` — one-sentence rationale.

All migrations are idempotent in `migrate_qa_columns(conn)`.

## 4.2 Developer view

Both modules expose `prompt_catalog()` which is surfaced via `GET /api/quality-appraisal/prompts` to every signed-in user. The dev view (🔧 icon in the topbar) renders:

- System prompt (verbatim).
- Judgement options + severity tiers.
- Per-subdomain guidance.
- Both prompt templates (with and without optional input).
- `_judgement_severity` source via `inspect.getsource()`.
- `severity_explanation` source.
- Imprecision additionally exposes `infer_outcome_is_binary` source so reviewers can see the binary-vs-continuous heuristic.
- Downgrade table.
- Out-of-scope list.

Transparency by default — reviewers can see exactly how a judgement was produced.
