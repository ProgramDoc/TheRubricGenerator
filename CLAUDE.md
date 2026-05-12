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
| Add a new risk-of-bias tool (ROBINS-I, QUADAS-2, AMSTAR-2, …) | New `backend/rob_tools/<tool>.py` exposing `run(pdf_bytes, fields, classification, primary_outcome, progress)` and `prompt_catalog()`, then register in `backend/quality_appraisal.py:STUDY_TYPE_REGISTRY` + `_TOOL_RUNNERS` |
| Add a new reporting guideline (STROBE, PRISMA, STARD, …) | New `backend/reporting_guidelines/<guide>.py` exposing `run(pdf_bytes, fields, classification)` and `prompt_catalog()`, then register in `backend/quality_appraisal.py:_GUIDELINE_RUNNERS` |
| Modify GRADE indirectness logic | `backend/indirectness.py` (PICO subdomains, severity decision tree, prompts) + `backend/quality_appraisal.py:compute_grade` (combines RoB + indirectness + imprecision downgrades) |
| Modify GRADE imprecision logic | `backend/imprecision.py` (CI / N / events / fragility subdomains, severity decision tree, prompts) + `backend/quality_appraisal.py:compute_grade` (combines RoB + indirectness + imprecision downgrades) |
| Reference: RoB 2 + ROBINS-I prompts + decision trees | `docs/quality_appraisal_rob_reference.md` — verbatim transcription of every signaling question, elaboration, and pure-Python decision tree from `prompt_catalog()`. For sharing without cloning. |
| Add a new lab agent | `backend/agents/lab_agents.py` + `backend/skills.py` (prompt) + `backend/lab.py` (routing) |
| Modify lab chat/sessions | `backend/lab.py` |
| Modify exports | `backend/exports.py` |
| Modify the daily scheduler | `backend/scheduler.py` + `backend/pubmed.py` |
| Modify search | `backend/search.py` |
| Modify search-result PDF import (6 modes) | `backend/search.py` (`import_results`, `import_results_extension`, `run_pdf_fetch_job`, `_upgrade_paper_to_pdf`) + `backend/pdf_fetcher.py` (PMC → Unpaywall → direct → meta-tag → Firecrawl, with per-strategy events + retries + tier-aware return) + `backend/browser_agent.py` (Playwright Chromium + LLM-driven link picker). Modal UI mirrored in `frontend/search.html` and `frontend/lab.html`. **`auto`** is the default — runs every tier, tier-priced 2/5/15 cr. **`extension`** queues for the user's paired Chrome extension (free). |
| Modify Chrome extension (PDF fetch via authenticated browser) | `backend/extension.py` (pairing, queue, upload, skip, status) + `backend/pdf_link_picker.py` (shared LLM picker — also used by `browser_agent.py`) + extension/ dir (`manifest.json`, `background.js`, `content.js`, `popup.html|js|css`). Pair via `/developers`. Tests in `tests/test_extension.py`. |
| Add a new PDF-fetch strategy | `backend/pdf_fetcher.py` — write a `_strat_*` helper that returns `(result_or_None, outcome, reason)` where `outcome ∈ {hit, miss, transient_error, permanent_error}`, then call it from `fetch_pdf_for_result` via `_run_with_retry(name, on_event, lambda attempt: _strat_*(...))`. Validate downloads via `_is_pdf_bytes`. Pass `attempts=1` for slow / metadata-driven strategies. Tag the tier when emitting the hit (`_hit(out, "free"|"firecrawl"|"browser")`). |
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
  ├── quality_appraisal.py — RoB + reporting-guideline + indirectness + imprecision + GRADE pipeline (registry, orchestrator, compute_grade combiner)
  ├── indirectness.py   — GRADE indirectness — single-trial PICO assessment (4 subdomains, severity decision tree)
  ├── imprecision.py    — GRADE imprecision — single-trial CI / N / events / fragility (4 subdomains, severity decision tree)
  ├── rob_tools/
  │   ├── rob2.py       — RoB 2 (RCTs)
  │   └── robins_i.py   — ROBINS-I V2 (20 Nov 2025 cribsheet, follow-up cohort studies; also dispatched for case-control / cross-sectional as approximation, and for Single-Arm Trial / Dose-Escalation Study via the adapted single_arm variant of D1 + D2)
  ├── reporting_guidelines/
  │   ├── consort.py    — CONSORT 2025 (RCTs)
  │   └── strobe.py     — STROBE 2007 (observational designs)
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
4. Per-domain LLM calls for the registered RoB tool — **pure-Python decision trees** map signal answers (`Y/PY/PN/N/NI` plus V2-only `WN/SN/WY/SY`) to tool-specific judgements (RoB 2 is 3-level Low/Some concerns/High; **ROBINS-I V2 is 4-level Low/Moderate/Serious/Critical** — V1's "No information" judgement was retired; Domain 1's "Low" is labelled "Low (except for concerns about uncontrolled confounding)" for cohort variants or "Low (except for concerns about uncontrolled benchmarking)" for the single-arm variant). For ROBINS-I V2 a preflight LLM call also answers B1/B2/B3 + C4 — B2=Y/PY (or B2-SA=Y/PY for single-arm) or B3=Y/PY short-circuits to Critical, and C4 dispatches Domain 1 to Variant A (ITT) or Variant B (per-protocol) for cohort studies. **For Single-Arm Trial / Dose-Escalation Study** (no comparator), `run_preflight()` uses an alternate single-arm prompt — B1/B2 are replaced with benchmark-pre-specification questions (B1-SA/B2-SA) — and the variant is pinned to `"single_arm"` based on study type BEFORE preflight. The single_arm variant rewrites Domain 1 (benchmark adequacy + prognostic-mix comparability, 5 questions 1S.1–1S.5) and Domain 2 (intervention fidelity + intent-vs-received cohort definition, 3 questions 2S.1–2S.3); D3–D6 reuse cohort signals + judges unchanged. Trees live in code (not prompts) so the developer view can show the exact logic via `inspect.getsource`.
5. Single-call adherence check against the registered reporting guideline.
6. **GRADE indirectness** — single LLM call via `backend/indirectness.py` judging each PICO subdomain (population/intervention/comparator/outcome) on a 4-level scale, then a pure-Python severity decision tree → 0/1/2/3 GRADE downgrade levels. Conditioned on the user's optional target PICO; falls back to outcome-surrogacy assessment when not supplied.
7. **GRADE imprecision** — single LLM call via `backend/imprecision.py` judging four subdomains (CI width / sample size / event count / fragility) on a 4-level scale, then the same severity decision tree → 0/1/2/3 GRADE downgrade levels. Conditioned on the user's optional MID thresholds (`mid_benefit`, `mid_harm`); falls back to line-of-no-effect + clinical-importance reasoning when not supplied. Event-count subdomain is N/A for continuous outcomes (excluded from severity counting via the `n_a → precise` normalization).
8. Compute initial GRADE (from registry) + updated GRADE after RoB **+ indirectness + imprecision** (sum of downgrade levels, capped at "Very low"). Other GRADE domains (inconsistency, publication bias) still require a body of evidence and are out of scope.

