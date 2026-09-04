# ROBINS-I V2 — Sharable Methodology Reference

A self-contained reference for implementing an automated ROBINS-I V2 risk-of-bias assessment of non-randomized studies of interventions. Contains:

- Signaling questions (verbatim from the 20 November 2025 cribsheet) for all 6 domains × 3 variants
- Decision-tree logic as plain Python (no framework / database / HTTP dependencies)
- LLM prompt templates (the exact strings sent to the model) — preflight + per-domain
- Expected JSON output shapes
- Overall worst-domain aggregation algorithm
- A turnkey single-file reference implementation

**Sources:**

- ROBINS-I V2 cribsheet (20 November 2025). ROBINS-I V2 development group: Sterne JA, Brandt Mathur M, Elbers R, Hróbjartsson A, McAleenan A, Reeves B, Shrier I, Tilling K, Armstrong R, Berkman N, Boutron I, Carpenter J, Chan AW, Deeks J, Golder S, Henry D, Jüni P, Kirkham J, Konstantinidis M, Lasserson T, Loke Y, McGuinness L, Page M, Savović J, Shea B, Mawdsley D, Shepperd S, Tugwell P, Valentine J, Viswanathan M, Waddington HS, Wells G, Hernán M, Higgins J.

**Scope:** ROBINS-I V2 (cohort variants A/B + single-arm adaptation). V2 is published explicitly for follow-up (cohort) studies. The single-arm adaptation is a project-specific extension for uncontrolled designs (Single-Arm Trial, Dose-Escalation Study). Out of scope: ROBINS-I V1 (the retired "Bias due to deviations from intended interventions" domain has been folded into Domain 1 Variant B); ROB 2 (which is for RCTs, separate tool); systematic-review assessment (AMSTAR-2, separate tool); QUADAS-2 / QUADAS-3 (for diagnostic test accuracy, separate tools).

**Conservative-tree note.** The decision trees below take a conservative interpretation of the cribsheet's narrative scoring guidance — keeping the logic pure and inspectable. Per-signal rationales (returned by the LLM with each answer) are preserved so reviewers can override the algorithmic judgement during write-up.

**Domain 1 "Low" labels.** Confounding cannot be eliminated in observational studies; the best achievable confidence at Domain 1 is therefore not plain "Low" but a labelled variant:

- Cohort variants A and B: `"Low (except for concerns about uncontrolled confounding)"` (per cribsheet footnote p4)
- Single-arm variant: `"Low (except for concerns about uncontrolled benchmarking)"` (single-arm has no comparator, so what remains is residual uncertainty about benchmark/external-control adequacy)

The overall worst-domain aggregator normalizes both labels back to plain Low for ranking purposes — the labelled forms surface in the per-domain detail view.

**Cascade enforcement is in Python, not the LLM.** V2 has cascading signaling questions in Domains 1B, 1-single-arm, 2 (cohort), 3, 4, and 5 — where one question's answer determines whether a downstream question is asked (NA). These cascade rules are deterministic from the cribsheet, so this implementation enforces them in pure Python (`enforce_cascade_dN_v2` functions in §13) AFTER the LLM has answered. The LLM is asked to answer every signaling question based on its reading of the paper; Python then overrides any answer to `NA` for questions the cribsheet's cascade rules indicate are gated out. This (1) prevents the LLM from incorrectly answering a substantive question when the cascade says NA, (2) catches LLM inconsistency between gating-question and downstream-question answers, and (3) ensures the same paper always produces the same cascade structure regardless of LLM stochasticity. See §17 for the design rationale and per-domain cascade rules.

---

**Assessment scope: one assessment per (study × outcome).** This instrument rates a *result*, not a paper. Several of its signalling questions — missing outcome data, measurement of the outcome, and selection of the reported result — are answered differently for different outcomes in the same study, so one trial can be *Low* for all-cause mortality and *High* for an unblinded symptom score. Run the whole instrument once per outcome you intend to report, passing that outcome as the assessed outcome, and store one judgement per (study × outcome). Reusing a single paper-level judgement across every outcome attaches a rating to outcomes it was never made about, and nothing in the output reveals that it happened. Only the instrument call repeats: classification and field extraction that feed the prompts are outcome-independent and run once per study.

## 1. Signal answer options

ROBINS-I V2 uses a richer signal vocabulary than QUADAS-2 or RoB 2. The universal 5 tokens are:

```python
# Universal — accepted by every question
# Y  = Yes
# PY = Probably yes
# PN = Probably no
# N  = No
# NI = No information
```

Some confounding and missing-data questions additionally accept **WN** (weak no) and **SN** (strong no); some misclassification and measurement questions additionally accept **WY** (weak yes) and **SY** (strong yes). A few questions also accept **NA** (not applicable) when the question is gated on a prior answer.

The complete legal token set:

```python
SIGNAL_OPTIONS_ALL = ("Y", "PY", "PN", "N", "NI", "WN", "SN", "WY", "SY")
```

Per-question option subsets used in this implementation:

```python
_BASIC          = ("Y", "PY", "PN", "N", "NI")
_BASIC_NA       = ("NA", "Y", "PY", "PN", "N", "NI")
_WITH_WN_SN     = ("Y", "PY", "WN", "SN", "NI")
_NA_WITH_WN_SN  = ("NA", "Y", "PY", "WN", "SN", "NI")
_WITH_WY_SY     = ("Y", "PY", "WY", "SY", "PN", "N", "NI")
_DIFFERENTIAL   = ("SY", "WY", "PN", "N", "NI")
_NA_DIFFERENTIAL = ("NA", "SY", "WY", "PN", "N", "NI")
```

Each signaling question declares its legal subset; the decision trees expect one of those tokens.

Helper predicates used by the decision trees:

```python
def _yes(ans: str) -> bool:
    """Truthy: any 'yes-flavoured' answer including weak / strong yes."""
    return ans in ("Y", "PY", "WY", "SY")


def _no(ans: str) -> bool:
    """Truthy: any 'no-flavoured' answer including weak / strong no."""
    return ans in ("N", "PN", "WN", "SN")


def _strict_yes(ans: str) -> bool:
    return ans in ("Y", "PY")


def _strict_no(ans: str) -> bool:
    return ans in ("N", "PN")


def _weak_no(ans: str) -> bool:
    return ans == "WN"


def _strong_no(ans: str) -> bool:
    return ans == "SN"


def _weak_yes(ans: str) -> bool:
    return ans == "WY"


def _strong_yes(ans: str) -> bool:
    return ans == "SY"


def _no_info(ans: str) -> bool:
    return ans == "NI"
```

**Domain judgement scale (4-level):**

```python
JUDGEMENTS = ("Low", "Moderate", "Serious", "Critical")
```

V1's separate "No information" judgement was retired in V2 — `NI` is still a valid signal answer, but the decision trees route NI through to a normal judgement rather than producing a distinct "No information" outcome.

**Domain 1 variant-labeled "Low" constants:**

```python
LOW_D1 = "Low (except for concerns about uncontrolled confounding)"
LOW_D1_SA = "Low (except for concerns about uncontrolled benchmarking)"
```

**Single-arm study types** (drive Domain 1/2 variant routing — see §2):

```python
SINGLE_ARM_STUDY_TYPES = frozenset({"Single-Arm Trial", "Dose-Escalation Study"})
```

---

## 2. Variant routing + Preflight

ROBINS-I V2 has three Domain 1 variants:

| Variant | Study type at run() entry | D1 questions | D2 questions | Selected by |
|--|--|--|--|--|
| **Cohort A** (ITT effect, baseline confounding only) | Cohort / Case-Control / Cross-Sectional / Case-Crossover / Non-Randomized Trial | 4 (1A.1–1A.4) | 5 (2.1–2.5) | Preflight C4 = "No" (ITT) |
| **Cohort B** (per-protocol effect, baseline + time-varying confounding) | Cohort / Case-Control / Cross-Sectional / Case-Crossover / Non-Randomized Trial | 5 (1B.1–1B.5) | 5 (2.1–2.5) | Preflight C4 = "Yes" (per-protocol) |
| **Single-arm** (adapted for uncontrolled designs — no comparator) | Single-Arm Trial / Dose-Escalation Study | 5 (1S.1–1S.5) | 3 (2S.1–2S.3) | `classification["study_type"]` matches `SINGLE_ARM_STUDY_TYPES` BEFORE preflight runs |

**Critical:** The single-arm variant is pinned at `run()` entry from the study type — **not** preflight-determined. C4 is still asked in the single-arm preflight (for metadata about whether the analysis is ITT-like or per-protocol-like, which informs D2-SA question 2S.3) but does NOT swap variants for uncontrolled designs.

Domains 3–6 (selection, missing data, measurement, reporting) are invariant across all three variants.

### 2.1 Preflight system prompt

Preflight uses the same system prompt as the per-domain calls:

```text
You are an evidence-synthesis methodologist assessing risk of bias in a non-randomized study of an intervention using the Cochrane ROBINS-I V2 tool (20 November 2025 cribsheet). Read the PDF carefully. Answer each signaling question with one of the allowed tokens for that question — Y (yes), PY (probably yes), PN (probably no), N (no), NI (no information), and where indicated WN (weak no), SN (strong no), WY (weak yes), SY (strong yes). Provide a 1-2 sentence rationale for each answer, quoting the paper where possible. Return ONLY a valid JSON object — no preamble, no markdown fences.
```

### 2.2 Preflight prompt — cohort (Variants A/B routing via C4)

```text
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
```

### 2.3 Preflight prompt — single-arm (B1/B2 replaced by benchmark-pre-specification questions)

```text
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
```

### 2.4 Preflight dispatcher (pure Python)

The short-circuit logic and variant routing:

```python
def run_preflight_dispatch(preflight_answers: dict, study_type: str) -> dict:
    """Given the LLM's preflight answers + the study type, decide:
       (a) whether to short-circuit to Critical, and
       (b) which Domain 1 variant to use.

    Returns {screening_decision, variant, [screening_reason]}.
    """
    b2 = preflight_answers.get("B2", "NA")
    b3 = preflight_answers.get("B3", "NI")
    c4 = preflight_answers.get("C4", "No")

    is_single_arm = study_type in SINGLE_ARM_STUDY_TYPES
    variant = "single_arm" if is_single_arm else ("B" if c4 == "Yes" else "A")

    # Screening short-circuits: B2 or B3 = Y/PY → Critical, skip all 6 domains.
    if b2 in ("Y", "PY"):
        reason = (
            "B2-SA: Absence of any pre-specified benchmark is severe enough "
            "that the single-arm proportion is uninterpretable."
            if is_single_arm
            else "B2: Sufficient potential for confounding that the unadjusted "
                 "result should not be considered further."
        )
        return {"screening_decision": "critical", "variant": variant, "screening_reason": reason}
    if b3 in ("Y", "PY"):
        return {
            "screening_decision": "critical",
            "variant": variant,
            "screening_reason": "B3: The method of measuring the outcome is inappropriate.",
        }
    return {"screening_decision": "proceed", "variant": variant, "screening_reason": ""}
```

---

## 3. Domain 1 — Bias due to confounding

Three variants. Each transcribes the cribsheet's signaling questions verbatim and includes the per-variant pure-Python decision tree.

### 3.1 Variant A (ITT effect, baseline confounding only)

Cribsheet p20. Selected when C4 = "No" for cohort studies (the analysis estimates the intention-to-treat effect).

Signaling questions:

- **1A.1** Did the authors control for all the important confounding factors for which this was necessary?

  Response options: Y / PY / WN / SN / NI

  *Elaboration:* Answer Y/PY if all important confounding factors identified in the preliminary consideration were appropriately controlled for (stratification, regression, matching, standardization, propensity scores, IPTW). Answer WN if most were controlled and uncontrolled confounding was probably not substantial. Answer SN if at least one important confounder should have been controlled but was not, and the failure is likely to have a material impact.

- **1A.2** Were confounding factors that were controlled for (and for which control was necessary) measured validly and reliably by the variables available in this study?

  Response options: NA / Y / PY / WN / SN / NI

  *Elaboration:* Adjustment helps only if confounders were measured well. Answer WN if measurement error was probably not substantial; SN if there was at least one important confounder measured poorly enough that the extent of measurement error in confounders was probably substantial.

- **1A.3** Did the authors control for any post-intervention variables that could have been affected by the intervention?

  Response options: NA / Y / PY / PN / N / NI

  *Elaboration:* Controlling for variables on the causal pathway between intervention and outcome (over-adjustment) biases the effect estimate. Classic example: adjusting for a biomarker that the intervention changes.

- **1A.4** Did the use of negative controls, quantitative bias analysis, or other considerations suggest serious uncontrolled confounding?

  Response options: Y / PY / PN / N

  *Elaboration:* If the study did not use negative controls and no other considerations suggest uncontrolled confounding, answer N. Answer Y/PY if negative controls indicate the result being assessed suffers from material bias due to confounding.

Decision tree:

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
            # 1.4 No: severity depends on 1.2 validity
            if _strict_yes(q2):
                return "Serious"
            return "Critical"
        # 1.3 N/PN/NI: no over-adjustment
        if _strict_yes(q2) or _weak_no(q2):
            return "Serious" if _yes(q4) else LOW_D1
        # 1.2 SN/NI: validity concerns plus possibly uncontrolled confounding
        return "Serious"

    # 1.1 WN: most-but-not-all controlled (floor is Moderate, not Low)
    if _weak_no(q1):
        if _yes(q3):
            if _yes(q4):
                return "Critical"
            if _strict_yes(q2):
                return "Serious"
            return "Critical"
        # 1.3 N/PN/NI
        if _strict_yes(q2) or _weak_no(q2):
            return "Serious" if _yes(q4) else "Moderate"
        return "Serious"

    # Fallthrough — shouldn't be reachable with a valid signal token
    return "Serious"
```

### 3.2 Variant B (per-protocol effect, baseline + time-varying confounding)

Cribsheet p24. Selected when C4 = "Yes" for cohort studies (the analysis estimates the per-protocol effect). Adds time-varying confounding considerations.

Signaling questions:

- **1B.1** Did the authors use an analysis method that was appropriate to control for time-varying as well as baseline confounding?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* Appropriate methods to control for time-varying confounding ('g-methods') include inverse probability weighting based on baseline- and time-varying confounding factors, with adjustment for the censoring weights. Standard regression models including time-varying confounders may be problematic when those confounders are affected by prior intervention (treatment-confounder feedback).

- **1B.2** Did the authors control for all the important baseline and time-varying confounding factors for which this was necessary?

  Response options: NA / Y / PY / WN / SN / NI

  *Elaboration:* Per-protocol analyses must control for both baseline and time-varying confounding factors that predict changes to intervention received. Same WN / SN semantics as Variant A 1.1.

- **1B.3** Were confounding factors that were controlled for measured validly and reliably by the variables available in this study?

  Response options: NA / Y / PY / WN / SN / NI

  *Elaboration:* Same measurement-validity question as Variant A 1.2 but applied to baseline + time-varying confounders.

- **1B.4** Did the authors control for time-varying factors or other variables measured after the start of intervention?

  Response options: NA / Y / PY / PN / N / NI

  *Elaboration:* Asked when an inappropriate analysis method (1B.1 N/PN/NI) has been used. Conditioning on time-varying factors measured after the start of intervention is likely to lead to bias when those factors are also on the causal pathway from intervention to outcome.

- **1B.5** Did the use of negative controls, or other considerations, suggest serious uncontrolled confounding?

  Response options: Y / PY / PN / N

  *Elaboration:* Same as Variant A 1.4.

Decision tree:

```python
def domain1_variant_b_judge(signals: dict[str, str]) -> str:
    """D1 Variant B (per-protocol effect, baseline + time-varying). Cribsheet p24."""
    q1 = signals.get("1B.1", "NI")
    q2 = signals.get("1B.2", "NI")
    q3 = signals.get("1B.3", "NI")
    q4 = signals.get("1B.4", "NI")
    q5 = signals.get("1B.5", "NI")

    # 1.1 N/PN/NI: wrong analysis method (e.g. plain regression with time-varying
    #               confounders) — bias risk dominated by 1.4 and 1.5
    if _strict_no(q1) or _no_info(q1):
        if _yes(q4):
            return "Critical"
        return "Critical" if _yes(q5) else "Serious"

    # 1.1 Y/PY: appropriate g-methods etc. used
    if _strict_yes(q1):
        # 1.2: all important factors controlled?
        if _strict_yes(q2):
            # 1.3: validity of measurement
            if _strict_yes(q3) or _weak_no(q3):
                return "Serious" if _yes(q5) else LOW_D1
            return "Serious"  # SN / NI on 1.3
        if _weak_no(q2):
            if _strict_yes(q3) or _weak_no(q3):
                return "Serious" if _yes(q5) else "Moderate"
            return "Serious"
        # 1.2 SN/NI: not all important factors controlled
        return "Critical" if _yes(q5) else "Serious"

    return "Serious"
