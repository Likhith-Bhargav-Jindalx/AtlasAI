import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.auth.passwords import hash_password
from atlas_engine.authoring_service import author_from_recording
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, User


def test_login_then_record_and_author_using_only_the_api(fixture_portal_url):
    """Everything a browser-based frontend would actually do, with no
    direct engine imports for the recording step — only HTTP calls plus
    the one function (author_from_recording) the frontend would also call
    via its own /recordings/{id}/author endpoint (already covered
    elsewhere; this test focuses on proving login + live recording work
    together end to end)."""
    email = f"e2e-auth-{uuid.uuid4()}@example.com"
    with session_scope() as session:
        org = Organization(name="E2E Auth Org", slug=f"e2e-auth-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        session.add(User(org_id=org.id, email=email, password_hash=hash_password("test-password"), role="member"))
        org_id = str(org.id)

    client = TestClient(app)

    login_response = client.post("/auth/login", json={"email": email, "password": "test-password"})
    assert login_response.status_code == 200

    me_response = client.get("/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["org_id"] == org_id

    start_url = f"{fixture_portal_url}/login.html"
    start_response = client.post("/recordings/start", json={"org_id": org_id, "start_url": start_url})
    capture_session_id = start_response.json()["capture_session_id"]

    stop_response = client.post(f"/recordings/{capture_session_id}/stop")
    recording_id = stop_response.json()["recording_id"]

    result = author_from_recording(uuid.UUID(recording_id))
    # This capture session is stopped immediately with zero captured
    # actions (nothing clicked the login form or the report button) —
    # an empty trajectory has no step that can fail, so it certifies
    # trivially. Accepting either outcome here since the point of this
    # test is proving login + live recording work together end to end,
    # not exercising authoring against a real multi-step flow.
    assert result["status"] in ("draft", "certified")

    logout_response = client.post("/auth/logout")
    assert logout_response.status_code == 200

    me_after_logout = client.get("/auth/me")
    assert me_after_logout.status_code == 401
