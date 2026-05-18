"""Tests for the Chrome-extension pairing + queue + upload flow.

Covers:
- Pairing-code lifecycle: mint, consume, expiry, already-consumed, not-found
- ``rg_ext_*`` token resolves to the user via the existing ``X-API-Key`` flow
- Queue endpoint returns only the caller's ``extension_pending`` papers
- Upload: rejects non-PDF, validates ownership, atomically upgrades paper
- Skip: marks ``fetch_failed``, idempotent on terminal status
- Search-import ``mode='extension'`` requires pairing (412 when unpaired)
- LLM-resolve endpoint is auth-gated and accepts the rg_ext_ token

All tests use the in-memory SQLite fixture from conftest.py — no network.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


def _mint_pairing_code(client, cookie: str) -> dict:
    r = client.post("/api/extension/pair-code",
                    cookies={"rubricgen_session": cookie})
    assert r.status_code == 200, r.text
    return r.json()


def _pair_extension(client, cookie: str) -> str:
    """Helper: end-to-end pair, return the rg_ext_ token."""
    code_data = _mint_pairing_code(client, cookie)
    r = client.post("/api/extension/pair", json={"code": code_data["code"]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["token"].startswith("rg_ext_")
    return data["token"]


# ─── Pairing lifecycle ────────────────────────────────────────────────────

def test_mint_pairing_code_returns_short_code(client, test_user):
    out = _mint_pairing_code(client, test_user["cookie"])
    assert out["code"].startswith("EX-")
    # EX-XXXX-YYYY → 12 chars
    assert len(out["code"]) == 12
    assert out["ttl_seconds"] == 600
    assert "expires_at" in out


def test_consume_pairing_code_returns_ext_token(client, test_user):
    code_data = _mint_pairing_code(client, test_user["cookie"])
    r = client.post("/api/extension/pair", json={"code": code_data["code"]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["token"].startswith("rg_ext_")
    assert data["user_id"] > 0


def test_pairing_code_consumed_only_once(client, test_user):
    code_data = _mint_pairing_code(client, test_user["cookie"])
    r1 = client.post("/api/extension/pair", json={"code": code_data["code"]})
    assert r1.status_code == 200
    r2 = client.post("/api/extension/pair", json={"code": code_data["code"]})
    assert r2.status_code == 409, r2.text


def test_unknown_pairing_code_returns_404(client, test_user):
    # Need an existing code to invalidate prior — but mint happens regardless
    r = client.post("/api/extension/pair", json={"code": "EX-XXXX-XXXX"})
    assert r.status_code == 404


def test_expired_pairing_code_returns_410(client, test_user):
    # Mint a code, then directly expire it in the DB
    code_data = _mint_pairing_code(client, test_user["cookie"])
    from main import get_db
    conn = get_db()
    past = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    conn.execute(
        "UPDATE extension_pairings SET expires_at = ? WHERE code = ?",
        (past, code_data["code"]),
    )
    conn.commit()
    conn.close()
    r = client.post("/api/extension/pair", json={"code": code_data["code"]})
    assert r.status_code == 410


def test_minting_a_new_code_invalidates_prior_unconsumed_code(client, test_user):
    code_a = _mint_pairing_code(client, test_user["cookie"])
    code_b = _mint_pairing_code(client, test_user["cookie"])
    # The first code should now be expired (TTL set to "now" by the mint helper)
    r = client.post("/api/extension/pair", json={"code": code_a["code"]})
    assert r.status_code == 410
    # The new code still works
    r = client.post("/api/extension/pair", json={"code": code_b["code"]})
    assert r.status_code == 200


# ─── Token auth ───────────────────────────────────────────────────────────

def test_rg_ext_token_resolves_to_user_via_x_api_key(client, test_user):
    token = _pair_extension(client, test_user["cookie"])
    # Hit a general-seat endpoint with the ext token. /api/extension/queue is
    # paired-only and accepts the token.
    r = client.get("/api/extension/queue", headers={"X-API-Key": token})
    assert r.status_code == 200
    assert "papers" in r.json()


def test_revoke_extension_token_makes_it_stop_working(client, test_user):
    token = _pair_extension(client, test_user["cookie"])
    r = client.delete("/api/extension/token",
                      cookies={"rubricgen_session": test_user["cookie"]})
    assert r.status_code == 200
    # Clear the TestClient cookie jar so the next call tests the X-API-Key
    # path in isolation (otherwise the still-valid session cookie would
    # authenticate the request via require_user's fallback).
    client.cookies.clear()
    r = client.get("/api/extension/queue", headers={"X-API-Key": token})
    assert r.status_code == 401


def test_status_shows_pairing_state(client, test_user):
    r = client.get("/api/extension/status",
                   cookies={"rubricgen_session": test_user["cookie"]})
    assert r.status_code == 200
    assert r.json()["paired"] is False

    _pair_extension(client, test_user["cookie"])
    r = client.get("/api/extension/status",
                   cookies={"rubricgen_session": test_user["cookie"]})
    assert r.status_code == 200
    body = r.json()
    assert body["paired"] is True
    assert body["queue_count"] == 0


# ─── Queue + upload flow ──────────────────────────────────────────────────

def _seed_one_extension_pending_paper(user_email: str = "testuser@example.com",
                                       title: str = "Paywalled paper",
                                       external_url: str = "https://publisher.example/article/1") -> int:
    """Insert a metadata-only paper marked extension_pending. Returns paper_id."""
    from main import get_db
    conn = get_db()
    user_row = conn.execute("SELECT id FROM users WHERE email = ?", (user_email,)).fetchone()
    user_id = user_row["id"]
    cur = conn.execute(
        """INSERT INTO papers (filename, disk_filename, storage_path, sha256,
                              user_id, source, external_url, pdf_status)
           VALUES (?, NULL, NULL, ?, ?, 'search', ?, 'extension_pending') RETURNING id""",
        (f"{title}.pdf", f"sha-{title}", user_id, external_url),
    )
    paper_id = cur.lastrowid
    conn.commit()
    conn.close()
    return paper_id


def test_queue_returns_only_extension_pending_papers(client, test_user):
    token = _pair_extension(client, test_user["cookie"])
    pid = _seed_one_extension_pending_paper(title="Paper A", external_url="https://ex/1")
    pid2 = _seed_one_extension_pending_paper(title="Paper B", external_url="https://ex/2")

    # Add a non-pending paper too — should NOT appear
    from main import get_db
    conn = get_db()
    user_row = conn.execute("SELECT id FROM users WHERE email = ?",
                            ("testuser@example.com",)).fetchone()
    conn.execute(
        """INSERT INTO papers (filename, disk_filename, storage_path, sha256,
                              user_id, source, pdf_status)
           VALUES ('done.pdf', NULL, NULL, ?, ?, 'upload', 'present')""",
        (f"done-sha", user_row["id"]),
    )
    conn.commit()
    conn.close()

    r = client.get("/api/extension/queue", headers={"X-API-Key": token})
    assert r.status_code == 200
    papers = r.json()["papers"]
    paper_ids = {p["paper_id"] for p in papers}
    assert pid in paper_ids
    assert pid2 in paper_ids
    assert all(p["paper_id"] in {pid, pid2} for p in papers)
    # Each entry has a landing_url
    for p in papers:
        assert p.get("landing_url"), p


def test_upload_pdf_for_paper_upgrades_to_present(client, test_user, tmp_path):
    token = _pair_extension(client, test_user["cookie"])
    paper_id = _seed_one_extension_pending_paper()
    fake_pdf = b"%PDF-1.4\nfake pdf body for test"
    payload = {"pdf_b64": base64.b64encode(fake_pdf).decode()}

    r = client.post(f"/api/extension/papers/{paper_id}/pdf",
                    headers={"X-API-Key": token},
                    json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["paper_id"] == paper_id
    assert body["sha256"]

    # Verify paper was upgraded
    from main import get_db
    conn = get_db()
    p = conn.execute("SELECT pdf_status, sha256 FROM papers WHERE id = ?",
                     (paper_id,)).fetchone()
    conn.close()
    assert p["pdf_status"] == "present"
    assert p["sha256"] == body["sha256"]


def test_upload_rejects_non_pdf_bytes(client, test_user):
    token = _pair_extension(client, test_user["cookie"])
    paper_id = _seed_one_extension_pending_paper()
    payload = {"pdf_b64": base64.b64encode(b"<html>not a pdf</html>").decode()}
    r = client.post(f"/api/extension/papers/{paper_id}/pdf",
                    headers={"X-API-Key": token},
                    json=payload)
    assert r.status_code == 415


def test_upload_rejects_other_users_paper(client, test_user):
    """Paper owned by user A cannot be uploaded by user B's extension token."""
    # User A: testuser@example.com — we already have them
    other_paper_id = _seed_one_extension_pending_paper()

    # User B
    r = client.post("/api/auth/register", json={
        "email": "other@example.com",
        "display_name": "Other",
        "password": "pw1234567",
    })
    assert r.status_code == 201
    r = client.post("/api/auth/login", json={
        "email": "other@example.com",
        "password": "pw1234567",
    })
    other_cookie = r.cookies.get("rubricgen_session")
    other_token = _pair_extension(client, other_cookie)

    fake_pdf = b"%PDF-1.4\ntest"
    payload = {"pdf_b64": base64.b64encode(fake_pdf).decode()}
    r = client.post(f"/api/extension/papers/{other_paper_id}/pdf",
                    headers={"X-API-Key": other_token},
                    json=payload)
    assert r.status_code == 404


