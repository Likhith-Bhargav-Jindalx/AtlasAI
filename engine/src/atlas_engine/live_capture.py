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
from atlas_engine.capture.video import finalize_video, start_capture_context, video_path_for_page
from atlas_engine.pending_recordings import write_pending_recording
from atlas_engine.recording_service import PageCapture

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
    # stop_event fired -- but the browser may have already died in the same
    # window before the explicit stop arrived (e.g. a human closes the
    # window, then clicks Stop). Trusting "not disconnected" here without a
    # real check sends the caller (CaptureSession.__exit__, finalize_video)
    # down live Playwright calls against an already-dead page, which is
    # what actually hung for minutes rather than failing fast in production.
    try:
        page.title()
    except Exception:
        return True
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

    def __init__(
        self, org_id: str, start_url: str, request_id: uuid.UUID | None, role: str, user_id: str | None = None,
        persist_fn=write_pending_recording,
    ):
        self.org_id = org_id
        self.start_url = start_url
        self.request_id = request_id
        self.role = role
        self.user_id = user_id
        self.persist_fn = persist_fn
        self.stop_event = threading.Event()
        self.done_event = threading.Event()
        self.result_id: uuid.UUID | None = None
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

                # page.on("popup") fires ONLY for a page spawned as a side
                # effect of this specific page's own click (window.open,
                # target=_blank, cmd/ctrl+click) -- unlike context.on("page")
                # above, which fires for ANY new page regardless of cause.
                # That makes it the reliable signal for telling a popup apart
                # from a tab the operator opened themselves (Cmd+T, or one
                # already open at recording start): a popup will be spawned
                # again by replaying the same click, so the golden script
                # must just wait for it; a manually-opened tab never will be,
                # so the golden script must open and navigate it itself (see
                # codegen/emit.py and brain/loop.py's run_step).
                popup_pages: set = set()
                page.on("popup", lambda popup: popup_pages.add(popup))

                extra_captures: list[tuple] = []
                pending_extractions: list[dict] = []

                def _attach_new_pages() -> None:
                    while new_pages:
                        new_page = new_pages.pop(0)
                        new_page.on("popup", lambda popup: popup_pages.add(popup))  # this tab can itself spawn popups
                        started_at_ms = int(time.time() * 1000)
                        capture = CaptureSession(new_page)
                        capture.__enter__()
                        origin = "popup" if new_page in popup_pages else "manual"

                        # A manually-opened tab is typically still blank
                        # (about:blank, chrome://newtab/) the instant it
                        # appears, and an operator may take any amount of
                        # time to type a destination into it. Recording the
                        # URL on a fixed timeout either fires too early
                        # (captures the blank page, breaking every later
                        # replayed step on this tab) or blocks this whole
                        # polling loop -- including the stop/health-check in
                        # _wait_for_stop_or_disconnect -- for as long as the
                        # bound. Event-driven instead: fires exactly once,
                        # whenever the tab actually lands somewhere real, no
                        # deadline either way.
                        recorded = {"done": False}

                        def _record_real_navigation(
                            frame, _page=new_page, _capture=capture, _recorded=recorded, _origin=origin,
                        ) -> None:
                            if _recorded["done"] or frame != _page.main_frame:
                                return
                            if frame.url in ("about:blank", "chrome://newtab/") or frame.url.startswith("chrome://"):
                                return
                            _recorded["done"] = True
                            try:
                                _capture.record_tab_opened(frame.url, "", origin=_origin)
                            except Exception:
                                logger.exception("Failed to record tab_opened for a new tab")

                        new_page.on("framenavigated", _record_real_navigation)

                        # Playwright fills every positional-or-keyword lambda
                        # param from the event's own args (regardless of a
                        # default) up to however many the lambda declares --
                        # a lambda with ONLY `capture` as its one param gets
                        # the closed Page object bound to it instead,
                        # clobbering the intended default. Giving the real
                        # event arg its own leading slot leaves the other
                        # defaults (this loop iteration's values, not
                        # whatever gets reassigned by a later iteration)
                        # untouched.
                        def _on_close(
                            _closed_page, _page=new_page, _capture=capture, _recorded=recorded, _origin=origin,
                        ) -> None:
                            # Closed before ever navigating anywhere real (the
                            # operator opened it and changed their mind) --
                            # tab_closed must still pair with a tab_opened, so
                            # fall back to whatever URL it had, even if blank.
                            if not _recorded["done"]:
                                _recorded["done"] = True
                                try:
                                    _capture.record_tab_opened(_page.url, "", origin=_origin)
                                except Exception:
                                    logger.exception("Failed to record tab_opened for a new tab")
                            _capture.record_tab_closed()

                        new_page.on("close", _on_close)
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

            self.result_id = self.persist_fn(
                self.org_id, self.start_url, actions,
                request_id=self.request_id, role=self.role, user_id=self.user_id,
                video_path=video_path,
                video_started_at_ms=video_started_at_ms,
                video_ended_at_ms=video_ended_at_ms,
                additional_pages=additional_pages,
                page_extractions=pending_extractions,
                codegen_script=codegen_script,
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
        return self.result_id


def start_live_capture(
    org_id: str, start_url: str, *, request_id: uuid.UUID | None = None, role: str = "demo",
    user_id: str | None = None, persist_fn=write_pending_recording,
) -> uuid.UUID:
    session_id = uuid.uuid4()
    session = _LiveCaptureSession(org_id, start_url, request_id, role, user_id, persist_fn=persist_fn)
    with _registry_lock:
        _sessions[session_id] = session
    session.start()
    return session_id


def stop_live_capture(capture_session_id: uuid.UUID, *, timeout: float = 120.0) -> uuid.UUID:
    """Persisting a recording uploads video/trace/har/actions to whatever
    store is configured — real Azure Blob uploads over a real network can
    legitimately take much longer than a local disk write, so a single
    /stop call can time out even though the capture thread is still
    working, not stuck. The session is only removed from the registry
    once stop() actually completes (success or a real persist error) —
    NOT on a timeout — so a second /stop call (e.g. the user clicking
    "Stop and review" again after seeing an error) finds the same
    still-running session and just keeps waiting on the same done_event,
    instead of getting "No active capture session" for a recording that
    was never actually lost."""
    with _registry_lock:
        session = _sessions.get(capture_session_id)
    if session is None:
        raise ValueError(f"No active capture session: {capture_session_id}")
    try:
        return session.stop(timeout=timeout)
    finally:
        # Only deregister once actually done (success or a real persist
        # error) -- a TimeoutError means it's still running (most likely
        # still uploading), so leave it registered for a retried /stop
        # call to keep waiting on instead of erroring.
        if session.done_event.is_set():
            with _registry_lock:
                _sessions.pop(capture_session_id, None)
