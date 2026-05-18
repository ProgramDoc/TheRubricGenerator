# RoB 2: Conditional-signal NA fix (May 2026)

## The issue

An SME reviewing Quality Appraisal output flagged that the RoB 2 tool was recording `"NI"` (No Information) for signaling questions whose precondition was not met. Per the Cochrane RoB 2 cribsheet, those questions should be marked `"NA"` (Not Applicable) — the two values are methodologically distinct:

| Value | Meaning |
|-------|---------|
| **NI** | The paper is silent on a question that **does** apply. Genuine uncertainty. |
| **NA** | The question is **correctly skipped** because the cribsheet branching rule says to skip it (precondition unmet). |

The example from the SME (paper `2024_Adhikari_aog_101097AO`):

- Q2.3 ("Were there deviations from intended intervention that arose because of trial context?") was answered **PN** (no — only rare mismatches).
- Per the cribsheet, Q2.4 ("If Y/PY to 2.3: Were these deviations likely to have affected the outcome?") and Q2.5 ("If Y/PY/NI to 2.4: Were these deviations balanced between groups?") are conditional on Q2.3 being Y/PY (or Y/PY/NI). With Q2.3 = PN, both should be NA.
- The system recorded NI for both, which (per the SME) misleadingly changes the path highlighted in the decision-tree figure shown in the per-paper detail modal.

## Prior state

Three pieces of the implementation together produced the bug:

1. **No NA in the vocabulary.** [`backend/rob_tools/rob2.py:39`](../backend/rob_tools/rob2.py) defined `SIGNAL_OPTIONS = ("Y", "PY", "PN", "N", "NI")` — a single tuple used for every signal, with NA absent entirely.
2. **Prompt offered the LLM no NA option.** [`build_domain_prompt`](../backend/rob_tools/rob2.py) hard-coded `"Y|PY|PN|N|NI"` into the JSON shape template for every question — so even if the LLM tried to mark a skipped question as NA, it had been instructed otherwise.
3. **No post-parse normalization.** [`_assess_domain`](../backend/rob_tools/rob2.py) accepted the LLM's answers verbatim. The per-domain decision trees (e.g. `rob2_domain2_judge`) correctly route around inapplicable questions when the predecessor short-circuits — so the bias-judgement output was unaffected — but the stored signal values shown in the UI were wrong.

ROBINS-I V2 ([`backend/rob_tools/robins_i.py:719`](../backend/rob_tools/robins_i.py)) already produces NA correctly via a similar normalization step. QUADAS-3 has no conditional signaling questions and needs no change.

## Architectural decision: NA is rules-only, never LLM-facing

NA in RoB 2 is **purely a function of predecessor signal answers** — no paper-specific judgement is required to decide whether a question is applicable. That made the original "let the LLM and Python rules both produce NA" design redundant: two systems doing the same job, with one always winning. The fix takes the simpler approach.

| Layer | Role |
|-------|------|
| **LLM** | Reads the paper and answers each signaling question from the per-question elaboration. Only ever emits Y / PY / PN / N / NI. |
| **Rules** (`_normalize_conditional_signals`) | Walks each domain's conditional chain after the LLM response and sets NA wherever the precondition is unmet. Sole source of NA. |
| **Decision tree** (`rob2_domainN_judge`) | Computes Low / Some concerns / High from the resulting signal map. Untouched by this fix — already routes around inapplicable questions. |

The LLM-facing vocabulary stays at five tokens; the storage-layer vocabulary (`SIGNAL_OPTIONS`) is the six-token superset with NA included.

## Conditional question inventory (RoB 2)

10 of 23 signal questions are conditional. These are the questions the rules system can set to NA:

| Q | Domain | Precondition | Becomes NA when |
|---|--------|--------------|------------------|
| 2.3 | Deviations | 2.1 ∈ {Y,PY,NI} OR 2.2 ∈ {Y,PY,NI} | Both 2.1 and 2.2 ∈ {N,PN} |
| 2.4 | Deviations | 2.3 ∈ {Y,PY} | 2.3 ∈ {N,PN,NI,NA} |
| 2.5 | Deviations | 2.4 ∈ {Y,PY,NI} | 2.4 ∈ {N,PN,NA} |
| 2.7 | Deviations | 2.6 ∈ {N,PN,NI} | 2.6 ∈ {Y,PY} |
| 3.2 | Missing data | 3.1 ∈ {N,PN,NI} | 3.1 ∈ {Y,PY} |
| 3.3 | Missing data | 3.2 ∈ {N,PN} | 3.2 ∈ {Y,PY,NI,NA} |
| 3.4 | Missing data | 3.3 ∈ {Y,PY,NI} | 3.3 ∈ {N,PN,NA} |
| 4.3 | Measurement | 4.1 ∈ {N,PN,NI} AND 4.2 ∈ {N,PN,NI} | 4.1 ∈ {Y,PY} OR 4.2 ∈ {Y,PY} |
| 4.4 | Measurement | 4.3 ∈ {Y,PY,NI} | 4.3 ∈ {N,PN,NA} |
| 4.5 | Measurement | 4.4 ∈ {Y,PY,NI} | 4.4 ∈ {N,PN,NA} |

