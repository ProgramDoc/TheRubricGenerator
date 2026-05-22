# RoB 2 Cluster-Randomized Trials (RoB 2 CRT) — Sharable Methodology Reference

A self-contained reference for implementing an automated Cochrane RoB 2
cluster-randomized-trial assessment on any platform. Contains:

- Signaling questions (transcribed from the cribsheet) for all 6 domains
- Decision-tree logic as plain Python (no framework / database / HTTP dependencies)
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

## 1. Signal answer options

Most signaling questions accept one of five answers; conditional questions additionally accept `NA`:

```python
SIGNAL_OPTIONS = ("Y", "PY", "PN", "N", "NI", "NA")
# Y  = Yes
# PY = Probably yes
# PN = Probably no
# N  = No
# NI = No information
# NA = Not applicable (offered only for conditional questions whose
#      precondition is not met)
```

Decision trees treat Y/PY as "yes" and N/PN as "no":

```python
def _yes(ans: str) -> bool:
    return ans in ("Y", "PY")

def _no(ans: str) -> bool:
    return ans in ("N", "PN")
```

For every tree **except the Domain 2 adhering tree**, a conditional question is consulted only on the branch where it is applicable, so a stray `NA` there is a model slip — map it to `NI` (the safe equivalent):

```python
def _g(signals: dict[str, str], sid: str) -> str:
    """Read a signal answer, mapping a stray 'NA' to 'NI'."""
    a = str(signals.get(sid, "NI")).strip().upper()
    return "NI" if a == "NA" else a
```

The Domain 2 **adhering** tree reads raw, because its flowchart deliberately routes `NA` alongside the no-concern branches.

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

### 2.1 Domain 1a — Bias arising from the randomization process

Signaling questions:

- **1a.1** Was the allocation sequence random?

  *Elaboration:* Considerations are mostly the same as for individually-randomized trials. The unit of allocation is the cluster. Answer 'No' for non-random methods that might be seen in cluster-randomized trials, including those based on geography (e.g., clusters near the main research centre allocated to the intervention and those further away to the control), alternation, or any systematic or judgement-based method.

- **1a.2** Was the allocation sequence concealed until clusters were enrolled and assigned to interventions?

  *Elaboration:* As for individually-randomized trials, but applied to clusters. Answer 'Yes' if clusters were enrolled and their identities fixed before allocation was revealed. Answer 'No' if those enrolling clusters could foresee assignments.

- **1a.3** Did baseline differences between intervention groups suggest a problem with the randomization process?

  *Elaboration:* Differences compatible with chance do not lead to a risk of bias. Answer 'No' if observed imbalances are compatible with chance or are likely due to identification/recruitment bias (assessed in Domain 1b). Because most cluster-randomized trials randomize few clusters, substantial chance imbalances are more common than in individually-randomized trials. Answer 'Yes' if imbalances indicate a problem with the randomization process: substantial differences in the numbers of clusters per arm vs the intended allocation ratio; a substantial excess of statistically-significant baseline differences beyond chance; imbalance in a baseline outcome measure unlikely to be due to chance and large enough to bias the estimate; or excessive similarity not compatible with chance. Answer 'No information' when no useful baseline data are reported.

Decision tree:

```python
def domain1a_judge(signals: dict[str, str]) -> str:
    """Domain 1a (randomization process) — RoB 2 CRT cribsheet p.6."""
    q1 = _g(signals, "1a.1")
    q2 = _g(signals, "1a.2")
    q3 = _g(signals, "1a.3")

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

Signaling questions:

- **1b.1** Were all the individual participants identified and recruited (if appropriate) before randomization of clusters?

  *Elaboration:* Answer 'Yes' if (1) all participants were identified and recruited before the clusters were randomized, or (2) individual participants were not recruited at all but all were identified before randomization — in these cases identification/recruitment bias is not possible. Answer 'No' if (1) some or all participants were identified or recruited after randomization, or (2) there are any clusters in which no participants were recruited (empty clusters).

- **1b.2** *(If N/PN/NI to 1b.1:)* Is it likely that selection of individual participants was affected by knowledge of the intervention assigned to the cluster?

  *Elaboration:* Answer 'Yes' if those recruiting or identifying participants were aware of cluster allocation and this is likely, consciously or subconsciously, to have affected recruitment differentially between the intervention groups; or if some participants were aware of cluster allocation before their recruitment in a way likely to have affected recruitment differentially. Answer 'No' if all relevant parties — those identifying actual or potential participants, those recruiting, and potential participants — were unaware of cluster allocation at recruitment.

- **1b.3** Were there baseline imbalances that suggest differential identification or recruitment of individual participants between intervention groups?

  *Elaboration:* As for signalling question 1a.3, imbalances compatible with chance should not be interpreted as suggesting differential identification or recruitment. Such imbalances are more common in cluster-randomized trials than imbalances due to problems with randomization. They can be in the numbers of participants recruited into each group, or in the characteristics of those individuals.

Decision tree:

```python
def domain1b_judge(signals: dict[str, str]) -> str:
    """Domain 1b (timing of identification/recruitment) — cribsheet p.9."""
    q1 = _g(signals, "1b.1")
    q2 = _g(signals, "1b.2")
    q3 = _g(signals, "1b.3")

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

Use this variant when the review team's aim is the **intention-to-treat** effect. Eight signaling questions; 1a-style two-part flowchart.

