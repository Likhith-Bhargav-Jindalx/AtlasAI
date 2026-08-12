import uuid

from playwright.sync_api import sync_playwright

from atlas_engine.authoring_service import author_from_recording
from atlas_engine.capture.recorder import CaptureSession
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, Organization, Recording, RecordingRequest, User
from atlas_engine.recording_service import (
    claim_recording_request,
    create_recording_request,
    list_recording_requests,
    persist_recording,
)


def _record_flow(start_url: str) -> list:
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
    return capture.actions


def test_operations_agent_to_fulfilled_request(fixture_portal_url):
    """The full loop this plan exists for: an operations agent submits a
    request and records a demo, a developer sees it in their queue,
    claims it, records their own authoring walkthrough, and generates a
    certified golden script — which marks the request fulfilled.
    """
    with session_scope() as session:
        org = Organization(name="E2E Requests Org", slug=f"e2e-requests-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        agent = User(
            org_id=org.id, email=f"agent-{uuid.uuid4()}@example.com", password_hash="x",
            role="member", job_function="operations_agent",
        )
        developer = User(
            org_id=org.id, email=f"dev-{uuid.uuid4()}@example.com", password_hash="x",
            role="member", job_function="developer",
        )
        session.add_all([agent, developer])
        session.flush()
        org_id = str(org.id)
        agent_id = str(agent.id)
        developer_id = developer.id

    start_url = f"{fixture_portal_url}/login.html"

    # 1. Operations agent submits a request.
    request_id = create_recording_request(org_id, agent_id, start_url, "Generate the dashboard report")

    # 2. Operations agent records the demo.
    demo_actions = _record_flow(start_url)
    demo_recording_id = persist_recording(org_id, start_url, demo_actions, request_id=request_id, role="demo")

    with session_scope() as session:
        assert session.get(RecordingRequest, request_id).status == "submitted"
        assert session.get(Recording, demo_recording_id).role == "demo"

    # 3. Developer sees it in their queue and claims it.
    submitted = list_recording_requests(org_id, status="submitted")
    assert any(r["id"] == str(request_id) for r in submitted)

    claim_recording_request(request_id, developer_id)

    # 4. Developer records their own authoring walkthrough under the same request.
    dev_actions = _record_flow(start_url)
    dev_recording_id = persist_recording(org_id, start_url, dev_actions, request_id=request_id, role="authoring")

    # 5. Developer authors the golden script from their own recording.
    result = author_from_recording(dev_recording_id)
    assert result["status"] == "certified"

    with session_scope() as session:
        request = session.get(RecordingRequest, request_id)
        assert request.status == "fulfilled"
        assert request.fulfilled_golden_script_id == uuid.UUID(result["golden_script_id"])

        golden = session.get(GoldenScript, uuid.UUID(result["golden_script_id"]))
        assert golden.created_from_recording_id == dev_recording_id

    fulfilled = list_recording_requests(org_id, status="fulfilled")
    assert any(r["id"] == str(request_id) for r in fulfilled)
