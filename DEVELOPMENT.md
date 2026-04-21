# The AI Researcher - Development Log

**Repository:** https://github.com/ProgramDoc/TheRubricGenerator
**Platform:** FastAPI + PostgreSQL (SQLite fallback for dev/tests) + Static HTML frontend
**Deployed on:** Render (PostgreSQL database, Python 3.12.3)

---

## Current State (April 20, 2026)

### Codebase Summary

| Component | Files | Lines |
|-----------|-------|-------|
| `main.py` | 1 | ~6,890 |
| `backend/` modules | 27 | ~13,100 |
| `frontend/` pages | 22 | ~21,650 |
| `frontend/_shared/design.css` | 1 | ~420 |
| `tests/` | 3 | ~670 |
| **Total** | **54** | **~42,730** |

### What's Live

- **Two-agent benchmark system:** Claude Rubric Generator + Claude Judge
- **Five frontier models:** Claude Opus 4.6, GPT-5.4, Gemini 3.1, Gemini 3.1 Pro, Kimi K2 Thinking
- **Daily AI Researcher Challenge:** Automated PubMed fetch (14 rotating themes), 7am PST Mon-Fri, mixed difficulty (2/2/4/2), bonus round, 100 pts/correct
- **Points system:** Individual (1/2/5/10 by difficulty), Daily (100 pts/correct, 10x Jedi), bonus round (20 pts/q if 10/10)
- **Dual leaderboard:** Overall (total points) + Daily (streak, movement, expandable drill-down)
- **Individual tests:** User-designed, private or public, four cognitive difficulty levels
- **Project folders:** Left sidebar, sharing by email, admin/member roles, admin transfer, protected delete
- **Model registry:** Unique name, version, team with can_run permissions, credit warning acknowledgment
- **Competition API:** External models fetch questions + submit answers (X-Model-Key auth), admin approval queue
- **Self-improvement loop:** Autoresearch-style experiment loop (propose → eval → binary keep/discard, up to 5 experiments per cycle)
- **Billing:** Stripe prepaid credits ($10/$25/$50), per-test deduction, daily pricing ($3/day or $10/week)
- **Promo codes:** Admin-created (free/breakeven), 48h auto-approve then admin gate
- **Legal agreements:** Model Publishing Agreement + Payment Agreement (acceptance tracked)
- **SSO:** Cross-app auth to The AI Researcher Annotator via HMAC-signed tokens
- **Obsidian vault:** Persistent markdown notes per challenge + agent skill version history
- **Password reset:** SMTP email flow with 1hr token expiry
- **Admin dashboard:** User management, daily scheduler controls, skill improvement panel, model approval queue
- **Dry-run mode:** Test daily challenges without recording to leaderboard
- **Analytics dashboard:** Per-model breakdown by theme/difficulty, historical trend charts (Chart.js), CSV/PDF export
- **Public leaderboard API:** Rate-limited JSON API for third-party integrations (`/api/public/leaderboard`)
- **Email notifications:** Opt-in daily challenge completion emails
- **Organizations:** Multi-tenant teams with viewer/contributor/admin roles, shared credit pools, invite-link and email-domain auto-join
- **Org leaderboard:** Aggregate model performance ranked by organization
- **Org billing:** Separate credit pool per org, Stripe checkout, personal-to-org credit transfers
- **Multi-paper comparative rubrics:** New `comparative` rubric type for cross-paper synthesis (contradictions, methodology, population, outcomes, evidence strength)
- **Rubric templates:** Reusable, versioned templates with living question stats (auto-flag too-easy/broken questions)
- **Community library:** Publish, browse, search, fork, and rate (1-5 stars) rubric templates
- **Ground truth annotations:** Import expert answers from The AI Researcher Annotator, compare AI judge accuracy to human experts
- **Literature search:** AI-powered multi-database search with chatbot strategist, PICO extraction, PubMed/Europe PMC full search, link-outs for Scholar/JSTOR/WoS/ScienceDirect/Wiley/OVID, import results as papers, RIS/BibTeX export, sortable results columns (title/authors/journal/year/cites), text filter across results, sidebar with search organization and project folders
- **Search sidebar:** Left sidebar with saved searches (auto-named from first prompt), project folders, drag-and-drop organization, context menu with rename/move/delete, collapsible folders with search counts
- **Project sharing & invitations:** Share projects by email, invite unregistered users (registration invite email sent, auto-added on sign-up), transfer ownership, leave/remove members, share modal with member management
- **PostgreSQL migration:** Primary database switched from SQLite to PostgreSQL for Render deployment persistence. Compatibility wrapper (`backend/db.py`) auto-converts SQL syntax between PostgreSQL and SQLite, enabling SQLite fallback for local dev and tests
- **Membership plans:** Free (20 PDFs, 100MB storage), Pro ($29/mo, 500 PDFs, 5GB storage, 1000 credits), Enterprise ($99/mo, unlimited, 50GB storage, 5000 credits) via Stripe subscriptions
- **Cloud storage:** S3-based file storage with local fallback (`backend/storage.py`), per-plan storage limits, usage tracking endpoint
- **Platform API:** User API keys (`rg_user_xxx`) authenticate all endpoints via `X-API-Key` header. Developers page with key management + full endpoint docs
- **Challenge improvements:** Inline PDF upload, 5 questions per PDF (batched for large sets), cost estimation + credit enforcement, unique run IDs, AI Brain Window with real-time progress events, cancel/delete, per-question speed metrics, paper removal
- **Enhanced Obsidian notes:** Run ID, user info, cost breakdown, generator/judge agent state, experiment history
- **Rebranded:** "The AI Researcher" (no OGAI/UCLA/INOVAi references)
- **The AI Researcher Lab:** 3-pane interface (sidebar + chat + tabbed workspace) with 8 AI agent speed buttons (Research Chat, Search Strategist, Statistician, Study Appraiser, Hypothesis Generator, Literature Reviewer, Study Builder, Protocol Evaluator), contextual right-pane tabs per agent, chat-first file uploads with context prompt, clipboard paste screenshot support, drag-and-drop documents
- **Nav restructured:** Benchmark Lab dropdown (Challenges + Leaderboard), Settings gear (Billing + Preferences), Developers tab, user display name
- **Unit tests:** pytest suite for Competition API (16 test cases) + Annotator (24 test cases) = 40 total
- **Daily scheduler fix:** Removed restrictive OA filter, MeSH-based queries, broad PubMed search → rank by citations → top 10
- **OGAI Annotator integration:** Native port of the annotator into the Benchmark Lab as `/annotator`. Full classify-and-extract flow with universal / type-specific / cross-cutting field groups, credit-gated AI calls, project-shared paper pool, optimistic-concurrency save/load with localStorage draft + keepalive-backed backend persistence
- **Annotator batch extraction:** Checkbox selection + batch bar with "Run batch…", "Move selected to project…", and "✕ Delete selected"; batch modal lets users pick field groups (Layer 1), per-study-type field subsets (Layer 2), and cross-cutting modifiers (Layer 3)
- **Annotator custom extraction:** ✨ Custom tab — upload a protocol/CRF/CSV, AI proposes an extraction schema, user inline-edits + refines with free-text instructions, saves per-user, runs on batch with credit-gated per-paper extraction. Results appear as a paper×field grid in the Results tab with CSV export
- **Annotator analytics dashboard:** Right-pane Analytics tab with four Chart.js views (field completion rates, categorical distributions, min/median/max numeric summaries, reviewer-action breakdown) scoped to current paper / project / batch / all
- **Paper storage on S3:** Paper uploads now route through `backend/paper_files.py` → `backend/storage.py` (S3 when `AWS_S3_BUCKET` is set, local `uploads/` otherwise). `papers.storage_path` records the durable location; legacy `disk_filename` is kept as a fallback for pre-migration rows. Survives Render's ephemeral-disk wipes
- **Lab Conversations section:** Now capped at 180px with internal scroll; click the header to expand to 60vh. State persists in localStorage so other sections (Context, Projects, Invitations, Interfaces) stay in view
- **Enterprise seat model (behind `ENTERPRISE_MODE` flag):** Legacy individual Free/Pro/Enterprise plans replaced by an enterprise-only seat model. Seats: Admin ($450/mo, 500 credits), Engineer ($250/mo, 300 credits), General ($100/mo, 100 credits). One Stripe subscription per org with three SubscriptionItems — quantity = purchased seat pool. `require_active_seat(user, min_seat)` middleware (141 call sites) is a no-op while the flag is off, then enforces `402 no_active_seat` / `403 insufficient_seat` on cutover. Owner/admin/engineer/general role hierarchy with 7-day past_due grace window
- **Enterprise pages:** `/onboarding` (join-by-code or start-enterprise) and `/enterprise/{id}` (seat pool management, members table, cost card). `/billing` plan grid gone; banner routes to `/enterprise/{id}` or `/onboarding` depending on seat state
- **Paper design system:** Global OKLCH-based Paper theme (`frontend/_shared/design.css`) with Geist + Source Serif 4 + JetBrains Mono typography. All pages migrated to shared tokens (`--paper`, `--ink`, `--rule`, `--accent`, `--radius-sm/md/lg`, `--shadow-1/2/3`, `--topbar-h`). Consistent brand-mark / topbar-right markup across every page
- **Lab Sources/Briefing/Methods:** Three per-user, cross-project features replacing the old "Context" section. **Sources**: uploaded files with selection checkboxes; only checked files' `document_ids` reach the chat API (previously all uploads were sent or none). **Briefing**: free-form text (4 000-char cap) prepended to every agent's system prompt. **Methods**: user-authored procedure cards (name + when-to-use + instructions + active toggle) appended to the system prompt, plus read-only `agent_skills` capability cards surfaced without exposing `prompt_text`. Research-native naming avoids Claude/Anthropic's Files/Instructions/Skills terms
- **The Benchmark Lab sidebar heading:** Lab sidebar's "Interfaces" section renamed to "The Benchmark Lab" with an inline flask+sparkline SVG logo. Flattened sub-group so every interface (Dashboard, Challenges, Leaderboard, Analytics, Models, PDF Viewer, Annotator, Admin) is a direct child. Library + Developers moved out to the topbar nav. Projects moved above Conversations in the left pane
- **Lab agent roles with logo + color:** "Research Chat" → "The Research Assistant"; every agent gets a "The X" role name (The Statistician, The Evidence Appraiser, The Hypothesis Generator, The Literature Reviewer, etc.). Each agent carries its own accent color and icon. `switchAgent()` sets `--agent-c` on `#panel-center`; the label, label dot, active speed button, and agent-switch divider pill all pick it up. Switching a role stays in the same thread — no new chat
- **Annotator draggable divider:** 6 px `.pane-resizer` between `#pdf-panel` and `#form-panel`; `--form-w` CSS variable baselines the post-sidebar split 50/50. Drag updates live, mouseup persists px to localStorage, double-click resets. Min widths 300 px (PDF) / 280 px (form) so neither pane collapses
- **Annotator results pane: delete runs + back button:** `DELETE /api/annotator/runs/{rid}` (engineer seat, schema + papers preserved). "← Back to annotator" button at the top of the Results pane returns to the Form tab. Red "✕ Delete" button with confirmation next to the CSV export
- **PDF viewer + annotator Paper-themed chrome:** The PDF viewer at `/pdf-viewer` (served from `rubric_generator.html`) and the annotator's `#pdf-panel` swap dark-IDE greys (#2a2a2a / #1a1a1a) for the cream `--paper-2` + hairline `--rule` borders. Toolbar buttons become pill-outlined chips, page shadow softened to a small-crisp + diffused pair, text-layer highlight + selection adopt the Paper accent. Annotator form sections render as soft cards on cream (1 px rule, 10 px radius, subtle shadow). Pure visual — no field IDs, inputs, spans, or handlers changed

