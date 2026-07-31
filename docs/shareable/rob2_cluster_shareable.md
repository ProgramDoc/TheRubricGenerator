# RoB 2 Cluster-Randomized Trials (RoB 2 CRT) — Sharable Methodology Reference

A self-contained reference for implementing an automated Cochrane RoB 2
cluster-randomized-trial assessment on any platform. Contains:

- Signaling questions (transcribed from the cribsheet) for all 6 domains
- Decision-tree logic as plain Python (no framework / database / HTTP dependencies)
- Cascade-enforcement logic — the conditional ("If X to Y") structure resolved in code
- Both Domain 2 variants — effect of *assignment* (ITT) and effect of *adhering* (per-protocol)
- LLM prompt templates (the exact strings sent to the model)
- Expected JSON output shape
- Overall aggregation algorithm
- A turnkey single-file reference implementation

**Source transcribed:**

- Revised Cochrane risk-of-bias tool for cluster-randomized trials (RoB 2 CRT) — SHORT VERSION (CRIBSHEET), version of 18 March 2021. Eldridge S, Campbell M, Campbell M, Drahota A, Giraudeau B, Higgins J, Reeves B, Siegfried N.
- For parallel-group reused items: 2019 RoB 2 cribsheet (Higgins, Savović, Page, Sterne et al.).

**Scope:** the cluster-randomized-trial extension to RoB 2, for **parallel** cluster-randomized trials. Stepped-wedge cluster trials are out of scope — they need an additional time-trend domain. The base 5-domain parallel-group flow does not apply unchanged: Domain 1 is split into 1a and 1b, Domain 1b is new, and Domains 2/3/4 add cluster-specific signaling questions.

---

**Assessment scope: one assessment per (study × outcome).** This instrument rates a *result*, not a paper. Several of its signalling questions — missing outcome data, measurement of the outcome, and selection of the reported result — are answered differently for different outcomes in the same study, so one trial can be *Low* for all-cause mortality and *High* for an unblinded symptom score. Run the whole instrument once per outcome you intend to report, passing that outcome as the assessed outcome, and store one judgement per (study × outcome). Reusing a single paper-level judgement across every outcome attaches a rating to outcomes it was never made about, and nothing in the output reveals that it happened. Only the instrument call repeats: classification and field extraction that feed the prompts are outcome-independent and run once per study.

## 1. Signal answer options

The LLM answers every signaling question on a 5-token scale:

```python
SIGNAL_OPTIONS = ("Y", "PY", "PN", "N", "NI")
# Y  = Yes
# PY = Probably yes
# PN = Probably no
# N  = No
# NI = No information
```

One exception: signaling question **3.2** offers no `NI` — its scale is `Y / PY / PN / N` (you either have evidence the result is unbiased, or you do not).

**`NA` is not a signal answer.** Many questions are conditional ("If Y/PY/NI to X…"). Rather than ask the LLM to decide whether a question applies — that is fully determined by the answers to *other* questions — the LLM answers every question on its own merits, and the **cascade enforcers** (§3) derive `NA` in code for any question whose precondition is not met. This keeps the conditional structure deterministic and auditable, and keeps the LLM's task simple.

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

RoB 2 CRT has **6 domains**, processed in the cribsheet's narrative order:

| Order | ID  | Name                                                                         |
| ----- | --- | ---------------------------------------------------------------------------- |
| 1     | 1a  | Bias arising from the randomization process                                  |
| 2     | 1b  | Bias arising from the timing of identification/recruitment of participants (NEW) |
| 3     | 2   | Bias due to deviations from the intended interventions                       |
| 4     | 3   | Bias due to missing outcome data                                             |
| 5     | 4   | Bias in measurement of the outcome                                           |
| 6     | 5   | Bias in selection of the reported result                                     |

Domain 2 has **two variants** selected by the review team's aim: *effect of assignment* (intention-to-treat) and *effect of adhering* (per-protocol). They have different signaling questions and different flowcharts.

> **Note — independent transcription.** The 2021 CRT flowcharts route several paths differently from the 2019 parallel-group tool (e.g. p.6 — a concealed-but-non-random sequence is *Some concerns* here; Domains 3 and 4 split signaling questions 3.1 and 4.3 into 3.1a/3.1b and 4.3a/4.3b). The trees below are transcribed directly from the CRT cribsheet — do not substitute the parallel-group RoB 2 trees. Only Domain 5 and the overall aggregation are genuinely identical to standard RoB 2.

The judges below run on **post-cascade** signals (§3): questions gated out by the cascade carry `"NA"`. Each tree short-circuits before reaching any gated-out question, so `_yes()`/`_no()` (which treat `NA` as neither) never affect a judgement.

### 2.1 Domain 1a — Bias arising from the randomization process

Signaling questions (all three always asked — no conditional gating):

- **1a.1** Was the allocation sequence random?

  *Elaboration:* Considerations are mostly the same as for individually-randomized trials. The unit of allocation is the cluster. Answer 'No' for non-random methods that might be seen in cluster-randomized trials, including those based on geography (e.g., clusters near the main research centre allocated to the intervention and those further away to the control), alternation, or any systematic or judgement-based method.

- **1a.2** Was the allocation sequence concealed until clusters were enrolled and assigned to interventions?

  *Elaboration:* As for individually-randomized trials, but applied to clusters. Answer 'Yes' if clusters were enrolled and their identities fixed before allocation was revealed. Answer 'No' if those enrolling clusters could foresee assignments.

- **1a.3** Did baseline differences between intervention groups suggest a problem with the randomization process?

  *Elaboration:* Differences compatible with chance do not lead to a risk of bias. Answer 'No' if observed imbalances are compatible with chance or are likely due to identification/recruitment bias (assessed in Domain 1b). Because most cluster-randomized trials randomize few clusters, substantial chance imbalances are more common than in individually-randomized trials. Answer 'Yes' if imbalances indicate a problem with the randomization process. Answer 'No information' when no useful baseline data are reported.

Decision tree:

```python
def domain1a_judge(signals: dict[str, str]) -> str:
    """Domain 1a (randomization process) — RoB 2 CRT cribsheet p.6."""
    q1 = signals.get("1a.1", "NI")
    q2 = signals.get("1a.2", "NI")
    q3 = signals.get("1a.3", "NI")

    if _no(q2):
        return "High"
    if q2 == "NI":
        return "High" if _yes(q3) else "Some concerns"
    # 1a.2 Y/PY (concealed)
    if _no(q1):
        return "Some concerns"
    # 1a.1 Y/PY/NI
    return "Some concerns" if _yes(q3) else "Low"
```

### 2.2 Domain 1b — Bias arising from the timing of identification or recruitment of participants (NEW)

The cluster-specific domain. It captures *identification/recruitment bias* — the risk that, because individual participants were identified or recruited *after* clusters were randomized, knowledge of cluster allocation differentially shaped who entered each arm.

Signaling questions (1b.2 is conditional — see §3):

- **1b.1** Were all the individual participants identified and recruited (if appropriate) before randomization of clusters?

  *Elaboration:* Answer 'Yes' if (1) all participants were identified and recruited before the clusters were randomized, or (2) individual participants were not recruited at all but all were identified before randomization — in these cases identification/recruitment bias is not possible. Answer 'No' if (1) some or all participants were identified or recruited after randomization, or (2) there are any clusters in which no participants were recruited (empty clusters).

