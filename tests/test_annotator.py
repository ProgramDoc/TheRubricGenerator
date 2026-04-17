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
    """Upload a tiny fake PDF and return its id.
    Uses the filename in the byte stream to keep the sha256 unique per test."""
    pdf_bytes = (b"%PDF-1.4\n%fake test pdf for " + filename.encode() + b"\n")
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


def test_field_groups_filter_universal_fields():
    from backend.annotator import (
        filter_universal_by_groups, UNIVERSAL_FIELD_IDS, FIELD_GROUPS,
    )
    # None / empty → all universal fields
    assert filter_universal_by_groups(None) == list(UNIVERSAL_FIELD_IDS)
    assert filter_universal_by_groups([]) == list(UNIVERSAL_FIELD_IDS)

    # Citation-only group returns citation fields, nothing else
    citation = filter_universal_by_groups(["citation"])
    assert set(citation) == set(FIELD_GROUPS["citation"])
    assert "population_participants" not in citation

    # Unknown groups are silently skipped; known ones still included
    got = filter_universal_by_groups(["citation", "unknown_xyz"])
    assert set(got) == set(FIELD_GROUPS["citation"])

    # All groups together cover every universal field
    all_ids: set[str] = set()
    for ids in FIELD_GROUPS.values():
        all_ids.update(ids)
    assert all_ids == set(UNIVERSAL_FIELD_IDS), "FIELD_GROUPS must partition UNIVERSAL_FIELD_IDS"


def test_prefill_prompt_honours_groups():
    from backend.annotator import build_prefill_prompt, FIELD_GROUPS
    prompt = build_prefill_prompt("Cohort Study", groups=["citation"])
    # Selected fields are listed
    for fid in FIELD_GROUPS["citation"]:
        assert f"- {fid}" in prompt
    # Unselected universal group is NOT listed
    assert "- funding_source" not in prompt
    # Type-specific fields always come along
    assert "- exposure_definition" in prompt


def test_prefill_rejects_non_list_groups(client, test_user):
    pid = _make_paper(client, test_user["cookie"], "bad_groups.pdf")
    r = client.post(f"/api/annotator/papers/{pid}/prefill",
                    cookies={"rubricgen_session": test_user["cookie"]},
                    json={"study_type": "Cohort Study", "groups": "citation"})
    assert r.status_code == 400


def test_prefill_rejects_non_list_type_or_modifier_fields(client, test_user):
    pid = _make_paper(client, test_user["cookie"], "bad_shape.pdf")
    cookie = {"rubricgen_session": test_user["cookie"]}
    r = client.post(f"/api/annotator/papers/{pid}/prefill", cookies=cookie,
                    json={"study_type": "Cohort Study", "type_fields": "exposure_definition"})
    assert r.status_code == 400
    r = client.post(f"/api/annotator/papers/{pid}/prefill", cookies=cookie,
                    json={"study_type": "Cohort Study", "modifier_fields": "clinical_trial_phase"})
    assert r.status_code == 400


def test_prefill_prompt_respects_type_and_modifier_fields():
    from backend.annotator import build_prefill_prompt, TYPE_FIELD_IDS, DESIGN_MODIFIER_COLS
    # type_fields=[] disables type-specific; modifier_fields=[] disables modifiers
    prompt = build_prefill_prompt("Cohort Study", groups=["citation"],
                                  type_fields=[], modifier_fields=[])
    for fid in TYPE_FIELD_IDS["Cohort Study"]:
        assert f"- {fid}" not in prompt, f"{fid} should be excluded"
    for fid in DESIGN_MODIFIER_COLS:
        assert f"- {fid}" not in prompt, f"{fid} should be excluded"

    # Explicit subsets show exactly those fields
    prompt = build_prefill_prompt(
        "Cohort Study",
        groups=[],    # empty groups → all universal; still, Layer 2/3 filters verify here
        type_fields=["exposure_definition"],
        modifier_fields=["clinical_trial_phase"],
    )
    assert "- exposure_definition" in prompt
    assert "- outcome_ascertainment" not in prompt  # other type-specific excluded
    assert "- clinical_trial_phase" in prompt
    assert "- industry_sponsored" not in prompt


def test_upload_records_storage_path(client, test_user):
    """New uploads must populate papers.storage_path (local uploads/ in tests)
    so reads succeed even if PAPERS_DIR is wiped."""
    pid = _make_paper(client, test_user["cookie"], "stored.pdf")
    from main import get_db
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT storage_path FROM papers WHERE id=?", (pid,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["storage_path"], "storage_path should be populated by upload"


