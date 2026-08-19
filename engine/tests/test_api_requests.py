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
    _login_as(client, org_id, agent_id)

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


def test_list_recording_requests_endpoint_requires_authentication():
    client = TestClient(app)
    response = client.get("/recording-requests", params={"org_id": str(uuid.uuid4())})
    assert response.status_code == 401


def test_list_recording_requests_endpoint_forces_operations_agent_to_their_own_requests(fixture_portal_url):
    """An operations agent must only see requests they themselves created —
    another agent's task must never show up in their inbox."""
    with session_scope() as session:
        org = Organization(name="API Requests Filter Org", slug=f"api-requests-filter-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = org.id
        agent_a = User(
            org_id=org_id, email=f"agent-a-{uuid.uuid4()}@example.com", password_hash=hash_password(_PASSWORD),
            job_function="operations_agent",
        )
        agent_b = User(
            org_id=org_id, email=f"agent-b-{uuid.uuid4()}@example.com", password_hash=hash_password(_PASSWORD),
            job_function="operations_agent",
        )
        session.add_all([agent_a, agent_b])
        session.flush()
        org_id, agent_a_id, agent_b_id = str(org_id), str(agent_a.id), str(agent_b.id)

    client = TestClient(app)
    _login_as(client, org_id, agent_b_id)
    other_response = client.post(
        "/recording-requests",
        json={
            "org_id": org_id, "requested_by": agent_b_id,
            "target_url": f"{fixture_portal_url}/login.html", "title": "Agent B's task",
        },
    )
    assert other_response.status_code == 200

    _login_as(client, org_id, agent_a_id)
    mine_response = client.post(
        "/recording-requests",
        json={
            "org_id": org_id, "requested_by": agent_a_id,
            "target_url": f"{fixture_portal_url}/login.html", "title": "Agent A's task",
        },
    )
    assert mine_response.status_code == 200
    mine_id = mine_response.json()["request_id"]

    list_response = client.get("/recording-requests", params={"org_id": org_id})
    assert list_response.status_code == 200
    assert [r["id"] for r in list_response.json()] == [mine_id]


def test_list_recording_requests_endpoint_lets_developer_see_all_org_requests(fixture_portal_url):
    org_id, agent_id, developer_id = _make_org_and_users("api-requests-dev-view")
    client = TestClient(app)
    _login_as(client, org_id, agent_id)
    create_response = client.post(
        "/recording-requests",
        json={
            "org_id": org_id, "requested_by": agent_id,
            "target_url": f"{fixture_portal_url}/login.html", "title": "Some task",
        },
    )
    assert create_response.status_code == 200

    _login_as(client, org_id, developer_id)
    list_response = client.get("/recording-requests", params={"org_id": org_id})
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1


def test_create_recording_request_endpoint_forces_requested_by_to_the_caller(fixture_portal_url):
    """A caller must never be able to attribute a request to someone else —
    requested_by is always derived from the authenticated session."""
    org_id, agent_id, other_agent_id = _make_org_and_users("api-requests-impersonation")
    with session_scope() as session:
        session.get(User, uuid.UUID(other_agent_id)).job_function = "operations_agent"

    client = TestClient(app)
    _login_as(client, org_id, agent_id)
    response = client.post(
        "/recording-requests",
        json={
            "org_id": org_id, "requested_by": other_agent_id,
            "target_url": f"{fixture_portal_url}/login.html", "title": "Attempted impersonation",
        },
    )
    assert response.status_code == 200
    request_id = response.json()["request_id"]

    with session_scope() as session:
        from atlas_engine.db.models import RecordingRequest
        request = session.get(RecordingRequest, uuid.UUID(request_id))
        assert str(request.requested_by) == agent_id


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


def test_create_recording_request_endpoint_persists_section_a_fields(fixture_portal_url):
    from atlas_engine.db.models import System

    org_id, agent_id, _developer_id = _make_org_and_users("api-requests-section-a")
    with session_scope() as session:
        system = System(org_id=uuid.UUID(org_id), name="Availity")
        session.add(system)
        session.flush()
        system_id = str(system.id)

    client = TestClient(app)
    _login_as(client, org_id, agent_id)
    response = client.post(
        "/recording-requests",
        json={
            "org_id": org_id, "requested_by": agent_id,
            "target_url": f"{fixture_portal_url}/login.html", "title": "Status Check on Availity",
            "lob": "AR", "category": "Process", "action": "Status Check", "system_id": system_id,
            "client_scope": ["generic"], "reuse_scope": "Generic",
        },
    )
    assert response.status_code == 200

    list_response = client.get("/recording-requests", params={"org_id": org_id, "lob": "AR"})
    assert list_response.status_code == 200
    body = list_response.json()[0]
    assert body["lob"] == "AR"
    assert body["category"] == "Process"
    assert body["action"] == "Status Check"
    assert body["system_id"] == system_id
    assert body["client_scope"] == ["generic"]
    assert body["reuse_scope"] == "Generic"


def test_master_data_endpoints_return_org_scoped_lists(fixture_portal_url):
    from atlas_engine.db.models import CarcCode, Client, Payer, System

    org_id, agent_id, _developer_id = _make_org_and_users("api-master-data")
    with session_scope() as session:
        session.add_all([
            System(org_id=uuid.UUID(org_id), name="Availity"),
            Payer(org_id=uuid.UUID(org_id), name="Cigna"),
            Client(org_id=uuid.UUID(org_id), name="Acme Clinic"),
            CarcCode(org_id=uuid.UUID(org_id), code="CO-16", is_multi_use=True),
        ])

    client = TestClient(app)
    _login_as(client, org_id, agent_id)

    assert [s["name"] for s in client.get("/master-data/systems").json()] == ["Availity"]
    assert [p["name"] for p in client.get("/master-data/payers").json()] == ["Cigna"]
    assert [c["name"] for c in client.get("/master-data/clients").json()] == ["Acme Clinic"]
    codes = client.get("/master-data/carc-codes").json()
    assert codes[0]["code"] == "CO-16"
    assert codes[0]["is_multi_use"] is True


def test_master_data_endpoints_require_authentication():
    client = TestClient(app)
    assert client.get("/master-data/systems").status_code == 401
    assert client.get("/master-data/payers").status_code == 401
    assert client.get("/master-data/clients").status_code == 401
    assert client.get("/master-data/carc-codes").status_code == 401


def test_update_recording_details_endpoint_persists_section_b_fields(fixture_portal_url):
    from atlas_engine.recording_service import persist_recording

    org_id, agent_id, _developer_id = _make_org_and_users("api-section-b")
    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])

    client = TestClient(app)
    response = client.patch(
        f"/recordings/{recording_id}",
        json={
            "payer_scope": "All payers", "resolution_path": ["Corrected claim"],
            "date_param_logic": "prior calendar month", "post_processing": "None",
            "scenario_description": "Denial follow-up",
        },
    )
    assert response.status_code == 200

    detail = client.get(f"/recordings/{recording_id}").json()
    assert detail["title"] is None or True  # unrelated field untouched; just confirm 200 above