- **1b.2** *(conditional — see §3)* Is it likely that selection of individual participants was affected by knowledge of the intervention assigned to the cluster?

  *Elaboration:* Answer 'Yes' if those recruiting or identifying participants were aware of cluster allocation and this is likely, consciously or subconsciously, to have affected recruitment differentially between the intervention groups. Answer 'No' if all relevant parties — those identifying actual or potential participants, those recruiting, and potential participants — were unaware of cluster allocation at recruitment.

- **1b.3** Were there baseline imbalances that suggest differential identification or recruitment of individual participants between intervention groups?

  *Elaboration:* As for signalling question 1a.3, imbalances compatible with chance should not be interpreted as suggesting differential identification or recruitment. They can be in the numbers of participants recruited into each group, or in the characteristics of those individuals.

Decision tree:

```python
def domain1b_judge(signals: dict[str, str]) -> str:
    """Domain 1b (timing of identification/recruitment) — cribsheet p.9."""
    q1 = signals.get("1b.1", "NI")
    q2 = signals.get("1b.2", "NI")
    q3 = signals.get("1b.3", "NI")

    if _yes(q1):
        # All participants identified/recruited before randomization →
        # identification/recruitment bias is not possible.
        return "Low"
    # 1b.1 N/PN/NI → 1b.2
    if _yes(q2):
        return "High"
    if _no(q2):
        return "Some concerns" if _yes(q3) else "Low"
    # 1b.2 NI → 1b.3
    return "High" if _yes(q3) else "Some concerns"
```

### 2.3 Domain 2 (assignment variant) — Bias due to deviations from intended interventions (effect of assignment to intervention)

Use this variant when the review team's aim is the **intention-to-treat** effect. Eight signaling questions; two-part flowchart. 2.1b/2.3/2.4/2.5/2.7 are conditional (see §3).

- **2.1a** Were participants aware that they were in a trial?

  *Elaboration:* In cluster-randomized trials participants may know they are receiving an intervention — or even that they are in a study — without knowing they are in a *trial*, so they may not know another intervention is being compared. This makes it impossible for them to cause deviations that arise because of the trial context. Answer 'No' if participants are not aware they are in a study, or are aware they are in a study but not that they are in a trial.

- **2.1b** *(conditional)* Were participants aware of their assigned intervention during the trial?

  *Elaboration:* Answer 'Yes' if participants were aware of any part of the assigned intervention during the trial; consider all parts of the assigned intervention.

- **2.2** Were carers and people delivering the interventions aware of participants' assigned intervention during the trial?

  *Elaboration:* If those caring for participants or delivering the interventions are aware of the assigned intervention, then implementation, or administration of non-protocol interventions, may differ between groups. Blinding carers and trial personnel is rare in cluster-randomized trials.

- **2.3** *(conditional)* Were there deviations from the intended intervention that arose because of the trial context?

  *Elaboration:* The guidance mostly applies as for individually-randomized trials. Such deviations are rarely reported in cluster-randomized trials and may in fact occur rarely. 'No information' is appropriate in many cases, but use 'Probably yes' if it seems likely such deviations occurred.

- **2.4** *(conditional)* Were these deviations likely to have affected the outcome?

  *Elaboration:* As for individually-randomized trials — deviations impact the effect estimate only if they affect the outcome.

- **2.5** *(conditional)* Were these deviations from intended intervention balanced between groups?

  *Elaboration:* As for individually-randomized trials — unbalanced trial-context deviations bias the effect estimate more than balanced ones.

- **2.6** Was an appropriate analysis used to estimate the effect of assignment to intervention?

  *Elaboration:* Answer 'Yes' if all clusters and individuals were analysed according to the groups to which they were assigned. Answer 'No' if participants were analysed by intervention received rather than assigned, if analyses exclude participants or whole clusters not receiving their assigned intervention, or if a stepped-wedge trial ignores the time trend.

- **2.7** *(conditional)* Was there potential for a substantial impact (on the result) of the failure to analyse participants in the group to which they were randomized?

  *Elaboration:* As for individually-randomized trials, but watch for entire clusters analysed in the wrong intervention group as well as individual participants. There is no precise threshold.

Decision tree (Part 1 covers 2.1a–2.5; Part 2 covers 2.6–2.7; combined per the cribsheet):

```python
def domain2_assignment_judge(signals: dict[str, str]) -> str:
    """Domain 2 — effect of ASSIGNMENT to intervention (ITT) — cribsheet p.12.

      Low    iff Part 1 = Low AND Part 2 = Low
      High   iff either part = High
      Some   otherwise
    """
    q21a = signals.get("2.1a", "NI")
    q21b = signals.get("2.1b", "NI")
    q22 = signals.get("2.2", "NI")
    q23 = signals.get("2.3", "NI")
    q24 = signals.get("2.4", "NI")
    q25 = signals.get("2.5", "NI")
    q26 = signals.get("2.6", "NI")
    q27 = signals.get("2.7", "NI")

    # ── Part 1 — awareness gate, then 2.3 → 2.4 → 2.5 ──
    if _no(q21a):
        aware = not _no(q22)
    else:
        aware = not (_no(q21b) and _no(q22))

    if not aware:
        part1 = "Low"
    elif _no(q23):
        part1 = "Low"
    elif q23 == "NI":
        part1 = "Some concerns"
    elif _no(q24):
        part1 = "Low"
    else:
        part1 = "Some concerns" if _yes(q25) else "High"

    # ── Part 2 — appropriate ITT analysis ──
    if _yes(q26):
        part2 = "Low"
    elif _no(q27):
        part2 = "Some concerns"
    else:
        part2 = "High"

    if part1 == "High" or part2 == "High":
        return "High"
    if part1 == "Low" and part2 == "Low":
        return "Low"
    return "Some concerns"
```

### 2.4 Domain 2 (adhering variant) — Bias due to deviations from intended interventions (effect of adhering to intervention)

Use this variant when the review team's aim is the **per-protocol** effect. Six signaling questions. 2.3 and 2.6 are conditional (see §3).

- **2.1** Were participants aware of their assigned intervention during the trial?

  *Elaboration:* If participants are aware of their assigned intervention, health-related behaviours are more likely to differ between groups. If participants experienced side effects or toxicities specific to one intervention, answer 'Yes'/'Probably yes'.

- **2.2** Were carers and people delivering the interventions aware of participants' assigned intervention during the trial?

  *Elaboration:* If carers or people delivering the interventions are aware of the assigned intervention, its implementation or the administration of non-protocol interventions may differ between groups. If randomized allocation was not concealed, they were likely aware.

- **2.3** *(conditional)* Were important non-protocol interventions balanced across intervention groups?

  *Elaboration:* Non-protocol interventions are co-interventions received in addition to the protocol-defined intervention. Answer 'Yes' if important non-protocol interventions were balanced across groups. **If there were no important non-protocol interventions to consider, answer 'Yes'** (there is no imbalance to be concerned about).

