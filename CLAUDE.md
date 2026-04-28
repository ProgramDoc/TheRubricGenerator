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
| Modify Quality Appraisal AI | `backend/quality_appraisal.py` (registry, orchestrator, GRADE, DDL) + `backend/rob_tools/*.py` (RoB tools) + `backend/reporting_guidelines/*.py` (checklists) + `frontend/quality-appraisal.html` |
| Add a new risk-of-bias tool (ROBINS-I, QUADAS-2, AMSTAR-2, …) | New `backend/rob_tools/<tool>.py` exposing `run(pdf_bytes, fields, classification, primary_outcome, progress)` and `prompt_catalog()`, then register in `backend/quality_appraisal.py:STUDY_TYPE_REGISTRY` + `_TOOL_RUNNERS` |
| Add a new reporting guideline (STROBE, PRISMA, STARD, …) | New `backend/reporting_guidelines/<guide>.py` exposing `run(pdf_bytes, fields, classification)` and `prompt_catalog()`, then register in `backend/quality_appraisal.py:_GUIDELINE_RUNNERS` |
| Add a new lab agent | `backend/agents/lab_agents.py` + `backend/skills.py` (prompt) + `backend/lab.py` (routing) |
| Modify lab chat/sessions | `backend/lab.py` |
| Modify exports | `backend/exports.py` |
| Modify the daily scheduler | `backend/scheduler.py` + `backend/pubmed.py` |
| Modify search | `backend/search.py` |
| Modify search-result PDF import (4 modes) | `backend/search.py` (`import_results`, `run_pdf_fetch_job`, `_upgrade_paper_to_pdf`) + `backend/pdf_fetcher.py` (PMC → Unpaywall → direct → meta-tag → Firecrawl) + `backend/browser_agent.py` (Playwright Chromium fallback). Modal UI mirrored in `frontend/search.html` and `frontend/lab.html`. |
| Add a new PDF-fetch strategy | `backend/pdf_fetcher.py` — append a new `_try_*` helper, then call it from `fetch_pdf_for_result` in priority order. Each strategy returns `{sha256, filename, storage_path}` or `None`. Validate downloads via the `_is_pdf_bytes` magic-byte check. |
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
  ├── quality_appraisal.py — RoB + reporting-guideline + GRADE pipeline (registry, orchestrator)
  ├── rob_tools/
  │   ├── rob2.py       — RoB 2 (RCTs)
  │   └── robins_i.py   — ROBINS-I (cohort, case-control, non-randomized trial, etc.)
  ├── reporting_guidelines/
  │   ├── consort.py    — CONSORT 2025 (RCTs)
  │   └── strobe.py     — STROBE 2007 (observational designs)
  ├── agreements.py     — Legal text
  └── promo.py          — Promo codes

frontend/ — ~26 self-contained HTML files (inline CSS + JS, no build step).
            Notable additions: library.html (personal PDF library),
            community-library.html (formerly library.html, moved on /community-library),
            review.html (3-judge adjudication review queue UI).
tests/    — pytest suite — 181 cases across Competition API, Annotator, Quality Appraisal,
            Adjudication. Run with `pytest tests/ -v` (Python 3.12+).
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

Lives at `/quality-appraisal` ([route in main.py](main.py), UI in [frontend/quality-appraisal.html](frontend/quality-appraisal.html), backend in [backend/quality_appraisal.py](backend/quality_appraisal.py) + [backend/rob_tools/](backend/rob_tools/) + [backend/reporting_guidelines/](backend/reporting_guidelines/)). Reuses the annotator's `classify_study_design`, `prefill_fields`, `_call_with_pdf` (3-stage oversize fallback), `load_paper_pdf`, credits, and `require_active_seat` — no parallel user/paper system.

