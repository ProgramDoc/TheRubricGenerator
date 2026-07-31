# RoB 2 Cross-Over Trials — Sharable Methodology Reference

A self-contained reference for implementing an automated Cochrane RoB 2 cross-over assessment on any platform. Contains:

- Signaling questions (verbatim from the cribsheet) for all 6 domains
- Decision-tree logic as plain Python (no framework / database / HTTP dependencies)
- LLM prompt templates (the exact strings sent to the model)
- Expected JSON output shape
- Overall aggregation algorithm

**Sources transcribed:**

- Cochrane RoB 2 cross-over extension cribsheet (Higgins, Eldridge, Li, Sterne — `RoB2_crossover_trial_assessment_tool`)
- For parallel-group reused items: 2019 cribsheet (Higgins, Savović, Page, Sterne et al. — `20190814_RoB_2.0_cribsheet_parallel_trial.pdf`)

**Scope:** the cross-over extension to RoB 2. The base 5-domain parallel-group flow does not apply unchanged — Domain S is added and Domain 5 has 4 signaling questions instead of 3.

---

**Assessment scope: one assessment per (study × outcome).** This instrument rates a *result*, not a paper. Several of its signalling questions — missing outcome data, measurement of the outcome, and selection of the reported result — are answered differently for different outcomes in the same study, so one trial can be *Low* for all-cause mortality and *High* for an unblinded symptom score. Run the whole instrument once per outcome you intend to report, passing that outcome as the assessed outcome, and store one judgement per (study × outcome). Reusing a single paper-level judgement across every outcome attaches a rating to outcomes it was never made about, and nothing in the output reveals that it happened. Only the instrument call repeats: classification and field extraction that feed the prompts are outcome-independent and run once per study.

## 1. Signal answer options

Every signaling question accepts one of five answers:

```python
SIGNAL_OPTIONS = ("Y", "PY", "PN", "N", "NI")
# Y  = Yes
# PY = Probably yes
# PN = Probably no
# N  = No
# NI = No information
```

Decision trees treat Y/PY as "yes" and N/PN as "no":

```python
def _yes(ans: str) -> bool:
    return ans in ("Y", "PY")

def _no(ans: str) -> bool:
    return ans in ("N", "PN")
```

Domain judgements are 3-level: `"Low"` / `"Some concerns"` / `"High"`.

---

## 2. Domain definitions

Domains are processed in the cribsheet's narrative order:

| Order | ID  | Name                                                       |
| ----- | --- | ---------------------------------------------------------- |
| 1     | 1   | Bias arising from the randomization process                |
| 2     | 2   | Bias due to deviations from intended interventions         |
| 3     | S   | Bias arising from period and carryover effects (NEW)       |
| 4     | 3   | Bias due to missing outcome data                           |
| 5     | 4   | Bias in measurement of the outcome                         |
| 6     | 5   | Bias in selection of the reported result (4 questions)     |

### 2.1 Domain 1 — Bias arising from the randomization process

Signaling questions:

- **1.1** Was the allocation sequence random?

  *Elaboration:* As for parallel group trials. Answer 'Yes' if a random component was used in the sequence generation process (computer-generated random numbers; random-number table; coin tossing; shuffling cards or envelopes; throwing dice; drawing lots). For cross-over trials the unit of allocation is the sequence of interventions, not the intervention itself.

- **1.2** Was the allocation sequence concealed until participants were enrolled and assigned to intervention sequences?

  *Elaboration:* As for parallel group trials. Answer 'Yes' if remote/central allocation, or opaque, sequentially-numbered, tamper-sealed envelopes were used. Concealment in a cross-over trial applies to the allocated sequence of interventions.

- **1.3** Did baseline differences between intervention groups suggest a problem with the randomization process?

  *Elaboration:* For cross-over trials, baseline imbalance is assessed across the sequence groups (e.g., AB vs BA) at study entry — before the first period begins. Differences compatible with chance do not indicate bias. Answer 'Yes' only if imbalances indicate a problem with the randomization process. Answer 'No information' when no useful sequence-group baseline data are reported.

Decision tree:

