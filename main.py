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

from fastapi import Cookie, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
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
from backend.skills import (
    SKILLS_TABLE_SQL, seed_v1_skills, migrate_agent_skills_check,
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
    """Look up a user by their personal API key."""
    if not api_key or not api_key.startswith("rg_user_"):
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
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "library.html"), media_type="text/html")


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
    # Add avatar URL if avatar_path is set
    if user.get("avatar_path"):
        user["avatar_url"] = f"/api/auth/avatar/{user['id']}"
    else:
        user["avatar_url"] = None
    user.pop("avatar_path", None)
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
    require_user(rubricgen_session)
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
    require_user(rubricgen_session)
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
    require_user(rubricgen_session)
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
    require_user(rubricgen_session)
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
    require_user(rubricgen_session)
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
    require_user(rubricgen_session)
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
    conn = get_db()
    try:
        models = mreg.list_registered_models(conn, user_id=user["id"] if mine_only else None)
    finally:
        conn.close()
    return models


@app.get("/api/models/mine")
def api_list_my_models(rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        models = mreg.list_registered_models(conn, user_id=user["id"])
    finally:
        conn.close()
    return models


@app.get("/api/models/{model_id}")
def api_get_model(model_id: int, rubricgen_session: str | None = Cookie(default=None)):
    require_user(rubricgen_session)
    conn = get_db()
    try:
        return mreg.get_registered_model(conn, model_id)
    finally:
        conn.close()


@app.delete("/api/models/{model_id}")
def api_delete_model(model_id: int, rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
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
    require_user(rubricgen_session)
    conn = get_db()
    try:
        return mreg.get_version_history(conn, model_id)
    finally:
        conn.close()


@app.post("/api/models/{model_id}/members")
def api_add_member(model_id: int, body: AddMemberPayload,
                   rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        return mreg.add_member(conn, model_id, user["id"], body.email)
    finally:
        conn.close()


@app.delete("/api/models/{model_id}/members/{member_user_id}")
def api_remove_member(model_id: int, member_user_id: int,
                      rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
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
    require_user(rubricgen_session)
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
    require_user(rubricgen_session)
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
    require_user(rubricgen_session)
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
    require_user(rubricgen_session)
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
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/papers/upload", status_code=201)
async def upload_paper(
    file: UploadFile = File(...),
    project_id: int | None = None,
    rubricgen_session: str | None = Cookie(default=None),
):
    user = require_user(rubricgen_session)
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(413, "File too large. Maximum size is 50 MB.")

    # Check membership PDF limit
    conn = get_db()
    try:
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
                    "INSERT INTO papers (filename, disk_filename, storage_path, sha256, user_id, project_id) "
                    "VALUES (?,?,?,?,?,?) RETURNING id",
                    (file.filename, disk_name, storage_path, sha256, user["id"], project_id),
                )
                conn.commit()
            pid = cur.lastrowid
        except Exception as e:
            logger.exception("Paper upload: DB insert failed for %s", file.filename)
            raise HTTPException(500, f"Upload stored but DB insert failed: {e}")
        try:
            member_mod.increment_pdf_count(conn, user["id"])
        except Exception:
            logger.exception("Paper upload: PDF count increment failed — continuing")
        return {"id": pid, "filename": file.filename, "duplicate": False,
                "storage": "s3" if storage_path.startswith("s3://") else "local"}
    finally:
        conn.close()


@app.patch("/api/papers/{pid}/assign")
def assign_paper(pid: int, body: PaperAssign, rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    conn = get_db()
    with conn:
        conn.execute(
            "UPDATE papers SET project_id=? WHERE id=? AND user_id=?",
            (body.project_id, pid, user["id"]),
        )
        conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/papers/{pid}")
def delete_paper(pid: int, rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
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
    role: str = "viewer"

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
    conn = get_db()
    try:
        org_mod.require_org_role(conn, org_id, user["id"], "viewer")
        return org_mod.get_organization(conn, org_id)
    finally:
        conn.close()


@app.patch("/api/orgs/{org_id}")
def api_update_org(org_id: int, body: OrgUpdatePayload, rubricgen_session: str | None = Cookie(default=None)):
    """Update organization settings. Requires admin role."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        return org_mod.update_organization(conn, org_id, user["id"], body.name, body.description, body.domain)
    finally:
        conn.close()


@app.delete("/api/orgs/{org_id}")
def api_delete_org(org_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Delete organization. Requires admin role."""
    user = require_user(rubricgen_session)
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
    conn = get_db()
    try:
        return org_mod.add_member(conn, org_id, user["id"], body.email, body.role)
    finally:
        conn.close()


@app.patch("/api/orgs/{org_id}/members/{member_user_id}")
def api_update_org_member(org_id: int, member_user_id: int, body: OrgMemberRolePayload, rubricgen_session: str | None = Cookie(default=None)):
    """Change a member's role. Requires admin role."""
    user = require_user(rubricgen_session)
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
    conn = get_db()
    try:
        org_mod.require_org_role(conn, org_id, user["id"], "viewer")
        return bill.get_org_balance(conn, org_id)
    finally:
        conn.close()


@app.get("/api/orgs/{org_id}/billing/transactions")
def api_org_transactions(org_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """List organization credit transactions. Requires viewer+ role."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        org_mod.require_org_role(conn, org_id, user["id"], "viewer")
        return bill.list_org_transactions(conn, org_id)
    finally:
        conn.close()


@app.post("/api/orgs/{org_id}/billing/checkout")
def api_org_checkout(org_id: int, body: dict, rubricgen_session: str | None = Cookie(default=None)):
    """Create Stripe checkout session for org credits. Requires admin role."""
    user = require_user(rubricgen_session)
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
    conn = get_db()
    try:
        org_mod.require_org_role(conn, org_id, user["id"], "contributor")
        bill.transfer_credits_to_org(conn, user["id"], org_id, body.amount)
        return {"ok": True}
    finally:
        conn.close()


# ─── Org models ───

@app.get("/api/orgs/{org_id}/models")
def api_org_models(org_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """List models belonging to an organization. Requires viewer+ role."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        org_mod.require_org_role(conn, org_id, user["id"], "viewer")
        from backend.models_registry import list_org_models
        return list_org_models(conn, org_id)
    finally:
        conn.close()


# ─── Org leaderboard ───

@app.get("/api/leaderboard/organizations")
def api_org_leaderboard(rubricgen_session: str | None = Cookie(default=None)):
    """Organization leaderboard."""
    require_user(rubricgen_session)
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
    require_user(rubricgen_session)
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
    conn = get_db()
    try:
        return search_mod.create_session(conn, user["id"])
    finally:
        conn.close()


@app.get("/api/search/sessions")
def api_search_sessions(rubricgen_session: str | None = Cookie(default=None)):
    """List user's search sessions."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        return search_mod.list_sessions(conn, user["id"])
    finally:
        conn.close()


@app.get("/api/search/sessions/{session_id}")
def api_search_session(session_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Get a search session with messages and results."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        return search_mod.get_session(conn, session_id, user["id"])
    finally:
        conn.close()


@app.delete("/api/search/sessions/{session_id}")
def api_delete_search_session(session_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Delete a search session."""
    user = require_user(rubricgen_session)
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


@app.post("/api/search/import")
def api_search_import(body: SearchImportPayload, rubricgen_session: str | None = Cookie(default=None)):
    """Import selected search results as papers."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        # Check PDF limit before import
        pdf_status = member_mod.check_pdf_limit(conn, user["id"])
        if not pdf_status["allowed"]:
            raise HTTPException(403, f"PDF limit reached ({pdf_status['used']}/{pdf_status['limit']}). Upgrade your membership.")
        return search_mod.import_results(
            conn, body.session_id, body.result_ids, user["id"], PAPERS_DIR,
            project_id=body.project_id,
        )
    finally:
        conn.close()


@app.post("/api/search/export/ris")
def api_search_export_ris(body: SearchExportPayload, rubricgen_session: str | None = Cookie(default=None)):
    """Export selected results as RIS."""
    require_user(rubricgen_session)
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
    require_user(rubricgen_session)
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
    require_user(rubricgen_session)
    conn = get_db()
    try:
        search_mod.toggle_result_selection(conn, body.result_ids, body.selected)
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/search/results/select-all")
def api_search_select_all(body: SearchSelectAllPayload, rubricgen_session: str | None = Cookie(default=None)):
    """Select/deselect all results for a query version."""
    require_user(rubricgen_session)
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
    conn = get_db()
    try:
        return lab_mod.create_session(conn, user["id"], body.agent_type, body.title)
    finally:
        conn.close()


@app.get("/api/lab/sessions")
def api_lab_sessions(agent_type: str | None = None, rubricgen_session: str | None = Cookie(default=None)):
    """List lab sessions, optionally filtered by agent_type."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        return lab_mod.list_sessions(conn, user["id"], agent_type)
    finally:
        conn.close()


@app.get("/api/lab/sessions/{session_id}")
def api_lab_session(session_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Get a lab session with messages."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        return lab_mod.get_session(conn, session_id, user["id"])
    finally:
        conn.close()


@app.delete("/api/lab/sessions/{session_id}")
def api_lab_session_delete(session_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Delete a lab session."""
    user = require_user(rubricgen_session)
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
    content = await file.read()
    filename = file.filename or "file"

    # Check storage limit
    conn = get_db()
    try:
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
    finally:
        conn.close()
    return doc


@app.get("/api/lab/documents")
def api_lab_list_documents(
    project_id: int | None = None,
    rubricgen_session: str | None = Cookie(default=None),
):
    user = require_user(rubricgen_session)
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
    conn = get_db()
    try:
        lab_mod.update_document(conn, doc_id, user["id"], body.project_id)
    finally:
        conn.close()
    return {"ok": True}


@app.delete("/api/lab/documents/{doc_id}")
def api_lab_delete_document(doc_id: int, rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
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
        return annotator_mod.classify_study_design(pdf_bytes)
    except HTTPException:
        _refund_annotator(user, annotator_mod.CREDIT_COST_CLASSIFY, "classify", filename)
        raise
    except Exception as e:
        _refund_annotator(user, annotator_mod.CREDIT_COST_CLASSIFY, "classify", filename)
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
        return annotator_mod.prefill_fields(
            pdf_bytes, study_type,
            groups=groups,
            type_fields=type_fields,
            modifier_fields=modifier_fields,
        )
    except HTTPException:
        _refund_annotator(user, annotator_mod.CREDIT_COST_PREFILL, "prefill", filename)
        raise
    except Exception as e:
        _refund_annotator(user, annotator_mod.CREDIT_COST_PREFILL, "prefill", filename)
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


@app.get("/api/annotator/analytics")
def api_annotator_analytics(project_id: int | None = None,
                            paper_ids: str | None = None,
                            rubricgen_session: str | None = Cookie(default=None),
                            x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Aggregate analytics over a user's annotated papers.

    Scope: ``project_id=N`` OR ``paper_ids=1,2,3``. Omit both for all-user.
    """
    user = require_user(rubricgen_session, x_api_key)
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
            else:
                # CSV / TXT / anything else: decode as text
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
                           fields: list[dict], paper_ids: list[int]) -> None:
    """Execute a custom-extraction run. Safe to call from a background thread.

    Writes ``results_json`` + ``status`` on completion, refunds per-paper on
    failure. Each paper is isolated: a deleted paper or a flaky LLM call
    fails just that row, not the whole run.
    """
    from backend import billing as bill_mod
    per_paper_cost = annotator_mod.CREDIT_COST_CUSTOM_PREFILL
    paper_results: dict[str, Any] = {}
    refunded = 0

    def _mark(status: str, payload: dict) -> None:
        conn2 = get_db()
        try:
            with conn2:
                conn2.execute(
                    """UPDATE annotator_custom_runs
                          SET status=?, results_json=?, credits_refunded=?,
                              error_message=?, completed_at=CURRENT_TIMESTAMP
                        WHERE id=?""",
                    (status, json.dumps(payload), refunded,
                     payload.get("error_message"), run_id),
                )
                conn2.commit()
        finally:
            conn2.close()

    for pid in paper_ids:
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
                continue
        finally:
            paper_conn.close()

        try:
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

    _mark("complete", {"papers": paper_results})


def _run_custom_extraction_async(user_id: int, is_admin: bool, run_id: int,
                                 fields: list[dict], paper_ids: list[int]) -> None:
    t = threading.Thread(
        target=_run_custom_extraction,
        args=(user_id, is_admin, run_id, fields, paper_ids),
        daemon=True,
        name=f"annotator-custom-run-{run_id}",
    )
    t.start()


@app.post("/api/annotator/schemas/{sid}/run")
def api_annotator_schemas_run(sid: int, body: CustomSchemaRunPayload,
                              rubricgen_session: str | None = Cookie(default=None),
                              x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
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

        # Credit pre-flight
        total_cost = len(paper_ids) * annotator_mod.CREDIT_COST_CUSTOM_PREFILL
        is_admin = user.get("role") == "admin"
        _annotator_ai_gate(conn, user, total_cost,
                           f"Annotator custom run (schema {sid}, {len(paper_ids)} papers)")

        snapshot = {"id": sid, "name": srow["name"],
                    "description": srow["description"] or "",
                    "fields": fields}
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
        _run_custom_extraction(user["id"], is_admin, run_id, fields, paper_ids)
        return {"run_id": run_id, "status": "complete"}
    _run_custom_extraction_async(user["id"], is_admin, run_id, fields, paper_ids)
    return {"run_id": run_id, "status": "running"}


@app.get("/api/annotator/runs")
def api_annotator_runs_list(rubricgen_session: str | None = Cookie(default=None),
                            x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT r.id, r.schema_id, r.schema_snapshot_json,
                      r.paper_ids_json, r.status, r.credit_cost,
                      r.credits_refunded, r.created_at, r.completed_at,
                      s.name AS schema_name
                 FROM annotator_custom_runs r
            LEFT JOIN annotator_custom_schemas s ON s.id = r.schema_id
                WHERE r.user_id = ?
                ORDER BY r.created_at DESC LIMIT 50""",
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
        snap_name = r["schema_name"]
        if not snap_name:
            try:
                snap = json.loads(r["schema_snapshot_json"] or "{}")
                snap_name = snap.get("name", "(deleted schema)")
            except Exception:
                snap_name = "(deleted schema)"
        out.append({
            "id": r["id"], "schema_id": r["schema_id"],
            "schema_name": snap_name,
            "paper_count": len(pids), "status": r["status"],
            "credit_cost": r["credit_cost"] or 0,
            "credits_refunded": r["credits_refunded"] or 0,
            "created_at": r["created_at"],
            "completed_at": r["completed_at"],
        })
    return out


@app.get("/api/annotator/runs/{rid}")
def api_annotator_runs_get(rid: int,
                           rubricgen_session: str | None = Cookie(default=None),
                           x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT id, schema_id, schema_snapshot_json, paper_ids_json,
                      results_json, status, credit_cost, credits_refunded,
                      error_message, created_at, completed_at
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
    }


@app.get("/api/annotator/runs/{rid}.csv")
def api_annotator_runs_csv(rid: int,
                           rubricgen_session: str | None = Cookie(default=None),
                           x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
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


@app.get("/api/annotator/export.csv")
def api_annotator_export_csv(paper_id: int | None = None,
                             project_id: int | None = None,
                             rubricgen_session: str | None = Cookie(default=None),
                             x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    user = require_user(rubricgen_session, x_api_key)
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
    conn = get_db()
    try:
        return tmpl_mod.create_template_from_rubric(conn, body.rubric_id, user["id"], body.name, body.description)
    finally:
        conn.close()


@app.get("/api/templates")
def api_list_templates(rubricgen_session: str | None = Cookie(default=None)):
    """List current user's templates."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        return tmpl_mod.list_user_templates(conn, user["id"])
    finally:
        conn.close()


@app.get("/api/templates/{template_id}")
def api_get_template(template_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Get a template with question stats."""
    require_user(rubricgen_session)
    conn = get_db()
    try:
        return tmpl_mod.get_template(conn, template_id)
    finally:
        conn.close()


@app.put("/api/templates/{template_id}")
def api_update_template(template_id: int, body: TemplateUpdatePayload, rubricgen_session: str | None = Cookie(default=None)):
    """Update a template. Bumps version on content change."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        return tmpl_mod.update_template(conn, template_id, user["id"], body.name, body.description, body.template_json)
    finally:
        conn.close()


@app.delete("/api/templates/{template_id}")
def api_delete_template(template_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Delete a template."""
    user = require_user(rubricgen_session)
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
    conn = get_db()
    try:
        return tmpl_mod.fork_template(conn, template_id, user["id"])
    finally:
        conn.close()


@app.get("/api/templates/{template_id}/flagged")
def api_flagged_questions(template_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Get questions flagged as too easy or broken."""
    require_user(rubricgen_session)
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
    conn = get_db()
    try:
        return tmpl_mod.publish_template(conn, template_id, user["id"], body.title, body.description)
    finally:
        conn.close()


@app.delete("/api/templates/{template_id}/publish")
def api_unpublish_template(template_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Remove a template from the community library."""
    user = require_user(rubricgen_session)
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
    require_user(rubricgen_session)
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
    require_user(rubricgen_session)
    conn = get_db()
    try:
        return tmpl_mod.get_community_template(conn, community_id)
    finally:
        conn.close()


@app.post("/api/community/templates/{community_id}/rate")
def api_rate_community_template(community_id: int, body: TemplateRatePayload, rubricgen_session: str | None = Cookie(default=None)):
    """Rate a community template (1-5)."""
    user = require_user(rubricgen_session)
    conn = get_db()
    try:
        return tmpl_mod.rate_template(conn, community_id, user["id"], body.rating)
    finally:
        conn.close()


@app.post("/api/community/templates/{community_id}/fork")
def api_fork_community_template(community_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Fork a community template to your account."""
    user = require_user(rubricgen_session)
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


@app.get("/api/ground-truth")
def api_get_ground_truth(rubric_id: int | None = None, challenge_id: int | None = None,
                         rubricgen_session: str | None = Cookie(default=None)):
    """Get ground truth annotations for a rubric or challenge."""
    require_user(rubricgen_session)
    conn = get_db()
    try:
        return tmpl_mod.get_ground_truth(conn, rubric_id, challenge_id)
    finally:
        conn.close()


@app.get("/api/evaluations/{evaluation_id}/accuracy")
def api_evaluation_accuracy(evaluation_id: int, rubricgen_session: str | None = Cookie(default=None)):
    """Compare judge grades to ground truth for an evaluation."""
    require_user(rubricgen_session)
    conn = get_db()
    try:
        return tmpl_mod.compare_judge_to_ground_truth(conn, evaluation_id)
    finally:
        conn.close()


# ─── Template stats recording (internal use) ───

@app.post("/api/templates/{template_id}/record-stats")
def api_record_template_stats(template_id: int, body: dict, rubricgen_session: str | None = Cookie(default=None)):
    """Record question stats from an evaluation. Called after grading."""
    require_user(rubricgen_session)
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
    require_user(rubricgen_session)
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
    require_user(rubricgen_session)
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
    require_user(rubricgen_session)
    model_ids = [m.strip() for m in models.split(",")] if models else None
    conn = get_db()
    try:
        return analytics_mod.get_historical_trends(conn, model_ids, date_from, date_to)
    finally:
        conn.close()


@app.get("/api/analytics/themes")
def api_analytics_themes(rubricgen_session: str | None = Cookie(default=None)):
    """Theme-level summary statistics."""
    require_user(rubricgen_session)
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
    require_user(rubricgen_session)
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
    require_user(rubricgen_session)
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