def test_pdf_read_survives_wiped_papers_dir(client, test_user, tmp_path, monkeypatch):
    """Simulating a Render disk wipe: uploaded paper remains readable because
    its content is served from storage_path, not PAPERS_DIR."""
    pid = _make_paper(client, test_user["cookie"], "survives.pdf")
    # Redirect PAPERS_DIR to an empty tmp dir (no legacy fallback available)
    import main
    monkeypatch.setattr(main, "PAPERS_DIR", tmp_path)
    r = client.get(f"/api/papers/{pid}/pdf",
                   cookies={"rubricgen_session": test_user["cookie"]})
    assert r.status_code == 200, r.text
    assert r.content.startswith(b"%PDF")


def test_schema_endpoint_exposes_catalog(client, test_user):
    r = client.get("/api/annotator/schema",
                   cookies={"rubricgen_session": test_user["cookie"]})
    assert r.status_code == 200
    schema = r.json()
    assert "type_fields" in schema
    assert "Randomized Controlled Trial" in schema["type_fields"]
    assert "randomization_method" in schema["type_fields"]["Randomized Controlled Trial"]
    assert "modifier_fields" in schema
    assert "clinical_trial_phase" in schema["modifier_fields"]


def test_validate_custom_fields_happy_and_errors():
    from backend.annotator import validate_custom_fields
    from fastapi import HTTPException

    ok = validate_custom_fields([
        {"id": "age", "label": "Age", "type": "number"},
        {"id": "arm", "label": "Arm", "type": "select", "options": ["A", "B"]},
    ])
    assert len(ok) == 2
    assert ok[1]["options"] == ["A", "B"]

    import pytest
    with pytest.raises(HTTPException):
        validate_custom_fields([])  # empty
    with pytest.raises(HTTPException):
        validate_custom_fields([{"id": "Bad-ID", "label": "x", "type": "text"}])  # id pattern
    with pytest.raises(HTTPException):
        validate_custom_fields([
            {"id": "a", "label": "x", "type": "text"},
            {"id": "a", "label": "y", "type": "text"},
        ])  # duplicate ids
    with pytest.raises(HTTPException):
        validate_custom_fields([{"id": "a", "label": "x", "type": "select"}])  # select w/o options
    with pytest.raises(HTTPException):
        validate_custom_fields([{"id": "a", "label": "x", "type": "nope"}])  # bad type


def test_to_float_coercion():
    from backend.annotator import _to_float
    assert _to_float("12.3") == 12.3
    assert _to_float("1,234") == 1234.0
    assert _to_float("85%") == 85.0
    assert _to_float("1e3") == 1000.0
    assert _to_float("n/a") is None
    assert _to_float("") is None
    assert _to_float(None) is None


def test_build_custom_prompt_describes_fields():
    from backend.annotator import build_custom_prompt
    fields = [
        {"id": "sample_n", "label": "Sample size", "type": "number",
         "description": "total enrolled"},
        {"id": "arm", "label": "Arm", "type": "select",
         "options": ["A", "B"], "description": ""},
    ]
    prompt = build_custom_prompt(fields)
    assert "- sample_n (number): total enrolled" in prompt
    assert "- arm (select) — one of: A, B" in prompt


def test_schema_crud_roundtrip(client, test_user):
    cookie = {"rubricgen_session": test_user["cookie"]}
    # Create
    r = client.post("/api/annotator/schemas", cookies=cookie,
                    json={"name": "My schema", "description": "hi",
                          "fields": [{"id": "age", "label": "Age", "type": "number"}]})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    # List
    r = client.get("/api/annotator/schemas", cookies=cookie)
    assert r.status_code == 200
    assert any(s["id"] == sid for s in r.json())
    # Duplicate name → 409
    r = client.post("/api/annotator/schemas", cookies=cookie,
                    json={"name": "My schema", "fields":
                          [{"id": "age", "label": "Age", "type": "number"}]})
    assert r.status_code == 409
    # Patch
    r = client.patch(f"/api/annotator/schemas/{sid}", cookies=cookie,
                     json={"name": "Renamed", "fields":
                           [{"id": "age", "label": "Age (years)", "type": "number"}]})
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"
    # Get
    r = client.get(f"/api/annotator/schemas/{sid}", cookies=cookie)
    assert r.json()["fields"][0]["label"] == "Age (years)"
    # Delete
    r = client.delete(f"/api/annotator/schemas/{sid}", cookies=cookie)
    assert r.status_code == 200
    r = client.get(f"/api/annotator/schemas/{sid}", cookies=cookie)
    assert r.status_code == 404


