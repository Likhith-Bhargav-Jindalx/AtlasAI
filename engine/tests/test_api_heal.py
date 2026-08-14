import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.auth.passwords import hash_password
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, HealEvent, Organization, User
from atlas_engine.db.repo import get_or_create_portal_profile


def _login_as_developer(client: TestClient, org_id) -> None:
    with session_scope() as session:
        user = User(
            org_id=org_id, email=f"dev-{uuid.uuid4()}@example.com", password_hash=hash_password("pw"),
            role="member", job_function="developer",
        )
        session.add(user)
        session.flush()
        email = user.email

    login_response = client.post("/auth/login", json={"email": email, "password": "pw"})
    assert login_response.status_code == 200


def test_approve_and_reject_endpoints():
    with session_scope() as session:
        org = Organization(name="API Heal Org", slug=f"api-heal-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        profile = get_or_create_portal_profile(session, str(org.id), "api-heal.example.com")

        old_golden = GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=1, script_blob_ref="v1.py", status="active")
        session.add(old_golden)
        session.flush()
        new_golden = GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=2, script_blob_ref="v2.py", status="certified", parent_version_id=old_golden.id)
        session.add(new_golden)
        session.flush()
        heal_event = HealEvent(org_id=org.id, golden_script_id=old_golden.id, proposed_version_id=new_golden.id, trigger="on_demand", status="pending_approval")
        session.add(heal_event)
        session.flush()
        heal_event_id = str(heal_event.id)
        org_id = org.id

    client = TestClient(app)
    _login_as_developer(client, org_id)
    response = client.post(f"/heal-events/{heal_event_id}/approve")

    assert response.status_code == 200
    body = response.json()
    assert body["deprecated_version"] == 1
    assert body["active_version"] == 2


def test_reject_endpoint():
    with session_scope() as session:
        org = Organization(name="API Reject Org", slug=f"api-reject-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        profile = get_or_create_portal_profile(session, str(org.id), "api-reject.example.com")

        old_golden = GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=1, script_blob_ref="v1.py", status="active")
        session.add(old_golden)
        session.flush()
        new_golden = GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=2, script_blob_ref="v2.py", status="certified", parent_version_id=old_golden.id)
        session.add(new_golden)
        session.flush()
        heal_event = HealEvent(org_id=org.id, golden_script_id=old_golden.id, proposed_version_id=new_golden.id, trigger="on_demand", status="pending_approval")
        session.add(heal_event)
        session.flush()
        heal_event_id = str(heal_event.id)
        org_id = org.id

    client = TestClient(app)
    _login_as_developer(client, org_id)
    response = client.post(f"/heal-events/{heal_event_id}/reject")

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


def test_heal_endpoint_reports_already_certified(fixture_portal_url):
    from atlas_engine.capture.recorder import CaptureSession
    from atlas_engine.cli import author_from_recorded_actions
    from playwright.sync_api import sync_playwright

    with session_scope() as session:
        org = Organization(name="API Heal Endpoint Org", slug=f"api-heal-endpoint-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

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

    result = author_from_recorded_actions(org_id, start_url, capture.actions)

    client = TestClient(app)
    response = client.post(f"/golden-scripts/{result['golden_script_id']}/heal")

    assert response.status_code == 200
    assert response.json()["status"] == "already_certified"
