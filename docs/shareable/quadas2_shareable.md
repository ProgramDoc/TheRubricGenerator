# QUADAS-2  — Sharable Methodology Reference

A self-contained reference for implementing an automated QUADAS-2 risk-of-bias + applicability assessment of diagnostic test accuracy studies. Contains:

- Signaling questions (verbatim from the 2011 paper) for all 4 domains
- Decision-tree logic as plain Python (no framework / database / HTTP dependencies)
- LLM prompt templates (the exact strings sent to the model)
- Expected JSON output shape
- Overall RoB + applicability aggregation algorithms
- A turnkey single-file reference implementation

**Sources:**

- Whiting PF, Rutjes AWS, Westwood ME, Mallett S, Deeks JJ, Reitsma JB, Leeflang MMG, Sterne JAC, Bossuyt PMM, and the QUADAS-2 Group. *QUADAS-2: A Revised Tool for the Quality Assessment of Diagnostic Accuracy Studies.* Ann Intern Med. 2011;155:529-536.

**Scope:** the QUADAS-2 tool (Whiting 2011) — risk of bias + applicability for primary diagnostic test accuracy studies. Out of scope: QUADAS-3 v1.2 (Yang 2021, a different 5-level scale), QUADAS-C (comparative accuracy), and the STARD 2015 reporting checklist (typically run alongside QUADAS-2 but is a separate companion tool).

**Conservative-tree note.** The QUADAS-2 paper narratively allows reviewers to keep a domain at Low even with a single "No" if the No is judged immaterial. The decision tree below takes the conservative interpretation (any N → High) to keep the logic pure and inspectable.

---

## 1. Signal answer options

Every signaling question accepts one of three answers:

```python
SIGNAL_OPTIONS = ("Y", "N", "U")
# Y = Yes
# N = No
# U = Unclear
```

(QUADAS-3 v1.2 uses a 5-level Y/PY/PN/N/NI scale; QUADAS-2 stays with the classic 3-level scale from Whiting 2011.)

Helper functions used by the decision tree:

```python
def _yes(ans: str) -> bool:
    return ans == "Y"


def _no(ans: str) -> bool:
    return ans == "N"
```

Domain RoB judgements are 3-level: `"Low"` / `"High"` / `"Unclear"`.
Applicability judgements are 3-level: `"Low"` / `"High"` / `"Unclear"`.

```python
JUDGEMENTS = ("Low", "High", "Unclear")
APPLICABILITY_OPTIONS = ("Low", "High", "Unclear")
```

---

## 2. Domain definitions

Domains are processed in the order specified by Whiting 2011 Table 1:

| Order | ID | Name               | Applicability? | # signaling questions |
| ----- | -- | ------------------ | -------------- | --------------------- |
| 1     | 1  | Patient Selection  | Yes            | 3                     |
| 2     | 2  | Index Test         | Yes            | 2                     |
| 3     | 3  | Reference Standard | Yes            | 2                     |
| 4     | 4  | Flow and Timing    | No (RoB-only)  | 4                     |

All 4 domains share the same pure-Python decision tree (`quadas2_domain_judge` — see end of this section).

### 2.1 Domain 1 — Patient Selection

**Applicability question:** Are there concerns that the included patients and setting do not match the review question?

*Elaboration:* Concerns about applicability may exist if patients included in the study differ from those targeted by the review question in terms of severity of the target condition, demographic features, presence of differential diagnosis or comorbid conditions, setting of the study, and previous testing protocols.

Signaling questions:

- **1.1** Was a consecutive or random sample of patients enrolled?

  *Elaboration:* A study should ideally enrol a consecutive or random sample of eligible patients with suspected disease to prevent the potential for bias. Convenience samples or selection on test-related criteria → 'No'.

- **1.2** Was a case-control design avoided?

  *Elaboration:* Studies enrolling participants with known disease and a separate control group without the condition may exaggerate diagnostic accuracy (spectrum bias). Answer 'Yes' for single-gate (cohort) designs; 'No' for case-control / multi-gate designs.

- **1.3** Did the study avoid inappropriate exclusions?

  *Elaboration:* Studies that make inappropriate exclusions (e.g. not including 'difficult-to-diagnose' patients, or excluding patients with 'red flags' for the target condition who may be easier to diagnose) may over- or underestimate diagnostic accuracy. 'No' if exclusions are likely to have distorted the spectrum.

Response options: Y/N/U for each signaling question.

### 2.2 Domain 2 — Index Test

**Applicability question:** Are there concerns that the index test, its conduct, or its interpretation differ from the review question?

*Elaboration:* Variations in test technology, execution, or interpretation may affect estimates of the diagnostic accuracy of a test. If index test methods vary from those specified in the review question, concerns about applicability may exist.

Signaling questions:

- **2.1** Were the index test results interpreted without knowledge of the results of the reference standard?

  *Elaboration:* Knowledge of the reference standard may influence interpretation of index test results (review bias). If the index test is always conducted and interpreted before the reference standard, this item can be rated 'Yes'.

- **2.2** If a threshold was used, was it pre-specified?

  *Elaboration:* Selecting the test threshold to optimize sensitivity and/or specificity post-hoc may lead to overestimation of test performance. Test performance is likely to be poorer in an independent sample of patients in whom the same threshold is used. Mark 'Unclear' if no threshold was used (e.g. continuous test reported as AUC only).

Response options: Y/N/U for each signaling question.

### 2.3 Domain 3 — Reference Standard