- **2.1a** Were participants aware that they were in a trial?

  *Elaboration:* In cluster-randomized trials participants may know they are receiving an intervention — or even that they are in a study — without knowing they are in a *trial*, so they may not know another intervention is being compared. This makes it impossible for them to cause deviations that arise because of the trial context. Answer 'No' if participants are not aware they are in a study, or are aware they are in a study but not that they are in a trial. Participants are those on whom investigators seek to measure the outcome — patients, the public, health professionals, or other cluster staff.

- **2.1b** *(If Y/PY/NI to 2.1a:)* Were participants aware of their assigned intervention during the trial?

  *Elaboration:* Answer 'Yes' if participants were aware of any part of the assigned intervention during the trial; consider all parts of the assigned intervention.

- **2.2** Were carers and people delivering the interventions aware of participants' assigned intervention during the trial?

  *Elaboration:* If those caring for participants or delivering the interventions are aware of the assigned intervention, then implementation, or administration of non-protocol interventions, may differ between groups. Blinding carers and trial personnel is rare in cluster-randomized trials.

- **2.3** *(If Y/PY/NI to 2.1b or 2.2:)* Were there deviations from the intended intervention that arose because of the trial context?

  *Elaboration:* The guidance mostly applies as for individually-randomized trials. Such deviations are rarely reported in cluster-randomized trials and may in fact occur rarely — interventions are often aimed at clusters and cluster staff, who may lack the authority or motivation to introduce deviations. 'No information' is appropriate in many cases, but use 'Probably yes' if it seems likely such deviations occurred.

- **2.4** *(If Y/PY to 2.3:)* Were these deviations likely to have affected the outcome?

  *Elaboration:* As for individually-randomized trials — deviations impact the effect estimate only if they affect the outcome.

- **2.5** *(If Y/PY/NI to 2.4:)* Were these deviations from intended intervention balanced between groups?

  *Elaboration:* As for individually-randomized trials — unbalanced trial-context deviations bias the effect estimate more than balanced ones.

- **2.6** Was an appropriate analysis used to estimate the effect of assignment to intervention?

  *Elaboration:* Answer 'Yes' if all clusters and individuals were analysed according to the groups to which they were assigned. Where the number of individuals whose assigned group cannot be identified with certainty (or who changed clusters) is small and unrelated to assigned group, an analysis that places all individuals in their assigned groups as far as possible is appropriate. Answer 'No' if participants were analysed by intervention received rather than assigned, if analyses exclude participants or whole clusters not receiving their assigned intervention, or if a stepped-wedge trial ignores the time trend.

- **2.7** *(If N/PN/NI to 2.6:)* Was there potential for a substantial impact (on the result) of the failure to analyse participants in the group to which they were randomized?

  *Elaboration:* As for individually-randomized trials, but watch for entire clusters analysed in the wrong intervention group as well as individual participants. There is no precise threshold.

Decision tree (Part 1 covers 2.1a–2.5; Part 2 covers 2.6–2.7; combined per the cribsheet):

