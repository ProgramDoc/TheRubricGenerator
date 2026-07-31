# Per-Paper GRADE (Quality-Appraisal Platform) — Sharable Methodology Reference

A self-contained reference for the **per-paper** GRADE certainty rating produced by the quality-appraisal platform — the rating shown against a *single appraised study*, alongside its risk-of-bias assessment and reporting-guideline check. Contains:

- The certainty ladder and the initial-certainty-by-design registry
- Risk-of-bias → downgrade-level mapping covering five RoB instruments (RoB 2 and its cross-over / cluster extensions, ROBINS-I V1 and V2, QUADAS-2, QUADAS-3)
- The **indirectness** module in full — four PICO subdomains, verbatim guidance, severity decision tree, prompts, output shape
- The **imprecision** module in full — four subdomains, verbatim guidance, MID-threshold handling, outcome-type heuristic, severity decision tree, prompts, output shape
- The combining arithmetic and the exact explanation strings it emits
- A turnkey single-file reference implementation (`llm_call` injected — no framework, database, or HTTP dependencies)
- Plain-`assert` test sketches

> **This is not the GRADE agent. Read this first.**
> There are two different GRADE components, and confusing them will produce wrong ratings:
>
> | | **This document** | **[`grade_certainty_shareable.md`](grade_certainty_shareable.md)** |
> |---|---|---|
> | Unit | one appraised paper | one pooled body of evidence for one outcome |
> | Domains | risk of bias, indirectness, imprecision | all five downgrade domains + three upgrade domains |
> | Risk of bias | one paper's overall judgement → levels | per-study labels aggregated by **pooled weight** |
> | Imprecision | four LLM-judged subdomains of one trial | pooled 95% CI vs null / MIDs + Optimal Information Size |
> | Indirectness | four PICO subdomains via one LLM call | a reviewer-supplied 0/1/2, with an optional LLM assist |
> | Rating up | none | large effect, dose-response, opposing confounding |
>
> **If you are building an evidence profile or a Summary-of-Findings table, you want the other document.** That is the GRADE agent proper — it consumes the pooling agent's output and is where the canonical body-of-evidence method lives. This document describes the appraisal platform's per-paper rating, which exists so a reviewer looking at one study gets a certainty signal without a synthesis. It is deliberately a lesser thing: three domains instead of eight, and no pooling.

> **The rating is per (paper × outcome), not per paper.** Every prompt below takes an `{assessed_outcome}` placeholder, and it is filled with **the outcome being rated** — which need not be the paper's stated primary outcome. Risk-of-bias instruments are outcome-specific (RoB 2 domain 4 covers measurement of the outcome, domain 5 the selection of the reported result), and GRADE rates certainty per outcome, so a study assessed for several outcomes runs this whole pipeline once per outcome and can legitimately come out Low for one and High for another. Where a rule reads a paper-level field that describes the *primary* outcome specifically, it is gated — see the note in §5.5.

**Source transcribed:**

- GRADE handbook, indirectness chapter — Schünemann H, Brożek J, Guyatt G, Oxman A (eds). <https://book.gradepro.org/guideline/indirectness>. Figure 1 of that chapter explicitly supports per-trial indirectness tables (judgements per PICO component for a single study).
- GRADE handbook, imprecision chapter — Murad MH, Neumann I, Brozek J, Langendam M, Dahm P, Schünemann H. <https://book.gradepro.org/guideline/imprecision>.
- GRADEpro guidance on mapping risk-of-bias judgements to certainty downgrades, applied to the published overall-judgement vocabularies of RoB 2 (Higgins/Savović/Page/Sterne 2019), ROBINS-I V1 (Sterne et al. 2016) and V2 (20 Nov 2025 cribsheet), QUADAS-2 (Whiting et al., Ann Intern Med 2011;155:529-536) and QUADAS-3 v1.2.

**Scope:** the three downgrade domains that one paper can support — **risk of bias**, **indirectness**, and **imprecision**.

Explicitly **out of scope**:

- **Inconsistency** and **publication bias.** Both need ≥2 studies and are undefined for n = 1. They are not deferred or approximated here — they belong to the GRADE agent, and are documented in [`grade_certainty_shareable.md`](grade_certainty_shareable.md) §4.2 and §4.4.
- **Rating up** (large effect, dose-response gradient, plausible residual confounding). Not implemented on this path — see §11. It *is* implemented in the GRADE agent, gated to non-randomized evidence with no rate-down factors.
- **Any pooling.** No effect-size conversion, no fixed/random-effects estimate, no heterogeneity, no small-study tests.
- Indirect comparisons / network meta-analysis; baseline-risk indirectness (needs external longitudinal data); the ICEMAN subgroup-credibility check.
- Six-threshold Evidence-to-Decision framing (two thresholds only: MID for benefit, MID for harm); machine-readable threshold-crossing arithmetic; formal Optimal Information Size / Review Information Size computation; the Walsh fragility index.

> **Interpretation note — this is a single-study adaptation of a body-of-evidence framework.**
> Canonical GRADE rates certainty for a *body of evidence* for an outcome, not for one paper. Per-study assessment is defensible for these three domains — the indirectness chapter's Figure 1 supports per-trial indirectness tables, and imprecision reduces to confidence-interval width versus decision thresholds plus sample/event adequacy — but the output is "certainty as far as this one study can carry it", not a guideline-grade rating. Two of the five downgrade domains were never assessed, so the number is a ceiling on confidence, not a considered rating. Do not present it as an evidence-profile certainty without saying so, and do not let a clean result here imply the body of evidence is clean.

> **Interpretation note — no rating-up path exists.**
> The arithmetic in §6 only ever *adds* downgrade levels; both extra-domain inputs are clamped at zero with `max(0, …)`. A consequence a forker must internalise: an observational design entering at "Low" can never be rated back up, no matter how large or how dose-responsive the observed effect. This is a substantive divergence from full GRADE. See §11.

> **Interpretation note — the ladder fails open on an unrecognised initial grade.**
> `_grade_index` returns `0` ("High") for any string not in `GRADE_LEVELS`. A typo in a design registry therefore silently produces the *most* favourable starting point rather than an error. If you reimplement this, consider raising instead — the fail-open behaviour is documented here because it is what the reference platform does, not because it is desirable.

> **Interpretation note — RoB labels must be normalised before they reach the mapper.**
> ROBINS-I Domain 1 emits `"Low (except for concerns about uncontrolled confounding)"` (cohort variants) or `"Low (except for concerns about uncontrolled benchmarking)"` (single-arm variant). The RoB tool's own overall aggregator collapses these to plain `"Low"` **before** the overall judgement reaches the downgrade mapper. A forker who skips that normalisation gets no error — the string falls through to the catch-all branch and silently downgrades one level. See §3.

---

## 1. The certainty ladder

Four ordered levels. Index 0 is the most certain; downgrading moves *up* the index.

```python
GRADE_LEVELS = ["High", "Moderate", "Low", "Very low"]


def _grade_index(level: str) -> int:
    try:
        return GRADE_LEVELS.index(level)
    except ValueError:
        return 0
```

Two properties of this ladder matter downstream:

- **"Very low" is a floor.** The combiner clamps with `min(idx + total, 3)`. A study can accumulate more downgrade levels than the ladder has room for — the maximum reachable total is `2 (RoB) + 3 (indirectness) + 3 (imprecision) = 8` — and everything past "Very low" is absorbed silently.
- **Unrecognised input fails open to "High"** (see the front-matter callout).

---

## 2. Initial certainty by study design

Every design enters the ladder at a fixed level determined by its study type, before any downgrading. In the reference platform this lives in the same registry that selects the RoB instrument and reporting guideline, so one lookup answers "which tool, which checklist, which starting certainty".

| Study type | RoB instrument | Initial certainty | Flags |
|---|---|---|---|
| Randomized Controlled Trial | RoB 2 | **High** | — |
| Crossover Trial | RoB 2 cross-over extension | **High** | — |
| Cluster Randomized Trial | RoB 2 cluster extension (CRT) | **High** | — |
| Cohort Study | ROBINS-I | **Low** | — |
| Case-Control | ROBINS-I | **Low** | — |
| Non-Randomized Trial | ROBINS-I | **Low** | — |
| Cross-Sectional (Analytical) | ROBINS-I | **Low** | — |
| Case-Crossover | ROBINS-I | **Low** | — |
| Single-Arm Trial | ROBINS-I (single-arm variant) | **Very low** | — |
| Dose-Escalation Study | ROBINS-I (single-arm variant) | **Very low** | — |
| Diagnostic Accuracy | QUADAS-2 or QUADAS-3 | **High** | `skip_grade_extras` |
| SR with Meta-Analysis | AMSTAR-2 | *(none)* | `skip_grade` |
| SR without Meta-Analysis | AMSTAR-2 | *(none)* | `skip_grade` |

Three of these placements are judgement calls worth carrying across:

**Single-arm and dose-escalation designs start at "Very low", not "Low".** Absence of a comparator is a more severe limitation than a confounded comparison, so uncontrolled designs enter at the floor. The clamp in §6 means further downgrades are absorbed — the rating cannot go lower, though the explanation string still reports the computed downgrade (see §6).

**Diagnostic accuracy starts at "High".** Per the GRADE handbook, cross-sectional accuracy designs start high; case-control accuracy designs are penalised inside the RoB instrument's participant-selection domain rather than by a lower starting point.

**Systematic reviews get no certainty rating at all.** AMSTAR-2 emits an overall *confidence in the review* rating (High / Moderate / Low / Critically low), which is a different construct from GRADE certainty in a body of evidence for an outcome. Conflating the two is a real hazard because the vocabularies collide — AMSTAR-2 "High" is good news, GRADE "High" is also good news, but they are answering different questions, and AMSTAR-2's "Low" and GRADE's "Low" are not comparable at all.

### Two distinct skip flags

| Flag | Set by | Effect |
|---|---|---|
| `skip_grade_extras` | Diagnostic accuracy | Indirectness and imprecision are **not run**; the combiner still runs with both level counts forced to `0`. The paper still gets a certainty rating, downgraded for risk of bias only. |
| `skip_grade` | Systematic reviews | Indirectness, imprecision, **and the combiner** are all skipped. Initial certainty, updated certainty, and the explanation are left empty; the RoB instrument's overall rating is the headline output. |

`skip_grade_extras` exists because indirectness and imprecision as implemented here are PICO-shaped (Population / Intervention / Comparator / Outcome). Diagnostic accuracy is PIRT-shaped (Patient / Index test / Reference standard / Target condition). Running the PICO modules against a PIRT question produces confident-sounding nonsense, so the honest move is to skip them and say so. If you build PIRT-aware variants, that flag is where they hook in.

> **Note.** In the reference platform QUADAS-2 is not a registry entry of its own — it is a per-run override of the diagnostic-accuracy slot, so it inherits that row's initial certainty and both flags. Whichever way you model tool selection, the GRADE consequences must follow the *study design*, not the tool.

---

## 3. Risk of bias → downgrade levels

One function maps an RoB instrument's **overall** judgement (plus, for two branches, the per-domain judgements) to 0, 1, or 2 downgrade levels and a reason fragment.

```python
def _rob_downgrade(rob_overall: str,
                   rob_domain_judgements: list[str] | None = None
                   ) -> tuple[int, str]:
    """Compute RoB-driven downgrade levels and a human-readable reason fragment.

    Returns ``(levels, reason)``. ``levels`` is 0/1/2; ``reason`` is the noun
    phrase that follows "for ..." in the explanation (e.g. "Some concerns in
    risk of bias").
    """
    judgements = rob_domain_judgements or []
    if rob_overall == "Low":
        return 0, "Low risk of bias"

    # RoB 2 branches
    if rob_overall == "Some concerns":
        return 1, "Some concerns in risk of bias"
    if rob_overall == "High":
        high_count = sum(1 for j in judgements if j == "High")
        if high_count >= 2:
            return 2, f"High risk of bias in {high_count} domains"
        return 1, "High risk of bias"

    # ROBINS-I V2 branches
    if rob_overall == "Moderate":
        return 1, "Moderate risk of bias (ROBINS-I V2)"
    if rob_overall == "Serious":
        serious_count = sum(1 for j in judgements if j == "Serious")
        if serious_count >= 2:
            return 2, f"Serious risk of bias in {serious_count} ROBINS-I V2 domains"
        return 1, "Serious risk of bias (ROBINS-I V2)"
    if rob_overall == "Critical":
        return 2, "Critical risk of bias (ROBINS-I V2)"

    # Legacy V1 stored results — V2 no longer produces "No information" overall
    if rob_overall == "No information":
        return 1, "No information in one or more ROBINS-I domains (conservative; legacy V1 result)"

    # QUADAS-3 branches (Low / High / Insufficient information). "Low" is
    # already handled at the top; "High" matches the RoB 2 branch above.
    if rob_overall == "Insufficient information":
        return 1, "Insufficient information in one or more QUADAS-3 domains (conservative)"

    # QUADAS-2 branches (Low / High / Unclear). "Low" and "High" share the
    # branches above; "Unclear" is QUADAS-2-specific.
    if rob_overall == "Unclear":
        return 1, "Unclear risk of bias in one or more QUADAS-2 domains (conservative)"

    return 1, f"risk of bias ({rob_overall})"
```

