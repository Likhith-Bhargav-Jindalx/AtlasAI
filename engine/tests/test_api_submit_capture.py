import json
import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.auth.passwords import hash_password
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, Recording, User


def _login_client(email: str, password: str) -> tuple[TestClient, str, str]:
    with session_scope() as session:
        org = Organization(name="Submit Capture Org", slug=f"submit-capture-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        user = User(org_id=org.id, email=email, password_hash=hash_password(password), role="member")
        session.add(user)
        session.flush()
        org_id, user_id = str(org.id), str(user.id)

    client = TestClient(app)
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return client, org_id, user_id


def _actions_json() -> str:
    return json.dumps([
        {
            "index": 0, "kind": "navigate", "selector_candidates": [], "value": None,
            "url_before": "https://example.com/", "url_after": "https://example.com/dashboard",
            "dom_before": "", "dom_after": "",
        },
    ])


def test_submit_capture_requires_authentication():
    client = TestClient(app)
    response = client.post(
        "/recordings/submit-capture",
        data={"start_url": "https://example.com", "actions": _actions_json()},
    )
    assert response.status_code == 401


def test_submit_capture_persists_a_recording_attributed_to_the_authenticated_session():
    """org_id and user_id aren't accepted as request fields at all — this
    proves the recording lands with the *session's* identity, which is
    the whole point (a remote client can't submit into an org it doesn't
    belong to, since there's nothing to spoof)."""
    email = f"submit-capture-{uuid.uuid4()}@example.com"
    client, org_id, user_id = _login_client(email, "test-password")

    response = client.post(
        "/recordings/submit-capture",
        data={"start_url": "https://example.com", "actions": _actions_json(), "role": "demo"},
        files={"video": ("video.webm", b"fake-webm-bytes", "video/webm")},
    )

    assert response.status_code == 200
    recording_id = response.json()["recording_id"]

    with session_scope() as session:
        recording = session.get(Recording, uuid.UUID(recording_id))
        assert recording.status == "captured"
        assert recording.role == "demo"
        assert str(recording.org_id) == org_id
        assert str(recording.user_id) == user_id


def test_submit_capture_stores_video_and_codegen_script():
    email = f"submit-capture-artifacts-{uuid.uuid4()}@example.com"
    client, _org_id, _user_id = _login_client(email, "test-password")

    response = client.post(
        "/recordings/submit-capture",
        data={"start_url": "https://example.com", "actions": _actions_json(), "codegen_script": "print('hi')"},
        files={"video": ("video.webm", b"fake-webm-bytes", "video/webm")},
    )
    recording_id = response.json()["recording_id"]

    detail = client.get(f"/recordings/{recording_id}").json()
    assert detail["codegen_script"] == "print('hi')"
    assert detail["pages"][0]["has_video"] is True
