# Quality Appraisal — RoB 2 + ROBINS-I Reference

Reference for the LLM prompts and Python decision-tree rules used by The Rubric Generator's Quality Appraisal AI. Transcribed verbatim from source on 2026-04-28.

**Sources:**
- **RoB 2** — Sterne JAC, Savović J, Page MJ, Higgins JPT, et al. *RoB 2: a revised tool for assessing risk of bias in randomised trials.* BMJ 2019; 366:l4898. Signaling-question text and elaborations transcribed from `20190814_RoB_2.0_cribsheet_parallel_trial.pdf`.
- **ROBINS-I** — Sterne JA, Hernán MA, Reeves BC, Savović J, Berkman ND, Viswanathan M, et al. *ROBINS-I: a tool for assessing risk of bias in non-randomised studies of interventions.* BMJ 2016; 355:i4919. https://doi.org/10.1136/bmj.i4919

**Source files in repo:**
- [backend/rob_tools/rob2.py](../backend/rob_tools/rob2.py)
- [backend/rob_tools/robins_i.py](../backend/rob_tools/robins_i.py)
- [backend/rob_tools/__init__.py](../backend/rob_tools/__init__.py)

**Tool contract (from `backend/rob_tools/__init__.py`):**

> Each tool module exposes:
> - a `DOMAINS` data structure describing signaling questions + elaborations,
> - a pure-Python decision-tree function per domain,
> - an `overall` function aggregating domain judgements,
> - a `run(pdf_bytes, fields, classification, primary_outcome, progress)` entry point that the orchestrator calls.

Both modules also expose `prompt_catalog()` for the developer-view UI (transparency: any signed-in user can inspect the prompts and decision trees behind a judgement).

---

# Section 1 — Cochrane RoB 2 (2019, parallel-group RCTs)

## 1.1 Overview

- **Scope (v1):** individually-randomized parallel-group trials assessing the effect of **assignment** to intervention (intention-to-treat). Cluster-randomized and cross-over trials use different Domain 1/2 signaling questions and are deferred to future modules (`rob2_cluster`, `rob2_crossover`).
- **Domains:** 5
  1. Bias arising from the randomization process
  2. Bias due to deviations from intended interventions (effect of assignment)
  3. Bias due to missing outcome data
  4. Bias in measurement of the outcome
  5. Bias in selection of the reported result
- **Judgement scale:** 3-level — `Low` / `Some concerns` / `High`.

## 1.2 Signal options + judgement levels

```python
SIGNAL_OPTIONS = ("Y", "PY", "PN", "N", "NI")
# Y = Yes, PY = Probably yes, PN = Probably no, N = No, NI = No information
```

Domain judgements: `"Low"` / `"Some concerns"` / `"High"`.

## 1.3 System prompt

```text
You are an evidence-synthesis methodologist assessing risk of bias in a randomized trial using the Cochrane RoB 2 tool. Read the PDF carefully. Answer each signaling question with one of: Y (yes), PY (probably yes), PN (probably no), N (no), NI (no information). Provide a 1-2 sentence rationale for each answer, quoting the paper where possible. Return ONLY a valid JSON object — no preamble, no markdown fences.
```

## 1.4 Per-domain prompt template

The prompt is built per domain by `build_domain_prompt(domain, study_type, primary_outcome, extracted_fields)`:

```text
Assess **Domain {id} — {name}** for the study described in the attached PDF.

Study type: {study_type}
Outcome being assessed: {primary_outcome}

Context (fields already extracted from the paper):
{ctx_json}

Signaling questions:
{questions_block}

Return a JSON object with exactly this shape:
{shape}

Answer N (or PN) when the paper gives enough information to rule out the problem, and NI only when the paper is silent. Rationales must be short (1-2 sentences) and quote the paper verbatim where possible.
```

`questions_block` is constructed per signaling question as:

```text
**{id}. {text}**
Elaboration: {elaboration}
Response options: Y/PY/PN/N/NI.
```

## 1.5 Expected JSON output shape

For each domain, the LLM returns:

```json
{
  "1.1": "Y|PY|PN|N|NI",
  "1.1_rationale": "1-2 sentences quoting the paper",
  "1.2": "Y|PY|PN|N|NI",
  "1.2_rationale": "1-2 sentences quoting the paper",
  "...": "...",
  "direction_of_bias": "NA|Favours experimental|Favours comparator|Towards null|Away from null|Unpredictable"
}
```

(Keys `1.1`, `1.2`, etc. mirror the signaling-question IDs of the domain being assessed.)

## 1.6 Domain definitions

### Domain 1 — Bias arising from the randomization process

**Relevant extracted fields:** `randomization_method`, `allocation_concealment`, `allocation_ratio`, `stratification_factors`, `baseline_balance`.

**1.1** — Was the allocation sequence random?
> Answer 'Yes' if a random component was used in the sequence generation process (computer-generated random numbers; random-number table; coin tossing; shuffling cards or envelopes; throwing dice; drawing lots). Minimization with a random element counts as random. Answer 'No' if no random element was used or the sequence is predictable (alternation; dates of birth/admission; patient record numbers; clinician/participant decisions; any systematic or haphazard method). Answer 'No information' if the only information is a bare statement that the study is randomized.