### Branch table

| Overall judgement | Instrument vocabulary | Levels | Reason fragment |
|---|---|---|---|
| `Low` | all | **0** | `Low risk of bias` |
| `Some concerns` | RoB 2 (+ extensions) | **1** | `Some concerns in risk of bias` |
| `High`, ≥2 domains High | RoB 2, QUADAS-2, QUADAS-3 | **2** | `High risk of bias in {n} domains` |
| `High`, ≤1 domain High | RoB 2, QUADAS-2, QUADAS-3 | **1** | `High risk of bias` |
| `Moderate` | ROBINS-I V1/V2 | **1** | `Moderate risk of bias (ROBINS-I V2)` |
| `Serious`, ≥2 domains Serious | ROBINS-I V1/V2 | **2** | `Serious risk of bias in {n} ROBINS-I V2 domains` |
| `Serious`, ≤1 domain Serious | ROBINS-I V1/V2 | **1** | `Serious risk of bias (ROBINS-I V2)` |
| `Critical` | ROBINS-I V1/V2 | **2** | `Critical risk of bias (ROBINS-I V2)` |
| `No information` | ROBINS-I V1 only | **1** | `No information in one or more ROBINS-I domains (conservative; legacy V1 result)` |
| `Insufficient information` | QUADAS-3 | **1** | `Insufficient information in one or more QUADAS-3 domains (conservative)` |
| `Unclear` | QUADAS-2 | **1** | `Unclear risk of bias in one or more QUADAS-2 domains (conservative)` |
| *anything else* | — | **1** | `risk of bias ({value})` |

Four things to carry across:

**Branch order is load-bearing.** The instrument vocabularies overlap: `Low` and `High` are shared by RoB 2, QUADAS-2 and QUADAS-3, so they are handled once at the top and the instrument-specific tokens are tested afterwards. Reordering the branches — or rewriting this as a flat dictionary lookup — changes behaviour for the shared tokens.

**`Critical` never escalates on domain count.** It is already the maximum (2 levels), so counting domains would be pointless. Contrast `High` and `Serious`, which escalate from 1 to 2 when at least two domains carry that judgement.

**Reason strings name "ROBINS-I V2" even for V1 results.** V1 and V2 share the Low / Moderate / Serious / Critical overall vocabulary, so V1 results land in the branches labelled V2. Only `No information` — which V2 retired — distinguishes them here. If your UI shows these strings, either relabel per the actual tool in use or accept the imprecision; it is cosmetic, not arithmetic.

**The catch-all downgrades one level.** An unrecognised overall judgement is treated as *some* risk of bias rather than none. Combined with the Domain 1 label-normalisation precondition (front matter), this is how an un-normalised `"Low (except for concerns about uncontrolled confounding)"` silently costs a level.

### Where the domain judgement list comes from

Two branches need the per-domain judgements. Build the list by filtering the RoB instrument's domain map down to entries that actually carry a judgement — instruments that store preflight metadata alongside the domains (ROBINS-I's aim/benchmark preflight, AMSTAR-2's design preflight) will otherwise poison the count:

```python
rob_domain_judgements = [
    d.get("judgement", "Low")
    for k, d in rob_domains.items()
    if k != "preflight" and isinstance(d, dict) and "judgement" in d
]
```

---

## 4. Indirectness

### 4.1 Judgement scale

Four levels per subdomain, matching the green / yellow / orange / red colour scheme of the GRADE handbook:

```python
JUDGEMENT_OPTIONS = ("direct", "probably_direct", "probably_not_direct", "not_direct")
# direct               — sufficiently direct
# probably_direct      — probably sufficiently direct
# probably_not_direct  — probably not sufficiently direct
# not_direct           — not sufficiently direct

SEVERITY_LEVELS = ("none", "serious", "very_serious", "extremely_serious")
```

### 4.2 Subdomains

Four PICO subdomains. The `guidance` strings below are transcribed verbatim — they are inlined into the prompt (§4.5), so paraphrasing them changes the assessment.

**Population** (`population`)

> Assess how closely the study population matches the population of interest in the target question (age, sex, comorbidities, severity, setting, geographic context). Highly selected, narrow, or atypical populations limit generalisability and warrant 'probably not sufficiently direct' or stronger.

**Intervention** (`intervention`)

> Assess how closely the studied intervention matches the intervention of interest (dose, formulation, mode of delivery, intensity, duration, provider type). Substantial differences in delivery context ("too ideal" trial conditions, specialised provider, non-translatable infrastructure) warrant downgrading.

**Comparator** (`comparator`)

> Assess how closely the studied comparator matches the comparator of interest. Active comparators that include potentially effective co-interventions, or 'usual care' that varies markedly across settings, warrant downgrading. Placebo controls when the question is about head-to-head comparison are not direct.

**Outcomes** (`outcome`)

> Assess whether outcome measures capture what matters to patients. Per the GRADE handbook: 'surrogate outcomes should be rated down for indirectness unless there is a strong and well-established correlation with meaningful, patient-important outcomes — a criterion that is rarely fulfilled.' Examples of surrogates: HbA1c (diabetes complications), LDL cholesterol (cardiovascular events), bone mineral density (fractures), progression-free survival (overall survival), tumour response rate (survival).

> **Note — id/label mismatch.** The subdomain id is `comparator` while parts of the prompt and the handbook call it "Comparison". Keep the id stable; it is the JSON key.

### 4.3 Severity decision tree

```python
def _judgement_severity(judgements: dict[str, str]) -> tuple[str, int, dict[str, int]]:
    """Aggregate per-subdomain judgements into an overall severity tier.

    Returns ``(severity_label, downgrade_levels, counts)``.

    Rule:
      reds=0 and oranges<=1                              → none (0 levels)
      reds=0 and oranges>=2                              → serious (1)
      reds=1 (regardless of oranges)                     → serious (1)
      reds=2                                             → very_serious (2)
      reds>=3                                            → extremely_serious (3)

    "reds" = ``not_direct``; "oranges" = ``probably_not_direct``.
    GRADE guidance: don't rate down unless concerns are likely to lead to
    meaningful, systematic differences — a single borderline ('probably not
    sufficiently direct') subdomain is treated as inherent indirectness, not
    a reason to downgrade.
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

| Severity | Levels | Condition |
|---|---|---|
| `none` | 0 | all subdomains direct or probably_direct (≤1 borderline allowed) |
| `serious` | 1 | exactly 1 `not_direct`, **or** ≥2 `probably_not_direct` |
| `very_serious` | 2 | 2 `not_direct` |
| `extremely_serious` | 3 | ≥3 `not_direct` |

The single-orange tolerance is deliberate and follows GRADE's threshold: rate down only when the mismatch is likely to produce *meaningful, systematic* differences in the effect estimate. One borderline subdomain is inherent indirectness, not a downgrade trigger.

### 4.4 Severity explanation

```python
def severity_explanation(severity: str, counts: dict[str, int],
                         per_subdomain: dict[str, str]) -> str:
    """One-sentence rationale for the chosen severity tier."""
    if severity == "none":
        return ("No serious indirectness: PICO components are sufficiently "
                "direct for the target question.")
    # Pick the "drivers" — non-direct subdomains
    drivers = [SUBDOMAIN_IDS_TO_LABEL.get(sid, sid)
               for sid, j in per_subdomain.items()
               if j in ("not_direct", "probably_not_direct")]
    drivers_text = ", ".join(drivers) if drivers else "PICO mismatch"
    if severity == "serious":
        return (f"Serious indirectness: concerns in {drivers_text} "
                f"({counts['reds']} not-direct, {counts['oranges']} probably-not-direct).")
    if severity == "very_serious":
        return (f"Very serious indirectness: 2 PICO components not sufficiently "
                f"direct ({drivers_text}).")
    return (f"Extremely serious indirectness: {counts['reds']} PICO components "
            f"not sufficiently direct ({drivers_text}).")
```

This string is what the combiner appends after the em-dash in the final GRADE explanation (§6), so it is user-visible.

### 4.5 System prompt

```text
You are an evidence-synthesis methodologist assessing the GRADE indirectness
domain for a single study. Read the PDF carefully. For each of the four PICO
subdomains (Population, Intervention, Comparison, Outcome), judge how directly
the study's evidence applies to the specified target question on a 4-level
scale: 'direct' (sufficiently direct), 'probably_direct' (probably sufficiently
direct), 'probably_not_direct' (probably not sufficiently direct), or
'not_direct' (not sufficiently direct). Provide a 1-2 sentence rationale per
subdomain, quoting the paper where possible. Per GRADE guidance, do NOT rate
down unless there are compelling reasons to believe the mismatch would lead to
meaningful, systematic differences in the effect estimate. Surrogate outcomes
(HbA1c, LDL, bone density, progression-free survival, etc.) should be rated
'probably_not_direct' or worse unless a strong, well-established correlation
with patient-important outcomes is documented in the paper. Return ONLY a valid
JSON object — no preamble, no markdown fences.
```

> **Note — send this as the system prompt.** In the reference platform this constant is defined and surfaced in the developer view, but the shared PDF-calling helper it routes through passes an empty system string, so in production only the user prompt (§4.6) reaches the model. That is an implementation gap in the reference platform, not a methodological choice: the user prompt restates the scale, the JSON shape, and the default-to-`probably_direct` instruction, which is why output remains usable without it. A new implementation should send this string as the system prompt.

### 4.6 User prompt template

`{…}` are substitution points. `{target_block}`, `{ctx_json}`, `{subdomains_block}` and `{shape}` are themselves built by the helpers below.

```text
Assess **GRADE indirectness** for the study described in the attached PDF.

Study type: {study_type}
Outcome being rated: {assessed_outcome}

{target_block}

Context (fields already extracted from the paper):
{ctx_json}

Subdomains to judge:
{subdomains_block}

Return a JSON object with exactly this shape:
{shape}

For each subdomain, weigh whether the mismatch is likely to produce systematic differences in effect estimates. Default to 'probably_direct' rather than 'direct' when there is any meaningful uncertainty. Reserve 'not_direct' for clear, substantial mismatches. Rationales must quote the paper verbatim where possible.
```

**`{subdomains_block}`** — for each subdomain, a blank line then:

```text
**{label} ({id})**
Guidance: {guidance}
```

**`{ctx_json}`** — a JSON dump (2-space indent) of whichever of these pre-extracted fields are non-empty, or the literal `(no pre-extracted fields)`:

```python
relevant_keys = [
    "population_description", "population_age", "population_sex",
    "population_comorbidities", "population_setting", "geography",
    "inclusion_criteria", "exclusion_criteria",
    "intervention_description", "intervention_dose",
    "intervention_duration", "intervention_provider",
    "comparator_description", "comparator_type",
    "primary_outcome_definition", "primary_outcome_measurement",
    "follow_up_duration",
]
```

**`{target_block}`** — the target PICO is optional, and the two renderings differ in more than formatting. With a target supplied:

```text
Target question (PICO):
  Population: {population}
  Intervention: {intervention}
  Comparator: {comparator}
  Outcome: {outcome}
