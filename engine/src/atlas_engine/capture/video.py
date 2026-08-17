import tempfile
import time
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page


def start_capture_context(browser: Browser) -> tuple[BrowserContext, Page, int, Path]:
    """Opens a browser context with video recording enabled and returns it
    with its one page, the wall-clock time recording began (epoch ms —
    used to align recorded-action timestamps to the video), and the temp
    directory Playwright is writing the video into. The caller removes
    that directory once the path returned by finalize_video has been
    consumed (persist_recording reads it into permanent storage).

    Does NOT capture a network HAR or Playwright trace — both were
    confirmed unused by every automated step (extract/brain/golden/
    certify/heal); the only consumer was a human manually opening one in
    a HAR viewer or Trace Viewer, which didn't justify capturing (and
    then uploading, at tens of MB each) on every single recording."""
    video_dir = Path(tempfile.mkdtemp(prefix="atlas-video-"))
    context = browser.new_context(record_video_dir=str(video_dir), accept_downloads=True)
    started_at_ms = int(time.time() * 1000)
    page = context.new_page()
    return context, page, started_at_ms, video_dir


def video_path_for_page(page: Page) -> Path | None:
    """Reads a page's video path without touching its context — safe to
    call once per page when a context has more than one (e.g. a tab/popup
    opened during the recording). context.close() only needs to happen
    once, after every page's path has already been read this way."""
    return Path(page.video.path()) if page.video else None


def finalize_video(context: BrowserContext, page: Page) -> tuple[Path | None, int]:
    """Closes the context — which flushes the recorded video to disk —
    and returns its path (None if this page had no video recording) plus
    the wall-clock time recording finished (epoch ms). Closing is
    best-effort: if the browser already disconnected on its own (e.g. a
    human closed the window before stopping the recording), the context
    is already gone and there is nothing left to close — that's not a
    failure worth propagating, since the video path itself doesn't
    require a live connection to read."""
    video_path = Path(page.video.path()) if page.video else None
    try:
        context.close()
    except Exception:
        pass
    ended_at_ms = int(time.time() * 1000)
    return video_path, ended_at_ms