**Extensibility contract** — [backend/quality_appraisal.py:STUDY_TYPE_REGISTRY](backend/quality_appraisal.py) is the single source of truth mapping `{study_type → (rob_tool, reporting_guideline, initial_grade)}`. v1 supports:
- **Randomized Controlled Trial → RoB 2 (2019) + CONSORT 2025 + High initial GRADE**
- **Cohort Study / Case-Control / Non-Randomized Trial / Cross-Sectional (Analytical) / Case-Crossover → ROBINS-I V2 (20 Nov 2025 cribsheet) + STROBE 2007 + Low initial GRADE.** V2 is published explicitly for follow-up/cohort studies; the other designs use V2 as a best-available approximation pending design-specific tooling.
- **Single-Arm Trial / Dose-Escalation Study → ROBINS-I V2 single_arm variant + STROBE 2007 + Very low initial GRADE.** Uncontrolled designs start at the lowest GRADE level (no comparator → more severe than confounded comparison); `compute_grade` clamps further downgrades at Very low. The single_arm variant reframes D1 (benchmark adequacy) + D2 (intervention fidelity / cohort definition) while reusing D3–D6 cohort signals unchanged. Dose-Escalation shares the variant wholesale — MTD/DLT/RP2D-specific bias is not modeled in v1.

