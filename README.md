# TheRubricGenerator

**OGAI Rubric Generator - Clinical Research LLM Benchmarking Platform**

Repository: [github.com/ProgramDoc/TheRubricGenerator](https://github.com/ProgramDoc/TheRubricGenerator)

---

## Overview

TheRubricGenerator is a platform for benchmarking frontier LLMs on clinical research comprehension. It uses a **two-agent architecture** where one Claude agent generates difficult evaluation rubrics from research papers and another Claude agent judges competing models' answers.

The platform supports three modes of operation:

1. **Daily Challenges** - Automated daily benchmarks using papers fetched from PubMed, scored on a public leaderboard
2. **Individual Tests** - User-designed tests with custom papers, difficulty levels, and model selection (private or public)
3. **Manual Evaluation** - Traditional rubric-based evaluation with human-in-the-loop editing

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
│   ├── helpers.py                   # LLM callers (Anthropic, Gemini, OpenAI-compat)
│   ├── challenges.py                # Challenge orchestration + scoring formulas
│   ├── pubmed.py                    # PubMed/PMC/iCite client + 14 seed themes
│   ├── scheduler.py                 # Daily challenge scheduler (asyncio)
│   ├── skills.py                    # Agent skill versioning (generator + judge)
│   ├── obsidian.py                  # Obsidian vault writer (markdown notes)
│   ├── billing.py                   # Stripe credit system
│   ├── promo.py                     # Promo code management
│   ├── agreements.py                # Legal agreements (model publishing + payment)
│   ├── models_registry.py           # Custom model registration + team management
│   └── agents/
│       ├── generator.py             # Rubric Generator Agent (Claude)
│       ├── judge.py                 # Judge Agent (Claude) + shadow regrade
│       └── participants.py          # Frontier model runner (routes to provider APIs)
│
└── frontend/
    ├── dashboard.html               # Main hub (stats, recent challenges, top models)
    ├── challenges.html              # Create + list challenges
    ├── challenge_viewer.html        # Detail view (rubric, answers, grades side-by-side)
    ├── leaderboard.html             # Model rankings with podium
    ├── public_tests.html            # Community-published test gallery
    ├── models.html                  # Model registry (name, version, team, API)
    ├── billing.html                 # Credit balance, purchase packs, transactions
    ├── rubric_generator.html        # Paper upload + manual rubric editing
    ├── daily.html                   # Admin: daily scheduler status + trigger
    ├── admin.html                   # Admin: user management dashboard
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

### Local Development

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
# Visit http://localhost:8001
```

### Render Deployment

1. Connect `ProgramDoc/TheRubricGenerator` repository
2. Build: `pip install -r requirements.txt`
3. Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Persistent disk: `/var/data` (1 GB)
5. Set environment variables (secrets via Render dashboard)

---

## Database (SQLite)

### Core tables
`users`, `sessions`, `projects`, `papers`, `rubrics`, `evaluations`, `password_resets`

### Benchmark tables
`challenges`, `challenge_papers`, `challenge_rubrics`, `model_participants`, `agent_skills`, `leaderboard_cache`, `scheduler_state`

### Marketplace tables
`registered_models`, `registered_model_members`

### Billing tables
`credit_packs`, `user_credits`, `credit_transactions`, `promo_codes`, `user_promo_activations`, `user_agreements`

---

## Related Projects

| Repo | Description |
|------|-------------|
| [OGAI_Annotator](https://ogai-annotator.onrender.com) | Human annotation platform for clinical studies |
| StudyTaxonomy | OGAI taxonomy v2.1 (33 study types) |
| [TheReviewer](https://github.com/ProgramDoc/TheReviewer) | Evidence reviewer interface |
| **TheRubricGenerator** | This repository |

---

**UCLA Health / INOVAi - OGAI Research**
