import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.auth.passwords import hash_password
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, User

_PASSWORD = "test-password"


def _make_org_and_users(slug_prefix: str) -> tuple[str, str, str]:
    with session_scope() as session:
        org = Organization(name="API Requests Org", slug=f"{slug_prefix}-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        agent = User(
            org_id=org.id, email=f"agent-{uuid.uuid4()}@example.com", password_hash=hash_password(_PASSWORD),
            role="member", job_function="operations_agent",
        )
        developer = User(
            org_id=org.id, email=f"dev-{uuid.uuid4()}@example.com", password_hash=hash_password(_PASSWORD),
            role="member", job_function="developer",
        )
        session.add_all([agent, developer])
        session.flush()
        return str(org.id), str(agent.id), str(developer.id)


def _login_as(client: TestClient, org_id: str, user_id: str) -> None:
    with session_scope() as session:
        email = session.get(User, uuid.UUID(user_id)).email
    login_response = client.post("/auth/login", json={"email": email, "password": _PASSWORD})
    assert login_response.status_code == 200


def test_create_list_and_claim_recording_request_endpoints(fixture_portal_url):
    org_id, agent_id, developer_id = _make_org_and_users("api-requests")
    client = TestClient(app)

    create_response = client.post(
        "/recording-requests",
        json={
            "org_id": org_id, "requested_by": agent_id,
            "target_url": f"{fixture_portal_url}/login.html", "title": "Check eligibility",
        },
    )
    assert create_response.status_code == 200
    request_id = create_response.json()["request_id"]

    list_response = client.get("/recording-requests", params={"org_id": org_id, "status": "open"})
    assert list_response.status_code == 200
    assert any(r["id"] == request_id for r in list_response.json())

    _login_as(client, org_id, developer_id)
    claim_response = client.post(f"/recording-requests/{request_id}/claim", json={"developer_id": developer_id})
    assert claim_response.status_code == 200


def test_recording_author_endpoint(fixture_portal_url):
    from playwright.sync_api import sync_playwright

    from atlas_engine.capture.recorder import CaptureSession
    from atlas_engine.recording_service import persist_recording

    org_id, _agent_id, developer_id = _make_org_and_users("api-author")
    start_url = f"{fixture_portal_url}/login.html"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(start_url)
        with CaptureSession(page) as capture:
            page.fill("#username", "demo")
            page.fill("#password", "demo-pass")
            page.click("#login-btn")
            page.wait_for_url("**/dashboard.html")
            page.click("#generate-report-btn")
        browser.close()

    recording_id = persist_recording(org_id, start_url, capture.actions)

    client = TestClient(app)
    _login_as(client, org_id, developer_id)
    response = client.post(f"/recordings/{recording_id}/author")

    assert response.status_code == 200
    assert "authoring_run_id" in response.json()
    authoring_run_id = response.json()["authoring_run_id"]
    uuid.UUID(authoring_run_id)  # raises ValueError if not a real UUID

    client.post(f"/authoring-runs/{authoring_run_id}/stop")
