"""
TheRubricGenerator — FastAPI backend
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
import sqlite3
import urllib.error
import urllib.request
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Cookie, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.helpers import (
    strip_markdown_fences as _strip_markdown_fences,
    call_anthropic as _call_anthropic,
    call_gemini as _call_gemini,
    call_openai as _call_openai,
)
from backend.skills import (
    SKILLS_TABLE_SQL, seed_v1_skills,
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
from backend.membership import MEMBERSHIP_TABLES_SQL, seed_plans
from backend import membership as member_mod

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

OBSIDIAN_VAULT_DIR = Path(os.environ.get("OBSIDIAN_VAULT_DIR", str(DATA_DIR / "obsidian_vault")))
OBSIDIAN_VAULT_DIR.mkdir(parents=True, exist_ok=True)
(OBSIDIAN_VAULT_DIR / "challenges").mkdir(exist_ok=True)
(OBSIDIAN_VAULT_DIR / "papers").mkdir(exist_ok=True)

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
# DB
# ─────────────────────────────────────────────
def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = get_db()
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT    NOT NULL UNIQUE COLLATE NOCASE,
                display_name  TEXT    NOT NULL,
                password_hash TEXT    NOT NULL,
                password_salt TEXT    NOT NULL,
                role          TEXT    NOT NULL DEFAULT 'reviewer',
                created_at    TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT    PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT    DEFAULT (datetime('now')),
                expires_at TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS projects (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT    NOT NULL,
                user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS papers (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                filename      TEXT    NOT NULL,
                disk_filename TEXT,
                sha256        TEXT    NOT NULL,
                user_id       INTEGER REFERENCES users(id) ON DELETE CASCADE,
                project_id    INTEGER REFERENCES projects(id) ON DELETE SET NULL,
                created_at    TEXT    DEFAULT (datetime('now')),
                UNIQUE(sha256, user_id)
            );

            CREATE TABLE IF NOT EXISTS rubrics (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id     INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                rubric_type  TEXT    NOT NULL DEFAULT 'classification',
                rubric_json  TEXT    NOT NULL DEFAULT '{}',
                instructions TEXT,
                created_at   TEXT    DEFAULT (datetime('now')),
                updated_at   TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS evaluations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                rubric_id   INTEGER NOT NULL REFERENCES rubrics(id) ON DELETE CASCADE,
                paper_id    INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                eval_model  TEXT    NOT NULL,
                eval_json   TEXT    DEFAULT '{}',
                graded_json TEXT    DEFAULT '{}',
                total_score REAL    DEFAULT 0,
                max_score   REAL    DEFAULT 0,
                status      TEXT    DEFAULT 'pending',
                created_at  TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS password_resets (
                token      TEXT    PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT    DEFAULT (datetime('now')),
                expires_at TEXT    NOT NULL,
                used       INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_papers_user ON papers(user_id);
            CREATE INDEX IF NOT EXISTS idx_rubrics_paper_user ON rubrics(paper_id, user_id);
            CREATE INDEX IF NOT EXISTS idx_evaluations_paper_user ON evaluations(paper_id, user_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

            -- ─── Benchmark platform (Phase 1) ───
            CREATE TABLE IF NOT EXISTS challenges (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                title               TEXT    NOT NULL,
                theme               TEXT,
                kind                TEXT    NOT NULL DEFAULT 'manual' CHECK(kind IN ('manual','daily','dry_run')),
                status              TEXT    NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','complete','failed')),
                created_by          INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at          TEXT    DEFAULT (datetime('now')),
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
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                challenge_id       INTEGER NOT NULL UNIQUE REFERENCES challenges(id) ON DELETE CASCADE,
                rubric_json        TEXT    NOT NULL,
                generation_time_ms INTEGER NOT NULL DEFAULT 0,
                generator_skill_id INTEGER REFERENCES agent_skills(id) ON DELETE SET NULL,
                created_at         TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS model_participants (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
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
                created_at      TEXT    DEFAULT (datetime('now')),
                UNIQUE(challenge_id, model_id)
            );

            CREATE TABLE IF NOT EXISTS leaderboard_cache (
                model_id            TEXT PRIMARY KEY,
                provider            TEXT,
                total_challenges    INTEGER DEFAULT 0,
                cumulative_score    REAL    DEFAULT 0,
                avg_accuracy        REAL    DEFAULT 0,
                avg_speed_bonus     REAL    DEFAULT 0,
                last_updated        TEXT    DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_challenges_status ON challenges(status);
            CREATE INDEX IF NOT EXISTS idx_participants_challenge ON model_participants(challenge_id);

            -- ─── Phase 1.5: Model registry ───
            CREATE TABLE IF NOT EXISTS registered_models (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT    NOT NULL UNIQUE COLLATE NOCASE,
                version      TEXT    NOT NULL,
                provider     TEXT,
                git_repo     TEXT,
                organization TEXT,
                created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at   TEXT    DEFAULT (datetime('now'))
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
                added_at   TEXT    DEFAULT (datetime('now')),
                PRIMARY KEY (project_id, user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_pm_user ON project_members(user_id);

            -- ─── Phase 2: Scheduler state ───
            CREATE TABLE IF NOT EXISTS scheduler_state (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TEXT DEFAULT (datetime('now'))
            );
        """)
        # Phase 1: agent skills
        conn.executescript(SKILLS_TABLE_SQL)
        # Phase 3: billing, promo, agreements
        conn.executescript(BILLING_TABLES_SQL)
        conn.executescript(PROMO_TABLES_SQL)
        conn.executescript(AGREEMENTS_TABLE_SQL)
        conn.executescript(EXPERIMENTS_TABLE_SQL)
        # Phase 5: competition submissions
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS challenge_submissions (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                challenge_id        INTEGER NOT NULL REFERENCES challenges(id) ON DELETE CASCADE,
                registered_model_id INTEGER NOT NULL REFERENCES registered_models(id) ON DELETE CASCADE,
                answer_json         TEXT,
                submitted_at        TEXT,
                grade_json          TEXT,
                judge_time_ms       INTEGER,
                accuracy            REAL    DEFAULT 0,
                points              INTEGER DEFAULT 0,
                status              TEXT    DEFAULT 'open' CHECK(status IN ('open','submitted','grading','graded','failed','expired')),
                created_at          TEXT    DEFAULT (datetime('now')),
                UNIQUE(challenge_id, registered_model_id)
            );
            CREATE INDEX IF NOT EXISTS idx_cs_challenge ON challenge_submissions(challenge_id);
            CREATE INDEX IF NOT EXISTS idx_cs_model ON challenge_submissions(registered_model_id);
        """)
        # Phase 6: analytics & notifications
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS analytics_snapshots (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id   TEXT NOT NULL,
                theme      TEXT,
                difficulty TEXT,
                challenges INTEGER DEFAULT 0,
                correct    INTEGER DEFAULT 0,
                total      INTEGER DEFAULT 0,
                accuracy   REAL    DEFAULT 0,
                updated_at TEXT    DEFAULT (datetime('now')),
                UNIQUE(model_id, theme, difficulty)
            );
            CREATE INDEX IF NOT EXISTS idx_as_model ON analytics_snapshots(model_id);

            CREATE TABLE IF NOT EXISTS notification_preferences (
                user_id         INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                daily_complete  INTEGER DEFAULT 0,
                weekly_digest   INTEGER DEFAULT 0,
                updated_at      TEXT DEFAULT (datetime('now'))
            );
        """)
        # Phase 7: organizations
        conn.executescript(ORG_TABLES_SQL)
        # Phase 8: templates, community library, ground truth
        conn.executescript(TEMPLATE_TABLES_SQL)
        # Literature search
        conn.executescript(SEARCH_TABLES_SQL)
        # Membership plans
        conn.executescript(MEMBERSHIP_TABLES_SQL)
        conn.commit()
    _migrate_challenges_columns(conn)
    _migrate_org_columns(conn)
    _migrate_challenge_columns_v2(conn)
    seed_v1_skills(conn)
    seed_credit_packs(conn)
    seed_plans(conn)
    conn.close()
    _ensure_admin_user()
    _ensure_system_user()