- **2.4** Were there failures in implementing the intervention that could have affected the outcome?

  *Elaboration:* Answer 'Yes' if the intervention was not implemented as intended in a way that could have affected the outcome. Implementation failures are especially relevant for complex interventions delivered to whole clusters or cluster staff. **If implementation fidelity is not a relevant concern for this intervention, answer 'No'.**

- **2.5** Was there non-adherence to the assigned intervention regimen that could have affected participants' outcomes?

  *Elaboration:* Mostly as for individually-randomized trials. Consider non-adherence and co-interventions at both the individual and the cluster level. **If non-adherence is not a relevant concern, answer 'No'.**

- **2.6** *(conditional)* Was an appropriate analysis used to estimate the effect of adhering to the intervention?

  *Elaboration:* Mostly as for individually-randomized trials — an appropriate analysis adjusts for prognostic factors and the timing of any non-protocol intervention, non-adherence, or implementation failure.

> The cribsheet's p.14 flowchart routes the answer `NA` ("not applicable") alongside the no-concern branches for 2.3/2.4/2.5. Because the LLM answers on the 5-token scale only — answering `Y` for 2.3 / `N` for 2.4-2.5 when a failure mode is not a relevant concern — those `NA` branches fold into `Y` and `N` here.

Decision tree:

```python
def domain2_adhering_judge(signals: dict[str, str]) -> str:
    """Domain 2 — effect of ADHERING to intervention (per-protocol) — p.14."""
    q21 = signals.get("2.1", "NI")
    q22 = signals.get("2.2", "NI")
    q23 = signals.get("2.3", "NI")
    q24 = signals.get("2.4", "NI")
    q25 = signals.get("2.5", "NI")
    q26 = signals.get("2.6", "NI")

    def _node_2425() -> str:
        # Both 2.4 and 2.5 N/PN → Low; otherwise consult 2.6.
        if _no(q24) and _no(q25):
            return "Low"
        return "Some concerns" if _yes(q26) else "High"

    if _no(q21) and _no(q22):
        return _node_2425()
    if _yes(q23):
        return _node_2425()
    # 2.3 N/PN/NI → 2.6
    return "Some concerns" if _yes(q26) else "High"
```

### 2.5 Domain 3 — Bias due to missing outcome data

Signaling question 3.1 is split into 3.1a (cluster-level) and 3.1b (participant-level). 3.2/3.3/3.4 are conditional (see §3).

- **3.1a** Were data for this outcome available for all clusters that recruited participants?

  *Elaboration:* Because a cluster-randomized trial usually has few clusters, there is potential for bias even if only one cluster has no analysable participants. Answer 'No' if any recruiting cluster contributed no outcome data.

- **3.1b** Were data for this outcome available for all, or nearly all, participants within clusters?

  *Elaboration:* Broadly as for individually-randomized trials: 'nearly all' means missingness small enough that it could have made no important difference.

- **3.2** *(conditional; Y/PY/PN/N — no NI)* Is there evidence that the result was not biased by missing outcome data?

  *Elaboration:* As for individually-randomized trials — evidence can come from an analysis method that corrects for bias, or from sensitivity analyses showing robustness. A single imputation method does not by itself establish lack of bias. This question offers no 'No information' option.

- **3.3** *(conditional)* Could missingness in the outcome depend on its true value?

  *Elaboration:* As for individually-randomized trials — if loss to follow-up or withdrawal might be related to participants' health status, missingness could depend on the true outcome value.

- **3.4** *(conditional)* Is it likely that missingness in the outcome depended on its true value?

  *Elaboration:* As for individually-randomized trials — distinguishes 'could depend' (Some concerns) from 'likely did depend' (High).

Decision tree:

```python
def domain3_judge(signals: dict[str, str]) -> str:
    """Domain 3 (missing outcome data) — cribsheet p.16."""
    q31a = signals.get("3.1a", "NI")
    q31b = signals.get("3.1b", "NI")
    q32 = signals.get("3.2", "N")     # 3.2 has no NI — default to N
    q33 = signals.get("3.3", "NI")
    q34 = signals.get("3.4", "NI")

    if _yes(q31a) and _yes(q31b):
        return "Low"
    if _yes(q32):
        return "Low"
    if _no(q33):
        return "Low"
    if _no(q34):
        return "Some concerns"
    return "High"
```

### 2.6 Domain 4 — Bias in measurement of the outcome

Signaling question 4.3 is split into 4.3a (assessors aware a trial is taking place) and 4.3b (assessors aware of the intervention received). 4.3a/4.3b/4.4/4.5 are conditional (see §3).

- **4.1** Was the method of measuring the outcome inappropriate?

  *Elaboration:* As for individually-randomized trials. Usually 'No' for pre-specified outcomes.

- **4.2** Could measurement or ascertainment of the outcome have differed between intervention groups?

  *Elaboration:* As for individually-randomized trials.

- **4.3a** *(conditional)* Were outcome assessors aware that a trial was taking place?

  *Elaboration:* Applies to cluster-randomized trials in which participants report their own outcomes. If they are not aware they are in a trial, their self-assessment cannot be affected by assignment.

- **4.3b** *(conditional)* Were outcome assessors aware of the intervention received by study participants?

  *Elaboration:* Answer 'No' if outcome assessors were blinded to intervention status. For participant-reported outcomes the outcome assessor IS the study participant.

- **4.4** *(conditional)* Could assessment of the outcome have been influenced by knowledge of intervention received?

  *Elaboration:* As for individually-randomized trials.

- **4.5** *(conditional)* Is it likely that assessment of the outcome was influenced by knowledge of intervention received?

  *Elaboration:* As for individually-randomized trials.

Decision tree:

```python
def domain4_judge(signals: dict[str, str]) -> str:
    """Domain 4 (measurement of the outcome) — cribsheet p.19."""
    q41 = signals.get("4.1", "NI")
    q42 = signals.get("4.2", "NI")
    q43a = signals.get("4.3a", "NI")
    q43b = signals.get("4.3b", "NI")
    q44 = signals.get("4.4", "NI")
    q45 = signals.get("4.5", "NI")

    if _yes(q41):
        return "High"
    if _yes(q42):
        return "High"
    # 4.2 N/PN keeps Low reachable; 4.2 NI floors the branch at Some concerns.
    floor = "Low" if _no(q42) else "Some concerns"
    if _no(q43a):
        return floor
    if _no(q43b):
        return floor
    if _no(q44):
        return floor
    if _no(q45):
        return "Some concerns"
    return "High"
```

### 2.7 Domain 5 — Bias in selection of the reported result

Three signaling questions, none conditional; identical to standard parallel-group RoB 2 (cribsheet p.21 — "as for individually-randomized trials").

- **5.1** Were the data that produced this result analysed in accordance with a pre-specified analysis plan that was finalized before unblinded outcome data were available for analysis?

- **5.2** Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible outcome measurements within the outcome domain?

- **5.3** Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible analyses of the data?

Decision tree:

```python
def domain5_judge(signals: dict[str, str]) -> str:
    """Domain 5 (selection of the reported result) — cribsheet p.21."""
    q51 = signals.get("5.1", "NI")
    q52 = signals.get("5.2", "NI")
    q53 = signals.get("5.3", "NI")

    if _yes(q52) or _yes(q53):
        return "High"
    if _no(q52) and _no(q53):
        return "Low" if _yes(q51) else "Some concerns"
    # At least one NI, none Y/PY
    return "Some concerns"
```

