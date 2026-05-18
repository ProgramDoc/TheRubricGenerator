# Quality Appraisal AI — Framework Overview

*A shareable walkthrough of the architecture, prompts, and scoring logic behind the Quality Appraisal feature, written for AI engineers who want to understand the building blocks and adapt portions to their own systems.*

This document intentionally stops short of being a drop-in replication kit. It shows the prompts verbatim and the scoring algorithms as pseudocode/flowcharts, but omits the orchestration plumbing (database schema, background-thread runner, credit/billing hooks, the batch API, the PDF.js detail view, etc.) that constitute most of the real build.

---

## 1. What the system does

Given a single clinical research paper (PDF), the system produces a structured quality-of-evidence assessment:

1. **Classifies** the study design (RCT, cohort, meta-analysis, …).
2. **Extracts** structured fields (citation, population, outcomes, design modifiers).
3. **Picks the primary outcome** automatically from the extracted fields.
4. **Runs a validated risk-of-bias tool** for that study type (e.g., Cochrane **RoB 2** for RCTs).
5. **Runs a reporting-guideline checklist** (e.g., **CONSORT 2025** for RCTs).
6. **Computes a GRADE certainty level** (initial, based on study type; updated, after risk-of-bias).

v1 ships with RCT support only. The architecture is a registry pattern so new study-type-/tool-/guideline-tuples can be added as three-line entries plus two small modules.

---

## 2. Pipeline (per paper)

| Step | What it does | Cost |
|---|---|---|
| 0 | Load paper PDF | — |
| 1 | Classify study design (RCT / Cohort / …) | 1 LLM call (~3 credits) |
| 2 | Extract structured fields (universal + type-specific + design modifiers) | 1 LLM call (~8 credits) |
| 3 | Auto-pick primary outcome | Pure Python |
| 4 | Risk of bias (RoB 2) — one call per domain + Python decision trees | 5 LLM calls (~3 each) |
| 5 | Reporting-guideline checklist (CONSORT 2025) | 1 LLM call (~4 credits) |
| 6 | GRADE: initial → updated | Pure Python |

Total: ~8 LLM calls per paper. Per-paper credit cost is ~30 in our pricing model.

Failures and unsupported study types refund the per-paper charge and move on — batch isolation is enforced so one bad PDF never blocks the run.

---

## 3. Extensibility contract

The whole system is driven by a single registry table keyed by study type:

```python
STUDY_TYPE_REGISTRY = {
    "Randomized Controlled Trial": {
        "rob_tool":            "rob2",
        "reporting_guideline": "consort2025",
        "initial_grade":       "High",
    },
    # Future — not wired yet; classifier skips + refunds:
    # "Cohort Study":       {"rob_tool": "robins_i", "reporting_guideline": "strobe",     "initial_grade": "Low"},
    # "SR with Meta-Analysis": {"rob_tool": "amstar2", "reporting_guideline": "prisma2020", "initial_grade": "High"},
    # "Diagnostic Accuracy":   {"rob_tool": "quadas2", "reporting_guideline": "stard",       "initial_grade": "Low"},
}
```

Each tool and guideline lives in its own module and exposes two entrypoints:

```python
def run(pdf_bytes, extracted_fields, classification, primary_outcome=None, progress=None):
    """Execute the assessment. Returns a structured result dict."""

def prompt_catalog():
    """Return prompts + decision trees for the developer transparency view."""
```

Adding a new study type is: one registry entry, one `rob_tools/<name>.py`, one `reporting_guidelines/<name>.py`, one dispatcher registration. That's it.

Registry keys must match the classifier's study-type labels; we enforce that with a unit test.

---

## 4. LLM interaction pattern

All AI calls funnel through a single oversize-robust function (called `_call_with_pdf` in our code). Conceptually:

```
try:
    # Stage 1 — PDF as a document block (fastest; covers ~95% of papers)
    return llm(prompt, pdf=pdf_bytes)
except ContextWindow413:
    # Stage 2 — pypdf text extraction (image-heavy papers shrink ~4×)
    text = extract_text(pdf_bytes)
    try:
        return llm(prompt, text=text)
    except ContextWindow413:
        # Stage 3 — overlapping 300k-char chunks, parallel, merge JSON
        chunks = split(text, size=300_000, overlap=8_000)
        results = parallel_map(lambda c: llm(prompt, text=c), chunks, workers=4)
        return merge_first_nonempty(results)
```

Classification and schema-proposal calls short-circuit to first-chunk-only (chunking a single holistic output doesn't make sense). All error paths surface vendor-agnostic 4xx/5xx messages and auto-refund credits.

A hard 32 MB byte-size guard runs before we even base64-encode, since the underlying PDF-beta endpoint rejects larger payloads.

---

## 5. Risk of Bias — Cochrane RoB 2 (2019, parallel-group RCTs)

### 5.1 Design choice: LLM answers, Python judges

RoB 2 has five domains. Each domain has 3–7 *signaling questions* answered with `Y / PY / PN / N / NI` (yes / probably-yes / probably-no / no / no-information). The Cochrane cribsheet defines a fixed flowchart mapping signal answers to a domain judgement (`Low / Some concerns / High`).

Our split:

- The **LLM** answers signaling questions from the paper text (subjective, paper-dependent).
- **Pure Python** maps those answers to judgements (deterministic, auditable).

Keeping the flowcharts in code means the developer-transparency view can show the *exact* logic via `inspect.getsource` — reviewers can see there is no hidden LLM arbitration step.

### 5.2 System prompt (verbatim)

```
You are an evidence-synthesis methodologist assessing risk of bias in a
randomized trial using the Cochrane RoB 2 tool. Read the PDF carefully.
Answer each signaling question with one of: Y (yes), PY (probably yes),
PN (probably no), N (no), NI (no information). Provide a 1-2 sentence
rationale for each answer, quoting the paper where possible. Return ONLY
a valid JSON object — no preamble, no markdown fences.
```

### 5.3 Per-domain prompt template (verbatim)

One call per domain. The template below is filled in with the study type, the primary outcome (RoB 2 is outcome-specific), structured fields already extracted from the paper, and the domain's signaling questions + the verbatim cribsheet elaborations.

```
Assess **Domain {N} — {domain name}** for the study described in the attached PDF.

Study type: {study_type}
Outcome being assessed: {primary_outcome}

Context (fields already extracted from the paper):
{json of relevant extracted fields for this domain}

Signaling questions:

**{id}. {signaling question text}**
Elaboration: {cribsheet elaboration — verbatim}
Response options: Y/PY/PN/N/NI.

…{repeat per signaling question}…

Return a JSON object with exactly this shape:
{
  "{id}": "Y|PY|PN|N|NI",
  "{id}_rationale": "1-2 sentences quoting the paper",
  …
  "direction_of_bias": "NA|Favours experimental|Favours comparator|Towards null|Away from null|Unpredictable"
}

Answer N (or PN) when the paper gives enough information to rule out the
problem, and NI only when the paper is silent. Rationales must be short
(1-2 sentences) and quote the paper verbatim where possible.
```

Notes on this design:

- **Context injection.** For each domain, only the *relevant* extracted fields are passed in, not the whole extraction. That keeps the prompt focused and reminds the model what we already know (e.g., Domain 1 gets `randomization_method`, `allocation_concealment`, `baseline_balance`, …).
- **Outcome-specific.** RoB 2 is assessed per-outcome, so the primary outcome is always in the header. A future version with user-selectable outcomes would loop this step.
- **Direction of bias** is captured alongside each domain in case reviewers want to aggregate "which way does the bias point" across a body of evidence.

### 5.4 Decision trees (pseudocode)

The five domain algorithms are transcribed directly from the 2019 cribsheet. Below are the flowcharts in pseudocode — enough to understand the structure, not verbatim Python.

**Helpers**

```
yes(a) := a ∈ {Y, PY}
no(a)  := a ∈ {N, PN}
```

**Domain 1 — Randomization process** (cribsheet p.6)

```
if no(1.2):                       return High             # allocation not concealed
if 1.2 == NI:
    return High if yes(1.3) else Some concerns            # no info on concealment
# 1.2 is Y/PY
if no(1.1):                       return High             # sequence not random
# 1.1 is Y/PY/NI, 1.2 is Y/PY
return Some concerns if yes(1.3) else Low                 # 1.3 flags baseline imbalance
```

**Domain 2 — Deviations from intended interventions, effect of assignment** (cribsheet p.10)

Two parts combined:

```
# Part 1 — awareness chain (questions 2.1-2.5)
aware := not (no(2.1) and no(2.2))
if not aware:
    part1 = Low
elif no(2.3) or 2.3 == NI:
    part1 = Some concerns
elif no(2.4):
    part1 = Some concerns
else:
    part1 = High if not yes(2.5) else Some concerns       # 2.5 = deviations balanced?

# Part 2 — analysis chain (questions 2.6-2.7)
if yes(2.6):
    part2 = Low
else:
    part2 = Some concerns if no(2.7) else High

# Combine
if part1 == High or part2 == High:       return High
if part1 == Low  and part2 == Low:       return Low
return Some concerns
```

**Domain 3 — Missing outcome data** (cribsheet p.16)

```
if yes(3.1):                               return Low    # data for (nearly) all
if yes(3.2):                               return Low    # evidence result not biased
if no(3.3):                                return Low    # missingness can't depend on true value
# 3.3 is Y/PY/NI → 3.4 likely depended?
if yes(3.4) or 3.4 == NI:                  return High
return Some concerns
```

**Domain 4 — Measurement of the outcome** (cribsheet p.19)

```
if yes(4.1):                               return High   # inappropriate method
if yes(4.2):                               return High   # measurement differs between groups
# Aware-of-assignment chain
base = {
    if no(4.3):                            → Low
    elif no(4.4):                          → Low
    elif yes(4.5) or 4.5 == NI:            → High
    else:                                  → Some concerns
}
# NI on 4.2 downgrades a Low to Some concerns
if 4.2 == NI and base == Low:
    return Some concerns
return base
```

**Domain 5 — Selection of the reported result** (cribsheet p.23)

```
if yes(5.2) or yes(5.3):                   return High   # cherry-picked measurement or analysis
if no(5.2) and no(5.3):
    return Low if yes(5.1) else Some concerns            # pre-specified analysis plan?
return Some concerns                                     # at least one NI
```

**Overall RoB** (cribsheet p.24)

```
if any(domain == High):                    return High
if count(domain == Some concerns) >= 2:    return High   # ≥2 "somes" aggregate to High
if count(domain == Some concerns) >= 1:    return Some concerns
return Low
```

**Overall direction of bias** (our aggregation, not from the cribsheet): the single most-common non-NA direction across the five domains; `Unpredictable` on ties; `NA` if every domain says NA.

### 5.5 Signaling questions at a glance

Domain count is fixed by the tool: 5 domains, 22 signaling questions (3 + 7 + 4 + 5 + 3). Question wording and elaborations are transcribed verbatim from `20190814_RoB_2.0_cribsheet_parallel_trial.pdf` and sent to the LLM unchanged so it sees the same guidance a human reviewer would.

A taste (Domain 1 only):

| # | Question | Gist of the elaboration |
|---|---|---|
| 1.1 | Was the allocation sequence random? | Random-number tables/computers/coin flips count; alternation/DOB/record-# do not; "randomized" alone = NI |
| 1.2 | Was the allocation sequence concealed until assignment? | Central pharmacy / sequentially-numbered tamper-sealed opaque envelopes count |
| 1.3 | Did baseline differences suggest a problem with randomization? | Chance imbalance is fine; flag substantial imbalance in prognostic factors |

The remaining four domains follow the same pattern; the full text is shipped in the developer-view endpoint so any engineer on a reviewing team can audit what the LLM sees.

---

## 6. Reporting guideline — CONSORT 2025

CONSORT 2025 is a 30-item checklist. Unlike RoB 2, we make a **single LLM call** covering all items — they're independent and each is a short yes/no with evidence, so there's no scoring logic that needs to be isolated from the model.

### 6.1 System prompt (verbatim)

```
You are an evidence-synthesis methodologist assessing adherence of a
randomised trial report to the CONSORT 2025 checklist. Read the PDF
carefully. For each checklist item, decide whether the trial report
reports the required information. Be strict but fair: an item is adhered
only if the information is actually present (not merely referenced as
'available elsewhere' unless the paper provides a usable pointer).
If an item is genuinely not applicable to this trial, mark it N/A.
Return ONLY a valid JSON object — no preamble, no markdown fences.
```

### 6.2 User prompt template (verbatim)

```
Assess this **{study_type}** report against the CONSORT 2025 checklist.

Context (fields already extracted from the paper):
{json of extracted fields}

CONSORT 2025 items:
- **1a** (Title and abstract — Title and structured abstract): Identification as a randomised trial
- **1b** (Title and abstract — Title and structured abstract): Structured summary of the trial design, methods, results, and conclusions
- **2**  (Open science — Trial registration): Name of trial registry, identifying number (with URL) and date of registration
- …{30 items total}…
- **30** (Discussion — Limitations): Trial limitations, addressing sources of potential bias, imprecision, generalisability, and, if relevant, multiplicity of analyses

For each item, return:
- ``adhered = true`` if the paper reports the required information,
- ``adhered = false`` if the paper should report it but does not,
- ``adhered = null`` if the item is legitimately not applicable to this trial
  (e.g., sub-item 12b "eligibility for sites/deliverers" if there is only one
  site and no special deliverer criteria; 16b interim analyses if none were
  performed).
- ``evidence`` is a brief quote (≤ 25 words) from the paper, or a one-line
  reason for a false/null judgement.

Return a JSON object with exactly this shape:
{
  "1a": {"adhered": true|false|null, "evidence": "short quote or … 'N/A' if not applicable"},
  "1b": {"adhered": true|false|null, "evidence": "…"},
  …
  "30": {"adhered": true|false|null, "evidence": "…"}
}

Return only the JSON object.
```

### 6.3 Scoring (pseudocode)

```
# Inputs: 30 item judgements from the LLM, each {adhered: true|false|null, evidence: str}
applicable = [it for it in items if it.adhered is not None]
adhered    = count(it.adhered == true for it in applicable)
proportion = adhered / len(applicable) if applicable else 0.0

return {
    items:      items,
    adhered:    adhered,
    applicable: len(applicable),
    total:      30,
    proportion: proportion,
}
```

Not-applicable items are excluded from both numerator and denominator so they don't inflate or deflate the proportion (e.g., 16b interim analyses for a trial that had none). Non-boolean returns from the LLM are coerced defensively (`"yes"`/`"true"`/`"1"` → `True`, `"na"`/`"none"` → `None`, everything else → `None`).

The 30-item list itself is transcribed from: *Hopewell S et al. "CONSORT 2025 Statement: updated guideline for reporting randomised trials." BMJ 2025; 388:e081123* (https://dx.doi.org/10.1136/bmj-2024-081123).

---

## 7. GRADE certainty

GRADE has five domains in full: risk of bias, inconsistency, indirectness, imprecision, publication bias. Four of them require a *body of evidence* (heterogeneity between studies, wide vs narrow confidence intervals across studies, funnel-plot asymmetry) and so don't apply to a single-study assessment.

**v1 downgrades for risk of bias only.** The developer view states this explicitly; it's a deliberate scope cut.

### 7.1 Initial GRADE

Assigned by study type from the registry. RCTs start at **High**; observational designs start at **Low** (standard GRADE convention).

### 7.2 Downgrade rule (pseudocode)

```
levels = [High, Moderate, Low, Very low]
i      = index(initial)

if rob_overall == Low:
    return initial, "No downgrade: overall risk of bias is Low."
if rob_overall == Some concerns:
    return levels[min(i+1, 3)], "Downgraded 1 level for Some concerns in risk of bias."
# rob_overall == High
high_domains = count(d == High for d in rob_domain_judgements)
if high_domains >= 2:
    return levels[min(i+2, 3)], f"Downgraded 2 levels for High risk of bias in {high_domains} domains."
return levels[min(i+1, 3)], "Downgraded 1 level for High risk of bias."
```

The 2-level downgrade for multiple high-RoB domains follows GradePro guidance. The explanation string is surfaced in the UI so reviewers see *why* certainty moved.

---

## 8. Primary outcome picker

Pure Python. Reads the extracted fields in this preference order and truncates to the first sentence or 200 chars so downstream prompts stay compact:

```
PREFERENCE_ORDER = [
    "primary_outcome_definition",
    "primary_outcome_measurement",
    "population_outcomes",
]

for key in PREFERENCE_ORDER:
    val = fields.get(key, "").strip()
    if val:
        return first_sentence_or_200_chars(val)
return "(primary outcome not specified in the extracted fields)"
```

A future version would let the user select the outcome from a dropdown (e.g., for multi-outcome trials); v1 auto-picks so single-click runs work.

---

## 9. Transparency principles

This feature is built on the idea that **reviewers should be able to audit every judgement**. Concretely:

- Every prompt, every signaling question, every elaboration paragraph, every decision tree, and the GRADE logic are exposed via a single developer-view endpoint that is available to every signed-in user (not hidden behind an admin flag).
- The decision trees are in Python, not in prompts. We want determinism and visibility, not LLM-improvised arbitration between a `Some concerns` and a `Low`.
- Domain judgements show both the LLM's signal answers *and* the rationales (with quotes), so a reviewer can disagree with an individual answer without having to re-read the whole paper.
- CONSORT evidence chips are short verbatim quotes. In the detail view we attempt to highlight them inside the original PDF, acknowledging in the UI when the quote is paraphrased and can't be exactly matched.

---

## 10. What this doc deliberately leaves out

So readers aren't misled into thinking this is a drop-in replication kit:

- The **database schema** (three tables: runs, results, events) and migration strategy.
- The **batch orchestrator**: per-paper isolation, credit pre-charge, per-paper auto-refund on error/skip, event logging, background-thread execution for larger batches.
- The **endpoint surface**: ~8 REST routes (create run, get run, incremental-poll events, CSV/XLSX export, delete, developer-view), with role-based access gating.
- The **frontend**: results grid, clickable detail modal with PDF.js viewer and quote-highlighting.
- The **oversize-PDF pipeline** details: byte-size caps, pypdf fallback, chunked map-reduce with overlapping windows and parallel execution, first-non-empty merge.
- Integration with upstream systems (classifier, field extractor, paper storage, auth, billing) — each of which is its own sub-system.

Those pieces are the bulk of the engineering effort; the prompts and decision trees above are the scientific substance but a small fraction of the code.

---

## 11. One-line summary for your teammates

> *Quality Appraisal AI = classifier + structured extractor + (tool-specific LLM-answered signaling questions → Python decision tree → domain judgement) + (single-call reporting-guideline checklist) + (Python GRADE downgrade for risk of bias). All prompts and trees are exposed through a transparency endpoint so reviewers can audit every judgement.*