**1.2** — Was the allocation sequence concealed until participants were enrolled and assigned to interventions?
> Answer 'Yes' if remote/central allocation was used (independent central pharmacy, telephone or internet-based service). Answer 'Yes' if opaque, sequentially-numbered, tamper-sealed envelopes or identical sequentially-numbered drug containers were used correctly. Answer 'No' if there is reason to suspect enrolling personnel or the participant had knowledge of the forthcoming allocation.

**1.3** — Did baseline differences between intervention groups suggest a problem with the randomization process?
> Differences compatible with chance do not indicate bias. Answer 'Yes' only if imbalances indicate a problem: substantial differences in group sizes vs the allocation ratio; a substantial excess of statistically-significant baseline differences; imbalance in one or more key prognostic factors unlikely to be due to chance; or excessive similarity not compatible with chance. Answer 'No information' when there are no useful baseline data.

**Decision tree (cribsheet p.6):**

```python
def rob2_domain1_judge(signals: dict[str, str]) -> str:
    """Domain 1 (randomization process) algorithm — cribsheet p.6.

    Inputs: ``{"1.1": Y/PY/PN/N/NI, "1.2": ..., "1.3": ...}``
    Output: "Low" / "Some concerns" / "High".
    """
    q11 = signals.get("1.1", "NI")
    q12 = signals.get("1.2", "NI")
    q13 = signals.get("1.3", "NI")

    if _no(q12):
        return "High"
    if q12 == "NI":
        return "High" if _yes(q13) else "Some concerns"
    # q12 is Y/PY
    if _no(q11):
        return "High"
    # q11 is Y/PY/NI
    return "Some concerns" if _yes(q13) else "Low"
```

---

### Domain 2 — Bias due to deviations from intended interventions (effect of assignment)

**Relevant extracted fields:** `blinding_participants`, `blinding_personnel`, `protocol_deviations`, `analysis_framework`, `missing_data_handling`.

**2.1** — Were participants aware of their assigned intervention during the trial?
> Participants aware of assignment may change health-related behaviours. Blinding (placebo/sham) prevents such differences. If participants experienced intervention-specific side effects they knew to be specific, answer 'Yes' or 'Probably yes'.

**2.2** — Were carers and people delivering the interventions aware of participants' assigned intervention during the trial?
> If carers/deliverers are aware, implementation may differ (e.g., co-intervention). If allocation was not concealed, awareness is likely. Side effects specific to one intervention also count as awareness.

**2.3** — If Y/PY/NI to 2.1 or 2.2: Were there deviations from the intended intervention that arose because of the trial context?
> Assess problems from changes inconsistent with protocol that arose because of trial context (recruitment/engagement, securing consent, etc). Answer 'Yes' only if evidence/strong reason to believe such deviations happened. Non-adherence consistent with real-world care = No. Protocol-permitted changes (e.g., drug cessation for acute toxicity) = No.

**2.4** — If Y/PY to 2.3: Were these deviations likely to have affected the outcome?
> Changes arising from trial context impact the effect estimate only if they affect the outcome.

**2.5** — If Y/PY/NI to 2.4: Were these deviations from intended intervention balanced between groups?
> Unbalanced trial-context deviations bias the effect estimate more than balanced ones.

**2.6** — Was an appropriate analysis used to estimate the effect of assignment to intervention?
> ITT and modified-ITT (excluding missing outcome data only) are appropriate. Naïve per-protocol and as-treated analyses are inappropriate. Post-randomization exclusion of ineligible participants whose eligibility was confirmed after randomization and couldn't have been affected by assignment can be appropriate.

**2.7** — If N/PN/NI to 2.6: Was there potential for a substantial impact (on the result) of the failure to analyse participants in the group to which they were randomized?
> No precise threshold. Substantial impact possible even with <5% wrong-group or excluded if the outcome is rare or exclusions relate to prognostic factors.

**Decision tree (cribsheet p.10 — Part 1 = 2.1–2.5, Part 2 = 2.6–2.7):**