def test_upload_already_present_returns_409(client, test_user):
    token = _pair_extension(client, test_user["cookie"])
    paper_id = _seed_one_extension_pending_paper()
    fake_pdf = b"%PDF-1.4\nfirst"
    r = client.post(f"/api/extension/papers/{paper_id}/pdf",
                    headers={"X-API-Key": token},
                    json={"pdf_b64": base64.b64encode(fake_pdf).decode()})
    assert r.status_code == 200
    # Second call should 409 (already present)
    r = client.post(f"/api/extension/papers/{paper_id}/pdf",
                    headers={"X-API-Key": token},
                    json={"pdf_b64": base64.b64encode(b"%PDF-1.4\nsecond").decode()})
    assert r.status_code == 409


# ─── Skip ─────────────────────────────────────────────────────────────────

def test_skip_marks_paper_fetch_failed(client, test_user):
    token = _pair_extension(client, test_user["cookie"])
    paper_id = _seed_one_extension_pending_paper()
    r = client.post(f"/api/extension/papers/{paper_id}/skip",
                    headers={"X-API-Key": token})
    assert r.status_code == 200
    assert r.json()["status"] == "fetch_failed"

    # Idempotent: second call doesn't error
    r = client.post(f"/api/extension/papers/{paper_id}/skip",
                    headers={"X-API-Key": token})
    assert r.status_code == 200


