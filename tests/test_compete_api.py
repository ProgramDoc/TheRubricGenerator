"""Unit tests for the Competition API and model registration flow.

Tests the full lifecycle: register model → opt-in → admin approve →
fetch questions → submit answers → get results. Also tests auth,
validation, and error cases.
"""


# ─── Model Registration ───

def test_register_model(client, test_user):
    """Register a new model and verify API key is returned."""
    r = client.post("/api/models", json={
        "name": "TestModel-v1",
        "version": "1.0",
        "provider": "custom",
    }, cookies={"rubricgen_session": test_user["cookie"]})
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "TestModel-v1"
    assert data["model_api_key"].startswith("rg_model_")
    return data


def test_register_model_duplicate_name(client, test_user):
    """Registering a model with a duplicate name returns 409."""
    client.post("/api/models", json={
        "name": "DuplicateModel",
        "version": "1.0",
    }, cookies={"rubricgen_session": test_user["cookie"]})

    r = client.post("/api/models", json={
        "name": "DuplicateModel",
        "version": "2.0",
    }, cookies={"rubricgen_session": test_user["cookie"]})
    assert r.status_code == 409


# ─── Daily Opt-In / Approval ───

def test_opt_in_daily(client, test_user):
    """Opt a model into daily challenges."""
    # Register model first
    reg = client.post("/api/models", json={
        "name": "OptInModel",
        "version": "1.0",
    }, cookies={"rubricgen_session": test_user["cookie"]})
    model_id = reg.json()["id"]

    r = client.post(f"/api/models/{model_id}/opt-in-daily",
                    cookies={"rubricgen_session": test_user["cookie"]})
    assert r.status_code == 200


def test_compete_list_unapproved(client, test_user, test_challenge):
    """Unapproved model gets 403 when accessing compete endpoints."""
    reg = client.post("/api/models", json={
        "name": "UnapprovedModel",
        "version": "1.0",
    }, cookies={"rubricgen_session": test_user["cookie"]})
    api_key = reg.json()["model_api_key"]

    # Opt in but don't approve
    client.post(f"/api/models/{reg.json()['id']}/opt-in-daily",
                cookies={"rubricgen_session": test_user["cookie"]})

    r = client.get("/api/compete/challenges",
                   headers={"X-Model-Key": api_key})
    assert r.status_code == 403


def test_admin_approve_model(client, test_user, admin_user):
    """Admin approves a model for daily challenges."""
    reg = client.post("/api/models", json={
        "name": "ApproveMe",
        "version": "1.0",
    }, cookies={"rubricgen_session": test_user["cookie"]})
    model_id = reg.json()["id"]

    client.post(f"/api/models/{model_id}/opt-in-daily",
                cookies={"rubricgen_session": test_user["cookie"]})

    r = client.post(f"/api/admin/models/{model_id}/approve-daily",
                    cookies={"rubricgen_session": admin_user["cookie"]})
    assert r.status_code == 200


# ─── Competition API Flow ───

def _register_and_approve(client, test_user, admin_user, name="CompeteModel"):
    """Helper: register, opt-in, and approve a model. Returns API key."""
    reg = client.post("/api/models", json={
        "name": name,
        "version": "1.0",
    }, cookies={"rubricgen_session": test_user["cookie"]})
    model_id = reg.json()["id"]
    api_key = reg.json()["model_api_key"]

    client.post(f"/api/models/{model_id}/opt-in-daily",
                cookies={"rubricgen_session": test_user["cookie"]})
    client.post(f"/api/admin/models/{model_id}/approve-daily",
                cookies={"rubricgen_session": admin_user["cookie"]})

    return api_key, model_id


def test_compete_list_approved(client, test_user, admin_user, test_challenge):
    """Approved model can list challenges."""
    api_key, _ = _register_and_approve(client, test_user, admin_user, "ListModel")
    r = client.get("/api/compete/challenges",
                   headers={"X-Model-Key": api_key})
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 1


def test_compete_fetch_questions(client, test_user, admin_user, test_challenge):
    """Fetch questions for a challenge — ideal answers must be stripped."""
    api_key, _ = _register_and_approve(client, test_user, admin_user, "FetchQModel")
    cid = test_challenge["challenge_id"]

    r = client.get(f"/api/compete/{cid}/questions",
                   headers={"X-Model-Key": api_key})
    assert r.status_code == 200
    data = r.json()
    assert "questions" in data
    assert len(data["questions"]) == 2


def test_compete_questions_stripped(client, test_user, admin_user, test_challenge):
    """Verify ideal_answer and scoring_criteria are NOT in compete response."""
    api_key, _ = _register_and_approve(client, test_user, admin_user, "StrippedModel")
    cid = test_challenge["challenge_id"]

    r = client.get(f"/api/compete/{cid}/questions",
                   headers={"X-Model-Key": api_key})
    data = r.json()
    for q in data["questions"]:
        assert "ideal_answer" not in q
        assert "scoring_criteria" not in q
        assert "question" in q
        assert "max_points" in q


