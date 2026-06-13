"""Tests for the Synthesis SR-pipeline expansion:

- Library <-> Synthesis delete cascade + JSON-blob scrub
- Paper collections (manual + auto 'selected' folder synced from a review)
- PubMed mapping helpers (title/DOI reverse lookup, MEDLINE parsing)
- Two-stage screening + multi-stage PRISMA counts

LLM and network calls are mocked; the statistics engine is exercised directly.
"""
import json

import pytest


def _uid(email):
    from main import get_db
    conn = get_db()
    row = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    conn.close()
    return row["id"]


def _mk_paper(uid, name, sha, source="upload", **cols):
    from main import get_db
    conn = get_db()
    keys = ["filename", "sha256", "user_id", "source"] + list(cols.keys())
    vals = [name, sha, uid, source] + list(cols.values())
    ph = ",".join("?" * len(keys))
    pid = conn.execute(
        f"INSERT INTO papers ({','.join(keys)}) VALUES ({ph}) RETURNING id", vals
    ).lastrowid
    conn.commit()
    conn.close()
    return pid


# ── Phase 2: delete cascade + scrub ──────────────────────────────────────────

def test_delete_paper_scrubs_synthesis_and_cascades(client, test_user):
    from main import get_db
    ck = {"rubricgen_session": test_user["cookie"]}
    uid = _uid(test_user["email"])
    p1 = _mk_paper(uid, "a.pdf", "h1")
    p2 = _mk_paper(uid, "b.pdf", "h2")

    conn = get_db()
    rid = conn.execute(
        "INSERT INTO synthesis_reviews (user_id,title,paper_ids_json,paper_count,status) "
        "VALUES (?,?,?,?,?) RETURNING id",
        (uid, "R", json.dumps([p1, p2]), 2, "complete"),
    ).lastrowid
    for pid in (p1, p2):
        conn.execute(
            "INSERT INTO synthesis_studies (review_id,paper_id,status,screening_decision) "
            "VALUES (?,?, 'included','include')",
            (rid, pid),
        )
    conn.commit()
    conn.close()

    # delete p1 through the real API (exercises the handler + scrub)
    r = client.delete(f"/api/papers/{p1}", cookies=ck)
    assert r.status_code == 200

    conn = get_db()
    # FK cascade removed the study row
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM synthesis_studies WHERE review_id=?", (rid,)
    ).fetchone()["n"]
    assert n == 1
    # scrub updated the JSON blob + count
    rv = conn.execute(
        "SELECT paper_ids_json, paper_count FROM synthesis_reviews WHERE id=?", (rid,)
    ).fetchone()
    assert json.loads(rv["paper_ids_json"]) == [p2]
    assert rv["paper_count"] == 1
    conn.close()


def test_delete_paper_substring_id_not_overmatched(client, test_user):
    """paper_id 1 must not be scrubbed from a list that only contains 11."""
    from main import get_db
    ck = {"rubricgen_session": test_user["cookie"]}
    uid = _uid(test_user["email"])
    # make enough papers that we have an id and a 1X id
    ids = [_mk_paper(uid, f"p{i}.pdf", f"hh{i}") for i in range(13)]
    target = ids[0]
    other = ids[11] if len(ids) > 11 else ids[-1]

    conn = get_db()
    rid = conn.execute(
        "INSERT INTO synthesis_reviews (user_id,title,paper_ids_json,paper_count,status) "
        "VALUES (?,?,?,?,?) RETURNING id",
        (uid, "R2", json.dumps([other]), 1, "complete"),
    ).lastrowid
    conn.commit()
    conn.close()

    r = client.delete(f"/api/papers/{target}", cookies=ck)
    assert r.status_code == 200

    conn = get_db()
    rv = conn.execute(
        "SELECT paper_ids_json, paper_count FROM synthesis_reviews WHERE id=?", (rid,)
    ).fetchone()
    # `other` (e.g. 12) must survive even if target id (e.g. 1) is a substring
    assert json.loads(rv["paper_ids_json"]) == [other]
    assert rv["paper_count"] == 1
    conn.close()


# ── Phase 3: collections (manual CRUD + auto 'selected' sync) ─────────────────

def test_collection_crud(client, test_user):
    ck = {"rubricgen_session": test_user["cookie"]}
    uid = _uid(test_user["email"])
    p1 = _mk_paper(uid, "a.pdf", "ha")
    p2 = _mk_paper(uid, "b.pdf", "hb")

    r = client.post("/api/collections", json={"name": "My folder"}, cookies=ck)
    assert r.status_code == 201
    cid = r.json()["id"]

    assert client.post(f"/api/collections/{cid}/papers/{p1}", cookies=ck).status_code == 201
    assert client.post(f"/api/collections/{cid}/papers/{p2}", cookies=ck).status_code == 201
    r = client.get(f"/api/collections/{cid}/papers", cookies=ck)
    assert sorted(r.json()["paper_ids"]) == sorted([p1, p2])

    # remove one, rename, list shows count
    assert client.delete(f"/api/collections/{cid}/papers/{p1}", cookies=ck).status_code == 200
    assert client.patch(f"/api/collections/{cid}", json={"name": "Renamed"}, cookies=ck).status_code == 200
    r = client.get("/api/collections", cookies=ck)
    coll = [c for c in r.json()["collections"] if c["id"] == cid][0]
    assert coll["name"] == "Renamed" and coll["paper_count"] == 1

    # empty name rejected
    assert client.post("/api/collections", json={"name": "  "}, cookies=ck).status_code == 400
    # adding a paper you don't own → 404
    assert client.post(f"/api/collections/{cid}/papers/999999", cookies=ck).status_code == 404

    assert client.delete(f"/api/collections/{cid}", cookies=ck).status_code == 200
    r = client.get("/api/collections", cookies=ck)
    assert all(c["id"] != cid for c in r.json()["collections"])


def test_sync_review_selected_mirrors_included(client, test_user):
    from main import get_db
    import backend.collections as collmod
    uid = _uid(test_user["email"])
    pi = _mk_paper(uid, "inc.pdf", "hi")
    pe = _mk_paper(uid, "exc.pdf", "he")

    conn = get_db()
    rid = conn.execute(
        "INSERT INTO synthesis_reviews (user_id,title,paper_ids_json,paper_count,status) "
        "VALUES (?,?,?,?, 'complete') RETURNING id",
        (uid, "RA", json.dumps([pi, pe]), 2),
    ).lastrowid
    conn.execute(
        "INSERT INTO synthesis_studies (review_id,paper_id,status,screening_decision) "
        "VALUES (?,?, 'included','include')", (rid, pi))
    conn.execute(
        "INSERT INTO synthesis_studies (review_id,paper_id,status,screening_decision) "
        "VALUES (?,?, 'excluded','exclude')", (rid, pe))
    conn.commit()

    cid = collmod.sync_review_selected(conn, rid)
    assert cid is not None
    # only the included paper is in the auto folder
    assert collmod.collection_paper_ids(conn, cid) == [pi]
    coll = collmod.get_collection(conn, cid, uid)
    assert coll["kind"] == "selected" and coll["review_id"] == rid

    # a manually-pinned paper survives a re-sync
    collmod.add_paper(conn, cid, pe, source="manual")
    conn.commit()
    collmod.sync_review_selected(conn, rid)
    assert sorted(collmod.collection_paper_ids(conn, cid)) == sorted([pi, pe])
    conn.close()
