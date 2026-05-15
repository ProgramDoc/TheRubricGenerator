# Quality Appraisal — RoB 2 + ROBINS-I V2 + QUADAS-3 Reference

Reference for the LLM prompts and Python decision-tree rules used by The Rubric Generator's Quality Appraisal AI. Transcribed from source.

**Sources:**
- **RoB 2** — Sterne JAC, Savović J, Page MJ, Higgins JPT, et al. *RoB 2: a revised tool for assessing risk of bias in randomised trials.* BMJ 2019; 366:l4898. Signaling-question text and elaborations transcribed from `20190814_RoB_2.0_cribsheet_parallel_trial.pdf`.
- **ROBINS-I V2** — ROBINS-I V2 development group (Sterne JA, Brandt Mathur M, Elbers R, Hróbjartsson A, McAleenan A, Reeves B, Shrier I, Tilling K, et al.). *The Risk Of Bias In Non-randomized Studies — of Interventions, Version 2 (ROBINS-I V2) assessment tool, 20 November 2025.* riskofbias.info. Published explicitly for follow-up (cohort) studies; supersedes ROBINS-I V1 (Sterne et al. BMJ 2016;355:i4919).
- **QUADAS-3 v1.2** — Whiting et al., University of Bristol. The successor to QUADAS-2 (2011), restructured around four domains and parallel risk-of-bias + applicability judgements per domain. Signaling-question text and elaborations transcribed from `QUADAS-3 1.2.docx` (May 2026).
- **STARD 2015** — Bossuyt PM, Reitsma JB, Bruns DE, Gatsonis CA, Glasziou PP, Irwig L, et al. *STARD 2015: An Updated List of Essential Items for Reporting Diagnostic Accuracy Studies.* BMJ 2015; 351:h5527. https://doi.org/10.1136/bmj.h5527

**Source files in repo:**
- [backend/rob_tools/rob2.py](../backend/rob_tools/rob2.py)
- [backend/rob_tools/robins_i.py](../backend/rob_tools/robins_i.py)
- [backend/rob_tools/quadas3.py](../backend/rob_tools/quadas3.py)
- [backend/reporting_guidelines/stard.py](../backend/reporting_guidelines/stard.py)
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

