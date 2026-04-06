# TheRubricGenerator - Development Log

**Repository:** https://github.com/ProgramDoc/TheRubricGenerator
**Platform:** FastAPI + SQLite + Static HTML frontend
**Deployed on:** Render (persistent disk, Python 3.12.3)

---

## Current State (Phase 3 - April 2026)

### Codebase Summary

| Component | Files | Lines |
|-----------|-------|-------|
| `main.py` | 1 | ~2,100 |
| `backend/` modules | 12 | ~2,700 |
| `frontend/` pages | 12 | ~4,400 |
| **Total** | **25** | **~9,200** |

### What's Live

- **Two-agent benchmark system:** Claude Rubric Generator + Claude Judge
- **Five frontier models:** Claude Opus 4.6, GPT-5.4, Gemini 3.1, Gemini 3.1 Pro, Kimi K2 Thinking
- **Daily challenges:** Automated PubMed fetch (14 rotating themes) + daily benchmark run
- **Individual tests:** User-designed, private or public, four difficulty levels
- **Model marketplace:** Register custom OpenAI-compatible models, set pricing, publish for community
- **Billing:** Stripe prepaid credits ($10/$25/$50 packs), per-test deduction
- **Promo codes:** Admin-created, free or break-even, 48h auto-approve then admin gate
- **Legal agreements:** Model Publishing Agreement + Payment Agreement (acceptance tracked)
- **Leaderboard:** Cumulative scores across daily challenges (manual tests excluded)
- **Obsidian vault:** Persistent markdown notes per challenge + agent skills
- **Password reset:** SMTP email flow with token expiry
- **Admin dashboard:** User management + daily scheduler controls

---

## Version History

### v1.0 - Initial Build (April 2, 2026)

Two-LLM evaluation platform. Claude generates rubrics from PDFs, a second LLM (originally GPT-4o) answers, Claude grades.

**Key decision:** Human-editable rubric between generation and evaluation prevents circularity.

**Files:** `main.py` (monolithic), `frontend/rubric_generator.html`, `frontend/login.html`

### v1.0.1 - Security & Robustness Fixes (April 2, 2026)

Code review findings addressed:
- Added missing `/api/auth/admin` endpoint (frontend was calling nonexistent route)
- 50MB file upload size limit
- Batch evaluation capped at 50 papers
- Safe markdown fence stripping (regex instead of crash-prone `split`)
- Server-side validation: password length, email format, field limits
- Database indices on frequently queried columns
- Logging level changed from ERROR to INFO
- Python 3.12.3 pinned for Render compatibility

### v1.1 - Gemini Support (April 5, 2026)

Added Google Gemini as a third evaluation model alongside OpenAI and Claude.
- `_call_gemini()` using Google Generative Language API v1beta
- Gemini natively accepts inline PDF base64 (advantage over OpenAI)
- Frontend model cards for Gemini 2.5 Pro and Gemini 2.0 Flash

### v1.2 - Password Reset & Admin Dashboard (April 5, 2026)

- Forgot password flow via SMTP with 1hr token expiry
- `reset_password.html` for setting new password from email link
- `admin.html` dashboard showing registered users + activity counts
- Admin email default set to `tck936@mail.harvard.edu`
- Admin login redirects to `/admin` instead of `/`

### Phase 1 - Benchmark Platform (April 5, 2026)

Major architecture evolution. Modular `backend/` package mirroring TheReviewer's pattern.

**New:**
- `backend/agents/` — Generator Agent, Judge Agent, Participant runner
- `backend/challenges.py` — Challenge orchestration with background threads
- `backend/skills.py` — Versioned agent prompts (seeded v1)
- `backend/obsidian.py` — Markdown vault writer
- `backend/helpers.py` — Shared LLM callers (extracted from main.py)
- Dashboard, Challenges, Challenge Viewer, Leaderboard pages
- Scoring: generator (difficulty x validity x speed), model (accuracy x speed), judge (consistency x speed)

**Design:** Root `/` now serves `dashboard.html`. Papers/rubric editor at `/papers`.

### Phase 1.5 - User Challenges & Model Registry (April 5, 2026)

