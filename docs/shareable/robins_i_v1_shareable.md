# ROBINS-I V1 — Sharable Methodology Reference for OVID_

A self-contained reference for implementing an automated ROBINS-I V1 (1 August 2016) risk-of-bias assessment of non-randomized studies of interventions. Contains:

- Signaling questions (verbatim from the V1 cribsheet) for all 7 domains
- Decision-tree logic as plain Python 
- LLM prompt templates (the exact strings sent to the model)
- Expected JSON output shapes
- V1 judgement tables (Tables 1, 2, 3) verbatim from the cribsheet


**Source:** Sterne JAC, Hernán MA, Reeves BC, Savović J, Berkman ND, Viswanathan M, Henry D, Altman DG, Ansari MT, Boutron I, Carpenter JR, Chan A-W, Churchill R, Hróbjartsson A, Kirkham J, Jüni P, Loke YK, Pigott TD, Ramsay CR, Regidor D, Rothstein HR, Sandhu L, Santaguida PL, Schünemann HJ, Shea B, Shrier I, Tugwell P, Turner L, Valentine JC, Waddington H, Waters E, Whiting P, Higgins JPT. *The Risk Of Bias In Non-randomized Studies — of Interventions (ROBINS-I) assessment tool (version for cohort-type studies).* Version 1 August 2016. Underlying paper: Sterne JAC et al., BMJ 2016;355:i4919.

**Scope:** ROBINS-I V1 (1 August 2016 cohort-type studies template) **plus a project-specific single-arm adaptation** (§19) that mirrors V2's `single_arm` variant for V1's 5-token vocab and 5-level judgement scale. Out of scope: ROBINS-I V2 (20 November 2025 — superseded V1; covered in a separate shareable at `docs/shareable/robins_i_v2_shareable.md`); ROB 2 (which is for RCTs, separate tool); systematic-review assessment (AMSTAR-2, separate tool); QUADAS-2 / QUADAS-3 (for diagnostic test accuracy, separate tools).

**Single-arm adaptation (project-specific, not in the original cribsheet).** The V1 cribsheet (1 August 2016) covers only cohort-type studies — single-arm / dose-escalation designs are out of its published scope. This implementation adds a single-arm variant (§19) so users on the V1 toolchain (e.g. the OVID team) can appraise single-arm papers without having to switch to V2 mid-review. The adaptation mirrors V2's pattern: Domain 1 is reframed as **benchmark adequacy + prognostic-mix comparability**; Domain 2 is reframed as **intervention fidelity + intent-vs-received cohort definition**; Domain 4 (Deviations from intended interventions) is set to NA in code with no LLM call (V2 retired this domain entirely; its concerns are folded into D2-SA's 2S.3). D3, D5, D6, D7 reuse cohort signals unchanged. V1's 5-token vocab (Y/PY/PN/N/NI — no WN/SN/WY/SY gradient) requires a conservative collapse documented in §19.7. The adaptation is **not officially endorsed by the ROBINS-I V1 authors** — it's a best-effort extension by the AI Researcher team for use on the V1 toolchain. The published Cochrane guidance for single-arm risk-of-bias is V2's `single_arm` variant (separate shareable).

**Conservative-tree note.** V1 expresses its domain judgements as **narrative tables** (Tables 1 + 2 in §11), not as deterministic decision trees. The Python trees in §15 are conservative interpretations of those tables; reviewers will sometimes legitimately override the algorithmic mapping based on materiality, "very strongly related" judgements, and other prose-table judgement calls. §17.5 details every place the trees are charitable or conservative interpretations of the cribsheet narrative.

**V1 has no preflight stage in the original cribsheet.** Unlike V2 (which runs a B1/B2/B3 + C4 preflight before any domain assessment), V1 unconditionally assesses all 7 domains. The aim of study (assessing effect of *assignment to* intervention vs effect of *starting and adhering to* intervention) is set once at the Stage-II study-spec stage and gates Domain 4 question selection. **This implementation adds one optional preflight LLM call — the aim preflight (§1.1) — that auto-determines the Stage-II aim from the paper, mechanically equivalent to V2's C4 question.** Reviewers may still set the aim manually; auto-determination only runs when no manual value is supplied.

**Production integration in The AI Researcher.** This shareable doc is now mirrored by [backend/rob_tools/robins_i_v1.py](../../backend/rob_tools/robins_i_v1.py), the production module that runs inside the Quality Appraisal pipeline. V1 is selectable per-run alongside V2 (V2 is the default) via the ROBINS-I version radio in the run-create modal. The choice persists on `quality_appraisal_runs.robins_i_tool_choice` (`NULL` = V2 default for back-compat). Single-arm / Dose-Escalation papers now also honour the toggle: when V1 is selected, the project-specific single-arm variant (§19) runs; when V2 is selected (or the toggle is unset), V2's `single_arm` variant runs as before. The production module returns the 3-tuple `(domain_results, overall, direction)` expected by the existing tool-runner contract; the aim-preflight payload (`{aim, rationale}`) is stashed in `domain_results["aim_preflight"]` so the per-paper detail view can surface how the aim was chosen (mirrors V2's `domain_results["preflight"]` convention). The reference implementation in §15 below returns a 4-tuple with `aim_rationale` as a top-level element instead — both forms are equivalent; the production wrapper just moves the rationale inside the dict to satisfy the existing call site.

**Cascade enforcement is in Python, not the LLM.** V1 has cascading signaling questions in Domains 1, 2, 4, and 5 — where one question's answer determines whether a downstream question is asked (NA). These cascade rules are deterministic from the cribsheet, so this implementation enforces them in pure Python (`enforce_cascade_dN_v1` functions in §15) AFTER the LLM has answered. The LLM is asked to answer every question based on its reading of the paper; Python then overrides any LLM answer to `NA` when the cribsheet's cascade rules indicate the question is gated out. This (1) prevents the LLM from incorrectly answering a substantive question when the cascade says NA, (2) catches LLM inconsistency between gating-question and downstream-question answers, and (3) ensures the same paper always produces the same cascade structure regardless of LLM stochasticity. See §18 for the design rationale and the per-domain cascade rules.

---

**Assessment scope: one assessment per (study × outcome).** This instrument rates a *result*, not a paper. Several of its signalling questions — missing outcome data, measurement of the outcome, and selection of the reported result — are answered differently for different outcomes in the same study, so one trial can be *Low* for all-cause mortality and *High* for an unblinded symptom score. Run the whole instrument once per outcome you intend to report, passing that outcome as the assessed outcome, and store one judgement per (study × outcome). Reusing a single paper-level judgement across every outcome attaches a rating to outcomes it was never made about, and nothing in the output reveals that it happened. Only the instrument call repeats: classification and field extraction that feed the prompts are outcome-independent and run once per study.

## 1. Signal answer options

V1 uses a 5-token signal vocabulary:

```python
SIGNAL_OPTIONS_V1 = ("Y", "PY", "PN", "N", "NI")
# Y  = Yes
# PY = Probably yes
# PN = Probably no
# N  = No
# NI = No information
```

Some questions are gated on prior answers; in those cases the cribsheet additionally allows `NA` (not applicable). One question — Domain 1 question 1.1 — uniquely **excludes NI** (the cribsheet says "There is no NI (No information) option for this signalling question" — you must commit to Y/PY/PN/N).

V1 does NOT use the weak/strong tokens (WN, SN, WY, SY) that V2 added.

Helper predicates used by the decision trees:

```python
def _yes(ans: str) -> bool:
    return ans in ("Y", "PY")


def _no(ans: str) -> bool:
    return ans in ("N", "PN")


def _no_info(ans: str) -> bool:
    return ans == "NI"
```

**Domain judgement scale (5-level):**

```python
JUDGEMENTS_V1 = ("Low", "Moderate", "Serious", "Critical", "No information")
```

Note "No information" is a full domain judgement in V1 (V2 retired it as a judgement; in V2 NI is only a valid signal answer).

**Aim of study:** set at the Stage-II study-spec stage. One of two values:

```python
AIMS = ("assignment_to", "starting_and_adhering")
# assignment_to        = assessing the effect of assignment to intervention (ITT)
# starting_and_adhering = assessing the effect of starting and adhering to intervention (per-protocol)
```

The aim gates Domain 4 (Bias due to deviations from intended interventions):
- `assignment_to` → answer 4.1, 4.2 (2 questions)
- `starting_and_adhering` → answer 4.3, 4.4, 4.5, 4.6 (4 questions)