```

### 3.3 Variant single-arm (uncontrolled / single-arm design — no comparator)

Adapts the V2 cribsheet's confounding logic to the single-arm context. Classical confounding-by-indication is N/A (no comparator), so the domain instead assesses (1) **benchmark adequacy** — was the implied comparison (historical control rate, performance criterion, or null hypothesis decision rule) pre-specified before data collection, and is it reasonable for the population? — and (2) **prognostic-mix comparability** — is the cohort's measured baseline prognostic profile comparable to the benchmark's population, and did authors address residual differences? 1S.5 (negative / falsification controls / external-validity considerations) serves the same Critical-elevating role as 1A.4 / 1B.5 in the cohort variants.

Signaling questions:

- **1S.1** Was the implied benchmark (historical control rate, pre-specified performance criterion, or null hypothesis with a quantitative decision rule) pre-specified before data collection?

  Response options: Y / PY / PN / N

  *Elaboration:* Single-arm trials have no internal comparator. They are interpreted against an implicit benchmark — usually a historical-control response rate, a regulatory performance criterion (e.g. ORR > 30% to support accelerated approval), or a null hypothesis with a pre-specified statistical decision rule (e.g. Simon's two-stage design). Answer Y/PY if a numeric benchmark + decision rule was clearly stated in the protocol / SAP / methods, BEFORE the data were collected. Answer N/PN if no benchmark is identifiable, or if the benchmark looks chosen post-hoc to match the observed result.

- **1S.2** Is the implied benchmark reasonable given current standard of care and the patient population being studied?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* A pre-specified benchmark is only useful if it reflects a clinically meaningful threshold for this population. Answer Y/PY if the benchmark is consistent with contemporary published control-arm rates in comparable patients (similar disease stage, prior therapy, biomarker status). Answer N/PN if the benchmark is implausibly low (inflates apparent benefit) or implausibly high (forces a near-impossible bar). NI if no contemporary comparable estimate exists.

- **1S.3** Is the cohort's measured baseline prognostic profile (stage, prior lines, ECOG / performance status, biomarker status, key comorbidities) comparable to that of the benchmark population?

  Response options: NA / Y / PY / WN / SN / NI

  *Elaboration:* The single-arm proportion is biased upward if the enrolled cohort is more prognostically favourable than the benchmark population (e.g. younger, less heavily pre-treated, biomarker-enriched). Answer Y/PY when measured baseline prognostic factors are comparable. WN when most-but-not-all prognostic factors look comparable. SN when at least one important prognostic factor is materially more favourable in this cohort. NA only when no benchmark was identified at 1S.1.

- **1S.4** Did the authors address residual prognostic-mix differences quantitatively (sensitivity analyses, propensity-score adjustment to external controls, prognostic-score stratification, or similar)?

  Response options: NA / Y / PY / PN / N / NI

  *Elaboration:* Even when 1S.3 raises concerns, quantitative external-control adjustment can rescue interpretability. Examples include propensity-score weighting against an external real-world cohort, prognostic-score stratification, MAIC, or pre-specified sensitivity analyses showing the conclusion is robust to plausible prognostic differences. Answer Y/PY when such methods were used and reported. N/PN when not addressed.

- **1S.5** Do negative / falsification controls, external-validity considerations, or other quantitative bias analyses suggest serious uncontrolled selection-prognostic bias?

  Response options: Y / PY / PN / N

  *Elaboration:* Analogous to 1A.4 / 1B.5 in the cohort variants. Answer Y/PY if a falsification analysis (e.g. testing the intervention against an outcome it shouldn't affect) suggested residual bias, or if external-validity checks revealed serious cohort-vs-benchmark mismatch. Answer N if no falsification analysis was performed and no other consideration suggests substantial uncontrolled bias — this is the typical answer.

Decision tree:

```python
def domain1_variant_single_arm_judge(signals: dict[str, str]) -> str:
    """D1 Variant single_arm (uncontrolled / single-arm design — no comparator).

    Returns Low (with the LOW_D1_SA label), Moderate, Serious, or Critical.
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
        # 1S.4 N/PN: no quantitative adjustment either → Critical
        if _strict_no(q4):
            return "Critical"
        return "Serious"

    if _no_info(q1):
        return "Serious"

    # 1S.1 Y/PY: benchmark pre-specified
    if _strict_yes(q1):
        # 1S.3 prognostic comparability
        if _strict_yes(q3):
            # 1S.2 (benchmark reasonable) decides Low vs Moderate
            if _strict_yes(q2):
                return LOW_D1_SA
            return "Moderate"
        if _weak_no(q3):
            # Most-but-not-all prognostic match — Moderate floor
            return "Moderate"
        if _strong_no(q3) or _no_info(q3):
            # Substantial mismatch — 1S.4 (quantitative adjustment) can rescue
            if _strict_yes(q4):
                return "Moderate"
            return "Serious"

    return "Serious"
```

---

## 4. Domain 2 — Bias in classification of interventions

Two variants. Cohort (Variants A and B share the same 5 questions) and single-arm (3 questions).

### 4.1 Cohort variant (Variants A and B)

Cribsheet p28.

Signaling questions:

- **2.1** Were the intervention strategies distinguishable at the time when follow-up would have started in the target trial?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* In most non-randomized studies, participants are classified to intervention strategies based on information about interventions prescribed or received. Some strategies (e.g. 'surgery within 6 months of diagnosis' vs 'delay surgery until clinical progression') cannot be distinguished at follow-up start, creating a period of 'immortal time' during which the outcome cannot occur for some groups.

- **2.2** Did all or nearly all outcome events occur after the intervention and comparator strategies could be distinguished?

  Response options: NA / Y / PY / PN / N / NI

  *Elaboration:* Asked only if 2.1 was N/PN/NI. If the indistinguishable period is short relative to total follow-up, the proportion of outcome events during that period may be low and the misclassification bias correspondingly small.

- **2.3** Did the analysis avoid problems arising from intervention strategies that are not distinguishable at the start of follow-up?

  Response options: NA / SY / WY / PN / N / NI

  *Elaboration:* Answer SY (strong yes, fully) if predictors of treatment during follow-up were measured and used appropriately to derive inverse-probability weights (e.g. clone-censor-weighting, g-formula), or if the study used a 'landmark' analysis. WY (partially) if appropriate but unlikely to have fully adjusted for prognostic factors predicting treatment after start of follow-up.

- **2.4** Was classification of intervention status influenced by knowledge of the outcome or risk of the outcome?

  Response options: SY / WY / PN / N / NI

  *Elaboration:* Differential misclassification arises when the outcome (or its causes, other than the intervention) influences how interventions are classified. SY = yes, and the impact was substantial; WY = yes, but the impact was not substantial.

- **2.5** Were further classification errors (not influenced by knowledge of the outcome or risk of the outcome) likely?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* Non-differential misclassification — receipt of intervention not recorded for some participants. Usually biases towards the null. 'Nearly all' should be interpreted as 'enough to be confident of the findings'.

Decision tree:

```python
def domain2_judge(signals: dict[str, str]) -> str:
    """D2 Bias in classification of interventions. Cribsheet p28."""
    q1 = signals.get("2.1", "NI")
    q2 = signals.get("2.2", "NI")
    q3 = signals.get("2.3", "NI")
    q4 = signals.get("2.4", "NI")
    q5 = signals.get("2.5", "NI")

    # Upstream tier: 2.1 → 2.2 → 2.3 cascade decides which matrix row to use
    if _yes(q1) or _yes(q2):
        tier = 0  # best — top matrix
    elif _strong_yes(q3):
        tier = 1  # middle matrix (per cribsheet: SY routes to middle 2.4)
    elif _weak_yes(q3) or _no_info(q3):
        tier = 1
    else:
        tier = 2  # worst — 2.3 N/PN: analysis did not address non-distinguishable strategies

    # 2.4 differential misclassification bump
    if _strict_no(q4):
        bump4 = 0
    elif _weak_yes(q4) or _no_info(q4):
        bump4 = 1
    elif _strong_yes(q4):
        bump4 = 2
    else:
        bump4 = 1  # defensive fallback

    # 2.5 non-differential misclassification bump
    if _strict_no(q5):
        bump5 = 0
    else:  # Y / PY / NI all bump
        bump5 = 1

    # Tier 2 has a direct CRITICAL for 2.4 SY/WY/NI per the diagram
    if tier == 2 and (_yes(q4) or _no_info(q4)):
        return "Critical"

    idx = min(tier + bump4 + bump5, 3)
    return JUDGEMENTS[idx]
```

### 4.2 Single-arm variant (degenerate classification — only one intervention)

With no comparator, classical differential misclassification by group is meaningless. What remains is whether the intervention was well-defined, whether dose modifications / discontinuations were recorded, and crucially whether the analyzed cohort was defined by *intended* treatment (ITT-like, low risk) or *received* treatment (per-protocol-like — risks selection bias toward responders).

Signaling questions:

- **2S.1** Was the intervention well-defined (dose, schedule, duration, dose-modifications protocol) at the start of follow-up?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* In a single-arm trial there is no comparator misclassification, but the single intervention must be specified precisely enough that the reported result corresponds to a reproducible regimen. Answer Y/PY when dose, schedule, duration, and dose-modification rules (reductions, holds, criteria for discontinuation) are fully reported. Answer N/PN when the intervention is described only at high level (e.g. 'standard chemotherapy').

- **2S.2** Were dose reductions, holds, and discontinuations recorded and reported?

  Response options: Y / PY / WN / SN / NI

  *Elaboration:* Recording of treatment delivery is essential for interpreting the single-arm result. WN if most exposure modifications were recorded; SN if material exposure detail is missing such that the analyzed 'intervention' is effectively undefined.

- **2S.3** Was the analyzed cohort defined by intended treatment (everyone enrolled, ITT-like) or by received treatment (only those completing ≥X cycles / responding to treatment)?

  Response options: SY / WY / PN / N / NI

  *Elaboration:* Defining the analyzed cohort by *received* treatment (per-protocol completers, 'evaluable population') selects for patients who tolerated the intervention well enough to keep receiving it — a strong selection toward responders that inflates the single-arm proportion. Answer SY (strong yes) when the primary analysis is explicitly restricted to completers or responders. WY (weak yes) when the analyzed cohort excludes some enrolled patients for treatment-related reasons but not dominantly. Answer N/PN when all enrolled (or all who received any dose of intervention — modified ITT) are analyzed.

Decision tree:

```python
def domain2_variant_single_arm_judge(signals: dict[str, str]) -> str:
    """D2 Variant single_arm — degenerate classification (only one intervention)."""
    q1 = signals.get("2S.1", "NI")
    q2 = signals.get("2S.2", "NI")
    q3 = signals.get("2S.3", "NI")

    # 2S.3 dominates: a "strong yes" to received-treatment-definition
    # filtering = selection-on-completers bias substantial → Critical
    if _strong_yes(q3):
        return "Critical"
    if _weak_yes(q3) or _no_info(q3):
        # Some / unclear filtering — Serious unless intervention definition
        # is solid (which doesn't really rescue it)
        return "Serious"

    # q3 in (PN, N): cohort defined by intended treatment → low concern here
    if _strict_yes(q1):
        # Well-defined intervention. 2S.2 (recording fidelity) decides.
        if _strict_yes(q2):
            return "Low"
        if _weak_no(q2):
            return "Moderate"
        if _strong_no(q2):
            return "Serious"
        # NI on 2S.2 — measurement-fidelity uncertain
        return "Moderate"

    # 2S.1 N/PN/NI: intervention definition unclear
    if _strict_no(q1):
        return "Serious"
    return "Moderate"  # NI on 2S.1
```

---

## 5. Domain 3 — Bias in selection of participants into the study (or analysis)

Cribsheet p32. Invariant across all three variants.

Signaling questions:

- **3.1** Did follow-up in the analysis begin at the start of the intervention strategies being compared?

  Response options: Y / PY / WN / SN / NI

  *Elaboration:* A. Prevalent-user bias and immortal time. Answer Y/PY if all outcome events and follow-up time after the start of the interventions were included in the analysis. WN if not substantial; SN if leading to a substantial risk of bias.

- **3.2** Were outcome events during a period of follow-up after the start of the interventions excluded from the analysis?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* Only asked if 3.1 was Y/PY. Such exclusion creates 'immortal time' during which events cannot occur and biases the effect estimate.

- **3.3** Was selection of participants into the study (or into the analysis) based on participant characteristics observed after the start of intervention, additional to the situations addressed in 3.1 and 3.2?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* B. Other selection bias. Answer Y/PY if selection into the study was based on post-intervention characteristics. N/PN if selection was based only on pre-intervention characteristics — baseline confounding is addressed in Domain 1, not here.

- **3.4** Were the post-intervention variables that influenced selection likely to be associated with intervention?

  Response options: NA / Y / PY / PN / N / NI

  *Elaboration:* Only asked if 3.3 was Y/PY. Selection bias occurs when selection is related to an effect of either intervention or a cause of intervention AND an effect of either the outcome or a cause of the outcome.

- **3.5** Were the post-intervention variables that influenced selection likely to be influenced by the outcome or a cause of the outcome?

  Response options: NA / Y / PY / PN / N / NI

  *Elaboration:* Only asked if 3.4 was Y/PY. Collider-style selection bias.

- **3.6** Is it likely that the analysis corrected for all of the potential selection biases identified above?

  Response options: NA / Y / PY / PN / N / NI

  *Elaboration:* C. Analysis / sensitivity / severity. Only asked if A or B raised concerns. Inverse probability weights can create a pseudo-population without the selection bias if assumptions are justified.

- **3.7** Did sensitivity analyses demonstrate that the likely impact of the potential selection biases identified above was minimal?

  Response options: NA / Y / PY / PN / N / NI

  *Elaboration:* Only asked if 3.6 was N/PN/NI.

- **3.8** Were potential selection biases identified above sufficiently severe that the result should not be included in a quantitative synthesis?

  Response options: NA / Y / PY / PN / N / NI

  *Elaboration:* Distinguishes 'Serious' from 'Critical' risk of selection bias. Answer N/PN/NI unless there is clear evidence that the selection biases identified were severe.

Decision tree:

```python
def domain3_judge(signals: dict[str, str]) -> str:
    """D3 Bias in selection of participants. Cribsheet p32."""
    q1 = signals.get("3.1", "NI")
    q2 = signals.get("3.2", "NI")
    q3 = signals.get("3.3", "NI")
    q4 = signals.get("3.4", "NI")
    q5 = signals.get("3.5", "NI")
    q6 = signals.get("3.6", "NI")
    q7 = signals.get("3.7", "NI")
    q8 = signals.get("3.8", "NI")

    # Subsection A: prevalent-user bias and immortal time
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
            if _yes(q5):
                b_judgement = "Serious"
            else:  # N/PN/NI to 3.5
                b_judgement = "Moderate"
        else:
            b_judgement = "Moderate"
    else:  # NI to 3.3
        b_judgement = "Moderate"

    # Combine A and B per the across-tier matrix (cribsheet p32)
    rank = {"Low": 0, "Moderate": 1, "Serious": 2, "Critical": 3}
    worst = max(rank[a_judgement], rank[b_judgement])

    if worst == 0:
        return "Low"
    if worst == 1:
        return "Moderate"

    # Subsection C only applies when A or B is Serious
    # 3.6: analysis corrected for biases?
    if _yes(q6):
        return "Moderate"
    # 3.7: sensitivity analyses minimal impact?
    if _yes(q7):
        return "Moderate"
    # 3.8: biases severe enough to exclude from synthesis?
    if _yes(q8):
        return "Critical"
    return "Serious"
```

---

## 6. Domain 4 — Bias due to missing data

Cribsheet p38. Invariant across all three variants. Largest domain by signal count (11 questions).

Signaling questions:

- **4.1** Were complete data on intervention status available for all, or nearly all, participants?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* 'Nearly all' should be interpreted as the number excluded due to missing intervention data is so small it could not have made an important difference to the estimated effect. NI usually leads to a high risk-of-bias judgement.

- **4.2** Were complete data on the outcome available for all, or nearly all, participants?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* For continuous outcomes, complete data for 95% (or 90%) is often sufficient. For dichotomous outcomes, the proportion required is directly linked to the risk of the outcome event.

- **4.3** Were complete data on important confounding variables available for all, or nearly all, participants?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* Same 'nearly all' interpretation as 4.1 and 4.2.

- **4.4** Is the result based on a complete case analysis?

  Response options: NA / Y / PY / PN / N / NI

  *Elaboration:* A complete case analysis is restricted to participants with complete data on all of the intervention, outcome and confounding variables.

- **4.5** Was exclusion from the analysis because of missing data (in intervention, confounders or the outcome) likely to be related to the true value of the outcome?

  Response options: NA / Y / PY / PN / N / NI

  *Elaboration:* Y/PY if e.g. (1) differences between intervention groups in proportions excluded; (2) reported reasons indicate missingness depends on the true outcome; (3) the outcome's nature makes missingness likely (severe depression participants missing appointments).

- **4.6** Is the relationship between the outcome and missingness likely to be explained by the variables in the analysis model?

  Response options: NA / Y / PY / WN / SN / NI

  *Elaboration:* If all variables that plausibly explain the outcome-missingness relationship are included in the complete-case analysis, bias due to missing data will be low. WN if not substantial; SN if bias is likely substantial.

- **4.7** Was the analysis based on imputing missing values?

  Response options: NA / Y / PY / PN / N / NI

  *Elaboration:* Y/PY if the analysis used either single or multiple imputation.

- **4.8** Is it reasonable to assume data were 'missing at random' (MAR) or 'missing completely at random' (MCAR)?

  Response options: NA / Y / PY / PN / N / NI

  *Elaboration:* Multiple imputation avoids bias provided incomplete variables are MAR or MCAR but not if MNAR (missing not at random). N/PN if there is reason to believe data are MNAR.

- **4.9** Was imputation performed appropriately?

  Response options: NA / Y / PY / WN / SN / NI

  *Elaboration:* WN / SN if simple methods (LOCF, mean imputation) were used; Y/PY if multiple imputation included all predictors of missingness and all variables in the main analysis model.

- **4.10** Was an appropriate alternative method used to correct for bias due to missing data?

  Response options: NA / Y / PY / WN / SN / NI

  *Elaboration:* Asked when the analysis was neither a complete case analysis nor based on imputation. Examples include inverse probability weighting and full information maximum likelihood.

- **4.11** Is there evidence that the result was not biased by missing data?

  Response options: NA / Y / PY / PN / N / NI

  *Elaboration:* Evidence may come from (1) analysis methods that would not be biased under plausible assumptions about missingness, or (2) sensitivity analyses showing results change little under plausible assumptions.

Decision tree:

```python
def domain4_judge(signals: dict[str, str]) -> str:
    """D4 Bias due to missing data. Cribsheet p38."""
    q1 = signals.get("4.1", "NI")
    q2 = signals.get("4.2", "NI")
    q3 = signals.get("4.3", "NI")
    q4 = signals.get("4.4", "NI")
    q5 = signals.get("4.5", "NI")
    q6 = signals.get("4.6", "NI")
    q7 = signals.get("4.7", "NI")
    q8 = signals.get("4.8", "NI")
    q9 = signals.get("4.9", "NI")
    q10 = signals.get("4.10", "NI")
    q11 = signals.get("4.11", "NI")

    # Best case: complete data on all three variables → Low directly
    if _strict_yes(q1) and _strict_yes(q2) and _strict_yes(q3):
        return "Low"

    # Complete case analysis path
    if _strict_yes(q4) or _no_info(q4):
        # 4.5: was exclusion related to outcome?
        if _strict_no(q5):  # N / PN: exclusion not related → Low
            return "Low"
        # Y / PY / NI to 4.5: concerning exclusion
        # 4.6: outcome-missingness explained by analysis model?
        if _strict_yes(q6):
            # Bias is plausibly addressed in the model; check 4.11
            if _strict_yes(q11):
                return "Moderate"
            return "Serious"
        if _weak_no(q6) or _no_info(q6):
            # Some concerns about whether the model captures the relationship
            if _strict_yes(q11):
                return "Moderate"
            return "Serious"
        # SN: bias likely substantial
        return "Critical" if _strict_no(q11) else "Serious"

    # Imputation path
    if _strict_yes(q7):
        if _strict_yes(q8):
            # 4.9: imputation appropriate?
            if _strict_yes(q9):
                return "Low"
            if _weak_no(q9) or _no_info(q9):
                return "Moderate" if _strict_yes(q11) else "Serious"
            # SN
            return "Critical" if _strict_no(q11) else "Serious"
        # 4.8 N/PN/NI: MAR/MCAR not reasonable → bias likely
        return "Critical" if _strict_no(q11) else "Serious"

    # Alternative method path (neither complete case nor imputation)
    if _strict_yes(q10):
        return "Low"
    if _weak_no(q10) or _no_info(q10):
        return "Moderate" if _strict_yes(q11) else "Serious"
    # SN to 4.10
    return "Critical" if _strict_no(q11) else "Serious"
```

---

## 7. Domain 5 — Bias arising from measurement of the outcome

Cribsheet p41. Invariant across all three variants.

Signaling questions:

- **5.1** Could measurement or ascertainment of the outcome have differed between intervention groups?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* Comparable methods involve the same measurement methods and thresholds, used at comparable time points. Differences can arise through 'diagnostic detection bias' or extra visits for intervention participants.

- **5.2** Were outcome assessors aware of the intervention received by study participants?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* N if outcome assessors were blinded, or if participants self-report and were themselves blinded. In observational studies, the answer will usually be Y when participants report their outcomes themselves.

- **5.3** Could assessment of the outcome have been influenced by knowledge of the intervention received?

  Response options: NA / SY / WY / PN / N / NI

  *Elaboration:* Only asked if 5.2 was Y/PY/NI. SY (yes, to a large extent) for patient-reported symptoms in homeopathy studies, or assessments of recovery by physiotherapists. WY (yes, to a small extent) when knowledge could have influenced assessment but no strong reason to believe it did.

Decision tree:

```python
def domain5_judge(signals: dict[str, str]) -> str:
    """D5 Bias arising from measurement of the outcome. Cribsheet p41."""
    q1 = signals.get("5.1", "NI")
    q2 = signals.get("5.2", "NI")
    q3 = signals.get("5.3", "NI")

    # 5.1 Y/PY: differential measurement → Serious directly
    if _yes(q1):
        return "Serious"

    # 5.1 N/PN: comparable measurement methods
    if _strict_no(q1):
        if _strict_no(q2):
            return "Low"
        # 5.2 Y/PY/NI: assessors knew → 5.3 (impact of knowledge)
        if _strong_yes(q3):
            return "Serious"
        if _weak_yes(q3) or _no_info(q3):
            return "Moderate"
        # PN / N on 5.3: knowledge unlikely to influence assessment
        return "Low"

    # 5.1 NI: unclear whether measurement differed
    if _strict_no(q2):
        return "Moderate"
    if _strong_yes(q3):
        return "Serious"
    return "Moderate"
```

---

## 8. Domain 6 — Bias in selection of the reported result

Cribsheet p47. Invariant across all three variants.

Signaling questions:

- **6.1** Was the result reported in accordance with an available, pre-determined analysis plan?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* Analysis plans are rarely publicly available for non-randomized studies, so most papers will not be assessed as Low risk of bias for this domain on the basis of 6.1 alone.

- **6.2** Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple outcome measurements within the outcome domain?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* Pain may be measured via VAS, McGill Pain Questionnaire, etc, at multiple time points. If only the most favourable is reported without justification, answer Y/PY.

- **6.3** Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple analyses of the data?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* Multiple analytic choices (unadjusted vs adjusted, alternative covariate sets, missing-data strategies) generate multiple estimates. Selection on favourable results is concerning.

- **6.4** Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple subgroups?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* Particularly with large cohorts from routine data, multiple subgroup estimates can be generated. Selection of the most interesting subgroup result is selective reporting.

Decision tree:

```python
def domain6_judge(signals: dict[str, str]) -> str:
    """D6 Bias in selection of the reported result. Cribsheet p47."""
    q1 = signals.get("6.1", "NI")
    q2 = signals.get("6.2", "NI")
    q3 = signals.get("6.3", "NI")
    q4 = signals.get("6.4", "NI")

    if _strict_yes(q1):
        return "Low"

    yes_count = sum(1 for q in (q2, q3, q4) if _yes(q))
    ni_count = sum(1 for q in (q2, q3, q4) if _no_info(q))

    if yes_count >= 2:
        return "Critical"
    if yes_count == 1:
        return "Serious"
    # No Y/PY among 6.2-6.4
    if ni_count == 3:
        return "Serious"
    if ni_count >= 1:
        return "Moderate"
    return "Low"
```

---

## 9. Overall aggregation

Worst-domain rule per cribsheet p48. Variant-labeled "Low" judgements normalize to Low for ranking.

```python
def robins_i_overall(domain_judgements: list[str]) -> str:
    """Overall judgement — worst-domain aggregation per cribsheet p48.

    The user may override upward when multiple Serious domains compound, but
    this code returns the algorithm default. Domain 1's special Low labels
    ("Low (except for concerns about uncontrolled confounding)" for cohort
    variants, "Low (except for concerns about uncontrolled benchmarking)"
    for the single-arm variant) are treated as Low for aggregation purposes.
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