- Project folders for organizing challenges
- Private vs Public visibility
- Four difficulty levels (Easy Breezy through Jedi) based on cognitive complexity
- Public tests gallery (`/public-tests`) separate from leaderboard
- Model registry: unique name, version, team linked by email
- Leaderboard isolation: only `kind='daily'` challenges count

### Phase 2 - PubMed Auto-Fetch & Daily Scheduler (April 5, 2026)

- `backend/pubmed.py` — E-utilities search + iCite citation filter + PMC PDF download
- `backend/scheduler.py` — asyncio background task, fires once per UTC day
- System user (`system@rubricgen.local`) owns auto-fetched papers
- 14 seed themes rotating by day-of-year
- Admin panel at `/admin/daily` with manual trigger
- Cost safety: one run per day, `DAILY_ENABLED` kill switch

### Phase 3 - Billing, Marketplace & Updated Models (April 5, 2026)

- Updated frontier models: Claude Opus 4.6, GPT-5.4, Gemini 3.1/Pro, Kimi K2 Thinking
- `call_openai_compatible()` for OpenAI, Kimi, and custom external models
- `backend/billing.py` — Stripe prepaid credits, checkout sessions, webhooks
- `backend/promo.py` — Admin-created promo codes (free/breakeven), 48h auto-approve
- `backend/agreements.py` — Model Publishing + Payment agreements (full legal text)
- Custom model API registration (base URL + encrypted key + per-test pricing)
- `/billing` page with balance, pack purchase, transaction history, promo entry

---

## Planned Development

### Phase 4 - Self-Improvement Loop

The generator and judge agents should iteratively refine their skills based on performance:

1. After every N daily challenges, spawn a meta-Claude call with the performance history
2. Propose a new skill version (modified system prompt)
3. Canary the new version on the next K challenges alongside the current active version
4. If the new version beats the current on avg_performance, promote it
5. Write the updated skill to Obsidian vault for human review

**Status:** Agent skills table exists with versioning + performance tracking. The actual mutation logic is not yet implemented.

### Phase 5 - External Model API Routing

Allow registered external models to participate in daily challenges:

