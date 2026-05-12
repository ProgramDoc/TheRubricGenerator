# ROBINS-I V2 — Comprehensive Reference (cohort variants A/B + single-arm adaptation)

Self-contained reference for the **full ROBINS-I V2 risk-of-bias tool** as implemented in The Rubric Generator's Quality Appraisal AI. Covers all six domains, three Domain 1 variants (A: ITT, B: per-protocol, single_arm: uncontrolled), both preflight prompts (cohort + single-arm), the pure-Python decision trees, and the registry/dispatch wiring. Designed to be shared with methodologists without requiring access to the repo.

## About this document

**Two sources, two scopes:**

1. **ROBINS-I V2 (cohort scope) — official tool.** Source: ROBINS-I V2 development group (Sterne JA, Brandt Mathur M, Elbers R, Hróbjartsson A, McAleenan A, Reeves B, Shrier I, Tilling K, et al.). *The Risk Of Bias In Non-randomized Studies — of Interventions, Version 2 (ROBINS-I V2) assessment tool, 20 November 2025.* riskofbias.info. V2 is published explicitly for **follow-up (cohort) studies**. The 6-domain structure removes V1's "Bias due to deviations from intended intervention" domain; protocol-deviation issues are folded into Domain 1 Variant B (time-varying confounding).
2. **Single-arm adaptation — implementation-specific.** This implementation extends V2's variant pattern (A: ITT, B: per-protocol) with a **third variant** (`single_arm`) that reframes Domain 1 (confounding) and Domain 2 (classification) for the absence of a comparator. Domains 3–6 reuse the cohort signals + judges unchanged. **The adaptation is not endorsed by the ROBINS-I V2 development group.** Methodological priors: GRADE for non-randomized evidence, IHE Quality Appraisal Checklist for Case Series, Murad et al. (2018) methodological quality of case series, Simon two-stage design literature, FDA guidance on accelerated approval based on single-arm evidence.

**Other non-randomized designs** (Case-Control, Case-Crossover, Cross-Sectional Analytical, Non-Randomized Trial) still dispatch to this tool from the registry — V2 is applied as a best-available approximation; a methodologically pure assessment for those designs would require V1 ROBINS-I or a design-specific tool.

**Source file in the repo:** [`backend/rob_tools/robins_i.py`](../backend/rob_tools/robins_i.py).

---

## 1. Overview

**The 6 domains** (all variants):

| # | Name | Variant-aware? |
|---|------|----------------|
| D1 | Risk of bias due to confounding | **Yes** — Variant A (4 questions), Variant B (5 questions), single_arm (5 questions) |
| D2 | Risk of bias in classification of interventions | **Yes** — cohort (5 questions, used for A & B), single_arm (3 questions) |
| D3 | Risk of bias in selection of participants into the study (or analysis) | No — same 8 questions across all variants |
| D4 | Risk of bias due to missing data | No — same 11 questions across all variants |
| D5 | Risk of bias arising from measurement of the outcome | No — same 3 questions across all variants |
| D6 | Risk of bias in selection of the reported result | No — same 4 questions across all variants |

**Variant selection:**

- **Variant A** — analysis estimates ITT effect; only baseline confounding needs addressing. Chosen when the cohort preflight question C4 answers "No".
- **Variant B** — analysis estimates per-protocol effect; baseline + time-varying confounding both need addressing. Chosen when C4 answers "Yes".
- **Variant single_arm** — uncontrolled design (no comparator). Selected at the top of `run()` based on `classification["study_type"] ∈ {"Single-Arm Trial", "Dose-Escalation Study"}`, BEFORE preflight. C4 is still asked and recorded as metadata but does NOT swap variants.

**Pipeline per paper** (1 preflight + 6 domain LLM calls = 7 total, regardless of variant):

1. **Preflight** — answers B1 / B2 / B3 + C4 (or B1-SA / B2-SA / B3 + C4 for single-arm).
2. **Short-circuit** — if B2 = Y/PY (or B2-SA = Y/PY) or B3 = Y/PY, return **Critical** and skip per-domain assessment.
3. **Per-domain assessment** — one LLM call per domain. The LLM only answers the signaling questions; pure-Python decision trees map signals → judgement.
4. **Overall aggregation** — worst-domain across all 6 domains.

**Judgement scale (4-level):** `Low / Moderate / Serious / Critical`. V1's separate "No information" judgement is retired; NI is still a valid signal token but the algorithms route NI through the trees rather than producing a distinct judgement.

**Domain 1's "Low" gets variant-specific labels** (normalized to "Low" by the overall aggregator):

```python
LOW_D1    = "Low (except for concerns about uncontrolled confounding)"  # cohort
LOW_D1_SA = "Low (except for concerns about uncontrolled benchmarking)"  # single_arm
```

