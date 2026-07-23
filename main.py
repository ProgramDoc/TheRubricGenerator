"""
The AI Researcher — FastAPI backend
v1.0 — PDF upload, rubric generation (Claude), LLM evaluation (OpenAI), grading (Claude-as-judge)
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, Cookie, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.db import get_db, IntegrityError, is_postgres, column_exists
from backend.helpers import (
    strip_markdown_fences as _strip_markdown_fences,
    call_anthropic as _call_anthropic,
    call_gemini as _call_gemini,
    call_openai as _call_openai,
)
from backend.user_tools import USER_TOOLS_TABLES_SQL
from backend import user_tools as user_tools_mod
from backend.skills import (
    SKILLS_TABLE_SQL, seed_v1_skills, migrate_agent_skills_check,
    migrate_agent_skills_metadata, list_system_methods,
    get_active_skill, list_skill_versions,
)
from backend import challenges as bench
from backend.billing import BILLING_TABLES_SQL, seed_credit_packs
from backend.promo import PROMO_TABLES_SQL
from backend.agreements import AGREEMENTS_TABLE_SQL
from backend.self_improve import EXPERIMENTS_TABLE_SQL
from backend import analytics as analytics_mod
from backend.organizations import ORG_TABLES_SQL
from backend import organizations as org_mod
from backend.enterprise import ENTERPRISE_TABLES_SQL, seed_seat_types
from backend import enterprise as enterprise_mod
from backend.templates import TEMPLATE_TABLES_SQL
from backend import templates as tmpl_mod
from backend.search import SEARCH_TABLES_SQL
from backend import search as search_mod
from backend.lab import LAB_TABLES_SQL
from backend import lab as lab_mod
from backend.membership import MEMBERSHIP_TABLES_SQL, seed_plans
from backend import membership as member_mod
from backend.annotator import ANNOTATOR_TABLES_SQL
from backend import annotator as annotator_mod
from backend import paper_files as paper_files_mod
from backend.quality_appraisal import QUALITY_APPRAISAL_TABLES_SQL
from backend import quality_appraisal as qa_mod
from backend.synthesis.grade_agent import GRADE_TABLES_SQL
from backend.synthesis import grade_agent as grade_mod
from backend.synthesis import grade_assess as grade_assess_mod
from backend.synthesis.pooling import pool_outcome as synthesis_pool_outcome
from backend.synthesis import SYNTHESIS_TABLES_SQL
from backend import synthesis as synthesis_mod
from backend.review import GRADE_REVIEWS_TABLES_SQL
from backend import review as review_mod
from backend.extension import EXTENSION_TABLES_SQL
from backend import extension as extension_mod

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rubricgen")

# ─────────────────────────────────────────────
# Paths & config
# ─────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
DATA_DIR   = Path(os.environ.get("RENDER_DATA_DIR", BASE_DIR))
PAPERS_DIR = DATA_DIR / "papers"
DB_PATH    = DATA_DIR / "rubricgen.db"
FRONTEND   = BASE_DIR / "frontend"

PAPERS_DIR.mkdir(parents=True, exist_ok=True)

# Let backend.db know where the SQLite fallback lives
os.environ.setdefault("SQLITE_DB_PATH", str(DB_PATH))

OBSIDIAN_VAULT_DIR = Path(os.environ.get("OBSIDIAN_VAULT_DIR", str(DATA_DIR / "obsidian_vault")))
OBSIDIAN_VAULT_DIR.mkdir(parents=True, exist_ok=True)
(OBSIDIAN_VAULT_DIR / "challenges").mkdir(exist_ok=True)
(OBSIDIAN_VAULT_DIR / "papers").mkdir(exist_ok=True)
(OBSIDIAN_VAULT_DIR / "lab").mkdir(exist_ok=True)

ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")
ADMIN_EMAIL  = os.environ.get("ADMIN_EMAIL",  "tck936@mail.harvard.edu")
ADMIN_NAME   = os.environ.get("ADMIN_NAME",   "Admin")

SSO_SECRET       = os.environ.get("SSO_SECRET", "")
ANNOTATOR_URL    = os.environ.get("ANNOTATOR_URL", "https://ogai-annotator.onrender.com")

SMTP_HOST    = os.environ.get("SMTP_HOST", "")
SMTP_PORT    = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER    = os.environ.get("SMTP_USER", "")
SMTP_PASS    = os.environ.get("SMTP_PASS", "")
SMTP_FROM    = os.environ.get("SMTP_FROM", SMTP_USER or "noreply@rubricgen.local")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8000")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL   = os.environ.get("ANTHROPIC_MODEL",   "claude-sonnet-4-20250514")
OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY",    "")
OPENAI_MODEL      = os.environ.get("OPENAI_MODEL",      "gpt-4o")
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY",    "")
GEMINI_MODEL      = os.environ.get("GEMINI_MODEL",      "gemini-2.5-pro")

SESSION_COOKIE = "rubricgen_session"
SESSION_DAYS   = 30
PBKDF2_ITERS   = 260_000


# ─────────────────────────────────────────────
# Password hashing
# ─────────────────────────────────────────────
def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_bytes(32)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERS)
    return dk.hex(), salt.hex()


def _verify_password(password: str, stored_hash: str, stored_salt: str) -> bool:
    salt = bytes.fromhex(stored_salt)
    dk   = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERS)
    return hmac.compare_digest(dk.hex(), stored_hash)


# ─────────────────────────────────────────────
# DB (connection factory imported from backend.db)
# ─────────────────────────────────────────────
def init_db() -> None:
    conn = get_db()
    with conn:
        # agent_skills must be created first (challenges table references it)
        conn.executescript(SKILLS_TABLE_SQL)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            SERIAL PRIMARY KEY,
                email         TEXT    NOT NULL UNIQUE,
                display_name  TEXT    NOT NULL,
                password_hash TEXT    NOT NULL,
                password_salt TEXT    NOT NULL,
                role          TEXT    NOT NULL DEFAULT 'reviewer',
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT    PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS projects (
                id         SERIAL PRIMARY KEY,
                name       TEXT    NOT NULL,
                user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS papers (
                id            SERIAL PRIMARY KEY,
                filename      TEXT    NOT NULL,
                disk_filename TEXT,
                sha256        TEXT    NOT NULL,
                user_id       INTEGER REFERENCES users(id) ON DELETE CASCADE,
                project_id    INTEGER REFERENCES projects(id) ON DELETE SET NULL,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(sha256, user_id)
            );

            -- Multi-project membership: a paper can belong to many projects.
            -- papers.project_id is kept as a "primary" pointer for back-compat
            -- (and for legacy single-project queries). The junction is the
            -- source of truth for filtering, sidebar grouping, and the
            -- /library page.
            CREATE TABLE IF NOT EXISTS paper_projects (
                paper_id   INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                added_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (paper_id, project_id)
            );
            CREATE INDEX IF NOT EXISTS idx_paper_projects_proj
                ON paper_projects(project_id);

            CREATE TABLE IF NOT EXISTS rubrics (
                id           SERIAL PRIMARY KEY,
                paper_id     INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                rubric_type  TEXT    NOT NULL DEFAULT 'classification',
                rubric_json  TEXT    NOT NULL DEFAULT '{}',
                instructions TEXT,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS evaluations (
                id          SERIAL PRIMARY KEY,
                rubric_id   INTEGER NOT NULL REFERENCES rubrics(id) ON DELETE CASCADE,
                paper_id    INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                eval_model  TEXT    NOT NULL,
                eval_json   TEXT    DEFAULT '{}',
                graded_json TEXT    DEFAULT '{}',
                total_score REAL    DEFAULT 0,
                max_score   REAL    DEFAULT 0,
                status      TEXT    DEFAULT 'pending',
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS password_resets (
                token      TEXT    PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TEXT    NOT NULL,
                used       INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_papers_user ON papers(user_id);
            CREATE INDEX IF NOT EXISTS idx_rubrics_paper_user ON rubrics(paper_id, user_id);
            CREATE INDEX IF NOT EXISTS idx_evaluations_paper_user ON evaluations(paper_id, user_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

            -- ─── Benchmark platform (Phase 1) ───
            CREATE TABLE IF NOT EXISTS challenges (
                id                  SERIAL PRIMARY KEY,
                title               TEXT    NOT NULL,
                theme               TEXT,
                kind                TEXT    NOT NULL DEFAULT 'manual' CHECK(kind IN ('manual','daily','dry_run')),
                status              TEXT    NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','complete','failed')),
                created_by          INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at          TEXT,
                completed_at        TEXT,
                generator_skill_id  INTEGER REFERENCES agent_skills(id) ON DELETE SET NULL,
                judge_skill_id      INTEGER REFERENCES agent_skills(id) ON DELETE SET NULL,
                generator_score     REAL,
                judge_score         REAL,
                error_message       TEXT
            );

            CREATE TABLE IF NOT EXISTS challenge_papers (
                challenge_id INTEGER NOT NULL REFERENCES challenges(id) ON DELETE CASCADE,
                paper_id     INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                PRIMARY KEY (challenge_id, paper_id)
            );

            CREATE TABLE IF NOT EXISTS challenge_rubrics (
                id                 SERIAL PRIMARY KEY,
                challenge_id       INTEGER NOT NULL UNIQUE REFERENCES challenges(id) ON DELETE CASCADE,
                rubric_json        TEXT    NOT NULL,
                generation_time_ms INTEGER NOT NULL DEFAULT 0,
                generator_skill_id INTEGER REFERENCES agent_skills(id) ON DELETE SET NULL,
                created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS model_participants (
                id              SERIAL PRIMARY KEY,
                challenge_id    INTEGER NOT NULL REFERENCES challenges(id) ON DELETE CASCADE,
                model_id        TEXT    NOT NULL,
                provider        TEXT    NOT NULL,
                answer_json     TEXT    DEFAULT '{}',
                answer_time_ms  INTEGER DEFAULT 0,
                grade_json      TEXT    DEFAULT '{}',
                judge_time_ms   INTEGER DEFAULT 0,
                accuracy        REAL    DEFAULT 0,
                speed_bonus     REAL    DEFAULT 0,
                total_score     REAL    DEFAULT 0,
                status          TEXT    NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','answering','answered','grading','graded','failed')),
                error_message   TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(challenge_id, model_id)
            );

            CREATE TABLE IF NOT EXISTS leaderboard_cache (
                model_id            TEXT PRIMARY KEY,
                provider            TEXT,
                total_challenges    INTEGER DEFAULT 0,
                cumulative_score    REAL    DEFAULT 0,
                avg_accuracy        REAL    DEFAULT 0,
                avg_speed_bonus     REAL    DEFAULT 0,
                last_updated        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_challenges_status ON challenges(status);
            CREATE INDEX IF NOT EXISTS idx_participants_challenge ON model_participants(challenge_id);

            -- ─── Phase 1.5: Model registry ───
            CREATE TABLE IF NOT EXISTS registered_models (
                id           SERIAL PRIMARY KEY,
                name         TEXT    NOT NULL UNIQUE,
                version      TEXT    NOT NULL,
                provider     TEXT,
                git_repo     TEXT,
                organization TEXT,
                created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS registered_model_members (
                registered_model_id INTEGER NOT NULL REFERENCES registered_models(id) ON DELETE CASCADE,
                user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                PRIMARY KEY (registered_model_id, user_id)
            );

            CREATE INDEX IF NOT EXISTS idx_rm_created_by ON registered_models(created_by);
            CREATE INDEX IF NOT EXISTS idx_rmm_user ON registered_model_members(user_id);

            -- Phase 3.5: Project sharing
            CREATE TABLE IF NOT EXISTS project_members (
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role       TEXT    NOT NULL DEFAULT 'member' CHECK(role IN ('admin','member')),
                added_by   INTEGER REFERENCES users(id),
                added_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_pm_user ON project_members(user_id);

            -- ─── Phase 2: Scheduler state ───
            CREATE TABLE IF NOT EXISTS scheduler_state (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Phase 3: billing, promo, agreements
        conn.executescript(BILLING_TABLES_SQL)
        conn.executescript(PROMO_TABLES_SQL)
        conn.executescript(AGREEMENTS_TABLE_SQL)
        conn.executescript(EXPERIMENTS_TABLE_SQL)
        # Phase 5: competition submissions
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS challenge_submissions (
                id                  SERIAL PRIMARY KEY,
                challenge_id        INTEGER NOT NULL REFERENCES challenges(id) ON DELETE CASCADE,
                registered_model_id INTEGER NOT NULL REFERENCES registered_models(id) ON DELETE CASCADE,
                answer_json         TEXT,
                submitted_at        TEXT,
                grade_json          TEXT,
                judge_time_ms       INTEGER,
                accuracy            REAL    DEFAULT 0,
                points              INTEGER DEFAULT 0,
                status              TEXT    DEFAULT 'open' CHECK(status IN ('open','submitted','grading','graded','failed','expired')),
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(challenge_id, registered_model_id)
            );
            CREATE INDEX IF NOT EXISTS idx_cs_challenge ON challenge_submissions(challenge_id);
            CREATE INDEX IF NOT EXISTS idx_cs_model ON challenge_submissions(registered_model_id);
        """)
        # Phase 6: analytics & notifications
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS analytics_snapshots (
                id         SERIAL PRIMARY KEY,
                model_id   TEXT NOT NULL,
                theme      TEXT,
                difficulty TEXT,
                challenges INTEGER DEFAULT 0,
                correct    INTEGER DEFAULT 0,
                total      INTEGER DEFAULT 0,
                accuracy   REAL    DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(model_id, theme, difficulty)
            );
            CREATE INDEX IF NOT EXISTS idx_as_model ON analytics_snapshots(model_id);

            CREATE TABLE IF NOT EXISTS notification_preferences (
                user_id         INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                daily_complete  INTEGER DEFAULT 0,
                weekly_digest   INTEGER DEFAULT 0,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Phase 7: organizations
        conn.executescript(ORG_TABLES_SQL)
        # Phase 7b: enterprise seat catalog + per-org subscriptions.
        # Tables are inert until ENTERPRISE_MODE=1 wires them into gating
        # (follow-up commits). Safe to create on every boot.
        conn.executescript(ENTERPRISE_TABLES_SQL)
        seed_seat_types(conn)
        # Phase 7c: one-shot migration of org_members from the legacy
        # viewer/contributor/admin vocabulary to the seat-based
        # general/engineer/admin vocabulary + add `is_owner` column.
        # Idempotent (guarded on is_owner existence).
        org_mod.migrate_to_seat_vocab(conn)
        # Phase 8: templates, community library, ground truth
        conn.executescript(TEMPLATE_TABLES_SQL)
        # Literature search
        conn.executescript(SEARCH_TABLES_SQL)
        # AI Researcher Lab
        conn.executescript(LAB_TABLES_SQL)
        # Membership plans
        conn.executescript(MEMBERSHIP_TABLES_SQL)
        # OGAI Annotator: per-paper annotation + span tables
        conn.executescript(ANNOTATOR_TABLES_SQL)
        # Quality Appraisal AI: risk-of-bias + reporting-guideline + GRADE per paper
        conn.executescript(QUALITY_APPRAISAL_TABLES_SQL)
        # Idempotent ALTER TABLEs for indirectness + target-PICO columns added
        # in the GRADE-indirectness rollout. Safe on both new and existing DBs.
        qa_mod.migrate_qa_columns(conn)
        # GRADE agent: body-of-evidence certainty runs (consumes the pooling agent)
        conn.executescript(GRADE_TABLES_SQL)
        # Synthesis: systematic review + meta-analysis (mirrors QA's run/results/events)
        conn.executescript(SYNTHESIS_TABLES_SQL)
        synthesis_mod.migrate_synthesis_columns(conn)
        # 3-judge adjudication: human-review queue for grade disagreements
        conn.executescript(GRADE_REVIEWS_TABLES_SQL)
        conn.executescript(EXTENSION_TABLES_SQL)
        extension_mod.migrate_user_columns(conn)
        # Project invitations (for unregistered users)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS project_invitations (
                id          SERIAL PRIMARY KEY,
                project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                email       TEXT    NOT NULL,
                invited_by  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                accepted_at TEXT,
                UNIQUE(project_id, email)
            );
            CREATE INDEX IF NOT EXISTS idx_pi_email ON project_invitations(email);
        """)
        conn.commit()
    _migrate_challenges_columns(conn)
    _migrate_org_columns(conn)
    _migrate_challenge_columns_v2(conn)
    _migrate_search_sessions_columns(conn)
    migrate_agent_skills_check(conn)
    seed_v1_skills(conn)
    # Sources / Briefing / Methods — user-authored tools that layer on top of
    # every Lab chat. Schema is purely additive; behavior lights up when the
    # Phase-1c prompt composer + Phase-2 frontend ship.
    conn.executescript(USER_TOOLS_TABLES_SQL)
    migrate_agent_skills_metadata(conn)
    seed_credit_packs(conn)
    seed_plans(conn)
    _migrate_storage_mb_column(conn)
    _migrate_project_invitations_status(conn)

    # Seed the agent skill vault directories (Anthropic SKILL.md format
    # + Karpathy autoresearch program.md + version history). Idempotent.
    try:
        from backend.skills import (
            get_active_skill, list_skill_versions,
            GENERATOR_SKILL_DESCRIPTION, JUDGE_SKILL_DESCRIPTION,
            RESEARCH_CHAT_SKILL_DESCRIPTION,
            SEARCH_STRATEGIST_SKILL_DESCRIPTION, STATISTICIAN_SKILL_DESCRIPTION,
            STUDY_APPRAISER_SKILL_DESCRIPTION, HYPOTHESIS_GENERATOR_SKILL_DESCRIPTION,
            LITERATURE_REVIEWER_SKILL_DESCRIPTION,
            STUDY_BUILDER_SKILL_DESCRIPTION, PROTOCOL_EVALUATOR_SKILL_DESCRIPTION,
        )
        from backend.obsidian import (
            write_agent_skill_file, write_agent_program_file, write_agent_history_file,
        )
        from backend.self_improve import (
            GENERATOR_PROGRAM_MD, JUDGE_PROGRAM_MD,
            RESEARCH_CHAT_PROGRAM_MD,
            SEARCH_STRATEGIST_PROGRAM_MD, STATISTICIAN_PROGRAM_MD,
            STUDY_APPRAISER_PROGRAM_MD, HYPOTHESIS_GENERATOR_PROGRAM_MD,
            LITERATURE_REVIEWER_PROGRAM_MD,
            STUDY_BUILDER_PROGRAM_MD, PROTOCOL_EVALUATOR_PROGRAM_MD,
        )

        for at, program_seed, desc in (
            ("generator", GENERATOR_PROGRAM_MD, GENERATOR_SKILL_DESCRIPTION),
            ("judge", JUDGE_PROGRAM_MD, JUDGE_SKILL_DESCRIPTION),
            ("research_chat", RESEARCH_CHAT_PROGRAM_MD, RESEARCH_CHAT_SKILL_DESCRIPTION),
            ("search_strategist", SEARCH_STRATEGIST_PROGRAM_MD, SEARCH_STRATEGIST_SKILL_DESCRIPTION),
            ("statistician", STATISTICIAN_PROGRAM_MD, STATISTICIAN_SKILL_DESCRIPTION),
            ("study_appraiser", STUDY_APPRAISER_PROGRAM_MD, STUDY_APPRAISER_SKILL_DESCRIPTION),
            ("hypothesis_generator", HYPOTHESIS_GENERATOR_PROGRAM_MD, HYPOTHESIS_GENERATOR_SKILL_DESCRIPTION),
            ("literature_reviewer", LITERATURE_REVIEWER_PROGRAM_MD, LITERATURE_REVIEWER_SKILL_DESCRIPTION),
            ("study_builder", STUDY_BUILDER_PROGRAM_MD, STUDY_BUILDER_SKILL_DESCRIPTION),
            ("protocol_evaluator", PROTOCOL_EVALUATOR_PROGRAM_MD, PROTOCOL_EVALUATOR_SKILL_DESCRIPTION),
        ):
            try:
                active = get_active_skill(conn, at)
                versions = list_skill_versions(conn, at)
                write_agent_skill_file(OBSIDIAN_VAULT_DIR, at, active, description=desc)
                write_agent_history_file(OBSIDIAN_VAULT_DIR, at, versions)
                write_agent_program_file(OBSIDIAN_VAULT_DIR, at, program_seed)  # idempotent
            except Exception as e:
                logger.error("Vault skill seed failed for %s: %s", at, e)
    except Exception as e:
        logger.error("Vault skill seed block failed: %s", e)

    conn.close()
    _ensure_admin_user()
    _ensure_system_user()


def _migrate_challenges_columns(conn) -> None:
    """Phase 1.5 additive migration: add project_id, visibility, difficulty,
    registered_model_id columns to challenges if missing."""
    with conn:
        if not column_exists(conn, "challenges", "project_id"):
            conn.execute("ALTER TABLE challenges ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL")
        if not column_exists(conn, "challenges", "visibility"):
            conn.execute("ALTER TABLE challenges ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'")
        if not column_exists(conn, "challenges", "difficulty"):
            conn.execute("ALTER TABLE challenges ADD COLUMN difficulty TEXT")
        if not column_exists(conn, "challenges", "registered_model_id"):
            conn.execute("ALTER TABLE challenges ADD COLUMN registered_model_id INTEGER REFERENCES registered_models(id) ON DELETE SET NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_challenges_project ON challenges(project_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_challenges_visibility ON challenges(visibility)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_challenges_owner_vis ON challenges(created_by, visibility)")
        conn.commit()
    # Migrate challenges kind CHECK to allow 'dry_run'
    # PostgreSQL CHECK constraints include 'dry_run' from creation, so this
    # table-recreation is only needed for legacy SQLite databases.
    try:
        conn.execute("INSERT INTO challenges (title, theme, kind, status, created_by) VALUES ('__migrate_test','','dry_run','pending',NULL)")
        conn.execute("DELETE FROM challenges WHERE title='__migrate_test'")
        conn.commit()
    except Exception:
        if not is_postgres():
            # SQLite: CHECK constraint rejected 'dry_run' — recreate table.
            logger.info("Migrating challenges table to support kind='dry_run'...")
            try:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS challenges_new (
                        id                  SERIAL PRIMARY KEY,
                        title               TEXT    NOT NULL,
                        theme               TEXT,
                        kind                TEXT    NOT NULL DEFAULT 'manual' CHECK(kind IN ('manual','daily','dry_run')),
                        status              TEXT    NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','complete','failed')),
                        created_by          INTEGER REFERENCES users(id) ON DELETE SET NULL,
                        created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        started_at          TEXT,
                        completed_at        TEXT,
                        generator_skill_id  INTEGER REFERENCES agent_skills(id) ON DELETE SET NULL,
                        judge_skill_id      INTEGER REFERENCES agent_skills(id) ON DELETE SET NULL,
                        generator_score     REAL,
                        judge_score         REAL,
                        error_message       TEXT,
                        project_id          INTEGER REFERENCES projects(id) ON DELETE SET NULL,
                        visibility          TEXT    NOT NULL DEFAULT 'private',
                        difficulty          TEXT,
                        registered_model_id INTEGER REFERENCES registered_models(id) ON DELETE SET NULL
                    );
                    INSERT INTO challenges_new SELECT id,title,theme,kind,status,created_by,created_at,started_at,completed_at,generator_skill_id,judge_skill_id,generator_score,judge_score,error_message,project_id,visibility,difficulty,registered_model_id FROM challenges;
                    DROP TABLE challenges;
                    ALTER TABLE challenges_new RENAME TO challenges;
                """)
                conn.commit()
            except Exception as e:
                logger.error("challenges table migration failed: %s", e)
        else:
            conn.rollback()

    # Phase 3: add API fields to registered_models
    with conn:
        if not column_exists(conn, "registered_models", "api_base_url"):
            conn.execute("ALTER TABLE registered_models ADD COLUMN api_base_url TEXT")
        if not column_exists(conn, "registered_models", "api_key_encrypted"):
            conn.execute("ALTER TABLE registered_models ADD COLUMN api_key_encrypted TEXT")
        if not column_exists(conn, "registered_models", "price_per_test_credits"):
            conn.execute("ALTER TABLE registered_models ADD COLUMN price_per_test_credits INTEGER DEFAULT 0")
        if not column_exists(conn, "registered_models", "public_for_testing"):
            conn.execute("ALTER TABLE registered_models ADD COLUMN public_for_testing INTEGER DEFAULT 0")
        if not column_exists(conn, "registered_models", "active_for_daily"):
            conn.execute("ALTER TABLE registered_models ADD COLUMN active_for_daily INTEGER DEFAULT 0")
        if not column_exists(conn, "registered_models", "daily_admin_approved"):
            conn.execute("ALTER TABLE registered_models ADD COLUMN daily_admin_approved INTEGER DEFAULT 0")
        if not column_exists(conn, "registered_models", "agreement_signed_at"):
            conn.execute("ALTER TABLE registered_models ADD COLUMN agreement_signed_at TEXT")
        if not column_exists(conn, "registered_models", "model_api_key"):
            conn.execute("ALTER TABLE registered_models ADD COLUMN model_api_key TEXT")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_rm_api_key ON registered_models(model_api_key)")
        conn.commit()
    # Phase 3.5: role on project_members
    if not column_exists(conn, "project_members", "role"):
        with conn:
            conn.execute("ALTER TABLE project_members ADD COLUMN role TEXT NOT NULL DEFAULT 'member'")
            conn.commit()
    # Phase 3.5: can_run on model members, points on participants
    with conn:
        if not column_exists(conn, "registered_model_members", "can_run"):
            conn.execute("ALTER TABLE registered_model_members ADD COLUMN can_run INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    with conn:
        if not column_exists(conn, "model_participants", "points"):
            conn.execute("ALTER TABLE model_participants ADD COLUMN points INTEGER DEFAULT 0")
        conn.commit()
    # Phase 3.5: daily leaderboard columns
    with conn:
        if not column_exists(conn, "leaderboard_cache", "total_points"):
            conn.execute("ALTER TABLE leaderboard_cache ADD COLUMN total_points INTEGER DEFAULT 0")
        if not column_exists(conn, "leaderboard_cache", "daily_points"):
            conn.execute("ALTER TABLE leaderboard_cache ADD COLUMN daily_points INTEGER DEFAULT 0")
        if not column_exists(conn, "leaderboard_cache", "daily_streak"):
            conn.execute("ALTER TABLE leaderboard_cache ADD COLUMN daily_streak INTEGER DEFAULT 0")
        if not column_exists(conn, "leaderboard_cache", "daily_rank_change"):
            conn.execute("ALTER TABLE leaderboard_cache ADD COLUMN daily_rank_change INTEGER DEFAULT 0")
        conn.commit()


def _migrate_org_columns(conn) -> None:
    """Phase 7 additive migration: add org_id to registered_models if missing."""
    with conn:
        if not column_exists(conn, "registered_models", "org_id"):
            conn.execute("ALTER TABLE registered_models ADD COLUMN org_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rm_org ON registered_models(org_id)")
        conn.commit()


def _migrate_challenge_columns_v2(conn) -> None:
    """Add run_id, cost_estimate, cost_approved, org_id to challenges if missing.
    Also adds papers.storage_path for the S3 migration."""
    with conn:
        if not column_exists(conn, "challenges", "run_id"):
            conn.execute("ALTER TABLE challenges ADD COLUMN run_id TEXT")
        if not column_exists(conn, "challenges", "cost_estimate"):
            conn.execute("ALTER TABLE challenges ADD COLUMN cost_estimate INTEGER")
        if not column_exists(conn, "challenges", "cost_approved"):
            conn.execute("ALTER TABLE challenges ADD COLUMN cost_approved INTEGER NOT NULL DEFAULT 0")
        if not column_exists(conn, "challenges", "org_id"):
            conn.execute("ALTER TABLE challenges ADD COLUMN org_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL")
        # Papers: durable storage path (s3:// URI or local uploads/ path). NULL = legacy local disk layout.
        if not column_exists(conn, "papers", "storage_path"):
            conn.execute("ALTER TABLE papers ADD COLUMN storage_path TEXT")
        # Papers: provenance tag — 'upload' | 'lab' | 'search' | 'pubmed' | 'imported'.
        # Drives the "Source" filter on /library and lets the Library show where each PDF came from.
        if not column_exists(conn, "papers", "source"):
            conn.execute("ALTER TABLE papers ADD COLUMN source TEXT NOT NULL DEFAULT 'upload'")
        # Papers: external URL for metadata-only imports (search results without an attached PDF).
        if not column_exists(conn, "papers", "external_url"):
            conn.execute("ALTER TABLE papers ADD COLUMN external_url TEXT")
        # Papers: pdf_status — 'present' | 'metadata_only' | 'fetching' | 'fetch_failed'.
        # Lets the Library + Annotator render rows that don't have a downloaded PDF.
        if not column_exists(conn, "papers", "pdf_status"):
            conn.execute("ALTER TABLE papers ADD COLUMN pdf_status TEXT NOT NULL DEFAULT 'present'")
        # pdf_fetch_runs: per-run mode + credit cost so refunds know how much
        # to give back. Older rows pre-date the firecrawl mode and default to
        # 'fetch' / 2 credits/paper.
        if not column_exists(conn, "pdf_fetch_runs", "mode"):
            conn.execute("ALTER TABLE pdf_fetch_runs ADD COLUMN mode TEXT NOT NULL DEFAULT 'fetch'")
        if not column_exists(conn, "pdf_fetch_runs", "credit_per_paper"):
            conn.execute("ALTER TABLE pdf_fetch_runs ADD COLUMN credit_per_paper INTEGER NOT NULL DEFAULT 2")
        # lab_documents migration cursor — the id of the papers row this lab_document
        # has been backfilled into. Lets the dual-write be idempotent.
        if not column_exists(conn, "lab_documents", "papers_id"):
            conn.execute("ALTER TABLE lab_documents ADD COLUMN papers_id INTEGER")
        # annotator_custom_runs: turn the table into a general "batch" container
        # so classify / prefill / custom runs can all show up in the Results tab.
        if not column_exists(conn, "annotator_custom_runs", "name"):
            conn.execute("ALTER TABLE annotator_custom_runs ADD COLUMN name TEXT")
        if not column_exists(conn, "annotator_custom_runs", "project_id"):
            conn.execute("ALTER TABLE annotator_custom_runs ADD COLUMN project_id INTEGER")
        if not column_exists(conn, "annotator_custom_runs", "did_classify"):
            conn.execute("ALTER TABLE annotator_custom_runs ADD COLUMN did_classify INTEGER NOT NULL DEFAULT 0")
        if not column_exists(conn, "annotator_custom_runs", "did_prefill"):
            conn.execute("ALTER TABLE annotator_custom_runs ADD COLUMN did_prefill INTEGER NOT NULL DEFAULT 0")
        # Challenge events table
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS challenge_events (
                id           SERIAL PRIMARY KEY,
                challenge_id INTEGER NOT NULL REFERENCES challenges(id) ON DELETE CASCADE,
                event_type   TEXT NOT NULL,
                message      TEXT NOT NULL,
                detail_json  TEXT,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_ce_challenge ON challenge_events(challenge_id);
        """)
        # User API keys
        if not column_exists(conn, "users", "api_key"):
            conn.execute("ALTER TABLE users ADD COLUMN api_key TEXT")
            conn.execute("ALTER TABLE users ADD COLUMN api_key_created_at TEXT")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_api_key ON users(api_key)")
        # User avatar
        if not column_exists(conn, "users", "avatar_path"):
            conn.execute("ALTER TABLE users ADD COLUMN avatar_path TEXT")
        # Model version history table
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS model_versions (
                id                  SERIAL PRIMARY KEY,
                registered_model_id INTEGER NOT NULL REFERENCES registered_models(id) ON DELETE CASCADE,
                version             TEXT NOT NULL,
                changelog           TEXT,
                created_by          INTEGER REFERENCES users(id),
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_mv_model ON model_versions(registered_model_id);
        """)
        if not column_exists(conn, "registered_models", "updated_at"):
            conn.execute("ALTER TABLE registered_models ADD COLUMN updated_at TEXT")
        # Backfill paper_projects from the legacy single papers.project_id FK.
        # Idempotent — re-run on every startup, ON CONFLICT DO NOTHING handles dupes.
        conn.execute(
            """INSERT INTO paper_projects (paper_id, project_id)
               SELECT id, project_id FROM papers
                WHERE project_id IS NOT NULL
               ON CONFLICT DO NOTHING"""
        )
        # Backfill lab_documents → papers. We use a synthetic sha256 of
        # 'lab:{user_id}:{lab_doc_id}' since lab_documents never stored a hash.
        # Idempotent: lab_documents.papers_id IS NULL gates the insert; once
        # set, the row is considered migrated.
        try:
            unmigrated = conn.execute(
                """SELECT id, user_id, project_id, filename, file_path
                     FROM lab_documents
                    WHERE papers_id IS NULL"""
            ).fetchall()
            for ld in unmigrated:
                synthetic_sha = f"lab:{ld['user_id']}:{ld['id']}"
                cur = conn.execute(
                    """INSERT INTO papers
                            (filename, sha256, user_id, project_id,
                             storage_path, source)
                       VALUES (?, ?, ?, ?, ?, 'lab')
                       ON CONFLICT DO NOTHING
                       RETURNING id""",
                    (ld["filename"], synthetic_sha, ld["user_id"],
                     ld["project_id"], ld["file_path"]),
                )
                row = cur.fetchone()
                if row:
                    conn.execute(
                        "UPDATE lab_documents SET papers_id=? WHERE id=?",
                        (row["id"], ld["id"]),
                    )
        except Exception as e:
            logger.warning("lab_documents backfill skipped: %s", e)
        conn.commit()


def _migrate_search_sessions_columns(conn) -> None:
    """Add project_id to search_sessions if missing."""
    with conn:
        if not column_exists(conn, "search_sessions", "project_id"):
            conn.execute("ALTER TABLE search_sessions ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ss_project ON search_sessions(project_id)")
        conn.commit()


def _migrate_storage_mb_column(conn) -> None:
    """Add storage_mb to membership_plans if missing."""
    if not column_exists(conn, "membership_plans", "storage_mb"):
        with conn:
            conn.execute("ALTER TABLE membership_plans ADD COLUMN storage_mb INTEGER NOT NULL DEFAULT 100")
            # Update existing plans
            conn.execute("UPDATE membership_plans SET storage_mb = 100 WHERE name = 'Free'")
            conn.execute("UPDATE membership_plans SET storage_mb = 5000 WHERE name = 'Pro'")
            conn.execute("UPDATE membership_plans SET storage_mb = 50000 WHERE name = 'Enterprise'")
            conn.commit()


def _migrate_project_invitations_status(conn) -> None:
    """Add status column to project_invitations for accept/reject flow."""
    if not column_exists(conn, "project_invitations", "status"):
        with conn:
            conn.execute(
                "ALTER TABLE project_invitations ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'"
            )
            # Backfill: mark existing accepted invitations
            conn.execute(
                "UPDATE project_invitations SET status='accepted' WHERE accepted_at IS NOT NULL"
            )
            conn.commit()


def _ensure_admin_user() -> None:
    if not ADMIN_SECRET:
        return
    conn = get_db()
    existing = conn.execute("SELECT id FROM users WHERE email=?", (ADMIN_EMAIL,)).fetchone()
    if not existing:
        ph, ps = _hash_password(ADMIN_SECRET)
        with conn:
            conn.execute(
                "INSERT INTO users (email, display_name, password_hash, password_salt, role) VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING",
                (ADMIN_EMAIL, ADMIN_NAME, ph, ps, "admin"),
            )
            conn.commit()
    conn.close()


SYSTEM_EMAIL = "system@rubricgen.local"
SYSTEM_NAME  = "System (Daily Challenges)"


def _ensure_system_user() -> None:
    """Create the system user used to own auto-fetched papers and daily challenges.
    The password is a random secret — no one logs in as this user; it exists only
    as an owning record for system-created rows."""
    conn = get_db()
    existing = conn.execute("SELECT id FROM users WHERE email=?", (SYSTEM_EMAIL,)).fetchone()
    if not existing:
        random_secret = secrets.token_hex(32)
        ph, ps = _hash_password(random_secret)
        with conn:
            conn.execute(
                "INSERT INTO users (email, display_name, password_hash, password_salt, role) VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING",
                (SYSTEM_EMAIL, SYSTEM_NAME, ph, ps, "system"),
            )
            conn.commit()
    conn.close()


def _get_system_user_id() -> int:
    conn = get_db()
    row = conn.execute("SELECT id FROM users WHERE email=?", (SYSTEM_EMAIL,)).fetchone()
    conn.close()
    if not row:
        raise RuntimeError("System user missing — call _ensure_system_user() at startup")
    return row["id"]


init_db()

# ─────────────────────────────────────────────
# App (with lifespan to start daily scheduler)
# ─────────────────────────────────────────────
from contextlib import asynccontextmanager
from backend import scheduler as sched


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Startup: spawn the daily scheduler background task
    task = None
    try:
        system_id = _get_system_user_id()
        task = asyncio.create_task(
            sched.daily_loop(get_db, PAPERS_DIR, OBSIDIAN_VAULT_DIR, system_id)
        )
        logger.info("Daily scheduler task started")
    except Exception as e:
        logger.error("Failed to start daily scheduler: %s", e)
    yield
    # Shutdown: cancel the scheduler task
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="The AI Researcher", lifespan=_lifespan)


# ─────────────────────────────────────────────
# Session helpers
# ─────────────────────────────────────────────
def _create_session(user_id: int) -> str:
    token   = secrets.token_hex(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
    conn = get_db()
    with conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?,?,?)",
            (token, user_id, expires),
        )
        conn.commit()
    conn.close()
    return token


def _get_user_from_token(token: str | None) -> dict | None:
    if not token:
        return None
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    row  = conn.execute(
        """SELECT u.id, u.email, u.display_name, u.role, u.avatar_path
           FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token=? AND s.expires_at > ?""",
        (token, now),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _get_user_by_api_key(api_key: str) -> dict | None:
    """Look up a user by their personal API key OR Chrome-extension token.

    Accepts either:
    - ``rg_user_*`` — full developer API key (users.api_key)
    - ``rg_ext_*``  — Chrome-extension token (users.extension_token)

    Both grant the same user identity. The extension token is a separate
    column so revoking the extension doesn't break the developer key.
    """
    if not api_key:
        return None
    if api_key.startswith("rg_ext_"):
        conn = get_db()
        try:
            return extension_mod.get_user_by_extension_token(conn, api_key)
        finally:
            conn.close()
    if not api_key.startswith("rg_user_"):
        return None
    conn = get_db()
    row = conn.execute(
        "SELECT id, email, display_name, role FROM users WHERE api_key=?",
        (api_key,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def require_user(rubricgen_session: str | None = Cookie(default=None),
                 x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> dict:
    """Authenticate via session cookie OR API key header."""
    user = _get_user_from_token(rubricgen_session)
    if not user and isinstance(x_api_key, str):
        user = _get_user_by_api_key(x_api_key)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=bool(os.environ.get("RENDER")),
        max_age=SESSION_DAYS * 86400,
        path="/",
    )


def require_admin(rubricgen_session: str | None) -> dict:
    user = _get_user_from_token(rubricgen_session)
    if not user:
        raise HTTPException(401, "Not authenticated")
    if user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    return user


# ─────────────────────────────────────────────
# Enterprise seat gating
# ─────────────────────────────────────────────
def require_active_seat(user: dict, min_seat: str = "general",
                        org_id: int | None = None) -> dict:
    """Require the caller to hold at least `min_seat` access in an active
    enterprise org. Returns {org_id, seat_type, is_owner}.

    Contract:
    - Platform admin (users.role='admin') bypasses all seat checks and gets a
      synthetic {bypass: True} stub. This is Thomas/the operator, not an
      enterprise admin.
    - When ENTERPRISE_MODE=0 the entire check is a no-op that returns a
      bypass stub regardless of seats. This lets the rest of the codebase
      call require_active_seat freely today without changing behavior until
      Phase 5 flips the flag on production.
    - 402 {error:'no_active_seat', redirect:'/onboarding'} when the user has
      no seat in any active/past_due org — the frontend intercepts globally.
    - 403 {error:'insufficient_seat', required, held} when the user holds a
      lower-ranked seat than required.
    - If org_id is supplied, the check is scoped to that org. Otherwise we
      pick the user's highest-ranked seat across all orgs they belong to.

    past_due grace: we accept past_due subscriptions as active for a 7-day
    grace window past current_period_end so transient payment failures don't
    lock users out immediately.
    """
    # Platform admin bypass — the operator runs the site, not an enterprise.
    if user.get("role") == "admin":
        return {"org_id": None, "seat_type": "admin",
                "is_owner": False, "bypass": True}

    # Pre-flag no-op: until ENTERPRISE_MODE flips to "1", the gate returns a
    # non-enforcing stub so existing flows keep working.
    if not enterprise_mod.ENTERPRISE_MODE:
        return {"org_id": None, "seat_type": "admin",
                "is_owner": False, "bypass": True, "pre_flag": True}

    conn = get_db()
    try:
        # Active == 'active' OR ('past_due' AND within 7-day grace). SQLite
        # and Postgres both accept the julianday/NOW arithmetic below via the
        # backend.db compat layer — we use a string comparison on ISO dates
        # that works in both engines.
        q = ("SELECT om.org_id, om.role AS seat_type, om.is_owner, "
             "       es.status, es.current_period_end "
             "  FROM org_members om "
             "  JOIN enterprise_subscriptions es ON es.org_id=om.org_id "
             " WHERE om.user_id=? "
             "   AND es.status IN ('active','past_due')")
        params: list = [user["id"]]
        if org_id:
            q += " AND om.org_id=?"
            params.append(org_id)
        rows = conn.execute(q, params).fetchall()
    finally:
        conn.close()

    # Filter out past_due subs whose grace window has elapsed.
    now = datetime.now(timezone.utc).isoformat()
    grace_cutoff = (datetime.now(timezone.utc) - timedelta(days=-7)).isoformat()  # +7d
    active_rows = []
    for r in rows:
        if r["status"] == "active":
            active_rows.append(r)
            continue
        # past_due: check grace window
        pe = r["current_period_end"] or ""
        if pe and pe > now:
            # Still within the billed period
            active_rows.append(r)
        elif pe and pe < grace_cutoff:
            # Outside the 7-day grace — treat as locked out
            continue
        else:
            # past_due but period_end not yet set, or within 7-day grace
            active_rows.append(r)

    if not active_rows:
        raise HTTPException(
            402,
            {"error": "no_active_seat",
             "redirect": "/onboarding",
             "message": "You need an active enterprise seat to use this feature."},
        )

    # Pick the highest-ranked seat the user holds.
    best = max(active_rows,
               key=lambda r: enterprise_mod.SEAT_RANK.get(r["seat_type"], 0))
    held_rank = enterprise_mod.SEAT_RANK.get(best["seat_type"], 0)
    required_rank = enterprise_mod.SEAT_RANK.get(min_seat, 99)
    if held_rank < required_rank:
        raise HTTPException(
            403,
            {"error": "insufficient_seat",
             "required": min_seat,
             "held": best["seat_type"],
             "message": f"This action requires a {min_seat} seat; you currently hold {best['seat_type']}."},
        )
    return {"org_id": best["org_id"],
            "seat_type": best["seat_type"],
            "is_owner": bool(best["is_owner"])}


def _user_active_seats(user_id: int) -> list[dict]:
    """Return a list of the user's active seats across all their orgs.
    Used to populate the `seat` field on /api/auth/me. Returns [] if the
    user has no active seats."""
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT om.org_id, om.role AS seat_type, om.is_owner,
                      o.slug AS org_slug, o.name AS org_name,
                      es.status
                 FROM org_members om
                 JOIN organizations o ON o.id = om.org_id
            LEFT JOIN enterprise_subscriptions es ON es.org_id = om.org_id
                WHERE om.user_id=?
                ORDER BY om.is_owner DESC, om.role DESC""",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"org_id": r["org_id"], "org_slug": r["org_slug"],
         "org_name": r["org_name"], "seat_type": r["seat_type"],
         "is_owner": bool(r["is_owner"]),
         "subscription_status": r["status"] or "none"}
        for r in rows
    ]


def _send_email(to: str, subject: str, body: str) -> None:
    """Send an email via SMTP. Raises HTTPException(500) on failure."""
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        logger.error("SMTP not configured; cannot send email to %s", to)
        raise HTTPException(500, "Email delivery is not configured on the server")
    msg = EmailMessage()
    msg["From"]    = SMTP_FROM
    msg["To"]      = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        logger.info("Sent email to %s (subject: %s)", to, subject)
    except Exception as e:
        logger.error("SMTP send failed: %s", e)
        raise HTTPException(500, "Failed to send email")


# ─────────────────────────────────────────────
# Pydantic models
# ─────────────────────────────────────────────
class RegisterPayload(BaseModel):
    email: str
    password: str
    display_name: str

class LoginPayload(BaseModel):
    email: str
    password: str

class ProjectRename(BaseModel):
    name: str

class PaperAssign(BaseModel):
    project_id: Optional[int] = None

class GenerateRubricRequest(BaseModel):
    paper_id: int
    rubric_type: str = "classification"
    instructions: Optional[str] = None

class SaveRubricRequest(BaseModel):
    paper_id: int
    rubric_type: str
    rubric_json: dict
    instructions: Optional[str] = None

class RunEvaluationRequest(BaseModel):
    rubric_id: int
    paper_id: int
    eval_model: str = "gpt-4o"

class GradeEvaluationRequest(BaseModel):
    evaluation_id: int


# ─────────────────────────────────────────────
# Pages
# ─────────────────────────────────────────────
@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def root(rubricgen_session: str | None = Cookie(default=None)):
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "lab.html"), media_type="text/html")


@app.get("/dashboard", include_in_schema=False)
def dashboard_page(rubricgen_session: str | None = Cookie(default=None)):
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "dashboard.html"), media_type="text/html")


@app.get("/pdf-viewer", include_in_schema=False)
def pdf_viewer_page(rubricgen_session: str | None = Cookie(default=None)):
    """Unified PDF Viewer: read PDFs, create quick test questions, run evaluations.
    This is the consolidated Papers + PDF Viewer page."""
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "rubric_generator.html"), media_type="text/html")


@app.get("/papers", include_in_schema=False)
def papers_page_redirect(rubricgen_session: str | None = Cookie(default=None)):
    """Legacy /papers route — redirects to the consolidated PDF Viewer."""
    return RedirectResponse("/pdf-viewer", status_code=302)


@app.get("/annotator", include_in_schema=False)
def annotator_page(rubricgen_session: str | None = Cookie(default=None)):
    """OGAI Annotator — classify study design and extract structured fields from PDFs."""
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "annotator.html"), media_type="text/html")


@app.get("/quality-appraisal", include_in_schema=False)
def quality_appraisal_page(rubricgen_session: str | None = Cookie(default=None)):
    """Quality Appraisal AI — risk-of-bias + reporting-guideline + GRADE per paper."""
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "quality-appraisal.html"), media_type="text/html")


@app.get("/grade", include_in_schema=False)
def grade_page(rubricgen_session: str | None = Cookie(default=None)):
    """GRADE agent — body-of-evidence certainty (consumes the pooling agent)."""
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "grade.html"), media_type="text/html")


@app.get("/synthesis", include_in_schema=False)
def synthesis_page(rubricgen_session: str | None = Cookie(default=None)):
    """Synthesis — systematic review + meta-analysis."""
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "synthesis.html"), media_type="text/html")

@app.get("/review", include_in_schema=False)
def review_page(rubricgen_session: str | None = Cookie(default=None)):
    """Admin inbox for human adjudication of 3-judge grade splits."""
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if user.get("role") != "admin":
        return RedirectResponse("/", status_code=302)
    return FileResponse(str(FRONTEND / "review.html"), media_type="text/html")


@app.get("/challenges", include_in_schema=False)
def challenges_page(rubricgen_session: str | None = Cookie(default=None)):
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "challenges.html"), media_type="text/html")


@app.get("/challenges/{challenge_id:int}", include_in_schema=False)
def challenge_viewer_page(challenge_id: int, rubricgen_session: str | None = Cookie(default=None)):
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "challenge_viewer.html"), media_type="text/html")


@app.get("/leaderboard", include_in_schema=False)
def leaderboard_page(rubricgen_session: str | None = Cookie(default=None)):
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "leaderboard.html"), media_type="text/html")


@app.get("/analytics", include_in_schema=False)
def analytics_page(rubricgen_session: str | None = Cookie(default=None)):
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "analytics.html"), media_type="text/html")


@app.get("/models", include_in_schema=False)
def models_page(rubricgen_session: str | None = Cookie(default=None)):
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "models.html"), media_type="text/html")


@app.get("/public-tests", include_in_schema=False)
def public_tests_page(rubricgen_session: str | None = Cookie(default=None)):
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "public_tests.html"), media_type="text/html")


@app.get("/billing", include_in_schema=False)
def billing_page(rubricgen_session: str | None = Cookie(default=None)):
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "billing.html"), media_type="text/html")


@app.get("/enterprise", include_in_schema=False)
@app.get("/enterprise/{org_id}", include_in_schema=False)
def enterprise_page(org_id: int | None = None,
                    rubricgen_session: str | None = Cookie(default=None)):
    """Enterprise management page for owner + admin-seat holders.
    `/enterprise` defaults to the user's primary enterprise; `/enterprise/{id}`
    scopes to a specific one (the frontend renders the same HTML either way
    and reads the ID from the URL)."""
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "enterprise.html"), media_type="text/html")


@app.get("/onboarding", include_in_schema=False)
def onboarding_page(rubricgen_session: str | None = Cookie(default=None)):
    """Shown to any authenticated user without an active enterprise seat.
    Two CTAs: start an enterprise, or join one with an invite code."""
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "onboarding.html"), media_type="text/html")


@app.get("/rubric-generator", include_in_schema=False)
def rubric_generator_v2_page(rubricgen_session: str | None = Cookie(default=None)):
    """Redesigned 3-column rubric builder, lifted from Claude Design
    (`Rubric Generator Redesign.html`). Uses the shared token stylesheet at
    `/static/_shared/design.css`. The old PDF viewer at `/pdf-viewer` stays
    live; this is the new canonical experience."""
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "rubric_generator_v2.html"), media_type="text/html")


@app.get("/admin/daily", include_in_schema=False)
def admin_daily_page(rubricgen_session: str | None = Cookie(default=None)):
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if user.get("role") != "admin":
        return RedirectResponse("/", status_code=302)
    return FileResponse(str(FRONTEND / "daily.html"), media_type="text/html")


@app.get("/org/{org_id}", include_in_schema=False)
def org_page(org_id: int, rubricgen_session: str | None = Cookie(default=None)):
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "org.html"), media_type="text/html")


@app.get("/library", include_in_schema=False)
def library_page(rubricgen_session: str | None = Cookie(default=None)):
    """Personal PDF library — all the user's papers across projects + sources."""
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "library.html"), media_type="text/html")


@app.get("/community-library", include_in_schema=False)
def community_library_page(rubricgen_session: str | None = Cookie(default=None)):
    """Public community library — shared rubrics + challenges. Used to live at /library."""
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "community-library.html"), media_type="text/html")


@app.get("/search", include_in_schema=False)
def search_page(rubricgen_session: str | None = Cookie(default=None)):
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "search.html"), media_type="text/html")


@app.get("/developers", include_in_schema=False)
def developers_page(rubricgen_session: str | None = Cookie(default=None)):
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "developers.html"), media_type="text/html")


@app.get("/annotate/{cid}", include_in_schema=False)
def annotate_page(cid: int, rubricgen_session: str | None = Cookie(default=None)):
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "annotate.html"), media_type="text/html")


@app.get("/login", include_in_schema=False)
def login_page(rubricgen_session: str | None = Cookie(default=None)):
    user = _get_user_from_token(rubricgen_session)
    if user:
        return RedirectResponse("/", status_code=302)
    return FileResponse(str(FRONTEND / "login.html"), media_type="text/html")


@app.get("/reset-password", include_in_schema=False)
def reset_password_page():
    return FileResponse(str(FRONTEND / "reset_password.html"), media_type="text/html")


@app.get("/admin", include_in_schema=False)
def admin_page(rubricgen_session: str | None = Cookie(default=None)):
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if user.get("role") != "admin":
        return RedirectResponse("/", status_code=302)
    return FileResponse(str(FRONTEND / "admin.html"), media_type="text/html")


app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="frontend")


# ─────────────────────────────────────────────
# Auth routes
# ─────────────────────────────────────────────
@app.post("/api/auth/register", status_code=201)
def register(body: RegisterPayload):
    email    = body.email.strip().lower()
    name     = body.display_name.strip()
    password = body.password
    if not email or not password or not name:
        raise HTTPException(400, "All fields required")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        raise HTTPException(400, "Invalid email address")
    if len(name) > 100:
        raise HTTPException(400, "Display name must be 100 characters or fewer")
    ph, ps = _hash_password(password)
    conn = get_db()
    try:
        with conn:
            conn.execute(
                "INSERT INTO users (email, display_name, password_hash, password_salt) VALUES (?,?,?,?)",
                (email, name, ph, ps),
            )
            conn.commit()
    except IntegrityError:
        raise HTTPException(409, "Email already registered")
    # Phase 7: auto-join orgs by email domain
    try:
        user_row = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if user_row:
            org_mod.join_by_domain(conn, user_row["id"])
    except Exception as e:
        logger.error("Domain auto-join failed for %s: %s", email, e)
    # Fulfill pending project invitations
    try:
        if user_row:
            invitations = conn.execute(
                "SELECT id, project_id, invited_by FROM project_invitations WHERE email=? AND status='pending'",
                (email,),
            ).fetchall()
            for inv in invitations:
                with conn:
                    conn.execute(
                        "INSERT INTO project_members (project_id, user_id, role, added_by) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                        (inv["project_id"], user_row["id"], "member", inv["invited_by"]),
                    )
                    conn.execute(
                        "UPDATE project_invitations SET accepted_at=CURRENT_TIMESTAMP, status='accepted' WHERE id=?",
                        (inv["id"],),
                    )
                    conn.commit()
    except Exception as e:
        logger.error("Invitation fulfillment failed for %s: %s", email, e)
    finally:
        conn.close()
    return {"ok": True}


@app.post("/api/auth/login")
def login(body: LoginPayload, response: Response):
    email = body.email.strip().lower()
    conn  = get_db()
    row   = conn.execute(
        "SELECT id, password_hash, password_salt FROM users WHERE email=?", (email,)
    ).fetchone()
    conn.close()
    if not row or not _verify_password(body.password, row["password_hash"], row["password_salt"]):
        raise HTTPException(401, "Invalid credentials")
    token = _create_session(row["id"])
    _set_session_cookie(response, token)
    return {"ok": True}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/auth/me")
def me(rubricgen_session: str | None = Cookie(default=None)):
    user = _get_user_from_token(rubricgen_session)
    if not user:
        raise HTTPException(401, "Not authenticated")
    # Avatar URL
    if user.get("avatar_path"):
        user["avatar_url"] = f"/api/auth/avatar/{user['id']}"
    else:
        user["avatar_url"] = None
    user.pop("avatar_path", None)

    # Enterprise seat info. Frontend uses these to decide whether to redirect
    # to /onboarding and to gate nav entries (e.g. show /enterprise link).
    # Platform admin (role=='admin') is never considered to need onboarding.
    seats = _user_active_seats(user["id"])
    user["seats"] = seats
    if seats:
        # Pick the primary seat — owner if available, otherwise highest rank.
        primary = next((s for s in seats if s["is_owner"]), None) or max(
            seats, key=lambda s: enterprise_mod.SEAT_RANK.get(s["seat_type"], 0)
        )
        user["seat"] = {
            "org_id":    primary["org_id"],
            "org_slug":  primary["org_slug"],
            "org_name":  primary["org_name"],
            "seat_type": primary["seat_type"],
            "is_owner":  primary["is_owner"],
            "subscription_status": primary["subscription_status"],
        }
    else:
        user["seat"] = None

    # Onboarding is only required when the flag is on, the user isn't a
    # platform admin, and they hold no seat.
    user["needs_onboarding"] = (
        enterprise_mod.ENTERPRISE_MODE
        and user.get("role") != "admin"
        and user["seat"] is None
    )
    user["enterprise_mode"] = enterprise_mod.ENTERPRISE_MODE
    return user


@app.post("/api/auth/avatar")
async def upload_avatar(file: UploadFile = File(...),
                        rubricgen_session: str | None = Cookie(default=None)):
    """Upload a profile photo. Stores via S3 (or local fallback)."""
    user = require_user(rubricgen_session)
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "File must be an image")
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(400, "Image must be under 5 MB")
    from backend.storage import upload_file as storage_upload
    disk_name = f"avatars/user_{user['id']}_{file.filename}"
    storage_upload(disk_name, content)
    conn = get_db()
    try:
        with conn:
            conn.execute("UPDATE users SET avatar_path=? WHERE id=?", (disk_name, user["id"]))
            conn.commit()
    finally:
        conn.close()
    return {"ok": True, "avatar_url": f"/api/auth/avatar/{user['id']}"}


@app.get("/api/auth/avatar/{user_id}")
def serve_avatar(user_id: int):
    """Serve a user's avatar image."""
    conn = get_db()
    row = conn.execute("SELECT avatar_path FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    if not row or not row["avatar_path"]:
        raise HTTPException(404, "No avatar")
    from backend.storage import download_file as storage_download, get_content_type
    data = storage_download(row["avatar_path"])
    if not data:
        raise HTTPException(404, "Avatar file not found")
    ct = get_content_type(row["avatar_path"])
    return Response(content=data, media_type=ct,
                    headers={"Cache-Control": "public, max-age=3600"})


class AdminLoginPayload(BaseModel):
    secret: str


@app.post("/api/auth/admin")
def admin_login(body: AdminLoginPayload, response: Response):
    if not ADMIN_SECRET:
        raise HTTPException(403, "Admin login is not configured")
    if not hmac.compare_digest(body.secret, ADMIN_SECRET):
        raise HTTPException(401, "Invalid admin secret")
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM users WHERE email=?", (ADMIN_EMAIL,)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(500, "Admin user not found. Check server configuration.")
    token = _create_session(row["id"])
    _set_session_cookie(response, token)
    return {"ok": True}


# ─────────────────────────────────────────────
# SSO: generate token for cross-app auth
# ─────────────────────────────────────────────
import time as _time


def _generate_sso_token(user: dict) -> str:
    """Create an HMAC-signed, time-limited SSO token containing user info.
    Format: base64(json({email, display_name, role, ts})) + '.' + hmac_signature
    Valid for 60 seconds."""
    if not SSO_SECRET:
        raise HTTPException(500, "SSO is not configured (SSO_SECRET missing)")
    payload = json.dumps({
        "email": user["email"],
        "display_name": user["display_name"],
        "role": user.get("role", "reviewer"),
        "ts": int(_time.time()),
    })
    payload_b64 = base64.urlsafe_b64encode(payload.encode()).decode()
    sig = hmac.new(SSO_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


@app.get("/api/sso/annotator")
def sso_to_annotator(rubricgen_session: str | None = Cookie(default=None)):
    """Legacy SSO endpoint — the external Annotator is deprecated.
    The PDF Viewer is now the consolidated local page."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    return RedirectResponse("/pdf-viewer", status_code=302)


class ForgotPasswordPayload(BaseModel):
    email: str


@app.post("/api/auth/forgot-password")
def forgot_password(body: ForgotPasswordPayload):
    """Generate a password reset token and email it to the user.
    Always returns 200 to avoid leaking which emails are registered."""
    email = body.email.strip().lower()
    if not email:
        raise HTTPException(400, "Email required")

    conn = get_db()
    row = conn.execute("SELECT id, display_name FROM users WHERE email=?", (email,)).fetchone()
    if row:
        token   = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with conn:
            conn.execute(
                "INSERT INTO password_resets (token, user_id, expires_at) VALUES (?,?,?)",
                (token, row["id"], expires),
            )
            conn.commit()
        conn.close()

        reset_url = f"{APP_BASE_URL.rstrip('/')}/reset-password?token={token}"
        subject   = "The AI Researcher — Password Reset"
        msg_body  = (
            f"Hi {row['display_name']},\n\n"
            f"We received a request to reset your password. Click the link below to set a new password. "
            f"This link expires in 1 hour.\n\n"
            f"{reset_url}\n\n"
            f"If you did not request this, you can safely ignore this email.\n\n"
            f"— The AI Researcher"
        )
        try:
            _send_email(email, subject, msg_body)
        except HTTPException:
            # Don't surface SMTP errors to the client — just log
            pass
    else:
        conn.close()

    return {"ok": True, "message": "If that email is registered, a reset link has been sent."}


class ResetPasswordPayload(BaseModel):
    token: str
    new_password: str


@app.post("/api/auth/reset-password")
def reset_password(body: ResetPasswordPayload):
    if len(body.new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    row = conn.execute(
        "SELECT user_id, used FROM password_resets WHERE token=? AND expires_at > ?",
        (body.token, now),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(400, "Invalid or expired reset token")
    if row["used"]:
        conn.close()
        raise HTTPException(400, "This reset link has already been used")

    ph, ps = _hash_password(body.new_password)
    with conn:
        conn.execute(
            "UPDATE users SET password_hash=?, password_salt=? WHERE id=?",
            (ph, ps, row["user_id"]),
        )
        conn.execute("UPDATE password_resets SET used=1 WHERE token=?", (body.token,))
        # Invalidate any existing sessions for this user
        conn.execute("DELETE FROM sessions WHERE user_id=?", (row["user_id"],))
        conn.commit()
    conn.close()
    return {"ok": True}


# ─────────────────────────────────────────────
# Admin dashboard
# ─────────────────────────────────────────────
@app.get("/api/admin/daily/status")
def admin_daily_status(rubricgen_session: str | None = Cookie(default=None)):
    require_admin(rubricgen_session)
    return sched.get_scheduler_status(get_db)


class DailyTriggerPayload(BaseModel):
    dry_run: Optional[bool] = False


@app.post("/api/admin/daily/trigger")
def admin_daily_trigger(body: DailyTriggerPayload = DailyTriggerPayload(),
                        rubricgen_session: str | None = Cookie(default=None)):
    require_admin(rubricgen_session)
    system_id = _get_system_user_id()
    result = sched.run_daily_challenge(
        get_db, PAPERS_DIR, OBSIDIAN_VAULT_DIR, system_id,
        force=True, dry_run=body.dry_run or False,
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("message", "daily run failed"))
    return result


# ─── Admin leaderboard management ───

@app.delete("/api/admin/leaderboard/{model_id}")
def admin_delete_leaderboard_entry(model_id: str,
                                   rubricgen_session: str | None = Cookie(default=None)):
    """Remove a model from the leaderboard cache."""
    require_admin(rubricgen_session)
    conn = get_db()
    with conn:
        conn.execute("DELETE FROM leaderboard_cache WHERE model_id=?", (model_id,))
        conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/admin/leaderboard/refresh")
def admin_refresh_leaderboard(rubricgen_session: str | None = Cookie(default=None)):
    """Force-recompute the leaderboard from scratch (only kind='daily' challenges)."""
    require_admin(rubricgen_session)
    conn = get_db()
    bench.refresh_leaderboard(conn)
    conn.close()
    return {"ok": True}


@app.post("/api/admin/challenges/{cid}/exclude")
def admin_exclude_challenge(cid: int,
                            rubricgen_session: str | None = Cookie(default=None)):
    """Exclude a challenge from the leaderboard by changing its kind to 'dry_run'."""
    require_admin(rubricgen_session)
    conn = get_db()
    row = conn.execute("SELECT id, kind FROM challenges WHERE id=?", (cid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Challenge not found")
    with conn:
        conn.execute("UPDATE challenges SET kind='dry_run' WHERE id=?", (cid,))
        conn.commit()
    bench.refresh_leaderboard(conn)
    conn.close()
    return {"ok": True, "message": f"Challenge {cid} excluded from leaderboard"}


@app.post("/api/admin/challenges/{cid}/include")
def admin_include_challenge(cid: int,
                            rubricgen_session: str | None = Cookie(default=None)):
    """Re-include a challenge in the leaderboard by changing its kind back to 'daily'."""
    require_admin(rubricgen_session)
    conn = get_db()
    row = conn.execute("SELECT id, kind FROM challenges WHERE id=?", (cid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Challenge not found")
    with conn:
        conn.execute("UPDATE challenges SET kind='daily' WHERE id=?", (cid,))
        conn.commit()
    bench.refresh_leaderboard(conn)
    conn.close()
    return {"ok": True, "message": f"Challenge {cid} included in leaderboard"}


# ─── Grade-review queue (3-judge adjudication escalations) ───
#
# When primary, shadow, and third judges all give a different score on the
# same question, the adjudicator in backend/agents/adjudicator.py flags it
# and backend/review.py persists it to the grade_reviews table. These
# endpoints power the /review admin inbox.

class ReviewResolvePayload(BaseModel):
    final_score: float
    reviewer_note: Optional[str] = ""


@app.get("/api/reviews/pending")
def api_reviews_pending(limit: int = 200,
                        rubricgen_session: str | None = Cookie(default=None)):
    """List pending grade-review items (3-way-split questions)."""
    require_admin(rubricgen_session)
    conn = get_db()
    try:
        return review_mod.list_pending_reviews(conn, limit=limit)
    finally:
        conn.close()


@app.get("/api/reviews/{review_id}")
def api_review_detail(review_id: int,
                      rubricgen_session: str | None = Cookie(default=None)):
    """Fetch a single review item — the admin UI's detail pane."""
    require_admin(rubricgen_session)
    conn = get_db()
    try:
        row = review_mod.get_review(conn, review_id)
        if not row:
            raise HTTPException(404, "Review not found")
        return row
    finally:
        conn.close()


@app.post("/api/reviews/{review_id}/resolve")
def api_review_resolve(review_id: int,
                       body: ReviewResolvePayload,
                       rubricgen_session: str | None = Cookie(default=None)):
    """Resolve a pending review with the human adjudicator's final score.

    Updates the grade_reviews row, rewrites the participant's grade_json +
    denormalized accuracy/total_score columns so the leaderboard reflects
    the final grade.
    """
    user = require_admin(rubricgen_session)
    conn = get_db()
    try:
        try:
            result = review_mod.resolve_review(
                conn,
                review_id=review_id,
                final_score=body.final_score,
                reviewer_user_id=user["id"],
                reviewer_note=body.reviewer_note or "",
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        # Refresh the leaderboard so the adjudicated score shows up
        # immediately rather than waiting for the next benchmark pass.
        try:
            bench.refresh_leaderboard(conn)
        except Exception as e:
            logger.warning("refresh_leaderboard after resolve failed: %s", e)
        return {"ok": True, "review": result}
    finally:
        conn.close()


@app.get("/api/admin/users")
def admin_list_users(rubricgen_session: str | None = Cookie(default=None)):
    require_admin(rubricgen_session)
    conn = get_db()
    rows = conn.execute(
        """SELECT u.id, u.email, u.display_name, u.role, u.created_at,
                  (SELECT COUNT(*) FROM papers      p WHERE p.user_id = u.id) AS paper_count,
                  (SELECT COUNT(*) FROM rubrics     r WHERE r.user_id = u.id) AS rubric_count,
                  (SELECT COUNT(*) FROM evaluations e WHERE e.user_id = u.id) AS eval_count
           FROM users u
           ORDER BY u.created_at DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────
# Benchmark challenges (Phase 1)
# ─────────────────────────────────────────────
class CreateChallengePayload(BaseModel):
    title: str
    theme: Optional[str] = ""
    paper_ids: list[int]
    participant_models: list[str]
    project_id: Optional[int] = None
    org_id: Optional[int] = None
    visibility: Optional[str] = "private"
    difficulty: Optional[str] = None
    registered_model_id: Optional[int] = None


class UpdateChallengePayload(BaseModel):
    title: Optional[str] = None
    theme: Optional[str] = None
    project_id: Optional[int] = None
    visibility: Optional[str] = None
    difficulty: Optional[str] = None
    registered_model_id: Optional[int] = None


@app.post("/api/challenges", status_code=201)
def api_create_challenge(body: CreateChallengePayload,
                         rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    try:
        cid = bench.create_challenge(
            get_db, user["id"], body.title.strip(), (body.theme or "").strip(),
            body.paper_ids, body.participant_models,
            project_id=body.project_id,
            visibility=body.visibility or "private",
            difficulty=body.difficulty,
            registered_model_id=body.registered_model_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"challenge_id": cid, "status": "pending"}


@app.patch("/api/challenges/{cid}")
def api_update_challenge(cid: int, body: UpdateChallengePayload,
                         rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    row = conn.execute("SELECT * FROM challenges WHERE id=?", (cid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Challenge not found")
    if row["created_by"] != user["id"]:
        conn.close()
        raise HTTPException(403, "Only the owner can update a challenge")

    current = dict(row)
    new_visibility = body.visibility if body.visibility is not None else current.get("visibility") or "private"
    new_difficulty = body.difficulty if body.difficulty is not None else current.get("difficulty")
    new_registered_model_id = body.registered_model_id if body.registered_model_id is not None else current.get("registered_model_id")

    if new_visibility not in ("private", "public"):
        conn.close()
        raise HTTPException(400, "visibility must be 'private' or 'public'")
    if new_difficulty and new_difficulty not in bench.DIFFICULTY_LEVELS:
        conn.close()
        raise HTTPException(400, f"Unknown difficulty '{new_difficulty}'")

    if new_visibility == "public":
        if not new_difficulty:
            conn.close()
            raise HTTPException(400, "Public challenges must specify a difficulty level")
        if not new_registered_model_id:
            conn.close()
            raise HTTPException(400, "Public challenges must specify a registered model")
        # Verify membership
        mem = conn.execute(
            "SELECT 1 FROM registered_model_members WHERE registered_model_id=? AND user_id=?",
            (new_registered_model_id, user["id"]),
        ).fetchone()
        if not mem:
            conn.close()
            raise HTTPException(403, "You must be a member of the registered model to publish with it")

    # project_id ownership
    if body.project_id is not None:
        proj = conn.execute("SELECT user_id FROM projects WHERE id=?", (body.project_id,)).fetchone()
        if not proj or proj["user_id"] != user["id"]:
            conn.close()
            raise HTTPException(400, "Project not found or not owned by user")

    fields = []
    values: list = []
    if body.title is not None:
        fields.append("title=?"); values.append(body.title.strip())
    if body.theme is not None:
        fields.append("theme=?"); values.append(body.theme.strip())
    if body.project_id is not None:
        fields.append("project_id=?"); values.append(body.project_id)
    if body.visibility is not None:
        fields.append("visibility=?"); values.append(new_visibility)
    if body.difficulty is not None:
        fields.append("difficulty=?"); values.append(new_difficulty)
    if body.registered_model_id is not None:
        fields.append("registered_model_id=?"); values.append(new_registered_model_id)

    if fields:
        values.append(cid)
        with conn:
            conn.execute(f"UPDATE challenges SET {', '.join(fields)} WHERE id=?", values)
            conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/challenges/estimate-cost")
def api_estimate_cost(paper_count: int = 1, models: str = "",
                      rubricgen_session: str | None = Cookie(default=None)):
    """Estimate the cost of running a challenge."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    model_ids = [m.strip() for m in models.split(",") if m.strip()]
    conn = get_db()
    try:
        estimate = bill.estimate_challenge_cost(model_ids, paper_count, conn)
        balance = bill.get_balance(conn, user["id"])
        estimate["balance"] = balance
        estimate["sufficient"] = balance >= estimate["total"]
        return estimate
    finally:
        conn.close()


class RunChallengePayload(BaseModel):
    approved: bool = False

@app.post("/api/challenges/{cid}/run", status_code=202)
def api_run_challenge(cid: int, body: RunChallengePayload | None = None,
                      rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    approved = body.approved if body else False
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM challenges WHERE id=?", (cid,)).fetchone()
        if not row:
            raise HTTPException(404, "Challenge not found")
        if row["status"] == "running":
            raise HTTPException(409, "Challenge is already running")
        if row["status"] == "complete":
            raise HTTPException(409, "Challenge already complete")

        # Count papers and models for cost estimate
        paper_count = conn.execute(
            "SELECT COUNT(*) AS c FROM challenge_papers WHERE challenge_id=?", (cid,)
        ).fetchone()["c"]
        model_rows = conn.execute(
            "SELECT model_id FROM model_participants WHERE challenge_id=?", (cid,)
        ).fetchall()
        model_ids = [r["model_id"] for r in model_rows]

        estimate = bill.estimate_challenge_cost(model_ids, paper_count, conn)
        total_cost = estimate["total"]
        is_admin = user.get("role") == "admin"

        # Admins bypass credit checks (for testing and platform management)
        if not is_admin:
            # Check balance
            balance = bill.get_balance(conn, user["id"])
            if balance < total_cost:
                raise HTTPException(402, {
                    "detail": "Insufficient credits",
                    "estimate": estimate,
                    "balance": balance,
                })

            # Require approval if cost > 50 credits ($5) or user has <= 50 credits
            APPROVAL_THRESHOLD = 50
            if not approved and (total_cost > APPROVAL_THRESHOLD or balance <= APPROVAL_THRESHOLD):
                return JSONResponse(status_code=402, content={
                    "approval_required": True,
                    "estimate": estimate,
                    "balance": balance,
                    "message": f"This challenge will cost approximately {total_cost} credits. Your balance is {balance} credits.",
                })

            # Debit credits before running
            success = bill.debit_credits(conn, user["id"], total_cost,
                                         f"Challenge #{cid} ({row['title']})", cid)
            if not success:
                raise HTTPException(402, "Failed to debit credits — insufficient balance")

        # Store cost estimate on challenge
        with conn:
            conn.execute(
                "UPDATE challenges SET cost_estimate=?, cost_approved=1 WHERE id=?",
                (total_cost, cid),
            )
            conn.commit()
    finally:
        conn.close()

    bench.run_challenge_async(get_db, cid, PAPERS_DIR, OBSIDIAN_VAULT_DIR)
    return {"challenge_id": cid, "status": "running", "cost_debited": total_cost, "run_id": row["run_id"]}


@app.get("/api/challenges")
def api_list_challenges(project_id: Optional[int] = None,
                        visibility: Optional[str] = None,
                        rubricgen_session: str | None = Cookie(default=None),
                        x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    conn = get_db()
    # Default view: user's own challenges (any visibility) + all public challenges
    where_parts = ["(c.created_by=? OR c.visibility='public')"]
    params: list = [user["id"]]
    if project_id is not None:
        where_parts.append("c.project_id=?")
        params.append(project_id)
    if visibility in ("private", "public"):
        where_parts.append("c.visibility=?")
        params.append(visibility)
    where_sql = " AND ".join(where_parts)
    rows = conn.execute(
        f"""SELECT c.id, c.title, c.theme, c.kind, c.status,
                  c.created_at, c.started_at, c.completed_at,
                  c.generator_score, c.judge_score,
                  c.project_id, c.visibility, c.difficulty, c.registered_model_id,
                  c.created_by, c.run_id,
                  u.display_name AS created_by_name,
                  rm.name AS registered_model_name, rm.version AS registered_model_version,
                  (SELECT COUNT(*) FROM challenge_papers cp WHERE cp.challenge_id=c.id) AS paper_count,
                  (SELECT COUNT(*) FROM model_participants mp WHERE mp.challenge_id=c.id) AS participant_count
           FROM challenges c
           LEFT JOIN users u ON u.id = c.created_by
           LEFT JOIN registered_models rm ON rm.id = c.registered_model_id
           WHERE {where_sql}
           ORDER BY c.created_at DESC
           LIMIT 200""",
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/challenges/{cid}")
def api_get_challenge(cid: int, rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    challenge = conn.execute("SELECT * FROM challenges WHERE id=?", (cid,)).fetchone()
    if not challenge:
        conn.close()
        raise HTTPException(404, "Challenge not found")
    # Access rule: owner OR public visibility
    ch_dict = dict(challenge)
    if ch_dict.get("created_by") != user["id"] and ch_dict.get("visibility") != "public":
        conn.close()
        raise HTTPException(403, "You do not have access to this challenge")
    rubric_row = conn.execute(
        "SELECT rubric_json, generation_time_ms FROM challenge_rubrics WHERE challenge_id=?",
        (cid,),
    ).fetchone()
    papers = conn.execute(
        """SELECT p.id, p.filename FROM papers p
           JOIN challenge_papers cp ON cp.paper_id=p.id
           WHERE cp.challenge_id=? ORDER BY p.id""",
        (cid,),
    ).fetchall()
    participants = conn.execute(
        "SELECT * FROM model_participants WHERE challenge_id=? ORDER BY total_score DESC",
        (cid,),
    ).fetchall()
    # Project and registered model enrichment
    project = None
    if ch_dict.get("project_id"):
        project = conn.execute(
            "SELECT id, name FROM projects WHERE id=?", (ch_dict["project_id"],)
        ).fetchone()
    registered_model = None
    if ch_dict.get("registered_model_id"):
        from backend.models_registry import get_registered_model as _get_rm
        try:
            registered_model = _get_rm(conn, ch_dict["registered_model_id"])
        except HTTPException:
            registered_model = None
    # Enrich with agent skill details (before closing conn)
    generator_skill_detail = None
    judge_skill_detail = None
    if ch_dict.get("generator_skill_id"):
        gs = conn.execute("SELECT version, avg_performance, times_used FROM agent_skills WHERE id=?",
                          (ch_dict["generator_skill_id"],)).fetchone()
        generator_skill_detail = dict(gs) if gs else None
    if ch_dict.get("judge_skill_id"):
        js = conn.execute("SELECT version, avg_performance, times_used FROM agent_skills WHERE id=?",
                          (ch_dict["judge_skill_id"],)).fetchone()
        judge_skill_detail = dict(js) if js else None
    conn.close()

    result: dict = dict(challenge)
    result["project"] = dict(project) if project else None
    result["generator_skill"] = generator_skill_detail
    result["judge_skill"] = judge_skill_detail
    result["registered_model"] = registered_model
    result["is_owner"] = (ch_dict.get("created_by") == user["id"])
    result["papers"] = [dict(p) for p in papers]
    result["participants"] = []
    for mp in participants:
        d = dict(mp)
        try:
            d["answer_data"] = json.loads(d.pop("answer_json") or "{}")
        except Exception:
            d["answer_data"] = {}
        try:
            d["grade_data"] = json.loads(d.pop("grade_json") or "{}")
        except Exception:
            d["grade_data"] = {}
        result["participants"].append(d)
    if rubric_row:
        try:
            result["rubric"] = json.loads(rubric_row["rubric_json"])
        except Exception:
            result["rubric"] = {}
        result["generation_time_ms"] = rubric_row["generation_time_ms"]
    else:
        result["rubric"] = None

    return result


@app.get("/api/challenges/{cid}/events")
def api_challenge_events(cid: int, rubricgen_session: str | None = Cookie(default=None)):
    """Get real-time progress events for a challenge (AI Brain Window)."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM challenge_events WHERE challenge_id=? ORDER BY created_at ASC",
            (cid,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.post("/api/challenges/{cid}/cancel")
def api_cancel_challenge(cid: int, rubricgen_session: str | None = Cookie(default=None)):
    """Cancel a pending/running challenge. Refunds credits."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM challenges WHERE id=?", (cid,)).fetchone()
        if not row:
            raise HTTPException(404, "Challenge not found")
        if row["created_by"] != user["id"]:
            raise HTTPException(403, "Only the owner can cancel")
        if row["status"] not in ("pending", "running"):
            raise HTTPException(400, "Can only cancel pending or running challenges")
        with conn:
            conn.execute(
                "UPDATE challenges SET status='failed', error_message='Cancelled by user', completed_at=CURRENT_TIMESTAMP WHERE id=?",
                (cid,),
            )
            conn.commit()
        # Refund credits
        if row["cost_estimate"] and row["created_by"]:
            try:
                bill.refund_credits(conn, row["created_by"], row["cost_estimate"],
                                    f"Cancelled: Challenge #{cid}", cid)
            except Exception:
                pass
        return {"ok": True, "status": "failed"}
    finally:
        conn.close()


@app.delete("/api/challenges/{cid}")
def api_delete_challenge(cid: int, rubricgen_session: str | None = Cookie(default=None)):
    """Delete a private challenge. Only owner, only if not running."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM challenges WHERE id=?", (cid,)).fetchone()
        if not row:
            raise HTTPException(404, "Challenge not found")
        if row["created_by"] != user["id"]:
            raise HTTPException(403, "Only the owner can delete")
        if row["status"] == "running":
            raise HTTPException(400, "Cannot delete a running challenge. Cancel it first.")
        if row["visibility"] == "public":
            raise HTTPException(400, "Cannot delete public challenges")
        with conn:
            conn.execute("DELETE FROM challenges WHERE id=?", (cid,))
            conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/leaderboard")
def api_leaderboard(rubricgen_session: str | None = Cookie(default=None)):
    """Overall leaderboard ranked by total points."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    rows = conn.execute(
        """SELECT * FROM leaderboard_cache
           ORDER BY total_points DESC, cumulative_score DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/leaderboard/daily")
def api_daily_leaderboard(rubricgen_session: str | None = Cookie(default=None)):
    """Daily AI Researcher Challenge leaderboard with streak and movement."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    rows = conn.execute(
        """SELECT * FROM leaderboard_cache
           WHERE daily_points > 0
           ORDER BY daily_points DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/leaderboard/head-to-head")
def api_head_to_head(
    model1: str,
    model2: str,
    rubricgen_session: str | None = Cookie(default=None),
):
    """Head-to-head comparison of two models across shared challenges."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        m1 = conn.execute("SELECT * FROM leaderboard_cache WHERE model_id=?", (model1,)).fetchone()
        m2 = conn.execute("SELECT * FROM leaderboard_cache WHERE model_id=?", (model2,)).fetchone()
        if not m1 or not m2:
            raise HTTPException(status_code=404, detail="Model not found in leaderboard")

        shared = conn.execute("""
            SELECT c.id, c.theme, c.completed_at,
                   p1.accuracy AS m1_accuracy, p1.total_score AS m1_score,
                   p2.accuracy AS m2_accuracy, p2.total_score AS m2_score
            FROM model_participants p1
            JOIN model_participants p2 ON p1.challenge_id = p2.challenge_id
            JOIN challenges c ON c.id = p1.challenge_id
            WHERE p1.model_id=? AND p2.model_id=?
              AND p1.status='graded' AND p2.status='graded'
              AND c.status='complete'
            ORDER BY c.completed_at DESC
            LIMIT 20
        """, (model1, model2)).fetchall()

        wins1, wins2, ties = 0, 0, 0
        for r in shared:
            s1 = r["m1_score"] or 0
            s2 = r["m2_score"] or 0
            if s1 > s2: wins1 += 1
            elif s2 > s1: wins2 += 1
            else: ties += 1

        return {
            "model1": dict(m1), "model2": dict(m2),
            "shared_challenges": [dict(r) for r in shared],
            "wins1": wins1, "wins2": wins2, "ties": ties,
            "total_shared": len(shared),
        }
    finally:
        conn.close()


@app.get("/api/daily-results")
def api_daily_results(rubricgen_session: str | None = Cookie(default=None)):
    """Recent Daily AI Researcher Challenge results with drill-down data."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return bench.get_daily_results(conn, limit=30)
    finally:
        conn.close()


@app.get("/api/skills/{agent_type}")
def api_get_skill(agent_type: str, rubricgen_session: str | None = Cookie(default=None)):
    require_admin(rubricgen_session)
    if agent_type not in ("generator", "judge"):
        raise HTTPException(400, "agent_type must be 'generator' or 'judge'")
    conn = get_db()
    active = get_active_skill(conn, agent_type)
    versions = list_skill_versions(conn, agent_type)
    conn.close()
    return {"active": active, "versions": versions}


from backend.self_improve import (
    get_improvement_status, run_experiment_loop,
)
from backend.skills import activate_skill_version


@app.get("/api/admin/skills/{agent_type}/status")
def api_skill_status(agent_type: str, rubricgen_session: str | None = Cookie(default=None)):
    """Self-improvement status: active version, performance, rollback status."""
    require_admin(rubricgen_session)
    if agent_type not in ("generator", "judge"):
        raise HTTPException(400, "agent_type must be 'generator' or 'judge'")
    conn = get_db()
    try:
        return get_improvement_status(conn, agent_type)
    finally:
        conn.close()


@app.post("/api/admin/skills/{agent_type}/improve")
def api_trigger_improvement(agent_type: str,
                            rubricgen_session: str | None = Cookie(default=None)):
    """Manually trigger an autoresearch-style experiment loop.
    Runs up to EXPERIMENT_BUDGET experiments: propose → eval → keep/discard."""
    require_admin(rubricgen_session)
    if agent_type not in ("generator", "judge"):
        raise HTTPException(400, "agent_type must be 'generator' or 'judge'")

    # Load papers from most recent daily challenge for the eval
    import base64 as _b64
    conn = get_db()
    try:
        latest = conn.execute(
            """SELECT c.id, c.theme FROM challenges c
               WHERE c.kind IN ('daily','dry_run') AND c.status='complete'
               ORDER BY c.completed_at DESC LIMIT 1"""
        ).fetchone()
        if not latest:
            raise HTTPException(400, "No completed challenges to use as test data")
        theme = latest["theme"] or ""
        paper_rows = conn.execute(
            """SELECT p.filename, p.disk_filename, p.storage_path
               FROM papers p JOIN challenge_papers cp ON cp.paper_id = p.id
               WHERE cp.challenge_id=? LIMIT 2""",
            (latest["id"],),
        ).fetchall()
        papers_b64 = []
        for r in paper_rows:
            try:
                data = paper_files_mod.read_paper_bytes(r, PAPERS_DIR)
                papers_b64.append({"filename": r["filename"], "b64": _b64.b64encode(data).decode()})
            except HTTPException:
                continue  # best-effort: skip missing papers
    finally:
        conn.close()

    if not papers_b64:
        raise HTTPException(400, "No papers available for evaluation")

    result = run_experiment_loop(get_db, agent_type, papers_b64, theme, OBSIDIAN_VAULT_DIR)
    return result


@app.post("/api/admin/skills/{agent_type}/{version}/activate")
def api_activate_skill(agent_type: str, version: int,
                       rubricgen_session: str | None = Cookie(default=None)):
    """Manually activate a specific skill version."""
    require_admin(rubricgen_session)
    if agent_type not in ("generator", "judge"):
        raise HTTPException(400, "agent_type must be 'generator' or 'judge'")
    conn = get_db()
    try:
        activate_skill_version(conn, agent_type, version)
        # Refresh the Anthropic-format vault files
        from backend.obsidian import write_agent_skill_file, write_agent_history_file
        active = get_active_skill(conn, agent_type)
        versions = list_skill_versions(conn, agent_type)
        write_agent_skill_file(OBSIDIAN_VAULT_DIR, agent_type, active)
        write_agent_history_file(OBSIDIAN_VAULT_DIR, agent_type, versions)
        return {"ok": True, "activated_version": version}
    finally:
        conn.close()


@app.get("/api/skills/{agent_type}/skill-md")
def api_get_skill_md(agent_type: str,
                     rubricgen_session: str | None = Cookie(default=None)):
    """Return the Anthropic-format SKILL.md content for an agent. Admin-only."""
    require_admin(rubricgen_session)
    if agent_type not in ("generator", "judge"):
        raise HTTPException(400, "agent_type must be 'generator' or 'judge'")
    p = OBSIDIAN_VAULT_DIR / "skills" / agent_type / "SKILL.md"
    if not p.exists():
        raise HTTPException(404, "SKILL.md not found — has the vault been seeded?")
    return {"content": p.read_text(encoding="utf-8"), "path": str(p)}


@app.get("/api/skills/{agent_type}/program-md")
def api_get_program_md(agent_type: str,
                       rubricgen_session: str | None = Cookie(default=None)):
    """Return the human-editable program.md meta-learner control file. Admin-only."""
    require_admin(rubricgen_session)
    if agent_type not in ("generator", "judge"):
        raise HTTPException(400, "agent_type must be 'generator' or 'judge'")
    p = OBSIDIAN_VAULT_DIR / "skills" / agent_type / "program.md"
    if not p.exists():
        raise HTTPException(404, "program.md not found")
    return {"content": p.read_text(encoding="utf-8"), "path": str(p)}


class UpdateProgramMdPayload(BaseModel):
    content: str


@app.put("/api/skills/{agent_type}/program-md")
def api_update_program_md(agent_type: str,
                          body: UpdateProgramMdPayload,
                          rubricgen_session: str | None = Cookie(default=None)):
    """Update the human-editable program.md meta-learner control file.

    This file is re-read on every self-improvement experiment — edits
    take effect immediately on the next run. Admin-only."""
    require_admin(rubricgen_session)
    if agent_type not in ("generator", "judge"):
        raise HTTPException(400, "agent_type must be 'generator' or 'judge'")
    if len(body.content) > 100_000:
        raise HTTPException(400, "program.md too large (100 KB limit)")
    p = OBSIDIAN_VAULT_DIR / "skills" / agent_type / "program.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body.content, encoding="utf-8")
    return {"ok": True, "path": str(p), "bytes": len(body.content)}


# ─────────────────────────────────────────────
# Registered Model Registry (Phase 1.5)
# ─────────────────────────────────────────────
from backend import models_registry as mreg


class RegisterModelPayload(BaseModel):
    name: str
    version: str
    provider: Optional[str] = ""
    git_repo: Optional[str] = ""
    organization: Optional[str] = ""
    team_member_emails: Optional[list[str]] = None
    org_id: Optional[int] = None


class AddMemberPayload(BaseModel):
    email: str


class UpdateModelPayload(BaseModel):
    name: Optional[str] = None
    version: Optional[str] = None
    provider: Optional[str] = None
    git_repo: Optional[str] = None
    organization: Optional[str] = None
    changelog: Optional[str] = None


@app.post("/api/models", status_code=201)
def api_create_model(body: RegisterModelPayload,
                     rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        model = mreg.create_registered_model(
            conn, user["id"],
            name=body.name, version=body.version,
            provider=body.provider or "", git_repo=body.git_repo or "",
            organization=body.organization or "",
            team_member_emails=body.team_member_emails,
            org_id=body.org_id,
        )
    finally:
        conn.close()
    return model


@app.get("/api/models")
def api_list_models(mine_only: bool = False,
                    rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        models = mreg.list_registered_models(conn, user_id=user["id"] if mine_only else None)
    finally:
        conn.close()
    return models


@app.get("/api/models/mine")
def api_list_my_models(rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        models = mreg.list_registered_models(conn, user_id=user["id"])
    finally:
        conn.close()
    return models


@app.get("/api/models/{model_id}")
def api_get_model(model_id: int, rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return mreg.get_registered_model(conn, model_id)
    finally:
        conn.close()


@app.delete("/api/models/{model_id}")
def api_delete_model(model_id: int, rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        mreg.delete_registered_model(conn, model_id, user["id"])
    finally:
        conn.close()
    return {"ok": True}


@app.patch("/api/models/{model_id}")
def api_update_model(model_id: int, body: UpdateModelPayload,
                     rubricgen_session: str | None = Cookie(default=None)):
    """Update model metadata. Creator only. If version changes, logs to version history."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        model = mreg.update_model(
            conn, model_id, user["id"],
            name=body.name, version=body.version,
            provider=body.provider, git_repo=body.git_repo,
            organization=body.organization, changelog=body.changelog,
        )
    finally:
        conn.close()
    return model


@app.get("/api/models/{model_id}/versions")
def api_model_versions(model_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Get version history for a model."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return mreg.get_version_history(conn, model_id)
    finally:
        conn.close()


@app.post("/api/models/{model_id}/members")
def api_add_member(model_id: int, body: AddMemberPayload,
                   rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return mreg.add_member(conn, model_id, user["id"], body.email)
    finally:
        conn.close()


@app.delete("/api/models/{model_id}/members/{member_user_id}")
def api_remove_member(model_id: int, member_user_id: int,
                      rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "admin")
    conn = get_db()
    try:
        return mreg.remove_member(conn, model_id, user["id"], member_user_id)
    finally:
        conn.close()


class UpdateMemberPermPayload(BaseModel):
    can_run: bool
    acknowledged: Optional[bool] = False


@app.patch("/api/models/{model_id}/members/{member_user_id}")
def api_update_member_perm(model_id: int, member_user_id: int,
                           body: UpdateMemberPermPayload,
                           rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "admin")
    if body.can_run and not body.acknowledged:
        return {
            "ok": False,
            "credit_warning": True,
            "message": "Granting run permission means your prepaid credits will be charged when this user runs your model. Set acknowledged=true to confirm.",
        }
    conn = get_db()
    try:
        return mreg.update_member_permission(conn, model_id, user["id"], member_user_id, body.can_run)
    finally:
        conn.close()


# ─────────────────────────────────────────────
# Public Tests Gallery (Phase 1.5)
# ─────────────────────────────────────────────
@app.get("/api/public-tests")
def api_list_public_tests(difficulty: Optional[str] = None,
                          rubricgen_session: str | None = Cookie(default=None)):
    """Feed of user-designed public tests. Not part of the leaderboard."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    where = ["c.visibility='public'"]
    params: list = []
    if difficulty and difficulty in bench.DIFFICULTY_LEVELS:
        where.append("c.difficulty=?")
        params.append(difficulty)
    rows = conn.execute(
        f"""SELECT c.id, c.title, c.theme, c.difficulty, c.status,
                  c.created_at, c.completed_at,
                  c.registered_model_id,
                  rm.name AS model_name, rm.version AS model_version,
                  rm.organization AS model_org,
                  u.display_name AS created_by_name,
                  (SELECT MAX(accuracy) FROM model_participants mp WHERE mp.challenge_id=c.id) AS best_accuracy
           FROM challenges c
           LEFT JOIN users u ON u.id = c.created_by
           LEFT JOIN registered_models rm ON rm.id = c.registered_model_id
           WHERE {' AND '.join(where)}
           ORDER BY (c.completed_at IS NULL), c.completed_at DESC, c.created_at DESC
           LIMIT 200""",
        params,
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        # Team member names
        if d.get("registered_model_id"):
            members = conn.execute(
                """SELECT u.display_name, u.email
                   FROM registered_model_members rmm
                   JOIN users u ON u.id = rmm.user_id
                   WHERE rmm.registered_model_id=?""",
                (d["registered_model_id"],),
            ).fetchall()
            d["team"] = [dict(m) for m in members]
        else:
            d["team"] = []
        result.append(d)
    conn.close()
    return result


# ─────────────────────────────────────────────
# Billing (Phase 3)
# ─────────────────────────────────────────────
from backend import billing as bill
from backend import promo
from backend import agreements as agree


class CheckoutPayload(BaseModel):
    pack_id: int


class ApplyPromoPayload(BaseModel):
    code: str


class CreatePromoPayload(BaseModel):
    code: str
    type: str
    max_uses: Optional[int] = 0
    discount_pct: Optional[int] = 100
    valid_until: Optional[str] = None
    auto_approve_hours: Optional[int] = 48


@app.get("/api/billing/balance")
def api_billing_balance(rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        balance = bill.get_balance(conn, user["id"])
        promo_status = promo.user_has_active_promo(conn, user["id"])
    finally:
        conn.close()
    return {"balance": balance, "active_promo": promo_status}


@app.get("/api/billing/packs")
def api_billing_packs(rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return bill.list_packs(conn)
    finally:
        conn.close()


@app.get("/api/billing/transactions")
def api_billing_transactions(rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        return bill.list_transactions(conn, user["id"])
    finally:
        conn.close()


@app.post("/api/billing/checkout")
def api_billing_checkout(body: CheckoutPayload,
                         rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    # Check payment agreement
    conn = get_db()
    try:
        if not agree.has_accepted(conn, user["id"], "payment"):
            raise HTTPException(403, "You must accept the Payment Agreement before purchasing credits")
        checkout_url = bill.create_checkout_session(
            conn, user["id"], body.pack_id,
            success_url=f"{APP_BASE_URL}/billing?success=1",
            cancel_url=f"{APP_BASE_URL}/billing?cancel=1",
        )
    finally:
        conn.close()
    return {"checkout_url": checkout_url}


@app.post("/api/billing/webhook")
async def api_stripe_webhook(request: Request):
    """Stripe webhook endpoint. No auth — verified by Stripe signature."""
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    return bill.handle_stripe_webhook(payload, sig, get_db)


@app.get("/api/billing/test-cost")
def api_test_cost(models: str = "", rubricgen_session: str | None = Cookie(default=None)):
    """Calculate credits needed for a test. models is comma-separated."""
    user = require_user(rubricgen_session)
    model_ids = [m.strip() for m in models.split(",") if m.strip()]
    conn = get_db()
    try:
        cost = bill.calculate_test_cost(model_ids, conn)
        promo_status = promo.user_has_active_promo(conn, user["id"])
    finally:
        conn.close()
    if promo_status and promo_status.get("type") == "free":
        return {"cost": cost, "discounted_cost": 0, "promo": "free"}
    elif promo_status and promo_status.get("type") == "breakeven":
        return {"cost": cost, "discounted_cost": cost, "promo": "breakeven"}
    return {"cost": cost, "discounted_cost": cost, "promo": None}


# ─── Promo codes ───

@app.post("/api/promo/apply")
def api_apply_promo(body: ApplyPromoPayload,
                    rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        return promo.apply_promo_code(conn, user["id"], body.code)
    finally:
        conn.close()


@app.get("/api/promo/status")
def api_promo_status(rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        return promo.get_user_promo_status(conn, user["id"])
    finally:
        conn.close()


@app.post("/api/admin/promo", status_code=201)
def api_create_promo(body: CreatePromoPayload,
                     rubricgen_session: str | None = Cookie(default=None)):
    user = require_admin(rubricgen_session)
    conn = get_db()
    try:
        return promo.create_promo_code(
            conn, body.code, body.type, user["id"],
            max_uses=body.max_uses or 0,
            discount_pct=body.discount_pct or 100,
            valid_until=body.valid_until,
            auto_approve_hours=body.auto_approve_hours or 48,
        )
    finally:
        conn.close()


@app.get("/api/admin/promo")
def api_list_promos(rubricgen_session: str | None = Cookie(default=None)):
    require_admin(rubricgen_session)
    conn = get_db()
    try:
        return promo.list_promo_codes(conn)
    finally:
        conn.close()


@app.post("/api/admin/promo/{activation_id}/approve")
def api_approve_promo(activation_id: int,
                      rubricgen_session: str | None = Cookie(default=None)):
    require_admin(rubricgen_session)
    conn = get_db()
    try:
        return promo.admin_approve_user_promo(conn, activation_id)
    finally:
        conn.close()


# ─── Agreements ───

@app.get("/api/agreements/{agreement_type}")
def api_get_agreement(agreement_type: str,
                      rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    return {"text": agree.get_agreement_text(agreement_type), "version": agree.CURRENT_VERSION}


@app.post("/api/agreements/{agreement_type}/accept")
def api_accept_agreement(agreement_type: str,
                         rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        return agree.accept_agreement(conn, user["id"], agreement_type)
    finally:
        conn.close()


@app.get("/api/agreements/status")
def api_agreements_status(rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        return agree.get_user_agreements_status(conn, user["id"])
    finally:
        conn.close()


# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# Competition API (Phase 5: external model submissions)
# ─────────────────────────────────────────────

SUBMISSION_WINDOW_HOURS = int(os.environ.get("SUBMISSION_WINDOW_HOURS", "24"))


def _auth_model_key(request: Request, conn) -> dict:
    """Authenticate a request via X-Model-Key header. Returns the registered model dict."""
    api_key = request.headers.get("X-Model-Key", "").strip()
    if not api_key:
        raise HTTPException(401, "Missing X-Model-Key header")
    model = mreg.get_model_by_api_key(conn, api_key)
    if not model:
        raise HTTPException(401, "Invalid model API key")
    return model


@app.get("/api/compete/challenges")
def compete_list_challenges(request: Request):
    """List open challenges this model can participate in."""
    conn = get_db()
    try:
        model = _auth_model_key(request, conn)
        if not model.get("daily_admin_approved"):
            raise HTTPException(403, "Model not approved for daily challenges")
        rows = conn.execute(
            """SELECT c.id, c.title, c.theme, c.kind, c.status, c.created_at,
                      cs.status AS submission_status
               FROM challenges c
               LEFT JOIN challenge_submissions cs
                   ON cs.challenge_id = c.id AND cs.registered_model_id = ?
               WHERE c.kind = 'daily' AND c.status = 'complete'
               ORDER BY c.created_at DESC LIMIT 30""",
            (model["id"],),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/compete/{challenge_id}/questions")
def compete_get_questions(challenge_id: int, request: Request):
    """Fetch challenge questions (ideal answers stripped for fairness)."""
    conn = get_db()
    try:
        model = _auth_model_key(request, conn)
        if not model.get("daily_admin_approved"):
            raise HTTPException(403, "Model not approved for daily challenges")

        challenge_row = conn.execute("SELECT * FROM challenges WHERE id=?", (challenge_id,)).fetchone()
        if not challenge_row:
            raise HTTPException(404, "Challenge not found")
        challenge = dict(challenge_row)

        rubric_row = conn.execute(
            "SELECT rubric_json FROM challenge_rubrics WHERE challenge_id=?",
            (challenge_id,),
        ).fetchone()
        if not rubric_row:
            raise HTTPException(404, "Rubric not yet generated")

        rubric = json.loads(rubric_row["rubric_json"] or "{}")

        # Strip ideal answers and scoring criteria — external models should not see them
        questions_safe = []
        for q in rubric.get("questions", []):
            questions_safe.append({
                "id": q.get("id"),
                "domain": q.get("domain", ""),
                "paper_ref": q.get("paper_ref", ""),
                "question": q.get("question", ""),
                "max_points": q.get("max_points", 1),
            })

        # Ensure a submission slot exists
        conn.execute(
            """INSERT INTO challenge_submissions
               (challenge_id, registered_model_id, status)
               VALUES (?,?,?) ON CONFLICT DO NOTHING""",
            (challenge_id, model["id"], "open"),
        )
        conn.commit()

        return {
            "challenge_id": challenge_id,
            "title": challenge.get("title", ""),
            "theme": challenge.get("theme", ""),
            "question_count": len(questions_safe),
            "questions": questions_safe,
        }
    finally:
        conn.close()


class CompeteSubmitPayload(BaseModel):
    responses: list[dict]


@app.post("/api/compete/{challenge_id}/submit")
def compete_submit(challenge_id: int, body: CompeteSubmitPayload, request: Request):
    """Submit model answers for a challenge."""
    conn = get_db()
    try:
        model = _auth_model_key(request, conn)

        # Check submission exists and is open
        sub = conn.execute(
            "SELECT id, status FROM challenge_submissions WHERE challenge_id=? AND registered_model_id=?",
            (challenge_id, model["id"]),
        ).fetchone()
        if not sub:
            raise HTTPException(404, "No submission slot — fetch questions first")
        if sub["status"] not in ("open", "submitted"):
            raise HTTPException(409, f"Submission is {sub['status']} — cannot resubmit")

        # Validate response format
        if not body.responses:
            raise HTTPException(400, "responses array is empty")

        answer_json = json.dumps({"responses": [dict(r) for r in body.responses]})

        with conn:
            conn.execute(
                """UPDATE challenge_submissions
                   SET answer_json=?, submitted_at=CURRENT_TIMESTAMP, status='submitted'
                   WHERE id=?""",
                (answer_json, sub["id"]),
            )
            conn.commit()

        return {"ok": True, "submission_id": sub["id"], "status": "submitted",
                "message": "Answers received. Grading will occur after the submission window closes."}
    finally:
        conn.close()


@app.get("/api/compete/{challenge_id}/results")
def compete_get_results(challenge_id: int, request: Request):
    """View grading results for this model's submission."""
    conn = get_db()
    try:
        model = _auth_model_key(request, conn)
        sub = conn.execute(
            "SELECT * FROM challenge_submissions WHERE challenge_id=? AND registered_model_id=?",
            (challenge_id, model["id"]),
        ).fetchone()
        if not sub:
            raise HTTPException(404, "No submission found")
        result = dict(sub)
        try:
            result["grade_data"] = json.loads(result.pop("grade_json") or "{}")
        except Exception:
            result["grade_data"] = {}
        try:
            result["answer_data"] = json.loads(result.pop("answer_json") or "{}")
        except Exception:
            result["answer_data"] = {}
        return result
    finally:
        conn.close()


# ─── Admin: model approval for daily challenges ───

@app.get("/api/admin/models/pending")
def admin_pending_models(rubricgen_session: str | None = Cookie(default=None)):
    """List models that have opted into daily but not yet been approved."""
    require_admin(rubricgen_session)
    conn = get_db()
    rows = conn.execute(
        """SELECT rm.*, u.display_name AS creator_name, u.email AS creator_email
           FROM registered_models rm
           LEFT JOIN users u ON u.id = rm.created_by
           WHERE rm.active_for_daily = 1 AND rm.daily_admin_approved = 0
           ORDER BY rm.created_at DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/admin/models/{model_id}/approve-daily")
def admin_approve_model(model_id: int, rubricgen_session: str | None = Cookie(default=None)):
    require_admin(rubricgen_session)
    conn = get_db()
    with conn:
        conn.execute(
            "UPDATE registered_models SET daily_admin_approved=1 WHERE id=?",
            (model_id,),
        )
        conn.commit()
    conn.close()
    return {"ok": True, "message": "Model approved for daily challenges"}


@app.post("/api/admin/models/{model_id}/reject-daily")
def admin_reject_model(model_id: int, rubricgen_session: str | None = Cookie(default=None)):
    require_admin(rubricgen_session)
    conn = get_db()
    with conn:
        conn.execute(
            "UPDATE registered_models SET active_for_daily=0, daily_admin_approved=0 WHERE id=?",
            (model_id,),
        )
        conn.commit()
    conn.close()
    return {"ok": True, "message": "Model rejected from daily challenges"}


# ─── User: opt in/out of daily ───

@app.post("/api/models/{model_id}/accept-agreement")
def model_accept_agreement(model_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Accept the Model Publishing Agreement (required before opt-in to daily challenges)."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    row = conn.execute("SELECT created_by FROM registered_models WHERE id=?", (model_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Model not found")
    if row["created_by"] != user["id"]:
        conn.close()
        raise HTTPException(403, "Only the model creator can accept the agreement")
    with conn:
        conn.execute(
            "UPDATE registered_models SET agreement_signed_at=CURRENT_TIMESTAMP WHERE id=?",
            (model_id,),
        )
        conn.commit()
    conn.close()
    return {"ok": True, "message": "Model Publishing Agreement accepted."}


@app.post("/api/models/{model_id}/opt-in-daily")
def model_opt_in_daily(model_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Model owner opts into daily challenges (pending admin approval)."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    row = conn.execute("SELECT created_by, agreement_signed_at FROM registered_models WHERE id=?", (model_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Model not found")
    if row["created_by"] != user["id"]:
        conn.close()
        raise HTTPException(403, "Only the model creator can opt into daily challenges")
    if not row["agreement_signed_at"]:
        conn.close()
        raise HTTPException(400, "Must accept the Model Publishing Agreement before opting into daily challenges")
    with conn:
        conn.execute("UPDATE registered_models SET active_for_daily=1 WHERE id=?", (model_id,))
        conn.commit()
    conn.close()
    return {"ok": True, "status": "pending_approval"}


@app.post("/api/models/{model_id}/opt-out-daily")
def model_opt_out_daily(model_id: int, rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    row = conn.execute("SELECT created_by FROM registered_models WHERE id=?", (model_id,)).fetchone()
    if not row or row["created_by"] != user["id"]:
        conn.close()
        raise HTTPException(403, "Only the model creator can opt out")
    with conn:
        conn.execute(
            "UPDATE registered_models SET active_for_daily=0, daily_admin_approved=0 WHERE id=?",
            (model_id,),
        )
        conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/models/{model_id}/regenerate-key")
def model_regenerate_key(model_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Regenerate the competition API key for a model."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        new_key = mreg.regenerate_api_key(conn, model_id, user["id"])
        return {"ok": True, "model_api_key": new_key}
    finally:
        conn.close()


# ─── Admin: grade external submissions ───

@app.post("/api/admin/challenges/{cid}/grade-submissions")
def admin_grade_submissions(cid: int, rubricgen_session: str | None = Cookie(default=None)):
    """Grade all submitted external model answers for a challenge."""
    require_admin(rubricgen_session)
    conn = get_db()
    try:
        rubric_row = conn.execute(
            "SELECT rubric_json FROM challenge_rubrics WHERE challenge_id=?", (cid,)
        ).fetchone()
        if not rubric_row:
            raise HTTPException(404, "No rubric for this challenge")
        rubric = json.loads(rubric_row["rubric_json"] or "{}")

        subs = conn.execute(
            "SELECT * FROM challenge_submissions WHERE challenge_id=? AND status='submitted'",
            (cid,),
        ).fetchall()
        if not subs:
            return {"ok": True, "graded": 0, "message": "No submissions to grade"}

        from backend.agents.judge import run_judge_agent
        from backend.skills import get_active_skill as _get_skill
        judge_skill = _get_skill(conn, "judge")

        graded_count = 0
        for sub in subs:
            try:
                answers = json.loads(sub["answer_json"] or "{}")
                with conn:
                    conn.execute(
                        "UPDATE challenge_submissions SET status='grading' WHERE id=?",
                        (sub["id"],),
                    )
                    conn.commit()

                grades, judge_ms = run_judge_agent(rubric, answers, judge_skill)

                total = float(grades.get("total_score", 0) or 0)
                max_s = float(grades.get("max_score", 0) or 0)
                accuracy = (total / max_s) if max_s > 0 else 0

                # Points: use daily rate (100 per correct)
                from backend.challenges import DAILY_POINTS_PER_CORRECT
                correct = sum(
                    1 for g in grades.get("grades", [])
                    if g.get("score", 0) >= g.get("max_points", 1)
                )
                pts = correct * DAILY_POINTS_PER_CORRECT

                with conn:
                    conn.execute(
                        """UPDATE challenge_submissions
                           SET grade_json=?, judge_time_ms=?, accuracy=?, points=?, status='graded'
                           WHERE id=?""",
                        (json.dumps(grades), judge_ms, round(accuracy, 4), pts, sub["id"]),
                    )
                    conn.commit()
                graded_count += 1
            except Exception as e:
                logger.error("Grading submission %d failed: %s", sub["id"], e)
                with conn:
                    conn.execute(
                        "UPDATE challenge_submissions SET status='failed' WHERE id=?",
                        (sub["id"],),
                    )
                    conn.commit()

        return {"ok": True, "graded": graded_count}
    finally:
        conn.close()


# ─────────────────────────────────────────────
# Projects (with sharing, admin roles, self-removal)
# ─────────────────────────────────────────────

def _user_project_role(conn, pid: int, user_id: int) -> str | None:
    """Returns 'admin' if project owner, or the role from project_members, or None."""
    proj = conn.execute("SELECT user_id FROM projects WHERE id=?", (pid,)).fetchone()
    if not proj:
        return None
    if proj["user_id"] == user_id:
        return "admin"
    mem = conn.execute(
        "SELECT role FROM project_members WHERE project_id=? AND user_id=?",
        (pid, user_id),
    ).fetchone()
    return mem["role"] if mem else None


class ProjectCreatePayload(BaseModel):
    name: str
    share_emails: Optional[list[str]] = None


class ShareProjectPayload(BaseModel):
    email: str


class TransferAdminPayload(BaseModel):
    new_admin_user_id: int


@app.get("/api/projects")
def list_projects(rubricgen_session: str | None = Cookie(default=None)):
    """List user's own projects + projects shared with them."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    own = conn.execute(
        """SELECT p.id, p.name, p.created_at, 'admin' AS role,
                  (SELECT COUNT(*) FROM challenges c WHERE c.project_id=p.id) AS challenge_count,
                  (SELECT COUNT(*) FROM project_members pm WHERE pm.project_id=p.id) AS member_count
           FROM projects p WHERE p.user_id=? ORDER BY p.created_at""",
        (user["id"],),
    ).fetchall()
    shared = conn.execute(
        """SELECT p.id, p.name, p.created_at, pm.role,
                  u.display_name AS owner_name,
                  (SELECT COUNT(*) FROM challenges c WHERE c.project_id=p.id) AS challenge_count,
                  (SELECT COUNT(*) FROM project_members pm2 WHERE pm2.project_id=p.id) AS member_count
           FROM project_members pm
           JOIN projects p ON p.id = pm.project_id
           JOIN users u ON u.id = p.user_id
           WHERE pm.user_id=? ORDER BY pm.added_at DESC""",
        (user["id"],),
    ).fetchall()
    conn.close()
    return [dict(r) for r in own] + [dict(r) for r in shared]


@app.post("/api/projects", status_code=201)
def create_project(body: ProjectCreatePayload, rubricgen_session: str | None = Cookie(default=None)):
    """Create a project, optionally sharing with team members at creation time."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Project name is required")
    if len(name) > 200:
        raise HTTPException(400, "Project name must be 200 characters or fewer")
    conn = get_db()
    with conn:
        cur = conn.execute("INSERT INTO projects (name, user_id) VALUES (?,?) RETURNING id", (name, user["id"]))
        pid = cur.lastrowid
        conn.commit()
    # Share with provided emails
    shared_results: list[dict] = []
    if body.share_emails:
        for raw_email in body.share_emails:
            email = (raw_email or "").strip().lower()
            if not email:
                continue
            target = conn.execute("SELECT id, display_name FROM users WHERE email=?", (email,)).fetchone()
            if not target:
                shared_results.append({"email": email, "status": "not_found"})
                continue
            if target["id"] == user["id"]:
                continue
            with conn:
                conn.execute(
                    "INSERT INTO project_members (project_id, user_id, role, added_by) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                    (pid, target["id"], "member", user["id"]),
                )
                conn.commit()
            shared_results.append({"email": email, "status": "shared"})
            try:
                _send_email(
                    email,
                    "The AI Researcher — Project shared with you",
                    f"Hi {target['display_name']},\n\n"
                    f"{user['display_name']} has shared the project \"{name}\" with you.\n\n"
                    f"Log in to view it: {APP_BASE_URL}\n\n— The AI Researcher",
                )
            except Exception:
                pass
    conn.close()
    return {"id": pid, "name": name, "shared": shared_results}


@app.patch("/api/projects/{pid}")
def rename_project(pid: int, body: ProjectRename, rubricgen_session: str | None = Cookie(default=None)):
    """Rename a project. Admin only."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    role = _user_project_role(conn, pid, user["id"])
    if role != "admin":
        conn.close()
        raise HTTPException(403, "Only project admins can rename projects")
    if not body.name or not body.name.strip():
        conn.close()
        raise HTTPException(400, "Name is required")
    with conn:
        conn.execute("UPDATE projects SET name=? WHERE id=?", (body.name.strip(), pid))
        conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/projects/{pid}")
def delete_project(pid: int, confirmed: bool = False,
                   rubricgen_session: str | None = Cookie(default=None)):
    """Delete a project. Admin only. Requires confirmed=true query param.
    Returns a warning if not confirmed."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    role = _user_project_role(conn, pid, user["id"])
    if role != "admin":
        conn.close()
        raise HTTPException(403, "Only project admins can delete projects")
    # Count what will be affected
    challenge_count = conn.execute(
        "SELECT COUNT(*) AS c FROM challenges WHERE project_id=?", (pid,)
    ).fetchone()["c"]
    member_count = conn.execute(
        "SELECT COUNT(*) AS c FROM project_members WHERE project_id=?", (pid,)
    ).fetchone()["c"]
    if not confirmed:
        conn.close()
        return {
            "ok": False,
            "warning": True,
            "message": f"This will remove the project and unlink {challenge_count} challenges. {member_count} team members will lose access. Pass confirmed=true to proceed.",
            "challenge_count": challenge_count,
            "member_count": member_count,
        }
    with conn:
        # Unlink challenges (don't delete them, just set project_id=NULL)
        conn.execute("UPDATE challenges SET project_id=NULL WHERE project_id=?", (pid,))
        conn.execute("DELETE FROM project_members WHERE project_id=?", (pid,))
        conn.execute("DELETE FROM projects WHERE id=?", (pid,))
        conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/projects/{pid}/members")
def list_project_members(pid: int, rubricgen_session: str | None = Cookie(default=None)):
    """List members. Any project member or admin can view."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    role = _user_project_role(conn, pid, user["id"])
    if not role:
        conn.close()
        raise HTTPException(403, "You are not a member of this project")
    # Include the project owner as "admin" in the list
    proj = conn.execute("SELECT user_id FROM projects WHERE id=?", (pid,)).fetchone()
    owner = conn.execute(
        "SELECT id AS user_id, email, display_name FROM users WHERE id=?", (proj["user_id"],)
    ).fetchone()
    members = conn.execute(
        """SELECT pm.user_id, u.email, u.display_name, pm.role, pm.added_at
           FROM project_members pm JOIN users u ON u.id = pm.user_id
           WHERE pm.project_id=?""",
        (pid,),
    ).fetchall()
    conn.close()
    result = [dict(owner, role="admin", added_at=None)]  # owner always first
    result.extend(dict(m) for m in members)
    return result


@app.post("/api/projects/{pid}/share")
def share_project(pid: int, body: ShareProjectPayload,
                  rubricgen_session: str | None = Cookie(default=None)):
    """Share a project with a user by email. Admin only."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    role = _user_project_role(conn, pid, user["id"])
    if role != "admin":
        conn.close()
        raise HTTPException(403, "Only project admins can share")
    proj = conn.execute("SELECT name FROM projects WHERE id=?", (pid,)).fetchone()
    email = body.email.strip().lower()
    target = conn.execute("SELECT id, display_name FROM users WHERE email=?", (email,)).fetchone()
    if target and target["id"] == user["id"]:
        conn.close()
        raise HTTPException(400, "Cannot share a project with yourself")
    # All shares go through project_invitations (pending until accepted)
    with conn:
        conn.execute(
            "INSERT INTO project_invitations (project_id, email, invited_by, status) "
            "VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
            (pid, email, user["id"], "pending"),
        )
        conn.commit()
    conn.close()
    email_sent = False
    if target:
        # Registered user — send notification
        try:
            _send_email(
                email,
                "The AI Researcher — Project invitation",
                f"Hi {target['display_name']},\n\n"
                f"{user['display_name']} has invited you to the project \"{proj['name']}\".\n\n"
                f"Log in to accept the invitation: {APP_BASE_URL}\n\n— The AI Researcher",
            )
            email_sent = True
        except Exception as e:
            logger.warning("Email to %s failed: %s", email, e)
        msg = f"Invitation sent to {target['display_name']}"
        if not email_sent:
            msg += " (email notification could not be delivered — they will see it when they log in)"
        return {"ok": True, "status": "invitation_sent", "email_sent": email_sent, "message": msg}
    else:
        # Unregistered user — send registration invite
        try:
            _send_email(
                email,
                "The AI Researcher — You've been invited to a project",
                f"Hi,\n\n"
                f"{user['display_name']} has invited you to the project \"{proj['name']}\" "
                f"on The AI Researcher.\n\n"
                f"Create your free account to get access:\n"
                f"{APP_BASE_URL}/login\n\n"
                f"— The AI Researcher",
            )
            email_sent = True
        except Exception as e:
            logger.warning("Email to %s failed: %s", email, e)
        msg = f"Invitation created for {email}"
        if email_sent:
            msg = f"Registration invitation sent to {email}"
        else:
            msg += " (email could not be delivered — configure SMTP to enable email notifications)"
        return {"ok": True, "status": "invitation_sent", "email_sent": email_sent, "message": msg}


# ─── Project Invitation Management ───

@app.get("/api/projects/invitations")
def api_my_invitations(rubricgen_session: str | None = Cookie(default=None)):
    """List pending invitations for the current user."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT pi.id, pi.project_id, p.name AS project_name,
                      u.display_name AS invited_by_name, u.email AS invited_by_email,
                      pi.created_at
               FROM project_invitations pi
               JOIN projects p ON p.id = pi.project_id
               JOIN users u ON u.id = pi.invited_by
               WHERE pi.email = ? AND pi.status = 'pending'
               ORDER BY pi.created_at DESC""",
            (user["email"],),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.post("/api/projects/invitations/{invitation_id}/accept")
def api_accept_invitation(invitation_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Accept a project invitation."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        inv = conn.execute(
            "SELECT id, project_id, email, invited_by FROM project_invitations WHERE id=? AND status='pending'",
            (invitation_id,),
        ).fetchone()
        if not inv:
            raise HTTPException(404, "Invitation not found or already handled")
        if inv["email"].lower() != user["email"].lower():
            raise HTTPException(403, "This invitation is not for you")
        with conn:
            conn.execute(
                "INSERT INTO project_members (project_id, user_id, role, added_by) "
                "VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                (inv["project_id"], user["id"], "member", inv["invited_by"]),
            )
            conn.execute(
                "UPDATE project_invitations SET status='accepted', accepted_at=CURRENT_TIMESTAMP WHERE id=?",
                (invitation_id,),
            )
            conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/projects/invitations/{invitation_id}/reject")
def api_reject_invitation(invitation_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Reject a project invitation."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        inv = conn.execute(
            "SELECT id, email FROM project_invitations WHERE id=? AND status='pending'",
            (invitation_id,),
        ).fetchone()
        if not inv:
            raise HTTPException(404, "Invitation not found or already handled")
        if inv["email"].lower() != user["email"].lower():
            raise HTTPException(403, "This invitation is not for you")
        with conn:
            conn.execute(
                "UPDATE project_invitations SET status='rejected' WHERE id=?",
                (invitation_id,),
            )
            conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/projects/{pid}/invitations")
def api_project_invitations(pid: int, rubricgen_session: str | None = Cookie(default=None)):
    """List pending invitations for a project (owner view)."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        role = _user_project_role(conn, pid, user["id"])
        if role != "admin":
            raise HTTPException(403, "Only project admins can view invitations")
        rows = conn.execute(
            """SELECT pi.id, pi.email, pi.status, pi.created_at,
                      u.display_name AS invited_by_name
               FROM project_invitations pi
               JOIN users u ON u.id = pi.invited_by
               WHERE pi.project_id = ? AND pi.status = 'pending'
               ORDER BY pi.created_at DESC""",
            (pid,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/users/lookup")
def api_user_lookup(email: str, rubricgen_session: str | None = Cookie(default=None)):
    """Check if an email belongs to a registered user."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    email = email.strip().lower()
    conn = get_db()
    row = conn.execute(
        "SELECT id, display_name, avatar_path FROM users WHERE email=?", (email,)
    ).fetchone()
    conn.close()
    if not row:
        return {"registered": False}
    avatar_url = f"/api/auth/avatar/{row['id']}" if row["avatar_path"] else None
    return {"registered": True, "display_name": row["display_name"], "avatar_url": avatar_url}


@app.post("/api/projects/{pid}/transfer-admin")
def transfer_project_admin(pid: int, body: TransferAdminPayload,
                           rubricgen_session: str | None = Cookie(default=None)):
    """Transfer project admin to another member. Current admin only.
    The current admin becomes a regular member."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    proj = conn.execute("SELECT user_id FROM projects WHERE id=?", (pid,)).fetchone()
    if not proj:
        conn.close()
        raise HTTPException(404, "Project not found")
    if proj["user_id"] != user["id"]:
        conn.close()
        raise HTTPException(403, "Only the project owner can transfer admin")
    # Verify new admin is a member
    mem = conn.execute(
        "SELECT user_id FROM project_members WHERE project_id=? AND user_id=?",
        (pid, body.new_admin_user_id),
    ).fetchone()
    if not mem:
        conn.close()
        raise HTTPException(400, "Target user is not a member of this project")
    with conn:
        # Transfer ownership: change projects.user_id
        conn.execute("UPDATE projects SET user_id=? WHERE id=?", (body.new_admin_user_id, pid))
        # Remove new admin from project_members (they're now the owner)
        conn.execute("DELETE FROM project_members WHERE project_id=? AND user_id=?",
                     (pid, body.new_admin_user_id))
        # Add old admin as a regular member
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, role, added_by) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
            (pid, user["id"], "member", body.new_admin_user_id),
        )
        conn.commit()
    conn.close()
    return {"ok": True, "message": "Admin transferred successfully"}


@app.post("/api/projects/{pid}/leave")
def leave_project(pid: int, rubricgen_session: str | None = Cookie(default=None)):
    """Remove yourself from a shared project. Non-admin members only.
    Admins must transfer admin first."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    proj = conn.execute("SELECT user_id FROM projects WHERE id=?", (pid,)).fetchone()
    if not proj:
        conn.close()
        raise HTTPException(404, "Project not found")
    if proj["user_id"] == user["id"]:
        conn.close()
        raise HTTPException(400, "Project admins cannot leave. Transfer admin to another member first, or delete the project.")
    mem = conn.execute(
        "SELECT user_id FROM project_members WHERE project_id=? AND user_id=?",
        (pid, user["id"]),
    ).fetchone()
    if not mem:
        conn.close()
        raise HTTPException(400, "You are not a member of this project")
    with conn:
        conn.execute("DELETE FROM project_members WHERE project_id=? AND user_id=?",
                     (pid, user["id"]))
        conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/projects/{pid}/members/{member_user_id}")
def remove_project_member(pid: int, member_user_id: int,
                          rubricgen_session: str | None = Cookie(default=None)):
    """Remove a member from a project. Admin only."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    role = _user_project_role(conn, pid, user["id"])
    if role != "admin":
        conn.close()
        raise HTTPException(403, "Only project admins can remove members")
    with conn:
        conn.execute("DELETE FROM project_members WHERE project_id=? AND user_id=?",
                     (pid, member_user_id))
        conn.commit()
    conn.close()
    return {"ok": True}


# ─────────────────────────────────────────────
# Papers
# ─────────────────────────────────────────────
@app.get("/api/papers")
def list_papers(rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    rows = conn.execute(
        """SELECT p.id, p.filename, p.sha256, p.project_id, p.created_at,
                  (SELECT COUNT(*) FROM rubrics r WHERE r.paper_id=p.id AND r.user_id=?) AS rubric_count,
                  (SELECT COUNT(*) FROM evaluations e WHERE e.paper_id=p.id AND e.user_id=?) AS eval_count
           FROM papers p
           WHERE p.user_id=?
           ORDER BY p.created_at DESC""",
        (user["id"], user["id"], user["id"]),
    ).fetchall()
    out = [dict(r) for r in rows]
    if out:
        # Attach multi-project membership in one round-trip.
        pp_rows = conn.execute(
            """SELECT pp.paper_id, pp.project_id
                 FROM paper_projects pp
                 JOIN papers p ON p.id = pp.paper_id
                WHERE p.user_id = ?""",
            (user["id"],),
        ).fetchall()
        by_pid: dict[int, list[int]] = {}
        for r in pp_rows:
            by_pid.setdefault(r["paper_id"], []).append(r["project_id"])
        for d in out:
            d["project_ids"] = by_pid.get(d["id"], [])
    conn.close()
    return out


@app.post("/api/papers/upload", status_code=201)
async def upload_paper(
    file: UploadFile = File(...),
    project_id: int | None = None,
    rubricgen_session: str | None = Cookie(default=None),
):
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(413, "File too large. Maximum size is 50 MB.")

    # Legacy membership PDF limit — enforced only while the enterprise-seat
    # model is still dark. When ENTERPRISE_MODE=1, the seat check above has
    # already authorized the upload and org credits are the new resource gate.
    conn = get_db()
    try:
        if not enterprise_mod.ENTERPRISE_MODE:
            pdf_status = member_mod.check_pdf_limit(conn, user["id"])
            if not pdf_status["allowed"]:
                raise HTTPException(403, {
                    "detail": f"PDF upload limit reached ({pdf_status['used']}/{pdf_status['limit']}). Upgrade your membership to upload more.",
                    "pdf_status": pdf_status,
                })

        sha256 = hashlib.sha256(content).hexdigest()
        existing = conn.execute(
            "SELECT id FROM papers WHERE sha256=? AND user_id=?", (sha256, user["id"])
        ).fetchone()
        if existing:
            return {"id": existing["id"], "duplicate": True}
        # Persist via storage.py (S3 when configured, local uploads/ otherwise).
        # write_paper_file handles S3 failures internally by falling back to local,
        # so by the time we get here we have *some* path on disk or in S3.
        try:
            storage_path = paper_files_mod.write_paper_file(content, file.filename)
        except Exception as e:
            logger.exception("Paper upload: write_paper_file crashed for %s", file.filename)
            raise HTTPException(502, f"Upload failed while writing to storage: {e}")
        disk_name = f"{sha256}.pdf"  # kept for backwards-compat display/debug
        try:
            with conn:
                cur = conn.execute(
                    "INSERT INTO papers (filename, disk_filename, storage_path, sha256, user_id, project_id, source) "
                    "VALUES (?,?,?,?,?,?, 'upload') RETURNING id",
                    (file.filename, disk_name, storage_path, sha256, user["id"], project_id),
                )
                conn.commit()
            pid = cur.lastrowid
        except Exception as e:
            logger.exception("Paper upload: DB insert failed for %s", file.filename)
            raise HTTPException(500, f"Upload stored but DB insert failed: {e}")
        if not enterprise_mod.ENTERPRISE_MODE:
            # Legacy per-user PDF counter — unused when seats are in play.
            try:
                member_mod.increment_pdf_count(conn, user["id"])
            except Exception:
                logger.exception("Paper upload: PDF count increment failed — continuing")
        return {"id": pid, "filename": file.filename, "duplicate": False,
                "storage": "s3" if storage_path.startswith("s3://") else "local"}
    finally:
        conn.close()


@app.put("/api/papers/{pid}/file")
async def replace_paper_file(
    pid: int,
    file: UploadFile = File(...),
    rubricgen_session: str | None = Cookie(default=None),
):
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(413, "File too large. Maximum size is 50 MB.")
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Only .pdf files are accepted.")

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, filename, disk_filename, storage_path "
            "FROM papers WHERE id=? AND user_id=?",
            (pid, user["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Paper not found.")

        try:
            new_storage_path = paper_files_mod.write_paper_file(content, file.filename)
        except Exception as e:
            logger.exception("Re-upload: write_paper_file crashed for paper %s", pid)
            raise HTTPException(502, f"Re-upload failed while writing to storage: {e}")

        sha256 = hashlib.sha256(content).hexdigest()
        disk_name = f"{sha256}.pdf"
        with conn:
            conn.execute(
                "UPDATE papers SET filename=?, disk_filename=?, storage_path=?, sha256=? "
                "WHERE id=? AND user_id=?",
                (file.filename, disk_name, new_storage_path, sha256, pid, user["id"]),
            )
            conn.commit()

        try:
            paper_files_mod.delete_paper_file(row, PAPERS_DIR)
        except Exception:
            logger.warning("Re-upload: old file cleanup failed for paper %s", pid, exc_info=True)

        return {
            "id": pid,
            "filename": file.filename,
            "storage": "s3" if new_storage_path.startswith("s3://") else "local",
        }
    finally:
        conn.close()


@app.patch("/api/papers/{pid}/assign")
def assign_paper(pid: int, body: PaperAssign, rubricgen_session: str | None = Cookie(default=None)):
    """Set the paper's *primary* project (the legacy single-FK pointer).
    Multi-project membership lives in ``paper_projects`` — see the dedicated
    POST/DELETE /api/papers/{pid}/projects/{project_id} endpoints. This route
    is kept for back-compat; new code should use the junction endpoints.
    """
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        # Ownership check.
        own = conn.execute(
            "SELECT id FROM papers WHERE id=? AND user_id=?",
            (pid, user["id"]),
        ).fetchone()
        if not own:
            raise HTTPException(404, "paper not found")
        with conn:
            conn.execute(
                "UPDATE papers SET project_id=? WHERE id=? AND user_id=?",
                (body.project_id, pid, user["id"]),
            )
            # Mirror to the junction so the multi-project view stays consistent.
            if body.project_id is not None:
                conn.execute(
                    """INSERT INTO paper_projects (paper_id, project_id)
                       VALUES (?, ?) ON CONFLICT DO NOTHING""",
                    (pid, body.project_id),
                )
            conn.commit()
    finally:
        conn.close()
    return {"ok": True}


# Accept POST too, for back-compat with frontend code that hasn't switched to PATCH yet.
# (annotator.html:assignPaperToProject historically posted here even though the route was PATCH-only.)
@app.post("/api/papers/{pid}/assign")
def assign_paper_post(pid: int, body: PaperAssign,
                      rubricgen_session: str | None = Cookie(default=None)):
    return assign_paper(pid, body, rubricgen_session)


@app.get("/api/papers/{pid}/projects")
def list_paper_projects(pid: int,
                        rubricgen_session: str | None = Cookie(default=None),
                        x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Return the project IDs this paper currently belongs to."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        own = conn.execute(
            "SELECT id, project_id FROM papers WHERE id=? AND user_id=?",
            (pid, user["id"]),
        ).fetchone()
        if not own:
            raise HTTPException(404, "paper not found")
        rows = conn.execute(
            """SELECT pp.project_id, p.name
                 FROM paper_projects pp
            LEFT JOIN projects p ON p.id = pp.project_id
                WHERE pp.paper_id = ?
                ORDER BY p.name ASC""",
            (pid,),
        ).fetchall()
    finally:
        conn.close()
    return {
        "paper_id": pid,
        "primary_project_id": own["project_id"],
        "projects": [{"id": r["project_id"], "name": r["name"]} for r in rows],
    }


@app.post("/api/papers/{pid}/projects/{project_id}", status_code=201)
def add_paper_to_project(pid: int, project_id: int,
                         rubricgen_session: str | None = Cookie(default=None),
                         x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Add this paper to a project. Idempotent — re-adding is a no-op."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        # Joint ownership check — both sides belong to the user.
        paper = conn.execute(
            "SELECT id, project_id FROM papers WHERE id=? AND user_id=?",
            (pid, user["id"]),
        ).fetchone()
        if not paper:
            raise HTTPException(404, "paper not found")
        proj = conn.execute(
            "SELECT id FROM projects WHERE id=? AND user_id=?",
            (project_id, user["id"]),
        ).fetchone()
        if not proj:
            raise HTTPException(404, "project not found")
        with conn:
            conn.execute(
                """INSERT INTO paper_projects (paper_id, project_id)
                   VALUES (?, ?) ON CONFLICT DO NOTHING""",
                (pid, project_id),
            )
            # If the paper has no primary yet, set this one so legacy single-project
            # consumers still see it grouped somewhere.
            if paper["project_id"] is None:
                conn.execute(
                    "UPDATE papers SET project_id=? WHERE id=?",
                    (project_id, pid),
                )
            conn.commit()
    finally:
        conn.close()
    return {"ok": True, "paper_id": pid, "project_id": project_id}


@app.delete("/api/papers/{pid}/projects/{project_id}")
def remove_paper_from_project(pid: int, project_id: int,
                              rubricgen_session: str | None = Cookie(default=None),
                              x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Remove this paper from a project. The paper itself is untouched."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        paper = conn.execute(
            "SELECT id, project_id FROM papers WHERE id=? AND user_id=?",
            (pid, user["id"]),
        ).fetchone()
        if not paper:
            raise HTTPException(404, "paper not found")
        with conn:
            conn.execute(
                "DELETE FROM paper_projects WHERE paper_id=? AND project_id=?",
                (pid, project_id),
            )
            # If the removed project was the primary, promote any remaining
            # membership (else NULL the primary).
            if paper["project_id"] == project_id:
                next_row = conn.execute(
                    "SELECT project_id FROM paper_projects WHERE paper_id=? ORDER BY added_at ASC LIMIT 1",
                    (pid,),
                ).fetchone()
                conn.execute(
                    "UPDATE papers SET project_id=? WHERE id=?",
                    (next_row["project_id"] if next_row else None, pid),
                )
            conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.get("/api/library/papers")
def api_library_papers(project: str | None = None,
                       source: str | None = None,
                       status: str | None = None,
                       q: str | None = None,
                       limit: int = 200,
                       rubricgen_session: str | None = Cookie(default=None),
                       x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Aggregated personal-library view. One row per paper with multi-project
    membership, source, annotation status, challenge count, and run count.

    Filters (all optional, AND-combined):
      - project=ID  → only papers in this project (junction OR primary). 'unassigned' for none.
      - source=upload|lab|search|pubmed|imported  → reserved; honored once papers.source ships.
      - status=annotated|unannotated|in_progress  → annotation state.
      - q=foo  → case-insensitive substring on filename.
    """
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    try:
        limit = max(1, min(int(limit), 1000))
    except Exception:
        limit = 200
    conn = get_db()
    try:
        # Aggregated paper list. Counts via correlated subqueries — fine for
        # personal libraries up to a few thousand papers.
        rows = conn.execute(
            f"""SELECT p.id, p.filename, p.sha256, p.project_id,
                       p.created_at, p.source, p.external_url, p.pdf_status,
                       a.status     AS ann_status,
                       a.updated_at AS ann_updated_at,
                       (SELECT COUNT(*) FROM rubrics r       WHERE r.paper_id=p.id AND r.user_id=?) AS rubric_count,
                       (SELECT COUNT(*) FROM evaluations e   WHERE e.paper_id=p.id AND e.user_id=?) AS eval_count,
                       (SELECT COUNT(*) FROM challenge_papers cp WHERE cp.paper_id=p.id) AS challenge_count
                  FROM papers p
             LEFT JOIN annotations a
                    ON a.paper_id = p.id AND a.reviewer_id = ?
                 WHERE p.user_id = ?
              ORDER BY p.created_at DESC""",
            (user["id"], user["id"], user["id"], user["id"]),
        ).fetchall()
        # Junction memberships in one round-trip.
        pp_rows = conn.execute(
            """SELECT pp.paper_id, pp.project_id, prj.name
                 FROM paper_projects pp
            LEFT JOIN projects prj ON prj.id = pp.project_id
                 JOIN papers p     ON p.id  = pp.paper_id
                WHERE p.user_id = ?""",
            (user["id"],),
        ).fetchall()
        # All projects for label lookup on the legacy primary FK.
        proj_rows = conn.execute(
            "SELECT id, name FROM projects WHERE user_id=? ORDER BY name ASC",
            (user["id"],),
        ).fetchall()
        # Custom-run participation: count runs whose paper_ids_json includes this paper.
        # Pulled in Python to avoid PG/SQLite JSON-fn portability mess.
        run_rows = conn.execute(
            "SELECT id, paper_ids_json FROM annotator_custom_runs WHERE user_id=?",
            (user["id"],),
        ).fetchall()
    finally:
        conn.close()

    proj_lookup = {r["id"]: r["name"] for r in proj_rows}
    junctions: dict[int, list[dict]] = {}
    for r in pp_rows:
        junctions.setdefault(r["paper_id"], []).append(
            {"id": r["project_id"], "name": r["name"]}
        )

    run_count_by_pid: dict[int, int] = {}
    for r in run_rows:
        try:
            for pid in json.loads(r["paper_ids_json"] or "[]"):
                run_count_by_pid[int(pid)] = run_count_by_pid.get(int(pid), 0) + 1
        except Exception:
            continue

    # Build the result list with normalized projects + filter application.
    q_lower = (q or "").strip().lower()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        membership = junctions.get(d["id"], [])
        # Always include the legacy primary FK if it isn't already in the junction
        # (covers freshly-uploaded papers before backfill catches them).
        if d.get("project_id") and not any(m["id"] == d["project_id"] for m in membership):
            membership.append({"id": d["project_id"], "name": proj_lookup.get(d["project_id"], f"#{d['project_id']}")})
        d["projects"] = membership
        d["project_ids"] = [m["id"] for m in membership]
        d["custom_run_count"] = run_count_by_pid.get(d["id"], 0)
        d["source"] = d.get("source") or "upload"
        d["pdf_status"] = d.get("pdf_status") or "present"
        d["external_url"] = d.get("external_url")
        # Filters
        if q_lower and q_lower not in (d["filename"] or "").lower():
            continue
        if project:
            if project == "unassigned":
                if membership:
                    continue
            else:
                try:
                    pid_filter = int(project)
                except ValueError:
                    continue
                if pid_filter not in d["project_ids"]:
                    continue
        if source and source != d["source"]:
            continue
        if status:
            ann = d.get("ann_status")
            if status == "annotated" and ann != "complete":
                continue
            if status == "unannotated" and ann not in (None, ""):
                continue
            if status == "in_progress" and ann != "in_progress":
                continue
        out.append(d)
        if len(out) >= limit:
            break

    return {
        "papers": out,
        "projects": [{"id": p["id"], "name": p["name"]} for p in proj_rows],
        "total": len(out),
    }


@app.delete("/api/papers/{pid}")
def delete_paper(pid: int, rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    row = conn.execute(
        "SELECT filename, disk_filename, storage_path FROM papers WHERE id=? AND user_id=?",
        (pid, user["id"]),
    ).fetchone()
    if row:
        paper_files_mod.delete_paper_file(row, PAPERS_DIR)
    with conn:
        conn.execute("DELETE FROM papers WHERE id=? AND user_id=?", (pid, user["id"]))
        conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/papers/{pid}/pdf")
def get_pdf(pid: int, rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    row = conn.execute(
        "SELECT filename, disk_filename, storage_path FROM papers WHERE id=? AND user_id=?",
        (pid, user["id"]),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Paper not found")
    data = paper_files_mod.read_paper_bytes(row, PAPERS_DIR)
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{row["filename"]}"'})


# ─────────────────────────────────────────────
# Rubric helpers
# ─────────────────────────────────────────────

RUBRIC_TYPE_PROMPTS = {
    "classification": """You are an expert in clinical research methodology. Your task is to generate a rigorous evaluation rubric for classifying a clinical research study.

Generate 8–12 questions that test a reader's (or LLM's) ability to correctly identify and justify the study design according to the OGAI taxonomy. Cover:
- Major category (Primary Study / Evidence Synthesis / Guidance / Economic Model)
- Study subcategory and specific study type
- Research question type (etiologic, prognostic, diagnostic, interventional)
- Key methodological features that determine classification (allocation, comparator, timing)
- Author-stated design vs. methodology-driven classification
- Applicable primacy gate rules (Rule 1: Diagnostic, Rule 2: Prediction Model, Rule 2b: Prognostic Factor, Rule 3: Quasi-experimental)
- Risk of bias tool that applies to this study type
""",
    "extraction": """You are an expert in clinical research data extraction. Generate a rigorous evaluation rubric for structured data extraction from a clinical research paper.

Generate 10–15 questions covering PICO/PICOS elements:
- Population (setting, eligibility, sample size, key baseline characteristics)
- Intervention (name, dose, duration, comparator)
- Outcomes (primary, secondary, follow-up duration, measurement instruments)
- Study design details (randomization, blinding, allocation concealment)
- Key results (effect estimates, confidence intervals, p-values for primary outcome)
- Risk of bias indicators present in the paper
""",
    "risk_of_bias": """You are an expert in risk of bias assessment for systematic reviews. Generate a rigorous evaluation rubric for assessing risk of bias in a clinical research paper.

Generate 10–14 questions covering relevant bias domains based on the study type:
- Selection bias / confounding
- Performance bias / deviations from intended intervention
- Detection bias / outcome measurement
- Attrition bias / missing data
- Reporting bias / selective reporting
- Overall risk of bias judgment with justification
Tailor questions to the specific RoB tool that applies (RoB 2, ROBINS-I, QUADAS-2, PROBAST, QUIPS, etc.)
""",
    "custom": """You are an expert in clinical research appraisal. Generate a rigorous evaluation rubric based on the specific instructions provided."""
}


def _pdf_to_base64(paper_id: int) -> str:
    """Read a PDF from storage (S3 or legacy local) and return base64."""
    conn = get_db()
    row = conn.execute(
        "SELECT filename, disk_filename, storage_path FROM papers WHERE id=?",
        (paper_id,),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Paper not found")
    data = paper_files_mod.read_paper_bytes(row, PAPERS_DIR)
    return base64.b64encode(data).decode()


# ─────────────────────────────────────────────
# Rubric routes
# ─────────────────────────────────────────────
@app.post("/api/rubrics/generate")
async def generate_rubric(body: GenerateRubricRequest, rubricgen_session: str | None = Cookie(default=None)):
    """Use Claude to generate a structured rubric from a PDF."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    if body.instructions and len(body.instructions) > 5000:
        raise HTTPException(400, "Instructions must be 5000 characters or fewer")

    type_prompt = RUBRIC_TYPE_PROMPTS.get(body.rubric_type, RUBRIC_TYPE_PROMPTS["custom"])
    if body.instructions:
        type_prompt += f"\n\nAdditional instructions from the user:\n{body.instructions}"

    system_prompt = type_prompt + """

CRITICAL OUTPUT FORMAT — respond ONLY with a valid JSON object, no preamble, no markdown fences. The JSON must follow this exact schema:

{
  "rubric_type": "<classification|extraction|risk_of_bias|custom>",
  "title": "<short descriptive title>",
  "total_max_points": <integer>,
  "questions": [
    {
      "id": "q1",
      "domain": "<domain label, e.g. Study Design, Population, Outcomes>",
      "question": "<the question to be answered>",
      "ideal_answer": "<comprehensive ideal answer based on this specific paper>",
      "scoring_criteria": "<explicit criteria for 0, partial, and full credit>",
      "max_points": <integer 1-5>
    }
  ]
}

Make ideal_answer specific to this exact paper's content — include actual values, names, numbers from the paper. Make scoring_criteria unambiguous and gradeable."""

    b64 = _pdf_to_base64(body.paper_id)
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
                },
                {
                    "type": "text",
                    "text": "Generate the evaluation rubric for this clinical research paper as instructed. Respond only with the JSON object.",
                },
            ],
        }
    ]

    raw = _call_anthropic(messages, system=system_prompt, max_tokens=4096)

    raw = _strip_markdown_fences(raw)

    try:
        rubric = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("JSON parse error: %s | raw: %s", e, raw[:300])
        raise HTTPException(500, "Rubric generation returned invalid JSON. Please try again.")

    # Recalculate total
    rubric["total_max_points"] = sum(q.get("max_points", 0) for q in rubric.get("questions", []))

    # Upsert rubric in DB (one rubric per paper per user per type)
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM rubrics WHERE paper_id=? AND user_id=? AND rubric_type=?",
        (body.paper_id, user["id"], body.rubric_type),
    ).fetchone()
    with conn:
        if existing:
            conn.execute(
                "UPDATE rubrics SET rubric_json=?, instructions=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (json.dumps(rubric), body.instructions, existing["id"]),
            )
            rubric_id = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO rubrics (paper_id, user_id, rubric_type, rubric_json, instructions) VALUES (?,?,?,?,?) RETURNING id",
                (body.paper_id, user["id"], body.rubric_type, json.dumps(rubric), body.instructions),
            )
            rubric_id = cur.lastrowid
        conn.commit()
    conn.close()

    return {"rubric_id": rubric_id, "rubric": rubric}


@app.get("/api/rubrics")
def list_rubrics(paper_id: int, rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    rows = conn.execute(
        "SELECT id, rubric_type, rubric_json, instructions, updated_at FROM rubrics WHERE paper_id=? AND user_id=? ORDER BY updated_at DESC",
        (paper_id, user["id"]),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["rubric"] = json.loads(d.pop("rubric_json"))
        except Exception:
            d["rubric"] = {}
        result.append(d)
    return result


@app.put("/api/rubrics/{rid}")
def save_rubric(rid: int, body: SaveRubricRequest, rubricgen_session: str | None = Cookie(default=None)):
    """Save/update a manually edited rubric."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    with conn:
        conn.execute(
            "UPDATE rubrics SET rubric_json=?, instructions=?, rubric_type=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",
            (json.dumps(body.rubric_json), body.instructions, body.rubric_type, rid, user["id"]),
        )
        conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/rubrics/{rid}")
def delete_rubric(rid: int, rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    with conn:
        conn.execute("DELETE FROM rubrics WHERE id=? AND user_id=?", (rid, user["id"]))
        conn.commit()
    conn.close()
    return {"ok": True}


# ─────────────────────────────────────────────
# Evaluation routes
# ─────────────────────────────────────────────
@app.post("/api/evaluations/run")
def run_evaluation(body: RunEvaluationRequest, rubricgen_session: str | None = Cookie(default=None)):
    """Send rubric questions to the specified LLM and collect answers."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")

    # Fetch rubric
    conn = get_db()
    rub_row = conn.execute(
        "SELECT rubric_json FROM rubrics WHERE id=? AND user_id=?", (body.rubric_id, user["id"])
    ).fetchone()
    conn.close()
    if not rub_row:
        raise HTTPException(404, "Rubric not found")
    rubric = json.loads(rub_row["rubric_json"])

    questions = rubric.get("questions", [])
    if not questions:
        raise HTTPException(400, "Rubric has no questions")

    # Get PDF as base64 for OpenAI vision
    b64 = _pdf_to_base64(body.paper_id)

    # Build question list
    q_block = "\n\n".join(
        f"Question {i+1} (ID: {q['id']}, Domain: {q.get('domain','')}, Max points: {q.get('max_points',1)}):\n{q['question']}"
        for i, q in enumerate(questions)
    )

    system_msg = """You are a clinical research expert answering questions about a research paper. 
Answer each question thoroughly and specifically, citing evidence from the paper where relevant.
Be precise about numbers, names, and methodology. Format your response as JSON only:

{
  "responses": [
    {
      "question_id": "q1",
      "answer": "<your detailed answer>"
    }
  ]
}

Respond ONLY with the JSON object, no preamble."""

    user_msg = f"Please answer all of the following questions about this research paper:\n\n{q_block}\n\nRespond with the JSON object as instructed."

    model = body.eval_model
    if model.startswith("gemini"):
        # Google Gemini — natively supports inline PDF
        raw = _call_gemini(
            system=system_msg, user_text=user_msg, model=model,
            pdf_b64=b64, max_tokens=4096,
        )
    elif model.startswith("gpt-"):
        # OpenAI — PDF base64 not natively supported; send text prompt only
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]
        raw = _call_openai(messages, model=model, max_tokens=4096)
    else:
        # Claude (with native PDF support)
        claude_messages = [
            {
                "role": "user",
                "content": [
                    {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}},
                    {"type": "text", "text": user_msg},
                ],
            }
        ]
        raw = _call_anthropic(claude_messages, system=system_msg, max_tokens=4096)

    raw = _strip_markdown_fences(raw)

    try:
        eval_data = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: wrap raw text
        eval_data = {"responses": [{"question_id": q["id"], "answer": raw} for q in questions]}

    # Create evaluation record
    conn = get_db()
    with conn:
        cur = conn.execute(
            "INSERT INTO evaluations (rubric_id, paper_id, user_id, eval_model, eval_json, status) VALUES (?,?,?,?,?,?) RETURNING id",
            (body.rubric_id, body.paper_id, user["id"], model, json.dumps(eval_data), "evaluated"),
        )
        eval_id = cur.lastrowid
        conn.commit()
    conn.close()

    return {"evaluation_id": eval_id, "eval_data": eval_data}


@app.post("/api/evaluations/{eid}/grade")
def grade_evaluation(eid: int, rubricgen_session: str | None = Cookie(default=None)):
    """Use Claude as judge to grade the LLM's answers against the rubric."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")

    conn = get_db()
    eval_row = conn.execute(
        "SELECT e.*, r.rubric_json FROM evaluations e JOIN rubrics r ON r.id=e.rubric_id WHERE e.id=? AND e.user_id=?",
        (eid, user["id"]),
    ).fetchone()
    conn.close()

    if not eval_row:
        raise HTTPException(404, "Evaluation not found")

    rubric   = json.loads(eval_row["rubric_json"])
    eval_data = json.loads(eval_row["eval_json"])

    questions = {q["id"]: q for q in rubric.get("questions", [])}
    responses = {r["question_id"]: r["answer"] for r in eval_data.get("responses", [])}

    # Build grading prompt
    grading_items = []
    for qid, q in questions.items():
        ans = responses.get(qid, "(no answer provided)")
        grading_items.append({
            "question_id": qid,
            "domain": q.get("domain", ""),
            "question": q["question"],
            "ideal_answer": q.get("ideal_answer", ""),
            "scoring_criteria": q.get("scoring_criteria", ""),
            "max_points": q.get("max_points", 1),
            "llm_answer": ans,
        })

    system_prompt = """You are an expert grader for clinical research evaluation rubrics. 
For each question, compare the LLM's answer against the ideal answer and scoring criteria. 
Assign a score from 0 to max_points based strictly on the scoring criteria.
Be rigorous and consistent. Partial credit is permitted where the criteria allow.

Respond ONLY with this JSON structure, no preamble:

{
  "grades": [
    {
      "question_id": "q1",
      "score": <number>,
      "max_points": <number>,
      "reasoning": "<brief explanation of why this score was awarded>"
    }
  ],
  "total_score": <sum of scores>,
  "max_score": <sum of max_points>,
  "percentage": <total_score/max_score*100 rounded to 1 decimal>,
  "overall_comments": "<brief overall assessment>"
}"""

    user_msg = f"Grade these evaluation responses:\n\n{json.dumps(grading_items, indent=2)}"

    raw = _call_anthropic([{"role": "user", "content": user_msg}], system=system_prompt, max_tokens=4096)

    raw = _strip_markdown_fences(raw)

    try:
        graded = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(500, "Grading returned invalid JSON. Please retry.")

    total   = graded.get("total_score", 0)
    max_s   = graded.get("max_score", 0)

    conn = get_db()
    with conn:
        conn.execute(
            "UPDATE evaluations SET graded_json=?, total_score=?, max_score=?, status=? WHERE id=?",
            (json.dumps(graded), total, max_s, "graded", eid),
        )
        conn.commit()
    conn.close()

    return {"graded": graded}


@app.get("/api/evaluations")
def list_evaluations(paper_id: int, rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    rows = conn.execute(
        """SELECT e.id, e.rubric_id, e.eval_model, e.total_score, e.max_score, e.status, e.created_at,
                  r.rubric_type, r.rubric_json
           FROM evaluations e JOIN rubrics r ON r.id=e.rubric_id
           WHERE e.paper_id=? AND e.user_id=?
           ORDER BY e.created_at DESC""",
        (paper_id, user["id"]),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        try:
            rub = json.loads(d.pop("rubric_json", "{}"))
            d["rubric_title"] = rub.get("title", d.get("rubric_type", ""))
        except Exception:
            d["rubric_title"] = ""
        result.append(d)
    return result


@app.get("/api/evaluations/{eid}")
def get_evaluation(eid: int, rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    row = conn.execute(
        """SELECT e.*, r.rubric_json
           FROM evaluations e JOIN rubrics r ON r.id=e.rubric_id
           WHERE e.id=? AND e.user_id=?""",
        (eid, user["id"]),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Evaluation not found")
    d = dict(row)
    try:
        d["eval_data"]  = json.loads(d.pop("eval_json",   "{}"))
        d["grade_data"] = json.loads(d.pop("graded_json", "{}"))
        d["rubric"]     = json.loads(d.pop("rubric_json", "{}"))
    except Exception:
        pass
    return d


@app.get("/api/evaluations/{eid}/export")
def export_evaluation(eid: int, rubricgen_session: str | None = Cookie(default=None)):
    """Export evaluation results as CSV."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    row = conn.execute(
        """SELECT e.*, r.rubric_json
           FROM evaluations e JOIN rubrics r ON r.id=e.rubric_id
           WHERE e.id=? AND e.user_id=?""",
        (eid, user["id"]),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)

    rubric    = json.loads(row["rubric_json"])
    eval_data = json.loads(row["eval_json"])
    graded    = json.loads(row["graded_json"]) if row["graded_json"] else {}

    questions  = {q["id"]: q for q in rubric.get("questions", [])}
    responses  = {r["question_id"]: r["answer"] for r in eval_data.get("responses", [])}
    grades_map = {g["question_id"]: g for g in graded.get("grades", [])}

    import io, csv
    buf = io.StringIO()
    w   = csv.writer(buf)
    w.writerow(["question_id", "domain", "question", "ideal_answer", "scoring_criteria",
                "max_points", "llm_model", "llm_answer", "score", "grade_reasoning"])
    for qid, q in questions.items():
        g = grades_map.get(qid, {})
        w.writerow([
            qid, q.get("domain", ""), q["question"], q.get("ideal_answer", ""),
            q.get("scoring_criteria", ""), q.get("max_points", ""),
            row["eval_model"], responses.get(qid, ""),
            g.get("score", ""), g.get("reasoning", ""),
        ])
    csv_bytes = buf.getvalue().encode()
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=eval_{eid}.csv"},
    )


# ─────────────────────────────────────────────
# Phase 7: Organizations API
# ─────────────────────────────────────────────

class OrgCreatePayload(BaseModel):
    name: str
    description: str = ""
    domain: str = ""

class OrgUpdatePayload(BaseModel):
    name: str | None = None
    description: str | None = None
    domain: str | None = None

class OrgMemberPayload(BaseModel):
    email: str
    role: str = "general"

class OrgMemberRolePayload(BaseModel):
    role: str

class OrgJoinPayload(BaseModel):
    invite_code: str

class OrgTransferPayload(BaseModel):
    amount: int


@app.post("/api/orgs")
def api_create_org(body: OrgCreatePayload, rubricgen_session: str | None = Cookie(default=None)):
    """Create a new organization."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        return org_mod.create_organization(conn, user["id"], body.name, body.description, body.domain)
    finally:
        conn.close()


@app.get("/api/orgs")
def api_list_orgs(rubricgen_session: str | None = Cookie(default=None)):
    """List organizations the current user belongs to."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        return org_mod.list_user_organizations(conn, user["id"])
    finally:
        conn.close()


@app.get("/api/orgs/{org_id}")
def api_get_org(org_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Get organization details. Requires viewer+ role."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        org_mod.require_org_role(conn, org_id, user["id"], "general")
        return org_mod.get_organization(conn, org_id)
    finally:
        conn.close()


@app.patch("/api/orgs/{org_id}")
def api_update_org(org_id: int, body: OrgUpdatePayload, rubricgen_session: str | None = Cookie(default=None)):
    """Update organization settings. Requires admin role."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "admin")
    conn = get_db()
    try:
        return org_mod.update_organization(conn, org_id, user["id"], body.name, body.description, body.domain)
    finally:
        conn.close()


@app.delete("/api/orgs/{org_id}")
def api_delete_org(org_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Delete organization. Requires admin role."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "admin")
    conn = get_db()
    try:
        org_mod.delete_organization(conn, org_id, user["id"])
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/orgs/join")
def api_join_org(body: OrgJoinPayload, rubricgen_session: str | None = Cookie(default=None)):
    """Join an organization via invite code."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        return org_mod.join_by_invite(conn, user["id"], body.invite_code)
    finally:
        conn.close()


@app.post("/api/orgs/{org_id}/regenerate-invite")
def api_regenerate_invite(org_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Generate a new invite code. Requires admin role."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "admin")
    conn = get_db()
    try:
        code = org_mod.regenerate_invite_code(conn, org_id, user["id"])
        return {"invite_code": code}
    finally:
        conn.close()


# ─── Org membership ───

@app.post("/api/orgs/{org_id}/members")
def api_add_org_member(org_id: int, body: OrgMemberPayload, rubricgen_session: str | None = Cookie(default=None)):
    """Add a member to the organization by email. Requires admin role."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "admin")
    conn = get_db()
    try:
        return org_mod.add_member(conn, org_id, user["id"], body.email, body.role)
    finally:
        conn.close()


@app.patch("/api/orgs/{org_id}/members/{member_user_id}")
def api_update_org_member(org_id: int, member_user_id: int, body: OrgMemberRolePayload, rubricgen_session: str | None = Cookie(default=None)):
    """Change a member's role. Requires admin role."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "admin")
    conn = get_db()
    try:
        org_mod.update_member_role(conn, org_id, user["id"], member_user_id, body.role)
        return {"ok": True}
    finally:
        conn.close()


@app.delete("/api/orgs/{org_id}/members/{member_user_id}")
def api_remove_org_member(org_id: int, member_user_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Remove a member or leave the organization."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "admin")
    conn = get_db()
    try:
        org_mod.remove_member(conn, org_id, user["id"], member_user_id)
        return {"ok": True}
    finally:
        conn.close()


# ─── Org billing ───

@app.get("/api/orgs/{org_id}/billing/balance")
def api_org_balance(org_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Get organization credit balance. Requires viewer+ role."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        org_mod.require_org_role(conn, org_id, user["id"], "general")
        return bill.get_org_balance(conn, org_id)
    finally:
        conn.close()


@app.get("/api/orgs/{org_id}/billing/transactions")
def api_org_transactions(org_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """List organization credit transactions. Requires viewer+ role."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        org_mod.require_org_role(conn, org_id, user["id"], "general")
        return bill.list_org_transactions(conn, org_id)
    finally:
        conn.close()


@app.post("/api/orgs/{org_id}/billing/checkout")
def api_org_checkout(org_id: int, body: dict, rubricgen_session: str | None = Cookie(default=None)):
    """Create Stripe checkout session for org credits. Requires admin role."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "admin")
    conn = get_db()
    try:
        org_mod.require_org_role(conn, org_id, user["id"], "admin")
        pack_id = body.get("pack_id")
        if not pack_id:
            raise HTTPException(400, "pack_id is required")
        return bill.create_org_checkout_session(conn, org_id, user["id"], int(pack_id))
    finally:
        conn.close()


@app.post("/api/orgs/{org_id}/billing/transfer")
def api_org_transfer(org_id: int, body: OrgTransferPayload, rubricgen_session: str | None = Cookie(default=None)):
    """Transfer personal credits to org pool. Requires contributor+ role."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        org_mod.require_org_role(conn, org_id, user["id"], "engineer")
        bill.transfer_credits_to_org(conn, user["id"], org_id, body.amount)
        return {"ok": True}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Enterprise seat-based billing (POST/GET /api/enterprise[/...])
# ═══════════════════════════════════════════════════════════════════════════
# These routes drive the Phase-2 enterprise flow: creating an organization
# with a Stripe subscription, adjusting seat pools, assigning members to
# seats, and managing the org-level credit balance. Most endpoints run
# independently of ENTERPRISE_MODE so staging can exercise them against test
# Stripe before the global flag is flipped.

class EnterpriseCreatePayload(BaseModel):
    name: str
    description: str | None = None
    admin_qty: int = 1
    engineer_qty: int = 0
    general_qty: int = 0
    success_url: str | None = None
    cancel_url: str | None = None


class EnterpriseSeatsPayload(BaseModel):
    admin_qty: int | None = None
    engineer_qty: int | None = None
    general_qty: int | None = None


class EnterpriseMemberPayload(BaseModel):
    email: str
    seat_type: str = "general"


class EnterpriseMemberUpdatePayload(BaseModel):
    seat_type: str


def _require_enterprise_admin(conn, org_id: int, user_id: int) -> dict:
    """Enterprise management endpoints require the caller to hold an admin
    seat in the target org. Owner is implicitly an admin."""
    return org_mod.require_org_role(conn, org_id, user_id, "admin")


def _require_enterprise_owner(conn, org_id: int, user_id: int) -> None:
    """Stricter check for billing-cancellation-class actions (seat pool size,
    subscription cancel). Only the single is_owner=1 member passes."""
    row = conn.execute(
        "SELECT is_owner FROM org_members WHERE org_id=? AND user_id=?",
        (org_id, user_id),
    ).fetchone()
    if not row or not row["is_owner"]:
        raise HTTPException(403, "Only the enterprise owner can perform this action.")


@app.post("/api/enterprise", status_code=201)
def api_enterprise_create(body: EnterpriseCreatePayload,
                          request: Request,
                          rubricgen_session: str | None = Cookie(default=None)):
    """Create a new enterprise org with the caller as owner + admin seat-holder.
    Returns a Stripe Checkout URL; owner completes payment out-of-band and
    the subscription.created webhook provisions seats."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        # 1) Create the organization (creator gets is_owner=1 + admin role)
        org = org_mod.create_organization(
            conn, user["id"], name=body.name,
            description=body.description or "", domain=None,
        )
        # 2) Build redirect URLs if not supplied. Hard-coded to our host because
        #    Stripe requires absolute URLs.
        base = str(request.base_url).rstrip("/")
        success = body.success_url or f"{base}/enterprise?created=1&org_id={org['id']}"
        cancel  = body.cancel_url  or f"{base}/onboarding?canceled=1"
        # 3) Kick off Stripe Checkout
        checkout_url = enterprise_mod.create_enterprise_checkout(
            conn, user, org_id=org["id"],
            admin_qty=body.admin_qty,
            engineer_qty=body.engineer_qty,
            general_qty=body.general_qty,
            success_url=success, cancel_url=cancel,
        )
        return {"org_id": org["id"], "org_name": org["name"],
                "org_slug": org["slug"], "checkout_url": checkout_url}
    finally:
        conn.close()


@app.get("/api/enterprise/{org_id}")
def api_enterprise_get(org_id: int,
                       rubricgen_session: str | None = Cookie(default=None)):
    """Return consolidated enterprise state: org, subscription, seat usage,
    credit balance. Visible to any admin-seat holder."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        _require_enterprise_admin(conn, org_id, user["id"])
        return enterprise_mod.get_enterprise_state(conn, org_id)
    finally:
        conn.close()


@app.patch("/api/enterprise/{org_id}/seats")
def api_enterprise_update_seats(org_id: int,
                                body: EnterpriseSeatsPayload,
                                rubricgen_session: str | None = Cookie(default=None)):
    """Adjust seat-pool quantities on the Stripe subscription. Owner only.
    Reduction below assigned count returns 409."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        _require_enterprise_owner(conn, org_id, user["id"])
        return enterprise_mod.update_seat_quantities(
            conn, org_id,
            admin_qty=body.admin_qty,
            engineer_qty=body.engineer_qty,
            general_qty=body.general_qty,
        )
    finally:
        conn.close()


@app.post("/api/enterprise/{org_id}/members", status_code=201)
def api_enterprise_add_member(org_id: int,
                              body: EnterpriseMemberPayload,
                              rubricgen_session: str | None = Cookie(default=None)):
    """Invite/assign a user to a seat. Admin only. 409 if pool full."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        _require_enterprise_admin(conn, org_id, user["id"])
        email = body.email.strip().lower()
        target = conn.execute(
            "SELECT id FROM users WHERE email=?", (email,),
        ).fetchone()
        if not target:
            raise HTTPException(404, f"No user found with email {email}.")
        return enterprise_mod.assign_seat(
            conn, org_id, target["id"], body.seat_type, assigned_by=user["id"],
        )
    finally:
        conn.close()


@app.patch("/api/enterprise/{org_id}/members/{member_user_id}")
def api_enterprise_update_member(org_id: int, member_user_id: int,
                                 body: EnterpriseMemberUpdatePayload,
                                 rubricgen_session: str | None = Cookie(default=None)):
    """Change a member's seat type. Admin only. 403 for the owner, 409 on full
    target pool."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        _require_enterprise_admin(conn, org_id, user["id"])
        return enterprise_mod.assign_seat(
            conn, org_id, member_user_id, body.seat_type, assigned_by=user["id"],
        )
    finally:
        conn.close()


@app.delete("/api/enterprise/{org_id}/members/{member_user_id}")
def api_enterprise_remove_member(org_id: int, member_user_id: int,
                                 rubricgen_session: str | None = Cookie(default=None)):
    """Remove a member's seat assignment (and org membership). Admin only.
    403 for the owner."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        _require_enterprise_admin(conn, org_id, user["id"])
        enterprise_mod.unassign_seat(conn, org_id, member_user_id)
        return {"status": "removed"}
    finally:
        conn.close()


@app.post("/api/enterprise/{org_id}/sync")
def api_enterprise_sync(org_id: int,
                        rubricgen_session: str | None = Cookie(default=None)):
    """Reconcile local subscription state from Stripe. Admin only. Used when
    a webhook is dropped mid-flight or status drifts."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        _require_enterprise_admin(conn, org_id, user["id"])
        return enterprise_mod.sync_from_stripe(conn, org_id)
    finally:
        conn.close()


# ─── Org models ───

@app.get("/api/orgs/{org_id}/models")
def api_org_models(org_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """List models belonging to an organization. Requires viewer+ role."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        org_mod.require_org_role(conn, org_id, user["id"], "general")
        from backend.models_registry import list_org_models
        return list_org_models(conn, org_id)
    finally:
        conn.close()


# ─── Org leaderboard ───

@app.get("/api/leaderboard/organizations")
def api_org_leaderboard(rubricgen_session: str | None = Cookie(default=None)):
    """Organization leaderboard."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT olc.*, o.name AS org_name, o.slug AS org_slug
               FROM org_leaderboard_cache olc
               JOIN organizations o ON o.id = olc.org_id
               ORDER BY olc.total_points DESC"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/public/leaderboard/organizations")
def api_public_org_leaderboard(request: Request):
    """Public organization leaderboard — no auth, rate-limited."""
    client_ip = request.client.host if request.client else "unknown"
    if not analytics_mod.check_rate_limit(client_ip):
        raise HTTPException(429, "Rate limit exceeded. Max 60 requests per minute.")
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT olc.total_models, olc.total_challenges, olc.total_points,
                      olc.daily_points, olc.avg_accuracy, olc.best_model_id,
                      o.name AS org_name, o.slug AS org_slug
               FROM org_leaderboard_cache olc
               JOIN organizations o ON o.id = olc.org_id
               ORDER BY olc.total_points DESC"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ─────────────────────────────────────────────
# Membership API
# ─────────────────────────────────────────────

@app.get("/api/membership")
def api_get_membership(rubricgen_session: str | None = Cookie(default=None)):
    """Get current user's membership and usage."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        membership = member_mod.get_user_membership(conn, user["id"])
        pdf_status = member_mod.check_pdf_limit(conn, user["id"])
        return {**membership, "pdf_status": pdf_status}
    finally:
        conn.close()


@app.get("/api/membership/plans")
def api_list_plans(rubricgen_session: str | None = Cookie(default=None)):
    """List available membership plans."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return member_mod.list_plans(conn)
    finally:
        conn.close()


class SubscribePayload(BaseModel):
    plan_id: int

@app.post("/api/membership/subscribe")
def api_subscribe(body: SubscribePayload, rubricgen_session: str | None = Cookie(default=None)):
    """Create Stripe subscription for a membership plan."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        return member_mod.create_subscription(
            conn, user["id"], body.plan_id,
            success_url=f"{APP_BASE_URL}/billing?membership=success",
            cancel_url=f"{APP_BASE_URL}/billing?membership=cancelled",
        )
    finally:
        conn.close()


@app.post("/api/membership/cancel")
def api_cancel_membership(rubricgen_session: str | None = Cookie(default=None)):
    """Cancel current subscription."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        return member_mod.cancel_subscription(conn, user["id"])
    finally:
        conn.close()


# ─────────────────────────────────────────────
# Developer API Key Management
# ─────────────────────────────────────────────

@app.post("/api/developers/generate-key")
def api_generate_api_key(rubricgen_session: str | None = Cookie(default=None)):
    """Generate or regenerate the user's personal API key."""
    user = require_user(rubricgen_session)
    new_key = f"rg_user_{secrets.token_urlsafe(32)}"
    conn = get_db()
    try:
        with conn:
            conn.execute(
                "UPDATE users SET api_key=?, api_key_created_at=CURRENT_TIMESTAMP WHERE id=?",
                (new_key, user["id"]),
            )
            conn.commit()
    finally:
        conn.close()
    return {"api_key": new_key, "message": "API key generated. Store it securely — it won't be shown in full again."}


@app.get("/api/developers/key-status")
def api_key_status(rubricgen_session: str | None = Cookie(default=None)):
    """Check if user has an API key (returns masked version)."""
    user = require_user(rubricgen_session)
    conn = get_db()
    row = conn.execute(
        "SELECT api_key, api_key_created_at FROM users WHERE id=?", (user["id"],)
    ).fetchone()
    conn.close()
    if row and row["api_key"]:
        key = row["api_key"]
        masked = key[:12] + "..." + key[-4:]
        return {"has_key": True, "masked_key": masked, "created_at": row["api_key_created_at"]}
    return {"has_key": False, "masked_key": None, "created_at": None}


@app.delete("/api/developers/revoke-key")
def api_revoke_key(rubricgen_session: str | None = Cookie(default=None)):
    """Revoke the user's API key."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        with conn:
            conn.execute(
                "UPDATE users SET api_key=NULL, api_key_created_at=NULL WHERE id=?",
                (user["id"],),
            )
            conn.commit()
    finally:
        conn.close()
    return {"ok": True, "message": "API key revoked."}


# ─────────────────────────────────────────────
# Literature Search API
# ─────────────────────────────────────────────

class SearchChatPayload(BaseModel):
    session_id: int | None = None
    message: str

class SearchExecutePayload(BaseModel):
    session_id: int
    database: str = "pubmed"
    query: str | None = None
    page: int = 1
    page_size: int = 50

class SearchImportPayload(BaseModel):
    session_id: int
    result_ids: list[int]
    project_id: int | None = None
    # 'metadata' (instant, free) | 'fetch' (2 cr) | 'firecrawl' (5 cr)
    # | 'browser' (15 cr) | 'auto' (2-15 cr, tier-priced — recommended default)
    # | 'extension' (free, queues for the user's Chrome extension)
    mode: str = "metadata"

class SearchExportPayload(BaseModel):
    session_id: int
    result_ids: list[int]

class SearchSelectionPayload(BaseModel):
    result_ids: list[int]
    selected: bool

class SearchSelectAllPayload(BaseModel):
    session_id: int
    query_version: int
    selected: bool

class SearchSessionUpdatePayload(BaseModel):
    title: str | None = None
    project_id: int | None = None
    remove_from_project: bool = False


@app.post("/api/search/chat")
def api_search_chat(body: SearchChatPayload, rubricgen_session: str | None = Cookie(default=None)):
    """Send a message to the AI search strategist."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        session_id = body.session_id
        if session_id is None:
            sess = search_mod.create_session(conn, user["id"])
            session_id = sess["id"]
        return search_mod.chat(conn, session_id, user["id"], body.message)
    finally:
        conn.close()


@app.post("/api/search/execute")
def api_search_execute(body: SearchExecutePayload, rubricgen_session: str | None = Cookie(default=None)):
    """Execute a search against a database."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    if not body.query:
        raise HTTPException(400, "Query is required")
    conn = get_db()
    try:
        return search_mod.execute_search(
            conn, body.session_id, user["id"],
            body.database, body.query, body.page, body.page_size,
        )
    finally:
        conn.close()


@app.post("/api/search/sessions", status_code=201)
def api_search_create_session(rubricgen_session: str | None = Cookie(default=None)):
    """Create a new search session (no LLM call — just creates the DB row)."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        return search_mod.create_session(conn, user["id"])
    finally:
        conn.close()


@app.get("/api/search/sessions")
def api_search_sessions(rubricgen_session: str | None = Cookie(default=None)):
    """List user's search sessions."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return search_mod.list_sessions(conn, user["id"])
    finally:
        conn.close()


@app.get("/api/search/sessions/{session_id}")
def api_search_session(session_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Get a search session with messages and results."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return search_mod.get_session(conn, session_id, user["id"])
    finally:
        conn.close()


@app.delete("/api/search/sessions/{session_id}")
def api_delete_search_session(session_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Delete a search session."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        search_mod.delete_session(conn, session_id, user["id"])
        return {"ok": True}
    finally:
        conn.close()


@app.patch("/api/search/sessions/{session_id}")
def api_update_search_session(session_id: int, body: SearchSessionUpdatePayload,
                              rubricgen_session: str | None = Cookie(default=None)):
    """Rename a search session or move it to/from a project."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        if body.title is not None:
            search_mod.update_session_title(conn, session_id, user["id"], body.title)
        if body.remove_from_project:
            search_mod.update_session_project(conn, session_id, user["id"], None)
        elif body.project_id is not None:
            search_mod.update_session_project(conn, session_id, user["id"], body.project_id)
        return {"ok": True}
    finally:
        conn.close()


PDF_FETCH_CREDIT_COST = 2  # per result for mode='fetch'
PDF_FIRECRAWL_CREDIT_COST = 5  # per result for mode='firecrawl' — covers Firecrawl API spend
PDF_BROWSER_CREDIT_COST = 15  # per result for mode='browser' — Chromium session is slow + RAM-hungry
PDF_AUTO_CREDIT_COST = PDF_BROWSER_CREDIT_COST  # auto pre-charges max; tier-based refund downstream


@app.post("/api/search/import")
def api_search_import(body: SearchImportPayload, rubricgen_session: str | None = Cookie(default=None)):
    """Import selected search results as papers.

    Five modes:
    - ``metadata`` (free, synchronous): metadata-only papers row +
      ``external_url`` click-out.
    - ``auto`` (2-15 credits/paper, async — *recommended default*): runs every
      strategy in order (PMC → Unpaywall → meta-tag → Firecrawl → browser) and
      charges based on which tier delivered the PDF. Pre-charges the browser-tier
      max and refunds the difference downstream.
    - ``fetch`` (2 credits/paper, async): background crawler tries
      PMC → Unpaywall → publisher landing pages with plain ``httpx``. No
      Firecrawl or browser fallback.
    - ``firecrawl`` (5 credits/paper, async): same as ``fetch`` but adds a
      JS-rendering Firecrawl fallback for landing pages that block plain
      ``httpx``. Requires ``FIRECRAWL_API_KEY``.
    - ``browser`` (15 credits/paper, async): everything in ``firecrawl``
      plus a final Playwright/Chromium browser-agent fallback that opens the
      publisher's landing page in a real browser, finds the PDF link
      (heuristics + LLM-driven nav), and coerces a download. Requires
      Playwright + Chromium installed on the server (see DEVELOPMENT.md).

    Per-result failures still create a ``pdf_status='fetch_failed'`` row and
    refund the per-paper credit.
    """
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    if body.mode not in ("metadata", "auto", "fetch", "firecrawl", "browser", "extension"):
        raise HTTPException(
            400,
            "mode must be 'metadata', 'auto', 'fetch', 'firecrawl', 'browser', or 'extension'",
        )
    if not body.result_ids:
        raise HTTPException(400, "result_ids cannot be empty")
    # 'auto' includes Firecrawl as one of its tiers but degrades gracefully
    # when the key is missing (the Firecrawl strategy returns permanent_error
    # and the browser tier still runs). Only the explicit Firecrawl/browser
    # modes hard-require the key — they don't make sense without it.
    if body.mode in ("firecrawl", "browser") and not os.environ.get("FIRECRAWL_API_KEY"):
        raise HTTPException(
            503,
            f"{body.mode!r} mode requires FIRECRAWL_API_KEY to be configured. "
            f"Set it in your environment (api.firecrawl.dev) or use 'auto' / 'fetch' mode."
        )

    conn = get_db()
    try:
        # Legacy membership PDF limit — only while flag is off (see paper upload).
        if not enterprise_mod.ENTERPRISE_MODE:
            pdf_status_chk = member_mod.check_pdf_limit(conn, user["id"])
            if not pdf_status_chk["allowed"]:
                raise HTTPException(
                    403,
                    f"PDF limit reached ({pdf_status_chk['used']}/{pdf_status_chk['limit']}). "
                    f"Upgrade your membership.",
                )

        if body.mode == "metadata":
            return search_mod.import_results(
                conn, body.session_id, body.result_ids, user["id"], PAPERS_DIR,
                project_id=body.project_id, mode="metadata",
            )

        if body.mode == "extension":
            # No daemon, no credit charge — the user's browser does the work.
            ext_status = extension_mod.get_extension_status(conn, user["id"])
            if not ext_status["paired"]:
                raise HTTPException(
                    412,
                    "Pair the Chrome extension first at /developers, then retry.",
                )
            return search_mod.import_results_extension(
                conn, body.session_id, body.result_ids, user["id"],
                project_id=body.project_id,
            )

        # async modes — credit-gate, enqueue, spawn worker. 'auto' pre-charges
        # the browser-tier max; backend refunds the excess based on which tier
        # actually delivered the PDF.
        credit_per_paper = {
            "fetch": PDF_FETCH_CREDIT_COST,
            "firecrawl": PDF_FIRECRAWL_CREDIT_COST,
            "browser": PDF_BROWSER_CREDIT_COST,
            "auto": PDF_AUTO_CREDIT_COST,
        }[body.mode]
        total = len(body.result_ids)
        total_cost = total * credit_per_paper
        _annotator_ai_gate(conn, user, total_cost,
                           f"PDF {body.mode} ({total} results)")
        run_id = search_mod.create_pdf_fetch_run(
            conn, user["id"], body.session_id, body.result_ids,
            body.project_id, mode=body.mode, credit_per_paper=credit_per_paper,
        )
    finally:
        conn.close()

    def _refund(uid: int, amt: int, reason: str) -> None:
        c = get_db()
        try:
            bill.refund_credits(c, uid, amt, reason)
        finally:
            c.close()

    t = threading.Thread(
        target=search_mod.run_pdf_fetch_job,
        args=(get_db, run_id, PAPERS_DIR, _refund),
        daemon=True,
        name=f"pdf-fetch-run-{run_id}",
    )
    t.start()

    return {"run_id": run_id, "total": total, "credits_charged": total_cost,
            "mode": body.mode}


@app.get("/api/search/pdf-fetch/{run_id}")
def api_search_pdf_fetch_status(run_id: int,
                                rubricgen_session: str | None = Cookie(default=None)):
    """Poll the status of a background PDF-fetch run."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        run = search_mod.get_pdf_fetch_run(conn, run_id, user["id"])
        if not run:
            raise HTTPException(404, "PDF fetch run not found")
        return run
    finally:
        conn.close()


@app.get("/api/search/pdf-fetch/{run_id}/events")
def api_search_pdf_fetch_events(run_id: int, after: int = 0,
                                rubricgen_session: str | None = Cookie(default=None)):
    """Stream incremental progress events for a PDF-fetch run."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        events = search_mod.get_pdf_fetch_events(conn, run_id, user["id"], after)
        return {"events": events}
    finally:
        conn.close()


# ─────────────────────────────────────────────
# Chrome extension: pairing + queue + upload
# See backend/extension.py for the logic; routes here are thin pass-throughs.
# ─────────────────────────────────────────────

class ExtensionPairPayload(BaseModel):
    code: str


class ExtensionResolvePayload(BaseModel):
    landing_url: str
    anchors: list[dict]


@app.post("/api/extension/pair-code")
def api_extension_mint_pairing_code(rubricgen_session: str | None = Cookie(default=None)):
    """Generate a one-time pairing code for the calling user.

    The code is short (``EX-XXXX-YYYY``) and TTL'd for 10 min. Any prior
    unconsumed code for this user is invalidated. The caller must be a real
    logged-in user (cookie session) — this endpoint is *not* available via
    API key, since you'd already have an API key if you had one.
    """
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        return extension_mod.mint_pairing_code(conn, user["id"])
    finally:
        conn.close()


@app.post("/api/extension/pair")
def api_extension_pair(body: ExtensionPairPayload):
    """Exchange a pairing code for a permanent ``rg_ext_*`` token.

    No auth required — the code itself is the auth. This is the one endpoint
    the Chrome extension hits before it has a token.
    """
    conn = get_db()
    try:
        try:
            return extension_mod.consume_pairing_code(conn, body.code)
        except ValueError as e:
            reason = str(e)
            status = {
                "not_found": 404,
                "expired": 410,
                "already_consumed": 409,
            }.get(reason, 400)
            raise HTTPException(status, f"Pairing code {reason}")
    finally:
        conn.close()


@app.delete("/api/extension/token")
def api_extension_revoke(rubricgen_session: str | None = Cookie(default=None)):
    """Revoke the calling user's extension token. Idempotent."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        extension_mod.revoke_extension_token(conn, user["id"])
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/extension/status")
def api_extension_status(rubricgen_session: str | None = Cookie(default=None),
                         x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Return ``{paired, paired_at, queue_count}`` for the developers page +
    the extension popup's connection check."""
    user = require_user(rubricgen_session, x_api_key)
    conn = get_db()
    try:
        return extension_mod.get_extension_status(conn, user["id"])
    finally:
        conn.close()


@app.get("/api/extension/queue")
def api_extension_queue(limit: int = 50,
                        rubricgen_session: str | None = Cookie(default=None),
                        x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Return papers waiting for extension fetch (``pdf_status='extension_pending'``).

    Auth: cookie session OR ``rg_ext_*`` token (the extension itself uses the
    token; the developers page uses the cookie)."""
    user = require_user(rubricgen_session, x_api_key)
    conn = get_db()
    try:
        return {"papers": extension_mod.get_queue(conn, user["id"], limit)}
    finally:
        conn.close()


@app.post("/api/extension/papers/{paper_id}/pdf")
async def api_extension_upload_pdf(paper_id: int, request: Request,
                                    rubricgen_session: str | None = Cookie(default=None),
                                    x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Receive PDF bytes for a queued paper.

    Body: JSON ``{pdf_b64: "<base64>"}``. We accept base64 in JSON to avoid
    multipart parsing in the extension and to keep the request shape boring.
    """
    user = require_user(rubricgen_session, x_api_key)
    payload = await request.json()
    pdf_b64 = payload.get("pdf_b64") or ""
    if not isinstance(pdf_b64, str) or not pdf_b64:
        raise HTTPException(400, "pdf_b64 is required")
    try:
        import base64
        pdf_bytes = base64.b64decode(pdf_b64, validate=True)
    except Exception:
        raise HTTPException(400, "pdf_b64 is not valid base64")

    conn = get_db()
    try:
        try:
            return extension_mod.upload_pdf_for_paper(
                conn, user["id"], paper_id, pdf_bytes,
            )
        except ValueError as e:
            reason = str(e)
            status = {
                "too_large": 413,
                "not_pdf": 415,
                "not_found": 404,
                "already_present": 409,
                "upgrade_failed": 500,
            }.get(reason, 400)
            raise HTTPException(status, reason)
    finally:
        conn.close()


@app.post("/api/extension/papers/{paper_id}/skip")
def api_extension_skip(paper_id: int,
                       rubricgen_session: str | None = Cookie(default=None),
                       x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Mark a queued paper as ``fetch_failed`` so the queue clears. Idempotent."""
    user = require_user(rubricgen_session, x_api_key)
    conn = get_db()
    try:
        try:
            return extension_mod.skip_paper(conn, user["id"], paper_id)
        except ValueError as e:
            if str(e) == "not_found":
                raise HTTPException(404, "Paper not found")
            raise HTTPException(400, str(e))
    finally:
        conn.close()


@app.post("/api/papers/{paper_id}/queue-for-extension")
def api_paper_queue_for_extension(paper_id: int,
                                   rubricgen_session: str | None = Cookie(default=None)):
    """Queue an existing paper for Chrome-extension PDF fetch.

    Used by the Library page's bulk "Send to extension" action — re-queues a
    metadata-only / fetch_failed paper that the user already has, without
    going through the search-import path.
    """
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        ext_status = extension_mod.get_extension_status(conn, user["id"])
        if not ext_status["paired"]:
            raise HTTPException(
                412, "Pair the Chrome extension first at /developers, then retry."
            )
        paper = conn.execute(
            "SELECT id, user_id, pdf_status FROM papers WHERE id = ?",
            (paper_id,),
        ).fetchone()
        if not paper or paper["user_id"] != user["id"]:
            raise HTTPException(404, "Paper not found")
        if paper["pdf_status"] == "present":
            raise HTTPException(409, "Paper already has a PDF")
        with conn:
            conn.execute(
                "UPDATE papers SET pdf_status = 'extension_pending' WHERE id = ?",
                (paper_id,),
            )
            conn.commit()
        return {"ok": True, "paper_id": paper_id, "pdf_status": "extension_pending"}
    finally:
        conn.close()


@app.post("/api/extension/resolve-pdf-url")
def api_extension_resolve_pdf_url(body: ExtensionResolvePayload,
                                   rubricgen_session: str | None = Cookie(default=None),
                                   x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """LLM-driven fallback for the extension's content script: when its DOM
    heuristics miss, send the rendered page's anchors here and Claude Haiku
    picks the most likely PDF download link.

    Same prompt + JSON contract as ``backend/browser_agent.py`` (both reuse
    ``backend/pdf_link_picker.py``).
    """
    require_user(rubricgen_session, x_api_key)
    if not body.landing_url:
        raise HTTPException(400, "landing_url is required")
    anchors = body.anchors or []
    if not anchors:
        return {"pdf_url": None, "reason": "no_anchors"}
    # Cap to 200 anchors regardless of what the extension sent
    anchors = anchors[:200]
    from backend import pdf_link_picker
    picked = pdf_link_picker.pick_pdf_url_from_anchors(body.landing_url, anchors)
    return {"pdf_url": picked}


@app.post("/api/search/export/ris")
def api_search_export_ris(body: SearchExportPayload, rubricgen_session: str | None = Cookie(default=None)):
    """Export selected results as RIS."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        ris = search_mod.export_ris(conn, body.session_id, body.result_ids)
    finally:
        conn.close()
    return Response(
        content=ris.encode("utf-8"),
        media_type="application/x-research-info-systems",
        headers={"Content-Disposition": "attachment; filename=search_results.ris"},
    )


@app.post("/api/search/export/bibtex")
def api_search_export_bibtex(body: SearchExportPayload, rubricgen_session: str | None = Cookie(default=None)):
    """Export selected results as BibTeX."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        bib = search_mod.export_bibtex(conn, body.session_id, body.result_ids)
    finally:
        conn.close()
    return Response(
        content=bib.encode("utf-8"),
        media_type="application/x-bibtex",
        headers={"Content-Disposition": "attachment; filename=search_results.bib"},
    )


@app.post("/api/search/results/select")
def api_search_select(body: SearchSelectionPayload, rubricgen_session: str | None = Cookie(default=None)):
    """Toggle selection on search results."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        search_mod.toggle_result_selection(conn, body.result_ids, body.selected)
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/search/results/select-all")
def api_search_select_all(body: SearchSelectAllPayload, rubricgen_session: str | None = Cookie(default=None)):
    """Select/deselect all results for a query version."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        search_mod.select_all_results(conn, body.session_id, body.query_version, body.selected)
        return {"ok": True}
    finally:
        conn.close()


# ─────────────────────────────────────────────
# AI Researcher Lab — Multi-agent chat
# ─────────────────────────────────────────────

class LabChatPayload(BaseModel):
    session_id: int | None = None
    agent_type: str = "search_strategist"
    message: str
    document_ids: list[int] = []

class LabSessionUpdatePayload(BaseModel):
    title: str | None = None
    project_id: int | None = None
    remove_from_project: bool = False

class LabExportPayload(BaseModel):
    session_id: int
    export_format: str  # docx, latex, xlsx, csv, py, r
    content_type: str = "text"


@app.post("/api/lab/chat")
def api_lab_chat(body: LabChatPayload, rubricgen_session: str | None = Cookie(default=None)):
    """Send a message to any lab agent."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        session_id = body.session_id
        if session_id is None:
            sess = lab_mod.create_session(conn, user["id"], body.agent_type)
            session_id = sess["id"]
        result = lab_mod.chat(conn, session_id, user["id"], body.message, body.agent_type,
                              document_ids=body.document_ids)
        # Write conversation to Obsidian vault (best-effort)
        try:
            from backend.obsidian import write_lab_conversation_note
            sess_data = lab_mod.get_session(conn, session_id, user["id"])
            write_lab_conversation_note(
                OBSIDIAN_VAULT_DIR, body.agent_type,
                sess_data, sess_data.get("messages", []), user,
            )
        except Exception:
            pass
        return result
    finally:
        conn.close()


class LabCreateSessionPayload(BaseModel):
    agent_type: str = "research_chat"
    title: str = "New Conversation"


@app.post("/api/lab/sessions", status_code=201)
def api_lab_create_session(body: LabCreateSessionPayload, rubricgen_session: str | None = Cookie(default=None)):
    """Create a new lab session."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        return lab_mod.create_session(conn, user["id"], body.agent_type, body.title)
    finally:
        conn.close()


@app.get("/api/lab/sessions")
def api_lab_sessions(agent_type: str | None = None, rubricgen_session: str | None = Cookie(default=None)):
    """List lab sessions, optionally filtered by agent_type."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return lab_mod.list_sessions(conn, user["id"], agent_type)
    finally:
        conn.close()


@app.get("/api/lab/sessions/{session_id}")
def api_lab_session(session_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Get a lab session with messages."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return lab_mod.get_session(conn, session_id, user["id"])
    finally:
        conn.close()


@app.delete("/api/lab/sessions/{session_id}")
def api_lab_session_delete(session_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Delete a lab session."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        lab_mod.delete_session(conn, session_id, user["id"])
        return {"ok": True}
    finally:
        conn.close()


@app.patch("/api/lab/sessions/{session_id}")
def api_lab_session_update(session_id: int, body: LabSessionUpdatePayload, rubricgen_session: str | None = Cookie(default=None)):
    """Rename or move a lab session to a project."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        return lab_mod.update_session(conn, session_id, user["id"],
                                     title=body.title, project_id=body.project_id,
                                     remove_from_project=body.remove_from_project)
    finally:
        conn.close()


@app.post("/api/lab/export")
def api_lab_export(body: LabExportPayload, rubricgen_session: str | None = Cookie(default=None)):
    """Export a lab session to a file format."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        session = lab_mod.get_session(conn, body.session_id, user["id"])
        messages = session["messages"]
        content = "\n\n".join(m["content"] for m in messages if m["role"] == "assistant")
        title = session.get("title", "export")

        from backend.exports import export_docx, export_latex, export_xlsx, export_csv, export_python_script, export_r_script

        if body.export_format == "docx":
            data = export_docx(content, title)
            return Response(content=data,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={"Content-Disposition": f'attachment; filename="{title}.docx"'})
        elif body.export_format == "latex":
            tex = export_latex(content, title)
            return Response(content=tex.encode(), media_type="application/x-tex",
                headers={"Content-Disposition": f'attachment; filename="{title}.tex"'})
        elif body.export_format == "xlsx":
            # Extract tabular data from metadata if available
            data = _extract_tabular_data(messages)
            xlsx_bytes = export_xlsx(data, title)
            return Response(content=xlsx_bytes,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f'attachment; filename="{title}.xlsx"'})
        elif body.export_format == "csv":
            data = _extract_tabular_data(messages)
            csv_str = export_csv(data)
            return Response(content=csv_str.encode(), media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{title}.csv"'})
        elif body.export_format == "py":
            code = _extract_code_blocks(messages, "python")
            py_str = export_python_script(code)
            return Response(content=py_str.encode(), media_type="text/x-python",
                headers={"Content-Disposition": f'attachment; filename="{title}.py"'})
        elif body.export_format == "r":
            code = _extract_code_blocks(messages, "r")
            r_str = export_r_script(code)
            return Response(content=r_str.encode(), media_type="text/plain",
                headers={"Content-Disposition": f'attachment; filename="{title}.R"'})
        else:
            raise HTTPException(400, f"Unsupported export format: {body.export_format}")
    finally:
        conn.close()


def _extract_tabular_data(messages: list[dict]) -> list[dict]:
    """Extract any tabular data from message metadata for spreadsheet export."""
    rows = []
    for m in messages:
        meta = m.get("metadata", {})
        if meta.get("critique"):
            for item in meta["critique"]:
                rows.append(item)
        if meta.get("hypotheses"):
            for h in meta["hypotheses"]:
                rows.append({"hypothesis": h.get("statement", ""), "type": h.get("type", ""),
                             "impact": h.get("potential_impact", ""), "novel": h.get("novelty_assessment", {}).get("is_novel", "")})
        if meta.get("citation_list"):
            for c in meta["citation_list"]:
                rows.append(c)
    if not rows:
        # Fallback: export messages as rows
        for m in messages:
            if m["role"] == "assistant":
                rows.append({"role": m["role"], "content": m["content"][:500]})
    return rows


def _extract_code_blocks(messages: list[dict], language: str) -> list[dict]:
    """Extract code blocks from message metadata for script export."""
    blocks = []
    for m in messages:
        meta = m.get("metadata", {})
        if meta.get("code_blocks"):
            for cb in meta["code_blocks"]:
                if cb.get("language", "").lower() == language.lower():
                    blocks.append(cb)
    return blocks


class CodeExecutePayload(BaseModel):
    code: str
    language: str = "python"  # python or r


@app.post("/api/lab/execute-code")
def api_lab_execute_code(body: CodeExecutePayload, rubricgen_session: str | None = Cookie(default=None)):
    """Execute Python or R code for the AI Statistician. Rate-limited."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    from backend.code_runner import run_python_analysis, run_r_analysis
    if body.language == "r":
        return run_r_analysis(body.code, user["id"])
    return run_python_analysis(body.code, user["id"])


# ── Lab Document Context ──

from backend.storage import upload_file as storage_upload, download_file as storage_download, delete_file as storage_delete, get_content_type
from backend.membership import check_storage_limit


@app.post("/api/lab/documents/upload")
async def api_lab_upload_document(
    file: UploadFile = File(...),
    project_id: int | None = Form(default=None),
    rubricgen_session: str | None = Cookie(default=None),
):
    """Upload a document to cloud/local storage. Enforces plan storage limits."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    content = await file.read()
    filename = file.filename or "file"

    # Legacy membership storage limit — bypassed in enterprise mode where
    # storage would be pooled at the org level instead (not yet implemented).
    conn = get_db()
    try:
        if not enterprise_mod.ENTERPRISE_MODE:
            limit_check = check_storage_limit(conn, user["id"], len(content))
            if not limit_check["allowed"]:
                raise HTTPException(
                    403,
                    f"Storage limit reached ({limit_check['used_mb']:.1f} / {limit_check['limit_mb']} MB). "
                    f"Upgrade your plan for more storage.",
                )

        # Upload to storage (S3 or local)
        ct = get_content_type(filename)
        storage_path = storage_upload(content, filename, ct)
        ext = Path(filename).suffix.lower().lstrip(".")

        doc = lab_mod.save_document(
            conn, user["id"], filename, ext,
            len(content), storage_path, project_id,
        )
        doc["storage_used_mb"] = limit_check["used_mb"]
        doc["storage_limit_mb"] = limit_check["limit_mb"]

        # Dual-write into the papers library so the upload also shows up in
        # /library and the Annotator. We compute a real sha256 here (cheap —
        # we already have the bytes in memory). On hash collision with an
        # existing paper, the ON CONFLICT keeps the existing row and we just
        # link `lab_documents.papers_id` to it.
        try:
            sha = hashlib.sha256(content).hexdigest()
            cur = conn.execute(
                """INSERT INTO papers
                        (filename, sha256, user_id, project_id,
                         storage_path, source)
                   VALUES (?, ?, ?, ?, ?, 'lab')
                   ON CONFLICT (sha256, user_id) DO NOTHING
                   RETURNING id""",
                (filename, sha, user["id"], project_id, storage_path),
            )
            row = cur.fetchone()
            paper_id = row["id"] if row else conn.execute(
                "SELECT id FROM papers WHERE sha256=? AND user_id=?",
                (sha, user["id"]),
            ).fetchone()["id"]
            with conn:
                conn.execute(
                    "UPDATE lab_documents SET papers_id=? WHERE id=?",
                    (paper_id, doc["id"]),
                )
                # Also mirror to the multi-project junction.
                if project_id:
                    conn.execute(
                        """INSERT INTO paper_projects (paper_id, project_id)
                           VALUES (?, ?) ON CONFLICT DO NOTHING""",
                        (paper_id, project_id),
                    )
                conn.commit()
            doc["papers_id"] = paper_id
        except Exception as e:
            logger.warning("Lab dual-write to papers failed (doc=%s): %s",
                           doc.get("id"), e)
    finally:
        conn.close()
    return doc


# ═══════════════════════════════════════════════════════════════════════════
# Sources / Briefing / Methods — user-authored tools layered on every Lab chat
# ═══════════════════════════════════════════════════════════════════════════
# Sources live in the existing lab_documents table + endpoints below.
# Briefing + user-authored Methods + system-agent method cards are defined here.

class BriefingPayload(BaseModel):
    text: str = ""


class MethodCreatePayload(BaseModel):
    name: str
    when_to_use: str = ""
    instructions: str


class MethodUpdatePayload(BaseModel):
    name: str | None = None
    when_to_use: str | None = None
    instructions: str | None = None
    active: bool | None = None


@app.get("/api/briefing")
def api_briefing_get(rubricgen_session: str | None = Cookie(default=None)):
    """Return the caller's briefing — the free-form instructions block that
    gets prepended onto every Lab agent's system prompt."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return user_tools_mod.get_briefing(conn, user["id"])
    finally:
        conn.close()


@app.put("/api/briefing")
def api_briefing_put(body: BriefingPayload,
                     rubricgen_session: str | None = Cookie(default=None)):
    """Upsert the caller's briefing. 413 if it exceeds the 4000-char cap."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return user_tools_mod.upsert_briefing(conn, user["id"], body.text or "")
    finally:
        conn.close()


@app.get("/api/methods")
def api_methods_list(rubricgen_session: str | None = Cookie(default=None)):
    """List every user-authored method for the caller (active + inactive)."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return user_tools_mod.list_methods(conn, user["id"])
    finally:
        conn.close()


@app.post("/api/methods", status_code=201)
def api_methods_create(body: MethodCreatePayload,
                       rubricgen_session: str | None = Cookie(default=None)):
    """Create a user-authored method card (name / when-to-use / instructions)."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        return user_tools_mod.create_method(
            conn, user["id"], body.name, body.when_to_use, body.instructions
        )
    finally:
        conn.close()


@app.patch("/api/methods/{method_id}")
def api_methods_update(method_id: int, body: MethodUpdatePayload,
                       rubricgen_session: str | None = Cookie(default=None)):
    """Partial update. Pass `active=False` to disable a method without deleting it."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        return user_tools_mod.update_method(
            conn, user["id"], method_id,
            name=body.name, when_to_use=body.when_to_use,
            instructions=body.instructions, active=body.active,
        )
    finally:
        conn.close()


@app.delete("/api/methods/{method_id}")
def api_methods_delete(method_id: int,
                       rubricgen_session: str | None = Cookie(default=None)):
    """Permanently remove a user-authored method."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        user_tools_mod.delete_method(conn, user["id"], method_id)
        return {"status": "deleted"}
    finally:
        conn.close()


@app.get("/api/methods/system")
def api_methods_system(rubricgen_session: str | None = Cookie(default=None)):
    """Read-only capability cards for each built-in Lab agent.

    IMPORTANT — this is the user-facing surface on top of the autoresearch-
    improved prompts. It returns ONLY metadata (display_name / description /
    when_to_use / version / avg_performance). The authoritative prompt_text
    NEVER leaves the server. If you add fields here, double-check they're not
    prompt-bearing.
    """
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return list_system_methods(conn)
    finally:
        conn.close()


@app.get("/api/lab/documents")
def api_lab_list_documents(
    project_id: int | None = None,
    rubricgen_session: str | None = Cookie(default=None),
):
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return lab_mod.list_documents(conn, user["id"], project_id)
    finally:
        conn.close()


@app.get("/api/lab/documents/{doc_id}/download")
def api_lab_download_document(doc_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Download a document from storage."""
    from fastapi.responses import Response
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT filename, file_type, file_path FROM lab_documents WHERE id=? AND user_id=?",
            (doc_id, user["id"]),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "Document not found")
    row = dict(row)
    data = storage_download(row["file_path"])
    if data is None:
        raise HTTPException(404, "File not found in storage")
    ct = get_content_type(row["filename"])
    return Response(
        content=data,
        media_type=ct,
        headers={"Content-Disposition": f'attachment; filename="{row["filename"]}"'},
    )


@app.get("/api/lab/storage")
def api_lab_storage_usage(rubricgen_session: str | None = Cookie(default=None)):
    """Get user's current storage usage vs plan limit."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return check_storage_limit(conn, user["id"])
    finally:
        conn.close()


class DocUpdatePayload(BaseModel):
    project_id: int | None = None


@app.patch("/api/lab/documents/{doc_id}")
def api_lab_update_document(doc_id: int, body: DocUpdatePayload, rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        lab_mod.update_document(conn, doc_id, user["id"], body.project_id)
    finally:
        conn.close()
    return {"ok": True}


@app.delete("/api/lab/documents/{doc_id}")
def api_lab_delete_document(doc_id: int, rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        file_path = lab_mod.delete_document(conn, doc_id, user["id"])
    finally:
        conn.close()
    if file_path:
        storage_delete(file_path)
    return {"ok": True}


# ─────────────────────────────────────────────
# OGAI Annotator API
#   - Reuses existing /api/projects, /api/papers (upload/delete/assign),
#     and /api/papers/{id}/pdf. Adds annotation save/load, AI classify/prefill
#     (credit-gated), and CSV export.
# ─────────────────────────────────────────────

class AnnotationSpan(BaseModel):
    field_name: str
    page: int | None = None
    text: str | None = None
    x0: float | None = None
    y0: float | None = None
    x1: float | None = None
    y1: float | None = None


class AnnotationSavePayload(BaseModel):
    data: dict = {}
    field_annotations: dict = {}
    spans: list[AnnotationSpan] = []
    status: str | None = None
    version: int | None = None


@app.get("/api/annotator/papers")
def api_annotator_list_papers(rubricgen_session: str | None = Cookie(default=None),
                              x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Paper list for the annotator sidebar, including per-paper annotation status."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return annotator_mod.list_papers_with_status(conn, user["id"])
    finally:
        conn.close()


@app.get("/api/annotator/schema")
def api_annotator_schema():
    """Field catalog (universal groups, type-specific, design modifiers)
    used by the batch modal to render selectable checkboxes."""
    return annotator_mod.get_schema()


@app.get("/api/admin/storage/diagnose")
def api_admin_storage_diagnose(rubricgen_session: str | None = Cookie(default=None)):
    """Admin-only: probe S3 connectivity to surface real upload errors.

    Reports whether the env vars are set, whether boto3 can instantiate a
    client, whether ``HeadBucket`` succeeds, and whether a tiny test
    ``PutObject`` + ``DeleteObject`` round-trip works. No secrets returned.
    """
    require_admin(rubricgen_session)
    import os as _os
    result: dict = {
        "aws_s3_bucket_set": bool(_os.environ.get("AWS_S3_BUCKET")),
        "aws_s3_region_set": bool(_os.environ.get("AWS_S3_REGION")),
        "aws_access_key_set": bool(_os.environ.get("AWS_ACCESS_KEY_ID")),
        "aws_secret_key_set": bool(_os.environ.get("AWS_SECRET_ACCESS_KEY")),
        "bucket": _os.environ.get("AWS_S3_BUCKET", ""),
        "region": _os.environ.get("AWS_S3_REGION", "us-east-1"),
        "steps": [],
    }
    bucket = result["bucket"]
    if not bucket:
        result["steps"].append({"step": "env", "ok": False,
                                "detail": "AWS_S3_BUCKET is not set — uploads fall back to local uploads/ dir"})
        return result

    try:
        import boto3  # noqa: F401
        result["steps"].append({"step": "import boto3", "ok": True})
    except Exception as e:
        result["steps"].append({"step": "import boto3", "ok": False, "detail": str(e)})
        return result

    try:
        from backend.storage import _get_s3
        s3 = _get_s3()
        result["steps"].append({"step": "instantiate s3 client", "ok": True})
    except Exception as e:
        result["steps"].append({"step": "instantiate s3 client", "ok": False, "detail": str(e)})
        return result

    try:
        s3.head_bucket(Bucket=bucket)
        result["steps"].append({"step": "head_bucket", "ok": True})
    except Exception as e:
        result["steps"].append({"step": "head_bucket", "ok": False, "detail": str(e)})
        return result

    # Round-trip a tiny test object so we actually exercise PutObject + DeleteObject
    probe_key = f"{_os.environ.get('AWS_S3_PREFIX', 'lab-documents/')}__probe_diag"
    try:
        s3.put_object(Bucket=bucket, Key=probe_key, Body=b"ok")
        result["steps"].append({"step": "put_object", "ok": True})
    except Exception as e:
        result["steps"].append({"step": "put_object", "ok": False, "detail": str(e)})
        return result
    try:
        s3.delete_object(Bucket=bucket, Key=probe_key)
        result["steps"].append({"step": "delete_object", "ok": True})
    except Exception as e:
        result["steps"].append({"step": "delete_object", "ok": False, "detail": str(e)})
    result["ok"] = True
    return result


@app.get("/api/annotator/papers/{pid}/annotation")
def api_annotator_get_annotation(pid: int,
                                 rubricgen_session: str | None = Cookie(default=None),
                                 x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT user_id FROM papers WHERE id=?", (pid,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Paper not found")
        if row["user_id"] != user["id"] and user.get("role") != "admin":
            raise HTTPException(403, "Access denied")
        return annotator_mod.load_annotation(conn, pid, user["id"])
    finally:
        conn.close()


@app.post("/api/annotator/papers/{pid}/annotation")
def api_annotator_save_annotation(pid: int, payload: AnnotationSavePayload,
                                  rubricgen_session: str | None = Cookie(default=None),
                                  x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT user_id FROM papers WHERE id=?", (pid,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Paper not found")
        if row["user_id"] != user["id"] and user.get("role") != "admin":
            raise HTTPException(403, "Access denied")
        return annotator_mod.save_annotation(
            conn, pid, user["id"],
            {
                "data": payload.data,
                "field_annotations": payload.field_annotations,
                "spans": [s.model_dump() for s in payload.spans],
                "status": payload.status,
                "version": payload.version,
            },
        )
    finally:
        conn.close()


def _annotator_ai_gate(conn, user: dict, cost: int, description: str) -> None:
    """Shared credit gate for classify/prefill. Admins bypass (matches challenges.py)."""
    if user.get("role") == "admin":
        return
    balance = bill.get_balance(conn, user["id"])
    if balance < cost:
        raise HTTPException(402, {
            "detail": "Insufficient credits",
            "required": cost,
            "balance": balance,
        })
    if not bill.debit_credits(conn, user["id"], cost, description):
        raise HTTPException(402, "Failed to debit credits — insufficient balance")


@app.post("/api/annotator/papers/{pid}/classify")
def api_annotator_classify(pid: int,
                           rubricgen_session: str | None = Cookie(default=None),
                           x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """AI-classify the paper's study design. Credit-gated."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        is_admin = user.get("role") == "admin"
        pdf_bytes, filename = annotator_mod.load_paper_pdf(
            conn, PAPERS_DIR, pid, user["id"], is_admin=is_admin
        )
        _annotator_ai_gate(conn, user,
                           annotator_mod.CREDIT_COST_CLASSIFY,
                           f"Annotator classify: {filename}")
    finally:
        conn.close()

    try:
        result = annotator_mod.classify_study_design(pdf_bytes)
        _log_annotator_action_safe(
            user["id"], pid, "classify", "ok",
            filename=filename, study_type=result.get("study_type") if isinstance(result, dict) else None,
        )
        return result
    except HTTPException as he:
        _refund_annotator(user, annotator_mod.CREDIT_COST_CLASSIFY, "classify", filename)
        _log_annotator_action_safe(
            user["id"], pid, "classify", "error",
            filename=filename, error=str(he.detail)[:300],
        )
        raise
    except Exception as e:
        _refund_annotator(user, annotator_mod.CREDIT_COST_CLASSIFY, "classify", filename)
        _log_annotator_action_safe(
            user["id"], pid, "classify", "error",
            filename=filename, error=str(e)[:300],
        )
        logger.error("Annotator classify failed: %s", e)
        raise HTTPException(502, f"Classification failed: {e}")


@app.post("/api/annotator/papers/{pid}/prefill")
def api_annotator_prefill(pid: int, body: dict,
                          rubricgen_session: str | None = Cookie(default=None),
                          x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """AI-extract structured fields for the given study_type. Credit-gated.

    Optional ``groups`` (list of field-group IDs) narrows which universal
    fields are extracted. Omitted → full extraction.
    """
    body = body or {}
    study_type = body.get("study_type", "")
    groups = body.get("groups") or None
    # Pass explicit [] to disable, None (omit) to include all
    type_fields = body.get("type_fields")
    modifier_fields = body.get("modifier_fields")
    if not study_type:
        raise HTTPException(400, "study_type is required")
    if groups is not None and not isinstance(groups, list):
        raise HTTPException(400, "groups must be a list of group IDs")
    if type_fields is not None and not isinstance(type_fields, list):
        raise HTTPException(400, "type_fields must be a list of field IDs")
    if modifier_fields is not None and not isinstance(modifier_fields, list):
        raise HTTPException(400, "modifier_fields must be a list of field IDs")

    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        is_admin = user.get("role") == "admin"
        pdf_bytes, filename = annotator_mod.load_paper_pdf(
            conn, PAPERS_DIR, pid, user["id"], is_admin=is_admin
        )
        _annotator_ai_gate(conn, user,
                           annotator_mod.CREDIT_COST_PREFILL,
                           f"Annotator prefill: {filename}")
    finally:
        conn.close()

    try:
        result = annotator_mod.prefill_fields(
            pdf_bytes, study_type,
            groups=groups,
            type_fields=type_fields,
            modifier_fields=modifier_fields,
        )
        _log_annotator_action_safe(
            user["id"], pid, "prefill", "ok",
            filename=filename, study_type=study_type,
            fields_count=len(result) if isinstance(result, dict) else 0,
        )
        return result
    except HTTPException as he:
        _refund_annotator(user, annotator_mod.CREDIT_COST_PREFILL, "prefill", filename)
        _log_annotator_action_safe(
            user["id"], pid, "prefill", "error",
            filename=filename, study_type=study_type, error=str(he.detail)[:300],
        )
        raise
    except Exception as e:
        _refund_annotator(user, annotator_mod.CREDIT_COST_PREFILL, "prefill", filename)
        _log_annotator_action_safe(
            user["id"], pid, "prefill", "error",
            filename=filename, study_type=study_type, error=str(e)[:300],
        )
        logger.error("Annotator prefill failed: %s", e)
        raise HTTPException(502, f"Prefill failed: {e}")


def _refund_annotator(user: dict, amount: int, op: str, filename: str) -> None:
    """Refund a failed annotator AI call. Silent on double-fault."""
    if user.get("role") == "admin":
        return
    try:
        rc = get_db()
        try:
            bill.refund_credits(rc, user["id"], amount,
                                f"Refund: Annotator {op} on {filename} failed")
        finally:
            rc.close()
    except Exception as refund_err:
        logger.error("Annotator refund failed: %s", refund_err)


def _log_annotator_action_safe(user_id: int, paper_id: int | None,
                               action_type: str, status: str,
                               schema_id: int | None = None,
                               run_id: int | None = None,
                               **detail) -> None:
    """Open a short-lived DB conn just to log an annotator action. Never raises."""
    try:
        conn = get_db()
        try:
            annotator_mod.log_annotator_action(
                conn, user_id, paper_id, action_type, status,
                schema_id=schema_id, run_id=run_id,
                detail=detail or None,
            )
        finally:
            conn.close()
    except Exception:
        pass


def _log_run_event_safe(run_id: int, event_type: str, message: str, **detail) -> None:
    """Append a per-run progress event from anywhere (incl. worker threads). Never raises."""
    try:
        conn = get_db()
        try:
            annotator_mod.log_run_event(conn, run_id, event_type, message,
                                        detail=detail or None)
        finally:
            conn.close()
    except Exception:
        pass


@app.get("/api/annotator/papers/{pid}/custom-runs")
def api_annotator_paper_custom_runs(pid: int,
                                    rubricgen_session: str | None = Cookie(default=None),
                                    x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """All custom-schema extractions for this paper, most recent first.
    Used by the Form tab's "Custom-schema extractions" panel so users can see
    AI-extracted values in context with the paper without merging them into
    the canonical annotation row.
    """
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    is_admin = user.get("role") == "admin"
    conn = get_db()
    try:
        # Ownership check (admin bypass).
        if not is_admin:
            own = conn.execute(
                "SELECT 1 FROM papers WHERE id=? AND user_id=?",
                (pid, user["id"]),
            ).fetchone()
            if not own:
                raise HTTPException(404, "paper not found")
        rows = conn.execute(
            """SELECT r.id, r.schema_id, r.schema_snapshot_json, r.results_json,
                      r.status, r.created_at, s.name AS schema_name
                 FROM annotator_custom_runs r
            LEFT JOIN annotator_custom_schemas s ON s.id = r.schema_id
                WHERE r.user_id = ?
                ORDER BY r.created_at DESC""",
            (user["id"],),
        ).fetchall()
    finally:
        conn.close()

    pid_key = str(pid)
    out: list[dict] = []
    for r in rows:
        try:
            results = json.loads(r["results_json"] or "{}")
        except Exception:
            continue
        paper_entry = (results.get("papers") or {}).get(pid_key)
        if not paper_entry:
            continue  # this run didn't include this paper
        try:
            snap = json.loads(r["schema_snapshot_json"] or "{}")
        except Exception:
            snap = {}
        field_defs = snap.get("fields") or []
        extracted = paper_entry.get("fields") or {}
        out.append({
            "run_id": r["id"],
            "schema_id": r["schema_id"],
            "schema_name": r["schema_name"] or snap.get("name") or "(deleted schema)",
            "status": paper_entry.get("status", "unknown"),
            "error": paper_entry.get("error"),
            "created_at": r["created_at"],
            "fields": [
                {"id": f.get("id"), "label": f.get("label", f.get("id", "")),
                 "value": str(extracted.get(f.get("id"), "") or "")}
                for f in field_defs
            ],
        })
    return out


@app.get("/api/annotator/actions")
def api_annotator_actions(limit: int = 50,
                          rubricgen_session: str | None = Cookie(default=None),
                          x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Recent annotator activity across all action types (classify / prefill / custom).
    Powers the "Recent activity" view in the Results tab."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    try:
        limit = max(1, min(int(limit), 200))
    except Exception:
        limit = 50
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT a.id, a.paper_id, a.action_type, a.schema_id, a.run_id,
                      a.status, a.detail_json, a.created_at,
                      p.filename AS paper_filename,
                      s.name     AS schema_name
                 FROM annotator_actions a
            LEFT JOIN papers p                    ON p.id = a.paper_id
            LEFT JOIN annotator_custom_schemas s  ON s.id = a.schema_id
                WHERE a.user_id = ?
                ORDER BY a.created_at DESC
                LIMIT ?""",
            (user["id"], limit),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        try:
            detail = json.loads(r["detail_json"] or "{}")
        except Exception:
            detail = {}
        out.append({
            "id": r["id"],
            "paper_id": r["paper_id"],
            "paper_filename": r["paper_filename"] or detail.get("filename"),
            "action_type": r["action_type"],
            "schema_id": r["schema_id"],
            "schema_name": r["schema_name"],
            "run_id": r["run_id"],
            "status": r["status"],
            "detail": detail,
            "created_at": r["created_at"],
        })
    return out


@app.get("/api/annotator/analytics")
def api_annotator_analytics(project_id: int | None = None,
                            paper_ids: str | None = None,
                            rubricgen_session: str | None = Cookie(default=None),
                            x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Aggregate analytics over a user's annotated papers.

    Scope: ``project_id=N`` OR ``paper_ids=1,2,3``. Omit both for all-user.
    """
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return annotator_mod.build_analytics(conn, user["id"], project_id, paper_ids)
    finally:
        conn.close()


# ─────────────────────────────────────────────
# Custom-extraction schemas (user-defined field sets)
# ─────────────────────────────────────────────

class CustomSchemaSavePayload(BaseModel):
    name: str
    description: str = ""
    fields: list[dict] = []


class CustomSchemaRefinePayload(BaseModel):
    fields: list[dict] = []
    instruction: str


class CustomSchemaParseTextPayload(BaseModel):
    text: str


class CustomSchemaRunPayload(BaseModel):
    paper_ids: list[int] = []
    # When true, Claude's extended-thinking output is captured per paper and
    # emitted as a `paper_thinking` event so the UI can show reasoning blocks.
    # Costs ~50% more credits per paper.
    thinking_enabled: bool = False
    # When set, the worker writes results into this existing run row (created
    # by POST /api/annotator/runs) instead of creating a new one. Lets a
    # single "batch" row hold classify + prefill + custom output together.
    run_id: int | None = None


class BatchContainerPayload(BaseModel):
    """Create an annotator_custom_runs row up-front so every batch (classify,
    prefill, custom, or any combination) is visible in the Results tab."""
    name: str = ""
    project_id: int | None = None
    paper_ids: list[int] = []
    did_classify: bool = False
    did_prefill: bool = False
    schema_id: int | None = None  # None = no custom schema; classify/prefill only


class BatchPaperResultPayload(BaseModel):
    """Append a single paper's per-step output to a batch's results_json.
    Called by the frontend after each /classify and /prefill so the Results
    tab can show the same fields for non-custom batches."""
    paper_id: int
    filename: str | None = None
    status: str = "ok"     # 'ok' | 'error' | 'skipped'
    fields: dict = {}      # merged into results_json.papers[pid].fields
    error: str | None = None


def _validate_schema_name(name: str) -> str:
    name = (name or "").strip()
    if not (1 <= len(name) <= 120):
        raise HTTPException(400, "schema name must be 1–120 chars")
    return name


@app.post("/api/annotator/schemas/parse")
async def api_annotator_schemas_parse(
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    rubricgen_session: str | None = Cookie(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    """Propose extraction fields from an uploaded protocol (PDF) or pasted text."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        _annotator_ai_gate(conn, user,
                           annotator_mod.CREDIT_COST_SCHEMA_PARSE,
                           "Annotator schema parse")
    finally:
        conn.close()

    try:
        if file is not None:
            content = await file.read()
            if not content:
                raise HTTPException(400, "uploaded file is empty")
            fname = (file.filename or "").lower()
            if fname.endswith(".pdf"):
                fields = annotator_mod.parse_schema_from_pdf(content)
            elif fname.endswith(".docx"):
                # Word 2007+ — convert to markdown then treat as text
                fields = annotator_mod.parse_schema_from_docx(content)
            elif fname.endswith(".doc"):
                # Legacy Word binary format — python-docx can't read it.
                raise HTTPException(
                    400,
                    "legacy .doc files are not supported — please save as .docx or paste the text.",
                )
            else:
                # CSV / TXT / MD / anything else: decode as text
                try:
                    text_body = content.decode("utf-8", errors="replace")
                except Exception:
                    raise HTTPException(400, "could not decode file as text")
                fields = annotator_mod.parse_schema_from_text(text_body)
        elif text is not None and text.strip():
            fields = annotator_mod.parse_schema_from_text(text)
        else:
            raise HTTPException(400, "provide either 'file' or 'text'")
        return {"proposed_fields": fields}
    except HTTPException:
        _refund_annotator(user, annotator_mod.CREDIT_COST_SCHEMA_PARSE, "schema-parse", "")
        raise
    except Exception as e:
        _refund_annotator(user, annotator_mod.CREDIT_COST_SCHEMA_PARSE, "schema-parse", "")
        logger.error("Schema parse failed: %s", e)
        raise HTTPException(502, f"Schema parse failed: {e}")


@app.post("/api/annotator/schemas/refine")
def api_annotator_schemas_refine(body: CustomSchemaRefinePayload,
                                 rubricgen_session: str | None = Cookie(default=None),
                                 x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        _annotator_ai_gate(conn, user,
                           annotator_mod.CREDIT_COST_SCHEMA_REFINE,
                           "Annotator schema refine")
    finally:
        conn.close()
    try:
        fields = annotator_mod.refine_schema(body.fields, body.instruction)
        return {"proposed_fields": fields}
    except HTTPException:
        _refund_annotator(user, annotator_mod.CREDIT_COST_SCHEMA_REFINE, "schema-refine", "")
        raise
    except Exception as e:
        _refund_annotator(user, annotator_mod.CREDIT_COST_SCHEMA_REFINE, "schema-refine", "")
        logger.error("Schema refine failed: %s", e)
        raise HTTPException(502, f"Schema refine failed: {e}")


@app.get("/api/annotator/schemas")
def api_annotator_schemas_list(rubricgen_session: str | None = Cookie(default=None),
                               x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT id, name, description, fields_json, created_at
                 FROM annotator_custom_schemas
                WHERE user_id=? ORDER BY created_at DESC""",
            (user["id"],),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        try:
            fields = json.loads(r["fields_json"] or "[]")
        except Exception:
            fields = []
        out.append({
            "id": r["id"], "name": r["name"],
            "description": r["description"] or "",
            "fields": fields, "field_count": len(fields),
            "created_at": r["created_at"],
        })
    return out


@app.post("/api/annotator/schemas", status_code=201)
def api_annotator_schemas_create(body: CustomSchemaSavePayload,
                                 rubricgen_session: str | None = Cookie(default=None),
                                 x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    name = _validate_schema_name(body.name)
    fields = annotator_mod.validate_custom_fields(body.fields)
    conn = get_db()
    try:
        with conn:
            try:
                cur = conn.execute(
                    """INSERT INTO annotator_custom_schemas
                            (user_id, name, description, fields_json)
                       VALUES (?, ?, ?, ?) RETURNING id""",
                    (user["id"], name, (body.description or "").strip(),
                     json.dumps(fields)),
                )
                sid = cur.lastrowid
                conn.commit()
            except IntegrityError:
                raise HTTPException(409, f"schema name already exists: {name}")
    finally:
        conn.close()
    return {"id": sid, "name": name, "fields": fields}


@app.get("/api/annotator/schemas/{sid}")
def api_annotator_schemas_get(sid: int,
                              rubricgen_session: str | None = Cookie(default=None),
                              x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT id, name, description, fields_json, created_at
                 FROM annotator_custom_schemas WHERE id=? AND user_id=?""",
            (sid, user["id"]),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "schema not found")
    try:
        fields = json.loads(row["fields_json"] or "[]")
    except Exception:
        fields = []
    return {"id": row["id"], "name": row["name"],
            "description": row["description"] or "",
            "fields": fields, "created_at": row["created_at"]}


@app.patch("/api/annotator/schemas/{sid}")
def api_annotator_schemas_update(sid: int, body: CustomSchemaSavePayload,
                                 rubricgen_session: str | None = Cookie(default=None),
                                 x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    name = _validate_schema_name(body.name)
    fields = annotator_mod.validate_custom_fields(body.fields)
    conn = get_db()
    try:
        exists = conn.execute(
            "SELECT id FROM annotator_custom_schemas WHERE id=? AND user_id=?",
            (sid, user["id"]),
        ).fetchone()
        if not exists:
            raise HTTPException(404, "schema not found")
        with conn:
            try:
                conn.execute(
                    """UPDATE annotator_custom_schemas
                          SET name=?, description=?, fields_json=?
                        WHERE id=? AND user_id=?""",
                    (name, (body.description or "").strip(),
                     json.dumps(fields), sid, user["id"]),
                )
                conn.commit()
            except IntegrityError:
                raise HTTPException(409, f"schema name already exists: {name}")
    finally:
        conn.close()
    return {"id": sid, "name": name, "fields": fields}


@app.delete("/api/annotator/schemas/{sid}")
def api_annotator_schemas_delete(sid: int,
                                 rubricgen_session: str | None = Cookie(default=None),
                                 x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        with conn:
            conn.execute(
                "DELETE FROM annotator_custom_schemas WHERE id=? AND user_id=?",
                (sid, user["id"]),
            )
            conn.commit()
    finally:
        conn.close()
    return {"ok": True}


# ─────────────────────────────────────────────
# Custom-extraction runs
# ─────────────────────────────────────────────

def _run_custom_extraction(user_id: int, is_admin: bool, run_id: int,
                           schema_id: int | None,
                           fields: list[dict], paper_ids: list[int],
                           thinking_enabled: bool = False) -> None:
    """Execute a custom-extraction run. Safe to call from a background thread.

    Writes ``results_json`` + ``status`` on completion, refunds per-paper on
    failure. Each paper is isolated: a deleted paper or a flaky LLM call
    fails just that row, not the whole run.

    When ``thinking_enabled``, Claude's extended thinking is captured per paper
    and emitted as a ``paper_thinking`` event for live display in the UI.
    """
    from backend import billing as bill_mod
    per_paper_cost = annotator_mod.CREDIT_COST_CUSTOM_PREFILL
    paper_results: dict[str, Any] = {}
    refunded = 0
    # Reasonable default for medical-paper extraction; calibrated to keep cost
    # bump in the ~50% range vs non-thinking runs.
    THINKING_BUDGET_TOKENS = 4000

    def _mark(status: str, payload: dict) -> None:
        # MERGE into existing results_json rather than overwrite, so classify /
        # prefill output that the frontend PATCHed in (via the container flow)
        # is preserved alongside the custom-schema output the worker produces.
        conn2 = get_db()
        try:
            existing_row = conn2.execute(
                "SELECT results_json FROM annotator_custom_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            try:
                existing = json.loads(existing_row["results_json"] or "{}") if existing_row else {}
            except Exception:
                existing = {}
            existing_papers = existing.get("papers") or {}
            new_papers = payload.get("papers") or {}
            for pid_key, new_entry in new_papers.items():
                prev = existing_papers.get(pid_key) or {}
                merged_fields = dict(prev.get("fields") or {})
                merged_fields.update(new_entry.get("fields") or {})
                existing_papers[pid_key] = {
                    **prev,
                    **{k: v for k, v in new_entry.items() if k != "fields"},
                    "fields": merged_fields,
                }
            existing["papers"] = existing_papers
            with conn2:
                conn2.execute(
                    """UPDATE annotator_custom_runs
                          SET status=?, results_json=?, credits_refunded=?,
                              error_message=?, completed_at=CURRENT_TIMESTAMP
                        WHERE id=?""",
                    (status, json.dumps(existing), refunded,
                     payload.get("error_message"), run_id),
                )
                conn2.commit()
        finally:
            conn2.close()

    total = len(paper_ids)
    _log_run_event_safe(run_id, "run_started",
                        f"Starting custom extraction on {total} paper(s)",
                        total=total, fields_count=len(fields))

    ok_count = 0
    err_count = 0
    skip_count = 0
    for idx, pid in enumerate(paper_ids, start=1):
        paper_conn = get_db()
        entry: dict[str, Any] = {"status": "error", "filename": None,
                                 "fields": {}, "error": None,
                                 "completed_at": None}
        try:
            try:
                pdf_bytes, filename = annotator_mod.load_paper_pdf(
                    paper_conn, PAPERS_DIR, pid, user_id, is_admin=is_admin
                )
                entry["filename"] = filename
                _log_run_event_safe(
                    run_id, "paper_started",
                    f"[{idx}/{total}] Loaded {filename}",
                    paper_id=pid, paper_index=idx, total=total, filename=filename,
                )
            except HTTPException as he:
                code = getattr(he, "status_code", 0)
                if code == 404:
                    entry["status"] = "skipped_deleted"
                elif code == 403:
                    entry["status"] = "skipped_permission_denied"
                else:
                    entry["status"] = "error"
                entry["error"] = str(he.detail)
                entry["completed_at"] = datetime.now(timezone.utc).isoformat()
                paper_results[str(pid)] = entry
                if not is_admin:
                    try:
                        bill_mod.refund_credits(paper_conn, user_id,
                                                per_paper_cost,
                                                f"Refund: custom run {run_id} paper {pid}")
                        refunded += per_paper_cost
                    except Exception:
                        pass
                _log_run_event_safe(
                    run_id, "paper_skipped",
                    f"[{idx}/{total}] Skipped paper #{pid} ({entry['status']}): {entry['error'][:100]}",
                    paper_id=pid, paper_index=idx, total=total,
                    status=entry["status"], error=entry["error"],
                )
                skip_count += 1
                continue
        finally:
            paper_conn.close()

        _log_run_event_safe(
            run_id, "extracting",
            f"[{idx}/{total}] Extracting {len(fields)} field(s) from {filename}",
            paper_id=pid, paper_index=idx, total=total,
            filename=filename, fields_count=len(fields),
        )

        try:
            if thinking_enabled:
                extracted, thinking_text = annotator_mod.extract_custom_fields(
                    pdf_bytes, fields, thinking_budget=THINKING_BUDGET_TOKENS,
                )
                if thinking_text:
                    _log_run_event_safe(
                        run_id, "paper_thinking",
                        f"[{idx}/{total}] Reasoning ({len(thinking_text)} chars)",
                        paper_id=pid, paper_index=idx, total=total,
                        filename=filename, thinking=thinking_text,
                    )
            else:
                extracted = annotator_mod.extract_custom_fields(pdf_bytes, fields)
            entry["fields"] = extracted
            entry["status"] = "ok"
        except Exception as e:
            logger.error("Custom extraction failed (run=%s paper=%s): %s",
                         run_id, pid, e)
            entry["status"] = "error"
            entry["error"] = str(e)
            if not is_admin:
                refund_conn = get_db()
                try:
                    bill_mod.refund_credits(refund_conn, user_id,
                                            per_paper_cost,
                                            f"Refund: custom run {run_id} paper {pid}")
                    refunded += per_paper_cost
                except Exception:
                    pass
                finally:
                    refund_conn.close()

        entry["completed_at"] = datetime.now(timezone.utc).isoformat()
        paper_results[str(pid)] = entry
        # Per-paper action log (status mirrors the run entry's status field).
        _log_annotator_action_safe(
            user_id, pid, "custom", entry["status"],
            schema_id=schema_id, run_id=run_id,
            filename=entry.get("filename"),
            error=(entry.get("error") or None),
            fields_count=len(entry.get("fields") or {}),
        )

        if entry["status"] == "ok":
            ok_count += 1
            _log_run_event_safe(
                run_id, "paper_done",
                f"[{idx}/{total}] {filename} — extracted {len(entry['fields'])} field(s)",
                paper_id=pid, paper_index=idx, total=total,
                filename=filename, fields_count=len(entry["fields"]),
            )
        else:
            err_count += 1
            _log_run_event_safe(
                run_id, "paper_error",
                f"[{idx}/{total}] {filename or ('#' + str(pid))} — {entry['error'][:120] if entry['error'] else 'failed'}",
                paper_id=pid, paper_index=idx, total=total,
                filename=entry.get("filename"), error=entry["error"],
            )

    _mark("complete", {"papers": paper_results})
    _log_run_event_safe(
        run_id, "run_complete",
        f"Run complete — {ok_count} ok, {err_count} failed, {skip_count} skipped",
        ok=ok_count, error=err_count, skipped=skip_count, total=total,
        refunded=refunded,
    )


def _run_custom_extraction_async(user_id: int, is_admin: bool, run_id: int,
                                 schema_id: int | None,
                                 fields: list[dict], paper_ids: list[int],
                                 thinking_enabled: bool = False) -> None:
    t = threading.Thread(
        target=_run_custom_extraction,
        args=(user_id, is_admin, run_id, schema_id, fields, paper_ids,
              thinking_enabled),
        daemon=True,
        name=f"annotator-custom-run-{run_id}",
    )
    t.start()


@app.post("/api/annotator/runs", status_code=201)
def api_annotator_create_batch(body: BatchContainerPayload,
                               rubricgen_session: str | None = Cookie(default=None),
                               x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Create a batch container so EVERY batch — classify, prefill, custom,
    or a combination — shows up in the Results tab. The frontend POSTs here
    before kicking off per-paper work, then PATCHes per-paper outputs in,
    then POSTs /finalize when done.

    The batch name is REQUIRED and must be unique per user — that lets people
    tell their runs apart in the Results list later. Returns 400 if missing,
    409 if a non-deleted batch with the same name already exists."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    paper_ids = [int(p) for p in (body.paper_ids or [])]
    if not paper_ids:
        raise HTTPException(400, "paper_ids must be a non-empty list")
    if len(paper_ids) > 200:
        raise HTTPException(400, "at most 200 papers per batch")

    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, {
            "error": "name_required",
            "detail": "Batch name is required so you can find this run later in Results.",
        })
    if len(name) > 120:
        raise HTTPException(400, {
            "error": "name_too_long",
            "detail": "Batch name must be 120 characters or fewer.",
        })

    snapshot = _batch_snapshot(body)
    conn = get_db()
    try:
        # Per-user uniqueness check (case-insensitive). We can't add a UNIQUE
        # constraint cleanly because legacy runs may share names — the check
        # at INSERT time is the canonical guard for new runs.
        existing = conn.execute(
            """SELECT id FROM annotator_custom_runs
                WHERE user_id = ? AND LOWER(name) = LOWER(?)
                LIMIT 1""",
            (user["id"], name),
        ).fetchone()
        if existing:
            raise HTTPException(409, {
                "error": "name_taken",
                "detail": f"You already have a batch named “{name}”. Pick a different name.",
                "existing_run_id": existing["id"],
            })
        with conn:
            cur = conn.execute(
                """INSERT INTO annotator_custom_runs
                        (user_id, schema_id, schema_snapshot_json, paper_ids_json,
                         credit_cost, status, name, project_id,
                         did_classify, did_prefill)
                   VALUES (?, ?, ?, ?, 0, 'running', ?, ?, ?, ?) RETURNING id""",
                (user["id"], body.schema_id, json.dumps(snapshot),
                 json.dumps(paper_ids), name, body.project_id,
                 1 if body.did_classify else 0, 1 if body.did_prefill else 0),
            )
            run_id = cur.lastrowid
            conn.commit()
    finally:
        conn.close()
    _log_run_event_safe(
        run_id, "run_started",
        f"Batch '{name}' starting on {len(paper_ids)} paper(s)",
        total=len(paper_ids), name=name,
        operations=_batch_ops_label(body),
    )
    return {"run_id": run_id, "name": name, "status": "running"}


@app.patch("/api/annotator/runs/{rid}/papers")
def api_annotator_batch_paper_result(rid: int, body: BatchPaperResultPayload,
                                     rubricgen_session: str | None = Cookie(default=None),
                                     x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Append (or update) one paper's outputs into the batch's results_json."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, results_json FROM annotator_custom_runs WHERE id=? AND user_id=?",
            (rid, user["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(404, "run not found")
        try:
            results = json.loads(row["results_json"] or "{}")
        except Exception:
            results = {}
        papers = results.get("papers") or {}
        pid_str = str(int(body.paper_id))
        existing = papers.get(pid_str) or {"fields": {}}
        # Merge fields rather than overwrite — classify and prefill arrive
        # as separate calls but should accumulate into the same paper row.
        merged_fields = dict(existing.get("fields") or {})
        merged_fields.update(body.fields or {})
        papers[pid_str] = {
            "filename": body.filename or existing.get("filename"),
            "status": body.status or existing.get("status") or "ok",
            "fields": merged_fields,
            "error": body.error if body.error is not None else existing.get("error"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        results["papers"] = papers
        with conn:
            conn.execute(
                "UPDATE annotator_custom_runs SET results_json=? WHERE id=?",
                (json.dumps(results), rid),
            )
            conn.commit()
    finally:
        conn.close()
    return {"ok": True}


class BatchPatchPayload(BaseModel):
    """Update an existing batch's project assignment or name. Both optional —
    omit a field to leave it unchanged."""
    project_id: int | None = None      # set to a project ID, or null to clear
    project_id_set: bool = False       # explicit "yes I'm sending project_id"
    name: str | None = None


@app.patch("/api/annotator/runs/{rid}")
def api_annotator_runs_patch(rid: int, body: BatchPatchPayload,
                             rubricgen_session: str | None = Cookie(default=None),
                             x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Move a batch into (or out of) a project, or rename it. Used by the
    Results-list "Move to project" dropdown so a user can save batches into
    project folders after the fact."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        own = conn.execute(
            "SELECT id, name FROM annotator_custom_runs WHERE id=? AND user_id=?",
            (rid, user["id"]),
        ).fetchone()
        if not own:
            raise HTTPException(404, "run not found")

        updates: list[tuple[str, object]] = []

        # Name change — re-validate uniqueness if provided.
        if body.name is not None:
            new_name = body.name.strip()
            if not new_name:
                raise HTTPException(400, "name must not be blank")
            if len(new_name) > 120:
                raise HTTPException(400, "name must be 120 characters or fewer")
            if new_name.lower() != (own["name"] or "").lower():
                clash = conn.execute(
                    """SELECT id FROM annotator_custom_runs
                        WHERE user_id=? AND id != ? AND LOWER(name) = LOWER(?)
                        LIMIT 1""",
                    (user["id"], rid, new_name),
                ).fetchone()
                if clash:
                    raise HTTPException(409, {
                        "error": "name_taken",
                        "detail": f"You already have a batch named “{new_name}”.",
                    })
            updates.append(("name", new_name))

        # Project change — verify ownership before assigning.
        if body.project_id_set:
            if body.project_id is not None:
                proj = conn.execute(
                    "SELECT id FROM projects WHERE id=? AND user_id=?",
                    (body.project_id, user["id"]),
                ).fetchone()
                if not proj:
                    raise HTTPException(404, "project not found")
            updates.append(("project_id", body.project_id))

        if not updates:
            return {"ok": True, "no_changes": True}

        set_clause = ", ".join(f"{col}=?" for col, _ in updates)
        values = [v for _, v in updates] + [rid]
        with conn:
            conn.execute(
                f"UPDATE annotator_custom_runs SET {set_clause} WHERE id=?",
                tuple(values),
            )
            conn.commit()
    finally:
        conn.close()
    return {"ok": True, "id": rid}


@app.post("/api/annotator/runs/{rid}/finalize")
def api_annotator_batch_finalize(rid: int,
                                 rubricgen_session: str | None = Cookie(default=None),
                                 x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Mark a batch complete + emit run_complete so the active-runs pill
    clears and the Results list refreshes."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, name, results_json FROM annotator_custom_runs WHERE id=? AND user_id=?",
            (rid, user["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(404, "run not found")
        try:
            papers = (json.loads(row["results_json"] or "{}").get("papers") or {})
        except Exception:
            papers = {}
        ok = sum(1 for p in papers.values() if (p.get("status") == "ok"))
        err = sum(1 for p in papers.values() if (p.get("status") == "error"))
        skip = sum(1 for p in papers.values() if str(p.get("status", "")).startswith("skipped"))
        with conn:
            conn.execute(
                "UPDATE annotator_custom_runs SET status='complete', completed_at=CURRENT_TIMESTAMP WHERE id=?",
                (rid,),
            )
            conn.commit()
    finally:
        conn.close()
    _log_run_event_safe(
        rid, "run_complete",
        f"Batch '{row['name'] or rid}' complete — {ok} ok, {err} failed, {skip} skipped",
        ok=ok, error=err, skipped=skip, total=len(papers), name=row["name"],
    )
    return {"ok": True, "ok_count": ok, "error_count": err, "skipped_count": skip}


def _default_batch_name(body: BatchContainerPayload, n: int) -> str:
    """e.g. 'Classify + Prefill — 5 papers'."""
    ops = []
    if body.did_classify: ops.append("Classify")
    if body.did_prefill:  ops.append("Prefill")
    if body.schema_id:    ops.append("Custom")
    if not ops:           ops = ["Batch"]
    return f"{' + '.join(ops)} — {n} paper{'s' if n != 1 else ''}"


def _batch_snapshot(body: BatchContainerPayload) -> dict:
    """Snapshot describing what this batch ran. Mirrors the shape that the
    Results-tab UI consumes: {name, fields: [{id, label}, ...]}.
    Fields are inferred from the operations selected — classify contributes
    study_type + subcategory; prefill contributes a placeholder that the
    actual extracted keys will populate. Custom-schema runs overwrite this
    snapshot via the existing /schemas/{sid}/run path."""
    fields: list[dict] = []
    if body.did_classify:
        fields.append({"id": "study_type",   "label": "Study Type"})
        fields.append({"id": "major_category", "label": "Major Category"})
        fields.append({"id": "subcategory",  "label": "Subcategory"})
    return {
        "name": "Batch run",
        "description": _batch_ops_label(body),
        "fields": fields,
    }


def _batch_ops_label(body: BatchContainerPayload) -> str:
    parts = []
    if body.did_classify: parts.append("classify")
    if body.did_prefill:  parts.append("prefill")
    if body.schema_id:    parts.append(f"custom_schema={body.schema_id}")
    return ", ".join(parts) or "no-op"


@app.post("/api/annotator/schemas/{sid}/run")
def api_annotator_schemas_run(sid: int, body: CustomSchemaRunPayload,
                              rubricgen_session: str | None = Cookie(default=None),
                              x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    paper_ids = [int(p) for p in (body.paper_ids or [])]
    if not paper_ids:
        raise HTTPException(400, "paper_ids must be a non-empty list")
    if len(paper_ids) > 100:
        raise HTTPException(400, "at most 100 papers per run")

    conn = get_db()
    try:
        # Load + validate schema
        srow = conn.execute(
            """SELECT id, name, description, fields_json
                 FROM annotator_custom_schemas WHERE id=? AND user_id=?""",
            (sid, user["id"]),
        ).fetchone()
        if not srow:
            raise HTTPException(404, "schema not found")
        try:
            fields = json.loads(srow["fields_json"] or "[]")
        except Exception:
            raise HTTPException(500, "schema has invalid fields_json")
        fields = annotator_mod.validate_custom_fields(fields)

        # Ownership-check every paper so an attacker can't run over someone else's files.
        placeholders = ",".join("?" * len(paper_ids))
        owned = conn.execute(
            f"SELECT id FROM papers WHERE user_id=? AND id IN ({placeholders})",
            (user["id"], *paper_ids),
        ).fetchall()
        owned_ids = [r["id"] for r in owned]
        missing = [p for p in paper_ids if p not in owned_ids]
        if missing:
            raise HTTPException(400, f"unknown or unowned paper ids: {missing}")

        # Credit pre-flight. Reasoning bumps cost ~50% per paper.
        per_paper = annotator_mod.CREDIT_COST_CUSTOM_PREFILL
        if body.thinking_enabled:
            per_paper = int(round(per_paper * 1.5))
        total_cost = len(paper_ids) * per_paper
        is_admin = user.get("role") == "admin"
        gate_label = (
            f"Annotator custom run (schema {sid}, {len(paper_ids)} papers"
            + (", reasoning on" if body.thinking_enabled else "")
            + ")"
        )
        _annotator_ai_gate(conn, user, total_cost, gate_label)

        snapshot = {"id": sid, "name": srow["name"],
                    "description": srow["description"] or "",
                    "fields": fields}
        # Reuse an existing container row when the caller passed run_id —
        # this is how the frontend stitches classify + prefill + custom into
        # one Results-tab row. We extend the existing snapshot's field list
        # so the table renders both the prior fields and the new ones.
        run_id: int | None = None
        if body.run_id:
            existing = conn.execute(
                "SELECT id, schema_snapshot_json FROM annotator_custom_runs WHERE id=? AND user_id=?",
                (body.run_id, user["id"]),
            ).fetchone()
            if not existing:
                raise HTTPException(404, "container run_id not found")
            run_id = existing["id"]
            try:
                prior_snap = json.loads(existing["schema_snapshot_json"] or "{}")
            except Exception:
                prior_snap = {}
            prior_fields = prior_snap.get("fields") or []
            seen = {f.get("id") for f in prior_fields}
            merged_fields = list(prior_fields) + [f for f in fields if f.get("id") not in seen]
            merged_snap = {**prior_snap,
                           "schema_id": sid,
                           "schema_name": srow["name"],
                           "fields": merged_fields}
            with conn:
                conn.execute(
                    """UPDATE annotator_custom_runs
                          SET schema_id=?, schema_snapshot_json=?,
                              credit_cost = COALESCE(credit_cost, 0) + ?
                        WHERE id=?""",
                    (sid, json.dumps(merged_snap), total_cost, run_id),
                )
                conn.commit()
        if run_id is None:
            with conn:
                cur = conn.execute(
                    """INSERT INTO annotator_custom_runs
                            (user_id, schema_id, schema_snapshot_json, paper_ids_json,
                             credit_cost, status)
                       VALUES (?, ?, ?, ?, ?, 'running') RETURNING id""",
                    (user["id"], sid, json.dumps(snapshot),
                     json.dumps(paper_ids), total_cost),
                )
                run_id = cur.lastrowid
                conn.commit()
    finally:
        conn.close()

    # Small runs finish inside the request; larger ones run in a background thread.
    if len(paper_ids) <= 10:
        _run_custom_extraction(user["id"], is_admin, run_id, sid, fields, paper_ids,
                               thinking_enabled=body.thinking_enabled)
        return {"run_id": run_id, "status": "complete"}
    _run_custom_extraction_async(user["id"], is_admin, run_id, sid, fields, paper_ids,
                                 thinking_enabled=body.thinking_enabled)
    return {"run_id": run_id, "status": "running"}


@app.get("/api/annotator/runs")
def api_annotator_runs_list(rubricgen_session: str | None = Cookie(default=None),
                            x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT r.id, r.schema_id, r.schema_snapshot_json,
                      r.paper_ids_json, r.status, r.credit_cost,
                      r.credits_refunded, r.created_at, r.completed_at,
                      r.name, r.project_id, r.did_classify, r.did_prefill,
                      s.name AS schema_name,
                      p.name AS project_name
                 FROM annotator_custom_runs r
            LEFT JOIN annotator_custom_schemas s ON s.id = r.schema_id
            LEFT JOIN projects p ON p.id = r.project_id
                WHERE r.user_id = ?
                ORDER BY r.created_at DESC LIMIT 100""",
            (user["id"],),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        try:
            pids = json.loads(r["paper_ids_json"] or "[]")
        except Exception:
            pids = []
        schema_name = r["schema_name"]
        if not schema_name:
            try:
                snap = json.loads(r["schema_snapshot_json"] or "{}")
                schema_name = snap.get("name") or None
            except Exception:
                schema_name = None
        # Display name falls back to the schema/snapshot name for legacy rows
        # that pre-date the dedicated `name` column.
        display_name = (r["name"] or schema_name or f"Run #{r['id']}").strip()
        out.append({
            "id": r["id"], "schema_id": r["schema_id"],
            "name": display_name,
            "schema_name": schema_name,
            "project_id": r["project_id"],
            "project_name": r["project_name"],
            "did_classify": bool(r["did_classify"]),
            "did_prefill": bool(r["did_prefill"]),
            "paper_count": len(pids), "status": r["status"],
            "credit_cost": r["credit_cost"] or 0,
            "credits_refunded": r["credits_refunded"] or 0,
            "created_at": r["created_at"],
            "completed_at": r["completed_at"],
        })
    return out


def _compute_run_aggregates(snapshot: dict, results: dict,
                            did_classify: bool) -> dict:
    """Compute per-batch aggregates from a run's results_json.

    Returns:
      {
        "study_type_breakdown": [{value, count, pct}, …]    # only if did_classify
        "field_aggregates": { field_id: {label, kind, summary} }
        "field_order": [field_id, …]   # display order
      }

    Field kinds:
      - "numeric"     ≥80% of non-empty values parse as float
      - "categorical" otherwise, when distinct ≤ ceil(n/3) AND ≤ 8
      - "text"        everything else

    Pure Python — no deps. Safe to call on partial / in-progress runs.
    """
    papers = (results or {}).get("papers") or {}
    n_papers = len(papers)

    # ── Study-type breakdown ────────────────────────────────────────────
    study_breakdown: list[dict] = []
    if did_classify:
        type_counts: dict[str, int] = {}
        for entry in papers.values():
            v = ((entry or {}).get("fields") or {}).get("study_type")
            if v not in (None, "", []):
                key = str(v)
                type_counts[key] = type_counts.get(key, 0) + 1
        for value, count in sorted(type_counts.items(), key=lambda kv: -kv[1]):
            pct = round(100.0 * count / n_papers, 1) if n_papers else 0.0
            study_breakdown.append({"value": value, "count": count, "pct": pct})

    # ── Field discovery (snapshot order first, then anything else) ──────
    snap_fields = (snapshot or {}).get("fields") or []
    snap_order = [f.get("id") for f in snap_fields if f.get("id")]
    snap_labels = {f.get("id"): (f.get("label") or f.get("id"))
                   for f in snap_fields if f.get("id")}
    seen_in_data: set[str] = set()
    for entry in papers.values():
        for k in ((entry or {}).get("fields") or {}).keys():
            seen_in_data.add(k)
    extras = sorted(seen_in_data - set(snap_order))
    field_order = [f for f in snap_order if f in seen_in_data] + extras

    # ── Per-field aggregates ────────────────────────────────────────────
    def _classify_kind(values: list) -> str:
        non_empty = [v for v in values if v not in (None, "", [])]
        if not non_empty:
            return "text"
        # Try numeric: strip commas/units, parse as float.
        n_num = 0
        for v in non_empty:
            try:
                float(str(v).replace(",", "").strip().split()[0])
                n_num += 1
            except (ValueError, IndexError):
                pass
        if n_num >= 0.8 * len(non_empty):
            return "numeric"
        unique = {str(v).strip() for v in non_empty}
        max_len = max(len(str(v).strip()) for v in non_empty)
        # Categorical when values are short labels (not sentences) AND repeat
        # often enough to be useful as a chart. The ceil(n/3) cap is bumped to
        # at least 3 so small batches (4-6 papers) still show categories.
        cap = max(3, -(-len(non_empty) // 3))
        if len(unique) <= min(cap, 8) and max_len <= 60:
            return "categorical"
        return "text"

    def _numeric_summary(values: list) -> dict:
        nums: list[float] = []
        for v in values:
            try:
                nums.append(float(str(v).replace(",", "").strip().split()[0]))
            except (ValueError, IndexError):
                continue
        if not nums:
            return {"n_with_value": 0, "n_total": len(values)}
        nums_sorted = sorted(nums)
        mid = len(nums_sorted) // 2
        median = (nums_sorted[mid] if len(nums_sorted) % 2 else
                  (nums_sorted[mid - 1] + nums_sorted[mid]) / 2)
        return {
            "median": round(median, 4),
            "mean": round(sum(nums) / len(nums), 4),
            "min": round(nums_sorted[0], 4),
            "max": round(nums_sorted[-1], 4),
            "n_with_value": len(nums),
            "n_total": n_papers,
        }

    def _categorical_summary(values: list) -> dict:
        counts: dict[str, int] = {}
        non_empty = [str(v).strip() for v in values if v not in (None, "", [])]
        for v in non_empty:
            counts[v] = counts.get(v, 0) + 1
        if not counts:
            return {"n_with_value": 0, "n_total": n_papers, "n_unique": 0}
        top, top_count = max(counts.items(), key=lambda kv: kv[1])
        return {
            "top": top,
            "top_count": top_count,
            "top_pct": round(100.0 * top_count / len(non_empty), 1),
            "n_unique": len(counts),
            "n_with_value": len(non_empty),
            "n_total": n_papers,
            "value_counts": [{"value": v, "count": c}
                             for v, c in sorted(counts.items(), key=lambda kv: -kv[1])],
        }

    def _text_summary(values: list) -> dict:
        non_empty = [str(v).strip() for v in values if v not in (None, "", [])]
        unique = list(dict.fromkeys(non_empty))   # preserve first-seen order
        return {
            "n_unique": len(set(unique)),
            "n_with_value": len(non_empty),
            "n_total": n_papers,
            "sample_values": unique[:3],
        }

    field_aggregates: dict[str, dict] = {}
    for fid in field_order:
        values = [((papers.get(pid) or {}).get("fields") or {}).get(fid)
                  for pid in papers.keys()]
        kind = _classify_kind(values)
        if kind == "numeric":
            summary = _numeric_summary(values)
        elif kind == "categorical":
            summary = _categorical_summary(values)
        else:
            summary = _text_summary(values)
        field_aggregates[fid] = {
            "label": snap_labels.get(fid, fid),
            "kind": kind,
            "summary": summary,
        }

    return {
        "study_type_breakdown": study_breakdown,
        "field_aggregates": field_aggregates,
        "field_order": field_order,
    }


@app.get("/api/annotator/runs/{rid}")
def api_annotator_runs_get(rid: int,
                           rubricgen_session: str | None = Cookie(default=None),
                           x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT id, schema_id, schema_snapshot_json, paper_ids_json,
                      results_json, status, credit_cost, credits_refunded,
                      error_message, created_at, completed_at,
                      name, project_id, did_classify, did_prefill
                 FROM annotator_custom_runs WHERE id=? AND user_id=?""",
            (rid, user["id"]),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "run not found")
    try:
        snap = json.loads(row["schema_snapshot_json"] or "{}")
    except Exception:
        snap = {}
    try:
        results = json.loads(row["results_json"] or "{}")
    except Exception:
        results = {}
    try:
        pids = json.loads(row["paper_ids_json"] or "[]")
    except Exception:
        pids = []
    aggregates = _compute_run_aggregates(snap, results, bool(row["did_classify"]))
    return {
        "id": row["id"],
        "schema_id": row["schema_id"],
        "schema": snap,
        "paper_ids": pids,
        "results": results,
        "status": row["status"],
        "credit_cost": row["credit_cost"] or 0,
        "credits_refunded": row["credits_refunded"] or 0,
        "error_message": row["error_message"],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
        "name": row["name"],
        "project_id": row["project_id"],
        "did_classify": bool(row["did_classify"]),
        "did_prefill": bool(row["did_prefill"]),
        # New: pivoted aggregates for the Results tab summary view.
        "study_type_breakdown": aggregates["study_type_breakdown"],
        "field_aggregates": aggregates["field_aggregates"],
        "field_order": aggregates["field_order"],
    }


@app.get("/api/annotator/runs/{rid}.csv")
def api_annotator_runs_csv(rid: int,
                           rubricgen_session: str | None = Cookie(default=None),
                           x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT schema_snapshot_json, results_json
                 FROM annotator_custom_runs WHERE id=? AND user_id=?""",
            (rid, user["id"]),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "run not found")
    try:
        snap = json.loads(row["schema_snapshot_json"] or "{}")
        results = json.loads(row["results_json"] or "{}").get("papers", {})
    except Exception:
        raise HTTPException(500, "corrupt run data")

    fields = snap.get("fields", [])
    field_ids = [f["id"] for f in fields]
    header = ["paper_id", "filename", "status"] + field_ids
    rows_out: list[str] = [annotator_mod._csv_row(header)]
    for pid_str, entry in results.items():
        line = [pid_str, entry.get("filename", ""), entry.get("status", "")]
        vals = entry.get("fields", {}) or {}
        line += [vals.get(fid, "") for fid in field_ids]
        rows_out.append(annotator_mod._csv_row(line))
    schema_name = snap.get("name") or f"run_{rid}"
    safe = (schema_name.replace('"', '') + "_" + str(rid) + ".csv").replace(" ", "_")
    return StreamingResponse(
        iter(rows_out),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{safe}"'},
    )


@app.get("/api/annotator/runs/{rid}/events")
def api_annotator_run_events(rid: int, after: int = 0,
                             rubricgen_session: str | None = Cookie(default=None),
                             x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Progress events for a custom-extraction run, newer than ``after``.
    Frontend polls with ?after=<last_event_id> to stream live progress.
    Same shape as /api/quality-appraisal/runs/{id}/events."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    try:
        after_id = max(0, int(after))
    except Exception:
        after_id = 0
    conn = get_db()
    try:
        # Ownership check on the run.
        own = conn.execute(
            "SELECT id, status FROM annotator_custom_runs WHERE id=? AND user_id=?",
            (rid, user["id"]),
        ).fetchone()
        if not own:
            raise HTTPException(404, "run not found")
        rows = conn.execute(
            """SELECT id, event_type, message, detail_json, created_at
                 FROM annotator_run_events
                WHERE run_id=? AND id > ?
                ORDER BY id ASC LIMIT 500""",
            (rid, after_id),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        try:
            detail = json.loads(r["detail_json"]) if r["detail_json"] else None
        except Exception:
            detail = None
        out.append({
            "id": r["id"],
            "event_type": r["event_type"],
            "message": r["message"],
            "detail": detail,
            "created_at": r["created_at"],
        })
    return {"run_status": own["status"], "events": out}


@app.delete("/api/annotator/runs/{rid}")
def api_annotator_runs_delete(rid: int,
                              rubricgen_session: str | None = Cookie(default=None),
                              x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Delete a past custom-extraction run. Only removes the run row — the
    source schema and the papers themselves are untouched. No credits are
    refunded by deletion (credits are already either consumed or refunded
    at run time based on status)."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM annotator_custom_runs WHERE id=? AND user_id=?",
            (rid, user["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(404, "run not found")
        with conn:
            conn.execute(
                "DELETE FROM annotator_custom_runs WHERE id=? AND user_id=?",
                (rid, user["id"]),
            )
            conn.commit()
    finally:
        conn.close()
    return {"ok": True, "deleted_id": rid}


@app.get("/api/annotator/export.csv")
def api_annotator_export_csv(paper_id: int | None = None,
                             project_id: int | None = None,
                             rubricgen_session: str | None = Cookie(default=None),
                             x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        rows, filename = annotator_mod.build_export_rows(
            conn, user["id"], paper_id=paper_id, project_id=project_id
        )
    finally:
        conn.close()
    safe_fn = filename.replace('"', '')
    return StreamingResponse(
        iter(rows),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{safe_fn}"'},
    )


# ─────────────────────────────────────────────
# Phase 8: Templates, Community Library & Ground Truth
# ─────────────────────────────────────────────

class TemplateCreatePayload(BaseModel):
    name: str
    description: str = ""
    rubric_type: str = "custom"
    template_json: str = "{}"

class TemplateUpdatePayload(BaseModel):
    name: str | None = None
    description: str | None = None
    template_json: str | None = None

class TemplatePublishPayload(BaseModel):
    title: str
    description: str = ""

class TemplateRatePayload(BaseModel):
    rating: int

class TemplateFromRubricPayload(BaseModel):
    rubric_id: int
    name: str
    description: str = ""

class GroundTruthImportPayload(BaseModel):
    rubric_id: int | None = None
    challenge_id: int | None = None
    annotations: list[dict]


# ─── Template CRUD ───

@app.post("/api/templates")
def api_create_template(body: TemplateCreatePayload, rubricgen_session: str | None = Cookie(default=None)):
    """Create a new rubric template."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        return tmpl_mod.create_template(conn, user["id"], body.name, body.description,
                                        body.rubric_type, body.template_json)
    finally:
        conn.close()


@app.post("/api/templates/from-rubric")
def api_create_template_from_rubric(body: TemplateFromRubricPayload, rubricgen_session: str | None = Cookie(default=None)):
    """Create a template from an existing rubric."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        return tmpl_mod.create_template_from_rubric(conn, body.rubric_id, user["id"], body.name, body.description)
    finally:
        conn.close()


@app.get("/api/templates")
def api_list_templates(rubricgen_session: str | None = Cookie(default=None)):
    """List current user's templates."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return tmpl_mod.list_user_templates(conn, user["id"])
    finally:
        conn.close()


@app.get("/api/templates/{template_id}")
def api_get_template(template_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Get a template with question stats."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return tmpl_mod.get_template(conn, template_id)
    finally:
        conn.close()


@app.put("/api/templates/{template_id}")
def api_update_template(template_id: int, body: TemplateUpdatePayload, rubricgen_session: str | None = Cookie(default=None)):
    """Update a template. Bumps version on content change."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return tmpl_mod.update_template(conn, template_id, user["id"], body.name, body.description, body.template_json)
    finally:
        conn.close()


@app.delete("/api/templates/{template_id}")
def api_delete_template(template_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Delete a template."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        tmpl_mod.delete_template(conn, template_id, user["id"])
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/templates/{template_id}/fork")
def api_fork_template(template_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Fork a template to your account."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        return tmpl_mod.fork_template(conn, template_id, user["id"])
    finally:
        conn.close()


@app.get("/api/templates/{template_id}/flagged")
def api_flagged_questions(template_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Get questions flagged as too easy or broken."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return tmpl_mod.get_flagged_questions(conn, template_id)
    finally:
        conn.close()


# ─── Community library ───

@app.post("/api/templates/{template_id}/publish")
def api_publish_template(template_id: int, body: TemplatePublishPayload, rubricgen_session: str | None = Cookie(default=None)):
    """Publish a template to the community library."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return tmpl_mod.publish_template(conn, template_id, user["id"], body.title, body.description)
    finally:
        conn.close()


@app.delete("/api/templates/{template_id}/publish")
def api_unpublish_template(template_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Remove a template from the community library."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "admin")
    conn = get_db()
    try:
        tmpl_mod.unpublish_template(conn, template_id, user["id"])
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/community/templates")
def api_community_templates(
    type: str | None = None,
    sort: str = "recent",
    search: str | None = None,
    page: int = 1,
    rubricgen_session: str | None = Cookie(default=None),
):
    """Browse the community template library."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    limit = 20
    offset = (page - 1) * limit
    conn = get_db()
    try:
        return tmpl_mod.list_community_templates(conn, type, sort, search, limit, offset)
    finally:
        conn.close()


@app.get("/api/community/templates/{community_id}")
def api_get_community_template(community_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Preview a community template."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return tmpl_mod.get_community_template(conn, community_id)
    finally:
        conn.close()


@app.post("/api/community/templates/{community_id}/rate")
def api_rate_community_template(community_id: int, body: TemplateRatePayload, rubricgen_session: str | None = Cookie(default=None)):
    """Rate a community template (1-5)."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return tmpl_mod.rate_template(conn, community_id, user["id"], body.rating)
    finally:
        conn.close()


@app.post("/api/community/templates/{community_id}/fork")
def api_fork_community_template(community_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Fork a community template to your account."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        return tmpl_mod.fork_community_template(conn, community_id, user["id"])
    finally:
        conn.close()


# ─── Comparative rubric generation ───

@app.post("/api/rubrics/generate-comparative")
def api_generate_comparative(body: dict, rubricgen_session: str | None = Cookie(default=None)):
    """Generate a comparative rubric from multiple papers."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    paper_ids = body.get("paper_ids", [])
    if len(paper_ids) < 2:
        raise HTTPException(400, "Comparative rubrics require at least 2 papers")
    if len(paper_ids) > 5:
        raise HTTPException(400, "Comparative rubrics support at most 5 papers")

    conn = get_db()
    try:
        import base64
        from backend.agents.generator import run_generator_agent
        from backend.skills import get_active_skill

        papers_b64 = []
        for pid in paper_ids:
            paper = conn.execute(
                "SELECT filename, disk_filename, storage_path FROM papers WHERE id=? AND user_id=?",
                (pid, user["id"]),
            ).fetchone()
            if not paper:
                raise HTTPException(404, f"Paper {pid} not found")
            data = paper_files_mod.read_paper_bytes(paper, PAPERS_DIR)
            b64 = base64.b64encode(data).decode()
            papers_b64.append({"id": pid, "filename": paper["filename"], "b64": b64})

        skill = get_active_skill(conn, "generator")
        rubric, elapsed_ms = run_generator_agent(
            papers_b64, "__comparative__",
            skill, difficulty=None, daily_composition=None,
        )
        rubric["rubric_type"] = "comparative"

        # Save as a rubric linked to the first paper
        rubric_json = json.dumps(rubric)
        with conn:
            cur = conn.execute(
                """INSERT INTO rubrics (paper_id, user_id, rubric_type, rubric_json, instructions)
                   VALUES (?, ?, 'comparative', ?, ?) RETURNING id""",
                (paper_ids[0], user["id"], rubric_json,
                 f"Comparative rubric across papers: {', '.join(str(p) for p in paper_ids)}"),
            )
            conn.commit()

        return {
            "rubric_id": cur.lastrowid,
            "rubric": rubric,
            "generation_time_ms": elapsed_ms,
            "paper_ids": paper_ids,
        }
    finally:
        conn.close()


# ─── Ground truth / Annotator integration ───

@app.post("/api/annotator/import")
def api_import_ground_truth(body: GroundTruthImportPayload, request: Request,
                            rubricgen_session: str | None = Cookie(default=None)):
    """Import ground truth annotations. Accepts either authenticated user or HMAC-signed request from Annotator."""
    # Try user auth first
    user = _get_user_from_token(rubricgen_session)

    # If no user auth, try HMAC verification from Annotator
    if not user:
        hmac_sig = request.headers.get("X-Annotator-Signature", "")
        if not hmac_sig or not SSO_SECRET:
            raise HTTPException(401, "Authentication required")
        import hmac as hmac_mod
        import hashlib
        expected = hmac_mod.new(SSO_SECRET.encode(), request.url.path.encode(), hashlib.sha256).hexdigest()
        if not hmac_mod.compare_digest(hmac_sig, expected):
            raise HTTPException(401, "Invalid signature")

    conn = get_db()
    try:
        count = tmpl_mod.import_ground_truth(
            conn, body.annotations, body.rubric_id, body.challenge_id,
        )
        return {"imported": count}
    finally:
        conn.close()


# ─────────────────────────────────────────────
# Quality Appraisal AI — risk-of-bias + reporting-guideline + GRADE
# ─────────────────────────────────────────────

class QualityAppraisalTargetPico(BaseModel):
    population: str | None = None
    intervention: str | None = None
    comparator: str | None = None
    outcome: str | None = None


class QualityAppraisalImprecisionThresholds(BaseModel):
    mid_benefit: str | None = None
    mid_harm: str | None = None


class QualityAppraisalRunPayload(BaseModel):
    paper_ids: list[int]
    project_id: int | None = None
    target_pico: QualityAppraisalTargetPico | None = None
    imprecision_thresholds: QualityAppraisalImprecisionThresholds | None = None
    quadas3_review_context: str | None = None
    # Map of {paper_id: [estimate_dict, ...]} for QUADAS-2/3 per-estimate runs.
    # Keys may arrive as ints or strings (frontend uses strings); we normalise
    # both into the run row.
    paper_estimates: dict[str, list[dict]] | None = None
    # Per-paper reviewer override: when the auto-picked primary outcome is
    # ambiguous, the reviewer can specify the outcome to assess. Keys are
    # paper-id strings (JSON object keys must be strings) and values are the
    # outcome description. Empty strings / unspecified keys → no override.
    paper_outcome_overrides: dict[str, str] | None = None
    # Per-run RoB tool selection for diagnostic-accuracy papers
    # ('quadas2' | 'quadas3' | None). None → registry default (QUADAS-3).
    diagnostic_tool_choice: str | None = None
    # Per-run RoB tool selection for non-randomized cohort-type papers
    # ('robins_i' | 'robins_i_v1' | None). None → registry default (V2).
    # Ignored for randomized / diagnostic / single-arm study types.
    robins_i_tool_choice: str | None = None
    # Per-run analysis aim for cluster-randomized trials
    # ('assignment' | 'adhering' | None). None → 'assignment' (ITT) default.
    # Selects the RoB 2 CRT Domain 2 variant; ignored for other study types.
    rob2_cluster_aim: str | None = None


class QualityAppraisalExtractEstimatesPayload(BaseModel):
    paper_id: int


@app.get("/api/quality-appraisal/supported-types")
def api_qa_supported_types(rubricgen_session: str | None = Cookie(default=None),
                           x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """List study types the Quality Appraisal AI currently supports."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    return [{"study_type": st, **cfg}
            for st, cfg in qa_mod.STUDY_TYPE_REGISTRY.items()]


@app.get("/api/quality-appraisal/prompts")
def api_qa_prompts(rubricgen_session: str | None = Cookie(default=None),
                   x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Developer view: all prompts + scoring code used by Quality Appraisal AI.

    Visible to every signed-in user. This is intentional — a research-facing
    tool benefits from letting reviewers see exactly how an AI judgement is
    produced.
    """
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    return qa_mod.prompt_catalog()


@app.post("/api/quality-appraisal/runs")
def api_qa_run_create(body: QualityAppraisalRunPayload,
                      rubricgen_session: str | None = Cookie(default=None),
                      x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Start a Quality Appraisal run over the given paper IDs.

    - Charges ``len(paper_ids) * CREDIT_COST_QA_PER_PAPER`` upfront; admins bypass.
    - Per-paper refund on any error / unsupported-type skip.
    - Inline for ≤3 papers (visible progress), background thread for larger batches.
    """
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    paper_ids = [int(p) for p in (body.paper_ids or [])]
    if not paper_ids:
        raise HTTPException(400, "paper_ids must be a non-empty list")
    if len(paper_ids) > 100:
        raise HTTPException(400, "at most 100 papers per run")

    is_admin = user.get("role") == "admin"
    conn = get_db()
    try:
        # Ownership check
        placeholders = ",".join("?" * len(paper_ids))
        owned = conn.execute(
            f"SELECT id FROM papers WHERE user_id=? AND id IN ({placeholders})",
            (user["id"], *paper_ids),
        ).fetchall()
        owned_ids = [r["id"] for r in owned]
        missing = [p for p in paper_ids if p not in owned_ids]
        if missing:
            raise HTTPException(400, f"unknown or unowned paper ids: {missing}")

        # Cost model: each "unit of work" is 36 cr. For non-QUADAS papers
        # there is exactly 1 unit per paper. For QUADAS-3 (per-estimate)
        # there is 1 unit per estimate; if no estimates were supplied for a
        # diagnostic-accuracy paper, the appraiser falls back to a single
        # primary-estimate iteration → 1 unit. We don't know the per-paper
        # study type at run-create time, so we treat any paper with
        # paper_estimates as QUADAS-3 (the modal only collects estimates
        # for diagnostic-accuracy papers).
        paper_estimates_clean: dict[str, list[dict]] = {}
        if body.paper_estimates:
            for k, v in body.paper_estimates.items():
                try:
                    pid_int = int(k)
                except Exception:
                    continue
                if pid_int not in paper_ids:
                    continue
                if not isinstance(v, list):
                    continue
                clean_list = [e for e in v if isinstance(e, dict)]
                if clean_list:
                    paper_estimates_clean[str(pid_int)] = clean_list

        unit_count = 0
        for pid in paper_ids:
            ests = paper_estimates_clean.get(str(pid)) or []
            unit_count += max(1, len(ests))
        total_cost = unit_count * qa_mod.CREDIT_COST_QA_PER_PAPER

        cost_label = (
            f"Quality Appraisal run ({len(paper_ids)} papers, {unit_count} units)"
            if unit_count != len(paper_ids)
            else f"Quality Appraisal run ({len(paper_ids)} papers)"
        )
        _annotator_ai_gate(conn, user, total_cost, cost_label)

        target_pico_json = None
        if body.target_pico is not None:
            tp = {
                "population":   (body.target_pico.population   or "").strip(),
                "intervention": (body.target_pico.intervention or "").strip(),
                "comparator":   (body.target_pico.comparator   or "").strip(),
                "outcome":      (body.target_pico.outcome      or "").strip(),
            }
            if any(tp.values()):
                target_pico_json = json.dumps(tp)

        imprecision_thresholds_json = None
        if body.imprecision_thresholds is not None:
            it = {
                "mid_benefit": (body.imprecision_thresholds.mid_benefit or "").strip(),
                "mid_harm":    (body.imprecision_thresholds.mid_harm    or "").strip(),
            }
            if any(it.values()):
                imprecision_thresholds_json = json.dumps(it)

        review_ctx = (body.quadas3_review_context or "").strip() or None
        paper_estimates_json = json.dumps(paper_estimates_clean) if paper_estimates_clean else "{}"

        dtc = (body.diagnostic_tool_choice or "").strip().lower() or None
        if dtc is not None and dtc not in ("quadas2", "quadas3"):
            raise HTTPException(400, "diagnostic_tool_choice must be 'quadas2' or 'quadas3'")

        rtc = (body.robins_i_tool_choice or "").strip().lower() or None
        if rtc is not None and rtc not in ("robins_i", "robins_i_v1"):
            raise HTTPException(400, "robins_i_tool_choice must be 'robins_i' or 'robins_i_v1'")

        rca = (body.rob2_cluster_aim or "").strip().lower() or None
        if rca is not None and rca not in ("assignment", "adhering"):
            raise HTTPException(400, "rob2_cluster_aim must be 'assignment' or 'adhering'")

        outcome_overrides_json = "{}"
        if body.paper_outcome_overrides:
            cleaned = {
                str(k): v.strip()
                for k, v in body.paper_outcome_overrides.items()
                if isinstance(v, str) and v.strip()
            }
            # Only keep overrides for papers in this run
            allowed_ids = {str(p) for p in paper_ids}
            cleaned = {k: v for k, v in cleaned.items() if k in allowed_ids}
            if cleaned:
                outcome_overrides_json = json.dumps(cleaned)

        with conn:
            cur = conn.execute(
                """INSERT INTO quality_appraisal_runs
                        (user_id, project_id, paper_ids_json, paper_count,
                         credit_cost, status, target_pico_json,
                         imprecision_thresholds_json,
                         quadas3_review_context, paper_estimates_json,
                         outcome_overrides_json, diagnostic_tool_choice,
                         robins_i_tool_choice, rob2_cluster_aim)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
                (user["id"], body.project_id,
                 json.dumps(paper_ids), len(paper_ids), total_cost,
                 target_pico_json, imprecision_thresholds_json,
                 review_ctx, paper_estimates_json,
                 outcome_overrides_json, dtc, rtc, rca),
            )
            run_id = cur.lastrowid
            conn.commit()
    finally:
        conn.close()

    if len(paper_ids) <= 3:
        qa_mod.run_batch(get_db, PAPERS_DIR, user["id"], is_admin,
                         run_id, paper_ids)
        return {"run_id": run_id, "status": "complete"}
    qa_mod.run_batch_async(get_db, PAPERS_DIR, user["id"], is_admin,
                           run_id, paper_ids)
    return {"run_id": run_id, "status": "running"}


@app.post("/api/quality-appraisal/extract-estimates")
def api_qa_extract_estimates(body: QualityAppraisalExtractEstimatesPayload,
                             rubricgen_session: str | None = Cookie(default=None),
                             x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Extract candidate Phase-4 accuracy estimates from a diagnostic-accuracy
    paper. Used by the run-create modal step 2 to populate per-paper estimate
    selectors.

    Charges 3 credits (matching annotator's classify cost). Auto-refunds on
    error.
    """
    from backend import annotator as annotator_mod
    from backend import billing as bill_mod
    from backend.rob_tools import quadas3 as quadas3_mod

    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    is_admin = user.get("role") == "admin"
    paper_id = int(body.paper_id)

    conn = get_db()
    try:
        owned = conn.execute(
            "SELECT id FROM papers WHERE id=? AND user_id=?",
            (paper_id, user["id"]),
        ).fetchone()
        if not owned and not is_admin:
            raise HTTPException(404, "paper not found or access denied")

        cost = 3
        _annotator_ai_gate(conn, user, cost,
                           f"Extract diagnostic-accuracy estimates (paper {paper_id})")

        try:
            pdf_bytes, _filename = annotator_mod.load_paper_pdf(
                conn, PAPERS_DIR, paper_id, user["id"], is_admin=is_admin)
        except HTTPException:
            if not is_admin:
                try:
                    bill_mod.refund_credits(conn, user["id"], cost,
                                            "Refund: extract-estimates load failed")
                except Exception:
                    pass
            raise

        try:
            estimates = quadas3_mod.extract_estimates(pdf_bytes, {})
        except HTTPException:
            if not is_admin:
                try:
                    bill_mod.refund_credits(conn, user["id"], cost,
                                            "Refund: extract-estimates extraction failed")
                except Exception:
                    pass
            raise
        except Exception:
            logger.exception("extract_estimates failed for paper %s", paper_id)
            if not is_admin:
                try:
                    bill_mod.refund_credits(conn, user["id"], cost,
                                            "Refund: extract-estimates extraction failed")
                except Exception:
                    pass
            raise HTTPException(502, "Estimate extraction failed — see server logs.")
    finally:
        conn.close()

    return {"paper_id": paper_id, "estimates": estimates}


@app.get("/api/quality-appraisal/runs")
def api_qa_runs_list(rubricgen_session: str | None = Cookie(default=None),
                     x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """List the user's recent Quality Appraisal runs (last 50, non-deleted)."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT r.id, r.project_id, r.paper_count, r.status,
                      r.credit_cost, r.credits_refunded, r.error_message,
                      r.diagnostic_tool_choice, r.robins_i_tool_choice,
                      r.created_at, r.completed_at,
                      p.name AS project_name
                 FROM quality_appraisal_runs r
            LEFT JOIN projects p ON p.id = r.project_id
                WHERE r.user_id=? AND r.deleted_at IS NULL
                ORDER BY r.created_at DESC LIMIT 50""",
            (user["id"],),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _load_qa_run(conn, run_id: int, user_id: int, is_admin: bool) -> dict:
    row = conn.execute(
        """SELECT r.id, r.user_id, r.project_id, r.paper_ids_json, r.paper_count,
                  r.status, r.credit_cost, r.credits_refunded, r.error_message,
                  r.target_pico_json, r.imprecision_thresholds_json,
                  r.quadas3_review_context, r.paper_estimates_json,
                  r.outcome_overrides_json,
                  r.diagnostic_tool_choice, r.robins_i_tool_choice,
                  r.created_at, r.completed_at, r.deleted_at,
                  p.name AS project_name
             FROM quality_appraisal_runs r
        LEFT JOIN projects p ON p.id = r.project_id
            WHERE r.id=?""",
        (run_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "run not found")
    if row["user_id"] != user_id and not is_admin:
        raise HTTPException(403, "access denied")
    if row["deleted_at"]:
        raise HTTPException(404, "run not found")
    d = dict(row)
    try:
        d["target_pico"] = json.loads(d.pop("target_pico_json") or "null") or None
    except Exception:
        d["target_pico"] = None
    try:
        d["imprecision_thresholds"] = json.loads(
            d.pop("imprecision_thresholds_json") or "null") or None
    except Exception:
        d["imprecision_thresholds"] = None
    try:
        d["paper_estimates"] = json.loads(d.pop("paper_estimates_json") or "{}") or {}
    except Exception:
        d["paper_estimates"] = {}
    try:
        d["paper_outcome_overrides"] = json.loads(
            d.pop("outcome_overrides_json") or "{}") or {}
    except Exception:
        d["paper_outcome_overrides"] = {}
    return d


@app.get("/api/quality-appraisal/runs/{run_id}")
def api_qa_run_get(run_id: int,
                   rubricgen_session: str | None = Cookie(default=None),
                   x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Full run detail: per-paper results with classification, RoB domains, CONSORT, GRADE."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    is_admin = user.get("role") == "admin"
    conn = get_db()
    try:
        run = _load_qa_run(conn, run_id, user["id"], is_admin)
        rows = conn.execute(
            """SELECT id, paper_id, status, error_message, filename,
                      study_type, rob_tool, reporting_guideline, primary_outcome,
                      assessed_outcome,
                      classification_json, extracted_fields_json,
                      rob_domains_json, rob_overall, rob_direction,
                      applicability_overall, estimate_id, estimate_json,
                      guideline_json, guideline_proportion,
                      guideline_adhered, guideline_applicable,
                      indirectness_json, indirectness_overall,
                      indirectness_levels, indirectness_explanation,
                      imprecision_json, imprecision_overall,
                      imprecision_levels, imprecision_explanation,
                      initial_grade, updated_grade, grade_explanation,
                      created_at
                 FROM quality_appraisal_results
                WHERE run_id=?
                ORDER BY id ASC""",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()

    results = []
    for r in rows:
        d = dict(r)
        for jkey in ("classification_json", "extracted_fields_json",
                     "rob_domains_json", "guideline_json", "indirectness_json",
                     "imprecision_json", "estimate_json"):
            try:
                d[jkey.replace("_json", "")] = json.loads(d.pop(jkey) or "{}")
            except Exception:
                d[jkey.replace("_json", "")] = {}
        results.append(d)
    try:
        run["paper_ids"] = json.loads(run.pop("paper_ids_json") or "[]")
    except Exception:
        run["paper_ids"] = []
    run["results"] = results
    return run


@app.get("/api/quality-appraisal/runs/{run_id}/events")
def api_qa_run_events(run_id: int, after: int = 0,
                      rubricgen_session: str | None = Cookie(default=None),
                      x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Progress events newer than ``after`` (for incremental polling)."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    is_admin = user.get("role") == "admin"
    conn = get_db()
    try:
        _load_qa_run(conn, run_id, user["id"], is_admin)
        rows = conn.execute(
            """SELECT id, event_type, message, detail_json, created_at
                 FROM quality_appraisal_events
                WHERE run_id=? AND id > ?
                ORDER BY id ASC LIMIT 500""",
            (run_id, int(after)),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        detail = None
        try:
            detail = json.loads(r["detail_json"] or "") if r["detail_json"] else None
        except Exception:
            detail = None
        out.append({"id": r["id"], "event_type": r["event_type"],
                    "message": r["message"], "detail": detail,
                    "created_at": r["created_at"]})
    return out


@app.delete("/api/quality-appraisal/runs/{run_id}")
def api_qa_run_delete(run_id: int,
                      rubricgen_session: str | None = Cookie(default=None),
                      x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Soft-delete a run (hidden from the user's list)."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    is_admin = user.get("role") == "admin"
    conn = get_db()
    try:
        _load_qa_run(conn, run_id, user["id"], is_admin)
        with conn:
            conn.execute(
                "UPDATE quality_appraisal_runs SET deleted_at=CURRENT_TIMESTAMP WHERE id=?",
                (run_id,),
            )
            conn.commit()
    finally:
        conn.close()
    return {"ok": True}


def _qa_flatten_for_export(run_detail: dict) -> list[dict]:
    """Build a list of flat dicts for CSV / XLSX export."""
    rows = []
    for r in run_detail.get("results", []):
        rows.append(qa_mod.flatten_result_row({
            "paper_id": r.get("paper_id"),
            "filename": r.get("filename"),
            "status": r.get("status"),
            "error_message": r.get("error_message"),
            "study_type": r.get("study_type"),
            "rob_tool": r.get("rob_tool"),
            "reporting_guideline": r.get("reporting_guideline"),
            "primary_outcome": r.get("primary_outcome"),
            "assessed_outcome": r.get("assessed_outcome"),
            "classification": r.get("classification") or {},
            "extracted_fields": r.get("extracted_fields") or {},
            "rob_domains": r.get("rob_domains") or {},
            "rob_overall": r.get("rob_overall"),
            "rob_direction": r.get("rob_direction"),
            "applicability_overall": r.get("applicability_overall"),
            "estimate_id": r.get("estimate_id"),
            "estimate": r.get("estimate") or {},
            "guideline": r.get("guideline") or {},
            "guideline_proportion": r.get("guideline_proportion"),
            "guideline_adhered": r.get("guideline_adhered"),
            "guideline_applicable": r.get("guideline_applicable"),
            "indirectness": r.get("indirectness") or {},
            "indirectness_overall": r.get("indirectness_overall"),
            "indirectness_levels": r.get("indirectness_levels"),
            "indirectness_explanation": r.get("indirectness_explanation"),
            "imprecision": r.get("imprecision") or {},
            "imprecision_overall": r.get("imprecision_overall"),
            "imprecision_levels": r.get("imprecision_levels"),
            "imprecision_explanation": r.get("imprecision_explanation"),
            "initial_grade": r.get("initial_grade"),
            "updated_grade": r.get("updated_grade"),
            "grade_explanation": r.get("grade_explanation"),
        }))
    return rows


@app.get("/api/quality-appraisal/runs/{run_id}.csv")
def api_qa_run_csv(run_id: int,
                   rubricgen_session: str | None = Cookie(default=None),
                   x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """CSV export of a run's results table."""
    import csv
    import io
    detail = api_qa_run_get(run_id, rubricgen_session, x_api_key)
    rows = _qa_flatten_for_export(detail)
    if not rows:
        raise HTTPException(404, "no results to export")
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    for r in rows:
        writer.writerow({k: (v if v is not None else "") for k, v in r.items()})
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="quality_appraisal_{run_id}.csv"'},
    )


@app.get("/api/quality-appraisal/runs/{run_id}.xlsx")
def api_qa_run_xlsx(run_id: int,
                    rubricgen_session: str | None = Cookie(default=None),
                    x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """XLSX export via backend.exports.export_xlsx."""
    from backend import exports as exports_mod
    detail = api_qa_run_get(run_id, rubricgen_session, x_api_key)
    rows = _qa_flatten_for_export(detail)
    if not rows:
        raise HTTPException(404, "no results to export")
    xlsx_bytes = exports_mod.export_xlsx(rows, title="Quality Appraisal")
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="quality_appraisal_{run_id}.xlsx"'},
    )


# ===========================================================================
# GRADE agent — body-of-evidence certainty (consumes the pooling agent)
# ===========================================================================

@app.post("/api/agents/grade")
def api_agents_grade(body: dict = Body(default=None),
                     rubricgen_session: str | None = Cookie(default=None),
                     x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Rate one pooled body of evidence on the GRADE scale (stateless).

    Body: either a precomputed ``pool_result`` or raw ``studies`` + ``measure``,
    plus ``per_study_rob`` and the human judgments (``baseline_risk_per_1000``,
    ``mid_benefit``/``mid_harm``, ``indirectness_levels``+``reason``,
    ``dose_response``, ``opposing_confounding``, ``overrides``). Pure — no model
    call, no persistence; indirectness defaults to 0 when omitted (use the run
    endpoint for hybrid auto-indirectness). See backend/synthesis/grade_agent.py.
    """
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    try:
        return grade_mod.grade_certainty(body or {})
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/agents/grade-sof")
def api_agents_grade_sof(body: dict = Body(default=None),
                         rubricgen_session: str | None = Cookie(default=None),
                         x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Grade one body and assemble its Summary-of-Findings row. Returns {pool, grade, sof_row}."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    try:
        return grade_mod.sof(body or {})
    except ValueError as e:
        raise HTTPException(400, str(e))


class GradeRunPayload(BaseModel):
    # Each body: {studies|pool_result, measure?, outcome_name?, comparison?,
    # timepoint?, design_class?, plus per-outcome judgments}.
    bodies: list[dict]
    name: str | None = None
    project_id: int | None = None
    rob_by_study: dict[str, str] | None = None
    target_pico: dict[str, str] | None = None
    auto_indirectness: bool = True


@app.post("/api/grade/runs")
def api_grade_run_create(body: GradeRunPayload,
                         rubricgen_session: str | None = Cookie(default=None),
                         x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Grade a set of pooled bodies and persist the run.

    Each body is pooled server-side (when only ``studies`` are given), RoB is joined
    from ``rob_by_study``, indirectness is hybrid (reviewer value wins; otherwise
    auto-assessed via one model call when ``auto_indirectness`` is on and a target
    PICO is available). Charges 2 cr per body that actually uses auto-indirectness;
    admins bypass. Refunds on failure.
    """
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    bodies_in = body.bodies or []
    if not bodies_in:
        raise HTTPException(400, "bodies must be a non-empty list")
    if len(bodies_in) > 100:
        raise HTTPException(400, "at most 100 bodies per run")

    is_admin = user.get("role") == "admin"
    target_pico = body.target_pico or None
    have_pico = bool(target_pico and any((target_pico.get(k) or "").strip()
                                         for k in ("population", "intervention", "comparator", "outcome")))

    # Pool each body (pure) and split reviewer-supplied judgments from the pooled result.
    pooled_bodies: list[dict] = []
    judgments_by_outcome: dict[str, dict] = {}
    _JUDGMENT_KEYS = {"baseline_risk_per_1000", "mid_benefit", "mid_harm",
                      "indirectness_levels", "indirectness_reason", "dose_response",
                      "opposing_confounding", "overrides", "initial", "subgroup",
                      "metaregression", "outcome", "study_context"}
    try:
        for b in bodies_in:
            pr = b.get("pool_result")
            if not (pr and isinstance(pr, dict) and pr.get("pooled")):
                studies = b.get("studies")
                measure = b.get("measure") or (pr or {}).get("measure")
                if not studies or not measure:
                    raise ValueError("each body needs a pooled 'pool_result' or 'studies' + 'measure'")
                pr = synthesis_pool_outcome(
                    studies, measure, model=b.get("model", "random"),
                    tau2_method=b.get("tau2_method", "REML"),
                    outcome_name=b.get("outcome_name"),
                    favorable_direction=b.get("favorable_direction", "lower"))
            wrapped = {
                "outcome_name": b.get("outcome_name") or pr.get("outcome_name"),
                "comparison": b.get("comparison"),
                "timepoint": b.get("timepoint"),
                "design_class": b.get("design_class") or pr.get("design_class"),
                "measure": pr.get("measure"),
                "k": pr.get("k"),
                "pooled": pr,
                "warnings": [],
            }
            pooled_bodies.append(wrapped)
            key = " | ".join(str(wrapped.get(k) or "").strip().lower()
                             for k in ("outcome_name", "comparison", "timepoint"))
            judgments_by_outcome[key] = {k: b[k] for k in _JUDGMENT_KEYS if k in b}
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Count bodies that will trigger the LLM indirectness pass (reviewer value absent).
    auto_bodies = 0
    if body.auto_indirectness:
        for wrapped in pooled_bodies:
            key = " | ".join(str(wrapped.get(k) or "").strip().lower()
                             for k in ("outcome_name", "comparison", "timepoint"))
            if judgments_by_outcome.get(key, {}).get("indirectness_levels") is None:
                auto_bodies += 1
    cost = auto_bodies * grade_mod.CREDIT_COST_GRADE_INDIRECTNESS

    conn = get_db()
    try:
        if cost:
            _annotator_ai_gate(conn, user, cost, f"GRADE run ({auto_bodies} auto-indirectness)")
        run_id = grade_mod.create_run(
            conn, user["id"], name=body.name, project_id=body.project_id,
            target_pico=target_pico, auto_indirectness=body.auto_indirectness,
            n_bodies=len(pooled_bodies))
        grade_mod.log_event(conn, run_id, "run_started",
                            f"Grading {len(pooled_bodies)} bodies of evidence")
        try:
            results = grade_assess_mod.grade_from_pooled(
                pooled_bodies, rob_by_study=body.rob_by_study or None,
                judgments_by_outcome=judgments_by_outcome,
                target_pico=target_pico if have_pico else None,
                auto_indirectness=body.auto_indirectness)
            for wrapped, res in zip(pooled_bodies, results):
                grade_mod.save_result(conn, run_id, res)
                grade_mod.log_event(conn, run_id, "body_done",
                                    f"{res.get('outcome_name') or 'outcome'}: "
                                    f"{(res.get('grade') or {}).get('final') or 'not graded'}")
            grade_mod.finalize_run(conn, run_id, "complete")
        except Exception as e:  # noqa: BLE001
            grade_mod.finalize_run(conn, run_id, "failed", str(e))
            if cost and not is_admin:
                bill.refund_credits(conn, user["id"], cost, "GRADE run failed")
            logger.exception("GRADE run %s failed", run_id)
            raise HTTPException(500, "GRADE run failed")
        detail = grade_mod.get_run_detail(conn, run_id, user["id"], is_admin)
    finally:
        conn.close()
    return detail


@app.get("/api/grade/runs")
def api_grade_runs_list(rubricgen_session: str | None = Cookie(default=None),
                        x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return grade_mod.list_runs(conn, user["id"], user.get("role") == "admin")
    finally:
        conn.close()


@app.get("/api/grade/runs/{run_id}")
def api_grade_run_get(run_id: int,
                      rubricgen_session: str | None = Cookie(default=None),
                      x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        detail = grade_mod.get_run_detail(conn, run_id, user["id"], user.get("role") == "admin")
    finally:
        conn.close()
    if detail is None:
        raise HTTPException(404, "run not found")
    return detail


@app.get("/api/grade/runs/{run_id}/events")
def api_grade_run_events(run_id: int, after: int = 0,
                         rubricgen_session: str | None = Cookie(default=None),
                         x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        if grade_mod._load_run(conn, run_id, user["id"], user.get("role") == "admin") is None:
            raise HTTPException(404, "run not found")
        rows = conn.execute(
            """SELECT id, event_type, message, detail_json, created_at
                 FROM grade_events WHERE run_id=? AND id > ?
                ORDER BY id ASC LIMIT 500""", (run_id, int(after))).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        try:
            detail = json.loads(r["detail_json"]) if r["detail_json"] else None
        except Exception:
            detail = None
        out.append({"id": r["id"], "event_type": r["event_type"], "message": r["message"],
                    "detail": detail, "created_at": r["created_at"]})
    return out


@app.delete("/api/grade/runs/{run_id}")
def api_grade_run_delete(run_id: int,
                         rubricgen_session: str | None = Cookie(default=None),
                         x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        ok = grade_mod.soft_delete_run(conn, run_id, user["id"], user.get("role") == "admin")
    finally:
        conn.close()
    if not ok:
        raise HTTPException(404, "run not found")
    return {"deleted": True}


@app.get("/api/grade/runs/{run_id}/csv")
def api_grade_run_csv(run_id: int,
                      rubricgen_session: str | None = Cookie(default=None),
                      x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    import csv
    import io
    detail = api_grade_run_get(run_id, rubricgen_session, x_api_key)
    rows = grade_mod.flatten_for_export(detail)
    if not rows:
        raise HTTPException(404, "no results to export")
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    for r in rows:
        writer.writerow({k: (v if v is not None else "") for k, v in r.items()})
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="grade_{run_id}.csv"'})


@app.get("/api/grade/runs/{run_id}/xlsx")
def api_grade_run_xlsx(run_id: int,
                       rubricgen_session: str | None = Cookie(default=None),
                       x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    from backend import exports as exports_mod
    detail = api_grade_run_get(run_id, rubricgen_session, x_api_key)
    rows = grade_mod.flatten_for_export(detail)
    if not rows:
        raise HTTPException(404, "no results to export")
    xlsx_bytes = exports_mod.export_xlsx(rows, title="GRADE Evidence Profile")
    return Response(content=xlsx_bytes,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="grade_{run_id}.xlsx"'})


@app.get("/api/ground-truth")

# Synthesis — systematic review + meta-analysis
# ═══════════════════════════════════════════════════════════════════════════
class SynthesisPicoPayload(BaseModel):
    population: str | None = None
    intervention: str | None = None
    comparator: str | None = None
    outcomes: list[str] | None = None
    study_designs: list[str] | None = None


class SynthesisOutcomePayload(BaseModel):
    name: str
    outcome_type: str            # continuous | binary | correlation | single_arm
    effect_measure: str          # MD|SMD|OR|RR|RD|ZCOR|PLOGIT|PFT|IRR
    model_choice: str = "random"
    tau2_method: str = "REML"
    fe_method: str | None = None
    re_ci_method: str = "wald"
    continuity_correction: float = 0.5
    subgroup_field: str | None = None
    mid_benefit: str | None = None
    mid_harm: str | None = None


class SynthesisRunPayload(BaseModel):
    paper_ids: list[int]
    project_id: int | None = None
    title: str | None = None
    pico: SynthesisPicoPayload | None = None
    inclusion: str | None = None
    exclusion: str | None = None
    outcomes: list[SynthesisOutcomePayload]
    run_rob: bool = True
    prisma_manual_counts: dict[str, int] | None = None


class SynthesisStudyPatch(BaseModel):
    decision: str | None = None          # include | exclude
    exclude_reason: str | None = None    # PRISMA category
    note: str | None = None


class SynthesisDataPointPatch(BaseModel):
    raw: dict[str, float | None] | None = None
    included_in_pool: bool | None = None
    subgroup_value: str | None = None


class SynthesisOutcomePatch(BaseModel):
    model_choice: str | None = None
    tau2_method: str | None = None
    fe_method: str | None = None
    re_ci_method: str | None = None
    subgroup_field: str | None = None
    mid_benefit: str | None = None
    mid_harm: str | None = None


def _synth_load_review(conn, review_id: int, user_id: int, is_admin: bool) -> dict:
    row = conn.execute("SELECT * FROM synthesis_reviews WHERE id=? AND deleted_at IS NULL",
                       (review_id,)).fetchone()
    if not row:
        raise HTTPException(404, "review not found")
    d = {k: row[k] for k in row.keys()}
    if d["user_id"] != user_id and not is_admin:
        raise HTTPException(403, "access denied")
    return d


def _synth_jload(s, default):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default


def _synth_detail(conn, review_id: int) -> dict:
    rv = conn.execute("SELECT * FROM synthesis_reviews WHERE id=?", (review_id,)).fetchone()
    review = {k: rv[k] for k in rv.keys()}
    review["pico"] = _synth_jload(review.pop("pico_json", None), {})
    review["eligibility_criteria"] = _synth_jload(review.pop("eligibility_criteria_json", None), None)
    review["prisma_manual_counts"] = _synth_jload(review.pop("prisma_manual_counts_json", None), {})
    review["paper_ids"] = _synth_jload(review.pop("paper_ids_json", None), [])

    studies = []
    for r in conn.execute("SELECT * FROM synthesis_studies WHERE review_id=? ORDER BY id", (review_id,)).fetchall():
        s = {k: r[k] for k in r.keys()}
        s["classification"] = _synth_jload(s.pop("classification_json", None), {})
        s["screening"] = _synth_jload(s.pop("screening_json", None), [])
        s["rob_domains"] = _synth_jload(s.pop("rob_domains_json", None), {})
        studies.append(s)

    outcomes = [{k: r[k] for k in r.keys()} for r in conn.execute(
        "SELECT * FROM synthesis_outcomes WHERE review_id=? ORDER BY sort_order, id", (review_id,)).fetchall()]

    data_points = []
    for r in conn.execute("SELECT * FROM synthesis_data_points WHERE review_id=? ORDER BY outcome_id, study_id", (review_id,)).fetchall():
        p = {k: r[k] for k in r.keys()}
        p["raw"] = _synth_jload(p.pop("raw_json", None), {})
        p["moderator"] = _synth_jload(p.pop("moderator_json", None), {})
        data_points.append(p)

    results = []
    for r in conn.execute("SELECT * FROM synthesis_results WHERE review_id=?", (review_id,)).fetchall():
        res = {k: r[k] for k in r.keys()}
        for col in ("fixed", "random", "heterogeneity", "publication_bias", "subgroup",
                    "metaregression", "sensitivity", "forest", "grade"):
            res[col] = _synth_jload(res.pop(col + "_json", None), {})
        res["code_blocks"] = _synth_jload(res.pop("code_blocks_json", None), [])
        results.append(res)

    review["studies"] = studies
    review["outcomes"] = outcomes
    review["data_points"] = data_points
    review["results"] = results
    review["prisma"] = synthesis_mod.compute_prisma_counts(conn, review_id)
    return review


@app.get("/api/synthesis/supported-measures")
def api_synth_supported(rubricgen_session: str | None = Cookie(default=None),
                        x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    return synthesis_mod.supported_measures()


@app.get("/api/synthesis/prompts")
def api_synth_prompts(rubricgen_session: str | None = Cookie(default=None),
                      x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Developer view: screening/extraction prompts + the exact source of every
    statistics function and the code generator. Visible to every signed-in user."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    return synthesis_mod.prompt_catalog()


@app.post("/api/synthesis/reviews")
def api_synth_create(body: SynthesisRunPayload,
                     rubricgen_session: str | None = Cookie(default=None),
                     x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Create a synthesis review and start the pipeline.

    Charges upfront for screening + extraction + RoB across all papers; the
    worker refunds extraction/RoB for excluded papers. Inline for ≤1 paper,
    background thread otherwise.
    """
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    paper_ids = [int(p) for p in (body.paper_ids or [])]
    if not paper_ids:
        raise HTTPException(400, "paper_ids must be a non-empty list")
    if len(paper_ids) > 100:
        raise HTTPException(400, "at most 100 papers per review")
    if not body.outcomes:
        raise HTTPException(400, "at least one outcome is required")
    for oc in body.outcomes:
        if oc.outcome_type not in synthesis_mod.OUTCOME_TYPE_MEASURES:
            raise HTTPException(400, f"unknown outcome_type: {oc.outcome_type}")
        if oc.effect_measure not in synthesis_mod.OUTCOME_TYPE_MEASURES[oc.outcome_type]:
            raise HTTPException(400, f"measure {oc.effect_measure} not valid for {oc.outcome_type}")

    is_admin = user.get("role") == "admin"
    conn = get_db()
    try:
        placeholders = ",".join("?" * len(paper_ids))
        owned = conn.execute(
            f"SELECT id FROM papers WHERE user_id=? AND id IN ({placeholders})",
            (user["id"], *paper_ids)).fetchall()
        owned_ids = [r["id"] for r in owned]
        missing = [p for p in paper_ids if p not in owned_ids]
        if missing:
            raise HTTPException(400, f"unknown or unowned paper ids: {missing}")

        total_cost = synthesis_mod.estimate_cost(len(paper_ids), len(body.outcomes), body.run_rob)
        _annotator_ai_gate(conn, user, total_cost,
                           f"Synthesis review ({len(paper_ids)} papers, {len(body.outcomes)} outcomes)")

        pico = {}
        if body.pico is not None:
            pico = {"population": (body.pico.population or "").strip(),
                    "intervention": (body.pico.intervention or "").strip(),
                    "comparator": (body.pico.comparator or "").strip(),
                    "outcomes": body.pico.outcomes or [oc.name for oc in body.outcomes],
                    "study_designs": body.pico.study_designs or []}
        if body.inclusion or body.exclusion:
            pico["inclusion_text"] = (body.inclusion or "").strip()
            pico["exclusion_text"] = (body.exclusion or "").strip()
        prisma_manual = json.dumps({k: int(v) for k, v in (body.prisma_manual_counts or {}).items()})

        with conn:
            cur = conn.execute(
                """INSERT INTO synthesis_reviews
                     (user_id, project_id, title, paper_ids_json, paper_count,
                      status, pico_json, run_rob, prisma_manual_counts_json, credit_cost)
                   VALUES (?,?,?,?,?,'pending',?,?,?,?) RETURNING id""",
                (user["id"], body.project_id, (body.title or "Untitled review"),
                 json.dumps(paper_ids), len(paper_ids), json.dumps(pico),
                 1 if body.run_rob else 0, prisma_manual, total_cost))
            review_id = cur.lastrowid
            for i, oc in enumerate(body.outcomes):
                conn.execute(
                    """INSERT INTO synthesis_outcomes
                         (review_id, name, outcome_type, effect_measure, model_choice,
                          tau2_method, fe_method, re_ci_method, continuity_correction,
                          subgroup_field, mid_benefit, mid_harm, sort_order)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (review_id, oc.name, oc.outcome_type, oc.effect_measure, oc.model_choice,
                     oc.tau2_method, oc.fe_method, oc.re_ci_method, oc.continuity_correction,
                     oc.subgroup_field, oc.mid_benefit, oc.mid_harm, i))
            conn.commit()
    finally:
        conn.close()

    if len(paper_ids) <= 1:
        synthesis_mod.run_synthesis(get_db, PAPERS_DIR, user["id"], is_admin, review_id)
        return {"review_id": review_id, "status": "complete"}
    synthesis_mod.run_synthesis_async(get_db, PAPERS_DIR, user["id"], is_admin, review_id)
    return {"review_id": review_id, "status": "running"}


@app.get("/api/synthesis/reviews")
def api_synth_list(rubricgen_session: str | None = Cookie(default=None),
                   x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT r.id, r.title, r.project_id, r.paper_count, r.status, r.phase,
                      r.credit_cost, r.credits_refunded, r.error_message,
                      r.created_at, r.completed_at, p.name AS project_name,
                      (SELECT COUNT(*) FROM synthesis_outcomes o WHERE o.review_id=r.id) AS outcome_count
                 FROM synthesis_reviews r
            LEFT JOIN projects p ON p.id = r.project_id
                WHERE r.user_id=? AND r.deleted_at IS NULL
                ORDER BY r.created_at DESC LIMIT 50""",
            (user["id"],)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


@app.get("/api/synthesis/reviews/{review_id:int}")
def api_synth_get(review_id: int,
                  rubricgen_session: str | None = Cookie(default=None),
                  x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    is_admin = user.get("role") == "admin"
    conn = get_db()
    try:
        _synth_load_review(conn, review_id, user["id"], is_admin)
        return _synth_detail(conn, review_id)
    finally:
        conn.close()


@app.get("/api/synthesis/reviews/{review_id}/events")
def api_synth_events(review_id: int, after: int = 0,
                     rubricgen_session: str | None = Cookie(default=None),
                     x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    is_admin = user.get("role") == "admin"
    conn = get_db()
    try:
        _synth_load_review(conn, review_id, user["id"], is_admin)
        rows = conn.execute(
            """SELECT id, event_type, message, detail_json, created_at
                 FROM synthesis_events WHERE review_id=? AND id > ?
                ORDER BY id ASC LIMIT 500""",
            (review_id, int(after))).fetchall()
    finally:
        conn.close()
    return [{"id": r["id"], "event_type": r["event_type"], "message": r["message"],
             "detail": _synth_jload(r["detail_json"], None), "created_at": r["created_at"]}
            for r in rows]


@app.get("/api/synthesis/reviews/{review_id}/prisma")
def api_synth_prisma(review_id: int,
                     rubricgen_session: str | None = Cookie(default=None),
                     x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    is_admin = user.get("role") == "admin"
    conn = get_db()
    try:
        _synth_load_review(conn, review_id, user["id"], is_admin)
        return synthesis_mod.compute_prisma_counts(conn, review_id)
    finally:
        conn.close()


@app.patch("/api/synthesis/studies/{study_id}")
def api_synth_patch_study(study_id: int, body: SynthesisStudyPatch,
                          rubricgen_session: str | None = Cookie(default=None),
                          x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Override a screening decision. No charge."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    is_admin = user.get("role") == "admin"
    conn = get_db()
    try:
        row = conn.execute("SELECT s.*, r.user_id AS owner FROM synthesis_studies s "
                           "JOIN synthesis_reviews r ON r.id=s.review_id WHERE s.id=?",
                           (study_id,)).fetchone()
        if not row:
            raise HTTPException(404, "study not found")
        if row["owner"] != user["id"] and not is_admin:
            raise HTTPException(403, "access denied")
        decision = (body.decision or row["screening_decision"] or "").strip().lower()
        if decision not in ("include", "exclude"):
            raise HTTPException(400, "decision must be include or exclude")
        with conn:
            conn.execute(
                """UPDATE synthesis_studies
                      SET screening_decision=?, status=?, prisma_exclusion_reason=?,
                          screening_reason=COALESCE(?, screening_reason), decision_overridden=1
                    WHERE id=?""",
                (decision, "included" if decision == "include" else "excluded",
                 (body.exclude_reason if decision == "exclude" else ""),
                 body.note, study_id))
            conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.patch("/api/synthesis/data-points/{dp_id}")
def api_synth_patch_datapoint(dp_id: int, body: SynthesisDataPointPatch,
                              rubricgen_session: str | None = Cookie(default=None),
                              x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Edit extracted numbers / inclusion. Recomputes yi/vi. No charge."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    is_admin = user.get("role") == "admin"
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT dp.*, o.effect_measure, o.continuity_correction, r.user_id AS owner "
            "FROM synthesis_data_points dp "
            "JOIN synthesis_outcomes o ON o.id=dp.outcome_id "
            "JOIN synthesis_reviews r ON r.id=dp.review_id WHERE dp.id=?",
            (dp_id,)).fetchone()
        if not row:
            raise HTTPException(404, "data point not found")
        if row["owner"] != user["id"] and not is_admin:
            raise HTTPException(403, "access denied")
        raw = _synth_jload(row["raw_json"], {})
        if body.raw is not None:
            raw.update({k: v for k, v in body.raw.items()})
        eff = synthesis_mod.compute_effect_for_point(
            row["effect_measure"], raw, float(row["continuity_correction"] or 0.5))
        included = row["included_in_pool"] if body.included_in_pool is None else (1 if body.included_in_pool else 0)
        subgroup = row["subgroup_value"] if body.subgroup_value is None else body.subgroup_value
        needs_review = 1 if eff.get("yi") is None else 0
        with conn:
            conn.execute(
                """UPDATE synthesis_data_points
                      SET raw_json=?, yi=?, vi=?, continuity_applied=?, included_in_pool=?,
                          subgroup_value=?, needs_review=?, edited_by_user=1
                    WHERE id=?""",
                (json.dumps(raw), eff.get("yi"), eff.get("vi"),
                 1 if eff.get("continuity_applied") else 0, included, subgroup,
                 needs_review, dp_id))
            conn.commit()
    finally:
        conn.close()
    return {"ok": True, "yi": eff.get("yi"), "vi": eff.get("vi")}


@app.patch("/api/synthesis/outcomes/{outcome_id}")
def api_synth_patch_outcome(outcome_id: int, body: SynthesisOutcomePatch,
                            rubricgen_session: str | None = Cookie(default=None),
                            x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Change an outcome's model/measure options (re-pool afterwards)."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    is_admin = user.get("role") == "admin"
    conn = get_db()
    try:
        row = conn.execute("SELECT o.*, r.user_id AS owner FROM synthesis_outcomes o "
                           "JOIN synthesis_reviews r ON r.id=o.review_id WHERE o.id=?",
                           (outcome_id,)).fetchone()
        if not row:
            raise HTTPException(404, "outcome not found")
        if row["owner"] != user["id"] and not is_admin:
            raise HTTPException(403, "access denied")
        fields = {k: getattr(body, k) for k in
                  ("model_choice", "tau2_method", "fe_method", "re_ci_method",
                   "subgroup_field", "mid_benefit", "mid_harm")
                  if getattr(body, k) is not None}
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            with conn:
                conn.execute(f"UPDATE synthesis_outcomes SET {sets} WHERE id=?",
                             (*fields.values(), outcome_id))
                conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.post("/api/synthesis/reviews/{review_id}/pool")
def api_synth_repool(review_id: int,
                     rubricgen_session: str | None = Cookie(default=None),
                     x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Re-run pooling + bias + sensitivity + GRADE from current data points.
    Free, synchronous (no LLM)."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    is_admin = user.get("role") == "admin"
    conn = get_db()
    try:
        _synth_load_review(conn, review_id, user["id"], is_admin)
        synthesis_mod.repool_review(conn, review_id)
        return _synth_detail(conn, review_id)
    finally:
        conn.close()


@app.post("/api/synthesis/reviews/{review_id}/eligibility")
def api_synth_eligibility(review_id: int, body: dict = Body(...),
                          rubricgen_session: str | None = Cookie(default=None),
                          x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Save reviewer-edited eligibility criteria. No charge."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "engineer")
    is_admin = user.get("role") == "admin"
    conn = get_db()
    try:
        _synth_load_review(conn, review_id, user["id"], is_admin)
        with conn:
            conn.execute("UPDATE synthesis_reviews SET eligibility_criteria_json=? WHERE id=?",
                         (json.dumps(body), review_id))
            conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.delete("/api/synthesis/reviews/{review_id}")
def api_synth_delete(review_id: int,
                     rubricgen_session: str | None = Cookie(default=None),
                     x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    is_admin = user.get("role") == "admin"
    conn = get_db()
    try:
        _synth_load_review(conn, review_id, user["id"], is_admin)
        with conn:
            conn.execute("UPDATE synthesis_reviews SET deleted_at=CURRENT_TIMESTAMP WHERE id=?",
                         (review_id,))
            conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.get("/api/synthesis/reviews/{review_id}/code/{outcome_id}.{ext}")
def api_synth_code(review_id: int, outcome_id: int, ext: str,
                   rubricgen_session: str | None = Cookie(default=None),
                   x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Download the generated R (.R) or Python (.py) analysis script."""
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    is_admin = user.get("role") == "admin"
    if ext not in ("R", "py"):
        raise HTTPException(404, "use .R or .py")
    conn = get_db()
    try:
        _synth_load_review(conn, review_id, user["id"], is_admin)
        row = conn.execute("SELECT r_code, python_code FROM synthesis_results WHERE outcome_id=?",
                           (outcome_id,)).fetchone()
        if not row:
            raise HTTPException(404, "no results for this outcome")
        code = row["r_code"] if ext == "R" else row["python_code"]
    finally:
        conn.close()
    return Response(content=code or "",
                    media_type="text/plain",
                    headers={"Content-Disposition": f'attachment; filename="meta_analysis_{outcome_id}.{ext}"'})


def _synth_flatten_for_export(conn, review_id: int) -> list[dict]:
    return synthesis_mod.flatten_for_export(conn, review_id)


@app.get("/api/synthesis/reviews/{review_id}.csv")
def api_synth_csv(review_id: int,
                  rubricgen_session: str | None = Cookie(default=None),
                  x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    import csv
    import io
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    is_admin = user.get("role") == "admin"
    conn = get_db()
    try:
        _synth_load_review(conn, review_id, user["id"], is_admin)
        rows = _synth_flatten_for_export(conn, review_id)
    finally:
        conn.close()
    if not rows:
        raise HTTPException(404, "no results to export")
    fieldnames = list({k for r in rows for k in r.keys()})
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        writer.writerow({k: (r.get(k) if r.get(k) is not None else "") for k in fieldnames})
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="synthesis_{review_id}.csv"'})


@app.get("/api/synthesis/reviews/{review_id}.xlsx")
def api_synth_xlsx(review_id: int,
                   rubricgen_session: str | None = Cookie(default=None),
                   x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    from backend import exports as exports_mod
    user = require_user(rubricgen_session, x_api_key)
    require_active_seat(user, "general")
    is_admin = user.get("role") == "admin"
    conn = get_db()
    try:
        _synth_load_review(conn, review_id, user["id"], is_admin)
        rows = _synth_flatten_for_export(conn, review_id)
    finally:
        conn.close()
    if not rows:
        raise HTTPException(404, "no results to export")
    xlsx_bytes = exports_mod.export_xlsx(rows, title="Synthesis")
    return Response(content=xlsx_bytes,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="synthesis_{review_id}.xlsx"'})




def api_get_ground_truth(rubric_id: int | None = None, challenge_id: int | None = None,
                         rubricgen_session: str | None = Cookie(default=None)):
    """Get ground truth annotations for a rubric or challenge."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return tmpl_mod.get_ground_truth(conn, rubric_id, challenge_id)
    finally:
        conn.close()


@app.get("/api/evaluations/{evaluation_id}/accuracy")
def api_evaluation_accuracy(evaluation_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Compare judge grades to ground truth for an evaluation."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return tmpl_mod.compare_judge_to_ground_truth(conn, evaluation_id)
    finally:
        conn.close()


# ─── Template stats recording (internal use) ───

@app.post("/api/templates/{template_id}/record-stats")
def api_record_template_stats(template_id: int, body: dict, rubricgen_session: str | None = Cookie(default=None)):
    """Record question stats from an evaluation. Called after grading."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    conn = get_db()
    try:
        tmpl_mod.update_question_stats(conn, template_id, body)
        return {"ok": True}
    finally:
        conn.close()


# ─────────────────────────────────────────────
# Phase 6: Analytics & Reporting API
# ─────────────────────────────────────────────

@app.get("/api/analytics/filters")
def api_analytics_filters(rubricgen_session: str | None = Cookie(default=None)):
    """Available filter values for the analytics page."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return analytics_mod.get_available_filters(conn)
    finally:
        conn.close()


@app.get("/api/analytics/breakdown")
def api_analytics_breakdown(
    model: str | None = None,
    theme: str | None = None,
    difficulty: str | None = None,
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
    rubricgen_session: str | None = Cookie(default=None),
):
    """Per-model performance breakdown by theme and difficulty."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return analytics_mod.get_model_breakdown(conn, model, theme, difficulty, date_from, date_to)
    finally:
        conn.close()


@app.get("/api/analytics/trends")
def api_analytics_trends(
    models: str | None = None,
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
    rubricgen_session: str | None = Cookie(default=None),
):
    """Historical accuracy time-series per model."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    model_ids = [m.strip() for m in models.split(",")] if models else None
    conn = get_db()
    try:
        return analytics_mod.get_historical_trends(conn, model_ids, date_from, date_to)
    finally:
        conn.close()


@app.get("/api/analytics/themes")
def api_analytics_themes(rubricgen_session: str | None = Cookie(default=None)):
    """Theme-level summary statistics."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        return analytics_mod.get_theme_stats(conn)
    finally:
        conn.close()


@app.get("/api/analytics/export/csv")
def api_analytics_export_csv(
    model: str | None = None,
    theme: str | None = None,
    difficulty: str | None = None,
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
    rubricgen_session: str | None = Cookie(default=None),
):
    """Download benchmark data as CSV."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        csv_bytes = analytics_mod.generate_csv_report(conn, model, theme, difficulty, date_from, date_to)
    finally:
        conn.close()
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=benchmark_report.csv"},
    )


@app.get("/api/analytics/export/pdf")
def api_analytics_export_pdf(
    model: str | None = None,
    theme: str | None = None,
    difficulty: str | None = None,
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
    rubricgen_session: str | None = Cookie(default=None),
):
    """Download benchmark report as PDF."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "general")
    conn = get_db()
    try:
        pdf_bytes = analytics_mod.generate_pdf_report(conn, model, theme, difficulty, date_from, date_to)
    finally:
        conn.close()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=benchmark_report.pdf"},
    )


# ─── Notification preferences ───

@app.get("/api/notifications/preferences")
def api_get_notification_prefs(rubricgen_session: str | None = Cookie(default=None)):
    """Get current user's notification preferences."""
    user = require_user(rubricgen_session)
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM notification_preferences WHERE user_id = ?", (user["id"],)
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    return {"user_id": user["id"], "daily_complete": 0, "weekly_digest": 0}


class NotificationPrefsBody(BaseModel):
    daily_complete: bool = False
    weekly_digest: bool = False

@app.put("/api/notifications/preferences")
def api_set_notification_prefs(body: NotificationPrefsBody, rubricgen_session: str | None = Cookie(default=None)):
    """Update notification preferences."""
    user = require_user(rubricgen_session)
    conn = get_db()
    with conn:
        conn.execute(
            """INSERT INTO notification_preferences (user_id, daily_complete, weekly_digest, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id) DO UPDATE SET
                 daily_complete = excluded.daily_complete,
                 weekly_digest = excluded.weekly_digest,
                 updated_at = CURRENT_TIMESTAMP""",
            (user["id"], int(body.daily_complete), int(body.weekly_digest)),
        )
        conn.commit()
    conn.close()
    return {"ok": True}


# ─── Public leaderboard API (no auth, rate-limited) ───

@app.get("/api/public/leaderboard")
def api_public_leaderboard(request: Request):
    """Public leaderboard — no auth required, rate-limited."""
    client_ip = request.client.host if request.client else "unknown"
    if not analytics_mod.check_rate_limit(client_ip):
        raise HTTPException(429, "Rate limit exceeded. Max 60 requests per minute.")
    conn = get_db()
    rows = conn.execute(
        """SELECT model_id, provider, total_challenges, avg_accuracy,
                  total_points, daily_points, daily_streak
           FROM leaderboard_cache
           ORDER BY total_points DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/public/leaderboard/daily")
def api_public_daily_leaderboard(request: Request):
    """Public daily leaderboard — no auth, rate-limited."""
    client_ip = request.client.host if request.client else "unknown"
    if not analytics_mod.check_rate_limit(client_ip):
        raise HTTPException(429, "Rate limit exceeded. Max 60 requests per minute.")
    conn = get_db()
    rows = conn.execute(
        """SELECT model_id, provider, daily_points, daily_streak, daily_rank_change
           FROM leaderboard_cache
           WHERE daily_points > 0
           ORDER BY daily_points DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/public/models")
def api_public_models(request: Request):
    """Public model list with aggregate stats — no auth, rate-limited."""
    client_ip = request.client.host if request.client else "unknown"
    if not analytics_mod.check_rate_limit(client_ip):
        raise HTTPException(429, "Rate limit exceeded. Max 60 requests per minute.")
    conn = get_db()
    rows = conn.execute(
        """SELECT model_id, provider, total_challenges,
                  avg_accuracy, total_points
           FROM leaderboard_cache
           ORDER BY total_points DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────
# Batch evaluation (multi-paper)
# ─────────────────────────────────────────────
class BatchEvalRequest(BaseModel):
    paper_ids: list[int]
    rubric_type: str = "classification"
    eval_model: str = "gpt-4o"
    instructions: Optional[str] = None

@app.post("/api/batch/evaluate")
def batch_evaluate(body: BatchEvalRequest, rubricgen_session: str | None = Cookie(default=None)):
    """Generate rubric and evaluate multiple papers."""
    user = require_user(rubricgen_session)
    require_active_seat(user, "engineer")
    if not body.paper_ids:
        raise HTTPException(400, "paper_ids must not be empty")
    if len(body.paper_ids) > 50:
        raise HTTPException(400, "Cannot batch more than 50 papers at once")
    results = []
    for pid in body.paper_ids:
        try:
            # Generate rubric
            gen_body = GenerateRubricRequest(
                paper_id=pid, rubric_type=body.rubric_type, instructions=body.instructions
            )
            gen_result = generate_rubric(gen_body, rubricgen_session=rubricgen_session)
            rubric_id = gen_result["rubric_id"]
            # Run evaluation
            run_body = RunEvaluationRequest(rubric_id=rubric_id, paper_id=pid, eval_model=body.eval_model)
            run_result = run_evaluation(run_body, rubricgen_session=rubricgen_session)
            eval_id = run_result["evaluation_id"]
            # Grade
            grade_result = grade_evaluation(eval_id, rubricgen_session=rubricgen_session)
            results.append({
                "paper_id": pid, "rubric_id": rubric_id, "evaluation_id": eval_id,
                "total_score": grade_result["graded"].get("total_score"),
                "max_score": grade_result["graded"].get("max_score"),
                "percentage": grade_result["graded"].get("percentage"),
                "status": "ok"
            })
        except Exception as e:
            results.append({"paper_id": pid, "status": "error", "error": str(e)})
    return {"results": results}