1. Admin approval queue for models opting into daily challenges
2. When daily challenge runs, include admin-approved external models alongside built-in frontier models
3. Route API calls to external model's stored base URL with decrypted API key
4. Handle timeout/failure gracefully (mark model as failed for that challenge, don't block others)
5. External model owners absorb their API costs (enforced by signed Model Publishing Agreement)

**Status:** Schema exists (`active_for_daily`, `daily_admin_approved`). Routing logic in `participants.py` supports `custom_base_url`/`custom_api_key` params. Admin approval UI not built yet.

### Phase 6 - Advanced Analytics & Reporting

- Per-model performance breakdown by theme, difficulty level, question domain
- Historical trend charts (e.g., accuracy over time per model)
- Exportable benchmark reports (PDF/CSV)
- Public API for querying leaderboard data
- Email notifications for daily challenge completions

### Phase 7 - Multi-Tenant Teams & Organizations

- Organization accounts with shared billing
- Team-level access control (viewer, contributor, admin)
- Organization leaderboard (aggregate team model performance)
- SSO integration for institutional users

### Phase 8 - Advanced Rubric Types

- Multi-paper comparative rubrics (compare findings across N studies)
- Living rubric templates that evolve based on accumulated test data
- Community rubric library (share and fork rubric templates)
- Integration with OGAI Annotator for human-annotated ground truth

---

## Architecture Notes

### Why Two Agents?

Using the same model to generate questions, answer them, and grade them creates circularity. The two-agent design separates concerns:

- **Generator Agent** (Claude) writes questions + ideal answers from the paper
- **Competing Models** (GPT-5.4, Gemini, Kimi, custom) answer without seeing ideal answers
- **Judge Agent** (Claude) grades answers against the rubric independently

The human can still edit the rubric before evaluation (Phase 1 legacy flow), but in automated mode the generator's skill evolves based on how well its questions discriminate between models.

### Why Prepaid Credits (Not Subscriptions)?

Clinical researchers use the platform irregularly. Monthly subscriptions would charge idle users unfairly. Prepaid credits let occasional users buy what they need and heavy users buy in bulk at a discount.

### Why SQLite (Not PostgreSQL)?

Simplicity. The platform has a single Render instance with a persistent disk. SQLite with WAL mode handles concurrent reads well, and the write load (one daily challenge + sporadic individual tests) is light. Migration to PostgreSQL is straightforward if needed — all queries use parameterized SQL with no ORM, so the switch requires only a connection adapter change.

### Why Not an ORM?

TheReviewer uses SQLAlchemy + Alembic. TheRubricGenerator uses raw `sqlite3` with parameterized queries. This was a deliberate choice for Phase 1 velocity. An ORM migration (with Alembic for schema management) is appropriate if the schema grows past ~20 tables or if PostgreSQL migration happens.

### Obsidian Vault

The vault at `OBSIDIAN_VAULT_DIR` is write-only from the backend. It creates:
- `SKILL_generator.md` / `SKILL_judge.md` — active prompts + version history
- `challenges/{id}_{theme}.md` — full challenge record (rubric, answers, grades, scores)
- `papers/{id}_{filename}.md` — paper metadata (future)

The user syncs this directory to their local machine via Obsidian Sync, iCloud, rsync, or git to browse it as an Obsidian vault.

---

## Key Files Reference

| File | Lines | Purpose |
|------|-------|---------|
| `main.py` | ~2,100 | App entry point, routes, auth, config, lifespan |
| `backend/challenges.py` | ~525 | Challenge orchestration, scoring formulas, SUPPORTED_MODELS |
| `backend/pubmed.py` | ~320 | PubMed/PMC/iCite client, 14 seed themes |
| `backend/billing.py` | ~264 | Stripe credit system, checkout, webhooks |
| `backend/agreements.py` | ~237 | Legal agreement text + acceptance tracking |
| `backend/scheduler.py` | ~220 | Daily challenge asyncio scheduler |
| `backend/models_registry.py` | ~189 | Custom model CRUD + team management |
| `backend/promo.py` | ~180 | Promo code management + 48h auto-approve |
| `backend/helpers.py` | ~163 | LLM callers (Anthropic, Gemini, OpenAI-compatible) |
| `backend/obsidian.py` | ~156 | Markdown vault writer |
| `backend/skills.py` | ~154 | Agent skill versioning + seed prompts |
| `backend/agents/participants.py` | ~141 | Frontier + custom model runner |
| `backend/agents/generator.py` | ~66 | Rubric Generator Agent |
| `backend/agents/judge.py` | ~50 | Judge Agent + shadow regrade |

---

## Environment Variables (Complete)

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `ANTHROPIC_API_KEY` | Yes | — | Claude API access |
| `OPENAI_API_KEY` | Yes | — | GPT-5.4 API access |
| `GEMINI_API_KEY` | Yes | — | Gemini API access |
| `ADMIN_SECRET` | Yes | — | Admin login secret code |
| `ADMIN_EMAIL` | No | `tck936@mail.harvard.edu` | Admin user email |
| `RENDER_DATA_DIR` | No | app dir | Persistent data path |
| `OBSIDIAN_VAULT_DIR` | No | `{DATA_DIR}/obsidian_vault` | Obsidian vault path |
| `STRIPE_SECRET_KEY` | For billing | — | Stripe API key |
| `STRIPE_WEBHOOK_SECRET` | For billing | — | Stripe webhook signature |
| `MOONSHOT_API_KEY` | For Kimi | — | Moonshot AI API key |
| `MODEL_ENCRYPTION_KEY` | For custom models | — | Fernet encryption key |
| `NCBI_API_KEY` | No | — | PubMed rate limit boost |
| `SMTP_HOST` | For email | — | SMTP server |
| `SMTP_PORT` | For email | 587 | SMTP port |
| `SMTP_USER` | For email | — | SMTP username |
| `SMTP_PASS` | For email | — | SMTP password |
| `SMTP_FROM` | No | SMTP_USER | Email sender address |
| `APP_BASE_URL` | For email | `http://localhost:8000` | Base URL for email links |
| `DAILY_ENABLED` | No | `true` | Enable daily scheduler |
| `DAILY_MAX_PAPERS` | No | `10` | Papers per daily run |

---

**Last updated:** April 5, 2026