```python
def domain1_judge(signals: dict[str, str]) -> str:
    """Domain 1 (randomization process) — cribsheet p.6.

    Inputs: {"1.1": Y/PY/PN/N/NI, "1.2": ..., "1.3": ...}
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

### 2.2 Domain 2 — Bias due to deviations from intended interventions (effect of assignment)

Signaling questions:

- **2.1** Were participants aware of their assigned intervention during the trial?

  *Elaboration:* As for parallel group trials. In a cross-over trial, participants experience both interventions; awareness during each period is relevant for behavioural co-interventions and reporting bias on participant-reported outcomes.

- **2.2** Were carers and people delivering the interventions aware of participants' assigned intervention during the trial?

  *Elaboration:* As for parallel group trials, evaluated separately for each period.

- **2.3** *(If Y/PY/NI to 2.1 or 2.2:)* Were there deviations from the intended intervention that arose because of the trial context?

  *Elaboration:* As for parallel group trials. Examples include unplanned dose adjustments or use of prohibited co-interventions that arose because the trial was being conducted. Per-period adherence may differ between sequence groups; consider both periods.

- **2.4** *(If Y/PY to 2.3:)* Were these deviations likely to have affected the outcome?

  *Elaboration:* As for parallel group trials. Deviations only impact the effect estimate if they affect the outcome.

- **2.5** *(If Y/PY/NI to 2.4:)* Were these deviations from intended intervention balanced between groups?

  *Elaboration:* For cross-over trials, 'balanced between groups' means balanced between sequence groups across periods. Unbalanced trial-context deviations bias the effect estimate.

- **2.6** Was an appropriate analysis used to estimate the effect of assignment to intervention?

  *Elaboration:* For cross-over trials the appropriate analysis is paired (within-participant) and either explicitly models period and/or carryover effects or demonstrates these are not material. Naïve unpaired analyses comparing arms ignore the within-participant structure and are inappropriate when paired data are available.

- **2.7** *(If N/PN/NI to 2.6:)* Was there potential for a substantial impact (on the result) of the failure to analyse participants in the group to which they were randomized?

  *Elaboration:* No precise threshold. In cross-over trials this includes participants dropping out before completing the second period — substantial impact is possible if their period-1 data are mishandled.

Decision tree (Part 1 covers 2.1–2.5; Part 2 covers 2.6–2.7; combined per the cribsheet):

```python
def domain2_judge(signals: dict[str, str]) -> str:
    """Domain 2 (deviations from intended interventions — assignment effect).

    Two parts combined by the criteria on cribsheet p.10:
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
    aware = (not _no(q21)) or (not _no(q22))
    if not aware:
        # Both 2.1 and 2.2 answered N/PN → Low on Part 1
        part1 = "Low"
    else:
        if _no(q23):
            part1 = "Some concerns"
        elif q23 == "NI":
            part1 = "Some concerns"
        else:
            # 2.3 Y/PY → 2.4
            if _no(q24):
                part1 = "Some concerns"
            else:
                # 2.4 Y/PY/NI → 2.5
                part1 = "High" if not _yes(q25) else "Some concerns"

    # Part 2 — 2.6 → 2.7
    if _yes(q26):
        part2 = "Low"
    else:
        part2 = "Some concerns" if _no(q27) else "High"

    if part1 == "High" or part2 == "High":
        return "High"
    if part1 == "Low" and part2 == "Low":
        return "Low"
    return "Some concerns"
```

### 2.3 Domain S — Bias arising from period and carryover effects (NEW)

Signaling questions:

- **S.1** Were carryover effects unlikely to occur in this trial, given the nature of the interventions and the outcome?

  *Elaboration:* Carryover is the persistence of a treatment effect into the subsequent period. Answer 'Yes' if pharmacokinetic/pharmacodynamic reasoning, or the nature of the condition and outcome, make carryover implausible. Answer 'No information' if carryover plausibility cannot be assessed from the report.

- **S.2** If carryover effects could occur, was there a suitable washout period between treatment periods?

  *Elaboration:* A 'suitable' washout is long enough relative to the half-life and pharmacodynamic effect of the interventions for residual effects to dissipate before the next period begins. Answer 'No information' if washout duration is not reported or its adequacy cannot be judged. If S.1 = Y/PY (carryover ruled out), this question can be answered 'NA'-equivalent (answer 'Yes').

- **S.3** For trials with potential for carryover effects, were unbiased data available for the analysis (e.g., from periods unaffected by carryover, or via methods that adjust for carryover)?

  *Elaboration:* Answer 'Yes' if unbiased data are available — for example, only first-period data are used (and there is no other selection bias from doing so), or carryover is statistically modelled. Answer 'No' if carryover is plausible and no adjustment or mitigation was applied. If S.1 = Y/PY, answer 'Yes'.

- **S.4** Were the data analysed using an appropriate paired analysis that takes the cross-over design into account?

  *Elaboration:* An appropriate cross-over analysis uses within-participant comparisons and, where indicated, models period and/or carryover effects. An unpaired analysis comparing period-1 outcomes between sequence groups discards the design's advantages and is generally inappropriate when paired data are available. Linear mixed models with participant random effects and fixed effects for period and treatment are typical.

Decision tree:

```python
def domainS_judge(signals: dict[str, str]) -> str:
    """Domain S (period and carryover effects).

    Inputs: {"S.1": ..., "S.2": ..., "S.3": ..., "S.4": ...}
    Output: "Low" / "Some concerns" / "High".
    """
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

### 2.4 Domain 3 — Bias due to missing outcome data

Signaling questions:

- **3.1** Were data for this outcome available for all, or nearly all, participants randomized?

  *Elaboration:* As for parallel group trials. For cross-over trials, 'available' means data are available for the participant in the period(s) contributing to the analysis. Participants missing data in one period only may still contribute to the analysis depending on the analytical approach.

- **3.2** *(If N/PN/NI to 3.1:)* Is there evidence that the result was not biased by missing outcome data?

  *Elaboration:* As for parallel group trials. Evidence can come from analysis methods correcting for bias, or sensitivity analyses robust to plausible assumptions about missingness.

- **3.3** *(If N/PN to 3.2:)* Could missingness in the outcome depend on its true value?

  *Elaboration:* As for parallel group trials. If loss-to-follow-up or period dropout might be related to participants' health status, missingness could depend on the true outcome value.

- **3.4** *(If Y/PY/NI to 3.3:)* Is it likely that missingness in the outcome depended on its true value?

  *Elaboration:* As for parallel group trials.

Decision tree:

```python
def domain3_judge(signals: dict[str, str]) -> str:
    """Domain 3 (missing outcome data) — cribsheet p.16."""
    q31 = signals.get("3.1", "NI")
    q32 = signals.get("3.2", "NI")
    q33 = signals.get("3.3", "NI")
    q34 = signals.get("3.4", "NI")

    if _yes(q31):
        return "Low"
    if _yes(q32):
        return "Low"
    if _no(q33):
        return "Low"
    if _yes(q34) or q34 == "NI":
        return "High"
    return "Some concerns"
```

### 2.5 Domain 4 — Bias in measurement of the outcome

Signaling questions:

- **4.1** Was the method of measuring the outcome inappropriate?

  *Elaboration:* As for parallel group trials. Methods unsuitable for the outcome of interest may produce systematically biased estimates.

- **4.2** Could measurement or ascertainment of the outcome have differed between intervention groups?

  *Elaboration:* For cross-over trials, this addresses whether measurement could differ between periods or between sequence groups. Identical measurement protocols across periods are expected.

- **4.3** *(If N/PN/NI to 4.1 and 4.2:)* Were outcome assessors aware of the intervention received by study participants?

  *Elaboration:* As for parallel group trials. For participant-reported outcomes, the outcome assessor IS the study participant.

- **4.4** *(If Y/PY/NI to 4.3:)* Could assessment of the outcome have been influenced by knowledge of intervention received?

  *Elaboration:* As for parallel group trials.

- **4.5** *(If Y/PY/NI to 4.4:)* Is it likely that assessment of the outcome was influenced by knowledge of intervention received?

  *Elaboration:* As for parallel group trials.

Decision tree:

```python
def domain4_judge(signals: dict[str, str]) -> str:
    """Domain 4 (measurement of the outcome) — cribsheet p.19."""
    q41 = signals.get("4.1", "NI")
    q42 = signals.get("4.2", "NI")
    q43 = signals.get("4.3", "NI")
    q44 = signals.get("4.4", "NI")
    q45 = signals.get("4.5", "NI")

    if _yes(q41):
        return "High"
    if _yes(q42):
        return "High"

    def _chain() -> str:
        if _no(q43):
            return "Low"
        if _no(q44):
            return "Low"
        if _yes(q45) or q45 == "NI":
            return "High"
        return "Some concerns"

    base = _chain()
    if q42 == "NI" and base == "Low":
        # NI on 4.2 downgrades Low to Some concerns (cribsheet flowchart p.19)
        return "Some concerns"
    return base
```

### 2.6 Domain 5 — Bias in selection of the reported result

Four signaling questions (5.4 is cross-over-specific):

- **5.1** Were the data that produced this result analysed in accordance with a pre-specified analysis plan that was finalized before unblinded outcome data were available for analysis?

  *Elaboration:* As for parallel group trials.

- **5.2** Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible outcome measurements (e.g., scales, definitions, time points) within the outcome domain?

  *Elaboration:* As for parallel group trials.

- **5.3** Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible analyses of the data?

  *Elaboration:* Largely as for parallel group trials. It is possible that trial authors might decide between presenting a paired analysis and an unpaired analysis, or between an analysis that does and does not include a period effect, on the basis of the results. The result of an unpaired analysis will generally be less precise, so a decision to present only an unpaired analysis might reflect a desire to minimize evidence of an intervention effect or to suggest equivalence of interventions. Similarly, including period effects in analysis will generally lead to less a precise intervention effect estimate than an analysis that does not include them.

- **5.4** Is a result based on data from both periods sought, but unavailable on the basis of carryover having been identified?

  *Elaboration:* This question addresses the situation in which only results from the first period are reported on the basis of a test for carryover. Answer 'N' if data from both periods contribute to the result being assessed for risk of bias.

Decision tree:

```python
def domain5_judge(signals: dict[str, str]) -> str:
    """Domain 5 (selection of the reported result) — cross-over cribsheet.

    5.4 is cross-over-specific: Y/PY on 5.4 → High (selective reporting of
    first-period-only data based on a carryover test).
    """
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

---

## 3. Overall RoB aggregation

Same as parallel-group RoB 2 (cribsheet p.24), applied to all six domains:

```python
def overall(domain_judgements: list[str]) -> str:
    """Overall RoB judgement.

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