def test_skip_unknown_paper_returns_404(client, test_user):
    token = _pair_extension(client, test_user["cookie"])
    r = client.post("/api/extension/papers/9999999/skip",
                    headers={"X-API-Key": token})
    assert r.status_code == 404


# ─── Search-import mode='extension' ───────────────────────────────────────

def _seed_search_session_with_results(user_email: str = "testuser@example.com"):
    from main import get_db
    conn = get_db()
    user_id = conn.execute("SELECT id FROM users WHERE email = ?",
                           (user_email,)).fetchone()["id"]
    cur = conn.execute(
        "INSERT INTO search_sessions (title, user_id) VALUES (?, ?) RETURNING id",
        ("Test session", user_id),
    )
    session_id = cur.lastrowid
    rids = []
    for pmid, doi, title in [
        ("aaa1", "10.1/a", "First"),
        ("bbb2", "10.2/b", "Second"),
    ]:
        cur = conn.execute(
            """INSERT INTO search_results (session_id, pmid, doi, title, url)
               VALUES (?, ?, ?, ?, ?) RETURNING id""",
            (session_id, pmid, doi, title, f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"),
        )
        rids.append(cur.lastrowid)
    conn.commit()
    conn.close()
    return session_id, rids


def test_extension_mode_requires_pairing(client, test_user):
    session_id, rids = _seed_search_session_with_results()
    r = client.post("/api/search/import",
                    cookies={"rubricgen_session": test_user["cookie"]},
                    json={"session_id": session_id, "result_ids": rids,
                          "mode": "extension"})
    assert r.status_code == 412


def test_extension_mode_queues_papers_when_paired(client, test_user):
    token = _pair_extension(client, test_user["cookie"])
    session_id, rids = _seed_search_session_with_results()
    r = client.post("/api/search/import",
                    cookies={"rubricgen_session": test_user["cookie"]},
                    json={"session_id": session_id, "result_ids": rids,
                          "mode": "extension"})
    assert r.status_code == 200
    body = r.json()
    assert body["imported"] == 2
    # Queue now shows 2
    r = client.get("/api/extension/queue", headers={"X-API-Key": token})
    assert r.status_code == 200
    assert len(r.json()["papers"]) == 2


# ─── /api/papers/{pid}/queue-for-extension ────────────────────────────────

def test_queue_for_extension_endpoint_requires_pairing(client, test_user):
    # Insert a metadata-only paper (not yet via extension)
    from main import get_db
    conn = get_db()
    user_id = conn.execute("SELECT id FROM users WHERE email = ?",
                           ("testuser@example.com",)).fetchone()["id"]
    cur = conn.execute(
        """INSERT INTO papers (filename, disk_filename, storage_path, sha256,
                              user_id, source, pdf_status)
           VALUES ('x.pdf', NULL, NULL, ?, ?, 'upload', 'metadata_only') RETURNING id""",
        (f"sha-x", user_id),
    )
    paper_id = cur.lastrowid
    conn.commit()
    conn.close()

    r = client.post(f"/api/papers/{paper_id}/queue-for-extension",
                    cookies={"rubricgen_session": test_user["cookie"]})
    assert r.status_code == 412


def test_queue_for_extension_promotes_metadata_to_pending(client, test_user):
    _pair_extension(client, test_user["cookie"])
    from main import get_db
    conn = get_db()
    user_id = conn.execute("SELECT id FROM users WHERE email = ?",
                           ("testuser@example.com",)).fetchone()["id"]
    cur = conn.execute(
        """INSERT INTO papers (filename, disk_filename, storage_path, sha256,
                              user_id, source, pdf_status)
           VALUES ('y.pdf', NULL, NULL, ?, ?, 'search', 'metadata_only') RETURNING id""",
        (f"sha-y", user_id),
    )
    paper_id = cur.lastrowid
    conn.commit()
    conn.close()

    r = client.post(f"/api/papers/{paper_id}/queue-for-extension",
                    cookies={"rubricgen_session": test_user["cookie"]})
    assert r.status_code == 200
    assert r.json()["pdf_status"] == "extension_pending"


def test_queue_for_extension_409_on_present_paper(client, test_user):
    _pair_extension(client, test_user["cookie"])
    from main import get_db
    conn = get_db()
    user_id = conn.execute("SELECT id FROM users WHERE email = ?",
                           ("testuser@example.com",)).fetchone()["id"]
    cur = conn.execute(
        """INSERT INTO papers (filename, disk_filename, storage_path, sha256,
                              user_id, source, pdf_status)
           VALUES ('z.pdf', NULL, NULL, ?, ?, 'upload', 'present') RETURNING id""",
        (f"sha-z", user_id),
    )
    paper_id = cur.lastrowid
    conn.commit()
    conn.close()

    r = client.post(f"/api/papers/{paper_id}/queue-for-extension",
                    cookies={"rubricgen_session": test_user["cookie"]})
    assert r.status_code == 409


# ─── LLM-resolve endpoint ─────────────────────────────────────────────────

def test_resolve_pdf_url_requires_auth(client):
    r = client.post("/api/extension/resolve-pdf-url",
                    json={"landing_url": "https://x", "anchors": []})
    assert r.status_code == 401


def test_resolve_pdf_url_returns_null_for_empty_anchors(client, test_user):
    token = _pair_extension(client, test_user["cookie"])
    r = client.post("/api/extension/resolve-pdf-url",
                    headers={"X-API-Key": token},
                    json={"landing_url": "https://publisher.example/article",
                          "anchors": []})
    assert r.status_code == 200
    assert r.json()["pdf_url"] is None


def test_resolve_pdf_url_calls_picker(client, test_user):
    """When anchors are present, the endpoint delegates to pdf_link_picker."""
    token = _pair_extension(client, test_user["cookie"])
    expected = "https://publisher.example/paper.pdf"
    with patch("backend.pdf_link_picker.pick_pdf_url_from_anchors",
               return_value=expected) as mock:
        r = client.post("/api/extension/resolve-pdf-url",
                        headers={"X-API-Key": token},
                        json={"landing_url": "https://publisher.example/article",
                              "anchors": [{"href": "https://x", "text": "Download PDF"}]})
        assert r.status_code == 200
        assert r.json()["pdf_url"] == expected
        mock.assert_called_once()