Registry keys MUST match `annotator.TYPE_FIELD_IDS` keys — the test `TestDispatch::test_registry_keys_match_annotator_types` enforces this. Unsupported study types return `None` → the paper is marked `skipped`, credits refund. Adding a new study type: add a registry entry + new module in `backend/rob_tools/` (for the tool) and/or `backend/reporting_guidelines/` (for the guideline), each exposing `run(...)` and `prompt_catalog()`, then register the callable in `_TOOL_RUNNERS` / `_GUIDELINE_RUNNERS`.

**Credit gate + refund**: pre-charge via `_annotator_ai_gate`; refund per-paper on error/skip via `billing.refund_credits` — same idiom as custom annotator runs. Admin bypasses.

**Background execution**: inline for ≤3 papers, daemon thread for larger batches via [backend/quality_appraisal.py:run_batch_async](backend/quality_appraisal.py). Progress is logged to `quality_appraisal_events` and polled every 5s by the frontend (`GET /runs/{id}/events?after=<last_id>` returns incremental events).

**Developer view** (🔧 icon in topbar, visible to every signed-in user) — `GET /api/quality-appraisal/prompts` returns the full prompt templates, signaling questions, and `inspect.getsource` output for every decision tree + GRADE logic. Transparency by default: reviewers can see exactly how a judgement was produced.

**DB tables** (initialised from `QUALITY_APPRAISAL_TABLES_SQL` + idempotent `migrate_qa_columns(conn)` for the post-launch indirectness + imprecision columns): `quality_appraisal_runs` (incl. `target_pico_json` for the user-supplied PICO and `imprecision_thresholds_json` for MID benefit/harm), `quality_appraisal_results` (incl. `indirectness_json`/`overall`/`levels`/`explanation` and `imprecision_json`/`overall`/`levels`/`explanation`), `quality_appraisal_events`. All date columns use `created_at` / `completed_at` (no `timestamp` column per the SQLite compat-wrapper gotcha). Runs are soft-deleted via `deleted_at`.