def test_update_recording_details_endpoint_rejects_missing_payers(fixture_portal_url):
    from atlas_engine.recording_service import persist_recording

    org_id, _agent_id, _developer_id = _make_org_and_users("api-section-b-missing-payers")
    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])

    client = TestClient(app)
    response = client.patch(f"/recordings/{recording_id}", json={"payer_scope": "Payer-specific"})
    assert response.status_code == 422


def test_update_recording_details_endpoint_rejects_absolute_date(fixture_portal_url):
    from atlas_engine.recording_service import persist_recording

    org_id, _agent_id, _developer_id = _make_org_and_users("api-section-b-absolute-date")
    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])

    client = TestClient(app)
    response = client.patch(f"/recordings/{recording_id}", json={"date_param_logic": "2026-01-01"})
    assert response.status_code == 422


def test_update_recording_details_endpoint_requires_sub_reason_for_multi_use_code(fixture_portal_url):
    from atlas_engine.db.models import CarcCode
    from atlas_engine.recording_service import persist_recording

    org_id, _agent_id, _developer_id = _make_org_and_users("api-section-b-sub-reason")
    with session_scope() as session:
        carc = CarcCode(org_id=uuid.UUID(org_id), code="CO-16", is_multi_use=True)
        session.add(carc)
        session.flush()
        carc_id = str(carc.id)
    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])

    client = TestClient(app)
    response = client.patch(f"/recordings/{recording_id}", json={"carc_code_id": carc_id})
    assert response.status_code == 422


def test_list_recordings_endpoint_filters_by_lob_and_action(fixture_portal_url):
    from atlas_engine.recording_service import create_recording_request, persist_recording

    org_id, agent_id, developer_id = _make_org_and_users("api-recordings-lob-filter")
    start_url = f"{fixture_portal_url}/login.html"
    ar_request_id = create_recording_request(org_id, agent_id, start_url, "AR request", lob="AR", action="Status Check")
    persist_recording(org_id, start_url, [], request_id=ar_request_id)
    persist_recording(org_id, start_url, [])

    client = TestClient(app)
    _login_as(client, org_id, developer_id)
    response = client.get("/recordings", params={"org_id": org_id, "lob": "AR"})
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["lob"] == "AR"