def test_schema_cross_user_isolation(client, test_user):
    cookie = {"rubricgen_session": test_user["cookie"]}
    r = client.post("/api/annotator/schemas", cookies=cookie,
                    json={"name": "Private", "fields":
                          [{"id": "x", "label": "X", "type": "text"}]})
    sid = r.json()["id"]

    # Register a second user
    client.post("/api/auth/register", json={
        "email": "other@example.com", "display_name": "Other",
        "password": "otherpass123",
    })
    r = client.post("/api/auth/login", json={
        "email": "other@example.com", "password": "otherpass123",
    })
    other = {"rubricgen_session": r.cookies.get("rubricgen_session")}

    r = client.get(f"/api/annotator/schemas/{sid}", cookies=other)
    assert r.status_code == 404
    r = client.get("/api/annotator/schemas", cookies=other)
    assert all(s["id"] != sid for s in r.json())


def test_analytics_endpoint_empty_scope(client, test_user):
    r = client.get("/api/annotator/analytics",
                   cookies={"rubricgen_session": test_user["cookie"]})
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) >= {
        "paper_count", "total_papers_in_scope", "scope",
        "completion_rates", "categorical_distributions",
        "numeric_summaries", "reviewer_actions",
    }


def test_analytics_with_annotated_papers(client, test_user):
    cookie = {"rubricgen_session": test_user["cookie"]}
    p1 = _make_paper(client, test_user["cookie"], "an1.pdf")
    p2 = _make_paper(client, test_user["cookie"], "an2.pdf")

    # Fill data: p1 has a cohort with sample_size; p2 has an RCT
    client.post(f"/api/annotator/papers/{p1}/annotation", cookies=cookie, json={
        "data": {"study_type": "Cohort Study", "sample_size_total": "250",
                 "country_region": "USA"},
        "field_annotations": {
            "sample_size_total": {"status": "confirmed"},
            "study_type": {"status": "confirmed"},
        },
        "spans": [],
    })
    client.post(f"/api/annotator/papers/{p2}/annotation", cookies=cookie, json={
        "data": {"study_type": "Randomized Controlled Trial",
                 "sample_size_total": "1200", "country_region": "USA",
                 "randomization_method": "computer-generated"},
        "field_annotations": {
            "randomization_method": {"status": "corrected"},
        },
        "spans": [],
    })

    r = client.get(f"/api/annotator/analytics?paper_ids={p1},{p2}", cookies=cookie)
    assert r.status_code == 200
    body = r.json()
    assert body["paper_count"] == 2

    # Numeric summary for sample size
    num = {n["field_id"]: n for n in body["numeric_summaries"]}
    assert "sample_size_total" in num
    assert num["sample_size_total"]["n"] == 2
    assert num["sample_size_total"]["min"] == 250
    assert num["sample_size_total"]["max"] == 1200

    # Categorical distribution for country
    cat = {c["field_id"]: c for c in body["categorical_distributions"]}
    assert "country_region" in cat
    usa = next(v for v in cat["country_region"]["values"] if v["value"] == "USA")
    assert usa["count"] == 2

    # Reviewer actions — study_type confirmed once
    actions = {a["field_id"]: a for a in body["reviewer_actions"]}
    assert actions["study_type"]["confirmed"] == 1
    assert actions["randomization_method"]["corrected"] == 1


def test_run_requires_schema_ownership_and_known_papers(client, test_user):
    cookie = {"rubricgen_session": test_user["cookie"]}
    r = client.post("/api/annotator/schemas", cookies=cookie,
                    json={"name": "runsch", "fields":
                          [{"id": "x", "label": "X", "type": "text"}]})
    sid = r.json()["id"]
    # empty paper list → 400
    r = client.post(f"/api/annotator/schemas/{sid}/run", cookies=cookie,
                    json={"paper_ids": []})
    assert r.status_code == 400
    # unowned paper → 400
    r = client.post(f"/api/annotator/schemas/{sid}/run", cookies=cookie,
                    json={"paper_ids": [99999]})
    assert r.status_code == 400
    # unknown schema → 404
    r = client.post("/api/annotator/schemas/99999/run", cookies=cookie,
                    json={"paper_ids": [1]})
    assert r.status_code == 404


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