Domain 1 (3 unconditional questions) and Domain 5 (3 unconditional questions) are pass-through.

## Changes shipped

### `backend/rob_tools/rob2.py`

```python
# LLM-facing answer set — every signal uses this.
_BASIC = ("Y", "PY", "PN", "N", "NI")

# Stored-value superset (LLM outputs + rules-set NA), kept for back-compat.
SIGNAL_OPTIONS = ("Y", "PY", "PN", "N", "NI", "NA")
```

- Every signal in `DOMAINS` declares `"options": list(_BASIC)` — the LLM never sees NA in the prompt or the JSON shape template.
- `_SYSTEM_PROMPT` mentions conditional questions only to reassure the LLM that it should still answer them normally if applicable; the downstream system marks them not-applicable if the precondition turns out unmet.
- `build_domain_prompt` renders the JSON shape with the same five tokens (`"Y|PY|PN|N|NI"`) for every question.
- New `_normalize_conditional_signals(domain_id, signals, rationales)` walks each domain's conditional chain after the LLM response and sets NA wherever the precondition is unmet. The rationale is overwritten with an auto-generated explanation (e.g. *"Not applicable — 2.3 was PN (Q2.4 only applies when 2.3 is Y/PY)."*). Idempotent.
- `_assess_domain` calls the normalizer between the LLM parse and the decision-tree judgement:

```python
signals, rationales = _normalize_conditional_signals(
    domain["id"], signals, rationales)
judgement = DOMAIN_JUDGES[domain["id"]](signals)
```

**The five decision-tree functions (`rob2_domainN_judge`) were not touched.** They already route around inapplicable questions by checking only the relevant predecessor's value, so domain judgements are unchanged.

### `frontend/quality-appraisal.html`

```css
.qa-ans-na { background: var(--paper-2); color: var(--muted);
             font-style: italic; opacity: 0.7; }
```

The badge-class function at line 1652 (`'qa-ans qa-ans-' + (ans || 'ni').toLowerCase()`) already maps a stored `"NA"` value to the new class automatically — no JS change required. Adjacent fix: added missing `.qa-ans-wn / sn / wy / sy` rules for ROBINS-I V2's confidence-graded answer tokens, which were previously unstyled.

### Reference documentation

[`docs/quality_appraisal_rob_reference.md`](quality_appraisal_rob_reference.md) auto-regenerates from `prompt_catalog()`, so it picks up the updated system prompt + per-signal options on the next regen. The developer view at `GET /api/quality-appraisal/prompts` reflects the new vocabulary today.

## Tests

21 new tests in [`tests/test_quality_appraisal.py`](../tests/test_quality_appraisal.py):

- **`TestConditionalSignalNormalization`** (19 tests) — exercises every conditional precondition transition, including the SME's exact Adhikari case (`test_d2_smes_adhikari_case_pn_chains_to_na`), the no-mutation guarantee, idempotency, and **decision-tree regression checks** confirming `rob2_domainN_judge` returns the same judgement whether skipped slots carry NI or NA.
- **`TestSignalOptions`** (2 tests) — verifies the LLM-facing contract: no signal advertises NA to the LLM (every signal's `options` equals `_BASIC`), and `SIGNAL_OPTIONS` is the storage superset (`_BASIC` ∪ `{NA}`).

Full suite result: **431 passed, 0 failures, 0 skipped** (`python3 -m pytest tests/`).

## User-visible impact

- The per-paper detail modal RoB Domain 2 (and 3, 4) sections now show italic gray `NA` badges for correctly skipped questions, with explanatory "Not applicable — precondition not met" rationales.
- The figure-path described by the SME — previously confused by NI in slots that should have been skipped — now follows the correct cribsheet branch.
- Domain judgements (Low / Some concerns / High) are **unchanged** for all historical assessments, because the decision-tree functions never read skipped slots.
- Previously stored results retain their old NI values; only newly-generated assessments are subject to the fix. Re-running QA on an existing paper will yield the corrected values.

## Out of scope

- **Backfilling existing stored assessments.** Old runs keep their NI values in `quality_appraisal_results.rob_domains_json`. Re-run QA on a paper to get the corrected display.
- **Decision-tree semantics.** Whether `rob2_domain2_judge` returns "Some concerns" vs "Low" when the LLM signals indicate awareness but no actual deviations (the existing `if _no(q23): part1 = "Some concerns"` branch) was not revisited. The SME's complaint was about the displayed signal values, not the domain-judgement algorithm; the algorithm is faithful to the existing transcription in [`docs/quality_appraisal_rob_reference.md`](quality_appraisal_rob_reference.md).
- **ROBINS-I V2 and QUADAS-3.** ROBINS-I V2 already handles NA correctly via its own normalization. QUADAS-3 has no conditional signaling questions (every signal aggregates independently per Phase 5), so no change was needed.
