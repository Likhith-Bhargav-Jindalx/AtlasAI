import uuid

from playwright.sync_api import sync_playwright

from atlas_engine.authoring.phases import run_brain_phase, run_extract_phase
from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.capture.recorder import CaptureSession
from atlas_engine.db import session_scope
from atlas_engine.db.models import AuthoringRun, AuthoringRunEvent, Organization, PortalProfile, Recording
from atlas_engine.llm.client import FakeLlmClient


def _make_authoring_run() -> uuid.UUID:
    with session_scope() as session:
        org = Organization(name="Phases Org", slug=f"phases-org-{uuid.uuid4()}")
        profile = PortalProfile(org=org, domain="portal-phases.example.com")
        recording = Recording(
            org=org, portal_profile=profile, status="captured", capture_blob_prefix="orgs/x/recordings/1/",
        )
        run = AuthoringRun(org=org, recording=recording, status="running", current_phase="extract")
        session.add_all([org, profile, recording, run])
        session.flush()
        return run.id


def test_run_extract_phase_returns_the_same_trajectory():
    trajectory = ReferenceTrajectory(
        steps=(TrajectoryStep(0, "click", [{"selector": "#a", "rank": 0}], None, "none"),)
    )

    result = run_extract_phase(trajectory)

    assert result == trajectory


def test_run_brain_phase_completes_on_track_when_nothing_has_drifted(fixture_portal_url):
    authoring_run_id = _make_authoring_run()
    start_url = f"{fixture_portal_url}/login.html"
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", "none"),
            TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
            TrajectoryStep(3, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
        )
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(start_url)
        outcomes = run_brain_phase(page, trajectory, authoring_run_id)
        browser.close()

    assert all(o.on_track for o in outcomes)
    with session_scope() as session:
        events = session.query(AuthoringRunEvent).filter_by(authoring_run_id=authoring_run_id).all()
        assert len(events) == 4
        assert all(e.level == "info" for e in events)


def test_run_brain_phase_repairs_a_drifted_selector_with_the_llm(fixture_portal_url, fixture_portal_server):
    authoring_run_id = _make_authoring_run()
    start_url = f"{fixture_portal_url}/login.html"
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", "none"),
            TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
            TrajectoryStep(3, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
        )
    )

    fixture_portal_server.set_drift_mode(True)
    try:
        fake_llm = FakeLlmClient(responses=[{"action": "replace_selector", "selector": "#run-report-btn"}])
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            page = browser.new_page()
            page.goto(start_url)
            outcomes = run_brain_phase(page, trajectory, authoring_run_id, llm=fake_llm)
            browser.close()

        assert all(o.on_track for o in outcomes)
        with session_scope() as session:
            events = session.query(AuthoringRunEvent).filter_by(authoring_run_id=authoring_run_id).all()
            assert len(events) == 4
            assert all(e.level == "info" for e in events)  # BrainLoop's internal repair already resolved it
    finally:
        fixture_portal_server.set_drift_mode(False)


def test_run_brain_phase_logs_a_warning_for_a_step_that_stays_off_track(fixture_portal_url, fixture_portal_server):
    authoring_run_id = _make_authoring_run()
    start_url = f"{fixture_portal_url}/login.html"
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", "none"),
            TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
            TrajectoryStep(3, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
        )
    )

    fixture_portal_server.set_drift_mode(True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            page = browser.new_page()
            page.goto(start_url)
            outcomes = run_brain_phase(page, trajectory, authoring_run_id)  # no llm -> can't repair
            browser.close()

        assert not all(o.on_track for o in outcomes)
        with session_scope() as session:
            events = session.query(AuthoringRunEvent).filter_by(authoring_run_id=authoring_run_id).all()
            assert any(e.level == "warning" for e in events)
    finally:
        fixture_portal_server.set_drift_mode(False)