**Endpoints** (seat tiers match the annotator's): `GET /api/quality-appraisal/supported-types` (general), `GET /api/quality-appraisal/prompts` (general, the dev view — surfaces both the indirectness and imprecision `prompt_catalog`s), `POST /api/quality-appraisal/runs` (engineer; body accepts optional `target_pico: {population, intervention, comparator, outcome}` for indirectness and optional `imprecision_thresholds: {mid_benefit, mid_harm}` for imprecision), `GET /api/quality-appraisal/runs` (general), `GET /api/quality-appraisal/runs/{id}` (general — response includes `target_pico` and `imprecision_thresholds` on the run + `indirectness_*` and `imprecision_*` fields per result), `GET /api/quality-appraisal/runs/{id}/events?after=<id>` (general, incremental poll), `GET /api/quality-appraisal/runs/{id}.csv|.xlsx` (general — exports include 8 indirectness + 8 imprecision columns), `DELETE /api/quality-appraisal/runs/{id}` (general, soft delete).

**Detail view** — each row in the results grid is clickable (📋 icon on the study cell, plus each RoB domain / Indirectness / Imprecision / CONSORT / GRADE cell). The grid carries dedicated **Indirectness** and **Imprecision** columns between RoB and the reporting-guideline column, each showing the severity badge (`None` / `Serious` / `Very serious` / `Extremely serious`) plus a `−N GRADE` subtext when the run downgraded. Clicking opens a full-screen split modal: **PDF.js viewer on the left** (loaded from `/api/papers/{pid}/pdf`, canvas + text layer per page) and a scrollable **detail panel on the right** with Summary → RoB (5 collapsible RoB 2 domains or 6 ROBINS-I V2 domains) → **Indirectness** (`qa-sec-indirectness` — severity badge + 4-cell PICO grid with green/yellow/orange/red Figure-2-style colouring + per-subdomain rationale + surrogate-outcome callout) → **Imprecision** (`qa-sec-imprecision` — severity badge + 4-cell subdomain grid with cyan/blue/orange/red palette + per-subdomain rationale + sample-size context note showing extracted N / events / CI summary; event-count cell renders "N/A — continuous outcome" when the LLM marks it n_a) → Reporting guideline (CONSORT 2025 or STROBE 2007, grouped by section, ✓/✗/N-A) → GRADE (initial → updated with combined RoB + indirectness + imprecision downgrade explanation and domain breakdown) → Extracted fields. Clicking any rationale or evidence chip **searches the live PDF text layer** for the first ~80 chars of the quote (with a longest-matching word n-gram fallback for paraphrased quotes) and flash-highlights the match. Prior highlight clears on next click; the clicked chip gets a yellow "active" marker. Quote-to-highlight is best-effort — we never asked the LLM for PDF coordinates, so the fallback may miss for heavily paraphrased quotes (toast surfaces the miss). The frontend looks up `domainMetaFor(r.rob_tool, variantOf(r))` to pick between `ROB2_DOMAIN_META` (5 domains), `ROBINS_I_DOMAIN_META` (6 cohort V2 domains), and `ROBINS_I_SA_DOMAIN_META` (6 single-arm domains — D1 "D1: Benchmarking" / D2 "D2: Intervention" reflect the SA reframing). `variantOf(result)` reads `result.rob_domains.preflight.variant`. `robBadgeCls(j)` maps any judgement (3-level RoB 2 or 4-level ROBINS-I V2; Domain 1's special cohort "Low (except for concerns about uncontrolled confounding)" and single-arm "Low (except for concerns about uncontrolled benchmarking)" labels are both recognized) to a badge CSS class. See [frontend/quality-appraisal.html](frontend/quality-appraisal.html) `openDetailModal`, `renderDetailPanel`, `loadDetailPdf`, `jumpToQuote`.

**Mixed-tool runs** — if a run includes papers of different study types (some RCT + some Cohort), the results grid column set is taken from the first successful row's tool. Other rows with a different tool still render correctly; domain cells for non-matching domain IDs show `—`. Single-design runs are the common case, so this trade-off is intentional for v1.

**Out of scope for v1**: Quasi-experimental designs (Uncontrolled Before-After, Interrupted Time Series, Difference-in-Differences, Regression Discontinuity) — each needs its own confounding prompt + ROBINS-I V2 adaptation. AMSTAR-2 (systematic reviews), QUADAS-2 (diagnostic accuracy), PRISMA 2020, STARD. Cluster/crossover/stepped-wedge RCT variants (parallel-trial cribsheet only). Editing / overriding AI judgements in the UI. Other GRADE domains beyond RoB, indirectness, and imprecision — inconsistency and publication bias — still require a body of evidence and are deferred. Per-outcome user selection (we auto-pick primary). Note: V2 retired V1's "Bias due to deviations from intended intervention" domain entirely; protocol-deviation issues are folded into Domain 1 Variant B (time-varying confounding) which the preflight selects when the analysis estimates the per-protocol effect. **Single-arm v1 caveats:** Dose-Escalation-specific bias (MTD declaration adequacy, DLT definition, RP2D justification, expansion-cohort selection) is not modeled — Dose-Escalation reuses the single-arm variant wholesale. No single-arm-specific reporting guideline module yet — STROBE is reused pragmatically; a `phase2_singlearm` checklist may be added in a follow-up. The new 1S.*/2S.* signal IDs join the existing CSV/XLSX column union — old cohort runs will show empty cells for those columns, and SA runs show empty cells for the A/B variant columns.

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

**Reference doc**: [docs/quality_appraisal_rob_reference.md](docs/quality_appraisal_rob_reference.md) is a separate self-contained markdown transcribing every RoB 2 + ROBINS-I V2 signaling question, elaboration, and decision tree from `prompt_catalog()` — useful for sharing the methodology without cloning the repo. Indirectness and imprecision are documented via the developer view (`GET /api/quality-appraisal/prompts` → `cat.indirectness`, `cat.imprecision`) rather than in that markdown.

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