---

## 10. LLM prompt templates

ROBINS-I V2 is implemented as **one preflight LLM call** (B1/B2/B3/C4) followed by **one LLM call per domain** (up to 6, skipped entirely if preflight short-circuits to Critical).

### 10.1 System prompt (shared across preflight + all 6 domain calls)

```text
You are an evidence-synthesis methodologist assessing risk of bias in a non-randomized study of an intervention using the Cochrane ROBINS-I V2 tool (20 November 2025 cribsheet). Read the PDF carefully. Answer each signaling question with one of the allowed tokens for that question — Y (yes), PY (probably yes), PN (probably no), N (no), NI (no information), and where indicated WN (weak no), SN (strong no), WY (weak yes), SY (strong yes). Provide a 1-2 sentence rationale for each answer, quoting the paper where possible. Return ONLY a valid JSON object — no preamble, no markdown fences.
```

### 10.2 Per-domain user prompt template

```text
Assess **Domain {domain_id} — {domain_name}{variant_suffix}** for the study described in the attached PDF using the ROBINS-I V2 tool.

Study type: {study_type}
Outcome being assessed: {primary_outcome}
{pico_block}
Context (fields already extracted from the paper):
{ctx_json}

Signaling questions:
{questions_block}

Return a JSON object with exactly this shape:
{shape}

Notes on ROBINS-I V2:
- The judgement scale is **Low / Moderate / Serious / Critical** (4 levels). Code maps your signal answers to the judgement — answer the signaling questions only.
- Some questions allow **WN / SN** (weak / strong no) or **WY / SY** (weak / strong yes). Use the strong version only when the magnitude is clearly substantial; use the weak version when the direction is right but the magnitude is uncertain.
- For each question, answer based on what the paper says about that specific question — **do NOT try to determine whether a question is gated out (NA) by the cribsheet's cascading structure**. Python applies the cascade rules after you answer and will set `NA` for any question that should be gated out. Just answer each question independently based on its own text.
- Answer each question exactly as worded: Y/PY when the answer to the question as written is yes/probably yes, N/PN when it is no/probably no. Some questions are phrased so that "yes" indicates a problem and others so that "yes" indicates good practice — never translate your answer into "problem present/absent". Reserve NI for when the paper provides no information to answer the question.
- Rationales must be short (1-2 sentences) and quote the paper verbatim where possible.
```

`{variant_suffix}` is `" (Variant A)"`, `" (Variant B)"`, or `" (Variant single_arm)"` for D1, the corresponding suffix for D2 when the single-arm variant fires, and empty for D3–D6.

`{pico_block}` is empty when no `target_pico` was supplied; otherwise:

```text

Target PICO (user-supplied):
{json.dumps(target_pico, indent=2)}

```

### 10.3 Per-signal questions block

```python
q_lines = []
for sig in signals:
    q_lines.append(
        f"\n**{sig['id']}. {sig['text']}**\n"
        f"Elaboration: {sig['elaboration']}\n"
        f"Response options: {'/'.join(sig['options'])}."
    )
questions_block = "\n".join(q_lines)
```

### 10.4 Direction-of-bias question

Every domain prompt asks the LLM to additionally rate `direction_of_bias` (used for narrative synthesis, NOT the worst-domain aggregator). The 6 legal values are:

```text
NA | Favours intervention | Favours comparator | Towards null | Away from null | Unpredictable
```

These are not used by the worst-domain aggregator (which only consumes the per-domain judgement); the overall `direction_of_bias` returned by `run()` is the modal value across domains (ties → "Unpredictable").

### 10.5 JSON-shape generator

```python
shape = "{\n"
for sig in signals:
    opt_string = "|".join(sig["options"])
    shape += f'  "{sig["id"]}": "{opt_string}",\n'
    shape += f'  "{sig["id"]}_rationale": "1-2 sentences quoting the paper",\n'
shape += '  "direction_of_bias": "NA|Favours intervention|Favours comparator|Towards null|Away from null|Unpredictable"\n'
shape += "}"
```

---

## 11. Expected JSON output shapes

### 11.1 Preflight cohort response

```json
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
```

### 11.2 Preflight single-arm response

Same shape as cohort — only the question wording differs (B1-SA / B2-SA replace B1/B2 in the prompt; the JSON keys stay `B1` / `B2`):

```json
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
```

### 11.3 Per-domain response (example: Domain 1 Variant A)

```json
{
  "1A.1": "Y|PY|WN|SN|NI",
  "1A.1_rationale": "1-2 sentences quoting the paper",
  "1A.2": "NA|Y|PY|WN|SN|NI",
  "1A.2_rationale": "...",
  "1A.3": "NA|Y|PY|PN|N|NI",
  "1A.3_rationale": "...",
  "1A.4": "Y|PY|PN|N",
  "1A.4_rationale": "...",
  "direction_of_bias": "NA|Favours intervention|Favours comparator|Towards null|Away from null|Unpredictable"
}
```

(The legal option set for each signal varies — see §3–§8 per-question response-options annotations.)

### 11.4 Final per-paper result returned by `run()`

```json
{
  "domain_results": {
    "preflight": {
      "B1": "PY",
      "B2": "NA",
      "B3": "N",
      "C4": "No",
      "rationales": {
        "B1": "Authors used multivariable logistic regression with propensity-score adjustment...",
        "B2": "...",
        "B3": "Outcome was measured by validated registry codes...",
        "C4": "Primary analysis defined the cohort by intent (everyone enrolled)..."
      },
      "screening_decision": "proceed",
      "screening_reason": "",
      "variant": "A"
    },
    "1": {
      "id": 1,
      "name": "Bias due to confounding",
      "variant": "A",
      "signals": {"1A.1": "PY", "1A.2": "Y", "1A.3": "N", "1A.4": "N"},
      "rationales": {
        "1A.1": "...", "1A.2": "...", "1A.3": "...", "1A.4": "..."
      },
      "judgement": "Low (except for concerns about uncontrolled confounding)",
      "direction": "NA"
    },
    "2": { "id": 2, "name": "...", "variant": "A", "signals": {...}, "rationales": {...}, "judgement": "Low", "direction": "NA" },
    "3": { "id": 3, "name": "...", "signals": {...}, "rationales": {...}, "judgement": "Moderate", "direction": "Favours intervention" },
    "4": { "id": 4, "name": "...", "signals": {...}, "rationales": {...}, "judgement": "Low", "direction": "NA" },
    "5": { "id": 5, "name": "...", "signals": {...}, "rationales": {...}, "judgement": "Low", "direction": "NA" },
    "6": { "id": 6, "name": "...", "signals": {...}, "rationales": {...}, "judgement": "Moderate", "direction": "Unpredictable" }
  },
  "overall_judgement": "Moderate",
  "overall_direction": "Favours intervention"
}
```

When preflight short-circuits to Critical:

```json
{
  "domain_results": {
    "preflight": {
      "B1": "N", "B2": "Y", "B3": "N", "C4": "No",
      "rationales": {...},
      "screening_decision": "critical",
      "screening_reason": "B2: Sufficient potential for confounding that the unadjusted result should not be considered further.",
      "variant": "A"
    }
  },
  "overall_judgement": "Critical",
  "overall_direction": "Unpredictable"
}
```

(Per-domain entries `"1"` through `"6"` are absent on a short-circuit.)

---

## 12. Sample data — pre-extracted fields

Each domain prompt receives a context block (`ctx_json`) containing a subset of fields already extracted from the paper by an upstream "annotator" stage. The fields surfaced per domain are:

| Domain | Relevant pre-extracted fields |
| ------ | ----------------------------- |
| Preflight (cohort) | `confounders_measured`, `adjustment_method`, `outcome_definition`, `outcome_ascertainment`, `analysis_framework`, `primary_outcome_measurement` |
| Preflight (single-arm) | `primary_endpoint_prespecified`, `inclusion_exclusion_criteria`, `comparator_historical_reference`, `consecutive_enrolment`, `outcome_definition`, `outcome_ascertainment`, `primary_outcome_measurement`, `analysis_framework` |
| 1 — Bias due to confounding | `confounders_measured`, `adjustment_method`, `exposure_definition`, `comparator_group`, `comparator_historical_reference`, `immortal_time_bias`, `confounding_control`, `primary_endpoint_prespecified`, `consecutive_enrolment` |
| 2 — Classification of interventions | `exposure_definition`, `exposure_measurement`, `exposure_ascertainment`, `intervention_classification`, `escalation_scheme`, `dose_levels`, `expansion_cohort` |
| 3 — Selection of participants | `case_source`, `control_selection`, `sampling_method`, `loss_to_follow_up`, `immortal_time_bias` |
| 4 — Missing data | `loss_to_follow_up`, `missing_data_handling`, `attrition_rate` |
| 5 — Measurement of the outcome | `outcome_ascertainment`, `outcome_definition` |
| 6 — Selection of the reported result | `outcome_definition`, `statistical_analysis` |

The fields are pre-extracted because giving the LLM both (a) the full PDF and (b) a JSON summary of the methodologically relevant sections noticeably improves grounding. If you don't have an annotator pipeline, you can pass an empty dict (`extracted_fields = {}`) and the prompts will simply contain `"(no pre-extracted fields)"` in the context block — the PDF itself still carries the evidence.

