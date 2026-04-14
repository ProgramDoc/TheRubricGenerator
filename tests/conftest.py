"""Test fixtures for The AI Researcher.

Uses an in-memory SQLite database and FastAPI's TestClient to test
endpoints without touching the production database or external APIs.
"""

import json
import os
import sqlite3
import tempfile

import pytest
from fastapi.testclient import TestClient

# Set env vars BEFORE importing main (prevents real API calls / daily scheduler)
os.environ["ADMIN_SECRET"] = "test_admin_secret"
os.environ["DAILY_ENABLED"] = "false"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""


@pytest.fixture(scope="session")
def _temp_dir():
    """Shared temp directory for all tests in the session."""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture(autouse=True)
def _patch_db(monkeypatch, _temp_dir):
    """Override DB path and papers dir to use temp directory for each test."""
    from pathlib import Path
    db_path = Path(_temp_dir) / "test.db"
    papers_dir = Path(_temp_dir) / "papers"
    papers_dir.mkdir(exist_ok=True)

    import main
    monkeypatch.setattr(main, "DB_PATH", db_path)
    monkeypatch.setattr(main, "PAPERS_DIR", papers_dir)
    # Also set env var so backend.db.get_db() uses the test database
    monkeypatch.setenv("SQLITE_DB_PATH", str(db_path))

    # Re-initialize the DB for a fresh state
    if db_path.exists():
        db_path.unlink()
    main.init_db()
    yield


@pytest.fixture
def client():
    """FastAPI TestClient."""
    from main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def test_user(client):
    """Register a test user and return their session cookie + info."""
    r = client.post("/api/auth/register", json={
        "email": "testuser@example.com",
        "display_name": "Test User",
        "password": "testpass123",
    })
    assert r.status_code == 201

    r = client.post("/api/auth/login", json={
        "email": "testuser@example.com",
        "password": "testpass123",
    })
    assert r.status_code == 200
    cookie = r.cookies.get("rubricgen_session")
    assert cookie
    return {"cookie": cookie, "email": "testuser@example.com"}


@pytest.fixture
def admin_user(client):
    """Login as admin and return session cookie."""
    r = client.post("/api/auth/admin", json={"secret": "test_admin_secret"})
    assert r.status_code == 200
    cookie = r.cookies.get("rubricgen_session")
    assert cookie
    return {"cookie": cookie}


@pytest.fixture
def test_challenge(client, admin_user):
    """Create a pre-populated challenge with rubric for Competition API testing.

    Uses admin to insert directly into DB since we can't run actual LLM calls.
    """
    from main import get_db
    conn = get_db()

    # Create system user's paper (minimal — no actual PDF needed for compete tests)
    conn.execute(
        "INSERT INTO papers (filename, disk_filename, sha256, user_id) VALUES (?,?,?,?)",
        ("test_paper.pdf", "test_sha256.pdf", "test_sha256_hash", 1),
    )
    paper_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Create a complete challenge
    conn.execute(
        """INSERT INTO challenges (title, theme, kind, status, created_by, visibility)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("Test Daily Challenge", "Oncology RCTs", "daily", "complete", 1, "public"),
    )
    challenge_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Link paper to challenge
    conn.execute(
        "INSERT INTO challenge_papers (challenge_id, paper_id) VALUES (?, ?)",
        (challenge_id, paper_id),
    )

    # Create rubric with questions (ideal answers included — will be stripped for compete)
    rubric = {
        "theme": "Oncology RCTs",
        "questions": [
            {
                "id": "q1",
                "domain": "Methods",
                "question": "What study design was used?",
                "ideal_answer": "This was a randomized controlled trial.",
                "scoring_criteria": "Full credit for identifying RCT design.",
                "max_points": 3,
                "paper_ref": "test_paper.pdf",
            },
            {
                "id": "q2",
                "domain": "Results",
                "question": "What was the primary outcome?",
                "ideal_answer": "Overall survival improved by 15%.",
                "scoring_criteria": "Full credit for correct percentage.",
                "max_points": 3,
                "paper_ref": "test_paper.pdf",
            },
        ],
        "total_max_points": 6,
    }
    conn.execute(
        "INSERT INTO challenge_rubrics (challenge_id, rubric_json, generation_time_ms) VALUES (?, ?, ?)",
        (challenge_id, json.dumps(rubric), 5000),
    )
    conn.commit()
    conn.close()

    return {"challenge_id": challenge_id, "paper_id": paper_id}
