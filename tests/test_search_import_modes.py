"""Tests for the two-mode search-result import flow.

Covers:
- mode='metadata' creates a paper row with pdf_status='metadata_only' and
  external_url set, and never calls the pdf fetcher
- mode='fetch' enqueues a pdf_fetch_runs row + spawns a worker
- the worker creates pdf_status='present' rows on success and
  pdf_status='fetch_failed' rows on failure (with credit refunds)
"""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest


def _seed_search_session(client, cookie: str) -> tuple[int, list[int]]:
    """Insert a search session + 3 search results directly via the DB.

    Returns ``(session_id, [result_ids])``. Bypasses the search execute
    endpoint so we don't need to mock PubMed.
    """
    from main import get_db
    conn = get_db()
    user_id = conn.execute("SELECT id FROM users WHERE email = ?",
                           ("testuser@example.com",)).fetchone()["id"]
    cur = conn.execute(
        "INSERT INTO search_sessions (title, user_id) VALUES (?, ?) RETURNING id",
        ("Test session", user_id),
    )
    session_id = cur.lastrowid

    rows = [
        # PMCID present, has DOI + URL — gives the fetcher every chance
        ("12345", "10.1234/foo", "Trial of foo", "Smith J", "JAMA", "2024",
         "Abstract", "PMC1234", "https://pubmed.ncbi.nlm.nih.gov/12345/"),
        # No PMCID, has DOI
        ("67890", "10.5678/bar", "Trial of bar", "Jones B", "NEJM", "2023",
         "Abstract", None, "https://pubmed.ncbi.nlm.nih.gov/67890/"),
        # Sparse — only title (test the fallback url builder)
        (None, None, "Lonely paper", None, None, None, None, None, None),
    ]
    rids = []
    for r in rows:
        cur = conn.execute(
            """INSERT INTO search_results
               (session_id, pmid, doi, title, authors, journal, pub_date,
                abstract, pmcid, url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
            (session_id,) + r,
        )
        rids.append(cur.lastrowid)
    conn.commit()
    conn.close()
    return session_id, rids


def test_metadata_import_creates_metadata_only_rows(client, test_user):
    cookie = {"rubricgen_session": test_user["cookie"]}
    session_id, rids = _seed_search_session(client, test_user["cookie"])

    r = client.post("/api/search/import", cookies=cookie, json={
        "session_id": session_id,
        "result_ids": rids,
        "mode": "metadata",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["imported"] == 3
    assert body["failed"] == 0

    from main import get_db
    conn = get_db()
    papers = conn.execute(
        "SELECT id, source, pdf_status, external_url FROM papers WHERE source='search' ORDER BY id"
    ).fetchall()
    conn.close()

    assert len(papers) == 3
    for p in papers:
        assert p["pdf_status"] == "metadata_only"
    # Two of the three seeded rows have PMID/DOI/URL → external_url; the
    # "lonely" third row has none, so its external_url is None.
    urls = [p["external_url"] for p in papers]
    assert sum(1 for u in urls if u) == 2


def test_metadata_import_does_not_call_pdf_fetcher(client, test_user, monkeypatch):
    """The metadata-only path must NEVER touch the network/fetcher."""
    cookie = {"rubricgen_session": test_user["cookie"]}
    session_id, rids = _seed_search_session(client, test_user["cookie"])

    # If the fetcher is invoked, fail loudly
    from backend import pdf_fetcher
    def boom(*a, **kw):
        raise AssertionError("pdf_fetcher must not be called in metadata mode")
    monkeypatch.setattr(pdf_fetcher, "fetch_pdf_for_result", boom)

    r = client.post("/api/search/import", cookies=cookie, json={
        "session_id": session_id,
        "result_ids": rids,
        "mode": "metadata",
    })
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 3


def test_fetch_mode_validates_input(client, test_user):
    cookie = {"rubricgen_session": test_user["cookie"]}
    session_id, _ = _seed_search_session(client, test_user["cookie"])

    # Empty result_ids should 400
    r = client.post("/api/search/import", cookies=cookie, json={
        "session_id": session_id, "result_ids": [], "mode": "fetch",
    })
    assert r.status_code == 400

    # Bad mode should 400
    r = client.post("/api/search/import", cookies=cookie, json={
        "session_id": session_id, "result_ids": [1], "mode": "wat",
    })
    assert r.status_code == 400


def test_fetch_mode_creates_run_and_completes(client, test_user, monkeypatch):
    """Stub the fetcher to return success for one row, None for another.

    Verify both papers end up in the DB with the right pdf_status and that
    pdf_fetch_runs is marked complete with succeeded/failed counts.
    """
    cookie = {"rubricgen_session": test_user["cookie"]}
    session_id, rids = _seed_search_session(client, test_user["cookie"])

    from backend import pdf_fetcher

    fake_pdf = b"%PDF-1.4\nfake"
    import hashlib
    fake_sha = hashlib.sha256(fake_pdf).hexdigest()

    def fake_fetch(result, dest_dir, use_firecrawl=False):
        # Succeed for the first PMID, fail for the rest
        if result.get("pmid") == "12345":
            return {"sha256": fake_sha, "filename": f"{fake_sha}.pdf",
                    "storage_path": f"local/{fake_sha}.pdf"}
        return None

    monkeypatch.setattr(pdf_fetcher, "fetch_pdf_for_result", fake_fetch)

    # Admin bypass means our test_user (non-admin) needs credits — give them some
    from main import get_db
    conn = get_db()
    user_id = conn.execute("SELECT id FROM users WHERE email = ?",
                           ("testuser@example.com",)).fetchone()["id"]
    conn.execute("INSERT OR IGNORE INTO user_credits (user_id, balance) VALUES (?, ?)",
                 (user_id, 1000))
    conn.execute("UPDATE user_credits SET balance = 1000 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

    r = client.post("/api/search/import", cookies=cookie, json={
        "session_id": session_id,
        "result_ids": rids,
        "mode": "fetch",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    assert body["credits_charged"] == 6  # 3 results * 2 credits
    run_id = body["run_id"]

    # Wait for the daemon thread to finish (≤ 5s on a stubbed fetcher)
    deadline = time.time() + 5.0
    while time.time() < deadline:
        s = client.get(f"/api/search/pdf-fetch/{run_id}", cookies=cookie)
        if s.status_code == 200 and s.json().get("status") == "complete":
            break
        time.sleep(0.05)

    final = client.get(f"/api/search/pdf-fetch/{run_id}", cookies=cookie).json()
    assert final["status"] == "complete"
    assert final["succeeded"] == 1
    assert final["failed"] == 2
    assert final["refunded"] == 2  # both failures refunded

    # Check the DB
    conn = get_db()
    papers = conn.execute(
        "SELECT pdf_status, external_url FROM papers WHERE source='search' ORDER BY id"
    ).fetchall()
    conn.close()
    statuses = sorted(p["pdf_status"] for p in papers)
    assert statuses == ["fetch_failed", "fetch_failed", "present"]


def test_fetch_upgrades_metadata_only_paper_to_pdf(client, test_user, monkeypatch):
    """A metadata-only paper from a previous run should be UPGRADED in place
    when a later fetch run succeeds — not re-imported, not skipped."""
    cookie = {"rubricgen_session": test_user["cookie"]}
    session_id, rids = _seed_search_session(client, test_user["cookie"])

    # Step 1: do a metadata-only import. All papers end up pdf_status='metadata_only'.
    r = client.post("/api/search/import", cookies=cookie, json={
        "session_id": session_id, "result_ids": rids, "mode": "metadata",
    })
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 3

    from main import get_db
    conn = get_db()
    user_id = conn.execute("SELECT id FROM users WHERE email = ?",
                           ("testuser@example.com",)).fetchone()["id"]
    conn.execute("INSERT OR IGNORE INTO user_credits (user_id, balance) VALUES (?, ?)",
                 (user_id, 1000))
    conn.execute("UPDATE user_credits SET balance = 1000 WHERE user_id = ?", (user_id,))
    conn.commit()
    before_papers = conn.execute(
        "SELECT id, pdf_status FROM papers WHERE user_id=? ORDER BY id",
        (user_id,),
    ).fetchall()
    metadata_only_ids = [p["id"] for p in before_papers if p["pdf_status"] == "metadata_only"]
    assert len(metadata_only_ids) == 3
    conn.close()

    # Step 2: re-run in fetch mode. Stub the fetcher to succeed for one paper.
    from backend import pdf_fetcher
    import hashlib
    fake_pdf = b"%PDF-1.4\nupgraded"
    fake_sha = hashlib.sha256(fake_pdf).hexdigest()
    def fake_fetch(result, dest_dir, use_firecrawl=False):
        if result.get("pmid") == "12345":
            return {"sha256": fake_sha, "filename": f"{fake_sha}.pdf",
                    "storage_path": f"local/{fake_sha}.pdf"}
        return None
    monkeypatch.setattr(pdf_fetcher, "fetch_pdf_for_result", fake_fetch)

    r = client.post("/api/search/import", cookies=cookie, json={
        "session_id": session_id, "result_ids": rids, "mode": "fetch",
    })
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    deadline = time.time() + 5.0
    while time.time() < deadline:
        s = client.get(f"/api/search/pdf-fetch/{run_id}", cookies=cookie)
        if s.status_code == 200 and s.json().get("status") == "complete":
            break
        time.sleep(0.05)

    # Step 3: assert the SAME paper id was upgraded (not duplicated)
    conn = get_db()
    after_papers = conn.execute(
        "SELECT id, pdf_status, sha256 FROM papers WHERE user_id=? ORDER BY id",
        (user_id,),
    ).fetchall()
    conn.close()
    # Same number of paper rows — no duplicates
    assert len(after_papers) == len(before_papers)
    # Exactly one row upgraded to 'present' with the fake sha
    upgraded = [p for p in after_papers if p["pdf_status"] == "present"]
    assert len(upgraded) == 1
    assert upgraded[0]["sha256"] == fake_sha
    # Two still metadata-only (or fetch_failed) — they didn't churn
    still_metadata = [p for p in after_papers if p["pdf_status"] in ("metadata_only", "fetch_failed")]
    assert len(still_metadata) == 2


def test_fetch_skips_papers_that_already_have_pdf(client, test_user, monkeypatch):
    """If pdf_status='present', a re-run should skip without calling the fetcher."""
    cookie = {"rubricgen_session": test_user["cookie"]}
    session_id, rids = _seed_search_session(client, test_user["cookie"])

    # Stage: insert papers as present-PDF directly via metadata import + manual UPDATE
    r = client.post("/api/search/import", cookies=cookie, json={
        "session_id": session_id, "result_ids": rids[:1], "mode": "metadata",
    })
    assert r.status_code == 200
    from main import get_db
    conn = get_db()
    conn.execute("UPDATE papers SET pdf_status='present' WHERE user_id IN (SELECT id FROM users WHERE email=?)",
                 ("testuser@example.com",))
    user_id = conn.execute("SELECT id FROM users WHERE email = ?",
                           ("testuser@example.com",)).fetchone()["id"]
    conn.execute("INSERT OR IGNORE INTO user_credits (user_id, balance) VALUES (?, ?)",
                 (user_id, 1000))
    conn.execute("UPDATE user_credits SET balance = 1000 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

    from backend import pdf_fetcher
    fetcher_calls = []
    def spy(result, dest_dir, use_firecrawl=False):
        fetcher_calls.append(result.get("pmid"))
        return None
    monkeypatch.setattr(pdf_fetcher, "fetch_pdf_for_result", spy)

    r = client.post("/api/search/import", cookies=cookie, json={
        "session_id": session_id, "result_ids": rids[:1], "mode": "fetch",
    })
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    deadline = time.time() + 5.0
    while time.time() < deadline:
        s = client.get(f"/api/search/pdf-fetch/{run_id}", cookies=cookie)
        if s.status_code == 200 and s.json().get("status") == "complete":
            break
        time.sleep(0.05)
    # Fetcher should NOT have been called for the already-PDF-backed paper
    assert fetcher_calls == []


def test_fetch_events_endpoint_returns_progress(client, test_user, monkeypatch):
    """The /events endpoint should stream run_started → result_* → run_complete."""
    cookie = {"rubricgen_session": test_user["cookie"]}
    session_id, rids = _seed_search_session(client, test_user["cookie"])

    from backend import pdf_fetcher
    monkeypatch.setattr(pdf_fetcher, "fetch_pdf_for_result", lambda *a, **kw: None)

    from main import get_db
    conn = get_db()
    uid = conn.execute("SELECT id FROM users WHERE email = ?",
                       ("testuser@example.com",)).fetchone()["id"]
    conn.execute("INSERT OR IGNORE INTO user_credits (user_id, balance) VALUES (?, ?)",
                 (uid, 1000))
    conn.execute("UPDATE user_credits SET balance = 1000 WHERE user_id = ?", (uid,))
    conn.commit()
    conn.close()

    r = client.post("/api/search/import", cookies=cookie, json={
        "session_id": session_id, "result_ids": rids[:1], "mode": "fetch",
    })
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]

    deadline = time.time() + 5.0
    while time.time() < deadline:
        s = client.get(f"/api/search/pdf-fetch/{run_id}", cookies=cookie)
        if s.status_code == 200 and s.json().get("status") == "complete":
            break
        time.sleep(0.05)

    ev = client.get(f"/api/search/pdf-fetch/{run_id}/events", cookies=cookie)
    assert ev.status_code == 200
    types = [e["event_type"] for e in ev.json()["events"]]
    assert types[0] == "run_started"
    assert "result_started" in types
    assert "result_failed" in types
    assert types[-1] == "run_complete"
