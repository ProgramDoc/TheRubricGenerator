# The AI Researcher - Development Log

**Repository:** https://github.com/ProgramDoc/TheRubricGenerator
**Platform:** FastAPI + PostgreSQL (SQLite fallback for dev/tests) + Static HTML frontend
**Deployed on:** Render (PostgreSQL database, Python 3.12.3)

---

## Current State (April 13, 2026)

### Codebase Summary

| Component | Files | Lines |
|-----------|-------|-------|
| `main.py` | 1 | ~4,500 |
| `backend/` modules | 19 | ~5,400 |
| `frontend/` pages | 18 | ~7,500 |
| `tests/` | 3 | ~250 |
| **Total** | **41** | **~17,650** |

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
- **Membership plans:** Free (20 PDFs), Pro ($29/mo, 500 PDFs, 1000 credits), Enterprise ($99/mo, unlimited, 5000 credits) via Stripe subscriptions
- **Platform API:** User API keys (`rg_user_xxx`) authenticate all endpoints via `X-API-Key` header. Developers page with key management + full endpoint docs
- **Challenge improvements:** Inline PDF upload, 5 questions per PDF (batched for large sets), cost estimation + credit enforcement, unique run IDs, AI Brain Window with real-time progress events, cancel/delete, per-question speed metrics, paper removal
- **Enhanced Obsidian notes:** Run ID, user info, cost breakdown, generator/judge agent state, experiment history
- **Rebranded:** "The AI Researcher" (no OGAI/UCLA/INOVAi references)
- **Nav restructured:** Benchmark Lab dropdown (Challenges + Leaderboard), Settings gear (Billing + Preferences), Developers tab, user display name
- **Unit tests:** pytest suite for Competition API (16 test cases covering full model lifecycle)
- **Daily scheduler fix:** Removed restrictive OA filter, MeSH-based queries, broad PubMed search → rank by citations → top 10

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

### Competition API (Not "We Call Them")

External models don't give us their API keys. We expose an API they submit answers to — like Kaggle. We publish questions, they fetch + submit, we grade. Cleaner security model.

### Obsidian Vault

Write-only from the backend:
- `SKILL_generator.md` / `SKILL_judge.md` — active prompts + version history
- `challenges/{id}_{theme}.md` — full challenge record
- User syncs to local machine via Obsidian Sync/iCloud/rsync/git

---

## Key Files Reference

| File | Lines | Purpose |
|------|-------|---------|
| `main.py` | ~4,500 | App entry, routes, auth (cookie + API key), config, all API endpoints |
| `backend/challenges.py` | ~780 | Challenge orchestration, scoring, points, daily composition, org leaderboard |
| `backend/self_improve.py` | ~500 | Autoresearch-style experiment loop |
| `backend/templates.py` | ~350 | Rubric templates, community library, living stats, ground truth |
| `backend/analytics.py` | ~350 | Analytics queries, CSV/PDF export, email notifications, rate limiter |
| `backend/db.py` | ~220 | Database compatibility layer (PostgreSQL + SQLite fallback) |
| `backend/search.py` | ~850 | Literature search: AI chat, PubMed/Europe PMC, import, export, session management |
| `backend/organizations.py` | ~300 | Organization CRUD, membership, roles, invite/domain-join |
| `backend/pubmed.py` | ~330 | PubMed/PMC/iCite client, 14 seed themes |
| `backend/scheduler.py` | ~270 | Daily scheduler (7am PST Mon-Fri) |
| `backend/models_registry.py` | ~285 | Model CRUD, team management, API key generation, org models |
| `backend/billing.py` | ~415 | Stripe credit system, checkout, webhooks, org billing |
| `backend/agreements.py` | ~237 | Legal agreement text + acceptance tracking |
| `backend/promo.py` | ~180 | Promo codes, 48h auto-approve |
| `backend/skills.py` | ~178 | Agent skill versioning, seed prompts |
| `backend/helpers.py` | ~163 | LLM callers (Anthropic, Gemini, OpenAI-compatible) |
| `backend/obsidian.py` | ~156 | Markdown vault writer |
| `backend/agents/participants.py` | ~142 | Frontier + custom model runner |
| `backend/agents/generator.py` | ~83 | Rubric Generator Agent (daily composition support) |
| `backend/agents/judge.py` | ~50 | Judge Agent + shadow regrade |
| `backend/membership.py` | ~350 | Membership plans, Stripe subscriptions, PDF limits |
| `tests/conftest.py` | ~120 | Test fixtures (in-memory DB, test users, challenges) |
| `tests/test_compete_api.py` | ~200 | Competition API unit tests (16 cases) |

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
| `DAILY_ENABLED` | No | `true` | Enable daily scheduler |
| `DAILY_MAX_PAPERS` | No | `10` | Papers per daily run |
| `DAILY_TIMEZONE` | No | `America/Los_Angeles` | Scheduler timezone |
| `DAILY_HOUR` | No | `7` | Hour to fire daily (in timezone) |
| `SUBMISSION_WINDOW_HOURS` | No | `24` | Hours external models have to submit |
| `SKILL_EXPERIMENT_BUDGET` | No | `5` | Experiments per improvement cycle |
| `SKILL_IMPROVEMENT_ENABLED` | No | `true` | Enable self-improvement loop |

---

**Last updated:** April 13, 2026