---

## 13. Reference implementation — single self-contained Python module

The module below is a turnkey adaptation: copy it into your project, supply your own LLM adapter via the `llm_call` parameter, and call `run(...)`. No project-specific imports.

The `llm_call` callable has the signature:

```python
llm_call(pdf_bytes: bytes, prompt: str, max_tokens: int) -> dict
```

It must send `pdf_bytes` + `prompt` to a vision-capable LLM (Claude PDF beta, Gemini direct upload, OpenAI vision, etc.) and return the parsed JSON response as a Python dict. Error handling, retries, and JSON-fence stripping are your concern.

```python
"""ROBINS-I V2 — Risk Of Bias In Non-randomised Studies of Interventions,
Version 2. Single-file reference implementation.

Source: ROBINS-I V2 cribsheet (20 November 2025). ROBINS-I V2 development group:
Sterne JA, Brandt Mathur M, Elbers R, Hróbjartsson A, McAleenan A, Reeves B,
Shrier I, Tilling K, Armstrong R, Berkman N, Boutron I, Carpenter J, Chan AW,
Deeks J, Golder S, Henry D, Jüni P, Kirkham J, Konstantinidis M, Lasserson T,
Loke Y, McGuinness L, Page M, Savović J, Shea B, Mawdsley D, Shepperd S,
Tugwell P, Valentine J, Viswanathan M, Waddington HS, Wells G, Hernán M, Higgins J.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Scales
# ─────────────────────────────────────────────
SIGNAL_OPTIONS_ALL = ("Y", "PY", "PN", "N", "NI", "WN", "SN", "WY", "SY")
JUDGEMENTS = ("Low", "Moderate", "Serious", "Critical")
LOW_D1 = "Low (except for concerns about uncontrolled confounding)"
LOW_D1_SA = "Low (except for concerns about uncontrolled benchmarking)"
SINGLE_ARM_STUDY_TYPES = frozenset({"Single-Arm Trial", "Dose-Escalation Study"})


# ─────────────────────────────────────────────
# Helper predicates
# ─────────────────────────────────────────────
def _yes(ans: str) -> bool:
    return ans in ("Y", "PY", "WY", "SY")


def _no(ans: str) -> bool:
    return ans in ("N", "PN", "WN", "SN")


def _strict_yes(ans: str) -> bool:
    return ans in ("Y", "PY")


def _strict_no(ans: str) -> bool:
    return ans in ("N", "PN")


def _weak_no(ans: str) -> bool:
    return ans == "WN"


def _strong_no(ans: str) -> bool:
    return ans == "SN"


def _weak_yes(ans: str) -> bool:
    return ans == "WY"


def _strong_yes(ans: str) -> bool:
    return ans == "SY"


def _no_info(ans: str) -> bool:
    return ans == "NI"


# ─────────────────────────────────────────────
# Decision trees
# ─────────────────────────────────────────────
def domain1_variant_a_judge(signals: dict[str, str]) -> str:
    """D1 Variant A (ITT effect, baseline confounding only). Cribsheet p20."""
    q1 = signals.get("1A.1", "NI")
    q2 = signals.get("1A.2", "NI")
    q3 = signals.get("1A.3", "NI")
    q4 = signals.get("1A.4", "NI")

    if _strong_no(q1) or _no_info(q1):
        return "Critical" if _yes(q4) else "Serious"

    if _strict_yes(q1):
        if _yes(q3):
            if _yes(q4):
                return "Critical"
            if _strict_yes(q2):
                return "Serious"
            return "Critical"
        if _strict_yes(q2) or _weak_no(q2):
            return "Serious" if _yes(q4) else LOW_D1
        return "Serious"

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


def domain1_variant_b_judge(signals: dict[str, str]) -> str:
    """D1 Variant B (per-protocol effect, baseline + time-varying). Cribsheet p24."""
    q1 = signals.get("1B.1", "NI")
    q2 = signals.get("1B.2", "NI")
    q3 = signals.get("1B.3", "NI")
    q4 = signals.get("1B.4", "NI")
    q5 = signals.get("1B.5", "NI")

    if _strict_no(q1) or _no_info(q1):
        if _yes(q4):
            return "Critical"
        return "Critical" if _yes(q5) else "Serious"

    if _strict_yes(q1):
        if _strict_yes(q2):
            if _strict_yes(q3) or _weak_no(q3):
                return "Serious" if _yes(q5) else LOW_D1
            return "Serious"
        if _weak_no(q2):
            if _strict_yes(q3) or _weak_no(q3):
                return "Serious" if _yes(q5) else "Moderate"
            return "Serious"
        return "Critical" if _yes(q5) else "Serious"

    return "Serious"


def domain1_variant_single_arm_judge(signals: dict[str, str]) -> str:
    """D1 Variant single_arm (uncontrolled / single-arm design — no comparator)."""
    q1 = signals.get("1S.1", "NI")
    q2 = signals.get("1S.2", "NI")
    q3 = signals.get("1S.3", "NI")
    q4 = signals.get("1S.4", "NI")
    q5 = signals.get("1S.5", "NI")

    if _yes(q5):
        return "Critical"

    if _strict_no(q1):
        if _strict_no(q4):
            return "Critical"
        return "Serious"

    if _no_info(q1):
        return "Serious"

    if _strict_yes(q1):
        if _strict_yes(q3):
            if _strict_yes(q2):
                return LOW_D1_SA
            return "Moderate"
        if _weak_no(q3):
            return "Moderate"
        if _strong_no(q3) or _no_info(q3):
            if _strict_yes(q4):
                return "Moderate"
            return "Serious"

    return "Serious"


def domain2_judge(signals: dict[str, str]) -> str:
    """D2 Bias in classification of interventions. Cribsheet p28."""
    q1 = signals.get("2.1", "NI")
    q2 = signals.get("2.2", "NI")
    q3 = signals.get("2.3", "NI")
    q4 = signals.get("2.4", "NI")
    q5 = signals.get("2.5", "NI")

    if _yes(q1) or _yes(q2):
        tier = 0
    elif _strong_yes(q3):
        tier = 1
    elif _weak_yes(q3) or _no_info(q3):
        tier = 1
    else:
        tier = 2

    if _strict_no(q4):
        bump4 = 0
    elif _weak_yes(q4) or _no_info(q4):
        bump4 = 1
    elif _strong_yes(q4):
        bump4 = 2
    else:
        bump4 = 1

    if _strict_no(q5):
        bump5 = 0
    else:
        bump5 = 1

    if tier == 2 and (_yes(q4) or _no_info(q4)):
        return "Critical"

    idx = min(tier + bump4 + bump5, 3)
    return JUDGEMENTS[idx]


def domain2_variant_single_arm_judge(signals: dict[str, str]) -> str:
    """D2 Variant single_arm — degenerate classification (only one intervention)."""
    q1 = signals.get("2S.1", "NI")
    q2 = signals.get("2S.2", "NI")
    q3 = signals.get("2S.3", "NI")

    if _strong_yes(q3):
        return "Critical"
    if _weak_yes(q3) or _no_info(q3):
        return "Serious"

    if _strict_yes(q1):
        if _strict_yes(q2):
            return "Low"
        if _weak_no(q2):
            return "Moderate"
        if _strong_no(q2):
            return "Serious"
        return "Moderate"

    if _strict_no(q1):
        return "Serious"
    return "Moderate"


def domain3_judge(signals: dict[str, str]) -> str:
    """D3 Bias in selection of participants. Cribsheet p32."""
    q1 = signals.get("3.1", "NI")
    q2 = signals.get("3.2", "NI")
    q3 = signals.get("3.3", "NI")
    q4 = signals.get("3.4", "NI")
    q5 = signals.get("3.5", "NI")
    q6 = signals.get("3.6", "NI")
    q7 = signals.get("3.7", "NI")
    q8 = signals.get("3.8", "NI")

    if _strict_yes(q1):
        a_judgement = "Low" if _strict_no(q2) or _no_info(q2) else "Moderate"
    elif _weak_no(q1) or _no_info(q1):
        a_judgement = "Moderate"
    elif _strong_no(q1):
        a_judgement = "Serious"
    else:
        a_judgement = "Moderate"

    if _strict_no(q3):
        b_judgement = "Low"
    elif _yes(q3):
        if _strict_no(q4) or _no_info(q4):
            b_judgement = "Low"
        elif _yes(q4):
            if _yes(q5):
                b_judgement = "Serious"
            else:
                b_judgement = "Moderate"
        else:
            b_judgement = "Moderate"
    else:
        b_judgement = "Moderate"

    rank = {"Low": 0, "Moderate": 1, "Serious": 2, "Critical": 3}
    worst = max(rank[a_judgement], rank[b_judgement])

    if worst == 0:
        return "Low"
    if worst == 1:
        return "Moderate"

    if _yes(q6):
        return "Moderate"
    if _yes(q7):
        return "Moderate"
    if _yes(q8):
        return "Critical"
    return "Serious"


def domain4_judge(signals: dict[str, str]) -> str:
    """D4 Bias due to missing data. Cribsheet p38."""
    q1 = signals.get("4.1", "NI")
    q2 = signals.get("4.2", "NI")
    q3 = signals.get("4.3", "NI")
    q4 = signals.get("4.4", "NI")
    q5 = signals.get("4.5", "NI")
    q6 = signals.get("4.6", "NI")
    q7 = signals.get("4.7", "NI")
    q8 = signals.get("4.8", "NI")
    q9 = signals.get("4.9", "NI")
    q10 = signals.get("4.10", "NI")
    q11 = signals.get("4.11", "NI")

    if _strict_yes(q1) and _strict_yes(q2) and _strict_yes(q3):
        return "Low"

    if _strict_yes(q4) or _no_info(q4):
        if _strict_no(q5):
            return "Low"
        if _strict_yes(q6):
            if _strict_yes(q11):
                return "Moderate"
            return "Serious"
        if _weak_no(q6) or _no_info(q6):
            if _strict_yes(q11):
                return "Moderate"
            return "Serious"
        return "Critical" if _strict_no(q11) else "Serious"

    if _strict_yes(q7):
        if _strict_yes(q8):
            if _strict_yes(q9):
                return "Low"
            if _weak_no(q9) or _no_info(q9):
                return "Moderate" if _strict_yes(q11) else "Serious"
            return "Critical" if _strict_no(q11) else "Serious"
        return "Critical" if _strict_no(q11) else "Serious"

    if _strict_yes(q10):
        return "Low"
    if _weak_no(q10) or _no_info(q10):
        return "Moderate" if _strict_yes(q11) else "Serious"
    return "Critical" if _strict_no(q11) else "Serious"


def domain5_judge(signals: dict[str, str]) -> str:
    """D5 Bias arising from measurement of the outcome. Cribsheet p41."""
    q1 = signals.get("5.1", "NI")
    q2 = signals.get("5.2", "NI")
    q3 = signals.get("5.3", "NI")

    if _yes(q1):
        return "Serious"

    if _strict_no(q1):
        if _strict_no(q2):
            return "Low"
        if _strong_yes(q3):
            return "Serious"
        if _weak_yes(q3) or _no_info(q3):
            return "Moderate"
        return "Low"

    if _strict_no(q2):
        return "Moderate"
    if _strong_yes(q3):
        return "Serious"
    return "Moderate"


def domain6_judge(signals: dict[str, str]) -> str:
    """D6 Bias in selection of the reported result. Cribsheet p47."""
    q1 = signals.get("6.1", "NI")
    q2 = signals.get("6.2", "NI")
    q3 = signals.get("6.3", "NI")
    q4 = signals.get("6.4", "NI")

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


DOMAIN_JUDGES_VARIANT_A: dict[int, Callable[[dict[str, str]], str]] = {
    1: domain1_variant_a_judge,
    2: domain2_judge,
    3: domain3_judge,
    4: domain4_judge,
    5: domain5_judge,
    6: domain6_judge,
}

DOMAIN_JUDGES_VARIANT_B: dict[int, Callable[[dict[str, str]], str]] = {
    1: domain1_variant_b_judge,
    2: domain2_judge,
    3: domain3_judge,
    4: domain4_judge,
    5: domain5_judge,
    6: domain6_judge,
}

DOMAIN_JUDGES_VARIANT_SINGLE_ARM: dict[int, Callable[[dict[str, str]], str]] = {
    1: domain1_variant_single_arm_judge,
    2: domain2_variant_single_arm_judge,
    3: domain3_judge,
    4: domain4_judge,
    5: domain5_judge,
    6: domain6_judge,
}


def robins_i_overall(domain_judgements: list[str]) -> str:
    """Overall judgement — worst-domain aggregation per cribsheet p48."""
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


# ─────────────────────────────────────────────
# Cascade enforcement — rule-based NA handling per the cribsheet's
# cascading-question structure. Called AFTER the LLM responds, before the
# decision tree runs. Overrides LLM answers for gated-out questions to NA.
# See §17 for the design rationale.
# ─────────────────────────────────────────────
def enforce_cascade_d1_variant_b_v2(signals: dict[str, str]) -> dict[str, str]:
    """V2 D1 Variant B — 1B.4 only asked when 1B.1 N/PN/NI (cribsheet).

    1B.4 elaboration: 'Asked when an inappropriate analysis method
    (1B.1 N/PN/NI) has been used.'
    """
    out = dict(signals)
    if out.get("1B.1", "NI") in ("Y", "PY"):
        out["1B.4"] = "NA"
    return out


def enforce_cascade_d1_variant_single_arm_v2(signals: dict[str, str]) -> dict[str, str]:
    """V2 D1 single-arm — 1S.3 NA when no benchmark identified at 1S.1.

    1S.3 elaboration: 'NA only when no benchmark was identified at 1S.1.'
    """
    out = dict(signals)
    if out.get("1S.1", "NI") in ("N", "PN"):
        out["1S.3"] = "NA"
    return out


def enforce_cascade_d2_cohort_v2(signals: dict[str, str]) -> dict[str, str]:
    """V2 D2 cohort — 2.2 only asked if 2.1 N/PN/NI.

    2.2 elaboration: 'Asked only if 2.1 was N/PN/NI.'
    """
    out = dict(signals)
    if out.get("2.1", "NI") in ("Y", "PY"):
        out["2.2"] = "NA"
    return out


def enforce_cascade_d3_v2(signals: dict[str, str]) -> dict[str, str]:
    """V2 D3 — 5 cascade rules (cribsheet p32):

    - 3.2 only asked if 3.1 Y/PY (immortal time follow-up)
    - 3.4 only asked if 3.3 Y/PY (post-intervention selection variables)
    - 3.5 only asked if 3.4 Y/PY (collider-style selection bias)
    - 3.6/3.7/3.8 only asked if subsection A or B raised concerns
    - 3.7 only asked if 3.6 N/PN/NI
    """
    out = dict(signals)
    # 3.2 gated on 3.1
    if out.get("3.1", "NI") not in ("Y", "PY"):
        out["3.2"] = "NA"
    # 3.4 gated on 3.3
    if out.get("3.3", "NI") not in ("Y", "PY"):
        out["3.4"] = "NA"
    # 3.5 gated on 3.4
    if out.get("3.4", "NA") not in ("Y", "PY"):
        out["3.5"] = "NA"
    # 3.6/3.7/3.8 only asked if subsection A or B raised concerns
    # A (prevalent-user/immortal time) concerns: 3.1 WN/SN/NI OR (3.1 Y/PY AND 3.2 Y/PY)
    a_concerns = (out.get("3.1", "NI") in ("WN", "SN", "NI")
                  or (out.get("3.1") in ("Y", "PY")
                      and out.get("3.2", "NA") in ("Y", "PY")))
    # B (other selection) concerns: 3.3 Y/PY OR 3.3 NI (NI conservative)
    b_concerns = out.get("3.3", "NI") in ("Y", "PY", "NI")
    if not (a_concerns or b_concerns):
        out["3.6"] = "NA"
        out["3.7"] = "NA"
        out["3.8"] = "NA"
    else:
        # 3.7 only asked if 3.6 N/PN/NI (when adjustment didn't fully fix)
        if out.get("3.6", "NI") in ("Y", "PY"):
            out["3.7"] = "NA"
    return out


def enforce_cascade_d4_v2(signals: dict[str, str]) -> dict[str, str]:
    """V2 D4 (missing data) — complex path-based cascade (cribsheet p38).

    Best case: 4.1 + 4.2 + 4.3 all Y/PY → 4.4-4.11 all NA (complete data).

    Otherwise, 4.4 is the complete-case-path selector:
    - 4.4 Y/PY/NI (complete case): use 4.5-4.6 path; 4.7-4.10 NA
        - 4.6 only asked if 4.5 Y/PY/NI (concerning exclusion)
    - 4.4 N/PN (not complete case): 4.5/4.6 NA; check 4.7 (imputation)
        - 4.7 Y/PY: imputation path; 4.10 NA
            - 4.9 only asked if 4.8 Y/PY
        - 4.7 N/PN: alternative-method path; 4.8/4.9 NA

    4.11 (sensitivity analysis rescue) is always asked when there's any
    missing-data concern (i.e. not in the all-complete-data short-circuit).
    """
    out = dict(signals)

    # Best case: complete data on all three variables
    if (out.get("4.1", "NI") in ("Y", "PY")
        and out.get("4.2", "NI") in ("Y", "PY")
        and out.get("4.3", "NI") in ("Y", "PY")):
        for sid in ("4.4", "4.5", "4.6", "4.7", "4.8", "4.9", "4.10", "4.11"):
            out[sid] = "NA"
        return out

    # 4.4 path selector
    q4_4 = out.get("4.4", "NI")
    if q4_4 in ("Y", "PY", "NI"):
        # Complete-case path
        for sid in ("4.7", "4.8", "4.9", "4.10"):
            out[sid] = "NA"
        # 4.6 only asked if 4.5 Y/PY/NI (concerning exclusion)
        if out.get("4.5", "NI") in ("N", "PN"):
            out["4.6"] = "NA"
    elif q4_4 in ("N", "PN"):
        # Not complete case: 4.5/4.6 NA
        out["4.5"] = "NA"
        out["4.6"] = "NA"
        # 4.7 imputation path selector
        q4_7 = out.get("4.7", "NI")
        if q4_7 in ("Y", "PY"):
            # Imputation path; 4.10 NA
            out["4.10"] = "NA"
            # 4.9 only asked if 4.8 Y/PY
            if out.get("4.8", "NI") not in ("Y", "PY"):
                out["4.9"] = "NA"
        elif q4_7 in ("N", "PN"):
            # Alternative-method path; 4.8/4.9 NA
            out["4.8"] = "NA"
            out["4.9"] = "NA"
        # 4.7 NI: leave 4.8-4.10 as-is (tree will handle conservatively)

    return out


def enforce_cascade_d5_v2(signals: dict[str, str]) -> dict[str, str]:
    """V2 D5 (measurement) — 5.3 only asked if 5.2 Y/PY/NI.

    5.3 elaboration: 'Only asked if 5.2 was Y/PY/NI.' (i.e., NOT N/PN —
    when assessors were demonstrably blinded, 5.3 doesn't apply.)
    """
    out = dict(signals)
    if out.get("5.2", "NI") in ("N", "PN"):
        out["5.3"] = "NA"
    return out


def enforce_cascade_v2(domain_id: int,
                       signals: dict[str, str],
                       variant: str) -> dict[str, str]:
    """Dispatch to per-domain cascade enforcer. Returns signals unchanged
    for domains/variants without cascade rules (D1 Variant A; D2 single-arm;
    D6)."""
    if domain_id == 1:
        if variant == "B":
            return enforce_cascade_d1_variant_b_v2(signals)
        if variant == "single_arm":
            return enforce_cascade_d1_variant_single_arm_v2(signals)
        return signals  # Variant A: no cribsheet cascade rules
    if domain_id == 2:
        if variant == "single_arm":
            return signals  # 2S.1/2S.2/2S.3: no cascade
        return enforce_cascade_d2_cohort_v2(signals)
    if domain_id == 3:
        return enforce_cascade_d3_v2(signals)
    if domain_id == 4:
        return enforce_cascade_d4_v2(signals)
    if domain_id == 5:
        return enforce_cascade_d5_v2(signals)
    return signals  # D6: no cascade


# ─────────────────────────────────────────────
# Per-question response option subsets
# ─────────────────────────────────────────────
_BASIC = ("Y", "PY", "PN", "N", "NI")
_BASIC_NA = ("NA", "Y", "PY", "PN", "N", "NI")
_WITH_WN_SN = ("Y", "PY", "WN", "SN", "NI")
_NA_WITH_WN_SN = ("NA", "Y", "PY", "WN", "SN", "NI")
_WITH_WY_SY = ("Y", "PY", "WY", "SY", "PN", "N", "NI")
_DIFFERENTIAL = ("SY", "WY", "PN", "N", "NI")
_NA_DIFFERENTIAL = ("NA", "SY", "WY", "PN", "N", "NI")


# ─────────────────────────────────────────────
# Signal definitions — verbatim text from the cribsheet
# ─────────────────────────────────────────────
DOMAIN1_VARIANT_A_SIGNALS: list[dict[str, Any]] = [
    {"id": "1A.1", "text": "Did the authors control for all the important confounding factors for which this was necessary?", "options": list(_WITH_WN_SN), "elaboration": "Answer Y/PY if all important confounding factors identified in the preliminary consideration were appropriately controlled for (stratification, regression, matching, standardization, propensity scores, IPTW). Answer WN if most were controlled and uncontrolled confounding was probably not substantial. Answer SN if at least one important confounder should have been controlled but was not, and the failure is likely to have a material impact."},
    {"id": "1A.2", "text": "Were confounding factors that were controlled for (and for which control was necessary) measured validly and reliably by the variables available in this study?", "options": list(_NA_WITH_WN_SN), "elaboration": "Adjustment helps only if confounders were measured well. Answer WN if measurement error was probably not substantial; SN if there was at least one important confounder measured poorly enough that the extent of measurement error in confounders was probably substantial."},
    {"id": "1A.3", "text": "Did the authors control for any post-intervention variables that could have been affected by the intervention?", "options": list(_BASIC_NA), "elaboration": "Controlling for variables on the causal pathway between intervention and outcome (over-adjustment) biases the effect estimate. Classic example: adjusting for a biomarker that the intervention changes."},
    {"id": "1A.4", "text": "Did the use of negative controls, quantitative bias analysis, or other considerations suggest serious uncontrolled confounding?", "options": list(_BASIC[:4]), "elaboration": "If the study did not use negative controls and no other considerations suggest uncontrolled confounding, answer N. Answer Y/PY if negative controls indicate the result being assessed suffers from material bias due to confounding."},
]

DOMAIN1_VARIANT_B_SIGNALS: list[dict[str, Any]] = [
    {"id": "1B.1", "text": "Did the authors use an analysis method that was appropriate to control for time-varying as well as baseline confounding?", "options": list(_BASIC), "elaboration": "Appropriate methods to control for time-varying confounding ('g-methods') include inverse probability weighting based on baseline- and time-varying confounding factors, with adjustment for the censoring weights. Standard regression models including time-varying confounders may be problematic when those confounders are affected by prior intervention (treatment-confounder feedback)."},
    {"id": "1B.2", "text": "Did the authors control for all the important baseline and time-varying confounding factors for which this was necessary?", "options": list(_NA_WITH_WN_SN), "elaboration": "Per-protocol analyses must control for both baseline and time-varying confounding factors that predict changes to intervention received. Same WN / SN semantics as Variant A 1.1."},
    {"id": "1B.3", "text": "Were confounding factors that were controlled for measured validly and reliably by the variables available in this study?", "options": list(_NA_WITH_WN_SN), "elaboration": "Same measurement-validity question as Variant A 1.2 but applied to baseline + time-varying confounders."},
    {"id": "1B.4", "text": "Did the authors control for time-varying factors or other variables measured after the start of intervention?", "options": list(_BASIC_NA), "elaboration": "Asked when an inappropriate analysis method (1B.1 N/PN/NI) has been used. Conditioning on time-varying factors measured after the start of intervention is likely to lead to bias when those factors are also on the causal pathway from intervention to outcome."},
    {"id": "1B.5", "text": "Did the use of negative controls, or other considerations, suggest serious uncontrolled confounding?", "options": list(_BASIC[:4]), "elaboration": "Same as Variant A 1.4."},
]

DOMAIN1_VARIANT_SINGLE_ARM_SIGNALS: list[dict[str, Any]] = [
    {"id": "1S.1", "text": "Was the implied benchmark (historical control rate, pre-specified performance criterion, or null hypothesis with a quantitative decision rule) pre-specified before data collection?", "options": list(_BASIC[:4]), "elaboration": "Single-arm trials have no internal comparator. They are interpreted against an implicit benchmark — usually a historical-control response rate, a regulatory performance criterion (e.g. ORR > 30% to support accelerated approval), or a null hypothesis with a pre-specified statistical decision rule (e.g. Simon's two-stage design). Answer Y/PY if a numeric benchmark + decision rule was clearly stated in the protocol / SAP / methods, BEFORE the data were collected. Answer N/PN if no benchmark is identifiable, or if the benchmark looks chosen post-hoc to match the observed result."},
    {"id": "1S.2", "text": "Is the implied benchmark reasonable given current standard of care and the patient population being studied?", "options": list(_BASIC), "elaboration": "A pre-specified benchmark is only useful if it reflects a clinically meaningful threshold for this population. Answer Y/PY if the benchmark is consistent with contemporary published control-arm rates in comparable patients (similar disease stage, prior therapy, biomarker status). Answer N/PN if the benchmark is implausibly low (inflates apparent benefit) or implausibly high (forces a near-impossible bar). NI if no contemporary comparable estimate exists."},
    {"id": "1S.3", "text": "Is the cohort's measured baseline prognostic profile (stage, prior lines, ECOG / performance status, biomarker status, key comorbidities) comparable to that of the benchmark population?", "options": list(_NA_WITH_WN_SN), "elaboration": "The single-arm proportion is biased upward if the enrolled cohort is more prognostically favourable than the benchmark population (e.g. younger, less heavily pre-treated, biomarker-enriched). Answer Y/PY when measured baseline prognostic factors are comparable. WN when most-but-not-all prognostic factors look comparable. SN when at least one important prognostic factor is materially more favourable in this cohort. NA only when no benchmark was identified at 1S.1."},
    {"id": "1S.4", "text": "Did the authors address residual prognostic-mix differences quantitatively (sensitivity analyses, propensity-score adjustment to external controls, prognostic-score stratification, or similar)?", "options": list(_BASIC_NA), "elaboration": "Even when 1S.3 raises concerns, quantitative external-control adjustment can rescue interpretability. Examples include propensity-score weighting against an external real-world cohort, prognostic-score stratification, MAIC, or pre-specified sensitivity analyses showing the conclusion is robust to plausible prognostic differences. Answer Y/PY when such methods were used and reported. N/PN when not addressed."},
    {"id": "1S.5", "text": "Do negative / falsification controls, external-validity considerations, or other quantitative bias analyses suggest serious uncontrolled selection-prognostic bias?", "options": list(_BASIC[:4]), "elaboration": "Analogous to 1A.4 / 1B.5 in the cohort variants. Answer Y/PY if a falsification analysis (e.g. testing the intervention against an outcome it shouldn't affect) suggested residual bias, or if external-validity checks revealed serious cohort-vs-benchmark mismatch. Answer N if no falsification analysis was performed and no other consideration suggests substantial uncontrolled bias — this is the typical answer."},
]

DOMAIN2_SIGNALS: list[dict[str, Any]] = [
    {"id": "2.1", "text": "Were the intervention strategies distinguishable at the time when follow-up would have started in the target trial?", "options": list(_BASIC), "elaboration": "In most non-randomized studies, participants are classified to intervention strategies based on information about interventions prescribed or received. Some strategies (e.g. 'surgery within 6 months of diagnosis' vs 'delay surgery until clinical progression') cannot be distinguished at follow-up start, creating a period of 'immortal time' during which the outcome cannot occur for some groups."},
    {"id": "2.2", "text": "Did all or nearly all outcome events occur after the intervention and comparator strategies could be distinguished?", "options": list(_BASIC_NA), "elaboration": "Asked only if 2.1 was N/PN/NI. If the indistinguishable period is short relative to total follow-up, the proportion of outcome events during that period may be low and the misclassification bias correspondingly small."},
    {"id": "2.3", "text": "Did the analysis avoid problems arising from intervention strategies that are not distinguishable at the start of follow-up?", "options": list(_NA_DIFFERENTIAL), "elaboration": "Answer SY (strong yes, fully) if predictors of treatment during follow-up were measured and used appropriately to derive inverse-probability weights (e.g. clone-censor-weighting, g-formula), or if the study used a 'landmark' analysis. WY (partially) if appropriate but unlikely to have fully adjusted for prognostic factors predicting treatment after start of follow-up."},
    {"id": "2.4", "text": "Was classification of intervention status influenced by knowledge of the outcome or risk of the outcome?", "options": list(_DIFFERENTIAL), "elaboration": "Differential misclassification arises when the outcome (or its causes, other than the intervention) influences how interventions are classified. SY = yes, and the impact was substantial; WY = yes, but the impact was not substantial."},
    {"id": "2.5", "text": "Were further classification errors (not influenced by knowledge of the outcome or risk of the outcome) likely?", "options": list(_BASIC), "elaboration": "Non-differential misclassification — receipt of intervention not recorded for some participants. Usually biases towards the null. 'Nearly all' should be interpreted as 'enough to be confident of the findings'."},
]

DOMAIN2_VARIANT_SINGLE_ARM_SIGNALS: list[dict[str, Any]] = [
    {"id": "2S.1", "text": "Was the intervention well-defined (dose, schedule, duration, dose-modifications protocol) at the start of follow-up?", "options": list(_BASIC), "elaboration": "In a single-arm trial there is no comparator misclassification, but the single intervention must be specified precisely enough that the reported result corresponds to a reproducible regimen. Answer Y/PY when dose, schedule, duration, and dose-modification rules (reductions, holds, criteria for discontinuation) are fully reported. Answer N/PN when the intervention is described only at high level (e.g. 'standard chemotherapy')."},
    {"id": "2S.2", "text": "Were dose reductions, holds, and discontinuations recorded and reported?", "options": list(_WITH_WN_SN), "elaboration": "Recording of treatment delivery is essential for interpreting the single-arm result. WN if most exposure modifications were recorded; SN if material exposure detail is missing such that the analyzed 'intervention' is effectively undefined."},
    {"id": "2S.3", "text": "Was the analyzed cohort defined by intended treatment (everyone enrolled, ITT-like) or by received treatment (only those completing ≥X cycles / responding to treatment)?", "options": list(_DIFFERENTIAL), "elaboration": "Defining the analyzed cohort by *received* treatment (per-protocol completers, 'evaluable population') selects for patients who tolerated the intervention well enough to keep receiving it — a strong selection toward responders that inflates the single-arm proportion. Answer SY (strong yes) when the primary analysis is explicitly restricted to completers or responders. WY (weak yes) when the analyzed cohort excludes some enrolled patients for treatment-related reasons but not dominantly. Answer N/PN when all enrolled (or all who received any dose of intervention — modified ITT) are analyzed."},
]

DOMAIN3_SIGNALS: list[dict[str, Any]] = [
    {"id": "3.1", "text": "Did follow-up in the analysis begin at the start of the intervention strategies being compared?", "options": list(_WITH_WN_SN), "elaboration": "A. Prevalent-user bias and immortal time. Answer Y/PY if all outcome events and follow-up time after the start of the interventions were included in the analysis. WN if not substantial; SN if leading to a substantial risk of bias."},
    {"id": "3.2", "text": "Were outcome events during a period of follow-up after the start of the interventions excluded from the analysis?", "options": list(_BASIC), "elaboration": "Only asked if 3.1 was Y/PY. Such exclusion creates 'immortal time' during which events cannot occur and biases the effect estimate."},
    {"id": "3.3", "text": "Was selection of participants into the study (or into the analysis) based on participant characteristics observed after the start of intervention, additional to the situations addressed in 3.1 and 3.2?", "options": list(_BASIC), "elaboration": "B. Other selection bias. Answer Y/PY if selection into the study was based on post-intervention characteristics. N/PN if selection was based only on pre-intervention characteristics — baseline confounding is addressed in Domain 1, not here."},
    {"id": "3.4", "text": "Were the post-intervention variables that influenced selection likely to be associated with intervention?", "options": list(_BASIC_NA), "elaboration": "Only asked if 3.3 was Y/PY. Selection bias occurs when selection is related to an effect of either intervention or a cause of intervention AND an effect of either the outcome or a cause of the outcome."},
    {"id": "3.5", "text": "Were the post-intervention variables that influenced selection likely to be influenced by the outcome or a cause of the outcome?", "options": list(_BASIC_NA), "elaboration": "Only asked if 3.4 was Y/PY. Collider-style selection bias."},
    {"id": "3.6", "text": "Is it likely that the analysis corrected for all of the potential selection biases identified above?", "options": list(_BASIC_NA), "elaboration": "C. Analysis / sensitivity / severity. Only asked if A or B raised concerns. Inverse probability weights can create a pseudo-population without the selection bias if assumptions are justified."},
    {"id": "3.7", "text": "Did sensitivity analyses demonstrate that the likely impact of the potential selection biases identified above was minimal?", "options": list(_BASIC_NA), "elaboration": "Only asked if 3.6 was N/PN/NI."},
    {"id": "3.8", "text": "Were potential selection biases identified above sufficiently severe that the result should not be included in a quantitative synthesis?", "options": list(_BASIC_NA), "elaboration": "Distinguishes 'Serious' from 'Critical' risk of selection bias. Answer N/PN/NI unless there is clear evidence that the selection biases identified were severe."},
]

DOMAIN4_SIGNALS: list[dict[str, Any]] = [
    {"id": "4.1", "text": "Were complete data on intervention status available for all, or nearly all, participants?", "options": list(_BASIC), "elaboration": "'Nearly all' should be interpreted as the number excluded due to missing intervention data is so small it could not have made an important difference to the estimated effect. NI usually leads to a high risk-of-bias judgement."},
    {"id": "4.2", "text": "Were complete data on the outcome available for all, or nearly all, participants?", "options": list(_BASIC), "elaboration": "For continuous outcomes, complete data for 95% (or 90%) is often sufficient. For dichotomous outcomes, the proportion required is directly linked to the risk of the outcome event."},
    {"id": "4.3", "text": "Were complete data on important confounding variables available for all, or nearly all, participants?", "options": list(_BASIC), "elaboration": "Same 'nearly all' interpretation as 4.1 and 4.2."},
    {"id": "4.4", "text": "Is the result based on a complete case analysis?", "options": list(_BASIC_NA), "elaboration": "A complete case analysis is restricted to participants with complete data on all of the intervention, outcome and confounding variables."},
    {"id": "4.5", "text": "Was exclusion from the analysis because of missing data (in intervention, confounders or the outcome) likely to be related to the true value of the outcome?", "options": list(_BASIC_NA), "elaboration": "Y/PY if e.g. (1) differences between intervention groups in proportions excluded; (2) reported reasons indicate missingness depends on the true outcome; (3) the outcome's nature makes missingness likely (severe depression participants missing appointments)."},
    {"id": "4.6", "text": "Is the relationship between the outcome and missingness likely to be explained by the variables in the analysis model?", "options": list(_NA_WITH_WN_SN), "elaboration": "If all variables that plausibly explain the outcome-missingness relationship are included in the complete-case analysis, bias due to missing data will be low. WN if not substantial; SN if bias is likely substantial."},
    {"id": "4.7", "text": "Was the analysis based on imputing missing values?", "options": list(_BASIC_NA), "elaboration": "Y/PY if the analysis used either single or multiple imputation."},
    {"id": "4.8", "text": "Is it reasonable to assume data were 'missing at random' (MAR) or 'missing completely at random' (MCAR)?", "options": list(_BASIC_NA), "elaboration": "Multiple imputation avoids bias provided incomplete variables are MAR or MCAR but not if MNAR (missing not at random). N/PN if there is reason to believe data are MNAR."},
    {"id": "4.9", "text": "Was imputation performed appropriately?", "options": list(_NA_WITH_WN_SN), "elaboration": "WN / SN if simple methods (LOCF, mean imputation) were used; Y/PY if multiple imputation included all predictors of missingness and all variables in the main analysis model."},
    {"id": "4.10", "text": "Was an appropriate alternative method used to correct for bias due to missing data?", "options": list(_NA_WITH_WN_SN), "elaboration": "Asked when the analysis was neither a complete case analysis nor based on imputation. Examples include inverse probability weighting and full information maximum likelihood."},
    {"id": "4.11", "text": "Is there evidence that the result was not biased by missing data?", "options": list(_BASIC_NA), "elaboration": "Evidence may come from (1) analysis methods that would not be biased under plausible assumptions about missingness, or (2) sensitivity analyses showing results change little under plausible assumptions."},
]

DOMAIN5_SIGNALS: list[dict[str, Any]] = [
    {"id": "5.1", "text": "Could measurement or ascertainment of the outcome have differed between intervention groups?", "options": list(_BASIC), "elaboration": "Comparable methods involve the same measurement methods and thresholds, used at comparable time points. Differences can arise through 'diagnostic detection bias' or extra visits for intervention participants."},
    {"id": "5.2", "text": "Were outcome assessors aware of the intervention received by study participants?", "options": list(_BASIC), "elaboration": "N if outcome assessors were blinded, or if participants self-report and were themselves blinded. In observational studies, the answer will usually be Y when participants report their outcomes themselves."},
    {"id": "5.3", "text": "Could assessment of the outcome have been influenced by knowledge of the intervention received?", "options": list(_NA_DIFFERENTIAL), "elaboration": "Only asked if 5.2 was Y/PY/NI. SY (yes, to a large extent) for patient-reported symptoms in homeopathy studies, or assessments of recovery by physiotherapists. WY (yes, to a small extent) when knowledge could have influenced assessment but no strong reason to believe it did."},
]

DOMAIN6_SIGNALS: list[dict[str, Any]] = [
    {"id": "6.1", "text": "Was the result reported in accordance with an available, pre-determined analysis plan?", "options": list(_BASIC), "elaboration": "Analysis plans are rarely publicly available for non-randomized studies, so most papers will not be assessed as Low risk of bias for this domain on the basis of 6.1 alone."},
    {"id": "6.2", "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple outcome measurements within the outcome domain?", "options": list(_BASIC), "elaboration": "Pain may be measured via VAS, McGill Pain Questionnaire, etc, at multiple time points. If only the most favourable is reported without justification, answer Y/PY."},
    {"id": "6.3", "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple analyses of the data?", "options": list(_BASIC), "elaboration": "Multiple analytic choices (unadjusted vs adjusted, alternative covariate sets, missing-data strategies) generate multiple estimates. Selection on favourable results is concerning."},
    {"id": "6.4", "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple subgroups?", "options": list(_BASIC), "elaboration": "Particularly with large cohorts from routine data, multiple subgroup estimates can be generated. Selection of the most interesting subgroup result is selective reporting."},
]


DOMAINS: list[dict[str, Any]] = [
    {
        "id": 1,
        "name": "Bias due to confounding",
        "variants": ["A", "B", "single_arm"],
        "variant_signals": {
            "A": DOMAIN1_VARIANT_A_SIGNALS,
            "B": DOMAIN1_VARIANT_B_SIGNALS,
            "single_arm": DOMAIN1_VARIANT_SINGLE_ARM_SIGNALS,
        },
        "signals": DOMAIN1_VARIANT_A_SIGNALS + DOMAIN1_VARIANT_B_SIGNALS + DOMAIN1_VARIANT_SINGLE_ARM_SIGNALS,
        "relevant_fields": ["confounders_measured", "adjustment_method", "exposure_definition", "comparator_group", "comparator_historical_reference", "immortal_time_bias", "confounding_control", "primary_endpoint_prespecified", "consecutive_enrolment"],
    },
    {
        "id": 2,
        "name": "Bias in classification of interventions",
        "variants": ["A", "B", "single_arm"],
        "variant_signals": {"A": DOMAIN2_SIGNALS, "B": DOMAIN2_SIGNALS, "single_arm": DOMAIN2_VARIANT_SINGLE_ARM_SIGNALS},
        "signals": DOMAIN2_SIGNALS + DOMAIN2_VARIANT_SINGLE_ARM_SIGNALS,
        "relevant_fields": ["exposure_definition", "exposure_measurement", "exposure_ascertainment", "intervention_classification", "escalation_scheme", "dose_levels", "expansion_cohort"],
    },
    {"id": 3, "name": "Bias in selection of participants into the study (or analysis)", "signals": DOMAIN3_SIGNALS, "relevant_fields": ["case_source", "control_selection", "sampling_method", "loss_to_follow_up", "immortal_time_bias"]},
    {"id": 4, "name": "Bias due to missing data", "signals": DOMAIN4_SIGNALS, "relevant_fields": ["loss_to_follow_up", "missing_data_handling", "attrition_rate"]},
    {"id": 5, "name": "Bias arising from measurement of the outcome", "signals": DOMAIN5_SIGNALS, "relevant_fields": ["outcome_ascertainment", "outcome_definition"]},
    {"id": 6, "name": "Bias in selection of the reported result", "signals": DOMAIN6_SIGNALS, "relevant_fields": ["outcome_definition", "statistical_analysis"]},
]


# ─────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing risk of bias in a "
    "non-randomized study of an intervention using the Cochrane ROBINS-I V2 "
    "tool (20 November 2025 cribsheet). Read the PDF carefully. Answer each "
    "signaling question with one of the allowed tokens for that question — "
    "Y (yes), PY (probably yes), PN (probably no), N (no), NI (no information), "
    "and where indicated WN (weak no), SN (strong no), WY (weak yes), "
    "SY (strong yes). Provide a 1-2 sentence rationale for each answer, "
    "quoting the paper where possible. Return ONLY a valid JSON object — no "
    "preamble, no markdown fences."
)


def _build_preflight_prompt_cohort(study_type: str, primary_outcome: str,
                                    extracted_fields: dict[str, str]) -> str:
    relevant_keys = ["confounders_measured", "adjustment_method", "outcome_definition",
                     "outcome_ascertainment", "analysis_framework", "primary_outcome_measurement"]
    relevant = {k: extracted_fields[k] for k in relevant_keys if extracted_fields.get(k)}
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"

    return f"""You are performing the **Preliminary Considerations** screen of ROBINS-I V2 on a non-randomized study.

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
{{
  "B1": "Y|PY|PN|N",
  "B1_rationale": "1-2 sentences quoting the paper",
  "B2": "Y|PY|PN|N|NA",
  "B2_rationale": "1-2 sentences (or 'NA' if B1 was Y/PY)",
  "B3": "Y|PY|PN|N",
  "B3_rationale": "1-2 sentences quoting the paper",
  "C4": "No|Yes",
  "C4_rationale": "1-2 sentences explaining whether the analysis estimates ITT or per-protocol"
}}"""


def _build_preflight_prompt_single_arm(study_type: str, primary_outcome: str,
                                        extracted_fields: dict[str, str]) -> str:
    relevant_keys = ["primary_endpoint_prespecified", "inclusion_exclusion_criteria",
                     "comparator_historical_reference", "consecutive_enrolment",
                     "outcome_definition", "outcome_ascertainment",
                     "primary_outcome_measurement", "analysis_framework"]
    relevant = {k: extracted_fields[k] for k in relevant_keys if extracted_fields.get(k)}
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"

    return f"""You are performing the **Preliminary Considerations** screen of ROBINS-I V2 (adapted for single-arm / uncontrolled designs) on an uncontrolled clinical study.

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
{{
  "B1": "Y|PY|PN|N",
  "B1_rationale": "1-2 sentences quoting the paper (answer to B1-SA)",
  "B2": "Y|PY|PN|N|NA",
  "B2_rationale": "1-2 sentences (or 'NA' if B1 was Y/PY)",
  "B3": "Y|PY|PN|N",
  "B3_rationale": "1-2 sentences quoting the paper",
  "C4": "No|Yes",
  "C4_rationale": "1-2 sentences explaining whether the analysis is ITT-like or per-protocol-like"
}}"""


def _signals_for_domain(domain: dict[str, Any], variant: str) -> list[dict[str, Any]]:
    if domain.get("variant_signals"):
        return domain["variant_signals"][variant]
    return domain["signals"]


def build_domain_prompt(domain: dict[str, Any], variant: str, study_type: str,
                        primary_outcome: str, extracted_fields: dict[str, str],
                        target_pico: dict[str, str] | None = None) -> str:
    signals = _signals_for_domain(domain, variant)

    relevant = {k: extracted_fields[k] for k in domain.get("relevant_fields", [])
                if extracted_fields.get(k)}
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"

    pico_block = ""
    if target_pico:
        pico_block = "\nTarget PICO (user-supplied):\n" + json.dumps(target_pico, indent=2) + "\n"

    domain_header = f"Domain {domain['id']} — {domain['name']}"
    if domain.get("variant_signals"):
        domain_header += f" (Variant {variant})"

    q_lines = []
    for sig in signals:
        q_lines.append(
            f"\n**{sig['id']}. {sig['text']}**\n"
            f"Elaboration: {sig['elaboration']}\n"
            f"Response options: {'/'.join(sig['options'])}."
        )
    questions_block = "\n".join(q_lines)

    shape = "{\n"
    for sig in signals:
        opt_string = "|".join(sig["options"])
        shape += f'  "{sig["id"]}": "{opt_string}",\n'
        shape += f'  "{sig["id"]}_rationale": "1-2 sentences quoting the paper",\n'
    shape += '  "direction_of_bias": "NA|Favours intervention|Favours comparator|Towards null|Away from null|Unpredictable"\n'
    shape += "}"

    return f"""Assess **{domain_header}** for the study described in the attached PDF using the ROBINS-I V2 tool.

Study type: {study_type}
Outcome being assessed: {primary_outcome}
{pico_block}
Context (fields already extracted from the paper):
{ctx_json}

Signaling questions:
{questions_block}

Return a JSON object with exactly this shape:
{shape}

Notes on ROBINS-I V2:
- The judgement scale is **Low / Moderate / Serious / Critical** (4 levels). Code maps your signal answers to the judgement — answer the signaling questions only.
- Some questions allow **WN / SN** (weak / strong no) or **WY / SY** (weak / strong yes). Use the strong version only when the magnitude is clearly substantial; use the weak version when the direction is right but the magnitude is uncertain.
- For each question, answer based on what the paper says about that specific question — **do NOT try to determine whether a question is gated out (NA) by the cribsheet's cascading structure**. Python applies the cascade rules after you answer and will set `NA` for any question that should be gated out. Just answer each question independently based on its own text.
- Answer each question exactly as worded: Y/PY when the answer to the question as written is yes/probably yes, N/PN when it is no/probably no. Some questions are phrased so that "yes" indicates a problem and others so that "yes" indicates good practice — never translate your answer into "problem present/absent". Reserve NI for when the paper provides no information to answer the question.
- Rationales must be short (1-2 sentences) and quote the paper verbatim where possible."""


# ─────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────
def run_preflight(pdf_bytes: bytes, study_type: str, primary_outcome: str,
                  extracted_fields: dict[str, str],
                  llm_call: Callable[[bytes, str, int], dict[str, Any]]) -> dict[str, Any]:
    """Run the preflight LLM call. Returns answers + variant + screening decision."""
    is_single_arm = study_type in SINGLE_ARM_STUDY_TYPES
    if is_single_arm:
        prompt = _build_preflight_prompt_single_arm(study_type, primary_outcome, extracted_fields)
    else:
        prompt = _build_preflight_prompt_cohort(study_type, primary_outcome, extracted_fields)
    raw = llm_call(pdf_bytes, prompt, 2048)

    def _opt(key: str, default: str = "NI",
             allowed: tuple = ("Y", "PY", "PN", "N")) -> str:
        v = str(raw.get(key, default)).strip().upper()
        if v not in allowed:
            return default
        return v

    b1 = _opt("B1")
    b2 = _opt("B2", default="NA", allowed=("Y", "PY", "PN", "N", "NA"))
    b3 = _opt("B3")
    c4_raw = str(raw.get("C4", "No")).strip().lower()
    c4 = "Yes" if c4_raw.startswith("y") else "No"

    rationales = {
        "B1": str(raw.get("B1_rationale", "")).strip(),
        "B2": str(raw.get("B2_rationale", "")).strip(),
        "B3": str(raw.get("B3_rationale", "")).strip(),
        "C4": str(raw.get("C4_rationale", "")).strip(),
    }

    if is_single_arm:
        variant = "single_arm"
        b2_reason = ("B2-SA: Absence of any pre-specified benchmark is severe enough "
                     "that the single-arm proportion is uninterpretable for causal inference.")
    else:
        variant = "A" if c4 == "No" else "B"
        b2_reason = ("B2: Sufficient potential for confounding that the unadjusted "
                     "result should not be considered further.")

    if b2 in ("Y", "PY"):
        return {"B1": b1, "B2": b2, "B3": b3, "C4": c4, "rationales": rationales,
                "screening_decision": "critical", "screening_reason": b2_reason,
                "variant": variant}
    if b3 in ("Y", "PY"):
        return {"B1": b1, "B2": b2, "B3": b3, "C4": c4, "rationales": rationales,
                "screening_decision": "critical",
                "screening_reason": "B3: The method of measuring the outcome is inappropriate.",
                "variant": variant}
    return {"B1": b1, "B2": b2, "B3": b3, "C4": c4, "rationales": rationales,
            "screening_decision": "proceed", "screening_reason": "", "variant": variant}


def _assess_domain(pdf_bytes: bytes, domain: dict[str, Any], variant: str,
                   study_type: str, primary_outcome: str,
                   extracted_fields: dict[str, str],
                   llm_call: Callable[[bytes, str, int], dict[str, Any]],
                   target_pico: dict[str, str] | None = None) -> dict[str, Any]:
    prompt = build_domain_prompt(domain, variant, study_type, primary_outcome,
                                 extracted_fields, target_pico)
    raw = llm_call(pdf_bytes, prompt, 8192)

    signals_for_this = _signals_for_domain(domain, variant)
    signals: dict[str, str] = {}
    rationales: dict[str, str] = {}
    for sig in signals_for_this:
        sid = sig["id"]
        ans = str(raw.get(sid, "NI")).strip().upper()
        allowed = set(sig["options"])
        if ans not in allowed:
            logger.warning("ROBINS-I V2 domain %s question %s: invalid answer %r — defaulting to NI",
                           domain["id"], sid, ans)
            ans = "NI" if "NI" in allowed else next(iter(allowed))
        signals[sid] = ans
        rationales[sid] = str(raw.get(f"{sid}_rationale", "")).strip()

    # Python-side cascade enforcement: override LLM answers to NA for
    # questions gated out by the cribsheet's cascading structure.
    # See §17 for the design rationale.
    pre_cascade = dict(signals)
    signals = enforce_cascade_v2(domain["id"], signals, variant=variant)
    overrides = {sid: (pre_cascade[sid], signals[sid])
                 for sid in signals
                 if sid in pre_cascade and pre_cascade[sid] != signals[sid]}
    if overrides:
        logger.debug("ROBINS-I V2 D%s variant %s cascade enforcement overrode LLM answers: %r",
                     domain["id"], variant, overrides)

    if variant == "A":
        judges = DOMAIN_JUDGES_VARIANT_A
    elif variant == "B":
        judges = DOMAIN_JUDGES_VARIANT_B
    elif variant == "single_arm":
        judges = DOMAIN_JUDGES_VARIANT_SINGLE_ARM
    else:
        judges = DOMAIN_JUDGES_VARIANT_A
    judgement = judges[domain["id"]](signals)
    direction = str(raw.get("direction_of_bias", "NA")).strip() or "NA"

    result: dict[str, Any] = {
        "signals": signals,
        "rationales": rationales,
        "judgement": judgement,
        "direction": direction,
    }
    if domain.get("variant_signals"):
        result["variant"] = variant
    return result


def run(pdf_bytes: bytes,
        extracted_fields: dict[str, str],
        classification: dict[str, str],
        primary_outcome: str,
        *,
        llm_call: Callable[[bytes, str, int], dict[str, Any]],
        target_pico: dict[str, str] | None = None,
        progress: Callable[[int], None] | None = None,
        ) -> tuple[dict[str, Any], str, str]:
    """Run ROBINS-I V2 against a non-randomized study.

    Pipeline:
      1. Preflight (B1/B2/B3 + C4) — single LLM call.
      2. If B2=Y/PY or B3=Y/PY → return Critical immediately (skip domains).
      3. Otherwise per-domain assessments (Domain 1 dispatched by Variant).

    Returns ``(domain_results, overall_judgement, overall_direction)``.
    """
    study_type = classification.get("study_type", "Cohort Study")

    if progress:
        try:
            progress(0)
        except Exception:
            pass

    preflight = run_preflight(pdf_bytes, study_type, primary_outcome,
                              extracted_fields, llm_call)
    domain_results: dict[str, Any] = {"preflight": preflight}

    if preflight["screening_decision"] == "critical":
        return domain_results, "Critical", "Unpredictable"

    variant = preflight["variant"]
    for domain in DOMAINS:
        if progress:
            try:
                progress(domain["id"])
            except Exception:
                pass
        result = _assess_domain(pdf_bytes, domain, variant, study_type,
                                primary_outcome, extracted_fields,
                                llm_call=llm_call, target_pico=target_pico)
        result["id"] = domain["id"]
        result["name"] = domain["name"]
        domain_results[str(domain["id"])] = result

    domain_judgements = [domain_results[str(d["id"])]["judgement"] for d in DOMAINS]
    overall = robins_i_overall(domain_judgements)

    dirs = [domain_results[str(d["id"])]["direction"]
            for d in DOMAINS
            if domain_results[str(d["id"])]["direction"] not in ("", "NA")]
    if not dirs:
        overall_direction = "NA"
    else:
        counts = Counter(dirs).most_common()
        if len(counts) > 1 and counts[0][1] == counts[1][1]:
            overall_direction = "Unpredictable"
        else:
            overall_direction = counts[0][0]

    return domain_results, overall, overall_direction
```