```

Any individual field left blank renders as `(unspecified — judge based on as-conducted PICO)`.

With no target supplied at all — every field blank or the whole object absent — the block becomes:

```text
(No target PICO supplied — assess against the as-conducted PICO of the study
itself. Focus the OUTCOME judgement on whether the primary outcome is a
surrogate vs. a patient-important outcome. For Population, Intervention, and
Comparator, default to 'probably_direct' unless the study's selection is
unusually narrow or atypical for routine clinical use.)
```

> **This fallback is a substantive methodological choice, not a formatting default.** Without a review question there is nothing to be indirect *to*, so the assessment collapses to outcome surrogacy and the other three subdomains are pinned near-direct by instruction. That is the honest behaviour, but it means an un-targeted run will rarely downgrade for P, I, or C. If you are running inside a systematic review, always thread the protocol PICO through.

### 4.7 Expected JSON output

```json
{
  "population": "direct|probably_direct|probably_not_direct|not_direct",
  "population_rationale": "1-2 sentences quoting the paper",
  "intervention": "direct|probably_direct|probably_not_direct|not_direct",
  "intervention_rationale": "1-2 sentences quoting the paper",
  "comparator": "direct|probably_direct|probably_not_direct|not_direct",
  "comparator_rationale": "1-2 sentences quoting the paper",
  "outcome": "direct|probably_direct|probably_not_direct|not_direct",
  "outcome_rationale": "1-2 sentences quoting the paper",
  "primary_outcome_is_surrogate": true,
  "surrogate_rationale": "If outcome is a surrogate, briefly explain (e.g., \"HbA1c is a surrogate for diabetes complications\")."
}
```

Assembled result, after normalisation and the severity tree:

```python
{
  "population":   {"judgement": "probably_direct", "rationale": "…", "label": "Population"},
  "intervention": {"judgement": "direct",          "rationale": "…", "label": "Intervention"},
  "comparator":   {"judgement": "direct",          "rationale": "…", "label": "Comparator"},
  "outcome":      {"judgement": "probably_not_direct", "rationale": "…", "label": "Outcomes"},
  "primary_outcome_is_surrogate": True,
  "surrogate_rationale": "…",
  "counts": {"reds": 0, "oranges": 1},
}
# returned alongside: severity="none", levels=0, explanation="No serious indirectness: …"
```

### 4.8 Answer normalisation

```python
def _normalize_judgement(raw: str) -> str:
    """Coerce LLM output to one of the four allowed values; default to probably_direct."""
    val = (raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    if val in JUDGEMENT_OPTIONS:
        return val
    aliases = {
        "sufficiently_direct": "direct",
        "probably_sufficiently_direct": "probably_direct",
        "probably_not_sufficiently_direct": "probably_not_direct",
        "not_sufficiently_direct": "not_direct",
        "yes": "direct",
        "no": "not_direct",
    }
    if val in aliases:
        return aliases[val]
    logger.warning("Indirectness: unknown judgement %r — defaulting to probably_direct", raw)
    return "probably_direct"
```

The unknown-value default is `probably_direct` — a *non*-downgrading value. A garbled response therefore fails toward "no concern". Log it; do not silently swallow it.

---

## 5. Imprecision

Deliberately parallel in shape to §4 — same four-level scale, same severity tree, same prompt skeleton. The differences are the subdomains, the optional thresholds, the outcome-type handling, and one extra normalisation rule.

### 5.1 Judgement scale

```python
JUDGEMENT_OPTIONS = ("precise", "probably_precise", "probably_not_precise", "not_precise")
# precise               — sufficiently precise for decision-making
# probably_precise      — probably sufficiently precise
# probably_not_precise  — probably not sufficiently precise
# not_precise           — not sufficiently precise

SEVERITY_LEVELS = ("none", "serious", "very_serious", "extremely_serious")
```

### 5.2 Subdomains

Verbatim guidance, including the numeric rules of thumb — those bands *are* the methodology and must not be re-tuned casually.

**Confidence-interval width** (`ci_width`)

> Per the GRADE handbook, imprecision is judged primarily by whether the 95% confidence interval around the absolute effect estimate crosses clinical-decision thresholds. Default thresholds are the line of no effect plus the minimal important difference (MID) for benefit and harm if supplied. A CI that does not cross any threshold → 'precise'. A CI crossing one threshold → 'probably_not_precise' (1-level concern). A CI crossing two or more thresholds → 'not_precise'. If no effect estimate / CI is reported, return 'probably_not_precise' and explain in rationale.

**Sample-size adequacy** (`sample_size`)

> Is the enrolled N large enough that the result is unlikely to flip with a few more participants? This is a single-trial surrogate for the GRADE Optimal Information Size (we do not compute formal RIS). Rule-of-thumb thresholds for a clinically important effect: <100 total participants → 'not_precise'; 100–300 → 'probably_not_precise'; 300–1000 → 'probably_precise'; >1000 → 'precise'. Adjust for outcome type and observed effect size; underpowered trials with extreme effects warrant concern.

**Event count (binary outcomes)** (`event_count`)

> For binary primary outcomes: are there enough events to support the observed effect? Rule-of-thumb: <100 total events across arms → 'not_precise'; 100–300 → 'probably_not_precise'; 300–1000 → 'probably_precise'; >1000 → 'precise'. Pay extra attention to the smaller arm — significance driven by ≤10 events in one arm is fragile. **Mark this subdomain N/A for continuous outcomes** (return `"n_a"` or `"not_applicable"`); the normalizer treats N/A as 'precise' so it never contributes to severity counting.

**Fragility / robustness** (`fragility`)

> Could a small number of additional events change the conclusion? Per the GRADE handbook: small studies that produce large relative effects on dichotomous outcomes can appear precise via narrow CIs but be fragile because CIs for odds ratios / relative risks tend to narrow as effects grow. Flag: extreme effect sizes from few events, p-values barely under 0.05 with small N, single-event-driven significance, or large relative effects (RRR > 50%) with sparse data. Continuous outcomes: judge robustness from observed variance + sample size.

### 5.3 Severity decision tree

Identical structure to indirectness, with the red/orange tokens swapped:

```python
def _judgement_severity(judgements: dict[str, str]) -> tuple[str, int, dict[str, int]]:
    """Aggregate per-subdomain judgements into an overall severity tier.

    Returns ``(severity_label, downgrade_levels, counts)``.

    Rule:
      reds=0 and oranges<=1                              → none (0 levels)
      reds=0 and oranges>=2                              → serious (1)
      reds=1 (regardless of oranges)                     → serious (1)
      reds=2                                             → very_serious (2)
      reds>=3                                            → extremely_serious (3)

    "reds" = ``not_precise``; "oranges" = ``probably_not_precise``.
    GRADE guidance: don't rate down unless concerns are likely to lead to
    meaningful, systematic uncertainty in the effect estimate — a single
    borderline ('probably not precise') subdomain is treated as inherent
    uncertainty, not a reason to downgrade.

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

> **The N/A normalisation is the single easiest thing to get wrong in this document.** For a continuous outcome, `event_count` does not apply. It is normalised to `precise` — *not* dropped from the dictionary, *not* left as a literal `"n_a"`. Leaving it as an unrecognised token would send it to the `probably_precise` default (harmless), but treating it as a concern would downgrade every continuous-outcome paper for a subdomain that does not apply to it. Because only three subdomains can then carry concern, a continuous-outcome study can still reach `extremely_serious` only by having all three remaining subdomains red.

### 5.4 Severity explanation

```python
def severity_explanation(severity: str, counts: dict[str, int],
                         per_subdomain: dict[str, str]) -> str:
    """One-sentence rationale for the chosen severity tier."""
    if severity == "none":
        return ("No serious imprecision: confidence intervals, sample size, "
                "and event counts are sufficient for the target question.")
    drivers = [SUBDOMAIN_IDS_TO_LABEL.get(sid, sid)
               for sid, j in per_subdomain.items()
               if j in ("not_precise", "probably_not_precise")]
    drivers_text = ", ".join(drivers) if drivers else "imprecision concerns"
    if severity == "serious":
        return (f"Serious imprecision: concerns in {drivers_text} "
                f"({counts['reds']} not-precise, {counts['oranges']} probably-not-precise).")
    if severity == "very_serious":
        return (f"Very serious imprecision: 2 subdomains not sufficiently "
                f"precise ({drivers_text}).")
    return (f"Extremely serious imprecision: {counts['reds']} subdomains "
            f"not sufficiently precise ({drivers_text}).")
```

### 5.5 Outcome-type heuristic

The prompt tells the model which outcome type was inferred, and the model may override it. The heuristic is cheap and deliberately conservative — it returns `None` rather than guessing when nothing matches.

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
                              assessed_outcome: str,
                              outcome_is_primary: bool = True,
                              outcome_type: str = "") -> bool | None:
    """Best-effort guess at whether the ASSESSED outcome is binary.

    Returns ``True`` for binary, ``False`` for continuous, ``None`` if
    indeterminate. The LLM is told the inferred answer and asked to confirm
    or override (and is instructed to mark event_count N/A for continuous).

    ``outcome_type`` is a per-outcome answer supplied by the caller, and wins
    outright. ``outcome_is_primary`` gates every paper-level field, because they
    all describe the paper's PRIMARY outcome (see the note below).
    """
    explicit = (outcome_type or "").strip().lower()
    if not explicit and outcome_is_primary:
        explicit = (extracted_fields.get("primary_outcome_type") or "").lower()
    if any(h in explicit for h in ("binary", "dichotom")):
        return True
    if "continuous" in explicit:
        return False

    if outcome_is_primary:
        measurement = (extracted_fields.get("primary_outcome_measurement") or "")
        definition = (extracted_fields.get("primary_outcome_definition") or "")
    else:
        measurement = definition = ""
    haystack = " ".join([assessed_outcome or "", measurement, definition]).lower()
    if any(h in haystack for h in _BINARY_HINTS):
        return True
    if any(h in haystack for h in _CONTINUOUS_HINTS):
        return False
    return None
```

Precedence matters: a caller-supplied per-outcome `outcome_type` wins; then, for the primary outcome only, an explicit `primary_outcome_type` field; otherwise binary hints are checked **before** continuous hints, so a phrase like "mean event rate" resolves to binary. The heuristic is only a hint — the model's `outcome_is_binary` overrides it when it returns an actual boolean.

**The paper-level fields describe the paper's primary outcome, so they are gated on `outcome_is_primary`.** When you rate a study for more than one outcome, `primary_outcome_type`, `primary_outcome_measurement`, and `primary_outcome_definition` all describe a *different* outcome than the one being rated. Consulting them for a secondary outcome mis-types it: a trial with binary all-cause mortality and a continuous 6-minute-walk secondary would type the secondary as binary, firing the event-count subdomain that should be N/A and manufacturing a downgrade. For a non-primary outcome, judge from the assessed-outcome string alone and return `None` (indeterminate, so the model decides) rather than guessing from the wrong outcome's description.

### 5.6 System prompt

```text
You are an evidence-synthesis methodologist assessing the GRADE imprecision
domain for a single trial. Read the PDF carefully. For each of the four
subdomains (CI width, sample size, event count, fragility), judge how precise
the primary-outcome evidence is on a 4-level scale: 'precise' (sufficiently
precise), 'probably_precise' (probably sufficiently precise),
'probably_not_precise' (probably not sufficiently precise), or 'not_precise'
(not sufficiently precise). Provide a 1-2 sentence rationale per subdomain,
quoting the paper where possible. Per the GRADE handbook, the primary tool is
whether the 95% CI for the absolute effect crosses decision thresholds — the
line of no effect, plus minimal important difference (MID) thresholds for
benefit and harm if supplied. Mark event_count as 'n_a' for continuous outcomes
(it will be excluded from severity counting). Be alert to single-trial
fragility: large relative effects on few events may appear precise but be
unreliable. If baseline risk is very low (<5%) and the absolute-risk CI is
narrow despite a wide relative-risk CI, briefly note this in rationale rather
than rating down. Return ONLY a valid JSON object — no preamble, no markdown
fences.
```

The same caveat as §4.5 applies: the reference platform defines this but sends an empty system string, so only the user prompt reaches the model in production. Send it.

The very-low-baseline-risk clause is a guardrail, not a rule the code enforces — a rare-event outcome can show a wide relative-risk CI while the absolute-risk difference is tightly bounded and decision-irrelevant. The model is asked to note it in the rationale rather than downgrade.

### 5.7 User prompt template

```text
Assess **GRADE imprecision** for the trial described in the attached PDF.

Study type: {study_type}
Outcome being rated: {assessed_outcome}

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

`{subdomains_block}` is built exactly as in §4.6. `{ctx_json}` uses a different key set:

```python
relevant_keys = [
    "primary_outcome_definition", "primary_outcome_measurement",
    "primary_outcome_type",
    "effect_size", "effect_estimate", "confidence_interval",
    "p_value", "statistical_test",
    "sample_size", "sample_size_intervention", "sample_size_comparator",
    "events_intervention", "events_comparator",
    "follow_up_duration", "baseline_risk",
    "population_outcomes",
]
```

**`{outcome_block}`** — one of three strings, from the heuristic in §5.5:

```text
Outcome type (inferred): BINARY. Judge event_count using the rule-of-thumb thresholds in the guidance.
```
```text
Outcome type (inferred): CONTINUOUS. Mark event_count as 'n_a' (it will be excluded from severity counting). Judge fragility from sample size and observed variance instead.
```
```text
Outcome type (inferred): UNCERTAIN. Determine binary vs continuous from the paper; if continuous, mark event_count as 'n_a'.
```

**`{threshold_block}`** — with MID thresholds supplied (GRADE's two-threshold framing):

```text
Decision thresholds (a priori):
  MID for benefit: {mid_benefit}
  MID for harm: {mid_harm}
```

Either field left blank renders as `(unspecified — use line-of-no-effect only)`. With neither supplied:

```text
(No MID thresholds supplied — assess CI width against the line of no effect
plus your judgement of clinically important effect sizes for this outcome.
Default to 'probably_precise' rather than 'precise' when CI width is uncertain.)
```

As with the indirectness target-PICO fallback, this is methodological rather than cosmetic: without a-priori thresholds the CI-width judgement reduces to crossing the line of no effect, and the model is told to lean toward `probably_precise` instead of `precise`.

### 5.8 Expected JSON output

Note that `n_a` appears in **only one** enum — `event_count`. The other three subdomains have no N/A path.

```json
{
  "ci_width": "precise|probably_precise|probably_not_precise|not_precise",
  "ci_width_rationale": "1-2 sentences quoting the paper",
  "sample_size": "precise|probably_precise|probably_not_precise|not_precise",
  "sample_size_rationale": "1-2 sentences quoting the paper",
  "event_count": "precise|probably_precise|probably_not_precise|not_precise|n_a",
  "event_count_rationale": "1-2 sentences quoting the paper",
  "fragility": "precise|probably_precise|probably_not_precise|not_precise",
  "fragility_rationale": "1-2 sentences quoting the paper",
  "outcome_is_binary": true,
  "sample_size_total": 1204,
  "events_total": 187,
  "ci_summary": "Brief description of the reported 95% CI for the primary outcome (e.g., \"RR 0.78, 95% CI 0.62 to 0.98\"), or null if not reported."
}
```

Assembled result adds `counts` and the coerced metadata:

```python
{
  "ci_width":     {"judgement": "probably_not_precise", "rationale": "…", "label": "Confidence-interval width"},
  "sample_size":  {"judgement": "probably_precise",     "rationale": "…", "label": "Sample-size adequacy"},
  "event_count":  {"judgement": "probably_not_precise", "rationale": "…", "label": "Event count (binary outcomes)"},
  "fragility":    {"judgement": "probably_precise",     "rationale": "…", "label": "Fragility / robustness"},
  "outcome_is_binary": True,
  "sample_size_total": 1204,
  "events_total": 187,
  "ci_summary": "RR 0.78, 95% CI 0.62 to 0.98",
  "counts": {"reds": 0, "oranges": 2},
}
# returned alongside: severity="serious", levels=1, explanation="Serious imprecision: …"
```

`sample_size_total` and `events_total` are coerced through `int()` with `None` on failure or empty string; `ci_summary` is coerced to a stripped string or `None`. These are reporting metadata — they do not feed the severity tree.

### 5.9 Answer normalisation

```python
def _normalize_judgement(raw: str) -> str:
    """Coerce LLM output to one of the four allowed values; default to probably_precise.

    N/A aliases (``n_a`` / ``not_applicable`` / ``na``) map to ``precise`` so
    they don't contribute to severity counting — used for ``event_count`` on
    continuous outcomes.
    """
    val = (raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    if val in JUDGEMENT_OPTIONS:
        return val
    aliases = {
        "sufficiently_precise": "precise",
        "probably_sufficiently_precise": "probably_precise",
        "probably_not_sufficiently_precise": "probably_not_precise",
        "not_sufficiently_precise": "not_precise",
        "yes": "precise",
        "no": "not_precise",
        "n_a": "precise",
        "na": "precise",
        "not_applicable": "precise",
        "n/a": "precise",
    }
    if val in aliases:
        return aliases[val]
    logger.warning("Imprecision: unknown judgement %r — defaulting to probably_precise", raw)
    return "probably_precise"
```

`"n/a"` survives the `-`/space substitution unchanged (the slash is not rewritten), which is why it appears in the alias table alongside `n_a`.

---

## 6. Combining — `compute_grade`

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
    idx = _grade_index(initial)
    rob_levels, rob_reason = _rob_downgrade(rob_overall, rob_domain_judgements)
    indir_levels = max(0, int(indirectness_levels or 0))
    imprec_levels = max(0, int(imprecision_levels or 0))
    total = rob_levels + indir_levels + imprec_levels
    new_idx = min(idx + total, len(GRADE_LEVELS) - 1)
    new_level = GRADE_LEVELS[new_idx]

    if total == 0:
        clean_parts: list[str] = []
        if indir_levels == 0:
            clean_parts.append("no serious indirectness")
        if imprec_levels == 0:
            clean_parts.append("no serious imprecision")
        suffix = " and " + ", ".join(clean_parts) + " detected" if clean_parts else ""
        return new_level, f"No downgrade: overall risk of bias is Low{suffix}."

    parts: list[str] = []
    if rob_levels > 0:
        unit = "level" if rob_levels == 1 else "levels"
        parts.append(f"{rob_levels} {unit} for {rob_reason}")
    if indir_levels > 0:
        sev_label = {1: "serious", 2: "very serious",
                     3: "extremely serious"}.get(indir_levels, f"{indir_levels}-level")
        unit = "level" if indir_levels == 1 else "levels"
        suffix = f" — {indirectness_explanation}" if indirectness_explanation else ""
        parts.append(f"{indir_levels} {unit} for {sev_label} indirectness{suffix}")
    if imprec_levels > 0:
        sev_label = {1: "serious", 2: "very serious",
                     3: "extremely serious"}.get(imprec_levels, f"{imprec_levels}-level")
        unit = "level" if imprec_levels == 1 else "levels"
        suffix = f" — {imprecision_explanation}" if imprecision_explanation else ""
        parts.append(f"{imprec_levels} {unit} for {sev_label} imprecision{suffix}")

    total_unit = "level" if total == 1 else "levels"
    return new_level, f"Downgraded {total} {total_unit}: " + " + ".join(parts) + "."
```

### Arithmetic

- Levels from the three domains are **summed**, not maxed. Three independent single-level concerns cost three levels.
- Both extra-domain inputs are clamped at zero — there is no rating-up path (§11).
- The result is clamped at index 3 ("Very low"). Maximum reachable total is `2 + 3 + 3 = 8`.

---

## 7. Orchestration and failure degradation

Per paper, in order:

1. Classify the study design → look up initial certainty + instrument (§2).
2. Run the RoB instrument → overall judgement + per-domain judgements.
3. If `skip_grade` (systematic reviews): stop. No certainty rating.
4. If not `skip_grade_extras`: run indirectness (§4) → `(results, severity, levels, explanation)`.
5. If not `skip_grade_extras`: run imprecision (§5) → same 4-tuple.
6. Combine (§6).

Two LLM calls total for the GRADE layer — one per module, each judging all four of its subdomains at once. Not one call per subdomain: the subdomains within a module are judged against shared context (the same target question, the same effect estimate), and splitting them multiplies cost without improving consistency.

**Failure degrades, it does not abort.** An exception in either module is caught, recorded as `{"error": "<message>"}` in that module's result slot, and leaves its level count at `0`. The paper still receives a certainty rating, downgraded for whatever did succeed. This is a deliberate trade: a partial rating with a visible error beats losing the whole paper.

> **The failure mode is optimistic.** A crashed indirectness call and a genuinely direct study both produce `levels = 0`. Whatever you persist must distinguish them — check for the `error` key before presenting "no serious indirectness" as a finding, and consider surfacing the failure in the certainty explanation itself.

---

## 8. Sample data — pre-extracted fields

Both modules consume a flat `dict[str, str]` of fields extracted from the paper in an earlier pass. Only non-empty keys from each module's `relevant_keys` list reach the prompt. A realistic union:

```python
extracted_fields = {
    # Indirectness context
    "population_description":    "Adults aged 45-80 with type 2 diabetes and established atherosclerotic cardiovascular disease",
    "population_age":            "Mean 63.1 years (SD 8.3)",
    "population_sex":            "62% male",
    "population_comorbidities":  "Hypertension 78%, prior MI 41%, CKD stage 3 22%",
    "population_setting":        "Secondary care, 412 centres",
    "geography":                 "31 countries; 44% North America, 33% Europe",
    "inclusion_criteria":        "HbA1c 7.0-10.5%, eGFR >= 30 mL/min/1.73m2",
    "exclusion_criteria":        "Type 1 diabetes, dialysis, NYHA class IV heart failure",
    "intervention_description":  "Empagliflozin 10 mg once daily, oral",
    "intervention_dose":         "10 mg once daily",
    "intervention_duration":     "Median 3.1 years",
    "intervention_provider":     "Self-administered; supervised by trial site endocrinologist",
    "comparator_description":    "Matching placebo once daily on top of standard of care",
    "comparator_type":           "Placebo",
    "follow_up_duration":        "Median 3.1 years",

    # Imprecision context
    "primary_outcome_definition":   "Composite of cardiovascular death, non-fatal MI, or non-fatal stroke",
    "primary_outcome_measurement":  "Time to first event, adjudicated by blinded committee",
    "primary_outcome_type":         "Binary (time-to-event composite)",
    "effect_size":                  "HR 0.86",
    "confidence_interval":          "95% CI 0.74 to 0.99",
    "p_value":                      "p = 0.04 for superiority",
    "statistical_test":             "Cox proportional-hazards model, stratified by region",
    "sample_size":                  "4187 randomised",
    "sample_size_intervention":     "2094",
    "sample_size_comparator":       "2093",
    "events_intervention":          "213",
    "events_comparator":            "246",
    "baseline_risk":                "11.8% over 3 years in the placebo arm",
}

target_pico = {
    "population":  "Adults with type 2 diabetes and established cardiovascular disease",
    "intervention": "SGLT2 inhibitor added to standard glucose-lowering therapy",
    "comparator":  "Standard glucose-lowering therapy alone",
    "outcome":     "Major adverse cardiovascular events",
}

imprecision_thresholds = {
    "mid_benefit": "2% absolute risk reduction in MACE over 3 years",
    "mid_harm":    "1% absolute increase in serious adverse events",
}
```

Both `target_pico` and `imprecision_thresholds` are optional; see §4.6 and §5.7 for what changes when they are absent.

---

## 9. Reference implementation as a single Python file

Turnkey and dependency-free. `llm_call` is injected: it takes the PDF bytes, the user prompt, and the system prompt, and returns the model's response already parsed into a dict. Everything else — the ladder, both severity trees, both prompt builders, both normalisers, the combiner — is here.

The two modules' identically-named helpers are prefixed (`_ind_*`, `_imp_*`) so they can coexist in one file.

```python
"""grade_certainty.py — single-study GRADE downgrade pipeline (RoB + indirectness + imprecision).

Self-contained: no framework, database, or HTTP dependencies. Supply your own
``llm_call(pdf_bytes, prompt, system) -> dict`` that returns parsed JSON.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

logger = logging.getLogger("grade")

LlmCall = Callable[[bytes, str, str], dict]


# ─────────────────────────────────────────────
# 1. Certainty ladder + initial certainty by design
# ─────────────────────────────────────────────
GRADE_LEVELS = ["High", "Moderate", "Low", "Very low"]


def grade_index(level: str) -> int:
    try:
        return GRADE_LEVELS.index(level)
    except ValueError:
        return 0


# initial_grade of None means "this design gets no GRADE certainty rating"
# (systematic reviews); skip_extras means "run the combiner, but with
# indirectness and imprecision forced to zero" (diagnostic accuracy).
INITIAL_GRADE_BY_DESIGN: dict[str, dict[str, Any]] = {
    "Randomized Controlled Trial":  {"initial_grade": "High"},
    "Crossover Trial":              {"initial_grade": "High"},
    "Cluster Randomized Trial":     {"initial_grade": "High"},
    "Cohort Study":                 {"initial_grade": "Low"},
    "Case-Control":                 {"initial_grade": "Low"},
    "Non-Randomized Trial":         {"initial_grade": "Low"},
    "Cross-Sectional (Analytical)": {"initial_grade": "Low"},
    "Case-Crossover":               {"initial_grade": "Low"},
    "Single-Arm Trial":             {"initial_grade": "Very low"},
    "Dose-Escalation Study":        {"initial_grade": "Very low"},
    "Diagnostic Accuracy":          {"initial_grade": "High", "skip_extras": True},
    "SR with Meta-Analysis":        {"initial_grade": None, "skip_grade": True},
    "SR without Meta-Analysis":     {"initial_grade": None, "skip_grade": True},
}


# ─────────────────────────────────────────────
# 2. Risk of bias → downgrade levels
# ─────────────────────────────────────────────
def rob_downgrade(rob_overall: str,
                  rob_domain_judgements: list[str] | None = None
                  ) -> tuple[int, str]:
    """Map an RoB instrument's overall judgement to 0/1/2 downgrade levels.

    Branch ORDER is load-bearing: the instrument vocabularies overlap on
    "Low" and "High", which are handled once at the top.

    NOTE: ROBINS-I Domain 1's "Low (except for concerns about uncontrolled
    confounding)" / "... benchmarking)" labels must be normalised to plain
    "Low" by the RoB tool's overall aggregator BEFORE reaching this function,
    or they fall through to the catch-all and cost a level.
    """
    judgements = rob_domain_judgements or []
    if rob_overall == "Low":
        return 0, "Low risk of bias"

    # RoB 2 (and its cross-over / cluster extensions)
    if rob_overall == "Some concerns":
        return 1, "Some concerns in risk of bias"
    if rob_overall == "High":
        high_count = sum(1 for j in judgements if j == "High")
        if high_count >= 2:
            return 2, f"High risk of bias in {high_count} domains"
        return 1, "High risk of bias"

    # ROBINS-I V2 (and V1, which shares this vocabulary)
    if rob_overall == "Moderate":
        return 1, "Moderate risk of bias (ROBINS-I V2)"
    if rob_overall == "Serious":
        serious_count = sum(1 for j in judgements if j == "Serious")
        if serious_count >= 2:
            return 2, f"Serious risk of bias in {serious_count} ROBINS-I V2 domains"
        return 1, "Serious risk of bias (ROBINS-I V2)"
    if rob_overall == "Critical":
        return 2, "Critical risk of bias (ROBINS-I V2)"

    # ROBINS-I V1 only — V2 retired the "No information" overall judgement
    if rob_overall == "No information":
        return 1, "No information in one or more ROBINS-I domains (conservative; legacy V1 result)"

    # QUADAS-3
    if rob_overall == "Insufficient information":
        return 1, "Insufficient information in one or more QUADAS-3 domains (conservative)"

    # QUADAS-2
    if rob_overall == "Unclear":
        return 1, "Unclear risk of bias in one or more QUADAS-2 domains (conservative)"

    return 1, f"risk of bias ({rob_overall})"


def collect_domain_judgements(rob_domains: dict[str, Any]) -> list[str]:
    """Filter an RoB instrument's domain map down to entries carrying a judgement.

    Instruments that store preflight metadata alongside the domains would
    otherwise poison the High/Serious domain counts.
    """
    return [
        d.get("judgement", "Low")
        for k, d in rob_domains.items()
        if k != "preflight" and isinstance(d, dict) and "judgement" in d
    ]


# ─────────────────────────────────────────────
# 3. Indirectness — scale, subdomains, tree
# ─────────────────────────────────────────────
IND_JUDGEMENT_OPTIONS = ("direct", "probably_direct", "probably_not_direct", "not_direct")
SEVERITY_LEVELS = ("none", "serious", "very_serious", "extremely_serious")

IND_SUBDOMAINS: list[dict[str, Any]] = [
    {
        "id": "population",
        "label": "Population",
        "guidance": (
            "Assess how closely the study population matches the population of "
            "interest in the target question (age, sex, comorbidities, severity, "
            "setting, geographic context). Highly selected, narrow, or atypical "
            "populations limit generalisability and warrant 'probably not "
            "sufficiently direct' or stronger."
        ),
    },
    {
        "id": "intervention",
        "label": "Intervention",
        "guidance": (
            "Assess how closely the studied intervention matches the intervention "
            "of interest (dose, formulation, mode of delivery, intensity, "
            "duration, provider type). Substantial differences in delivery "
            "context (\"too ideal\" trial conditions, specialised provider, "
            "non-translatable infrastructure) warrant downgrading."
        ),
    },
    {
        "id": "comparator",
        "label": "Comparator",
        "guidance": (
            "Assess how closely the studied comparator matches the comparator of "
            "interest. Active comparators that include potentially effective "
            "co-interventions, or 'usual care' that varies markedly across "
            "settings, warrant downgrading. Placebo controls when the question "
            "is about head-to-head comparison are not direct."
        ),
    },
    {
        "id": "outcome",
        "label": "Outcomes",
        "guidance": (
            "Assess whether outcome measures capture what matters to patients. "
            "Per the GRADE handbook: 'surrogate outcomes should be rated down "
            "for indirectness unless there is a strong and well-established "
            "correlation with meaningful, patient-important outcomes — a "
            "criterion that is rarely fulfilled.' Examples of surrogates: "
            "HbA1c (diabetes complications), LDL cholesterol (cardiovascular "
            "events), bone mineral density (fractures), progression-free "
            "survival (overall survival), tumour response rate (survival)."
        ),
    },
]

IND_SUBDOMAIN_IDS = [s["id"] for s in IND_SUBDOMAINS]
IND_LABELS = {s["id"]: s["label"] for s in IND_SUBDOMAINS}


def indirectness_severity(judgements: dict[str, str]) -> tuple[str, int, dict[str, int]]:
    """reds=not_direct, oranges=probably_not_direct.

      reds>=3            → extremely_serious (3)
      reds==2            → very_serious (2)
      reds==1 or o>=2    → serious (1)
      otherwise          → none (0)
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


def indirectness_explanation(severity: str, counts: dict[str, int],
                             per_subdomain: dict[str, str]) -> str:
    if severity == "none":
        return ("No serious indirectness: PICO components are sufficiently "
                "direct for the target question.")
    drivers = [IND_LABELS.get(sid, sid)
               for sid, j in per_subdomain.items()
               if j in ("not_direct", "probably_not_direct")]
    drivers_text = ", ".join(drivers) if drivers else "PICO mismatch"
    if severity == "serious":
        return (f"Serious indirectness: concerns in {drivers_text} "
                f"({counts['reds']} not-direct, {counts['oranges']} probably-not-direct).")
    if severity == "very_serious":
        return (f"Very serious indirectness: 2 PICO components not sufficiently "
                f"direct ({drivers_text}).")
    return (f"Extremely serious indirectness: {counts['reds']} PICO components "
            f"not sufficiently direct ({drivers_text}).")


# ─────────────────────────────────────────────
# 4. Indirectness — prompts
# ─────────────────────────────────────────────
IND_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing the GRADE "
    "indirectness domain for a single study. Read the PDF carefully. For each "
    "of the four PICO subdomains (Population, Intervention, Comparison, "
    "Outcome), judge how directly the study's evidence applies to the "
    "specified target question on a 4-level scale: 'direct' (sufficiently "
    "direct), 'probably_direct' (probably sufficiently direct), "
    "'probably_not_direct' (probably not sufficiently direct), or 'not_direct' "
    "(not sufficiently direct). Provide a 1-2 sentence rationale per "
    "subdomain, quoting the paper where possible. Per GRADE guidance, do NOT "
    "rate down unless there are compelling reasons to believe the mismatch "
    "would lead to meaningful, systematic differences in the effect estimate. "
    "Surrogate outcomes (HbA1c, LDL, bone density, progression-free survival, "
    "etc.) should be rated 'probably_not_direct' or worse unless a strong, "
    "well-established correlation with patient-important outcomes is "
    "documented in the paper. Return ONLY a valid JSON object — no preamble, "
    "no markdown fences."
)

IND_RELEVANT_KEYS = [
    "population_description", "population_age", "population_sex",
    "population_comorbidities", "population_setting", "geography",
    "inclusion_criteria", "exclusion_criteria",
    "intervention_description", "intervention_dose",
    "intervention_duration", "intervention_provider",
    "comparator_description", "comparator_type",
    "primary_outcome_definition", "primary_outcome_measurement",
    "follow_up_duration",
]


def format_target_pico(target_pico: dict[str, str] | None) -> str:
    """Render the target PICO block, or the no-target fallback.

    The fallback is methodological, not cosmetic: with no review question the
    assessment collapses to outcome surrogacy and P/I/C are pinned near-direct.
    """
    if not target_pico or not any(
            (target_pico.get(k) or "").strip()
            for k in ("population", "intervention", "comparator", "outcome")):
        return (
            "(No target PICO supplied — assess against the as-conducted PICO "
            "of the study itself. Focus the OUTCOME judgement on whether the "
            "primary outcome is a surrogate vs. a patient-important outcome. "
            "For Population, Intervention, and Comparator, default to "
            "'probably_direct' unless the study's selection is unusually "
            "narrow or atypical for routine clinical use.)"
        )
    lines = []
    for key, label in (("population", "Population"),
                       ("intervention", "Intervention"),
                       ("comparator", "Comparator"),
                       ("outcome", "Outcome")):
        val = (target_pico.get(key) or "").strip()
        lines.append(f"  {label}: {val if val else '(unspecified — judge based on as-conducted PICO)'}")
    return "Target question (PICO):\n" + "\n".join(lines)


def _context_json(extracted_fields: dict[str, str], keys: list[str]) -> str:
    relevant = {k: extracted_fields[k] for k in keys if extracted_fields.get(k)}
    return json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"


def _subdomains_block(subdomains: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"\n**{sub['label']} ({sub['id']})**\nGuidance: {sub['guidance']}"
        for sub in subdomains
    )


def build_indirectness_prompt(target_pico: dict[str, str] | None,
                              study_type: str,
                              primary_outcome: str,
                              extracted_fields: dict[str, str]) -> str:
    ctx_json = _context_json(extracted_fields, IND_RELEVANT_KEYS)
    target_block = format_target_pico(target_pico)
    subdomains_block = _subdomains_block(IND_SUBDOMAINS)

    shape = "{\n"
    for sub in IND_SUBDOMAINS:
        sid = sub["id"]
        shape += f'  "{sid}": "direct|probably_direct|probably_not_direct|not_direct",\n'
        shape += f'  "{sid}_rationale": "1-2 sentences quoting the paper",\n'
    shape += '  "primary_outcome_is_surrogate": true|false,\n'
    shape += ('  "surrogate_rationale": "If outcome is a surrogate, briefly explain '
              '(e.g., \\"HbA1c is a surrogate for diabetes complications\\")."\n')
    shape += "}"

    return f"""Assess **GRADE indirectness** for the study described in the attached PDF.

Study type: {study_type}
Outcome being rated: {assessed_outcome}

{target_block}

Context (fields already extracted from the paper):
{ctx_json}

Subdomains to judge:
{subdomains_block}

Return a JSON object with exactly this shape:
{shape}

For each subdomain, weigh whether the mismatch is likely to produce systematic differences in effect estimates. Default to 'probably_direct' rather than 'direct' when there is any meaningful uncertainty. Reserve 'not_direct' for clear, substantial mismatches. Rationales must quote the paper verbatim where possible."""


def normalize_indirectness(raw: str) -> str:
    """Coerce to one of the four options; unknown → probably_direct (non-downgrading)."""
    val = (raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    if val in IND_JUDGEMENT_OPTIONS:
        return val
    aliases = {
        "sufficiently_direct": "direct",
        "probably_sufficiently_direct": "probably_direct",
        "probably_not_sufficiently_direct": "probably_not_direct",
        "not_sufficiently_direct": "not_direct",
        "yes": "direct",
        "no": "not_direct",
    }
    if val in aliases:
        return aliases[val]
    logger.warning("Indirectness: unknown judgement %r — defaulting to probably_direct", raw)
    return "probably_direct"


def assess_indirectness(llm_call: LlmCall,
                        pdf_bytes: bytes,
                        extracted_fields: dict[str, str],
                        study_type: str,
                        primary_outcome: str,
                        target_pico: dict[str, str] | None = None,
                        ) -> tuple[dict[str, Any], str, int, str]:
    """Returns (per_subdomain_results, severity_label, downgrade_levels, explanation)."""
    prompt = build_indirectness_prompt(target_pico, study_type, primary_outcome,
                                       extracted_fields)
    raw = llm_call(pdf_bytes, prompt, IND_SYSTEM_PROMPT)

    per_sub: dict[str, Any] = {}
    judgements: dict[str, str] = {}
    for sub in IND_SUBDOMAINS:
        sid = sub["id"]
        judgement = normalize_indirectness(str(raw.get(sid, "")))
        per_sub[sid] = {"judgement": judgement,
                        "rationale": str(raw.get(f"{sid}_rationale", "")).strip(),
                        "label": sub["label"]}
        judgements[sid] = judgement

    per_sub["primary_outcome_is_surrogate"] = bool(raw.get("primary_outcome_is_surrogate", False))
    per_sub["surrogate_rationale"] = str(raw.get("surrogate_rationale", "")).strip()

    severity, levels, counts = indirectness_severity(judgements)
    per_sub["counts"] = counts
    return per_sub, severity, levels, indirectness_explanation(severity, counts, judgements)


# ─────────────────────────────────────────────
# 5. Imprecision — scale, subdomains, tree
# ─────────────────────────────────────────────
IMP_JUDGEMENT_OPTIONS = ("precise", "probably_precise", "probably_not_precise", "not_precise")

IMP_SUBDOMAINS: list[dict[str, Any]] = [
    {
        "id": "ci_width",
        "label": "Confidence-interval width",
        "guidance": (
            "Per the GRADE handbook, imprecision is judged primarily by "
            "whether the 95% confidence interval around the absolute effect "
            "estimate crosses clinical-decision thresholds. Default thresholds "
            "are the line of no effect plus the minimal important difference "
            "(MID) for benefit and harm if supplied. A CI that does not cross "
            "any threshold → 'precise'. A CI crossing one threshold → "
            "'probably_not_precise' (1-level concern). A CI crossing two or "
            "more thresholds → 'not_precise'. If no effect estimate / CI is "
            "reported, return 'probably_not_precise' and explain in rationale."
        ),
    },
    {
        "id": "sample_size",
        "label": "Sample-size adequacy",
        "guidance": (
            "Is the enrolled N large enough that the result is unlikely to "
            "flip with a few more participants? This is a single-trial "
            "surrogate for the GRADE Optimal Information Size (we do not "
            "compute formal RIS). Rule-of-thumb thresholds for a clinically "
            "important effect: <100 total participants → 'not_precise'; "
            "100–300 → 'probably_not_precise'; 300–1000 → 'probably_precise'; "
            ">1000 → 'precise'. Adjust for outcome type and observed effect "
            "size; underpowered trials with extreme effects warrant concern."
        ),
    },
    {
        "id": "event_count",
        "label": "Event count (binary outcomes)",
        "guidance": (
            "For binary primary outcomes: are there enough events to support "
            "the observed effect? Rule-of-thumb: <100 total events across "
            "arms → 'not_precise'; 100–300 → 'probably_not_precise'; "
            "300–1000 → 'probably_precise'; >1000 → 'precise'. Pay extra "
            "attention to the smaller arm — significance driven by ≤10 "
            "events in one arm is fragile. **Mark this subdomain N/A for "
            "continuous outcomes** (return ``\"n_a\"`` or ``\"not_applicable\"``); "
            "the normalizer treats N/A as 'precise' so it never contributes "
            "to severity counting."
        ),
    },
    {
        "id": "fragility",
        "label": "Fragility / robustness",
        "guidance": (
            "Could a small number of additional events change the conclusion? "
            "Per the GRADE handbook: small studies that produce large "
            "relative effects on dichotomous outcomes can appear precise via "
            "narrow CIs but be fragile because CIs for odds ratios / relative "
            "risks tend to narrow as effects grow. Flag: extreme effect "
            "sizes from few events, p-values barely under 0.05 with small N, "
            "single-event-driven significance, or large relative effects "
            "(RRR > 50%) with sparse data. Continuous outcomes: judge "
            "robustness from observed variance + sample size."
        ),
    },
]

IMP_SUBDOMAIN_IDS = [s["id"] for s in IMP_SUBDOMAINS]
IMP_LABELS = {s["id"]: s["label"] for s in IMP_SUBDOMAINS}


def imprecision_severity(judgements: dict[str, str]) -> tuple[str, int, dict[str, int]]:
    """reds=not_precise, oranges=probably_not_precise. Same tree as indirectness.

    N/A subdomains (event_count on continuous outcomes) are normalised to
    'precise' upstream so they never contribute to reds/oranges.
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


def imprecision_explanation(severity: str, counts: dict[str, int],
                            per_subdomain: dict[str, str]) -> str:
    if severity == "none":
        return ("No serious imprecision: confidence intervals, sample size, "
                "and event counts are sufficient for the target question.")
    drivers = [IMP_LABELS.get(sid, sid)
               for sid, j in per_subdomain.items()
               if j in ("not_precise", "probably_not_precise")]
    drivers_text = ", ".join(drivers) if drivers else "imprecision concerns"
    if severity == "serious":
        return (f"Serious imprecision: concerns in {drivers_text} "
                f"({counts['reds']} not-precise, {counts['oranges']} probably-not-precise).")
    if severity == "very_serious":
        return (f"Very serious imprecision: 2 subdomains not sufficiently "
                f"precise ({drivers_text}).")
    return (f"Extremely serious imprecision: {counts['reds']} subdomains "
            f"not sufficiently precise ({drivers_text}).")


# ─────────────────────────────────────────────
# 6. Imprecision — outcome-type heuristic + prompts
# ─────────────────────────────────────────────
_BINARY_HINTS = (
    "binary", "dichotom", "event", "incidence", "mortalit", "death",
    "rate", "proportion", "frequency", "occurrence",
)
_CONTINUOUS_HINTS = (
    "continuous", "mean", "score", "scale", "concentration",
    "level", "change from baseline",
)


def infer_outcome_is_binary(extracted_fields: dict[str, str],
                            assessed_outcome: str,
                            outcome_is_primary: bool = True,
                            outcome_type: str = "") -> bool | None:
    """True=binary, False=continuous, None=indeterminate.

    A caller-supplied outcome_type wins; then, for the PRIMARY outcome only,
    primary_outcome_type; otherwise binary hints are checked BEFORE continuous
    hints. Paper-level primary_outcome_* fields are gated on outcome_is_primary.
    """
    explicit = (outcome_type or "").strip().lower()
    if not explicit and outcome_is_primary:
        explicit = (extracted_fields.get("primary_outcome_type") or "").lower()
    if any(h in explicit for h in ("binary", "dichotom")):
        return True
    if "continuous" in explicit:
        return False

    if outcome_is_primary:
        measurement = (extracted_fields.get("primary_outcome_measurement") or "")
        definition = (extracted_fields.get("primary_outcome_definition") or "")
    else:
        measurement = definition = ""
    haystack = " ".join([assessed_outcome or "", measurement, definition]).lower()
    if any(h in haystack for h in _BINARY_HINTS):
        return True
    if any(h in haystack for h in _CONTINUOUS_HINTS):
        return False
    return None


IMP_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing the GRADE "
    "imprecision domain for a single trial. Read the PDF carefully. For each "
    "of the four subdomains (CI width, sample size, event count, fragility), "
    "judge how precise the primary-outcome evidence is on a 4-level scale: "
    "'precise' (sufficiently precise), 'probably_precise' (probably "
    "sufficiently precise), 'probably_not_precise' (probably not "
    "sufficiently precise), or 'not_precise' (not sufficiently precise). "
    "Provide a 1-2 sentence rationale per subdomain, quoting the paper "
    "where possible. Per the GRADE handbook, the primary tool is whether "
    "the 95% CI for the absolute effect crosses decision thresholds — the "
    "line of no effect, plus minimal important difference (MID) thresholds "
    "for benefit and harm if supplied. Mark event_count as 'n_a' for "
    "continuous outcomes (it will be excluded from severity counting). Be "
    "alert to single-trial fragility: large relative effects on few events "
    "may appear precise but be unreliable. If baseline risk is very low "
    "(<5%) and the absolute-risk CI is narrow despite a wide relative-risk "
    "CI, briefly note this in rationale rather than rating down. Return "
    "ONLY a valid JSON object — no preamble, no markdown fences."
)