**Pipeline per paper** (≈ 8 LLM calls for RCTs, ≈ 10 for non-randomized designs — 7 domain calls + classify + prefill + guideline; flat **30 credits** per paper):
1. Classify study design via annotator.
2. Extract universal + type-specific + modifier fields via annotator.
3. Auto-pick primary outcome from `primary_outcome_definition` → `primary_outcome_measurement` → `population_outcomes`.
4. Per-domain LLM calls for the registered RoB tool — **pure-Python decision trees** map Y/PY/PN/N/NI signal answers to tool-specific judgements (RoB 2 is 3-level Low/Some concerns/High; ROBINS-I is 5-level Low/Moderate/Serious/Critical/No information). Trees live in code (not prompts) so the developer view can show the exact logic via `inspect.getsource`.
5. Single-call adherence check against the registered reporting guideline.
6. Compute initial GRADE (from registry) + updated GRADE after RoB. Downgrades only for RoB in v1 (other GRADE domains need a body of evidence, not a single study).

**Extensibility contract** — [backend/quality_appraisal.py:STUDY_TYPE_REGISTRY](backend/quality_appraisal.py) is the single source of truth mapping `{study_type → (rob_tool, reporting_guideline, initial_grade)}`. v1 supports:
- **Randomized Controlled Trial → RoB 2 (2019) + CONSORT 2025 + High initial GRADE**
- **Cohort Study / Case-Control / Non-Randomized Trial / Cross-Sectional (Analytical) / Case-Crossover → ROBINS-I (2016) + STROBE 2007 + Low initial GRADE**

Registry keys MUST match `annotator.TYPE_FIELD_IDS` keys — the test `TestDispatch::test_registry_keys_match_annotator_types` enforces this. Unsupported study types return `None` → the paper is marked `skipped`, credits refund. Adding a new study type: add a registry entry + new module in `backend/rob_tools/` (for the tool) and/or `backend/reporting_guidelines/` (for the guideline), each exposing `run(...)` and `prompt_catalog()`, then register the callable in `_TOOL_RUNNERS` / `_GUIDELINE_RUNNERS`.

**Credit gate + refund**: pre-charge via `_annotator_ai_gate`; refund per-paper on error/skip via `billing.refund_credits` — same idiom as custom annotator runs. Admin bypasses.

**Background execution**: inline for ≤3 papers, daemon thread for larger batches via [backend/quality_appraisal.py:run_batch_async](backend/quality_appraisal.py). Progress is logged to `quality_appraisal_events` and polled every 5s by the frontend (`GET /runs/{id}/events?after=<last_id>` returns incremental events).

**Developer view** (🔧 icon in topbar, visible to every signed-in user) — `GET /api/quality-appraisal/prompts` returns the full prompt templates, signaling questions, and `inspect.getsource` output for every decision tree + GRADE logic. Transparency by default: reviewers can see exactly how a judgement was produced.

**DB tables** (initialised from `QUALITY_APPRAISAL_TABLES_SQL`): `quality_appraisal_runs`, `quality_appraisal_results`, `quality_appraisal_events`. All date columns use `created_at` / `completed_at` (no `timestamp` column per the SQLite compat-wrapper gotcha). Runs are soft-deleted via `deleted_at`.

