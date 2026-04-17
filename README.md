# The AI Researcher

**The AI Researcher - Clinical Research LLM Benchmarking Platform**

Repository: [github.com/ProgramDoc/TheRubricGenerator](https://github.com/ProgramDoc/TheRubricGenerator)

---

## Overview

The AI Researcher is a platform for benchmarking frontier LLMs on clinical research comprehension. It uses a **two-agent architecture** where one Claude agent generates difficult evaluation rubrics from research papers and another Claude agent judges competing models' answers. The agents autonomously improve their own prompts using an autoresearch-style experiment loop.

The platform supports five modes of operation:

1. **Daily AI Researcher Challenge** - Automated daily benchmarks (7am PST Mon-Fri) using PubMed papers, mixed difficulty (2/2/4/2 composition), bonus round, 100 pts/correct, dual leaderboard with streak tracking
2. **Individual Tests** - User-designed tests with custom papers, four cognitive difficulty levels, private or public
3. **Competition API** - External models fetch questions and submit answers via API (Kaggle-style)
4. **Manual Evaluation** - Traditional rubric-based evaluation with human-in-the-loop editing
5. **Annotator** - Native OGAI Annotator port at `/annotator` for expert review: classify study design, AI-extract structured fields by study type, link PDF text spans to fields, run custom user-defined extraction schemas in batch, and explore aggregate analytics (field completion rates, categorical distributions, numeric summaries, reviewer-action breakdown)

---

## Architecture

```
TheRubricGenerator/
├── main.py                          # FastAPI app (routes, auth, config, lifespan)
├── requirements.txt
├── render.yaml                      # Render deployment config
├── .python-version                  # Pin Python 3.12.3
│
├── backend/
│   ├── db.py                        # Database layer (PostgreSQL + SQLite fallback)
│   ├── helpers.py                   # LLM callers (Anthropic, Gemini, OpenAI-compat)
│   ├── challenges.py                # Challenge orchestration, scoring, points system
│   ├── self_improve.py              # Autoresearch-style skill experiment loop
│   ├── pubmed.py                    # PubMed/PMC/iCite client + 14 seed themes
│   ├── scheduler.py                 # Daily scheduler (7am PST Mon-Fri)
│   ├── skills.py                    # Agent skill versioning (generator + judge)
│   ├── obsidian.py                  # Obsidian vault writer (markdown notes)
│   ├── billing.py                   # Stripe credit system
│   ├── promo.py                     # Promo code management
│   ├── agreements.py                # Legal agreements (model publishing + payment)
│   ├── models_registry.py           # Model registration, team, API keys
│   ├── search.py                    # Literature search (AI chat, PubMed, sidebar, projects)
│   ├── organizations.py             # Multi-tenant orgs with roles
│   ├── analytics.py                 # Performance breakdown, CSV/PDF export
│   ├── templates.py                 # Rubric templates, community library
│   ├── membership.py                # Free/Pro/Enterprise plans
│   ├── storage.py                   # S3/local file storage abstraction
│   ├── paper_files.py               # Paper-file reader (S3 storage_path + legacy fallback)
│   ├── annotator.py                 # OGAI Annotator: tables, field catalog, prompts, analytics
│   ├── lab.py                       # Lab session CRUD, chat orchestrator, document mgmt
│   └── agents/
│       ├── generator.py             # Rubric Generator Agent (daily composition)
│       ├── judge.py                 # Judge Agent + shadow regrade
│       ├── participants.py          # Frontier model runner (routes to provider APIs)
│       └── lab_agents.py            # Lab agent runners (6 specialised agents)
│
└── frontend/
    ├── lab.html                     # The AI Researcher Lab (3-pane; homepage)
    ├── annotator.html               # OGAI Annotator (3-pane; tabbed right pane)
    ├── dashboard.html               # Benchmark hub (stats, recent challenges, top models)
    ├── challenges.html              # Create + list challenges
    ├── challenge_viewer.html        # Detail view (rubric, answers, grades side-by-side)
    ├── leaderboard.html             # Model rankings with podium
    ├── public_tests.html            # Community-published test gallery
    ├── models.html                  # Model registry (name, version, team, API)
    ├── billing.html                 # Credit balance, purchase packs, transactions
    ├── rubric_generator.html        # Paper upload + manual rubric editing
    ├── search.html                  # Literature search (AI chat + PubMed/Europe PMC)
    ├── library.html                 # Community rubric-template library
    ├── analytics.html               # Benchmark analytics (Chart.js)
    ├── daily.html                   # Admin: scheduler + skill improvement panel
    ├── admin.html                   # Admin: users + model approval queue
    ├── login.html                   # Authentication (login, register, admin, forgot password)
    └── reset_password.html          # Password reset form
```

---

## Frontier Models (April 2026)

| Model | Provider | API Format | Credits/Test |
|-------|----------|------------|-------------|
| Claude Opus 4.6 | Anthropic | Anthropic Messages API | 15 |
| GPT-5.4 | OpenAI | OpenAI Chat Completions | 12 |
| Gemini 3.1 | Google | Gemini Generative Language API | 8 |
| Gemini 3.1 Pro | Google | Gemini Generative Language API | 10 |
| Kimi K2 Thinking | Moonshot AI | OpenAI-compatible | 8 |

Custom models can be registered with any OpenAI-compatible API endpoint.

---

## Two-Agent System

### Rubric Generator Agent (Claude)
Reads uploaded PDFs and generates a structured evaluation rubric with 10 questions. Questions have ideal answers and explicit scoring criteria. The difficulty level (Easy Breezy through Jedi) controls the cognitive complexity:

- **Easy Breezy** - Simple field extraction (PICO, sample size, outcomes)
- **Minor League** - Study classification and design taxonomy
- **Professional** - Methodological appraisal and validity assessment
- **Jedi** - Adversarial expert appraisal with subtle methodology distinctions

### Judge Agent (Claude)
Grades each competing model's answers against the rubric. Uses a shadow regrade (second independent grading pass) to measure its own consistency. The judge flags unverifiable questions to prevent the generator from being rewarded for impossible rubrics.

---

## Points System

| Source | Points per correct answer |
|--------|--------------------------|
| Easy Breezy (individual) | 1 |
| Minor League (individual) | 2 |
| Professional (individual) | 5 |
| Jedi (individual) | 10 |
| **Daily AI Researcher Challenge** | **100** (10x Jedi) |
| Daily bonus (if 10/10 correct) | 20 per bonus question |

**Daily composition:** 2 easy + 2 minor + 4 professional + 2 jedi = 10 questions. Max daily score: 1,040 pts.

**Leaderboard:** Two tabs — Overall (total points from all challenges) and Daily (streak tracking, position movement, expandable drill-down by day).

## Scoring

**Model score** = `accuracy x speed_bonus`

**Generator score** = `difficulty x validity x speed_bonus`
- Difficulty: fraction of models that got questions wrong (harder = better)
- Validity: judge's confidence that rubric answers are verifiable from the paper

**Judge score** = `consistency x speed_bonus`
- Consistency: agreement between primary and shadow regrade

---

## Daily Challenges

The platform automatically fetches fresh clinical research papers from PubMed every day and runs all frontier models against them.

**Pipeline:** PubMed search (E-utilities) -> citation filter (iCite, >=10 citations) -> PMC PDF download -> Rubric generation -> All 5 frontier models answer -> Judge grades -> Leaderboard updated -> Obsidian vault written

**Filters:** Open access, published within 10 years, in PubMed Central, has abstract

**Themes:** 14 seed themes rotate by day-of-year (oncology RCTs, cardiovascular cohort studies, diagnostic accuracy, meta-analyses, etc.)

**Admin controls:** Enable/disable via `DAILY_ENABLED` env var, manual trigger at `/admin/daily`, cost ~$5-10/day.

---

## Billing

Prepaid credit system via Stripe:

| Pack | Credits | Price |
|------|---------|-------|
| Starter | 100 | $10 |
| Researcher | 300 | $25 |
| Lab | 750 | $50 |

Each individual test deducts credits based on the models selected. Daily challenges are free for users (absorbed by the platform). Promo codes (free or break-even) available for technology partners with 48-hour auto-approval.

---

## Competition API (External Models)

External models compete by submitting answers to our API — we don't call their infrastructure.

```
1. Register model → receive API key (rg_model_xxxxx)
2. Opt into daily challenges → pending admin approval
3. Admin approves → model receives daily challenge access
4. Fetch questions:  GET /api/compete/{id}/questions  (X-Model-Key header)
5. Run model locally, generate answers
6. Submit:          POST /api/compete/{id}/submit
7. View results:    GET /api/compete/{id}/results
```

Questions are served **without ideal answers** — external models cannot see the rubric answers.

## Self-Improvement (Autoresearch-Style)

After each daily challenge, the Generator and Judge agents run an autonomous experiment loop inspired by [karpathy/autoresearch](https://github.com/karpathy/autoresearch):

1. Meta-Claude proposes ONE focused modification to the agent's prompt
2. Lightweight evaluation (~$1): mini-rubric quality or judge discrimination test
3. Binary keep/discard: metric improved → keep (advance version). Didn't → discard.
4. Repeat up to 5 experiments per cycle
5. Simplicity criterion: "simpler is better; removing something for equal results is a win"

All experiments logged in `skill_experiments` table (like autoresearch's results.tsv).

## OGAI Annotator

Natively integrated at `/annotator` (no separate deployment). Opens as a fullscreen tab from the Benchmark Lab sidebar.

**Workflow:**
1. Upload a PDF — or reuse any paper already uploaded via the PDF Viewer / challenge creator (all papers share one pool keyed on `sha256`).
2. Click **Classify with AI** to pick a major_category / subcategory / study_type from the OGAI taxonomy.
3. Click **Auto-fill from PDF** to populate the universal + type-specific + cross-cutting fields for the classified study design.
4. Per-field **Confirm / Correct / Flag** with optional PDF span links (select text, click "Link to: field").
5. Mark the paper **Complete / Needs Review / Flagged** and move on.

**Batch workflow:**
- Check multiple papers in the sidebar → purple bar appears with **Run batch…**, **Move selected to project…**, and **✕ Delete selected**.
- Batch modal offers per-study-type field subsets (Layer 2) and cross-cutting modifiers (Layer 3) in addition to the universal Layer 1 groups. Defaults to "all selected".

**Custom extraction (✨ Custom tab):**
- Upload a protocol / CRF / CSV — or paste a description — and an AI proposes a structured extraction schema. Inline-edit fields (label, id, type, options, required, description) or "refine with an instruction" ("add ethnicity", "remove funding") for a second LLM pass. Save per-user, then run on any batch selection.
- Results land in the **Results tab** as a sticky-header paper×field table with CSV export.

**Analytics (Analytics tab):**
- Scope selector: current paper / current project / batch selection / all my papers.
- Four Chart.js views: field completion rates, categorical distributions, numeric min/median/max, confirmed / corrected / flagged / empty breakdown per field.

**Storage:** Papers persist on S3 when `AWS_S3_BUCKET` is set (recommended for Render), else `uploads/` locally. `papers.storage_path` survives redeploys; legacy rows keep their original `disk_filename` fallback.

---

## Model Marketplace

Users can register custom models for public benchmarking:

1. **Register** a model with unique name, version, team members (linked by email), provider, optional git repo and organization
2. **Publish** for individual testing (requires accepting the Model Publishing Agreement)
3. **Set pricing** in credits per test (if actual API cost exceeds price, owner absorbs the difference)
4. **Opt into daily challenges** (requires admin approval)

Models use OpenAI-compatible API endpoints (`/v1/chat/completions`).

---

## Setup

### Environment Variables

**Required:**
```bash
ANTHROPIC_API_KEY=sk-ant-...      # Claude API
OPENAI_API_KEY=sk-...             # GPT-5.4 API
GEMINI_API_KEY=...                # Gemini API
ADMIN_SECRET=...                  # Admin login secret
```

**Billing (required for payments):**
```bash
STRIPE_SECRET_KEY=sk_...          # Stripe secret key
STRIPE_WEBHOOK_SECRET=whsec_...   # Stripe webhook signing secret
```

**Optional:**
```bash
MOONSHOT_API_KEY=...              # Kimi K2 Thinking
NCBI_API_KEY=...                  # PubMed rate limit boost (3/s -> 10/s)
MODEL_ENCRYPTION_KEY=...          # Fernet key for custom model API keys
SMTP_HOST/SMTP_USER/SMTP_PASS    # Password reset emails
APP_BASE_URL=https://...          # For email links
DAILY_ENABLED=true                # Enable/disable daily scheduler
DAILY_MAX_PAPERS=10               # Papers per daily challenge
ADMIN_EMAIL=tck936@mail.harvard.edu
```

**Durable paper storage (recommended for Render):**
```bash
AWS_S3_BUCKET=...                 # S3 bucket name (omit → local uploads/ fallback)
AWS_S3_REGION=us-east-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_S3_PREFIX=lab-documents/      # optional key prefix
```
When unset the app writes to `uploads/` locally, which Render's free tier wipes on redeploy. Setting the S3 vars makes paper PDFs durable.

### Local Development

```bash
pip install -r requirements.txt
# No DATABASE_URL → uses SQLite automatically (rubricgen.db)
uvicorn main:app --reload --port 8001
# Visit http://localhost:8001
```

### Running Tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

40 unit tests: 16 covering the Competition API lifecycle (model registration, approval, question fetching, submission, grading, auth validation, key regeneration, user API keys) and 24 covering the Annotator (save/load concurrency, span replacement, CSV export, field validators, custom-schema CRUD, analytics aggregations, S3 storage_path resilience).

---

## Platform API

All endpoints support authentication via personal API key (alternative to browser session).

### Generate Your Key
1. Visit `/developers` or **Settings → Developers**
2. Click **Generate API Key** → get `rg_user_xxxxx`
3. Add header to all requests: `X-API-Key: rg_user_xxxxx`

### Example
```bash
curl -H "X-API-Key: rg_user_your_key" \
  https://therubricgenerator.onrender.com/api/challenges
```

### Key Endpoints
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/challenges` | Create a challenge |
| `POST` | `/api/challenges/{id}/run` | Run a challenge |
| `POST` | `/api/papers/upload` | Upload a PDF |
| `POST` | `/api/rubrics/generate` | Generate rubric from paper |
| `POST` | `/api/evaluations/run` | Run LLM evaluation |
| `POST` | `/api/search/chat` | AI search strategist |
| `POST` | `/api/search/execute` | Search PubMed/Europe PMC |

Full reference at `/developers` page.

### Render Deployment

1. Connect `ProgramDoc/TheRubricGenerator` repository
2. Build: `pip install -r requirements.txt`
3. Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. PostgreSQL: Free database auto-provisioned via `render.yaml` — set `DATABASE_URL` env var from database connection string
5. Set environment variables (secrets via Render dashboard)

---

## Database (PostgreSQL)

**Production:** PostgreSQL on Render (data persists across deploys)
**Local dev:** SQLite fallback when `DATABASE_URL` is not set
**Compatibility:** `backend/db.py` wrapper auto-converts SQL syntax between PostgreSQL and SQLite

### Core tables
`users`, `sessions`, `projects`, `papers`, `rubrics`, `evaluations`, `password_resets`

### Benchmark tables
`challenges`, `challenge_papers`, `challenge_rubrics`, `model_participants`, `agent_skills`, `leaderboard_cache`, `scheduler_state`

### Marketplace tables
`registered_models`, `registered_model_members`

### Billing tables
`credit_packs`, `user_credits`, `credit_transactions`, `promo_codes`, `user_promo_activations`, `user_agreements`, `membership_plans`, `user_memberships`

### Organization tables
`organizations`, `org_members`, `org_credits`, `org_credit_transactions`, `org_leaderboard_cache`

### Search tables
`search_sessions`, `search_messages`, `search_results`, `project_invitations`

### Template tables
`rubric_templates`, `template_question_stats`, `community_templates`, `community_ratings`, `ground_truth_annotations`

### Analytics tables
`analytics_snapshots`, `notification_preferences`, `challenge_events`

### Annotator tables
`annotations`, `annotation_spans`, `annotator_custom_schemas`, `annotator_custom_runs`

---

## Related Projects

| Repo | Description |
|------|-------------|
| [OGAI_Annotator](https://github.com/ProgramDoc/OGAI_Annotator) | Original standalone annotator — now natively integrated in this repo at `/annotator` |
| StudyTaxonomy | The AI Researcher taxonomy v2.1 (33 study types) |
| [TheReviewer](https://github.com/ProgramDoc/TheReviewer) | Evidence reviewer interface |
| **The AI Researcher** | This repository |

---

**The AI Researcher**