---

## 3. Cascade enforcement — Python-derived `NA`

Several signaling questions are conditional ("If X to Y…"). The LLM answers **every** question on the 5-token scale; these functions then run — *after* the LLM responds, *before* the decision tree — and overwrite any gated-out question's answer with `"NA"`. Domains 1a and 5 have no conditional questions and need no enforcer.

```python
def enforce_cascade_1b(signals):
    """D1b — 1b.2 is asked only if 1b.1 is N/PN/NI."""
    out = dict(signals)
    if out.get("1b.1", "NI") in ("Y", "PY"):
        out["1b.2"] = "NA"
    return out


def enforce_cascade_2_assignment(signals):
    """D2 (assignment): 2.1b iff 2.1a Y/PY/NI; 2.3 iff 2.1b or 2.2 Y/PY/NI;
    2.4 iff 2.3 Y/PY; 2.5 iff 2.4 Y/PY/NI; 2.7 iff 2.6 N/PN/NI."""
    out = dict(signals)
    if out.get("2.1a", "NI") in ("N", "PN"):
        out["2.1b"] = "NA"
    aware = (out.get("2.1b", "NA") in ("Y", "PY", "NI")
             or out.get("2.2", "NI") in ("Y", "PY", "NI"))
    if not aware:
        out["2.3"] = "NA"
    if out.get("2.3", "NA") not in ("Y", "PY"):
        out["2.4"] = "NA"
    if out.get("2.4", "NA") not in ("Y", "PY", "NI"):
        out["2.5"] = "NA"
    if out.get("2.6", "NI") in ("Y", "PY"):
        out["2.7"] = "NA"
    return out


def enforce_cascade_2_adhering(signals):
    """D2 (adhering): 2.3 iff 2.1 or 2.2 Y/PY/NI; 2.6 iff 2.3 N/PN/NI or
    2.4/2.5 Y/PY/NI. 2.4 and 2.5 are always asked."""
    out = dict(signals)
    aware = (out.get("2.1", "NI") in ("Y", "PY", "NI")
             or out.get("2.2", "NI") in ("Y", "PY", "NI"))
    if not aware:
        out["2.3"] = "NA"
    need_26 = (out.get("2.3", "NA") in ("N", "PN", "NI")
               or out.get("2.4", "NI") in ("Y", "PY", "NI")
               or out.get("2.5", "NI") in ("Y", "PY", "NI"))
    if not need_26:
        out["2.6"] = "NA"
    return out


def enforce_cascade_3(signals):
    """D3 — 3.2 iff 3.1a or 3.1b N/PN/NI; 3.3 iff 3.2 N/PN; 3.4 iff 3.3 Y/PY/NI."""
    out = dict(signals)
    missing = (out.get("3.1a", "NI") in ("N", "PN", "NI")
               or out.get("3.1b", "NI") in ("N", "PN", "NI"))
    if not missing:
        out["3.2"] = "NA"
    if out.get("3.2", "NA") not in ("N", "PN"):
        out["3.3"] = "NA"
    if out.get("3.3", "NA") not in ("Y", "PY", "NI"):
        out["3.4"] = "NA"
    return out


def enforce_cascade_4(signals):
    """D4 — 4.3a iff 4.1 and 4.2 both N/PN/NI; 4.3b iff 4.3a Y/PY/NI;
    4.4 iff 4.3b Y/PY/NI; 4.5 iff 4.4 Y/PY/NI."""
    out = dict(signals)
    if out.get("4.1", "NI") in ("Y", "PY") or out.get("4.2", "NI") in ("Y", "PY"):
        for sid in ("4.3a", "4.3b", "4.4", "4.5"):
            out[sid] = "NA"
        return out
    if out.get("4.3a", "NI") not in ("Y", "PY", "NI"):
        out["4.3b"] = "NA"
    if out.get("4.3b", "NA") not in ("Y", "PY", "NI"):
        out["4.4"] = "NA"
    if out.get("4.4", "NA") not in ("Y", "PY", "NI"):
        out["4.5"] = "NA"
    return out
```

Each enforcer re-encodes the same path the judge walks, so a question is set `NA` exactly when the judge would never consult it. The judge then runs on the post-cascade signals.

---

## 4. Overall RoB aggregation

Same as parallel-group RoB 2 (cribsheet p.22), applied to all six domains:

```python
def overall(domain_judgements: list[str]) -> str:
    """Low iff all domains Low. High iff any domain High OR >= 2 Some concerns.
    Some concerns otherwise."""
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

## 5. LLM prompt templates

### 5.1 System prompt (sent on every per-domain call)

```text
You are an evidence-synthesis methodologist assessing risk of bias in a
**cluster-randomized** trial using the Cochrane RoB 2 tool (cluster-randomized
trial extension, RoB 2 CRT). Read the PDF carefully. Answer every signaling
question on the Y/PY/PN/N/NI scale — Y (yes), PY (probably yes), PN (probably
no), N (no), NI (no information) — based on what the paper reports about that
specific question. Do NOT decide whether a question is 'not applicable': the
cribsheet's conditional ('If X to Y') structure is resolved in code after you
answer. Just answer each question independently on its own merits. Provide a
1-2 sentence rationale for each answer, quoting the paper where possible.
Return ONLY a valid JSON object — no preamble, no markdown fences.
```

### 5.2 Per-domain user prompt template

Variables: `{id}` (domain id), `{name}`, `{study_type}`, `{assessed_outcome}`, `{ctx_json}` (pre-extracted fields), `{questions_block}`, `{shape}` (§6), `{override_note}` (Domain 1a only, when the assessed outcome is a reviewer override).

```text
Assess **Domain {id} — {name}** for the **cluster-randomized** trial described in the attached PDF.

Study type: {study_type}
Outcome being assessed: {assessed_outcome}{override_note}

Context (fields already extracted from the paper):
{ctx_json}

Signaling questions:
{questions_block}

Return a JSON object with exactly this shape:
{shape}

Answer each question using only the response options listed for it. Answer N (or PN) when the paper gives enough information to rule out the problem, and NI only when the paper is silent. Answer every question on its own merits — do not skip a question or mark it not-applicable; the tool resolves the cribsheet's conditional structure in code. Rationales must be short (1-2 sentences) and quote the paper verbatim where possible.
```

The optional Domain-1a override note (appended only when the reviewer has overridden the auto-picked outcome):

```text


Note: this assessment is scoped to one specific outcome, selected by the
reviewer. Domain 1a signaling
questions concern the randomization process for the trial as a whole, not
the specific outcome — answer accordingly.
```

The `{questions_block}` is built by joining, for each signaling question:

```text

