# Table 2 Evidence Table Agent — Development Notes

Internal companion to [`../shareable/table2_evidence_table_shareable.md`](../shareable/table2_evidence_table_shareable.md). The shareable
document is the methodology, framework-free and history-free, for readers implementing on another
stack. This document holds what is specific to *this* codebase: where the code lives, what state it
is in, and the revision history.

**Backing modules**

- `backend/synthesis/table2.py` — assembly, `build_study_id()`, `canonicalize_metric()`
- `backend/synthesis/table2_extract.py` — the per-study extraction pass

**Implementation status.** Implemented on branch `claude/grade-agent`; not on `main`.

---

## Revision notes

Substantive changes to the methodology, newest-first, so downstream implementations (e.g. forks maintained by other teams) can see what changed and why. Cosmetic / wording-only edits are not logged.

This history lives here rather than in the shareable document: the shareable document is the methodology as a reader on another stack should implement it, with no history and no repo-internal references. Everything about *this* codebase — where the code lives, what state it is in, what changed when — belongs on this side.

### 2026-07-05 — Added guideline-examples + rendering-model sections

**What changed.** Added §8 "Table 2 in practice: column variability across guidelines" (screenshots of real ASCO guideline Table 2s, a column-variability matrix, and a product-type × Table-2-presence taxonomy) and §9 "Rendering model: extract-once projection vs per-table render agent" (a recommendation with a tradeoff table). The reference-implementation / test / platform-notes sections renumbered to §10–§12.
**Why.** To ground the rendering-architecture decision — a dynamic-table UI serving columns off the extracted data vs a bespoke per-table render agent — in the actual layout variability observed across guideline Table 2s.
**Impact.** Documentation only — no change to any decision logic, prompt, schema, or output shape; no stored/historical results are affected. Adds PNG image assets under `assets/table2/`.
**Sections touched:** new §8, §9; renumbered §10–§12.

### 2026-06-30 — Initial publication

Self-contained Table 2 (per-study evidence table) methodology: the three-level extraction-tag mapping, the `outcomes[]` multi-outcome schema, the enumerated non-extraction calculations, the two extraction prompts + output shapes, the dual-mode (injected vs isolation) contract, and a turnkey Python reference implementation with plain-`assert` tests. The reference module's self-checks pass on CPython 3.13.

---