---

## 14. Quick test sketches

Plain `assert` statements (no framework) — drop these at the bottom of the reference module and run with `python3 robins_i_v2.py` to confirm the decision trees + aggregation logic behave as documented.

```python
# ─────────────────────────────────────────────
# Domain 1 — Variant A (ITT)
# ─────────────────────────────────────────────
# All-yes path → Low (with variant label)
assert domain1_variant_a_judge({"1A.1": "Y", "1A.2": "Y", "1A.3": "N", "1A.4": "N"}) == LOW_D1
# Negative-control hit on 1A.4 with otherwise-clean → Serious
assert domain1_variant_a_judge({"1A.1": "Y", "1A.2": "Y", "1A.3": "N", "1A.4": "Y"}) == "Serious"
# Strong-no on 1A.1 → Serious
assert domain1_variant_a_judge({"1A.1": "SN", "1A.2": "Y", "1A.3": "N", "1A.4": "N"}) == "Serious"
# Strong-no on 1A.1 + negative-control hit → Critical
assert domain1_variant_a_judge({"1A.1": "SN", "1A.2": "Y", "1A.3": "N", "1A.4": "Y"}) == "Critical"
# Weak-no on 1A.1 (floor: Moderate, not Low)
assert domain1_variant_a_judge({"1A.1": "WN", "1A.2": "Y", "1A.3": "N", "1A.4": "N"}) == "Moderate"

# ─────────────────────────────────────────────
# Domain 1 — Variant B (per-protocol)
# ─────────────────────────────────────────────
assert domain1_variant_b_judge({"1B.1": "Y", "1B.2": "Y", "1B.3": "Y", "1B.4": "N", "1B.5": "N"}) == LOW_D1
# Inappropriate analysis method (1B.1 N) → Serious or Critical
assert domain1_variant_b_judge({"1B.1": "N", "1B.2": "Y", "1B.3": "Y", "1B.4": "N", "1B.5": "N"}) == "Serious"
assert domain1_variant_b_judge({"1B.1": "N", "1B.2": "Y", "1B.3": "Y", "1B.4": "Y", "1B.5": "N"}) == "Critical"

# ─────────────────────────────────────────────
# Domain 1 — Single-arm variant
# ─────────────────────────────────────────────
assert domain1_variant_single_arm_judge({"1S.1": "Y", "1S.2": "Y", "1S.3": "Y", "1S.4": "NA", "1S.5": "N"}) == LOW_D1_SA
# 1S.5 dominates — falsification-control hit → Critical
assert domain1_variant_single_arm_judge({"1S.1": "Y", "1S.2": "Y", "1S.3": "Y", "1S.4": "NA", "1S.5": "Y"}) == "Critical"
# No benchmark + no quantitative adjustment → Critical
assert domain1_variant_single_arm_judge({"1S.1": "N", "1S.2": "NI", "1S.3": "NA", "1S.4": "N", "1S.5": "N"}) == "Critical"
# Benchmark + prognostic mismatch + quantitative adjustment → Moderate
assert domain1_variant_single_arm_judge({"1S.1": "Y", "1S.2": "Y", "1S.3": "SN", "1S.4": "Y", "1S.5": "N"}) == "Moderate"

# ─────────────────────────────────────────────
# Domain 2 cohort
# ─────────────────────────────────────────────
# Strategies distinguishable + no diff/non-diff misclassification → Low
assert domain2_judge({"2.1": "Y", "2.2": "NA", "2.3": "NA", "2.4": "N", "2.5": "N"}) == "Low"
# Bottom tier (2.3 N) + 2.4 SY → Critical via direct-route
assert domain2_judge({"2.1": "N", "2.2": "N", "2.3": "N", "2.4": "SY", "2.5": "N"}) == "Critical"

# ─────────────────────────────────────────────
# Domain 2 single-arm
# ─────────────────────────────────────────────
# Per-protocol completers cohort definition → Critical
assert domain2_variant_single_arm_judge({"2S.1": "Y", "2S.2": "Y", "2S.3": "SY"}) == "Critical"
# ITT cohort + well-defined intervention + good recording → Low
assert domain2_variant_single_arm_judge({"2S.1": "Y", "2S.2": "Y", "2S.3": "N"}) == "Low"

# ─────────────────────────────────────────────
# Domain 3 — selection
# ─────────────────────────────────────────────
# Best case: no immortal time, no other selection bias → Low
assert domain3_judge({"3.1": "Y", "3.2": "N", "3.3": "N",
                      "3.4": "NA", "3.5": "NA",
                      "3.6": "NA", "3.7": "NA", "3.8": "NA"}) == "Low"

# ─────────────────────────────────────────────
# Domain 4 — missing data
# ─────────────────────────────────────────────
# All complete data → Low directly
assert domain4_judge({"4.1": "Y", "4.2": "Y", "4.3": "Y", "4.4": "NA",
                      "4.5": "NA", "4.6": "NA", "4.7": "NA", "4.8": "NA",
                      "4.9": "NA", "4.10": "NA", "4.11": "NA"}) == "Low"

# ─────────────────────────────────────────────
# Domain 5 — outcome measurement
# ─────────────────────────────────────────────
assert domain5_judge({"5.1": "N", "5.2": "N", "5.3": "NA"}) == "Low"
# Differential measurement → Serious directly
assert domain5_judge({"5.1": "Y", "5.2": "N", "5.3": "NA"}) == "Serious"

# ─────────────────────────────────────────────
# Domain 6 — selective reporting
# ─────────────────────────────────────────────
# Pre-determined plan → Low
assert domain6_judge({"6.1": "Y", "6.2": "N", "6.3": "N", "6.4": "N"}) == "Low"
# Two selective-reporting flags → Critical
assert domain6_judge({"6.1": "N", "6.2": "Y", "6.3": "Y", "6.4": "N"}) == "Critical"

# ─────────────────────────────────────────────
# Preflight short-circuit logic
# ─────────────────────────────────────────────
def _preflight_decide(b2, b3, c4, study_type):
    """Inline mirror of the dispatcher logic for unit-testing without an LLM."""
    is_sa = study_type in SINGLE_ARM_STUDY_TYPES
    variant = "single_arm" if is_sa else ("B" if c4 == "Yes" else "A")
    if b2 in ("Y", "PY"):
        return {"screening_decision": "critical", "variant": variant}
    if b3 in ("Y", "PY"):
        return {"screening_decision": "critical", "variant": variant}
    return {"screening_decision": "proceed", "variant": variant}

# B2 = Y → Critical
assert _preflight_decide("Y", "N", "No", "Cohort Study")["screening_decision"] == "critical"
assert _preflight_decide("PY", "N", "No", "Cohort Study")["screening_decision"] == "critical"
# B3 = Y → Critical
assert _preflight_decide("NA", "Y", "No", "Cohort Study")["screening_decision"] == "critical"
# All clean → proceed
assert _preflight_decide("NA", "N", "No", "Cohort Study")["screening_decision"] == "proceed"
# C4 = No → Variant A
assert _preflight_decide("NA", "N", "No", "Cohort Study")["variant"] == "A"
# C4 = Yes → Variant B
assert _preflight_decide("NA", "N", "Yes", "Cohort Study")["variant"] == "B"
# Single-arm study type pins variant regardless of C4
assert _preflight_decide("NA", "N", "No", "Single-Arm Trial")["variant"] == "single_arm"
assert _preflight_decide("NA", "N", "Yes", "Single-Arm Trial")["variant"] == "single_arm"
assert _preflight_decide("NA", "N", "Yes", "Dose-Escalation Study")["variant"] == "single_arm"

# ─────────────────────────────────────────────
# robins_i_overall — worst-domain aggregation
# ─────────────────────────────────────────────
assert robins_i_overall(["Low", "Low", "Low", "Low", "Low", "Low"]) == "Low"
# Variant-labeled Low normalizes to Low for ranking
assert robins_i_overall([LOW_D1, "Low", "Low", "Low", "Low", "Low"]) == "Low"
assert robins_i_overall([LOW_D1_SA, "Low", "Low", "Low", "Low", "Low"]) == "Low"
# Worst domain wins
assert robins_i_overall([LOW_D1, "Moderate", "Low", "Serious", "Low", "Low"]) == "Serious"
assert robins_i_overall(["Low", "Low", "Low", "Critical", "Low", "Low"]) == "Critical"
assert robins_i_overall([]) == "Low"

# ─────────────────────────────────────────────
# DOMAINS structural invariants
# ─────────────────────────────────────────────
assert len(DOMAINS) == 6
assert [d["id"] for d in DOMAINS] == [1, 2, 3, 4, 5, 6]

# Domain 1: 3 variants × (4 + 5 + 5) = 14 unique signals via variant_signals
d1 = DOMAINS[0]
assert set(d1["variant_signals"].keys()) == {"A", "B", "single_arm"}
assert len(d1["variant_signals"]["A"]) == 4
assert len(d1["variant_signals"]["B"]) == 5
assert len(d1["variant_signals"]["single_arm"]) == 5

# Domain 2: cohort variants share 5, single-arm has 3
d2 = DOMAINS[1]
assert d2["variant_signals"]["A"] == d2["variant_signals"]["B"]  # cohort shares
assert len(d2["variant_signals"]["A"]) == 5
assert len(d2["variant_signals"]["single_arm"]) == 3

# D3-D6 invariant (no variant_signals key)
for d in DOMAINS[2:]:
    assert "variant_signals" not in d

# Signal counts per invariant domain
assert len(DOMAINS[2]["signals"]) == 8   # D3
assert len(DOMAINS[3]["signals"]) == 11  # D4
assert len(DOMAINS[4]["signals"]) == 3   # D5
assert len(DOMAINS[5]["signals"]) == 4   # D6

# Variant-specific signal ID prefixes appear exactly where expected
all_d1_ids = {s["id"] for v in ("A", "B", "single_arm") for s in d1["variant_signals"][v]}
assert {"1A.1", "1A.2", "1A.3", "1A.4"}.issubset(all_d1_ids)
assert {"1B.1", "1B.2", "1B.3", "1B.4", "1B.5"}.issubset(all_d1_ids)
assert {"1S.1", "1S.2", "1S.3", "1S.4", "1S.5"}.issubset(all_d1_ids)

all_d2_ids = {s["id"] for v in ("A", "B", "single_arm") for s in d2["variant_signals"][v]}
assert {"2.1", "2.2", "2.3", "2.4", "2.5"}.issubset(all_d2_ids)
assert {"2S.1", "2S.2", "2S.3"}.issubset(all_d2_ids)

# ─────────────────────────────────────────────
# Cascade enforcement (Python-side NA gating) — V2
# ─────────────────────────────────────────────
# D1 Variant B: 1B.4 NA when 1B.1 Y/PY (appropriate analysis method)
out = enforce_cascade_d1_variant_b_v2({"1B.1": "Y", "1B.4": "Y"})
assert out["1B.4"] == "NA"
out = enforce_cascade_d1_variant_b_v2({"1B.1": "N", "1B.4": "Y"})
assert out["1B.4"] == "Y"  # kept when 1B.1 inappropriate

# D1 single-arm: 1S.3 NA when 1S.1 N/PN (no benchmark)
out = enforce_cascade_d1_variant_single_arm_v2({"1S.1": "N", "1S.3": "Y"})
assert out["1S.3"] == "NA"
out = enforce_cascade_d1_variant_single_arm_v2({"1S.1": "Y", "1S.3": "Y"})
assert out["1S.3"] == "Y"  # benchmark identified, 1S.3 applies

# D2 cohort: 2.2 NA when 2.1 Y/PY (strategies distinguishable)
out = enforce_cascade_d2_cohort_v2({"2.1": "Y", "2.2": "N"})
assert out["2.2"] == "NA"
out = enforce_cascade_d2_cohort_v2({"2.1": "N", "2.2": "Y"})
assert out["2.2"] == "Y"

# D3: 3.2 NA when 3.1 not Y/PY
out = enforce_cascade_d3_v2({"3.1": "WN", "3.2": "Y", "3.3": "N",
                             "3.4": "Y", "3.5": "Y", "3.6": "N",
                             "3.7": "N", "3.8": "N"})
assert out["3.2"] == "NA"  # 3.1 not Y/PY → 3.2 gated
# 3.4, 3.5 NA when 3.3 not Y/PY
assert out["3.4"] == "NA"
assert out["3.5"] == "NA"
# A raises concerns (3.1 WN) → 3.6 asked
assert out["3.6"] == "N"

# D3: 3.7 NA when 3.6 Y/PY (analysis fully corrected)
out = enforce_cascade_d3_v2({"3.1": "WN", "3.3": "N",
                             "3.6": "Y", "3.7": "Y", "3.8": "N"})
assert out["3.7"] == "NA"

# D3: 3.6/3.7/3.8 NA when no concerns (3.1 Y/PY AND 3.3 N/PN)
out = enforce_cascade_d3_v2({"3.1": "Y", "3.2": "N", "3.3": "N",
                             "3.6": "Y", "3.7": "Y", "3.8": "Y"})
assert out["3.6"] == "NA"
assert out["3.7"] == "NA"
assert out["3.8"] == "NA"

# D4 best case: complete data → all downstream NA
out = enforce_cascade_d4_v2({"4.1": "Y", "4.2": "Y", "4.3": "Y",
                             "4.4": "Y", "4.5": "Y", "4.7": "Y",
                             "4.11": "Y"})
for sid in ("4.4", "4.5", "4.6", "4.7", "4.8", "4.9", "4.10", "4.11"):
    assert out[sid] == "NA", f"D4 best-case didn't NA {sid}"

# D4 complete-case path (4.4 Y/PY): 4.7-4.10 NA, 4.5/4.6/4.11 kept
out = enforce_cascade_d4_v2({"4.1": "N", "4.2": "Y", "4.3": "Y",
                             "4.4": "Y", "4.5": "Y", "4.6": "Y",
                             "4.7": "Y", "4.8": "Y", "4.9": "Y",
                             "4.10": "Y", "4.11": "Y"})
for sid in ("4.7", "4.8", "4.9", "4.10"):
    assert out[sid] == "NA"
assert out["4.5"] == "Y"
assert out["4.6"] == "Y"
assert out["4.11"] == "Y"

# D4 complete-case path with 4.5 N/PN (no concerning exclusion): 4.6 NA
out = enforce_cascade_d4_v2({"4.1": "N", "4.2": "Y", "4.3": "Y",
                             "4.4": "Y", "4.5": "N", "4.6": "Y"})
assert out["4.6"] == "NA"

# D4 imputation path (4.4 N/PN, 4.7 Y/PY): 4.5/4.6/4.10 NA, 4.8/4.9 kept
out = enforce_cascade_d4_v2({"4.1": "N", "4.2": "Y", "4.3": "Y",
                             "4.4": "N", "4.5": "Y", "4.6": "Y",
                             "4.7": "Y", "4.8": "Y", "4.9": "Y",
                             "4.10": "Y", "4.11": "Y"})
assert out["4.5"] == "NA"
assert out["4.6"] == "NA"
assert out["4.10"] == "NA"
assert out["4.8"] == "Y"
assert out["4.9"] == "Y"

# D4 imputation path with 4.8 N/PN (MAR/MCAR unreasonable): 4.9 NA
out = enforce_cascade_d4_v2({"4.1": "N", "4.4": "N", "4.7": "Y",
                             "4.8": "N", "4.9": "Y", "4.10": "Y"})
assert out["4.9"] == "NA"

# D4 alternative-method path (4.4 N/PN, 4.7 N/PN): 4.8/4.9 NA, 4.10 kept
out = enforce_cascade_d4_v2({"4.1": "N", "4.4": "N", "4.7": "N",
                             "4.8": "Y", "4.9": "Y", "4.10": "Y"})
assert out["4.8"] == "NA"
assert out["4.9"] == "NA"
assert out["4.10"] == "Y"

# D5: 5.3 NA when 5.2 N/PN (assessor blinded — no influence to assess)
out = enforce_cascade_d5_v2({"5.1": "N", "5.2": "N", "5.3": "SY"})
assert out["5.3"] == "NA"
out = enforce_cascade_d5_v2({"5.1": "N", "5.2": "Y", "5.3": "SY"})
assert out["5.3"] == "SY"  # 5.2 Y/PY → 5.3 applies

# Dispatch helper: D1 Variant A + D2 single-arm + D6 unchanged
assert enforce_cascade_v2(1, {"1A.1": "Y"}, variant="A") == {"1A.1": "Y"}
assert enforce_cascade_v2(2, {"2S.1": "Y"}, variant="single_arm") == {"2S.1": "Y"}
assert enforce_cascade_v2(6, {"6.1": "N"}, variant="A") == {"6.1": "N"}

# Integration: LLM inconsistency is caught
# LLM answered 3.2 = "Y" even though 3.1 = "SN" (which gates 3.2 out)
llm_response = {"3.1": "SN", "3.2": "Y", "3.3": "N",
                "3.6": "Y", "3.8": "N"}
enforced = enforce_cascade_d3_v2(llm_response)
assert enforced["3.2"] == "NA"  # cascade override caught LLM error
assert enforced["3.4"] == "NA"  # 3.3 N → 3.4 NA
assert enforced["3.5"] == "NA"
# 3.1 SN → A concerns → 3.6 asked; 3.6 Y → 3.7 NA
assert enforced["3.7"] == "NA"
# Tree should still produce a sensible judgement
result = domain3_judge(enforced)
assert result in ("Low", "Moderate", "Serious", "Critical")

print("All ROBINS-I V2 sanity checks passed.")
```