**{id}. {question_text}**
Elaboration: {elaboration}
Response options: {options}.
```

`{options}` is the slash-joined option list for that question — `Y/PY/PN/N/NI` for almost every question, and `Y/PY/PN/N` for signaling question 3.2.

---

## 6. Expected JSON output shape

The model returns JSON with two keys per signaling question (`{sid}` and `{sid}_rationale`) plus one direction-of-bias key. Example for Domain 1b:

```json
{
  "1b.1": "Y|PY|PN|N|NI",
  "1b.1_rationale": "1-2 sentences quoting the paper",
  "1b.2": "Y|PY|PN|N|NI",
  "1b.2_rationale": "1-2 sentences quoting the paper",
  "1b.3": "Y|PY|PN|N|NI",
  "1b.3_rationale": "1-2 sentences quoting the paper",
  "direction_of_bias": "NA|Favours experimental|Favours comparator|Towards null|Away from null|Unpredictable"
}
```

The model answers **every** question (it does not emit `NA`). After parsing, cascade enforcement (§3) overwrites gated-out questions with `"NA"`, the decision tree runs, and the domain result is enriched:

```json
{
  "id": "1b",
  "name": "Bias arising from the timing of identification or recruitment of participants",
  "signals":   {"1b.1": "Y", "1b.2": "NA", "1b.3": "Y"},
  "rationales":{"1b.1": "...", "1b.2": "...", "1b.3": "..."},
  "judgement": "Low",
  "direction": "NA"
}
```

(Here `1b.2` is `"NA"` because the cascade gated it — `1b.1` was `Y`.) The overall trial-level result:

```json
{
  "domains": {
    "1a": { ... }, "1b": { ... }, "2": { ... },
    "3": { ... }, "4": { ... }, "5": { ... }
  },
  "aim": "assignment",
  "overall_judgement": "Low|Some concerns|High",
  "overall_direction": "NA|Favours experimental|..."
}
```

`aim` records which Domain 2 variant was used. `overall_direction` is the most common non-`NA` direction across the six domains; ties (or all `NA`) → `Unpredictable` / `NA`.

---

## 7. Sample data — what gets passed to each domain prompt

Each per-domain prompt receives the relevant subset of pre-extracted study fields. Useful relevance hints:

| Domain | Useful pre-extracted fields                                                                                |
| ------ | ---------------------------------------------------------------------------------------------------------- |
| 1a     | `cluster_unit`, `n_clusters`, `icc_reported`, `allocation_concealment`, `baseline_balance`                 |
| 1b     | `recruitment_after_randomization`, `contamination_risk`, `n_clusters`, `cluster_unit`                      |
| 2      | `contamination_risk`, `clustering_in_analysis`, `blinding_participants`, `blinding_personnel`, `protocol_deviations`, `analysis_framework` |
| 3      | `clustering_in_analysis`, `n_clusters`, `attrition_rate`, `missing_data_handling`                          |
| 4      | `blinding_outcome_assessors`, `outcome_measurement_method`, `clustering_in_analysis`                       |
| 5      | `protocol_available`, `outcomes_match_protocol`                                                            |

If a field is absent or empty, the prompt omits it from the context block.

---

## 8. Reference implementation as a single Python file

A turnkey reference: the complete cluster assessor logic in one file (no platform dependencies). It assumes you provide your own `call_llm(system_prompt, user_prompt, pdf_bytes) -> dict` function.

```python
"""rob2_cluster_assessor.py — reference implementation.

Public API:
    assess_cluster_trial(pdf_bytes, study_type, assessed_outcome,
                         extracted_fields, call_llm,
                         outcome_is_override=False, aim="assignment") -> dict

`call_llm(system_prompt, user_prompt, pdf_bytes) -> dict` must return the
parsed JSON object the model produced.

`aim` selects the Domain 2 variant: "assignment" (intention-to-treat, the
default) or "adhering" (per-protocol).
"""

import json
from collections import Counter

SIGNAL_OPTIONS = ("Y", "PY", "PN", "N", "NI")
_BASE = ["Y", "PY", "PN", "N", "NI"]
_NO_NI = ["Y", "PY", "PN", "N"]            # signaling question 3.2 only


def _yes(a):  return a in ("Y", "PY")
def _no(a):   return a in ("N", "PN")


# ── Decision trees ─────────────────────────────────────────────

def domain1a_judge(s):
    q1, q2, q3 = s.get("1a.1", "NI"), s.get("1a.2", "NI"), s.get("1a.3", "NI")
    if _no(q2): return "High"
    if q2 == "NI":
        return "High" if _yes(q3) else "Some concerns"
    if _no(q1): return "Some concerns"
    return "Some concerns" if _yes(q3) else "Low"


def domain1b_judge(s):
    q1, q2, q3 = s.get("1b.1", "NI"), s.get("1b.2", "NI"), s.get("1b.3", "NI")
    if _yes(q1): return "Low"
    if _yes(q2): return "High"
    if _no(q2):
        return "Some concerns" if _yes(q3) else "Low"
    return "High" if _yes(q3) else "Some concerns"


def domain2_assignment_judge(s):
    q21a, q21b, q22 = s.get("2.1a", "NI"), s.get("2.1b", "NI"), s.get("2.2", "NI")
    q23, q24, q25 = s.get("2.3", "NI"), s.get("2.4", "NI"), s.get("2.5", "NI")
    q26, q27 = s.get("2.6", "NI"), s.get("2.7", "NI")
    if _no(q21a):
        aware = not _no(q22)
    else:
        aware = not (_no(q21b) and _no(q22))
    if not aware:
        part1 = "Low"
    elif _no(q23):
        part1 = "Low"
    elif q23 == "NI":
        part1 = "Some concerns"
    elif _no(q24):
        part1 = "Low"
    else:
        part1 = "Some concerns" if _yes(q25) else "High"
    if _yes(q26):
        part2 = "Low"
    elif _no(q27):
        part2 = "Some concerns"
    else:
        part2 = "High"
    if part1 == "High" or part2 == "High": return "High"
    if part1 == "Low" and part2 == "Low":  return "Low"
    return "Some concerns"


def domain2_adhering_judge(s):
    q21, q22, q23 = s.get("2.1", "NI"), s.get("2.2", "NI"), s.get("2.3", "NI")
    q24, q25, q26 = s.get("2.4", "NI"), s.get("2.5", "NI"), s.get("2.6", "NI")

    def _node_2425():
        if _no(q24) and _no(q25): return "Low"
        return "Some concerns" if _yes(q26) else "High"

    if _no(q21) and _no(q22):
        return _node_2425()
    if _yes(q23):
        return _node_2425()
    return "Some concerns" if _yes(q26) else "High"


def domain3_judge(s):
    q31a, q31b = s.get("3.1a", "NI"), s.get("3.1b", "NI")
    q32, q33, q34 = s.get("3.2", "N"), s.get("3.3", "NI"), s.get("3.4", "NI")
    if _yes(q31a) and _yes(q31b): return "Low"
    if _yes(q32): return "Low"
    if _no(q33):  return "Low"
    if _no(q34):  return "Some concerns"
    return "High"


def domain4_judge(s):
    q41, q42 = s.get("4.1", "NI"), s.get("4.2", "NI")
    q43a, q43b = s.get("4.3a", "NI"), s.get("4.3b", "NI")
    q44, q45 = s.get("4.4", "NI"), s.get("4.5", "NI")
    if _yes(q41): return "High"
    if _yes(q42): return "High"
    floor = "Low" if _no(q42) else "Some concerns"
    if _no(q43a): return floor
    if _no(q43b): return floor
    if _no(q44):  return floor
    if _no(q45):  return "Some concerns"
    return "High"


