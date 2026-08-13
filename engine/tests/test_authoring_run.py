import time
import uuid

import pytest
from playwright.sync_api import sync_playwright

from atlas_engine.authoring.run import resume_authoring_run, start_authoring_run, stop_authoring_run
from atlas_engine.capture.recorder import CaptureSession
from atlas_engine.db import session_scope
from atlas_engine.db.models import AuthoringRun, CertificationRun, GoldenScript, Organization
from atlas_engine.recording_service import persist_recording


def _record_against_fixture(org_id: str, fixture_portal_url: str) -> uuid.UUID:
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


def _wait_for_status(authoring_run_id, statuses, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with session_scope() as session:
            run = session.get(AuthoringRun, authoring_run_id)
            if run.status in statuses:
                return run.status
        time.sleep(0.5)
    raise TimeoutError(f"authoring run never reached one of {statuses}")


def test_start_authoring_run_completes_and_certifies(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Full Run Org", slug=f"full-run-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = _record_against_fixture(org_id, fixture_portal_url)

    authoring_run_id = start_authoring_run(recording_id)
    status = _wait_for_status(authoring_run_id, {"completed", "failed", "cancelled"})

    assert status == "completed"
    with session_scope() as session:
        run = session.get(AuthoringRun, authoring_run_id)
        assert run.current_phase == "done"
        golden = session.get(GoldenScript, run.golden_script_id)
        assert golden.status == "certified"
        cert_runs = session.query(CertificationRun).filter_by(authoring_run_id=authoring_run_id).all()
        assert len(cert_runs) == 3
        assert all(c.status == "pass" for c in cert_runs)


def test_stop_authoring_run_cancels_while_waiting_for_developer(fixture_portal_url, fixture_portal_server):
    with session_scope() as session:
        org = Organization(name="Stop Run Org", slug=f"stop-run-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = _record_against_fixture(org_id, fixture_portal_url)

    fixture_portal_server.set_drift_mode(True)
    try:
        authoring_run_id = start_authoring_run(recording_id)
        status = _wait_for_status(authoring_run_id, {"waiting_for_developer", "failed", "completed"})
        assert status == "waiting_for_developer"

        stop_authoring_run(authoring_run_id)

        with session_scope() as session:
            run = session.get(AuthoringRun, authoring_run_id)
            assert run.status == "cancelled"
            assert run.golden_script_id is None
    finally:
        fixture_portal_server.set_drift_mode(False)


def test_resume_authoring_run_raises_for_unknown_run():
    with pytest.raises(ValueError, match="No active authoring run"):
        resume_authoring_run(uuid.uuid4())


def test_stop_authoring_run_raises_for_unknown_run():
    with pytest.raises(ValueError, match="No active authoring run"):
        stop_authoring_run(uuid.uuid4())


def test_start_authoring_run_raises_for_unknown_recording():
    with pytest.raises(ValueError, match="No such recording"):
        start_authoring_run(uuid.uuid4())