```python
def domain2_assignment_judge(signals: dict[str, str]) -> str:
    """Domain 2 — effect of ASSIGNMENT to intervention (ITT) — cribsheet p.12.

      Low    iff Part 1 = Low AND Part 2 = Low
      High   iff either part = High
      Some   otherwise
    """
    q21a = _g(signals, "2.1a")
    q21b = _g(signals, "2.1b")
    q22 = _g(signals, "2.2")
    q23 = _g(signals, "2.3")
    q24 = _g(signals, "2.4")
    q25 = _g(signals, "2.5")
    q26 = _g(signals, "2.6")
    q27 = _g(signals, "2.7")

    # ── Part 1 — awareness gate, then 2.3 → 2.4 → 2.5 ──
    if _no(q21a):
        # Participants not aware they were in a trial → only carers/deliverers
        # (2.2) can introduce trial-context deviations.
        aware = not _no(q22)
    else:
        # 2.1a Y/PY/NI → consult 2.1b + 2.2; both N/PN means "not aware".
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
        # 2.4 Y/PY/NI → 2.5
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

Use this variant when the review team's aim is the **per-protocol** effect. Six signaling questions. `NA` is a meaningful routing token in this flowchart.

- **2.1** Were participants aware of their assigned intervention during the trial?

  *Elaboration:* If participants are aware of their assigned intervention, health-related behaviours are more likely to differ between groups. If participants experienced side effects or toxicities specific to one intervention, answer 'Yes'/'Probably yes'.

- **2.2** Were carers and people delivering the interventions aware of participants' assigned intervention during the trial?

  *Elaboration:* If carers or people delivering the interventions are aware of the assigned intervention, implementation or the administration of non-protocol interventions may differ between groups. If randomized allocation was not concealed, they were likely aware.

- **2.3** *(If applicable; if Y/PY/NI to 2.1 or 2.2:)* Were important non-protocol interventions balanced across intervention groups?

  *Elaboration:* Non-protocol interventions are co-interventions received in addition to the protocol-defined intervention. Answer 'Yes' if important non-protocol interventions were balanced across groups. Imbalanced non-protocol interventions — at the individual or cluster level — can bias the estimated effect of adhering. Mark 'NA' if there were no important non-protocol interventions to consider.

- **2.4** *(If applicable:)* Were there failures in implementing the intervention that could have affected the outcome?

  *Elaboration:* Answer 'Yes' if the intervention was not implemented as intended in a way that could have affected the outcome — incomplete or inconsistent delivery, or departures from the protocol-specified intervention. Implementation failures are especially relevant for complex interventions delivered to whole clusters or cluster staff.

- **2.5** *(If applicable:)* Was there non-adherence to the assigned intervention regimen that could have affected participants' outcomes?

  *Elaboration:* Mostly as for individually-randomized trials. Consider non-adherence and co-interventions at both the individual and the cluster level.

- **2.6** *(If N/PN/NI to 2.3, or Y/PY/NI to 2.4 or 2.5:)* Was an appropriate analysis used to estimate the effect of adhering to the intervention?

  *Elaboration:* Mostly as for individually-randomized trials — an appropriate analysis adjusts for prognostic factors and the timing of any non-protocol intervention, non-adherence, or implementation failure. For multifaceted interventions, consider every intervention for which implementation failures could have affected the outcome.

Decision tree:

```python
def domain2_adhering_judge(signals: dict[str, str]) -> str:
    """Domain 2 — effect of ADHERING to intervention (per-protocol) — p.14.

    'NA' is a meaningful routing token (grouped with the no-concern
    branches), so this tree reads raw — it does NOT use _g().
    """
    q21 = str(signals.get("2.1", "NI")).strip().upper()
    q22 = str(signals.get("2.2", "NI")).strip().upper()
    q23 = str(signals.get("2.3", "NI")).strip().upper()
    q24 = str(signals.get("2.4", "NI")).strip().upper()
    q25 = str(signals.get("2.5", "NI")).strip().upper()
    q26 = str(signals.get("2.6", "NI")).strip().upper()

    def _node_2425() -> str:
        # Both 2.4 and 2.5 NA/N/PN → Low; otherwise consult 2.6.
        if q24 in ("NA", "N", "PN") and q25 in ("NA", "N", "PN"):
            return "Low"
        return "Some concerns" if _yes(q26) else "High"

    if _no(q21) and _no(q22):
        # Neither participants nor carers/deliverers aware → straight to 2.4/2.5.
        return _node_2425()
    # Either 2.1 or 2.2 Y/PY/NI → consult 2.3.
    if q23 in ("NA", "Y", "PY"):
        return _node_2425()
    # 2.3 N/PN/NI → consult 2.6 directly.
    return "Some concerns" if _yes(q26) else "High"
```

### 2.5 Domain 3 — Bias due to missing outcome data

Signaling question 3.1 is split into 3.1a (cluster-level) and 3.1b (participant-level within clusters).

- **3.1a** Were data for this outcome available for all clusters that recruited participants?

  *Elaboration:* In some cluster-randomized trials there may be clusters in which no participants were recruited — this happens only when participants are recruited after randomization and is handled in Domain 1b. Because a cluster-randomized trial usually has few clusters, there is potential for bias even if only one cluster has no analysable participants. Answer 'No' if any recruiting cluster contributed no outcome data.

- **3.1b** Were data for this outcome available for all, or nearly all, participants within clusters?

  *Elaboration:* Broadly as for individually-randomized trials: 'nearly all' means missingness small enough that it could have made no important difference. In cluster-randomized trials there may be particular complexities when clusters merge, split, or disappear.

- **3.2** *(If N/PN/NI to 3.1a or 3.1b:)* Is there evidence that the result was not biased by missing outcome data?

  *Elaboration:* As for individually-randomized trials — evidence can come from an analysis method that corrects for bias from missing data, or from sensitivity analyses showing robustness. A single imputation method does not by itself establish lack of bias.

- **3.3** *(If N/PN to 3.2:)* Could missingness in the outcome depend on its true value?

  *Elaboration:* As for individually-randomized trials — if loss to follow-up or withdrawal might be related to participants' health status, missingness could depend on the true outcome value.

- **3.4** *(If Y/PY/NI to 3.3:)* Is it likely that missingness in the outcome depended on its true value?

  *Elaboration:* As for individually-randomized trials — distinguishes 'could depend' (Some concerns) from 'likely did depend' (High).

Decision tree:

```python
def domain3_judge(signals: dict[str, str]) -> str:
    """Domain 3 (missing outcome data) — cribsheet p.16."""
    q31a = _g(signals, "3.1a")
    q31b = _g(signals, "3.1b")
    q32 = _g(signals, "3.2")
    q33 = _g(signals, "3.3")
    q34 = _g(signals, "3.4")

    if _yes(q31a) and _yes(q31b):
        return "Low"
    # Either 3.1a/3.1b N/PN/NI → 3.2
    if _yes(q32):
        return "Low"
    # 3.2 N/PN → 3.3
    if _no(q33):
        return "Low"
    # 3.3 Y/PY/NI → 3.4
    if _no(q34):
        return "Some concerns"
    return "High"