---

## Version History

### v1.0 - Initial Build (April 2, 2026)

Two-LLM evaluation platform. Claude generates rubrics from PDFs, a second LLM (originally GPT-4o) answers, Claude grades.

**Key decision:** Human-editable rubric between generation and evaluation prevents circularity.

### v1.0.1 - Security & Robustness Fixes (April 2, 2026)

- Missing `/api/auth/admin` endpoint, 50MB upload limit, batch cap, safe markdown parsing
- Server-side validation, database indices, INFO logging, Python 3.12.3 pinned

### v1.1 - Gemini Support (April 5, 2026)

- Google Gemini API via `_call_gemini()` with native PDF base64 support

### v1.2 - Password Reset & Admin Dashboard (April 5, 2026)

- SMTP password reset, admin.html user dashboard, admin email `tck936@mail.harvard.edu`

### Phase 1 - Benchmark Platform (April 5, 2026)

- Modular `backend/` package (agents, challenges, skills, obsidian, helpers)
- Two-agent architecture: Generator + Judge with versioned skills
- Challenge orchestration on background threads
- Dashboard, Challenges, Challenge Viewer, Leaderboard pages

### Phase 1.5 - User Challenges & Model Registry (April 5, 2026)

- Project folders, private/public visibility, four difficulty levels
- Model registry with team members, public tests gallery
- Leaderboard isolation: only `kind='daily'` counts

### Phase 2 - PubMed Auto-Fetch & Daily Scheduler (April 5, 2026)

- `backend/pubmed.py` — E-utilities + iCite + PMC PDF download
- `backend/scheduler.py` — asyncio daily loop, 7am PST Mon-Fri
- 14 seed themes, theme fallback on failure, system user

### Phase 3 - Billing, Marketplace & Updated Models (April 5, 2026)

- Updated frontier models (Claude Opus 4.6, GPT-5.4, Gemini 3.1/Pro, Kimi K2)
- `call_openai_compatible()` for OpenAI-compatible APIs
- Stripe prepaid credits, promo codes, legal agreements

### Phase 3.5 - Folders, Teams, Points & Daily AI Researcher Challenge (April 5, 2026)

- Points system: individual (1/2/5/10) + daily (100 pts/correct) + bonus round
- Daily composition: 2 easy + 2 minor + 4 professional + 2 jedi
- Dual leaderboard: overall (total points) + daily (streak, movement, expandable rows)
- Dashboard left sidebar with project folders (admin/member roles)
- Project sharing, admin transfer, protected delete, self-removal
- Model team `can_run` permissions with credit warning acknowledgment
- SSO to The AI Researcher Annotator via HMAC-signed redirect tokens

### Phase 4 - Agent Self-Improvement Loop (April 5, 2026)

Autoresearch-style experiment loop (inspired by karpathy/autoresearch):
- After each daily challenge, runs up to 5 experiments per agent
- Each experiment: meta-Claude proposes ONE focused modification → lightweight eval → binary keep/discard
- Lightweight eval: generator (3-question mini-rubric + quality assessment), judge (discrimination test)
- Simplicity criterion: simpler prompts that achieve equal results preferred
- Experiment log table (`skill_experiments`) tracks every attempt like autoresearch's results.tsv
- Admin panel shows experiment history per agent

### Phase 5 - External Model Competition API (April 5, 2026)

