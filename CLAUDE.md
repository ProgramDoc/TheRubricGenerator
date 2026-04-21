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
| Modify the rubric generator | `backend/agents/generator.py` |
| Modify the judge | `backend/agents/judge.py` |
| Modify challenge scoring | `backend/challenges.py` (scoring functions + `run_challenge()`) |
| Call an LLM | `backend/helpers.py` — `call_anthropic()`, `call_gemini()`, `call_openai_compatible()` |
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
| Add organization feature | `backend/organizations.py` |
| Write Obsidian notes | `backend/obsidian.py` |
| Run tests | `pytest tests/ -v` (requires Python 3.12 for `str | None` syntax) |

## Architecture at a Glance

```
main.py (5040 lines)
  ├── Database init + migrations (lines 120-560)
  ├── Auth: cookie sessions + X-API-Key header (lines 650-690)
  ├── Page routes: GET /, /lab, /challenges, /search, etc. (lines 730-880)
  ├── API endpoints grouped by resource (~lines 880-5040)
  └── Static files mount: /static → frontend/

backend/
  ├── db.py             — Database compatibility layer (PostgreSQL + SQLite fallback)
  ├── helpers.py        — LLM callers (Anthropic, Gemini, OpenAI-compatible), JSON parsing
  ├── challenges.py     — Challenge orchestration, scoring, points, leaderboard, event logging
  ├── agents/
  │   ├── generator.py  — Rubric generation from PDFs (batched for >3 papers)
  │   ├── judge.py      — Answer grading + shadow regrade
  │   ├── participants.py — Run competing models against rubric
  │   └── lab_agents.py — Lab agent runners (6 new agent types)
  ├── lab.py            — Lab session CRUD, chat orchestrator, document management
  ├── storage.py        — S3/local file storage abstraction
  ├── paper_files.py    — Paper-file read/write/delete (uses storage.py + legacy fallback)
  ├── annotator.py      — OGAI Annotator: tables, field catalog, AI prompts, analytics
  ├── exports.py        — Export converters (Word, LaTeX, Excel, CSV, Python, R)
  ├── code_runner.py    — Sandboxed Python/R code execution
  ├── pubmed.py         — PubMed E-utilities, iCite citations, PMC PDF download
  ├── scheduler.py      — Daily challenge automation (7am PST Mon-Fri)
  ├── search.py         — AI search chatbot, PubMed/Europe PMC, import/export
  ├── billing.py        — Stripe credits, cost estimation, refunds
  ├── membership.py     — Free/Pro/Enterprise plans, PDF limits, storage limits
  ├── organizations.py  — Multi-tenant orgs with roles
  ├── templates.py      — Rubric templates, community library
  ├── analytics.py      — Performance breakdown, CSV/PDF export (challenge benchmarks)
  ├── skills.py         — Agent skill versioning (10 agent types)
  ├── self_improve.py   — Autoresearch experiment loop
  ├── obsidian.py       — Markdown vault writer
  ├── agreements.py     — Legal text
  └── promo.py          — Promo codes

frontend/ — 20 self-contained HTML files (inline CSS + JS, no build step)
tests/    — pytest suite (Competition API + Annotator; 40 cases)
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

**Data model:** `annotations` (per paper+reviewer, optimistic-concurrency `version` column, `updated_at` not `timestamp`), `annotation_spans` (text→field linkages), plus `annotator_custom_schemas` + `annotator_custom_runs` for the custom-extraction feature. All initialised from `ANNOTATOR_TABLES_SQL` in `backend/annotator.py`, executed by `main.py:init_db()`.

**Right-pane tab bar:** `Form` / `✨ Custom` / `Results` / `Analytics ↗`. Form uses `display: none` when inactive so span linking keeps working. Active tab persists in `localStorage` under `annotator_active_tab` (allowed values: `form` / `chat` / `results`). The `Analytics ↗` button is NOT a pane — it navigates to `/analytics#annotator`. All annotator analytics live in the unified analytics page ([frontend/analytics.html](frontend/analytics.html)) which has tabs for Benchmark / Annotator / Admin analytics; hash routing (`#annotator`, `#admin`) selects the initial tab.