---

## 4. LLM prompt templates

### 4.1 System prompt (sent on every per-domain call)

```text
You are an evidence-synthesis methodologist assessing risk of bias in a
**cross-over** randomized trial using the Cochrane RoB 2 tool (cross-over
extension). Read the PDF carefully. Answer each signaling question with one
of: Y (yes), PY (probably yes), PN (probably no), N (no), NI (no
information). Provide a 1-2 sentence rationale for each answer, quoting the
paper where possible. Return ONLY a valid JSON object — no preamble, no
markdown fences.
```

### 4.2 Per-domain user prompt template

Built per domain. Variables:

- `{id}` — domain id (1, 2, S, 3, 4, 5)
- `{name}` — full domain name
- `{study_type}` — e.g. "Crossover Trial"
- `{assessed_outcome}` — the outcome being assessed (auto-picked or reviewer-overridden)
- `{ctx_json}` — JSON of pre-extracted fields, or `(no pre-extracted fields)`
- `{questions_block}` — bulleted signaling questions + elaborations (see below)
- `{shape}` — expected JSON shape (see §5)
- `{override_note}` — optional, appended only to Domain 1 when the assessed outcome is a reviewer override

```text
Assess **Domain {id} — {name}** for the **cross-over** trial described in the attached PDF.

Study type: {study_type}
Outcome being assessed: {assessed_outcome}{override_note}

Context (fields already extracted from the paper):
{ctx_json}

Signaling questions:
{questions_block}

Return a JSON object with exactly this shape:
{shape}

Answer N (or PN) when the paper gives enough information to rule out the problem, and NI only when the paper is silent. Rationales must be short (1-2 sentences) and quote the paper verbatim where possible.
```

