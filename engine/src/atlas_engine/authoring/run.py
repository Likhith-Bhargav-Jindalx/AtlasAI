import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from atlas_engine.authoring.events import log_event
from atlas_engine.authoring.phases import run_brain_phase, run_certify_phase, run_extract_phase, run_golden_phase
from atlas_engine.authoring.replay import run_replay_phase
from atlas_engine.authoring.state import update_run
from atlas_engine.brain.serialize import trajectory_from_dict
from atlas_engine.db import session_scope
from atlas_engine.db.models import AuthoringRun, GoldenScript, PortalProfile, Recording
from atlas_engine.db.models import ReferenceTrajectory as ReferenceTrajectoryModel
from atlas_engine.llm.client import LlmClient

_MAX_REPAIR_ATTEMPTS = 2
_CERTIFY_RUNS = 3

_sessions: dict[uuid.UUID, "_AuthoringRunSession"] = {}
_registry_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class _AuthoringRunSession:
    """Owns the Playwright sync-API objects for the lifetime of one
    authoring run. Everything outside this class communicates with it
    only through DB rows (AuthoringRun/AuthoringRunEvent/CertificationRun)
    and stop_event/resume_event/done_event — never by touching Playwright
    objects from another thread, mirroring live_capture.py's
    _LiveCaptureSession exactly."""

    def __init__(self, authoring_run_id: uuid.UUID, recording_id: uuid.UUID, llm: LlmClient | None):
        self.authoring_run_id = authoring_run_id
        self.recording_id = recording_id
        self.llm = llm
        self.resume_event = threading.Event()
        self.stop_event = threading.Event()
        self.done_event = threading.Event()
        self.error: Exception | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def resume(self) -> None:
        self.resume_event.set()

    def stop(self, timeout: float = 30.0) -> None:
        self.stop_event.set()
        self.done_event.wait(timeout=timeout)

    def _run(self) -> None:
        try:
            with session_scope() as session:
                recording = session.get(Recording, self.recording_id)
                profile = session.get(PortalProfile, recording.portal_profile_id)
                trajectory_row = (
                    session.query(ReferenceTrajectoryModel).filter_by(recording_id=self.recording_id).one()
                )
                org_id = recording.org_id
                portal_profile_id = recording.portal_profile_id
                start_url = profile.inferred_login_url or f"https://{profile.domain}"
                trajectory_blob_ref = trajectory_row.trajectory_blob_ref

            trajectory = trajectory_from_dict(json.loads(Path(trajectory_blob_ref).read_text()))

            update_run(self.authoring_run_id, current_phase="replay")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=False)
                page = browser.new_page()
                page.goto(start_url)
                trajectory = run_replay_phase(
                    page, trajectory, self.authoring_run_id, self.resume_event, self.stop_event,
                )
                browser.close()

            if trajectory is None:
                update_run(self.authoring_run_id, status="cancelled", ended_at=_now())
                return

            update_run(self.authoring_run_id, current_phase="extract")
            trajectory = run_extract_phase(trajectory)

            while True:
                if self.stop_event.is_set():
                    update_run(self.authoring_run_id, status="cancelled", ended_at=_now())
                    return

                update_run(self.authoring_run_id, current_phase="brain")
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=False)
                    page = browser.new_page()
                    page.goto(start_url)
                    outcomes = run_brain_phase(page, trajectory, self.authoring_run_id, llm=self.llm)
                    browser.close()

                if not all(outcome.on_track for outcome in outcomes):
                    update_run(self.authoring_run_id, status="failed", ended_at=_now())
                    return

                update_run(self.authoring_run_id, current_phase="golden")
                golden_script_id, script_path = run_golden_phase(
                    trajectory, start_url, org_id, portal_profile_id, self.recording_id, self.authoring_run_id,
                )
                update_run(self.authoring_run_id, golden_script_id=golden_script_id)

                update_run(self.authoring_run_id, current_phase="certifying")
                passed, total = run_certify_phase(
                    script_path, self.authoring_run_id, runs=_CERTIFY_RUNS,
                )

                if passed == total:
                    self._finalize_golden(golden_script_id, "certified")
                    break

                with session_scope() as session:
                    run = session.get(AuthoringRun, self.authoring_run_id)
                    repair_attempt = run.repair_attempt

                if repair_attempt >= _MAX_REPAIR_ATTEMPTS:
                    self._finalize_golden(golden_script_id, "partially_certified")
                    break

                update_run(self.authoring_run_id, repair_attempt=repair_attempt + 1)
                log_event(
                    self.authoring_run_id, phase="certifying", level="warning",
                    message=(
                        f"Certification {passed}/{total} passed — retrying via brain "
                        f"(repair {repair_attempt + 1}/{_MAX_REPAIR_ATTEMPTS})"
                    ),
                )

            update_run(self.authoring_run_id, status="completed", current_phase="done", ended_at=_now())
        except Exception as exc:  # surfaced to the caller of stop(), and leaves the run in a diagnosable state
            self.error = exc
            update_run(self.authoring_run_id, status="failed", ended_at=_now())
        finally:
            self.done_event.set()

    def _finalize_golden(self, golden_script_id: uuid.UUID, status: str) -> None:
        with session_scope() as session:
            golden = session.get(GoldenScript, golden_script_id)
            golden.status = status
        log_event(self.authoring_run_id, phase="certifying", message=f"Golden script finalized as {status}")


def start_authoring_run(recording_id: uuid.UUID, *, llm: LlmClient | None = None) -> uuid.UUID:
    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        if recording is None:
            raise ValueError(f"No such recording: {recording_id}")
        run = AuthoringRun(
            org_id=recording.org_id, recording_id=recording_id, status="running", current_phase="replay",
        )
        session.add(run)
        session.flush()
        authoring_run_id = run.id

    session_obj = _AuthoringRunSession(authoring_run_id, recording_id, llm)
    with _registry_lock:
        _sessions[authoring_run_id] = session_obj
    session_obj.start()
    return authoring_run_id


def resume_authoring_run(authoring_run_id: uuid.UUID) -> None:
    with _registry_lock:
        session_obj = _sessions.get(authoring_run_id)
    if session_obj is None:
        raise ValueError(f"No active authoring run: {authoring_run_id}")
    session_obj.resume()


def stop_authoring_run(authoring_run_id: uuid.UUID, *, timeout: float = 30.0) -> None:
    with _registry_lock:
        session_obj = _sessions.pop(authoring_run_id, None)
    if session_obj is None:
        raise ValueError(f"No active authoring run: {authoring_run_id}")
    session_obj.stop(timeout=timeout)
