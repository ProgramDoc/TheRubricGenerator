"""Tests for the 3-judge adjudication pipeline.

Covers the pure-Python parts that don't require LLM calls:
- Majority-of-3 voting logic in ``backend.agents.adjudicator``.
- 3-way split detection and needs_review payload shape.
- ``needs_adjudication`` early-exit when primary == shadow.
- The ``grade_reviews`` persistence layer in ``backend.review``.
- The ``/api/reviews/*`` admin-only endpoints — auth + happy path.

These tests deliberately avoid touching the actual judge/gemini callers;
``run_third_judge`` is exercised indirectly via ``adjudicate_grades``.
"""

from __future__ import annotations

import json

import pytest

from backend.agents import adjudicator as adj
from backend import review as review_mod


# ─────────────────────────────────────────────────────────────────────
# _majority_score — the heart of the adjudicator
# ─────────────────────────────────────────────────────────────────────

class TestMajorityScore:
    """Resolution table for the 4 status paths."""

    def test_agree_no_third_needed(self):
        # primary == shadow → "agree", third is ignored
        final, status = adj._majority_score(1.0, 1.0, None)
        assert status == "agree"
        assert final == 1.0

        # Still "agree" even if third would disagree
        final, status = adj._majority_score(0.0, 0.0, 1.0)
        assert status == "agree"
        assert final == 0.0

    def test_majority_primary_and_third_agree(self):
        final, status = adj._majority_score(1.0, 0.0, 1.0)
        assert status == "majority"
        assert final == 1.0

    def test_majority_shadow_and_third_agree(self):
        final, status = adj._majority_score(1.0, 0.0, 0.0)
        assert status == "majority"
        assert final == 0.0

    def test_three_way_split_returns_none(self):
        # 0 / 0.5 / 1 → no two agree → split → needs human review
        final, status = adj._majority_score(0.0, 0.5, 1.0)
        assert status == "split"
        assert final is None

    def test_no_third_falls_back_to_primary(self):
        # Gemini unavailable — primary wins by default, same as pre-adjudicator behavior
        final, status = adj._majority_score(1.0, 0.0, None)
        assert status == "no_third"
        assert final == 1.0

    def test_fractional_scores_use_epsilon(self):
        # 0.5 partial credit matching across judges should still count as agree
        final, status = adj._majority_score(0.5, 0.5, None)
        assert status == "agree"
        assert final == 0.5

        # And majority of 3 with fractional
        final, status = adj._majority_score(0.5, 1.0, 0.5)
        assert status == "majority"
        assert final == 0.5

    def test_none_scores_treated_as_zero(self):
        # Defensive: missing scores shouldn't crash
        final, status = adj._majority_score(None, None, None)
        assert status == "agree"
        assert final is None  # primary=None passthrough


# ─────────────────────────────────────────────────────────────────────
# adjudicate_grades — end-to-end grade merge
# ─────────────────────────────────────────────────────────────────────

def _g(qid, score, reasoning="", max_points=1):
    return {"question_id": qid, "score": score, "reasoning": reasoning,
            "max_points": max_points, "question": f"{qid}?",
            "ideal_answer": f"ideal-{qid}", "scoring_criteria": "crit"}