**AI calls (all credit-gated, admin bypass, auto-refund on failure):**
- Classify study design: 3 credits — `POST /api/annotator/papers/{id}/classify`
- Prefill fields: 8 credits — `POST /api/annotator/papers/{id}/prefill` (accepts `groups`, `type_fields`, `modifier_fields`)
- Parse custom schema from upload/text: 2 credits — `POST /api/annotator/schemas/parse` (accepts PDF, DOCX — converted to markdown via `python-docx` — CSV, TXT, MD, or raw text)
- Refine custom schema: 1 credit — `POST /api/annotator/schemas/refine`
- Custom batch run: 8 credits × paper — `POST /api/annotator/schemas/{id}/run` (≤10 papers in-request, larger = background thread)

**Unified extraction entry point:** the Custom tab's "▶ Run extraction (batch)" button and the topbar "☰ Batch" button both open the same batch modal. That modal has three optional steps: (1) classify study design, (2) prefill Form-tab fields, (3) run a saved custom schema. Opening from the Custom tab preselects the currently-loaded schema in the modal's "Custom schema" dropdown; opening from the topbar leaves it empty. There is no separate "run this custom schema alone" code path — custom runs always happen via `runBatch()` in [frontend/annotator.html](frontend/annotator.html).

**Saved schemas are clickable:** `renderSchemaList()` renders each row as a role=button that calls `loadSchemaIntoBuilder(id)` on click/Enter/Space. The active schema gets a "loaded" chip; the ✕ button uses `event.stopPropagation()` so it doesn't fire the row click.

**Large-file extraction pipeline (`backend/annotator.py:_call_with_pdf`):** Every annotator AI call (classify / prefill / custom / schema-parse) funnels through this function. It degrades gracefully through three stages:

1. **PDF-as-document (fast path).** Base64-encode the PDF and attach as an Anthropic document block. Covers ~95% of papers.
2. **Text fallback.** If stage 1 raises the context-window 413, we run `_extract_pdf_text()` (pypdf, already in requirements.txt) to pull plain text out of the PDF. Image-heavy papers that were ~200k tokens as a document are usually well under 100k as plain text.
3. **Chunked map-reduce.** If the extracted text is still too large, we split into overlapping 300k-char windows (`_CHUNK_CHAR_SIZE`, `_CHUNK_OVERLAP=8000`) and run them in parallel via `concurrent.futures.ThreadPoolExecutor` (max 4 workers, 8 chunks). Per-chunk JSON responses are merged in `_merge_extractions`: for each field, the earliest non-empty value wins — earlier chunks carry abstract/methods/results, which is where authoritative values typically live. Failed chunks are logged and skipped; if all chunks fail we raise a clean 502.

Classification tasks (`classify_study_design`) and the schema-proposal task (`parse_schema_from_pdf`) pass `classification_mode=True`, which short-circuits stage 3 to first-chunk-only — chunk-and-merge doesn't make sense for a single holistic output (study type / proposed field list). Regular prefill and custom extraction use the full merge pipeline.

Upfront: a 32 MB byte-size guard (`_PDF_UPLOAD_BYTE_LIMIT`) short-circuits before we even base64-encode, since Anthropic's PDF beta rejects anything larger. All error paths raise `HTTPException` with actionable vendor-free messages, and the existing `_refund_annotator` / `refund_credits` handlers auto-refund the credit charge.

**Persistence:** localStorage draft (not sessionStorage, so it survives tab close) + backend save on `input` (1.5 s debounce) + `keepalive: true` fetch on `beforeunload` / `pagehide` / `visibilitychange → hidden` / `logout()`. `loadExistingAnnotation` prefers the local draft over the backend copy, so unsent edits come back even if the network save failed.

## Quality Appraisal AI

Lives at `/quality-appraisal` ([route in main.py](main.py), UI in [frontend/quality-appraisal.html](frontend/quality-appraisal.html), backend in [backend/quality_appraisal.py](backend/quality_appraisal.py) + [backend/rob_tools/](backend/rob_tools/) + [backend/reporting_guidelines/](backend/reporting_guidelines/)). Reuses the annotator's `classify_study_design`, `prefill_fields`, `_call_with_pdf` (3-stage oversize fallback), `load_paper_pdf`, credits, and `require_active_seat` — no parallel user/paper system.