```

### 2.6 Domain 4 — Bias in measurement of the outcome

Signaling question 4.3 is split into 4.3a (assessors aware a trial is taking place — relevant for participant-reported outcomes) and 4.3b (assessors aware of the intervention received).

- **4.1** Was the method of measuring the outcome inappropriate?

  *Elaboration:* As for individually-randomized trials. Usually 'No' for pre-specified outcomes.

- **4.2** Could measurement or ascertainment of the outcome have differed between intervention groups?

  *Elaboration:* As for individually-randomized trials.

- **4.3a** *(If N/PN/NI to 4.1 and 4.2:)* Were outcome assessors aware that a trial was taking place?

  *Elaboration:* Applies to cluster-randomized trials in which participants report their own outcomes (e.g., a questionnaire). If they are not aware they are in a trial, their self-assessment cannot be affected by assignment even if they are aware of the intervention received.

- **4.3b** *(If Y/PY/NI to 4.3a:)* Were outcome assessors aware of the intervention received by study participants?

  *Elaboration:* Answer 'No' if outcome assessors were blinded to intervention status. For participant-reported outcomes the outcome assessor IS the study participant. Where outcomes come from routine data, the individual providing the data and the individual extracting it can both be considered outcome assessors.

- **4.4** *(If Y/PY/NI to 4.3b:)* Could assessment of the outcome have been influenced by knowledge of intervention received?

  *Elaboration:* As for individually-randomized trials.

- **4.5** *(If Y/PY/NI to 4.4:)* Is it likely that assessment of the outcome was influenced by knowledge of intervention received?

  *Elaboration:* As for individually-randomized trials.

Decision tree:

```python
def domain4_judge(signals: dict[str, str]) -> str:
    """Domain 4 (measurement of the outcome) — cribsheet p.19."""
    q41 = _g(signals, "4.1")
    q42 = _g(signals, "4.2")
    q43a = _g(signals, "4.3a")
    q43b = _g(signals, "4.3b")
    q44 = _g(signals, "4.4")
    q45 = _g(signals, "4.5")

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

Three signaling questions; identical to standard parallel-group RoB 2 (cribsheet p.21 — "as for individually-randomized trials").

- **5.1** Were the data that produced this result analysed in accordance with a pre-specified analysis plan that was finalized before unblinded outcome data were available for analysis?

- **5.2** Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible outcome measurements (e.g., scales, definitions, time points) within the outcome domain?

- **5.3** Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible analyses of the data?

  *Elaboration (5.3, cluster-specific note):* An outcome measurement may be analysed in multiple ways, including different ways of accounting for clustering. If multiple estimates exist but only one is reported on the basis of the results, answer 'Yes'.

Decision tree:

```python
def domain5_judge(signals: dict[str, str]) -> str:
    """Domain 5 (selection of the reported result) — cribsheet p.21."""
    q51 = _g(signals, "5.1")
    q52 = _g(signals, "5.2")
    q53 = _g(signals, "5.3")

    if _yes(q52) or _yes(q53):
        return "High"
    if _no(q52) and _no(q53):
        return "Low" if _yes(q51) else "Some concerns"
    # At least one NI, none Y/PY
    return "Some concerns"
```

---

## 3. Overall RoB aggregation

Same as parallel-group RoB 2 (cribsheet p.22), applied to all six domains:

```python
def overall(domain_judgements: list[str]) -> str:
    """Overall RoB judgement.

      Low iff all domains Low.
      High iff any domain High OR >= 2 domains Some concerns.
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
**cluster-randomized** trial using the Cochrane RoB 2 tool (cluster-randomized
trial extension, RoB 2 CRT). Read the PDF carefully. Answer each signaling
question with one of the response options listed for that question — Y (yes),
PY (probably yes), PN (probably no), N (no), NI (no information), or NA (not
applicable, only where offered). Provide a 1-2 sentence rationale for each
answer, quoting the paper where possible. Return ONLY a valid JSON object —
no preamble, no markdown fences.
```

### 4.2 Per-domain user prompt template

Built per domain. Variables:

- `{id}` — domain id (`1a`, `1b`, `2`, `3`, `4`, `5`)
- `{name}` — full domain name
- `{study_type}` — e.g. "Cluster Randomized Trial"
- `{assessed_outcome}` — the outcome being assessed (auto-picked or reviewer-overridden)
- `{ctx_json}` — JSON of pre-extracted fields, or `(no pre-extracted fields)`
- `{questions_block}` — bulleted signaling questions + elaborations (see below)
- `{shape}` — expected JSON shape (see §5)
- `{override_note}` — optional, appended only to Domain 1a when the assessed outcome is a reviewer override

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

Answer N (or PN) when the paper gives enough information to rule out the problem, and NI only when the paper is silent. Use NA only for a question whose stated precondition is not met. Rationales must be short (1-2 sentences) and quote the paper verbatim where possible.
```

The optional Domain-1a override note (appended only when the reviewer has overridden the auto-picked outcome):

```text


Note: this assessment is for a non-primary outcome chosen by the reviewer
because the paper's primary outcome was unclear. Domain 1a signaling
questions concern the randomization process for the trial as a whole, not
the specific outcome — answer accordingly.
```

The `{questions_block}` is built by joining, for each signaling question:

```text