```python
def rob2_domain2_judge(signals: dict[str, str]) -> str:
    """Domain 2 (deviations from intended interventions — assignment effect)
    algorithm — cribsheet p.10.

    Two parts combined by the criteria on p.10:
      Low    iff Part 1 = Low AND Part 2 = Low
      High   iff either part = High
      Some   otherwise

    Part 1 uses 2.1-2.5, Part 2 uses 2.6-2.7.
    """
    q21 = signals.get("2.1", "NI")
    q22 = signals.get("2.2", "NI")
    q23 = signals.get("2.3", "NI")
    q24 = signals.get("2.4", "NI")
    q25 = signals.get("2.5", "NI")
    q26 = signals.get("2.6", "NI")
    q27 = signals.get("2.7", "NI")

    # Part 1 — 2.1/2.2 awareness then 2.3/2.4/2.5 chain
    aware = (not _no(q21)) or (not _no(q22))  # "Either Y/PY/NI"
    if not aware:
        # Both 2.1 and 2.2 answered N/PN → Low on Part 1
        part1 = "Low"
    else:
        # 2.3 "deviations that arose because of trial context?"
        if _no(q23):
            part1 = "Some concerns"
        elif q23 == "NI":
            part1 = "Some concerns"
        else:
            # 2.3 Y/PY → 2.4 "affect outcome?"
            if _no(q24):
                part1 = "Some concerns"
            else:
                # 2.4 Y/PY/NI → 2.5 "deviations balanced between groups?"
                part1 = "High" if not _yes(q25) else "Some concerns"

    # Part 2 — 2.6 appropriate analysis → 2.7 substantial impact
    if _yes(q26):
        part2 = "Low"
    else:
        # 2.6 N/PN/NI → 2.7
        part2 = "Some concerns" if _no(q27) else "High"

    if part1 == "High" or part2 == "High":
        return "High"
    if part1 == "Low" and part2 == "Low":
        return "Low"
    return "Some concerns"
```

---

### Domain 3 — Bias due to missing outcome data

**Relevant extracted fields:** `attrition_rate`, `missing_data_handling`.

**3.1** — Were data for this outcome available for all, or nearly all, participants randomized?
> ITT analysis requires data from all randomized. 'Nearly all' = missingness small enough that it could have made no important difference. For continuous outcomes, ≥95% is often sufficient; for dichotomous outcomes, the proportion depends on event rate. Imputed data counts as missing here.

**3.2** — If N/PN/NI to 3.1: Is there evidence that the result was not biased by missing outcome data?
> Evidence can come from (1) analysis methods correcting for bias, or (2) sensitivity analyses robust to plausible assumptions. Single-method imputation (LOCF or intervention-group-only multiple imputation) does NOT establish lack of bias.

**3.3** — If N/PN to 3.2: Could missingness in the outcome depend on its true value?
> If loss-to-follow-up/withdrawal might be related to participants' health status, missingness could depend on the true outcome. Documented reasons unrelated to outcome (device failure, routine-collection interruption) → missingness unlikely to depend on true value.

**3.4** — If Y/PY/NI to 3.3: Is it likely that missingness in the outcome depended on its true value?
> Five reasons to answer Yes: differential missingness proportions between groups; reported reasons provide evidence; reasons differ between groups; trial circumstances make dependence likely (e.g., schizophrenia trials); time-to-event censoring when participants change treatment for outcome-related reasons. Answer No if analysis accounted for participant characteristics likely to explain the relationship.

**Decision tree (cribsheet p.16):**

```python
def rob2_domain3_judge(signals: dict[str, str]) -> str:
    """Domain 3 (missing outcome data) — cribsheet p.16."""
    q31 = signals.get("3.1", "NI")
    q32 = signals.get("3.2", "NI")
    q33 = signals.get("3.3", "NI")
    q34 = signals.get("3.4", "NI")

    if _yes(q31):
        return "Low"
    # 3.1 N/PN/NI → 3.2 evidence result not biased
    if _yes(q32):
        return "Low"
    # 3.2 N/PN → 3.3 could missingness depend on true value?
    if _no(q33):
        return "Low"
    # 3.3 Y/PY/NI → 3.4 likely that it depended?
    if _yes(q34) or q34 == "NI":
        return "High"
    return "Some concerns"
```

---

### Domain 4 — Bias in measurement of the outcome

**Relevant extracted fields:** `blinding_outcome_assessors`, `outcome_measurement_method`.

**4.1** — Was the method of measuring the outcome inappropriate?
> Identifies measurement methods unsuitable for the outcome. Usually 'No' for pre-specified outcomes. 'Yes' if the method is unlikely to detect plausible effects or has poor validity.

**4.2** — Could measurement or ascertainment of the outcome have differed between intervention groups?
> Differences may arise from diagnostic detection bias in passive ascertainment, or from intervention-driven extra clinician visits giving more chances to detect outcome events.

**4.3** — If N/PN/NI to 4.1 and 4.2: Were outcome assessors aware of the intervention received by study participants?
> Answer 'No' if assessors were blinded to intervention status. For participant-reported outcomes, the outcome assessor IS the study participant.

**4.4** — If Y/PY/NI to 4.3: Could assessment of the outcome have been influenced by knowledge of intervention received?
> Knowledge could influence participant-reported outcomes (like pain), observer-reported outcomes involving judgement, and intervention-provider decision outcomes. Unlikely to influence all-cause mortality.

**4.5** — If Y/PY/NI to 4.4: Is it likely that assessment of the outcome was influenced by knowledge of intervention received?
> Distinguishes 'could have' (Some concerns) from 'likely did' (High). With strong beliefs in benefit/harm, it's more likely assessment was influenced (e.g., homeopathy participant-reported symptoms; physiotherapist-delivered-then-assessed function).