class TestAdjudicateGrades:

    def test_all_agree_no_review_flagged(self):
        primary = {"grades": [_g("q1", 1.0), _g("q2", 0.5, max_points=1)]}
        shadow  = {"grades": [_g("q1", 1.0), _g("q2", 0.5, max_points=1)]}
        final, flagged = adj.adjudicate_grades(primary, shadow, None)
        assert flagged == []
        assert final["adjudication_summary"]["agree"] == 2
        assert final["adjudication_summary"]["split"] == 0
        assert final["grades"][0]["adjudication"]["status"] == "agree"

    def test_majority_wins_primary_third(self):
        primary = {"grades": [_g("q1", 1.0, "primary says 1")]}
        shadow  = {"grades": [_g("q1", 0.0, "shadow says 0")]}
        third   = {"grades": [_g("q1", 1.0, "third says 1")]}
        final, flagged = adj.adjudicate_grades(primary, shadow, third)
        assert flagged == []
        assert final["grades"][0]["score"] == 1.0
        assert final["grades"][0]["adjudication"]["status"] == "majority"
        assert final["adjudication_summary"]["majority"] == 1

    def test_majority_wins_shadow_third(self):
        # Shadow overrides primary when third agrees with shadow
        primary = {"grades": [_g("q1", 1.0)]}
        shadow  = {"grades": [_g("q1", 0.0)]}
        third   = {"grades": [_g("q1", 0.0)]}
        final, flagged = adj.adjudicate_grades(primary, shadow, third)
        assert flagged == []
        assert final["grades"][0]["score"] == 0.0
        assert final["grades"][0]["adjudication"]["status"] == "majority"

    def test_three_way_split_is_flagged(self):
        primary = {"grades": [_g("q1", 0.0, "p", max_points=2)]}
        shadow  = {"grades": [_g("q1", 1.0, "s", max_points=2)]}
        third   = {"grades": [_g("q1", 2.0, "t", max_points=2)]}
        final, flagged = adj.adjudicate_grades(primary, shadow, third)
        assert len(flagged) == 1
        entry = flagged[0]
        # needs_review payload matches what flag_for_review expects
        assert entry["question_id"] == "q1"
        assert entry["primary"] == 0.0
        assert entry["shadow"] == 1.0
        assert entry["third"] == 2.0
        assert entry["max_points"] == 2
        assert entry["primary_reasoning"] == "p"
        assert entry["shadow_reasoning"] == "s"
        assert entry["third_reasoning"] == "t"
        # Provisional: primary score kept so the leaderboard has something
        # to render until the human resolves.
        assert final["grades"][0]["score"] == 0.0
        assert final["grades"][0]["adjudication"]["status"] == "split"
        assert final["grades"][0]["adjudication"]["provisional"] is True

    def test_no_third_grade_uses_primary(self):
        # GEMINI_API_KEY unset → third_grades=None → primary wins on any diff
        primary = {"grades": [_g("q1", 1.0)]}
        shadow  = {"grades": [_g("q1", 0.0)]}
        final, flagged = adj.adjudicate_grades(primary, shadow, None)
        assert flagged == []
        assert final["grades"][0]["score"] == 1.0
        assert final["grades"][0]["adjudication"]["status"] == "no_third"

    def test_mixed_batch_multiple_paths(self):
        primary = {"grades": [_g("q1", 1), _g("q2", 1), _g("q3", 0), _g("q4", 0)]}
        shadow  = {"grades": [_g("q1", 1), _g("q2", 0), _g("q3", 1), _g("q4", 0)]}
        third   = {"grades": [_g("q1", 1), _g("q2", 1), _g("q3", 0.5), _g("q4", 0)]}
        final, flagged = adj.adjudicate_grades(primary, shadow, third)
        summary = final["adjudication_summary"]
        assert summary["total"] == 4
        assert summary["agree"]    == 2   # q1, q4
        assert summary["majority"] == 1   # q2 (primary+third)
        assert summary["split"]    == 1   # q3 (0/1/0.5)
        assert len(flagged) == 1
        assert flagged[0]["question_id"] == "q3"

    def test_preserves_primary_metadata(self):
        # Ideal-answer, scoring_criteria, reasoning should survive the merge
        primary = {"grades": [_g("q1", 1.0, "rationale")]}
        shadow  = {"grades": [_g("q1", 1.0)]}
        final, _ = adj.adjudicate_grades(primary, shadow, None)
        row = final["grades"][0]
        assert row["reasoning"] == "rationale"
        assert row["ideal_answer"] == "ideal-q1"
        assert row["scoring_criteria"] == "crit"


class TestNeedsAdjudication:

    def test_all_equal_returns_false(self):
        primary = {"grades": [_g("q1", 1), _g("q2", 0.5)]}
        shadow  = {"grades": [_g("q1", 1), _g("q2", 0.5)]}
        assert adj.needs_adjudication(primary, shadow) is False

    def test_any_diff_returns_true(self):
        primary = {"grades": [_g("q1", 1), _g("q2", 1)]}
        shadow  = {"grades": [_g("q1", 1), _g("q2", 0)]}
        assert adj.needs_adjudication(primary, shadow) is True

    def test_epsilon_tolerates_floats(self):
        primary = {"grades": [_g("q1", 0.5000001)]}
        shadow  = {"grades": [_g("q1", 0.5)]}
        assert adj.needs_adjudication(primary, shadow) is False