**{id}. {question_text}**
Elaboration: {elaboration}
Response options: {options}.
```

(`{options}` is the slash-joined option list for that question — `Y/PY/PN/N/NI` for base questions, `NA/Y/PY/PN/N/NI` for conditional ones.)

---

## 5. Expected JSON output shape

The model returns JSON with two keys per signaling question (`{sid}` and `{sid}_rationale`) plus one direction-of-bias key. Example for Domain 1b:

```json
{
  "1b.1": "Y|PY|PN|N|NI",
  "1b.1_rationale": "1-2 sentences quoting the paper",
  "1b.2": "NA|Y|PY|PN|N|NI",
  "1b.2_rationale": "1-2 sentences quoting the paper",
  "1b.3": "Y|PY|PN|N|NI",
  "1b.3_rationale": "1-2 sentences quoting the paper",
  "direction_of_bias": "NA|Favours experimental|Favours comparator|Towards null|Away from null|Unpredictable"
}
```

After parsing, each domain's result is enriched with the local judgement:

```json
{
  "id": "1b",
  "name": "Bias arising from the timing of identification or recruitment of participants",
  "signals":   {"1b.1": "N", "1b.2": "N", "1b.3": "N"},
  "rationales":{"1b.1": "...", "1b.2": "...", "1b.3": "..."},
  "judgement": "Low",
  "direction": "NA"
}
```

The overall trial-level result is then:

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

`aim` records which Domain 2 variant was used (`"assignment"` or `"adhering"`). `overall_direction` is the most common non-`NA` direction across the six domains; ties (or all `NA`) → `Unpredictable` / `NA`.

---

## 6. Sample data — what gets passed to each domain prompt

Each per-domain prompt receives the relevant subset of pre-extracted study fields. Useful relevance hints:

| Domain | Useful pre-extracted fields                                                                                |
| ------ | ---------------------------------------------------------------------------------------------------------- |
| 1a     | `cluster_unit`, `n_clusters`, `icc_reported`, `allocation_concealment`, `baseline_balance`                 |
| 1b     | `recruitment_after_randomization`, `contamination_risk`, `n_clusters`, `cluster_unit`                      |
| 2      | `contamination_risk`, `clustering_in_analysis`, `blinding_participants`, `blinding_personnel`, `protocol_deviations`, `analysis_framework` |
| 3      | `clustering_in_analysis`, `n_clusters`, `attrition_rate`, `missing_data_handling`                          |
| 4      | `blinding_outcome_assessors`, `outcome_measurement_method`, `clustering_in_analysis`                       |
| 5      | `protocol_available`, `outcomes_match_protocol`                                                            |

If a field is absent or empty, the prompt omits it from the context block. The model is told `(no pre-extracted fields)` when none are present, and is still asked to assess from the PDF directly.

---

## 7. Reference implementation as a single Python file

A turnkey reference: the complete cluster assessor logic in one file (no platform dependencies). It assumes you provide your own `call_llm(system_prompt, user_prompt, pdf_bytes) -> dict` function.

```python
"""rob2_cluster_assessor.py — reference implementation.

Public API:
    assess_cluster_trial(pdf_bytes, study_type, assessed_outcome,
                         extracted_fields, call_llm,
                         outcome_is_override=False, aim="assignment") -> dict

`call_llm` is a callable you provide:
    call_llm(system_prompt: str, user_prompt: str, pdf_bytes: bytes) -> dict
It must return the parsed JSON object the model produced.

`aim` selects the Domain 2 variant: "assignment" (intention-to-treat,
the default) or "adhering" (per-protocol).
"""

import json
from collections import Counter

SIGNAL_OPTIONS = ("Y", "PY", "PN", "N", "NI", "NA")


def _yes(ans):  return ans in ("Y", "PY")
def _no(ans):   return ans in ("N", "PN")


def _g(s, sid):
    """Read a signal answer, mapping a stray 'NA' to 'NI' (non-adhering trees)."""
    a = str(s.get(sid, "NI")).strip().upper()
    return "NI" if a == "NA" else a


# ── Decision trees ─────────────────────────────────────────────

def domain1a_judge(s):
    q1, q2, q3 = _g(s, "1a.1"), _g(s, "1a.2"), _g(s, "1a.3")
    if _no(q2): return "High"
    if q2 == "NI":
        return "High" if _yes(q3) else "Some concerns"
    if _no(q1): return "Some concerns"
    return "Some concerns" if _yes(q3) else "Low"


def domain1b_judge(s):
    q1, q2, q3 = _g(s, "1b.1"), _g(s, "1b.2"), _g(s, "1b.3")
    if _yes(q1): return "Low"
    if _yes(q2): return "High"
    if _no(q2):
        return "Some concerns" if _yes(q3) else "Low"
    return "High" if _yes(q3) else "Some concerns"


def domain2_assignment_judge(s):
    q21a, q21b, q22 = _g(s, "2.1a"), _g(s, "2.1b"), _g(s, "2.2")
    q23, q24, q25 = _g(s, "2.3"), _g(s, "2.4"), _g(s, "2.5")
    q26, q27 = _g(s, "2.6"), _g(s, "2.7")
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
    # NA is meaningful here — read raw, no _g().
    q21 = str(s.get("2.1", "NI")).strip().upper()
    q22 = str(s.get("2.2", "NI")).strip().upper()
    q23 = str(s.get("2.3", "NI")).strip().upper()
    q24 = str(s.get("2.4", "NI")).strip().upper()
    q25 = str(s.get("2.5", "NI")).strip().upper()
    q26 = str(s.get("2.6", "NI")).strip().upper()

    def _node_2425():
        if q24 in ("NA", "N", "PN") and q25 in ("NA", "N", "PN"):
            return "Low"
        return "Some concerns" if _yes(q26) else "High"

    if _no(q21) and _no(q22):
        return _node_2425()
    if q23 in ("NA", "Y", "PY"):
        return _node_2425()
    return "Some concerns" if _yes(q26) else "High"