External models submit answers to OUR API (we don't call theirs):
- `GET /api/compete/{id}/questions` — fetch questions (ideal answers stripped)
- `POST /api/compete/{id}/submit` — submit answers
- `GET /api/compete/{id}/results` — view grades
- Authenticated via `X-Model-Key` header (key generated at registration)
- Admin approval queue for daily challenge participation
- `POST /api/admin/challenges/{id}/grade-submissions` — batch grade external answers
- Dry-run mode for testing daily challenges without leaderboard impact

---

## Planned Development

### Phase 6 - Advanced Analytics & Reporting (April 6, 2026)

- `backend/analytics.py` — query engine for per-model breakdown, historical trends, theme stats
- Analytics dashboard (`/analytics`) with Chart.js bar/line charts, filter controls, export buttons
- Per-model accuracy breakdown by theme, difficulty level, question domain
- Historical trend charts (accuracy over time per model, daily challenges)
- Exportable benchmark reports: CSV (per-question detail) and PDF (summary tables via reportlab)
- Public leaderboard API: `GET /api/public/leaderboard`, `/api/public/leaderboard/daily`, `/api/public/models` — no auth, rate-limited (60 req/min per IP)
- Notification preferences: opt-in daily challenge completion emails via existing SMTP
- `analytics_snapshots` cache table rebuilt after each challenge for fast aggregation
- Integration hooks in challenge pipeline: analytics refresh + email notification (try/except, non-blocking)

### Phase 7 - Multi-Tenant Teams & Organizations (April 6, 2026)

- `backend/organizations.py` — org CRUD, membership management, role hierarchy (viewer < contributor < admin)
- Organization dashboard (`/org/{id}`) with Members, Models, Billing, Settings tabs
- Shared billing: `org_credits` pool, Stripe checkout for orgs, personal-to-org credit transfers
- Invite-link joining (`organizations.invite_code`) + email-domain auto-join on registration
- Organization leaderboard: third tab on leaderboard page, aggregates `challenge_submissions` by org
- Public org leaderboard API: `GET /api/public/leaderboard/organizations` (rate-limited)
- Model assignment: `registered_models.org_id` FK, org dropdown on model creation form
- Dashboard sidebar: organizations section with role badges and "Create Organization" button
- Billing page: org credits summary table with links to per-org billing
- 17 new API endpoints: org CRUD (7), membership (3), billing (4), models (1), leaderboard (2)
- 5 new DB tables: `organizations`, `org_members`, `org_credits`, `org_credit_transactions`, `org_leaderboard_cache`

### Phase 8 - Advanced Rubric Types (April 6, 2026)

- `backend/templates.py` — template CRUD, community library, living stats, ground truth import
- Multi-paper comparative rubrics: new `comparative` type in generator.py, cross-paper synthesis questions with `paper_refs`
- Rubric templates: versioned (`rubric_templates` table), fork chain via `parent_id`, save from existing rubric
- Living template stats: `template_question_stats` tracks per-question avg score, auto-flags too_easy (>95%) and broken (<5%) after 5+ uses
- Community library (`/library`): publish/unpublish, browse with search/filter/sort (recent/rating/popular), fork, rate (1-5 stars)
- 5 new DB tables: `rubric_templates`, `template_question_stats`, `community_templates`, `community_ratings`, `ground_truth_annotations`
- Ground truth / Annotator integration: HMAC-authenticated import endpoint, expert answer storage, judge-vs-human accuracy comparison
- 20 new API endpoints: template CRUD (8), community library (5), comparative generation (1), ground truth (3), stats (1), evaluation accuracy (1)
- Frontend: `library.html` (community browser with cards, preview modal, fork/rate), "Comparative" rubric type + save/load template buttons in `rubric_generator.html`

### Literature Search Interface (April 6, 2026)

- `backend/search.py` — session-based conversational search with AI strategist chatbot
- Dual-panel UI (`/search`): left = AI chat with PICO extraction and query refinement, right = Query Builder + Results workspace
- AI search strategist: extracts PICO elements, generates structured PubMed Boolean queries with MeSH terms, translates to Ovid MEDLINE/Web of Science syntax, refines queries conversationally
- Full search integration: PubMed (esearch → esummary → efetch with abstracts → iCite citations) and Europe PMC (REST API)
- Link-out support: Google Scholar, JSTOR, Web of Science, ScienceDirect, Wiley Online, OVID — AI generates database-specific syntax, opens in new tab
- Query versioning: v0, v1, v2... with dropdown history, each AI refinement creates a new version
- Results table: title, authors, journal, year, citations, database badge, expandable abstracts, checkbox selection
- Import: selected results → download PMC PDF → create paper record (reuses existing dedup) → available in Papers page
- Export: RIS and BibTeX citation formats
- Session persistence: multiple named sessions, sessionStorage for page refresh survival
- 3 new DB tables: `search_sessions`, `search_messages`, `search_results`
- 10 new API endpoints: chat, execute, sessions CRUD, import, export RIS/BibTeX, selection

### Challenge System Improvements (April 6, 2026)

- **Inline PDF upload** in challenge creation form — drag-and-drop or click-to-upload, auto-adds to paper list
- **5 questions per PDF** — dynamic question count (previously fixed 10 total). With 3 PDFs → 15 questions, 10 PDFs → 50 questions
- **100-PDF limit** per challenge (previously 10)
- **Cost estimation** — comprehensive breakdown: generator + participant + judge costs. New `estimate_challenge_cost()` in billing.py, new `GET /api/challenges/estimate-cost` endpoint
- **Credit enforcement** — `debit_credits()` now called before runs (was previously a stub). Insufficient balance returns 402. Failed runs auto-refund via new `refund_credits()` function
- **Pre-run approval** — cost >$5 (50 credits) requires explicit user approval via modal. Always requires approval if user balance ≤$5
- **Unique run ID** — `RG-YYYYMMDD-xxxxxx` format, generated at creation, displayed in challenge viewer
- **Enhanced Obsidian notes** — now includes: run ID, user info, cost breakdown, generator agent state (version, performance, prompt preview), judge agent state, recent autoresearch experiment history table
- **Obsidian linking docs** — admin page now explains how to sync vault via Obsidian Sync, iCloud, Git, or rsync

### Search Sidebar & Project Organization (April 13, 2026)

- **Left sidebar** replaces session dropdown: saved searches listed above project folders, collapsible folder tree, active search highlighting
- **Auto-named searches**: first user message generates a cleaned title (strips preambles like "I'm looking for")
- **Project folders**: create, rename, delete from sidebar; collapsible with search counts; open/close state persists in localStorage
- **Search organization**: context menu ("...") on each search with rename, move to project (clickable project list), remove from project, delete
- **Drag and drop**: drag searches onto project folders to move; drag back to "Searches" section to unassign; visual feedback with dashed blue outline
- **Project sharing**: share modal with email input, member list, role badges; invite unregistered users (registration invite email sent, auto-added on sign-up); transfer ownership; leave/remove members
- **Pending invitations**: new `project_invitations` table; `POST /api/projects/{pid}/share` handles unregistered emails; `POST /api/auth/register` auto-fulfills pending invitations
- **Chat follow-up improvements**: `SEARCH_SYSTEM_PROMPT` updated so `follow_up_questions` are specific clickable choices ("Narrow to adults ≥18 years") not open-ended questions
- New endpoints: `PATCH /api/search/sessions/{id}` (rename/move), modified `POST /api/projects/{pid}/share` (unregistered invitations)
- 1 new DB table: `project_invitations`; 1 new column: `search_sessions.project_id`

### The AI Researcher Lab (April 13-14, 2026)

New primary interface replacing dashboard as homepage — a 3-pane research workspace:

- **`frontend/lab.html`** — 3-pane layout: left sidebar (conversations, other interfaces, context documents, projects) + center chat + right tabbed workspace
- **8 AI agent speed buttons**: Research Chat (default), AI Search Strategist, AI Statistician, Study Appraiser, Hypothesis Generator, Literature Reviewer, The Study Builder, The Protocol Evaluator
- **`backend/lab.py`** — Lab session CRUD, chat orchestrator routing by `agent_type`, document management (`lab_documents` table)
- **`backend/agents/lab_agents.py`** — Agent runner functions for Statistician, Study Appraiser, Hypothesis Generator, Literature Reviewer, Study Builder, Protocol Evaluator
- **`backend/skills.py`** — 10 agent types (expanded CHECK constraint), v1 skill prompts for all new agents, extended `seed_v1_skills()`
- **`backend/exports.py`** — Export format converters (Word, LaTeX, Excel, CSV, Python script, R script)
- **`backend/code_runner.py`** — Sandboxed Python/R code execution with timeout
- **`backend/self_improve.py`** — Program.md templates for all new agent types
- **Contextual right-pane tabs**: each agent type opens relevant tabs (e.g., Search Strategist opens Query Builder + Results)
- **Chat-first file uploads**: files attach to chat messages, user prompted to save to Context section after sending
- **Clipboard paste**: screenshots paste directly into chat input
- **Drag-and-drop**: files to chat input, context documents to project folders
- **Agent-specific exports**: text agents show Word/LaTeX, stats agents show all formats including Python/R scripts

### Cloud Storage & Per-Plan Limits (April 14, 2026)

- **`backend/storage.py`** (NEW) — S3/local file storage abstraction. When `AWS_S3_BUCKET` is set, uploads go to S3; otherwise falls back to local `uploads/` directory
- **Per-plan storage limits**: Free (100MB), Pro (5GB), Enterprise (50GB) — enforced at upload time via `check_storage_limit()`
- **`storage_mb` column** added to `membership_plans` table with migration for existing databases
- **File download endpoint**: `GET /api/lab/documents/{id}/download` serves files from S3 or local with proper content-disposition
- **Storage usage endpoint**: `GET /api/lab/storage` returns used vs. limit for the user's plan
- **Env vars**: `AWS_S3_BUCKET`, `AWS_S3_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`

### OGAI Annotator — Native Integration (April 16, 2026)

Ported [OGAI_Annotator](https://github.com/ProgramDoc/OGAI_Annotator) into The AI Researcher as a Benchmark Lab application at `/annotator`. Reuses the platform's auth, `papers` table, projects, credits, and `call_anthropic` helper — no duplicated infrastructure.

- **`backend/annotator.py`** (NEW, ~800 lines) — `ANNOTATOR_TABLES_SQL` (annotations + annotation_spans), universal/type-specific/modifier field catalogs, classify + prefill prompts, save/load with optimistic concurrency, CSV export with formula-injection protection
- **`frontend/annotator.html`** (NEW, ~3,900 lines) — Ported 3-pane layout (paper list + PDF viewer + form panel) with PDF.js 3.11, per-field confirm/correct/flag UI, text-to-field span linking, localStorage draft + debounced backend save
- **`/annotator` page route + 6 `/api/annotator/*` endpoints** in `main.py` covering paper list, annotation load/save, AI classify, AI prefill, CSV export
- **Credit gating**: classify = 3 credits, prefill = 8 credits; admins bypass; automatic refund on LLM or storage failure
- **New tables**: `annotations` (one per paper+reviewer, optimistic `version` column) and `annotation_spans` (text→field linkages)
- **Sidebar entry in `frontend/lab.html`** under Interfaces + entries added to the duplicated nav bar across 17 other HTML files
- **Tests**: new `tests/test_annotator.py` — save/load round-trip, 409 on stale version, span replacement, CSV export, cross-user isolation, classify-without-auth, prefill validation

### Annotator Refinements & Batch Workflow (April 16, 2026)

- **In-iframe chrome hiding**: annotator's own topbar brand/nav/sign-out/back-link are tagged `tb-chrome` and hidden via `.in-iframe #topbar .tb-chrome { display: none }` so nothing duplicates the Lab's topbar when opened from the Lab
- **Sidebar redesign**: removed the annotator's independent project folder tree (previously duplicated Lab projects). Replaced with a flat paper list + compact "All projects / Unassigned / …" dropdown that reads from the shared `/api/projects`
- **Project scoping via URL**: `?project_id=N` auto-filters to a specific project. The Lab's project context menu now has an "Open in Annotator" item that threads the id through
- **Batch bar in the sidebar**: checkboxes per paper enable a purple bar with "Run batch…", "Move selected to project…", and "✕ Delete selected"
- **Batch modal**: pick steps (classify / extract) and Layer 1 field groups. Later expanded with Layer 2 (collapsible per-study-type subsets with "All"/"None" buttons) and Layer 3 (cross-cutting modifier field checkboxes)
- **`/api/annotator/schema`** endpoint exposes the field catalog so the modal can render without hard-coding field lists in JS
- **Prefill endpoint extensions**: `POST /api/annotator/papers/{id}/prefill` now accepts optional `groups`, `type_fields`, and `modifier_fields` (None = include all, `[]` = exclude)
- **Persistence hardening**: save flow now fires on `beforeunload`, `pagehide`, and `visibilitychange → hidden` via `fetch({keepalive: true})` — edits survive tab close, Lab "Back to Chat", and tab switches. `logout()` awaits a final forced save. Draft cache moved from sessionStorage to localStorage so unsent edits survive tab close in any tab
- **Tab bar + right-pane panes** (Form / ✨ Custom / Results / Analytics). Inactive panes use `display: none` so span-linking DOM lookups (`getElementById('spans-…')`) keep resolving

### Paper Storage Migration to S3 (April 16, 2026)

Paper uploads now route through durable storage so PDFs survive Render redeploys.

- **`backend/paper_files.py`** (NEW) — thin helper over `backend/storage.py` with `write_paper_file()`, `read_paper_bytes()`, `delete_paper_file()`
- **`papers.storage_path`** column added via additive migration in `_migrate_challenge_columns_v2`. `s3://bucket/key` when S3 configured, else `uploads/<uuid>.pdf`
- **Legacy fallback**: rows with NULL storage_path still read from `PAPERS_DIR / disk_filename` so pre-migration PDFs on persistent disks keep working. New error message prompts re-upload for wiped ephemeral files
- **All paper readers migrated**: `/api/papers/{id}/pdf`, `_pdf_to_base64`, the /last-test-papers helper, comparative-rubric loader, `backend/annotator.py:load_paper_pdf`, `backend/challenges.py:_load_papers_b64`, and `backend/self_improve.py`
- **Auto-fetch paths updated**: `backend/pubmed.py:download_pmc_pdf` now also uploads to storage.py and returns a dict including `storage_path`. `backend/scheduler.py` and `backend/search.py` thread it through to `INSERT INTO papers`
- **DELETE `/api/papers/{pid}`** now calls `delete_paper_file()` to clean up the S3 object (and any legacy local copy) before removing the row
- **Tests**: new `test_upload_records_storage_path` and `test_pdf_read_survives_wiped_papers_dir` — the second simulates a Render disk wipe by redirecting `PAPERS_DIR` to an empty tmp dir and confirming `/api/papers/{id}/pdf` still serves from storage

### Enterprise Seat Model (April 16–17, 2026)

Legacy individual plans (Free / Pro / Enterprise) are being replaced by an enterprise-only, seat-based model. Shipped across 5 commits (phases 1a–4b) behind the `ENTERPRISE_MODE` env flag (default `"0"` — inert).

- **`backend/enterprise.py`** (NEW, ~690 lines) — `SEAT_TYPES` catalog, Stripe Checkout + Subscription provisioning, consolidated state API, seat-qty adjust with proration handling, webhook reconciliation
- **Seat catalog** (source of truth in `backend/enterprise.py:SEAT_TYPES`):
  - Admin — $450/mo + 500 credit floor, rank 3 (full org control)
  - Engineer — $250/mo + 300 credit floor, rank 2 (create/run challenges, rubrics, lab, annotator)
  - General — $100/mo + 100 credit floor, rank 1 (annotator + view)
- **Stripe shape**: one `Subscription` per org, three `SubscriptionItem`s (one per seat type) with `quantity` = purchased pool size. Stripe is canonical; `enterprise_subscriptions` mirrors state via webhook. Monthly bundled credits grant on `invoice.paid` via `_grant_monthly_credits`
- **Role migration (phase 1b)**: `org_members.role` vocab moved `{viewer, contributor, admin}` → `{general, engineer, admin}` (viewer→general, contributor→engineer, admin→admin). `organizations.migrate_to_seat_vocab(conn)` runs idempotently on both PG and SQLite. New `is_owner` column backfilled from `organizations.created_by`
- **Access gating**: `main.py:require_active_seat(user, min_seat, org_id)` sits next to `require_user`. When `ENTERPRISE_MODE=0` it's a no-op returning `{bypass:True, pre_flag:True}` so it's safe to sprinkle throughout. When flipped: platform admins bypass, unseated users get `402 {error:'no_active_seat', redirect:'/onboarding'}`, held seat ranked below `min_seat` gets `403 {error:'insufficient_seat', required, held}`. `past_due` subs honored for 7 days past `current_period_end`
- **Endpoint gating audit (phase 3b)**: 141 `require_active_seat(user, min_seat=…)` calls added across `main.py` — `general` for reads / light actions, `engineer` for paper upload / challenge create / rubric generator / lab run / annotator classify+prefill+schema-run / model register / project create / search execute / compete submit, `admin` for model-member and org-member CRUD. Legacy `check_pdf_limit` / `check_storage_limit` calls wrapped in `if not enterprise_mod.ENTERPRISE_MODE:` so they stay active while flag is off, skip when it flips. Compete routes (`/api/compete/*`) are intentionally not seat-gated — models are not users
- **Endpoints** (all in `main.py`, handlers in `backend/enterprise.py`):
  - `POST /api/enterprise` — create org + Stripe Checkout (caller becomes owner+admin seat)
  - `GET /api/enterprise/{org_id}` — consolidated state (org, sub, seats, credits)
  - `PATCH /api/enterprise/{org_id}/seats` — owner-only, adjusts subscription item qty
  - `POST /api/enterprise/{org_id}/members` — admin; 409 `pool_full`
  - `PATCH /api/enterprise/{org_id}/members/{user_id}` — admin; 409 `pool_full`, 403 if owner
  - `DELETE /api/enterprise/{org_id}/members/{user_id}` — admin; 403 if owner
  - `POST /api/enterprise/{org_id}/sync` — reconcile from Stripe after a webhook drop
- **Frontend**: `/onboarding` (`frontend/onboarding.html`, ~450 lines) for unseated users — join with invite code or start an enterprise; `/enterprise/{id}` (`frontend/enterprise.html`, ~480 lines) for owners + admin-seat users — seat pool management, members table, cost card. `frontend/billing.html` plan grid replaced with an enterprise banner that routes to `/enterprise/{id}` or `/onboarding` depending on seat state. `frontend/login.html:postAuthRedirect()` routes to `/onboarding` when `me.needs_onboarding` is true
- **Setup script**: `scripts/setup_enterprise_stripe.py` provisions Products + Prices in Stripe and prints the env vars to paste
- **Legacy cancel script**: `scripts/cancel_legacy_subscriptions.py` — dry-run first; marks every active Pro/Enterprise individual sub as `cancel_at_period_end=True`. Leaves accounts + data intact
- **New env vars**: `STRIPE_PRICE_SEAT_ADMIN`, `STRIPE_PRICE_SEAT_ENGINEER`, `STRIPE_PRICE_SEAT_GENERAL`, `ENTERPRISE_MODE`

### Design System & Paper Theme Rollout (April 17–18, 2026)

Global OKLCH-based design system replacing the per-page ad-hoc styling that had accumulated over the prior 18 HTML files.

- **`frontend/_shared/design.css`** (NEW, ~420 lines) — shared token layer:
  - Color: `--paper`, `--paper-2`, `--paper-3`, `--ink`, `--ink-2`, `--ink-3`, `--ink-4`, `--rule`, `--rule-2`, `--accent`, `--ok`, `--warn`, `--err` (+ soft variants)
  - Type: `--sans` (Geist), `--serif` (Source Serif 4), `--mono` (JetBrains Mono)
  - Space + radius + shadow scales, `--topbar-h`, motion tokens
- **Redesigned `/rubric-generator`** as the design reference — serif headings, mono eyebrows, paper cards with hairline rules
- **Migrations** (one commit per page cluster so each is easy to bisect/revert):
  1. `dashboard`, `login`, `onboarding`
  2. `billing`, `enterprise`, `org`
  3. `challenges`, `leaderboard`, `analytics`, `models`, `admin`, `daily`, `challenge_viewer`
  4. `annotator`, `lab` + remaining shim pages (`library`, `developers`, `reset_password`, `public_tests`, `search`, `annotate`, `rubric_generator_v2`)
- **Chrome polish pass**: topbar markup standardized to `<header class="topbar">` + `.brand-mark` + `.brand-name` + `.tag` + `.topbar-right` across every page. Fixed a broken `<header>` that was never closed on every shim-migrated page (topbar floated above content)
- **Annotator + Lab polish**: batch-4 pass recoloring in-page surfaces (sidebar, PDF toolbar, right pane) from dark-theme-era greys to Paper tokens. Repaints applied:
  - `#sidebar` from navy to `--paper` with ink text for paper-count, paper items, project filter
  - `#upload-zone` text + border (was rgba white on navy → now ink on paper)
  - `.project-filter-select` rebuilt Paper-first, single clean rule, explicit `background-size: 10 px 10 px` so the caret SVG never tiles
  - `#pdf-panel` + `#pdf-toolbar` + `#pdf-scroll` swap #2a2a2a / #1a1a1a for `--paper-2` + hairline rule; toolbar buttons become outlined pill chips; `.page-wrapper` gets a layered shadow (small crisp + large diffused)

### Lab Redesign — Sources / Briefing / Methods + Benchmark Lab (April 18–19, 2026)

Restructure of the Lab sidebar and center pane around the research-team metaphor.

- **`backend/user_tools.py`** (NEW, ~230 lines) — per-user cross-project features that reach the LLM on every chat turn:
  - `user_briefing` table (user_id PK, text, `updated_at`) — free-form 4 000-char block, prepended to every agent's system prompt
  - `user_methods` table (id, user_id, name, when_to_use, instructions, active, timestamps) — structured procedure cards toggleable per session
  - `compose_overlay(conn, user_id)` → `"# User briefing\n{text}\n\n# Active methods\n## {name}\nWhen to use: …\n{instructions}"` or empty string — consumed by both `_chat_search_strategist` and `_chat_lab_agent` via `backend/lab.py:_system_prompt_for`
- **`backend/skills.py` metadata layer**: `SKILL_METADATA` dict with `display_name` / `description` / `when_to_use` for all 10 agent types. `migrate_agent_skills_metadata(conn)` adds columns + backfills idempotently. `list_system_methods(conn)` returns only metadata (never `prompt_text`); `USER_FACING_AGENT_TYPES` excludes benchmark-internal generator + judge
- **Sources (replaces "Context")**: `lab_documents` rows now render with selection checkboxes driving `ACTIVE_SOURCES` Set. Chat send merges uploaded-file ids with active-source ids so only checked files travel as `document_ids`. Previously all uploads were sent or nothing
- **Briefing**: sidebar preview card with mono eyebrow "SHARED WITH EVERY AGENT"; click to open the Briefing modal with 4 000-char counter; saved via `PUT /api/briefing`
- **Methods**: sidebar preview card (same shape as Briefing) with mono eyebrow "APPENDED TO EVERY AGENT". Preview shows up to two active-method chips with "+N more" overflow + "N agent capabilities listed." footer. Clicking opens `openMethodsTab()` — a lazy right-pane tab rendering the full system + user methods UI (add / edit / toggle / delete). "+" in the sidebar header opens the new-method modal directly
- **Seven new endpoints** (all seat-gated, never credit-charged):
  - `GET`, `PUT /api/briefing`
  - `GET`, `POST /api/methods`
  - `PATCH`, `DELETE /api/methods/{id}`
  - `GET /api/methods/system`
- **Sidebar restructure**: "Interfaces" section renamed to **The Benchmark Lab** with inline flask+sparkline SVG logo. Old `Benchmark Lab` sub-group flattened — every interface now a direct child (Admin / Dashboard / Challenges / Leaderboard / Analytics / Models / PDF Viewer / Annotator). Projects moved above Conversations. Library + Developers moved out to the topbar nav (`.topbar-nav`) so they're one click from any state and don't clutter the benchmark-lab list
- **Agent roles with logo + color**: "Research Chat" → **The Research Assistant**; every agent renamed to a "The X" role title (The Search Strategist, The Statistician, The Evidence Appraiser, The Hypothesis Generator, The Literature Reviewer, The Study Builder, The Protocol Evaluator). Each agent carries its own `color` + `icon` in `AGENT_CONFIG`. `switchAgent()` sets `--agent-c` on `#panel-center`; the label, label dot, active speed button, and agent-switch divider pill all pick it up. Switching a role inserts a divider in the current thread — does NOT start a new chat. Initial load + `switchSession()` apply the theme too

### Annotator Polish Sprint (April 18–20, 2026)

Visual and UX gaps surfaced once the Paper theme rolled across the annotator.

- **Draggable divider between PDF and form pane**: 6 px `.pane-resizer` element between `#pdf-panel` and `#form-panel`. `--form-w` CSS variable baselines the post-sidebar area 50/50 (`calc((100vw - 230px - 6px) / 2)`). JS drag handler updates live on mousemove, persists px to localStorage (`annotator_form_w_px`) on mouseup, double-click resets to baseline. Min widths 300 px (PDF) / 280 px (form) so neither side can collapse; window-resize re-clamps
- **Results tab: delete runs + back button**: `DELETE /api/annotator/runs/{rid}` endpoint (engineer seat, 404 if not owned, deletes the run row only — schema + papers preserved). Top of the Results pane now carries a "← Back to annotator" button returning to the Form tab, plus a red "✕ Delete" button (disabled until a run is selected) that confirms before firing. Eyebrow label "Past runs" above the runs dropdown
- **Upload PDF zone visibility**: classic flexbox gotcha — `#project-list` had `flex:1` but inherited the default `min-height: auto`, so it refused to shrink below its content height and pushed `#upload-zone` off the bottom of the sidebar when the paper list was long or `#batch-bar` toggled visible. Set `min-height: 0` + explicit `flex-basis: 0` so it shrinks into available space, keeping Upload PDF docked
- **Project-filter dropdown legibility**: `.project-filter-select` was originally rgba-white text on near-transparent white — unreadable on both the navy and the repainted paper sidebar. Rebuilt as a single Paper-first rule: `var(--paper-2)` background, ink text, `var(--rule)` border, inline SVG caret stroked in ink, explicit `background-size: 10 px 10 px` so the 10×10 SVG can't tile across the row
- **Paper-theme form cards** (visual only — zero behaviour change): each `.form-section` renders as a soft card on cream — white `--paper` fill, 1 px `--rule` border, 10 px radius, faint shadow. `.form-section-header` inside a card fills the card's head region (negative margin + matching radius + bottom rule) so eyebrows read as card titles. Field inputs / textareas / selects adopt paper surfaces, `--rule` borders, and an accent-tinted focus ring. `.rp-tab` bar goes mono small-caps with an accent-colored active underline
- **PDF chrome**: `#pdf-panel` + `#pdf-scroll` use `--paper-2` instead of `#2a2a2a`; `#pdf-toolbar` uses `--paper` with a `--rule` bottom border and pill-style outlined buttons with ink text; `.page-wrapper` gets a layered shadow (small crisp + larger diffused) on a white fill so pages float on the cream. `.textLayer` highlight + selection adopt the Paper accent

### Annotator Custom Extraction & Analytics (April 16, 2026)

Two long-requested features behind a right-pane tab bar in the annotator.

- **✨ Custom tab (schema chat)**: upload a protocol PDF / CSV / pasted description → AI proposes an extraction schema (field id, label, type, options, required, description). Users inline-edit, optionally "refine with an instruction" for a free-text follow-up pass, save per-user, run on any batch selection with pre-flight credit debit (8 credits/paper) and per-paper refund on deletion / permission / extraction failure
- **Results tab**: runs dropdown, sticky-header paper×field table with cell styling for ok / error / skipped, ⬇ CSV export via `GET /api/annotator/runs/{id}.csv`
- **Analytics tab**: four Chart.js cards over the existing annotations table
  1. Field completion rates (20 lowest-populated fields, horizontal bar)
  2. Categorical distributions (picker → doughnut chart; `study_type`, `country_region`, `funding_source`, etc.)
  3. Numeric summaries (log-scale grouped bar: min / median / max across `NUMERIC_FIELDS`)
  4. Reviewer actions (stacked bar: confirmed / corrected / flagged / empty per field)
- **Scope selector**: current paper / current project / batch selection / all my papers — drives the same `/api/annotator/analytics` endpoint with different query params
- **2 new DB tables**: `annotator_custom_schemas` (per-user unique name, fields_json blob) and `annotator_custom_runs` (schema_snapshot_json, paper_ids_json, results_json, credit_cost, credits_refunded, status)
- **New constants in `backend/annotator.py`**: `NUMERIC_FIELDS` (17 fields), `CATEGORICAL_FIELDS` (17 fields), `FIELD_GROUPS` (8 universal groups), plus `validate_custom_fields`, `build_custom_prompt`, `_to_float` coercion
- **9 new endpoints** under `/api/annotator/*`: `schemas/parse`, `schemas/refine`, `schemas` CRUD (`GET`, `POST`, `PATCH`, `DELETE`), `schemas/{id}/run`, `runs`, `runs/{id}`, `runs/{id}.csv`, `analytics`
- **Credit costs**: parse = 2, refine = 1, run = 8/paper
- **Background threading**: runs ≤10 papers finish in-request; larger runs run in a daemon thread and the Results tab polls
- **8 new tests**: validator happy/error paths, `_to_float` coercion, prompt builder, schema CRUD round-trip, cross-user isolation, analytics endpoint with fixture annotations, run ownership checks

### Search Results Improvements (April 13, 2026)

- **Sortable columns**: click Title, Authors, Journal, Year, or Cites headers to sort ascending/descending with arrow indicators; Cites defaults to descending
- **Filter bar**: text input in results toolbar filters across title, authors, journal, and abstract; 300ms debounce; shows "X matching of Y shown" count
- **Import error fix**: validation error arrays now display properly as toast messages instead of `[object Object]`; success uses green/blue toasts

### PostgreSQL Migration (April 13, 2026)

Migrated from SQLite to PostgreSQL for persistent data across Render deploys:
- **`backend/db.py`**: dual-mode database layer — `PgConnection` (PostgreSQL) and `SqliteConnection` (SQLite fallback); auto-converts `?` → `%s` params, strips `RETURNING` for SQLite, converts PG DDL to SQLite at runtime (`SERIAL` → `AUTOINCREMENT`, `CURRENT_TIMESTAMP` → `datetime('now')`)
- **All DDL converted** to PostgreSQL syntax across 15+ files: `SERIAL PRIMARY KEY`, `CURRENT_TIMESTAMP`, `ON CONFLICT DO NOTHING`, `ILIKE`, `RETURNING id`
- **Migration functions** use `column_exists()` helper instead of `PRAGMA table_info()`
- **FK ordering**: `agent_skills` table created before `challenges` (PostgreSQL validates FKs at CREATE TABLE time)
- **`render.yaml`**: provisions free PostgreSQL database, `DATABASE_URL` env var auto-set from database connection string
- **Tests**: continue using SQLite fallback (no DATABASE_URL needed)
- **`IntegrityError`**: imported from `backend.db` (not `sqlite3`) across all backend modules

### Obsidian Integration Architecture

The Obsidian vault is a **write-only local directory** configured via `OBSIDIAN_VAULT_DIR` env var. After each challenge completes, markdown notes are written to `{vault}/challenges/` and agent skill files to `{vault}/SKILL_generator.md` / `SKILL_judge.md`. Users sync the vault externally:
- **Obsidian Sync**: Point env var to the synced vault folder
- **iCloud/Dropbox**: Point to cloud-synced folder
- **Git**: Point to a git repo, auto-commit via cron
- **Self-hosted**: rsync or scp from server

---

## Testing

### Running Tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

### Test Suite: `tests/test_compete_api.py`

16 test cases covering the full Competition API lifecycle:
- Model registration (create, duplicate name rejection)
- Daily opt-in and admin approval flow
- Competition API auth (valid key, invalid key, missing key, unapproved model)
- Question fetching (verify ideal answers stripped)
- Answer submission (valid, without slot, empty responses)
- Results retrieval
- API key regeneration (old key invalidated)
- User API key auth (`X-API-Key` header on platform endpoints)

### Test Suite: `tests/test_annotator.py`

24 test cases covering annotator CRUD, storage, and analytics:
- Papers list includes annotation status
- Save/load round-trip bumps version 1 → 2; stale version returns 409
- Span replacement on save (set A → set B is atomic)
- CSV export contains header + one row per annotated paper
- Cross-user annotation is forbidden (403)
- `FIELD_GROUPS` partition UNIVERSAL_FIELD_IDS; prefill prompt honours groups/type_fields/modifier_fields
- Paper upload records `storage_path`; PDF read survives wiped `PAPERS_DIR`
- Schema endpoint exposes the type/modifier catalog
- Custom-field validator rejects empty lists, bad ids, duplicate ids, select-without-options, bad type
- `_to_float` accepts `"12.3"`, `"1,234"`, `"85%"`, `"1e3"`; rejects `"n/a"`
- `build_custom_prompt` describes each field with type + options
- Schema CRUD round-trip (create, list, duplicate-name 409, patch, get, delete)
- Schema cross-user isolation (list + get both filtered by user_id)
- Analytics endpoint empty scope returns all four keys
- Analytics with 2 annotated papers computes numeric summaries, categorical distributions, and reviewer-action counts
- Run endpoint validates schema ownership + known papers + non-empty `paper_ids`

Tests use FastAPI's TestClient with SQLite fallback (no DATABASE_URL) — no external API calls, no PostgreSQL required.

---

## Architecture Notes

### Why Two Agents?

The two-agent design separates concerns to avoid circularity:
- **Generator Agent** (Claude) writes questions + ideal answers from the paper
- **Competing Models** (GPT-5.4, Gemini, Kimi, custom) answer without seeing ideal answers
- **Judge Agent** (Claude) grades answers against the rubric independently

### Why Prepaid Credits?

Clinical researchers use the platform irregularly. Prepaid credits let occasional users buy what they need and heavy users buy in bulk at a discount.

### Why PostgreSQL? (Migrated from SQLite)

Originally used SQLite with persistent disk on Render. Migrated to PostgreSQL to ensure data persists across Render deploys (free tier doesn't support persistent disks reliably). A compatibility wrapper (`backend/db.py`) allows SQLite fallback for local development and tests — all DDL is written in PostgreSQL syntax and auto-converted to SQLite at runtime when no `DATABASE_URL` is set.

### Autoresearch-Style Self-Improvement

Inspired by karpathy/autoresearch: a single mutable file (the agent prompt) iterated on autonomously. Each iteration: modify ONE thing → run → measure → binary keep/discard. The key insight: many small experiments with clear metrics, not one big rewrite.

### Competition API (External Model Integration)

External models don't give us their API keys. We expose an API they submit answers to — like Kaggle. We publish questions, they fetch + submit, we grade. Cleaner security model.

**How an external model connects (full flow):**

```
1. User registers a model on the platform (POST /api/models)
   → receives model_api_key (rg_model_xxx)

2. User accepts the Model Publishing Agreement (POST /api/models/{id}/accept-agreement)

3. User opts into daily challenges (POST /api/models/{id}/opt-in-daily)

4. Admin approves the model (POST /api/admin/models/{id}/approve-daily)

5. Model's automation script uses the API:
   GET  /api/compete/challenges              — list available challenges
   GET  /api/compete/{id}/questions           — fetch questions (answers stripped)
   POST /api/compete/{id}/submit              — submit responses
   GET  /api/compete/{id}/results             — view grades after grading
```

**Authentication:** All `/api/compete/*` endpoints require `X-Model-Key: rg_model_xxx` header.

**Two key types:**
- `rg_user_*` — personal API key for platform endpoints (X-API-Key header)
- `rg_model_*` — model competition key for `/api/compete/*` only (X-Model-Key header)

**Example script for external model:**
```python
import httpx

API_KEY = "rg_model_xxx"  # from model registration
BASE = "https://therubricgenerator.onrender.com"
HEADERS = {"X-Model-Key": API_KEY}

# 1. List available challenges
challenges = httpx.get(f"{BASE}/api/compete/challenges", headers=HEADERS).json()

for ch in challenges:
    if ch["submission_status"]:  # already submitted
        continue
    cid = ch["id"]

    # 2. Fetch questions
    data = httpx.get(f"{BASE}/api/compete/{cid}/questions", headers=HEADERS).json()

    # 3. Generate answers (your model logic here)
    responses = []
    for q in data["questions"]:
        answer = your_model.answer(q["question"])
        responses.append({"question_id": q["id"], "answer": answer})

    # 4. Submit
    httpx.post(f"{BASE}/api/compete/{cid}/submit", headers=HEADERS,
               json={"responses": responses})

    # 5. Check results (available after admin grades)
    results = httpx.get(f"{BASE}/api/compete/{cid}/results", headers=HEADERS).json()
```

### Obsidian Vault

Write-only from the backend. Vault path: `OBSIDIAN_VAULT_DIR` env var (default: `{DATA_DIR}/obsidian_vault`).

```
obsidian_vault/
├── skills/{agent_type}/
│   ├── SKILL.md        — active prompt (regenerated on challenge completion + startup)
│   ├── history.md      — version table (regenerated)
│   ├── program.md      — human-editable meta-learner guidance (written once, never overwritten)
│   └── experiments/    — autoresearch iteration artifacts
├── challenges/
│   └── {id}_{theme}.md — full challenge record with rubric + model results
├── lab/{agent_type}/
│   └── {sid}_{title}.md — lab conversation archives (written after each chat turn)
└── papers/             — placeholder for future paper notes
```

User syncs to local machine via Obsidian Sync/iCloud/rsync/git.

---

## Key Files Reference

| File | Lines | Purpose |
|------|-------|---------|
| `main.py` | ~6,890 | App entry, routes, auth (cookie + API key), config, all API endpoints, seat gating |
| `backend/annotator.py` | ~1,440 | OGAI Annotator: tables, field catalog, classify/prefill/custom-run prompts, large-PDF pipeline, analytics |
| `backend/challenges.py` | ~1,090 | Challenge orchestration, scoring, points, daily composition, org leaderboard |
| `backend/skills.py` | ~915 | Agent skill versioning, 10 agent type prompts, skill metadata + user-facing types, seed function |
| `backend/self_improve.py` | ~900 | Autoresearch-style experiment loop, program.md templates for 10 agent types |
| `backend/search.py` | ~840 | Literature search: AI chat, PubMed/Europe PMC, import, export, session management |
| `backend/obsidian.py` | ~725 | Markdown vault writer (challenge notes, agent skill files, history) |
| `backend/enterprise.py` | ~690 | Seat catalog, Stripe subscription, per-org seat pool, webhook dispatch, consolidated state |
| `backend/templates.py` | ~680 | Rubric templates, community library, living stats, ground truth |
| `backend/organizations.py` | ~545 | Organization CRUD, membership, role vocab migration (general/engineer/admin), invite/domain-join |
| `backend/lab.py` | ~530 | Lab session CRUD, chat orchestrator (wired through `_system_prompt_for` overlay), document management |
| `backend/membership.py` | ~515 | Legacy membership plans, Stripe subscriptions (PDF/storage limits — deprecated under ENTERPRISE_MODE) |
| `backend/analytics.py` | ~510 | Analytics queries, CSV/PDF export, email notifications, rate limiter |
| `backend/billing.py` | ~490 | Stripe credit system, checkout, webhooks, org billing |
| `backend/models_registry.py` | ~375 | Model CRUD, team management, API key generation, org models |
| `backend/pubmed.py` | ~335 | PubMed/PMC/iCite client, 14 seed themes |
| `backend/scheduler.py` | ~270 | Daily scheduler (7am PST Mon-Fri) |
| `backend/agreements.py` | ~240 | Legal agreement text + acceptance tracking |
| `backend/exports.py` | ~230 | Export converters (Word, LaTeX, Excel, CSV, Python, R) |
| `backend/user_tools.py` | ~230 | Per-user briefing + methods (global overlay on every agent's system prompt) |
| `backend/code_runner.py` | ~225 | Sandboxed Python/R code execution |
| `backend/db.py` | ~220 | Database compatibility layer (PostgreSQL + SQLite fallback) |
| `backend/promo.py` | ~180 | Promo codes, 48h auto-approve |
| `backend/helpers.py` | ~190 | LLM callers (Anthropic, Gemini, OpenAI-compatible), vendor-free error translation |
| `backend/storage.py` | ~155 | S3/local file storage abstraction |
| `backend/paper_files.py` | ~100 | Paper-file read/write/delete helper (S3 via storage.py + legacy disk fallback) |
| `backend/agents/participants.py` | ~142 | Frontier + custom model runner |
| `backend/agents/lab_agents.py` | ~130 | Lab agent runners (6 agent types) |
| `backend/agents/generator.py` | ~83 | Rubric Generator Agent (daily composition support) |
| `backend/agents/judge.py` | ~50 | Judge Agent + shadow regrade |
| `frontend/annotator.html` | ~4,355 | Annotator UI: 3-pane layout with draggable divider, PDF.js viewer, tabbed right pane (Form / Custom / Results / Analytics), batch bar, span linking, Paper-theme card forms |
| `frontend/lab.html` | ~3,605 | Lab UI: sidebar (Projects / Conversations / Sources / Briefing / Methods / The Benchmark Lab) + center chat (role-themed) + right tabbed workspace |
| `frontend/rubric_generator.html` | ~1,430 | Rubric generator / PDF viewer (served at `/pdf-viewer`) — Paper-themed chrome |
| `frontend/search.html` | ~1,370 | Literature search UI (chat, query builder, sortable/filterable results) |
| `frontend/analytics.html` | ~1,175 | Unified analytics page (Benchmark / Annotator / Admin tabs) |
| `frontend/challenges.html` | ~715 | Challenges index + creation |
| `frontend/models.html` | ~715 | Model registry UI |
| `frontend/enterprise.html` | ~480 | Enterprise dashboard: seat pool management, members, cost card |
| `frontend/onboarding.html` | ~450 | Unseated-user landing: join by invite code or start an enterprise |
| `frontend/_shared/design.css` | ~420 | Shared design tokens (Paper theme OKLCH palette + type + space/radius/shadow scales) |
| `tests/conftest.py` | ~120 | Test fixtures (in-memory DB, test users, challenges) |
| `tests/test_compete_api.py` | ~200 | Competition API unit tests (16 cases) |
| `tests/test_annotator.py` | ~350 | Annotator unit tests (24 cases) |

---

## Environment Variables (Complete)

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `ANTHROPIC_API_KEY` | Yes | — | Claude API access |
| `OPENAI_API_KEY` | Yes | — | GPT-5.4 API access |
| `GEMINI_API_KEY` | Yes | — | Gemini API access |
| `ADMIN_SECRET` | Yes | — | Admin login secret code |
| `ADMIN_EMAIL` | No | `tck936@mail.harvard.edu` | Admin user email |
| `DATABASE_URL` | For Render | — | PostgreSQL connection string (omit for SQLite fallback) |
| `RENDER_DATA_DIR` | No | app dir | Persistent data path |
| `OBSIDIAN_VAULT_DIR` | No | `{DATA_DIR}/obsidian_vault` | Obsidian vault path |
| `STRIPE_SECRET_KEY` | For billing | — | Stripe API key |
| `STRIPE_WEBHOOK_SECRET` | For billing | — | Stripe webhook signature |
| `MOONSHOT_API_KEY` | For Kimi | — | Moonshot AI API key |
| `NCBI_API_KEY` | No | — | PubMed rate limit boost |
| `SSO_SECRET` | For SSO | — | Shared secret with Annotator |
| `ANNOTATOR_URL` | No | `https://ogai-annotator.onrender.com` | Annotator URL for SSO redirect |
| `SMTP_HOST` | For email | — | SMTP server |
| `SMTP_PORT` | For email | 587 | SMTP port |
| `SMTP_USER` | For email | — | SMTP username |
| `SMTP_PASS` | For email | — | SMTP password |
| `SMTP_FROM` | No | SMTP_USER | Email sender address |
| `APP_BASE_URL` | For email | `http://localhost:8000` | Base URL for email links |
| `AWS_S3_BUCKET` | For cloud storage | — | S3 bucket name (omit for local fallback) |
| `AWS_S3_REGION` | No | `us-east-1` | S3 bucket region |
| `AWS_ACCESS_KEY_ID` | For cloud storage | — | AWS IAM access key |
| `AWS_SECRET_ACCESS_KEY` | For cloud storage | — | AWS IAM secret key |
| `AWS_S3_PREFIX` | No | `lab-documents/` | S3 key prefix for uploads |
| `DAILY_ENABLED` | No | `true` | Enable daily scheduler |
| `DAILY_MAX_PAPERS` | No | `10` | Papers per daily run |
| `DAILY_TIMEZONE` | No | `America/Los_Angeles` | Scheduler timezone |
| `DAILY_HOUR` | No | `7` | Hour to fire daily (in timezone) |
| `SUBMISSION_WINDOW_HOURS` | No | `24` | Hours external models have to submit |
| `SKILL_EXPERIMENT_BUDGET` | No | `5` | Experiments per improvement cycle |
| `SKILL_IMPROVEMENT_ENABLED` | No | `true` | Enable self-improvement loop |
| `ENTERPRISE_MODE` | No | `0` | When `1`, `require_active_seat()` enforces seat gating and legacy PDF/storage limit checks become no-ops. Leave at `0` until post-cutover |
| `STRIPE_PRICE_SEAT_ADMIN` | For enterprise | — | Stripe Price id for the $450/mo Admin seat (set after running `scripts/setup_enterprise_stripe.py`) |
| `STRIPE_PRICE_SEAT_ENGINEER` | For enterprise | — | Stripe Price id for the $250/mo Engineer seat |
| `STRIPE_PRICE_SEAT_GENERAL` | For enterprise | — | Stripe Price id for the $100/mo General seat |

---

**Last updated:** April 20, 2026