**Applicability question:** Are there concerns that the target condition as defined by the reference standard does not match the review question?

*Elaboration:* The reference standard may be free of bias, but the target condition that it defines may differ from the target condition specified in the review question. For example, when defining urinary tract infection, the reference standard is generally based on specimen culture; however, the threshold above which a result is considered positive may vary.

Signaling questions:

- **3.1** Is the reference standard likely to correctly classify the target condition?

  *Elaboration:* Estimates of test accuracy are based on the assumptions that the reference standard is 100% sensitive and that any specific disagreements between the reference standard and index test result from incorrect classification by the index test. 'No' if the reference standard is known to be inaccurate or substantially different from the accepted diagnostic criterion.

- **3.2** Were the reference standard results interpreted without knowledge of the results of the index test?

  *Elaboration:* Potential for bias is related to the potential influence of previous knowledge of the index test result on the interpretation of the reference standard.

Response options: Y/N/U for each signaling question.

### 2.4 Domain 4 — Flow and Timing

Domain 4 is **RoB-only** — there is no applicability assessment for flow and timing.

Signaling questions:

- **4.1** Was there an appropriate interval between the index test and reference standard?

  *Elaboration:* Results of the index test and reference standard are ideally collected on the same patients at the same time. If a delay occurs or if treatment begins between the index test and the reference standard, recovery or deterioration of the condition may cause misclassification. The appropriate interval is condition-specific (hours for stroke, weeks for a slow-growing tumour).

- **4.2** Did all patients receive a reference standard?

  *Elaboration:* Partial verification — applying the reference standard only to a subset (e.g. index-positive participants) — biases sensitivity and specificity estimates. 'No' if verification was selective.

- **4.3** Did all patients receive the same reference standard?

  *Elaboration:* Differential verification — different reference standards for index-positive vs index-negative patients — introduces bias. 'No' if multiple reference standards were used non-randomly.

- **4.4** Were all patients included in the analysis?

  *Elaboration:* All patients recruited into the study should be included in the analysis. A potential for bias exists if the number of patients enrolled differs from the number of patients included in the 2×2 table of results, because patients lost to follow-up differ systematically from those who remain.

Response options: Y/N/U for each signaling question.

### 2.5 Domain decision tree (shared across all 4 domains)

Per Whiting 2011 Phase 4 ("If all signaling questions are 'yes' then risk of bias can be judged 'low'. If any signaling question is answered 'no', potential for bias exists."):

```python
def quadas2_domain_judge(signals: dict[str, str]) -> str:
    """Map signaling-question answers (Y/N/U) to a domain-level RoB
    judgement (Low / High / Unclear) per the QUADAS-2 Phase 4 narrative.

    Rule (conservative):
      - All signals Y → "Low"
      - Any N → "High"
      - Any U without N → "Unclear"
      - Empty / all-U → "Unclear"
    """
    answered = [v for v in signals.values()]
    if not answered:
        return "Unclear"
    if any(_no(v) for v in answered):
        return "High"
    if all(_yes(v) for v in answered):
        return "Low"
    return "Unclear"
```

All four domains use this same function (via a dispatch table):

```python
DOMAIN_JUDGES = {
    1: quadas2_domain_judge,
    2: quadas2_domain_judge,
    3: quadas2_domain_judge,
    4: quadas2_domain_judge,
}
```

---

## 3. Overall aggregation

### 3.1 Overall risk of bias (4 domains)

Per Whiting 2011 "Incorporating Assessments" section ("If a study is judged 'low' on all domains relating to bias …, then it is appropriate to have an overall judgment of 'low risk of bias' … If a study is judged 'high' or 'unclear' in 1 or more domains, then it may be judged 'at risk of bias' or as having 'concerns regarding applicability'."):

```python
def quadas2_overall(domain_judgements: list[str]) -> str:
    """Aggregate per the QUADAS-2 paper ("Incorporating Assessments" section).

    - Any domain High → "High"
    - All domains Low → "Low"
    - Otherwise (any Unclear, none High) → "Unclear"
    """
    if not domain_judgements:
        return "Unclear"
    if any(j == "High" for j in domain_judgements):
        return "High"
    if all(j == "Low" for j in domain_judgements):
        return "Low"
    return "Unclear"
```

### 3.2 Overall applicability concern (3 domains)

Same rule as RoB but applied only to the 3 domains that carry applicability (Patient Selection, Index Test, Reference Standard — D4 Flow and Timing is excluded):

```python
def quadas2_applicability_overall(judgements: list[str]) -> str:
    """Aggregate applicability judgements per the QUADAS-2 paper (same rule
    as RoB). Only 3 domains carry applicability (Patient Selection, Index
    Test, Reference Standard); Flow and Timing is excluded from the input
    list.
    """
    return quadas2_overall(judgements)
```

---

## 4. LLM prompt templates

QUADAS-2 is implemented as **one LLM call per domain** (4 calls per paper). Each call returns BOTH signal answers (RoB) AND, where applicable, an applicability concern, in a single JSON response.

### 4.1 System prompt (fixed)

```text
You are an evidence-synthesis methodologist assessing a diagnostic test accuracy study using the QUADAS-2 tool (Whiting et al., 2011, Ann Intern Med). For each domain, read the PDF carefully and answer the signaling questions with one of: Y (yes), N (no), U (unclear). When the domain has an applicability assessment, also rate concern that the as-conducted study matches the review question (PIRT: Patient population / Index test / Reference standard / Target condition) as: Low / High / Unclear. Provide a short rationale (1-2 sentences, quoting the paper where possible) for every answer. Return ONLY a valid JSON object — no preamble, no markdown fences.
```

