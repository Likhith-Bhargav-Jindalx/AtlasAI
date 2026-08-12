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
    consumed (persist_recording reads it into permanent storage)."""
    video_dir = Path(tempfile.mkdtemp(prefix="atlas-video-"))
    context = browser.new_context(record_video_dir=str(video_dir))
    started_at_ms = int(time.time() * 1000)
    page = context.new_page()
    return context, page, started_at_ms, video_dir


def finalize_video(context: BrowserContext, page: Page) -> tuple[Path | None, int]:
    """Closes the context — which flushes the recorded video to disk —
    and returns its path (None if this page had no video recording) plus
    the wall-clock time recording finished (epoch ms)."""
    video_path = Path(page.video.path()) if page.video else None
    context.close()
    ended_at_ms = int(time.time() * 1000)
    return video_path, ended_at_ms