The optional Domain-1 override note (appended only when the reviewer has overridden the auto-picked outcome):

```text


Note: this assessment is scoped to one specific outcome, selected by the
reviewer. Domain 1 signaling
questions concern the randomization process for the trial as a whole, not
the specific outcome — answer accordingly.
```

The `{questions_block}` is built by joining, for each signaling question:

```text

**{id}. {question_text}**
Elaboration: {elaboration}
Response options: Y/PY/PN/N/NI.
```

(One blank line before each question, signal id and text bold, elaboration on its own line.)

---

## 5. Expected JSON output shape

The model is asked to return JSON with two keys per signaling question (`{sid}` and `{sid}_rationale`) plus one direction-of-bias key. Example for Domain S:

```json
{
  "S.1": "Y|PY|PN|N|NI",
  "S.1_rationale": "1-2 sentences quoting the paper",
  "S.2": "Y|PY|PN|N|NI",
  "S.2_rationale": "1-2 sentences quoting the paper",
  "S.3": "Y|PY|PN|N|NI",
  "S.3_rationale": "1-2 sentences quoting the paper",
  "S.4": "Y|PY|PN|N|NI",
  "S.4_rationale": "1-2 sentences quoting the paper",
  "direction_of_bias": "NA|Favours experimental|Favours comparator|Towards null|Away from null|Unpredictable"
}
```

After parsing, each domain's result is enriched with the local judgement:

```json
{
  "id": "S",
  "name": "Bias arising from period and carryover effects",
  "signals":   {"S.1": "Y", "S.2": "Y", "S.3": "Y", "S.4": "Y"},
  "rationales":{"S.1": "...", "S.2": "...", "S.3": "...", "S.4": "..."},
  "judgement": "Low",
  "direction": "NA"
}
```

The overall trial-level result is then:

```json
{
  "domains": {
    "1": { ... }, "2": { ... }, "S": { ... },
    "3": { ... }, "4": { ... }, "5": { ... }
  },
  "overall_judgement": "Low|Some concerns|High",
  "overall_direction": "NA|Favours experimental|..."
}
```

`overall_direction` is the most common non-`NA` direction across the six domains; ties (or all `NA`) → `Unpredictable` / `NA` respectively.

---

## 6. Sample data — what gets passed to each domain prompt

Each per-domain prompt receives the relevant subset of pre-extracted study fields. Useful relevance hints:

