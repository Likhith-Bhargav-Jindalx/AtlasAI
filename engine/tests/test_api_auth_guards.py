import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.auth.passwords import hash_password
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, User


def _make_user(*, job_function: str | None, role: str = "member") -> tuple[str, str, TestClient]:
    with session_scope() as session:
        org = Organization(name="Auth Guard Org", slug=f"auth-guard-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        user = User(
            org_id=org.id, email=f"{uuid.uuid4()}@example.com", password_hash=hash_password("pw"),
            role=role, job_function=job_function,
        )
        session.add(user)
        session.flush()
        email = user.email

    client = TestClient(app)
    login = client.post("/auth/login", json={"email": email, "password": "pw"})
    assert login.status_code == 200
    body = login.json()
    return body["org_id"], body["user_id"], client


def test_claim_request_requires_developer_job_function():
    _, _, agent_client = _make_user(job_function="operations_agent")
    response = agent_client.post(f"/recording-requests/{uuid.uuid4()}/claim", json={"developer_id": str(uuid.uuid4())})
    assert response.status_code == 403


def test_claim_request_allows_developer_job_function():
    org_id, user_id, dev_client = _make_user(job_function="developer")
    from atlas_engine.recording_service import create_recording_request

    request_id = create_recording_request(org_id, user_id, "https://portal.example.com/x", "Title")
    response = dev_client.post(f"/recording-requests/{request_id}/claim", json={"developer_id": user_id})
    assert response.status_code == 200


def test_claim_request_allows_admin_regardless_of_job_function():
    org_id, user_id, admin_client = _make_user(job_function="operations_agent", role="admin")
    from atlas_engine.recording_service import create_recording_request

    request_id = create_recording_request(org_id, user_id, "https://portal.example.com/x", "Title")
    response = admin_client.post(f"/recording-requests/{request_id}/claim", json={"developer_id": user_id})
    assert response.status_code == 200


def test_claim_request_401_without_a_session():
    client = TestClient(app)
    response = client.post(f"/recording-requests/{uuid.uuid4()}/claim", json={"developer_id": str(uuid.uuid4())})
    assert response.status_code == 401


def test_approve_heal_event_requires_developer_job_function():
    _, _, agent_client = _make_user(job_function="operations_agent")
    response = agent_client.post(f"/heal-events/{uuid.uuid4()}/approve")
    assert response.status_code == 403


def test_submit_recording_requires_operations_agent_job_function():
    _, _, dev_client = _make_user(job_function="developer")
    response = dev_client.post(f"/recordings/{uuid.uuid4()}/submit", json={"submitted_by": str(uuid.uuid4())})
    assert response.status_code == 403