### 4.2 Per-domain user prompt template

The user prompt is assembled from five sub-blocks: the estimate descriptor, the pre-extracted context, the signaling questions, the (optional) applicability block, and the expected JSON shape.

```text
Assess **Domain {domain_id} — {domain_name}** of QUADAS-2 (Whiting 2011) for the diagnostic test accuracy study described in the attached PDF.

Study type: {study_type}
Primary outcome (target condition): {primary_outcome}

Estimate being assessed:
{estimate_block}

Context (fields already extracted from the paper):
{ctx_json}

Signaling questions:
{questions_block}{applicability_block}

Return a JSON object with exactly this shape:
{shape}

Answer N only when the paper gives enough information to rule out adherence; answer U only when the paper is silent or the information is ambiguous. Rationales must be short (1-2 sentences) and quote the paper verbatim where possible.
```

#### 4.2a Estimate-block format

Renders the Phase-4 estimate descriptor (when per-estimate iteration is in use). Falls back to a single-estimate placeholder otherwise.

```python
def _format_estimate_block(estimate: dict | None) -> str:
    """Render the estimate context block for the prompt header. Empty when
    no estimate was supplied (single-estimate fallback)."""
    if not estimate:
        return "(assessment is for the paper's primary / headline accuracy estimate)"
    parts = []
    for key in ("description", "subgroup", "index_test", "threshold",
                "reference_standard", "unit_of_analysis", "sensitivity",
                "specificity", "n"):
        val = estimate.get(key)
        if val:
            parts.append(f"- {key.replace('_', ' ').title()}: {val}")
    return "\n".join(parts) if parts else "(assessment is for an estimate but no descriptor fields were supplied)"
```

#### 4.2b Review-context format (used inside the applicability block)

```python
def _format_review_context(review_context: str | None) -> str:
    """Render the review-level context (PIRT review question) for the
    prompt header. Empty when not supplied — the LLM falls back to a
    generic intended-use baseline."""
    if not review_context or not review_context.strip():
        return (
            "(no review question supplied — judge applicability against the "
            "generic 'intended-use population' implied by the paper)"
        )
    return review_context.strip()
```

#### 4.2c Signaling-questions block

Renders the per-signal question text + elaboration + response options:

```python
q_lines = []
for sig in domain["signals"]:
    q_lines.append(
        f"\n**{sig['id']}. {sig['text']}**\n"
        f"Elaboration: {sig['elaboration']}\n"
        f"Response options: {'/'.join(sig['options'])}."
    )
questions_block = "\n".join(q_lines)
```

#### 4.2d Applicability block (domains 1–3 only)

```python
applicability_block = ""
if domain["has_applicability"]:
    applicability_block = (
        "\n\n**Applicability assessment** (rate as Low / High / Unclear):\n"
        f"{domain['applicability_question']}\n"
        f"Elaboration: {domain['applicability_elaboration']}\n"
        "\n**Review question** (PIRT — use this to judge applicability):\n"
        f"{_format_review_context(review_context)}"
    )
```

For D4 (Flow and Timing) `applicability_block` is the empty string.

#### 4.2e JSON-shape generator

The expected JSON shape is built dynamically per domain so the LLM sees the exact keys it must return:

```python
shape_lines = ["{"]
for sig in domain["signals"]:
    shape_lines.append(f'  "{sig["id"]}": "Y|N|U",')
    shape_lines.append(f'  "{sig["id"]}_rationale": "1-2 sentences quoting the paper",')
if domain["has_applicability"]:
    shape_lines.append('  "applicability_judgement": "Low|High|Unclear",')
    shape_lines.append('  "applicability_rationale": "1-2 sentences explaining the concern relative to the review question"')
else:
    if shape_lines[-1].endswith(","):
        shape_lines[-1] = shape_lines[-1][:-1]
shape_lines.append("}")
shape = "\n".join(shape_lines)
```

---

## 5. Expected JSON output shape

### 5.1 Per-domain LLM response — with applicability (D1, D2, D3)

Example for Domain 1 (3 signaling questions + applicability):

```json
{
  "1.1": "Y|N|U",
  "1.1_rationale": "1-2 sentences quoting the paper",
  "1.2": "Y|N|U",
  "1.2_rationale": "1-2 sentences quoting the paper",
  "1.3": "Y|N|U",
  "1.3_rationale": "1-2 sentences quoting the paper",
  "applicability_judgement": "Low|High|Unclear",
  "applicability_rationale": "1-2 sentences explaining the concern relative to the review question"
}
```

### 5.2 Per-domain LLM response — RoB-only (D4 Flow and Timing)

```json
{
  "4.1": "Y|N|U",
  "4.1_rationale": "1-2 sentences quoting the paper",
  "4.2": "Y|N|U",
  "4.2_rationale": "1-2 sentences quoting the paper",
  "4.3": "Y|N|U",
  "4.3_rationale": "1-2 sentences quoting the paper",
  "4.4": "Y|N|U",
  "4.4_rationale": "1-2 sentences quoting the paper"
}
```

### 5.3 Final per-paper result (after running all 4 domains + aggregation)

The `run(...)` function returns a 4-tuple `(domain_results, overall_rob, overall_direction, overall_applicability)`:

```json
{
  "domain_results": {
    "1": {
      "id": 1,
      "name": "Patient Selection",
      "has_applicability": true,
      "signals": {"1.1": "Y", "1.2": "Y", "1.3": "N"},
      "rationales": {
        "1.1": "Quote from paper...",
        "1.2": "Quote from paper...",
        "1.3": "Quote from paper..."
      },
      "judgement": "High",
      "applicability_judgement": "Low",
      "applicability_rationale": "Patient spectrum matches the intended-use population..."
    },
    "2": { "...": "..." },
    "3": { "...": "..." },
    "4": {
      "id": 4,
      "name": "Flow and Timing",
      "has_applicability": false,
      "signals": {"4.1": "Y", "4.2": "Y", "4.3": "Y", "4.4": "U"},
      "rationales": {"4.1": "...", "4.2": "...", "4.3": "...", "4.4": "..."},
      "judgement": "Unclear"
    }
  },
  "overall_rob": "High",
  "overall_direction": "NA",
  "overall_applicability": "Low"
}
```

Notes:
- `overall_direction` is **always `"NA"`** for diagnostic accuracy — direction-of-effect is a treatment-trial concept and does not apply.
- `overall_applicability` aggregates over the 3 applicability-bearing domains only (D1, D2, D3); D4 is excluded.

---

## 6. Sample data — pre-extracted fields

Each domain prompt receives a context block (`ctx_json`) containing a subset of fields already extracted from the paper by an upstream "annotator" stage. The fields surfaced per domain are:

| Domain                 | Relevant pre-extracted fields                                                                |
| ---------------------- | -------------------------------------------------------------------------------------------- |
| 1 — Patient Selection  | `spectrum_of_patients`, `verification_bias`, `flow_and_timing`, `population_inclusion`, `population_exclusion` |
| 2 — Index Test         | `index_test`, `blinding_index_to_reference`, `threshold_effects`                             |
| 3 — Reference Standard | `reference_standard`, `blinding_reference_to_index`, `flow_and_timing`                       |
| 4 — Flow and Timing    | `flow_and_timing`, `verification_bias`, `two_by_two_table`                                   |

The fields are pre-extracted because giving the LLM both (a) the full PDF and (b) a JSON summary of the methodologically relevant sections noticeably improves grounding — the model can quote either source.

Any subset of these fields may be empty for a given paper; the helper just skips missing keys:

```python
relevant = {k: extracted_fields[k]
            for k in domain["relevant_fields"] if extracted_fields.get(k)}
ctx_json = json.dumps(relevant, indent=2) if relevant else "(no pre-extracted fields)"
```

If you don't have an annotator pipeline, you can pass an empty dict (`extracted_fields = {}`) and the prompts will simply contain `"(no pre-extracted fields)"` in the context block — the PDF itself still carries the evidence.

---

## 7. Reference implementation — single self-contained Python module

The module below is a turnkey adaptation: copy it into your project, supply your own LLM adapter via the `llm_call` parameter, and call `run(...)`. No project-specific imports.

The `llm_call` callable has the signature:

```python
llm_call(pdf_bytes: bytes, prompt: str, max_tokens: int) -> dict
```

It must send `pdf_bytes` + `prompt` to a vision-capable LLM PDF and return the parsed JSON response as a Python dict. Error handling, retries, and JSON-fence stripping are your concern.