**Decision tree (cribsheet p.19):**

```python
def rob2_domain4_judge(signals: dict[str, str]) -> str:
    """Domain 4 (measurement of the outcome) — cribsheet p.19."""
    q41 = signals.get("4.1", "NI")
    q42 = signals.get("4.2", "NI")
    q43 = signals.get("4.3", "NI")
    q44 = signals.get("4.4", "NI")
    q45 = signals.get("4.5", "NI")

    # 4.1 method inappropriate?
    if _yes(q41):
        return "High"
    # 4.2 measurement differ between groups?
    if _yes(q42):
        return "High"

    def _chain() -> str:
        # 4.3 outcome assessors aware?
        if _no(q43):
            return "Low"
        # Y/PY/NI → 4.4 could assessment be influenced?
        if _no(q44):
            return "Low"
        # Y/PY/NI → 4.5 likely that it was influenced?
        if _yes(q45) or q45 == "NI":
            return "High"
        return "Some concerns"

    base = _chain()
    if q42 == "NI":
        # NI on 4.2 downgrades a Low to Some concerns (flowchart p.19)
        if base == "Low":
            return "Some concerns"
    return base
```

---

### Domain 5 — Bias in selection of the reported result

**Relevant extracted fields:** `protocol_available`, `outcomes_match_protocol`.

**5.1** — Were the data that produced this result analysed in accordance with a pre-specified analysis plan that was finalized before unblinded outcome data were available for analysis?
> If researchers' pre-specified intentions are available in sufficient detail, planned measurements/analyses can be compared to published. To avoid selection of the reported result, analysis-plan finalization must precede availability of unblinded outcome data.

**5.2** — Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible outcome measurements (e.g., scales, definitions, time points) within the outcome domain?
> An outcome domain may be measured multiple ways. If multiple measurements were made but only one (or a subset) is fully reported without justification and the reported result is likely selected on the basis of results (novelty, significance, confirmation of prior hypothesis), answer 'Yes'.

**5.3** — Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible analyses of the data?
> An outcome measurement may be analysed multiple ways (unadjusted vs adjusted; final value vs change; different composite definitions; covariate sets; missing-data strategies). If multiple estimates exist but only one is reported on the basis of results, answer 'Yes'.

**Decision tree (cribsheet p.23):**

```python
def rob2_domain5_judge(signals: dict[str, str]) -> str:
    """Domain 5 (selection of the reported result) — cribsheet p.23."""
    q51 = signals.get("5.1", "NI")
    q52 = signals.get("5.2", "NI")
    q53 = signals.get("5.3", "NI")

    if _yes(q52) or _yes(q53):
        return "High"
    if _no(q52) and _no(q53):
        # Results selected-from guardrails both N/PN → consult 5.1
        return "Low" if _yes(q51) else "Some concerns"
    # Remaining case: at least one NI, none Y/PY
    return "Some concerns"
```

---

## 1.7 Overall RoB 2 algorithm

Per cribsheet p.24:

```python
def rob2_overall(domain_judgements: list[str]) -> str:
    """Overall RoB judgement per cribsheet p.24.

    Criteria:
      Low iff all domains Low.
      High iff any domain High OR >=2 domains Some concerns.
      Some concerns otherwise.
    """
    if any(j == "High" for j in domain_judgements):
        return "High"
    some = sum(1 for j in domain_judgements if j == "Some concerns")
    if some >= 2:
        return "High"
    if some >= 1:
        return "Some concerns"
    return "Low"
```

**Helpers used by every decision tree:**

```python
def _yes(ans: str) -> bool:
    return ans in ("Y", "PY")

def _no(ans: str) -> bool:
    return ans in ("N", "PN")
```

---

# Section 2 — ROBINS-I (2016, non-randomized studies of interventions)

## 2.1 Overview

- **Scope (v1):** non-randomized studies of interventions — Cohort, Case-Control, Case-Crossover, Non-Randomized Trial, and analytical Cross-Sectional studies. Quasi-experimental designs (before-after, ITS, DiD, regression discontinuity) need their own confounding-prompt adaptations and are deferred.
- **D4 variant:** **effect-of-assignment** only (effect-of-adherence variant deferred).
- **Domains:** 7
  1. Confounding
  2. Selection of participants into the study
  3. Classification of interventions
  4. Deviations from intended interventions (effect of assignment)
  5. Missing data
  6. Measurement of outcomes
  7. Selection of the reported result
- **Judgement scale:** 5-level — `Low` / `Moderate` / `Serious` / `Critical` / `No information`.

## 2.2 Signal options + judgement levels

```python
SIGNAL_OPTIONS = ("Y", "PY", "PN", "N", "NI")
JUDGEMENTS = ("Low", "Moderate", "Serious", "Critical", "No information")
```

## 2.3 System prompt

```text
You are an evidence-synthesis methodologist assessing risk of bias in a non-randomized study of an intervention using the Cochrane ROBINS-I tool. Read the PDF carefully. Answer each signaling question with one of: Y (yes), PY (probably yes), PN (probably no), N (no), NI (no information). Provide a 1-2 sentence rationale for each answer, quoting the paper where possible. Return ONLY a valid JSON object — no preamble, no markdown fences.
```