def domain3_judge(s):
    q31a, q31b = _g(s, "3.1a"), _g(s, "3.1b")
    q32, q33, q34 = _g(s, "3.2"), _g(s, "3.3"), _g(s, "3.4")
    if _yes(q31a) and _yes(q31b): return "Low"
    if _yes(q32): return "Low"
    if _no(q33):  return "Low"
    if _no(q34):  return "Some concerns"
    return "High"


def domain4_judge(s):
    q41, q42 = _g(s, "4.1"), _g(s, "4.2")
    q43a, q43b = _g(s, "4.3a"), _g(s, "4.3b")
    q44, q45 = _g(s, "4.4"), _g(s, "4.5")
    if _yes(q41): return "High"
    if _yes(q42): return "High"
    floor = "Low" if _no(q42) else "Some concerns"
    if _no(q43a): return floor
    if _no(q43b): return floor
    if _no(q44):  return floor
    if _no(q45):  return "Some concerns"
    return "High"


def domain5_judge(s):
    q51, q52, q53 = _g(s, "5.1"), _g(s, "5.2"), _g(s, "5.3")
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


# ── Domain definitions ─────────────────────────────────────────
# Each domain: id, name, list of {id, text, elaboration, options}, and judge.
# (Question text + elaborations condensed here; full text is in §2 above.)

_BASE = ["Y", "PY", "PN", "N", "NI"]
_NA = ["NA", "Y", "PY", "PN", "N", "NI"]

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
    {"id": "1b.2", "text": "If N/PN/NI to 1b.1: Is it likely that selection of individual participants was affected by knowledge of the intervention assigned to the cluster?", "options": _NA, "elaboration": "<see §2.2>"},
    {"id": "1b.3", "text": "Were there baseline imbalances that suggest differential identification or recruitment of individual participants between intervention groups?", "options": _BASE, "elaboration": "<see §2.2>"},
]}

_DOMAIN_2_ASSIGNMENT = {"id": 2, "judge": domain2_assignment_judge,
    "relevant_fields": ["contamination_risk", "clustering_in_analysis",
                        "blinding_participants", "blinding_personnel",
                        "protocol_deviations", "analysis_framework"],
    "name": "Bias due to deviations from intended interventions (effect of assignment to intervention)", "signals": [
    {"id": "2.1a", "text": "Were participants aware that they were in a trial?", "options": _BASE, "elaboration": "<see §2.3>"},
    {"id": "2.1b", "text": "If Y/PY/NI to 2.1a: Were participants aware of their assigned intervention during the trial?", "options": _NA, "elaboration": "<see §2.3>"},
    {"id": "2.2", "text": "Were carers and people delivering the interventions aware of participants' assigned intervention during the trial?", "options": _BASE, "elaboration": "<see §2.3>"},
    {"id": "2.3", "text": "If Y/PY/NI to 2.1b or 2.2: Were there deviations from the intended intervention that arose because of the trial context?", "options": _NA, "elaboration": "<see §2.3>"},
    {"id": "2.4", "text": "If Y/PY to 2.3: Were these deviations likely to have affected the outcome?", "options": _NA, "elaboration": "<see §2.3>"},
    {"id": "2.5", "text": "If Y/PY/NI to 2.4: Were these deviations from intended intervention balanced between groups?", "options": _NA, "elaboration": "<see §2.3>"},
    {"id": "2.6", "text": "Was an appropriate analysis used to estimate the effect of assignment to intervention?", "options": _BASE, "elaboration": "<see §2.3>"},
    {"id": "2.7", "text": "If N/PN/NI to 2.6: Was there potential for a substantial impact (on the result) of the failure to analyse participants in the group to which they were randomized?", "options": _NA, "elaboration": "<see §2.3>"},
]}

_DOMAIN_2_ADHERING = {"id": 2, "judge": domain2_adhering_judge,
    "relevant_fields": ["contamination_risk", "clustering_in_analysis",
                        "blinding_participants", "blinding_personnel",
                        "protocol_deviations", "analysis_framework"],
    "name": "Bias due to deviations from intended interventions (effect of adhering to intervention)", "signals": [
    {"id": "2.1", "text": "Were participants aware of their assigned intervention during the trial?", "options": _BASE, "elaboration": "<see §2.4>"},
    {"id": "2.2", "text": "Were carers and people delivering the interventions aware of participants' assigned intervention during the trial?", "options": _BASE, "elaboration": "<see §2.4>"},
    {"id": "2.3", "text": "If applicable; if Y/PY/NI to 2.1 or 2.2: Were important non-protocol interventions balanced across intervention groups?", "options": _NA, "elaboration": "<see §2.4>"},
    {"id": "2.4", "text": "If applicable: Were there failures in implementing the intervention that could have affected the outcome?", "options": _NA, "elaboration": "<see §2.4>"},
    {"id": "2.5", "text": "If applicable: Was there non-adherence to the assigned intervention regimen that could have affected participants' outcomes?", "options": _NA, "elaboration": "<see §2.4>"},
    {"id": "2.6", "text": "If N/PN/NI to 2.3, or Y/PY/NI to 2.4 or 2.5: Was an appropriate analysis used to estimate the effect of adhering to the intervention?", "options": _NA, "elaboration": "<see §2.4>"},
]}

