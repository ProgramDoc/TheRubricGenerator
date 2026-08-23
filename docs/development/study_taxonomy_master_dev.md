# Study Taxonomy & Agent Pipeline (Master Doc) — Development Notes

Internal companion to [`../shareable/study_taxonomy_master_shareable.md`](../shareable/study_taxonomy_master_shareable.md). The shareable document is the methodology, framework-free and history-free — the taxonomy, routing, classification + extraction agents in full, and a digest of every appraisal and synthesis agent. This document holds what is specific to *this* codebase: backing modules, source lineage, and revision notes.

**Backing modules**

- `backend/annotator.py` — `_TAXONOMY`, `build_classify_prompt`, `classify_study_design` (shareable §3.11); `UNIVERSAL_FIELD_IDS`, `TYPE_FIELD_IDS`, `FIELD_GROUPS`, `DESIGN_MODIFIER_COLS`, `build_prefill_prompt`, `prefill_fields`, `_call_with_pdf` (§4)
- `backend/quality_appraisal.py` — `STUDY_TYPE_REGISTRY`, `dispatch`, `_TOOL_RUNNERS`, `_GUIDELINE_RUNNERS`, `_ESTIMATE_EXTRACTORS`, `appraise_paper`, `compute_grade`, `_rob_downgrade` (§§2.3, 5, 8.3)
- `backend/rob_tools/` — `rob2.py`, `rob2_crossover.py`, `rob2_cluster.py`, `robins_i.py`, `robins_i_v1.py`, `quadas2.py`, `quadas3.py`, `amstar2.py` (§6)
- `backend/reporting_guidelines/` — `consort2025.py`, `consort_crossover.py`, `consort_cluster.py`, `strobe.py`, `stard.py`, `prisma2020.py` (§7)
- `backend/outcomes.py`, `backend/indirectness.py`, `backend/imprecision.py` (§8)
- `backend/evidence_synthesis/` + `backend/synthesis.py`, `backend/synthesis_stats.py`, `backend/synthesis_table2.py` (§9)
- Tests: `tests/test_quality_appraisal.py` (`TestDispatch::test_registry_keys_match_annotator_types` pins registry keys ⊆ `TYPE_FIELD_IDS` keys), plus the per-agent suites listed in each agent's own dev doc.

**External source.** The OGAI taxonomy/rubric material (shareable §§2–3 reference layers, §4.9, §7.4) is consolidated from the sibling `ProgramDoc/StudyTaxonomy` repository: `index.html` (Taxonomy v1.9, 32 types), `rubric.html` (Classification Rubric v1.8, incl. Rule 2b), `extraction.html` (Extraction Fields Reference v1.6), and `OGAI_AI-CEA_pipeline_rubric_v3.1.md`. That repo's HTML pages remain the visual/interactive reference; the shareable master doc is now the canonical *unified* text.

**Implementation status.** The deployed/reference status tags in the shareable are the status ledger. Deployed here: the 13-type appraisal registry, the 33-type classifier, the three-layer extraction catalog, all eight RoB tools' runners, six guideline checkers, indirectness/imprecision/GRADE combiner, and the synthesis stack. Not deployed (reference): primacy-rule/design-feature structured classification output, red-flag re-routing + dual extraction, critical-item reporting tiers, stepped-wedge appraisal, Controlled Before-After (absent from `_TAXONOMY`), and every routing row marked reference in shareable §2.3 (EPOC, NOS, QUIPS, PROBAST, JBI, AXIS, CASP, MMAT, CHEC, STROBE-MR, SCCS, AGREE II, CINeMA).

**Related documents.** Digest-of-record vs. companion-of-record split is defined in shareable §1.2–1.3. Note: shareable §§6.1, 6.6, 6.8 and §7 are currently the *only* sharable coverage of RoB 2 parallel, QUADAS-3, AMSTAR-2, and the guideline checkers — CLAUDE.md's reference to `docs/shareable/amstar2_shareable.md` predates that file existing; until standalone docs land, point readers at the master doc. Internal full references remain at `docs/quality_appraisal_rob_reference.md` (RoB 2 / ROBINS-I V2 / QUADAS-3 verbatim prompts + trees).

## Revision notes

Substantive changes to the shareable methodology are logged here, newest-first, so downstream implementations (e.g. forks maintained by other teams) can see what changed and why. Cosmetic / wording-only edits are not logged.

### 2026-08-23 — Initial consolidated release

- Created the master shareable doc unifying the OGAI taxonomy lineage (site v1.9, rubric v1.8, extraction v1.6, pipeline rubric v3.1) with the platform lineage (annotator taxonomy v2.1, 13-type appraisal registry).
- **Taxonomy union decision:** canonical tree = OGAI v1.9's 32 consolidated types ∪ platform v2.1's Single-Arm Trial + Dose-Escalation Study = 34 types. Controlled Before-After enters as taxonomy-only (reference) — it is not in the deployed `_TAXONOMY` prompt. v1.9's consolidation principle (merge types that route identically) adopted as the documented rationale; prospective/retrospective cohort and case-control-family distinctions live in extraction fields, not the tree.
- **Cluster subtypes:** OGAI v3.1's parallel / stepped-wedge / cluster-crossover subtype tree recorded (§2.4); platform keeps Stepped-Wedge as a separate classify-only type with appraisal deliberately unrouted (CRT cribsheet covers parallel only).
- **Status-tag convention introduced** (deployed / reference, as plain-text tags) so one document can carry the OGAI reference methodology and the deployed behavior without conflating them. Deployed prompts (classify, prefill) transcribed verbatim from `backend/annotator.py`.
- Indirectness/imprecision cross-references use the *actual* section numbers of `quality_appraisal_grade_shareable.md` (§4 indirectness, §5 imprecision) — CLAUDE.md's quick-reference row (§5/§6) is off by one.