IMP_RELEVANT_KEYS = [
    "primary_outcome_definition", "primary_outcome_measurement",
    "primary_outcome_type",
    "effect_size", "effect_estimate", "confidence_interval",
    "p_value", "statistical_test",
    "sample_size", "sample_size_intervention", "sample_size_comparator",
    "events_intervention", "events_comparator",
    "follow_up_duration", "baseline_risk",
    "population_outcomes",
]


def format_thresholds(thresholds: dict[str, str] | None) -> str:
    if not thresholds or not any(
            (thresholds.get(k) or "").strip() for k in ("mid_benefit", "mid_harm")):
        return (
            "(No MID thresholds supplied — assess CI width against the line "
            "of no effect plus your judgement of clinically important "
            "effect sizes for this outcome. Default to 'probably_precise' "
            "rather than 'precise' when CI width is uncertain.)"
        )
    lines = []
    for key, label in (("mid_benefit", "MID for benefit"),
                       ("mid_harm", "MID for harm")):
        val = (thresholds.get(key) or "").strip()
        lines.append(f"  {label}: {val if val else '(unspecified — use line-of-no-effect only)'}")
    return "Decision thresholds (a priori):\n" + "\n".join(lines)


def format_outcome_type(outcome_is_binary: bool | None) -> str:
    if outcome_is_binary is True:
        return ("Outcome type (inferred): BINARY. Judge event_count using "
                "the rule-of-thumb thresholds in the guidance.")
    if outcome_is_binary is False:
        return ("Outcome type (inferred): CONTINUOUS. Mark event_count as "
                "'n_a' (it will be excluded from severity counting). Judge "
                "fragility from sample size and observed variance instead.")
    return ("Outcome type (inferred): UNCERTAIN. Determine binary vs "
            "continuous from the paper; if continuous, mark event_count as "
            "'n_a'.")