## 2.4 Per-domain prompt template

```text
Assess **Domain {id} — {name}** for the study described in the attached PDF.

Study type: {study_type}
Outcome being assessed: {primary_outcome}

Context (fields already extracted from the paper):
{ctx_json}

Signaling questions:
{questions_block}

Return a JSON object with exactly this shape:
{shape}

ROBINS-I answers carry different meaning than RoB 2: the judgement scale is Low / Moderate / Serious / Critical / No information (code maps your signal answers to this judgement). Answer N (or PN) when the paper gives enough information to rule out the problem, and NI only when the paper is silent. Rationales must be short (1-2 sentences) and quote the paper verbatim where possible.
```

## 2.5 Expected JSON output shape

For each domain, the LLM returns:

```json
{
  "1.1": "Y|PY|PN|N|NI",
  "1.1_rationale": "1-2 sentences quoting the paper",
  "1.2": "Y|PY|PN|N|NI",
  "1.2_rationale": "1-2 sentences quoting the paper",
  "...": "...",
  "direction_of_bias": "NA|Favours experimental|Favours comparator|Towards null|Away from null|Unpredictable"
}
```

## 2.6 Domain definitions

### Domain 1 — Bias due to confounding

**Relevant extracted fields:** `confounders_measured`, `adjustment_method`, `exposure_definition`, `comparator_group`, `immortal_time_bias`, `confounding_control`.

**1.1** — Is there potential for confounding of the effect of intervention in this study?
> Confounding is expected in almost all non-randomized studies. Answer 'No' or 'Probably no' only when randomization or strong quasi-experimental design rules it out (e.g., if the intervention is truly unrelated to participant characteristics).

**1.2** — Was the analysis based on splitting participants' follow up time according to intervention received?
> Time-split analyses compare time on vs off treatment within participants. They avoid some selection issues but require handling of time-varying confounding.

**1.3** — If Y/PY to 1.2: Were intervention discontinuations or switches likely to be related to factors that are prognostic for the outcome?
> If people stopped/switched intervention for reasons related to outcome prognosis (e.g., side effects, worsening disease), the time-split comparison is confounded.

**1.4** — Did the authors use an appropriate analysis method that controlled for all the important confounding domains?
> The paper should identify important confounding domains (baseline characteristics associated with both intervention and outcome) and use multivariable adjustment, stratification, matching, propensity scores, or similar. 'No' if no adjustment is attempted or key domains are omitted.

**1.5** — If Y/PY to 1.4: Were confounding domains that were controlled for measured validly and reliably by the variables available in this study?
> Adjustment only helps if the confounders were measured well. Self-report of important variables, missing data on confounders, or measurement at the wrong time point weakens adjustment.

**1.6** — Did the authors control for any post-intervention variables that could have been affected by the intervention?
> Adjusting for variables on the causal pathway between intervention and outcome biases the effect estimate. Classic example: adjusting for a biomarker that the intervention changes.

**1.7** — Did the authors use an appropriate analysis method that controlled for time-varying confounding?
> Applies when confounders change over time and intervention decisions depend on those time-varying values (e.g., clinicians adjusting dose based on disease progression). Methods like marginal structural models or g-estimation are appropriate; standard regression is not.

**1.8** — Were there important time-varying confounding effects that the analysis did not account for?
> Answer 'Yes' only if you have clear evidence of unaddressed time-varying confounding; 'No' when not relevant or appropriately handled.

**Decision tree (ROBINS-I §4.2):**

```python
def robins_i_domain1_judge(signals: dict[str, str]) -> str:
    """Domain 1 (confounding) — ROBINS-I guidance §4.2.

    1.1 potential for confounding    → if N/PN, result = Low (no confounding expected).
    1.4 appropriate analysis         → if N/PN, result = Serious/Critical.
    1.5 confounders measured validly → downgrades 1.4-Y to Serious if poor.
    1.6 adjustment for post-intervention variables → if Y/PY, result = Serious
                                                     (adjustment on causal pathway biases the estimate).
    1.8 time-varying confounding     → if Y/PY unaddressed, worst-case Serious.
    """
    q11 = signals.get("1.1", "NI")
    q14 = signals.get("1.4", "NI")
    q15 = signals.get("1.5", "NI")
    q16 = signals.get("1.6", "NI")
    q18 = signals.get("1.8", "NI")

    # No potential for confounding → Low (rare for observational data)
    if _no(q11):
        return "Low"
    # Adjusted for a post-intervention variable → causal-pathway bias → Serious
    if _yes(q16):
        return "Serious"
    # No adjustment attempted at all → Critical
    if _no(q14):
        return "Critical"
    if q14 == "NI":
        return "No information"
    # 1.4 Y/PY: analysis attempted. Quality of measurement matters.
    if _no(q15):
        return "Serious"
    if q15 == "NI":
        return "Moderate"
    # Time-varying confounding unaddressed → bump to Serious
    if _yes(q18):
        return "Serious"
    # Best case for a well-adjusted observational study
    return "Moderate"
```

