import threading
import time
import uuid

import pytest
from playwright.sync_api import sync_playwright

from atlas_engine.authoring.run import resume_authoring_run, start_authoring_run, stop_authoring_run
from atlas_engine.capture.recorder import CaptureSession
from atlas_engine.db import session_scope
from atlas_engine.db.models import (
    AuthoringRun,
    CertificationRun,
    GoldenScript,
    Organization,
    PortalProfile,
    Recording,
    RecordingRequest,
    User,
)
from atlas_engine.recording_service import create_recording_request, persist_recording
from atlas_engine.storage import get_store


def _record_against_fixture(
    org_id: str, fixture_portal_url: str, request_id=None, role="demo",
) -> uuid.UUID:
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
    return persist_recording(org_id, start_url, capture.actions, request_id=request_id, role=role)


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


def test_stop_authoring_run_cancels_an_active_run(fixture_portal_url, fixture_portal_server):
    """The replay no longer pauses for a developer on a drifted step — but an
    active run can still be stopped, and a stop during replay cancels it
    cleanly (no golden is ever emitted)."""
    with session_scope() as session:
        org = Organization(name="Stop Run Org", slug=f"stop-run-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = _record_against_fixture(org_id, fixture_portal_url)

    fixture_portal_server.set_drift_mode(True)
    try:
        authoring_run_id = start_authoring_run(recording_id)
        stop_authoring_run(authoring_run_id)

        with session_scope() as session:
            run = session.get(AuthoringRun, authoring_run_id)
            assert run.status == "cancelled"
            assert run.golden_script_id is None
    finally:
        fixture_portal_server.set_drift_mode(False)


def test_stop_authoring_run_reconciles_an_orphaned_run(fixture_portal_url):
    """No in-process session exists for this run — e.g. the server restarted
    (uvicorn --reload picking up a code change) since the run started,
    wiping the in-memory _sessions registry that owns it. Its DB row is
    stuck at a non-terminal status with nothing left able to ever advance
    or stop it normally. stop_authoring_run must reconcile the row to
    "failed" instead of raising ValueError forever with the UI stuck
    showing "Running"."""
    with session_scope() as session:
        org = Organization(name="Orphaned Run Org", slug=f"orphaned-run-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])

    with session_scope() as session:
        run = AuthoringRun(
            org_id=uuid.UUID(org_id), recording_id=recording_id, status="running", current_phase="replay",
        )
        session.add(run)
        session.flush()
        authoring_run_id = run.id

    stop_authoring_run(authoring_run_id)  # no _sessions entry for this id — must not raise

    with session_scope() as session:
        run = session.get(AuthoringRun, authoring_run_id)
        assert run.status == "failed"
        assert run.ended_at is not None


def test_stop_authoring_run_on_an_already_terminal_orphaned_run_is_a_noop(fixture_portal_url):
    """Calling stop twice (or after the run already finished) on a run with
    no in-process session must not clobber an already-terminal status."""
    with session_scope() as session:
        org = Organization(name="Terminal Orphan Org", slug=f"terminal-orphan-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])

    with session_scope() as session:
        run = AuthoringRun(
            org_id=uuid.UUID(org_id), recording_id=recording_id, status="completed", current_phase="done",
        )
        session.add(run)
        session.flush()
        authoring_run_id = run.id

    stop_authoring_run(authoring_run_id)

    with session_scope() as session:
        run = session.get(AuthoringRun, authoring_run_id)
        assert run.status == "completed"


def test_start_authoring_run_repairs_and_reaches_partially_certified(
    fixture_portal_url, fixture_portal_server,
):
    """Drives the repair loop through the REAL golden/certify pipeline
    (not a standalone certify call) by keeping the fixture portal drifted
    only while the orchestrator is in its "golden" or "certifying" phase,
    and undrifted everywhere else (replay/extract/brain).

    This makes brain always resolve the real, working
    "#generate-report-btn" selector (drift is off there, so nothing needs
    repairing and no LLM is involved), but every certify subprocess run —
    which headlessly replays that exact frozen selector — hits the
    drifted dashboard (button renamed to "#run-report-btn") and reliably
    fails, every single time, for all 3 repair iterations. That forces
    the loop to exhaust _MAX_REPAIR_ATTEMPTS (2) and finalize as
    partially_certified, while proving run_certify_phase's
    run_number_offset fix prevents the second and third certify calls
    from colliding with CertificationRun's UniqueConstraint.

    A background thread toggles drift based on polling
    AuthoringRun.current_phase — safe because each phase transition is
    committed to the DB well before the next slow operation (a fresh
    headless chromium subprocess, or a fresh headed browser) actually
    reaches the point in the fixture portal where drift would matter.
    """
    with session_scope() as session:
        org = Organization(name="Repair Run Org", slug=f"repair-run-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = _record_against_fixture(org_id, fixture_portal_url)

    authoring_run_id = start_authoring_run(recording_id)

    stop_watching = threading.Event()

    def _drift_watcher():
        drifted = False
        while not stop_watching.is_set():
            with session_scope() as session:
                run = session.get(AuthoringRun, authoring_run_id)
                should_drift = run.current_phase in ("golden", "certifying")
                terminal = run.status in ("completed", "failed", "cancelled")
            if should_drift != drifted:
                fixture_portal_server.set_drift_mode(should_drift)
                drifted = should_drift
            if terminal:
                break
            time.sleep(0.02)
        fixture_portal_server.set_drift_mode(False)

    watcher = threading.Thread(target=_drift_watcher, daemon=True)
    watcher.start()
    try:
        status = _wait_for_status(authoring_run_id, {"completed", "failed", "cancelled"}, timeout=600)
    finally:
        stop_watching.set()
        watcher.join(timeout=10)
        fixture_portal_server.set_drift_mode(False)

    assert status == "completed"
    with session_scope() as session:
        run = session.get(AuthoringRun, authoring_run_id)
        assert run.current_phase == "done"
        assert run.repair_attempt == 2
        golden = session.get(GoldenScript, run.golden_script_id)
        assert golden.status == "partially_certified"
        cert_runs = (
            session.query(CertificationRun)
            .filter_by(authoring_run_id=authoring_run_id)
            .order_by(CertificationRun.run_number)
            .all()
        )
        assert len(cert_runs) == 9
        assert [c.run_number for c in cert_runs] == list(range(1, 10))
        assert all(c.status == "fail" for c in cert_runs)


def test_resume_authoring_run_raises_for_unknown_run():
    with pytest.raises(ValueError, match="No active authoring run"):
        resume_authoring_run(uuid.uuid4())


def test_stop_authoring_run_raises_for_unknown_run():
    with pytest.raises(ValueError, match="No active authoring run"):
        stop_authoring_run(uuid.uuid4())


def test_start_authoring_run_raises_for_unknown_recording():
    with pytest.raises(ValueError, match="No such recording"):
        start_authoring_run(uuid.uuid4())


def test_start_authoring_run_fulfills_a_linked_request_on_success(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Run Fulfill Org", slug=f"run-fulfill-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        agent = User(org_id=org.id, email=f"agent-{uuid.uuid4()}@example.com", password_hash="x", role="member")
        session.add(agent)
        session.flush()
        org_id = str(org.id)
        agent_id = str(agent.id)

    request_id = create_recording_request(org_id, agent_id, "http://placeholder/", "Fulfillment test")
    recording_id = _record_against_fixture(org_id, fixture_portal_url, request_id=request_id, role="authoring")

    authoring_run_id = start_authoring_run(recording_id)
    status = _wait_for_status(authoring_run_id, {"completed", "failed", "cancelled"})
    assert status == "completed"

    with session_scope() as session:
        run = session.get(AuthoringRun, authoring_run_id)
        golden_script_id = run.golden_script_id

        request = session.get(RecordingRequest, request_id)
        assert request.status == "fulfilled"
        assert request.fulfilled_golden_script_id == golden_script_id


def test_start_authoring_run_threads_portal_pacing_end_to_end(fixture_portal_url):
    """A PortalProfile with pacing configured must flow all the way through
    replay/brain/golden without breaking the run — proves the wiring added
    in authoring/run.py actually reaches BrainLoop and the codegen emitter,
    not just that the run happens to still pass with pacing off."""
    with session_scope() as session:
        org = Organization(name="Pacing Run Org", slug=f"pacing-run-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = _record_against_fixture(org_id, fixture_portal_url)

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        profile = session.get(PortalProfile, recording.portal_profile_id)
        profile.pacing_pre_action_ms = 50
        profile.pacing_settle_after_nav_ms = 500

    authoring_run_id = start_authoring_run(recording_id)
    status = _wait_for_status(authoring_run_id, {"completed", "failed", "cancelled"})

    assert status == "completed"
    with session_scope() as session:
        run = session.get(AuthoringRun, authoring_run_id)
        golden = session.get(GoldenScript, run.golden_script_id)
        script_text = get_store().read_text(golden.script_blob_ref)
        assert "PACING_MS = 50" in script_text
        assert "def _pace(pg):" in script_text