# ─────────────────────────────────────────────────────────────────────
# Persistence: grade_reviews CRUD
# ─────────────────────────────────────────────────────────────────────

def _extract_id(cur, conn):
    """Pull the id out of a RETURNING-id cursor, with a last-insert-rowid fallback."""
    row = cur.fetchone()
    if row is None:
        return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    if isinstance(row, (tuple, list)):
        return int(row[0])
    # sqlite3.Row supports index + key
    try:
        return int(row["id"])
    except Exception:
        return int(row[0])


def _seed_participant(conn, *, grade_json: dict, speed_bonus: float = 1.0):
    """Insert a minimal challenge + model_participants row we can mutate.

    Returns ``(participant_id, challenge_id)``.
    """
    cur = conn.execute(
        """INSERT INTO challenges (title, theme, kind, status, created_by, visibility)
           VALUES (?,?,?,?,?,?)
           RETURNING id""",
        ("adj test", "theme", "daily", "complete", 1, "private"),
    )
    cid = _extract_id(cur, conn)

    cur = conn.execute(
        """INSERT INTO model_participants
                (challenge_id, model_id, provider, status, accuracy,
                 speed_bonus, total_score, answer_json, grade_json)
           VALUES (?,?,?,?,?,?,?,?,?)
           RETURNING id""",
        (cid, "test-model", "anthropic", "graded", 0.5, speed_bonus, 0.5,
         json.dumps({"responses": []}), json.dumps(grade_json)),
    )
    pid = _extract_id(cur, conn)
    conn.commit()
    return pid, cid


@pytest.fixture
def db_conn():
    from main import get_db
    conn = get_db()
    yield conn
    conn.close()


class TestFlagForReview:

    def test_flag_empty_list_is_noop(self, db_conn):
        assert review_mod.flag_for_review(
            db_conn,
            challenge_id=1, participant_id=1,
            flagged=[], model_answer_map={},
        ) == []

    def test_flag_persists_all_fields(self, db_conn):
        pid, cid = _seed_participant(
            db_conn,
            grade_json={"grades": [{"question_id": "q1", "score": 0, "max_points": 1}]},
        )
        flagged = [{
            "question_id": "q1",
            "primary": 0, "shadow": 1, "third": 0.5,
            "max_points": 1,
            "question": "What design?",
            "ideal_answer": "RCT",
            "scoring_criteria": "Must say RCT",
            "primary_reasoning": "says cohort",
            "shadow_reasoning": "says RCT",
            "third_reasoning":  "partial",
        }]
        ids = review_mod.flag_for_review(
            db_conn,
            challenge_id=cid, participant_id=pid,
            flagged=flagged,
            model_answer_map={"q1": "The study was a cohort."},
        )
        assert len(ids) == 1

        pending = review_mod.list_pending_reviews(db_conn)
        assert len(pending) == 1
        row = pending[0]
        assert row["status"] == "pending"
        assert row["question_id"] == "q1"
        assert row["primary_score"] == 0
        assert row["shadow_score"] == 1
        assert row["third_score"] == 0.5
        assert row["model_answer"] == "The study was a cohort."
        assert row["ideal_answer"] == "RCT"
        assert row["primary_reasoning"] == "says cohort"