---

### Domain 2 — Bias in selection of participants into the study

**Relevant extracted fields:** `case_source`, `control_selection`, `matching`, `sampling_method`, `loss_to_follow_up`, `immortal_time_bias`.

**2.1** — Was selection of participants into the study (or analysis) based on participant characteristics observed after the start of intervention?
> Examples: restricting the analytic sample to people who completed treatment; selecting cases based on events observed during follow-up; excluding early deaths.

**2.2** — If Y/PY to 2.1: Were the post-intervention variables that influenced selection likely to be associated with intervention?
> If selection variables are associated with intervention (e.g., completers differ between arms), selection can bias the effect estimate.

**2.3** — If Y/PY to 2.1 and 2.2: Were the post-intervention variables that influenced selection likely to be influenced by the outcome or a cause of the outcome?
> Selection linked to intervention AND to outcome (or its causes) produces collider/selection bias.

**2.4** — Do start of follow-up and start of intervention coincide for most participants?
> Answer 'No' if participants accrue person-time before intervention start (immortal time) or after (lag). Mis-timed follow-up usually inflates apparent protective effects of treatment.

**2.5** — Were adjustment techniques used that are likely to correct for the presence of selection biases?
> Inverse-probability-of-selection weighting, exclusion of the immortal period, or landmark analysis can correct selection bias. Simple multivariable adjustment typically does not.

**Decision tree (ROBINS-I §4.3):**

```python
def robins_i_domain2_judge(signals: dict[str, str]) -> str:
    """Domain 2 (selection of participants) — ROBINS-I guidance §4.3.

    2.1 selection based on post-intervention characteristics?
    2.2 post-intervention selection associated with intervention?
    2.3 post-intervention selection influenced by outcome?
    2.4 follow-up start coincides with intervention start?
    2.5 adjustment methods used that correct selection bias?
    """
    q21 = signals.get("2.1", "NI")
    q22 = signals.get("2.2", "NI")
    q23 = signals.get("2.3", "NI")
    q24 = signals.get("2.4", "NI")
    q25 = signals.get("2.5", "NI")

    # Clean case: no post-intervention selection AND follow-up aligns
    if _no(q21) and _yes(q24):
        return "Low"
    # Post-intervention selection linked to both intervention and outcome
    if _yes(q21) and _yes(q22) and _yes(q23):
        return "Moderate" if _yes(q25) else "Serious"
    # Post-intervention selection linked to intervention only
    if _yes(q21) and _yes(q22):
        return "Moderate" if _yes(q25) else "Serious"
    # Immortal-time-bias risk (follow-up doesn't coincide)
    if _no(q24):
        return "Moderate" if _yes(q25) else "Serious"
    return "Moderate"
```

---

### Domain 3 — Bias in classification of interventions

**Relevant extracted fields:** `exposure_definition`, `exposure_measurement`, `exposure_ascertainment`.

**3.1** — Were intervention groups clearly defined?
> Clear definitions specify the intervention(s) under study, its dose/duration/route, and an explicit comparator.

**3.2** — Was the information used to define intervention groups recorded at the start of the intervention?
> Prospective recording of intervention status prevents recall/memory-based misclassification. Retrospective ascertainment from medical records at a later time is weaker.

**3.3** — Could classification of intervention status have been affected by knowledge of the outcome or risk of the outcome?
> Differential misclassification — e.g., exposure recorded after the event occurred, case-control studies reconstructing exposure history — inflates or distorts effect estimates.

**Decision tree (ROBINS-I §4.4):**

```python
def robins_i_domain3_judge(signals: dict[str, str]) -> str:
    """Domain 3 (classification of interventions) — ROBINS-I guidance §4.4.

    3.1 intervention groups clearly defined?
    3.2 information recorded at the start of the intervention?
    3.3 classification affected by knowledge of the outcome?
    """
    q31 = signals.get("3.1", "NI")
    q32 = signals.get("3.2", "NI")
    q33 = signals.get("3.3", "NI")

    if _yes(q33):
        return "Serious"
    if _no(q31) or _no(q32):
        return "Moderate"
    if q31 == "NI" or q32 == "NI":
        return "No information"
    return "Low"
```

---

### Domain 4 — Bias due to deviations from intended interventions (effect of assignment)

**Relevant extracted fields:** `blinding`, `allocation_mechanism`, `baseline_comparability`, `analysis_framework`.

**4.1** — Were there deviations from the intended intervention beyond what would be expected in usual practice?
> Usual-practice deviations (treatment stopped for side effects, patient preference changes) are acceptable. Trial-context deviations (unblinded providers altering care, protocol-mandated changes not planned for routine use) are concerning.

**4.2** — If Y/PY to 4.1: Were these deviations from intended intervention unbalanced between groups and likely to have affected the outcome?
> Balanced deviations bias less than unbalanced. Judge substantial effect on the outcome of interest.

