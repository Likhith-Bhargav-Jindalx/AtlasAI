# Live Capture Disconnect Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A recording survives the browser disconnecting unexpectedly — most commonly a human closing the window before clicking "Stop and review," but equally a crash — instead of the background capture thread hanging forever waiting for a snapshot from a page that no longer exists, silently losing everything captured so far.

**Architecture:** The obvious-looking approach — wiring Playwright's `browser.on("disconnected", ...)` event to the same `threading.Event` a normal "Stop and review" click sets — was tried first and empirically disproven (three standalone probe scripts against this project's real Playwright install): that event fires promptly for a *self-initiated* `browser.close()`, but never fires at all, even after 15 seconds, when the browser process is killed externally — which is exactly the human-closes-the-window scenario this plan exists to fix. `browser.is_connected()` was checked too and found to be a stale client-side flag, not a live probe. The only reliable, fast (~1s) signal is *attempting* a real operation against the page (`page.title()`) and catching the `TargetClosedError` it raises once the browser is truly gone. The fix polls with exactly that call instead of waiting on an event. `CaptureSession` gains a `mark_browser_disconnected()` flag so its exit path skips the live-page queries (`wait_for_load_state`, `content()`) that would otherwise block forever on a dead connection, finalizing the last pending action against its own before-state instead. `live_capture.py`'s background thread polls continuously (it runs unattended for the life of the recording); `cli.py`'s `record` command — which blocks synchronously on `input()` with no room to poll — does a single health check the moment `input()` returns, using the same call. Video/browser cleanup calls become best-effort (wrapped, not required to succeed) since there's nothing left to close if the disconnect already did it.

**Tech Stack:** Playwright sync API (`page.title()` as a liveness probe) — no new dependencies, no schema changes.

## Global Constraints

