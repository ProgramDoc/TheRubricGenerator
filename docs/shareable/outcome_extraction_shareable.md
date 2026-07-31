# Outcome Extraction — Sharable Methodology Reference

A self-contained reference for identifying **which outcomes a study can be separately appraised for**, so that risk-of-bias and certainty assessment can run once per outcome instead of once per paper. Contains:

- The core principle and why the granularity matters
- The outcome schema, field by field
- The exact extraction prompt
- The label-composition rule, and why the prompt string and the join key differ
- A turnkey reference implementation (`llm_call` injected — no framework dependencies)
- Plain-`assert` test sketches
- Implementation notes for other platforms

**Source.** Not a transcription of a published instrument — there is no canonical "outcome extraction" checklist. The *requirement* comes from the risk-of-bias instruments themselves: RoB 2 (Sterne JAC et al. BMJ 2019;366:l4898) is explicitly applied "to the result of a specific outcome", with domain 4 covering measurement of the outcome and domain 5 the selection of the reported result; ROBINS-I (Sterne JAC et al. BMJ 2016;355:i4919, and the 2025 V2 revision) is likewise per-outcome. The GRADE handbook rates certainty per outcome, not per study. This document specifies one way to produce the outcome list those instruments need.

**Scope.** Covers producing a candidate outcome list from a single paper, and the shape a downstream appraiser consumes. Out of scope: extracting the *numerical results* attached to each outcome (that is an effect-size extraction routine — see a pooling/meta-analysis reference); harmonizing outcome names *across* studies into canonical bodies of evidence (that is the synthesis layer's job, and is where the label choice in §4 pays off); diagnostic-accuracy studies, where the unit of assessment is an accuracy estimate (one 2×2 table) rather than an outcome, and a separate estimate-extraction routine applies.

**Interpretation callout.** The prompt's splitting rules are a deliberate, conservative reading. "One outcome, several statistics" is the hard case: a hazard ratio, a Kaplan-Meier curve, and a median survival time for overall survival are **one** outcome, not three. Over-splitting is the expensive failure — each spurious outcome costs a full appraisal pass and produces a result row a reviewer has to dismiss.

---

## 1. Core principle

**One call = one paper = a list of separately appraisable outcomes.**

Risk-of-bias instruments are outcome-specific. The same trial can be at *low* risk of bias for all-cause mortality — an objectively-ascertained, pre-registered primary endpoint — and at *high* risk for an investigator-assessed symptom score in an open-label design, because domain 4 (measurement of the outcome) and domain 5 (selection of the reported result) genuinely differ between them. Collapsing a paper to a single assessment throws that distinction away and attaches one judgement to outcomes it was never made about.

So the appraisal unit is the **(study × outcome)** pair, and something has to produce the outcome axis. That is this routine. It makes no judgement: it lists what a reviewer could appraise, and the reviewer picks.

The extraction is **advisory and fully optional**. Every consumer must have a fallback — appraise the paper's primary outcome alone — because the model can return an empty list, the call can fail, and the reviewer may not want the extra passes. A pipeline that hard-depends on this routine is built wrong.

---

## 2. The outcome schema

```json
{
  "id": 1,
  "name": "All-cause mortality",
  "description": "Death from any cause during the trial follow-up period",
  "measure": "proportion of randomized participants who died",
  "timing": "median 18.2 months",
  "outcome_type": "binary",
  "is_primary": true,
  "source": "extracted"
}
```

| Field | Type | Purpose |
|---|---|---|
| `id` | int | Synthetic, `1..N`, assigned **in code** over the entries kept — never taken from the model. Stable within one paper; the appraisal result row stores it. |
| `name` | str | Short label for a UI control, ≤ 120 chars. **This is the join key** — see §4. |
| `description` | str | The outcome as the paper defines it, ≤ 200 chars. |
| `measure` | str | How it was measured: the instrument, scale, or metric. |
| `timing` | str | The timepoint or follow-up window, as reported. |
| `outcome_type` | str | `"binary"` / `"continuous"` / `"time-to-event"` / `""`. Normalized against that closed set; anything else becomes `""`. |
| `is_primary` | bool | True only for the paper's stated primary outcome(s). |
| `source` | str | `"extracted"` here; `"reviewer"` for outcomes a human added. |

**Why `outcome_type` earns its place.** Downstream imprecision assessment needs to know whether an outcome is binary (so an event-count subdomain applies) or continuous (so it is N/A). Inferring that from paper-level fields is wrong for every non-primary outcome, because those fields describe the primary. A per-outcome answer, captured here, removes the guess. It is normalized to a closed set precisely so an unrecognized value degrades to "don't know" rather than to a confident wrong answer.

---

## 3. The extraction prompt

`{context}` is filled with any already-extracted study-level fields, as JSON, or a placeholder when there are none.

```
Identify every distinct outcome the attached study reports that could be separately appraised for risk of bias.

Risk-of-bias instruments (RoB 2, ROBINS-I) are outcome-specific: domain 4 (measurement of the outcome) and domain 5 (selection of the reported result) genuinely differ between outcomes in the same paper. List the outcomes a reviewer would appraise separately.

For each outcome return:
- ``name`` — short label suitable for a UI checkbox (<= 80 chars), e.g. "All-cause mortality"
- ``description`` — the outcome as the paper defines it (<= 200 chars)
- ``measure`` — how it was measured; the instrument, scale, or metric used
- ``timing`` — the timepoint or follow-up window, as reported
- ``outcome_type`` — one of "binary", "continuous", "time-to-event", or "" if unclear
- ``is_primary`` — true only for the paper's stated primary outcome(s)

Rules:
- Do NOT split one outcome into the several statistics reported for it. A hazard ratio, a Kaplan-Meier curve, and a median survival for overall survival are ONE outcome.
- Composite outcomes are one outcome. Do not decompose them into components unless the components are themselves pre-specified outcomes.
- Do NOT list every adverse-event tally as a separate outcome. Include a safety outcome only where the paper pre-specifies a named one.
- List the primary outcome first.
- If the paper states no outcomes at all, return an empty list.

Return ONLY a JSON object of the shape:
{
  "outcomes": [
    {"name": "...", "description": "...", "measure": "...", "timing": "...", "outcome_type": "...", "is_primary": true}
  ]
}

Context (fields already extracted from the paper):
{context}
```

---

## 4. Two strings, two jobs

The appraiser wants a *rich* outcome string: telling an instrument it is rating "Quality of life — measured as KCCQ total symptom score — at 8 months" produces a better assessment than telling it "Quality of life", because two outcomes can share a name and differ only in instrument or follow-up.

A synthesis layer grouping studies into bodies of evidence wants the *opposite*: a short label that normalizes and matches across papers. `"Quality of life — measured as KCCQ total symptom score — at 8 months"` will never match a body keyed on `"Quality of life"`.

**Keep both.** Compose the rich form for prompts; keep `name` clean and use it as the join key. Storing only the composed string is the failure mode worth naming: every cross-study outcome lookup misses, and a fully-appraised body silently reads as unappraised.

```
outcome_label(o) = "{name} — measured as {measure} — at {timing}"
```
with absent parts dropped, capped at 200 characters to stay prompt-compact.

---

## 5. Reference implementation

Dependency-free. `llm_call(pdf_bytes, prompt) -> dict` is injected; supply whatever your stack uses to send a PDF plus a prompt and parse a JSON reply.

```python
"""Outcome extraction — the list of outcomes a paper can be appraised for."""
from typing import Any, Callable

NAME_CAP, DESCRIPTION_CAP, MEASURE_CAP, TIMING_CAP, LABEL_CAP = 120, 200, 120, 80, 200
OUTCOME_TYPES = ("binary", "continuous", "time-to-event")

PROMPT_HEADER = """Identify every distinct outcome the attached study reports that could be separately appraised for risk of bias.

Risk-of-bias instruments (RoB 2, ROBINS-I) are outcome-specific: domain 4 (measurement of the outcome) and domain 5 (selection of the reported result) genuinely differ between outcomes in the same paper. List the outcomes a reviewer would appraise separately.

For each outcome return:
- ``name`` — short label suitable for a UI checkbox (<= 80 chars), e.g. "All-cause mortality"
- ``description`` — the outcome as the paper defines it (<= 200 chars)
- ``measure`` — how it was measured; the instrument, scale, or metric used
- ``timing`` — the timepoint or follow-up window, as reported
- ``outcome_type`` — one of "binary", "continuous", "time-to-event", or "" if unclear
- ``is_primary`` — true only for the paper's stated primary outcome(s)

Rules:
- Do NOT split one outcome into the several statistics reported for it. A hazard ratio, a Kaplan-Meier curve, and a median survival for overall survival are ONE outcome.
- Composite outcomes are one outcome. Do not decompose them into components unless the components are themselves pre-specified outcomes.
- Do NOT list every adverse-event tally as a separate outcome. Include a safety outcome only where the paper pre-specifies a named one.
- List the primary outcome first.
- If the paper states no outcomes at all, return an empty list.

Return ONLY a JSON object of the shape:
{
  "outcomes": [
    {"name": "...", "description": "...", "measure": "...", "timing": "...", "outcome_type": "...", "is_primary": true}
  ]
}
"""


def extract_outcomes(pdf_bytes: bytes,
                     llm_call: Callable[[bytes, str], dict],
                     context: str = "(no pre-extracted fields)",
                     ) -> list[dict[str, Any]]:
    """Every appraisable outcome in the paper. Returns [] rather than raising
    when the model gives back nothing usable — the caller falls back to the
    paper's primary outcome."""
    raw = llm_call(pdf_bytes, PROMPT_HEADER +
                   "\n\nContext (fields already extracted from the paper):\n" + context)
    items = raw.get("outcomes")
    if not isinstance(items, list):
        return []

    out: list[dict[str, Any]] = []
    for oc in items:
        if not isinstance(oc, dict):
            continue
        # Numbered over the entries we keep, so a malformed entry leaves no gap
        # in ids that get stored and exported.
        idx = len(out) + 1
        otype = str(oc.get("outcome_type") or "").strip().lower()
        clean = {
            "id": idx,
            "name": str(oc.get("name") or "").strip()[:NAME_CAP],
            "description": str(oc.get("description") or "").strip()[:DESCRIPTION_CAP],
            "measure": str(oc.get("measure") or "").strip()[:MEASURE_CAP],
            "timing": str(oc.get("timing") or "").strip()[:TIMING_CAP],
            "outcome_type": otype if otype in OUTCOME_TYPES else "",
            "is_primary": bool(oc.get("is_primary")),
            "source": "extracted",
        }
        if not clean["name"]:
            bits = [b for b in (clean["measure"], clean["timing"]) if b]
            clean["name"] = (clean["description"] or " — ".join(bits)
                             or f"Outcome {idx}")[:NAME_CAP]
        out.append(clean)
    return out


def outcome_label(outcome: dict[str, Any]) -> str:
    """The prompt string — NOT the join key. Group studies by outcome["name"]."""
    name = (outcome.get("name") or outcome.get("description") or "").strip()
    bits = [name]
    measure = (outcome.get("measure") or "").strip()
    timing = (outcome.get("timing") or "").strip()
    if measure:
        bits.append(f"measured as {measure}")
    if timing:
        bits.append(f"at {timing}")
    return " — ".join(b for b in bits if b)[:LABEL_CAP]
```

---

## 6. Test sketches

```python
def _stub(payload):
    return lambda pdf, prompt: payload

# ids are contiguous over the entries kept, so a malformed entry leaves no gap
out = extract_outcomes(b"", _stub({"outcomes": [
    "not a dict", {"name": "Mortality"}, None, {"name": "Quality of life"}]}))
assert [o["id"] for o in out] == [1, 2]
assert [o["name"] for o in out] == ["Mortality", "Quality of life"]

# anything unusable returns [] so the caller can fall back
assert extract_outcomes(b"", _stub({"outcomes": "mortality"})) == []
assert extract_outcomes(b"", _stub({"something_else": []})) == []

# fields are coerced to stripped strings; a missing name is synthesized
out = extract_outcomes(b"", _stub({"outcomes": [
    {"name": "  Mortality  ", "measure": None, "timing": 12},
    {"description": "Death from any cause"},
    {"measure": "6-minute walk distance", "timing": "12 weeks"},
    {}]}))
assert out[0]["name"] == "Mortality" and out[0]["measure"] == "" and out[0]["timing"] == "12"
assert out[1]["name"] == "Death from any cause"
assert out[2]["name"] == "6-minute walk distance — 12 weeks"
assert out[3]["name"] == "Outcome 4"

# outcome_type is normalized to the closed set; unknown degrades to ""
out = extract_outcomes(b"", _stub({"outcomes": [
    {"name": "A", "outcome_type": "  BINARY "},
    {"name": "B", "outcome_type": "ordinal"}]}))
assert [o["outcome_type"] for o in out] == ["binary", ""]

# the label composes; the name stays clean for joining
o = {"name": "Quality of life", "measure": "KCCQ score", "timing": "8 months"}
assert outcome_label(o) == "Quality of life — measured as KCCQ score — at 8 months"
assert outcome_label({"name": "Mortality"}) == "Mortality"
assert outcome_label({}) == ""
assert len(outcome_label({"name": "N" * 150, "measure": "M" * 150})) == 200

print("all outcome-extraction self-checks passed")
```

---

## 7. Implementation notes for other platforms

- **Always keep the fallback.** Empty list, failed call, reviewer opts out — every path must still appraise *something*. The natural default is the paper's stated primary outcome, picked from whatever fields your extraction already captures. This routine is an enhancement, not a dependency.
- **Assign ids yourself, over the entries you keep.** Never trust a model-supplied id, and never index the raw list — a skipped malformed entry then leaves a gap in ids that get stored and exported. If the ids cross a trust boundary (a client posting them back), re-key `1..N` server-side so they cannot be spoofed or collided.
- **Let reviewers add outcomes.** Extraction misses things, particularly in papers that report an outcome only in a figure or supplement. A free-text add path costs little and removes the "the extractor didn't find it, so I can't appraise it" dead end. Tag those `source: "reviewer"` — provenance matters when someone audits why a body was rated as it was.
- **Cap the count.** Each selected outcome is a full appraisal pass. A per-paper ceiling (ten is generous) stops a runaway extraction from turning one paper into a very large bill.
- **Price the marginal outcome, not a whole paper.** Classification, field extraction, and reporting-guideline checks are once-per-paper. Only the per-outcome work — risk of bias, indirectness, imprecision — repeats. Charging a full paper's cost per extra outcome over-bills substantially.
- **Do not apply this to diagnostic-accuracy studies.** There the assessment unit is an accuracy estimate (one 2×2 table, grouping all metrics computed from it), not an outcome; the two axes are alternatives, and offering both for one paper produces a meaningless cross-product.
- **Do not apply it to instruments that rate a whole review.** A systematic-review appraisal tool scores the review's conduct, not an outcome. Collapse those papers back to a single assessment.
- **Cap the composed label.** It goes into every downstream prompt. Two hundred characters is enough to disambiguate an instrument and a timepoint without crowding the assessment prompt.