| Domain | Useful pre-extracted fields                                                                                                                 |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| 1      | `randomization_method`, `allocation_concealment`, `allocation_ratio`, `stratification_factors`, `baseline_balance`, `sequence_order`        |
| 2      | `blinding_participants`, `blinding_personnel`, `protocol_deviations`, `analysis_framework`, `missing_data_handling`                         |
| S      | `washout_period`, `carryover_assessment`, `period_effects`, `paired_analysis`                                                               |
| 3      | `attrition_rate`, `missing_data_handling`                                                                                                   |
| 4      | `blinding_outcome_assessors`, `outcome_measurement_method`                                                                                  |
| 5      | `protocol_available`, `outcomes_match_protocol`, `paired_analysis`, `carryover_assessment`                                                  |

If a field is absent or empty, the prompt omits it from the context block. The model is told `(no pre-extracted fields)` when none are present, and is still asked to assess from the PDF directly.

---

## 7. Reference implementation as a single Python file

For a turnkey reference, this is the complete cross-over assessor logic in one file (no platform dependencies). It assumes you provide your own `call_llm(system_prompt, user_prompt, pdf_bytes) -> dict` function.

```python
"""rob2_crossover_assessor.py — reference implementation.

Public API:
    assess_crossover_trial(pdf_bytes, study_type, assessed_outcome,
                            extracted_fields, call_llm,
                            outcome_is_override=False) -> dict

`call_llm` is a callable you provide:
    call_llm(system_prompt: str, user_prompt: str, pdf_bytes: bytes) -> dict
It must return the parsed JSON object the model produced.
"""

import json

SIGNAL_OPTIONS = ("Y", "PY", "PN", "N", "NI")


def _yes(ans):  return ans in ("Y", "PY")
def _no(ans):   return ans in ("N", "PN")


# ── Decision trees ─────────────────────────────────────────────

def domain1_judge(s):
    q11, q12, q13 = (s.get(k, "NI") for k in ("1.1", "1.2", "1.3"))
    if _no(q12): return "High"
    if q12 == "NI":
        return "High" if _yes(q13) else "Some concerns"
    if _no(q11): return "High"
    return "Some concerns" if _yes(q13) else "Low"


def domain2_judge(s):
    q21, q22, q23, q24, q25, q26, q27 = (s.get(k, "NI") for k in
        ("2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7"))
    aware = (not _no(q21)) or (not _no(q22))
    if not aware:
        part1 = "Low"
    elif _no(q23) or q23 == "NI":
        part1 = "Some concerns"
    elif _no(q24):
        part1 = "Some concerns"
    else:
        part1 = "High" if not _yes(q25) else "Some concerns"
    if _yes(q26):
        part2 = "Low"
    else:
        part2 = "Some concerns" if _no(q27) else "High"
    if part1 == "High" or part2 == "High": return "High"
    if part1 == "Low" and part2 == "Low":  return "Low"
    return "Some concerns"


def domainS_judge(s):
    s1, s2, s3, s4 = (s.get(k, "NI") for k in ("S.1", "S.2", "S.3", "S.4"))
    if _yes(s1) and _yes(s4): return "Low"
    if _no(s1) and _no(s2) and _no(s3): return "High"
    if _no(s1) and _no(s4): return "High"
    if _no(s4) and not _yes(s1): return "High"
    return "Some concerns"


def domain3_judge(s):
    q31, q32, q33, q34 = (s.get(k, "NI") for k in ("3.1", "3.2", "3.3", "3.4"))
    if _yes(q31): return "Low"
    if _yes(q32): return "Low"
    if _no(q33):  return "Low"
    if _yes(q34) or q34 == "NI": return "High"
    return "Some concerns"


def domain4_judge(s):
    q41, q42, q43, q44, q45 = (s.get(k, "NI") for k in
        ("4.1", "4.2", "4.3", "4.4", "4.5"))
    if _yes(q41): return "High"
    if _yes(q42): return "High"
    if _no(q43):  base = "Low"
    elif _no(q44): base = "Low"
    elif _yes(q45) or q45 == "NI": base = "High"
    else: base = "Some concerns"
    if q42 == "NI" and base == "Low":
        return "Some concerns"
    return base


def domain5_judge(s):
    q51, q52, q53, q54 = (s.get(k, "NI") for k in ("5.1", "5.2", "5.3", "5.4"))
    if _yes(q52) or _yes(q53) or _yes(q54): return "High"
    if _no(q52) and _no(q53) and _no(q54):
        return "Low" if _yes(q51) else "Some concerns"
    return "Some concerns"


def overall(judgements):
    if any(j == "High" for j in judgements): return "High"
    some = sum(1 for j in judgements if j == "Some concerns")
    if some >= 2: return "High"
    if some >= 1: return "Some concerns"
    return "Low"


# ── Domain definitions ─────────────────────────────────────────
# Each domain: id, name, list of {id, text, elaboration}, and the judge.
# (Question text + elaborations are condensed here; full text is in §2 above.)

DOMAINS = [
    {"id": 1,   "judge": domain1_judge, "relevant_fields": ["randomization_method", "allocation_concealment", "allocation_ratio", "stratification_factors", "baseline_balance", "sequence_order"], "name": "Bias arising from the randomization process",                       "signals": [
        {"id": "1.1", "text": "Was the allocation sequence random?", "elaboration": "<see §2.1>"},
        {"id": "1.2", "text": "Was the allocation sequence concealed until participants were enrolled and assigned to intervention sequences?", "elaboration": "<see §2.1>"},
        {"id": "1.3", "text": "Did baseline differences between intervention groups suggest a problem with the randomization process?", "elaboration": "<see §2.1>"},
    ]},
    {"id": 2,   "judge": domain2_judge, "relevant_fields": ["blinding_participants", "blinding_personnel", "protocol_deviations", "analysis_framework", "missing_data_handling"], "name": "Bias due to deviations from intended interventions (effect of assignment)", "signals": [
        {"id": "2.1", "text": "Were participants aware of their assigned intervention during the trial?", "elaboration": "<see §2.2>"},
        {"id": "2.2", "text": "Were carers and people delivering the interventions aware of participants' assigned intervention during the trial?", "elaboration": "<see §2.2>"},
        {"id": "2.3", "text": "If Y/PY/NI to 2.1 or 2.2: Were there deviations from the intended intervention that arose because of the trial context?", "elaboration": "<see §2.2>"},
        {"id": "2.4", "text": "If Y/PY to 2.3: Were these deviations likely to have affected the outcome?", "elaboration": "<see §2.2>"},
        {"id": "2.5", "text": "If Y/PY/NI to 2.4: Were these deviations from intended intervention balanced between groups?", "elaboration": "<see §2.2>"},
        {"id": "2.6", "text": "Was an appropriate analysis used to estimate the effect of assignment to intervention?", "elaboration": "<see §2.2>"},
        {"id": "2.7", "text": "If N/PN/NI to 2.6: Was there potential for a substantial impact (on the result) of the failure to analyse participants in the group to which they were randomized?", "elaboration": "<see §2.2>"},
    ]},
    {"id": "S", "judge": domainS_judge, "relevant_fields": ["washout_period", "carryover_assessment", "period_effects", "paired_analysis"], "name": "Bias arising from period and carryover effects", "signals": [
        {"id": "S.1", "text": "Were carryover effects unlikely to occur in this trial, given the nature of the interventions and the outcome?", "elaboration": "<see §2.3>"},
        {"id": "S.2", "text": "If carryover effects could occur, was there a suitable washout period between treatment periods?", "elaboration": "<see §2.3>"},
        {"id": "S.3", "text": "For trials with potential for carryover effects, were unbiased data available for the analysis (e.g., from periods unaffected by carryover, or via methods that adjust for carryover)?", "elaboration": "<see §2.3>"},
        {"id": "S.4", "text": "Were the data analysed using an appropriate paired analysis that takes the cross-over design into account?", "elaboration": "<see §2.3>"},
    ]},
    {"id": 3,   "judge": domain3_judge, "relevant_fields": ["attrition_rate", "missing_data_handling"], "name": "Bias due to missing outcome data", "signals": [
        {"id": "3.1", "text": "Were data for this outcome available for all, or nearly all, participants randomized?", "elaboration": "<see §2.4>"},
        {"id": "3.2", "text": "If N/PN/NI to 3.1: Is there evidence that the result was not biased by missing outcome data?", "elaboration": "<see §2.4>"},
        {"id": "3.3", "text": "If N/PN to 3.2: Could missingness in the outcome depend on its true value?", "elaboration": "<see §2.4>"},
        {"id": "3.4", "text": "If Y/PY/NI to 3.3: Is it likely that missingness in the outcome depended on its true value?", "elaboration": "<see §2.4>"},
    ]},
    {"id": 4,   "judge": domain4_judge, "relevant_fields": ["blinding_outcome_assessors", "outcome_measurement_method"], "name": "Bias in measurement of the outcome", "signals": [
        {"id": "4.1", "text": "Was the method of measuring the outcome inappropriate?", "elaboration": "<see §2.5>"},
        {"id": "4.2", "text": "Could measurement or ascertainment of the outcome have differed between intervention groups?", "elaboration": "<see §2.5>"},
        {"id": "4.3", "text": "If N/PN/NI to 4.1 and 4.2: Were outcome assessors aware of the intervention received by study participants?", "elaboration": "<see §2.5>"},
        {"id": "4.4", "text": "If Y/PY/NI to 4.3: Could assessment of the outcome have been influenced by knowledge of intervention received?", "elaboration": "<see §2.5>"},
        {"id": "4.5", "text": "If Y/PY/NI to 4.4: Is it likely that assessment of the outcome was influenced by knowledge of intervention received?", "elaboration": "<see §2.5>"},
    ]},
    {"id": 5,   "judge": domain5_judge, "relevant_fields": ["protocol_available", "outcomes_match_protocol", "paired_analysis", "carryover_assessment"], "name": "Bias in selection of the reported result", "signals": [
        {"id": "5.1", "text": "Were the data that produced this result analysed in accordance with a pre-specified analysis plan that was finalized before unblinded outcome data were available for analysis?", "elaboration": "<see §2.6>"},
        {"id": "5.2", "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible outcome measurements (e.g., scales, definitions, time points) within the outcome domain?", "elaboration": "<see §2.6>"},
        {"id": "5.3", "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible analyses of the data?", "elaboration": "<see §2.6>"},
        {"id": "5.4", "text": "Is a result based on data from both periods sought, but unavailable on the basis of carryover having been identified?", "elaboration": "<see §2.6>"},
    ]},
]


# ── Prompt building ────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing risk of bias in a "
    "**cross-over** randomized trial using the Cochrane RoB 2 tool (cross-over "
    "extension). Read the PDF carefully. Answer each signaling question with "
    "one of: Y (yes), PY (probably yes), PN (probably no), N (no), NI (no "
    "information). Provide a 1-2 sentence rationale for each answer, quoting "
    "the paper where possible. Return ONLY a valid JSON object — no preamble, "
    "no markdown fences."
)

OVERRIDE_NOTE = (
    "\\n\\nNote: this assessment is scoped to one specific outcome, "
    "selected by the reviewer. Domain 1 "
    "signaling questions concern the randomization process for the trial as a "
    "whole, not the specific outcome — answer accordingly."
)


def build_domain_prompt(domain, study_type, assessed_outcome,
                        extracted_fields, outcome_is_override=False):
    relevant = {k: extracted_fields[k]
                for k in domain["relevant_fields"] if extracted_fields.get(k)}
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"
    q_lines = []
    for sig in domain["signals"]:
        q_lines.append(
            f"\n**{sig['id']}. {sig['text']}**\n"
            f"Elaboration: {sig['elaboration']}\n"
            f"Response options: Y/PY/PN/N/NI."
        )
    questions_block = "\n".join(q_lines)
    shape = "{\n"
    for sig in domain["signals"]:
        shape += f'  "{sig["id"]}": "Y|PY|PN|N|NI",\n'
        shape += f'  "{sig["id"]}_rationale": "1-2 sentences quoting the paper",\n'
    shape += '  "direction_of_bias": "NA|Favours experimental|Favours comparator|Towards null|Away from null|Unpredictable"\n'
    shape += "}"
    override_note = OVERRIDE_NOTE if (outcome_is_override and domain["id"] == 1) else ""
    return (
        f"Assess **Domain {domain['id']} — {domain['name']}** for the "
        f"**cross-over** trial described in the attached PDF.\n\n"
        f"Study type: {study_type}\n"
        f"Outcome being assessed: {assessed_outcome}{override_note}\n\n"
        f"Context (fields already extracted from the paper):\n{ctx_json}\n\n"
        f"Signaling questions:\n{questions_block}\n\n"
        f"Return a JSON object with exactly this shape:\n{shape}\n\n"
        f"Answer N (or PN) when the paper gives enough information to rule "
        f"out the problem, and NI only when the paper is silent. Rationales "
        f"must be short (1-2 sentences) and quote the paper verbatim where "
        f"possible."
    )


# ── Per-domain LLM call + parse ────────────────────────────────

def assess_domain(pdf_bytes, domain, study_type, assessed_outcome,
                   extracted_fields, call_llm, outcome_is_override=False):
    prompt = build_domain_prompt(
        domain, study_type, assessed_outcome,
        extracted_fields, outcome_is_override,
    )
    raw = call_llm(SYSTEM_PROMPT, prompt, pdf_bytes)
    signals, rationales = {}, {}
    for sig in domain["signals"]:
        sid = sig["id"]
        ans = str(raw.get(sid, "NI")).strip().upper()
        if ans not in SIGNAL_OPTIONS:
            ans = "NI"
        signals[sid] = ans
        rationales[sid] = str(raw.get(f"{sid}_rationale", "")).strip()
    return {
        "id": domain["id"],
        "name": domain["name"],
        "signals": signals,
        "rationales": rationales,
        "judgement": domain["judge"](signals),
        "direction": str(raw.get("direction_of_bias", "NA")).strip() or "NA",
    }


# ── Top-level entry point ──────────────────────────────────────

def assess_crossover_trial(pdf_bytes, study_type, assessed_outcome,
                            extracted_fields, call_llm,
                            outcome_is_override=False):
    """Run all 6 domains; return per-domain results + overall judgement."""
    domain_results = {}
    for domain in DOMAINS:
        domain_results[str(domain["id"])] = assess_domain(
            pdf_bytes, domain, study_type, assessed_outcome,
            extracted_fields, call_llm, outcome_is_override,
        )
    overall_j = overall([d["judgement"] for d in domain_results.values()])
    # Aggregate direction — most-common non-NA, ties → Unpredictable
    from collections import Counter
    dirs = [d["direction"] for d in domain_results.values()
            if d["direction"] not in ("", "NA")]
    if not dirs:
        overall_d = "NA"
    else:
        counts = Counter(dirs).most_common()
        if len(counts) > 1 and counts[0][1] == counts[1][1]:
            overall_d = "Unpredictable"
        else:
            overall_d = counts[0][0]
    return {
        "domains": domain_results,
        "overall_judgement": overall_j,
        "overall_direction": overall_d,
    }
```

