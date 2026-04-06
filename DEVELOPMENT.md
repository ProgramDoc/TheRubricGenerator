# TheRubricGenerator - Development Log

**Repository:** https://github.com/ProgramDoc/TheRubricGenerator
**Platform:** FastAPI + SQLite + Static HTML frontend
**Deployed on:** Render (persistent disk, Python 3.12.3)

---

## Current State (Phase 5 Complete - April 2026)

### Codebase Summary

| Component | Files | Lines |
|-----------|-------|-------|
| `main.py` | 1 | ~3,040 |
| `backend/` modules | 14 | ~3,535 |
| `frontend/` pages | 13 | ~4,910 |
| **Total** | **28** | **~11,485** |

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
- **SSO:** Cross-app auth to OGAI Annotator via HMAC-signed tokens
- **Obsidian vault:** Persistent markdown notes per challenge + agent skill version history
- **Password reset:** SMTP email flow with 1hr token expiry
- **Admin dashboard:** User management, daily scheduler controls, skill improvement panel, model approval queue
- **Dry-run mode:** Test daily challenges without recording to leaderboard

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
- SSO to OGAI Annotator via HMAC-signed redirect tokens

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

### Phase 6 - Advanced Analytics & Reporting

- Per-model performance breakdown by theme, difficulty level, question domain
- Historical trend charts (accuracy over time per model)
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

The two-agent design separates concerns to avoid circularity:
- **Generator Agent** (Claude) writes questions + ideal answers from the paper
- **Competing Models** (GPT-5.4, Gemini, Kimi, custom) answer without seeing ideal answers
- **Judge Agent** (Claude) grades answers against the rubric independently

### Why Prepaid Credits?

Clinical researchers use the platform irregularly. Prepaid credits let occasional users buy what they need and heavy users buy in bulk at a discount.

### Why SQLite?

Single Render instance with persistent disk. SQLite with WAL mode handles the write load (one daily challenge + sporadic individual tests). Migration to PostgreSQL straightforward — all parameterized SQL.

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
| `main.py` | ~3,040 | App entry, routes, auth, config, lifespan, all API endpoints |
| `backend/challenges.py` | ~710 | Challenge orchestration, scoring, points, daily composition |
| `backend/self_improve.py` | ~500 | Autoresearch-style experiment loop |
| `backend/pubmed.py` | ~330 | PubMed/PMC/iCite client, 14 seed themes |
| `backend/scheduler.py` | ~270 | Daily scheduler (7am PST Mon-Fri) |
| `backend/models_registry.py` | ~270 | Model CRUD, team management, API key generation |
| `backend/billing.py` | ~264 | Stripe credit system, checkout, webhooks |
| `backend/agreements.py` | ~237 | Legal agreement text + acceptance tracking |
| `backend/promo.py` | ~180 | Promo codes, 48h auto-approve |
| `backend/skills.py` | ~178 | Agent skill versioning, seed prompts |
| `backend/helpers.py` | ~163 | LLM callers (Anthropic, Gemini, OpenAI-compatible) |
| `backend/obsidian.py` | ~156 | Markdown vault writer |
| `backend/agents/participants.py` | ~142 | Frontier + custom model runner |
| `backend/agents/generator.py` | ~83 | Rubric Generator Agent (daily composition support) |
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

**Last updated:** April 6, 2026