_DOMAIN_3 = {"id": 3, "judge": domain3_judge,
    "relevant_fields": ["clustering_in_analysis", "n_clusters", "attrition_rate", "missing_data_handling"],
    "name": "Bias due to missing outcome data", "signals": [
    {"id": "3.1a", "text": "Were data for this outcome available for all clusters that recruited participants?", "options": _BASE, "elaboration": "<see §2.5>"},
    {"id": "3.1b", "text": "Were data for this outcome available for all, or nearly all, participants within clusters?", "options": _BASE, "elaboration": "<see §2.5>"},
    {"id": "3.2", "text": "If N/PN/NI to 3.1a or 3.1b: Is there evidence that the result was not biased by missing data?", "options": ["NA", "Y", "PY", "PN", "N"], "elaboration": "<see §2.5>"},
    {"id": "3.3", "text": "If N/PN to 3.2: Could missingness in the outcome depend on its true value?", "options": _NA, "elaboration": "<see §2.5>"},
    {"id": "3.4", "text": "If Y/PY/NI to 3.3: Is it likely that missingness in the outcome depended on its true value?", "options": _NA, "elaboration": "<see §2.5>"},
]}

_DOMAIN_4 = {"id": 4, "judge": domain4_judge,
    "relevant_fields": ["blinding_outcome_assessors", "outcome_measurement_method", "clustering_in_analysis"],
    "name": "Bias in measurement of the outcome", "signals": [
    {"id": "4.1", "text": "Was the method of measuring the outcome inappropriate?", "options": _BASE, "elaboration": "<see §2.6>"},
    {"id": "4.2", "text": "Could measurement or ascertainment of the outcome have differed between intervention groups?", "options": _BASE, "elaboration": "<see §2.6>"},
    {"id": "4.3a", "text": "If N/PN/NI to 4.1 and 4.2: Were outcome assessors aware that a trial was taking place?", "options": _NA, "elaboration": "<see §2.6>"},
    {"id": "4.3b", "text": "If Y/PY/NI to 4.3a: Were outcome assessors aware of the intervention received by study participants?", "options": _NA, "elaboration": "<see §2.6>"},
    {"id": "4.4", "text": "If Y/PY/NI to 4.3b: Could assessment of the outcome have been influenced by knowledge of intervention received?", "options": _NA, "elaboration": "<see §2.6>"},
    {"id": "4.5", "text": "If Y/PY/NI to 4.4: Is it likely that assessment of the outcome was influenced by knowledge of intervention received?", "options": _NA, "elaboration": "<see §2.6>"},
]}

_DOMAIN_5 = {"id": 5, "judge": domain5_judge,
    "relevant_fields": ["protocol_available", "outcomes_match_protocol"],
    "name": "Bias in selection of the reported result", "signals": [
    {"id": "5.1", "text": "Were the data that produced this result analysed in accordance with a pre-specified analysis plan that was finalized before unblinded outcome data were available for analysis?", "options": _BASE, "elaboration": "<see §2.7>"},
    {"id": "5.2", "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible outcome measurements within the outcome domain?", "options": _BASE, "elaboration": "<see §2.7>"},
    {"id": "5.3", "text": "Is the numerical result being assessed likely to have been selected, on the basis of the results, from multiple eligible analyses of the data?", "options": _BASE, "elaboration": "<see §2.7>"},
]}


def domains_for_aim(aim):
    """Return the 6-domain list for the chosen aim (assignment / adhering)."""
    d2 = _DOMAIN_2_ADHERING if aim == "adhering" else _DOMAIN_2_ASSIGNMENT
    return [_DOMAIN_1A, _DOMAIN_1B, d2, _DOMAIN_3, _DOMAIN_4, _DOMAIN_5]


# ── Prompt building ────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing risk of bias in a "
    "**cluster-randomized** trial using the Cochrane RoB 2 tool (cluster-randomized "
    "trial extension, RoB 2 CRT). Read the PDF carefully. Answer each signaling "
    "question with one of the response options listed for that question — Y (yes), "
    "PY (probably yes), PN (probably no), N (no), NI (no information), or NA (not "
    "applicable, only where offered). Provide a 1-2 sentence rationale for each "
    "answer, quoting the paper where possible. Return ONLY a valid JSON object — "
    "no preamble, no markdown fences."
)