- **Scope (v1):** individually-randomized parallel-group trials assessing the effect of **assignment** to intervention (intention-to-treat). Cluster-randomized trials use different Domain 1/2 signaling questions and are deferred to a future module (`rob2_cluster`). **Cross-over trials are now supported via the [RoB 2 cross-over extension](#section-1b--cochrane-rob-2-cross-over-trials-extension) module (`rob2_crossover`) — Section 1B below.**
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

# Section 1B — Cochrane RoB 2 cross-over trials extension

## 1B.1 Overview

- **Scope:** randomized **cross-over** trials. Source: the Cochrane RoB 2 cross-over extension (Higgins, Eldridge, Li, Sterne — `RoB2_crossover_trial_assessment_tool`).
- **Domains:** 6 (parallel-group has 5; the extension inserts Domain S after D2)
  1. Bias arising from the randomization process
  2. Bias due to deviations from intended interventions (effect of assignment)
  - **S.** Bias arising from period and carryover effects ← NEW (cross-over only)
  3. Bias due to missing outcome data
  4. Bias in measurement of the outcome
  5. Bias in selection of the reported result — **4 signaling questions** (5.4 added)
- **Judgement scale:** 3-level — `Low` / `Some concerns` / `High`, same as parallel-group.
- **Module:** [`backend/rob_tools/rob2_crossover.py`](../backend/rob_tools/rob2_crossover.py). Registered in `STUDY_TYPE_REGISTRY["Crossover Trial"]`.

## 1B.2 Domain S — Bias arising from period and carryover effects

Carryover is the persistence of a treatment effect from one period into the next. The extension domain checks both whether carryover can be ruled out (study design + biology) and whether the analysis handled it appropriately.

### Signaling questions (verbatim from the cross-over cribsheet)

```text
S.1  Were carryover effects unlikely to occur in this trial, given the nature
     of the interventions and the outcome?
S.2  If carryover effects could occur, was there a suitable washout period
     between treatment periods?
S.3  For trials with potential for carryover effects, were unbiased data
     available for the analysis (e.g., from periods unaffected by carryover,
     or via methods that adjust for carryover)?
S.4  Were the data analysed using an appropriate paired analysis that takes
     the cross-over design into account?
```

### Decision tree

```python
def rob2_crossover_domainS_judge(signals: dict[str, str]) -> str:
    s1 = signals.get("S.1", "NI")
    s2 = signals.get("S.2", "NI")
    s3 = signals.get("S.3", "NI")
    s4 = signals.get("S.4", "NI")

    # Carryover ruled out + paired/appropriate analysis → Low
    if _yes(s1) and _yes(s4):
        return "Low"

    # Carryover plausible + no washout + no unbiased data → High
    if _no(s1) and _no(s2) and _no(s3):
        return "High"

    # Carryover plausible + analysis didn't take design into account → High
    if _no(s1) and _no(s4):
        return "High"

    # Inappropriate analysis on its own (regardless of carryover ruled-out
    # status) → High, because results aren't usable
    if _no(s4) and not _yes(s1):
        return "High"

    return "Some concerns"
```

## 1B.3 Domain 5 — extends parallel-group with 5.4

Domain 5 in the cross-over cribsheet has **four** signaling questions. 5.1–5.3 are reused verbatim from the parallel-group cribsheet (elaborations note "as for parallel group trials"); 5.4 is cross-over-specific.

### 5.4 — verbatim from the cross-over cribsheet

```text
5.4  Is a result based on data from both periods sought, but unavailable on
     the basis of carryover having been identified?

Elaboration: This question addresses the situation in which only results from
the first period are reported on the basis of a test for carryover. Answer 'N'
if data from both periods contribute to the result being assessed for risk of
bias.
```

A "Y/PY" answer on 5.4 escalates Domain 5 to High because it represents selective reporting (first-period-only data chosen post-hoc based on a carryover test result).

### Decision tree

```python
def rob2_crossover_domain5_judge(signals: dict[str, str]) -> str:
    q51 = signals.get("5.1", "NI")
    q52 = signals.get("5.2", "NI")
    q53 = signals.get("5.3", "NI")
    q54 = signals.get("5.4", "NI")

    if _yes(q52) or _yes(q53) or _yes(q54):
        return "High"
    if _no(q52) and _no(q53) and _no(q54):
        return "Low" if _yes(q51) else "Some concerns"
    return "Some concerns"
```

## 1B.4 Other domains

Domains 1, 2, 3, and 4 reuse the parallel-group signaling questions verbatim — the elaborations note "as for parallel group trials" with cross-over-specific framing (e.g., D1.3 refers to baseline differences between **sequence** groups at study entry; D2.5 refers to "balanced between sequence groups across periods"; D2.6 expects a **paired** analysis that accounts for period and/or carryover effects). The full module source is the canonical reference: [`backend/rob_tools/rob2_crossover.py`](../backend/rob_tools/rob2_crossover.py).

## 1B.5 Overall algorithm

Same as parallel-group RoB 2 (cribsheet p.24): Low iff all 6 domains Low; High iff any domain High OR ≥2 domains Some concerns; Some concerns otherwise. The `rob2_crossover_overall()` function delegates to `rob2.rob2_overall()` for this aggregation.

## 1B.6 Reporting guideline — CONSORT cross-over extension

Cross-over trials use the combined **CONSORT 2025 + cross-over extension** reporting checklist, implemented in [`backend/reporting_guidelines/consort_crossover.py`](../backend/reporting_guidelines/consort_crossover.py). The cross-over extension (Dwan, Li, Altman, Elbourne 2019, BMJ 366: l4378) adds 16 items prefixed `X-` covering: identification as a cross-over trial; description of periods and sequences; washout duration and rationale; carryover considerations; period effects; per-period outcomes; paired sample-size calculation; sequence randomization; paired statistical methods; pre-specified first-period contingency; flow diagram by period and sequence; period-specific losses; baseline data by sequence; period-specific outcome data; paired effect estimates with 95% CI; carryover test results. One LLM call per paper assesses adherence to all 30 + 16 = 46 items; proportion is `adhered / applicable` (null items excluded).

---

# Section 2 — ROBINS-I V2 (20 November 2025, follow-up cohort studies)

## 2.1 Overview

- **Scope (V2):** published explicitly for **follow-up (cohort) studies**. V2 removes V1's "Bias due to deviations from intended interventions" domain; protocol-deviation issues are folded into Domain 1 Variant B (time-varying confounding). Quasi-experimental designs (before-after, ITS, DiD, regression discontinuity) need their own adaptations and are deferred.
- **Domains:** 6 (vs V1's 7 — D4 "deviations" is gone)
  1. Bias due to confounding (two variants — see below)
  2. Bias in classification of interventions
  3. Bias in selection of participants into the study (or analysis)
  4. Bias due to missing data
  5. Bias arising from measurement of the outcome
  6. Bias in selection of the reported result
- **Domain 1 variants** chosen per paper at the preflight stage:
  - **Variant A** — analysis estimates the intention-to-treat effect; only **baseline** confounding needs addressing (4 signaling questions).
  - **Variant B** — analysis estimates the per-protocol effect; **baseline + time-varying** confounding need addressing (5 signaling questions).
- **Judgement scale:** 4-level — `Low` / `Moderate` / `Serious` / `Critical`. V1's separate "No information" judgement is **retired** in V2 (NI is still a valid *signal* answer; the algorithms route NI through the trees rather than producing a distinct judgement). **Domain 1's "Low" is labelled "Low (except for concerns about uncontrolled confounding)"** per the cribsheet footnote on page 4 — confounding cannot be eliminated in an observational study, so the best achievable confidence is "Low except…".
- **Repo scope caveat:** other non-randomized designs (Case-Control, Case-Crossover, analytical Cross-Sectional, Non-Randomized Trial) still dispatch to this tool from `STUDY_TYPE_REGISTRY` and run V2 as a best-available approximation. A pure assessment for those designs would require V1 ROBINS-I or a design-specific tool.

## 2.2 Signal vocabulary + judgement levels

```python
# Union of all V2 tokens — per-question subsets are declared on each signal entry.
SIGNAL_OPTIONS_ALL = ("Y", "PY", "PN", "N", "NI", "WN", "SN", "WY", "SY")
JUDGEMENTS = ("Low", "Moderate", "Serious", "Critical")
LOW_D1 = "Low (except for concerns about uncontrolled confounding)"
```

**Signal token semantics:**

| Token | Meaning |
|-------|---------|
| `Y` / `PY` | Yes / Probably yes |
| `N` / `PN` | No / Probably no |
| `NI` | No information |
| `WN` | Weak no — direction is no but magnitude is uncertain (e.g. "most-but-not-all important confounders controlled") |
| `SN` | Strong no — magnitude clearly substantial (e.g. "at least one important confounder not controlled, likely material impact") |
| `WY` | Weak yes — direction is yes but magnitude is small (e.g. "knowledge could have influenced assessment, small impact") |
| `SY` | Strong yes — magnitude clearly substantial (e.g. "knowledge influenced assessment, large impact") |

Different signaling questions accept different subsets. The per-question allowed list is declared on each `signal` entry in `DOMAINS` and surfaced in the prompt's "Response options:" line so the LLM picks only legal tokens.

## 2.3 System prompt

```text
You are an evidence-synthesis methodologist assessing risk of bias in a non-randomized study of an intervention using the Cochrane ROBINS-I V2 tool (20 November 2025 cribsheet). Read the PDF carefully. Answer each signaling question with one of the allowed tokens for that question — Y (yes), PY (probably yes), PN (probably no), N (no), NI (no information), and where indicated WN (weak no), SN (strong no), WY (weak yes), SY (strong yes). Provide a 1-2 sentence rationale for each answer, quoting the paper where possible. Return ONLY a valid JSON object — no preamble, no markdown fences.
```

## 2.4 Per-domain prompt template

```text
Assess **Domain {id} — {name}** (Variant A|B for Domain 1) for the study described in the attached PDF using the ROBINS-I V2 tool.

Study type: {study_type}
Outcome being assessed: {primary_outcome}

Target PICO (user-supplied):
{target_pico_block — optional}

Context (fields already extracted from the paper):
{ctx_json}

Signaling questions:
{questions_block}    # each declares its allowed response-option subset

Return a JSON object with exactly this shape:
{shape}

Notes on ROBINS-I V2:
- The judgement scale is Low / Moderate / Serious / Critical (4 levels). Code maps your signal answers to the judgement — answer the signaling questions only.
- Some questions allow WN / SN (weak / strong no) or WY / SY (weak / strong yes). Use the strong version only when the magnitude is clearly substantial; use the weak version when the direction is right but the magnitude is uncertain.
- Answer N (or PN) when the paper gives enough information to rule out the problem; NI only when the paper is silent.
- Rationales must be short (1-2 sentences) and quote the paper verbatim where possible.
```

## 2.5 Expected JSON output shape

For each domain, the LLM returns:

```json
{
  "<sig_id>": "<one of the per-question allowed tokens>",
  "<sig_id>_rationale": "1-2 sentences quoting the paper",
  "...": "...",
  "direction_of_bias": "NA|Favours intervention|Favours comparator|Towards null|Away from null|Unpredictable"
}
```

Signal ids for Domain 1 are prefixed with the variant: `1A.1` / `1A.2` / … or `1B.1` / `1B.2` / …. All other domains use plain numeric ids (`2.1`, `3.4`, etc).

## 2.6 Preflight — preliminary considerations (B1 / B2 / B3 + C4)

Before any per-domain assessment, V2 runs a **preliminary considerations** screen as a single LLM call. The orchestrator answers four questions:

- **B1** — Did the authors make any attempt to control for confounding? (`Y / PY / PN / N`)
- **B2** (only if N/PN to B1) — Is there sufficient potential for confounding that an unadjusted result should not be considered further? (`Y / PY / PN / N`)
- **B3** — Was the method of measuring the outcome inappropriate? (`Y / PY / PN / N`)
- **C4** — Did the analysis account for switches / protocol deviations during follow-up? (`No` → ITT → Domain 1 Variant A; `Yes` → per-protocol → Domain 1 Variant B)

**Short-circuit rule (cribsheet p9):** if **B2 = Y/PY** *or* **B3 = Y/PY**, the result is at **Critical risk of bias** and no further per-domain assessment is required. This saves 6 domain LLM calls per shorted paper.

```text
Preflight prompt (excerpt):

You are performing the Preliminary Considerations screen of ROBINS-I V2 on a non-randomized study.

Answer four preliminary-consideration questions:

B1. Did the authors make any attempt to control for confounding in the result being assessed?
B2. (Only if N/PN to B1) Is there sufficient potential for confounding that an unadjusted result should not be considered further?
B3. Was the method of measuring the outcome inappropriate?
C4. Did the analysis account for switches during follow-up between the intervention strategies being compared, or for other protocol deviations during follow-up?
     - No  → the analysis is estimating the intention-to-treat effect (Variant A)
     - Yes → the analysis is estimating the per-protocol effect (Variant B)
```

## 2.7 Domain definitions

### Domain 1 — Bias due to confounding

**Relevant extracted fields:** `confounders_measured`, `adjustment_method`, `exposure_definition`, `comparator_group`, `immortal_time_bias`, `confounding_control`.

V2 splits Domain 1 into two variants, chosen per-paper at the preflight stage by C4 (whether the analysis estimates ITT or per-protocol). Only the chosen variant's signaling questions are answered for a given paper.

**Domain 1 produces the special label** `"Low (except for concerns about uncontrolled confounding)"` rather than plain `"Low"` — confounding cannot be ruled out observationally.

#### Variant A — ITT effect (C4 = No, baseline confounding only)

**1A.1** — Did the authors control for all the important confounding factors for which this was necessary? (`Y / PY / WN / SN / NI`)
> Y/PY if all important confounders identified at the preliminary stage were appropriately controlled (stratification, regression, matching, standardization, propensity scores, IPTW). WN if most were controlled and uncontrolled confounding was probably not substantial. SN if at least one important confounder should have been controlled but was not, and the failure is likely to have a material impact.

**1A.2** — Were confounding factors that were controlled for measured validly and reliably by the variables available in this study? (`NA / Y / PY / WN / SN / NI`)
> Adjustment helps only if confounders were measured well. WN if measurement error was probably not substantial; SN if there was at least one important confounder measured poorly enough that the extent of measurement error was probably substantial.

**1A.3** — Did the authors control for any post-intervention variables that could have been affected by the intervention? (`NA / Y / PY / PN / N / NI`)
> Controlling for variables on the causal pathway between intervention and outcome (over-adjustment) biases the effect estimate. Classic example: adjusting for a biomarker that the intervention changes.

**1A.4** — Did the use of negative controls, quantitative bias analysis, or other considerations suggest serious uncontrolled confounding? (`Y / PY / PN / N`)
> If the study did not use negative controls and no other considerations suggest uncontrolled confounding, answer N. Y/PY if negative controls indicate the result being assessed suffers from material bias due to confounding.

**Decision tree (cribsheet p20):**

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
        if _yes(q3):  # over-adjusted for post-intervention vars → causal-pathway bias
            if _yes(q4):
                return "Critical"
            if _strict_yes(q2):
                return "Serious"
            return "Critical"
        # 1.3 N/PN/NI: no over-adjustment
        if _strict_yes(q2) or _weak_no(q2):
            return "Serious" if _yes(q4) else LOW_D1
        return "Serious"  # 1.2 SN/NI

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

#### Variant B — Per-protocol effect (C4 = Yes, baseline + time-varying confounding)

**1B.1** — Did the authors use an analysis method that was appropriate to control for time-varying as well as baseline confounding? (`Y / PY / PN / N / NI`)
> Appropriate "g-methods" include inverse probability weighting based on baseline + time-varying confounding factors with adjustment for censoring weights. Standard regression including time-varying confounders may be problematic when those confounders are affected by prior intervention (treatment-confounder feedback).

**1B.2** — Did the authors control for all the important baseline and time-varying confounding factors for which this was necessary? (`NA / Y / PY / WN / SN / NI`)
> Same WN / SN semantics as Variant A 1A.1, applied to baseline + time-varying confounders.

**1B.3** — Were confounding factors that were controlled for measured validly and reliably by the variables available in this study? (`NA / Y / PY / WN / SN / NI`)
> Same measurement-validity question as Variant A 1A.2, applied to the broader factor set.

**1B.4** — Did the authors control for time-varying factors or other variables measured after the start of intervention? (`NA / Y / PY / PN / N / NI`)
> Asked when 1B.1 is N/PN/NI. Conditioning on time-varying factors measured after intervention start is likely to bias the result when those factors are on the causal pathway from intervention to outcome.

**1B.5** — Did the use of negative controls, or other considerations, suggest serious uncontrolled confounding? (`Y / PY / PN / N`)
> Same as Variant A 1A.4.

**Decision tree (cribsheet p24):**

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

### Domain 2 — Bias in classification of interventions

**Relevant extracted fields:** `exposure_definition`, `exposure_measurement`, `exposure_ascertainment`, `intervention_classification`.

**2.1** — Were the intervention strategies distinguishable at the time when follow-up would have started in the target trial? (`Y / PY / PN / N / NI`)
> Some strategies (e.g. "surgery within 6 months of diagnosis" vs "delay surgery until clinical progression") cannot be distinguished at follow-up start, creating an "immortal time" period during which the outcome cannot occur for some groups.

**2.2** — Did all or nearly all outcome events occur after the intervention and comparator strategies could be distinguished? (`NA / Y / PY / PN / N / NI`)
> Asked only if 2.1 was N/PN/NI. If the indistinguishable period is short relative to total follow-up, misclassification bias may be small.

**2.3** — Did the analysis avoid problems arising from intervention strategies that are not distinguishable at the start of follow-up? (`NA / SY / WY / PN / N / NI`)
> SY (fully) if clone-censor-weighting, g-formula, or a landmark analysis was used with predictors of treatment during follow-up measured and used appropriately. WY (partially) if appropriate but unlikely to have fully adjusted.

**2.4** — Was classification of intervention status influenced by knowledge of the outcome or risk of the outcome? (`SY / WY / PN / N / NI`)
> Differential misclassification — outcome (or its causes other than intervention) influences how interventions are classified. SY = yes, substantial impact; WY = yes, impact not substantial.

**2.5** — Were further classification errors (not influenced by knowledge of the outcome or risk of the outcome) likely? (`Y / PY / PN / N / NI`)
> Non-differential misclassification — receipt of intervention not recorded for some participants. Usually biases towards the null.

**Decision tree (cribsheet p28) — linear-tier model:**

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

### Domain 3 — Bias in selection of participants into the study (or analysis)

**Relevant extracted fields:** `case_source`, `control_selection`, `sampling_method`, `loss_to_follow_up`, `immortal_time_bias`.

V2 D3 has three sub-sections — **A** (prevalent-user bias / immortal time), **B** (other selection bias), and **C** (analysis / sensitivity / severity).

#### A. Prevalent-user bias and immortal time

**3.1** — Did follow-up in the analysis begin at the start of the intervention strategies being compared? (`Y / PY / WN / SN / NI`)
> Y/PY if all outcome events and follow-up time after intervention start were included in the analysis. WN if not substantial; SN if leading to a substantial risk of bias.

**3.2** — Were outcome events during a period of follow-up after the start of the interventions excluded from the analysis? (`Y / PY / PN / N / NI`)
> Only asked if 3.1 was Y/PY. Such exclusion creates "immortal time" during which events cannot occur and biases the effect estimate.

#### B. Other selection bias

**3.3** — Was selection of participants into the study (or analysis) based on participant characteristics observed after the start of intervention, additional to the situations addressed in 3.1 and 3.2? (`Y / PY / PN / N / NI`)
> N/PN if selection was based only on pre-intervention characteristics — that's baseline confounding (Domain 1), not selection bias.

**3.4** — Were the post-intervention variables that influenced selection likely to be associated with intervention? (`NA / Y / PY / PN / N / NI`)
> Only asked if 3.3 was Y/PY. Selection bias requires selection related to BOTH intervention and outcome.

**3.5** — Were the post-intervention variables that influenced selection likely to be influenced by the outcome or a cause of the outcome? (`NA / Y / PY / PN / N / NI`)
> Only asked if 3.4 was Y/PY. Collider-style selection bias.

#### C. Analysis / sensitivity / severity

**3.6** — Is it likely that the analysis corrected for all of the potential selection biases identified above? (`NA / Y / PY / PN / N / NI`)
> Only asked if A or B raised concerns. IPW can create a pseudo-population without the selection bias if assumptions are justified.

**3.7** — Did sensitivity analyses demonstrate that the likely impact of the potential selection biases was minimal? (`NA / Y / PY / PN / N / NI`)
> Only asked if 3.6 was N/PN/NI.

**3.8** — Were potential selection biases identified above sufficiently severe that the result should not be included in a quantitative synthesis? (`NA / Y / PY / PN / N / NI`)
> Distinguishes Serious from Critical. Answer N/PN/NI unless there is clear evidence that the selection biases were severe.

**Decision tree (cribsheet p32):**

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

### Domain 4 — Bias due to missing data

**Relevant extracted fields:** `loss_to_follow_up`, `missing_data_handling`, `attrition_rate`.

The tree branches by how the analysis handled missingness: **complete-case**, **imputation**, or **alternative method**.

**4.1** — Were complete data on intervention status available for all, or nearly all, participants? (`Y / PY / PN / N / NI`)
**4.2** — Were complete data on the outcome available for all, or nearly all, participants? (`Y / PY / PN / N / NI`)
**4.3** — Were complete data on important confounding variables available for all, or nearly all, participants? (`Y / PY / PN / N / NI`)
> "Nearly all" = number excluded so small it could not have made an important difference. For continuous outcomes, 95% (or 90%) is often sufficient. For dichotomous outcomes, the threshold depends on event rate.

**4.4** — Is the result based on a complete case analysis? (`NA / Y / PY / PN / N / NI`)
**4.5** — Was exclusion from the analysis because of missing data likely to be related to the true value of the outcome? (`NA / Y / PY / PN / N / NI`)
**4.6** — Is the relationship between the outcome and missingness likely to be explained by the variables in the analysis model? (`NA / Y / PY / WN / SN / NI`)
> If all variables that plausibly explain the outcome-missingness relationship are included in the complete-case analysis, bias is low. WN if not substantial; SN if bias is likely substantial.

**4.7** — Was the analysis based on imputing missing values? (`NA / Y / PY / PN / NI`)
**4.8** — Is it reasonable to assume data were MAR or MCAR? (`NA / Y / PY / PN / N / NI`)
**4.9** — Was imputation performed appropriately? (`NA / Y / PY / WN / SN / NI`)
> WN/SN if simple methods (LOCF, mean imputation) were used; Y/PY if multiple imputation included all predictors of missingness and all variables in the main analysis model.

**4.10** — Was an appropriate alternative method used to correct for bias due to missing data? (`NA / Y / PY / WN / SN / NI`)
> Asked when the analysis was neither complete case nor imputation. Examples: IPW, FIML.

**4.11** — Is there evidence that the result was not biased by missing data? (`NA / Y / PY / PN / N`)
> Evidence from (1) analysis methods that would not be biased under plausible missingness assumptions, or (2) sensitivity analyses showing results change little.

**Decision tree (cribsheet p38):**

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

### Domain 5 — Bias arising from measurement of the outcome

**Relevant extracted fields:** `outcome_ascertainment`, `outcome_definition`.

**5.1** — Could measurement or ascertainment of the outcome have differed between intervention groups? (`Y / PY / PN / N / NI`)
> Y/PY → Serious directly. Differences arise through "diagnostic detection bias" or extra visits for intervention participants.

**5.2** — Were outcome assessors aware of the intervention received by study participants? (`Y / PY / PN / N / NI`)
> N if blinded, or if participants self-report and were themselves blinded. In observational studies, usually Y when participants report outcomes themselves.

**5.3** — Could assessment of the outcome have been influenced by knowledge of the intervention received? (`NA / SY / WY / PN / N / NI`)
> Only asked if 5.2 was Y/PY/NI. SY = yes, to a large extent (e.g. patient-reported symptoms in homeopathy studies; recovery assessments by physiotherapists). WY = yes, to a small extent (knowledge could have influenced but no strong reason to believe it did).

**Decision tree (cribsheet p41):**

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

### Domain 6 — Bias in selection of the reported result

**Relevant extracted fields:** `outcome_definition`, `statistical_analysis`.

**6.1** — Was the result reported in accordance with an available, pre-determined analysis plan? (`Y / PY / PN / N / NI`)
> Analysis plans are rarely publicly available for non-randomized studies, so most papers will not be assessed as Low on the basis of 6.1 alone.

**6.2** — Selected from multiple outcome **measurements** within the outcome domain? (`Y / PY / PN / N / NI`)
**6.3** — Selected from multiple **analyses** of the data? (`Y / PY / PN / N / NI`)
**6.4** — Selected from multiple **subgroups**? (`Y / PY / PN / N / NI`)

**Decision tree (cribsheet p47):**

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

### 2.7.1 Single-Arm Trial / Dose-Escalation adaptation

V2 is published for cohort/follow-up studies. We extend it to **single-arm** (uncontrolled) designs — Single-Arm Trial, Dose-Escalation Study — via a third variant of Domain 1 and Domain 2. D3–D6 reuse cohort signals + judges unchanged. Selected at the top of `run()` based on `classification["study_type"]` (BEFORE preflight), NOT via C4. C4 is still asked but recorded as metadata; the variant is pinned to `"single_arm"` for these designs.

**Per-domain disposition:**

| Domain | Disposition |
|---|---|
| D1 Confounding | **Replaced.** No comparator → confounding-by-indication N/A. Reframed as benchmark adequacy + prognostic-mix comparability. |
| D2 Classification | **Replaced (degenerate, 3 questions).** Only one arm, but intent-vs-received cohort definition still matters. |
| D3 Selection | Reused unchanged — most-relevant domain for single-arm (eligibility creep, prevalent-user bias, immortal-time all transfer). |
| D4 Missing data | Reused unchanged. |
| D5 Outcome measurement | Reused unchanged. |
| D6 Reported result | Reused unchanged. |

**Registry entries:** `Single-Arm Trial` and `Dose-Escalation Study` both map to `rob_tool="robins_i"` (the runner internally selects the single_arm variant) + `reporting_guideline="strobe"` + `initial_grade="Very low"` (conservative — uncontrolled designs start at the lowest GRADE level; `compute_grade` clamps further downgrades at Very low).

**Dose-Escalation caveat:** MTD/DLT/RP2D-specific bias considerations (e.g. selection of the expansion cohort based on DLT observation) are intentionally not modeled in v1. Dose-Escalation reuses the single-arm variant wholesale.

**Preflight (single-arm variant)** — the cohort B1/B2 (confounding control) are replaced with benchmark-pre-specification questions. B3 (outcome measurement) is comparator-agnostic and reused verbatim. C4 (ITT vs per-protocol) is still asked but recorded as metadata only — single-arm trials can still have protocol deviations; the answer informs interpretation of D2-single-arm question 2S.3 but does NOT swap variants.

- **B1-SA** — Did the authors pre-specify a quantitative benchmark (historical control rate, performance criterion, or null hypothesis with a statistical decision rule) against which the single-arm result is being judged? `(Y / PY / PN / N)`
- **B2-SA** — (If N/PN to B1-SA) Is the absence of any pre-specified benchmark severe enough that the single-arm proportion is uninterpretable for causal inference? `(Y / PY / PN / N / NA)` — Y/PY short-circuits to **Critical**.
- **B3** — Reused verbatim. Y/PY short-circuits to **Critical**.
- **C4** — Reused verbatim. Recorded as metadata. Variant stays `"single_arm"` regardless of answer.

#### Domain 1 (single-arm variant) — signaling questions

**Relevant extracted fields:** `confounders_measured`, `adjustment_method`, `comparator_historical_reference`, `primary_endpoint_prespecified`, `consecutive_enrolment`, `outcome_definition`.

**1S.1** — Was the implied benchmark (historical control rate, pre-specified performance criterion, or null hypothesis with a quantitative decision rule) pre-specified before data collection? `(Y / PY / PN / N)`
> Examples of pre-specified benchmarks: a Simon two-stage design with a numeric response-rate threshold; an FDA accelerated-approval ORR threshold cited in the protocol; a published historical control rate that the trial was powered against. Answer Y/PY if such a benchmark is clearly identifiable in the protocol/SAP/methods. Answer N/PN if no benchmark is stated, or if the benchmark looks post-hoc.

**1S.2** — Is the implied benchmark reasonable given current standard of care and the patient population being studied? `(Y / PY / PN / N / NI)`
> Answer Y/PY if the benchmark is consistent with contemporary published control-arm rates in comparable patients. Answer N/PN if implausibly low (inflates apparent benefit) or implausibly high (forces a near-impossible bar).

**1S.3** — Is the cohort's measured baseline prognostic profile (stage, prior lines, ECOG / performance status, biomarker status, key comorbidities) comparable to that of the benchmark population? `(NA / Y / PY / WN / SN / NI)`
> WN when most-but-not-all prognostic factors look comparable. SN when at least one important prognostic factor is materially more favourable in this cohort. NA only when no benchmark was identified at 1S.1.

**1S.4** — Did the authors address residual prognostic-mix differences quantitatively (sensitivity analyses, propensity-score adjustment to external controls, prognostic-score stratification, or similar)? `(NA / Y / PY / PN / N / NI)`
> Examples: propensity-score weighting against an external real-world cohort, MAIC, pre-specified sensitivity analyses.

**1S.5** — Do negative / falsification controls, external-validity considerations, or other quantitative bias analyses suggest serious uncontrolled selection-prognostic bias? `(Y / PY / PN / N)`
> Analogous to 1A.4 / 1B.5 in the cohort variants. Answer N when no such consideration suggests substantial bias — the typical answer.

**Judgement scale:** `Low (except for concerns about uncontrolled benchmarking)` / `Moderate` / `Serious` / `Critical` — Domain 1's "Low" gets the SA-specific label since benchmark-mismatch confounding can never be fully ruled out without a comparator.

**Decision tree:**

```python
def domain1_variant_single_arm_judge(signals: dict[str, str]) -> str:
    """D1 Variant single_arm (uncontrolled — no comparator)."""
    q1 = signals.get("1S.1", "NI")  # benchmark pre-specified?
    q2 = signals.get("1S.2", "NI")  # benchmark reasonable?
    q3 = signals.get("1S.3", "NI")  # prognostic profile comparable?
    q4 = signals.get("1S.4", "NI")  # quantitative adjustment?
    q5 = signals.get("1S.5", "NI")  # negative/falsification controls?

    # 1S.5 dominates — falsification hit → Critical regardless
    if _yes(q5):
        return "Critical"

    # No pre-specified benchmark + no quantitative adjustment → Critical
    if _strict_no(q1):
        if _strict_no(q4):
            return "Critical"
        return "Serious"
    if _no_info(q1):
        return "Serious"

    # Benchmark pre-specified (Y/PY)
    if _strict_yes(q1):
        if _strict_yes(q3):
            return LOW_D1_SA if _strict_yes(q2) else "Moderate"
        if _weak_no(q3):
            return "Moderate"
        if _strong_no(q3) or _no_info(q3):
            return "Moderate" if _strict_yes(q4) else "Serious"

    return "Serious"
```

#### Domain 2 (single-arm variant) — signaling questions

**Relevant extracted fields:** `exposure_definition`, `intervention_classification`, `escalation_scheme`, `dose_levels`, `expansion_cohort`.

**2S.1** — Was the intervention well-defined (dose, schedule, duration, dose-modifications protocol) at the start of follow-up? `(Y / PY / PN / N / NI)`
> Answer N/PN when the intervention is described only at high level (e.g. "standard chemotherapy").

**2S.2** — Were dose reductions, holds, and discontinuations recorded and reported? `(Y / PY / WN / SN / NI)`
> WN if most exposure modifications were recorded; SN if material exposure detail is missing such that the analyzed "intervention" is effectively undefined.

**2S.3** — Was the analyzed cohort defined by intended treatment (everyone enrolled, ITT-like) or by received treatment (only those completing ≥X cycles / responding to treatment)? `(NA / SY / WY / PN / N / NI)`
> SY (strong yes) when the primary analysis is explicitly restricted to completers or responders. WY (weak yes) when the analyzed cohort excludes some enrolled patients for treatment-related reasons but not dominantly. N/PN when all enrolled (or modified ITT) are analyzed.

**Decision tree:**

```python
def domain2_variant_single_arm_judge(signals: dict[str, str]) -> str:
    """D2 Variant single_arm — degenerate classification (one intervention)."""
    q1 = signals.get("2S.1", "NI")  # intervention well-defined?
    q2 = signals.get("2S.2", "NI")  # modifications recorded?
    q3 = signals.get("2S.3", "NI")  # ITT-like vs received-treatment cohort?

    # 2S.3 SY = strongly responder-restricted analysis → Critical
    if _strong_yes(q3):
        return "Critical"
    if _weak_yes(q3) or _no_info(q3):
        return "Serious"

    # q3 in (PN, N): cohort defined by intended treatment
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

The `LOW_D1_SA` label is normalized to `"Low"` by `robins_i_overall()` so single-arm + cohort runs aggregate consistently.

---

## 2.8 Overall ROBINS-I V2 algorithm

Worst-domain aggregation per cribsheet p48. The user may override upward when multiple Serious domains compound, but the algorithm default is the worst single-domain judgement. Domain 1's special `"Low (except for concerns about uncontrolled confounding)"` label is normalized to `"Low"` for aggregation.

```python
def robins_i_overall(domain_judgements: list[str]) -> str:
    """Overall judgement — worst-domain aggregation per cribsheet p48.

    Severity (worst → best): Critical > Serious > Moderate > Low.
    """
    rank = {LOW_D1: 0, "Low": 0, "Moderate": 1, "Serious": 2, "Critical": 3}
    worst = max((rank.get(j, 1) for j in domain_judgements), default=0)
    if worst == 0:
        return "Low"
    return JUDGEMENTS[worst]
```

**Preflight short-circuit:** if B2=Y/PY or B3=Y/PY at the preflight stage, the result is **Critical** with no domain-level assessment performed — see §2.6.

**Helpers used by the V2 decision trees (alongside RoB 2's `_yes` / `_no`):**

```python
def _yes(ans: str) -> bool:
    """Any 'yes-flavoured' answer including weak / strong yes."""
    return ans in ("Y", "PY", "WY", "SY")

def _no(ans: str) -> bool:
    """Any 'no-flavoured' answer including weak / strong no."""
    return ans in ("N", "PN", "WN", "SN")

def _strict_yes(ans: str) -> bool:  return ans in ("Y", "PY")
def _strict_no(ans: str) -> bool:   return ans in ("N", "PN")
def _weak_no(ans: str) -> bool:     return ans == "WN"
def _strong_no(ans: str) -> bool:   return ans == "SN"
def _weak_yes(ans: str) -> bool:    return ans == "WY"
def _strong_yes(ans: str) -> bool:  return ans == "SY"
def _no_info(ans: str) -> bool:     return ans == "NI"
```


---

# Section 3 — QUADAS-3 v1.2 (diagnostic test accuracy)

## 3.1 Overview

- **Scope (v1):** single-test diagnostic accuracy reviews (one index test vs one reference standard). QUADAS-C — the separate tool for comparative-accuracy reviews of two index tests — is out of scope.
- **Domains:** 4
  1. Participants
  2. Index Test
  3. Target Condition
  4. Analysis
- **Dual assessment:** every paper gets **two** parallel Phase-6 aggregates:
  1. an overall **risk of bias** (`overall_rob`) across all 4 domains, and
  2. an overall **applicability concern** (`overall_applicability`) across domains 1–3 only. Domain 4 (Analysis) is RoB-only — it has no applicability question.
- **Judgement scale:** 3-level — `Low` / `High` / `Insufficient information` (II). Mapped to a new badge class on the frontend (`Insufficient information → rob-ni`, reusing the No-information greyscale from ROBINS-I).
- **Per-estimate path:** unlike RoB 2 and ROBINS-I, QUADAS-3 is per-estimate, not per-paper. A single DTA paper can produce many sensitivity/specificity estimates from different subgroups × thresholds × reference standards, and each estimate gets its own row in `quality_appraisal_results`. See section 3.8 below.

## 3.2 Signal options + judgement levels

```python
SIGNAL_OPTIONS = ("Y", "PY", "PN", "N", "NI")
# Y = Yes, PY = Probably yes, PN = Probably no, N = No, NI = No information

JUDGEMENTS            = ("Low", "High", "Insufficient information")
APPLICABILITY_OPTIONS = ("Low", "High", "Insufficient information")
```

Domain RoB judgements and per-domain applicability judgements both use the same 3-level scale.

## 3.3 System prompt

```text
You are an evidence-synthesis methodologist assessing a diagnostic test accuracy study using the QUADAS-3 v1.2 tool (Whiting et al., University of Bristol). For each domain, read the PDF carefully and answer the signaling questions with one of: Y (yes), PY (probably yes), PN (probably no), N (no), NI (no information). When the domain has an applicability assessment, also rate concern that the as-conducted study matches the ideal test accuracy trial as: Low / High / Insufficient information. Provide a short rationale (1-2 sentences, quoting the paper where possible) for every answer. Return ONLY a valid JSON object — no preamble, no markdown fences.
```

## 3.4 Per-domain prompt template

The prompt is built per domain by `build_domain_prompt(domain, study_type, primary_outcome, extracted_fields, estimate=None, review_context=None)`. The same template covers all 4 domains; for D1–D3 it also asks for an applicability judgement against the optional user-supplied review context (Phases 1+2 — synthesis question + ideal test accuracy trial).

```text
Assess **Domain {id} — {name}** of QUADAS-3 v1.2 for the diagnostic test accuracy study described in the attached PDF.

Study type: {study_type}
Primary outcome (target condition): {primary_outcome}

Estimate being assessed:
{estimate_block}   # one of N estimates extracted from the paper, or "primary / headline estimate" fallback

Context (fields already extracted from the paper):
{relevant_fields_as_json}

Signaling questions:
{questions_block}   # verbatim signaling-question text + elaborations from the docx

**Applicability assessment** (only shown for D1–D3; rate as Low / High / Insufficient information):
{applicability_question}
Elaboration: {applicability_elaboration}

**Review-level context** (use this to judge applicability):
{review_context}   # user's Phases 1+2 free-text input, or a fallback line when not supplied

Return a JSON object with exactly this shape: {shape}

Answer N (or PN) only when the paper gives enough information to rule out adherence; answer NI only when the paper is silent. Rationales must be short (1-2 sentences) and quote the paper verbatim where possible.
```

## 3.5 Expected JSON output shape

For domains with an applicability assessment (D1, D2, D3):

```json
{
  "1.1": "Y|PY|PN|N|NI",
  "1.1_rationale": "...",
  "1.2": "...",  "1.2_rationale": "...",
  "1.3": "...",  "1.3_rationale": "...",
  "1.4": "...",  "1.4_rationale": "...",
  "applicability_judgement": "Low|High|Insufficient information",
  "applicability_rationale": "..."
}
```

For Domain 4 (Analysis, RoB-only), the same shape minus the two applicability fields.

## 3.6 Domain definitions

The docx Tables 6–9 transcribed verbatim into `DOMAINS` in [backend/rob_tools/quadas3.py](../backend/rob_tools/quadas3.py).

### Domain 1 — Participants (4 signals + applicability)

- **1.1** Was a single-gate design used?
  - Elaboration: A single-gate design enrols one group of participants in whom the diagnosis is not yet known (this could include multiple groups from different locations, e.g. different hospitals). A multi-gate design (case-control) enrols participants with known diagnosis and is at higher risk of spectrum bias. Answer 'Yes' for single-gate; 'No' for multi-gate / case-control.
- **1.2** Were participants prospectively enrolled?
  - Elaboration: Prospective enrolment lets investigators standardise the test workflow and minimises differential verification. Retrospective enrolment from medical records or stored samples increases the risk that participants entered the two-by-two table for reasons related to the test result.
- **1.3** Was a consecutive or random sample of participants included?
  - Elaboration: Consecutive enrolment of all eligible participants in a defined window, or random sampling from the eligible pool, minimises selection bias. 'No' for convenience samples or selection on test-related criteria.
- **1.4** Is the study group a representative sample of the intended-use population?
  - Elaboration: Compare the enrolled spectrum (presentation, prior tests, comorbidity, prevalence) to the population in which the index test would be deployed. 'No' if cases or controls are unusually severe / mild / pre-screened relative to the intended-use population.

**Applicability concern:** *Concern that the included participants do not match those in the ideal test accuracy trial.* Describe how differences between the included participants (presentation, prior testing, setting, intended use of index test) and the ideal trial defined for the review have led to this judgement.

### Domain 2 — Index Test (4 signals + applicability)

- **2.1** Was the index test conducted and interpreted according to the recommended instructions?
  - Elaboration: Following manufacturer's instructions, published protocols, or accepted standard clinical methods. 'No' for ad-hoc modifications or undocumented procedure.
- **2.2** Were the index test results interpreted without knowledge of the reference standard results?
  - Elaboration: Blinding of the index-test interpreter to the reference standard prevents review bias. For automated reads or tests interpreted before the reference standard is known, this is naturally satisfied.
- **2.3** Were the index test results interpreted with the same information as would be available when the test is used in practice?
  - Elaboration: Interpretation should be representative of clinical use. 'No' if interpreters had extra information (e.g., MRI for a screening mammogram) or were deprived of routinely available context (e.g., clinical history).
- **2.4** If an index test threshold was used, was it standard or pre-specified?
  - Elaboration: Thresholds derived post-hoc from the data ('data-driven') are at high risk of optimistic bias. Pre-specified or standard manufacturer thresholds are preferable. Mark 'NI' if no threshold was used (continuous test reported as AUC only).

**Applicability concern:** *Concern that the index test, its conduct, or interpretation does not match the ideal test accuracy trial.*

### Domain 3 — Target Condition (8 signals + applicability)

- **3.1** Does the reference standard adequately identify those with and without the target condition?
  - Elaboration: The reference standard should correctly classify all participants. 'No' if the reference standard is known to be inaccurate (low-sensitivity gold standard, imperfect composite) or substantially different from the accepted diagnostic criterion.
- **3.2** Was the target condition assessed in all participants?
  - Elaboration: Partial verification — applying the reference standard only to a subset (e.g., index-positive participants) — biases sensitivity and specificity estimates. 'No' if verification was selective.
- **3.3** Was the target condition assessed in the same way in all participants?
  - Elaboration: Differential verification — different reference standards for index-positive vs index-negative — introduces bias. 'No' if multiple reference standards were used non-randomly.
- **3.4** Did the reference standard avoid incorporating the index test?
  - Elaboration: Incorporation bias arises when the index test is part of a composite reference standard, inflating apparent accuracy. 'No' for any composite reference that uses the index test as a component.
- **3.5** Was the reference standard conducted and interpreted according to the recommended instructions?
  - Elaboration: Same standard as the index-test version of this question but applied to the reference. Followed protocols, manufacturer's instructions, or accepted clinical criteria → 'Yes'. Ad-hoc reading or undocumented procedure → 'No'.
- **3.6** Were the reference standard results interpreted without knowledge of the index test results?
  - Elaboration: Blinding of the reference-standard interpreter to the index test prevents review bias.
- **3.7** If a reference standard threshold was used, was it standard or pre-specified?
  - Elaboration: Same logic as for the index test: data-driven thresholds introduce optimistic bias; standard or pre-specified thresholds are preferable. 'NI' if no threshold was used.
- **3.8** Was there an appropriate time interval between index test and reference standard?
  - Elaboration: The interval must be short enough that the disease state could not have changed between tests. Long delays introduce disease-progression bias. The appropriate duration is condition-specific (hours for stroke, weeks for a slow-growing tumour).

**Applicability concern:** *Concern that the target condition as defined by the reference standard does not match the ideal test accuracy trial.*

### Domain 4 — Analysis (4 signals; RoB-only, **no applicability**)

- **4.1** Were all participants included in the analysis?
  - Elaboration: Per the flow diagram (Phase 3): every enrolled participant should appear in the two-by-two table or have a documented reason for exclusion. 'No' if participants disappear from the analysis without explanation.
- **4.2** Were missing data handled appropriately?
  - Elaboration: Missing index test, missing reference standard, and uninterpretable results should be reported and handled transparently. Indeterminate results coded as test-positive or test-negative without justification introduces bias. 'No' if missing data are silently dropped or default-coded.
- **4.3** Does the unit of analysis match the ideal test accuracy trial?
  - Elaboration: Unit of analysis should match the clinical decision (participant, lesion, sample, image, etc.). 'No' if the paper reports per-lesion accuracy when per-participant would be the clinically relevant unit, or vice versa.
- **4.4** Were the estimates of sensitivity and specificity calculated appropriately?
  - Elaboration: Standard 2×2 sensitivity and specificity formulas, exact or score CIs, no improper pooling across non-independent observations. 'No' for non-standard or unjustified statistical methods.

## 3.7 Per-domain decision tree (Phase 5)

The QUADAS-3 docx Phase 5 narrative explicitly allows reviewer judgement to keep a domain at Low even when one or more signaling questions are answered N/PN. Baking that judgement into a deterministic tree would be arbitrary, so we take the **conservative** interpretation: any flagged signal forces High. Rationales are preserved verbatim so reviewers can override in their own write-up.

All four domains use the **same** judge function (registered identically in `DOMAIN_JUDGES`):

```python
def quadas3_domain_judge(signals: dict[str, str]) -> str:
    """Map signaling-question answers (Y/PY/PN/N/NI) to a domain-level RoB
    judgement (Low / High / Insufficient information) per QUADAS-3 Phase 5.

    Rule:
      - All signals Y or PY → "Low"
      - Any N or PN → "High"
      - Any NI without N/PN → "Insufficient information"
      - All NI (or empty) → "Insufficient information"
    """
    answered = [v for v in signals.values()]
    if not answered:
        return "Insufficient information"
    if any(_no(v) for v in answered):
        return "High"
    if all(_yes(v) for v in answered):
        return "Low"
    # Mixed Y/PY + NI → Insufficient information per Phase 5
    return "Insufficient information"
```

Helpers (`_yes`, `_no`) are identical to those used by RoB 2 and ROBINS-I.

## 3.8 Phase 6 — overall judgements

The same Phase 6 rule applies to both the RoB aggregate (all 4 domains) and the applicability aggregate (3 domains — Analysis is excluded). Run as `quadas3_overall(judgements)` and `quadas3_applicability_overall(judgements)` respectively (the second is just an alias that wraps the same function, called with the 3-element list).

```python
def quadas3_overall(domain_judgements: list[str]) -> str:
    """Aggregate per QUADAS-3 Phase 6.

    - Any domain High → "High"
    - All domains Low → "Low"
    - Otherwise (any II, none High) → "Insufficient information"
    """
    if not domain_judgements:
        return "Insufficient information"
    if any(j == "High" for j in domain_judgements):
        return "High"
    if all(j == "Low" for j in domain_judgements):
        return "Low"
    return "Insufficient information"
```

`run()` returns a **4-tuple** rather than the 3-tuple used by RoB 2 / ROBINS-I:

```python
def run(pdf_bytes, extracted_fields, classification, primary_outcome,
        progress=None, *, estimate=None, review_context=None):
    """Returns (domain_results, overall_rob, "NA", overall_applicability)."""
```

The 3rd slot (direction-of-bias / direction-of-effect) is always literal `"NA"` for diagnostic accuracy — direction is a treatment-trial concept that doesn't apply to sensitivity/specificity.

## 3.9 Per-estimate Phase-4 path

Diagnostic-accuracy papers often report multiple sens/spec estimates (subgroup × index test × threshold × reference standard × unit of analysis). The QUADAS-3 docx Phase 4 instructs reviewers to extract every estimate relevant to the synthesis question and assess each one separately. The orchestrator implements this:

1. **`extract_estimates(pdf_bytes, extracted_fields) → list[dict]`** in `backend/rob_tools/quadas3.py` runs a single LLM call returning every numerical accuracy estimate the paper reports. Synthetic ids 1..N are assigned in Python. Each estimate carries `description`, `subgroup`, `index_test`, `threshold`, `reference_standard`, `unit_of_analysis`, `sensitivity`, `specificity`, `n`.
2. Surfaced as `POST /api/quality-appraisal/extract-estimates` (engineer seat, 3 cr/paper, auto-refund on error).
3. The run-create modal calls this endpoint per selected paper and renders a checkbox card per paper. User selects 0+ estimates per paper; selections go into `payload.paper_estimates`.
4. In `appraise_paper`, when `cfg.get("supports_estimates")` is true the per-estimate helper `_appraise_paper_with_estimates` runs classify + prefill + STARD **once per paper**, then loops over estimates running QUADAS-3 + GRADE **once per estimate**. Each estimate produces a separate `quality_appraisal_results` row (same `paper_id`, distinct `estimate_id`).
5. Cost: one "unit of work" (36 cr) per (paper, estimate) tuple. Refunds are also per-unit — `summary["estimates_errored"]` tells `run_batch` how many units to refund.

When no estimates are supplied for a diagnostic-accuracy paper, the path falls back to a **single-estimate iteration** against the paper's primary / headline estimate (same idiom as the RCT primary-outcome auto-pick).

## 3.10 Phases 1+2 — review-level context

The docx asks reviewers to specify, once per review, the synthesis question (Phase 1) and the ideal test accuracy trial design (Phase 2). In v1 we collect both as a single optional free-text textarea on the run-create modal (`quadas3_review_context` on the run row). This text is threaded into the D1/D2/D3 applicability prompts so the LLM can judge "concern that the as-conducted study does not match the ideal trial" against the reviewer's stated synthesis question + ideal-trial design. Without this context, applicability is judged against a generic intended-use baseline; the prompt explicitly tells the LLM to default to that.

## 3.11 GRADE for diagnostic accuracy

`STUDY_TYPE_REGISTRY["Diagnostic Accuracy"]` sets `initial_grade="High"` (GRADE handbook default for cross-sectional accuracy designs) and `skip_grade_extras=True`. `_rob_downgrade(rob_overall, rob_domain_judgements)` handles the QUADAS-3 outcome scale:

| `rob_overall`             | Downgrade | Notes |
|---------------------------|-----------|-------|
| Low                       | 0         | No risk-of-bias downgrade |
| High (1 domain High)      | 1         | Standard single-domain downgrade |
| High (≥2 domains High)    | 2         | Multi-domain downgrade |
| Insufficient information  | 1         | Conservative — same as ROBINS-I "No information" |

`compute_grade` is called with `indirectness_levels=0, imprecision_levels=0` because `cfg.skip_grade_extras=True` — the existing modules in `backend/indirectness.py` and `backend/imprecision.py` assume PICO/treatment trials, not PIRT (Patient / Index test / Reference standard / Target condition), so we defer them rather than produce subtly wrong outputs. PIRT-aware versions are deferred to v2.

## 3.12 Out of scope for v1 QUADAS-3

- Per-estimate domain-difference shortcut from the docx ("After the first estimate, only domains where characteristics are different need to be assessed" — every estimate runs all 4 domains in v1).
- PIRT-aware indirectness + imprecision for diagnostic accuracy.
- Structured Phase 1 + Phase 2 inputs (collected as one free-text field in v1, not the tables in the docx).
- QUADAS-C — separate tool for comparative-accuracy reviews of two index tests.
- Editing / overriding AI judgements in the UI.

---

# Section 4 — STARD 2015 reporting checklist (for diagnostic accuracy)

Paired with QUADAS-3 in `STUDY_TYPE_REGISTRY["Diagnostic Accuracy"]`. Single LLM call per paper, same shape as STROBE / CONSORT. 34 entries total (30 numbered items with a/b sub-items at 10, 12, 13, 21):

- **1** Title — Identification as a study of diagnostic accuracy using at least one measure of accuracy.
- **2** Abstract — Structured summary of study design, methods, results, and conclusions.
- **3** Introduction (Background) — Scientific and clinical background, including the intended use and clinical role of the index test.
- **4** Introduction (Objectives) — Study objectives and hypotheses.
- **5** Methods (Study design) — Prospective vs retrospective.
- **6** Methods (Participants — Eligibility) — Eligibility criteria.
- **7** Methods (Participants — Identification) — Basis for identifying potentially eligible participants.
- **8** Methods (Participants — Sampling) — Where and when potentially eligible participants were identified.
- **9** Methods (Participants — Recruitment) — Consecutive, random, or convenience series.
- **10a / 10b** Methods (Test methods — Index test / Reference standard) — In sufficient detail to allow replication.
- **11** Methods (Test methods — Rationale) — Rationale for choosing the reference standard.
- **12a / 12b** Methods (Test methods — Index / Reference thresholds) — Definition + rationale, distinguishing pre-specified from exploratory.
- **13a / 13b** Methods (Test methods — Blinding) — Whether clinical information / opposite-test results were available to readers.
- **14** Methods (Analysis — Estimates) — Methods for estimating or comparing measures of diagnostic accuracy.
- **15** Methods (Analysis — Indeterminate results) — How indeterminate results were handled.
- **16** Methods (Analysis — Missing data) — How missing data were handled.
- **17** Methods (Analysis — Subgroups) — Any analyses of variability in accuracy, distinguishing pre-specified from exploratory.
- **18** Methods (Analysis — Sample size) — Intended sample size + derivation.
- **19** Results (Participants — Flow) — Flow diagram.
- **20** Results (Participants — Baseline) — Baseline demographic and clinical characteristics.
- **21a / 21b** Results (Participants — Distribution by reference standard / Alternative diagnoses).
- **22** Results (Participants — Time interval) — Time interval and any clinical interventions between index test and reference standard.
- **23** Results (Test results — Cross-tabulation) — 2×2 table or its distribution.
- **24** Results (Test results — Estimates) — Estimates of accuracy and precision (e.g. 95% CIs).
- **25** Results (Test results — Adverse events) — Any adverse events from performing the index test or reference standard. **Often N/A for non-invasive imaging.**
- **26** Discussion (Limitations) — Including sources of potential bias, statistical uncertainty, generalisability.
- **27** Discussion (Implications) — Implications for practice including intended use and clinical role of the index test.
- **28** Other (Registration) — Registry name and number. **Often N/A for retrospective records reviews.**
- **29** Other (Protocol) — Where the full study protocol can be accessed.
- **30** Other (Funding) — Sources of funding and role of funders.

Items marked N/A by the LLM (`adhered=null`) are excluded from the proportion denominator so the adherence rate isn't deflated by structurally non-applicable items.


---

# Appendix — How the orchestrator wires these together

For each paper, [backend/quality_appraisal.py](../backend/quality_appraisal.py) classifies the study type, picks the matching tool from `STUDY_TYPE_REGISTRY`:

| Study type                                                                              | RoB tool   | Reporting guideline | Initial GRADE | Extras                                       |
|-----------------------------------------------------------------------------------------|------------|---------------------|---------------|----------------------------------------------|
| Randomized Controlled Trial                                                             | rob2       | consort2025         | High          | indirectness + imprecision                   |
| Cohort / Case-Control / Non-Randomized Trial / Cross-Sectional (Analytical) / Case-Crossover | robins_i   | strobe              | Low           | indirectness + imprecision                   |
| Diagnostic Accuracy                                                                     | quadas3    | stard               | High          | `skip_grade_extras=True`, `supports_estimates=True` |

Inside `tool.run()` for treatment-trial designs:

1. For each `domain` in `DOMAINS`, build a per-domain prompt via `build_domain_prompt(...)`.
2. Send PDF + prompt to the LLM via `backend/annotator.py:_call_with_pdf` (3-stage oversize fallback: PDF-as-document → pypdf text → chunked map-reduce).
3. Parse JSON; coerce each signal answer into `Y/PY/PN/N/NI` (default `NI`).
4. Map signals → domain judgement via `DOMAIN_JUDGES[domain_id](signals)` (the pure-Python decision tree — never the LLM).
5. After all domains: aggregate via `rob2_overall(...)` or `robins_i_overall(...)`.
6. Return `(domain_results, overall_judgement, overall_direction)`.

For QUADAS-3, `appraise_paper` branches on `cfg.get("supports_estimates")` and routes through `_appraise_paper_with_estimates`. Each domain LLM call returns both signal answers (RoB) and applicability judgement+rationale (where applicable) in one JSON payload. `run()` returns a 4-tuple including the overall applicability aggregate. One `quality_appraisal_results` row is written per (paper, estimate) tuple.

The orchestrator then computes initial GRADE (from the registry) and an updated GRADE downgraded for RoB (+ indirectness + imprecision for non-QUADAS-3 tools). The full prompt catalog, signaling questions, and decision-tree source for **every** tool are exposed via `prompt_catalog()` for the developer-view UI at `GET /api/quality-appraisal/prompts` — readers can verify the exact logic behind any judgement.