def domain5_judge(s):
    q51, q52, q53 = s.get("5.1", "NI"), s.get("5.2", "NI"), s.get("5.3", "NI")
    if _yes(q52) or _yes(q53): return "High"
    if _no(q52) and _no(q53):
        return "Low" if _yes(q51) else "Some concerns"
    return "Some concerns"


def overall(judgements):
    if any(j == "High" for j in judgements): return "High"
    some = sum(1 for j in judgements if j == "Some concerns")
    if some >= 2: return "High"
    if some >= 1: return "Some concerns"
    return "Low"


# ── Cascade enforcement (Python-derived NA) ────────────────────

def enforce_cascade_1b(s):
    out = dict(s)
    if out.get("1b.1", "NI") in ("Y", "PY"):
        out["1b.2"] = "NA"
    return out


def enforce_cascade_2_assignment(s):
    out = dict(s)
    if out.get("2.1a", "NI") in ("N", "PN"):
        out["2.1b"] = "NA"
    aware = (out.get("2.1b", "NA") in ("Y", "PY", "NI")
             or out.get("2.2", "NI") in ("Y", "PY", "NI"))
    if not aware:
        out["2.3"] = "NA"
    if out.get("2.3", "NA") not in ("Y", "PY"):
        out["2.4"] = "NA"
    if out.get("2.4", "NA") not in ("Y", "PY", "NI"):
        out["2.5"] = "NA"
    if out.get("2.6", "NI") in ("Y", "PY"):
        out["2.7"] = "NA"
    return out


def enforce_cascade_2_adhering(s):
    out = dict(s)
    aware = (out.get("2.1", "NI") in ("Y", "PY", "NI")
             or out.get("2.2", "NI") in ("Y", "PY", "NI"))
    if not aware:
        out["2.3"] = "NA"
    need_26 = (out.get("2.3", "NA") in ("N", "PN", "NI")
               or out.get("2.4", "NI") in ("Y", "PY", "NI")
               or out.get("2.5", "NI") in ("Y", "PY", "NI"))
    if not need_26:
        out["2.6"] = "NA"
    return out


def enforce_cascade_3(s):
    out = dict(s)
    missing = (out.get("3.1a", "NI") in ("N", "PN", "NI")
               or out.get("3.1b", "NI") in ("N", "PN", "NI"))
    if not missing:
        out["3.2"] = "NA"
    if out.get("3.2", "NA") not in ("N", "PN"):
        out["3.3"] = "NA"
    if out.get("3.3", "NA") not in ("Y", "PY", "NI"):
        out["3.4"] = "NA"
    return out


def enforce_cascade_4(s):
    out = dict(s)
    if out.get("4.1", "NI") in ("Y", "PY") or out.get("4.2", "NI") in ("Y", "PY"):
        for sid in ("4.3a", "4.3b", "4.4", "4.5"):
            out[sid] = "NA"
        return out
    if out.get("4.3a", "NI") not in ("Y", "PY", "NI"):
        out["4.3b"] = "NA"
    if out.get("4.3b", "NA") not in ("Y", "PY", "NI"):
        out["4.4"] = "NA"
    if out.get("4.4", "NA") not in ("Y", "PY", "NI"):
        out["4.5"] = "NA"
    return out


def enforce_cascade(domain_id, signals, aim="assignment"):
    if domain_id == "1b": return enforce_cascade_1b(signals)
    if domain_id == 2:
        return (enforce_cascade_2_adhering(signals) if aim == "adhering"
                else enforce_cascade_2_assignment(signals))
    if domain_id == 3: return enforce_cascade_3(signals)
    if domain_id == 4: return enforce_cascade_4(signals)
    return dict(signals)  # 1a, 5 — no conditional questions


# ── Domain definitions ─────────────────────────────────────────
# Each domain: id, name, judge, list of {id, text, elaboration, options}.
# (Question text + elaborations condensed here; full text is in §2 above.)

_DOMAIN_1A = {"id": "1a", "judge": domain1a_judge,
    "relevant_fields": ["cluster_unit", "n_clusters", "icc_reported",
                        "allocation_concealment", "baseline_balance"],
    "name": "Bias arising from the randomization process", "signals": [
    {"id": "1a.1", "text": "Was the allocation sequence random?", "options": _BASE, "elaboration": "<see §2.1>"},
    {"id": "1a.2", "text": "Was the allocation sequence concealed until clusters were enrolled and assigned to interventions?", "options": _BASE, "elaboration": "<see §2.1>"},
    {"id": "1a.3", "text": "Did baseline differences between intervention groups suggest a problem with the randomization process?", "options": _BASE, "elaboration": "<see §2.1>"},
]}

_DOMAIN_1B = {"id": "1b", "judge": domain1b_judge,
    "relevant_fields": ["recruitment_after_randomization", "contamination_risk", "n_clusters", "cluster_unit"],
    "name": "Bias arising from the timing of identification or recruitment of participants", "signals": [
    {"id": "1b.1", "text": "Were all the individual participants identified and recruited (if appropriate) before randomization of clusters?", "options": _BASE, "elaboration": "<see §2.2>"},
    {"id": "1b.2", "text": "Is it likely that selection of individual participants was affected by knowledge of the intervention assigned to the cluster?", "options": _BASE, "elaboration": "<see §2.2>"},
    {"id": "1b.3", "text": "Were there baseline imbalances that suggest differential identification or recruitment of individual participants between intervention groups?", "options": _BASE, "elaboration": "<see §2.2>"},
]}

_DOMAIN_2_ASSIGNMENT = {"id": 2, "judge": domain2_assignment_judge,
    "relevant_fields": ["contamination_risk", "clustering_in_analysis",
                        "blinding_participants", "blinding_personnel",
                        "protocol_deviations", "analysis_framework"],
    "name": "Bias due to deviations from intended interventions (effect of assignment to intervention)", "signals": [
    {"id": "2.1a", "text": "Were participants aware that they were in a trial?", "options": _BASE, "elaboration": "<see §2.3>"},
    {"id": "2.1b", "text": "Were participants aware of their assigned intervention during the trial?", "options": _BASE, "elaboration": "<see §2.3>"},
    {"id": "2.2", "text": "Were carers and people delivering the interventions aware of participants' assigned intervention during the trial?", "options": _BASE, "elaboration": "<see §2.3>"},
    {"id": "2.3", "text": "Were there deviations from the intended intervention that arose because of the trial context?", "options": _BASE, "elaboration": "<see §2.3>"},
    {"id": "2.4", "text": "Were these deviations likely to have affected the outcome?", "options": _BASE, "elaboration": "<see §2.3>"},
    {"id": "2.5", "text": "Were these deviations from intended intervention balanced between groups?", "options": _BASE, "elaboration": "<see §2.3>"},
    {"id": "2.6", "text": "Was an appropriate analysis used to estimate the effect of assignment to intervention?", "options": _BASE, "elaboration": "<see §2.3>"},
    {"id": "2.7", "text": "Was there potential for a substantial impact (on the result) of the failure to analyse participants in the group to which they were randomized?", "options": _BASE, "elaboration": "<see §2.3>"},
]}