---

## 8. Quick test sketches (no framework — plain `assert`)

```python
# Domain 1 — concealed + random + no baseline issue → Low
assert domain1_judge({"1.1": "Y", "1.2": "Y", "1.3": "N"}) == "Low"

# Domain 1 — concealment not done → High regardless of others
assert domain1_judge({"1.1": "Y", "1.2": "N", "1.3": "N"}) == "High"

# Domain S — carryover ruled out + paired analysis → Low
assert domainS_judge({"S.1": "Y", "S.2": "NI", "S.3": "NI", "S.4": "Y"}) == "Low"

# Domain S — carryover plausible + no washout + no unbiased data → High
assert domainS_judge({"S.1": "N", "S.2": "N", "S.3": "N", "S.4": "Y"}) == "High"

# Domain 5 — 5.4 Y/PY (first-period-only on carryover test) → High
assert domain5_judge({"5.1": "Y", "5.2": "N", "5.3": "N", "5.4": "Y"}) == "High"

# Domain 5 — 5.4 N + 5.1 Y + 5.2/5.3 N → Low (matches parallel-group)
assert domain5_judge({"5.1": "Y", "5.2": "N", "5.3": "N", "5.4": "N"}) == "Low"

# Overall — all six Low → Low
assert overall(["Low"] * 6) == "Low"

# Overall — two Some concerns → High
assert overall(["Low", "Low", "Some concerns", "Low", "Some concerns", "Low"]) == "High"
```