**4.3** — Were important co-interventions balanced between intervention groups?
> Co-interventions (other treatments received alongside the intervention) that differ between groups can confound the effect estimate.

**4.4** — Was the intervention implemented as intended, with fidelity?
> Was each participant in the intervention group actually exposed to (a dose of) the intended intervention? Poor fidelity (low adherence, wrong dose) attenuates observed effects.

**4.5** — Was an appropriate analysis used to estimate the effect of assignment to intervention?
> ITT or intention-equivalent analyses (using the intervention as assigned rather than as received) estimate the effect of assignment. Per-protocol or as-treated analyses estimate something different and can be biased.

**Decision tree (ROBINS-I §4.5, effect-of-assignment variant):**

```python
def robins_i_domain4_judge(signals: dict[str, str]) -> str:
    """Domain 4 (deviations from intended interventions, effect-of-assignment
    variant) — ROBINS-I guidance §4.5.

    4.1 deviations beyond usual care?
    4.2 unbalanced deviations likely to have affected outcome?
    4.3 important co-interventions balanced between groups?
    4.4 intervention implemented correctly?
    4.5 appropriate analysis for effect of assignment?
    """
    q41 = signals.get("4.1", "NI")
    q42 = signals.get("4.2", "NI")
    q43 = signals.get("4.3", "NI")
    q44 = signals.get("4.4", "NI")
    q45 = signals.get("4.5", "NI")

    # Best case: no unusual deviations, correct implementation, balanced co-interventions
    if _no(q41) and _yes(q44) and _yes(q43):
        return "Low"
    # Unbalanced deviations likely to affect outcome
    if _yes(q41) and _yes(q42):
        return "Moderate" if _yes(q45) else "Serious"
    # Intervention not correctly implemented → Serious
    if _no(q44):
        return "Serious"
    # Co-interventions unbalanced → Moderate at least
    if _no(q43):
        return "Moderate"
    return "Moderate"
```

---

### Domain 5 — Bias due to missing data

**Relevant extracted fields:** `loss_to_follow_up`, `missing_data_handling`, `attrition_rate`.

**5.1** — Were outcome data available for all, or nearly all, participants?
> 'Nearly all' means missingness is small enough that it could not meaningfully change the effect estimate. Judge by proportion AND by whether missing participants differ from available ones.

**5.2** — Were participants excluded from the analysis due to missing data on the intervention status?
> Excluding people with unknown intervention status can introduce bias if their outcomes differ systematically.

**5.3** — Were participants excluded from the analysis due to missing data on other variables needed for the analysis?
> Complete-case analysis on confounders/effect modifiers may bias the estimate when missingness is not completely at random.

**Decision tree (ROBINS-I §4.6):**

```python
def robins_i_domain5_judge(signals: dict[str, str]) -> str:
    """Domain 5 (missing data) — ROBINS-I guidance §4.6.

    5.1 outcome data available for all/nearly all participants?
    5.2 participants excluded due to missing intervention status?
    5.3 participants excluded due to missing data on other variables?
    """
    q51 = signals.get("5.1", "NI")
    q52 = signals.get("5.2", "NI")
    q53 = signals.get("5.3", "NI")

    if _yes(q51) and _no(q52) and _no(q53):
        return "Low"
    if _no(q51) and (_yes(q52) or _yes(q53)):
        return "Serious"
    if _no(q51):
        return "Moderate"
    if q51 == "NI":
        return "No information"
    return "Moderate"
```

---

### Domain 6 — Bias in measurement of outcomes

**Relevant extracted fields:** `outcome_ascertainment`, `outcome_definition`.

**6.1** — Could the outcome measure have been influenced by knowledge of the intervention received?
> Subjective outcomes (pain, quality of life, clinician judgement) can be influenced by intervention knowledge. Objective outcomes (all-cause mortality, linked-registry events) usually cannot.

**6.2** — Were outcome assessors aware of the intervention received by study participants?
> Blinded assessors eliminate knowledge-driven measurement bias. In observational studies blinding is rarely formal — assess whether assessors had effective access to intervention status when scoring the outcome.

**6.3** — Were the methods of outcome assessment comparable across intervention groups?
> Differential follow-up frequency, different diagnostic workups, or different case-finding between groups create detection bias.

**6.4** — Were any systematic errors in measurement of the outcome related to the intervention received?
> Yes if intervention modifies the measured quantity (e.g., treatment that changes a biomarker used to define the outcome) without truly changing the underlying clinical state.

**6.5** — Is it likely that assessment of the outcome was influenced by knowledge of the intervention received?
> Distinguishes 'could have been' (some concerns) from 'likely was' (serious). Knowledge influence is more likely with strong beliefs about intervention benefit/harm.

**Decision tree (ROBINS-I §4.7):**