_DOMAIN_2_ADHERING = {"id": 2, "judge": domain2_adhering_judge,
    "relevant_fields": ["contamination_risk", "clustering_in_analysis",
                        "blinding_participants", "blinding_personnel",
                        "protocol_deviations", "analysis_framework"],
    "name": "Bias due to deviations from intended interventions (effect of adhering to intervention)", "signals": [
    {"id": "2.1", "text": "Were participants aware of their assigned intervention during the trial?", "options": _BASE, "elaboration": "<see §2.4>"},
    {"id": "2.2", "text": "Were carers and people delivering the interventions aware of participants' assigned intervention during the trial?", "options": _BASE, "elaboration": "<see §2.4>"},
    {"id": "2.3", "text": "Were important non-protocol interventions balanced across intervention groups?", "options": _BASE, "elaboration": "<see §2.4>"},
    {"id": "2.4", "text": "Were there failures in implementing the intervention that could have affected the outcome?", "options": _BASE, "elaboration": "<see §2.4>"},
    {"id": "2.5", "text": "Was there non-adherence to the assigned intervention regimen that could have affected participants' outcomes?", "options": _BASE, "elaboration": "<see §2.4>"},
    {"id": "2.6", "text": "Was an appropriate analysis used to estimate the effect of adhering to the intervention?", "options": _BASE, "elaboration": "<see §2.4>"},
]}

_DOMAIN_3 = {"id": 3, "judge": domain3_judge,
    "relevant_fields": ["clustering_in_analysis", "n_clusters", "attrition_rate", "missing_data_handling"],
    "name": "Bias due to missing outcome data", "signals": [
    {"id": "3.1a", "text": "Were data for this outcome available for all clusters that recruited participants?", "options": _BASE, "elaboration": "<see §2.5>"},
    {"id": "3.1b", "text": "Were data for this outcome available for all, or nearly all, participants within clusters?", "options": _BASE, "elaboration": "<see §2.5>"},
    {"id": "3.2", "text": "Is there evidence that the result was not biased by missing outcome data?", "options": _NO_NI, "elaboration": "<see §2.5>"},
    {"id": "3.3", "text": "Could missingness in the outcome depend on its true value?", "options": _BASE, "elaboration": "<see §2.5>"},
    {"id": "3.4", "text": "Is it likely that missingness in the outcome depended on its true value?", "options": _BASE, "elaboration": "<see §2.5>"},
]}

_DOMAIN_4 = {"id": 4, "judge": domain4_judge,
    "relevant_fields": ["blinding_outcome_assessors", "outcome_measurement_method", "clustering_in_analysis"],
    "name": "Bias in measurement of the outcome", "signals": [
    {"id": "4.1", "text": "Was the method of measuring the outcome inappropriate?", "options": _BASE, "elaboration": "<see §2.6>"},
    {"id": "4.2", "text": "Could measurement or ascertainment of the outcome have differed between intervention groups?", "options": _BASE, "elaboration": "<see §2.6>"},
    {"id": "4.3a", "text": "Were outcome assessors aware that a trial was taking place?", "options": _BASE, "elaboration": "<see §2.6>"},
    {"id": "4.3b", "text": "Were outcome assessors aware of the intervention received by study participants?", "options": _BASE, "elaboration": "<see §2.6>"},
    {"id": "4.4", "text": "Could assessment of the outcome have been influenced by knowledge of intervention received?", "options": _BASE, "elaboration": "<see §2.6>"},
    {"id": "4.5", "text": "Is it likely that assessment of the outcome was influenced by knowledge of intervention received?", "options": _BASE, "elaboration": "<see §2.6>"},
]}

_DOMAIN_5 = {"id": 5, "judge": domain5_judge,
    "relevant_fields": ["protocol_available", "outcomes_match_protocol"],
    "name": "Bias in selection of the reported result", "signals": [
    {"id": "5.1", "text": "Were the data that produced this result analysed in accordance with a pre-specified analysis plan that was finalized before unblinded outcome data were available for analysis?", "options": _BASE, "elaboration": "<see §2.7>"},
    {"id": "5.2", "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible outcome measurements within the outcome domain?", "options": _BASE, "elaboration": "<see §2.7>"},
    {"id": "5.3", "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible analyses of the data?", "options": _BASE, "elaboration": "<see §2.7>"},
]}


def domains_for_aim(aim):
    d2 = _DOMAIN_2_ADHERING if aim == "adhering" else _DOMAIN_2_ASSIGNMENT
    return [_DOMAIN_1A, _DOMAIN_1B, d2, _DOMAIN_3, _DOMAIN_4, _DOMAIN_5]


# ── Prompt building ────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing risk of bias in a "
    "**cluster-randomized** trial using the Cochrane RoB 2 tool (cluster-randomized "
    "trial extension, RoB 2 CRT). Read the PDF carefully. Answer every signaling "
    "question on the Y/PY/PN/N/NI scale based on what the paper reports. Do NOT "
    "decide whether a question is 'not applicable' — the cribsheet's conditional "
    "structure is resolved in code after you answer. Provide a 1-2 sentence "
    "rationale for each answer. Return ONLY a valid JSON object — no preamble, "
    "no markdown fences."
)

OVERRIDE_NOTE = (
    "\\n\\nNote: this assessment is scoped to one specific outcome, "
    "selected by the reviewer. Domain 1a "
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
            f"Response options: {'/'.join(sig['options'])}."
        )
    questions_block = "\n".join(q_lines)
    shape = "{\n"
    for sig in domain["signals"]:
        shape += f'  "{sig["id"]}": "{"|".join(sig["options"])}",\n'
        shape += f'  "{sig["id"]}_rationale": "1-2 sentences quoting the paper",\n'
    shape += '  "direction_of_bias": "NA|Favours experimental|Favours comparator|Towards null|Away from null|Unpredictable"\n'
    shape += "}"
    override_note = OVERRIDE_NOTE if (outcome_is_override and str(domain["id"]) == "1a") else ""
    return (
        f"Assess **Domain {domain['id']} — {domain['name']}** for the "
        f"**cluster-randomized** trial described in the attached PDF.\n\n"
        f"Study type: {study_type}\n"
        f"Outcome being assessed: {assessed_outcome}{override_note}\n\n"
        f"Context (fields already extracted from the paper):\n{ctx_json}\n\n"
        f"Signaling questions:\n{questions_block}\n\n"
        f"Return a JSON object with exactly this shape:\n{shape}\n\n"
        f"Answer each question using only its listed response options, on its "
        f"own merits — do not mark a question not-applicable; the tool resolves "
        f"the conditional structure in code."
    )


# ── Per-domain LLM call + parse + cascade ──────────────────────

def assess_domain(pdf_bytes, domain, aim, study_type, assessed_outcome,
                   extracted_fields, call_llm, outcome_is_override=False):
    prompt = build_domain_prompt(
        domain, study_type, assessed_outcome,
        extracted_fields, outcome_is_override,
    )
    raw = call_llm(SYSTEM_PROMPT, prompt, pdf_bytes)
    signals, rationales = {}, {}
    for sig in domain["signals"]:
        sid = sig["id"]
        allowed = sig["options"]
        ans = str(raw.get(sid, "NI")).strip().upper()
        if ans not in allowed:
            ans = "NI" if "NI" in allowed else "N"
        signals[sid] = ans
        rationales[sid] = str(raw.get(f"{sid}_rationale", "")).strip()
    # Python-derived NA — resolve the conditional structure in code.
    signals = enforce_cascade(domain["id"], signals, aim=aim)
    return {
        "id": domain["id"],
        "name": domain["name"],
        "signals": signals,
        "rationales": rationales,
        "judgement": domain["judge"](signals),
        "direction": str(raw.get("direction_of_bias", "NA")).strip() or "NA",
    }