The aim does NOT gate Domain 1 in V1 (V2's variant routing replaces this).

### 1.1 Aim preflight (LLM-determined)

The cribsheet expects the aim to be committed at Stage II — historically a reviewer-supplied checkbox set before any domain assessment. This implementation adds one **optional** LLM call that auto-determines the aim from the paper, so V1 can be run end-to-end without a manual study-spec step. The auto-determination is **mechanically equivalent** to V2's C4 preflight question (does the analysis account for protocol deviations during follow-up?) — only the output mapping differs (V2 routes to Variant A vs B; V1 sets `assignment_to` vs `starting_and_adhering`).

**Manual override remains supported.** Reviewers who want to commit the aim before the run (e.g. because they're appraising a specific pre-registered analysis) pass `aim="assignment_to"` or `aim="starting_and_adhering"` directly to `run_v1` and the preflight is skipped. Auto-determination only runs when `aim=None`.

**Decision logic** (deterministic Python wrapper around the LLM answer):
- Paper's analysis is ITT-like (analyses everyone as-randomized / as-assigned, ignores switches and crossovers) → `assignment_to`
- Paper's analysis is per-protocol-like (censors at discontinuation / switch, restricts to adherers, applies IPCW / g-methods / marginal structural models / instrumental-variable analysis for adherence) → `starting_and_adhering`
- Paper reports both (ITT primary + per-protocol sensitivity, or vice versa) → pick the headline / primary estimate the appraisal is targeting
- Observational cohort defaulting (exposed = self-selected starter) → typically `starting_and_adhering` unless the analysis defines exposure on first-prescription-style ITT-like grounds
- Genuinely ambiguous (analysis section is silent on protocol-deviation handling) → default `assignment_to` and surface the ambiguity in the rationale

**Prompt** — one call, JSON output, modeled on V2's `_build_preflight_prompt_cohort`:

```text
You are determining the **aim of study** for a ROBINS-I V1 risk-of-bias assessment of a non-randomized study.

Outcome being assessed: {primary_outcome}

Context (fields already extracted from the paper):
{ctx_json}

ROBINS-I V1 assesses risk of bias against a target estimand. The Stage-II aim of study commits to which estimand the appraisal targets:

- **"assignment_to"** — the analysis estimates the effect of *assignment to* intervention (intention-to-treat). Participants are analysed in the group they were originally assigned to; switches, crossovers, and non-adherence are ignored. This aim is set when the paper reports an as-randomized / as-assigned / mITT analysis as its primary estimate.
- **"starting_and_adhering"** — the analysis estimates the effect of *starting and adhering to* intervention (per-protocol). The analysis is restricted to (or weights toward) participants who actually started and adhered to the assigned intervention, and protocol deviations are accounted for via censoring, IPCW, g-methods, marginal structural models, or instrumental-variable estimation. This aim is set when the paper reports a per-protocol / as-treated / completer analysis as its primary estimate.

**Question.** Which aim does the primary analysis of the paper target for the outcome being assessed?

Elaboration:
- If the paper reports both an ITT and a per-protocol analysis, pick the aim that matches the **headline / primary estimate** for this outcome, not the sensitivity analysis.
- Observational cohort studies typically map to **"starting_and_adhering"** because exposure is defined by who actually started the treatment — unless the analysis explicitly uses an ITT-like exposure definition (e.g. first prescription regardless of refill).
- If the analysis section is genuinely silent on whether protocol deviations are accounted for, default to **"assignment_to"** and note the ambiguity in the rationale.

Return JSON with exactly this shape:
{{
  "aim": "assignment_to|starting_and_adhering",
  "rationale": "1-2 sentences quoting or paraphrasing the analysis-section text that supports the choice"
}}
```

**Python reference function** — see §15 (`determine_aim_v1`, `_build_aim_preflight_prompt_v1`).

---

## 2. Domain definitions

Domains are organized in temporal order (pre-intervention → at-intervention → post-intervention):

| Order | ID | Name | Stage | # signaling questions |
|--|--|--|--|--|
| 1 | 1 | Bias due to confounding | Pre-intervention | 8 (cascading) |
| 2 | 2 | Bias in selection of participants into the study | Pre-intervention | 5 |
| 3 | 3 | Bias in classification of interventions | At-intervention | 3 |
| 4 | 4 | Bias due to deviations from intended interventions | Post-intervention | 6 (2 + 4, aim-gated) |
| 5 | 5 | Bias due to missing data | Post-intervention | 5 |
| 6 | 6 | Bias in measurement of outcomes | Post-intervention | 4 |
| 7 | 7 | Bias in selection of the reported result | Post-intervention | 3 |

**Total: 34 signaling questions.** Per-paper effective question count depends on cascading (D1) + aim (D4): a typical cohort study answers ~22–26 questions.

---

## 3. Domain 1 — Bias due to confounding

8 signaling questions in a cascading structure. The cribsheet allows early-exit at 1.1 = N/PN (study judged Low without further questions).

- **1.1** Is there potential for confounding of the effect of intervention in this study?

  Response options: Y / PY / PN / N  *(note: no NI option — V1 D1.1 is the only question without NI)*

  *Elaboration:* In rare situations, such as when studying harms that are very unlikely to be related to factors that influence treatment decisions, no confounding is expected and the study can be considered to be at low risk of bias due to confounding, equivalent to a fully randomized trial. There is no NI (No information) option for this signalling question.

  **If N/PN to 1.1:** the study can be considered to be at low risk of bias due to confounding and no further signalling questions need be considered.

- **If Y/PY to 1.1: determine whether there is a need to assess time-varying confounding:**

  - **1.2** Was the analysis based on splitting participants' follow up time according to intervention received?

    Response options: NA / Y / PY / PN / N / NI

    *Elaboration:* If participants could switch between intervention groups then associations between intervention and outcome may be biased by time-varying confounding. This occurs when prognostic factors influence switches between intended interventions.

    **If N/PN to 1.2:** answer questions relating to baseline confounding (1.4 to 1.6).
    **If Y/PY to 1.2:** proceed to question 1.3.

  - **1.3** Were intervention discontinuations or switches likely to be related to factors that are prognostic for the outcome?

    Response options: NA / Y / PY / PN / N / NI

    *Elaboration:* If intervention switches are unrelated to the outcome, for example when the outcome is an unexpected harm, then time-varying confounding will not be present and only control for baseline confounding is required.

    **If N/PN to 1.3:** answer questions relating to baseline confounding (1.4 to 1.6).
    **If Y/PY to 1.3:** answer questions relating to both baseline and time-varying confounding (1.7 and 1.8).

- **Questions relating to baseline confounding only:**

  - **1.4** Did the authors use an appropriate analysis method that controlled for all the important confounding domains?

    Response options: NA / Y / PY / PN / N / NI

    *Elaboration:* Appropriate methods to control for measured confounders include stratification, regression, matching, standardization, and inverse probability weighting. They may control for individual variables or for the estimated propensity score. Inverse probability weighting is based on a function of the propensity score. Each method depends on the assumption that there is no unmeasured or residual confounding.

  - **1.5** If Y/PY to 1.4: Were confounding domains that were controlled for measured validly and reliably by the variables available in this study?

    Response options: NA / Y / PY / PN / N / NI

    *Elaboration:* Appropriate control of confounding requires that the variables adjusted for are valid and reliable measures of the confounding domains. For some topics, a list of valid and reliable measures of the confounding domains will be specified in the review protocol but for others such a list may not be available. Study authors may cite references to support the use of a particular measure. If authors control for variables with no indication of their validity or reliability pay attention to the subjectivity of the measure. Subjective measures (e.g. based on self-report) may have lower validity and reliability than objective measures such as lab findings.

  - **1.6** Did the authors control for any post-intervention variables that could have been affected by the intervention?

    Response options: NA / Y / PY / PN / N / NI

    *Elaboration:* Controlling for post-intervention variables that are affected by intervention is not appropriate. Controlling for mediating variables estimates the direct effect of intervention and may introduce bias. Controlling for common effects of intervention and outcome introduces bias.

- **Questions relating to baseline and time-varying confounding:**

  - **1.7** Did the authors use an appropriate analysis method that adjusted for all the important confounding domains and for time-varying confounding?

    Response options: NA / Y / PY / PN / N / NI

    *Elaboration:* Adjustment for time-varying confounding is necessary to estimate the effect of starting and adhering to intervention, in both randomized trials and NRSI. Appropriate methods include those based on inverse probability weighting. Standard regression models that include time-updated confounders may be problematic if time-varying confounding is present.

  - **1.8** If Y/PY to 1.7: Were confounding domains that were adjusted for measured validly and reliably by the variables available in this study?

    Response options: NA / Y / PY / PN / N / NI

    *Elaboration:* See 1.5 above.

- **Risk of bias judgement:** See Table 1 (§11). Options: Low / Moderate / Serious / Critical / NI.
- **Optional: predicted direction of bias due to confounding.** Options: Favours experimental / Favours comparator / Unpredictable. *(Note: V1 D1 has 3 direction options; D2–D7 have 5 — see §1.)*

Decision tree:

```python
def domain1_judge_v1(signals: dict[str, str]) -> str:
    """V1 D1 — Bias due to confounding. Cribsheet Table 1 row.

    Cascading structure:
      1.1 = N/PN → Low (early exit per cribsheet — no further questions)
      1.1 = Y/PY → assess 1.2/1.3 to choose baseline-only OR time-varying path
        baseline-only (1.4-1.6): both 1.2 and 1.3 in (N, PN, NI)
        time-varying  (1.7-1.8): 1.2 = Y/PY AND 1.3 = Y/PY
      1.1 = NI → "No information"
    """
    q1_1 = signals.get("1.1", "NI")
    if q1_1 in ("N", "PN"):
        return "Low"  # cribsheet early exit
    if q1_1 == "NI":
        return "No information"

    # 1.1 = Y/PY → continue
    q1_2 = signals.get("1.2", "NI")
    q1_3 = signals.get("1.3", "NI")

    # Time-varying path requires BOTH 1.2 Y/PY AND 1.3 Y/PY
    if q1_2 in ("Y", "PY") and q1_3 in ("Y", "PY"):
        q1_7 = signals.get("1.7", "NI")
        q1_8 = signals.get("1.8", "NI")
        if q1_7 in ("Y", "PY") and q1_8 in ("Y", "PY"):
            return "Moderate"
        if q1_7 in ("N", "PN") or q1_8 in ("N", "PN"):
            return "Serious"
        return "No information"

    # Baseline-only path (1.4-1.6)
    q1_4 = signals.get("1.4", "NI")
    q1_5 = signals.get("1.5", "NI")
    q1_6 = signals.get("1.6", "NI")

    # 1.6 Y/PY = over-adjustment for post-intervention vars → Serious
    if q1_6 in ("Y", "PY"):
        return "Serious"
    if q1_4 in ("N", "PN") or q1_5 in ("N", "PN"):
        return "Serious"
    if q1_4 in ("Y", "PY") and q1_5 in ("Y", "PY") and q1_6 in ("N", "PN"):
        return "Moderate"
    return "No information"
```

---

## 4. Domain 2 — Bias in selection of participants into the study

5 signaling questions.

- **2.1** Was selection of participants into the study (or into the analysis) based on participant characteristics observed after the start of intervention?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* This domain is concerned only with selection into the study based on participant characteristics observed *after* the start of intervention. Selection based on characteristics observed *before* the start of intervention can be addressed by controlling for imbalances between experimental intervention and comparator groups in baseline characteristics that are prognostic for the outcome (baseline confounding).

  **If N/PN to 2.1:** go to 2.4.

- **2.2** If Y/PY to 2.1: Were the post-intervention variables that influenced selection likely to be associated with intervention?

  Response options: NA / Y / PY / PN / N / NI

  *Elaboration:* Selection bias occurs when selection is related to an effect of either intervention or a cause of intervention AND an effect of either the outcome or a cause of the outcome. Therefore, the result is at risk of selection bias if selection into the study is related to both the intervention and the outcome.

- **2.3** If Y/PY to 2.2: Were the post-intervention variables that influenced selection likely to be influenced by the outcome or a cause of the outcome?

  Response options: NA / Y / PY / PN / N / NI

- **2.4** Do start of follow-up and start of intervention coincide for most participants?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* If participants are not followed from the start of the intervention then a period of follow up has been excluded, and individuals who experienced the outcome soon after intervention will be missing from analyses. This problem may occur when prevalent, rather than new (incident), users of the intervention are included in analyses.

- **2.5** If Y/PY to 2.2 and 2.3, or N/PN to 2.4: Were adjustment techniques used that are likely to correct for the presence of selection biases?

  Response options: NA / Y / PY / PN / N / NI

  *Elaboration:* It is in principle possible to correct for selection biases, for example by using inverse probability weights to create a pseudo-population in which the selection bias has been removed, or by modelling the distributions of the missing participants or follow up times and outcome events and including them using missing data methodology. However such methods are rarely used and the answer to this question will usually be "No".

- **Risk of bias judgement:** See Table 1 (§11). Options: Low / Moderate / Serious / Critical / NI.
- **Optional: predicted direction of bias due to selection of participants into the study.** Options: Favours experimental / Favours comparator / Towards null / Away from null / Unpredictable.

Decision tree:

```python
def domain2_judge_v1(signals: dict[str, str]) -> str:
    """V1 D2 — Bias in selection of participants. Cribsheet Table 1 row.

    Low: 2.1 N/PN (no post-intervention selection) AND 2.4 Y/PY (start of
         follow-up coincides with start of intervention).
    """
    q2_1 = signals.get("2.1", "NI")
    q2_4 = signals.get("2.4", "NI")
    q2_2 = signals.get("2.2", "NI")
    q2_3 = signals.get("2.3", "NI")
    q2_5 = signals.get("2.5", "NI")

    # Low: best case
    if q2_1 in ("N", "PN") and q2_4 in ("Y", "PY"):
        return "Low"

    # Adjustment rescues otherwise problematic selection (Moderate, not Low —
    # cribsheet says "below well-performed RCT")
    if q2_5 in ("Y", "PY"):
        return "Moderate"

    # 2.1 = Y/PY AND 2.2 = Y/PY AND 2.3 = Y/PY AND no adjustment → Serious
    # (Critical would require "very strongly related" — a reviewer judgement
    # not coded here; see §17.5)
    if q2_1 in ("Y", "PY") and q2_2 in ("Y", "PY") and q2_3 in ("Y", "PY"):
        return "Serious"

    # 2.4 N/PN without adjustment → Serious
    if q2_4 in ("N", "PN"):
        return "Serious"

    # NI on the key gating questions
    if q2_1 == "NI" and q2_4 == "NI":
        return "No information"

    # Default — some concerns, not severe
    return "Moderate"
```

---

## 5. Domain 3 — Bias in classification of interventions

3 signaling questions.

- **3.1** Were intervention groups clearly defined?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* A pre-requisite for an appropriate comparison of interventions is that the interventions are well defined. Ambiguity in the definition may lead to bias in the classification of participants. For individual-level interventions, criteria for considering individuals to have received each intervention should be clear and explicit, covering issues such as type, setting, dose, frequency, intensity and/or timing of intervention. For population-level interventions (e.g. measures to control air pollution), the question relates to whether the population is clearly defined, and the answer is likely to be 'Yes'.

- **3.2** Was the information used to define intervention groups recorded at the start of the intervention?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* In general, if information about interventions received is available from sources that could not have been affected by subsequent outcomes, then differential misclassification of intervention status is unlikely. Collection of the information at the time of the intervention makes it easier to avoid such misclassification. For population-level interventions (e.g. measures to control air pollution), the answer to this question is likely to be 'Yes'.

- **3.3** Could classification of intervention status have been affected by knowledge of the outcome or risk of the outcome?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* Collection of the information at the time of the intervention may not be sufficient to avoid bias. The way in which the data are collected for the purposes of the NRSI should also avoid misclassification.

- **Risk of bias judgement:** See Table 1 (§11). Options: Low / Moderate / Serious / Critical / NI.
- **Optional: predicted direction of bias due to measurement of outcomes or interventions.** Options: Favours experimental / Favours comparator / Towards null / Away from null / Unpredictable.

Decision tree:

```python
def domain3_judge_v1(signals: dict[str, str]) -> str:
    """V1 D3 — Bias in classification of interventions. Cribsheet Table 1 row.

    Low: 3.1 Y/PY (well-defined) AND 3.2 Y/PY (recorded at start) AND
         3.3 N/PN (not affected by outcome knowledge).
    """
    q3_1 = signals.get("3.1", "NI")
    q3_2 = signals.get("3.2", "NI")
    q3_3 = signals.get("3.3", "NI")

    if q3_1 in ("Y", "PY") and q3_2 in ("Y", "PY") and q3_3 in ("N", "PN"):
        return "Low"

    # Serious if not well-defined OR knowledge of outcome influenced classification
    if q3_1 in ("N", "PN") or q3_3 in ("Y", "PY"):
        return "Serious"

    # 3.1 NI = no definition → cribsheet says "No information"
    if q3_1 == "NI":
        return "No information"

    # 3.2 N/PN (retrospective) but 3.1 Y/PY and 3.3 N/PN → Moderate
    return "Moderate"
```

---

## 6. Domain 4 — Bias due to deviations from intended interventions

6 signaling questions, **aim-gated**. The aim of the study (set at Stage II, or auto-determined by the §1.1 aim preflight) determines which subset is asked:

- **Aim = `assignment_to`** (assessing effect of assignment to intervention): answer 4.1 and 4.2.
- **Aim = `starting_and_adhering`** (assessing effect of starting and adhering to intervention): answer 4.3, 4.4, 4.5, 4.6.

**If your aim for this study is to assess the effect of assignment to intervention, answer questions 4.1 and 4.2:**

- **4.1** Were there deviations from the intended intervention beyond what would be expected in usual practice?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* Deviations that happen in usual practice following the intervention (for example, cessation of a drug intervention because of acute toxicity) are part of the intended intervention and therefore do not lead to bias in the effect of assignment to intervention. Deviations may arise due to expectations of a difference between intervention and comparator (for example because participants feel unlucky to have been assigned to the comparator group and therefore seek the active intervention, or components of it, or other interventions). Such deviations are not part of usual practice, so may lead to biased effect estimates. However these are not expected in observational studies of individuals in routine care.

- **4.2** If Y/PY to 4.1: Were these deviations from intended intervention unbalanced between groups and likely to have affected the outcome?

  Response options: NA / Y / PY / PN / N / NI

  *Elaboration:* Deviations from intended interventions that do not reflect usual practice will be important if they affect the outcome, but not otherwise. Bias will arise only if there is imbalance in the deviations across the two groups.

**If your aim for this study is to assess the effect of starting and adhering to intervention, answer questions 4.3 to 4.6:**

- **4.3** Were important co-interventions balanced across intervention groups?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* Risk of bias will be higher if unplanned co-interventions were implemented in a way that would bias the estimated effect of intervention. Co-interventions will be important if they affect the outcome, but not otherwise. Bias will arise only if there is imbalance in such co-interventions between the intervention groups. Consider the co-interventions, including any pre-specified co-interventions, that are likely to affect the outcome and to have been administered in this study. Consider whether these co-interventions are balanced between intervention groups.

- **4.4** Was the intervention implemented successfully for most participants?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* Risk of bias will be higher if the intervention was not implemented as intended by, for example, the health care professionals delivering care during the trial. Consider whether implementation of the intervention was successful for most participants.

- **4.5** Did study participants adhere to the assigned intervention regimen?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* Risk of bias will be higher if participants did not adhere to the intervention as intended. Lack of adherence includes imperfect compliance, cessation of intervention, crossovers to the comparator intervention and switches to another active intervention. Consider available information on the proportion of study participants who continued with their assigned intervention throughout follow up, and answer 'No' or 'Probably No' if this proportion is high enough to raise concerns. Answer 'Yes' for studies of interventions that are administered once, so that imperfect adherence is not possible. We distinguish between analyses where follow-up time after interventions switches (including cessation of intervention) is assigned to (1) the new intervention or (2) the original intervention. (1) is addressed under time-varying confounding, and should not be considered further here.

- **4.6** If N/PN to 4.3, 4.4 or 4.5: Was an appropriate analysis used to estimate the effect of starting and adhering to the intervention?

  Response options: NA / Y / PY / PN / N / NI

  *Elaboration:* It is possible to conduct an analysis that corrects for some types of deviation from the intended intervention. Examples of appropriate analysis strategies include inverse probability weighting or instrumental variable estimation. It is possible that a paper reports such an analysis without reporting information on the deviations from intended intervention, but it would be hard to judge such an analysis to be appropriate in the absence of such information. Specialist advice may be needed to assess studies that used these approaches. If everyone in one group received a co-intervention, adjustments cannot be made to overcome this.

- **Risk of bias judgement:** See Table 2 (§11). Options: Low / Moderate / Serious / Critical / NI.
- **Optional: predicted direction of bias due to deviations from the intended interventions.** Options: Favours experimental / Favours comparator / Towards null / Away from null / Unpredictable.

Decision tree:

```python
def domain4_judge_v1(signals: dict[str, str], aim: str = "assignment_to") -> str:
    """V1 D4 — Bias due to deviations from intended interventions.
    Cribsheet Table 2 row.

    aim must be "assignment_to" (uses 4.1, 4.2) or
                "starting_and_adhering" (uses 4.3-4.6).
    """
    if aim == "assignment_to":
        q4_1 = signals.get("4.1", "NI")
        q4_2 = signals.get("4.2", "NI")
        # Low: deviations reflected usual practice OR didn't impact outcome
        if q4_1 in ("N", "PN"):
            return "Low"
        if q4_2 in ("N", "PN"):
            return "Low"
        # Serious: deviations beyond usual practice AND likely affected outcome
        if q4_2 in ("Y", "PY"):
            return "Serious"
        if q4_1 == "NI" or q4_2 == "NI":
            return "No information"
        return "Moderate"

    if aim == "starting_and_adhering":
        q4_3 = signals.get("4.3", "NI")
        q4_4 = signals.get("4.4", "NI")
        q4_5 = signals.get("4.5", "NI")
        q4_6 = signals.get("4.6", "NI")

        # Low: all three execution conditions met
        if (q4_3 in ("Y", "PY") and q4_4 in ("Y", "PY") and q4_5 in ("Y", "PY")):
            return "Low"

        # Moderate: appropriate analysis rescues otherwise-problematic execution
        if q4_6 in ("Y", "PY"):
            return "Moderate"

        # Serious: any N/PN on 4.3/4.4/4.5 without appropriate analysis
        bad = (q4_3 in ("N", "PN") or q4_4 in ("N", "PN") or q4_5 in ("N", "PN"))
        if bad and q4_6 in ("N", "PN", "NI"):
            return "Serious"

        if q4_3 == "NI" and q4_4 == "NI" and q4_5 == "NI":
            return "No information"
        return "Moderate"

    raise ValueError(f"Unknown aim: {aim}")
```

---

## 7. Domain 5 — Bias due to missing data

5 signaling questions.

- **5.1** Were outcome data available for all, or nearly all, participants?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* "Nearly all" should be interpreted as "enough to be confident of the findings", and a suitable proportion depends on the context. In some situations, availability of data from 95% (or possibly 90%) of the participants may be sufficient, providing that events of interest are reasonably common in both intervention groups. One aspect of this is that review authors would ideally try and locate an analysis plan for the study.

- **5.2** Were participants excluded due to missing data on intervention status?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* Missing intervention status may be a problem. This requires that the *intended* study sample is clear, which it may not be in practice.

- **5.3** Were participants excluded due to missing data on other variables needed for the analysis?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* This question relates particularly to participants excluded from the analysis because of missing information on confounders that were controlled for in the analysis.

- **5.4** If PN/N to 5.1, or Y/PY to 5.2 or 5.3: Are the proportion of participants and reasons for missing data similar across interventions?

  Response options: NA / Y / PY / PN / N / NI

  *Elaboration:* This aims to elicit whether either (i) differential proportion of missing observations or (ii) differences in reasons for missing observations could substantially impact on our ability to answer the question being addressed. "Similar" includes some minor degree of discrepancy across intervention groups as expected by chance.

- **5.5** If PN/N to 5.1, or Y/PY to 5.2 or 5.3: Is there evidence that results were robust to the presence of missing data?

  Response options: NA / Y / PY / PN / N / NI

  *Elaboration:* Evidence for robustness may come from how missing data were handled in the analysis and whether sensitivity analyses were performed by the investigators, or occasionally from additional analyses performed by the systematic reviewers. It is important to assess whether assumptions employed in analyses are clear and plausible. Both content knowledge and statistical expertise will often be required for this. For instance, use of a statistical method such as multiple imputation does not guarantee an appropriate answer. Review authors should seek naïve (complete-case) analyses for comparison, and clear differences between complete-case and multiple imputation-based findings should lead to careful assessment of the validity of the methods used.

- **Risk of bias judgement:** See Table 2 (§11). Options: Low / Moderate / Serious / Critical / NI.
- **Optional: predicted direction of bias due to missing data.** Options: Favours experimental / Favours comparator / Towards null / Away from null / Unpredictable.

Decision tree:

```python
def domain5_judge_v1(signals: dict[str, str]) -> str:
    """V1 D5 — Bias due to missing data. Cribsheet Table 2 row."""
    q5_1 = signals.get("5.1", "NI")
    q5_2 = signals.get("5.2", "NI")
    q5_3 = signals.get("5.3", "NI")
    q5_4 = signals.get("5.4", "NI")
    q5_5 = signals.get("5.5", "NI")

    # Low: data complete + no exclusions
    if (q5_1 in ("Y", "PY") and q5_2 in ("N", "PN") and q5_3 in ("N", "PN")):
        return "Low"

    # Missingness triggered: assess 5.4/5.5 rescue
    has_missing = (q5_1 in ("N", "PN") or q5_2 in ("Y", "PY") or q5_3 in ("Y", "PY"))
    if has_missing:
        # 5.4 Y/PY (similar across interventions) OR 5.5 Y/PY (robust) → Moderate
        if q5_4 in ("Y", "PY") or q5_5 in ("Y", "PY"):
            return "Moderate"
        # 5.4 N/PN OR 5.5 N/PN → Serious (no rescue)
        if q5_4 in ("N", "PN") or q5_5 in ("N", "PN"):
            return "Serious"
        # Both NI → "No information"
        if q5_4 == "NI" and q5_5 == "NI":
            return "No information"
        return "Moderate"

    # NI on key questions
    if q5_1 == "NI" and q5_2 == "NI" and q5_3 == "NI":
        return "No information"

    return "Moderate"
```

---

## 8. Domain 6 — Bias in measurement of outcomes

4 signaling questions.

- **6.1** Could the outcome measure have been influenced by knowledge of the intervention received?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* Some outcome measures involve negligible assessor judgment, e.g. all-cause mortality or non-repeatable automated laboratory assessments. Risk of bias due to measurement of these outcomes would be expected to be low.

- **6.2** Were outcome assessors aware of the intervention received by study participants?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* If outcome assessors were blinded to intervention status, the answer to this question would be 'No'. In other situations, outcome assessors may be unaware of the interventions being received by participants despite there being no active blinding by the study investigators; the answer this question would then also be 'No'. In studies where participants report their outcomes themselves, for example in a questionnaire, the outcome assessor is the study participant. In an observational study, the answer to this question will usually be 'Yes' when the participants report their outcomes themselves.

- **6.3** Were the methods of outcome assessment comparable across intervention groups?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* Comparable assessment methods (i.e. data collection) would involve the same outcome detection methods and thresholds, same time point, same definition, and same measurements.

- **6.4** Were any systematic errors in measurement of the outcome related to intervention received?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* This question refers to differential misclassification of outcomes. Systematic errors in measuring the outcome, if present, could cause bias if they are related to intervention or to a confounder of the intervention-outcome relationship. This will usually be due either to outcome assessors being aware of the intervention received or to non-comparability of outcome assessment methods, but there are examples of differential misclassification arising despite these controls being in place.

- **Risk of bias judgement:** See Table 2 (§11). Options: Low / Moderate / Serious / Critical / NI.
- **Optional: predicted direction of bias due to measurement of outcomes.** Options: Favours experimental / Favours comparator / Towards null / Away from null / Unpredictable.

Decision tree:

```python
def domain6_judge_v1(signals: dict[str, str]) -> str:
    """V1 D6 — Bias in measurement of outcomes. Cribsheet Table 2 row.

    Low: methods comparable (6.3 Y/PY) AND (outcome objective 6.1 N/PN OR
         assessor unaware 6.2 N/PN) AND error unrelated to intervention
         (6.4 N/PN).
    """
    q6_1 = signals.get("6.1", "NI")
    q6_2 = signals.get("6.2", "NI")
    q6_3 = signals.get("6.3", "NI")
    q6_4 = signals.get("6.4", "NI")

    if (q6_3 in ("Y", "PY")
        and (q6_1 in ("N", "PN") or q6_2 in ("N", "PN"))
        and q6_4 in ("N", "PN")):
        return "Low"

    # Serious: methods not comparable
    if q6_3 in ("N", "PN"):
        return "Serious"
    # Serious: subjective outcome + assessor aware
    if q6_1 in ("Y", "PY") and q6_2 in ("Y", "PY"):
        return "Serious"
    # Serious: error related to intervention
    if q6_4 in ("Y", "PY"):
        return "Serious"

    # NI on the key gating question
    if q6_3 == "NI" and q6_1 == "NI" and q6_2 == "NI":
        return "No information"

    return "Moderate"
```

---

## 9. Domain 7 — Bias in selection of the reported result

3 signaling questions.

Is the reported effect estimate likely to be selected, on the basis of the results, from…

- **7.1** … multiple outcome *measurements* within the outcome domain?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* For a specified outcome domain, it is possible to generate multiple effect estimates for different measurements. If multiple measurements were made, but only one or a subset is reported, there is a risk of selective reporting on the basis of results.

- **7.2** … multiple *analyses* of the intervention-outcome relationship?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* Because of the limitations of using data from non-randomized studies for analyses of effectiveness (need to control confounding, substantial missing data, etc), analysts may implement different analytic methods to address these limitations. Examples include unadjusted and adjusted models; use of final value vs change from baseline vs analysis of covariance; different transformations of variables; a continuously scaled outcome converted to categorical data with different cut-points; different sets of covariates used for adjustment; and different analytic strategies for dealing with missing data. Application of such methods generates multiple estimates of the effect of the intervention versus the comparator on the outcome. If the analyst does not pre-specify the methods to be applied, and multiple estimates are generated but only one or a subset is reported, there is a risk of selective reporting on the basis of results.

- **7.3** … different *subgroups*?

  Response options: Y / PY / PN / N / NI

  *Elaboration:* Particularly with large cohorts often available from routine data sources, it is possible to generate multiple effect estimates for different subgroups or simply to omit varying proportions of the original cohort. If multiple estimates are generated but only one or a subset is reported, there is a risk of selective reporting on the basis of results.

- **Risk of bias judgement:** See Table 2 (§11). Options: Low / Moderate / Serious / Critical / NI.
- **Optional: predicted direction of bias due to selection of the reported result.** Options: Favours experimental / Favours comparator / Towards null / Away from null / Unpredictable.

Decision tree:

```python
def domain7_judge_v1(signals: dict[str, str]) -> str:
    """V1 D7 — Bias in selection of the reported result. Cribsheet Table 2 row.

    No question explicitly anchors Low (cribsheet says Low requires "clear
    evidence usually through examination of a pre-registered protocol" — not
    a signal question). We map:
      0 Y/PY, all N/PN     → Low (charitable, assuming protocol checked offline)
      0 Y/PY, some NI      → Moderate
      0 Y/PY, all NI       → No information
      1 Y/PY               → Serious
      ≥2 Y/PY              → Critical
    """
    q7_1 = signals.get("7.1", "NI")
    q7_2 = signals.get("7.2", "NI")
    q7_3 = signals.get("7.3", "NI")

    yes_count = sum(1 for q in (q7_1, q7_2, q7_3) if q in ("Y", "PY"))
    ni_count = sum(1 for q in (q7_1, q7_2, q7_3) if q == "NI")

    if yes_count >= 2:
        return "Critical"
    if yes_count == 1:
        return "Serious"
    # 0 Y/PY
    if ni_count == 3:
        return "No information"
    if ni_count >= 1:
        return "Moderate"
    return "Low"
```

---

## 10. Overall aggregation (Table 3)

```python
def robins_i_v1_overall(domain_judgements: list[str]) -> str:
    """Overall risk-of-bias per V1 Table 3.

    - All Low                     → Low
    - All in (Low, Moderate)      → Moderate
    - At least one Serious        → Serious
    - At least one Critical       → Critical
    - All "No information"        → "No information"
    - Mix of (Low/Moderate) + NI  → "No information" (cribsheet: 'a judgement
                                     is required for this' — we default to NI
                                     conservatively; reviewer may override)
    """
    if not domain_judgements:
        return "No information"

    if any(j == "Critical" for j in domain_judgements):
        return "Critical"
    if any(j == "Serious" for j in domain_judgements):
        return "Serious"
    if all(j == "Low" for j in domain_judgements):
        return "Low"
    if all(j == "No information" for j in domain_judgements):
        return "No information"
    if all(j in ("Low", "Moderate") for j in domain_judgements):
        return "Moderate"
    # Mix of (Low/Moderate) + NI, no Serious/Critical
    return "No information"
```

---

## 11. V1 judgement tables (verbatim from the cribsheet)

V1 expresses its decision rules as **narrative tables**, not as Python decision trees. The 2016 cribsheet provides three tables. The Python trees in §3–§10 are conservative interpretations of these tables — see §17.5 for the interpretation caveats.

### Table 1 — Pre-intervention and at-intervention domains (D1, D2, D3)

**Low risk of bias** (the study is comparable to a well-performed randomized trial with regard to this domain):
- **D1 (Confounding):** No confounding expected.
- **D2 (Selection):** (i) All participants who would have been eligible for the target trial were included in the study; **and** (ii) For each participant, start of follow up and start of intervention coincided.
- **D3 (Classification):** (i) Intervention status is well defined; **and** (ii) Intervention definition is based solely on information collected at the time of intervention.

**Moderate risk of bias** (the study is sound for a non-randomized study with regard to this domain but cannot be considered comparable to a well-performed randomized trial):
- **D1:** (i) Confounding expected, all known important confounding domains appropriately measured and controlled for; **and** (ii) Reliability and validity of measurement of important domains were sufficient, such that we do not expect serious residual confounding.
- **D2:** (i) Selection into the study may have been related to intervention and outcome; **and** The authors used appropriate methods to adjust for the selection bias; **or** (ii) Start of follow up and start of intervention do not coincide for all participants; **and** (a) the proportion of participants for which this was the case was too low to induce important bias; **or** (b) the authors used appropriate methods to adjust for the selection bias; **or** (c) the review authors are confident that the rate (hazard) ratio for the effect of intervention remains constant over time.
- **D3:** (i) Intervention status is well defined; **and** (ii) Some aspects of the assignments of intervention status were determined retrospectively.

**Serious risk of bias** (the study has some important problems):
- **D1:** (i) At least one known important domain was not appropriately measured, or not controlled for; **or** (ii) Reliability or validity of measurement of an important domain was low enough that we expect serious residual confounding.
- **D2:** (i) Selection into the study was related (but not very strongly) to intervention and outcome; **and** This could not be adjusted for in analyses; **or** (ii) Start of follow up and start of intervention do not coincide; **and** A potentially important amount of follow-up time is missing from analyses; **and** The rate ratio is not constant over time.
- **D3:** (i) Intervention status is not well defined; **or** (ii) Major aspects of the assignments of intervention status were determined in a way that could have been affected by knowledge of the outcome.

**Critical risk of bias** (the study is too problematic to provide any useful evidence on the effects of intervention):
- **D1:** (i) Confounding inherently not controllable; **or** (ii) The use of negative controls strongly suggests unmeasured confounding.
- **D2:** (i) Selection into the study was very strongly related to intervention and outcome; **and** This could not be adjusted for in analyses; **or** (ii) A substantial amount of follow-up time is likely to be missing from analyses; **and** The rate ratio is not constant over time.
- **D3:** (Unusual) An extremely high amount of misclassification of intervention status, e.g. because of unusually strong recall biases.

**No information** on which to base a judgement about risk of bias for this domain:
- **D1:** No information on whether confounding might be present.
- **D2:** No information is reported about selection of participants into the study or whether start of follow up and start of intervention coincide.
- **D3:** No definition of the intervention or no explanation of the source of information about intervention status is reported.

### Table 2 — Post-intervention domains (D4, D5, D6, D7)

**Low risk of bias** (the study is comparable to a well-performed randomized trial with regard to this domain):
- **D4 (Deviations) — Effect of assignment to intervention:** (i) Any deviations from intended intervention reflected usual practice; **or** (ii) Any deviations from usual practice were unlikely to impact on the outcome.
- **D4 — Effect of starting and adhering to intervention:** The important co-interventions were balanced across intervention groups, and there were no deviations from the intended interventions (in terms of implementation or adherence) that were likely to impact on the outcome.
- **D5 (Missing data):** (i) Data were reasonably complete; **or** (ii) Proportions of and reasons for missing participants were similar across intervention groups; **or** (iii) The analysis addressed missing data and is likely to have removed any risk of bias.
- **D6 (Measurement):** (i) The methods of outcome assessment were comparable across intervention groups; **and** (ii) The outcome measure was unlikely to be influenced by knowledge of the intervention received by study participants (i.e. is objective) or the outcome assessors were unaware of the intervention received by study participants; **and** (iii) Any error in measuring the outcome is unrelated to intervention status.
- **D7 (Selective reporting):** There is clear evidence (usually through examination of a pre-registered protocol or statistical analysis plan) that all reported results correspond to all intended outcomes, analyses and sub-cohorts.

**Moderate risk of bias** (the study is sound for a non-randomized study with regard to this domain but cannot be considered comparable to a well-performed randomized trial):
- **D4 — Effect of assignment to intervention:** There were deviations from usual practice, but their impact on the outcome is expected to be slight.
- **D4 — Effect of starting and adhering to intervention:** (i) There were deviations from intended intervention, but their impact on the outcome is expected to be slight. **or** (ii) The important co-interventions were not balanced across intervention groups, or there were deviations from the intended interventions (in terms of implementation and/or adherence) that were likely to impact on the outcome; **and** The analysis was appropriate to estimate the effect of starting and adhering to intervention, allowing for deviations (in terms of implementation, adherence and co-intervention) that were likely to impact on the outcome.
- **D5:** (i) Proportions of and reasons for missing participants differ slightly across intervention groups; **and** (ii) The analysis is unlikely to have removed the risk of bias arising from the missing data.
- **D6:** (i) The methods of outcome assessment were comparable across intervention groups; **and** (ii) The outcome measure is only minimally influenced by knowledge of the intervention received by study participants; **and** (iii) Any error in measuring the outcome is only minimally related to intervention status.
- **D7:** (i) The outcome measurements and analyses are consistent with an *a priori* plan; or are clearly defined and both internally and externally consistent; **and** (ii) There is no indication of selection of the reported analysis from among multiple analyses; **and** (iii) There is no indication of selection of the cohort or subgroups for analysis and reporting on the basis of the results.

**Serious risk of bias** (the study has some important problems):
- **D4 — Effect of assignment to intervention:** There were deviations from usual practice that were unbalanced between the intervention groups and likely to have affected the outcome.
- **D4 — Effect of starting and adhering to intervention:** (i) The important co-interventions were not balanced across intervention groups, or there were deviations from the intended interventions (in terms of implementation and/or adherence) that were likely to impact on the outcome; **and** (ii) The analysis was not appropriate to estimate the effect of starting and adhering to intervention, allowing for deviations (in terms of implementation, adherence and co-intervention) that were likely to impact on the outcome.
- **D5:** (i) Proportions of missing participants differ substantially across interventions; **or** Reasons for missingness differ substantially across interventions; **and** (ii) The analysis is unlikely to have removed the risk of bias arising from the missing data; **or** Missing data were addressed inappropriately in the analysis; **or** The nature of the missing data means that the risk of bias cannot be removed through appropriate analysis.
- **D6:** (i) The methods of outcome assessment were not comparable across intervention groups; **or** (ii) The outcome measure was subjective (i.e. vulnerable to influence by knowledge of the intervention received by study participants); **and** The outcome was assessed by assessors aware of the intervention received by study participants; **or** (iii) Error in measuring the outcome was related to intervention status.
- **D7:** (i) Outcomes are defined in different ways in the methods and results sections, or in different publications of the study; **or** (ii) There is a high risk of selective reporting from among multiple analyses; **or** (iii) The cohort or subgroup is selected from a larger study for analysis and appears to be reported on the basis of the results.

**Critical risk of bias** (the study is too problematic to provide any useful evidence on the effects of intervention):
- **D4 — Effect of assignment to intervention:** There were substantial deviations from usual practice that were unbalanced between the intervention groups and likely to have affected the outcome.
- **D4 — Effect of starting and adhering to intervention:** (i) There were substantial imbalances in important co-interventions across intervention groups, or there were substantial deviations from the intended interventions (in terms of implementation and/or adherence) that were likely to impact on the outcome; **and** (ii) The analysis was not appropriate to estimate the effect of starting and adhering to intervention, allowing for deviations (in terms of implementation, adherence and co-intervention) that were likely to impact on the outcome.
- **D5:** (i) (Unusual) There were critical differences between interventions in participants with missing data; **and** (ii) Missing data were not, or could not, be addressed through appropriate analysis.
- **D6:** The methods of outcome assessment were so different that they cannot reasonably be compared across intervention groups.
- **D7:** (i) There is evidence or strong suspicion of selective reporting of results; **and** (ii) The unreported results are likely to be substantially different from the reported results.

**No information** on which to base a judgement about risk of bias for this domain:
- **D4:** No information is reported on whether there is deviation from the intended intervention.
- **D5:** No information is reported about missing data or the potential for data to be missing.
- **D6:** No information is reported about the methods of outcome assessment.
- **D7:** There is too little information to make a judgement (for example, if only an abstract is available for the study).

### Table 3 — Interpretation of domain-level and overall risk of bias judgements

| Judgement | Within each domain | Across domains | Criterion |
|--|--|--|--|
| **Low risk of bias** | The study is comparable to a well-performed randomized trial with regard to this domain | The study is comparable to a well-performed randomized trial | The study is judged to be at **low risk of bias for all domains**. |
| **Moderate risk of bias** | The study is sound for a non-randomized study with regard to this domain but cannot be considered comparable to a well-performed randomized trial | The study provides sound evidence for a non-randomized study but cannot be considered comparable to a well-performed randomized trial | The study is judged to be at **low or moderate risk of bias for all domains**. |
| **Serious risk of bias** | the study has some important problems in this domain | The study has some important problems | The study is judged to be at **serious risk of bias** in at least one domain, but not at critical risk of bias in any domain. |
| **Critical risk of bias** | the study is too problematic in this domain to provide any useful evidence on the effects of intervention | The study is too problematic to provide any useful evidence and should not be included in any synthesis | The study is judged to be at **critical risk of bias in at least one domain.** |
| **No information** | No information on which to base a judgement about risk of bias for this domain | No information on which to base a judgement about risk of bias | There is no clear indication that the study is at serious or critical risk of bias *and* there is a lack of information in one or more key domains of bias (*a judgement is required for this*). |

---

## 12. LLM prompt templates

V1 is implemented as **one LLM call per domain** (7 calls per paper). There is no preflight stage.

### 12.1 System prompt

```text
You are an evidence-synthesis methodologist assessing risk of bias in a non-randomized study of an intervention using the Cochrane ROBINS-I tool (Version 1, 1 August 2016 cribsheet — Sterne JAC et al., BMJ 2016;355:i4919). Read the PDF carefully. Answer each signaling question with one of: Y (yes), PY (probably yes), PN (probably no), N (no), NI (no information). Some questions are gated on prior answers and additionally allow NA (not applicable). Provide a 1-2 sentence rationale for each answer, quoting the paper where possible. Return ONLY a valid JSON object — no preamble, no markdown fences.
```

### 12.2 Per-domain user prompt template

```text
Assess **Domain {domain_id} — {domain_name}** for the study described in the attached PDF using the ROBINS-I V1 tool (1 August 2016 cribsheet).

Study type: {study_type}
Outcome being assessed: {primary_outcome}
{aim_block}
{pico_block}
Context (fields already extracted from the paper):
{ctx_json}

Signaling questions:
{questions_block}

Return a JSON object with exactly this shape:
{shape}

Notes on ROBINS-I V1:
- The signal vocabulary is **Y / PY / PN / N / NI** (5 tokens). For each question, answer based on what the paper says about that specific question — **do NOT try to determine whether a question is gated out by the cribsheet's cascading structure**. Python applies the cascade rules after you answer and will set `NA` for any question that should be gated out. Just answer each question independently based on its own text.
- The judgement scale is **Low / Moderate / Serious / Critical / No information** (5 levels). The code maps your signal answers to a judgement — answer the signaling questions only.
- Answer each question exactly as worded: Y/PY when the answer to the question as written is yes/probably yes, N/PN when it is no/probably no. Some questions are phrased so that "yes" indicates a problem and others so that "yes" indicates good practice — never translate your answer into "problem present/absent". Reserve NI for when the paper provides no information to answer the question.
- Rationales must be short (1-2 sentences) and quote the paper verbatim where possible.
```

`{aim_block}` is empty for Domains 1, 2, 3, 5, 6, 7 and is rendered for Domain 4 as:

```text

Aim of study: {aim}
- "assignment_to" → answer signaling questions 4.1 and 4.2 only.
- "starting_and_adhering" → answer signaling questions 4.3 through 4.6 only.
```

The `aim` value may have been set manually at Stage II or auto-determined by the §1.1 aim preflight — the per-domain prompt doesn't need to distinguish the source.

`{pico_block}` is empty when no `target_pico` is supplied; otherwise:

```text

Target PICO (user-supplied):
{json.dumps(target_pico, indent=2)}
```

### 12.3 Per-signal questions block

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

### 12.4 JSON-shape generator

```python
shape = "{\n"
for sig in signals:
    opt_string = "|".join(sig["options"])
    shape += f'  "{sig["id"]}": "{opt_string}",\n'
    shape += f'  "{sig["id"]}_rationale": "1-2 sentences quoting the paper",\n'
direction_options = domain.get("direction_options", ("NA",))
shape += f'  "direction_of_bias": "{"|".join(direction_options)}"\n'
shape += "}"
```

---

## 13. Expected JSON output shapes

### 13.1 Domain 1 — Bias due to confounding (cascading)

The LLM answers all 8 signals but uses NA for questions gated out by the cascade:

```json
{
  "1.1": "Y|PY|PN|N",
  "1.1_rationale": "1-2 sentences quoting the paper",
  "1.2": "NA|Y|PY|PN|N|NI",
  "1.2_rationale": "...",
  "1.3": "NA|Y|PY|PN|N|NI",
  "1.3_rationale": "...",
  "1.4": "NA|Y|PY|PN|N|NI",
  "1.4_rationale": "...",
  "1.5": "NA|Y|PY|PN|N|NI",
  "1.5_rationale": "...",
  "1.6": "NA|Y|PY|PN|N|NI",
  "1.6_rationale": "...",
  "1.7": "NA|Y|PY|PN|N|NI",
  "1.7_rationale": "...",
  "1.8": "NA|Y|PY|PN|N|NI",
  "1.8_rationale": "...",
  "direction_of_bias": "NA|Favours experimental|Favours comparator|Unpredictable"
}
```

Note: V1 D1's direction-of-bias options are only 4 values (3 directions + NA). D2–D7 have 6 (adds Towards null / Away from null).

### 13.2 Domains 2, 3, 5, 6, 7 — standard 6-option direction-of-bias

```json
{
  "{sig_id}": "Y|PY|PN|N|NI",
  "{sig_id}_rationale": "...",
  ...
  "direction_of_bias": "NA|Favours experimental|Favours comparator|Towards null|Away from null|Unpredictable"
}
```

### 13.3 Domain 4 — gated by aim

When `aim = "assignment_to"`, only 4.1 and 4.2 are required; 4.3–4.6 should be omitted (or NA). When `aim = "starting_and_adhering"`, only 4.3–4.6 are required; 4.1 and 4.2 should be omitted (or NA).

### 13.4 Final per-paper result returned by `run_v1()`

`run_v1()` returns the 4-tuple `(domain_results, overall_judgement, overall_direction, aim_rationale)`. Conceptual JSON shape of the assembled result:

```json
{
  "domain_results": {
    "1": {"id": 1, "name": "Bias due to confounding", "signals": {...}, "rationales": {...}, "judgement": "Moderate", "direction": "Favours experimental"},
    "2": {"id": 2, "name": "Bias in selection of participants into the study", "signals": {...}, "rationales": {...}, "judgement": "Low", "direction": "NA"},
    "3": {"id": 3, "name": "Bias in classification of interventions", "signals": {...}, "rationales": {...}, "judgement": "Low", "direction": "NA"},
    "4": {"id": 4, "name": "Bias due to deviations from intended interventions", "aim": "assignment_to", "signals": {...}, "rationales": {...}, "judgement": "Low", "direction": "NA"},
    "5": {"id": 5, "name": "Bias due to missing data", "signals": {...}, "rationales": {...}, "judgement": "Moderate", "direction": "Towards null"},
    "6": {"id": 6, "name": "Bias in measurement of outcomes", "signals": {...}, "rationales": {...}, "judgement": "Low", "direction": "NA"},
    "7": {"id": 7, "name": "Bias in selection of the reported result", "signals": {...}, "rationales": {...}, "judgement": "Low", "direction": "NA"}
  },
  "overall_judgement": "Moderate",
  "overall_direction": "Favours experimental",
  "aim_rationale": "Methods section reports an intention-to-treat analysis where all randomized participants were analysed in their assigned group regardless of crossovers."
}
```

`aim_rationale` is `null` when the aim was manually supplied by the caller (preflight skipped); a string when the §1.1 aim preflight ran. V1 still has no domain-level preflight key (B1/B2/B3/C4 are V2-only) — the aim preflight is V1-specific and returns only the chosen aim plus rationale.

---

## 14. Pre-extracted fields

Each domain prompt receives a context block (`ctx_json`) containing a subset of fields already extracted from the paper by an upstream "annotator" stage. Suggested per-domain field sets (mirrors the V2 doc convention; adapt to your annotator):

| Domain | Suggested pre-extracted fields |
|--|--|
| 1 — Bias due to confounding | `confounders_measured`, `adjustment_method`, `exposure_definition`, `comparator_group`, `immortal_time_bias`, `confounding_control`, `consecutive_enrolment` |
| 2 — Selection of participants | `case_source`, `control_selection`, `sampling_method`, `loss_to_follow_up`, `immortal_time_bias` |
| 3 — Classification of interventions | `exposure_definition`, `exposure_measurement`, `exposure_ascertainment`, `intervention_classification` |
| 4 — Deviations from intended interventions | `intervention_classification`, `loss_to_follow_up`, `co_interventions`, `adherence` |
| 5 — Missing data | `loss_to_follow_up`, `missing_data_handling`, `attrition_rate` |
| 6 — Measurement of outcomes | `outcome_ascertainment`, `outcome_definition`, `assessor_blinding` |
| 7 — Selection of the reported result | `outcome_definition`, `statistical_analysis`, `pre_registered_protocol` |

If you don't have an annotator pipeline, pass an empty dict (`extracted_fields = {}`) and the prompts will simply contain `"(no pre-extracted fields)"` in the context block — the PDF itself still carries the evidence.

---

## 15. Reference implementation — single self-contained Python module

Supply your own LLM adapter via the `llm_call` parameter, and call `run_v1(...)`. No project-specific imports.

The `llm_call` callable has the signature:

```python
llm_call(pdf_bytes: bytes, prompt: str, max_tokens: int) -> dict
```

It must send `pdf_bytes` + `prompt` to a vision-capable LLM and return the parsed JSON response as a Python dict. Error handling, retries, and JSON-fence stripping are your concern.

```python
"""ROBINS-I V1 (1 August 2016) — Single-file reference implementation.

Source: Sterne JAC, Hernán MA, Reeves BC, Savović J, Berkman ND, Viswanathan M,
Henry D, Altman DG, Ansari MT, Boutron I, Carpenter JR, Chan A-W, Churchill R,
Hróbjartsson A, Kirkham J, Jüni P, Loke YK, Pigott TD, Ramsay CR, Regidor D,
Rothstein HR, Sandhu L, Santaguida PL, Schünemann HJ, Shea B, Shrier I, Tugwell P,
Turner L, Valentine JC, Waddington H, Waters E, Whiting P, Higgins JPT.
*The Risk Of Bias In Non-randomized Studies — of Interventions (ROBINS-I)
assessment tool (version for cohort-type studies).* Version 1 August 2016.
Underlying paper: Sterne JAC et al., BMJ 2016;355:i4919.
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
SIGNAL_OPTIONS_V1 = ("Y", "PY", "PN", "N", "NI")
JUDGEMENTS_V1 = ("Low", "Moderate", "Serious", "Critical", "No information")
AIMS = ("assignment_to", "starting_and_adhering")


# ─────────────────────────────────────────────
# Per-question response option subsets
# ─────────────────────────────────────────────
_BASIC = ("Y", "PY", "PN", "N", "NI")          # standard 5-token
_BASIC_NA = ("NA", "Y", "PY", "PN", "N", "NI") # gated questions add NA
_BASIC_NO_NI = ("Y", "PY", "PN", "N")          # 1.1 has no NI option


# ─────────────────────────────────────────────
# Helper predicates
# ─────────────────────────────────────────────
def _yes(ans: str) -> bool:
    return ans in ("Y", "PY")


def _no(ans: str) -> bool:
    return ans in ("N", "PN")


def _no_info(ans: str) -> bool:
    return ans == "NI"


# ─────────────────────────────────────────────
# Decision trees — conservative interpretations of Tables 1 + 2
# ─────────────────────────────────────────────
def domain1_judge_v1(signals: dict[str, str]) -> str:
    """V1 D1 — Bias due to confounding. Cribsheet Table 1 row."""
    q1_1 = signals.get("1.1", "NI")
    if q1_1 in ("N", "PN"):
        return "Low"  # cribsheet early exit
    if q1_1 == "NI":
        return "No information"

    q1_2 = signals.get("1.2", "NI")
    q1_3 = signals.get("1.3", "NI")

    if q1_2 in ("Y", "PY") and q1_3 in ("Y", "PY"):
        q1_7 = signals.get("1.7", "NI")
        q1_8 = signals.get("1.8", "NI")
        if q1_7 in ("Y", "PY") and q1_8 in ("Y", "PY"):
            return "Moderate"
        if q1_7 in ("N", "PN") or q1_8 in ("N", "PN"):
            return "Serious"
        return "No information"

    q1_4 = signals.get("1.4", "NI")
    q1_5 = signals.get("1.5", "NI")
    q1_6 = signals.get("1.6", "NI")

    if q1_6 in ("Y", "PY"):
        return "Serious"
    if q1_4 in ("N", "PN") or q1_5 in ("N", "PN"):
        return "Serious"
    if q1_4 in ("Y", "PY") and q1_5 in ("Y", "PY") and q1_6 in ("N", "PN"):
        return "Moderate"
    return "No information"


def domain2_judge_v1(signals: dict[str, str]) -> str:
    """V1 D2 — Bias in selection of participants. Cribsheet Table 1 row."""
    q2_1 = signals.get("2.1", "NI")
    q2_4 = signals.get("2.4", "NI")
    q2_2 = signals.get("2.2", "NI")
    q2_3 = signals.get("2.3", "NI")
    q2_5 = signals.get("2.5", "NI")

    if q2_1 in ("N", "PN") and q2_4 in ("Y", "PY"):
        return "Low"
    if q2_5 in ("Y", "PY"):
        return "Moderate"
    if q2_1 in ("Y", "PY") and q2_2 in ("Y", "PY") and q2_3 in ("Y", "PY"):
        return "Serious"
    if q2_4 in ("N", "PN"):
        return "Serious"
    if q2_1 == "NI" and q2_4 == "NI":
        return "No information"
    return "Moderate"


def domain3_judge_v1(signals: dict[str, str]) -> str:
    """V1 D3 — Bias in classification of interventions. Cribsheet Table 1 row."""
    q3_1 = signals.get("3.1", "NI")
    q3_2 = signals.get("3.2", "NI")
    q3_3 = signals.get("3.3", "NI")

    if q3_1 in ("Y", "PY") and q3_2 in ("Y", "PY") and q3_3 in ("N", "PN"):
        return "Low"
    if q3_1 in ("N", "PN") or q3_3 in ("Y", "PY"):
        return "Serious"
    if q3_1 == "NI":
        return "No information"
    return "Moderate"


def domain4_judge_v1(signals: dict[str, str], aim: str = "assignment_to") -> str:
    """V1 D4 — Bias due to deviations from intended interventions. Table 2 row.

    aim must be "assignment_to" (uses 4.1, 4.2) or
                "starting_and_adhering" (uses 4.3-4.6).
    """
    if aim == "assignment_to":
        q4_1 = signals.get("4.1", "NI")
        q4_2 = signals.get("4.2", "NI")
        if q4_1 in ("N", "PN"):
            return "Low"
        if q4_2 in ("N", "PN"):
            return "Low"
        if q4_2 in ("Y", "PY"):
            return "Serious"
        if q4_1 == "NI" or q4_2 == "NI":
            return "No information"
        return "Moderate"

    if aim == "starting_and_adhering":
        q4_3 = signals.get("4.3", "NI")
        q4_4 = signals.get("4.4", "NI")
        q4_5 = signals.get("4.5", "NI")
        q4_6 = signals.get("4.6", "NI")
        if (q4_3 in ("Y", "PY") and q4_4 in ("Y", "PY") and q4_5 in ("Y", "PY")):
            return "Low"
        if q4_6 in ("Y", "PY"):
            return "Moderate"
        bad = (q4_3 in ("N", "PN") or q4_4 in ("N", "PN") or q4_5 in ("N", "PN"))
        if bad and q4_6 in ("N", "PN", "NI"):
            return "Serious"
        if q4_3 == "NI" and q4_4 == "NI" and q4_5 == "NI":
            return "No information"
        return "Moderate"

    raise ValueError(f"Unknown aim: {aim}")


def domain5_judge_v1(signals: dict[str, str]) -> str:
    """V1 D5 — Bias due to missing data. Cribsheet Table 2 row."""
    q5_1 = signals.get("5.1", "NI")
    q5_2 = signals.get("5.2", "NI")
    q5_3 = signals.get("5.3", "NI")
    q5_4 = signals.get("5.4", "NI")
    q5_5 = signals.get("5.5", "NI")

    if (q5_1 in ("Y", "PY") and q5_2 in ("N", "PN") and q5_3 in ("N", "PN")):
        return "Low"

    has_missing = (q5_1 in ("N", "PN") or q5_2 in ("Y", "PY") or q5_3 in ("Y", "PY"))
    if has_missing:
        if q5_4 in ("Y", "PY") or q5_5 in ("Y", "PY"):
            return "Moderate"
        if q5_4 in ("N", "PN") or q5_5 in ("N", "PN"):
            return "Serious"
        if q5_4 == "NI" and q5_5 == "NI":
            return "No information"
        return "Moderate"

    if q5_1 == "NI" and q5_2 == "NI" and q5_3 == "NI":
        return "No information"
    return "Moderate"


def domain6_judge_v1(signals: dict[str, str]) -> str:
    """V1 D6 — Bias in measurement of outcomes. Cribsheet Table 2 row."""
    q6_1 = signals.get("6.1", "NI")
    q6_2 = signals.get("6.2", "NI")
    q6_3 = signals.get("6.3", "NI")
    q6_4 = signals.get("6.4", "NI")

    if (q6_3 in ("Y", "PY")
        and (q6_1 in ("N", "PN") or q6_2 in ("N", "PN"))
        and q6_4 in ("N", "PN")):
        return "Low"
    if q6_3 in ("N", "PN"):
        return "Serious"
    if q6_1 in ("Y", "PY") and q6_2 in ("Y", "PY"):
        return "Serious"
    if q6_4 in ("Y", "PY"):
        return "Serious"
    if q6_3 == "NI" and q6_1 == "NI" and q6_2 == "NI":
        return "No information"
    return "Moderate"


def domain7_judge_v1(signals: dict[str, str]) -> str:
    """V1 D7 — Bias in selection of the reported result. Cribsheet Table 2 row."""
    q7_1 = signals.get("7.1", "NI")
    q7_2 = signals.get("7.2", "NI")
    q7_3 = signals.get("7.3", "NI")

    yes_count = sum(1 for q in (q7_1, q7_2, q7_3) if q in ("Y", "PY"))
    ni_count = sum(1 for q in (q7_1, q7_2, q7_3) if q == "NI")

    if yes_count >= 2:
        return "Critical"
    if yes_count == 1:
        return "Serious"
    if ni_count == 3:
        return "No information"
    if ni_count >= 1:
        return "Moderate"
    return "Low"


def robins_i_v1_overall(domain_judgements: list[str]) -> str:
    """Overall risk-of-bias per V1 Table 3."""
    if not domain_judgements:
        return "No information"
    if any(j == "Critical" for j in domain_judgements):
        return "Critical"
    if any(j == "Serious" for j in domain_judgements):
        return "Serious"
    if all(j == "Low" for j in domain_judgements):
        return "Low"
    if all(j == "No information" for j in domain_judgements):
        return "No information"
    if all(j in ("Low", "Moderate") for j in domain_judgements):
        return "Moderate"
    return "No information"


DOMAIN_JUDGES_V1 = {
    1: domain1_judge_v1,
    2: domain2_judge_v1,
    3: domain3_judge_v1,
    # 4: dispatched separately because it takes aim= kwarg
    5: domain5_judge_v1,
    6: domain6_judge_v1,
    7: domain7_judge_v1,
}


# ─────────────────────────────────────────────
# Cascade enforcement — rule-based NA handling per the cribsheet's
# cascading-question structure. Called AFTER the LLM responds, before the
# decision tree runs. Overrides LLM answers for gated-out questions to NA.
#
# Why Python-side enforcement instead of asking the LLM to determine NA?
#   1. The cascade rules are deterministic from the cribsheet, not a
#      judgement call — Python is the right place.
#   2. Prevents LLM inconsistency (e.g. answering 1.3 = Y when 1.2 = N,
#      which would incorrectly route into the time-varying confounding path).
#   3. Cleanly separates concerns: LLM answers "what does the paper say?",
#      Python answers "what does the cribsheet say to ask next?".
#   4. Same paper always produces the same cascade structure regardless of
#      LLM stochasticity.
# ─────────────────────────────────────────────
def enforce_cascade_d1_v1(signals: dict[str, str]) -> dict[str, str]:
    """V1 D1 — confounding. Cascading structure (cribsheet pp 5-6).

    Gating rules:
      1.1 = N/PN/NI → 1.2-1.8 are all NA (early-exit per cribsheet, or
                       insufficient info to proceed)
      1.1 = Y/PY →
        1.2 = Y/PY AND 1.3 = Y/PY → time-varying path
          1.4, 1.5, 1.6 are NA
          1.7 always asked; 1.8 NA unless 1.7 = Y/PY
        else (1.2 N/PN/NI OR 1.3 N/PN/NI) → baseline-only path
          1.7, 1.8 are NA
          1.4, 1.6 always asked; 1.5 NA unless 1.4 = Y/PY
          1.3 NA if 1.2 not Y/PY (1.3 only asked when 1.2 Y/PY)
    """
    out = dict(signals)
    q1_1 = out.get("1.1", "NI")

    if q1_1 in ("N", "PN", "NI"):
        for sid in ("1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8"):
            out[sid] = "NA"
        return out

    # 1.1 Y/PY: continue cascade
    q1_2 = out.get("1.2", "NI")
    q1_3 = out.get("1.3", "NI")

    if q1_2 in ("Y", "PY") and q1_3 in ("Y", "PY"):
        # Time-varying path
        for sid in ("1.4", "1.5", "1.6"):
            out[sid] = "NA"
        if out.get("1.7", "NI") not in ("Y", "PY"):
            out["1.8"] = "NA"
    else:
        # Baseline-only path
        for sid in ("1.7", "1.8"):
            out[sid] = "NA"
        if out.get("1.4", "NI") not in ("Y", "PY"):
            out["1.5"] = "NA"
        # 1.3 only asked if 1.2 Y/PY
        if q1_2 not in ("Y", "PY"):
            out["1.3"] = "NA"

    return out


def enforce_cascade_d2_v1(signals: dict[str, str]) -> dict[str, str]:
    """V1 D2 — selection. Cascading structure (cribsheet p 7).

    Gating rules:
      2.1 = N/PN/NI → 2.2, 2.3 are NA (go to 2.4)
      2.1 = Y/PY →
        2.2 = Y/PY → 2.3 asked
        2.2 = N/PN/NI → 2.3 is NA
      2.5 only asked if (2.2 Y/PY AND 2.3 Y/PY) OR (2.4 N/PN); else NA
    """
    out = dict(signals)
    q2_1 = out.get("2.1", "NI")

    if q2_1 not in ("Y", "PY"):
        out["2.2"] = "NA"
        out["2.3"] = "NA"
    else:
        if out.get("2.2", "NI") not in ("Y", "PY"):
            out["2.3"] = "NA"

    cond_22_23 = (out.get("2.2", "NA") in ("Y", "PY")
                  and out.get("2.3", "NA") in ("Y", "PY"))
    cond_24 = out.get("2.4", "NI") in ("N", "PN")
    if not (cond_22_23 or cond_24):
        out["2.5"] = "NA"

    return out


def enforce_cascade_d4_v1(signals: dict[str, str], aim: str) -> dict[str, str]:
    """V1 D4 — deviations. Within-path cascading (cribsheet pp 9-10).

    The aim-gating itself (assignment_to → 4.1+4.2 only;
    starting_and_adhering → 4.3-4.6 only) is handled upstream by
    _signals_for_domain_v1. This function handles WITHIN-PATH gating:
      assignment_to:
        4.2 only asked if 4.1 = Y/PY; else NA
      starting_and_adhering:
        4.6 only asked if any of (4.3, 4.4, 4.5) = N/PN; else NA
    """
    out = dict(signals)
    if aim == "assignment_to":
        if out.get("4.1", "NI") not in ("Y", "PY"):
            out["4.2"] = "NA"
    elif aim == "starting_and_adhering":
        any_bad = any(out.get(sid, "NI") in ("N", "PN")
                      for sid in ("4.3", "4.4", "4.5"))
        if not any_bad:
            out["4.6"] = "NA"
    return out


def enforce_cascade_d5_v1(signals: dict[str, str]) -> dict[str, str]:
    """V1 D5 — missing data. Cascading structure (cribsheet p 11).

    Gating rules:
      5.4, 5.5 only asked if (5.1 PN/N) OR (5.2 Y/PY) OR (5.3 Y/PY); else NA
    """
    out = dict(signals)
    trigger = (out.get("5.1", "NI") in ("PN", "N")
               or out.get("5.2", "NI") in ("Y", "PY")
               or out.get("5.3", "NI") in ("Y", "PY"))
    if not trigger:
        out["5.4"] = "NA"
        out["5.5"] = "NA"
    return out


# Dispatch helper — used by _assess_domain_v1 after the LLM response is parsed
def enforce_cascade_v1(domain_id: int,
                       signals: dict[str, str],
                       aim: str = "assignment_to") -> dict[str, str]:
    """Apply the appropriate per-domain cascade enforcer. D3, D6, D7 have
    no cascading and return signals unchanged."""
    if domain_id == 1:
        return enforce_cascade_d1_v1(signals)
    if domain_id == 2:
        return enforce_cascade_d2_v1(signals)
    if domain_id == 4:
        return enforce_cascade_d4_v1(signals, aim=aim)
    if domain_id == 5:
        return enforce_cascade_d5_v1(signals)
    return signals  # D3, D6, D7 have no cascade


# ─────────────────────────────────────────────
# Signal definitions — verbatim from the V1 cribsheet
# ─────────────────────────────────────────────
DOMAIN1_SIGNALS_V1: list[dict[str, Any]] = [
    {"id": "1.1", "text": "Is there potential for confounding of the effect of intervention in this study?", "options": list(_BASIC_NO_NI), "elaboration": "In rare situations, such as when studying harms that are very unlikely to be related to factors that influence treatment decisions, no confounding is expected and the study can be considered to be at low risk of bias due to confounding, equivalent to a fully randomized trial. There is no NI (No information) option for this signalling question."},
    {"id": "1.2", "text": "Was the analysis based on splitting participants' follow up time according to intervention received?", "options": list(_BASIC_NA), "elaboration": "If participants could switch between intervention groups then associations between intervention and outcome may be biased by time-varying confounding. This occurs when prognostic factors influence switches between intended interventions."},
    {"id": "1.3", "text": "Were intervention discontinuations or switches likely to be related to factors that are prognostic for the outcome?", "options": list(_BASIC_NA), "elaboration": "If intervention switches are unrelated to the outcome, for example when the outcome is an unexpected harm, then time-varying confounding will not be present and only control for baseline confounding is required."},
    {"id": "1.4", "text": "Did the authors use an appropriate analysis method that controlled for all the important confounding domains?", "options": list(_BASIC_NA), "elaboration": "Appropriate methods to control for measured confounders include stratification, regression, matching, standardization, and inverse probability weighting. They may control for individual variables or for the estimated propensity score. Each method depends on the assumption that there is no unmeasured or residual confounding."},
    {"id": "1.5", "text": "If Y/PY to 1.4: Were confounding domains that were controlled for measured validly and reliably by the variables available in this study?", "options": list(_BASIC_NA), "elaboration": "Appropriate control of confounding requires that the variables adjusted for are valid and reliable measures of the confounding domains. Subjective measures (e.g. based on self-report) may have lower validity and reliability than objective measures such as lab findings."},
    {"id": "1.6", "text": "Did the authors control for any post-intervention variables that could have been affected by the intervention?", "options": list(_BASIC_NA), "elaboration": "Controlling for post-intervention variables that are affected by intervention is not appropriate. Controlling for mediating variables estimates the direct effect of intervention and may introduce bias."},
    {"id": "1.7", "text": "Did the authors use an appropriate analysis method that adjusted for all the important confounding domains and for time-varying confounding?", "options": list(_BASIC_NA), "elaboration": "Adjustment for time-varying confounding is necessary to estimate the effect of starting and adhering to intervention. Appropriate methods include those based on inverse probability weighting. Standard regression models that include time-updated confounders may be problematic if time-varying confounding is present."},
    {"id": "1.8", "text": "If Y/PY to 1.7: Were confounding domains that were adjusted for measured validly and reliably by the variables available in this study?", "options": list(_BASIC_NA), "elaboration": "Same measurement-validity question as 1.5 but applied to baseline + time-varying confounders."},
]

DOMAIN2_SIGNALS_V1: list[dict[str, Any]] = [
    {"id": "2.1", "text": "Was selection of participants into the study (or into the analysis) based on participant characteristics observed after the start of intervention?", "options": list(_BASIC), "elaboration": "This domain is concerned only with selection into the study based on participant characteristics observed after the start of intervention. Baseline confounding is addressed in Domain 1, not here."},
    {"id": "2.2", "text": "If Y/PY to 2.1: Were the post-intervention variables that influenced selection likely to be associated with intervention?", "options": list(_BASIC_NA), "elaboration": "Selection bias occurs when selection is related to an effect of either intervention or a cause of intervention AND an effect of either the outcome or a cause of the outcome."},
    {"id": "2.3", "text": "If Y/PY to 2.2: Were the post-intervention variables that influenced selection likely to be influenced by the outcome or a cause of the outcome?", "options": list(_BASIC_NA), "elaboration": "Collider-style selection bias."},
    {"id": "2.4", "text": "Do start of follow-up and start of intervention coincide for most participants?", "options": list(_BASIC), "elaboration": "If participants are not followed from the start of the intervention then a period of follow up has been excluded, and individuals who experienced the outcome soon after intervention will be missing from analyses."},
    {"id": "2.5", "text": "If Y/PY to 2.2 and 2.3, or N/PN to 2.4: Were adjustment techniques used that are likely to correct for the presence of selection biases?", "options": list(_BASIC_NA), "elaboration": "It is in principle possible to correct for selection biases using inverse probability weights or missing-data methods, but such methods are rarely used in practice."},
]

DOMAIN3_SIGNALS_V1: list[dict[str, Any]] = [
    {"id": "3.1", "text": "Were intervention groups clearly defined?", "options": list(_BASIC), "elaboration": "A pre-requisite for an appropriate comparison of interventions is that the interventions are well defined. For individual-level interventions, criteria for considering individuals to have received each intervention should be clear and explicit, covering issues such as type, setting, dose, frequency, intensity and/or timing of intervention."},
    {"id": "3.2", "text": "Was the information used to define intervention groups recorded at the start of the intervention?", "options": list(_BASIC), "elaboration": "If information about interventions received is available from sources that could not have been affected by subsequent outcomes, then differential misclassification of intervention status is unlikely. Collection at the time of intervention makes it easier to avoid such misclassification."},
    {"id": "3.3", "text": "Could classification of intervention status have been affected by knowledge of the outcome or risk of the outcome?", "options": list(_BASIC), "elaboration": "Collection of the information at the time of the intervention may not be sufficient to avoid bias. The way in which the data are collected for the purposes of the NRSI should also avoid misclassification."},
]

DOMAIN4_SIGNALS_V1: list[dict[str, Any]] = [
    # Aim = "assignment_to" path
    {"id": "4.1", "text": "Were there deviations from the intended intervention beyond what would be expected in usual practice?", "options": list(_BASIC), "elaboration": "Deviations that happen in usual practice following the intervention (for example, cessation of a drug intervention because of acute toxicity) are part of the intended intervention and therefore do not lead to bias in the effect of assignment to intervention. Such deviations are not expected in observational studies of individuals in routine care."},
    {"id": "4.2", "text": "If Y/PY to 4.1: Were these deviations from intended intervention unbalanced between groups and likely to have affected the outcome?", "options": list(_BASIC_NA), "elaboration": "Deviations from intended interventions that do not reflect usual practice will be important if they affect the outcome, but not otherwise. Bias will arise only if there is imbalance in the deviations across the two groups."},
    # Aim = "starting_and_adhering" path
    {"id": "4.3", "text": "Were important co-interventions balanced across intervention groups?", "options": list(_BASIC), "elaboration": "Risk of bias will be higher if unplanned co-interventions were implemented in a way that would bias the estimated effect of intervention. Bias will arise only if there is imbalance in such co-interventions between the intervention groups."},
    {"id": "4.4", "text": "Was the intervention implemented successfully for most participants?", "options": list(_BASIC), "elaboration": "Risk of bias will be higher if the intervention was not implemented as intended by, for example, the health care professionals delivering care during the trial."},
    {"id": "4.5", "text": "Did study participants adhere to the assigned intervention regimen?", "options": list(_BASIC), "elaboration": "Risk of bias will be higher if participants did not adhere to the intervention as intended. Lack of adherence includes imperfect compliance, cessation of intervention, crossovers, and switches to another active intervention."},
    {"id": "4.6", "text": "If N/PN to 4.3, 4.4 or 4.5: Was an appropriate analysis used to estimate the effect of starting and adhering to the intervention?", "options": list(_BASIC_NA), "elaboration": "Examples of appropriate analysis strategies include inverse probability weighting or instrumental variable estimation. Specialist advice may be needed to assess studies that used these approaches."},
]

DOMAIN5_SIGNALS_V1: list[dict[str, Any]] = [
    {"id": "5.1", "text": "Were outcome data available for all, or nearly all, participants?", "options": list(_BASIC), "elaboration": "'Nearly all' should be interpreted as 'enough to be confident of the findings'. Availability of data from 95% (or 90%) of participants may be sufficient when events are reasonably common in both intervention groups."},
    {"id": "5.2", "text": "Were participants excluded due to missing data on intervention status?", "options": list(_BASIC), "elaboration": "Missing intervention status may be a problem. This requires that the intended study sample is clear, which it may not be in practice."},
    {"id": "5.3", "text": "Were participants excluded due to missing data on other variables needed for the analysis?", "options": list(_BASIC), "elaboration": "This question relates particularly to participants excluded from the analysis because of missing information on confounders that were controlled for in the analysis."},
    {"id": "5.4", "text": "If PN/N to 5.1, or Y/PY to 5.2 or 5.3: Are the proportion of participants and reasons for missing data similar across interventions?", "options": list(_BASIC_NA), "elaboration": "This aims to elicit whether either differential proportion of missing observations or differences in reasons for missing observations could substantially impact on our ability to answer the question being addressed."},
    {"id": "5.5", "text": "If PN/N to 5.1, or Y/PY to 5.2 or 5.3: Is there evidence that results were robust to the presence of missing data?", "options": list(_BASIC_NA), "elaboration": "Evidence for robustness may come from how missing data were handled and whether sensitivity analyses were performed. Both content knowledge and statistical expertise will often be required for this judgement."},
]

DOMAIN6_SIGNALS_V1: list[dict[str, Any]] = [
    {"id": "6.1", "text": "Could the outcome measure have been influenced by knowledge of the intervention received?", "options": list(_BASIC), "elaboration": "Some outcome measures involve negligible assessor judgment, e.g. all-cause mortality or non-repeatable automated laboratory assessments. Risk of bias due to measurement of these outcomes would be expected to be low."},
    {"id": "6.2", "text": "Were outcome assessors aware of the intervention received by study participants?", "options": list(_BASIC), "elaboration": "N if outcome assessors were blinded. In studies where participants report their outcomes themselves, the outcome assessor is the study participant — in observational studies the answer will usually be 'Yes' when participants report their outcomes themselves."},
    {"id": "6.3", "text": "Were the methods of outcome assessment comparable across intervention groups?", "options": list(_BASIC), "elaboration": "Comparable assessment methods would involve the same outcome detection methods and thresholds, same time point, same definition, and same measurements."},
    {"id": "6.4", "text": "Were any systematic errors in measurement of the outcome related to intervention received?", "options": list(_BASIC), "elaboration": "This refers to differential misclassification of outcomes. Systematic errors in measuring the outcome, if present, could cause bias if they are related to intervention or to a confounder of the intervention-outcome relationship."},
]

DOMAIN7_SIGNALS_V1: list[dict[str, Any]] = [
    {"id": "7.1", "text": "Is the reported effect estimate likely to be selected, on the basis of the results, from multiple outcome measurements within the outcome domain?", "options": list(_BASIC), "elaboration": "For a specified outcome domain, it is possible to generate multiple effect estimates for different measurements. If multiple measurements were made but only one or a subset is reported, there is a risk of selective reporting on the basis of results."},
    {"id": "7.2", "text": "Is the reported effect estimate likely to be selected, on the basis of the results, from multiple analyses of the intervention-outcome relationship?", "options": list(_BASIC), "elaboration": "Examples include unadjusted vs adjusted models; final value vs change from baseline vs ANCOVA; different transformations; different covariate sets; different missing-data strategies. If the analyst does not pre-specify methods and multiple estimates are generated but only one or a subset is reported, there is a risk of selective reporting."},
    {"id": "7.3", "text": "Is the reported effect estimate likely to be selected, on the basis of the results, from different subgroups?", "options": list(_BASIC), "elaboration": "Particularly with large cohorts often available from routine data sources, it is possible to generate multiple effect estimates for different subgroups or simply to omit varying proportions of the original cohort."},
]


DOMAINS_V1: list[dict[str, Any]] = [
    {"id": 1, "name": "Bias due to confounding", "signals": DOMAIN1_SIGNALS_V1, "direction_options": ("NA", "Favours experimental", "Favours comparator", "Unpredictable"), "relevant_fields": ["confounders_measured", "adjustment_method", "exposure_definition", "comparator_group", "immortal_time_bias", "confounding_control", "consecutive_enrolment"]},
    {"id": 2, "name": "Bias in selection of participants into the study", "signals": DOMAIN2_SIGNALS_V1, "direction_options": ("NA", "Favours experimental", "Favours comparator", "Towards null", "Away from null", "Unpredictable"), "relevant_fields": ["case_source", "control_selection", "sampling_method", "loss_to_follow_up", "immortal_time_bias"]},
    {"id": 3, "name": "Bias in classification of interventions", "signals": DOMAIN3_SIGNALS_V1, "direction_options": ("NA", "Favours experimental", "Favours comparator", "Towards null", "Away from null", "Unpredictable"), "relevant_fields": ["exposure_definition", "exposure_measurement", "exposure_ascertainment", "intervention_classification"]},
    {"id": 4, "name": "Bias due to deviations from intended interventions", "signals": DOMAIN4_SIGNALS_V1, "aim_gated": True, "direction_options": ("NA", "Favours experimental", "Favours comparator", "Towards null", "Away from null", "Unpredictable"), "relevant_fields": ["intervention_classification", "loss_to_follow_up", "co_interventions", "adherence"]},
    {"id": 5, "name": "Bias due to missing data", "signals": DOMAIN5_SIGNALS_V1, "direction_options": ("NA", "Favours experimental", "Favours comparator", "Towards null", "Away from null", "Unpredictable"), "relevant_fields": ["loss_to_follow_up", "missing_data_handling", "attrition_rate"]},
    {"id": 6, "name": "Bias in measurement of outcomes", "signals": DOMAIN6_SIGNALS_V1, "direction_options": ("NA", "Favours experimental", "Favours comparator", "Towards null", "Away from null", "Unpredictable"), "relevant_fields": ["outcome_ascertainment", "outcome_definition", "assessor_blinding"]},
    {"id": 7, "name": "Bias in selection of the reported result", "signals": DOMAIN7_SIGNALS_V1, "direction_options": ("NA", "Favours experimental", "Favours comparator", "Towards null", "Away from null", "Unpredictable"), "relevant_fields": ["outcome_definition", "statistical_analysis", "pre_registered_protocol"]},
]


# ─────────────────────────────────────────────
# Prompts + orchestration
# ─────────────────────────────────────────────
_SYSTEM_PROMPT_V1 = (
    "You are an evidence-synthesis methodologist assessing risk of bias in a "
    "non-randomized study of an intervention using the Cochrane ROBINS-I tool "
    "(Version 1, 1 August 2016 cribsheet — Sterne JAC et al., BMJ 2016;355:i4919). "
    "Read the PDF carefully. Answer each signaling question with one of: "
    "Y (yes), PY (probably yes), PN (probably no), N (no), NI (no information). "
    "Some questions are gated on prior answers and additionally allow NA (not "
    "applicable). Provide a 1-2 sentence rationale for each answer, quoting "
    "the paper where possible. Return ONLY a valid JSON object — no preamble, "
    "no markdown fences."
)


def _signals_for_domain_v1(domain: dict[str, Any], aim: str) -> list[dict[str, Any]]:
    """For D4, return only the aim-relevant signals. For other domains,
    return all signals."""
    if domain.get("aim_gated"):
        if aim == "assignment_to":
            return [s for s in domain["signals"] if s["id"] in ("4.1", "4.2")]
        if aim == "starting_and_adhering":
            return [s for s in domain["signals"] if s["id"] in ("4.3", "4.4", "4.5", "4.6")]
    return domain["signals"]


# ─────────────────────────────────────────────
# Aim preflight (§1.1) — one LLM call, auto-determines the Stage-II aim
# ─────────────────────────────────────────────
_AIM_PREFLIGHT_RELEVANT_KEYS = (
    "analysis_framework",
    "primary_outcome_measurement",
    "outcome_definition",
    "outcome_ascertainment",
)


def _build_aim_preflight_prompt_v1(primary_outcome: str,
                                   extracted_fields: dict[str, str]) -> str:
    """Build the §1.1 aim-preflight prompt.

    Mirrors V2's `_build_preflight_prompt_cohort` structure (context block
    derived from prefilled methods/analysis fields + a single question);
    the question is V2's C4 reworded to map onto V1's AIMS output values.
    """
    relevant = {k: extracted_fields[k] for k in _AIM_PREFLIGHT_RELEVANT_KEYS
                if extracted_fields.get(k)}
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"

    return f"""You are determining the **aim of study** for a ROBINS-I V1 risk-of-bias assessment of a non-randomized study.

Outcome being assessed: {primary_outcome}

Context (fields already extracted from the paper):
{ctx_json}

ROBINS-I V1 assesses risk of bias against a target estimand. The Stage-II aim of study commits to which estimand the appraisal targets:

- **"assignment_to"** — the analysis estimates the effect of *assignment to* intervention (intention-to-treat). Participants are analysed in the group they were originally assigned to; switches, crossovers, and non-adherence are ignored. This aim is set when the paper reports an as-randomized / as-assigned / mITT analysis as its primary estimate.
- **"starting_and_adhering"** — the analysis estimates the effect of *starting and adhering to* intervention (per-protocol). The analysis is restricted to (or weights toward) participants who actually started and adhered to the assigned intervention, and protocol deviations are accounted for via censoring, IPCW, g-methods, marginal structural models, or instrumental-variable estimation. This aim is set when the paper reports a per-protocol / as-treated / completer analysis as its primary estimate.

**Question.** Which aim does the primary analysis of the paper target for the outcome being assessed?

Elaboration:
- If the paper reports both an ITT and a per-protocol analysis, pick the aim that matches the **headline / primary estimate** for this outcome, not the sensitivity analysis.
- Observational cohort studies typically map to **"starting_and_adhering"** because exposure is defined by who actually started the treatment — unless the analysis explicitly uses an ITT-like exposure definition (e.g. first prescription regardless of refill).
- If the analysis section is genuinely silent on whether protocol deviations are accounted for, default to **"assignment_to"** and note the ambiguity in the rationale.

Return JSON with exactly this shape:
{{
  "aim": "assignment_to|starting_and_adhering",
  "rationale": "1-2 sentences quoting or paraphrasing the analysis-section text that supports the choice"
}}"""


def determine_aim_v1(pdf_bytes: bytes,
                     primary_outcome: str,
                     extracted_fields: dict[str, str],
                     llm_call: Callable[[bytes, str, int], dict[str, Any]],
                     ) -> tuple[str, str]:
    """V1 aim preflight — single LLM call returns (aim, rationale).

    aim ∈ AIMS = ("assignment_to", "starting_and_adhering").
    Mechanically equivalent to V2's C4 question; only the output mapping differs.
    Falls back to "assignment_to" when the LLM returns an unrecognized value
    (matches the cribsheet's ambiguous-methods guidance).
    """
    prompt = _build_aim_preflight_prompt_v1(primary_outcome, extracted_fields)
    raw = llm_call(pdf_bytes, prompt, 512)
    aim_raw = str(raw.get("aim", "")).strip().lower()
    if aim_raw not in AIMS:
        logger.warning("ROBINS-I V1 aim preflight: invalid LLM answer %r — defaulting to 'assignment_to'", aim_raw)
        aim_raw = "assignment_to"
    rationale = str(raw.get("rationale", "")).strip()
    return aim_raw, rationale


def build_domain_prompt_v1(domain: dict[str, Any],
                           study_type: str,
                           primary_outcome: str,
                           extracted_fields: dict[str, str],
                           aim: str = "assignment_to",
                           target_pico: dict[str, str] | None = None) -> str:
    """Per-domain prompt for ROBINS-I V1."""
    signals = _signals_for_domain_v1(domain, aim)

    relevant = {k: extracted_fields[k] for k in domain.get("relevant_fields", [])
                if extracted_fields.get(k)}
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"

    aim_block = ""
    if domain.get("aim_gated"):
        aim_block = (
            f"\nAim of study: {aim}\n"
            '- "assignment_to" → answer signaling questions 4.1 and 4.2 only.\n'
            '- "starting_and_adhering" → answer signaling questions 4.3 through 4.6 only.\n'
        )

    pico_block = ""
    if target_pico:
        pico_block = "\nTarget PICO (user-supplied):\n" + json.dumps(target_pico, indent=2) + "\n"

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
    direction_options = domain.get("direction_options", ("NA",))
    shape += f'  "direction_of_bias": "{"|".join(direction_options)}"\n'
    shape += "}"

    return f"""Assess **Domain {domain['id']} — {domain['name']}** for the study described in the attached PDF using the ROBINS-I V1 tool (1 August 2016 cribsheet).

Study type: {study_type}
Outcome being assessed: {primary_outcome}
{aim_block}{pico_block}
Context (fields already extracted from the paper):
{ctx_json}

Signaling questions:
{questions_block}

Return a JSON object with exactly this shape:
{shape}

Notes on ROBINS-I V1:
- The signal vocabulary is **Y / PY / PN / N / NI** (5 tokens). For each question, answer based on what the paper says about that specific question — **do NOT try to determine whether a question is gated out by the cribsheet's cascading structure**. Python applies the cascade rules after you answer and will set `NA` for any question that should be gated out. Just answer each question independently based on its own text.
- The judgement scale is **Low / Moderate / Serious / Critical / No information** (5 levels). The code maps your signal answers to a judgement — answer the signaling questions only.
- Answer each question exactly as worded: Y/PY when the answer to the question as written is yes/probably yes, N/PN when it is no/probably no. Some questions are phrased so that "yes" indicates a problem and others so that "yes" indicates good practice — never translate your answer into "problem present/absent". Reserve NI for when the paper provides no information to answer the question.
- Rationales must be short (1-2 sentences) and quote the paper verbatim where possible."""


def _assess_domain_v1(pdf_bytes: bytes, domain: dict[str, Any],
                      study_type: str, primary_outcome: str,
                      extracted_fields: dict[str, str],
                      llm_call: Callable[[bytes, str, int], dict[str, Any]],
                      aim: str = "assignment_to",
                      target_pico: dict[str, str] | None = None) -> dict[str, Any]:
    prompt = build_domain_prompt_v1(
        domain, study_type, primary_outcome, extracted_fields, aim, target_pico)
    raw = llm_call(pdf_bytes, prompt, 8192)

    signals_for_this = _signals_for_domain_v1(domain, aim)
    signals: dict[str, str] = {}
    rationales: dict[str, str] = {}
    for sig in signals_for_this:
        sid = sig["id"]
        ans = str(raw.get(sid, "NI")).strip().upper()
        allowed = set(sig["options"])
        if ans not in allowed:
            logger.warning("ROBINS-I V1 domain %s question %s: invalid answer %r — defaulting to NI",
                           domain["id"], sid, ans)
            ans = "NI" if "NI" in allowed else next(iter(allowed))
        signals[sid] = ans
        rationales[sid] = str(raw.get(f"{sid}_rationale", "")).strip()

    # Python-side cascade enforcement: override LLM answers to NA for
    # questions gated out by the cribsheet's cascading structure.
    # See §17.6 and the enforce_cascade_*_v1 docstrings for the rules.
    pre_cascade = dict(signals)
    signals = enforce_cascade_v1(domain["id"], signals, aim=aim)
    overrides = {sid: (pre_cascade[sid], signals[sid])
                 for sid in signals
                 if sid in pre_cascade and pre_cascade[sid] != signals[sid]}
    if overrides:
        logger.debug("ROBINS-I V1 D%s cascade enforcement overrode LLM answers: %r",
                     domain["id"], overrides)

    if domain["id"] == 4:
        judgement = domain4_judge_v1(signals, aim=aim)
    else:
        judgement = DOMAIN_JUDGES_V1[domain["id"]](signals)

    direction = str(raw.get("direction_of_bias", "NA")).strip() or "NA"

    out: dict[str, Any] = {
        "signals": signals,
        "rationales": rationales,
        "judgement": judgement,
        "direction": direction,
    }
    if domain.get("aim_gated"):
        out["aim"] = aim
    return out


def run_v1(pdf_bytes: bytes,
           extracted_fields: dict[str, str],
           classification: dict[str, str],
           primary_outcome: str,
           *,
           llm_call: Callable[[bytes, str, int], dict[str, Any]],
           aim: str | None = None,
           target_pico: dict[str, str] | None = None,
           progress: Callable[[int], None] | None = None,
           ) -> tuple[dict[str, Any], str, str, str | None]:
    """Run ROBINS-I V1 against a non-randomized study.

    Returns (domain_results, overall_judgement, overall_direction, aim_rationale).

    `aim`:
        - `None` (default) → auto-determine via the §1.1 aim preflight (one
          LLM call). The chosen aim is recorded on the D4 result; the
          rationale is returned as the 4th tuple element.
        - `"assignment_to"` → uses D4 questions 4.1+4.2 (ITT estimand).
          Manual Stage-II value; preflight is skipped; aim_rationale is None.
        - `"starting_and_adhering"` → uses D4 questions 4.3-4.6 (per-protocol
          estimand). Manual Stage-II value; preflight is skipped;
          aim_rationale is None.

    V1's original cribsheet has no preflight stage; this implementation adds
    the optional aim-preflight LLM call (§1.1) so the Stage-II aim can be
    auto-determined from the paper. All 7 domains are still assessed
    unconditionally — the preflight only chooses which D4 question subset
    to ask.
    """
    if aim is None:
        aim, aim_rationale = determine_aim_v1(
            pdf_bytes, primary_outcome, extracted_fields, llm_call)
    else:
        if aim not in AIMS:
            raise ValueError(f"aim must be None or one of {AIMS}; got {aim!r}")
        aim_rationale = None
    study_type = classification.get("study_type", "Cohort Study")

    domain_results: dict[str, Any] = {}
    for domain in DOMAINS_V1:
        if progress:
            try:
                progress(domain["id"])
            except Exception:
                pass
        result = _assess_domain_v1(pdf_bytes, domain, study_type,
                                   primary_outcome, extracted_fields,
                                   llm_call=llm_call, aim=aim,
                                   target_pico=target_pico)
        result["id"] = domain["id"]
        result["name"] = domain["name"]
        domain_results[str(domain["id"])] = result

    domain_judgements = [domain_results[str(d["id"])]["judgement"] for d in DOMAINS_V1]
    overall = robins_i_v1_overall(domain_judgements)

    dirs = [domain_results[str(d["id"])]["direction"]
            for d in DOMAINS_V1
            if domain_results[str(d["id"])]["direction"] not in ("", "NA")]
    if not dirs:
        overall_direction = "NA"
    else:
        counts = Counter(dirs).most_common()
        if len(counts) > 1 and counts[0][1] == counts[1][1]:
            overall_direction = "Unpredictable"
        else:
            overall_direction = counts[0][0]

    return domain_results, overall, overall_direction, aim_rationale
```

---

## 16. Quick test sketches

Plain `assert` statements (no framework) covering the V1 decision trees + overall aggregation. Drop these at the bottom of the V1 module and run with `python3 robins_i_v1.py`.

```python
# ─────────────────────────────────────────────
# Domain 1 — confounding (cascading)
# ─────────────────────────────────────────────
assert domain1_judge_v1({"1.1": "N"}) == "Low"
assert domain1_judge_v1({"1.1": "PN"}) == "Low"
assert domain1_judge_v1({"1.1": "NI"}) == "No information"
# Baseline-only, all clean → Moderate
assert domain1_judge_v1({"1.1": "Y", "1.2": "N", "1.3": "N",
                         "1.4": "Y", "1.5": "Y", "1.6": "N"}) == "Moderate"
# 1.4 N → Serious
assert domain1_judge_v1({"1.1": "Y", "1.2": "N", "1.3": "N",
                         "1.4": "N", "1.5": "Y", "1.6": "N"}) == "Serious"
# 1.6 Y (over-adjustment) → Serious
assert domain1_judge_v1({"1.1": "Y", "1.2": "N", "1.3": "N",
                         "1.4": "Y", "1.5": "Y", "1.6": "Y"}) == "Serious"
# Time-varying path, all clean → Moderate
assert domain1_judge_v1({"1.1": "Y", "1.2": "Y", "1.3": "Y",
                         "1.7": "Y", "1.8": "Y"}) == "Moderate"
# Time-varying, 1.7 N → Serious
assert domain1_judge_v1({"1.1": "Y", "1.2": "Y", "1.3": "Y",
                         "1.7": "N", "1.8": "Y"}) == "Serious"

# ─────────────────────────────────────────────
# Domain 2 — selection
# ─────────────────────────────────────────────
assert domain2_judge_v1({"2.1": "N", "2.4": "Y"}) == "Low"
assert domain2_judge_v1({"2.1": "Y", "2.2": "Y", "2.3": "Y",
                         "2.4": "Y", "2.5": "Y"}) == "Moderate"
assert domain2_judge_v1({"2.1": "Y", "2.2": "Y", "2.3": "Y",
                         "2.4": "Y", "2.5": "N"}) == "Serious"
assert domain2_judge_v1({"2.1": "N", "2.4": "N", "2.5": "N"}) == "Serious"

# ─────────────────────────────────────────────
# Domain 3 — classification
# ─────────────────────────────────────────────
assert domain3_judge_v1({"3.1": "Y", "3.2": "Y", "3.3": "N"}) == "Low"
assert domain3_judge_v1({"3.1": "N", "3.2": "Y", "3.3": "N"}) == "Serious"
assert domain3_judge_v1({"3.1": "Y", "3.2": "Y", "3.3": "Y"}) == "Serious"
assert domain3_judge_v1({"3.1": "Y", "3.2": "N", "3.3": "N"}) == "Moderate"
assert domain3_judge_v1({"3.1": "NI"}) == "No information"

# ─────────────────────────────────────────────
# Domain 4 — deviations (aim-gated)
# ─────────────────────────────────────────────
assert domain4_judge_v1({"4.1": "N"}, aim="assignment_to") == "Low"
assert domain4_judge_v1({"4.1": "Y", "4.2": "N"}, aim="assignment_to") == "Low"
assert domain4_judge_v1({"4.1": "Y", "4.2": "Y"}, aim="assignment_to") == "Serious"
assert domain4_judge_v1({"4.3": "Y", "4.4": "Y", "4.5": "Y"},
                        aim="starting_and_adhering") == "Low"
assert domain4_judge_v1({"4.3": "N", "4.4": "Y", "4.5": "Y", "4.6": "Y"},
                        aim="starting_and_adhering") == "Moderate"
assert domain4_judge_v1({"4.3": "N", "4.4": "Y", "4.5": "Y", "4.6": "N"},
                        aim="starting_and_adhering") == "Serious"
try:
    domain4_judge_v1({}, aim="bogus")
    assert False, "should have raised"
except ValueError:
    pass

# ─────────────────────────────────────────────
# §1.1 Aim preflight — auto-determination of the Stage-II aim
# ─────────────────────────────────────────────
def _mock_llm_itt(pdf_bytes, prompt, max_tokens):
    return {"aim": "assignment_to",
            "rationale": "Methods: intention-to-treat analysis; all participants analysed in originally assigned group."}

def _mock_llm_pp(pdf_bytes, prompt, max_tokens):
    return {"aim": "starting_and_adhering",
            "rationale": "Methods: per-protocol with inverse probability of censoring weights for treatment discontinuation."}

def _mock_llm_garbage(pdf_bytes, prompt, max_tokens):
    return {"aim": "Maybe?", "rationale": ""}

# ITT-like analysis → assignment_to
aim, rat = determine_aim_v1(b"", "all-cause mortality at 12 months",
                            {"analysis_framework": "ITT; participants analysed as originally assigned"},
                            llm_call=_mock_llm_itt)
assert aim == "assignment_to"
assert "intention-to-treat" in rat.lower()

# Per-protocol-like analysis (with IPCW) → starting_and_adhering
aim, rat = determine_aim_v1(b"", "all-cause mortality at 12 months",
                            {"analysis_framework": "Per-protocol with IPCW for treatment discontinuation"},
                            llm_call=_mock_llm_pp)
assert aim == "starting_and_adhering"
assert "ipcw" in rat.lower() or "per-protocol" in rat.lower()

# Garbage LLM answer → safe default per cribsheet ambiguity guidance
aim, rat = determine_aim_v1(b"", "any outcome", {}, llm_call=_mock_llm_garbage)
assert aim == "assignment_to"

# Empty extracted_fields → prompt builder still produces a valid prompt
prompt = _build_aim_preflight_prompt_v1("all-cause mortality at 12 months", {})
assert "Outcome being assessed: all-cause mortality at 12 months" in prompt
assert "(no pre-extracted fields)" in prompt
assert '"aim": "assignment_to|starting_and_adhering"' in prompt

# ─────────────────────────────────────────────
# Domain 5 — missing data
# ─────────────────────────────────────────────
assert domain5_judge_v1({"5.1": "Y", "5.2": "N", "5.3": "N"}) == "Low"
assert domain5_judge_v1({"5.1": "N", "5.2": "N", "5.3": "N",
                         "5.4": "Y", "5.5": "NI"}) == "Moderate"
assert domain5_judge_v1({"5.1": "N", "5.2": "N", "5.3": "N",
                         "5.4": "N", "5.5": "N"}) == "Serious"

# ─────────────────────────────────────────────
# Domain 6 — measurement
# ─────────────────────────────────────────────
assert domain6_judge_v1({"6.1": "N", "6.2": "Y", "6.3": "Y", "6.4": "N"}) == "Low"
assert domain6_judge_v1({"6.1": "N", "6.2": "Y", "6.3": "N", "6.4": "N"}) == "Serious"
assert domain6_judge_v1({"6.1": "Y", "6.2": "Y", "6.3": "Y", "6.4": "N"}) == "Serious"
assert domain6_judge_v1({"6.1": "N", "6.2": "N", "6.3": "Y", "6.4": "Y"}) == "Serious"

# ─────────────────────────────────────────────
# Domain 7 — selective reporting
# ─────────────────────────────────────────────
assert domain7_judge_v1({"7.1": "N", "7.2": "N", "7.3": "N"}) == "Low"
assert domain7_judge_v1({"7.1": "Y", "7.2": "N", "7.3": "N"}) == "Serious"
assert domain7_judge_v1({"7.1": "Y", "7.2": "Y", "7.3": "N"}) == "Critical"
assert domain7_judge_v1({"7.1": "NI", "7.2": "NI", "7.3": "NI"}) == "No information"

# ─────────────────────────────────────────────
# Overall aggregation (Table 3)
# ─────────────────────────────────────────────
assert robins_i_v1_overall(["Low"] * 7) == "Low"
assert robins_i_v1_overall(["Low", "Moderate", "Low", "Low", "Moderate", "Low", "Low"]) == "Moderate"
assert robins_i_v1_overall(["Low", "Serious", "Low", "Low", "Low", "Low", "Low"]) == "Serious"
assert robins_i_v1_overall(["Low", "Serious", "Critical", "Low", "Low", "Low", "Low"]) == "Critical"
assert robins_i_v1_overall(["No information"] * 7) == "No information"
assert robins_i_v1_overall(["Low", "Moderate", "No information", "Low", "Low", "Low", "Low"]) == "No information"
assert robins_i_v1_overall([]) == "No information"

# ─────────────────────────────────────────────
# Cascade enforcement (Python-side NA gating)
# ─────────────────────────────────────────────
# D1 — 1.1 N/PN → all downstream NA (early exit)
out = enforce_cascade_d1_v1({"1.1": "N", "1.2": "Y", "1.3": "Y",
                             "1.4": "Y", "1.5": "Y", "1.6": "N",
                             "1.7": "Y", "1.8": "Y"})
assert out["1.1"] == "N"
for sid in ("1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8"):
    assert out[sid] == "NA", f"D1 early-exit failed for {sid}"

# D1 — 1.1 NI → all downstream NA
out = enforce_cascade_d1_v1({"1.1": "NI"})
for sid in ("1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8"):
    assert out[sid] == "NA"

# D1 — time-varying path: 1.1 Y, 1.2 Y, 1.3 Y → 1.4-1.6 NA, 1.7/1.8 kept
out = enforce_cascade_d1_v1({"1.1": "Y", "1.2": "Y", "1.3": "Y",
                             "1.4": "Y", "1.5": "Y", "1.6": "N",
                             "1.7": "Y", "1.8": "PY"})
for sid in ("1.4", "1.5", "1.6"):
    assert out[sid] == "NA", f"D1 time-varying path didn't NA {sid}"
assert out["1.7"] == "Y"
assert out["1.8"] == "PY"

# D1 — baseline-only path: 1.1 Y, 1.2 N → 1.3 NA (1.2 not Y/PY), 1.7/1.8 NA
out = enforce_cascade_d1_v1({"1.1": "Y", "1.2": "N", "1.3": "Y",
                             "1.4": "Y", "1.5": "Y", "1.6": "N",
                             "1.7": "Y", "1.8": "Y"})
assert out["1.3"] == "NA", "1.3 should be NA when 1.2 not Y/PY"
assert out["1.7"] == "NA"
assert out["1.8"] == "NA"
assert out["1.4"] == "Y"  # baseline-only path keeps 1.4-1.6
assert out["1.6"] == "N"

# D1 — baseline-only, 1.4 N → 1.5 NA (1.5 only asked if 1.4 Y/PY)
out = enforce_cascade_d1_v1({"1.1": "Y", "1.2": "N", "1.3": "N",
                             "1.4": "N", "1.5": "Y", "1.6": "N"})
assert out["1.5"] == "NA"
assert out["1.4"] == "N"

# D1 — time-varying, 1.7 N → 1.8 NA
out = enforce_cascade_d1_v1({"1.1": "Y", "1.2": "Y", "1.3": "Y",
                             "1.7": "N", "1.8": "Y"})
assert out["1.8"] == "NA"

# D2 — 2.1 N → 2.2/2.3 NA
out = enforce_cascade_d2_v1({"2.1": "N", "2.2": "Y", "2.3": "Y",
                             "2.4": "Y", "2.5": "Y"})
assert out["2.2"] == "NA"
assert out["2.3"] == "NA"
assert out["2.5"] == "NA"  # neither (2.2+2.3 Y/PY) nor (2.4 N/PN)

# D2 — 2.1 Y, 2.2 N → 2.3 NA
out = enforce_cascade_d2_v1({"2.1": "Y", "2.2": "N", "2.3": "Y",
                             "2.4": "Y", "2.5": "Y"})
assert out["2.3"] == "NA"

# D2 — 2.5 kept when 2.4 N/PN
out = enforce_cascade_d2_v1({"2.1": "N", "2.2": "Y", "2.3": "Y",
                             "2.4": "N", "2.5": "Y"})
assert out["2.5"] == "Y"  # 2.4 N triggers 2.5

# D4 — assignment_to: 4.2 NA when 4.1 N
out = enforce_cascade_d4_v1({"4.1": "N", "4.2": "Y"}, aim="assignment_to")
assert out["4.2"] == "NA"

# D4 — assignment_to: 4.2 kept when 4.1 Y
out = enforce_cascade_d4_v1({"4.1": "Y", "4.2": "Y"}, aim="assignment_to")
assert out["4.2"] == "Y"

# D4 — starting_and_adhering: 4.6 NA when 4.3/4.4/4.5 all Y/PY (no rescue needed)
out = enforce_cascade_d4_v1({"4.3": "Y", "4.4": "Y", "4.5": "Y", "4.6": "Y"},
                            aim="starting_and_adhering")
assert out["4.6"] == "NA"

# D4 — starting_and_adhering: 4.6 kept when any of 4.3/4.4/4.5 N/PN
out = enforce_cascade_d4_v1({"4.3": "N", "4.4": "Y", "4.5": "Y", "4.6": "Y"},
                            aim="starting_and_adhering")
assert out["4.6"] == "Y"

# D5 — 5.4/5.5 NA when no missingness trigger
out = enforce_cascade_d5_v1({"5.1": "Y", "5.2": "N", "5.3": "N",
                             "5.4": "Y", "5.5": "Y"})
assert out["5.4"] == "NA"
assert out["5.5"] == "NA"

# D5 — 5.4/5.5 kept when 5.1 N (missingness triggered)
out = enforce_cascade_d5_v1({"5.1": "N", "5.2": "N", "5.3": "N",
                             "5.4": "Y", "5.5": "Y"})
assert out["5.4"] == "Y"
assert out["5.5"] == "Y"

# Dispatch helper — D3/D6/D7 unchanged
assert enforce_cascade_v1(3, {"3.1": "Y"}) == {"3.1": "Y"}
assert enforce_cascade_v1(6, {"6.1": "N"}) == {"6.1": "N"}
assert enforce_cascade_v1(7, {"7.1": "N"}) == {"7.1": "N"}

# Integration: an LLM that wrongly answered a gated question is caught.
# Imagine the LLM said 1.3 = "Y" even though 1.2 = "N" (which gates 1.3 out).
# Without cascade enforcement, the tree would route into time-varying path
# and look for 1.7/1.8 → No information. WITH enforcement, 1.3 → NA, and
# the tree correctly routes baseline-only.
llm_response = {"1.1": "Y", "1.2": "N", "1.3": "Y",  # 1.3 inconsistent!
                "1.4": "Y", "1.5": "Y", "1.6": "N"}
enforced = enforce_cascade_d1_v1(llm_response)
assert enforced["1.3"] == "NA"
assert domain1_judge_v1(enforced) == "Moderate"  # baseline-only path

# ─────────────────────────────────────────────
# DOMAINS_V1 structural invariants
# ─────────────────────────────────────────────
assert len(DOMAINS_V1) == 7
assert [d["id"] for d in DOMAINS_V1] == [1, 2, 3, 4, 5, 6, 7]
assert len(DOMAIN1_SIGNALS_V1) == 8
assert len(DOMAIN2_SIGNALS_V1) == 5
assert len(DOMAIN3_SIGNALS_V1) == 3
assert len(DOMAIN4_SIGNALS_V1) == 6
assert len(DOMAIN5_SIGNALS_V1) == 5
assert len(DOMAIN6_SIGNALS_V1) == 4
assert len(DOMAIN7_SIGNALS_V1) == 3
assert sum(len(d["signals"]) for d in DOMAINS_V1) == 34
assert "NI" not in DOMAIN1_SIGNALS_V1[0]["options"]
assert DOMAINS_V1[3].get("aim_gated") is True
assert sum(1 for d in DOMAINS_V1 if d.get("aim_gated")) == 1
assert len(DOMAINS_V1[0]["direction_options"]) == 4  # D1 special: 3 + NA
for d in DOMAINS_V1[1:]:
    assert len(d["direction_options"]) == 6

print("All ROBINS-I V1 sanity checks passed.")
```

---


## 17. V1 → V2 differences and migration

### 17.1 V1 → V2 at a glance

| Aspect | V1 (1 Aug 2016) | V2 (20 Nov 2025) |
|--|--|--|
| Domain count | **7** | **6** (V1 D4 retired — see §18.2) |
| Signal vocabulary | Y / PY / PN / N / NI | Y / PY / PN / N / NI + **WN / SN / WY / SY** (weak/strong tokens added) |
| Per-domain judgement scale | **5-level** Low / Moderate / Serious / Critical / **No information** | **4-level** Low / Moderate / Serious / Critical (NI retired as a judgement) |
| Overall judgement scale | 5-level (NI possible) | 4-level worst-domain |
| Variant support for D1 | Single tree, two paths chosen by 1.2/1.3 cascade | Three explicit variants: **A** (ITT/baseline only), **B** (per-protocol/baseline + time-varying), **single_arm** (uncontrolled designs) |
| Preflight | None in original cribsheet; this impl adds an **optional aim preflight** (§1.1) that auto-determines the Stage-II aim of study; all 7 domains still assessed | **Mandatory preflight** with B1/B2/B3 + C4 short-circuits + variant routing |
| D1 "Low" label | Plain "Low" | **"Low (except for concerns about uncontrolled confounding)"** for cohort; "Low (except for concerns about uncontrolled benchmarking)" for single-arm |
| Aim-of-study handling | Stage-II checkbox OR §1.1 aim-preflight LLM call (single question, same protocol-deviation-accounting decision as V2's C4); gates D4 question subset | Preflight C4 selects D1 variant A vs B; D4 no longer exists |
| Direction-of-bias options | 3 (D1) or 5 (D2+); per-domain | 6 per-domain + 1 overall (modal across domains, ties → "Unpredictable") |
| Single-arm support | **Not addressed in the published cribsheet.** This implementation adds a project-specific `single_arm` variant (§19) that mirrors V2's pattern for V1's 5-token vocab + 5-level scale: D1-SA (1S.1–1S.5 benchmark adequacy), D2-SA (2S.1–2S.3 intervention fidelity + intent-vs-received), D4 marked NA in code. | Project-specific `single_arm` variant of D1 + D2 |

### 17.2 Domain mapping V1 ↔ V2

| V1 domain | V2 equivalent | Notes |
|--|--|--|
| **D1** Bias due to confounding | **D1** Bias due to confounding | Same intent. V2 splits into 3 explicit variants (A/B/single-arm) instead of V1's branching-cascade tree; V2 adds 1A.4 / 1B.5 / 1S.5 (negative-control evidence) as an explicit Critical-elevating question. |
| **D2** Bias in selection of participants into the study | **D3** Bias in selection of participants into the study (or analysis) | Renumbered. V2 expands to 8 signals (V1 had 5); adds C-subsection (analysis correction / sensitivity / severity). |
| **D3** Bias in classification of interventions | **D2** Bias in classification of interventions | Renumbered. V2 expands to 5 signals (V1 had 3); adds explicit immortal-time question (2.1/2.2) and weak/strong misclassification tokens (2.4 SY/WY). |
| **D4** Bias due to deviations from intended interventions | **— Retired —** | Folded into V2 Domain 1 Variant B (time-varying confounding) when the analysis estimates the per-protocol effect. V1 D4.1/D4.2 (assignment-to effect) effectively disappears in V2 because V2 Variant A already assumes ITT. |
| **D5** Bias due to missing data | **D4** Bias due to missing data | Renumbered. V2 expands substantially: 11 signals (V1 had 5) with explicit MAR/MCAR/MNAR paths + complete-case vs imputation vs alternative-method branching + 4.11 sensitivity-analysis rescue. |
| **D6** Bias in measurement of outcomes | **D5** Bias arising from measurement of the outcome | Renumbered. V2 reduces to 3 signals (V1 had 4) but adds weak/strong yes tokens on 5.3. V1 6.1 (could outcome be influenced by knowledge?) and V1 6.4 (systematic errors related to intervention?) are folded into the V2 5.1 + 5.3 decision-tree branching. |
| **D7** Bias in selection of the reported result | **D6** Bias in selection of the reported result | Renumbered. Signal questions essentially identical (V1 7.1/7.2/7.3 ≅ V2 6.2/6.3/6.4). V2 adds 6.1 (was the result reported in accordance with a pre-determined analysis plan?) as the explicit Low-risk anchor. |

### 17.3 Single-arm vocab collapse (V1 → V2)

The V1 single-arm adaptation in §19 ports V2's `single_arm` signal sets into V1's 5-token vocab (no WN/SN/WY/SY gradient). Three V2-SA signals carry gradient tokens that have to collapse:

| V2-SA signal | V2 vocab | V1-SA vocab | Collapse mapping |
|--|--|--|--|
| **1S.3** (prognostic comparability) | `NA / Y / PY / WN / SN / NI` | `NA / Y / PY / PN / N / NI` | **PN ≈ V2 WN** (most-but-not-all comparable); **N ≈ V2 SN** (materially less comparable) |
| **2S.2** (dose modifications recorded) | `Y / PY / WN / SN / NI` | `Y / PY / PN / N / NI` | **PN ≈ V2 WN** (most exposure modifications recorded); **N ≈ V2 SN** (material detail missing) |
| **2S.3** (intent-vs-received cohort) | `SY / WY / PN / N / NI` | `Y / PY / PN / N / NI` | **Y ≈ V2 SY** (primary analysis explicitly restricted to completers / responders — Critical); **PY ≈ V2 WY** (excludes some enrolled but not dominantly — Serious) |

The 1S.3 / 2S.2 collapse loses one severity level (V2 routes WN to "Moderate floor" and SN to "Serious" separately; V1 collapses PN→Moderate and N→Serious, omitting the intermediate). The 2S.3 collapse is the most consequential — reviewers using V1-SA must answer 2S.3 with **Y only when the paper explicitly restricts the primary analysis to completers/responders** (V2-SY-equivalent → Critical). PY is the V2-WY-equivalent for borderline cases → Serious. Both decisions are documented in the per-signal elaborations and in §19.4's decision tree.

This collapse is "conservative" in the same sense as the rest of V1's narrative-table interpretations (see §0 conservative-tree note): the deterministic tree picks the harsher of the two plausible V1-tokens-to-V2-bucket mappings, and reviewers may legitimately override based on the prose rationale.

---

## 18. Cascade enforcement — design notes

V1 has cascading signaling questions in Domains 1, 2, 4, and 5 — where one question's answer determines whether a downstream question is gated out (NA). This implementation enforces those rules in pure Python via the `enforce_cascade_d{1,2,4,5}_v1()` functions in §15, called from `_assess_domain_v1()` after the LLM responds and before the decision tree judges. The LLM is asked to answer every signaling question based solely on its reading of the paper; Python then overrides any answer to `NA` for questions the cascade rules indicate are gated out.

### 18.1 Why Python-side, not LLM-side

1. **Cascade rules are deterministic.** They come from the cribsheet itself (e.g. "If N/PN to 1.1: the study can be considered to be at low risk of bias due to confounding and no further signalling questions need be considered"). There is no judgement call about when a question is NA. Putting deterministic logic in Python is the right separation of concerns: LLM answers "what does the paper say?", Python answers "what does the cribsheet say to ask next?".

2. **LLMs sometimes answer gated questions inconsistently.** If the LLM is asked to determine NA from the cascade, it has to (a) read the cribsheet rules, (b) read the paper, (c) decide whether the gating question's answer triggers NA, (d) decide what the paper says about the gated question. Step (c) is unnecessary work that frequently produces errors — the LLM sees `1.3` and answers what the paper says about it (e.g. "Y"), regardless of the gating signal at `1.2`. Without Python enforcement, this inconsistency silently routes the decision tree into the wrong path (e.g. into the time-varying confounding branch when the paper used baseline-only analysis).

3. **Catches LLM inconsistency at runtime.** When the LLM answers a gated question substantively (e.g. `1.3 = "Y"` when `1.2 = "N"`), Python overrides to `NA` and logs the override at debug level. Downstream judgement uses the cascade-enforced signals, so the same paper always produces the same tree path regardless of LLM stochasticity.

4. **The prompt is simpler.** Instead of explaining the cascading-question rules to the LLM in the prompt (and asking it to apply them), the prompt just says "answer each question based on the paper; Python applies the cascade rules". Shorter prompt, fewer instructions for the LLM to misinterpret.

5. **Cost-neutral.** The LLM still answers every signal in one call (one LLM call per domain). The alternative — calling the LLM twice per cascading domain (first for gating questions, then conditionally for downstream questions) — would add latency and roughly double the cost for D1, D2, D4, D5.

**Trade-off accepted:** the LLM occasionally wastes tokens answering a question that will be overridden to NA. For V1's cascading domains, this is at most ~5 unnecessary signal answers per paper (D1 = 4 baseline OR time-varying questions; D2 = up to 2 selection-cascade questions; D4 = 1 within-path question; D5 = 2 follow-up questions). The cost is negligible (~$0.001 per paper); the gain is determinism + LLM-error catching.

### 18.2 Cascade rules per domain

The exact rules each `enforce_cascade_*_v1` function applies (cribsheet page references in parentheses):

- **D1** (cribsheet pp 5-6):
  - `1.1 ∈ {N, PN, NI}` → `1.2-1.8` all NA (early exit; or insufficient info to proceed)
  - `1.1 ∈ {Y, PY}` AND (`1.2 ∈ {Y, PY}` AND `1.3 ∈ {Y, PY}`) → **time-varying path**: `1.4, 1.5, 1.6` NA; `1.7` asked; `1.8` NA unless `1.7 ∈ {Y, PY}`
  - `1.1 ∈ {Y, PY}` AND NOT (1.2 Y/PY AND 1.3 Y/PY) → **baseline-only path**: `1.7, 1.8` NA; `1.4, 1.6` asked; `1.5` NA unless `1.4 ∈ {Y, PY}`; `1.3` NA if `1.2 ∉ {Y, PY}`

- **D2** (cribsheet p 7):
  - `2.1 ∉ {Y, PY}` → `2.2, 2.3` NA (go to 2.4)
  - `2.1 ∈ {Y, PY}` AND `2.2 ∉ {Y, PY}` → `2.3` NA
  - `2.5` NA unless (`2.2 ∈ {Y, PY}` AND `2.3 ∈ {Y, PY}`) OR (`2.4 ∈ {N, PN}`)

- **D4** (cribsheet pp 9-10), aim-gating applied first by `_signals_for_domain_v1`:
  - `aim = assignment_to`: `4.2` NA unless `4.1 ∈ {Y, PY}`
  - `aim = starting_and_adhering`: `4.6` NA unless any of `4.3, 4.4, 4.5 ∈ {N, PN}`

- **D5** (cribsheet p 11):
  - `5.4, 5.5` NA unless `5.1 ∈ {PN, N}` OR `5.2 ∈ {Y, PY}` OR `5.3 ∈ {Y, PY}`

- **D3, D6, D7**: no cascading. `enforce_cascade_v1()` returns the signals unchanged for these domains.

### 18.3 Single-arm D4 NA-derivation rule

For single-arm runs (§19), Domain 4 is set to `judgement: "NA"` in code with **no LLM call** — mirroring the RoB 2 Cluster NA-cascade pattern for conditional questions. V2 retired V1's D4 entirely; its concerns (intent-vs-received cohort definition) are folded into V2-SA's D2-SA question 2S.3, which V1-SA also adopts (see §19.4). The NA flag carries a `reason` field on the result dict so downstream UIs can explain why D4 was skipped. The aggregator (`robins_i_v1_overall`) excludes NA from the worst-domain calculation, so a single-arm paper with all D1-D7 (minus D4) = Low still produces overall = Low.

The single-arm D1 + D2 signals (§19.3 + §19.4) **have no cribsheet cascade** — V2's published SA cribsheet specifies independent signals for each of 1S.1–1S.5 and 2S.1–2S.3, so `enforce_cascade_v1()` is bypassed when `variant == "single_arm"`. The LLM answers each SA signal independently; no Python override applies.

---

## 19. Single-arm adaptation (project-specific extension)

V1's published cribsheet (1 August 2016) covers only cohort-type studies. This section documents the **project-specific single-arm adaptation** that ships alongside V1 cohort in [backend/rob_tools/robins_i_v1.py](../../backend/rob_tools/robins_i_v1.py). It mirrors V2's `single_arm` variant (see `docs/shareable/robins_i_v2_shareable.md` §3.3 + §4.2) but uses V1's 5-token signal vocab (Y/PY/PN/N/NI — no WN/SN/WY/SY) and 5-level judgement scale (Low / Moderate / Serious / Critical / No information). See §17.3 for the V1→V2 vocab-collapse mapping rules used by the SA decision trees.

**Status.** This is a best-effort adaptation by the AI Researcher team. It is **not officially endorsed by the ROBINS-I V1 authors**. The published Cochrane guidance for single-arm risk-of-bias is V2's `single_arm` variant. The V1 SA adaptation exists so users on the V1 toolchain (e.g. the OVID team) can appraise single-arm papers without switching tools mid-review.

**Initial GRADE.** Single-arm papers start at "Very low" certainty in the GRADE registry — uncontrolled designs cannot achieve higher than Very low without a comparator group. `compute_grade` clamps further downgrades at Very low. The same registry entry applies regardless of which ROBINS-I version (V1 or V2) is selected.

### 19.1 Variant routing

The variant is pinned at `run()` entry from `classification["study_type"]` BEFORE the preflight LLM call. This mirrors V2's pattern (see V2 shareable §2.1):

```python
SINGLE_ARM_STUDY_TYPES = frozenset({"Single-Arm Trial", "Dose-Escalation Study"})

def run(pdf_bytes, extracted_fields, classification, primary_outcome, ...):
    study_type = classification.get("study_type", "Cohort Study")
    is_single_arm = study_type in SINGLE_ARM_STUDY_TYPES
    if is_single_arm:
        # benchmark preflight (§19.2) + SA domain assessment (§19.3-19.5)
        ...
    else:
        # standard V1 cohort path: aim preflight (§1.1) + 7-domain assessment
        ...
```

Variant is NOT selected from a preflight LLM answer (V2's C4 question is also not used for variant routing in V2-SA — it's recorded as metadata only). The study-type classification (from the upstream annotator classifier) is the authoritative variant selector.

### 19.2 Benchmark preflight (single-arm path)

Replaces the §1.1 aim preflight when the variant is single_arm. Single LLM call asks four preliminary-consideration questions adapted from V2's single-arm preflight (V2 shareable §2.3):

**B1-SA.** Did the authors pre-specify a quantitative benchmark (historical control rate, performance criterion, or null hypothesis with a statistical decision rule) against which the single-arm result is being judged?
Options: `Y / PY / PN / N` (no NI — mirrors V1's 1.1 convention; you must commit).

**B2-SA.** (Only if N/PN to B1-SA) Is the absence of any pre-specified benchmark severe enough that the single-arm proportion is uninterpretable for causal inference?
Options: `Y / PY / PN / N / NA` (NA when B1-SA was Y/PY).

**B3.** Was the method of measuring the outcome inappropriate? (Reused verbatim from V2's cohort preflight.)
Options: `Y / PY / PN / N`.

**C4.** Did the analysis account for protocol deviations during follow-up?
Options: `No` (ITT-like / modified-ITT) / `Yes` (per-protocol restricted to completers / responders).
**Recorded as metadata only** — does NOT swap variants. Informs interpretation of D2-SA question 2S.3.

**Screening short-circuit** (V2's pattern, V1 shareable §0):
- `B2-SA ∈ {Y, PY}` → **Critical overall**, skip D1–D7 entirely. Reason: "Absence of any pre-specified benchmark is severe enough that the single-arm proportion is uninterpretable for causal inference."
- `B3 ∈ {Y, PY}` → **Critical overall**, skip D1–D7 entirely. Reason: "The method of measuring the outcome is inappropriate."
- Otherwise → proceed to per-domain assessment.

The preflight payload is stashed in `domain_results["preflight"]` (same key V2 uses, so the per-paper detail view can render it through the shared code path). `domain_results["aim_preflight"]` is set to `None` for single-arm runs to signal "no aim preflight was run".

### 19.3 Domain 1 single-arm (1S.1–1S.5) — Benchmark adequacy + prognostic-mix comparability

**1S.1.** Was the implied benchmark (historical control rate, pre-specified performance criterion, or null hypothesis with a quantitative decision rule) pre-specified before data collection?
Options: `Y / PY / PN / N` (no NI).
Elaboration: Single-arm trials have no internal comparator. They are interpreted against an implicit benchmark — usually a historical-control response rate, a regulatory performance criterion (e.g. ORR > 30% to support accelerated approval), or a null hypothesis with a pre-specified statistical decision rule (e.g. Simon's two-stage design). Answer Y/PY if a numeric benchmark + decision rule was clearly stated in the protocol / SAP / methods, BEFORE the data were collected. Answer N/PN if no benchmark is identifiable, or if the benchmark looks chosen post-hoc to match the observed result.

**1S.2.** Is the implied benchmark reasonable given current standard of care and the patient population being studied?
Options: `Y / PY / PN / N / NI`.
Elaboration: A pre-specified benchmark is only useful if it reflects a clinically meaningful threshold for this population. Answer Y/PY if the benchmark is consistent with contemporary published control-arm rates in comparable patients (similar disease stage, prior therapy, biomarker status). Answer N/PN if the benchmark is implausibly low (inflates apparent benefit) or implausibly high (forces a near-impossible bar). NI if no contemporary comparable estimate exists.

**1S.3.** Is the cohort's measured baseline prognostic profile (stage, prior lines, ECOG/performance status, biomarker status, key comorbidities) comparable to that of the benchmark population?
Options: `NA / Y / PY / PN / N / NI`.
Elaboration: The single-arm proportion is biased upward if the enrolled cohort is more prognostically favourable than the benchmark population (e.g. younger, less heavily pre-treated, biomarker-enriched). Answer Y/PY when measured baseline prognostic factors are comparable. **PN when most-but-not-all prognostic factors look comparable** (V1 collapse of V2's WN). **N when at least one important prognostic factor is materially more favourable in this cohort** (V1 collapse of V2's SN). NA only when no benchmark was identified at 1S.1.

**1S.4.** Did the authors address residual prognostic-mix differences quantitatively (sensitivity analyses, propensity-score adjustment to external controls, prognostic-score stratification, or similar)?
Options: `NA / Y / PY / PN / N / NI`.
Elaboration: Even when 1S.3 raises concerns, quantitative external-control adjustment can rescue interpretability. Examples include propensity-score weighting against an external real-world cohort, prognostic-score stratification, MAIC, or pre-specified sensitivity analyses showing the conclusion is robust to plausible prognostic differences. Answer Y/PY when such methods were used and reported. N/PN when not addressed. NA only when no benchmark was identified at 1S.1.

**1S.5.** Do negative / falsification controls, external-validity considerations, or other quantitative bias analyses suggest serious uncontrolled selection-prognostic bias?
Options: `Y / PY / PN / N` (no NI).
Elaboration: Analogous to V2's 1A.4 / 1B.5. Answer Y/PY if a falsification analysis (e.g. testing the intervention against an outcome it shouldn't affect) suggested residual bias, or if external-validity checks revealed serious cohort-vs-benchmark mismatch. Answer N if no falsification analysis was performed and no other consideration suggests substantial uncontrolled bias — this is the typical answer.

**Decision tree** ([backend/rob_tools/robins_i_v1.py:domain1_single_arm_judge](../../backend/rob_tools/robins_i_v1.py)):

```python
def domain1_single_arm_judge(signals: dict[str, str]) -> str:
    q1 = signals.get("1S.1", "NI")
    q2 = signals.get("1S.2", "NI")
    q3 = signals.get("1S.3", "NI")
    q4 = signals.get("1S.4", "NI")
    q5 = signals.get("1S.5", "NI")

    # 1S.5 dominates: falsification-control hit → Critical regardless
    if _yes(q5):
        return "Critical"

    # 1S.1 N/PN: no pre-specified benchmark
    if _no(q1):
        # 1S.4 N/PN: no quantitative adjustment either → Critical
        if _no(q4):
            return "Critical"
        return "Serious"

    if _no_info(q1):
        return "No information"

    # 1S.1 Y/PY: benchmark pre-specified
    if _yes(q1):
        # 1S.3 prognostic comparability
        if _yes(q3):
            # 1S.2 (benchmark reasonable) decides Low-SA vs Moderate
            if _yes(q2):
                return LOW_D1_SA  # "Low (except for concerns about uncontrolled benchmarking)"
            return "Moderate"
        # V1 collapse: PN → V2-WN-equivalent (Moderate floor)
        if q3 == "PN":
            return "Moderate"
        # V1 collapse: N → V2-SN-equivalent (substantial mismatch).
        # NI on 1S.3 — silent on prognostic comparability — treated the same.
        if q3 == "N" or _no_info(q3):
            # 1S.4 (quantitative adjustment) can rescue
            if _yes(q4):
                return "Moderate"
            return "Serious"

    return "Serious"
```

Returns one of: `LOW_D1_SA` (the labelled "Low" — "Low (except for concerns about uncontrolled benchmarking)"), `Moderate`, `Serious`, `Critical`, `No information`.

### 19.4 Domain 2 single-arm (2S.1–2S.3) — Intervention fidelity + intent-vs-received

**2S.1.** Was the intervention well-defined (dose, schedule, duration, dose-modifications protocol) at the start of follow-up?
Options: `Y / PY / PN / N / NI`.
Elaboration: In a single-arm trial there is no comparator misclassification, but the single intervention must be specified precisely enough that the reported result corresponds to a reproducible regimen. Answer Y/PY when dose, schedule, duration, and dose-modification rules (reductions, holds, criteria for discontinuation) are fully reported. Answer N/PN when the intervention is described only at high level (e.g. "standard chemotherapy").

**2S.2.** Were dose reductions, holds, and discontinuations recorded and reported?
Options: `Y / PY / PN / N / NI`.
Elaboration: Recording of treatment delivery is essential for interpreting the single-arm result. Answer Y/PY when reductions/holds/discontinuations are tabulated or otherwise reported. **PN when most exposure modifications were recorded but some detail is missing** (V1 collapse of V2's WN). **N when material exposure detail is missing such that the analyzed "intervention" is effectively undefined** (V1 collapse of V2's SN).

**2S.3.** Was the analyzed cohort defined by intended treatment (everyone enrolled, ITT-like) or by received treatment (only those completing ≥X cycles / responding to treatment)?
Options: `Y / PY / PN / N / NI`.
Elaboration: Defining the analyzed cohort by *received* treatment (per-protocol completers, "evaluable population") selects for patients who tolerated the intervention well enough to keep receiving it — a strong selection toward responders that inflates the single-arm proportion. **Answer Y only when the primary analysis is explicitly restricted to completers / responders / evaluable population** (V1 collapse of V2's SY — Critical-routing). **Answer PY when the analyzed cohort excludes some enrolled patients for treatment-related reasons but not dominantly** (V1 collapse of V2's WY — Serious-routing). Answer N/PN when all enrolled (or all who received any dose of intervention — modified ITT) are analyzed.

**Decision tree** ([backend/rob_tools/robins_i_v1.py:domain2_single_arm_judge](../../backend/rob_tools/robins_i_v1.py)):

```python
def domain2_single_arm_judge(signals: dict[str, str]) -> str:
    q1 = signals.get("2S.1", "NI")
    q2 = signals.get("2S.2", "NI")
    q3 = signals.get("2S.3", "NI")

    # 2S.3 dominates: cohort-definition selection bias
    if q3 == "Y":
        # V2-SY-equivalent — primary analysis explicitly restricted to
        # completers/responders → Critical
        return "Critical"
    if q3 == "PY" or _no_info(q3):
        # V2-WY-equivalent or unclear → Serious
        return "Serious"

    # q3 in (PN, N): cohort defined by intended treatment → low concern here
    if _yes(q1):
        # Well-defined intervention. 2S.2 (recording fidelity) decides.
        if _yes(q2):
            return "Low"
        if q2 == "PN":
            return "Moderate"
        if q2 == "N":
            return "Serious"
        # NI on 2S.2 — measurement-fidelity uncertain
        return "Moderate"

    # 2S.1 N/PN: intervention definition unclear
    if _no(q1):
        return "Serious"
    # NI on 2S.1
    return "No information"
```

Returns one of: `Low`, `Moderate`, `Serious`, `Critical`, `No information`.

### 19.5 Domains 3, 5, 6, 7 — reused from cohort

For single-arm runs, D3 (Classification of interventions), D5 (Missing data), D6 (Measurement of outcomes), and D7 (Selective reporting) reuse the cohort signal sets and judges unchanged. The signal questions translate meaningfully to single-arm:

- **D3** asks whether the *single* intervention was clearly defined and classified — still meaningful (a poorly-described intervention is still a problem; classification within the arm can still vary, e.g. if some patients received a slightly different protocol).
- **D5** asks about missing outcome data — same concerns in single-arm (loss to follow-up, missing-at-random vs not).
- **D6** asks about outcome measurement — same concerns (blinding, comparable assessment, systematic errors).
- **D7** asks about selective reporting — same concerns (multiple-outcome selection, multiple-analysis selection, subgroup selection).

The single-arm decision trees for these domains are identical to the cohort versions. The interpretation guidance is slightly adjusted in the per-domain prompt template (no comparator-arm framing), but the signal IDs (`3.1`, `3.2`, `3.3`, `5.1`–`5.5`, `6.1`–`6.4`, `7.1`–`7.3`) are unchanged so the same `enforce_cascade_v1()` rules apply (D5 has cascade; D3, D6, D7 do not).

### 19.6 Domain 4 — NA for single-arm

Set to `judgement: "NA"` in code with **no LLM call**. Mirrors the RoB 2 Cluster NA-cascade pattern and matches V2's design (V2 retired V1's D4 entirely; its concerns are folded into V2-SA's D2-SA question 2S.3 → V1-SA's 2S.3).

The result dict carries a `reason` field for downstream UIs:

```python
domain_results["4"] = {
    "id": 4,
    "name": "Bias due to deviations from intended interventions",
    "signals": {},
    "rationales": {},
    "judgement": "NA",
    "direction": "NA",
    "reason": (
        "Not applicable to single-arm trials — V2 retired this domain "
        "entirely; intent-vs-received cohort definition is assessed in "
        "Domain 2-SA (question 2S.3)."
    ),
}
```

The aggregator (`robins_i_v1_overall`) excludes `NA` judgements from the worst-domain calculation, so a single-arm paper with all active domains = Low still produces overall = Low.

### 19.7 V1 → V2 vocab-collapse rules (referenced from §17.3)

See §17.3 for the full table. Short version:

- V2's `WN` (weak no) collapses to V1's `PN` on signals 1S.3 and 2S.2.
- V2's `SN` (strong no) collapses to V1's `N` on signals 1S.3 and 2S.2.
- V2's `WY` (weak yes) collapses to V1's `PY` on signal 2S.3.
- V2's `SY` (strong yes) collapses to V1's `Y` on signal 2S.3.

For 2S.3 in particular, the V1 Y/PY distinction carries semantic weight that V1's other signals do not: a paper-aware reviewer must distinguish "primary analysis restricted to completers" (V1 Y) from "some treatment-related exclusions but not dominantly" (V1 PY). The per-signal elaboration in §19.4 instructs the LLM accordingly. Reviewers who want to override the algorithmic mapping can do so based on the prose rationale, same as for any other V1 narrative-table judgement (§0 conservative-tree note).

### 19.8 Reference implementation — SA additions to the §15 single-file module

Add the following to the §15 reference implementation. The cohort code stays unchanged.

```python
# ─────────────────────────────────────────────
# Single-arm constants
# ─────────────────────────────────────────────
SINGLE_ARM_STUDY_TYPES = frozenset({"Single-Arm Trial", "Dose-Escalation Study"})

LOW_D1_SA = "Low (except for concerns about uncontrolled benchmarking)"


# ─────────────────────────────────────────────
# Single-arm signal sets (port of V2-SA, collapsed to V1's 5-token vocab)
# ─────────────────────────────────────────────
DOMAIN1_SIGNALS_SA = [
    # 1S.1, 1S.2, 1S.3, 1S.4, 1S.5 — full text in §19.3 above
]

DOMAIN2_SIGNALS_SA = [
    # 2S.1, 2S.2, 2S.3 — full text in §19.4 above
]


# ─────────────────────────────────────────────
# Single-arm decision trees — code in §19.3 and §19.4 above
# ─────────────────────────────────────────────
# def domain1_single_arm_judge(signals): ...
# def domain2_single_arm_judge(signals): ...


# ─────────────────────────────────────────────
# Single-arm judge dispatch
# ─────────────────────────────────────────────
DOMAIN_JUDGES_SA = {
    1: domain1_single_arm_judge,
    2: domain2_single_arm_judge,
    3: domain3_judge,  # reused
    5: domain5_judge,  # reused
    6: domain6_judge,  # reused
    7: domain7_judge,  # reused
    # 4: not present — set to NA in run() with no LLM call
}


# ─────────────────────────────────────────────
# Benchmark preflight prompt (§19.2)
# ─────────────────────────────────────────────
def build_benchmark_preflight_prompt(study_type, primary_outcome, extracted_fields):
    relevant_keys = (
        "primary_endpoint_prespecified", "inclusion_exclusion_criteria",
        "comparator_historical_reference", "consecutive_enrolment",
        "outcome_definition", "outcome_ascertainment",
        "primary_outcome_measurement", "analysis_framework",
    )
    relevant = {k: extracted_fields[k] for k in relevant_keys if extracted_fields.get(k)}
    import json
    ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"
    return f\"\"\"You are performing the **Preliminary Considerations** screen of ROBINS-I V1 (adapted for single-arm / uncontrolled designs) on an uncontrolled clinical study.

Study type: {{study_type}}
Outcome being assessed: {{primary_outcome}}

Context (fields already extracted from the paper):
{{ctx_json}}

[... full prompt body — see §19.2 above, or backend/rob_tools/robins_i_v1.py:_build_benchmark_preflight_prompt ...]

Return JSON with exactly this shape:
{{{{
  "B1": "Y|PY|PN|N",
  "B1_rationale": "1-2 sentences (B1-SA)",
  "B2": "Y|PY|PN|N|NA",
  "B2_rationale": "1-2 sentences",
  "B3": "Y|PY|PN|N",
  "B3_rationale": "1-2 sentences",
  "C4": "No|Yes",
  "C4_rationale": "1-2 sentences"
}}}}\"\"\"


# ─────────────────────────────────────────────
# Single-arm run dispatch (extends the cohort run() in §15)
# ─────────────────────────────────────────────
def run_single_arm(pdf_bytes, extracted_fields, classification, primary_outcome,
                   llm_call_with_pdf):
    \"\"\"Single-arm path. Called by run() when study_type ∈ SINGLE_ARM_STUDY_TYPES.\"\"\"
    study_type = classification.get("study_type", "Single-Arm Trial")

    # Stage 1 — benchmark preflight
    preflight = run_benchmark_preflight(
        pdf_bytes, study_type, primary_outcome, extracted_fields,
        llm_call_with_pdf=llm_call_with_pdf,
    )
    domain_results = {"preflight": preflight, "aim_preflight": None}

    # Stage 2 — screening short-circuit
    if preflight["screening_decision"] == "critical":
        return domain_results, "Critical", "Unpredictable"

    # Stage 3 — 6 active domains; D4 set to NA in code
    active_domains = (
        # D1-SA with DOMAIN1_SIGNALS_SA
        # D2-SA with DOMAIN2_SIGNALS_SA
        # D3, D5, D6, D7 reused unchanged
    )
    for domain in active_domains:
        result = _assess_domain_sa(pdf_bytes, domain, ..., llm_call_with_pdf)
        domain_results[str(domain["id"])] = result

    # D4 = NA — no LLM call
    domain_results["4"] = {
        "id": 4, "name": "Bias due to deviations from intended interventions",
        "signals": {}, "rationales": {}, "judgement": "NA", "direction": "NA",
        "reason": "Not applicable to single-arm trials — V2 retired this "
                  "domain entirely; intent-vs-received is in D2-SA's 2S.3.",
    }

    judgements = [domain_results[str(d["id"])]["judgement"] for d in active_domains]
    overall = robins_i_v1_overall(judgements)  # excludes NA from aggregation
    return domain_results, overall, "NA"


def run(pdf_bytes, extracted_fields, classification, primary_outcome,
        llm_call_with_pdf, aim=None):
    study_type = classification.get("study_type", "Cohort Study")
    if study_type in SINGLE_ARM_STUDY_TYPES:
        return run_single_arm(pdf_bytes, extracted_fields, classification,
                              primary_outcome, llm_call_with_pdf)
    # ... existing cohort path (§15) ...
```

For the full production-quality module (with prompt builders, cascade enforcement bypass for SA, dev-view exposure, etc.), see [backend/rob_tools/robins_i_v1.py](../../backend/rob_tools/robins_i_v1.py).

### 19.9 Sanity test sketches

Same shape as §16 cohort tests. Drop in alongside:

```python
def test_v1_sa_d1_pre_specified_low():
    # Best case: benchmark pre-specified + reasonable + prognostic match
    assert domain1_single_arm_judge({
        "1S.1": "Y", "1S.2": "Y", "1S.3": "Y", "1S.4": "Y", "1S.5": "N",
    }) == LOW_D1_SA

def test_v1_sa_d1_no_benchmark_no_adjustment_critical():
    # No pre-specified benchmark AND no quantitative adjustment → Critical
    assert domain1_single_arm_judge({
        "1S.1": "N", "1S.2": "NI", "1S.3": "NI", "1S.4": "N", "1S.5": "N",
    }) == "Critical"

def test_v1_sa_d1_falsification_hit_dominates():
    # 1S.5 falsification hit → Critical regardless of other signals
    assert domain1_single_arm_judge({
        "1S.1": "Y", "1S.2": "Y", "1S.3": "Y", "1S.4": "Y", "1S.5": "Y",
    }) == "Critical"

def test_v1_sa_d2_completers_only_critical():
    # 2S.3 = Y (V2-SY-equivalent: restricted to completers) → Critical
    assert domain2_single_arm_judge({
        "2S.1": "Y", "2S.2": "Y", "2S.3": "Y",
    }) == "Critical"

def test_v1_sa_d2_partial_filter_serious():
    # 2S.3 = PY (V2-WY-equivalent: some treatment-related exclusions) → Serious
    assert domain2_single_arm_judge({
        "2S.1": "Y", "2S.2": "Y", "2S.3": "PY",
    }) == "Serious"

def test_v1_sa_d2_itt_well_defined_low():
    # 2S.3 = N (ITT-like), well-defined intervention, recording fidelity → Low
    assert domain2_single_arm_judge({
        "2S.1": "Y", "2S.2": "Y", "2S.3": "N",
    }) == "Low"

def test_v1_sa_overall_excludes_d4_na():
    # D4 = NA must not block "Low" overall judgement
    assert robins_i_v1_overall(
        [LOW_D1_SA, "Low", "Low", "NA", "Low", "Low", "Low"]
    ) == "Low"
```