**Initial GRADE conventions** (used by the orchestrator's `compute_grade`):

- Cohort designs → `"Low"`
- Single-Arm Trial / Dose-Escalation Study → `"Very low"` (uncontrolled designs start at the lowest GRADE level; `compute_grade` clamps further downgrades at Very low)

---

## 2. Signal vocabulary + judgement scale

```python
SIGNAL_OPTIONS_ALL = ("Y", "PY", "PN", "N", "NI", "WN", "SN", "WY", "SY")
JUDGEMENTS         = ("Low", "Moderate", "Serious", "Critical")
```

| Token | Meaning |
|-------|---------|
| `Y` / `PY` | Yes / Probably yes |
| `N` / `PN` | No / Probably no |
| `NI`       | No information |
| `WN`       | Weak no — direction is no, magnitude uncertain |
| `SN`       | Strong no — magnitude clearly substantial |
| `WY`       | Weak yes — magnitude small |
| `SY`       | Strong yes — magnitude clearly substantial |

Different signaling questions accept different response-option subsets — the per-question allowed list is declared on each signal entry and surfaced in the prompt's "Response options:" line.

---

## 3. Helper functions + response-option subsets

**Per-question response-option subsets** (from `backend/rob_tools/robins_i.py`):

```python
_BASIC          = ("Y", "PY", "PN", "N", "NI")
_BASIC_NA       = ("NA", "Y", "PY", "PN", "N", "NI")
_WITH_WN_SN     = ("Y", "PY", "WN", "SN", "NI")
_NA_WITH_WN_SN  = ("NA", "Y", "PY", "WN", "SN", "NI")
_DIFFERENTIAL   = ("SY", "WY", "PN", "N", "NI")
_NA_DIFFERENTIAL = ("NA", "SY", "WY", "PN", "N", "NI")
```

**Helper predicates** used by every decision tree:

```python
def _yes(ans):        return ans in ("Y", "PY", "WY", "SY")
def _no(ans):         return ans in ("N", "PN", "WN", "SN")
def _strict_yes(ans): return ans in ("Y", "PY")
def _strict_no(ans):  return ans in ("N", "PN")
def _weak_no(ans):    return ans == "WN"
def _strong_no(ans):  return ans == "SN"
def _weak_yes(ans):   return ans == "WY"
def _strong_yes(ans): return ans == "SY"
def _no_info(ans):    return ans == "NI"
```

---

## 4. Preflight — Preliminary Considerations

### 4.1 System prompt (used for both cohort and single-arm preflights, and all domain prompts)

```text
You are an evidence-synthesis methodologist assessing risk of bias in a
non-randomized study of an intervention using the Cochrane ROBINS-I V2
tool (20 November 2025 cribsheet). Read the PDF carefully. Answer each
signaling question with one of the allowed tokens for that question —
Y (yes), PY (probably yes), PN (probably no), N (no), NI (no information),
and where indicated WN (weak no), SN (strong no), WY (weak yes),
SY (strong yes). Provide a 1-2 sentence rationale for each answer,
quoting the paper where possible. Return ONLY a valid JSON object — no
preamble, no markdown fences.
```

### 4.2 Cohort preflight prompt (variants A / B)

Built by `_build_preflight_prompt_cohort(study_type, primary_outcome, extracted_fields)`. Placeholders are substituted at call time.

````text
You are performing the **Preliminary Considerations** screen of ROBINS-I V2 on a non-randomized study.

Study type: {study_type}
Outcome being assessed: {primary_outcome}

Context (fields already extracted from the paper):
{ctx_json}

Answer four preliminary-consideration questions:

**B1. Did the authors make any attempt to control for confounding in the result being assessed?**
Options: Y / PY / PN / N
Elaboration: Confounding is a substantial problem in most non-randomized studies. Answer Y/PY if the analysis includes multivariable adjustment, matching, stratification, propensity-score methods, or inverse probability weighting.

**B2. (Only if N/PN to B1) Is there sufficient potential for confounding that an unadjusted result should not be considered further?**
Options: Y / PY / PN / N
Elaboration: If there is sufficient potential for confounding that an unadjusted result should not be considered, the result is at Critical risk of bias.

**B3. Was the method of measuring the outcome inappropriate?**
Options: Y / PY / PN / N
Elaboration: Identify methods of outcome measurement unsuitable for the outcome they evaluate. Answer Y/PY if (1) important outcome values fall outside levels detectable by the method; (2) the instrument has demonstrated poor reliability/validity; or (3) measurement differed substantially between intervention and comparator groups so that group differences are not interpretable. In most circumstances answer N/PN.

**C4. Did the analysis account for switches during follow-up between the intervention strategies being compared, or for other protocol deviations during follow-up?**
Options: No (the analysis is estimating the intention-to-treat effect — Variant A) / Yes (the analysis is estimating the per-protocol effect — Variant B)

Return JSON with exactly this shape:
{
  "B1": "Y|PY|PN|N",
  "B1_rationale": "1-2 sentences quoting the paper",
  "B2": "Y|PY|PN|N|NA",
  "B2_rationale": "1-2 sentences (or 'NA' if B1 was Y/PY)",
  "B3": "Y|PY|PN|N",
  "B3_rationale": "1-2 sentences quoting the paper",
  "C4": "No|Yes",
  "C4_rationale": "1-2 sentences explaining whether the analysis estimates ITT or per-protocol"
}
````

### 4.3 Single-arm preflight prompt (variant single_arm)

Built by `_build_preflight_prompt_single_arm(study_type, primary_outcome, extracted_fields)`. B1/B2 are replaced with benchmark-pre-specification questions; B3 reused verbatim; C4 reused but recorded as metadata only (does NOT swap variants).

````text
You are performing the **Preliminary Considerations** screen of ROBINS-I V2 (adapted for single-arm / uncontrolled designs) on an uncontrolled clinical study.

Study type: {study_type}
Outcome being assessed: {primary_outcome}

Context (fields already extracted from the paper):
{ctx_json}

This study has **no comparator group** — every participant received the intervention. Risk of bias is therefore not about confounding-by-indication (which requires a comparator) but about whether the implied benchmark for interpretation (historical control rate, performance criterion, or null hypothesis with a decision rule) was pre-specified and reasonable.

Answer four preliminary-consideration questions:

**B1-SA. Did the authors pre-specify a quantitative benchmark (historical control rate, performance criterion, or null hypothesis with a statistical decision rule) against which the single-arm result is being judged?**
Options: Y / PY / PN / N
Elaboration: Examples of pre-specified benchmarks include: a Simon two-stage design with a numeric response-rate threshold; an FDA accelerated-approval ORR threshold cited in the protocol; a published historical control rate that the trial was powered against. Answer Y/PY if such a benchmark is clearly identifiable in the protocol/SAP/methods. Answer N/PN if no benchmark is stated, or if the benchmark looks post-hoc.

**B2-SA. (Only if N/PN to B1-SA) Is the absence of any pre-specified benchmark severe enough that the single-arm proportion is uninterpretable for causal inference?**
Options: Y / PY / PN / N
Elaboration: Y/PY when the result is reported as a bare proportion with no reference point at all, such that any interpretation depends entirely on post-hoc comparisons. This short-circuits to Critical risk of bias.

**B3. Was the method of measuring the outcome inappropriate?**
Options: Y / PY / PN / N
Elaboration: Identify methods of outcome measurement unsuitable for the outcome they evaluate. Answer Y/PY if (1) important outcome values fall outside levels detectable by the method; (2) the instrument has demonstrated poor reliability/validity; or (3) measurement methods are not interpretable for the question. In most circumstances answer N/PN.

**C4. Did the analysis account for protocol deviations during follow-up (e.g. participants who discontinued the intervention or switched to another therapy)?**
Options: No (the analysis is an intention-to-treat / modified-ITT analysis of all enrolled) / Yes (the analysis is a per-protocol analysis restricted to those who completed treatment / responded)
Note: For single-arm studies this answer is recorded as metadata but does NOT swap risk-of-bias variants. It informs interpretation of D2-single-arm question 2S.3.

Return JSON with exactly this shape:
{
  "B1": "Y|PY|PN|N",
  "B1_rationale": "1-2 sentences quoting the paper (answer to B1-SA)",
  "B2": "Y|PY|PN|N|NA",
  "B2_rationale": "1-2 sentences (or 'NA' if B1 was Y/PY)",
  "B3": "Y|PY|PN|N",
  "B3_rationale": "1-2 sentences quoting the paper",
  "C4": "No|Yes",
  "C4_rationale": "1-2 sentences explaining whether the analysis is ITT-like or per-protocol-like"
}
````

### 4.4 Parsing + short-circuit logic

```python
SINGLE_ARM_STUDY_TYPES = frozenset({"Single-Arm Trial", "Dose-Escalation Study"})

def run_preflight(pdf_bytes, study_type, primary_outcome, extracted_fields):
    is_single_arm = study_type in SINGLE_ARM_STUDY_TYPES
    prompt = (_build_preflight_prompt_single_arm if is_single_arm
              else _build_preflight_prompt_cohort)(
        study_type, primary_outcome, extracted_fields)
    raw = _call_with_pdf(pdf_bytes, prompt, max_tokens=2048)
    # ... token coercion / NI defaulting omitted ...

    if is_single_arm:
        variant = "single_arm"
        b2_reason = ("B2-SA: Absence of any pre-specified benchmark is severe "
                     "enough that the single-arm proportion is uninterpretable "
                     "for causal inference.")
    else:
        variant = "A" if c4 == "No" else "B"
        b2_reason = ("B2: Sufficient potential for confounding that the "
                     "unadjusted result should not be considered further.")

    # B2/B2-SA Y/PY → Critical short-circuit
    if b2 in ("Y", "PY"):
        return {..., "screening_decision": "critical",
                "screening_reason": b2_reason, "variant": variant}
    # B3 Y/PY → Critical (comparator-agnostic, identical wording across variants)
    if b3 in ("Y", "PY"):
        return {..., "screening_decision": "critical",
                "screening_reason": "B3: The method of measuring the outcome is inappropriate.",
                "variant": variant}
    return {..., "screening_decision": "proceed", "variant": variant}
```

---

## 5. Per-domain prompt template

Every domain prompt is built by `build_domain_prompt(domain, variant, study_type, primary_outcome, extracted_fields, target_pico=None)`. For variant-aware domains (D1 and D2), the active signal list is the variant-specific subset; the prompt header indicates the variant.

```text
Assess **Domain {id} — {name} (Variant {variant})** for the study described in the attached PDF using the ROBINS-I V2 tool.

Study type: {study_type}
Outcome being assessed: {primary_outcome}

{optional target-PICO block}
Context (fields already extracted from the paper):
{ctx_json}

Signaling questions:
{questions_block}     # each question's text + elaboration + allowed response-option subset

Return a JSON object with exactly this shape:
{shape}              # {"<sig_id>": "<allowed tokens>", "<sig_id>_rationale": "...", ..., "direction_of_bias": "..."}

Notes on ROBINS-I V2:
- The judgement scale is **Low / Moderate / Serious / Critical** (4 levels). Code maps your signal answers to the judgement — answer the signaling questions only.
- Some questions allow **WN / SN** (weak / strong no) or **WY / SY** (weak / strong yes). Use the strong version only when the magnitude is clearly substantial; use the weak version when the direction is right but the magnitude is uncertain.
- Answer N (or PN) when the paper gives enough information to rule out the problem; NI only when the paper is silent.
- Rationales must be short (1-2 sentences) and quote the paper verbatim where possible.
```

The `direction_of_bias` field is one of: `NA | Favours intervention | Favours comparator | Towards null | Away from null | Unpredictable`.

---

## 6. Domain 1 — Bias due to confounding

**Relevant extracted fields:** `confounders_measured`, `adjustment_method`, `exposure_definition`, `comparator_group`, `comparator_historical_reference`, `immortal_time_bias`, `confounding_control`, `primary_endpoint_prespecified`, `consecutive_enrolment`.

Domain 1 has three variants. The active variant is chosen at the top of `run()` (single_arm) or by the cohort preflight's C4 answer (A vs B).

---

### 6.1 Variant A — ITT effect, baseline confounding only

**Signaling questions:**

**1A.1** — *Did the authors control for all the important confounding factors for which this was necessary?* `(Y / PY / WN / SN / NI)`
> Answer Y/PY if all important confounding factors identified in the preliminary consideration were appropriately controlled for (stratification, regression, matching, standardization, propensity scores, IPTW). Answer WN if most were controlled and uncontrolled confounding was probably not substantial. Answer SN if at least one important confounder should have been controlled but was not, and the failure is likely to have a material impact.

**1A.2** — *Were confounding factors that were controlled for (and for which control was necessary) measured validly and reliably by the variables available in this study?* `(NA / Y / PY / WN / SN / NI)`
> Adjustment helps only if confounders were measured well. Answer WN if measurement error was probably not substantial; SN if there was at least one important confounder measured poorly enough that the extent of measurement error in confounders was probably substantial.

**1A.3** — *Did the authors control for any post-intervention variables that could have been affected by the intervention?* `(NA / Y / PY / PN / N / NI)`
> Controlling for variables on the causal pathway between intervention and outcome (over-adjustment) biases the effect estimate. Classic example: adjusting for a biomarker that the intervention changes.

**1A.4** — *Did the use of negative controls, quantitative bias analysis, or other considerations suggest serious uncontrolled confounding?* `(Y / PY / PN / N)`
> If the study did not use negative controls and no other considerations suggest uncontrolled confounding, answer N. Answer Y/PY if negative controls indicate the result being assessed suffers from material bias due to confounding.

**Decision tree** (cribsheet p20):

```python
def domain1_variant_a_judge(signals: dict[str, str]) -> str:
    """D1 Variant A (ITT effect, baseline confounding only). Cribsheet p20."""
    q1 = signals.get("1A.1", "NI")
    q2 = signals.get("1A.2", "NI")
    q3 = signals.get("1A.3", "NI")
    q4 = signals.get("1A.4", "NI")

    # 1.1 SN or NI: nothing further to redeem the result
    if _strong_no(q1) or _no_info(q1):
        return "Critical" if _yes(q4) else "Serious"

    # 1.1 Y/PY: well-controlled
    if _strict_yes(q1):
        if _yes(q3):  # over-adjusted → causal-pathway bias
            if _yes(q4):
                return "Critical"
            if _strict_yes(q2):
                return "Serious"
            return "Critical"
        # 1.3 N/PN/NI: no over-adjustment
        if _strict_yes(q2) or _weak_no(q2):
            return "Serious" if _yes(q4) else LOW_D1
        return "Serious"  # SN/NI on 1.2

    # 1.1 WN: most-but-not-all controlled (floor is Moderate, not Low)
    if _weak_no(q1):
        if _yes(q3):
            if _yes(q4):
                return "Critical"
            if _strict_yes(q2):
                return "Serious"
            return "Critical"
        if _strict_yes(q2) or _weak_no(q2):
            return "Serious" if _yes(q4) else "Moderate"
        return "Serious"

    return "Serious"
```

---

### 6.2 Variant B — Per-protocol effect, baseline + time-varying confounding

**Signaling questions:**

**1B.1** — *Did the authors use an analysis method that was appropriate to control for time-varying as well as baseline confounding?* `(Y / PY / PN / N / NI)`
> Appropriate "g-methods" include inverse probability weighting based on baseline + time-varying confounding factors with adjustment for censoring weights. Standard regression including time-varying confounders may be problematic when those confounders are affected by prior intervention (treatment-confounder feedback).

**1B.2** — *Did the authors control for all the important baseline and time-varying confounding factors for which this was necessary?* `(NA / Y / PY / WN / SN / NI)`
> Same WN / SN semantics as Variant A 1A.1, applied to baseline + time-varying confounders.

**1B.3** — *Were confounding factors that were controlled for measured validly and reliably by the variables available in this study?* `(NA / Y / PY / WN / SN / NI)`
> Same measurement-validity question as Variant A 1A.2, applied to the broader factor set.

**1B.4** — *Did the authors control for time-varying factors or other variables measured after the start of intervention?* `(NA / Y / PY / PN / N / NI)`
> Asked when 1B.1 is N/PN/NI. Conditioning on time-varying factors measured after intervention start is likely to bias the result when those factors are on the causal pathway from intervention to outcome.

**1B.5** — *Did the use of negative controls, or other considerations, suggest serious uncontrolled confounding?* `(Y / PY / PN / N)`
> Same as Variant A 1A.4.

**Decision tree** (cribsheet p24):

```python
def domain1_variant_b_judge(signals: dict[str, str]) -> str:
    """D1 Variant B (per-protocol effect, baseline + time-varying). Cribsheet p24."""
    q1 = signals.get("1B.1", "NI")
    q2 = signals.get("1B.2", "NI")
    q3 = signals.get("1B.3", "NI")
    q4 = signals.get("1B.4", "NI")
    q5 = signals.get("1B.5", "NI")

    # 1.1 N/PN/NI: wrong analysis method
    if _strict_no(q1) or _no_info(q1):
        if _yes(q4):
            return "Critical"
        return "Critical" if _yes(q5) else "Serious"

    # 1.1 Y/PY: appropriate g-methods etc. used
    if _strict_yes(q1):
        if _strict_yes(q2):
            if _strict_yes(q3) or _weak_no(q3):
                return "Serious" if _yes(q5) else LOW_D1
            return "Serious"  # SN / NI on 1.3
        if _weak_no(q2):
            if _strict_yes(q3) or _weak_no(q3):
                return "Serious" if _yes(q5) else "Moderate"
            return "Serious"
        return "Critical" if _yes(q5) else "Serious"  # 1.2 SN/NI

    return "Serious"
```

---

### 6.3 Variant single_arm — Uncontrolled design (no comparator) [ADAPTED]

> **Adaptation note.** No comparator → classical confounding-by-indication is N/A. The domain instead assesses (1) **benchmark adequacy** (1S.1 + 1S.2) and (2) **prognostic-mix comparability** (1S.3 + 1S.4). 1S.5 (negative / falsification controls) plays the same Critical-elevating role as 1A.4 / 1B.5 in the cohort variants. This adaptation is not endorsed by the V2 development group.

**Signaling questions:**

**1S.1** — *Was the implied benchmark (historical control rate, pre-specified performance criterion, or null hypothesis with a quantitative decision rule) pre-specified before data collection?* `(Y / PY / PN / N)`
> Single-arm trials have no internal comparator. They are interpreted against an implicit benchmark — usually a historical-control response rate, a regulatory performance criterion (e.g. ORR > 30% to support accelerated approval), or a null hypothesis with a pre-specified statistical decision rule (e.g. Simon's two-stage design). Answer Y/PY if a numeric benchmark + decision rule was clearly stated in the protocol / SAP / methods, BEFORE the data were collected. Answer N/PN if no benchmark is identifiable, or if the benchmark looks chosen post-hoc to match the observed result.

**1S.2** — *Is the implied benchmark reasonable given current standard of care and the patient population being studied?* `(Y / PY / PN / N / NI)`
> A pre-specified benchmark is only useful if it reflects a clinically meaningful threshold for this population. Answer Y/PY if the benchmark is consistent with contemporary published control-arm rates in comparable patients (similar disease stage, prior therapy, biomarker status). Answer N/PN if the benchmark is implausibly low (inflates apparent benefit) or implausibly high (forces a near-impossible bar). NI if no contemporary comparable estimate exists.

**1S.3** — *Is the cohort's measured baseline prognostic profile (stage, prior lines, ECOG / performance status, biomarker status, key comorbidities) comparable to that of the benchmark population?* `(NA / Y / PY / WN / SN / NI)`
> The single-arm proportion is biased upward if the enrolled cohort is more prognostically favourable than the benchmark population (e.g. younger, less heavily pre-treated, biomarker-enriched). Answer Y/PY when measured baseline prognostic factors are comparable. WN when most-but-not-all prognostic factors look comparable. SN when at least one important prognostic factor is materially more favourable in this cohort. NA only when no benchmark was identified at 1S.1.

**1S.4** — *Did the authors address residual prognostic-mix differences quantitatively (sensitivity analyses, propensity-score adjustment to external controls, prognostic-score stratification, or similar)?* `(NA / Y / PY / PN / N / NI)`
> Even when 1S.3 raises concerns, quantitative external-control adjustment can rescue interpretability. Examples include propensity-score weighting against an external real-world cohort, MAIC (matching-adjusted indirect comparison), prognostic-score stratification, or pre-specified sensitivity analyses showing the conclusion is robust to plausible prognostic differences. Answer Y/PY when such methods were used and reported. N/PN when not addressed.

**1S.5** — *Do negative / falsification controls, external-validity considerations, or other quantitative bias analyses suggest serious uncontrolled selection-prognostic bias?* `(Y / PY / PN / N)`
> Analogous to 1A.4 / 1B.5 in the cohort variants. Answer Y/PY if a falsification analysis (e.g. testing the intervention against an outcome it shouldn't affect) suggested residual bias, or if external-validity checks revealed serious cohort-vs-benchmark mismatch. Answer N if no falsification analysis was performed and no other consideration suggests substantial uncontrolled bias — this is the typical answer.

**Decision tree:**

```python
def domain1_variant_single_arm_judge(signals: dict[str, str]) -> str:
    """D1 Variant single_arm (uncontrolled / single-arm design — no comparator).

    Returns LOW_D1_SA, Moderate, Serious, or Critical.
    """
    q1 = signals.get("1S.1", "NI")
    q2 = signals.get("1S.2", "NI")
    q3 = signals.get("1S.3", "NI")
    q4 = signals.get("1S.4", "NI")
    q5 = signals.get("1S.5", "NI")

    # 1S.5 dominates: falsification-control hit → Critical regardless
    if _yes(q5):
        return "Critical"

    # 1S.1 N/PN: no pre-specified benchmark
    if _strict_no(q1):
        if _strict_no(q4):
            return "Critical"   # no adjustment either
        return "Serious"

    if _no_info(q1):
        return "Serious"

    # 1S.1 Y/PY: benchmark pre-specified
    if _strict_yes(q1):
        if _strict_yes(q3):
            # 1S.2 decides Low vs Moderate
            if _strict_yes(q2):
                return LOW_D1_SA
            return "Moderate"
        if _weak_no(q3):
            return "Moderate"   # most-but-not-all prognostic match
        if _strong_no(q3) or _no_info(q3):
            # Substantial mismatch — 1S.4 (quantitative adjustment) can rescue
            if _strict_yes(q4):
                return "Moderate"
            return "Serious"

    return "Serious"
```

**Canonical input → output pairs (single-arm D1):**

| Signal answers | Result |
|---|---|
| `1S.1=Y, 1S.2=Y, 1S.3=Y, 1S.4=NA, 1S.5=N` | **Low** (LOW_D1_SA) — clean benchmark + clean prognostic match |
| `1S.1=Y, 1S.2=N, 1S.3=Y, 1S.5=N` | **Moderate** — benchmark unreasonable |
| `1S.1=Y, 1S.2=Y, 1S.3=WN, 1S.5=N` | **Moderate** — partial prognostic match |
| `1S.1=Y, 1S.2=Y, 1S.3=SN, 1S.4=Y, 1S.5=N` | **Moderate** — mismatch rescued by adjustment |
| `1S.1=Y, 1S.2=Y, 1S.3=SN, 1S.4=N, 1S.5=N` | **Serious** — mismatch not addressed |
| `1S.1=N, 1S.4=Y, 1S.5=N` | **Serious** — no benchmark, adjustment present |
| `1S.1=N, 1S.4=N, 1S.5=N` | **Critical** — no benchmark, no adjustment |
| `1S.5=Y` (any upstream) | **Critical** — falsification controls hit |

---

## 7. Domain 2 — Bias in classification of interventions

Domain 2 has two variants: a single 5-question cohort tree (used for both Variant A and Variant B), and a 3-question single-arm tree.

---

### 7.1 Cohort variant (used for Variants A and B)

**Relevant extracted fields:** `exposure_definition`, `exposure_measurement`, `exposure_ascertainment`, `intervention_classification`.

**Signaling questions:**

**2.1** — *Were the intervention strategies distinguishable at the time when follow-up would have started in the target trial?* `(Y / PY / PN / N / NI)`
> Some strategies (e.g. "surgery within 6 months of diagnosis" vs "delay surgery until clinical progression") cannot be distinguished at follow-up start, creating an "immortal time" period during which the outcome cannot occur for some groups.

**2.2** — *Did all or nearly all outcome events occur after the intervention and comparator strategies could be distinguished?* `(NA / Y / PY / PN / N / NI)`
> Asked only if 2.1 was N/PN/NI. If the indistinguishable period is short relative to total follow-up, misclassification bias may be small.

**2.3** — *Did the analysis avoid problems arising from intervention strategies that are not distinguishable at the start of follow-up?* `(NA / SY / WY / PN / N / NI)`
> SY (fully) if clone-censor-weighting, g-formula, or a landmark analysis was used. WY (partially) if appropriate but unlikely to have fully adjusted.

**2.4** — *Was classification of intervention status influenced by knowledge of the outcome or risk of the outcome?* `(SY / WY / PN / N / NI)`
> Differential misclassification. SY = yes, substantial impact; WY = yes, impact not substantial.

**2.5** — *Were further classification errors (not influenced by knowledge of the outcome or risk of the outcome) likely?* `(Y / PY / PN / N / NI)`
> Non-differential misclassification — receipt of intervention not recorded for some participants. Usually biases towards the null.

**Decision tree** (cribsheet p28 — linear-tier model):

```python
def domain2_judge(signals: dict[str, str]) -> str:
    """D2 Bias in classification of interventions. Cribsheet p28."""
    q1, q2, q3, q4, q5 = (signals.get(k, "NI") for k in ("2.1","2.2","2.3","2.4","2.5"))

    # Tier from upstream (2.1 → 2.2 → 2.3 cascade)
    if _yes(q1) or _yes(q2):
        tier = 0  # best — top matrix
    elif _strong_yes(q3) or _weak_yes(q3) or _no_info(q3):
        tier = 1  # middle matrix
    else:
        tier = 2  # worst — 2.3 N/PN: analysis did not address

    # 2.4 differential misclassification bump
    bump4 = 0 if _strict_no(q4) else (1 if _weak_yes(q4) or _no_info(q4) else 2)
    # 2.5 non-differential misclassification bump
    bump5 = 0 if _strict_no(q5) else 1

    # Tier 2 + 2.4 SY/WY/NI → Critical directly
    if tier == 2 and (_yes(q4) or _no_info(q4)):
        return "Critical"

    idx = min(tier + bump4 + bump5, 3)
    return JUDGEMENTS[idx]
```

---

### 7.2 Variant single_arm — Uncontrolled design [ADAPTED]

> **Adaptation note.** Only one intervention → classical differential misclassification by group is meaningless. What remains: was the intervention well-defined, were modifications recorded, and was the analyzed cohort defined by intended treatment (ITT-like, low risk) or received treatment (per-protocol — risks selection toward responders)? This adaptation is not endorsed by the V2 development group.

**Relevant extracted fields:** `exposure_definition`, `intervention_classification`, `escalation_scheme`, `dose_levels`, `expansion_cohort`.

**Signaling questions:**

**2S.1** — *Was the intervention well-defined (dose, schedule, duration, dose-modifications protocol) at the start of follow-up?* `(Y / PY / PN / N / NI)`
> In a single-arm trial there is no comparator misclassification, but the single intervention must be specified precisely enough that the reported result corresponds to a reproducible regimen. Answer Y/PY when dose, schedule, duration, and dose-modification rules (reductions, holds, criteria for discontinuation) are fully reported. Answer N/PN when the intervention is described only at high level (e.g. "standard chemotherapy").

**2S.2** — *Were dose reductions, holds, and discontinuations recorded and reported?* `(Y / PY / WN / SN / NI)`
> Recording of treatment delivery is essential for interpreting the single-arm result. WN if most exposure modifications were recorded; SN if material exposure detail is missing such that the analyzed "intervention" is effectively undefined.

**2S.3** — *Was the analyzed cohort defined by intended treatment (everyone enrolled, ITT-like) or by received treatment (only those completing ≥X cycles / responding to treatment)?* `(NA / SY / WY / PN / N / NI)`
> Defining the analyzed cohort by *received* treatment (per-protocol completers, "evaluable population") selects for patients who tolerated the intervention well enough to keep receiving it — a strong selection toward responders that inflates the single-arm proportion. Answer SY (strong yes) when the primary analysis is explicitly restricted to completers or responders. WY (weak yes) when the analyzed cohort excludes some enrolled patients for treatment-related reasons but not dominantly. Answer N/PN when all enrolled (or modified ITT) are analyzed.

**Decision tree:**

```python
def domain2_variant_single_arm_judge(signals: dict[str, str]) -> str:
    """D2 Variant single_arm — degenerate classification (only one intervention)."""
    q1 = signals.get("2S.1", "NI")
    q2 = signals.get("2S.2", "NI")
    q3 = signals.get("2S.3", "NI")

    # 2S.3 dominates: SY = selection-on-completers substantial → Critical
    if _strong_yes(q3):
        return "Critical"
    if _weak_yes(q3) or _no_info(q3):
        return "Serious"  # some / unclear filtering

    # q3 in (PN, N): cohort defined by intended treatment → low concern
    if _strict_yes(q1):
        if _strict_yes(q2):
            return "Low"
        if _weak_no(q2):
            return "Moderate"
        if _strong_no(q2):
            return "Serious"
        return "Moderate"  # NI on 2S.2

    if _strict_no(q1):
        return "Serious"
    return "Moderate"  # NI on 2S.1
```

**Canonical input → output pairs (single-arm D2):**

| Signal answers | Result |
|---|---|
| `2S.1=Y, 2S.2=Y, 2S.3=N` | **Low** — well-defined intervention, recorded modifications, ITT-like cohort |
| `2S.1=Y, 2S.2=WN, 2S.3=N` | **Moderate** — most modifications recorded |
| `2S.1=Y, 2S.2=SN, 2S.3=N` | **Serious** — material exposure detail missing |
| `2S.1=N, 2S.3=N` | **Serious** — intervention not well-defined |
| `2S.3=WY` | **Serious** — some completer-filtering |
| `2S.3=SY` | **Critical** — analysis restricted to responders / completers |

---

## 8. Domain 3 — Bias in selection of participants into the study (or analysis)

Single 8-question tree used unchanged across all three variants. Cribsheet p32. Three sub-sections — **A** (prevalent-user / immortal time), **B** (other selection bias), **C** (severity / correction).

**Relevant extracted fields:** `case_source`, `control_selection`, `sampling_method`, `loss_to_follow_up`, `immortal_time_bias`.

**Signaling questions:**

**A. Prevalent-user bias and immortal time**

**3.1** — *Did follow-up in the analysis begin at the start of the intervention strategies being compared?* `(Y / PY / WN / SN / NI)`
> Y/PY if all outcome events and follow-up time after intervention start were included. WN if not substantial; SN if leading to substantial bias.

**3.2** — *Were outcome events during a period of follow-up after the start of the interventions excluded from the analysis?* `(Y / PY / PN / N / NI)`
> Only asked if 3.1 was Y/PY. Such exclusion creates immortal time.

**B. Other selection bias**

**3.3** — *Was selection of participants into the study (or analysis) based on participant characteristics observed after the start of intervention, additional to the situations addressed in 3.1 and 3.2?* `(Y / PY / PN / N / NI)`
> N/PN if selection was based only on pre-intervention characteristics — that's baseline confounding (Domain 1), not selection bias.

**3.4** — *Were the post-intervention variables that influenced selection likely to be associated with intervention?* `(NA / Y / PY / PN / N / NI)`
> Only asked if 3.3 was Y/PY. Selection bias requires selection related to BOTH intervention and outcome.

**3.5** — *Were the post-intervention variables that influenced selection likely to be influenced by the outcome or a cause of the outcome?* `(NA / Y / PY / PN / N / NI)`
> Only asked if 3.4 was Y/PY. Collider-style selection bias.

**C. Analysis / sensitivity / severity**

**3.6** — *Is it likely that the analysis corrected for all of the potential selection biases identified above?* `(NA / Y / PY / PN / N / NI)`
> Only asked if A or B raised concerns. IPW can create a pseudo-population without the selection bias if assumptions are justified.

**3.7** — *Did sensitivity analyses demonstrate that the likely impact of the potential selection biases was minimal?* `(NA / Y / PY / PN / N / NI)`
> Only asked if 3.6 was N/PN/NI.

**3.8** — *Were potential selection biases identified above sufficiently severe that the result should not be included in a quantitative synthesis?* `(NA / Y / PY / PN / N / NI)`
> Distinguishes Serious from Critical. Answer N/PN/NI unless there is clear evidence the biases were severe.

**Decision tree** (cribsheet p32):

```python
def domain3_judge(signals: dict[str, str]) -> str:
    """D3 Bias in selection of participants. Cribsheet p32."""
    q1, q2, q3, q4, q5, q6, q7, q8 = (
        signals.get(k, "NI")
        for k in ("3.1","3.2","3.3","3.4","3.5","3.6","3.7","3.8")
    )

    # Subsection A: prevalent-user / immortal time
    if _strict_yes(q1):
        a_judgement = "Low" if _strict_no(q2) or _no_info(q2) else "Moderate"
    elif _weak_no(q1) or _no_info(q1):
        a_judgement = "Moderate"
    elif _strong_no(q1):
        a_judgement = "Serious"
    else:
        a_judgement = "Moderate"

    # Subsection B: other selection bias
    if _strict_no(q3):
        b_judgement = "Low"
    elif _yes(q3):
        if _strict_no(q4) or _no_info(q4):
            b_judgement = "Low"
        elif _yes(q4):
            b_judgement = "Serious" if _yes(q5) else "Moderate"
        else:
            b_judgement = "Moderate"
    else:
        b_judgement = "Moderate"  # NI to 3.3

    rank = {"Low": 0, "Moderate": 1, "Serious": 2, "Critical": 3}
    worst = max(rank[a_judgement], rank[b_judgement])
    if worst == 0: return "Low"
    if worst == 1: return "Moderate"

    # Subsection C only applies when A or B is Serious
    if _yes(q6): return "Moderate"      # analysis corrected
    if _yes(q7): return "Moderate"      # sensitivity minimal impact
    if _yes(q8): return "Critical"      # severe → exclude from synthesis
    return "Serious"
```

---

## 9. Domain 4 — Bias due to missing data

Single 11-question tree used unchanged across all three variants. Cribsheet p38. Branches by how the analysis handled missingness: **complete-case**, **imputation**, or **alternative method**.

**Relevant extracted fields:** `loss_to_follow_up`, `missing_data_handling`, `attrition_rate`.

**Signaling questions:**

**4.1** — *Were complete data on intervention status available for all, or nearly all, participants?* `(Y / PY / PN / N / NI)`
**4.2** — *Were complete data on the outcome available for all, or nearly all, participants?* `(Y / PY / PN / N / NI)`
**4.3** — *Were complete data on important confounding variables available for all, or nearly all, participants?* `(Y / PY / PN / N / NI)`
> "Nearly all" = number excluded so small it could not have made an important difference. For continuous outcomes, 95% (or 90%) is often sufficient. For dichotomous outcomes, the threshold depends on event rate.

**4.4** — *Is the result based on a complete case analysis?* `(NA / Y / PY / PN / N / NI)`
**4.5** — *Was exclusion from the analysis because of missing data likely to be related to the true value of the outcome?* `(NA / Y / PY / PN / N / NI)`
**4.6** — *Is the relationship between the outcome and missingness likely to be explained by the variables in the analysis model?* `(NA / Y / PY / WN / SN / NI)`
> If all variables that plausibly explain the outcome-missingness relationship are included in the complete-case analysis, bias is low. WN if not substantial; SN if bias is likely substantial.

**4.7** — *Was the analysis based on imputing missing values?* `(NA / Y / PY / PN / NI)`
**4.8** — *Is it reasonable to assume data were MAR or MCAR?* `(NA / Y / PY / PN / N / NI)`
**4.9** — *Was imputation performed appropriately?* `(NA / Y / PY / WN / SN / NI)`
> WN/SN if simple methods (LOCF, mean imputation) were used; Y/PY if multiple imputation included all predictors of missingness and all variables in the main analysis model.

**4.10** — *Was an appropriate alternative method used to correct for bias due to missing data?* `(NA / Y / PY / WN / SN / NI)`
> Asked when the analysis was neither complete case nor imputation. Examples: IPW, FIML.

**4.11** — *Is there evidence that the result was not biased by missing data?* `(NA / Y / PY / PN / N)`
> Evidence from (1) analysis methods that would not be biased under plausible missingness assumptions, or (2) sensitivity analyses showing results change little.

**Decision tree** (cribsheet p38):

```python
def domain4_judge(signals: dict[str, str]) -> str:
    """D4 Bias due to missing data. Cribsheet p38."""
    q1, q2, q3 = (signals.get(k, "NI") for k in ("4.1","4.2","4.3"))
    q4, q5, q6 = (signals.get(k, "NI") for k in ("4.4","4.5","4.6"))
    q7, q8, q9, q10, q11 = (signals.get(k, "NI") for k in ("4.7","4.8","4.9","4.10","4.11"))

    # Best case: complete data on all three variables → Low
    if _strict_yes(q1) and _strict_yes(q2) and _strict_yes(q3):
        return "Low"

    # Complete case analysis path
    if _strict_yes(q4) or _no_info(q4):
        if _strict_no(q5):
            return "Low"
        if _strict_yes(q6):
            return "Moderate" if _strict_yes(q11) else "Serious"
        if _weak_no(q6) or _no_info(q6):
            return "Moderate" if _strict_yes(q11) else "Serious"
        return "Critical" if _strict_no(q11) else "Serious"  # SN on 4.6

    # Imputation path
    if _strict_yes(q7):
        if _strict_yes(q8):
            if _strict_yes(q9):
                return "Low"
            if _weak_no(q9) or _no_info(q9):
                return "Moderate" if _strict_yes(q11) else "Serious"
            return "Critical" if _strict_no(q11) else "Serious"  # SN on 4.9
        return "Critical" if _strict_no(q11) else "Serious"  # 4.8 N/PN/NI

    # Alternative method path
    if _strict_yes(q10):
        return "Low"
    if _weak_no(q10) or _no_info(q10):
        return "Moderate" if _strict_yes(q11) else "Serious"
    return "Critical" if _strict_no(q11) else "Serious"  # SN on 4.10
```

---

## 10. Domain 5 — Bias arising from measurement of the outcome

Single 3-question tree used unchanged across all three variants. Cribsheet p41.

**Relevant extracted fields:** `outcome_ascertainment`, `outcome_definition`.

**Signaling questions:**

**5.1** — *Could measurement or ascertainment of the outcome have differed between intervention groups?* `(Y / PY / PN / N / NI)`
> Y/PY → Serious directly. Differences arise through "diagnostic detection bias" or extra visits for intervention participants.

**5.2** — *Were outcome assessors aware of the intervention received by study participants?* `(Y / PY / PN / N / NI)`
> N if blinded, or if participants self-report and were themselves blinded. In observational studies, usually Y when participants report outcomes themselves.

**5.3** — *Could assessment of the outcome have been influenced by knowledge of the intervention received?* `(NA / SY / WY / PN / N / NI)`
> Only asked if 5.2 was Y/PY/NI. SY = yes, to a large extent (e.g. patient-reported symptoms in homeopathy studies). WY = yes, to a small extent.

**Decision tree** (cribsheet p41):

```python
def domain5_judge(signals: dict[str, str]) -> str:
    """D5 Bias arising from measurement of the outcome. Cribsheet p41."""
    q1, q2, q3 = (signals.get(k, "NI") for k in ("5.1","5.2","5.3"))

    if _yes(q1):
        return "Serious"  # differential measurement

    if _strict_no(q1):
        if _strict_no(q2):
            return "Low"
        if _strong_yes(q3):
            return "Serious"
        if _weak_yes(q3) or _no_info(q3):
            return "Moderate"
        return "Low"

    # 5.1 NI
    if _strict_no(q2):
        return "Moderate"
    if _strong_yes(q3):
        return "Serious"
    return "Moderate"
```

---

## 11. Domain 6 — Bias in selection of the reported result

Single 4-question tree used unchanged across all three variants. Cribsheet p47.

**Relevant extracted fields:** `outcome_definition`, `statistical_analysis`.

**Signaling questions:**

**6.1** — *Was the result reported in accordance with an available, pre-determined analysis plan?* `(Y / PY / PN / N / NI)`
> Analysis plans are rarely publicly available for non-randomized studies, so most papers will not be assessed as Low on the basis of 6.1 alone.

**6.2** — *Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple outcome measurements within the outcome domain?* `(Y / PY / PN / N / NI)`
**6.3** — *...from multiple analyses of the data?* `(Y / PY / PN / N / NI)`
**6.4** — *...from multiple subgroups?* `(Y / PY / PN / N / NI)`

**Decision tree** (cribsheet p47):

```python
def domain6_judge(signals: dict[str, str]) -> str:
    """D6 Bias in selection of the reported result. Cribsheet p47."""
    q1, q2, q3, q4 = (signals.get(k, "NI") for k in ("6.1","6.2","6.3","6.4"))

    if _strict_yes(q1):
        return "Low"

    yes_count = sum(1 for q in (q2, q3, q4) if _yes(q))
    ni_count = sum(1 for q in (q2, q3, q4) if _no_info(q))

    if yes_count >= 2:
        return "Critical"
    if yes_count == 1:
        return "Serious"
    if ni_count == 3:
        return "Serious"
    if ni_count >= 1:
        return "Moderate"
    return "Low"
```

---

## 12. Overall aggregation

Worst-domain across all 6 domains. Both `LOW_D1` (cohort) and `LOW_D1_SA` (single-arm) labels are normalized to `Low` for aggregation.

```python
def robins_i_overall(domain_judgements: list[str]) -> str:
    """Overall judgement — worst-domain aggregation per cribsheet p48.

    Severity (worst → best): Critical > Serious > Moderate > Low.
    """
    rank = {
        LOW_D1: 0,
        LOW_D1_SA: 0,
        "Low": 0,
        "Moderate": 1,
        "Serious": 2,
        "Critical": 3,
    }
    worst = max((rank.get(j, 1) for j in domain_judgements), default=0)
    if worst == 0:
        return "Low"
    return JUDGEMENTS[worst]
```

A preflight short-circuit (`B2`/`B2-SA = Y/PY` or `B3 = Y/PY`) returns `Critical` without running any per-domain assessment.

**Per-variant judge tables** (D3–D6 are aliased into the single-arm table):

```python
DOMAIN_JUDGES_VARIANT_A = {
    1: domain1_variant_a_judge,
    2: domain2_judge,
    3: domain3_judge, 4: domain4_judge,
    5: domain5_judge, 6: domain6_judge,
}
DOMAIN_JUDGES_VARIANT_B = {
    1: domain1_variant_b_judge,
    2: domain2_judge,
    3: domain3_judge, 4: domain4_judge,
    5: domain5_judge, 6: domain6_judge,
}
DOMAIN_JUDGES_VARIANT_SINGLE_ARM = {
    1: domain1_variant_single_arm_judge,
    2: domain2_variant_single_arm_judge,
    3: domain3_judge, 4: domain4_judge,
    5: domain5_judge, 6: domain6_judge,
}
```

---

## 13. Registry + dispatch

In `backend/quality_appraisal.py`:

```python
STUDY_TYPE_REGISTRY: dict[str, dict[str, str]] = {
    "Randomized Controlled Trial":  {"rob_tool": "rob2",     "reporting_guideline": "consort2025", "initial_grade": "High"},
    "Cohort Study":                 {"rob_tool": "robins_i", "reporting_guideline": "strobe",      "initial_grade": "Low"},
    "Case-Control":                 {"rob_tool": "robins_i", "reporting_guideline": "strobe",      "initial_grade": "Low"},
    "Non-Randomized Trial":         {"rob_tool": "robins_i", "reporting_guideline": "strobe",      "initial_grade": "Low"},
    "Cross-Sectional (Analytical)": {"rob_tool": "robins_i", "reporting_guideline": "strobe",      "initial_grade": "Low"},
    "Case-Crossover":               {"rob_tool": "robins_i", "reporting_guideline": "strobe",      "initial_grade": "Low"},
    # Single-arm / uncontrolled designs
    "Single-Arm Trial":             {"rob_tool": "robins_i", "reporting_guideline": "strobe",      "initial_grade": "Very low"},
    "Dose-Escalation Study":        {"rob_tool": "robins_i", "reporting_guideline": "strobe",      "initial_grade": "Very low"},
}
```

All non-randomized designs map to `rob_tool="robins_i"`. The variant selection happens inside `robins_i.run()`:

- `study_type ∈ SINGLE_ARM_STUDY_TYPES` → variant `"single_arm"` (pinned BEFORE preflight)
- Otherwise → variant chosen by preflight C4 answer (`"A"` if No, `"B"` if Yes)

STROBE 2007 is reused as the reporting guideline for all non-randomized designs (including single-arm) pending design-specific tooling.

---

## 14. Caveats and limitations

**V2 (cohort scope) — official tool:**

1. **Published for follow-up (cohort) studies only.** Case-Control / Case-Crossover / Cross-Sectional / Non-Randomized Trial are dispatched to V2 as a best-available approximation; a methodologically pure assessment would require V1 ROBINS-I or a design-specific tool.
2. **No "No information" overall judgement.** V1's separate "No information" judgement was retired. NI signal tokens route through the decision trees rather than producing a distinct judgement.
3. **User-override allowed.** The cribsheet (p48) permits overriding the algorithm default upward when multiple Serious domains compound. The orchestrator returns the algorithm default; manual override is an operator decision.

**Single-arm adaptation — implementation-specific:**

4. **Not endorsed by the V2 development group.** The variant extends V2's pattern with a third option. The 1S.*/2S.* signaling questions and decision trees are this implementation's interpretation of standard single-arm bias considerations.
5. **Dose-Escalation reuses single-arm wholesale.** MTD-declaration adequacy, DLT-definition rigor, RP2D justification, and expansion-cohort selection are not modeled. A Phase 1-specific adaptation is a follow-up.
6. **No single-arm reporting-guideline module.** STROBE is reused. A purpose-built `phase2_singlearm` checklist (or a modified CONSORT-for-single-arm) may be added later.
7. **Decision-tree thresholds need methodological review.** The trees pass canonical test cases (8 for D1S, 6 for D2S), but a methodologist's review against (e.g.) the Murad 2018 case-series tool or the IHE Quality Appraisal Checklist would be valuable before regulatory-grade use.

**Both:**

8. **CSV/XLSX exports are additive.** New 1S.* / 2S.* columns join the existing union; cohort runs show empty cells for those columns, and single-arm runs show empty cells for the cohort 1A.* / 1B.* / cohort-2.* columns.
9. **`compute_grade` clamps at "Very low".** Initial GRADE for single-arm starts at "Very low"; further downgrades leave the level unchanged but are still reported in the explanation.
10. **No new credit cost from single-arm.** Same 1 preflight + 6 domains = 7 LLM calls per paper as cohort V2.

---

## 15. How to reproduce locally

The full implementation is in [`backend/rob_tools/robins_i.py`](../backend/rob_tools/robins_i.py). To run the canonical decision-tree tests:

```bash
pip install -r requirements.txt   # Python 3.12+
# Cohort variants A + B
pytest tests/test_quality_appraisal.py::TestRobinsIDomain1VariantA -v
pytest tests/test_quality_appraisal.py::TestRobinsIDomain1VariantB -v
pytest tests/test_quality_appraisal.py::TestRobinsIDomain2 -v
pytest tests/test_quality_appraisal.py::TestRobinsIDomain3 -v
pytest tests/test_quality_appraisal.py::TestRobinsIDomain4 -v
pytest tests/test_quality_appraisal.py::TestRobinsIDomain5 -v
pytest tests/test_quality_appraisal.py::TestRobinsIDomain6 -v
# Single-arm variant
pytest tests/test_quality_appraisal.py::TestRobinsIDomain1VariantSingleArm -v
pytest tests/test_quality_appraisal.py::TestRobinsIDomain2VariantSingleArm -v
# Preflight (cohort + single-arm)
pytest tests/test_quality_appraisal.py::TestRobinsIPreflight -v
# Overall aggregation
pytest tests/test_quality_appraisal.py::TestRobinsIOverall -v
```

To exercise the LLM end-to-end (requires `ANTHROPIC_API_KEY`):

```bash
uvicorn main:app --reload --port 8000
# Navigate to /quality-appraisal in a browser, upload a PDF, classify it
# (e.g. as "Cohort Study" or "Single-Arm Trial"), and run a Quality Appraisal.
# The result's rob_domains.preflight.variant will be "A" / "B" / "single_arm".
```

The full prompt catalog (all prompts + decision-tree source for every variant) is exposed via the developer view at `GET /api/quality-appraisal/prompts` once signed in.