# ── Top-level entry point ──────────────────────────────────────

def assess_cluster_trial(pdf_bytes, study_type, assessed_outcome,
                         extracted_fields, call_llm,
                         outcome_is_override=False, aim="assignment"):
    """Run all 6 domains; return per-domain results + overall judgement."""
    aim = "adhering" if str(aim or "").strip().lower() == "adhering" else "assignment"
    domain_results = {}
    for domain in domains_for_aim(aim):
        domain_results[str(domain["id"])] = assess_domain(
            pdf_bytes, domain, aim, study_type, assessed_outcome,
            extracted_fields, call_llm, outcome_is_override,
        )
    overall_j = overall([d["judgement"] for d in domain_results.values()])
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
        "aim": aim,
        "overall_judgement": overall_j,
        "overall_direction": overall_d,
    }
```

---

## 9. Quick test sketches (no framework — plain `assert`)

```python
# Domain 1a — concealed + random + no baseline issue → Low
assert domain1a_judge({"1a.1": "Y", "1a.2": "Y", "1a.3": "N"}) == "Low"

# Domain 1a — concealed but NOT random → Some concerns (CRT-specific routing)
assert domain1a_judge({"1a.1": "N", "1a.2": "Y", "1a.3": "N"}) == "Some concerns"

# Domain 1b — all participants recruited before randomization → Low
assert domain1b_judge({"1b.1": "Y", "1b.2": "NA", "1b.3": "NA"}) == "Low"

# Domain 2 (assignment) — not aware + appropriate ITT analysis → Low
assert domain2_assignment_judge({"2.1a": "N", "2.2": "N", "2.6": "Y"}) == "Low"

# Domain 2 (adhering) — not aware, no failures → Low
assert domain2_adhering_judge(
    {"2.1": "N", "2.2": "N", "2.4": "N", "2.5": "N"}) == "Low"

# Cascade — 1b.2 is gated to NA when 1b.1 is Y
assert enforce_cascade_1b({"1b.1": "Y", "1b.2": "Y", "1b.3": "N"})["1b.2"] == "NA"

# Cascade — assignment chain: not-aware gates 2.3 → 2.4 → 2.5
out = enforce_cascade_2_assignment(
    {"2.1a": "N", "2.1b": "Y", "2.2": "N", "2.3": "Y",
     "2.4": "Y", "2.5": "Y", "2.6": "Y", "2.7": "Y"})
assert out["2.1b"] == out["2.3"] == out["2.4"] == out["2.5"] == out["2.7"] == "NA"

# Cascade — D4: an inappropriate measurement method gates the assessor chain
out4 = enforce_cascade_4(
    {"4.1": "Y", "4.2": "N", "4.3a": "N", "4.3b": "N", "4.4": "N", "4.5": "N"})
assert out4["4.3a"] == out4["4.3b"] == out4["4.4"] == out4["4.5"] == "NA"

# Overall — all six Low → Low; two Some concerns → High
assert overall(["Low"] * 6) == "Low"
assert overall(["Low", "Low", "Some concerns", "Low", "Some concerns", "Low"]) == "High"
```

---

## 10. Implementation notes for other platforms

- **PDF as document attachment.** Each per-domain call sends the full paper as a PDF document plus the system prompt and the per-domain user prompt. Image-heavy papers exceeding the context window need a PDF→text fallback; the dispatch logic and expected output shape don't change.

- **Per-domain calls.** One LLM call per domain (six total) rather than a single mega-call — focused prompts, single-domain retry on parse errors, no context-limit pressure on long PDFs.

- **`NA` is derived, not answered.** Every conditional signaling question ("If X to Y…") is a pure prior-answer dependency — its applicability is fully determined by the answers to *other* questions, so the LLM has no business deciding it. The LLM answers every question on the 5-token Y/PY/PN/N/NI scale, and `enforce_cascade_*` (§3) derives `NA` in code. This keeps the conditional structure deterministic and auditable, makes the LLM's task simpler, and removes any chance of an LLM-assigned `NA` contradicting the decision tree. (The per-protocol Domain 2's 2.3/2.4/2.5 carry a substantive `[If applicable]` judgement in the cribsheet; because `NA` there is routing-equivalent to `Y`/`N`, it folds into the 5-token scale via the elaboration guidance rather than needing a separate token.)

- **The analysis aim is a review-team decision.** Domain 2 has two variants — intention-to-treat (effect of *assignment*) vs per-protocol (effect of *adhering*). The aim is chosen by the review team for the result being assessed, *not* inferred from the paper. Expose it as a per-run setting and default to `assignment` (ITT). Record the chosen aim on the output.

- **Independent transcription, not parallel-group reuse.** The CRT cribsheet flowcharts diverge from the 2019 parallel-group RoB 2 tool on several paths (notably Domain 1a, and the 3.1/4.3 splits in Domains 3 and 4). Transcribe the CRT trees directly; only Domain 5 and the overall aggregation are genuinely identical to the parallel-group tool.

- **Signaling question 3.2 has no `NI`.** Its scale is `Y / PY / PN / N`. If the model returns anything else for 3.2, default to `N` (conservative — no evidence the result is unbiased).

- **Where the "assessed outcome" comes from.** Pick the primary outcome from the paper's extracted fields by trying, in order: `primary_outcome_definition` → `primary_outcome_measurement` → `population_outcomes`. If your platform supports a reviewer override, pass `outcome_is_override=True` so Domain 1a gets a clarifying note (§5.2).

- **Stepped-wedge is out of scope.** This tool covers parallel cluster-randomized trials. Stepped-wedge cluster trials need an additional time-trend domain — do not route them here.

---

## 11. Reporting-guideline companion — CONSORT cluster extension

Cluster-randomized trials should also be assessed against the **CONSORT extension to cluster randomised trials** (Campbell MK, Piaggio G, Elbourne DR, Altman DG. BMJ 2012; 345: e5661) in addition to the base CONSORT checklist. The extension items cover cluster-specific reporting: identification as a cluster-randomised trial; the rationale for using a cluster design; the definition of the cluster; eligibility criteria for clusters *and* for individual participants; whether the intervention and each outcome apply at the cluster or the individual level; a sample-size calculation accounting for the number of clusters, cluster size, and the intracluster correlation coefficient (ICC); clusters as the unit of randomisation; statistical methods that account for clustering; a flow diagram and counts at both the cluster and individual level; baseline data at both levels; numbers analysed and the estimated ICC for each primary outcome; and generalisability discussed for both clusters and individuals.

Implementation pattern: one LLM call per paper, asked to judge each combined (base + extension) item as adhered (`true`), not adhered (`false`), or not applicable (`null`). The proportion reported is `adhered / applicable` (the `null` items are excluded from both numerator and denominator).
