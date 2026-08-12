# Live Capture Disconnect Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A recording survives the browser disconnecting unexpectedly — most commonly a human closing the window before clicking "Stop and review," but equally a crash — instead of the background capture thread hanging forever waiting for a snapshot from a page that no longer exists, silently losing everything captured so far.

**Architecture:** Playwright's `browser.on("disconnected", ...)` fires the moment the browser process goes away, from Playwright's own internal dispatcher thread. The fix wires that event to the *same* `threading.Event` a normal "Stop and review" click already sets, so a disconnect is treated as an implicit stop rather than a hang. `CaptureSession` gains a `mark_browser_disconnected()` flag so its exit path skips the live-page queries (`wait_for_load_state`, `content()`) that would otherwise block forever on a dead connection, finalizing the last pending action against its own before-state instead. Both call sites that own a browser (`live_capture.py`'s background thread and `cli.py`'s `record` command) get the identical wiring, and video/browser cleanup calls become best-effort (wrapped, not required to succeed) since there's nothing left to close if the disconnect already did it.

**Tech Stack:** Playwright sync API event listeners (`browser.on("disconnected", ...)`) — no new dependencies, no schema changes.

## Global Constraints

- Setting a plain `threading.Event` or a plain Python boolean attribute from Playwright's internal dispatcher thread is safe (these are ordinary Python objects, not Playwright objects) — this does **not** violate the established "only one thread touches Playwright sync objects" rule, which is specifically about calling Playwright *methods* cross-thread, not setting plain flags.
- Cleanup calls (`context.close()`, `browser.close()`) become best-effort (wrapped, exceptions swallowed) — by the time they run, whatever mattered (actions, video file) has already been captured or is already unobtainable; a cleanup call failing must never mask or discard already-captured data.
- No test in this plan kills or force-closes any real OS-level browser process — that class of action is exactly what caused today's data-loss incident when done to the *wrong* process, and Playwright test browsers can't be safely targeted by name/pattern when other Playwright-driven processes (including this project's own other tests, or unrelated browser automation) may be running concurrently on the same machine. The new behavior is tested by driving `CaptureSession`'s disconnect flag directly and by unit-testing the callback wiring in isolation — both exercise the real code path without needing an actual process kill.

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

**Interfaces:**
- Produces: `_LiveCaptureSession._handle_disconnect(capture: CaptureSession) -> None` — marks the capture session disconnected and sets `stop_event`, exactly as if `/stop` had been called. Registered via `browser.on("disconnected", ...)` right after the `CaptureSession` is created.

- [ ] **Step 1: Write the failing test**

This tests the wiring in isolation — no real disconnect or browser needed, since Task 1 already proved `CaptureSession`'s own behavior:

```python
# add to engine/tests/test_live_capture.py
def test_handle_disconnect_marks_capture_and_sets_stop_event():
    from atlas_engine.capture.recorder import CaptureSession
    from atlas_engine.live_capture import _LiveCaptureSession

    session = _LiveCaptureSession("org-id", "https://example.com", None, "demo")
    capture = CaptureSession.__new__(CaptureSession)  # no real page needed for this check
    capture.browser_disconnected = False

    assert not session.stop_event.is_set()
    session._handle_disconnect(capture)

    assert capture.browser_disconnected is True
    assert session.stop_event.is_set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_live_capture.py::test_handle_disconnect_marks_capture_and_sets_stop_event -v`
Expected: FAIL — `AttributeError: '_LiveCaptureSession' object has no attribute '_handle_disconnect'`

- [ ] **Step 3: Implement**

In `engine/src/atlas_engine/live_capture.py`, replace the body of `_run` and add the new method:

```python
    def _run(self) -> None:
        video_dir = None
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=False)
                context, page, video_started_at_ms, video_dir = start_capture_context(browser)
                page.goto(self.start_url)

                with CaptureSession(page) as capture:
                    browser.on("disconnected", lambda _browser: self._handle_disconnect(capture))
                    self.stop_event.wait()

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

    def _handle_disconnect(self, capture: "CaptureSession") -> None:
        """The browser vanished out from under a live capture — most often
        a human closing the window before clicking Stop. Treat it exactly
        like an explicit stop: mark the capture so its exit path won't try
        to query a dead page, then release stop_event so _run proceeds to
        finalize and persist whatever was captured instead of hanging
        forever waiting for a signal that will never come now."""
        capture.mark_browser_disconnected()
        self.stop_event.set()
```

(`CaptureSession` needs to be imported for the type hint — it already is, per the existing `from atlas_engine.capture.recorder import CaptureSession` import line.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_live_capture.py -v`
Expected: PASS (all tests in the file — the new wiring test, plus the two pre-existing tests confirming normal start/stop still works with real browsers unaffected by this change)

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
- Consumes: `CaptureSession.mark_browser_disconnected` (Task 1). No new test: as established in the `recording-video-capture` plan, `atlas record` blocks on `input()` in a real terminal and has never had direct test coverage for that reason — the logic being touched here (`browser.on("disconnected", ...)` calling straight into `capture.mark_browser_disconnected`, no intermediate method needed since the CLI has no `stop_event` to also set) is a strict subset of what Task 1 and Task 3 already cover.

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
            browser.on("disconnected", lambda _browser: capture.mark_browser_disconnected())
            click.echo("Recording... perform your steps in the opened browser, then press Enter here to finish.")
            input()

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

The only changes from the current version: a `browser.on("disconnected", ...)` registration added right after entering the `with CaptureSession(page)` block, and `browser.close()` wrapped in `try/except` to match Task 3's live-capture handling. Playwright calls a `"disconnected"` handler with the browser instance as its one argument — the lambda accepts and discards it (`mark_browser_disconnected` itself takes none). If the operator closes the browser before pressing Enter, they still need to press Enter for `input()` to return (this command's blocking design is unchanged) — but once they do, exit and cleanup won't hang or crash trying to reach a page that's gone.

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

### Task 6: Manual verification and finish branch

**Files:** none (verification task)

- [ ] **Step 1: Restart both dev servers with the fix in place**

The backend process currently running has an orphaned background thread from today's incident, permanently stuck talking to a browser that no longer exists. Restart it (fresh process) so the fix actually takes effect — there is nothing recoverable in that stuck thread; it was already confirmed unsalvageable before this plan started.

- [ ] **Step 2: Reproduce the exact scenario that caused today's incident**

As the operations agent: start a new recording against any real page, close the browser window yourself (don't click Stop first), then go back to the Atlas tab and click "Stop and review." Confirm it now completes promptly with a real recording (video may be partial or absent depending on how much Chromium managed to flush before exiting, but the DOM actions captured up to that point should be there) — instead of hanging for 30 seconds and erroring.

- [ ] **Step 3: Confirm the normal path still works**

Run a full normal record → stop → review cycle (browser left open, stopped via the button as usual) and confirm nothing regressed — video and steps still show up exactly as before.

- [ ] **Step 4: Finish the branch**

**REQUIRED SUB-SKILL:** Use superpowers:finishing-a-development-branch — run the full backend suite and the frontend build/test, present the merge/PR/keep menu, act on the answer.