```python
"""QUADAS-2 (2011) — Risk of bias + applicability for diagnostic test
accuracy studies. Single-file reference implementation.

Source: Whiting PF, Rutjes AWS, Westwood ME, Mallett S, Deeks JJ, Reitsma JB,
Leeflang MMG, Sterne JAC, Bossuyt PMM, and the QUADAS-2 Group.
"QUADAS-2: A Revised Tool for the Quality Assessment of Diagnostic Accuracy
Studies." Ann Intern Med. 2011;155:529-536.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Scales
# ─────────────────────────────────────────────
SIGNAL_OPTIONS = ("Y", "N", "U")
JUDGEMENTS = ("Low", "High", "Unclear")
APPLICABILITY_OPTIONS = ("Low", "High", "Unclear")


# ─────────────────────────────────────────────
# Decision trees (pure Python — no LLM)
# ─────────────────────────────────────────────
def _yes(ans: str) -> bool:
    return ans == "Y"


def _no(ans: str) -> bool:
    return ans == "N"


def quadas2_domain_judge(signals: dict[str, str]) -> str:
    """Map signaling-question answers (Y/N/U) to a domain-level RoB
    judgement (Low / High / Unclear) per the QUADAS-2 Phase 4 narrative.

    Rule (conservative):
      - All signals Y → "Low"
      - Any N → "High"
      - Any U without N → "Unclear"
      - Empty / all-U → "Unclear"
    """
    answered = [v for v in signals.values()]
    if not answered:
        return "Unclear"
    if any(_no(v) for v in answered):
        return "High"
    if all(_yes(v) for v in answered):
        return "Low"
    return "Unclear"


def quadas2_overall(domain_judgements: list[str]) -> str:
    """Aggregate per the QUADAS-2 paper ("Incorporating Assessments" section).

    - Any domain High → "High"
    - All domains Low → "Low"
    - Otherwise (any Unclear, none High) → "Unclear"
    """
    if not domain_judgements:
        return "Unclear"
    if any(j == "High" for j in domain_judgements):
        return "High"
    if all(j == "Low" for j in domain_judgements):
        return "Low"
    return "Unclear"


def quadas2_applicability_overall(judgements: list[str]) -> str:
    """Aggregate applicability judgements (same rule as RoB).

    Only 3 domains carry applicability (Patient Selection, Index Test,
    Reference Standard); Flow and Timing is excluded from the input list.
    """
    return quadas2_overall(judgements)


DOMAIN_JUDGES: dict[int, Callable[[dict[str, str]], str]] = {
    1: quadas2_domain_judge,
    2: quadas2_domain_judge,
    3: quadas2_domain_judge,
    4: quadas2_domain_judge,
}


# ─────────────────────────────────────────────
# Domain definitions — signaling questions transcribed verbatim from
# QUADAS-2 (Whiting 2011, Table 1 + section-by-section narrative)
# ─────────────────────────────────────────────
DOMAINS: list[dict[str, Any]] = [
    {
        "id": 1,
        "name": "Patient Selection",
        "has_applicability": True,
        "applicability_question": (
            "Are there concerns that the included patients and setting do "
            "not match the review question?"
        ),
        "applicability_elaboration": (
            "Concerns about applicability may exist if patients included in "
            "the study differ from those targeted by the review question in "
            "terms of severity of the target condition, demographic features, "
            "presence of differential diagnosis or comorbid conditions, "
            "setting of the study, and previous testing protocols."
        ),
        "relevant_fields": [
            "spectrum_of_patients", "verification_bias", "flow_and_timing",
            "population_inclusion", "population_exclusion",
        ],
        "signals": [
            {
                "id": "1.1",
                "text": "Was a consecutive or random sample of patients enrolled?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "A study should ideally enrol a consecutive or random "
                    "sample of eligible patients with suspected disease to "
                    "prevent the potential for bias. Convenience samples or "
                    "selection on test-related criteria → 'No'."
                ),
            },
            {
                "id": "1.2",
                "text": "Was a case-control design avoided?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Studies enrolling participants with known disease and a "
                    "separate control group without the condition may "
                    "exaggerate diagnostic accuracy (spectrum bias). Answer "
                    "'Yes' for single-gate (cohort) designs; 'No' for "
                    "case-control / multi-gate designs."
                ),
            },
            {
                "id": "1.3",
                "text": "Did the study avoid inappropriate exclusions?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Studies that make inappropriate exclusions (e.g. not "
                    "including 'difficult-to-diagnose' patients, or excluding "
                    "patients with 'red flags' for the target condition who "
                    "may be easier to diagnose) may over- or underestimate "
                    "diagnostic accuracy. 'No' if exclusions are likely to "
                    "have distorted the spectrum."
                ),
            },
        ],
    },
    {
        "id": 2,
        "name": "Index Test",
        "has_applicability": True,
        "applicability_question": (
            "Are there concerns that the index test, its conduct, or its "
            "interpretation differ from the review question?"
        ),
        "applicability_elaboration": (
            "Variations in test technology, execution, or interpretation may "
            "affect estimates of the diagnostic accuracy of a test. If index "
            "test methods vary from those specified in the review question, "
            "concerns about applicability may exist."
        ),
        "relevant_fields": [
            "index_test", "blinding_index_to_reference", "threshold_effects",
        ],
        "signals": [
            {
                "id": "2.1",
                "text": "Were the index test results interpreted without knowledge of the results of the reference standard?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Knowledge of the reference standard may influence "
                    "interpretation of index test results (review bias). If "
                    "the index test is always conducted and interpreted "
                    "before the reference standard, this item can be rated "
                    "'Yes'."
                ),
            },
            {
                "id": "2.2",
                "text": "If a threshold was used, was it pre-specified?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Selecting the test threshold to optimize sensitivity "
                    "and/or specificity post-hoc may lead to overestimation "
                    "of test performance. Test performance is likely to be "
                    "poorer in an independent sample of patients in whom the "
                    "same threshold is used. Mark 'Unclear' if no threshold "
                    "was used (e.g. continuous test reported as AUC only)."
                ),
            },
        ],
    },
    {
        "id": 3,
        "name": "Reference Standard",
        "has_applicability": True,
        "applicability_question": (
            "Are there concerns that the target condition as defined by the "
            "reference standard does not match the review question?"
        ),
        "applicability_elaboration": (
            "The reference standard may be free of bias, but the target "
            "condition that it defines may differ from the target condition "
            "specified in the review question. For example, when defining "
            "urinary tract infection, the reference standard is generally "
            "based on specimen culture; however, the threshold above which a "
            "result is considered positive may vary."
        ),
        "relevant_fields": [
            "reference_standard", "blinding_reference_to_index",
            "flow_and_timing",
        ],
        "signals": [
            {
                "id": "3.1",
                "text": "Is the reference standard likely to correctly classify the target condition?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Estimates of test accuracy are based on the assumptions "
                    "that the reference standard is 100% sensitive and that "
                    "any specific disagreements between the reference "
                    "standard and index test result from incorrect "
                    "classification by the index test. 'No' if the reference "
                    "standard is known to be inaccurate or substantially "
                    "different from the accepted diagnostic criterion."
                ),
            },
            {
                "id": "3.2",
                "text": "Were the reference standard results interpreted without knowledge of the results of the index test?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Potential for bias is related to the potential influence "
                    "of previous knowledge of the index test result on the "
                    "interpretation of the reference standard."
                ),
            },
        ],
    },
    {
        "id": 4,
        "name": "Flow and Timing",
        "has_applicability": False,
        "applicability_question": None,
        "applicability_elaboration": None,
        "relevant_fields": [
            "flow_and_timing", "verification_bias", "two_by_two_table",
        ],
        "signals": [
            {
                "id": "4.1",
                "text": "Was there an appropriate interval between the index test and reference standard?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Results of the index test and reference standard are "
                    "ideally collected on the same patients at the same time. "
                    "If a delay occurs or if treatment begins between the "
                    "index test and the reference standard, recovery or "
                    "deterioration of the condition may cause "
                    "misclassification. The appropriate interval is "
                    "condition-specific (hours for stroke, weeks for a "
                    "slow-growing tumour)."
                ),
            },
            {
                "id": "4.2",
                "text": "Did all patients receive a reference standard?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Partial verification — applying the reference standard "
                    "only to a subset (e.g. index-positive participants) — "
                    "biases sensitivity and specificity estimates. 'No' if "
                    "verification was selective."
                ),
            },
            {
                "id": "4.3",
                "text": "Did all patients receive the same reference standard?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "Differential verification — different reference "
                    "standards for index-positive vs index-negative patients "
                    "— introduces bias. 'No' if multiple reference standards "
                    "were used non-randomly."
                ),
            },
            {
                "id": "4.4",
                "text": "Were all patients included in the analysis?",
                "options": list(SIGNAL_OPTIONS),
                "elaboration": (
                    "All patients recruited into the study should be included "
                    "in the analysis. A potential for bias exists if the "
                    "number of patients enrolled differs from the number of "
                    "patients included in the 2×2 table of results, because "
                    "patients lost to follow-up differ systematically from "
                    "those who remain."
                ),
            },
        ],
    },
]


# ─────────────────────────────────────────────
# Prompt building + LLM orchestration
# ─────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "You are an evidence-synthesis methodologist assessing a diagnostic test "
    "accuracy study using the QUADAS-2 tool (Whiting et al., 2011, Ann Intern "
    "Med). For each domain, read the PDF carefully and answer the signaling "
    "questions with one of: Y (yes), N (no), U (unclear). When the domain has "
    "an applicability assessment, also rate concern that the as-conducted "
    "study matches the review question (PIRT: Patient population / Index test "
    "/ Reference standard / Target condition) as: Low / High / Unclear. "
    "Provide a short rationale (1-2 sentences, quoting the paper where "
    "possible) for every answer. Return ONLY a valid JSON object — no "
    "preamble, no markdown fences."
)


def _format_estimate_block(estimate: dict[str, Any] | None) -> str:
    """Render the estimate context block for the prompt header. Empty when
    no estimate was supplied (single-estimate fallback)."""
    if not estimate:
        return "(assessment is for the paper's primary / headline accuracy estimate)"
    parts = []
    for key in ("description", "subgroup", "index_test", "threshold",
                "reference_standard", "unit_of_analysis", "sensitivity",
                "specificity", "n"):
        val = estimate.get(key)
        if val:
            parts.append(f"- {key.replace('_', ' ').title()}: {val}")
    return "\n".join(parts) if parts else "(assessment is for an estimate but no descriptor fields were supplied)"


def _format_review_context(review_context: str | None) -> str:
    """Render the review-level context (PIRT review question) for the
    prompt header. Empty when not supplied — the LLM falls back to a
    generic intended-use baseline."""
    if not review_context or not review_context.strip():
        return (
            "(no review question supplied — judge applicability against the "
            "generic 'intended-use population' implied by the paper)"
        )
    return review_context.strip()


def build_domain_prompt(domain: dict[str, Any],
                        study_type: str,
                        primary_outcome: str,
                        extracted_fields: dict[str, str],
                        estimate: dict[str, Any] | None = None,
                        review_context: str | None = None) -> str:
    """Per-domain prompt for QUADAS-2 signaling-question + applicability assessment."""
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

    shape_lines = ["{"]
    for sig in domain["signals"]:
        shape_lines.append(f'  "{sig["id"]}": "Y|N|U",')
        shape_lines.append(f'  "{sig["id"]}_rationale": "1-2 sentences quoting the paper",')
    if domain["has_applicability"]:
        shape_lines.append('  "applicability_judgement": "Low|High|Unclear",')
        shape_lines.append('  "applicability_rationale": "1-2 sentences explaining the concern relative to the review question"')
    else:
        if shape_lines[-1].endswith(","):
            shape_lines[-1] = shape_lines[-1][:-1]
    shape_lines.append("}")
    shape = "\n".join(shape_lines)

    applicability_block = ""
    if domain["has_applicability"]:
        applicability_block = (
            "\n\n**Applicability assessment** (rate as Low / High / Unclear):\n"
            f"{domain['applicability_question']}\n"
            f"Elaboration: {domain['applicability_elaboration']}\n"
            "\n**Review question** (PIRT — use this to judge applicability):\n"
            f"{_format_review_context(review_context)}"
        )

    return f"""Assess **Domain {domain['id']} — {domain['name']}** of QUADAS-2 (Whiting 2011) for the diagnostic test accuracy study described in the attached PDF.

Study type: {study_type}
Primary outcome (target condition): {primary_outcome}

Estimate being assessed:
{_format_estimate_block(estimate)}

Context (fields already extracted from the paper):
{ctx_json}

Signaling questions:
{questions_block}{applicability_block}

Return a JSON object with exactly this shape:
{shape}

Answer N only when the paper gives enough information to rule out adherence; answer U only when the paper is silent or the information is ambiguous. Rationales must be short (1-2 sentences) and quote the paper verbatim where possible."""


def _normalise_answer(raw_value: Any) -> str:
    """Normalise an LLM answer string to one of Y / N / U.

    Accepts Y / Yes / yes / YES → Y;
            N / No / no / NO → N;
            anything else (U / Unclear / NI / ?) → U.
    """
    s = str(raw_value or "").strip().lower()
    if s in ("y", "yes"):
        return "Y"
    if s in ("n", "no"):
        return "N"
    return "U"


def _assess_domain(pdf_bytes: bytes,
                   domain: dict[str, Any],
                   study_type: str,
                   primary_outcome: str,
                   extracted_fields: dict[str, str],
                   llm_call: Callable[[bytes, str, int], dict[str, Any]],
                   estimate: dict[str, Any] | None = None,
                   review_context: str | None = None) -> dict[str, Any]:
    """LLM-assess one domain. Returns
    ``{signals, rationales, judgement, applicability_judgement, applicability_rationale}``
    (the last two are absent for the Flow and Timing domain)."""
    prompt = build_domain_prompt(domain, study_type, primary_outcome,
                                 extracted_fields, estimate=estimate,
                                 review_context=review_context)
    raw = llm_call(pdf_bytes, prompt, 8192)

    signals: dict[str, str] = {}
    rationales: dict[str, str] = {}
    for sig in domain["signals"]:
        sid = sig["id"]
        ans = _normalise_answer(raw.get(sid))
        if ans not in SIGNAL_OPTIONS:
            logger.warning("QUADAS-2 domain %s question %s: invalid answer %r — defaulting to U",
                           domain["id"], sid, raw.get(sid))
            ans = "U"
        signals[sid] = ans
        rationales[sid] = str(raw.get(f"{sid}_rationale", "")).strip()

    judgement = DOMAIN_JUDGES[domain["id"]](signals)

    out: dict[str, Any] = {
        "signals": signals,
        "rationales": rationales,
        "judgement": judgement,
    }

    if domain["has_applicability"]:
        app = str(raw.get("applicability_judgement", "Unclear")).strip()
        norm = app.lower()
        if norm in ("u", "unclear", "?", "insufficient", "insufficient information",
                    "no information", "ni"):
            app = "Unclear"
        elif norm in ("low", "low concern", "low concerns"):
            app = "Low"
        elif norm in ("high", "high concern", "high concerns"):
            app = "High"
        else:
            app = "Unclear"
        out["applicability_judgement"] = app
        out["applicability_rationale"] = str(
            raw.get("applicability_rationale", "")).strip()

    return out


def run(pdf_bytes: bytes,
        extracted_fields: dict[str, str],
        classification: dict[str, str],
        primary_outcome: str,
        *,
        llm_call: Callable[[bytes, str, int], dict[str, Any]],
        estimate: dict[str, Any] | None = None,
        review_context: str | None = None,
        progress: Callable[[int], None] | None = None,
        ) -> tuple[dict[str, Any], str, str, str]:
    """Run QUADAS-2 against a diagnostic test accuracy study.

    Returns ``(domain_results, overall_rob, overall_direction, overall_applicability)``.

    - ``domain_results`` is keyed by domain id (``"1"`` … ``"4"``), each with
      ``{name, signals, rationales, judgement, applicability_judgement,
      applicability_rationale}`` (the last two only for domains 1-3).
    - ``overall_rob`` is "Low" / "High" / "Unclear".
    - ``overall_direction`` is always ``"NA"`` for diagnostic accuracy.
    - ``overall_applicability`` is "Low" / "High" / "Unclear", aggregated
      over the 3 applicability-bearing domains only.
    """
    study_type = classification.get("study_type", "Diagnostic Accuracy")

    domain_results: dict[str, Any] = {}
    for domain in DOMAINS:
        if progress:
            try:
                progress(domain["id"])
            except Exception:
                pass
        result = _assess_domain(pdf_bytes, domain, study_type,
                                primary_outcome, extracted_fields,
                                llm_call=llm_call,
                                estimate=estimate,
                                review_context=review_context)
        result["id"] = domain["id"]
        result["name"] = domain["name"]
        result["has_applicability"] = domain["has_applicability"]
        domain_results[str(domain["id"])] = result

    rob_overall = quadas2_overall(
        [domain_results[str(d["id"])]["judgement"] for d in DOMAINS])
    app_overall = quadas2_applicability_overall(
        [domain_results[str(d["id"])]["applicability_judgement"]
         for d in DOMAINS if d["has_applicability"]])

    return domain_results, rob_overall, "NA", app_overall
```

