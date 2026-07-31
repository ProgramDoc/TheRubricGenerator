# Per-Paper GRADE (Quality-Appraisal Platform) — Development Notes

Internal companion to [`../shareable/quality_appraisal_grade_shareable.md`](../shareable/quality_appraisal_grade_shareable.md). The shareable
document is the methodology, framework-free and history-free, for readers implementing on another
stack. This document holds what is specific to *this* codebase: where the code lives, what state it
is in, and the revision history.

**Backing modules**

- `backend/quality_appraisal.py` — `GRADE_LEVELS`, `_grade_index()`, `_rob_downgrade()`, `compute_grade()`, `STUDY_TYPE_REGISTRY`, `appraise_paper()`, `_appraise_units()`, `build_outcome_units()`, `build_estimate_units()`, `paper_charge()`, `refund_for()`
- `backend/indirectness.py`, `backend/imprecision.py`
- `backend/outcomes.py` — `extract_outcomes()` / `outcome_label()`, the per-outcome candidate list
- `tests/test_quality_appraisal.py`, `tests/test_imprecision.py`

**Implementation status.** Shipped on `main`. Distinct from the body-of-evidence GRADE agent — see `grade_certainty_dev.md`; the two share domain *names* but not logic.

**Related documents**

- `docs/quality_appraisal_grade_reference.md` — the older, fuller internal reference for the indirectness and imprecision modules specifically (verbatim prompts + trees).

---

## Revision notes

Substantive changes to the methodology, newest-first, so downstream implementations (e.g. forks maintained by other teams) can see what changed and why. Cosmetic / wording-only edits are not logged.

This history lives here rather than in the shareable document: the shareable document is the methodology as a reader on another stack should implement it, with no history and no repo-internal references. Everything about *this* codebase — where the code lives, what state it is in, what changed when — belongs on this side.

### 2026-07-31 — The rating is per (paper × outcome); imprecision's outcome typing is gated

**What changed.** The appraisal orchestrator now writes one result row per assessment unit rather than one per paper, so risk of bias, indirectness, imprecision, and the GRADE computation all run **once per selected outcome**. Every prompt's outcome placeholder is filled with the outcome being rated rather than the paper's auto-picked primary, and the shareable document's `As-conducted primary outcome: {primary_outcome}` placeholder is now `Outcome being rated: {assessed_outcome}` at all four sites (two prose prompt blocks and their duplicates inside the reference implementation).

`infer_outcome_is_binary` gains `outcome_is_primary` and `outcome_type` parameters. A caller-supplied per-outcome type wins outright; the paper-level `primary_outcome_type` / `_measurement` / `_definition` fields are consulted **only when the outcome being rated is the paper's primary**.

**Why.** Risk-of-bias instruments are outcome-specific — RoB 2 domain 4 (measurement of the outcome) and domain 5 (selection of the reported result) genuinely differ between outcomes — and GRADE rates certainty per outcome. The imprecision gate fixes a real defect: the three `primary_outcome_*` fields describe the paper's *primary* outcome, so rating a secondary against them mis-typed it. A trial with binary all-cause mortality and a continuous 6-minute-walk secondary typed the secondary as binary, firing the event-count subdomain that should have been N/A and manufacturing an imprecision downgrade. For a non-primary outcome the function now judges from the assessed-outcome string alone and returns `None` (indeterminate → the model decides) rather than guessing from the wrong outcome's description.

**Impact.** **Logic change, not prompt-only.** Stored results do not change and single-outcome runs are byte-identical — `outcome_is_primary` defaults to `True`, which is the old behaviour exactly. New multi-outcome runs produce per-outcome certainty ratings that can differ within one study, which is the point. Any fork that appraises more than one outcome per paper should take the gate; a fork that only ever rates the primary outcome is unaffected.

One behaviour worth flagging to reviewers comparing old and new runs: the diagnostic-accuracy path previously **dropped** the reviewer's outcome override (it passed the auto-picked primary into the per-estimate helper), so a QUADAS run with an override now produces a different judgement than it did before. Runs without an override are unchanged.