---

## 9. Implementation notes for other platforms

- **PDF as document attachment.** Each per-domain call sends the full paper as a PDF document, plus the system prompt and the per-domain user prompt. Most LLM providers accept PDFs as a document content block (or equivalent file/PDF attachment). Smaller models may need a PDF→text fallback if the document exceeds the context window — the dispatch logic and the expected model output shape don't change.

- **Per-domain calls vs. one big call.** We make one LLM call per domain (six total) rather than a single mega-call. This keeps each prompt focused, lets us re-try a single domain on parse errors, and avoids hitting context limits on long PDFs.

- **Where the "assessed outcome" comes from.** Pick the primary outcome from the paper's extracted fields by trying, in order: `primary_outcome_definition` → `primary_outcome_measurement` → `population_outcomes`. Trim to ~200 characters for prompt-header compactness. If your platform supports a reviewer override (e.g., the user picks a secondary outcome because the primary is unclear), pass `outcome_is_override=True` so Domain 1 gets a clarifying note (§4.2).

- **Field-extraction is upstream.** The decision-tree code makes no assumption about how `extracted_fields` is produced — it's an opaque dict you build elsewhere (manual annotation, prior LLM pass, structured-field extractor, etc.). The relevant subset is filtered per-domain via `domain["relevant_fields"]`.