---

## 8. Quick test sketches

Plain `assert` statements (no framework) — drop these at the bottom of the reference module and run with `python3 quadas2.py` to confirm the decision tree + aggregation logic behaves as documented.

```python
# Domain decision tree
assert quadas2_domain_judge({"1.1": "Y", "1.2": "Y", "1.3": "Y"}) == "Low"
assert quadas2_domain_judge({"1.1": "Y", "1.2": "N", "1.3": "Y"}) == "High"
assert quadas2_domain_judge({"1.1": "Y", "1.2": "U", "1.3": "Y"}) == "Unclear"
assert quadas2_domain_judge({"1.1": "U", "1.2": "U", "1.3": "U"}) == "Unclear"
assert quadas2_domain_judge({}) == "Unclear"
# An N anywhere outranks any number of U or Y
assert quadas2_domain_judge({"1.1": "U", "1.2": "N", "1.3": "Y"}) == "High"

# Overall RoB across 4 domains
assert quadas2_overall(["Low", "Low", "Low", "Low"]) == "Low"
assert quadas2_overall(["Low", "High", "Low", "Low"]) == "High"
assert quadas2_overall(["Low", "Unclear", "Low", "Low"]) == "Unclear"
assert quadas2_overall(["Unclear", "High", "Unclear", "Low"]) == "High"
assert quadas2_overall([]) == "Unclear"

# Overall applicability across the 3 applicability-bearing domains
# (D4 Flow and Timing is excluded by the caller)
assert quadas2_applicability_overall(["Low", "Low", "Low"]) == "Low"
assert quadas2_applicability_overall(["Low", "High", "Low"]) == "High"
assert quadas2_applicability_overall(["Low", "Unclear", "Low"]) == "Unclear"

# DOMAINS structural invariants
assert len(DOMAINS) == 4
assert [d["id"] for d in DOMAINS] == [1, 2, 3, 4]
assert [d["has_applicability"] for d in DOMAINS] == [True, True, True, False]
assert [len(d["signals"]) for d in DOMAINS] == [3, 2, 2, 4]
# 11 total signals across the tool
assert sum(len(d["signals"]) for d in DOMAINS) == 11

print("All QUADAS-2 sanity checks passed.")
```

