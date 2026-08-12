import shutil
import threading
import uuid

from playwright.sync_api import sync_playwright

from atlas_engine.capture.recorder import CaptureSession
from atlas_engine.capture.video import finalize_video, start_capture_context
from atlas_engine.recording_service import persist_recording

_sessions: dict[uuid.UUID, "_LiveCaptureSession"] = {}
_registry_lock = threading.Lock()


class _LiveCaptureSession:
    """Owns the Playwright sync-API objects for the lifetime of one live
    recording. Everything outside this class communicates with it only
    through stop_event/done_event — never by touching Playwright objects
    from another thread, which is what makes this safe."""

    def __init__(self, org_id: str, start_url: str, request_id: uuid.UUID | None, role: str):
        self.org_id = org_id
        self.start_url = start_url
        self.request_id = request_id
        self.role = role
        self.stop_event = threading.Event()
        self.done_event = threading.Event()
        self.recording_id: uuid.UUID | None = None
        self.error: Exception | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        video_dir = None
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=False)
                context, page, video_started_at_ms, video_dir = start_capture_context(browser)
                page.goto(self.start_url)
                with CaptureSession(page) as capture:
                    self.stop_event.wait()
                actions = capture.actions
                video_path, video_ended_at_ms = finalize_video(context, page)
                browser.close()

            self.recording_id = persist_recording(
                self.org_id, self.start_url, actions,
                request_id=self.request_id, role=self.role,
                video_path=video_path,
                video_started_at_ms=video_started_at_ms,
                video_ended_at_ms=video_ended_at_ms,
            )
        except Exception as exc:  # surfaced to the caller of stop()
            self.error = exc
        finally:
            if video_dir is not None:
                shutil.rmtree(video_dir, ignore_errors=True)
            self.done_event.set()

    def start(self) -> None:
        self.thread.start()

    def stop(self, timeout: float = 30.0) -> uuid.UUID:
        self.stop_event.set()
        completed = self.done_event.wait(timeout=timeout)
        if not completed:
            raise TimeoutError("Capture session did not finish stopping in time")
        if self.error is not None:
            raise self.error
        return self.recording_id


def start_live_capture(
    org_id: str, start_url: str, *, request_id: uuid.UUID | None = None, role: str = "demo"
) -> uuid.UUID:
    session_id = uuid.uuid4()
    session = _LiveCaptureSession(org_id, start_url, request_id, role)
    with _registry_lock:
        _sessions[session_id] = session
    session.start()
    return session_id


def stop_live_capture(capture_session_id: uuid.UUID, *, timeout: float = 30.0) -> uuid.UUID:
    with _registry_lock:
        session = _sessions.pop(capture_session_id, None)
    if session is None:
        raise ValueError(f"No active capture session: {capture_session_id}")
    return session.stop(timeout=timeout)
