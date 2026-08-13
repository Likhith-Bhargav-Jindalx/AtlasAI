import time
import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.capture.recorder import CaptureSession
from atlas_engine.db import session_scope
from atlas_engine.db.models import (
    AuthoringRun,
    AuthoringRunEvent,
    CertificationRun,
    GoldenScript,
    Organization,
    PortalProfile,
    Recording,
)
from atlas_engine.recording_service import persist_recording


def _record_against_fixture(org_id: str, fixture_portal_url: str) -> uuid.UUID:
    from playwright.sync_api import sync_playwright

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
    return persist_recording(org_id, start_url, capture.actions)


def _make_authoring_run_with_details():
    with session_scope() as session:
        org = Organization(name="API Authoring Org", slug=f"api-authoring-org-{uuid.uuid4()}")
        profile = PortalProfile(org=org, domain="portal-api-authoring.example.com")
        recording = Recording(
            org=org, portal_profile=profile, status="captured", capture_blob_prefix="orgs/x/recordings/1/",
        )
        golden = GoldenScript(
            org=org, portal_profile=profile, version=1, script_blob_ref=__file__, status="certified",
        )
        session.add_all([org, profile, recording, golden])
        session.flush()
        run = AuthoringRun(
            org=org, recording=recording, golden_script=golden, status="completed",
            current_phase="done", repair_attempt=1,
        )
        session.add(run)
        session.flush()
        session.add(AuthoringRunEvent(authoring_run=run, phase="replay", step_index=0, message="Step 0: click - OK"))
        session.add(AuthoringRunEvent(authoring_run=run, phase="certifying", level="warning", message="retrying"))
        session.add(CertificationRun(authoring_run=run, run_number=1, status="pass"))
        session.add(CertificationRun(authoring_run=run, run_number=2, status="fail", failure_evidence_blob_ref="x.txt"))
        session.flush()
        return run.id


def test_get_authoring_run_returns_full_detail():
    authoring_run_id = _make_authoring_run_with_details()

    client = TestClient(app)
    response = client.get(f"/authoring-runs/{authoring_run_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["current_phase"] == "done"
    assert body["repair_attempt"] == 1
    assert len(body["events"]) == 2
    assert body["events"][0]["phase"] == "replay"
    assert body["events"][0]["message"] == "Step 0: click - OK"
    assert body["events"][1]["level"] == "warning"
    assert body["golden_script"]["status"] == "certified"
    assert len(body["certification_runs"]) == 2
    assert body["certification_runs"][0]["status"] == "pass"
    assert body["certification_runs"][1]["status"] == "fail"
    assert body["certification_runs"][1]["failure_evidence_blob_ref"] == "x.txt"


def test_get_authoring_run_has_null_golden_script_before_golden_phase():
    with session_scope() as session:
        org = Organization(name="No Golden Org", slug=f"no-golden-org-{uuid.uuid4()}")
        profile = PortalProfile(org=org, domain="portal-no-golden.example.com")
        recording = Recording(
            org=org, portal_profile=profile, status="captured", capture_blob_prefix="orgs/x/recordings/2/",
        )
        session.add_all([org, profile, recording])
        session.flush()
        run = AuthoringRun(org=org, recording=recording, status="running", current_phase="replay")
        session.add(run)
        session.flush()
        authoring_run_id = run.id

    client = TestClient(app)
    response = client.get(f"/authoring-runs/{authoring_run_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["golden_script"] is None
    assert body["events"] == []
    assert body["certification_runs"] == []


def test_get_authoring_run_404_for_unknown_id():
    client = TestClient(app)
    response = client.get(f"/authoring-runs/{uuid.uuid4()}")
    assert response.status_code == 404


def test_resume_authoring_run_endpoint_404_for_unknown_run():
    client = TestClient(app)
    response = client.post(f"/authoring-runs/{uuid.uuid4()}/resume")
    assert response.status_code == 404


def test_stop_authoring_run_endpoint_404_for_unknown_run():
    client = TestClient(app)
    response = client.post(f"/authoring-runs/{uuid.uuid4()}/stop")
    assert response.status_code == 404


def test_full_authoring_run_via_http_reaches_certified(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="HTTP Authoring Org", slug=f"http-authoring-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = _record_against_fixture(org_id, fixture_portal_url)

    client = TestClient(app)
    start_response = client.post(f"/recordings/{recording_id}/author")
    assert start_response.status_code == 200
    authoring_run_id = start_response.json()["authoring_run_id"]

    deadline = time.time() + 90
    while time.time() < deadline:
        poll = client.get(f"/authoring-runs/{authoring_run_id}")
        assert poll.status_code == 200
        if poll.json()["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.5)

    final = client.get(f"/authoring-runs/{authoring_run_id}")
    body = final.json()
    assert body["status"] == "completed"
    assert body["current_phase"] == "done"
    assert body["golden_script"]["status"] == "certified"
    assert body["golden_script"]["script_text"] is not None
    assert "#generate-report-btn" in body["golden_script"]["script_text"]
    assert len(body["certification_runs"]) == 3
    assert all(c["status"] == "pass" for c in body["certification_runs"])
    assert any("OK" in e["message"] for e in body["events"])


def test_stop_authoring_run_via_http_while_waiting_for_developer(fixture_portal_url, fixture_portal_server):
    with session_scope() as session:
        org = Organization(name="HTTP Stop Org", slug=f"http-stop-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = _record_against_fixture(org_id, fixture_portal_url)

    fixture_portal_server.set_drift_mode(True)
    try:
        client = TestClient(app)
        start_response = client.post(f"/recordings/{recording_id}/author")
        authoring_run_id = start_response.json()["authoring_run_id"]

        deadline = time.time() + 30
        paused = False
        while time.time() < deadline:
            poll = client.get(f"/authoring-runs/{authoring_run_id}")
            if poll.json()["status"] == "waiting_for_developer":
                paused = True
                break
            time.sleep(0.3)
        assert paused, "authoring run never reached waiting_for_developer via HTTP polling"

        stop_response = client.post(f"/authoring-runs/{authoring_run_id}/stop")
        assert stop_response.status_code == 200
        assert stop_response.json() == {"ok": True}

        final = client.get(f"/authoring-runs/{authoring_run_id}")
        assert final.json()["status"] == "cancelled"
    finally:
        fixture_portal_server.set_drift_mode(False)