**Endpoints** (seat tiers match the annotator's): `GET /api/quality-appraisal/supported-types` (general), `GET /api/quality-appraisal/prompts` (general, the dev view), `POST /api/quality-appraisal/runs` (engineer), `GET /api/quality-appraisal/runs` (general), `GET /api/quality-appraisal/runs/{id}` (general), `GET /api/quality-appraisal/runs/{id}/events?after=<id>` (general, incremental poll), `GET /api/quality-appraisal/runs/{id}.csv|.xlsx` (general), `DELETE /api/quality-appraisal/runs/{id}` (general, soft delete).

**Detail view** — each row in the results grid is clickable (📋 icon on the study cell, plus each RoB domain / CONSORT / GRADE cell). Opens a full-screen split modal: **PDF.js viewer on the left** (loaded from `/api/papers/{pid}/pdf`, canvas + text layer per page) and a scrollable **detail panel on the right** with Summary → RoB (5 collapsible RoB 2 domains or 7 ROBINS-I domains depending on the row's tool) → Reporting guideline (CONSORT 2025 or STROBE 2007, grouped by section, ✓/✗/N-A) → GRADE (initial → updated with downgrade explanation and domain breakdown) → Extracted fields. Clicking any rationale or evidence chip **searches the live PDF text layer** for the first ~80 chars of the quote (with a longest-matching word n-gram fallback for paraphrased quotes) and flash-highlights the match. Prior highlight clears on next click; the clicked chip gets a yellow "active" marker. Quote-to-highlight is best-effort — we never asked the LLM for PDF coordinates, so the fallback may miss for heavily paraphrased quotes (toast surfaces the miss). The frontend looks up `domainMetaFor(r.rob_tool)` to pick between `ROB2_DOMAIN_META` (5 domains) and `ROBINS_I_DOMAIN_META` (7 domains); `robBadgeCls(j)` maps any judgement (3-level RoB 2 or 5-level ROBINS-I) to a badge CSS class. See [frontend/quality-appraisal.html](frontend/quality-appraisal.html) `openDetailModal`, `renderDetailPanel`, `loadDetailPdf`, `jumpToQuote`.

**Mixed-tool runs** — if a run includes papers of different study types (some RCT + some Cohort), the results grid column set is taken from the first successful row's tool. Other rows with a different tool still render correctly; domain cells for non-matching domain IDs show `—`. Single-design runs are the common case, so this trade-off is intentional for v1.

**Out of scope for v1**: Quasi-experimental designs (Uncontrolled Before-After, Interrupted Time Series, Difference-in-Differences, Regression Discontinuity) — each needs its own confounding prompt + ROBINS-I adaptation. ROBINS-I effect-of-adherence D4 variant (effect-of-assignment only in v1). AMSTAR-2 (systematic reviews), QUADAS-2 (diagnostic accuracy), PRISMA 2020, STARD. Cluster/crossover/stepped-wedge RCT variants (parallel-trial cribsheet only). Editing / overriding AI judgements in the UI. Full GRADE assessment across inconsistency / indirectness / imprecision / publication bias (requires a body of evidence). Per-outcome user selection (we auto-pick primary).

## Search Strategist — 4-tier PDF Import Pipeline

Lives at `/search` ([frontend/search.html](frontend/search.html)) and inside the Lab ([frontend/lab.html](frontend/lab.html)). Both surfaces share `/api/search/import` which dispatches into one of four modes via the `mode` field on `SearchImportPayload`:

| Mode | Cost / paper | Sync? | What it does |
|------|--------------|-------|--------------|
| `metadata` | free | sync | Stash a `papers` row with title/authors/abstract + `external_url`. Sets `pdf_status='metadata_only'`. No download. |
| `fetch` | 2 credits | async | Background worker tries: `download_pmc_pdf` → Unpaywall → direct GET → `citation_pdf_url` meta tag. Browser-spoof UA + Referer header so paywall publishers don't 403 us. |
| `firecrawl` | 5 credits | async | Same as `fetch` plus a final Firecrawl JS-render fallback. Crawls the **Unpaywall-resolved publisher landing URL** (not the PubMed URL — PubMed rarely has citation_pdf_url). Requires `FIRECRAWL_API_KEY`. |
| `browser` | 15 credits | async | Same as `firecrawl` plus a final Playwright/Chromium browser-agent fallback that picks up real session cookies. Requires Playwright + Chromium installed (see [render.yaml](render.yaml) buildCommand + [apt.txt](apt.txt)). |

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
- `playwright install chromium` adds ~170MB to the build. First deploy after enabling browser mode takes 4–8 minutes.
- Defeats simple bot detection (UA + cookies + Referer) but **not** Cloudflare Turnstile / hCaptcha. Login-walled content needs the user's institutional credentials, which we don't store.
- We use heuristic link selectors (`citation_pdf_url` → `[href*="/pdf/"]` → "Download PDF" text) — no LLM in the loop yet. The browser agent module is structured so an LLM-driven navigator can be slotted in as a follow-up if heuristics aren't enough.

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
