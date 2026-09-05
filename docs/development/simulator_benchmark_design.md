# Meta-analysis and GRADE simulator

This module runs uploaded primary study reports through the application's production classification, eligibility, screening, extraction, outcome-specific RoB and pooling steps, then passes the same pooled estimate and aligned study weights to the separate GRADE agent with automatic body-level indirectness. The simulator preserves both GRADE outputs and scores the latter. It is a reproduction benchmark, not a certification of clinical validity.

## Using the platform

Open `/simulator`, choose a published benchmark or import a reference JSON, upload its primary study PDFs, confirm that the uploaded reports contain no benchmark answer document, choose repetitions, and review the credit estimate before launching. Use the Analytics tab for effect/interval agreement, GRADE agreement, overconfidence, confusion matrices, weighted kappa, stage checks, variation across repeats and individual event logs. Download an immutable run record to investigate a discrepancy. Filters separate reference revisions and configured model/code versions. The dashboard currently loads the most recent 200 runs for the signed-in user; it is not a whole-platform census.

Each import is a new immutable reference revision. A run snapshots the reference, paper IDs and recorded SHA-256 values, PICO and pooling settings, configured model, source-file hashes, outputs and deterministic scores. The configured model name is not a provider attestation: the existing provider wrapper does not expose actual token counts, fallback model identity, temperature or deterministic seeds. No dollar-cost estimate or fixed-seed reproducibility is claimed.

## Agent inputs and evaluation

The runner builds an explicit allowlist containing PICO, outcome definitions, effect measure, pooling and interval settings, continuity correction, and optional prespecified clinical thresholds/baseline risk. Published effects, certainty labels, domain labels, expected study classifications, extraction targets and publication citations stay in the evaluator. The primary paper can of course cite a review, and the model may have encountered public papers during training. This separation prevents direct benchmark-answer injection by the runner; it does not establish absence of training contamination.

A small optional observation callback in `synthesis.run_synthesis` captures PICO fields already extracted for RoB. No additional extraction calls are made for that context. `simulator_grade` translates the production pooled estimate and CI from log to display scale where needed, retains the chosen model's study weights, joins RoB by `(study_id, outcome_id)`, and translates sample/event totals. It never pools the data a second time. A body with duplicate dependent rows, unsupported/mixed designs or misaligned weights is refused by the bridge. These refusals and the original pooled result remain in the export.

The existing GRADE agent clips study context to 6,000 characters; truncation and absent PICO fields are flagged. The agent's failed-indirectness fallback is preserved for diagnostic evaluation and explicitly flagged, with the run marked partial. Neither a matching final GRADE label nor a completed run establishes that all domain judgments are methodologically sound. Human edits to screening, extraction or the prespecified protocol during execution invalidate unassisted benchmarking. Later edits to ordinary synthesis reviews cannot change the frozen simulator output.

## Initial published-target dataset

`data/benchmarks/published-v1.json` contains three development cases and six outcome targets, transcribed on 2026-09-04:

