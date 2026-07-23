# CLAUDE.md — AI Agent Instructions for The AI Researcher

## What Is This Project?

**The AI Researcher** is a clinical research LLM benchmarking platform. It generates evaluation rubrics from research papers (PDFs), has competing AI models answer the questions, then judges the answers. Think: automated exam for AI models on medical research comprehension.

**Tech stack**: FastAPI + PostgreSQL (SQLite fallback for local dev/tests) + vanilla HTML/JS (no build step) + Render deployment.

## Quick Reference: "I Want to Do X, Look at File Y"

| Task | File(s) |
|------|---------|
| Add a new API endpoint | `main.py` (add route + handler) |
| Add a new database table | Define SQL in `backend/{module}.py` as `{MODULE}_TABLES_SQL` (use PostgreSQL syntax: `SERIAL PRIMARY KEY`, `CURRENT_TIMESTAMP`), import + execute in `main.py:init_db()` |
| Add a column to existing table | `main.py:_migrate_challenge_columns_v2()` — use `column_exists()` from `backend/db.py` + ALTER TABLE |
| Change database connection | `backend/db.py` — compatibility wrapper (PostgreSQL when `DATABASE_URL` set, SQLite fallback otherwise) |
| Add a new frontend page | Create `frontend/{page}.html` (self-contained HTML+CSS+JS), add page route in `main.py`, add nav link to ALL other HTML files |
| Modify the rubric generator | `backend/agents/generator.py` (system prompt in `backend/skills.py:GENERATOR_SKILL_V1`); batching + retry orchestration in `backend/challenges.py:run_challenge` |
| Modify the judge / adjudication | `backend/agents/judge.py` (judge 1, OpenAI judge 2) + `backend/agents/adjudicator.py` (Gemini judge 3 + majority vote) + `backend/review.py` (3-way-split review queue + UI in `frontend/review.html`) |
| Modify challenge scoring | `backend/challenges.py` (scoring functions + `run_challenge()`) |
| Call an LLM | `backend/helpers.py` — `call_anthropic()` (now supports `thinking_budget=` for extended thinking), `call_gemini()`, `call_openai_compatible()` |
| Personal PDF library | `frontend/library.html` (cards + filter rail) + `GET /api/library/papers` in `main.py` (aggregates membership / annotation status / rubric+eval+challenge+run counts). Community library moved to `/community-library` |
| Multi-project paper membership | `paper_projects` junction table (defined in `main.py:init_db`, idempotent backfill from legacy `papers.project_id` on startup). Endpoints: `GET /api/papers/{pid}/projects`, `POST/DELETE /api/papers/{pid}/projects/{project_id}`. `papers.project_id` kept as a "primary" pointer for back-compat |
| Paper provenance | `papers.source` column (`'upload' \| 'lab' \| 'search' \| 'pubmed' \| 'imported'`). Lab uploads dual-write to `papers` (with sha256 dedup); idempotent `lab_documents` → `papers` backfill via `lab_documents.papers_id` cursor |
| Modify billing/credits | `backend/billing.py` |
| **Modify enterprise seats** | `backend/enterprise.py` — seat catalog, Stripe subscription, per-org seat pool, webhook dispatch |
| Modify membership/storage limits (legacy, being deprecated) | `backend/membership.py` — scheduled for deletion in the flag-flip commit |
| Modify file storage (S3/local) | `backend/storage.py` |
| Read/write paper PDF bytes | `backend/paper_files.py` (handles `storage_path` + legacy `disk_filename` fallback) |
| Modify the annotator | `backend/annotator.py` (tables, field catalog, prompts, analytics) + `frontend/annotator.html` (3-pane UI + tabbed right pane) |
| Add an annotator field group / type-specific / modifier constant | `backend/annotator.py` — `FIELD_GROUPS`, `TYPE_FIELD_IDS`, `DESIGN_MODIFIER_COLS`, `NUMERIC_FIELDS`, `CATEGORICAL_FIELDS` |
| Modify Quality Appraisal AI | `backend/quality_appraisal.py` (registry, orchestrator, GRADE combine, DDL) + `backend/rob_tools/*.py` (RoB tools) + `backend/reporting_guidelines/*.py` (checklists) + `backend/indirectness.py` (GRADE indirectness PICO assessment) + `backend/imprecision.py` (GRADE imprecision single-trial assessment) + `frontend/quality-appraisal.html` |
| Modify Synthesis (systematic review + meta-analysis) | `backend/synthesis.py` (orchestrator: screening/extraction LLM prompts, `run_synthesis`/`run_synthesis_async`, DDL for 6 `synthesis_*` tables, `pool_outcome`/`repool_review`, PRISMA counts, `prompt_catalog`, `flatten_for_export`, credit model) + `backend/synthesis_stats.py` (pure numpy/scipy engine: effect sizes incl. **HR / time-to-event** (from HR+CI, log-HR+SE, or log-rank O-E/V — never a 2×2; pooled via R `metagen`), IV/M-H/RE pooling with DL + REML + **Paule-Mandel** τ² (`tau2_method` ∈ {DL, REML, PM}), heterogeneity Q/I²/τ²/H, Egger + Duval-Tweedie trim-and-fill, subgroup + meta-regression, leave-one-out + influence, `grade_body_of_evidence` combiner) + `backend/synthesis_codegen.py` (emits runnable R `meta`/`metafor` + Python per calculation) + `frontend/synthesis.html` (8-tab workspace + SVG forest/funnel/PRISMA renderers + 3-step create wizard + editable screening/extraction tables + PDF.js detail modal). Reuses the QA RoB tools via `quality_appraisal.appraise_rob_only(...)` (a per-study RoB-only entry point refactored out of `appraise_paper`) and feeds per-study RoB into body-of-evidence GRADE (completing the inconsistency + publication-bias domains QA defers). Each review has many outcomes, each its own meta-analysis. Endpoints: `/api/synthesis/*` in `main.py`. Tests: `tests/test_synthesis_stats.py` (39, validated against the metafor golden fixture g=0.468/I²=75.0%/τ²=0.824, + HR effect sizes and Paule-Mandel τ²) + `tests/test_synthesis_api.py` (11, mocked LLM). **Sharable cross-platform methodology**: [docs/shareable/synthesis_meta_analysis_shareable.md](docs/shareable/synthesis_meta_analysis_shareable.md) (self-contained — all formulas + prompts + a turnkey single-file numpy/scipy reference implementation). |
| Modify RoB 2 cross-over extension | `backend/rob_tools/rob2_crossover.py` (6 domains incl. Domain S period/carryover + 5.4 selective first-period reporting). Registered in `STUDY_TYPE_REGISTRY["Crossover Trial"]` with `reporting_guideline="consort_crossover"`. Reuses parallel-group D1/D2/D3/D4 signal trees from `rob2.py` and adds a fourth Domain 5 question. Frontend: `ROB2_CROSSOVER_DOMAIN_META` + dispatch in `domainMetaFor('rob2_crossover')`. Reference: [docs/quality_appraisal_rob_reference.md](docs/quality_appraisal_rob_reference.md) Section 1B. **Sharable cross-platform methodology**: [docs/shareable/rob2_crossover_shareable.md](docs/shareable/rob2_crossover_shareable.md) (self-contained — signaling questions + decision trees + prompt templates, no framework dependencies). |
| Modify RoB 2 Cluster extension (RoB 2 CRT) | `backend/rob_tools/rob2_cluster.py` (6 domains: 1a randomization + 1b identification/recruitment-timing (cluster-specific, NEW) + 2-5; 18 March 2021 cribsheet). Signal scale is the 5-token Y/PY/PN/N/NI (signaling question 3.2 has no NI). **NA is not an LLM answer** — the LLM answers every question, and `enforce_cascade_*` functions derive NA in code for conditional ("If X to Y") questions whose precondition is not met, after the LLM responds (the ROBINS-I V1 cascade pattern). Decision trees are transcribed independently from the CRT cribsheet flowcharts (they diverge from `rob2.py` — e.g. concealed-but-non-random is *Some concerns* in D1a); only D5 + overall delegate to `rob2.py`. **Domain 2 has two variants** — `assignment` (ITT, 8 signals 2.1a/2.1b/2.2-2.7) and `adhering` (per-protocol, 6 signals 2.1/2.2-2.6) — selected per run by `quality_appraisal_runs.rob2_cluster_aim` (`NULL` = `assignment` default). Plumbed via `appraise_paper(..., rob2_cluster_aim=...)` → `rob2_cluster.run(..., aim=...)`; `run()` records the aim in `rob_domains["aim"]`. Registered in `STUDY_TYPE_REGISTRY["Cluster Randomized Trial"]` with `reporting_guideline="consort_cluster"`. Frontend: `ROB2_CLUSTER_DOMAIN_META` (6 domains) + dispatch in `domainMetaFor('rob2_cluster')` + `<input name="rob2-cluster-aim">` radio in run-create modal + purple `RoB 2 Cluster` chip on result rows + reviewer-selected aim chip in the detail-modal Summary. CSV/XLSX: `flatten_result_row` dispatches `tool == 'rob2_cluster'` to `rob2_cluster.domains_for_aim(rob_domains["aim"])`. **Stepped-Wedge Cluster RCT stays unsupported** — the CRT cribsheet covers only parallel cluster trials. **Sharable cross-platform methodology**: [docs/shareable/rob2_cluster_shareable.md](docs/shareable/rob2_cluster_shareable.md). |
| Modify ROBINS-I V1 (cohort + single-arm) | `backend/rob_tools/robins_i_v1.py` (7 domains × 5-token Y/PY/PN/N/NI scale × 5-level Low/Moderate/Serious/Critical/No information judgement scale; aim-gated Domain 4 with per-path cascade enforcement). **Cohort path**: aim of study is auto-determined by the §1.1 aim preflight — one LLM call asking V2's C4 question (protocol-deviation accounting) and mapping to V1's AIMS (`assignment_to` ITT vs `starting_and_adhering` per-protocol). **Single-arm path (project-specific extension, mirrors V2's single_arm pattern for V1's 5-token vocab)**: variant pinned at `run()` entry from `classification["study_type"] in SINGLE_ARM_STUDY_TYPES`. Replaces the §1.1 aim preflight with a benchmark preflight (B1-SA / B2-SA / B3 + C4 as metadata) — `run_benchmark_preflight`. B2-SA=Y/PY or B3=Y/PY short-circuits to Critical. D1 uses `DOMAIN1_SIGNALS_SINGLE_ARM` (1S.1–1S.5) + `domain1_single_arm_judge` (returns `LOW_D1_SA` = "Low (except for concerns about uncontrolled benchmarking)"); D2 uses `DOMAIN2_SIGNALS_SINGLE_ARM` (2S.1–2S.3) + `domain2_single_arm_judge`; D3, D5, D6, D7 reuse cohort signals + judges unchanged; **D4 is set to NA in code with no LLM call** (V2 retired this domain — its intent-vs-received concerns are folded into D2-SA's 2S.3). V1's 5-token vocab requires a conservative WN/SN/WY/SY collapse documented in the shareable doc §17.3 + §19.7. Co-resident with V2; per-run toggle via `quality_appraisal_runs.robins_i_tool_choice` (`NULL` = V2 default for back-compat, `'robins_i_v1'` opts in). Plumbed via `appraise_paper(..., robins_i_tool_override=...)` which swaps whenever the registry default is `robins_i` — applies to both cohort and single-arm study types now. Frontend: `ROBINS_I_V1_DOMAIN_META` (7 cohort domains) + `ROBINS_I_V1_SA_DOMAIN_META` (7 SA-labelled domains incl. D4 "NA" marker) + dispatch in `domainMetaFor('robins_i_v1', variant)` + `<input name="ri-rob-tool">` radio in run-create modal + amber `ROBINS-I V1` chip on result rows. Detail modal Summary section renders the aim preflight (cohort runs) or the benchmark preflight chips (SA runs). CSV/XLSX: `flatten_result_row` dispatches `tool == 'robins_i_v1'` to `robins_i_v1.DOMAINS` with `signals + signals_single_arm` unioned for D1/D2 so SA columns coexist with cohort columns + emits `robins_v1_aim` + `robins_v1_aim_rationale` (cohort) + `robins_v1_b1/b2/b3/c4/variant/screening_decision/screening_reason` (SA) alongside per-signal columns. **Sharable cross-platform methodology**: [docs/shareable/robins_i_v1_shareable.md](docs/shareable/robins_i_v1_shareable.md) (§19 covers the single-arm adaptation in full). |
| Modify CONSORT cross-over extension | `backend/reporting_guidelines/consort_crossover.py` (base CONSORT 2025 items reused via import + 16 cross-over extension items prefixed `X-` from Dwan et al. 2019). One LLM call per paper; same `adhered/applicable/proportion` shape as `consort2025.py`. Registered in `_GUIDELINE_RUNNERS["consort_crossover"]`. |
| Modify CONSORT cluster extension | `backend/reporting_guidelines/consort_cluster.py` (base CONSORT 2025 items reused via import + 14 cluster extension items prefixed `C-` from Campbell et al. 2012). One LLM call per paper; same `adhered/applicable/proportion` shape as `consort2025.py`. Registered in `_GUIDELINE_RUNNERS["consort_cluster"]`. |
| Reviewer override the assessed outcome | Run-create modal exposes a per-paper "Override outcome" text input. Backend stores it in `quality_appraisal_runs.outcome_overrides_json` and the chosen outcome on `quality_appraisal_results.assessed_outcome` (separate from the auto-pick which stays in `primary_outcome`). When override is in effect, `rob2.run` and `rob2_crossover.run` add a Domain 1 prompt reminder that randomization is per-trial, not per-outcome. Detail modal renders a "Reviewer override" chip in the Summary section. |
| Add a new risk-of-bias tool (…) | New `backend/rob_tools/<tool>.py` exposing `run(pdf_bytes, fields, classification, primary_outcome, progress)` and `prompt_catalog()`, then register in `backend/quality_appraisal.py:STUDY_TYPE_REGISTRY` + `_TOOL_RUNNERS`. Tools that need per-estimate iteration (like QUADAS-3) also expose `extract_estimates(pdf_bytes, fields)` and register in `_ESTIMATE_EXTRACTORS`; the registry entry sets `supports_estimates=True` so `appraise_paper` routes through `_appraise_paper_with_estimates`. Tools whose output is not a GRADE input (like AMSTAR-2) set `skip_grade=True` so `appraise_paper` skips indirectness / imprecision / `compute_grade` |
| Add a new reporting guideline (…) | New `backend/reporting_guidelines/<guide>.py` exposing `run(pdf_bytes, fields, classification)` and `prompt_catalog()`, then register in `backend/quality_appraisal.py:_GUIDELINE_RUNNERS` |
| Modify QUADAS-3 (diagnostic accuracy) | `backend/rob_tools/quadas3.py` (4 domains × 20 signals + applicability + decision tree + estimate extractor) + `backend/reporting_guidelines/stard.py` (STARD 2015 — 34 items including a/b sub-items) + registry entry in `backend/quality_appraisal.py:STUDY_TYPE_REGISTRY["Diagnostic Accuracy"]` (skip_grade_extras=True, supports_estimates=True) + frontend wiring in `frontend/quality-appraisal.html` (`QUADAS3_DOMAIN_META`, applicability column + detail section, Phase-4 estimate selector). Endpoint: `POST /api/quality-appraisal/extract-estimates` |
| Modify QUADAS-2 (diagnostic accuracy, parallel tool) | `backend/rob_tools/quadas2.py` (4 domains × 10 signals + applicability + Y/N/U decision tree). Per-run toggle between QUADAS-2 / QUADAS-3 is plumbed via `quality_appraisal_runs.diagnostic_tool_choice` + `tool_override` kwarg on `appraise_paper(...)`. Reuses `quadas3.extract_estimates` (tool-agnostic) and STARD via `stard.py`. Frontend: `QUADAS2_DOMAIN_META` + `domainMetaFor()` + radio toggle inside the diagnostic-accuracy `<details>` block in `frontend/quality-appraisal.html`. **Sharable cross-platform methodology**: [docs/shareable/quadas2_shareable.md](docs/shareable/quadas2_shareable.md) (self-contained — 4 domains × 11 signaling questions verbatim from Whiting 2011 + decision trees + prompt templates + turnkey single-file reference implementation, no framework dependencies). |
| Modify AMSTAR-2 (systematic reviews) | `backend/rob_tools/amstar2.py` (16 checklist items, each rated Yes / Partial Yes / No — items 11/12/15 also allow "No meta-analysis conducted"; the 7 critical items 2,4,7,9,11,13,15 drive an overall confidence rating of High / Moderate / Low / Critically low via `amstar2_overall`). The LLM answers each item's Y/N sub-criteria; `amstar2_item_judge` derives the item rating (logic types `all_required` / `one_of` / `tiered` / `rob_design` / `meta_design`). A 1-call preflight determines `review_includes` (rct/nrsi/both — items 9+11 are design-aware) and `meta_analysis`; meta-gated items 11/12/15 are set to "No meta-analysis conducted" in code when no synthesis was performed (no LLM call — the RoB 2 Cluster NA-cascade pattern). Registered in `STUDY_TYPE_REGISTRY["SR with Meta-Analysis"]` and `["SR without Meta-Analysis"]` with `reporting_guideline="prisma2020"`, `initial_grade=None`, `skip_grade=True`. GRADE / indirectness / imprecision are skipped — AMSTAR-2's confidence rating is the headline output, stored in `rob_overall`. Frontend: `AMSTAR2_DOMAIN_META` (16 items) + dispatch in `domainMetaFor('amstar2')` + tool-aware `robBadgeCls(j, tool)` (its labels collide with the RoB scale) + magenta `AMSTAR-2` chip + the grid hides the GRADE/indirectness/imprecision columns for SR runs. CSV/XLSX: `flatten_result_row` dispatches `tool == 'amstar2'` to `amstar2.ITEMS` + emits `amstar2_confidence` / `amstar2_review_includes` / `amstar2_meta_analysis` / `amstar2_critical_flaws` / `amstar2_noncritical_weaknesses` columns. **Sharable cross-platform methodology**: [docs/shareable/amstar2_shareable.md](docs/shareable/amstar2_shareable.md) (self-contained — 16 items × signaling sub-criteria + decision logic + overall-confidence algorithm + prompt templates + turnkey single-file reference implementation, no framework dependencies). |
| Modify PRISMA 2020 reporting checklist | `backend/reporting_guidelines/prisma2020.py` (27 PRISMA 2020 items with a/b/c… sub-items = 42 entries, from Page et al. BMJ 2021;372:n71). One LLM call per paper; same `adhered/applicable/proportion` shape as `strobe.py`. Registered in `_GUIDELINE_RUNNERS["prisma2020"]`. |
| Modify GRADE indirectness logic | `backend/indirectness.py` (PICO subdomains, severity decision tree, prompts) + `backend/quality_appraisal.py:compute_grade` (combines RoB + indirectness + imprecision downgrades) |
| Modify GRADE imprecision logic | `backend/imprecision.py` (CI / N / events / fragility subdomains, severity decision tree, prompts) + `backend/quality_appraisal.py:compute_grade` (combines RoB + indirectness + imprecision downgrades) |
| Reference: RoB 2 + ROBINS-I V2 + QUADAS-3 prompts + decision trees | `docs/quality_appraisal_rob_reference.md` — verbatim transcription of every signaling question, elaboration, and pure-Python decision tree from `prompt_catalog()` for all three tools (including ROBINS-I V2 cohort variants A/B + single-arm adaptation). For sharing without cloning. **Sharable cross-platform methodology** (with turnkey single-file Python reference implementations): [docs/shareable/robins_i_v2_shareable.md](docs/shareable/robins_i_v2_shareable.md) for V2 (20 Nov 2025 cribsheet, 6 domains × A/B/single-arm variants), [docs/shareable/robins_i_v1_shareable.md](docs/shareable/robins_i_v1_shareable.md) for V1 (1 Aug 2016 cribsheet, 7 domains, aim-gated D4 — used by the OVID team and other ongoing V1 workflows). |
| Add a new lab agent | `backend/agents/lab_agents.py` + `backend/skills.py` (prompt) + `backend/lab.py` (routing) |
| Modify lab chat/sessions | `backend/lab.py` |
| Modify exports | `backend/exports.py` |
| Modify the daily scheduler | `backend/scheduler.py` + `backend/pubmed.py` |
| Modify search | `backend/search.py` |
| Modify search-result PDF import (6 modes) | `backend/search.py` (`import_results`, `import_results_extension`, `run_pdf_fetch_job`, `_upgrade_paper_to_pdf`) + `backend/pdf_fetcher.py` (PMC → Unpaywall → direct → meta-tag → Firecrawl, with per-strategy events + retries + tier-aware return) + `backend/browser_agent.py` (Playwright Chromium + LLM-driven link picker). Modal UI mirrored in `frontend/search.html` and `frontend/lab.html`. **`auto`** is the default — runs every tier, tier-priced 2/5/15 cr. **`extension`** queues for the user's paired Chrome extension (free). |
| Modify Chrome extension (PDF fetch via authenticated browser) | `backend/extension.py` (pairing, queue, upload, skip, status) + `backend/pdf_link_picker.py` (shared LLM picker — also used by `browser_agent.py`) + extension/ dir (`manifest.json`, `background.js`, `content.js`, `popup.html|js|css`). Pair via `/developers`. Tests in `tests/test_extension.py`. |
| Add a new PDF-fetch strategy | `backend/pdf_fetcher.py` — write a `_strat_*` helper that returns `(result_or_None, outcome, reason)` where `outcome ∈ {hit, miss, transient_error, permanent_error}`, then call it from `fetch_pdf_for_result` via `_run_with_retry(name, on_event, lambda attempt: _strat_*(...))`. Validate downloads via `_is_pdf_bytes`. Pass `attempts=1` for slow / metadata-driven strategies. Tag the tier when emitting the hit (`_hit(out, "free"|"firecrawl"|"browser")`). |
| Modify evidence-synthesis agents (ASCO tables) | `backend/synthesis/` — pure-Python, model-optional building blocks for ASCO guideline evidence tables. **Table 2** (per-study evidence table): `table2.py` (pure calc/assembly core) + `table2_extract.py` (outcomes extraction wiring); methodology in [docs/shareable/table2_evidence_table_shareable.md](docs/shareable/table2_evidence_table_shareable.md). **Pooling / meta-analysis** (body-of-evidence "T5" input, Component B): three modules. `pooling.py` — model-free pooling engine (**uses numpy + scipy.stats**; not restricted to stdlib): inverse-variance fixed/random-effects pooling with DL/REML/PM τ², heterogeneity (Q/I²/τ²/prediction interval), effect-size computation per measure (OR/RR/RD from 2×2; MD/SMD from mean/SD/N; **IRR from events + person-time `time_int`/`time_ctrl` — NOT from a 2×2 count table, dropped with a named warning if only counts given**; any measure from a pre-computed estimate+CI), Egger + trim-and-fill; all returned values are plain Python floats (JSON-safe). `pooling_prep.py` — the **extraction→pool bridge** (pure): `pool_extractions(studies)` absorbs many studies' `outcomes[]`, groups them into bodies of evidence (one per outcome × comparison × timepoint × design-class — RCTs never share a body with non-randomized studies; **unrecognized designs → `unknown` body, quarantined + flagged in the body's `warnings`, never merged with rct/nrs**), picks the measure (majority reported metric / override / inferred), maps each to a pooling input (raw arm data preferred, else reported effect+CI), excludes unreconcilable metrics with a named reason, propagates per-outcome `favorable_direction` into `pool_outcome`, and routes each body to `pool_outcome`. **Comparison strings are matched lexically only (no synonym harmonization — unlike outcomes); canonicalize upstream if worded differently.** `pooling_extract.py` — **dual-mode** (like Table 2's injected-vs-isolation): `pool_studies(items)` is the primary entry — per study it uses the extraction agent's `outcomes[]` when poolable (`study_is_poolable` gate → zero model calls) and **self-extracts from `pdf_bytes` as a fallback** when they're missing (`prepare_study` → `extract_outcome_data`, one model call, reuses `annotator._call_with_pdf`); `pool_from_pdfs()` is an always-extract shim. The extraction prompt captures 2×2 counts, **events + person-time (`time_int`/`time_ctrl`, `outcome_type:"rate"`) for IRR**, and mean/SD; the bridge's raw-data mapping is target-aware (IRR body takes person-time arms, never a 2×2; person-time arms infer IRR when no metric reported). Also hosts the LLM outcome-name clusterer (`cluster_outcome_names_map`, one text call via `helpers.call_anthropic`) + `harmonize_outcomes(...)`. `pooling_harmonize.py` — **outcome harmonization** (pure): maps differently-worded outcomes onto one `canonical_outcome` label so synonyms group together — `harmonize_by_targets(studies, targets)` (reviewer dictionary/alias, exact-normalized + conservative token-subset/Jaccard fuzzy match, returns unmatched report) + `apply_canonical_map` / `build_alias_index` / `match_outcome_name` / `clusters_to_map`. `group_into_bodies` keys on `canonical_outcome` when present. `pool_studies(outcome_targets=[...], harmonize_llm=True)` layers dictionary-first then LLM-for-the-gaps. Tests: `tests/test_pooling.py` + `tests/test_pooling_prep.py` + `tests/test_pooling_harmonize.py`. The shareable doc also ships a dependency-free variant (stdlib shims) for forks without scipy. Top-level `pool_outcome(studies, measure)`; GRADE hand-off via `grade_pooling_inputs(result)` (raw numbers, no certainty decisions — that is the separate GRADE agent). **Do not pool RCTs with non-randomized studies in one call** — call once per body of evidence. Tests: `tests/test_pooling.py`. **Sharable cross-platform methodology**: [docs/shareable/pooling_meta_analysis_shareable.md](docs/shareable/pooling_meta_analysis_shareable.md) (mirrored in the `synthesis-` repo as `Sharable_pooling_meta_analysis_agent.md`). |
| Modify the GRADE agent (evidence certainty / ASCO "T5") | `backend/synthesis/grade.py` (pure decision engine — **stdlib only**, consumes a `pooling.pool_outcome` result: starting certainty by design, the 5 downgrade + 3 upgrade domains as inspectable decision functions, `GradeConfig` thresholds, the certainty combiner with `overrides`, `absolute_effects`, and the `sof_row` Summary-of-Findings assembler) + `backend/synthesis/grade_prep.py` (pure bridge — joins per-study RoB labels by `study_id` onto the pooled `studies[]`, weighted; `grade_bodies(...)`) + `backend/synthesis/grade_indirectness.py` (the ONLY model call — hybrid body-level indirectness auto-assessor, reuses `backend/indirectness.py`'s PICO severity tree; runs only when the reviewer omits `indirectness_levels`) + `backend/synthesis/grade_assess.py` (orchestrator — `grade_from_pooled` / `grade_from_studies`, resolves hybrid indirectness) + `backend/synthesis/grade_agent.py` (HTTP glue `grade_certainty`/`sof` + `GRADE_TABLES_SQL` persistence: `grade_runs`/`grade_results`/`grade_events`). Endpoints in `main.py`: `POST /api/agents/grade` + `/api/agents/grade-sof` (stateless), `POST /api/grade/runs` + `GET /api/grade/runs[/{id}]` + `.../events` + `/{id}/csv|/xlsx` + `DELETE` (persisted). Page: `frontend/grade.html` (route `/grade`). **Natural-scale CI in, natural-scale out** (null = 1.0 ratios / 0.0 differences); **grade RCT and non-randomized evidence as separate bodies** (upgrades gated to NRS with no downgrades). Tests: `tests/test_grade.py` + `tests/test_grade_api.py`. **Sharable cross-platform methodology**: [docs/shareable/grade_certainty_shareable.md](docs/shareable/grade_certainty_shareable.md). |
| Modify the per-study evidence table (ASCO Table 2) | `backend/synthesis_table2.py` (pure calc/assembly core, stdlib-only — study-id building, metric canonicalization, direction inference, statistical reconciliation, quality-rating mapping, row explosion, dedupe, dual-mode `assemble_table2`) + `backend/synthesis_table2_extract.py` (outcomes-extraction wiring; reuses `annotator._call_with_pdf`). **One row = study × outcome × comparison × timepoint**, transcribing each study's REPORTED results — no pooling/synthesis (that is the meta-analysis engine in `backend/synthesis_stats.py`). **Dual-mode**: assemble from injected extraction tags (zero model calls) OR extract in isolation. Tests: `tests/test_synthesis_table2.py`. **Sharable cross-platform methodology**: [docs/shareable/table2_evidence_table_shareable.md](docs/shareable/table2_evidence_table_shareable.md). |
| Add organization feature | `backend/organizations.py` |
| Write Obsidian notes | `backend/obsidian.py` |
| Run tests | `pytest tests/ -v` (requires Python 3.12 for `str | None` syntax) |

## Architecture at a Glance

```
main.py (~8,600 lines)
  ├── Database init + migrations (lines 120-700)
  ├── Auth: cookie sessions + X-API-Key header
  ├── Page routes: GET /, /lab, /challenges, /search, /library, /community-library, /annotator, /quality-appraisal, /review, etc.
  ├── API endpoints grouped by resource
  └── Static files mount: /static → frontend/

backend/
  ├── db.py             — Database compatibility layer (PostgreSQL + SQLite fallback)
  ├── helpers.py        — LLM callers (Anthropic with optional thinking_budget, Gemini, OpenAI-compatible), JSON parsing
  ├── challenges.py     — Challenge orchestration, scoring, points, leaderboard, event logging.
  │                       run_challenge() batches >3 PDFs (size 3), retries each batch up to 3×
  │                       on transient failures, splits domain_composition proportionally per batch.
  ├── agents/
  │   ├── generator.py  — Rubric generation from PDFs (system prompt in skills.py)
  │   ├── judge.py      — Judge 1 (Anthropic). Now also defines run_second_judge (OpenAI w/ Claude fallback).
  │   ├── adjudicator.py — Judge 3 (Gemini) + majority-of-3 vote + 3-way-split detection
  │   ├── participants.py — Run competing models against rubric
  │   └── lab_agents.py — Lab agent runners
  ├── review.py         — Grade-review queue (3-way splits go here for human resolution)
  ├── lab.py            — Lab session CRUD, chat orchestrator, document management
  ├── storage.py        — S3/local file storage abstraction
  ├── paper_files.py    — Paper-file read/write/delete (uses storage.py + legacy fallback)
  ├── annotator.py      — OGAI Annotator: tables, field catalog, AI prompts, analytics,
  │                       log_run_event() helper for per-batch progress streaming
  ├── exports.py        — Export converters (Word, LaTeX, Excel, CSV, Python, R)
  ├── code_runner.py    — Sandboxed Python/R code execution
  ├── pubmed.py         — PubMed E-utilities, iCite citations, PMC PDF download
  ├── scheduler.py      — Daily challenge automation (7am PST Mon-Fri); stamps source='pubmed'
  ├── search.py         — AI search chatbot, PubMed/Europe PMC, import/export; stamps source='search';
  │                       4-mode PDF import (metadata / fetch / firecrawl / browser) +
  │                       run_pdf_fetch_job background worker that upgrades metadata-only papers in place
  ├── pdf_fetcher.py    — Best-effort PDF resolver. Pipeline: PMC → Unpaywall (free OA index) →
  │                       direct GET → citation_pdf_url meta tag → Firecrawl JS-render fallback.
  │                       Browser-spoofing UA + Referer header for paywalled publishers (BMJ/NEJM/Wiley).
  ├── browser_agent.py  — Final-tier Playwright/Chromium fetcher. Opens publisher landing in a real
  │                       browser, picks up cookies, locates citation_pdf_url or "Download PDF" links,
  │                       grabs bytes from the same context. Slow + RAM-hungry; opt-in via mode='browser'.
  ├── billing.py        — Stripe credits, cost estimation, refunds
  ├── membership.py     — Free/Pro/Enterprise plans (legacy, deprecated under ENTERPRISE_MODE)
  ├── enterprise.py     — Enterprise seat catalog, Stripe subscription, per-org seat pool
  ├── organizations.py  — Multi-tenant orgs with roles
  ├── templates.py      — Rubric templates, community library
  ├── analytics.py      — Performance breakdown, CSV/PDF export (challenge benchmarks)
  ├── skills.py         — Agent skill versioning (10 agent types)
  ├── self_improve.py   — Autoresearch experiment loop
  ├── obsidian.py       — Markdown vault writer
  ├── quality_appraisal.py — RoB + reporting-guideline + indirectness + imprecision + GRADE pipeline (registry, orchestrator, compute_grade combiner; `appraise_rob_only` reused by Synthesis)
  ├── synthesis.py      — Synthesis: systematic review + meta-analysis orchestrator (screen → extract → RoB → pool → body-of-evidence GRADE; 6 synthesis_* tables, events polling, credit model)
  ├── synthesis_stats.py — pure numpy/scipy meta-analysis engine (effect sizes, FE/RE pooling, heterogeneity, publication bias, subgroup/meta-regression, sensitivity, GRADE combiner)
  ├── synthesis_codegen.py — emits runnable R (meta/metafor) + Python per meta-analysis calculation for the Analysis tab
  ├── indirectness.py   — GRADE indirectness — single-trial PICO assessment (4 subdomains, severity decision tree)
  ├── imprecision.py    — GRADE imprecision — single-trial CI / N / events / fragility (4 subdomains, severity decision tree)
  ├── rob_tools/
  │   ├── rob2.py            — RoB 2 (parallel-group RCTs, 5 domains, 2019 cribsheet)
  │   ├── rob2_crossover.py  — RoB 2 cross-over extension (6 domains incl. Domain S period/carryover + Domain 5 question 5.4)
  │   ├── rob2_cluster.py    — RoB 2 Cluster extension / RoB 2 CRT (6 domains: 1a + 1b identification/recruitment-timing + 2-5; 18 Mar 2021 cribsheet; Domain 2 has ITT + per-protocol variants selected per run via `quality_appraisal_runs.rob2_cluster_aim`)
  │   ├── robins_i.py        — ROBINS-I V2 (20 Nov 2025 cribsheet, follow-up cohort studies; also dispatched for case-control / cross-sectional as approximation, and for Single-Arm Trial / Dose-Escalation Study via the adapted single_arm variant of D1 + D2)
  │   ├── robins_i_v1.py     — ROBINS-I V1 (1 Aug 2016 cribsheet, 7 domains, aim-gated D4; opt-in per run via `quality_appraisal_runs.robins_i_tool_choice='robins_i_v1'`. Cohort path auto-determines the Stage-II aim via the §1.1 aim-preflight LLM call. Single-arm path — project-specific extension — uses a benchmark preflight (B1-SA/B2-SA/B3) + D1-SA/D2-SA signals + D4=NA in code; selected when `study_type ∈ SINGLE_ARM_STUDY_TYPES`.)
  │   ├── quadas2.py         — QUADAS-2 (2011) + quadas3.py — QUADAS-3 v1.2 (diagnostic accuracy, per-run toggle)
  │   └── amstar2.py         — AMSTAR-2 (Shea 2017, systematic reviews — 16 checklist items, overall confidence rating; no GRADE)
  ├── reporting_guidelines/
  │   ├── consort2025.py       — CONSORT 2025 (parallel-group RCTs, 30 items)
  │   ├── consort_crossover.py — CONSORT 2025 base + Dwan et al. 2019 cross-over extension (30 + 16 items)
  │   ├── consort_cluster.py   — CONSORT 2025 base + Campbell et al. 2012 cluster extension (+ 14 items prefixed C-)
  │   ├── stard.py             — STARD 2015 (diagnostic accuracy, 34 items)
  │   ├── strobe.py            — STROBE 2007 (observational designs)
  │   └── prisma2020.py        — PRISMA 2020 (systematic reviews, 27 items / 42 entries with sub-items)
  ├── agreements.py     — Legal text
  └── promo.py          — Promo codes

frontend/ — ~26 self-contained HTML files (inline CSS + JS, no build step).
            Notable additions: library.html (personal PDF library),
            community-library.html (formerly library.html, moved on /community-library),
            review.html (3-judge adjudication review queue UI).
tests/    — pytest suite — ~336 cases across Competition API, Annotator, Quality Appraisal,
            Adjudication, Indirectness (42 in `tests/test_indirectness.py`), Imprecision
            (55 in `tests/test_imprecision.py`).
            Run with `pytest tests/ -v` (Python 3.12+).
```

## Critical Patterns

### Authentication
Two methods, both checked by `require_user()`:
1. **Session cookie**: `rubricgen_session` (browser login)
2. **API key header**: `X-API-Key: rg_user_xxx` (programmatic access)

Competition API uses a separate `X-Model-Key: rg_model_xxx` header.

### Database Access
```python
from backend.db import get_db, IntegrityError, column_exists

conn = get_db()        # Returns PgConnection (PostgreSQL) or SqliteConnection (fallback)
try:
    # ... queries — use ? placeholders (auto-converted to %s for PostgreSQL) ...
finally:
    conn.close()       # ALWAYS close — forgetting causes connection leaks
```

### Adding New Tables
1. Define SQL in your backend module using **PostgreSQL syntax**: `SERIAL PRIMARY KEY`, `CURRENT_TIMESTAMP`, no `COLLATE NOCASE`
2. In `main.py:init_db()`, add: `conn.executescript(MY_TABLES_SQL)`
3. For ALTER TABLE migrations, use `column_exists(conn, "table", "column")` from `backend/db.py`
4. The `SqliteConnection` wrapper auto-converts PG DDL back to SQLite at runtime

### Frontend Pages
Every page is self-contained: `<style>` + `<body>` + `<script>` in one file. The nav bar is duplicated across all 18 files — changes must be applied to ALL of them. Use an agent to batch-edit.

### Background Tasks
Challenges run on daemon threads (`threading.Thread`). Progress is logged to `challenge_events` table and polled by the frontend every 5 seconds.

## Known Gotchas

| Issue | Details |
|-------|---------|
| **DDL syntax** | Write all DDL in PostgreSQL syntax (`SERIAL PRIMARY KEY`, `CURRENT_TIMESTAMP`). The `SqliteConnection` wrapper converts automatically for local dev. |
| **Parameter markers** | Always use `?` — the PgConnection wrapper converts to `%s` for PostgreSQL. |
| **INSERT OR IGNORE** | Use `INSERT INTO ... ON CONFLICT DO NOTHING` (works in both PostgreSQL and SQLite 3.24+). |
| **RETURNING id** | Add `RETURNING id` to INSERTs that use `cur.lastrowid`. The SQLite wrapper strips it automatically. |
| **IntegrityError** | Import from `backend.db`, not `sqlite3`. |
| **conn.close() ordering** | Query the DB BEFORE calling `conn.close()`. The connection object becomes invalid after close. |
| **max_tokens** | Generator/Judge/Participant agents need 16384 tokens for 40+ question rubrics. Default 4096 truncates JSON. |
| **OA filter** | `pubmed.py:search_pubmed()` has `apply_oa_filter` param. Default is False (broad search). Setting True returns 0 results for most queries. |
| **Python version** | Codebase uses `str | None` union syntax (Python 3.10+). Tests require Python 3.12. |
| **Nav duplication** | The topbar nav is copy-pasted in all 18 HTML files. Use batch edit (agent) for changes. |
| **Circular imports** | `backend/helpers.py` exists to break circular imports. Agents import from helpers, not from main. |
| **PDF batching** | Generator can't handle >3 PDFs in one API call (context window). `run_challenge()` batches into groups of 3. |
| **Annotator oversized-PDF fallback** | `backend/annotator.py:_call_with_pdf` has a 3-stage pipeline: PDF-as-document → pypdf text extraction → chunked map-reduce (parallel 300k-char windows, first-non-empty merge). Classify + schema-proposal pass `classification_mode=True` so they only use the first chunk. 32 MB upload cap + friendly 413s for all failure modes. See the "Large-file extraction pipeline" section below. |
| **No vendor names in user-facing errors** | The annotator treats its AI backend as a black-box "extraction service". Any `HTTPException` message must use generic terms (`the AI model`, `the extractor`, `AI service error`) — never `Claude`, `Anthropic`, `Gemini`, `OpenAI`, etc. Vendor names are OK in `logger.*` calls (server logs only). `backend/helpers.py:call_anthropic` and `call_gemini` already translate vendor error bodies into generic 413/502 responses. |
| **Admin bypass** | Admins skip credit checks on challenge runs. Regular users need credits. |
| **Column named `timestamp`** | The SQLite compat wrapper in `backend/db.py` case-insensitively rewrites `TIMESTAMP` → `TEXT`, which **also clobbers any column literally named `timestamp`**. Use `updated_at` / `created_at` etc. The annotator's `annotations` table was renamed for exactly this reason. |
| **Paper file access** | Always go through `backend/paper_files.py:read_paper_bytes(row, PAPERS_DIR)` — it picks S3 or local automatically. Direct `PAPERS_DIR / disk_filename` works for legacy rows only and will fail on new S3-backed uploads. |
| **Search results need DB ids** | `save_results` in [backend/search.py](backend/search.py) MUST use `RETURNING id` and stamp `a["id"] = cur.lastrowid` onto each article before returning. Frontend checkboxes bind to `data-id="${r.id}"` — without this the IDs are undefined and Import Selected silently sends an empty array (looks like a dead button). |
| **Browser-agent RAM on Render** | `backend/browser_agent.py` boots Chromium per call (~500MB). Render Free (512MB) will OOM-kill the worker. Need Standard ($25/mo, 2GB) for `mode='browser'` to work in production. Also needs `playwright install chromium` in build command + system libs in [apt.txt](apt.txt) (libnss3, libatk*, libcups2, etc.). |
| **Bot detection on paywalled publishers** | BMJ / NEJM / Wiley / Springer 403 anything that looks like a bot. [backend/pdf_fetcher.py](backend/pdf_fetcher.py) sends a Chrome User-Agent + Accept-Language for the main httpx client, and retries with `Referer: <landing>` when the first GET still 403s. Don't change the UA back to `TheRubricGenerator/1.0` — that hard-blocks at the door. The polite UA is reserved for Unpaywall (where it's required). |
| **Annotator iframe chrome** | When the annotator is opened from the Lab it loads in an iframe. Elements tagged `tb-chrome` in the annotator's topbar get hidden via `.in-iframe` CSS. Don't tag annotator-specific action buttons (Batch, Save, Export CSV) with `tb-chrome` or they'll disappear inside the Lab. |
| **Annotator form tab must stay in DOM** | `renderSpans()` looks up `getElementById('spans-' + fieldName)`. The right-pane tabs use `display: none` to hide inactive panes — do NOT remove them from the DOM or span linking breaks. |
| **LLM JSON parsing** | Use `backend/helpers.py:parse_json_response(raw)` — it strips markdown fences Claude sometimes wraps JSON in. Don't `json.loads` raw Anthropic output directly. |

## Enterprise Seat Model (being rolled out; flag-gated)

The legacy individual Free/Pro/Enterprise plans are being replaced by an enterprise-only, seat-based model. Rolled out in 5 commits (`1a`–`4b`) and gated behind `ENTERPRISE_MODE` (default `"0"` — inert). When `ENTERPRISE_MODE=1`, the system enforces seat-based access and the legacy `/api/membership/*` UI is dead.

**Seat pricing** (source of truth: [backend/enterprise.py:SEAT_TYPES](backend/enterprise.py)):
- **Admin** — $450/mo + 500 credit floor, rank 3 (full org control)
- **Engineer** — $250/mo + 300 credit floor, rank 2 (create/run challenges, rubrics, lab, annotator)
- **General** — $100/mo + 100 credit floor, rank 1 (annotator + view)

**Stripe model:** one `Subscription` per org, three `SubscriptionItem`s (one per seat type) with quantity = purchased pool size. Stripe is the canonical source for quantities; our `enterprise_subscriptions` table mirrors state via webhook. Monthly bundled credits grant on `invoice.paid` via `_grant_monthly_credits`.

**Distinct roles** — don't confuse them:
- **Platform admin** (`users.role='admin'`) — Thomas, the operator. Bypasses every seat check. Unchanged by this rollout.
- **Enterprise owner** — the user who created an enterprise org; `org_members.is_owner=1`. Unique per org. Controls billing (seat-qty changes, subscription cancel). Always holds an admin seat.
- **Enterprise admin** — any member with `org_members.role='admin'`. Manages members + seat assignments but not billing cancellation.
- **Engineer / General** seat-holders — their role is their seat type.

**Role migration (Phase 1b, already shipped):** `org_members.role` moved from `{viewer, contributor, admin}` → `{general, engineer, admin}`. Migration function `organizations.migrate_to_seat_vocab(conn)` is idempotent, runs in `init_db`, works on both PG and SQLite. Legacy → new mapping: viewer→general, contributor→engineer, admin→admin. `is_owner` column added + backfilled from `organizations.created_by`.

**Access gating:** `main.py:require_active_seat(user, min_seat, org_id)` sits next to `require_user`. When `ENTERPRISE_MODE=0` it's a no-op returning `{bypass:True, pre_flag:True}` — safe to call from anywhere today. When the flag flips:
- Platform admin bypasses.
- Unseated users get `402 {error:'no_active_seat', redirect:'/onboarding'}`.
- Held seat ranked below `min_seat` gets `403 {error:'insufficient_seat', required, held}`.
- `past_due` subs honored for 7 days past `current_period_end` (grace window).

**Endpoints** (all in `main.py`, handlers in [backend/enterprise.py](backend/enterprise.py)):
- `POST /api/enterprise` — create org + Stripe Checkout (caller becomes owner+admin seat)
- `GET /api/enterprise/{org_id}` — consolidated state (org, sub, seats, credits)
- `PATCH /api/enterprise/{org_id}/seats` — owner-only; adjusts subscription item qty
- `POST /api/enterprise/{org_id}/members` — admin; 409 `pool_full`
- `PATCH /api/enterprise/{org_id}/members/{user_id}` — admin; 409 `pool_full`, 403 if owner
- `DELETE /api/enterprise/{org_id}/members/{user_id}` — admin; 403 if owner
- `POST /api/enterprise/{org_id}/sync` — reconcile from Stripe after a webhook drop
- Credit pack overage reuses `POST /api/orgs/{org_id}/billing/checkout` (existing)

**Frontend:** `/onboarding` (`frontend/onboarding.html`) for unseated users — join with invite code or start an enterprise; `/enterprise/{id}` (`frontend/enterprise.html`) for owners + admin-seat users — seat pool management, members table, cost card. `frontend/billing.html` plan grid is gone; shows an enterprise banner that routes to `/enterprise/{id}` or `/onboarding` depending on seat state. `frontend/login.html:postAuthRedirect()` routes to `/onboarding` when `me.needs_onboarding` is true.

**Required env vars:**
- `STRIPE_PRICE_SEAT_ADMIN`, `STRIPE_PRICE_SEAT_ENGINEER`, `STRIPE_PRICE_SEAT_GENERAL` — set after running [scripts/setup_enterprise_stripe.py](scripts/setup_enterprise_stripe.py), which provisions the Products and Prices in Stripe and prints them for you to paste.
- `ENTERPRISE_MODE=1` — flip this last, after cutover.

**Cutover checklist (follow-up work, not yet shipped):**
1. [scripts/setup_enterprise_stripe.py](scripts/setup_enterprise_stripe.py) — run against Stripe test, then live, paste env vars.
2. [scripts/cancel_legacy_subscriptions.py](scripts/cancel_legacy_subscriptions.py) — dry-run first, then real run; marks every active Pro/Enterprise individual sub as `cancel_at_period_end=True`. Leaves accounts + data intact.
3. ~~Endpoint gating audit~~ **DONE.** 141 `require_active_seat(user, min_seat=…)` calls sprinkled across `main.py`: `general` for reads / light actions, `engineer` for paper upload / challenge create / rubric generator / lab run / annotator classify/prefill/schema-run / model register / project create / search execute / compete submit, `admin` for model-member and org-member CRUD. Legacy `check_pdf_limit` / `check_storage_limit` calls are now wrapped in `if not enterprise_mod.ENTERPRISE_MODE:` so they stay active while the flag is off but skip when it flips (otherwise seated users whose legacy Pro subs were cancelled would fall back to the Free-tier PDF cap). Compete routes (`/api/compete/*`) use model API keys and are intentionally not seat-gated — models are not users.
4. **Nav sprinkle** — add `<a id="nav-enterprise" href="/enterprise" style="display:none">Enterprise</a>` to the ~18 remaining HTML files' topbar nav, plus a JS toggle `if (me.seat && (me.seat.is_owner || me.seat.seat_type === 'admin')) document.getElementById('nav-enterprise').style.display='';`.
5. **402 global interceptor** — optional; today each page's `loadMe` / auth check already redirects on `me.needs_onboarding`. A shared fetch wrapper would catch 402s from mid-session API calls too.
6. Delete `backend/membership.py` + `/api/membership/*` routes + `membership_plans` / `user_memberships` tables after a 30-day soak.
7. Flip `ENTERPRISE_MODE=1` in Render env.

## OGAI Annotator

Lives at `/annotator` (route in `main.py`, UI in `frontend/annotator.html`, backend in `backend/annotator.py`). Reuses `papers`, `projects`, `require_user`, credits, and `call_anthropic` — no parallel user system.

**Three field layers:**
- **Layer 1 (universal)** — `UNIVERSAL_FIELD_IDS` + `FIELD_GROUPS` (citation / objective / population / sample / setting / outcomes / results / admin).
- **Layer 2 (type-specific)** — `TYPE_FIELD_IDS` keyed by study type (RCT, Cohort, Meta-Analysis, etc.).
- **Layer 3 (cross-cutting)** — `DESIGN_MODIFIER_COLS` (clinical_trial_phase, industry_sponsored, …).

`NUMERIC_FIELDS` + `CATEGORICAL_FIELDS` drive chart selection in the Analytics tab.

**Data model:** `annotations` (per paper+reviewer, optimistic-concurrency `version` column, `updated_at` not `timestamp`), `annotation_spans` (text→field linkages), `annotator_custom_schemas` (per-user named schemas), `annotator_custom_runs` (the **batch container** for every Classify / Prefill / Custom run — see below), `annotator_run_events` (per-paper progress events, polled by the UI), and `annotator_actions` (per-paper action audit log). All initialised from `ANNOTATOR_TABLES_SQL` in `backend/annotator.py`, executed by `main.py:init_db()`.

**Batch container model** (every classify/prefill/custom batch creates one row): `annotator_custom_runs` carries `id, user_id, name (REQUIRED, unique-per-user, ≤120 chars), project_id, did_classify, did_prefill, schema_id, schema_snapshot_json, paper_ids_json, results_json, status, created_at, completed_at, name`. The frontend `runBatch()` flow:
1. `POST /api/annotator/runs` with `{name, project_id, paper_ids, did_classify, did_prefill, schema_id?}` — creates the container; **400** on missing name, **409** on duplicate per-user. Frontend ABORTS the batch loudly if this fails (no silent runs).
2. `PATCH /api/annotator/runs/{rid}/papers` after each per-paper classify or prefill — merges per-paper output into `results_json.papers[pid].fields`.
3. The optional `POST /api/annotator/schemas/{sid}/run` accepts `run_id=` to reuse the same container (the worker's `_mark` MERGES into existing `results_json` rather than overwriting).
4. `POST /api/annotator/runs/{rid}/finalize` — marks complete + emits a `run_complete` progress event.
5. `PATCH /api/annotator/runs/{rid}` — moves an existing run between projects (or renames it). Body: `{project_id, project_id_set: true, name?}`. The `project_id_set` flag distinguishes "explicitly clear to null" from "leave unchanged."

**Right-pane tab bar:** `Form` / `✨ Custom` / `Results` / `Analytics ↗`. Form uses `display: none` when inactive so span linking keeps working. Active tab persists in `localStorage` under `annotator_active_tab` (allowed values: `form` / `chat` / `results`). The `Analytics ↗` button is NOT a pane — it navigates to `/analytics#annotator`. All annotator analytics live in the unified analytics page ([frontend/analytics.html](frontend/analytics.html)) which has tabs for Benchmark / Annotator / Admin analytics; hash routing (`#annotator`, `#admin`) selects the initial tab.

**Results tab — pivoted batch summary view:** when a batch row is selected, `renderRunTable()` builds (top to bottom) a summary card (name + project pill + op chips + status + ok/err/skip counts) → study-type stacked bar (when `did_classify`) → field summary card grid (one per `field_aggregates` entry; numeric / categorical / text rendering varies) → the existing per-paper `rt-table`. Each field card opens a **field-detail modal** (value-frequency bars or numeric stats; click a row to load that paper into the Form tab); each paper row has a 📋 button that opens a **paper-detail modal** (every extracted field as label/value rows, plus an "Open in Form tab" button). Aggregates are computed server-side by `_compute_run_aggregates(snapshot, results, did_classify)` in `main.py` — kind classifier: numeric (≥80% parse as float) / categorical (≤8 unique AND ≤60-char max) / text (everything else); summaries include median+min+max for numeric, top+top_pct+value_counts for categorical, n_unique+sample_values for text.

**Batch run-list:** the runs are listed (chronological, latest-first, capped at 100) as a flat scrolling list with name, status badge, paper count, project pill, op chips, and timestamp. Each row has a `+ Save to project…` `<select>` for moving the run between projects. The list also has a filter input that searches name / project / status / schema name client-side.

**Browser notifications + active-runs pill:** the topbar gains a purple "▶ N runs in progress" pill when there are live containers (visible across page refresh — `pickupInFlightRuns()` re-attaches pollers on load). Clicking the pill opens a modal with a per-run live event log. When a `run_complete` event arrives and the tab isn't focused, `fireBatchNotification(ev)` shows a desktop notification — permission is asked lazily on the first batch start (`ensureNotificationPermission()`).

**Per-paper progress events** stream from `_run_custom_extraction` via `_log_run_event_safe(run_id, event_type, message, **detail)` (calls `backend/annotator.py:log_run_event`). Event types: `run_started` / `paper_started` / `extracting` / `paper_done` / `paper_error` / `paper_skipped` / `run_complete` / `paper_thinking`. Frontend polls `GET /api/annotator/runs/{rid}/events?after=<id>` every 3s via `streamRunEvents()`.

**Multi-project paper membership** — papers belong to many projects via `paper_projects` (`paper_id, project_id, added_at`). The legacy `papers.project_id` is kept as a "primary" pointer for back-compat; the junction is the source of truth for sidebar filtering and the Library page. `assignPaperToProject()` mirrors writes into the junction. Sidebar paper items render project chips with ✕ to remove + a native `<select class="proj-add-select">` "+ Add to project…" dropdown (chosen over a button-with-popover after repeated discoverability complaints — native widgets are unmissable). Empty state shows an "unassigned" pill.

**AI calls (all credit-gated, admin bypass, auto-refund on failure):**
- Classify study design: 3 credits — `POST /api/annotator/papers/{id}/classify`
- Prefill fields: 8 credits — `POST /api/annotator/papers/{id}/prefill` (accepts `groups`, `type_fields`, `modifier_fields`)
- Parse custom schema from upload/text: 2 credits — `POST /api/annotator/schemas/parse` (accepts PDF, DOCX — converted to markdown via `python-docx` — CSV, TXT, MD, or raw text)
- Refine custom schema: 1 credit — `POST /api/annotator/schemas/refine`
- Custom batch run: 8 credits × paper — `POST /api/annotator/schemas/{id}/run` (≤10 papers in-request, larger = background thread). With `thinking_enabled: true`, cost bumps ~50% per paper and Claude's extended thinking is captured per-paper into `paper_thinking` events (rendered as collapsible blocks in the batch modal).

**Unified extraction entry point:** the Custom tab's "▶ Run extraction (batch)" button and the topbar "☰ Batch" button both open the same batch modal. That modal has three optional steps: (1) classify study design, (2) prefill Form-tab fields, (3) run a saved custom schema. Opening from the Custom tab preselects the currently-loaded schema in the modal's "Custom schema" dropdown; opening from the topbar leaves it empty. There is no separate "run this custom schema alone" code path — custom runs always happen via `runBatch()` in [frontend/annotator.html](frontend/annotator.html).

**Saved schemas are clickable:** `renderSchemaList()` renders each row as a role=button that calls `loadSchemaIntoBuilder(id)` on click/Enter/Space. The active schema gets a "loaded" chip; the ✕ button uses `event.stopPropagation()` so it doesn't fire the row click.

**Large-file extraction pipeline (`backend/annotator.py:_call_with_pdf`):** Every annotator AI call (classify / prefill / custom / schema-parse) funnels through this function. It degrades gracefully through three stages:

1. **PDF-as-document (fast path).** Base64-encode the PDF and attach as an Anthropic document block. Covers ~95% of papers.
2. **Text fallback.** If stage 1 raises the context-window 413, we run `_extract_pdf_text()` (pypdf, already in requirements.txt) to pull plain text out of the PDF. Image-heavy papers that were ~200k tokens as a document are usually well under 100k as plain text.
3. **Chunked map-reduce.** If the extracted text is still too large, we split into overlapping 300k-char windows (`_CHUNK_CHAR_SIZE`, `_CHUNK_OVERLAP=8000`) and run them in parallel via `concurrent.futures.ThreadPoolExecutor` (max 4 workers, 8 chunks). Per-chunk JSON responses are merged in `_merge_extractions`: for each field, the earliest non-empty value wins — earlier chunks carry abstract/methods/results, which is where authoritative values typically live. Failed chunks are logged and skipped; if all chunks fail we raise a clean 502.

Classification tasks (`classify_study_design`) and the schema-proposal task (`parse_schema_from_pdf`) pass `classification_mode=True`, which short-circuits stage 3 to first-chunk-only — chunk-and-merge doesn't make sense for a single holistic output (study type / proposed field list). Regular prefill and custom extraction use the full merge pipeline.

Upfront: a 32 MB byte-size guard (`_PDF_UPLOAD_BYTE_LIMIT`) short-circuits before we even base64-encode, since Anthropic's PDF beta rejects anything larger. All error paths raise `HTTPException` with actionable vendor-free messages, and the existing `_refund_annotator` / `refund_credits` handlers auto-refund the credit charge.

**Persistence:** localStorage draft (not sessionStorage, so it survives tab close) + backend save on `input` (1.5 s debounce) + `keepalive: true` fetch on `beforeunload` / `pagehide` / `visibilitychange → hidden` / `logout()`. `loadExistingAnnotation` prefers the local draft over the backend copy, so unsent edits come back even if the network save failed.

## Personal Library

Lives at `/library` ([route in main.py](main.py), UI in [frontend/library.html](frontend/library.html)). The community library was renamed to `/community-library` (file moved to `community-library.html`) — `/library` is now the user's personal PDF browser.

**Layout:** left filter rail (search, project, annotation status, source) + responsive card grid. Each card shows filename, upload date, source badge, project chips, annotation status, and a 4-stat strip (rubrics, evaluations, challenges, custom runs). Cards are clickable → opens `/annotator?paper_id=N` (annotator picks up the param and auto-loads the paper). The toolbar exposes select-multi → "Add to project…" / "Delete" bulk actions.

**Data**: `GET /api/library/papers?project=&source=&status=&q=&limit=` aggregates membership, annotation status, rubric/eval/challenge/custom-run counts in a single call. Uses correlated subqueries — fine for personal libraries up to a few thousand papers. Source filter values: `upload | lab | search | pubmed | imported`. The `papers.source` column is stamped at INSERT time by every uploader (annotator upload → 'upload', search import → 'search', PubMed scheduler → 'pubmed', Lab upload → 'lab'). Lab uploads dual-write to `papers` so the same PDF appears in both `lab_documents` and the unified Library.

**Source backfill** — `init_db()` runs an idempotent backfill that copies any `lab_documents` rows missing a `papers_id` cursor into `papers` (with synthetic sha256 `lab:{user_id}:{lab_doc_id}` since `lab_documents` never stored a hash). The `lab_documents.papers_id` column is the migration cursor — once set, the row is considered migrated and skipped on subsequent startups.

## 3-Judge Adjudication Pipeline

Single-judge grading was the original design; production hit too many borderline cases where Claude's score was contested. The adjudication pipeline replaces it with a sequential 3-judge majority vote that escalates only on disagreement.

**Pipeline** (escalates lazily — no extra LLM calls when judges already agree):
1. **Judge 1 (Anthropic)** — `backend/agents/judge.py:run_judge_agent`. Always runs.
2. **Judge 2 (OpenAI)** — `backend/agents/judge.py:run_second_judge`. Runs only on per-question disagreement; falls back to a second Claude call if `OPENAI_API_KEY` is missing. The `shadow_regrade` name is preserved as a thin alias for un-migrated call sites.
3. **Judge 3 (Gemini)** — `backend/agents/adjudicator.py:run_third_judge`. Runs only when judges 1 and 2 still disagree on a question.
4. **Majority vote** — `adjudicator.majority_vote(j1, j2, j3)` picks the score that two of three agree on, per question.
5. **3-way split** — when no two judges agree on a question's score, the adjudicator emits a `needs_review` payload and `backend/review.py:enqueue_review` drops the question into the review queue for human resolution.

**Review queue** ([backend/review.py](backend/review.py) + [frontend/review.html](frontend/review.html)) — moderators see the question, rubric criteria, and all three judge grades side-by-side; pick the winning score (or override entirely with a manual annotation), and resolution is logged in the audit trail. Tests in [tests/test_adjudication.py](tests/test_adjudication.py) cover the pure-Python parts (majority logic, 3-way-split detection, needs_review payload shape) without hitting any LLM.

## Rubric Generator (April 26 hardening)

Three small but production-meaningful changes shipped in the rubric generator orchestration:

- **Default model** ([backend/helpers.py:20](backend/helpers.py)) bumped from `claude-sonnet-4-20250514` to `claude-sonnet-4-6`. Override per-environment via `ANTHROPIC_MODEL`. For premium generator quality, set `ANTHROPIC_MODEL=claude-opus-4-7` on the relevant Render service — `call_anthropic(model=)` already supports per-call overrides if you want to keep judge cheap.
- **Retry loop on the batched generator** ([backend/challenges.py](backend/challenges.py): `_generator_with_retry`) — wraps each `run_generator_agent()` call in 3 attempts with 1s/2s backoff. Skips retry on permanent errors (400, 401, 403, 413). Both the single-call and batched paths use it now, so a transient 5xx or JSON parse blip no longer kills the whole challenge.
- **Domain composition split per batch** ([backend/challenges.py](backend/challenges.py): `_split_composition_for_batches`) — when a >3-PDF challenge gets batched, the daily composition / domain composition is now divided proportionally per batch using **largest-remainder allocation**, so per-key totals are exact across the batches (no rounding drift). Replaces the previous behavior where composition was silently dropped in batched mode.

## Quality Appraisal AI

Lives at `/quality-appraisal` ([route in main.py](main.py), UI in [frontend/quality-appraisal.html](frontend/quality-appraisal.html), backend in [backend/quality_appraisal.py](backend/quality_appraisal.py) + [backend/rob_tools/](backend/rob_tools/) + [backend/reporting_guidelines/](backend/reporting_guidelines/) + [backend/indirectness.py](backend/indirectness.py) + [backend/imprecision.py](backend/imprecision.py)). Reuses the annotator's `classify_study_design`, `prefill_fields`, `_call_with_pdf` (3-stage oversize fallback), `load_paper_pdf`, credits, and `require_active_seat` — no parallel user/paper system.

**Pipeline per paper** (≈ 10 LLM calls for RCTs, ≈ 12 for non-randomized designs — 5–7 RoB domain calls + classify + prefill + guideline + indirectness + imprecision; flat **36 credits** per paper):
1. Classify study design via annotator.
2. Extract universal + type-specific + modifier fields via annotator.
3. Auto-pick primary outcome from `primary_outcome_definition` → `primary_outcome_measurement` → `population_outcomes`.
4. Per-domain LLM calls for the registered RoB tool — **pure-Python decision trees** map signal answers (`Y/PY/PN/N/NI` plus V2-only `WN/SN/WY/SY`) to tool-specific judgements (RoB 2 is 3-level Low/Some concerns/High; **ROBINS-I V2 is 4-level Low/Moderate/Serious/Critical** — V1's "No information" judgement was retired; Domain 1's "Low" is labelled "Low (except for concerns about uncontrolled confounding)" for cohort variants or "Low (except for concerns about uncontrolled benchmarking)" for the single-arm variant; QUADAS-3 is 3-level Low/High/Insufficient information). For ROBINS-I V2 a preflight LLM call also answers B1/B2/B3 + C4 — B2=Y/PY (or B2-SA=Y/PY for single-arm) or B3=Y/PY short-circuits to Critical, and C4 dispatches Domain 1 to Variant A (ITT) or Variant B (per-protocol) for cohort studies. **For Single-Arm Trial / Dose-Escalation Study** (no comparator), `run_preflight()` uses an alternate single-arm prompt — B1/B2 are replaced with benchmark-pre-specification questions (B1-SA/B2-SA) — and the variant is pinned to `"single_arm"` based on study type BEFORE preflight. The single_arm variant rewrites Domain 1 (benchmark adequacy + prognostic-mix comparability, 5 questions 1S.1–1S.5) and Domain 2 (intervention fidelity + intent-vs-received cohort definition, 3 questions 2S.1–2S.3); D3–D6 reuse cohort signals + judges unchanged. Trees live in code (not prompts) so the developer view can show the exact logic via `inspect.getsource`.
5. Single-call adherence check against the registered reporting guideline.
6. **GRADE indirectness** — single LLM call via `backend/indirectness.py` judging each PICO subdomain (population/intervention/comparator/outcome) on a 4-level scale, then a pure-Python severity decision tree → 0/1/2/3 GRADE downgrade levels. Conditioned on the user's optional target PICO; falls back to outcome-surrogacy assessment when not supplied.
7. **GRADE imprecision** — single LLM call via `backend/imprecision.py` judging four subdomains (CI width / sample size / event count / fragility) on a 4-level scale, then the same severity decision tree → 0/1/2/3 GRADE downgrade levels. Conditioned on the user's optional MID thresholds (`mid_benefit`, `mid_harm`); falls back to line-of-no-effect + clinical-importance reasoning when not supplied. Event-count subdomain is N/A for continuous outcomes (excluded from severity counting via the `n_a → precise` normalization).
8. Compute initial GRADE (from registry) + updated GRADE after RoB **+ indirectness + imprecision** (sum of downgrade levels, capped at "Very low"). Other GRADE domains (inconsistency, publication bias) still require a body of evidence and are out of scope.

**Extensibility contract** — [backend/quality_appraisal.py:STUDY_TYPE_REGISTRY](backend/quality_appraisal.py) is the single source of truth mapping `{study_type → (rob_tool, reporting_guideline, initial_grade, [skip_grade_extras], [supports_estimates], [skip_grade])}`. v1 supports:
- **Randomized Controlled Trial → RoB 2 (2019) + CONSORT 2025 + High initial GRADE**
- **Crossover Trial → RoB 2 cross-over extension + CONSORT cross-over (Dwan 2019) + High initial GRADE.** Adds Domain S (period/carryover).
- **Cluster Randomized Trial → RoB 2 Cluster / RoB 2 CRT (2021) + CONSORT cluster (Campbell 2012) + High initial GRADE.** Adds Domain 1b (identification/recruitment timing); Domain 2 has ITT + per-protocol variants selected per run via `quality_appraisal_runs.rob2_cluster_aim`.
- **Cohort Study / Case-Control / Non-Randomized Trial / Cross-Sectional (Analytical) / Case-Crossover → ROBINS-I V2 (20 Nov 2025 cribsheet) + STROBE 2007 + Low initial GRADE.** V2 is published explicitly for follow-up/cohort studies; the other designs use V2 as a best-available approximation pending design-specific tooling.
- **Single-Arm Trial / Dose-Escalation Study → ROBINS-I V2 single_arm variant (default) OR ROBINS-I V1 single_arm variant (opt-in via the V1 toggle) + STROBE 2007 + Very low initial GRADE.** Uncontrolled designs start at the lowest GRADE level (no comparator → more severe than confounded comparison); `compute_grade` clamps further downgrades at Very low. The V2 single_arm variant reframes D1 (benchmark adequacy) + D2 (intervention fidelity / cohort definition) while reusing D3–D6 cohort signals unchanged. The V1 single_arm variant (project-specific extension; mirrors V2's pattern for V1's 5-token vocab + 5-level scale) reframes D1 (1S.1–1S.5) + D2 (2S.1–2S.3) the same way and marks D4 as NA in code (V2 retired that domain entirely); D3, D5, D6, D7 reuse cohort signals unchanged. Per-run toggle via `quality_appraisal_runs.robins_i_tool_choice`. Dose-Escalation shares the SA variant wholesale on both V1 and V2 — MTD/DLT/RP2D-specific bias is not modeled in v1.
- **Diagnostic Accuracy → QUADAS-2 (2011) or QUADAS-3 v1.2 (per-run toggle, QUADAS-3 default) + STARD 2015 + High initial GRADE** (per-estimate path; indirectness + imprecision skipped)
- **SR with Meta-Analysis / SR without Meta-Analysis → AMSTAR-2 (Shea 2017) + PRISMA 2020 + no GRADE.** AMSTAR-2 scores 16 checklist items and emits an overall confidence rating (High / Moderate / Low / Critically low), not a GRADE certainty — the registry entry sets `skip_grade=True` and `initial_grade=None`, and `appraise_paper` skips indirectness, imprecision, and `compute_grade` entirely. The confidence rating is stored in `rob_overall`.

Registry keys MUST match `annotator.TYPE_FIELD_IDS` keys — the test `TestDispatch::test_registry_keys_match_annotator_types` enforces this. Unsupported study types return `None` → the paper is marked `skipped`, credits refund. Adding a new study type: add a registry entry + new module in `backend/rob_tools/` (for the tool) and/or `backend/reporting_guidelines/` (for the guideline), each exposing `run(...)` and `prompt_catalog()`, then register the callable in `_TOOL_RUNNERS` / `_GUIDELINE_RUNNERS`. Tools that need per-estimate iteration (one paper → many result rows) also expose `extract_estimates(pdf_bytes, fields)`, register in `_ESTIMATE_EXTRACTORS`, and set `supports_estimates=True` on the registry entry — `appraise_paper` then routes through `_appraise_paper_with_estimates` which loops the RoB+GRADE pass per estimate while running classify/prefill/guideline once per paper.

**Credit gate + refund**: pre-charge via `_annotator_ai_gate`; refund per-paper on error/skip via `billing.refund_credits` — same idiom as custom annotator runs. Admin bypasses.

**Background execution**: inline for ≤3 papers, daemon thread for larger batches via [backend/quality_appraisal.py:run_batch_async](backend/quality_appraisal.py). Progress is logged to `quality_appraisal_events` and polled every 5s by the frontend (`GET /runs/{id}/events?after=<last_id>` returns incremental events).

**Developer view** (🔧 icon in topbar, visible to every signed-in user) — `GET /api/quality-appraisal/prompts` returns the full prompt templates, signaling questions, and `inspect.getsource` output for every decision tree + GRADE logic. Transparency by default: reviewers can see exactly how a judgement was produced.

**DB tables** (initialised from `QUALITY_APPRAISAL_TABLES_SQL` + idempotent `migrate_qa_columns(conn)` for the post-launch indirectness + imprecision columns): `quality_appraisal_runs` (incl. `target_pico_json` for the user-supplied PICO and `imprecision_thresholds_json` for MID benefit/harm), `quality_appraisal_results` (incl. `indirectness_json`/`overall`/`levels`/`explanation` and `imprecision_json`/`overall`/`levels`/`explanation`), `quality_appraisal_events`. All date columns use `created_at` / `completed_at` (no `timestamp` column per the SQLite compat-wrapper gotcha). Runs are soft-deleted via `deleted_at`.

**Endpoints** (seat tiers match the annotator's): `GET /api/quality-appraisal/supported-types` (general), `GET /api/quality-appraisal/prompts` (general, the dev view — surfaces both the indirectness and imprecision `prompt_catalog`s), `POST /api/quality-appraisal/runs` (engineer; body accepts optional `target_pico: {population, intervention, comparator, outcome}` for indirectness and optional `imprecision_thresholds: {mid_benefit, mid_harm}` for imprecision), `GET /api/quality-appraisal/runs` (general), `GET /api/quality-appraisal/runs/{id}` (general — response includes `target_pico` and `imprecision_thresholds` on the run + `indirectness_*` and `imprecision_*` fields per result), `GET /api/quality-appraisal/runs/{id}/events?after=<id>` (general, incremental poll), `GET /api/quality-appraisal/runs/{id}.csv|.xlsx` (general — exports include 8 indirectness + 8 imprecision columns), `DELETE /api/quality-appraisal/runs/{id}` (general, soft delete).

**Detail view** — each row in the results grid is clickable (📋 icon on the study cell, plus each RoB domain / Indirectness / Imprecision / CONSORT / GRADE cell). The grid carries dedicated **Indirectness** and **Imprecision** columns between RoB and the reporting-guideline column, each showing the severity badge (`None` / `Serious` / `Very serious` / `Extremely serious`) plus a `−N GRADE` subtext when the run downgraded. Clicking opens a full-screen split modal: **PDF.js viewer on the left** (loaded from `/api/papers/{pid}/pdf`, canvas + text layer per page) and a scrollable **detail panel on the right** with Summary → RoB (5 collapsible RoB 2 domains or 6 ROBINS-I V2 domains or 4 QUADAS-3 domains) → **Indirectness** (`qa-sec-indirectness` — severity badge + 4-cell PICO grid with green/yellow/orange/red Figure-2-style colouring + per-subdomain rationale + surrogate-outcome callout) → **Imprecision** (`qa-sec-imprecision` — severity badge + 4-cell subdomain grid with cyan/blue/orange/red palette + per-subdomain rationale + sample-size context note showing extracted N / events / CI summary; event-count cell renders "N/A — continuous outcome" when the LLM marks it n_a) → Reporting guideline (CONSORT 2025 or STROBE 2007 or STARD 2015, grouped by section, ✓/✗/N-A) → GRADE (initial → updated with combined RoB + indirectness + imprecision downgrade explanation and domain breakdown) → Extracted fields. Clicking any rationale or evidence chip **searches the live PDF text layer** for the first ~80 chars of the quote (with a longest-matching word n-gram fallback for paraphrased quotes) and flash-highlights the match. Prior highlight clears on next click; the clicked chip gets a yellow "active" marker. Quote-to-highlight is best-effort — we never asked the LLM for PDF coordinates, so the fallback may miss for heavily paraphrased quotes (toast surfaces the miss). The frontend looks up `domainMetaFor(r.rob_tool, variantOf(r))` to pick between `ROB2_DOMAIN_META` (5 domains), `ROBINS_I_DOMAIN_META` (6 cohort V2 domains), `ROBINS_I_SA_DOMAIN_META` (6 single-arm domains with reframed D1/D2 labels), and `QUADAS3_DOMAIN_META` (4 diagnostic-accuracy domains, 3 with applicability). `variantOf(result)` reads `result.rob_domains.preflight.variant`. `robBadgeCls(j)` maps any judgement (3-level RoB 2, 4-level ROBINS-I V2 with both Low-except-… variants recognized, or 3-level QUADAS-3 including "Insufficient information") to a badge CSS class. For QUADAS-3 runs the detail modal also includes Applicability + Estimate descriptor sections between RoB and STARD. See [frontend/quality-appraisal.html](frontend/quality-appraisal.html) `openDetailModal`, `renderDetailPanel`, `loadDetailPdf`, `jumpToQuote`.

**Mixed-tool runs** — if a run includes papers of different study types (some RCT + some Cohort), the results grid column set is taken from the first successful row's tool. Other rows with a different tool still render correctly; domain cells for non-matching domain IDs show `—`. Single-design runs are the common case, so this trade-off is intentional for v1.

**Out of scope for v1**: Quasi-experimental designs (Uncontrolled Before-After, Interrupted Time Series, Difference-in-Differences, Regression Discontinuity) — each needs its own confounding prompt + ROBINS-I V2 adaptation. Stepped-Wedge Cluster RCT (the RoB 2 CRT cribsheet covers only parallel cluster-randomized trials — stepped-wedge needs an additional time-trend domain). QUADAS-C (comparative accuracy reviews — separate tool from QUADAS-3). Editing / overriding AI judgements in the UI. Other GRADE domains beyond RoB, indirectness, and imprecision — inconsistency and publication bias — still require a body of evidence and are deferred. Per-outcome user selection for treatment trials (we auto-pick primary; for diagnostic accuracy, the user can select multiple Phase-4 estimates via QUADAS-3's per-estimate UI). Note: V2 retired V1's "Bias due to deviations from intended intervention" domain entirely; protocol-deviation issues are folded into Domain 1 Variant B (time-varying confounding) which the preflight selects when the analysis estimates the per-protocol effect. **Single-arm v1 caveats:** Dose-Escalation-specific bias (MTD declaration adequacy, DLT definition, RP2D justification, expansion-cohort selection) is not modeled — Dose-Escalation reuses the single-arm variant wholesale. No single-arm-specific reporting guideline module yet — STROBE is reused pragmatically; a `phase2_singlearm` checklist may be added in a follow-up. The new 1S.*/2S.* signal IDs join the existing CSV/XLSX column union — old cohort runs will show empty cells for those columns, and SA runs show empty cells for the A/B variant columns.

## QUADAS-3 v1.2 — Diagnostic Test Accuracy

Lives in [backend/rob_tools/quadas3.py](backend/rob_tools/quadas3.py) + [backend/reporting_guidelines/stard.py](backend/reporting_guidelines/stard.py). Extends the RoB-tool contract with per-estimate iteration + dual RoB/applicability assessment.

**4 domains × 3-level scale** (Low / High / Insufficient information):
- **D1 Participants** — 4 signals (single-gate design, prospective enrolment, consecutive sampling, intended-use representativeness) + applicability
- **D2 Index Test** — 4 signals (recommended instructions, blinding to reference, in-practice information, threshold pre-specification) + applicability
- **D3 Target Condition** — 8 signals (reference-standard adequacy, full vs partial verification, differential verification, incorporation bias, reference-standard conduct + blinding + threshold + interval) + applicability
- **D4 Analysis** — 4 signals (all participants in analysis, missing-data handling, unit of analysis, sens/spec calculation) — **RoB only, no applicability**

**Decision tree** ([backend/rob_tools/quadas3.py:quadas3_domain_judge](backend/rob_tools/quadas3.py)) — conservative interpretation of Phase 5: all Y/PY → Low; any N/PN → High; otherwise → Insufficient information. The QUADAS-3 docx narratively allows reviewer judgement to keep a domain at Low even with one or more N/PN signals, but baking that judgement into a deterministic tree would be arbitrary, so we surface the per-signal rationale and let reviewers override in their own write-up.

**Phase 6 overall** — same 3-level rule applied to (a) all 4 domain RoB judgements → `overall_rob`, and (b) the 3 applicability-bearing domains → `overall_applicability`. The `run()` return is a 4-tuple: `(domain_results, overall_rob, "NA", overall_applicability)`. The 3rd slot is always "NA" — direction-of-bias is a treatment-trial concept that doesn't apply to diagnostic accuracy.

**Per-estimate path** — `cfg.supports_estimates=True` routes `appraise_paper` through `_appraise_paper_with_estimates`. Classify + prefill + STARD run once per paper; QUADAS-3 + GRADE run once per estimate. Each estimate produces a separate `quality_appraisal_results` row (same `paper_id`, distinct `estimate_id`). Cost = 36 cr per (paper, estimate) unit. Refunds are also per-unit — `summary["estimates_errored"]` tells `run_batch` how many units to refund.

**Phase-4 estimate selection** — `quadas3.extract_estimates(pdf_bytes, fields)` does a single LLM call asking for every numerical sens/spec result tuple in the paper (subgroup × index test × threshold × reference standard × unit of analysis). Surfaced via `POST /api/quality-appraisal/extract-estimates` (3 cr per call, auto-refund on error). The run-create modal shows an "Extract Phase-4 estimates" button that pre-populates per-paper estimate checklists. If the user submits without extracting, QUADAS-3 falls back to a single-estimate iteration against the paper's primary / headline estimate — same idiom as the RCT primary-outcome auto-pick.

**Phases 1+2 review context** — collected as a single optional textarea on the run-create modal (`quadas3_review_context` on the run row). Threaded into D1/D2/D3 applicability prompts so the LLM can judge "concern that the as-conducted study does not match the ideal trial" against the reviewer's stated synthesis question + ideal-trial design. Without this context, applicability is judged against a generic intended-use baseline.

**GRADE for diagnostic accuracy** — `_rob_downgrade` handles the QUADAS-3 outcome scale (Low → 0; High single-domain → 1, ≥2 High → 2; Insufficient information → 1 conservatively). `compute_grade` is called with `indirectness_levels=0, imprecision_levels=0` for QUADAS-3 because `cfg.skip_grade_extras=True` — the existing indirectness + imprecision modules assume PICO/treatment trials, not PIRT (Patient / Index test / Reference standard / Target condition), so we defer them rather than produce garbage. Initial GRADE starts at "High" for diagnostic accuracy (cross-sectional accuracy default per the GRADE handbook).

**STARD 2015 reporting checklist** — 34 entries (30 numbered items with a/b sub-items at 10/12/13/21). Single LLM call per paper; same shape as STROBE / CONSORT. N/A items (e.g., adverse events for non-invasive imaging studies, registration for retrospective records reviews) excluded from the proportion denominator.

**Frontend** — [frontend/quality-appraisal.html](frontend/quality-appraisal.html) gains: `QUADAS3_DOMAIN_META` (4 entries with `hasApplicability` flag) dispatched via `domainMetaFor('quadas3')`; an `Insufficient information` → `rob-ni` badge mapping; an Applicability column conditionally shown when any row in the run uses QUADAS-3 (with N/A cells for non-QUADAS rows in mixed runs); the Indirectness + Imprecision columns hidden on QUADAS-3-only runs; an Applicability section + Estimate descriptor section in the per-paper detail modal between RoB and STARD; per-estimate descriptor chip in the study cell; result-id-based detail navigation (`openDetailModalById(rowId, anchor)`) so multi-estimate papers correctly route per-row clicks.

**Out of scope for v1 QUADAS-3**: per-estimate domain-difference shortcut from the docx ("After the first estimate, only domains where characteristics are different need to be assessed" — every estimate runs all 4 domains in v1); structured Phase 1 + Phase 2 inputs (collected as one free-text field instead); QUADAS-C comparative accuracy; PIRT-aware indirectness + imprecision (deferred to v2); editing / overriding AI judgements in the UI.

## QUADAS-2 (2011) — parallel diagnostic-accuracy tool

Lives in [backend/rob_tools/quadas2.py](backend/rob_tools/quadas2.py). Co-resident with QUADAS-3 v1.2 — users pick per run via a radio toggle in the run-create modal. QUADAS-3 is the default; QUADAS-2 is the classic 2011 tool (Whiting PF et al., Ann Intern Med 2011;155:529-536) that most published systematic reviews still use.

**Tool contract** — same `run(pdf_bytes, fields, classification, primary_outcome, progress, *, estimate, review_context)` signature as `quadas3.run`, same 4-tuple return `(domains, rob_overall, "NA", app_overall)`, registered in `_TOOL_RUNNERS["quadas2"]`. `_ESTIMATE_EXTRACTORS["quadas2"]` is aliased to `quadas3.extract_estimates` — numerical sens/spec extraction is RoB-tool-agnostic, so a single shared extractor keeps the prompt single-source. A test (`TestQuadas2Dispatch.test_quadas2_estimate_extractor_aliases_quadas3`) pins this identity.

**4 domains, 10 signaling questions** (transcribed verbatim from Whiting 2011 Table 1):
- **D1 Patient Selection** — 3 signals (consecutive/random sample; case-control avoided; inappropriate exclusions avoided) + applicability
- **D2 Index Test** — 2 signals (blind to reference standard; threshold pre-specified) + applicability
- **D3 Reference Standard** — 2 signals (correctly classifies target condition; blind to index test) + applicability
- **D4 Flow & Timing** — 4 signals (appropriate interval; all received reference; same reference for all; all in analysis) — **RoB only, no applicability**

**3-level scale** — signal answers `Y / N / U`; domain RoB and applicability judgements `Low / High / Unclear`. Distinct from QUADAS-3's 5-level Y/PY/PN/N/NI scale and Insufficient-information judgement.

**Decision tree** ([backend/rob_tools/quadas2.py:quadas2_domain_judge](backend/rob_tools/quadas2.py)) — conservative interpretation of Whiting 2011 Phase 4: all Y → Low; any N → High; otherwise → Unclear. QUADAS-2 narratively allows reviewers to override a single-N domain back to Low if judged immaterial; the deterministic tree keeps the inspector logic pure and surfaces per-signal rationales for human override in write-up.

**Per-run tool selection** — `quality_appraisal_runs.diagnostic_tool_choice` (TEXT NULL; NULL = QUADAS-3 default for back-compat). `POST /api/quality-appraisal/runs` accepts `diagnostic_tool_choice: 'quadas2' | 'quadas3'`. `appraise_paper(..., tool_override=...)` shallow-copies the `cfg` dict and rewrites `cfg["rob_tool"]` for diagnostic-accuracy papers only; the registry is never mutated (thread-safe with `run_batch_async`). Override is also restricted to `study_type == "Diagnostic Accuracy"` so a stray param can't reroute an RCT to a QUADAS tool.

**Review-question framing** — QUADAS-2 frames applicability against the **review question in PIRT terms** (Patient / Index test / Reference standard / Target condition). QUADAS-3 frames it against an "ideal test accuracy trial" (Phases 1+2 of the v1.2 docx). The DB column `quadas3_review_context` is retained — its meaning is tool-tagged via `diagnostic_tool_choice`. The frontend dynamically swaps the textarea placeholder + label when the radio flips (`onDxToolChange()` in `frontend/quality-appraisal.html`).

**GRADE downgrade** — `_rob_downgrade` has a new branch: `Unclear → 1 level` (conservative; reason text mentions "QUADAS-2"). `Low` and `High` reuse the existing RoB 2 branches; the `≥2 High domains → 2 levels` rule applies symmetrically. `compute_grade` skips indirectness + imprecision for diagnostic-accuracy papers per `cfg["skip_grade_extras"]` (registry entry unchanged from QUADAS-3).

**Frontend** ([frontend/quality-appraisal.html](frontend/quality-appraisal.html)) — adds:
- Radio toggle inside the renamed `<details>` summary "Diagnostic accuracy (QUADAS-2 / QUADAS-3) (optional)". Default QUADAS-3. The toggle is reset on every modal open.
- `QUADAS2_DOMAIN_META` (4 entries, D4 has `hasApplicability:false`) dispatched via `domainMetaFor('quadas2')`.
- `isDxToolRow(r)` helper that returns `true` for both QUADAS variants — used in 9+ sites that previously hardcoded `r.rob_tool === 'quadas3'`.
- `ROB_BADGE_CLS["Unclear"] = 'rob-ni'` — reuses the yellow QUADAS-3 `rob-ni` style.
- Tool chip in the classification cell: amber `QUADAS-2` chip or blue `QUADAS-3 v1.2` chip, visible at a glance for audit.
- Detail-modal applicability section, estimate descriptor section, STARD heading, sub-nav RoB label all widened to handle both tools. Header note + N/A note swap between "review question (PIRT)" and "ideal test accuracy trial" / "Analysis vs Flow & Timing" based on which tool produced the row.

**Mixed dx-tool rows are not supported** — a single run uses one tool for all diagnostic-accuracy papers (the radio is per-run, not per-paper). The frontend chooses the column header set from the first successful row; mixed QUADAS-2 + QUADAS-3 in one run isn't constructible via the UI.

**CSV/XLSX export** — `flatten_result_row` dispatches `tool == 'quadas2'` to `quadas2.DOMAINS` for the per-domain + per-signal columns. Column union with existing QUADAS-3 runs is automatic — QUADAS-2 emits `rob_d1_judgement`, `rob_d1_applicability`, `rob_1.1`, `rob_1.2`, `rob_1.3`, `rob_d2_judgement` etc.; old QUADAS-3 rows show empty cells for the QUADAS-2-specific signal IDs and vice versa (same precedent as ROBINS-I V2 single-arm columns).

**Out of scope for v1 QUADAS-2**: review-specific tailoring of signaling questions (Phase 2 of Whiting 2011) — we use the canonical core questions for every review; structured PIRT input fields (collected as one free-text textarea, same idiom as QUADAS-3); reviewer override of a single-N domain back to Low via the UI; QUADAS-C (comparative accuracy reviews — a separate tool).

## AMSTAR-2 — critical appraisal of systematic reviews

Lives in [backend/rob_tools/amstar2.py](backend/rob_tools/amstar2.py) + [backend/reporting_guidelines/prisma2020.py](backend/reporting_guidelines/prisma2020.py). Source: Shea BJ et al., "AMSTAR 2: a critical appraisal tool for systematic reviews," BMJ 2017;358:j4008. Registered for **both** `SR with Meta-Analysis` and `SR without Meta-Analysis` (AMSTAR-2 covers reviews with or without meta-analysis — items 11/12/15 carry a "No meta-analysis conducted" path).

**Structurally unlike the primary-study tools.** AMSTAR-2 scores **16 checklist items**, not 4–7 bias domains. Each item is rated **Yes / Partial Yes / No** (some items Yes/No only; items 11/12/15 also allow **No meta-analysis conducted**). The headline output is an **overall confidence rating** — High / Moderate / Low / Critically low — *not* a GRADE certainty. The registry entry sets `skip_grade=True` + `initial_grade=None`; `appraise_paper` skips indirectness, imprecision, and `compute_grade` for SR papers. The confidence rating lands in `quality_appraisal_results.rob_overall`; `initial_grade` / `updated_grade` stay NULL. **No DB migration was needed** — the existing nullable columns absorb AMSTAR-2.

**Per-item scoring** — the LLM answers each item's Y/N signaling sub-criteria (transcribed from the AMSTAR-2 checklist + guidance document); a pure-Python decision tree (`amstar2_item_judge`) derives the item rating. Logic types: `all_required` (Yes iff every sub-criterion Y), `one_of` (Yes iff any), `tiered` (Partial Yes = all `tier:"partial"` sub-criteria; Yes = those + all `tier:"yes"`), `rob_design` (item 9 — tiered, evaluated per included design; "both" → lower rating), `meta_design` (item 11 — Yes/No, design-aware). Decision trees live in code (not prompts) so the developer view shows the exact logic via `inspect.getsource`.

**Overall confidence** (`amstar2_overall`, Shea 2017 algorithm) — a *critical flaw* is a critical item rated "No"; a *non-critical weakness* is a non-critical item rated "No" ("Partial Yes" and "No meta-analysis conducted" are not flaws). High = 0 critical + ≤1 non-critical; Moderate = 0 critical + >1 non-critical; Low = exactly 1 critical; Critically low = ≥2 critical. The 7 critical items are the **published default set** (items **2, 4, 7, 9, 11, 13, 15**), hardcoded — v1 does not support per-run custom critical sets.

**Preflight** — one LLM call (`run_preflight`) determines `review_includes` (rct / nrsi / both — items 9 and 11 have design-specific sub-criteria) and `meta_analysis` (was a quantitative synthesis performed). Stored under `rob_domains["preflight"]`. **Meta-gated NA** — when the preflight reports no synthesis, items 11/12/15 are set to "No meta-analysis conducted" *in code, with no LLM call* (the RoB 2 Cluster NA-cascade pattern). LLM-call count per paper: 1 preflight + ≤16 item calls.

**Reporting guideline** — PRISMA 2020 ([backend/reporting_guidelines/prisma2020.py](backend/reporting_guidelines/prisma2020.py)): the 27-item checklist with a/b/c… sub-items (42 entries), one LLM call per paper, same `adhered/applicable/proportion` shape as `strobe.py`. Transcribed from Page et al. BMJ 2021;372:n71 (no source PDF in the repo).

**Frontend** ([frontend/quality-appraisal.html](frontend/quality-appraisal.html)) — `AMSTAR2_DOMAIN_META` (16 items, `critical` flag) + dispatch in `domainMetaFor('amstar2')`. `robBadgeCls(j, tool)` is **tool-aware**: AMSTAR-2's labels collide with the RoB scale ("High" is good for AMSTAR-2, bad for RoB 2; "Low" is bad for AMSTAR-2, good for RoB 2), so AMSTAR-2 rows use `AMSTAR2_BADGE_CLS`. The results grid relabels the RoB column "AMSTAR-2 confidence", hides the Indirectness / Imprecision / Initial GRADE / Updated GRADE / GRADE explanation columns for SR runs (`allSrTool`), and shows a magenta `AMSTAR-2` chip. The detail modal renders the 16 items as collapsible `<details>` with critical-item markers, a preflight + flaw-count summary block, and skips the GRADE / indirectness / imprecision sections. No run-create modal control (no per-run options).

**CSV/XLSX export** — `flatten_result_row` dispatches `tool == 'amstar2'` to `amstar2.ITEMS` for the per-item `rob_d{id}_judgement` + `rob_{signal}` columns, and emits `amstar2_confidence` / `amstar2_review_includes` / `amstar2_meta_analysis` / `amstar2_critical_flaws` / `amstar2_noncritical_weaknesses`.

**Sharable cross-platform methodology**: [docs/shareable/amstar2_shareable.md](docs/shareable/amstar2_shareable.md) — self-contained transcription of all 16 items + signaling sub-criteria, the decision logic, the overall-confidence algorithm, prompt templates, and a turnkey single-file Python reference implementation (`llm_call` injected, no framework dependencies). For sharing the methodology without cloning the repo.

**Out of scope for v1 AMSTAR-2**: per-run custom critical-domain sets (the published default 7 are hardcoded); AMSTAR-2 for umbrella reviews / network meta-analyses (registered only for the two SR study types); reviewer override of an item rating via the UI. The flat 36-credit per-paper charge is unchanged even though AMSTAR-2 papers use more LLM calls (preflight + up to 16 items) than the other tools.

## GRADE Indirectness — single-trial PICO assessment

Lives in [backend/indirectness.py](backend/indirectness.py). Follows the GRADE handbook indirectness chapter (Schünemann et al., book.gradepro.org/guideline/indirectness — Figure 1 explicitly supports per-trial indirectness tables, so single-study assessment is methodologically sound).

**4 PICO subdomains, 4-level judgement scale:**
- `direct` (sufficiently direct), `probably_direct` (probably sufficiently direct), `probably_not_direct` (probably not sufficiently direct), `not_direct` (not sufficiently direct).
- One LLM call per paper judges all four subdomains at once + flags whether the primary outcome is a surrogate.

**Severity decision tree** ([backend/indirectness.py:_judgement_severity](backend/indirectness.py)) — pure-Python aggregation, mirrors the GRADE downgrade convention:
- `none` (0 levels) — all subdomains direct or probably_direct (≤ 1 borderline orange allowed).
- `serious` (−1 level) — exactly 1 `not_direct`, OR ≥ 2 `probably_not_direct`.
- `very_serious` (−2 levels) — 2 `not_direct`.
- `extremely_serious` (−3 levels) — 3 or more `not_direct`.

**Surrogate-outcome rule** (verbatim from the GRADE handbook, baked into the system prompt): "surrogate outcomes should be rated down for indirectness unless there is a strong and well-established correlation with meaningful, patient-important outcomes — a criterion that is rarely fulfilled." Surrogates default to `probably_not_direct` or worse.

**Target PICO** — optional. Supplied via the run-create modal as `{population, intervention, comparator, outcome}` text fields. When provided, the prompt asks the LLM to judge each subdomain *against* the user's review question. When blank, falls back to outcome-surrogacy assessment only — the prompt explicitly tells the LLM to default the other 3 subdomains to `probably_direct` unless the as-conducted PICO is unusually narrow.

**GRADE combination** — `compute_grade(initial, rob_overall, rob_domain_judgements, indirectness_levels, indirectness_explanation, imprecision_levels, imprecision_explanation)` in `backend/quality_appraisal.py` sums RoB + indirectness + imprecision downgrade levels and caps at "Very low" (3 below initial). The `_rob_downgrade(rob_overall, rob_domain_judgements)` helper is extracted so the developer view can show it separately. Explanation text mentions every contributor that fires (e.g. "Downgraded 3 levels: 1 level for Some concerns in risk of bias + 1 level for serious indirectness — surrogate primary outcome (HbA1c) + 1 level for serious imprecision — wide CI crossing line of no effect").

**Out of scope for v1 indirectness**: indirect comparisons / network meta-analysis (body-of-evidence only — not applicable to a single trial), baseline-risk indirectness (needs external longitudinal data to model alternative baselines), ICEMAN credibility check for subgroup effects.

**Reference doc**: [docs/quality_appraisal_rob_reference.md](docs/quality_appraisal_rob_reference.md) is a separate self-contained markdown transcribing every RoB 2 + ROBINS-I V2 (cohort variants A/B + single-arm adaptation) + QUADAS-3 v1.2 + STARD 2015 signaling question, elaboration, and decision tree from `prompt_catalog()` — useful for sharing the methodology without cloning the repo. Indirectness and imprecision are documented via the developer view (`GET /api/quality-appraisal/prompts` → `cat.indirectness`, `cat.imprecision`) rather than in that markdown.

## GRADE Imprecision — single-trial assessment

Lives in [backend/imprecision.py](backend/imprecision.py). Follows the GRADE handbook imprecision chapter (Murad, Neumann, Brozek, Langendam, Dahm, Schünemann — book.gradepro.org/guideline/imprecision). GRADE is conventionally a body-of-evidence rating, but per-trial imprecision is well-defined via CI width vs decision thresholds + sample/event adequacy + fragility.

**4 subdomains, 4-level judgement scale:**
- `precise` (sufficiently precise), `probably_precise`, `probably_not_precise`, `not_precise`.
- One LLM call per paper judges all four subdomains at once + reports the inferred outcome type (binary vs continuous), extracted N + event count + CI summary.

**Subdomains**:
- `ci_width` — primary GRADE tool: does the 95% CI cross clinical-decision thresholds (line of no effect + MID-benefit/MID-harm if supplied)?
- `sample_size` — adequacy heuristic (rule-of-thumb: <100 → not_precise; 100–300 → probably_not_precise; 300–1000 → probably_precise; >1000 → precise).
- `event_count` — binary outcomes only (<100 → not_precise; same gradient). **N/A for continuous outcomes** — the normalizer maps `n_a`/`not_applicable`/`na` → `precise` so it never contributes to severity counting.
- `fragility` — qualitative robustness check for large relative effects from few events, p-just-under-0.05 with small N, single-event-driven significance.

**Severity decision tree** ([backend/imprecision.py:_judgement_severity](backend/imprecision.py)) — identical logic to indirectness with red/orange labels swapped:
- `none` (0 levels) — all subdomains precise/probably_precise (≤ 1 borderline orange).
- `serious` (−1 level) — exactly 1 `not_precise`, OR ≥ 2 `probably_not_precise`.
- `very_serious` (−2 levels) — 2 `not_precise`.
- `extremely_serious` (−3 levels) — 3 or more `not_precise`.

**Optional MID thresholds** — supplied via the run-create modal as `{mid_benefit, mid_harm}` text fields. When provided, the prompt asks the LLM to judge CI width against the user's a-priori thresholds (the GRADE 2-threshold framing). When blank, the LLM falls back to line-of-no-effect + clinical-importance reasoning, defaulting to `probably_precise` rather than `precise` when CI width is uncertain.

**Outcome-type heuristic** ([backend/imprecision.py:infer_outcome_is_binary](backend/imprecision.py)) — best-effort guess from extracted fields: explicit `primary_outcome_type` field → keyword match on outcome name and definition (mortality / event / dichotomous → binary; mean / score / change-from-baseline → continuous). Returned heuristic is passed to the prompt; the LLM can override.

**Out of scope for v1 imprecision**: six-threshold EtD framing (only 2-threshold MID-benefit + MID-harm in v1), machine-readable threshold-crossing arithmetic (LLM judges qualitatively), formal Optimal Information Size / Review Information Size computation, Walsh fragility-index, very-low-baseline-risk auto-override (guardrail in rationale only), random-effects double-counting caveat (meta-analysis only — single-trial here).

## Chrome extension for authenticated PDF fetch

Lives at `extension/` (Chrome MV3 — `manifest.json`, `background.js`, `content.js`, `popup.html|js|css`). Pair via `/developers`; once paired, the user can pick "🧩 Via my Chrome extension" in any import modal and the extension processes the queue inside their authenticated browser session.

**Why this exists.** Server-side PDF fetching can't reach paywalled publishers (BMJ, NEJM, Wiley, Annals) — Render's IP isn't on the user's institutional VPN. The extension runs in the user's logged-in browser, so cookies / SSO / VPN-IP gating all work transparently. Auth never touches our server.

**Pairing flow.** One-per-user dedicated `rg_ext_*` token, separate from the developer `rg_user_*` API key so revoking one doesn't break the other:
1. User clicks "Generate pairing code" in `/developers` → backend mints `EX-XXXX-YYYY` (10-min TTL, ~38 bits of entropy, confusion-resistant alphabet without 0/O/1/I/L)
2. User pastes code into the extension popup → extension POSTs `/api/extension/pair {code}` (no auth needed — code is the auth) → backend mints `rg_ext_<token_urlsafe(32)>`, stores in `users.extension_token`, marks the pairing row consumed, returns the token
3. Extension stashes token in `chrome.storage.local`; subsequent calls use `X-API-Key: rg_ext_*`

**Auth check.** [`main.py:_get_user_by_api_key`](main.py) accepts both `rg_user_*` (`users.api_key`) and `rg_ext_*` (`users.extension_token`) — both grant the same user identity. Revoke = clear the column.

**Endpoints** (all in [`backend/extension.py`](backend/extension.py); routes in [`main.py`](main.py)):
- `POST /api/extension/pair-code` (cookie auth) — mint a pairing code; invalidates any prior unconsumed code
- `POST /api/extension/pair` (no auth) — exchange code for `rg_ext_*` token. 404/410/409 on not-found / expired / already-consumed
- `DELETE /api/extension/token` (cookie auth) — revoke the calling user's extension token (idempotent)
- `GET /api/extension/status` (cookie or `rg_ext_*`) — `{paired, paired_at, queue_count}`
- `GET /api/extension/queue?limit=50` (cookie or `rg_ext_*`) — papers where `pdf_status='extension_pending'` and `user_id=me`, oldest-first
- `POST /api/extension/papers/{paper_id}/pdf` (`rg_ext_*`) — body `{pdf_b64}`. Validates `%PDF` magic + ownership + size cap (50 MB) → calls `paper_files.write_paper_file` + `search._upgrade_paper_to_pdf` (atomic in-place upgrade preserving paper id, so annotations / rubrics keep their references)
- `POST /api/extension/papers/{paper_id}/skip` (`rg_ext_*`) — mark as `fetch_failed`, idempotent on terminal status
- `POST /api/extension/resolve-pdf-url` (cookie or `rg_ext_*`) — LLM-pick a PDF link from rendered anchors (mirrors `browser_agent.py`)
- `POST /api/papers/{paper_id}/queue-for-extension` (cookie auth) — Library page "Send to extension" bulk action: re-queue an existing metadata-only / fetch_failed paper without going through the search-import path

**Search-import dispatch.** `mode='extension'` in `api_search_import` ⇒ free, synchronous, requires pairing (412 if not paired). Calls [`search.import_results_extension`](backend/search.py) which mirrors `import_results` but stamps `pdf_status='extension_pending'` and re-queues existing metadata-only / fetch_failed rows.

**Schema:** [`backend/extension.py:EXTENSION_TABLES_SQL`](backend/extension.py) creates `extension_pairings(code PK, user_id, created_at, expires_at, consumed_at, consumed_token)` + idempotent ALTER TABLEs adding `users.extension_token` and `users.extension_paired_at` via `migrate_user_columns(conn)` (called from `init_db`). New `papers.pdf_status` value: `'extension_pending'`. Cleanup: `purge_expired_pairings(conn, max_age_days=7)` is provided but not yet wired to a periodic task.

**Extension architecture (MV3):**
- `manifest.json` — `permissions: storage, tabs, scripting, activeTab`; `host_permissions: <all_urls>`; content scripts run at `document_idle` on every page
- `background.js` — service worker. Owns token storage + server URL. Processes queue: GET queue → for each paper, `chrome.tabs.create({ url, active: false })`, wait ≤45s for content script to message back, POST PDF bytes (or skip), close tab, throttle 3s between papers. Long-lived port to popup for streaming progress events
- `content.js` — runs on every page, dormant unless background is awaiting a tab. After 1.5s settle, finds PDF link via meta tag → common selectors (`a[href*="/pdf/"]`, `a[type="application/pdf"]`, anchor text matches "Download PDF" / "Full text PDF" / "View PDF" / "Get PDF" / "PDF") → LLM fallback (asks background to call `/api/extension/resolve-pdf-url`). Fetches the URL with `credentials: 'include'`, validates `%PDF` magic, base64-encodes (chunked to dodge call-stack limits on large PDFs), sends back via `chrome.runtime.sendMessage`. If nothing found within 30s, sends `pdf_not_found`
- `popup.html|js|css` — two views: pair (paste code + server URL) / connected (queue count + Process queue button + live event log + unpair). Long-lived port to background for real-time progress

**Privacy contract.** The extension only fetches URLs that came from the user's queue on the paired server. It never reads other tabs (the content script does run on every page but only does anything if the background is processing that exact tab). PDF bytes are never persisted locally — they're streamed to the server and dropped from memory.

**Loading the extension** (until it's published to the Web Store): `chrome://extensions` → toggle Developer mode → Load unpacked → select `extension/`. Icons aren't included in v0.1.0 (Chrome shows a generic puzzle-piece icon — drop PNGs into `extension/icons/` and re-add the icon refs in `manifest.json` to customize).

**Tests** ([`tests/test_extension.py`](tests/test_extension.py)) — 24 tests covering: pairing-code lifecycle (mint, consume, expiry, already-consumed, not-found, mint-invalidates-prior), `rg_ext_*` token auth, queue ordering + filtering by user, upload (validates magic / ownership / already-present 409 / cross-user 404), skip (idempotent), search-import `mode='extension'` (412 unpaired / queues when paired), library `queue-for-extension` endpoint, resolve-pdf-url (auth gate + delegation to picker).

## Search Strategist — 5-tier PDF Import Pipeline

Lives at `/search` ([frontend/search.html](frontend/search.html)) and inside the Lab ([frontend/lab.html](frontend/lab.html)). Both surfaces share `/api/search/import` which dispatches into one of five modes via the `mode` field on `SearchImportPayload`:

| Mode | Cost / paper | Sync? | What it does |
|------|--------------|-------|--------------|
| `metadata` | free | sync | Stash a `papers` row with title/authors/abstract + `external_url`. Sets `pdf_status='metadata_only'`. No download. |
| **`auto`** (default) | **2–15 credits** | async | Runs every strategy in order — PMC → Unpaywall → meta-tag → Firecrawl → browser+LLM. Pre-charges the browser-tier max (15 cr) and refunds the excess based on which tier won: free chain → 2 cr, Firecrawl → 5 cr, browser → 15 cr. Failures refund the full 15. |
| `fetch` | 2 credits | async | Background worker tries: `download_pmc_pdf` → Unpaywall → direct GET → `citation_pdf_url` meta tag. Browser-spoof UA + Referer header so paywall publishers don't 403 us. No Firecrawl, no browser. |
| `firecrawl` | 5 credits | async | Same as `fetch` plus a final Firecrawl JS-render fallback. Crawls the **Unpaywall-resolved publisher landing URL** (not the PubMed URL — PubMed rarely has citation_pdf_url). Requires `FIRECRAWL_API_KEY`. |
| `browser` | 15 credits | async | Same as `firecrawl` plus a final Playwright/Chromium browser-agent fallback that picks up real session cookies. Includes an LLM-driven link picker (Haiku 4.5) when DOM heuristics miss. Requires Playwright + Chromium installed (see [render.yaml](render.yaml) buildCommand + [apt.txt](apt.txt)). |

**Per-strategy event log.** Every strategy attempt emits a `strategy_attempt` event into `pdf_fetch_run_events` with shape `{strategy, outcome, reason, duration_ms, attempt}`. Outcomes: `hit` / `miss` / `transient_error` / `permanent_error`. Strategies that fail on transient HTTP errors (5xx, 429, connect/read timeout) retry up to 2 attempts with 1s/2s backoff; permanent errors and the slow browser tier skip retry. Use this log to debug why a paper failed — the user sees exactly which tier was reached and why each strategy missed.

**Tier-aware return.** [`backend/pdf_fetcher.py:fetch_pdf_for_result`](backend/pdf_fetcher.py) returns `{sha256, filename, storage_path, tier}` where `tier ∈ {"free", "firecrawl", "browser"}`. `backend/search.py:run_pdf_fetch_job` reads `tier` and computes the auto-mode refund.

**Per-result failures are graceful** — the worker creates a `pdf_status='fetch_failed'` paper row with an `external_url` click-out and refunds the per-paper credit. The user gets *something* useful even when no PDF lands.

**Re-runs upgrade in place.** When a metadata-only / fetch-failed row already exists for a search result, the worker doesn't skip — it retries the fetch and, on success, **UPDATEs the existing paper row** (same id) via [`backend/search.py:_upgrade_paper_to_pdf`](backend/search.py). Annotations / rubrics on that paper id stay valid. Only `pdf_status='present'` rows are skipped.

**Schema:** `papers.external_url` (TEXT, NULL for non-search papers) + `papers.pdf_status` (`'present' | 'metadata_only' | 'fetching' | 'fetch_failed'`). `pdf_fetch_runs` (run container with `mode` + `credit_per_paper`) + `pdf_fetch_run_events` (per-paper progress for the polling endpoint). All migrations are idempotent in `init_db()`.

**Endpoints** (all `engineer` seat):
- `POST /api/search/import` — dispatch by mode. For async modes returns `{run_id, total, credits_charged, mode}`.
- `GET /api/search/pdf-fetch/{run_id}` — current status (running / complete / failed + counts).
- `GET /api/search/pdf-fetch/{run_id}/events?after=<id>` — incremental polling (mirrors annotator's batch runner pattern in [`backend/annotator.py:147 log_run_event`](backend/annotator.py)).

**UX downstream of metadata-only papers:** [frontend/library.html](frontend/library.html) renders an "↗ External" chip + status badge ("📋 metadata", "⚠ no PDF", "▶ fetching"). [frontend/annotator.html](frontend/annotator.html) `loadPdf` catches the 404 and renders a placeholder card with title + external link + "PDF unavailable — annotate from metadata only" instead of alerting.

**Result fields gotcha:** `save_results` in [backend/search.py](backend/search.py) **must** stamp `a["id"] = cur.lastrowid` after each INSERT. Without it, frontend checkboxes (`data-id="${r.id}"`) bind to `undefined` and Import Selected silently sends an empty array.

**Browser-agent caveats** ([backend/browser_agent.py](backend/browser_agent.py)):
- ~500MB RAM during a session. Render Free (512MB) will OOM-kill — **needs Standard ($25/mo) or higher**.
- **Playwright ≥1.49 split Chromium into two packages** — `chromium` (full browser) and `chromium-headless-shell` (the lightweight headless binary `headless=True` defaults to). Installing only `chromium` causes `BrowserType.launch: Executable doesn't exist at .../chromium_headless_shell-*/chrome-headless-shell`. The build command in [render.yaml](render.yaml) installs both: `playwright install chromium chromium-headless-shell`. Don't drop the second package.
- `playwright install chromium chromium-headless-shell` adds ~295MB to the build. First deploy takes 4–8 minutes.
- Defeats simple bot detection (UA + cookies + Referer) but **not** Cloudflare Turnstile / hCaptcha. Login-walled content needs the user's institutional credentials, which we don't store.
- **LLM-driven link picker.** When DOM heuristics miss (`citation_pdf_url` → `[href*="/pdf/"]` → "Download PDF" text), `_llm_resolve_pdf_url` harvests the rendered page's first 200 anchors (href + visible text + aria-label) and asks Claude Haiku 4.5 to pick the PDF download link, returning JSON `{pdf_url, confidence, reason}`. Decline-aware (returns null on paywalls). Sync `call_anthropic` runs in `asyncio.to_thread` so it doesn't block the Playwright event loop. ~$0.0005/page; bundled into the browser tier's 15 cr.

## Environment Variables

**Required**: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ADMIN_SECRET`
**Database**: `DATABASE_URL` (PostgreSQL connection string — set on Render, omit locally for SQLite fallback)
**Billing**: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`
**Cloud storage**: `AWS_S3_BUCKET`, `AWS_S3_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (omit all for local fallback)
**Optional**: `NCBI_API_KEY` (PubMed rate boost), `MOONSHOT_API_KEY` (Kimi), `FIRECRAWL_API_KEY` (search-import `mode='firecrawl'` and `mode='browser'`), SMTP vars (email)

See `DEVELOPMENT.md` for full list.

## Running Locally

```bash
pip install -r requirements.txt
export ADMIN_SECRET=test123
export ANTHROPIC_API_KEY=sk-ant-...
# No DATABASE_URL → uses SQLite automatically (rubricgen.db)
uvicorn main:app --reload --port 8000
```

To test with PostgreSQL locally:
```bash
export DATABASE_URL=postgresql://user:pass@localhost:5432/rubricgen
uvicorn main:app --reload --port 8000
```

## Running Tests

```bash
pytest tests/ -v    # Requires Python 3.12+
```

Tests use SQLite fallback (no DATABASE_URL) — no external API calls, no production DB access.