def build_imprecision_prompt(thresholds: dict[str, str] | None,
                             study_type: str,
                             primary_outcome: str,
                             extracted_fields: dict[str, str],
                             outcome_is_binary: bool | None = None) -> str:
    ctx_json = _context_json(extracted_fields, IMP_RELEVANT_KEYS)
    threshold_block = format_thresholds(thresholds)
    outcome_block = format_outcome_type(outcome_is_binary)
    subdomains_block = _subdomains_block(IMP_SUBDOMAINS)

    shape = "{\n"
    for sub in IMP_SUBDOMAINS:
        sid = sub["id"]
        if sid == "event_count":
            shape += f'  "{sid}": "precise|probably_precise|probably_not_precise|not_precise|n_a",\n'
        else:
            shape += f'  "{sid}": "precise|probably_precise|probably_not_precise|not_precise",\n'
        shape += f'  "{sid}_rationale": "1-2 sentences quoting the paper",\n'
    shape += '  "outcome_is_binary": true|false,\n'
    shape += '  "sample_size_total": <integer or null>,\n'
    shape += '  "events_total": <integer or null>,\n'
    shape += ('  "ci_summary": "Brief description of the reported 95% CI for the primary '
              'outcome (e.g., \\"RR 0.78, 95% CI 0.62 to 0.98\\"), or null if not reported."\n')
    shape += "}"

    return f"""Assess **GRADE imprecision** for the trial described in the attached PDF.

Study type: {study_type}
Outcome being rated: {assessed_outcome}

{outcome_block}

{threshold_block}

Context (fields already extracted from the paper):
{ctx_json}

Subdomains to judge:
{subdomains_block}

Return a JSON object with exactly this shape:
{shape}

For each subdomain, weigh whether imprecision is likely to leave the truth uncertain for clinical decision-making. Default to 'probably_precise' rather than 'precise' when there is meaningful uncertainty. Reserve 'not_precise' for clear, substantial imprecision concerns. Rationales must quote the paper verbatim where possible (effect estimates, CIs, sample sizes, event counts)."""


