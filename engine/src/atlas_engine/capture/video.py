import tempfile
import time
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page


# Capture artifacts belong to a browser context, while the public capture API
# intentionally returns the familiar `(context, page, started_at_ms, dir)` tuple.
# Keep the paths here rather than changing that API (and every live-capture
# caller); artifacts are copied into durable recording storage before `dir` is
# removed.
_CAPTURE_ARTIFACTS: dict[int, tuple[Path, Path]] = {}


def start_capture_context(browser: Browser) -> tuple[BrowserContext, Page, int, Path]:
    """Opens a browser context with video recording enabled and returns it
    with its one page, the wall-clock time recording began (epoch ms —
    used to align recorded-action timestamps to the video), and the temp
    directory Playwright is writing the video into. The caller removes
    that directory once the path returned by finalize_video has been
    consumed (persist_recording reads it into permanent storage)."""
    video_dir = Path(tempfile.mkdtemp(prefix="atlas-video-"))
    trace_path = video_dir / "trace.zip"
    har_path = video_dir / "network.har"
    context = browser.new_context(
        record_video_dir=str(video_dir), record_har_path=str(har_path), accept_downloads=True,
    )
    _CAPTURE_ARTIFACTS[id(context)] = (trace_path, har_path)
    try:
        context.tracing.start(screenshots=True, snapshots=True, sources=False)
    except Exception:
        # Video/codegen recording remains useful if a browser backend does not
        # expose tracing; live capture must not fail before the operator starts.
        pass
    started_at_ms = int(time.time() * 1000)
    page = context.new_page()
    return context, page, started_at_ms, video_dir


def capture_artifact_paths(context: BrowserContext) -> tuple[Path, Path] | None:
    """Returns the trace/HAR paths allocated for this capture context.

    The paths are temporary until `persist_recording()` copies them to the
    recording's storage prefix. Call this before `finalize_video()`, which
    closes the context and flushes both Playwright artifacts.
    """
    return _CAPTURE_ARTIFACTS.get(id(context))


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
        artifacts = _CAPTURE_ARTIFACTS.get(id(context))
        if artifacts is not None:
            context.tracing.stop(path=str(artifacts[0]))
    except Exception:
        pass
    try:
        context.close()
    except Exception:
        pass
    finally:
        _CAPTURE_ARTIFACTS.pop(id(context), None)
    ended_at_ms = int(time.time() * 1000)
    return video_path, ended_at_ms