```python
def robins_i_domain6_judge(signals: dict[str, str]) -> str:
    """Domain 6 (measurement of outcomes) — ROBINS-I guidance §4.7.

    6.1 could outcome measure be influenced by intervention knowledge?
    6.2 were outcome assessors aware of intervention received?
    6.3 were outcome methods comparable across groups?
    6.4 were systematic measurement errors related to intervention?
    6.5 likely that measurement was influenced by intervention knowledge?
    """
    q61 = signals.get("6.1", "NI")
    q62 = signals.get("6.2", "NI")
    q63 = signals.get("6.3", "NI")
    q64 = signals.get("6.4", "NI")
    q65 = signals.get("6.5", "NI")

    if _yes(q64) or _yes(q65):
        return "Serious"
    if _no(q63):
        return "Serious"
    # Objective measurement OR blinded assessors → Low
    if _no(q61) and _no(q62):
        return "Low"
    # Some NI — honest "no information"
    if q61 == "NI" and q62 == "NI":
        return "No information"
    return "Moderate"
```

---

### Domain 7 — Bias in selection of the reported result

**Relevant extracted fields:** `outcome_definition`, `statistical_analysis`.

**7.1** — Is the reported effect estimate likely to be selected, on the basis of the results, from multiple outcome measurements within the outcome domain?
> Outcome domain may be measured multiple ways (scales, time points, definitions). If only the most favorable measurement is reported without prespecification, answer 'Yes'.

**7.2** — Is the reported effect estimate likely to be selected, on the basis of the results, from multiple analyses of the intervention-outcome relationship?
> Multiple modeling choices (unadjusted vs adjusted, alternative covariate sets, different missing-data strategies) can produce different estimates. Selection on favorable results is concerning.

**7.3** — Is the reported effect estimate likely to be selected, on the basis of the results, from different subgroups?
> Post-hoc subgroup reporting driven by where effects look largest is a form of result-driven selection.

**Decision tree (ROBINS-I §4.8):**

```python
def robins_i_domain7_judge(signals: dict[str, str]) -> str:
    """Domain 7 (selection of reported result) — ROBINS-I guidance §4.8.

    7.1 selected from multiple eligible outcome measurements?
    7.2 selected from multiple eligible analyses?
    7.3 selected from different subgroups on basis of results?
    """
    q71 = signals.get("7.1", "NI")
    q72 = signals.get("7.2", "NI")
    q73 = signals.get("7.3", "NI")

    if _yes(q71) or _yes(q72) or _yes(q73):
        return "Serious"
    if q71 == "NI" and q72 == "NI" and q73 == "NI":
        return "No information"
    return "Low"
```

---

## 2.7 Overall ROBINS-I algorithm

Worst-domain aggregation per ROBINS-I guidance p.10:

```python
def robins_i_overall(domain_judgements: list[str]) -> str:
    """Overall judgement — worst-domain aggregation per ROBINS-I guidance p.10.

    Order of severity (worst → best):
      Critical > Serious > Moderate > No information > Low.

    A single Critical domain makes the study Critical overall. A single Serious
    domain makes it Serious. "No information" means at least one domain could
    not be judged but no Moderate/Serious/Critical domains were found.
    """
    if any(j == "Critical" for j in domain_judgements):
        return "Critical"
    if any(j == "Serious" for j in domain_judgements):
        return "Serious"
    if any(j == "Moderate" for j in domain_judgements):
        return "Moderate"
    if any(j == "No information" for j in domain_judgements):
        return "No information"
    return "Low"
```

**Helpers used by every decision tree (same as RoB 2):**

```python
def _yes(ans: str) -> bool:
    return ans in ("Y", "PY")

def _no(ans: str) -> bool:
    return ans in ("N", "PN")
```

---

# Appendix — How the orchestrator wires these together

For each paper, [backend/quality_appraisal.py](../backend/quality_appraisal.py) classifies the study type, picks the matching tool from `STUDY_TYPE_REGISTRY` (RCT → RoB 2 + CONSORT 2025; Cohort / Case-Control / Case-Crossover / Non-Randomized Trial / Cross-Sectional → ROBINS-I + STROBE 2007), and calls `tool.run(pdf_bytes, fields, classification, primary_outcome, progress)`. Inside `run()`:

1. For each `domain` in `DOMAINS`, build a per-domain prompt via `build_domain_prompt(...)`.
2. Send PDF + prompt to the LLM via `backend/annotator.py:_call_with_pdf` (3-stage oversize fallback: PDF-as-document → pypdf text → chunked map-reduce).
3. Parse JSON; coerce each signal answer into `Y/PY/PN/N/NI` (default `NI`).
4. Map signals → domain judgement via `DOMAIN_JUDGES[domain_id](signals)` (the pure-Python decision tree — never the LLM).
5. After all domains: aggregate via `rob2_overall(...)` or `robins_i_overall(...)`.
6. Return `(domain_results, overall_judgement, overall_direction)`.

The orchestrator then computes initial GRADE (from the registry) and an updated GRADE downgraded for RoB. The full prompt catalog and decision-tree source are also exposed via `prompt_catalog()` for the developer-view UI — readers can verify the exact logic behind any judgement.