def normalize_imprecision(raw: str) -> str:
    """Coerce to one of the four options. N/A aliases → 'precise' so they never
    contribute to severity counting. Unknown → probably_precise."""
    val = (raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    if val in IMP_JUDGEMENT_OPTIONS:
        return val
    aliases = {
        "sufficiently_precise": "precise",
        "probably_sufficiently_precise": "probably_precise",
        "probably_not_sufficiently_precise": "probably_not_precise",
        "not_sufficiently_precise": "not_precise",
        "yes": "precise",
        "no": "not_precise",
        "n_a": "precise",
        "na": "precise",
        "not_applicable": "precise",
        "n/a": "precise",
    }
    if val in aliases:
        return aliases[val]
    logger.warning("Imprecision: unknown judgement %r — defaulting to probably_precise", raw)
    return "probably_precise"


def _coerce_int(v: Any) -> int | None:
    try:
        if v is None or v == "":
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def assess_imprecision(llm_call: LlmCall,
                       pdf_bytes: bytes,
                       extracted_fields: dict[str, str],
                       study_type: str,
                       primary_outcome: str,
                       thresholds: dict[str, str] | None = None,
                       ) -> tuple[dict[str, Any], str, int, str]:
    """Returns (per_subdomain_results, severity_label, downgrade_levels, explanation)."""
    inferred_binary = infer_outcome_is_binary(extracted_fields, primary_outcome)
    prompt = build_imprecision_prompt(thresholds, study_type, primary_outcome,
                                      extracted_fields, outcome_is_binary=inferred_binary)
    raw = llm_call(pdf_bytes, prompt, IMP_SYSTEM_PROMPT)

    per_sub: dict[str, Any] = {}
    judgements: dict[str, str] = {}
    for sub in IMP_SUBDOMAINS:
        sid = sub["id"]
        judgement = normalize_imprecision(str(raw.get(sid, "")))
        per_sub[sid] = {"judgement": judgement,
                        "rationale": str(raw.get(f"{sid}_rationale", "")).strip(),
                        "label": sub["label"]}
        judgements[sid] = judgement

    raw_binary = raw.get("outcome_is_binary")
    per_sub["outcome_is_binary"] = raw_binary if isinstance(raw_binary, bool) else inferred_binary
    per_sub["sample_size_total"] = _coerce_int(raw.get("sample_size_total"))
    per_sub["events_total"] = _coerce_int(raw.get("events_total"))
    per_sub["ci_summary"] = str(raw.get("ci_summary", "") or "").strip() or None

    severity, levels, counts = imprecision_severity(judgements)
    per_sub["counts"] = counts
    return per_sub, severity, levels, imprecision_explanation(severity, counts, judgements)


# ─────────────────────────────────────────────
# 8. Combining
# ─────────────────────────────────────────────
def compute_grade(initial: str,
                  rob_overall: str,
                  rob_domain_judgements: list[str] | None = None,
                  indirectness_levels: int = 0,
                  indirectness_explanation: str = "",
                  imprecision_levels: int = 0,
                  imprecision_explanation: str = "",
                  *,
                  inconsistency_levels: int = 0,
                  inconsistency_explanation: str = "",
                  publication_bias_levels: int = 0,
                  publication_bias_explanation: str = "",
                  not_assessed: frozenset[str] = frozenset(),
                  ) -> tuple[str, str]:
    """Sum the downgrade domains, clamp at 'Very low', build the explanation.

    There is no rating-up path — every domain input is clamped at 0.

    The two body-of-evidence domains default to inert: with no inconsistency
    or publication-bias arguments this returns a byte-identical level AND
    explanation to the three-domain single-study version. Explanation parts
    are emitted in canonical GRADE order: risk of bias, inconsistency,
    indirectness, imprecision, publication bias.
    """
    idx = grade_index(initial)
    rob_levels, rob_reason = rob_downgrade(rob_overall, rob_domain_judgements)
    indir_levels = max(0, int(indirectness_levels or 0))
    imprec_levels = max(0, int(imprecision_levels or 0))
    inconsis_levels = max(0, int(inconsistency_levels or 0))
    pubbias_levels = max(0, int(publication_bias_levels or 0))
    total = rob_levels + inconsis_levels + indir_levels + imprec_levels + pubbias_levels
    new_idx = min(idx + total, len(GRADE_LEVELS) - 1)
    new_level = GRADE_LEVELS[new_idx]

    if total == 0:
        clean_parts: list[str] = []
        # A body-of-evidence domain only appears in the string when it was
        # actually in play — that is what keeps single-study output unchanged.
        if "inconsistency" in not_assessed:
            clean_parts.append("inconsistency not applicable")
        elif inconsistency_explanation:
            clean_parts.append("no serious inconsistency")
        if indir_levels == 0:
            clean_parts.append("no serious indirectness")
        if imprec_levels == 0:
            clean_parts.append("no serious imprecision")
        if "publication_bias" in not_assessed:
            clean_parts.append("publication bias not applicable")
        elif publication_bias_explanation:
            clean_parts.append("publication bias undetected")
        suffix = " and " + ", ".join(clean_parts) + " detected" if clean_parts else ""
        return new_level, f"No downgrade: overall risk of bias is Low{suffix}."

    parts: list[str] = []
    if rob_levels > 0:
        unit = "level" if rob_levels == 1 else "levels"
        parts.append(f"{rob_levels} {unit} for {rob_reason}")
    if inconsis_levels > 0:
        sev_label = {1: "serious", 2: "very serious"}.get(
            inconsis_levels, f"{inconsis_levels}-level")
        unit = "level" if inconsis_levels == 1 else "levels"
        suffix = f" — {inconsistency_explanation}" if inconsistency_explanation else ""
        parts.append(f"{inconsis_levels} {unit} for {sev_label} inconsistency{suffix}")
    if indir_levels > 0:
        sev_label = {1: "serious", 2: "very serious",
                     3: "extremely serious"}.get(indir_levels, f"{indir_levels}-level")
        unit = "level" if indir_levels == 1 else "levels"
        suffix = f" — {indirectness_explanation}" if indirectness_explanation else ""
        parts.append(f"{indir_levels} {unit} for {sev_label} indirectness{suffix}")
    if imprec_levels > 0:
        sev_label = {1: "serious", 2: "very serious",
                     3: "extremely serious"}.get(imprec_levels, f"{imprec_levels}-level")
        unit = "level" if imprec_levels == 1 else "levels"
        suffix = f" — {imprecision_explanation}" if imprecision_explanation else ""
        parts.append(f"{imprec_levels} {unit} for {sev_label} imprecision{suffix}")
    if pubbias_levels > 0:
        # GRADE phrases this domain differently and only ever costs 1 level.
        unit = "level" if pubbias_levels == 1 else "levels"
        suffix = f" — {publication_bias_explanation}" if publication_bias_explanation else ""
        parts.append(f"{pubbias_levels} {unit} for publication bias strongly suspected{suffix}")

    total_unit = "level" if total == 1 else "levels"
    return new_level, f"Downgraded {total} {total_unit}: " + " + ".join(parts) + "."


# ─────────────────────────────────────────────
# 9. Top-level entry point
# ─────────────────────────────────────────────
def assess_certainty(llm_call: LlmCall,
                     pdf_bytes: bytes,
                     *,
                     study_type: str,
                     extracted_fields: dict[str, str],
                     primary_outcome: str,
                     rob_overall: str,
                     rob_domains: dict[str, Any] | None = None,
                     target_pico: dict[str, str] | None = None,
                     imprecision_thresholds: dict[str, str] | None = None,
                     ) -> dict[str, Any]:
    """Run the GRADE downgrade pipeline for one study.

    Failure in either extra-domain module degrades to zero levels with an
    ``error`` key rather than aborting the assessment. Check for that key
    before presenting "no serious indirectness/imprecision" as a finding.
    """
    cfg = INITIAL_GRADE_BY_DESIGN.get(study_type)
    if cfg is None:
        return {"status": "unsupported_study_type", "study_type": study_type}

    if cfg.get("skip_grade"):
        # Systematic reviews: the appraisal tool's own rating is the headline
        # output; a review's methodological quality is not a GRADE certainty.
        return {"status": "grade_skipped", "study_type": study_type,
                "rob_overall": rob_overall}

    domain_judgements = collect_domain_judgements(rob_domains or {})
    skip_extras = bool(cfg.get("skip_extras"))

    indirectness: dict[str, Any] = {}
    indir_severity, indir_levels, indir_expl = "none", 0, ""
    imprecision: dict[str, Any] = {}
    imprec_severity, imprec_levels, imprec_expl = "none", 0, ""

    if not skip_extras:
        try:
            indirectness, indir_severity, indir_levels, indir_expl = assess_indirectness(
                llm_call, pdf_bytes, extracted_fields, study_type,
                primary_outcome, target_pico=target_pico)
        except Exception:
            logger.exception("Indirectness assessment failed")
            indirectness = {"error": "Indirectness assessment failed."}

        try:
            imprecision, imprec_severity, imprec_levels, imprec_expl = assess_imprecision(
                llm_call, pdf_bytes, extracted_fields, study_type,
                primary_outcome, thresholds=imprecision_thresholds)
        except Exception:
            logger.exception("Imprecision assessment failed")
            imprecision = {"error": "Imprecision assessment failed."}

    initial_grade = cfg["initial_grade"]
    updated_grade, explanation = compute_grade(
        initial_grade, rob_overall, domain_judgements,
        indirectness_levels=indir_levels, indirectness_explanation=indir_expl,
        imprecision_levels=imprec_levels, imprecision_explanation=imprec_expl)

    return {
        "status": "ok",
        "study_type": study_type,
        "initial_grade": initial_grade,
        "updated_grade": updated_grade,
        "grade_explanation": explanation,
        "rob_overall": rob_overall,
        "indirectness": indirectness,
        "indirectness_overall": indir_severity,
        "indirectness_levels": indir_levels,
        "indirectness_explanation": indir_expl,
        "imprecision": imprecision,
        "imprecision_overall": imprec_severity,
        "imprecision_levels": imprec_levels,
        "imprecision_explanation": imprec_expl,
        "extras_skipped": skip_extras,
    }
```

---

## 10. Quick test sketches (no framework — plain assert)

Drop at the bottom of the reference module and run with `python3 grade_certainty.py`.

```python
# ── Ladder ──
assert grade_index("High") == 0
assert grade_index("Very low") == 3
assert grade_index("nonsense") == 0          # fails OPEN to High — documented, not desirable

# ── RoB → downgrade: every branch ──
assert rob_downgrade("Low") == (0, "Low risk of bias")
assert rob_downgrade("Some concerns")[0] == 1
assert rob_downgrade("High", ["High", "Low", "Low"])[0] == 1
assert rob_downgrade("High", ["High", "High", "Low"]) == (2, "High risk of bias in 2 domains")
assert rob_downgrade("Moderate")[0] == 1
assert rob_downgrade("Serious", ["Serious", "Low"])[0] == 1
assert rob_downgrade("Serious", ["Serious", "Serious", "Moderate"])[0] == 2
assert rob_downgrade("Critical")[0] == 2
assert rob_downgrade("Critical", ["Critical", "Critical"])[0] == 2   # never escalates past 2
assert rob_downgrade("No information")[0] == 1                        # ROBINS-I V1 legacy
assert rob_downgrade("Insufficient information")[0] == 1              # QUADAS-3
assert rob_downgrade("Unclear")[0] == 1                               # QUADAS-2
# Un-normalised ROBINS-I Domain 1 label hits the catch-all and silently costs a level
assert rob_downgrade("Low (except for concerns about uncontrolled confounding)")[0] == 1
assert rob_downgrade("Banana")[0] == 1

# Preflight metadata must not leak into the domain count
assert collect_domain_judgements({
    "preflight": {"variant": "single_arm"},
    "1": {"judgement": "High"}, "2": {"judgement": "High"},
    "notes": "free text",
}) == ["High", "High"]

# ── Indirectness severity tree ──
D, PD, PND, ND = "direct", "probably_direct", "probably_not_direct", "not_direct"
assert indirectness_severity({"a": D, "b": D, "c": D, "d": D})[:2] == ("none", 0)
assert indirectness_severity({"a": D, "b": PD, "c": D, "d": PND})[:2] == ("none", 0)   # 1 orange tolerated
assert indirectness_severity({"a": D, "b": PND, "c": D, "d": PND})[:2] == ("serious", 1)
assert indirectness_severity({"a": D, "b": D, "c": D, "d": ND})[:2] == ("serious", 1)
assert indirectness_severity({"a": ND, "b": PND, "c": PND, "d": D})[:2] == ("serious", 1)  # reds==1 wins
assert indirectness_severity({"a": ND, "b": ND, "c": D, "d": D})[:2] == ("very_serious", 2)
assert indirectness_severity({"a": ND, "b": ND, "c": ND, "d": D})[:2] == ("extremely_serious", 3)
assert indirectness_severity({"a": ND, "b": ND, "c": ND, "d": ND})[:2] == ("extremely_serious", 3)

# ── Imprecision severity tree (same shape, different tokens) ──
P, PP, PNP, NP = "precise", "probably_precise", "probably_not_precise", "not_precise"
assert imprecision_severity({"a": P, "b": PP, "c": P, "d": PNP})[:2] == ("none", 0)
assert imprecision_severity({"a": PNP, "b": PNP, "c": P, "d": P})[:2] == ("serious", 1)
assert imprecision_severity({"a": NP, "b": NP, "c": P, "d": P})[:2] == ("very_serious", 2)
assert imprecision_severity({"a": NP, "b": NP, "c": NP, "d": P})[:2] == ("extremely_serious", 3)

# ── N/A never contributes: a continuous-outcome paper that is otherwise clean ──
assert normalize_imprecision("n_a") == "precise"
assert normalize_imprecision("N/A") == "precise"
assert normalize_imprecision("not applicable") == "precise"
assert imprecision_severity({
    "ci_width": P, "sample_size": PP,
    "event_count": normalize_imprecision("n_a"),   # continuous outcome
    "fragility": PP,
})[:2] == ("none", 0)

# ── Normalisation defaults are non-downgrading ──
assert normalize_indirectness("Probably Direct") == "probably_direct"
assert normalize_indirectness("not-sufficiently-direct") == "not_direct"
assert normalize_indirectness("???") == "probably_direct"
assert normalize_imprecision("???") == "probably_precise"

# ── Outcome-type heuristic ──
assert infer_outcome_is_binary({"primary_outcome_type": "Binary"}, "") is True
assert infer_outcome_is_binary({"primary_outcome_type": "continuous"}, "") is False
assert infer_outcome_is_binary({}, "All-cause mortality at 12 months") is True
assert infer_outcome_is_binary({}, "Mean change in HbA1c from baseline") is False
assert infer_outcome_is_binary({}, "Investigator-assessed global impression") is None
# Binary hints are checked BEFORE continuous hints
assert infer_outcome_is_binary({}, "mean event rate") is True

# ── Combining ──
level, expl = compute_grade("High", "Low")
assert level == "High"
assert expl == ("No downgrade: overall risk of bias is Low and no serious "
                "indirectness, no serious imprecision detected.")

# Note the doubled full stop: the module explanation already ends in "." and the
# combiner appends its own. Cosmetic, and faithful to the reference platform.
level, expl = compute_grade("High", "Some concerns", indirectness_levels=1,
                            indirectness_explanation="Surrogate primary outcome.")
assert level == "Low"
assert expl == ("Downgraded 2 levels: 1 level for Some concerns in risk of bias "
                "+ 1 level for serious indirectness — Surrogate primary outcome..")

level, expl = compute_grade("High", "High", ["High", "High"], imprecision_levels=1,
                            imprecision_explanation="Wide CI crossing the MID.")
assert level == "Very low"
assert expl.startswith("Downgraded 3 levels: 2 levels for High risk of bias in 2 domains")

# Singular/plural agreement
assert compute_grade("High", "Unclear")[1] == (
    "Downgraded 1 level: 1 level for Unclear risk of bias in one or more "
    "QUADAS-2 domains (conservative).")

# Very-low floor: the explanation reports the COMPUTED downgrade, not the applied one
level, expl = compute_grade("Very low", "Critical")
assert level == "Very low"
assert expl == "Downgraded 2 levels: 2 levels for Critical risk of bias (ROBINS-I V2)."

# Maximum possible total is 8; still clamps to Very low
assert compute_grade("High", "Critical", indirectness_levels=3,
                     imprecision_levels=3)[0] == "Very low"

# Negative level counts are clamped, not subtracted — there is no rating-up path
assert compute_grade("Moderate", "Low", indirectness_levels=-2)[0] == "Moderate"

print("All GRADE downgrade-pipeline sanity checks passed.")
```

---

## 11. GRADE rating-up — reserved, not implemented

GRADE defines three factors that can raise certainty, applicable chiefly to observational evidence:

1. **Large magnitude of effect** — conventionally, a relative risk greater than 2 (or less than 0.5) with no plausible confounders rates up one level; greater than 5 (or less than 0.2) rates up two.
2. **Dose-response gradient** — a demonstrated gradient across exposure levels rates up one level.
3. **Plausible residual confounding that would reduce a demonstrated effect** — when all plausible unmeasured confounders would bias the estimate toward the null, yet an effect is still observed.

**None of these is implemented in v1.** The arithmetic in §6 only adds; both extra-domain inputs are clamped at zero. This section number is reserved so that adding rating-up later does not renumber the rest of the document.

The consequence is concentrated in the observational designs, which is exactly where GRADE intends rating-up to matter: a cohort study enters at "Low" (§2) and can only move downward. A large, dose-responsive, well-conducted cohort study receives the same "Low" ceiling as a marginal one. **Say this out loud in any output that presents a certainty rating for an observational design** — otherwise the rating reads as a considered judgement when it is a floor imposed by an unimplemented feature.

Two of the three factors already have their inputs available on the body-of-evidence path, which is worth knowing before anyone re-derives them: the pooling agent's hand-off carries `pooled_estimate`, `ci_lower`, and `ci_upper`, and the conventional large-effect thresholds are agreed as **≥ 2-fold → +1 level** and **≥ 5-fold → +2 levels**. So the large-effect factor is close to mechanical once this section is implemented. Dose-response needs exposure-stratified estimates that no current component produces, and plausible-residual-confounding is a judgement with no statistical input at all.

When implementing this later, three design decisions have to be made and should be recorded in the revision notes:

- **Order of operations.** GRADE applies rating-up *after* downgrading, and a body of evidence with serious limitations is normally not eligible to be rated up at all. A naive `total = downgrades - upgrades` is not equivalent.
- **Eligibility gate.** Rating-up conventionally requires no serious concerns in the downgrade domains. Encoding that gate in the tree keeps the deterministic logic honest.
- **The floor still binds.** Rating up from "Very low" past the initial certainty of the design needs an explicit policy.

---

## 12. Implementation notes for other platforms

### This is a single-study adaptation

Restating the front-matter callout because it is the thing most likely to be misused: canonical GRADE rates a body of evidence, and this rates one paper. Inconsistency and publication bias are not clean here — they are **unassessed**, because they are undefined for n = 1. A rating produced this way is a ceiling on what one study can support, not a guideline-grade rating. If your product surfaces it next to guideline-style certainty ratings, label it, and never let a "High" from this path stand in for a High from the body-of-evidence engine.

### Getting the paper to the model

The reference implementation assumes a vision-capable model that ingests PDF bytes. For text-only models, pre-extract with `pypdf` or equivalent and inline the text. Large papers may exceed the context window; a workable fallback ladder is (1) PDF as a document block, (2) extracted text in one call, (3) overlapping text chunks merged field-by-field with first-non-empty-wins. Whatever you do, both modules need the *results* section — an abstract-only extraction will produce confident imprecision judgements from no data.

### Do not reuse these modules for a pooled body of evidence

The indirectness and imprecision modules here take **one paper** and are tuned for it. Pointing them at a synthesis produces confident nonsense in both directions:

- **Imprecision** judges a single trial's CI and applies rule-of-thumb participant bands that are explicitly a single-trial stand-in for the Optimal Information Size. A pooled body has a pooled CI, a summed N, and a summed event count, and the body-of-evidence engine judges CI-versus-null and CI-versus-MID against those, with real OIS thresholds. Running the single-trial bands over one study of a ten-study body ignores nine of them.
- **Indirectness** judges one paper's PICO. A body can be indirect in ways no single study is — for instance when each study is individually direct but they collectively answer a different question than the review asks.
- **Risk of bias** here maps one paper's overall judgement. GRADE rates risk of bias across the body, weighted toward the studies carrying most of the information, and explicitly warns against averaging.

For any of that, use the GRADE agent in [`grade_certainty_shareable.md`](grade_certainty_shareable.md). It is not a superset of this module — it is a different implementation of the same domain names.

### Two LLM calls, not eight

Each module judges all four of its subdomains in one call. The subdomains share context (one target question, one effect estimate), so splitting them multiplies cost without improving consistency — the opposite of the per-domain RoB instruments, where domains are genuinely independent.

### Normalise RoB labels before mapping

The single highest-value defensive check in this document. ROBINS-I Domain 1 emits `"Low (except for concerns about uncontrolled confounding)"` / `"… benchmarking)"`; these must be collapsed to `"Low"` by the instrument's own aggregator. The mapper's catch-all branch will not error — it will quietly downgrade a level. If you cannot guarantee the upstream normalisation, add an explicit `rob_overall.startswith("Low")` guard.

### Answer normalisation is forgiving by design

Models emit `"Probably Direct"`, `"not-sufficiently-precise"`, and `"N/A"` regardless of prompt instructions. Both normalisers lowercase, substitute spaces and hyphens for underscores, then consult an alias table. Both default *unknown* values to the non-downgrading judgement and log a warning — the log line is the only signal that a response was garbled, so do not drop it.

### `n_a` is a value, not an absence

Continuous outcomes mark `event_count` as N/A, which normalises to `precise`. Do not delete the key, do not leave the literal token in the judgement dict, and do not count it as a concern. Getting this wrong downgrades every continuous-outcome paper for a subdomain that does not apply to it.

### PICO-shaped modules do not fit every design

Both modules assume Population / Intervention / Comparator / Outcome. Diagnostic accuracy is Patient / Index test / Reference standard / Target condition, so the reference platform skips both modules for that design and rates certainty on risk of bias alone (`skip_extras`). Running the PICO modules against a PIRT question yields fluent, confident, wrong output. If you build PIRT-aware variants, that flag is the hook.

### Optional inputs change the methodology, not just the wording

Two run-level inputs are optional, and both fallbacks materially change what is assessed:

- **No target PICO** → indirectness collapses to outcome surrogacy; P, I, and C are instructed to default near-direct.
- **No MID thresholds** → CI-width judgement reduces to crossing the line of no effect, with a lean toward `probably_precise`.

Neither fallback is wrong, but both make downgrades rarer. If you run inside a systematic review with a protocol, thread the protocol's PICO and MIDs through — otherwise you are systematically under-downgrading.

### Distinguish "clean" from "failed"

A crashed module and a genuinely clean one both yield zero levels. Persist the `error` key and check it before rendering "no serious indirectness detected" — otherwise a service outage looks like a quality finding.

### Out of scope (v1)

- **Rating up** (§11).
- **Inconsistency and publication bias** — out of scope by construction, not deferred. See [`grade_certainty_shareable.md`](grade_certainty_shareable.md).
- **Anything requiring more than one paper** — pooling, body-level risk-of-bias aggregation, absolute effects, Summary-of-Findings rows.
- **Indirect comparisons / network meta-analysis**; **baseline-risk indirectness** (needs external longitudinal data); the **ICEMAN** subgroup-credibility check.
- **Six-threshold Evidence-to-Decision framing** — v1 uses two thresholds (MID benefit, MID harm).
- **Machine-readable threshold-crossing arithmetic** — the model judges CI-vs-threshold qualitatively rather than the code computing it.
- **Formal Optimal Information Size / Review Information Size** and the **Walsh fragility index** — replaced by the rule-of-thumb bands in §5.2.
- **Very-low-baseline-risk auto-override** — a prompt guardrail only; the code does not detect or enforce it.
- **Random-effects double-counting** — a meta-analysis concern, not applicable to a single study.
- **Reviewer override of a subdomain judgement or a final rating** via UI — surface the rationales and let humans override in their write-up.
