import logging
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

from atlas_engine.capture.codegen import attach_codegen_recorder, strip_extraction_markers
from atlas_engine.capture.extraction import attach_extraction_button, collect_page_data
from atlas_engine.capture.recorder import CaptureSession
from atlas_engine.capture.video import capture_artifact_paths, finalize_video, start_capture_context, video_path_for_page
from atlas_engine.recording_service import PageCapture, persist_recording

logger = logging.getLogger(__name__)

_sessions: dict[uuid.UUID, "_LiveCaptureSession"] = {}
_registry_lock = threading.Lock()


def _wait_for_stop_or_disconnect(
    stop_event: threading.Event, page, *, poll_interval: float = 1.0, on_tick=lambda: None,
) -> bool:
    """Blocks until stop_event is set (an explicit /stop call) or the
    browser stops responding to a cheap health check (it died on its own —
    most often a human closing the window before clicking Stop) —
    whichever happens first. Returns True if it was the browser that died.

    on_tick is called once per poll iteration and once more after the
    loop exits, on this same calling thread — used to attach a
    CaptureSession to any tab/popup that opened since the last tick,
    since Playwright objects must only ever be touched from the one
    thread already doing all the other capture work for this session.

    Playwright's own "disconnected" event does NOT fire for this case —
    verified empirically: it fires promptly for a self-initiated
    browser.close(), but not within 15 seconds of the process being
    killed externally. browser.is_connected() is equally useless, since
    it just reflects a stale client-side flag rather than probing the
    live connection. Only an actual operation against the page reliably
    detects a truly dead browser, so that's what this polls with."""
    while not stop_event.wait(timeout=poll_interval):
        on_tick()
        try:
            page.title()
        except Exception:
            on_tick()
            return True
    on_tick()
    return False


def _resolve_page_index(page, main_page, extra_captures: list[tuple]) -> int | None:
    """Maps a Page object back to the page_index it has (or will have)
    in the eventual PageCapture list — 0 for the main page, position+1
    for a page already tracked in extra_captures, None if the page isn't
    tracked yet (e.g. a grab request that raced a not-yet-attached
    popup; the caller drops these rather than guessing)."""
    if page is main_page:
        return 0
    for i, (extra_page, _capture, _started_at_ms) in enumerate(extra_captures):
        if extra_page is page:
            return i + 1
    return None


class _LiveCaptureSession:
    """Owns the Playwright sync-API objects for the lifetime of one live
    recording. Everything outside this class communicates with it only
    through stop_event/done_event — never by touching Playwright objects
    from another thread, which is what makes this safe."""

    def __init__(self, org_id: str, start_url: str, request_id: uuid.UUID | None, role: str, user_id: str | None = None):
        self.org_id = org_id
        self.start_url = start_url
        self.request_id = request_id
        self.role = role
        self.user_id = user_id
        self.stop_event = threading.Event()
        self.done_event = threading.Event()
        self.recording_id: uuid.UUID | None = None
        self.error: Exception | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        video_dir = None
        codegen_dir = None
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=False)
                context, page, video_started_at_ms, video_dir = start_capture_context(browser)

                pending_grab_pages: list = []
                attach_extraction_button(context, pending_grab_pages.append)

                codegen_dir = Path(tempfile.mkdtemp(prefix="atlas-codegen-"))
                codegen_path = codegen_dir / "codegen.py"
                attach_codegen_recorder(context, codegen_path)

                page.goto(self.start_url)

                new_pages: list = []
                context.on("page", lambda new_page: new_pages.append(new_page))

                extra_captures: list[tuple] = []
                pending_extractions: list[dict] = []

                def _attach_new_pages() -> None:
                    while new_pages:
                        new_page = new_pages.pop(0)
                        started_at_ms = int(time.time() * 1000)
                        capture = CaptureSession(new_page)
                        capture.__enter__()
                        extra_captures.append((new_page, capture, started_at_ms))

                    while pending_grab_pages:
                        grabbed_page = pending_grab_pages.pop(0)
                        try:
                            page_index = _resolve_page_index(grabbed_page, page, extra_captures)
                            if page_index is None:
                                logger.warning(
                                    "Dropping page extraction: clicked page is not yet tracked "
                                    "(likely raced a not-yet-attached popup)"
                                )
                                continue
                            collected = collect_page_data(grabbed_page)
                            if not collected["fields"] and not collected["tables"]:
                                logger.warning(
                                    "Dropping page extraction for page_index=%s url=%s: "
                                    "no fields or tables found",
                                    page_index, grabbed_page.url,
                                )
                                continue
                            pending_extractions.append({
                                "page_index": page_index,
                                "url": grabbed_page.url,
                                "data": collected,
                            })
                        except Exception:
                            # Best-effort side feature: a failed extraction must
                            # never take down the whole recording.
                            logger.exception("Failed to process a page-extraction request")

                with CaptureSession(page) as capture:
                    if _wait_for_stop_or_disconnect(self.stop_event, page, on_tick=_attach_new_pages):
                        capture.mark_browser_disconnected()

                actions = capture.actions

                additional_pages = []
                for extra_page, extra_capture, extra_started_at_ms in extra_captures:
                    try:
                        extra_page.title()
                    except Exception:
                        extra_capture.mark_browser_disconnected()
                    extra_capture.__exit__(None, None, None)
                    additional_pages.append(PageCapture(
                        page_index=len(additional_pages) + 1,
                        actions=extra_capture.actions,
                        video_path=video_path_for_page(extra_page),
                        video_started_at_ms=extra_started_at_ms,
                        video_ended_at_ms=int(time.time() * 1000),
                    ))

                artifact_paths = capture_artifact_paths(context)
                video_path, video_ended_at_ms = finalize_video(context, page)
                try:
                    browser.close()
                except Exception:
                    pass

            codegen_script = None
            try:
                if codegen_path.exists() and codegen_path.stat().st_size > 0:
                    codegen_script = strip_extraction_markers(codegen_path.read_text())
            except Exception:
                # Best-effort side artifact: a failed read must never abort
                # the recording that already happened.
                logger.exception("Failed to read the native codegen script")

            self.recording_id = persist_recording(
                self.org_id, self.start_url, actions,
                request_id=self.request_id, role=self.role, user_id=self.user_id,
                video_path=video_path,
                video_started_at_ms=video_started_at_ms,
                video_ended_at_ms=video_ended_at_ms,
                additional_pages=additional_pages,
                page_extractions=pending_extractions,
                codegen_script=codegen_script,
                capture_artifacts=(
                    [("trace.zip", artifact_paths[0]), ("network.har", artifact_paths[1])]
                    if artifact_paths is not None else None
                ),
            )
        except Exception as exc:  # surfaced to the caller of stop()
            self.error = exc
        finally:
            if video_dir is not None:
                shutil.rmtree(video_dir, ignore_errors=True)
            if codegen_dir is not None:
                shutil.rmtree(codegen_dir, ignore_errors=True)
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
    org_id: str, start_url: str, *, request_id: uuid.UUID | None = None, role: str = "demo",
    user_id: str | None = None,
) -> uuid.UUID:
    session_id = uuid.uuid4()
    session = _LiveCaptureSession(org_id, start_url, request_id, role, user_id)
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