def test_compete_submit_answers(client, test_user, admin_user, test_challenge):
    """Submit answers to a challenge."""
    api_key, _ = _register_and_approve(client, test_user, admin_user, "SubmitModel")
    cid = test_challenge["challenge_id"]

    # Must fetch questions first to create submission slot
    client.get(f"/api/compete/{cid}/questions",
               headers={"X-Model-Key": api_key})

    r = client.post(f"/api/compete/{cid}/submit",
                    headers={"X-Model-Key": api_key},
                    json={"responses": [
                        {"question_id": "q1", "answer": "It was an RCT."},
                        {"question_id": "q2", "answer": "Survival improved 15%."},
                    ]})
    assert r.status_code == 200
    assert r.json()["status"] == "submitted"


def test_compete_submit_without_slot(client, test_user, admin_user, test_challenge):
    """Submit without fetching questions first → 404."""
    api_key, _ = _register_and_approve(client, test_user, admin_user, "NoSlotModel")
    cid = test_challenge["challenge_id"]

    r = client.post(f"/api/compete/{cid}/submit",
                    headers={"X-Model-Key": api_key},
                    json={"responses": [{"question_id": "q1", "answer": "test"}]})
    assert r.status_code == 404


def test_compete_submit_empty(client, test_user, admin_user, test_challenge):
    """Submit with empty responses → 400."""
    api_key, _ = _register_and_approve(client, test_user, admin_user, "EmptyModel")
    cid = test_challenge["challenge_id"]

    client.get(f"/api/compete/{cid}/questions",
               headers={"X-Model-Key": api_key})

    r = client.post(f"/api/compete/{cid}/submit",
                    headers={"X-Model-Key": api_key},
                    json={"responses": []})
    assert r.status_code == 400


def test_compete_get_results(client, test_user, admin_user, test_challenge):
    """Get results after submission."""
    api_key, _ = _register_and_approve(client, test_user, admin_user, "ResultsModel")
    cid = test_challenge["challenge_id"]

    client.get(f"/api/compete/{cid}/questions",
               headers={"X-Model-Key": api_key})
    client.post(f"/api/compete/{cid}/submit",
                headers={"X-Model-Key": api_key},
                json={"responses": [
                    {"question_id": "q1", "answer": "RCT"},
                    {"question_id": "q2", "answer": "15%"},
                ]})

    r = client.get(f"/api/compete/{cid}/results",
                   headers={"X-Model-Key": api_key})
    assert r.status_code == 200
    assert r.json()["status"] == "submitted"


# ─── Authentication Tests ───

def test_compete_invalid_key(client, test_challenge):
    """Invalid API key returns 401."""
    r = client.get("/api/compete/challenges",
                   headers={"X-Model-Key": "rg_model_invalid_key"})
    assert r.status_code == 401


def test_compete_missing_key(client, test_challenge):
    """Missing API key returns 401."""
    r = client.get("/api/compete/challenges")
    assert r.status_code == 401


def test_regenerate_key(client, test_user, admin_user, test_challenge):
    """After regenerating key, old key stops working."""
    api_key, model_id = _register_and_approve(client, test_user, admin_user, "RegenModel")

    # Old key works
    r = client.get("/api/compete/challenges",
                   headers={"X-Model-Key": api_key})
    assert r.status_code == 200

    # Regenerate
    r = client.post(f"/api/models/{model_id}/regenerate-key",
                    cookies={"rubricgen_session": test_user["cookie"]})
    assert r.status_code == 200
    new_key = r.json()["model_api_key"]
    assert new_key != api_key

    # Old key fails
    r = client.get("/api/compete/challenges",
                   headers={"X-Model-Key": api_key})
    assert r.status_code == 401

    # New key works
    r = client.get("/api/compete/challenges",
                   headers={"X-Model-Key": new_key})
    assert r.status_code == 200


# ─── User API Key Auth ───

def test_user_api_key_auth(client, test_user):
    """User can generate a personal API key and use it for auth."""
    # Generate key
    r = client.post("/api/developers/generate-key",
                    cookies={"rubricgen_session": test_user["cookie"]})
    assert r.status_code == 200
    api_key = r.json()["api_key"]
    assert api_key.startswith("rg_user_")

    # Use key to access challenges endpoint
    r = client.get("/api/challenges", headers={"X-API-Key": api_key})
    assert r.status_code == 200

    # Revoke key
    r = client.delete("/api/developers/revoke-key",
                      cookies={"rubricgen_session": test_user["cookie"]})
    assert r.status_code == 200

    # Revoked key fails
    r = client.get("/api/challenges", headers={"X-API-Key": api_key})
    assert r.status_code == 401