def _migrate_challenges_columns(conn) -> None:
    """Phase 1.5 additive migration: add project_id, visibility, difficulty,
    registered_model_id columns to challenges if missing."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(challenges)").fetchall()}
    with conn:
        if "project_id" not in cols:
            conn.execute("ALTER TABLE challenges ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL")
        if "visibility" not in cols:
            conn.execute("ALTER TABLE challenges ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'")
        if "difficulty" not in cols:
            conn.execute("ALTER TABLE challenges ADD COLUMN difficulty TEXT")
        if "registered_model_id" not in cols:
            conn.execute("ALTER TABLE challenges ADD COLUMN registered_model_id INTEGER REFERENCES registered_models(id) ON DELETE SET NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_challenges_project ON challenges(project_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_challenges_visibility ON challenges(visibility)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_challenges_owner_vis ON challenges(created_by, visibility)")
        conn.commit()
    # Migrate challenges kind CHECK to allow 'dry_run'
    # SQLite doesn't support ALTER CONSTRAINT, so we disable foreign keys
    # temporarily and recreate the table if the check doesn't include dry_run.
    # Pragmatic approach: just try inserting and deleting a dry_run row.
    try:
        conn.execute("INSERT INTO challenges (title, theme, kind, status, created_by) VALUES ('__migrate_test','','dry_run','pending',NULL)")
        conn.execute("DELETE FROM challenges WHERE title='__migrate_test'")
        conn.commit()
    except Exception:
        # CHECK constraint rejected 'dry_run' — need to recreate table.
        # This is safe because CREATE TABLE IF NOT EXISTS already ran with the new CHECK.
        logger.info("Migrating challenges table to support kind='dry_run'...")
        try:
            conn.executescript("""
                PRAGMA foreign_keys=OFF;
                CREATE TABLE IF NOT EXISTS challenges_new (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    title               TEXT    NOT NULL,
                    theme               TEXT,
                    kind                TEXT    NOT NULL DEFAULT 'manual' CHECK(kind IN ('manual','daily','dry_run')),
                    status              TEXT    NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','complete','failed')),
                    created_by          INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at          TEXT    DEFAULT (datetime('now')),
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
                PRAGMA foreign_keys=ON;
            """)
            conn.commit()
        except Exception as e:
            logger.error("challenges table migration failed: %s", e)

    # Phase 3: add API fields to registered_models
    rm_cols = {r["name"] for r in conn.execute("PRAGMA table_info(registered_models)").fetchall()}
    with conn:
        if "api_base_url" not in rm_cols:
            conn.execute("ALTER TABLE registered_models ADD COLUMN api_base_url TEXT")
        if "api_key_encrypted" not in rm_cols:
            conn.execute("ALTER TABLE registered_models ADD COLUMN api_key_encrypted TEXT")
        if "price_per_test_credits" not in rm_cols:
            conn.execute("ALTER TABLE registered_models ADD COLUMN price_per_test_credits INTEGER DEFAULT 0")
        if "public_for_testing" not in rm_cols:
            conn.execute("ALTER TABLE registered_models ADD COLUMN public_for_testing INTEGER DEFAULT 0")
        if "active_for_daily" not in rm_cols:
            conn.execute("ALTER TABLE registered_models ADD COLUMN active_for_daily INTEGER DEFAULT 0")
        if "daily_admin_approved" not in rm_cols:
            conn.execute("ALTER TABLE registered_models ADD COLUMN daily_admin_approved INTEGER DEFAULT 0")
        if "agreement_signed_at" not in rm_cols:
            conn.execute("ALTER TABLE registered_models ADD COLUMN agreement_signed_at TEXT")
        if "model_api_key" not in rm_cols:
            conn.execute("ALTER TABLE registered_models ADD COLUMN model_api_key TEXT")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_rm_api_key ON registered_models(model_api_key)")
        conn.commit()
    # Phase 3.5: role on project_members
    pm_cols = {r["name"] for r in conn.execute("PRAGMA table_info(project_members)").fetchall()}
    if pm_cols and "role" not in pm_cols:
        with conn:
            conn.execute("ALTER TABLE project_members ADD COLUMN role TEXT NOT NULL DEFAULT 'member'")
            conn.commit()
    # Phase 3.5: can_run on model members, points on participants
    rmm_cols = {r["name"] for r in conn.execute("PRAGMA table_info(registered_model_members)").fetchall()}
    with conn:
        if "can_run" not in rmm_cols:
            conn.execute("ALTER TABLE registered_model_members ADD COLUMN can_run INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    mp_cols = {r["name"] for r in conn.execute("PRAGMA table_info(model_participants)").fetchall()}
    with conn:
        if "points" not in mp_cols:
            conn.execute("ALTER TABLE model_participants ADD COLUMN points INTEGER DEFAULT 0")
        conn.commit()
    # Phase 3.5: daily leaderboard columns
    lb_cols = {r["name"] for r in conn.execute("PRAGMA table_info(leaderboard_cache)").fetchall()}
    with conn:
        if "total_points" not in lb_cols:
            conn.execute("ALTER TABLE leaderboard_cache ADD COLUMN total_points INTEGER DEFAULT 0")
        if "daily_points" not in lb_cols:
            conn.execute("ALTER TABLE leaderboard_cache ADD COLUMN daily_points INTEGER DEFAULT 0")
        if "daily_streak" not in lb_cols:
            conn.execute("ALTER TABLE leaderboard_cache ADD COLUMN daily_streak INTEGER DEFAULT 0")
        if "daily_rank_change" not in lb_cols:
            conn.execute("ALTER TABLE leaderboard_cache ADD COLUMN daily_rank_change INTEGER DEFAULT 0")
        conn.commit()


def _migrate_org_columns(conn) -> None:
    """Phase 7 additive migration: add org_id to registered_models if missing."""
    rm_cols = {r["name"] for r in conn.execute("PRAGMA table_info(registered_models)").fetchall()}
    with conn:
        if "org_id" not in rm_cols:
            conn.execute("ALTER TABLE registered_models ADD COLUMN org_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rm_org ON registered_models(org_id)")
        conn.commit()


def _migrate_challenge_columns_v2(conn) -> None:
    """Add run_id, cost_estimate, cost_approved, org_id to challenges if missing."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(challenges)").fetchall()}
    with conn:
        if "run_id" not in cols:
            conn.execute("ALTER TABLE challenges ADD COLUMN run_id TEXT")
        if "cost_estimate" not in cols:
            conn.execute("ALTER TABLE challenges ADD COLUMN cost_estimate INTEGER")
        if "cost_approved" not in cols:
            conn.execute("ALTER TABLE challenges ADD COLUMN cost_approved INTEGER NOT NULL DEFAULT 0")
        if "org_id" not in cols:
            conn.execute("ALTER TABLE challenges ADD COLUMN org_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL")
        # Challenge events table
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS challenge_events (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                challenge_id INTEGER NOT NULL REFERENCES challenges(id) ON DELETE CASCADE,
                event_type   TEXT NOT NULL,
                message      TEXT NOT NULL,
                detail_json  TEXT,
                created_at   TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_ce_challenge ON challenge_events(challenge_id);
        """)
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
                "INSERT OR IGNORE INTO users (email, display_name, password_hash, password_salt, role) VALUES (?,?,?,?,?)",
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
                "INSERT OR IGNORE INTO users (email, display_name, password_hash, password_salt, role) VALUES (?,?,?,?,?)",
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


app = FastAPI(title="TheRubricGenerator", lifespan=_lifespan)


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
        """SELECT u.id, u.email, u.display_name, u.role
           FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token=? AND s.expires_at > ?""",
        (token, now),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def require_user(rubricgen_session: str | None = Cookie(default=None)) -> dict:
    user = _get_user_from_token(rubricgen_session)
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
    return FileResponse(str(FRONTEND / "dashboard.html"), media_type="text/html")


@app.get("/papers", include_in_schema=False)
def papers_page(rubricgen_session: str | None = Cookie(default=None)):
    user = _get_user_from_token(rubricgen_session)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(str(FRONTEND / "rubric_generator.html"), media_type="text/html")


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
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Email already registered")
    # Phase 7: auto-join orgs by email domain
    try:
        user_row = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if user_row:
            org_mod.join_by_domain(conn, user_row["id"])
    except Exception as e:
        logger.error("Domain auto-join failed for %s: %s", email, e)
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
    return user


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
    """Generate an SSO token and redirect the user to the Annotator."""
    user = require_user(rubricgen_session)
    token = _generate_sso_token(user)
    redirect_url = f"{ANNOTATOR_URL.rstrip('/')}/sso?token={token}"
    return RedirectResponse(redirect_url, status_code=302)


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
        subject   = "OGAI Rubric Generator — Password Reset"
        msg_body  = (
            f"Hi {row['display_name']},\n\n"
            f"We received a request to reset your password. Click the link below to set a new password. "
            f"This link expires in 1 hour.\n\n"
            f"{reset_url}\n\n"
            f"If you did not request this, you can safely ignore this email.\n\n"
            f"— OGAI Rubric Generator"
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
                        rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
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
    conn.close()

    result: dict = dict(challenge)
    result["project"] = dict(project) if project else None
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

    # Enrich with agent skill details
    if result.get("generator_skill_id"):
        gs = conn.execute("SELECT version, avg_performance, times_used FROM agent_skills WHERE id=?",
                          (result["generator_skill_id"],)).fetchone()
        result["generator_skill"] = dict(gs) if gs else None
    if result.get("judge_skill_id"):
        js = conn.execute("SELECT version, avg_performance, times_used FROM agent_skills WHERE id=?",
                          (result["judge_skill_id"],)).fetchone()
        result["judge_skill"] = dict(js) if js else None

    conn.close()
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
                "UPDATE challenges SET status='failed', error_message='Cancelled by user', completed_at=datetime('now') WHERE id=?",
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
            """SELECT p.filename, p.disk_filename
               FROM papers p JOIN challenge_papers cp ON cp.paper_id = p.id
               WHERE cp.challenge_id=? LIMIT 2""",
            (latest["id"],),
        ).fetchall()
        papers_b64 = []
        for r in paper_rows:
            path = PAPERS_DIR / (r["disk_filename"] or "")
            if path.exists():
                papers_b64.append({"filename": r["filename"], "b64": _b64.b64encode(path.read_bytes()).decode()})
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
        # Write to Obsidian
        from backend.obsidian import write_skill_note
        active = get_active_skill(conn, agent_type)
        versions = list_skill_versions(conn, agent_type)
        write_skill_note(OBSIDIAN_VAULT_DIR, agent_type, active, versions)
        return {"ok": True, "activated_version": version}
    finally:
        conn.close()


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


def _auth_model_key(request: Request, conn: sqlite3.Connection) -> dict:
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

        challenge = conn.execute("SELECT * FROM challenges WHERE id=?", (challenge_id,)).fetchone()
        if not challenge:
            raise HTTPException(404, "Challenge not found")

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
            """INSERT OR IGNORE INTO challenge_submissions
               (challenge_id, registered_model_id, status)
               VALUES (?,?,?)""",
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
                   SET answer_json=?, submitted_at=datetime('now'), status='submitted'
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
        cur = conn.execute("INSERT INTO projects (name, user_id) VALUES (?,?)", (name, user["id"]))
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
                    "INSERT OR IGNORE INTO project_members (project_id, user_id, role, added_by) VALUES (?,?,?,?)",
                    (pid, target["id"], "member", user["id"]),
                )
                conn.commit()
            shared_results.append({"email": email, "status": "shared"})
            try:
                _send_email(
                    email,
                    "OGAI Rubric Generator — Project shared with you",
                    f"Hi {target['display_name']},\n\n"
                    f"{user['display_name']} has shared the project \"{name}\" with you.\n\n"
                    f"Log in to view it: {APP_BASE_URL}\n\n— OGAI Rubric Generator",
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
    if not target:
        conn.close()
        raise HTTPException(400, f"No registered user with email {email}")
    if target["id"] == user["id"]:
        conn.close()
        raise HTTPException(400, "Cannot share a project with yourself")
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO project_members (project_id, user_id, role, added_by) VALUES (?,?,?,?)",
            (pid, target["id"], "member", user["id"]),
        )
        conn.commit()
    conn.close()
    try:
        _send_email(
            email,
            "OGAI Rubric Generator — Project shared with you",
            f"Hi {target['display_name']},\n\n"
            f"{user['display_name']} has shared the project \"{proj['name']}\" with you.\n\n"
            f"Log in to view it: {APP_BASE_URL}\n\n— OGAI Rubric Generator",
        )
    except Exception:
        pass
    return {"ok": True}


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
            "INSERT OR IGNORE INTO project_members (project_id, user_id, role, added_by) VALUES (?,?,?,?)",
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
        disk_name = f"{sha256}.pdf"
        (PAPERS_DIR / disk_name).write_bytes(content)
        with conn:
            cur = conn.execute(
                "INSERT INTO papers (filename, disk_filename, sha256, user_id, project_id) VALUES (?,?,?,?,?)",
                (file.filename, disk_name, sha256, user["id"], project_id),
            )
            conn.commit()
        pid = cur.lastrowid
        member_mod.increment_pdf_count(conn, user["id"])
        return {"id": pid, "filename": file.filename, "duplicate": False}
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
    row  = conn.execute("SELECT disk_filename FROM papers WHERE id=? AND user_id=?", (pid, user["id"])).fetchone()
    if row and row["disk_filename"]:
        disk = PAPERS_DIR / row["disk_filename"]
        if disk.exists():
            disk.unlink()
    with conn:
        conn.execute("DELETE FROM papers WHERE id=? AND user_id=?", (pid, user["id"]))
        conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/papers/{pid}/pdf")
def get_pdf(pid: int, rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    conn = get_db()
    row  = conn.execute(
        "SELECT disk_filename, filename FROM papers WHERE id=? AND user_id=?", (pid, user["id"])
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Paper not found")
    disk_name = row["disk_filename"] or f"{row['filename']}.pdf"
    path = PAPERS_DIR / disk_name
    if not path.exists():
        raise HTTPException(404, "PDF file not found on disk")
    return FileResponse(str(path), media_type="application/pdf", filename=row["filename"])


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
    """Read a PDF from disk and return base64."""
    conn = get_db()
    row  = conn.execute("SELECT disk_filename FROM papers WHERE id=?", (paper_id,)).fetchone()
    conn.close()
    if not row or not row["disk_filename"]:
        raise HTTPException(404, "Paper not found")
    path = PAPERS_DIR / row["disk_filename"]
    if not path.exists():
        raise HTTPException(404, "PDF file not found on disk")
    return base64.b64encode(path.read_bytes()).decode()


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
                "UPDATE rubrics SET rubric_json=?, instructions=?, updated_at=datetime('now') WHERE id=?",
                (json.dumps(rubric), body.instructions, existing["id"]),
            )
            rubric_id = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO rubrics (paper_id, user_id, rubric_type, rubric_json, instructions) VALUES (?,?,?,?,?)",
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
            "UPDATE rubrics SET rubric_json=?, instructions=?, rubric_type=?, updated_at=datetime('now') WHERE id=? AND user_id=?",
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
            "INSERT INTO evaluations (rubric_id, paper_id, user_id, eval_model, eval_json, status) VALUES (?,?,?,?,?,?)",
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
                "SELECT filename, disk_filename FROM papers WHERE id=? AND user_id=?",
                (pid, user["id"]),
            ).fetchone()
            if not paper:
                raise HTTPException(404, f"Paper {pid} not found")
            path = PAPERS_DIR / (paper["disk_filename"] or f"{paper['filename']}.pdf")
            if not path.exists():
                raise HTTPException(404, f"PDF file missing for paper {pid}")
            b64 = base64.b64encode(path.read_bytes()).decode()
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
                   VALUES (?, ?, 'comparative', ?, ?)""",
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
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET
                 daily_complete = excluded.daily_complete,
                 weekly_digest = excluded.weekly_digest,
                 updated_at = datetime('now')""",
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
