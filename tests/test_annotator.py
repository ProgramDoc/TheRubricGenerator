"""Tests for the OGAI Annotator lab application.

Covers:
 - list endpoint includes per-paper annotation status
 - save→load round-trip bumps version 1→2
 - stale client version returns HTTP 409
 - spans are replaced atomically on save
 - CSV export includes header + per-paper rows
"""

from __future__ import annotations

import hashlib


def _make_paper(client, cookie: str, filename: str) -> int:
    """Upload a tiny fake PDF and return its id."""
    pdf_bytes = b"%PDF-1.4\n%fake test pdf\n"
    files = {"file": (filename, pdf_bytes, "application/pdf")}
    r = client.post("/api/papers/upload", files=files,
                    cookies={"rubricgen_session": cookie})
    assert r.status_code in (201, 200), r.text
    return r.json()["id"]


def test_list_papers_initially_unannotated(client, test_user):
    pid = _make_paper(client, test_user["cookie"], "list_unann.pdf")
    r = client.get("/api/annotator/papers",
                   cookies={"rubricgen_session": test_user["cookie"]})
    assert r.status_code == 200
    papers = r.json()
    row = next((p for p in papers if p["id"] == pid), None)
    assert row is not None
    assert row["ann_status"] is None
    assert row["ann_version"] is None


def test_save_load_roundtrip_bumps_version(client, test_user):
    pid = _make_paper(client, test_user["cookie"], "rt.pdf")
    cookie = {"rubricgen_session": test_user["cookie"]}

    # No annotation yet
    r = client.get(f"/api/annotator/papers/{pid}/annotation", cookies=cookie)
    assert r.status_code == 200
    assert r.json()["annotation"] is None

    # First save → version 1
    r = client.post(
        f"/api/annotator/papers/{pid}/annotation",
        cookies=cookie,
        json={"data": {"citation_title": "A trial"}, "field_annotations": {}, "spans": []},
    )
    assert r.status_code == 200
    assert r.json()["version"] == 1

    # Load back
    r = client.get(f"/api/annotator/papers/{pid}/annotation", cookies=cookie)
    assert r.status_code == 200
    body = r.json()
    assert body["annotation"]["version"] == 1
    assert body["annotation"]["data"]["citation_title"] == "A trial"

    # Second save with correct version → version 2
    r = client.post(
        f"/api/annotator/papers/{pid}/annotation",
        cookies=cookie,
        json={"data": {"citation_title": "A trial v2"},
              "field_annotations": {}, "spans": [],
              "version": 1},
    )
    assert r.status_code == 200
    assert r.json()["version"] == 2


def test_stale_version_returns_409(client, test_user):
    pid = _make_paper(client, test_user["cookie"], "conflict.pdf")
    cookie = {"rubricgen_session": test_user["cookie"]}

    client.post(f"/api/annotator/papers/{pid}/annotation", cookies=cookie,
                json={"data": {"citation_year": "2024"}, "spans": []})
    client.post(f"/api/annotator/papers/{pid}/annotation", cookies=cookie,
                json={"data": {"citation_year": "2025"}, "spans": [], "version": 1})

    # Third save with stale version → 409
    r = client.post(
        f"/api/annotator/papers/{pid}/annotation",
        cookies=cookie,
        json={"data": {"citation_year": "2026"}, "spans": [], "version": 1},
    )
    assert r.status_code == 409


def test_spans_replaced_on_save(client, test_user):
    pid = _make_paper(client, test_user["cookie"], "spans.pdf")
    cookie = {"rubricgen_session": test_user["cookie"]}

    set_a = [
        {"field_name": "citation_title", "page": 1, "text": "first", "x0": 0, "y0": 0, "x1": 10, "y1": 5},
        {"field_name": "citation_doi",   "page": 2, "text": "10.x",  "x0": 1, "y0": 1, "x1": 20, "y1": 6},
    ]
    client.post(f"/api/annotator/papers/{pid}/annotation", cookies=cookie,
                json={"data": {}, "spans": set_a})
    r = client.get(f"/api/annotator/papers/{pid}/annotation", cookies=cookie)
    assert len(r.json()["spans"]) == 2

    # Replace with a different set
    set_b = [
        {"field_name": "citation_title", "page": 3, "text": "only",  "x0": 0, "y0": 0, "x1": 10, "y1": 5},
    ]
    client.post(f"/api/annotator/papers/{pid}/annotation", cookies=cookie,
                json={"data": {}, "spans": set_b, "version": 1})
    r = client.get(f"/api/annotator/papers/{pid}/annotation", cookies=cookie)
    spans = r.json()["spans"]
    assert len(spans) == 1
    assert spans[0]["text"] == "only"
    assert spans[0]["page"] == 3


def test_csv_export_has_header_and_row(client, test_user):
    pid = _make_paper(client, test_user["cookie"], "export.pdf")
    cookie = {"rubricgen_session": test_user["cookie"]}
    client.post(f"/api/annotator/papers/{pid}/annotation", cookies=cookie,
                json={"data": {"citation_title": "CSV Test", "study_type": "Cohort Study"},
                      "field_annotations": {}, "spans": []})

    r = client.get("/api/annotator/export.csv", cookies=cookie)
    assert r.status_code == 200
    text = r.text
    header, *rest = text.strip().splitlines()
    assert "filename" in header and "citation_title" in header and "study_type" in header
    assert any("CSV Test" in line for line in rest)


def test_classify_requires_auth(client):
    r = client.post("/api/annotator/papers/1/classify")
    assert r.status_code == 401


def test_prefill_missing_study_type_returns_400(client, test_user):
    pid = _make_paper(client, test_user["cookie"], "pre.pdf")
    r = client.post(f"/api/annotator/papers/{pid}/prefill",
                    cookies={"rubricgen_session": test_user["cookie"]},
                    json={})
    assert r.status_code == 400


def test_cross_user_annotation_is_forbidden(client, test_user):
    """User A cannot read or write User B's annotations."""
    pid = _make_paper(client, test_user["cookie"], "privacy.pdf")

    r = client.post("/api/auth/register", json={
        "email": "otheruser@example.com",
        "display_name": "Other",
        "password": "otherpass123",
    })
    assert r.status_code == 201
    r = client.post("/api/auth/login", json={
        "email": "otheruser@example.com",
        "password": "otherpass123",
    })
    assert r.status_code == 200
    other_cookie = {"rubricgen_session": r.cookies.get("rubricgen_session")}

    r = client.get(f"/api/annotator/papers/{pid}/annotation", cookies=other_cookie)
    assert r.status_code == 403
    r = client.post(f"/api/annotator/papers/{pid}/annotation", cookies=other_cookie,
                    json={"data": {}, "spans": []})
    assert r.status_code == 403