---

## 9. Implementation notes for other platforms

### PDF attachment

The reference implementation assumes a **vision-capable LLM** that can ingest PDF bytes directly. For text-only models, pre-extract the PDF text with `pypdf` (or equivalent) and inline the text in the prompt; you may need to chunk very large papers and run domain calls per-chunk with a first-non-empty merge.

### Per-domain LLM calls

The orchestrator runs **4 calls per paper** (one per domain) rather than asking the model to assess everything in a single mega-call. This keeps each prompt focused, isolates per-domain failures (one bad call doesn't poison the rest), and avoids the model dropping later domains as response-length pressure mounts. Cost: ~4 × (PDF + ~1 KB prompt) input tokens per paper.

### Review-question framing (PIRT)

Applicability is judged against the **review question in PIRT terms** (Patient population / Index test / Reference standard / Target condition — NOT PICO). When no review question is supplied via `review_context`, the prompt falls back to a generic "intended-use population implied by the paper" baseline, which is methodologically defensible for single-paper assessment but undersells review-specific concerns. If you're running QUADAS-2 inside a systematic review, always thread the protocol PIRT question through.

### Per-estimate iteration

Whiting 2011 originally assumed one estimate per study. Many modern reviews run QUADAS-2 per-estimate (subgroup × index test × threshold × reference standard × unit of analysis). The optional `estimate` kwarg threads the descriptor into all 4 domain prompts so the LLM knows which estimate it's judging. To run per-estimate:

1. Extract candidate estimates with a separate LLM pass (the QUADAS-3 v1.2 extract-estimates prompt is tool-agnostic and works for QUADAS-2 too — see the QUADAS-3 reference).
2. Loop `run(...)` per estimate; persist one result row per (paper, estimate) pair.

### Conservative decision tree

The tree maps **any N → High**. Whiting 2011 narratively allows reviewers to keep a domain at Low even with a single N if judged immaterial. The conservative choice is intentional — automated assessment cannot reliably judge materiality, and the per-signal rationales returned by the LLM let a human reviewer override the judgement in their write-up. If you want the option to override in your UI, surface the rationales prominently and provide an "override → Low" affordance.

### Answer-normalisation tolerance

`_normalise_answer` accepts case-insensitive `Y/Yes`, `N/No`, and treats anything else (including `U`, `Unclear`, `NI`, `?`, empty) as `U`. This is forgiving by design — LLMs sometimes emit `"yes"` instead of `"Y"` even with explicit prompt instructions.

### GRADE integration

QUADAS-2 produces a Low/High/Unclear overall RoB. A common GRADE mapping for diagnostic accuracy:

- Low → 0 GRADE downgrade levels
- High → 1 (or 2 if ≥2 domains are High)
- Unclear → 1 (conservative)

Indirectness and imprecision modules for treatment trials (PICO) do **not** apply directly to diagnostic accuracy (which is PIRT) — defer those or build PIRT-specific variants.

The full certainty ladder, the initial-certainty-by-design table, the mapping above in its complete form (covering all five RoB instruments), and the indirectness and imprecision modules themselves are documented in [quality_appraisal_grade_shareable.md](quality_appraisal_grade_shareable.md), with a turnkey reference implementation. Diagnostic accuracy enters that per-paper pipeline at **High** initial certainty with the indirectness and imprecision modules skipped (`skip_grade_extras`) for exactly the PIRT reason above. For a certainty rating over a *pooled body* of accuracy studies rather than one paper, see [grade_certainty_shareable.md](grade_certainty_shareable.md) instead — its risk-of-bias domain aggregates per-study labels by pooled weight.

### Out of scope (v1)

- **Phase-2 review-specific tailoring** of signaling questions (Whiting 2011 allows reviewers to add review-specific signals; this implementation uses only the canonical core questions).
- **Structured PIRT input fields** (the implementation accepts a single free-text `review_context`; for richer downstream filtering you may want to split this into 4 fields).
- **Reviewer override of single-N domains** via UI (per the conservative-tree note above).
- **QUADAS-C** (comparative accuracy of two or more index tests) — a separate tool.
- **STARD 2015 reporting checklist** — typically run alongside QUADAS-2 but is a separate companion tool, not bundled here.