---

## 15. Implementation notes for other platforms

### PDF attachment

The reference implementation assumes a **vision-capable LLM** that can ingest PDF bytes directly (Claude PDF beta, Gemini direct upload, OpenAI vision). For text-only models, pre-extract the PDF text with `pypdf` (or equivalent) and inline the text in the prompt. ROBINS-I V2 papers are typically 8–25 pages with dense methods sections; chunking shouldn't be needed for most papers.

### Preflight short-circuit semantics

When B2 = Y/PY or B3 = Y/PY trips, skip all 6 domain LLM calls and return Critical immediately. This is a substantial cost saving (1 LLM call vs 7) and is methodologically correct — the cribsheet itself routes both paths directly to Critical without further analysis. Preserve the preflight rationales as evidence in the final result.

### Variant routing

Single-arm is pinned at `run()` entry from `classification["study_type"]` — **not** preflight-determined. C4 is still asked in the single-arm preflight (for downstream interpretation of D2-SA question 2S.3) but doesn't change variant routing for uncontrolled designs. Cohort A vs B is determined by preflight C4 (No → A, Yes → B).

### 4-level judgement scale + GRADE downgrade

Typical mapping when feeding ROBINS-I V2 results into GRADE:

- Low / Low (except…) → 0 GRADE downgrade levels (observational studies start at Low GRADE anyway)
- Moderate → 1 (some reviews use 0 here when the moderate concerns are non-systematic)
- Serious → 1–2 (cribsheet allows reviewers to keep observational evidence at Low GRADE if Serious bias is the only concern)
- Critical → 2, or the GRADE handbook allows "rated very low regardless of starting level"

