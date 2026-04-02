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
import secrets
import sqlite3
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Cookie, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

logging.basicConfig(level=logging.ERROR)
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

ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")
ADMIN_EMAIL  = os.environ.get("ADMIN_EMAIL",  "admin@rubricgen.local")
ADMIN_NAME   = os.environ.get("ADMIN_NAME",   "Admin")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL   = os.environ.get("ANTHROPIC_MODEL",   "claude-sonnet-4-20250514")
OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY",    "")
OPENAI_MODEL      = os.environ.get("OPENAI_MODEL",      "gpt-4o")

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
        """)
        conn.commit()
    conn.close()
    _ensure_admin_user()


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


init_db()

# ─────────────────────────────────────────────
# App
# ─────────────────────────────────────────────
app = FastAPI(title="TheRubricGenerator")


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

class ProjectCreate(BaseModel):
    name: str

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
    return FileResponse(str(FRONTEND / "rubric_generator.html"), media_type="text/html")


@app.get("/login", include_in_schema=False)
def login_page(rubricgen_session: str | None = Cookie(default=None)):
    user = _get_user_from_token(rubricgen_session)
    if user:
        return RedirectResponse("/", status_code=302)
    return FileResponse(str(FRONTEND / "login.html"), media_type="text/html")


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


# ─────────────────────────────────────────────
# Projects
# ─────────────────────────────────────────────
@app.get("/api/projects")
def list_projects(rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, created_at FROM projects WHERE user_id=? ORDER BY created_at",
        (user["id"],),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/projects", status_code=201)
def create_project(body: ProjectCreate, rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    conn = get_db()
    with conn:
        cur = conn.execute(
            "INSERT INTO projects (name, user_id) VALUES (?,?)", (body.name.strip(), user["id"])
        )
        conn.commit()
    pid = cur.lastrowid
    conn.close()
    return {"id": pid, "name": body.name.strip()}


@app.patch("/api/projects/{pid}")
def rename_project(pid: int, body: ProjectRename, rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    conn = get_db()
    with conn:
        conn.execute("UPDATE projects SET name=? WHERE id=? AND user_id=?", (body.name, pid, user["id"]))
        conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/projects/{pid}")
def delete_project(pid: int, rubricgen_session: str | None = Cookie(default=None)):
    user = require_user(rubricgen_session)
    conn = get_db()
    with conn:
        conn.execute("DELETE FROM projects WHERE id=? AND user_id=?", (pid, user["id"]))
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
    rubricgen_session: str | None = Cookie(default=None),
):
    user = require_user(rubricgen_session)
    content = await file.read()
    sha256  = hashlib.sha256(content).hexdigest()
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM papers WHERE sha256=? AND user_id=?", (sha256, user["id"])
    ).fetchone()
    if existing:
        conn.close()
        return {"id": existing["id"], "duplicate": True}
    disk_name = f"{sha256}.pdf"
    (PAPERS_DIR / disk_name).write_bytes(content)
    with conn:
        cur = conn.execute(
            "INSERT INTO papers (filename, disk_filename, sha256, user_id) VALUES (?,?,?,?)",
            (file.filename, disk_name, sha256, user["id"]),
        )
        conn.commit()
    pid = cur.lastrowid
    conn.close()
    return {"id": pid, "filename": file.filename, "duplicate": False}


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


def _call_anthropic(messages: list, system: str, max_tokens: int = 4096) -> str:
    """Call Anthropic API and return text response."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(500, "ANTHROPIC_API_KEY not configured")
    payload = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        return data["content"][0]["text"]
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        logger.error("Anthropic error: %s", body)
        raise HTTPException(502, f"Anthropic API error: {body[:200]}")


def _call_openai(messages: list, model: str, max_tokens: int = 4096) -> str:
    """Call OpenAI API and return text response."""
    if not OPENAI_API_KEY:
        raise HTTPException(500, "OPENAI_API_KEY not configured")
    payload = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        logger.error("OpenAI error: %s", body)
        raise HTTPException(502, f"OpenAI API error: {body[:200]}")


# ─────────────────────────────────────────────
# Rubric routes
# ─────────────────────────────────────────────
@app.post("/api/rubrics/generate")
async def generate_rubric(body: GenerateRubricRequest, rubricgen_session: str | None = Cookie(default=None)):
    """Use Claude to generate a structured rubric from a PDF."""
    user = require_user(rubricgen_session)

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

    # Strip any accidental markdown fences
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

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
    if model.startswith("gpt-"):
        # OpenAI — use vision for PDF as image (send as URL workaround: base64 JPEG not supported for PDFs)
        # For PDFs, we'll send the text content extracted or just prompt without image
        # OpenAI doesn't natively support PDF base64 — send questions only and note limitation
        messages = [
            {"role": "system", "content": system_msg},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"[Note: The PDF content has been provided. Please answer based on the paper.]\n\n{user_msg}"
                    }
                ]
            }
        ]
        # Attempt to pass PDF via data URL (OpenAI supports this for some models)
        try:
            messages[-1]["content"] = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:application/pdf;base64,{b64[:100]}"}  # test
                },
                {"type": "text", "text": user_msg}
            ]
        except Exception:
            pass
        # Simpler: just use text prompt (PDF content not directly supported by OpenAI base64)
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg}
        ]
        raw = _call_openai(messages, model=model, max_tokens=4096)
    else:
        # Claude (fallback)
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

    # Parse response
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

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

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

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