OVERRIDE_NOTE = (
    "\n\nNote: this assessment is for a non-primary outcome chosen by the "
    "reviewer because the paper's primary outcome was unclear. Domain 1a "
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
        f"Answer N (or PN) when the paper gives enough information to rule "
        f"out the problem, and NI only when the paper is silent. Use NA only "
        f"for a question whose stated precondition is not met. Rationales "
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

def assess_cluster_trial(pdf_bytes, study_type, assessed_outcome,
                         extracted_fields, call_llm,
                         outcome_is_override=False, aim="assignment"):
    """Run all 6 domains; return per-domain results + overall judgement.

    `aim` ("assignment" | "adhering") selects the Domain 2 variant.
    """
    aim = "adhering" if str(aim or "").strip().lower() == "adhering" else "assignment"
    domain_results = {}
    for domain in domains_for_aim(aim):
        domain_results[str(domain["id"])] = assess_domain(
            pdf_bytes, domain, study_type, assessed_outcome,
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

## 8. Quick test sketches (no framework — plain `assert`)

```python
# Domain 1a — concealed + random + no baseline issue → Low
assert domain1a_judge({"1a.1": "Y", "1a.2": "Y", "1a.3": "N"}) == "Low"

# Domain 1a — concealed but NOT random → Some concerns (CRT-specific routing)
assert domain1a_judge({"1a.1": "N", "1a.2": "Y", "1a.3": "N"}) == "Some concerns"

# Domain 1b — all participants recruited before randomization → Low
assert domain1b_judge({"1b.1": "Y", "1b.2": "NA", "1b.3": "NA"}) == "Low"

# Domain 1b — recruitment affected by knowledge of allocation → High
assert domain1b_judge({"1b.1": "N", "1b.2": "Y", "1b.3": "N"}) == "High"

# Domain 2 (assignment) — not aware + appropriate ITT analysis → Low
assert domain2_assignment_judge({"2.1a": "N", "2.2": "N", "2.6": "Y"}) == "Low"

# Domain 2 (assignment) — inappropriate analysis, large impact → High
assert domain2_assignment_judge(
    {"2.1a": "N", "2.2": "N", "2.6": "N", "2.7": "Y"}) == "High"

# Domain 2 (adhering) — not aware, no failures → Low (NA routes like N/PN)
assert domain2_adhering_judge(
    {"2.1": "N", "2.2": "N", "2.4": "NA", "2.5": "NA"}) == "Low"

# Domain 3 — complete cluster + participant data → Low
assert domain3_judge({"3.1a": "Y", "3.1b": "Y"}) == "Low"

# Domain 4 — assessors unaware a trial is happening → Low
assert domain4_judge({"4.1": "N", "4.2": "N", "4.3a": "N"}) == "Low"

# Overall — all six Low → Low
assert overall(["Low"] * 6) == "Low"

# Overall — two Some concerns → High
assert overall(["Low", "Low", "Some concerns", "Low", "Some concerns", "Low"]) == "High"
```

---

## 9. Implementation notes for other platforms

- **PDF as document attachment.** Each per-domain call sends the full paper as a PDF document plus the system prompt and the per-domain user prompt. Most LLM providers accept PDFs as a document content block. Image-heavy papers exceeding the context window need a PDF→text fallback; the dispatch logic and expected output shape don't change.

- **Per-domain calls.** One LLM call per domain (six total) rather than a single mega-call — focused prompts, single-domain retry on parse errors, no context-limit pressure on long PDFs.

- **The analysis aim is a review-team decision.** Domain 2 has two variants. The aim — intention-to-treat (effect of *assignment*) vs per-protocol (effect of *adhering*) — is chosen by the review team for the result being assessed, *not* inferred from the paper. Expose it as a per-run setting and default to `assignment` (ITT), the usual Cochrane default. Record the chosen aim on the output so downstream consumers know which Domain 2 was used.

- **The `NA` token.** Conditional questions offer `NA` for the case where their stated precondition is not met. For every tree except the Domain 2 adhering tree, a conditional question is consulted only on the branch where it is applicable, so map a stray `NA` there to `NI` (the `_g` helper). The adhering tree is the exception — its flowchart deliberately routes `NA` alongside the no-concern branches, so it reads raw.

- **Independent transcription, not parallel-group reuse.** The CRT cribsheet flowcharts diverge from the 2019 parallel-group RoB 2 tool on several paths (notably Domain 1a, and the 3.1/4.3 splits in Domains 3 and 4). Transcribe the CRT trees directly; only Domain 5 and the overall aggregation are genuinely identical to the parallel-group tool.

- **Where the "assessed outcome" comes from.** Pick the primary outcome from the paper's extracted fields by trying, in order: `primary_outcome_definition` → `primary_outcome_measurement` → `population_outcomes`. If your platform supports a reviewer override (the user picks a secondary outcome because the primary is unclear), pass `outcome_is_override=True` so Domain 1a gets a clarifying note (§4.2).

- **Field-extraction is upstream.** The decision-tree code makes no assumption about how `extracted_fields` is produced — it's an opaque dict you build elsewhere. The relevant subset is filtered per-domain via `domain["relevant_fields"]`.

- **Stepped-wedge is out of scope.** This tool covers parallel cluster-randomized trials. Stepped-wedge cluster trials need an additional time-trend domain and a modified Domain 2.6 — do not route them here.

---

## 10. Reporting-guideline companion — CONSORT cluster extension

Cluster-randomized trials should also be assessed against the **CONSORT extension to cluster randomised trials** (Campbell MK, Piaggio G, Elbourne DR, Altman DG. BMJ 2012; 345: e5661) in addition to the base CONSORT checklist. The extension items cover cluster-specific reporting: identification as a cluster-randomised trial; the rationale for using a cluster design; the definition of the cluster; eligibility criteria for clusters *and* for individual participants; whether the intervention and each outcome apply at the cluster or the individual level; a sample-size calculation accounting for the number of clusters, cluster size, and the intracluster correlation coefficient (ICC); clusters as the unit of randomisation (with sequence generation, allocation concealment, and who enrolled/assigned clusters); blinding at both levels; statistical methods that account for clustering; a flow diagram and counts at both the cluster and individual level; baseline data at both levels; numbers analysed and the estimated ICC for each primary outcome; and generalisability discussed for both clusters and individuals.

Implementation pattern: one LLM call per paper, asked to judge each combined (base + extension) item as adhered (`true`), not adhered (`false`), or not applicable (`null`). The proportion reported is `adhered / applicable` (the `null` items are excluded from both numerator and denominator).