Observational designs already start at "Low" GRADE — so a "Serious" RoB does not automatically bottom out the GRADE level. Confirm your synthesis protocol's specific mapping before applying.

### Target PICO threading

Optional but recommended for review-context applicability. Domain 1's confounding-by-indication assessment sharpens significantly when the prompt knows what comparison the systematic review is asking about (e.g., "drug X vs drug Y" vs "drug X vs no treatment"). Pass via `target_pico = {"population": "...", "intervention": "...", "comparator": "...", "outcome": "..."}`.

### Direction-of-bias collection

V2 collects `direction_of_bias` per domain (6 legal values — see §10.4). This is useful for narrative synthesis (e.g., "5 of 7 domains favoured intervention → estimate likely upward-biased") but is **not** used by the worst-domain RoB aggregator. The orchestrator computes overall direction as the modal value across domains; ties → "Unpredictable".

### LLM-side error tolerance

`_normalise_answer`-style tolerance (case-insensitive token matching, fallback to NI on unknown tokens) is essential — LLMs sometimes emit `"yes"` instead of `"Y"`, `"PYes"`, or other near-miss spellings. The reference implementation validates against the per-question allowed-options set and defaults to `NI` (or the first allowed token if NI is not in the subset) on mismatch.

### Conservative tree notes