- Clarke et al., compression stockings for airline passengers, Cochrane 2021, CD004002: symptomless DVT, superficial vein thrombosis and oedema. [Published review summary](https://www.cochrane.org/evidence/CD004002_compression-stockings-preventing-deep-vein-thrombosis-dvt-airline-passengers).
- Goodman et al., probiotics for antibiotic-associated diarrhoea in adults, BMJ Open 2021;11:e043054: overall complete-case diarrhoea outcome. [Publication](https://bmjopen.bmj.com/content/11/8/e043054).
- Rochwerg et al., corticosteroids in sepsis, Critical Care Medicine 2018;46:1411–1420: short- and long-term mortality. [Review](https://pubmed.ncbi.nlm.nih.gov/29979221/), [linked BMJ Rapid Recommendation](https://www.bmj.com/content/362/bmj.k3284).

These are published output targets, **not a completed, independently adjudicated study-level gold corpus**. Primary-study PDFs are not bundled or redistributed. Pooling settings and numerical tolerances remain explicitly unverified/exploratory. Stage-level accuracy is unavailable until a curated manifest supplies expected study decisions, extraction values and RoB labels. Published domain labels are left empty where exact source footnotes have not been verified; they are never inferred from the final certainty.

The review version is part of the benchmark identity. For example, the [Cochrane Handbook's illustrative stockings table](https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-14) labels its ratio as RR and gives slightly different bounds from the 2021 review abstract's OR. Do not combine those numbers into one target. Source locators, outcome timepoints, denominator definitions and versions must be reconciled by curators.

To create an adjudicated revision, two independent reviewers should verify the full included/excluded study manifest, publication-to-trial mapping, primary reports and supplements, effect direction, comparison, timepoint, duplicate cohorts, raw values with source locators, RoB results, final certainty and domain-level reasons. Verify pooling settings and clinical decision thresholds against the actual publication. Record disagreements and their resolution outside the model loop. The import schema requires two distinct adjudicator names, a study manifest and verified methods to accept `curation="adjudicated"`; those fields record an attestation, not platform verification of the reviewers' work. Keep related reports together in development/holdout splits. The current release records split labels; it does not enforce a blinded holdout-access service.

## Metric definitions and testing suggestions

Effect error is absolute difference on the analysis scale: log ratios for RR/OR/HR and original units for MD/SMD/RD. The interval score requires both confidence bounds within the prespecified tolerance. The default tolerance of 0.05 is exploratory, not a GRADE guideline threshold. All requested outcomes stay in accuracy denominators, including failures, missing outputs and partial runs.

GRADE metrics include exact agreement, ordinal distance, overconfidence (agent certainty above the reference), unrated counts, domain agreement where gold domain labels exist, and quadratic weighted kappa on paired valid labels. Missing labels are excluded from the confusion matrix/kappa but retained in the agreement denominator. Kappa is undefined if its expected-disagreement denominator is zero. Repeat ranges are descriptive variation across independent runs in the same launch, not statistical confidence intervals.

Mapped reference studies enable classification accuracy, screening precision/recall and decision accuracy, raw extraction comparisons, and outcome-specific RoB agreement. Extraction tolerance is max(1e-6 absolute, 1e-4 times the reference magnitude). Multiple candidate rows for a single study/outcome are treated as ambiguous, not matched to the most favorable value. Zero targets means unbenchmarked. A manifest containing only included studies cannot estimate specificity or characterize false inclusion on a realistic search corpus.

Recommended evaluation progression:

1. Adjudicate a small mixed-topic pilot before expanding the corpus. Reproduce each publication with an independent statistical package and verified settings first.
2. Run full pipelines repeatedly on frozen reports, without manual corrections. Review study-set errors before interpreting pooled disagreements.
3. Add a separate, labeled stage-replay mode with adjudicated upstream inputs to distinguish extraction errors from statistical/GRADE errors. That mode is future work; the current Run button always executes the full pipeline.
4. Test missing/invalid model responses, unsupported designs, duplicates, correlated cohorts, zero-event trials, continuous outcomes, changed files, failed RoB, incomplete weight coverage, and failed indirectness. Keep these synthetic robustness cases separate from published targets.
5. Compare model/code versions on the same frozen review sets. Add review-clustered uncertainty intervals and held-out clinical topics when the dataset is large enough. Current dashboard ranges/kappa do not provide inferential comparison of model versions.
6. Audit all five certainty domains and the source rationale, not only the final four-level label. The [Cochrane Handbook Chapter 14](https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-14) describes outcome-level certainty, domain judgments and explanatory notes that a reference evidence profile should capture.

## Render operations

The feature uses the existing web service, PostgreSQL database and paper storage abstraction. No new service, paid plan, provider credential or external database is required. `main.init_db()` creates additive simulator tables; application startup starts the worker. `SIMULATOR_WORKER_ENABLED=1` is the default; set `0` to disable new launches and worker startup while keeping results readable. In-flight work is not cancelled by changing the flag in a running process. Public access uses the application's existing login/session or API key, seat checks and per-user ownership.

The queue persists in PostgreSQL. A database lease allows one simulator worker owner across web processes; the current synthesis pipeline's own jobs are outside that limit. The lease is renewed every 20 seconds and expires after 120 seconds. Epoch columns use double precision because PostgreSQL REAL cannot retain second resolution at present-day Unix timestamps. After a worker loss, the next owner marks running jobs interrupted and does not replay paid model calls. Partial stage records remain in ordinary synthesis tables. It claims queued jobs in order. A model or code change while queued refuses the job before execution and refunds charged credits. Inputs are checked against their recorded database hashes before and after execution; this is not repeated byte-level rehashing of remote storage.

Launches use a client UUID as an idempotency key. Queue insertion, balance debit and deduplication commit together; replaying the same request cannot charge twice. There are at most five repeats per launch and ten queued/running jobs per user. Credits include synthesis plus two per requested body for automatic indirectness. Admin accounts bypass platform credit debit, although provider usage still applies. Existing synthesis excluded-study/appraisal refunds remain in force; unavailable indirectness is refunded. Unexpected interruption after agent execution has begun requires checking the existing billing ledger; automatic proportional refunds and resumable per-stage billing are future work.

Deployment check: confirm the new commit is live, `/simulator` requires authentication, the signed-in page lists the three reference cases, the upload/library controls work, and stored run/export endpoints remain private. Run the test suite before rollout. Test-client runs mock model boundaries and do not demonstrate live clinical performance. The first real reproduction run needs a matching primary-study corpus and a reviewed credit estimate.

## Baseline review notes

The simulator branch incorporates main commit `2c66c93`, which merged fixes `8928fb3` and `466d536`. The added GRADE bridge does not change either engine's methodology. Remaining issues should be measured and resolved separately:

- Synthesis still checks classifier strings directly in its design filter and RoB registry lookup, bypassing Quality Appraisal's new normalization.
- Low-coverage RoB refusals improve the 99%-unassessed/1%-Low case, but renormalizing a small assessed high-risk subset can overstate its share of the whole evidence body. Filling previously unknown studies with Low-risk labels can remove the downgrade; the statement that unknown studies can only add concerns is not generally valid under normalized weighting.
- Failed body-level indirectness still uses a zero-downgrade fallback in `grade_assess`. The simulator marks this as partial and preserves the warning; it is not an assessed zero.