**Pipeline per paper** (≈ 8 LLM calls, **30 credits**):
1. Classify study design via annotator.
2. Extract universal + type-specific + modifier fields via annotator.
3. Auto-pick primary outcome from `primary_outcome_definition` → `primary_outcome_measurement` → `population_outcomes`.
4. Per-domain LLM calls for the registered RoB tool — **pure-Python decision trees** map Y/PY/PN/N/NI signal answers to Low / Some concerns / High judgements. Trees live in code (not prompts) so the developer view can show the exact logic via `inspect.getsource`.
5. Single-call adherence check against the registered reporting guideline.
6. Compute initial GRADE (from registry) + updated GRADE after RoB. Downgrades only for RoB in v1 (other GRADE domains need a body of evidence, not a single study).

**Extensibility contract** — [backend/quality_appraisal.py:STUDY_TYPE_REGISTRY](backend/quality_appraisal.py) is the single source of truth mapping `{study_type → (rob_tool, reporting_guideline, initial_grade)}`. v1 supports **Randomized Controlled Trial → RoB 2 (2019) + CONSORT 2025 + High initial GRADE**. Registry keys MUST match `annotator.TYPE_FIELD_IDS` keys — the test `TestDispatch::test_registry_keys_match_annotator_types` enforces this. Unsupported study types return `None` → the paper is marked `skipped`, credits refund. Adding a new study type: add a registry entry + new module in `backend/rob_tools/` (for the tool) and/or `backend/reporting_guidelines/` (for the guideline), each exposing `run(...)` and `prompt_catalog()`, then register the callable in `_TOOL_RUNNERS` / `_GUIDELINE_RUNNERS`.

**Credit gate + refund**: pre-charge via `_annotator_ai_gate`; refund per-paper on error/skip via `billing.refund_credits` — same idiom as custom annotator runs. Admin bypasses.

**Background execution**: inline for ≤3 papers, daemon thread for larger batches via [backend/quality_appraisal.py:run_batch_async](backend/quality_appraisal.py). Progress is logged to `quality_appraisal_events` and polled every 5s by the frontend (`GET /runs/{id}/events?after=<last_id>` returns incremental events).

**Developer view** (🔧 icon in topbar, visible to every signed-in user) — `GET /api/quality-appraisal/prompts` returns the full prompt templates, signaling questions, and `inspect.getsource` output for every decision tree + GRADE logic. Transparency by default: reviewers can see exactly how a judgement was produced.

**DB tables** (initialised from `QUALITY_APPRAISAL_TABLES_SQL`): `quality_appraisal_runs`, `quality_appraisal_results`, `quality_appraisal_events`. All date columns use `created_at` / `completed_at` (no `timestamp` column per the SQLite compat-wrapper gotcha). Runs are soft-deleted via `deleted_at`.

**Endpoints** (seat tiers match the annotator's): `GET /api/quality-appraisal/supported-types` (general), `GET /api/quality-appraisal/prompts` (general, the dev view), `POST /api/quality-appraisal/runs` (engineer), `GET /api/quality-appraisal/runs` (general), `GET /api/quality-appraisal/runs/{id}` (general), `GET /api/quality-appraisal/runs/{id}/events?after=<id>` (general, incremental poll), `GET /api/quality-appraisal/runs/{id}.csv|.xlsx` (general), `DELETE /api/quality-appraisal/runs/{id}` (general, soft delete).

**Out of scope for v1**: non-RCT study types (registry stubs in place), ROBINS-I / AMSTAR-2 / QUADAS-2 and other tools, STROBE / PRISMA / STARD and other guidelines, editing / overriding AI judgements in the UI, full GRADE assessment across inconsistency/indirectness/imprecision/publication bias, per-outcome user selection (we auto-pick primary), cluster/crossover/stepped-wedge RCT variants (cribsheet is parallel-trial only).

## Environment Variables

**Required**: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ADMIN_SECRET`
**Database**: `DATABASE_URL` (PostgreSQL connection string — set on Render, omit locally for SQLite fallback)
**Billing**: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`
**Cloud storage**: `AWS_S3_BUCKET`, `AWS_S3_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (omit all for local fallback)
**Optional**: `NCBI_API_KEY` (PubMed rate boost), `MOONSHOT_API_KEY` (Kimi), SMTP vars (email)

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