class TestResolveReview:

    def test_resolve_updates_participant_grade_json(self, db_conn):
        grades = {
            "grades": [
                {"question_id": "q1", "score": 0.0, "max_points": 1.0,
                 "adjudication": {"status": "split", "primary_score": 0.0,
                                  "shadow_score": 1.0, "third_score": 0.5,
                                  "reviewed": False, "provisional": True}},
                {"question_id": "q2", "score": 1.0, "max_points": 1.0,
                 "adjudication": {"status": "agree"}},
            ]
        }
        pid, cid = _seed_participant(db_conn, grade_json=grades, speed_bonus=1.0)

        ids = review_mod.flag_for_review(
            db_conn,
            challenge_id=cid, participant_id=pid,
            flagged=[{
                "question_id": "q1",
                "primary": 0, "shadow": 1, "third": 0.5,
                "max_points": 1,
                "question": "q?", "ideal_answer": "a",
                "scoring_criteria": "crit",
                "primary_reasoning": "p", "shadow_reasoning": "s",
                "third_reasoning": "t",
            }],
            model_answer_map={"q1": "answer"},
        )
        rid = ids[0]

        # Before resolve: participant.accuracy still from seed (0.5)
        before = db_conn.execute(
            "SELECT accuracy, total_score FROM model_participants WHERE id=?",
            (pid,),
        ).fetchone()
        assert float(before["accuracy"]) == 0.5

        result = review_mod.resolve_review(
            db_conn,
            review_id=rid,
            final_score=1.0,
            reviewer_user_id=1,
            reviewer_note="Shadow + third were right; primary misread the abstract.",
        )
        assert result["status"] == "resolved"
        assert float(result["final_score"]) == 1.0

        # Participant grade_json now reflects the adjudicated score
        after = db_conn.execute(
            "SELECT accuracy, total_score, grade_json FROM model_participants WHERE id=?",
            (pid,),
        ).fetchone()
        # Final accuracy = (1 + 1) / (1 + 1) = 1.0
        assert float(after["accuracy"]) == 1.0
        # total_score = accuracy * speed_bonus = 1.0 * 1.0
        assert float(after["total_score"]) == 1.0

        new_grades = json.loads(after["grade_json"])
        q1 = next(g for g in new_grades["grades"] if g["question_id"] == "q1")
        assert q1["score"] == 1.0
        assert q1["adjudication"]["reviewed"] is True
        assert q1["adjudication"]["status"] == "human"
        assert q1["adjudication"]["final_score"] == 1.0
        assert "provisional" not in q1["adjudication"]

    def test_resolve_rejects_out_of_range_score(self, db_conn):
        pid, cid = _seed_participant(
            db_conn,
            grade_json={"grades": [{"question_id": "q1", "score": 0,
                                    "max_points": 2, "adjudication": {}}]},
        )
        ids = review_mod.flag_for_review(
            db_conn,
            challenge_id=cid, participant_id=pid,
            flagged=[{
                "question_id": "q1",
                "primary": 0, "shadow": 1, "third": 2,
                "max_points": 2, "question": "?", "ideal_answer": "", "scoring_criteria": "",
                "primary_reasoning": "", "shadow_reasoning": "", "third_reasoning": "",
            }],
            model_answer_map={"q1": ""},
        )
        with pytest.raises(ValueError, match="outside"):
            review_mod.resolve_review(
                db_conn, review_id=ids[0], final_score=5.0,
                reviewer_user_id=1, reviewer_note="",
            )
        with pytest.raises(ValueError, match="outside"):
            review_mod.resolve_review(
                db_conn, review_id=ids[0], final_score=-0.5,
                reviewer_user_id=1, reviewer_note="",
            )

    def test_resolve_rejects_already_resolved(self, db_conn):
        pid, cid = _seed_participant(
            db_conn,
            grade_json={"grades": [{"question_id": "q1", "score": 0,
                                    "max_points": 1, "adjudication": {}}]},
        )
        ids = review_mod.flag_for_review(
            db_conn,
            challenge_id=cid, participant_id=pid,
            flagged=[{
                "question_id": "q1",
                "primary": 0, "shadow": 1, "third": 0.5,
                "max_points": 1, "question": "?", "ideal_answer": "", "scoring_criteria": "",
                "primary_reasoning": "", "shadow_reasoning": "", "third_reasoning": "",
            }],
            model_answer_map={"q1": ""},
        )
        review_mod.resolve_review(
            db_conn, review_id=ids[0], final_score=0.5,
            reviewer_user_id=1, reviewer_note="",
        )
        with pytest.raises(ValueError, match="already resolved"):
            review_mod.resolve_review(
                db_conn, review_id=ids[0], final_score=1.0,
                reviewer_user_id=1, reviewer_note="",
            )

    def test_resolve_missing_review_raises(self, db_conn):
        with pytest.raises(ValueError, match="not found"):
            review_mod.resolve_review(
                db_conn, review_id=999999, final_score=1.0,
                reviewer_user_id=1, reviewer_note="",
            )


# ─────────────────────────────────────────────────────────────────────
# HTTP endpoints: /api/reviews/*
# ─────────────────────────────────────────────────────────────────────

