# Outcome Extraction — Development Notes

Internal companion to [`../shareable/outcome_extraction_shareable.md`](../shareable/outcome_extraction_shareable.md). The shareable
document is the methodology, framework-free and history-free, for readers implementing on another
stack. This document holds what is specific to *this* codebase: where the code lives, what state it
is in, and the revision history.

**Backing modules**

- `backend/outcomes.py` — `extract_outcomes()`, `outcome_label()`, `prompt_catalog()`
- `backend/quality_appraisal.py` — `build_outcome_units()` / `build_estimate_units()` turn the list into `_Unit`s; `_appraise_units()` runs one appraisal pass per unit; `pick_primary_outcome()` is the fallback when no outcomes are supplied
- `main.py` — `POST /api/quality-appraisal/extract-outcomes` (3 cr, auto-refunds on failure); `paper_outcomes` on `QualityAppraisalRunPayload`
- `frontend/quality-appraisal.html` — `_paperOutcomes` state, `extractOutcomesForSelectedPapers()`, `renderOutcomeCard()`, `addFreeTextOutcome()`, `updateRunCost()`
- `tests/test_quality_appraisal.py` — `TestOutcomeExtraction`, `TestOutcomeLabel`, `TestOutcomeUnits`, `TestEstimateUnits`, `TestAppraiseUnitsFanOut`

**Implementation status.** Shipped on `main`.

**Where it sits.** `backend/outcomes.py` is a sibling of `indirectness.py` / `imprecision.py`, deliberately **not** under `rob_tools/`. The outcome an assessment is scoped to feeds the risk-of-bias tool, the indirectness assessment, *and* the imprecision assessment, so it is tool-agnostic by construction — unlike `quadas3.extract_estimates`, which is genuinely specific to one instrument and lives with it. The registry entry is a plain `_OUTCOME_EXTRACTOR` callable rather than a per-tool dict for the same reason.

**Storage.** `quality_appraisal_runs.paper_outcomes_json` holds the reviewer's per-paper selection (`{paper_id: [outcome_dict, ...]}`, mirroring `paper_estimates_json`). Each result row carries `outcome_id` + `outcome_json`. All three columns were added via the idempotent `migrate_qa_columns` idiom; there is no uniqueness constraint on `quality_appraisal_results(run_id, paper_id)`, so multi-row-per-paper needed no schema relaxation.

**Cost.** `CREDIT_COST_QA_ADDITIONAL_OUTCOME = 21` (risk of bias ~15 + indirectness ~3 + imprecision ~3). `paper_charge()` is the single source of truth, called by both the run-create endpoint and `run_batch`'s refund path so the two cannot drift; `updateRunCost()` in the frontend mirrors it for the live preview. Extraction itself is 3 cr per paper per press, matching the classify cost and the sibling estimate extractor.

---

## Revision notes

Substantive changes to the methodology, newest-first, so downstream implementations (e.g. forks maintained by other teams) can see what changed and why. Cosmetic / wording-only edits are not logged.

This history lives here rather than in the shareable document: the shareable document is the methodology as a reader on another stack should implement it, with no history and no repo-internal references. Everything about *this* codebase — where the code lives, what state it is in, what changed when — belongs on this side.

### 2026-07-31 — Initial publication

**What changed.** New routine and new document. One LLM call per paper returns the outcomes a reviewer could appraise separately, each with a name, description, measure, timing, outcome type, and primary flag. Synthetic ids are assigned in code over the entries kept. `outcome_label()` composes the richer prompt string while `name` stays a clean join key.

**Why.** Risk-of-bias instruments are outcome-specific — RoB 2 domain 4 (measurement of the outcome) and domain 5 (selection of the reported result) genuinely differ between outcomes, and GRADE rates certainty per outcome. The appraisal orchestrator previously collapsed each paper to one assessment against its auto-picked primary outcome, so a trial's secondary outcomes inherited a judgement that was never made about them. Producing the outcome axis is the prerequisite for appraising per (paper × outcome), which in turn is what lets a body-of-evidence layer resolve a real risk-of-bias label for every body rather than only for primary-outcome bodies.

**Impact.** Additive. Extraction is optional and advisory: an empty list, a failed call, or a reviewer who does not use the panel all fall back to `pick_primary_outcome`, which is unchanged. Selecting a single outcome is byte-identical to the previous behaviour. No stored results change.

**Sections touched:** all (new document).

---

## Known gaps

- **No cross-study harmonization here.** This routine names outcomes as *one paper* reports them. Matching "Death from any cause" in one trial to "Overall mortality" in another is the synthesis layer's job — see the outcome-harmonization section of the pooling methodology. The `name`-vs-composed-label split in §4 of the shareable doc exists precisely so that harmonization has something matchable to work with.
- **The extractor does not read numerical results.** It says an outcome exists and how it was measured, not what the effect estimate was. Effect-size extraction is a separate routine.
- **Diagnostic accuracy is excluded by design**, not by omission: there the unit is an accuracy estimate, and `quadas3.extract_estimates` covers it. The API rejects a paper carrying both axes, because charging happens at run-create time while study classification happens at run time — a paper with both would be charged on one axis and executed on the other.
- **AMSTAR-2 papers collapse to one unit** and report `units_skipped` so the extra charged units refund. A systematic-review appraisal tool rates the review's conduct, not an outcome.