- Don't trust a Playwright API's apparent purpose without checking it against this project's actual installed version first — `browser.on("disconnected", ...)` reads as exactly the right tool for this job and isn't; `is_connected()` reads as a liveness check and isn't one. Both were verified with small throwaway scripts before being ruled out, not from documentation or memory alone.
- Cleanup calls (`context.close()`, `browser.close()`) become best-effort (wrapped, exceptions swallowed) — by the time they run, whatever mattered (actions, video file) has already been captured or is already unobtainable; a cleanup call failing must never mask or discard already-captured data.
- Automated tests in this plan never kill or force-close a real OS-level browser process — that class of action is exactly what caused today's data-loss incident when done to the *wrong* process, and Playwright test browsers can't be safely targeted by name/pattern when other Playwright-driven processes (this project's own other tests, or unrelated browser automation) may be running concurrently on the same machine. The polling logic is tested by driving `_wait_for_stop_or_disconnect` directly with a mocked page (Task 3) and `CaptureSession`'s exit behavior directly (Task 1) — both exercise the real code path without needing an actual process kill. (Manual verification in Task 6 does kill a real, precisely-identified process, deliberately and with the operator watching — that's a one-time human-supervised check, not something committed as an automated test.)

---

### Task 1: `CaptureSession` tolerates a disconnected browser on exit

**Files:**
- Modify: `engine/src/atlas_engine/capture/recorder.py`
- Test: `engine/tests/test_capture_recorder.py`

**Interfaces:**
- Produces: `CaptureSession.mark_browser_disconnected() -> None` and `CaptureSession.browser_disconnected: bool` (read-only via a property, or a plain public attribute — either is fine, tests should check whichever is chosen). When set, `__exit__` finalizes any pending action using its own `url_before`/`dom_before` as both before and after (no real after-state is knowable from a dead page) instead of calling `self.page.wait_for_load_state(...)`/`self.page.content()`.

- [ ] **Step 1: Write the failing test**

Check the existing test file first — `engine/tests/test_capture_recorder.py` — to match its existing fixture/style (it already exercises `CaptureSession` against the real fixture portal). Add:

```python
def test_exit_after_browser_disconnected_finalizes_without_querying_the_page(fixture_portal_url):
    from playwright.sync_api import sync_playwright

    from atlas_engine.capture.recorder import CaptureSession

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        capture = CaptureSession(page)
        capture.__enter__()
        page.fill("#username", "demo")  # leaves one action pending

        capture.mark_browser_disconnected()
        assert capture.browser_disconnected is True

        # Must not hang or raise trying to query a page we're pretending is
        # already gone — and must not need the page to still work at all.
        capture.__exit__(None, None, None)

        browser.close()

    assert len(capture.actions) == 1
    action = capture.actions[0]
    assert action.url_after == action.url_before
    assert action.dom_after == action.dom_before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engine && .venv/bin/pytest tests/test_capture_recorder.py::test_exit_after_browser_disconnected_finalizes_without_querying_the_page -v`
Expected: FAIL — `AttributeError: 'CaptureSession' object has no attribute 'mark_browser_disconnected'`

- [ ] **Step 3: Implement**

```python
class CaptureSession:
    def __init__(self, page):
        self.page = page
        self.actions: list[RecordedAction] = []
        self._pending: dict | None = None
        self.browser_disconnected = False

    def __enter__(self) -> "CaptureSession":
        self.page.expose_binding("__atlas_record", self._on_event)
        self.page.add_init_script(_LISTENER_SCRIPT)
        self.page.evaluate(_LISTENER_SCRIPT)  # attach to the already-loaded document too
        return self

    def mark_browser_disconnected(self) -> None:
        """The browser process is gone — e.g. a human closed the window
        before clicking Stop, or it crashed. __exit__ can no longer ask a
        live page for a fresh snapshot; anything already pending gets
        finalized against its own before-state instead of blocking on a
        Playwright call that will never return."""
        self.browser_disconnected = True

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.browser_disconnected:
            if self._pending is not None:
                self._finalize_pending(
                    url_after=self._pending["url_before"], dom_after=self._pending["dom_before"]
                )
            return
        self.page.wait_for_load_state("domcontentloaded")
        self._finalize_pending(url_after=self.page.url, dom_after=self.page.content())
```

(`_finalize_pending`, `_on_event`, `_selector_candidates` and the module-level `_LISTENER_SCRIPT` are unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_capture_recorder.py -v`
Expected: PASS (all tests in the file, including the new one and the pre-existing ones)

- [ ] **Step 5: Commit**

```bash
git add src/atlas_engine/capture/recorder.py tests/test_capture_recorder.py
git commit -m "feat: CaptureSession tolerates a disconnected browser on exit"
```

---

### Task 2: `finalize_video` never blocks on cleanup

**Files:**
- Modify: `engine/src/atlas_engine/capture/video.py`
- Test: `engine/tests/test_capture_video.py`

**Interfaces:**
- Produces: `finalize_video` keeps its existing signature and return type (`tuple[Path | None, int]`) — no caller changes needed. `context.close()` becomes best-effort: a failure (e.g. the browser already disconnected on its own) is swallowed rather than propagated, since by this point the video path is already known and there is nothing meaningful left to fail.

- [ ] **Step 1: Write the failing test**

```python
# add to engine/tests/test_capture_video.py
def test_finalize_video_tolerates_a_context_that_is_already_closed(fixture_portal_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context, page, started_at_ms, video_dir = start_capture_context(browser)
        try:
            page.goto(f"{fixture_portal_url}/login.html")
            context.close()  # simulates the browser having already gone away

            # Must not raise even though the context is already closed.
            video_path, ended_at_ms = finalize_video(context, page)

            assert ended_at_ms >= started_at_ms
            browser.close()
        finally:
            shutil.rmtree(video_dir, ignore_errors=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_capture_video.py::test_finalize_video_tolerates_a_context_that_is_already_closed -v`
Expected: FAIL — `context.close()` inside `finalize_video` raises on an already-closed context, propagating out of the function.

- [ ] **Step 3: Implement**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_capture_video.py -v`
Expected: PASS (both tests in the file)

- [ ] **Step 5: Commit**

```bash
git add src/atlas_engine/capture/video.py tests/test_capture_video.py
git commit -m "feat: finalize_video tolerates an already-closed context"
```

---

### Task 3: Wire disconnect detection into live recording

**Files:**
- Modify: `engine/src/atlas_engine/live_capture.py`
- Test: `engine/tests/test_live_capture.py`

**Design correction from the original draft of this plan:** `browser.on("disconnected", ...)` was verified empirically (three standalone probe scripts, run against this project's actual Playwright install) to fire promptly for a *self-initiated* `browser.close()` but **never fires** — not even after 15 seconds — when the browser process is killed externally (`kill` or `kill -9`), which is exactly the human-closes-the-window scenario this plan exists to fix. `browser.is_connected()` was also checked and found to just return a stale client-side flag (`True` for the full 15-second probe window after the process was already dead) — useless as a detection signal. The only mechanism that reliably and quickly (~1s in testing) detects a truly dead browser is *attempting* a real operation against it (e.g. `page.title()`) and catching the resulting `TargetClosedError`. The fix below polls with exactly that call instead of relying on an event that doesn't fire for this case.

**Interfaces:**
- Produces: `_wait_for_stop_or_disconnect(stop_event: threading.Event, page, *, poll_interval: float = 1.0) -> bool` — blocks until either `stop_event` is set (returns `False`, a normal stop) or a `page.title()` health check raises (returns `True`, the browser died). `poll_interval` is a parameter specifically so tests can drive this in milliseconds instead of real seconds.

- [ ] **Step 1: Write the failing test**

```python
# add to engine/tests/test_live_capture.py
from unittest.mock import Mock

def test_wait_for_stop_or_disconnect_detects_a_dead_page():
    from atlas_engine.live_capture import _wait_for_stop_or_disconnect

    stop_event = threading.Event()  # never set
    page = Mock()
    page.title.side_effect = Exception("Target page, context or browser has been closed")

    disconnected = _wait_for_stop_or_disconnect(stop_event, page, poll_interval=0.01)

    assert disconnected is True


def test_wait_for_stop_or_disconnect_returns_false_on_explicit_stop():
    from atlas_engine.live_capture import _wait_for_stop_or_disconnect

    stop_event = threading.Event()
    stop_event.set()  # already stopped, as if /stop had just been called
    page = Mock()

    disconnected = _wait_for_stop_or_disconnect(stop_event, page, poll_interval=0.01)

    assert disconnected is False
    page.title.assert_not_called()
```

Add `import threading` at the top of `test_live_capture.py` if it isn't already imported there (check first — `live_capture.py` itself already imports it, but the test file may not).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_live_capture.py -v -k wait_for_stop_or_disconnect`
Expected: FAIL — `ImportError: cannot import name '_wait_for_stop_or_disconnect'`

- [ ] **Step 3: Implement**

In `engine/src/atlas_engine/live_capture.py`, add a module-level function and replace the body of `_run`:

```python
def _wait_for_stop_or_disconnect(stop_event: threading.Event, page, *, poll_interval: float = 1.0) -> bool:
    """Blocks until stop_event is set (an explicit /stop call) or the
    browser stops responding to a cheap health check (it died on its own —
    most often a human closing the window before clicking Stop) —
    whichever happens first. Returns True if it was the browser that died.

    Playwright's own "disconnected" event does NOT fire for this case —
    verified empirically: it fires promptly for a self-initiated
    browser.close(), but not within 15 seconds of the process being
    killed externally. browser.is_connected() is equally useless, since
    it just reflects a stale client-side flag rather than probing the
    live connection. Only an actual operation against the page reliably
    detects a truly dead browser, so that's what this polls with."""
    while not stop_event.wait(timeout=poll_interval):
        try:
            page.title()
        except Exception:
            return True
    return False


class _LiveCaptureSession:
    ...  # __init__ unchanged

    def _run(self) -> None:
        video_dir = None
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=False)
                context, page, video_started_at_ms, video_dir = start_capture_context(browser)
                page.goto(self.start_url)

                with CaptureSession(page) as capture:
                    if _wait_for_stop_or_disconnect(self.stop_event, page):
                        capture.mark_browser_disconnected()

                actions = capture.actions
                video_path, video_ended_at_ms = finalize_video(context, page)
                try:
                    browser.close()
                except Exception:
                    pass

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
```

(Only `_run`'s body changes — `__init__`, `start`, `stop` are unchanged. Show the plan reader the full class only to make clear nothing else moves; the diff is just the one method.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_live_capture.py -v`
Expected: PASS (the two new polling-logic tests, plus the two pre-existing tests confirming normal start/stop still works end-to-end with a real browser)

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/atlas_engine/live_capture.py tests/test_live_capture.py
git commit -m "feat: recover a recording when the browser disconnects during live capture"
```

---

### Task 4: Apply the same wiring to `atlas record` (CLI)

**Files:**
- Modify: `engine/src/atlas_engine/cli.py`

**Interfaces:**
- Consumes: `CaptureSession.mark_browser_disconnected` (Task 1). No new test: as established in the `recording-video-capture` plan, `atlas record` blocks on `input()` in a real terminal and has never had direct test coverage for that reason. Unlike Task 3, the CLI has no background polling loop to fix, since `input()` blocks the one and only thread synchronously — there's nothing else running that could poll while it waits. The fix here is a single health check performed once `input()` returns, using the exact mechanism (`page.title()`, catching the exception) that Task 3's `_wait_for_stop_or_disconnect` proved actually works.

- [ ] **Step 1: Implement**

Replace the `record` command's body in `engine/src/atlas_engine/cli.py`:

```python
def record(url: str, org_id: str, request_id: str | None, role: str) -> None:
    """Open a headed browser at URL, capture actions until you press Enter,
    persist the recording (with video), and print its ID."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context, page, video_started_at_ms, video_dir = start_capture_context(browser)
        page.goto(url)

        with CaptureSession(page) as capture:
            click.echo("Recording... perform your steps in the opened browser, then press Enter here to finish.")
            input()
            try:
                page.title()
            except Exception:
                capture.mark_browser_disconnected()

        video_path, video_ended_at_ms = finalize_video(context, page)
        try:
            browser.close()
        except Exception:
            pass

    try:
        recording_id = persist_recording(
            org_id, url, capture.actions,
            request_id=uuid.UUID(request_id) if request_id else None,
            role=role,
            video_path=video_path,
            video_started_at_ms=video_started_at_ms,
            video_ended_at_ms=video_ended_at_ms,
        )
    finally:
        shutil.rmtree(video_dir, ignore_errors=True)
    click.echo(f"Recording saved: {recording_id}")
```

The only changes from the current version: a `page.title()` health check (swallowing any exception into `capture.mark_browser_disconnected()`) right after `input()` returns, and `browser.close()` wrapped in `try/except` to match Task 3's live-capture handling. If the operator closes the browser before pressing Enter, they still need to press Enter for `input()` to return (this command's blocking design is unchanged) — but once they do, the health check catches the dead browser before exit tries (and fails) to query it.

- [ ] **Step 2: Run the full suite to confirm nothing else broke**

Run: `.venv/bin/pytest -q`
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add src/atlas_engine/cli.py
git commit -m "feat: apply browser-disconnect resilience to atlas record CLI"
```

---

### Task 5: Surface stop failures in the frontend instead of failing silently

**Files:**
- Modify: `frontend/src/pages/agent/LiveRecordingPage.tsx`

**Interfaces:**
- Produces: a visible error message when `POST /recordings/{id}/stop` fails, instead of the button silently resetting with no feedback (today's actual symptom — the click did register and error, but nothing told the user).

- [ ] **Step 1: Implement**

```tsx
// frontend/src/pages/agent/LiveRecordingPage.tsx
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { MonitorPlay, Square } from 'lucide-react'
import { useAuth } from '../../auth/AuthContext'
import { ApiError } from '../../lib/api'
import { useStartRecording, useStopRecording } from '../../lib/recordings'

export function LiveRecordingPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const startUrl = searchParams.get('url') ?? ''
  const requestId = searchParams.get('requestId') ?? undefined

  const startRecording = useStartRecording()
  const stopRecording = useStopRecording()
  const [captureSessionId, setCaptureSessionId] = useState<string | null>(null)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [stopError, setStopError] = useState<string | null>(null)
  const startedRef = useRef(false)

  useEffect(() => {
    if (startedRef.current || !startUrl) return
    startedRef.current = true
    startRecording
      .mutateAsync({ org_id: user!.org_id, start_url: startUrl, request_id: requestId, role: 'demo' })
      .then((result) => setCaptureSessionId(result.capture_session_id))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startUrl])

  useEffect(() => {
    if (!captureSessionId) return
    const interval = setInterval(() => setElapsedSeconds((s) => s + 1), 1000)
    return () => clearInterval(interval)
  }, [captureSessionId])

  async function handleStop() {
    if (!captureSessionId) return
    setStopError(null)
    try {
      const result = await stopRecording.mutateAsync(captureSessionId)
      navigate(`/agent/recordings/${result.recording_id}`)
    } catch (err) {
      setStopError(err instanceof ApiError ? err.message : 'Could not stop the recording. If you closed the browser window, wait a moment and try again.')
    }
  }

  const minutes = Math.floor(elapsedSeconds / 60)
  const seconds = elapsedSeconds % 60
  const elapsed = `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`

  return (
    <div className="min-h-screen bg-[#0d0d0c] flex flex-col items-center px-10 pt-8 pb-10 gap-6 box-border">
      <div className="w-full max-w-[1080px] flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="w-2.5 h-2.5 rounded-full bg-atlas-danger animate-pulse" />
          <span className="text-white text-sm font-medium tracking-tight">Recording</span>
          <span className="text-atlas-body text-[13px]">{startUrl}</span>
        </div>
        <div className="text-white text-[22px] tabular-nums tracking-wide">{elapsed}</div>
      </div>

      <div className="w-full max-w-[1080px] aspect-video rounded-[18px] overflow-hidden border border-[#2a2a28] bg-[#141413] flex flex-col items-center justify-center gap-3.5 text-[#5c5c56]">
        <MonitorPlay size={34} />
        <div className="text-[13px]">Live capture in progress in a separate browser window</div>
        <div className="text-xs text-[#3f3f3b]">Switch to it, perform the flow, then come back and stop here.</div>
      </div>

      <button
        onClick={handleStop}
        disabled={!captureSessionId || stopRecording.isPending}
        className="flex items-center gap-2.5 bg-atlas-danger hover:bg-atlas-dangerHover text-white px-[26px] py-3.5 rounded-full text-sm font-semibold disabled:opacity-50"
      >
        <Square size={11} fill="white" />
        {stopRecording.isPending ? 'Stopping…' : 'Stop and review'}
      </button>

      {stopError && <div className="text-[13px] text-atlas-danger">{stopError}</div>}
    </div>
  )
}
```

- [ ] **Step 2: Run the frontend build and tests**

Run: `cd frontend && npm run build && npm run test`
Expected: clean build, all tests pass (this page has no dedicated test file today, consistent with the rest of the page components — its logic is now the same shape as `ApprovalsPage`'s already-tested error-surfacing pattern)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/agent/LiveRecordingPage.tsx
git commit -m "feat: surface stop-recording failures instead of failing silently"
```

---

### Task 6: `persist_recording` rejects an empty video file

**Files:**
- Modify: `engine/src/atlas_engine/recording_service.py`
- Test: `engine/tests/test_recording_service.py`

**Found during manual verification of Task 3:** when the browser is killed before it can flush any frames to the `ffmpeg` process muxing the video (a `SIGKILL`-abrupt disconnect leaves no time for that), Playwright still pre-allocates the video file path, so `video_path.exists()` is `True` even though the file is 0 bytes. `persist_recording` was treating that as a valid video — the recording detail endpoint reported `has_video: true` for a file with nothing playable in it. The fix is one extra condition: a video only counts if it has actual bytes.

**Interfaces:**
- Produces: no signature change — `persist_recording` now checks `video_path.stat().st_size > 0` in addition to `video_path.exists()` before creating a `VideoAsset`/setting `duration_seconds`. A recording whose video never got any frames now correctly reports `has_video: false`, same as if no `video_path` had been passed at all.

- [ ] **Step 1: Write the failing test**

```python
# add to engine/tests/test_recording_service.py
def test_persist_recording_ignores_an_empty_video_file(tmp_path, fixture_portal_url):
    video_file = tmp_path / "session.webm"
    video_file.touch()  # exists, but zero bytes — as if the browser died before any frame was muxed

    with session_scope() as session:
        org = Organization(name="Empty Video Org", slug=f"empty-video-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = persist_recording(
        org_id, f"{fixture_portal_url}/login.html", [],
        video_path=video_file, video_started_at_ms=0, video_ended_at_ms=1000,
    )

    with session_scope() as session:
        assert session.query(VideoAsset).filter_by(recording_id=recording_id).one_or_none() is None
        recording = session.get(Recording, recording_id)
        assert recording.duration_seconds is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engine && .venv/bin/pytest tests/test_recording_service.py::test_persist_recording_ignores_an_empty_video_file -v`
Expected: FAIL — a `VideoAsset` row gets created for the empty file today.

- [ ] **Step 3: Implement**

In `engine/src/atlas_engine/recording_service.py`, change the video-detection condition:

```python
    if video_path is not None and video_path.exists() and video_path.stat().st_size > 0:
        video_blob_ref = run_dir.write_bytes("video.webm", video_path.read_bytes())
        if video_started_at_ms is not None and video_ended_at_ms is not None:
            duration_seconds = max(0, round((video_ended_at_ms - video_started_at_ms) / 1000))
```

(Only the `if` condition's third clause, `and video_path.stat().st_size > 0`, is new — the body is unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_recording_service.py -v`
Expected: PASS (all tests in the file, including this new one and the pre-existing video test with a real non-empty file)

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/atlas_engine/recording_service.py tests/test_recording_service.py
git commit -m "fix: persist_recording ignores a video file that exists but has no frames"
```

---

### Task 7: Manual verification and finish branch

**Files:** none (verification task)

- [ ] **Step 1: Restart both dev servers with the fix in place**

The backend process currently running has an orphaned background thread from today's incident, permanently stuck talking to a browser that no longer exists. Restart it (fresh process) so the fix actually takes effect — there is nothing recoverable in that stuck thread; it was already confirmed unsalvageable before this plan started.

- [ ] **Step 2: Reproduce the exact scenario that caused today's incident**

As the operations agent: start a new recording against any real page, close the browser window yourself (don't click Stop first), then go back to the Atlas tab and click "Stop and review." Confirm it now completes promptly with a real recording — instead of hanging for 30 seconds and erroring. A recording killed this abruptly may legitimately have `has_video: false` (Task 6) since Chromium never got to flush any frames; that's correct behavior, not a bug, and is why Task 7's next step (a normal stop) is what proves video capture itself still works.

- [ ] **Step 3: Confirm the normal path still works**

Run a full normal record → stop → review cycle (browser left open, stopped via the button as usual) and confirm nothing regressed — video and steps still show up exactly as before.

- [ ] **Step 4: Finish the branch**

**REQUIRED SUB-SKILL:** Use superpowers:finishing-a-development-branch — run the full backend suite and the frontend build/test, present the merge/PR/keep menu, act on the answer.