class TestReviewAPI:

    def test_pending_requires_auth(self, client):
        r = client.get("/api/reviews/pending")
        assert r.status_code == 401

    def test_pending_non_admin_forbidden(self, client, test_user):
        r = client.get("/api/reviews/pending",
                       cookies={"rubricgen_session": test_user["cookie"]})
        assert r.status_code == 403

    def test_pending_empty_list(self, client, admin_user):
        r = client.get("/api/reviews/pending",
                       cookies={"rubricgen_session": admin_user["cookie"]})
        assert r.status_code == 200
        assert r.json() == []

    def test_review_page_redirects_for_non_admin(self, client, test_user):
        r = client.get("/review",
                       cookies={"rubricgen_session": test_user["cookie"]},
                       follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/"

    def test_review_page_renders_for_admin(self, client, admin_user):
        r = client.get("/review",
                       cookies={"rubricgen_session": admin_user["cookie"]})
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_resolve_happy_path(self, client, admin_user):
        # Seed a review directly via review_mod so we don't need to run a challenge
        from main import get_db
        conn = get_db()
        try:
            pid, cid = _seed_participant(
                conn,
                grade_json={"grades": [{"question_id": "q1", "score": 0,
                                        "max_points": 1, "adjudication": {}}]},
            )
            ids = review_mod.flag_for_review(
                conn,
                challenge_id=cid, participant_id=pid,
                flagged=[{
                    "question_id": "q1",
                    "primary": 0, "shadow": 1, "third": 0.5,
                    "max_points": 1, "question": "q?", "ideal_answer": "a",
                    "scoring_criteria": "c",
                    "primary_reasoning": "", "shadow_reasoning": "", "third_reasoning": "",
                }],
                model_answer_map={"q1": "answer"},
            )
            rid = ids[0]
        finally:
            conn.close()

        # Admin sees the row in /pending
        r = client.get("/api/reviews/pending",
                       cookies={"rubricgen_session": admin_user["cookie"]})
        assert r.status_code == 200
        rows = r.json()
        assert any(x["id"] == rid for x in rows)

        # Detail endpoint returns full row
        r = client.get(f"/api/reviews/{rid}",
                       cookies={"rubricgen_session": admin_user["cookie"]})
        assert r.status_code == 200
        assert r.json()["question_id"] == "q1"

        # Resolve it
        r = client.post(
            f"/api/reviews/{rid}/resolve",
            cookies={"rubricgen_session": admin_user["cookie"]},
            json={"final_score": 1.0, "reviewer_note": "shadow was right"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["review"]["status"] == "resolved"
        assert float(body["review"]["final_score"]) == 1.0

        # Now gone from /pending
        r = client.get("/api/reviews/pending",
                       cookies={"rubricgen_session": admin_user["cookie"]})
        assert all(x["id"] != rid for x in r.json())

    def test_resolve_rejects_bad_score(self, client, admin_user):
        from main import get_db
        conn = get_db()
        try:
            pid, cid = _seed_participant(
                conn,
                grade_json={"grades": [{"question_id": "q1", "score": 0,
                                        "max_points": 1, "adjudication": {}}]},
            )
            ids = review_mod.flag_for_review(
                conn,
                challenge_id=cid, participant_id=pid,
                flagged=[{
                    "question_id": "q1",
                    "primary": 0, "shadow": 1, "third": 0.5,
                    "max_points": 1, "question": "q?", "ideal_answer": "",
                    "scoring_criteria": "",
                    "primary_reasoning": "", "shadow_reasoning": "", "third_reasoning": "",
                }],
                model_answer_map={"q1": ""},
            )
            rid = ids[0]
        finally:
            conn.close()

        r = client.post(
            f"/api/reviews/{rid}/resolve",
            cookies={"rubricgen_session": admin_user["cookie"]},
            json={"final_score": 99.0, "reviewer_note": ""},
        )
        assert r.status_code == 400
        assert "outside" in r.json()["detail"].lower()

    def test_resolve_requires_admin(self, client, test_user):
        r = client.post(
            "/api/reviews/1/resolve",
            cookies={"rubricgen_session": test_user["cookie"]},
            json={"final_score": 1.0, "reviewer_note": ""},
        )
        assert r.status_code == 403
