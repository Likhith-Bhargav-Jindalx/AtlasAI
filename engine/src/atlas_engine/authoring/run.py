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
from atlas_engine.brain.models import ReferenceTrajectory
from atlas_engine.brain.serialize import trajectory_from_dict
from atlas_engine.capture.codegen import build_codegen_trajectory, parse_codegen_actions
from atlas_engine.config import settings
from atlas_engine.db import session_scope
from atlas_engine.db.models import AuthoringRun, GoldenScript, PortalProfile, Recording, RecordingRequest
from atlas_engine.db.models import ReferenceTrajectory as ReferenceTrajectoryModel
from atlas_engine.llm.client import LlmClient

_MAX_REPAIR_ATTEMPTS = 2
_CERTIFY_RUNS = 3

_sessions: dict[uuid.UUID, "_AuthoringRunSession"] = {}
_registry_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def apply_stored_codegen_selectors(
    trajectory: ReferenceTrajectory, capture_blob_prefix: str,
) -> tuple[ReferenceTrajectory, bool]:
    """Loads the recording's native codegen script (if any) and rebuilds the
    replay trajectory from it — the script is the ground truth of what the
    user did (it covers navigations, popups and iframes that the raw
    recorder's CSS candidates often lack). Returns the rebuilt trajectory and
    whether it was replaced (False for old recordings with no script, or a
    script with no replayable action lines)."""
    codegen_path = Path(settings.ensure_run_root()) / capture_blob_prefix / "codegen.py"
    if not (codegen_path.exists() and codegen_path.stat().st_size > 0):
        return trajectory, False
    codegen_actions = parse_codegen_actions(codegen_path.read_text())
    rebuilt = build_codegen_trajectory(codegen_actions)
    if rebuilt is None:
        return trajectory, False
    return rebuilt, True


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
        completed = self.done_event.wait(timeout=timeout)
        if not completed:
            raise TimeoutError("Authoring run did not finish stopping in time")
        if self.error is not None:
            raise self.error

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
                request_id = recording.request_id
                capture_blob_prefix = recording.capture_blob_prefix

            trajectory = trajectory_from_dict(json.loads(Path(trajectory_blob_ref).read_text()))

            # When the recording captured a native codegen script, drive the
            # initial run with its selectors (rank-0 candidates).
            trajectory, script_applied = apply_stored_codegen_selectors(trajectory, capture_blob_prefix)
            if script_applied:
                log_event(
                    self.authoring_run_id, phase="replay",
                    message="Replaying with the recorded script's selectors",
                )

            update_run(self.authoring_run_id, current_phase="replay")
            replayed_trajectory = None
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=False)
                    context = browser.new_context(accept_downloads=True)
                    context.set_default_timeout(60000)
                    context.on("page", lambda p: p.on("dialog", lambda d: d.dismiss()))
                    page = context.new_page()
                    page.on("dialog", lambda d: d.dismiss())
                    try:
                        page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
                    except Exception as goto_exc:
                        log_event(
                            self.authoring_run_id, phase="replay", level="warning",
                            message=f"Initial navigation to {start_url} slow or redirected ({goto_exc}) — proceeding with recorded steps",
                        )
                    replayed_trajectory = run_replay_phase(
                        page, trajectory, self.authoring_run_id, self.resume_event, self.stop_event,
                    )
                    browser.close()
            except Exception as replay_exc:
                log_event(
                    self.authoring_run_id, phase="replay", level="warning",
                    message=f"Replay stopped early ({replay_exc}) — partial reference captured; the brain will work the rest live.",
                )
                replayed_trajectory = trajectory

            if self.stop_event.is_set() or replayed_trajectory is None:
                log_event(self.authoring_run_id, phase="replay", message="Run cancelled")
                update_run(self.authoring_run_id, status="cancelled", ended_at=_now())
                return

            trajectory = replayed_trajectory
            update_run(self.authoring_run_id, current_phase="extract")
            trajectory = run_extract_phase(trajectory)

            while True:
                if self.stop_event.is_set():
                    log_event(self.authoring_run_id, phase="extract", message="Run cancelled")
                    update_run(self.authoring_run_id, status="cancelled", ended_at=_now())
                    return

                with session_scope() as session:
                    run = session.get(AuthoringRun, self.authoring_run_id)
                    repair_attempt = run.repair_attempt

                update_run(self.authoring_run_id, current_phase="brain")
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=False)
                    context = browser.new_context(accept_downloads=True)
                    context.set_default_timeout(60000)
                    context.on("page", lambda p: p.on("dialog", lambda d: d.dismiss()))
                    page = context.new_page()
                    page.on("dialog", lambda d: d.dismiss())
                    try:
                        page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
                    except Exception as goto_exc:
                        log_event(
                            self.authoring_run_id, phase="brain", level="warning",
                            message=f"Initial navigation to {start_url}: {goto_exc}",
                        )
                    result = run_brain_phase(page, trajectory, self.authoring_run_id, llm=self.llm)
                    browser.close()

                if self.stop_event.is_set():
                    log_event(self.authoring_run_id, phase="brain", message="Run cancelled")
                    update_run(self.authoring_run_id, status="cancelled", ended_at=_now())
                    return

                if not result.success:
                    log_event(
                        self.authoring_run_id, phase="brain", level="warning",
                        message=f"Step {result.failed_step.index if result.failed_step else '?'} failed: {result.failure_reason}",
                    )
                    update_run(self.authoring_run_id, status="failed", ended_at=_now())
                    return

                # BrainLoop already applies any successful repair internally
                # (retrying with a cached/LLM-resolved selector) and returns
                # the VERIFIED trajectory — golden/certify freeze the selector
                # that actually worked, not the stale recorded one. Mirrors
                # verify/heal.py's heal_golden_script.
                trajectory = result.trajectory

                update_run(self.authoring_run_id, current_phase="golden")
                golden_script_id, script_path = run_golden_phase(
                    trajectory, start_url, org_id, portal_profile_id, self.recording_id, self.authoring_run_id,
                )
                update_run(self.authoring_run_id, golden_script_id=golden_script_id)

                if self.stop_event.is_set():
                    log_event(self.authoring_run_id, phase="golden", message="Run cancelled")
                    update_run(self.authoring_run_id, status="cancelled", ended_at=_now())
                    return

                update_run(self.authoring_run_id, current_phase="certifying")
                passed, total = run_certify_phase(
                    script_path, self.authoring_run_id, runs=_CERTIFY_RUNS,
                    run_number_offset=repair_attempt * _CERTIFY_RUNS,
                )

                if self.stop_event.is_set():
                    log_event(self.authoring_run_id, phase="certifying", message="Run cancelled")
                    update_run(self.authoring_run_id, status="cancelled", ended_at=_now())
                    return

                if passed == total:
                    self._finalize_golden(golden_script_id, "certified", request_id)
                    break

                if repair_attempt >= _MAX_REPAIR_ATTEMPTS:
                    self._finalize_golden(golden_script_id, "partially_certified", request_id)
                    break

                update_run(self.authoring_run_id, repair_attempt=repair_attempt + 1)
                log_event(
                    self.authoring_run_id, phase="certifying", level="warning",
                    message=(
                        f"Certification {passed}/{total} passed — retrying via brain "
                        f"(repair {repair_attempt + 1}/{_MAX_REPAIR_ATTEMPTS})"
                    ),
                )

            log_event(self.authoring_run_id, phase="certifying", message="Authoring run completed")
            update_run(self.authoring_run_id, status="completed", current_phase="done", ended_at=_now())
        except Exception as exc:  # surfaced to the caller of stop(), and leaves the run in a diagnosable state
            self.error = exc
            with session_scope() as session:
                run = session.get(AuthoringRun, self.authoring_run_id)
                failure_phase = run.current_phase if run is not None else "replay"
            log_event(
                self.authoring_run_id, phase=failure_phase, level="error",
                message=f"{type(exc).__name__}: {exc}",
            )
            update_run(self.authoring_run_id, status="failed", ended_at=_now())
        finally:
            with _registry_lock:
                _sessions.pop(self.authoring_run_id, None)
            self.done_event.set()

    def _finalize_golden(
        self, golden_script_id: uuid.UUID, status: str, request_id: uuid.UUID | None = None,
    ) -> None:
        with session_scope() as session:
            golden = session.get(GoldenScript, golden_script_id)
            golden.status = status

            if status == "certified" and request_id is not None:
                request = session.get(RecordingRequest, request_id)
                if request is not None:
                    request.status = "fulfilled"
                    request.fulfilled_golden_script_id = golden_script_id
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
        session_obj = _sessions.get(authoring_run_id)
    if session_obj is not None:
        session_obj.stop(timeout=timeout)
        return

    # No in-process session for this id — most commonly the server restarted
    # (e.g. uvicorn --reload picking up a code change) since this run
    # started, wiping the in-memory registry that owned it. Its DB row is
    # then stuck at whatever non-terminal status it had, with nothing left
    # able to ever advance or stop it the normal way — reconcile it here
    # instead of leaving the caller to retry a stop that can never succeed.
    with session_scope() as session:
        run = session.get(AuthoringRun, authoring_run_id)
        if run is None:
            raise ValueError(f"No active authoring run: {authoring_run_id}")
        status = run.status
        current_phase = run.current_phase

    if status not in ("running", "waiting_for_developer"):
        return  # already terminal — stop is idempotent, nothing to reconcile

    update_run(authoring_run_id, status="failed", ended_at=_now())
    log_event(
        authoring_run_id, phase=current_phase, level="warning",
        message="Run orphaned (no active session, likely a server restart) — marked failed by stop.",
    )