- **Domain S sometimes legitimately N/A.** A trial where the intervention can be shown pharmacologically to have no carryover (e.g., a single dose with rapid metabolism) may make S.2/S.3 vacuously satisfied. We treat that as `S.1 = Y` with `S.4 = Y` → Low. Don't second-guess the model — if the paper provides reasoning, it'll surface in the rationale.

- **5.4's evidence requirement.** Be strict: 5.4 should only be `Y/PY` if the paper explicitly states that first-period-only data are being reported on the basis of a carryover test. Decisions to use first-period data for other reasons (e.g., pre-specified primary analysis) are not 5.4 concerns.

---

## 10. Reporting-guideline companion — CONSORT cross-over

Cross-over trials should also be assessed against the **CONSORT cross-over extension** (Dwan, Li, Altman, Elbourne 2019, BMJ 366: l4378) in addition to the base CONSORT 2025 checklist. Sixteen extension items cover cross-over-specific reporting: identification as a cross-over trial; description of periods + sequences; washout duration and rationale; carryover considerations; period effects; per-period outcomes; paired sample-size calculation; sequence randomization; paired statistical analysis methods; pre-specified first-period contingency; flow diagram by period and sequence; period-specific losses; baseline data by sequence; period-specific outcome data; paired effect estimates with 95% CI; carryover test results.

Implementation pattern: one LLM call per paper, asked to judge each combined (base + extension) item as adhered (`true`), not adhered (`false`), or not applicable (`null`). The proportion reported is `adhered / applicable` (the `null` items are excluded from both numerator and denominator).