The per-variant trees are conservative — they map borderline answer patterns toward the worse judgement rather than the better one. This reflects the bias-of-bias-assessment problem: LLMs tend to be charitable in their signal answers, so a conservative tree compensates. Reviewers can override single-domain judgements based on materiality during write-up; surface the per-signal rationales prominently to support that override.

### Out of scope (v1)

- **Randomized trials** — use RoB 2 (separate tool).
- **Diagnostic test accuracy** — use QUADAS-2 or QUADAS-3 (separate tools).
- **Systematic reviews of reviews** — use AMSTAR-2 (separate tool).
- **GRADE indirectness + imprecision** — these are separate single-trial modules built around PICO; they are not bundled here. They are documented in full, along with the certainty ladder and the ROBINS-I → downgrade-level mapping that consumes this tool's overall judgement, in [quality_appraisal_grade_shareable.md](quality_appraisal_grade_shareable.md) — the **per-paper** rating. When this tool's per-study judgements instead feed a pooled body of evidence, the consuming component is the GRADE agent in [grade_certainty_shareable.md](grade_certainty_shareable.md), which aggregates the per-study labels by pooled weight rather than reading one paper's judgement. Note in particular that Domain 1's `Low (except for concerns about uncontrolled confounding)` / `… benchmarking)` labels must be normalised to plain `Low` by the overall aggregator before they reach that mapping.
- **Reviewer override UI** — the trees are deterministic; provide your own UI for human override based on the returned rationales.
- **Quasi-experimental designs** (Uncontrolled Before-After, Interrupted Time Series, Difference-in-Differences, Regression Discontinuity) — each warrants its own confounding prompt. ROBINS-I V2 is the best-available approximation for those designs but a methodologically pure assessment would require a design-specific tool.

---

## 16. ROBINS-I V1 — see the dedicated V1 shareable

V1 was the original 1 August 2016 ROBINS-I cribsheet (Sterne et al., BMJ 2016;355:i4919). V2 supersedes it, but many published systematic reviews still use V1 — and some teams (notably the OVID systematic-review team) have ongoing V1 workflows that need to be maintained.

The full V1 reference — including all 7 domains × signaling questions verbatim, V1 Tables 1/2/3 (the narrative judgement rules), a turnkey single-file Python reference implementation, test sketches, and operational config notes — lives in its own shareable doc:

**[docs/shareable/robins_i_v1_shareable.md](./robins_i_v1_shareable.md)**

That doc is fully self-contained — it does not require any of the V2 content here. The V1 shareable also contains the V1 → V2 differences + migration guidance, so readers maintaining a V1 pipeline who want to know what's changed in V2 should read the V1 doc's §18.

### 16.1 V1 vs V2 — short comparison

At a glance:

| Aspect | V1 (1 Aug 2016) | V2 (20 Nov 2025) |
|--|--|--|
| Domain count | **7** | **6** (V1 D4 retired — folded into V2 D1 Variant B) |
| Signal vocabulary | Y / PY / PN / N / NI | Y / PY / PN / N / NI + **WN / SN / WY / SY** |
| Per-domain judgement scale | **5-level** (adds "No information") | **4-level** (NI retired as a judgement) |
| Variant support for D1 | Single tree, two paths (baseline-only vs time-varying) chosen by 1.2/1.3 cascade | Three explicit variants: A (ITT), B (per-protocol), single_arm (uncontrolled) |
| Preflight | None | Mandatory B1/B2/B3 + C4 with two short-circuits to Critical |
| Aim-of-study handling | Stage-II checkbox gates D4 question subset | Preflight C4 selects D1 variant A vs B; D4 no longer exists |
| Direction-of-bias options | 3 (D1) or 5 (D2+) | 6 (uniform across domains) + 1 overall (modal) |
| Single-arm support | Not addressed — out of scope for V1 | Project-specific single_arm variant of D1 + D2 |

See the V1 shareable's §18 for the full domain mapping table and mechanical V1 → V2 migration translation.

---

## 17. Cascade enforcement — design notes

V2 has cascading signaling questions in Domains 1B (per-protocol), 1-single-arm, 2 (cohort), 3, 4, and 5 — where one question's answer determines whether a downstream question is gated out (NA). This implementation enforces those rules in pure Python via the `enforce_cascade_d*_v2()` functions in §13, called from `_assess_domain()` after the LLM responds and before the decision tree judges. The LLM is asked to answer every signaling question based solely on its reading of the paper; Python then overrides any answer to `NA` for questions the cascade rules indicate are gated out.

### 17.1 Why Python-side, not LLM-side

Same rationale as the V1 shareable's §18.1 (this design choice was made consistently across both V1 and V2):

1. **Cascade rules are deterministic** — they come from the cribsheet itself ("Only asked if 3.1 was Y/PY", "Asked when 1B.1 N/PN/NI has been used", etc.). There is no judgement call about when a question is NA. Putting deterministic logic in Python is the right separation of concerns: LLM answers "what does the paper say?", Python answers "what does the cribsheet say to ask next?".
2. **LLMs sometimes answer gated questions inconsistently.** Without Python enforcement, an LLM that answers 3.2 = "Y" when 3.1 = "SN" (which gates 3.2 out) silently routes the decision tree into the wrong branch.
3. **Catches LLM inconsistency at runtime.** Overrides are logged at `logger.debug` level; downstream judgement uses the cascade-enforced signals, so the same paper always produces the same tree path regardless of LLM stochasticity.
4. **The prompt is simpler.** Instead of explaining the cascading-question rules to the LLM in the prompt, the prompt just says "answer each question based on the paper; Python applies the cascade rules". This is especially valuable for V2's D4, where the missing-data cascade has three paths (complete-case, imputation, alternative method) — asking the LLM to compute the path itself wastes tokens and introduces errors.
5. **Cost-neutral.** The LLM still answers every signal in one call (one LLM call per domain). The trade-off is that the LLM occasionally wastes tokens answering a question that will be overridden to NA — but for V2's cascading domains this is at most ~10 unnecessary signals per paper (D3 has 5 cascade rules, D4 has up to 6, D5 has 1, D2 cohort has 1, D1B/single-arm have 1 each). The cost is negligible (~$0.002 per paper); the gain is determinism + LLM-error catching.

### 17.2 Cascade rules per domain (V2)

The exact rules each `enforce_cascade_*_v2` function applies (cribsheet page references in parens):

- **D1 Variant A** (cribsheet p20): no cascade rules — every question always asked. `enforce_cascade_v2(1, signals, variant="A")` returns the signals unchanged.

- **D1 Variant B** (cribsheet p24):
  - `1B.4` NA unless `1B.1 ∈ {N, PN, NI}` (the question is only asked when an inappropriate analysis method has been used).

- **D1 Variant single-arm** (project extension, single-arm-trial methodology):
  - `1S.3` NA when `1S.1 ∈ {N, PN}` (no benchmark identified → prognostic comparability to benchmark is undefined).

- **D2 cohort** (cribsheet p28):
  - `2.2` NA unless `2.1 ∈ {N, PN, NI}` (only asked when strategies were not distinguishable at follow-up start, to assess the misclassification's effect on outcome events).

- **D2 single-arm**: no cascade rules — every question always asked.

- **D3** (cribsheet p32) — 5 cascade rules:
  - `3.2` NA unless `3.1 ∈ {Y, PY}`
  - `3.4` NA unless `3.3 ∈ {Y, PY}`
  - `3.5` NA unless `3.4 ∈ {Y, PY}`
  - `3.6`, `3.7`, `3.8` all NA unless subsection A or B raised concerns (A: 3.1 WN/SN/NI OR (3.1 Y/PY AND 3.2 Y/PY); B: 3.3 Y/PY/NI)
  - `3.7` NA when `3.6 ∈ {Y, PY}` (analysis fully corrected → no need for sensitivity analysis)

- **D4** (cribsheet p38) — most complex cascade (3-way path selection):
  - Best case: `4.1 + 4.2 + 4.3` all `∈ {Y, PY}` → `4.4 through 4.11` all NA (complete data on all three variables).
  - Otherwise, `4.4` is the complete-case path selector:
    - `4.4 ∈ {Y, PY, NI}` (complete case): `4.7-4.10` NA; `4.5/4.6/4.11` asked; `4.6` NA if `4.5 ∈ {N, PN}` (no concerning exclusion).
    - `4.4 ∈ {N, PN}` (not complete case): `4.5/4.6` NA; `4.7` is the imputation path selector:
      - `4.7 ∈ {Y, PY}` (imputation): `4.10` NA; `4.8/4.9/4.11` asked; `4.9` NA if `4.8 ∉ {Y, PY}` (MAR/MCAR not reasonable).
      - `4.7 ∈ {N, PN}` (alternative method): `4.8/4.9` NA; `4.10/4.11` asked.
      - `4.7 = NI`: tree handles conservatively, leave 4.8-4.10 as-is.

- **D5** (cribsheet p41):
  - `5.3` NA when `5.2 ∈ {N, PN}` (assessors blinded → no influence to assess; note: NI counts as "applicable").

- **D6** (cribsheet p47): no cascade rules — every question always asked.

### 17.3 Operational notes for deployment

- **Override logging.** The orchestrator builds an `overrides` dict comparing pre-cascade and post-cascade signals, and logs it at `logger.debug`. If you want to monitor LLM-cascade inconsistency in production, raise the log level to `INFO`/`WARNING` or hook the override dict into your telemetry pipeline. High override rates on a specific question may indicate a prompt issue worth investigating.

- **Decision tree compatibility.** The decision trees in §3–§9 already handle `NA` correctly — they use `signals.get(sid, "NI")` lookups and check `in ("Y", "PY")` / `in ("N", "PN")` membership, so `NA` falls through to the same path as unknown tokens. No tree changes are needed.

- **Variant-aware dispatch.** `enforce_cascade_v2(domain_id, signals, variant)` takes the variant kwarg because D1 cascade rules differ between Variant A (no cascade), Variant B (1B.4 gating), and single-arm (1S.3 gating). Don't strip the variant kwarg if you wire this into a different orchestrator.

- **JSON shape unchanged.** The expected JSON output shape (§11) still includes `NA` as a valid token for gated questions — the LLM is allowed to answer NA if it correctly identifies a gated question, but Python doesn't require it to.

- **Consistency with V1.** The V1 shareable applies the same pattern in its §18; both docs use the same `enforce_cascade_*` naming convention and the same per-domain dispatcher idiom.