**Sections touched:** front matter (per-(paper × outcome) scope note), §5.5 (outcome-type heuristic + the gating note), and the four `{assessed_outcome}` prompt sites.

### 2026-07-30 — Rename and rescope: this is the per-paper appraisal GRADE, not the GRADE agent

**What changed.** The document was renamed from `grade_shareable.md` to `quality_appraisal_grade_shareable.md` and rescoped to what it actually describes: the **per-paper** certainty rating produced by the quality-appraisal platform. A disambiguation table was added to the front matter contrasting it with the body-of-evidence GRADE agent. Draft sections specifying inconsistency and publication bias were **removed**, along with a speculative five-domain extension of the combiner and a body-of-evidence orchestration path. The combiner is back to its three-domain production form, and section numbering is back to the original scheme.

**Why.** Those two domains, and the body-of-evidence method generally, are not a future extension of this component — they belong to a **different component** that already exists: the GRADE agent documented in [`grade_certainty_shareable.md`](grade_certainty_shareable.md), which consumes a pooling agent's output and implements all five downgrade domains plus the three upgrade domains. Publishing a second, thinner specification of the same domains would have given forks two incompatible answers for inconsistency and publication bias. Worse, the two components' shared domains are not variants of each other but genuinely different logic: risk of bias there is aggregated across studies by pooled weight rather than read off one paper, imprecision is the pooled CI versus null/MIDs plus Optimal Information Size rather than four LLM-judged subdomains, and indirectness is a reviewer-supplied integer rather than a four-subdomain model call.

**Impact.** No behaviour change and no stored results change — the three documented domains, their prompts, trees, and the combiner are unchanged, and the reference implementation remains byte-identical to the production `compute_grade` (re-verified across 24,640 input combinations after the revert). The change is to scope and naming. **Anyone who pulled the earlier draft should discard its inconsistency and publication-bias sections and use `grade_certainty_shareable.md` for those domains instead** — the thresholds are compatible, but that document's versions are the implemented ones and carry the surrounding contract.

**Sections touched:** front matter, Revision notes, §7, §12; former §4 and §7 removed; sections renumbered back (§5→§4, §6→§5, §8→§6, §9→§7, §10→§8, §11→§9, §12→§10, §13→§11, §14→§12).

### 2026-07-30 — Initial publication

**What changed.** First publication of the GRADE downgrade pipeline as a shareable methodology document. Covers the certainty ladder (§1), initial certainty by study design (§2), the risk-of-bias → downgrade-level mapping for all five supported RoB instruments (§3), the indirectness module (§4), the imprecision module (§5), the combining arithmetic and explanation strings (§6), orchestration and failure degradation (§7), sample input fields (§8), a turnkey single-file reference implementation (§9), and plain-`assert` test sketches (§10). Rating-up is named but deliberately unimplemented (§11).

**Why.** The RoB instruments already had shareable references (RoB 2 Cluster, RoB 2 Cross-over, ROBINS-I V1, ROBINS-I V2, QUADAS-2), but the GRADE layer that consumes their output had none — a downstream team could reimplement the instruments and still not reproduce the certainty rating. Two existing documents pointed at this gap: the QUADAS-2 reference carried a partial RoB→downgrade mapping in its "GRADE integration" note, and the ROBINS-I V2 reference stated that indirectness and imprecision "are not bundled here". Both now cross-reference this document.

**Impact.** No stored results change — this is a transcription of existing behaviour, not a logic change. Implementers who previously derived indirectness or imprecision from the developer-view prompt dump should re-check two details this document makes explicit and which are easy to get wrong: (a) the imprecision `n_a` → `precise` normalisation, without which continuous-outcome papers are downgraded for a subdomain that does not apply to them; and (b) the ROBINS-I Domain 1 label normalisation described in §3.

**Sections touched:** all (new document).

---
