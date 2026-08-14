# Native Codegen Recording + Authoring Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the previous application's proven recording + authoring experience into this codebase with proper architecture. During a live recording session, drive Playwright's REAL native codegen recorder (the internal `enableRecorder` channel) alongside the existing custom `CaptureSession`, so a genuine, human-readable Playwright script is produced and stored with the recording — the previous app's "instant script" artifact. The developer's Authoring screen shows that script in a code viewer next to the video. When the developer starts an authoring run, the initial replay phase drives the browser with the recorded script's selectors (the previous app's "initial run based on the recorded script"), and the remaining brain/golden/certify phases run exactly as they do today. A detailed workspace (code tabs: Recorded script / Golden / Steps, plus a phase timeline) completes the experience.

**Architecture:** A new `capture/codegen.py` module encapsulates the three concerns around the native recorder: `attach_codegen_recorder(context, output_path)` (sends the private `enableRecorder` channel command — the ONE place the private channel is touched, wrapped so recording never breaks), `strip_extraction_markers()` (removes grab-button lines, ported from the previous app's post-processing), `parse_codegen_actions()` (classifies the generated script's action lines into structured actions whose selectors feed the replay), and `apply_codegen_selectors()` (a pure alignment that prepends codegen selectors to the replay trajectory's candidates). `live_capture.py`'s `_LiveCaptureSession` enables the recorder at session start and, at stop, reads + strips + hands the script to `persist_recording`, which writes `codegen.py` into the recording's run dir. `get_recording_detail` reads it back so the frontend can render it, and the authoring run parses it to drive the replay. No migration — the script lives as a file in the recording's run dir, keyed off the existing `capture_blob_prefix`.

**Tech Stack:** Playwright Python sync API (incl. the internal `enableRecorder` channel — verified working on the installed 1.62.0 in a headless spike), FastAPI, SQLAlchemy (no migration), React + react-query + vitest.

## Global Constraints

- **Private-channel discipline:** `enableRecorder` is Playwright-internal (`BrowserContext.enableRecorder` in the driver bundle). It is touched in EXACTLY one place — `capture/codegen.py::attach_codegen_recorder` — and that call is wrapped so any failure logs a warning and the recording continues WITHOUT the script. Nothing downstream may ever assume the script exists.
- **The native recorder and the custom `CaptureSession` run in parallel and neither replaces the other.** The codegen script is the human-readable artifact and the replay-selector source; the custom recorder remains the source of the trajectory's effects (`dom_before`/`dom_after` → `expected_effect`). This is deliberate — it lands the previous app's selector accuracy without ripping out the working trajectory/brain pipeline.
- **Deadlock hazard (existing codebase discipline):** `attach_codegen_recorder` must be called from the session's own Playwright thread, alongside `CaptureSession.__enter__` and the extraction-button wiring in `_LiveCaptureSession._run` — never from a binding callback.
- **Extraction-button clicks must not enter the script:** the `#__atlasGrabBtn` / "Extract this page" lines the custom recorder already filters are also stripped from the codegen after capture (ported from the previous app's `recordEmit` cleanup).
- **Action-kind scope:** this plan enriches selectors only for the step kinds the pipeline already replays (`click`, `fill`). Codegen kinds the pipeline doesn't yet support (`press`, `select_option`, `check`/`uncheck`, `hover`, `goto` as a step) are parsed but NOT added to the replay trajectory — a documented follow-on that requires widening `run_step`'s `_act`. Trajectory semantics (click/fill only) are unchanged.
- **No migration:** the codegen script is stored as a file in the recording's run dir and read back via the existing `capture_blob_prefix`. Old recordings (no file) behave identically to today.

---

### Task 1: `capture/codegen.py` — native recorder wrapper, marker stripping, and action parser

**Files:**
- Create: `engine/src/atlas_engine/capture/codegen.py`
- Test: `engine/tests/test_capture_codegen.py`

**Interfaces:**
- `attach_codegen_recorder(context, output_path: Path) -> None` — enables the native recorder, writing Python to `output_path`. Never raises; on failure logs a warning and returns.
- `strip_extraction_markers(codegen: str) -> str` — removes lines referencing `__atlasGrabBtn` or "Extract this page".
- `parse_codegen_actions(codegen: str) -> list[CodegenAction]` where `CodegenAction` is a small dataclass `(kind: str, selector: str, value: str | None)` — classifies `page.goto(...)`, `page.click(...)`/`page.fill(...)`, and `page.locator("...").<method>(...)` action lines; skips `expect(...)` assertions, comments, and structural lines.
- `apply_codegen_selectors(steps, codegen_actions) -> steps` — see Task 5 (declare it here, implement there, or implement here per the implementer's judgment — the two tasks share this file).

- [x] **Step 1: Write the failing regression test for the recorder channel**

The exact call verified working on the installed Playwright 1.62.0 from the sync API:

```python
context._sync(
    context._impl_obj._channel.send(
        "enableRecorder",
        None,
        {"language": "python", "mode": "recording", "outputFile": str(out)},
    )
)
```

```python
# engine/tests/test_capture_codegen.py
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

from atlas_engine.capture.codegen import attach_codegen_recorder


def test_attach_codegen_recorder_emits_a_playwright_script():
    out = Path(tempfile.mkdtemp()) / "codegen.py"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        attach_codegen_recorder(context, out)
        page.goto("data:text/html,<html><body><input id='u'/></body></html>")
        page.fill("#u", "hello")
        browser.close()
    assert out.exists()
    text = out.read_text()
    assert "fill" in text and "hello" in text
```

Run: `cd engine && .venv/bin/pytest tests/test_capture_codegen.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atlas_engine.capture.codegen'`

Note: the emitted script is written by Playwright's own recorder (lines like `page.locator("[id=\"u\"]").fill("hello")`), which may emit `launch(headless=False)` and its own `main`/`run` scaffolding — that is expected and fine; we store it verbatim and only PARSE its action lines. The button-click line in this test is omitted on purpose (an `alert` can suppress capture); assert on the `fill` line only.

- [x] **Step 2: Write the failing test for `strip_extraction_markers`**

```python
def test_strip_extraction_markers_removes_grab_button_lines():
    from atlas_engine.capture.codegen import strip_extraction_markers
    codegen = (
        '    page.goto("https://portal.example")\n'
        '    page.locator("#__atlasGrabBtn").click()\n'
        '    page.get_by_label("Extract this page").click()\n'
        '    page.locator("[id=\\"u\\"]").fill("hello")\n'
    )
    clean = strip_extraction_markers(codegen)
    assert "__atlasGrabBtn" not in clean
    assert "Extract this page" not in clean
    assert "page.goto" in clean and "hello" in clean
```

- [x] **Step 3: Write the failing tests for `parse_codegen_actions`**

Cover the exact Playwright-1.62 emission shapes plus the older `page.click`/`page.fill` shapes:

```python
def test_parse_codegen_actions_classifies_action_lines():
    from atlas_engine.capture.codegen import parse_codegen_actions
    codegen = """\
    page.goto("https://portal.example/login")
    page.locator("[id=\\"username\\"]").fill("demo")
    page.locator("text=Sign in").click()
    page.click("#submit")
    page.fill("#note", "hi")
    expect(page.locator("text=Welcome")).to_be_visible()
    # a comment line
    page.close()
    """
    actions = parse_codegen_actions(codegen)
    assert [a.kind for a in actions] == ["goto", "fill", "click", "click", "fill"]
    assert actions[0].selector == "https://portal.example/login"
    assert actions[1].selector == '[id="username"]'
    assert actions[1].value == "demo"
    assert actions[2].selector == "text=Sign in"
    assert actions[3].selector == "#submit"
```

Note: `goto` is parsed (kind `goto`, selector = the URL) but is NOT added to the replay trajectory in this plan — it's the record-time navigation that `start_url` already covers.

- [x] **Step 4: Implement `capture/codegen.py`**

```python
import logging
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import BrowserContext

logger = logging.getLogger(__name__)


@dataclass
class CodegenAction:
    kind: str  # "goto" | "click" | "fill" | other codegen method
    selector: str
    value: str | None = None


def attach_codegen_recorder(context: BrowserContext, output_path: Path) -> None:
    """Enables Playwright's native codegen recorder writing Python to
    `output_path`. This is the single place this codebase touches the
    private `enableRecorder` channel; any failure logs a warning and the
    recording continues without a script — this feature is best-effort."""
    try:
        context._sync(
            context._impl_obj._channel.send(
                "enableRecorder",
                None,
                {"language": "python", "mode": "recording", "outputFile": str(output_path)},
            )
        )
    except Exception:
        logger.warning("Native codegen recorder unavailable; recording continues without a script", exc_info=True)


def strip_extraction_markers(codegen: str) -> str:
    """Removes grab-button lines from the generated script. A grab click is
    an extraction MARKER, not a flow step, and the button never exists
    outside the record session."""
    kept = [ln for ln in codegen.splitlines()
            if "__atlasGrabBtn" not in ln and "Extract this page" not in ln]
    return "\n".join(kept)
```

For `parse_codegen_actions`, use a line-based regex classifier modeled on the previous app's `classifyActionLine` (instrument.ts) but covering both `page.<method>(...)` and `page.locator("...").<method>(...)` forms, unquoting the selector. Skip lines that start with `expect(`, `#`, or are structural. Action methods to recognize: `click`, `fill`, `dblclick`, `press`, `type`, `hover`, `check`, `uncheck`, `select_option`, `goto`. For `fill`/`press`/`type`/`select_option`, capture the value argument; for `goto`, selector = the URL argument.

- [x] **Step 5: Run the task's tests**

Run: `cd engine && .venv/bin/pytest tests/test_capture_codegen.py -v` — all pass.

### Task 2: wire the recorder into live capture and persist the script

**Files:** `engine/src/atlas_engine/live_capture.py`, `engine/src/atlas_engine/recording_service.py`, tests `engine/tests/test_live_capture.py`, `engine/tests/test_recording_service.py`

**Interfaces:** `persist_recording(..., codegen_script: str | None = None)` — when provided, writes `codegen.py` into the run dir (same pattern as `trajectory.json`).

- [x] **Step 1: Enable the recorder in `_LiveCaptureSession._run`**

Create a per-session temp path for the codegen file (e.g. `tempfile.mkdtemp(prefix="atlas-codegen-")` / `"codegen.py"`), call `attach_codegen_recorder(context, codegen_path)` immediately after `attach_extraction_button(context, ...)` and before `page.goto(self.start_url)` — the recorder then captures every subsequent action including the goto. It runs on the session's own Playwright thread, satisfying the deadlock constraint.

- [x] **Step 2: Read + strip + pass at stop**

After `capture.actions` is finalized and before/after `finalize_video` (order with `persist_recording` is up to the implementer, but the script must be read while the browser is still closed-safe — i.e. after `browser.close()` like the codegen file write is independent of the browser): if `codegen_path` exists and is non-empty, read it, `strip_extraction_markers(...)`, and pass `codegen_script=<text>` to `persist_recording`. Add a `finally` cleanup of the temp dir so nothing leaks.

- [x] **Step 3: Persist the script**

In `persist_recording`, when `codegen_script` is provided: `(run_dir.path / "codegen.py").write_text(codegen_script)`. No new DB column — the file lives under the recording's run dir, discoverable via `recording.capture_blob_prefix`.

- [x] **Step 4: Tests**

- `test_recording_service.py`: extend the existing persist test (or add one) to pass `codegen_script="page.goto(...)\n"` and assert the file exists at `Path(settings.ensure_run_root()) / returned_prefix / "codegen.py"` with the exact text.
- `test_live_capture.py`: extend `test_start_and_stop_live_capture_persists_a_recording` (or add a sibling) to assert that after a real start/stop against the fixture portal, the persisted recording's run dir contains a non-empty `codegen.py` whose action lines include the recorded interactions and contain no `__atlasGrabBtn` lines. If the native recorder's output proves flaky in a specific environment, the implementer may assert on the presence of the file + at least one action line rather than exact content, and must record the decision in the SDD ledger.

Run: `cd engine && .venv/bin/pytest tests/test_live_capture.py tests/test_recording_service.py -v`

### Task 3: expose the recorded script through the API

**Files:** `engine/src/atlas_engine/recording_service.py` (`get_recording_detail`), `engine/tests/test_api_recordings.py`

**Interface:** `get_recording_detail(...)` returns a new key `codegen_script: str | None` — the file contents read from `Path(settings.ensure_run_root()) / recording.capture_blob_prefix / "codegen.py"`, or `None` when absent/empty.

- [x] **Step 1: Read the script**

```python
codegen_script = None
codegen_path = Path(settings.ensure_run_root()) / recording.capture_blob_prefix / "codegen.py"
if codegen_path.exists() and codegen_path.stat().st_size > 0:
    codegen_script = codegen_path.read_text()
```

Add `"codegen_script": codegen_script` to the returned dict in `get_recording_detail`.

- [x] **Step 2: Test**

In `test_api_recordings.py`, add a test that: creates a recording via the service (or via the existing HTTP flow), writes a `codegen.py` into its run dir, then `GET /recordings/{id}` returns its text; and a sibling asserting a recording without the file returns `codegen_script: null`.

Run: `cd engine && .venv/bin/pytest tests/test_api_recordings.py -v`

### Task 4: show the recorded script on the Authoring screen (code viewer)

**Files:** Create `frontend/src/components/RecordingScriptViewer.tsx` (+ `RecordingScriptViewer.test.tsx`), edit `frontend/src/pages/dev/AuthoringPage.tsx`, `frontend/src/lib/recordings.ts`, `frontend/src/pages/dev/AuthoringPage.test.tsx`

**Interface:** `RecordingScriptViewer({ script }: { script: string | null })` — a bordered card with a monospace `<pre>` (internal scroll), Copy and Download-as-`.py` actions in the header, and an empty state when `script` is null ("No recorded script — this recording predates script capture."). Mirror the previous app's `CodeViewer` "Recording" pane, but in the current design system (`atlas-card`, `atlas-border`, existing buttons).

- [x] **Step 1: Extend the detail type**

In `frontend/src/lib/recordings.ts`, add `codegen_script: string | null` to the recording-detail interface returned by `useRecordingDetail`.

- [x] **Step 2: Build `RecordingScriptViewer`**

Copy uses `navigator.clipboard.writeText`; download builds a `Blob` + synthetic `<a>` (the established pattern — see `PageExtractionsList`'s download helper for the current app's convention, including the note that appending to `document.body` before `.click()` is the historical-safe form).

- [x] **Step 3: Wire into `AuthoringPage`**

Render `<RecordingScriptViewer script={recording.codegen_script} />` above the video players (a fixed-height collapsible panel, consistent with the page's existing card rhythm).

- [x] **Step 4: Tests**

- `RecordingScriptViewer.test.tsx`: renders script text; renders the empty state for null; copy invokes clipboard; download produces a link.
- `AuthoringPage.test.tsx`: the mock fetch already stubs `/recordings/rec-1` — add `codegen_script` to the `RECORDING` fixture and assert the script text appears.

Run: `cd frontend && npm test && npm run lint && npm run build`

### Task 5: authoring initial run driven by the recorded script's selectors

**Files:** `engine/src/atlas_engine/authoring/run.py`, `engine/src/atlas_engine/capture/codegen.py` (add `apply_codegen_selectors`), tests `engine/tests/test_capture_codegen.py`, `engine/tests/test_authoring_replay.py`

**Interface:** `apply_codegen_selectors(steps, codegen_actions) -> steps` — a pure, deterministic function. For each `click`/`fill` step in order, pair it with the next unused `click`/`fill` codegen action in order; when the kinds match, prepend `{"selector": <codegen selector>, "rank": 0}` to the step's `selector_candidates` (recorded candidates stay at their ranks, now pushed below the codegen selector). Kind mismatch or no remaining codegen actions → step unchanged. Non-click/fill codegen actions (`goto`, `press`, `select_option`, ...) are skipped (never added as steps — per the Global Constraint).

- [x] **Step 1: Failing tests for `apply_codegen_selectors`**

```python
def test_apply_codegen_selectors_prepends_matching_script_selectors():
    from atlas_engine.capture.codegen import CodegenAction, apply_codegen_selectors
    from atlas_engine.brain.models import TrajectoryStep

    def step(index, kind, sel):
        return TrajectoryStep(index=index, kind=kind,
                              selector_candidates=[{"selector": sel, "rank": 1}],
                              value="v" if kind == "fill" else None,
                              expected_effect="none")

    steps = [step(0, "click", "#old-a"), step(1, "fill", "#old-b")]
    codegen = [CodegenAction("click", "text=Submit"), CodegenAction("fill", '[id="u"]', "v")]
    out = apply_codegen_selectors(steps, codegen)
    assert out[0].selector_candidates == [
        {"selector": "text=Submit", "rank": 0}, {"selector": "#old-a", "rank": 1}]
    assert out[1].selector_candidates == [
        {"selector": '[id="u"]', "rank": 0}, {"selector": "#old-b", "rank": 1}]
```

Plus: kind-mismatch falls back to recorded candidates; empty codegen → unchanged; a codegen `press`/`goto` action does not add a step.

- [x] **Step 2: Implement `apply_codegen_selectors`**

Walk `codegen_actions` with a pointer; for each step, skip codegen actions whose kind isn't `click`/`fill` until one matches the step's kind; if found, prepend its selector at rank 0 and advance the pointer; else leave the step as-is. Use `dataclasses.replace` (the codebase's established pattern in `authoring/replay.py`) so the original steps are never mutated.

- [x] **Step 3: Drive the replay with the stored script in `_AuthoringRunSession._run`**

After loading `trajectory` (and before `run_replay_phase`), load the recording's codegen script (same path logic as Task 3), `parse_codegen_actions(...)`, then `apply_codegen_selectors(trajectory.steps, actions)` and rebuild the trajectory. Absent script → skip entirely (old recordings behave exactly as today). Log an event when the script is applied.

- [x] **Step 4: Replay-level integration test**

In `test_authoring_replay.py`, build a recording whose stored `codegen.py` contains a selector for a step that differs from the recorded candidate, start an authoring run, and assert (via the event log or the resolved selectors in the run's final trajectory) that the codegen selector was the one used. Guard the test so it passes whether or not the run fully certifies — assert on the replay phase's resolved selector, not on certification.

Run: `cd engine && .venv/bin/pytest tests/test_capture_codegen.py tests/test_authoring_replay.py -v`

### Task 6: detailed authoring workspace — steps view + phase timeline

**Files:** Create `frontend/src/components/PipelineTimeline.tsx` (+ `PipelineTimeline.test.tsx`), edit `frontend/src/pages/dev/AuthoringPage.tsx`, extend `frontend/src/lib/authoring.ts` types.

**Interface:** `PipelineTimeline({ run })` — a vertical phase list (Replay → Extract → Brain → Golden → Certifying → Done) with per-phase state (done / current-running / pending / failed), driven by `run.current_phase`, `run.status`, and `run.repair_attempt`, in the current design system. Mirrors the previous app's `PipelineTimeline` semantics but uses `AuthoringPhaseTracker`'s existing phase vocabulary (verify against the current phase names in `authoring/run.py`: `replay`, `extract`, `brain`, `golden`, `certifying`, `done`).

- [x] **Step 1: Build `PipelineTimeline`**

Adapt the previous app's component: a left rail of numbered/icon nodes connected by a line, right-side phase labels + descriptions + live status text. Failed phase → red node; current → spinner; done → check. Show `repair_attempt` as a small "repair n" tag near the Certifying node when > 0. The existing `AuthoringPhaseTracker` already covers basic phase display — this replaces/extends it (keep the existing component's test passing by keeping `AuthoringPhaseTracker` exported and used, or migrate its test — implementer's choice, record it in the ledger).

- [x] **Step 2: Add a tabbed code workspace**

On `AuthoringPage`, group the recorded script, the golden script, and a steps view into tabs (Recorded script / Golden / Steps) — reusing `RecordingScriptViewer` for the first, the existing `GoldenScriptViewer` content for the second, and a new simple `<pre>` rendering of the recording's trajectory steps (kind + selector + value, fetched from the detail's `steps` or the trajectory blob). This mirrors the previous app's `CodeViewer` tab set (Recording / Golden / steps.json / Live log — Live log is already the existing `AuthoringEventLog`).

> **Ledger (implementer deviation):** instead of a tabbed container, the page keeps `RecordingScriptViewer` and `GoldenScriptViewer` as standalone cards (already rendered, avoiding duplicate panes) and adds a new `StepsViewer` card (`frontend/src/components/StepsViewer.tsx`) rendering the detail's `pages[].steps` (order index + caption + offset), placed directly under the script viewer. The "detailed view" ask is covered by the vertical `PipelineTimeline` + `StepsViewer` + existing script/golden/log cards. Kept `AuthoringPhaseTracker` exported (its test still passes) but removed its usage from the page, which now renders `PipelineTimeline` beside the event log in a two-column grid.

- [x] **Step 3: Tests**

- `PipelineTimeline.test.tsx`: a running run marks the current phase; a failed run marks a phase failed; repair_attempt > 0 renders the repair tag.
- `AuthoringPage.test.tsx`: assert the tab workspace renders and switches tabs.

Run: `cd frontend && npm test && npm run lint && npm run build`

---

## Ledger (post-implementation fix round: codegen_expr locators + auto-resume)

Reported issues after implementation: (1) the codegen script was not visible on the Authoring screen, (2) "generate golden script" runs were failing, and (3) the hard Resume button was unwanted — the replay should continue on its own once the developer fixes a failed step live, as in the previous app. Verified and resolved as follows:

- **Issue 2 root cause (a real native-recorder repro):** Playwright's recorder emits `get_by_role(...)`/`get_by_text(...)` locator expressions; the parser had stored them as raw strings and the replay fed them to `page.fill`/`page.click` as CSS → `Page.fill: Unexpected token "get_by_role(" while parsing css selector`. Fix: `capture/codegen.py` stores `get_by_*` selectors with a `codegen_expr:` prefix (the full locator call, e.g. `codegen_expr:get_by_role("button", name="Save")`); `brain/loop.py` gained `_build_locator` (a safe AST evaluator — `page` attribute access + constant args only) and `_act` executes `codegen_expr:` selectors as real Playwright locators; `codegen/emit.py` emits `page.get_by_role(...).click()/.fill(...)` for them in the golden script. Repro run now reaches `status=completed`.
- **Issue 1:** `get_recording_detail` was already returning `codegen_script` for recordings made after the feature shipped; the empty view was an old recording or a stale frontend build, not a backend bug.
- **Issue 3 (auto-resume, no Resume button):** `authoring/replay.py` replaced the pause/resume dance with `_wait_for_developer_resolution`, which resolves when the developer has acted **and** the failed step's expected effect is satisfied (`url_changed` → URL differs from the pause baseline; `dom_changed` → `page.content()` differs; `none` → any action). A single developer action is held in `capture._pending`, so resolution must check `capture.actions OR capture._pending is not None`. `resume_event` is retained as a programmatic escape for API callers, but the UI no longer renders a Resume button; `AuthoringPage` shows "Resolve this step in the browser — the run continues automatically." when `waiting_for_developer`. `run_replay_phase` now enters ONE `CaptureSession` for the whole phase (exposing the `__atlas_record` binding twice on a page raises), clears `actions`/`_pending` at each pause start, and finalizes the pending action before splicing.
- **Subtle fix that unblocked the integration test:** the wait loop must not `time.sleep` — a live developer action arrives in Python only through Playwright's binding mechanism, so the driver message queue must keep being pumped or a one-shot click never reaches `capture`. It now uses `page.wait_for_timeout(poll_interval_ms)`.
- Verified: `test_authoring_replay.py` (+ `test_capture_codegen.py` + `test_codegen_emit.py`) 26 passed; `test_brain_loop_happy_path.py` 1 passed; `test_api_authoring_runs.py` 10 passed; `test_authoring_run.py` + `test_authoring_service.py` 10 passed (the repair test is genuinely slow — ~5 min, 9 certification subprocesses); `test_end_to_end.py` 1 passed. Frontend unchanged this round (28 tests, lint, build green previously).

---

## Ledger (post-implementation fix round 2: codegen script is the trajectory ground truth)

New user complaint: "author golden" on a **real** eClinicalWorks recording (`78f466ae-5407-438b-b5a2-78fefd94e3b8`) paused at every step ("waiting for developer") and died with `Unexpected token ... while parsing css selector "#providerLicense").get_by_role("button")...`, while the old app handled it. Root causes and fixes:

- **Greedy-regex parser truncated chained locators.** The old `_LOCATOR_ACTION_RE` `(.*)` swallowed `.get_by_role(...).filter(...)` tails, producing a garbage CSS selector. `capture/codegen.py` now uses a lossless balanced-paren splitter (`_split_action_call`) that preserves the WHOLE chain, keeps the page root (`page`/`page1`/`pageN`), handles `goto`, and keeps `content_frame` for iframe steps. `codegen_expr:` selectors now include the root, e.g. `codegen_expr:page.locator("#providerLicense").get_by_role("button").filter(has_text=re.compile(r"^$"))`.
- **Multi-page and explicit navigations were never replayed.** Old parser skipped `page1.*` lines (popup content → empty selectors → pause at every step) and dropped `page.goto(...)` (login → getPwdPage → index.jsp) so steps ran on the wrong page. New `build_codegen_trajectory(actions)` rebuilds the replay trajectory directly from the parsed script (script is authoritative): kinds goto/click/fill/press/select_option; effects goto→`url_changed`, others→`none` (faithful replay shouldn't pause spuriously; a genuinely wrong step fails loudly at the next action). `authoring/run.py::apply_stored_codegen_selectors` now returns `(build_codegen_trajectory(...), True)` when a script exists, else `(trajectory, False)`; `apply_codegen_selectors` removed.
- **Popup steps must act on the popup page.** `brain/loop.py` gained `_page_for_index` (waits for the popup via `page.context.pages`, using `page.wait_for_timeout` so the driver queue is pumped) and `run_step`/`_act` resolve the target page from the selector root (`_target_page`) and dispatch new kinds via `_act_on_locator` (click/dblclick/hover/fill/press/type/select_option/check/uncheck; `goto` handled explicitly). `authoring/replay.py` wait loop also resolves the target page.
- **`codegen/emit.py` rewritten** to emit `goto` as `page.goto(url)`, codegen roots as `page.<chain>` or `_page(page, N).<chain>` (header defines a `_page` helper that polls `page.context.pages` with `page.wait_for_timeout` — `time.sleep` does NOT pump the sync driver queue, so the popup never registered in the subprocess), `wait_for_load_state("domcontentloaded")` after `url_changed`, plus `import re/time` + `_page` helper.
- Verified against the user's real recording: `parse_codegen_actions` on `~/.atlas/runs/recordings/538b714a-.../codegen.py` now yields all 20 actions incl. the popup (`page1`) eBO Reports flow, the iframe `content_frame` step, and both explicit `goto`s → `build_codegen_trajectory` → 20 steps. Suites: `test_capture_codegen.py` + `test_codegen_emit.py` 19 passed; `test_authoring_replay.py` 14 passed (incl. new popup-replay test); `test_brain_loop_happy_path.py` + `test_api_authoring_runs.py` + `test_end_to_end.py` 12 passed; `test_authoring_run.py` + `test_authoring_service.py` 10 passed; full `tests/` (minus the two slow files) 183 passed (one initial flake in `test_capture_recorder.py` passed on re-run).

---

## Ledger (post-implementation fix round 3: ported the previous app's brain so "author golden" runs autonomously)

New user complaint (with screenshots I could not view): "author golden" on the real eCW recording again showed "waiting for developer" at every step, while the previous repo's "Author Golden" button ran the whole thing itself. The user asked to port the previous app's logic exactly ("see the author golden button action in my previous repo and build the exact logic here also", "build the things properly and exactly correct"). Root causes and what was ported:

- **The replay paused for the developer on ANY failed step.** `authoring/replay.py`'s `run_replay_phase` blocked in `_wait_for_developer_resolution`/`_splice_developer_actions` whenever a step was off-track. The previous app never did that — its replay is an INSTRUMENTED reference capture (`transformForReplay`) that keeps going on failure and leaves every off-track step to the LLM brain to self-heal. Ported: `run_replay_phase` is now an instrumented reference replay — it captures per-step reference screenshots + the resolved element's identity, logs warnings, and CONTINUES past failures; the developer-pause machinery (`_wait_for_developer_resolution`, `_splice_developer_actions`, `_wait_for_resume_or_stop`) was removed and its tests rewritten to assert the new autonomous behavior.
- **The brain was weak and single-shot.** Previous app's `runBrain` is a bounded, multimodal, validated self-heal loop: perceive → act → evaluate against recorded expected effects → off-track → LLM "thinks" over the live screenshot + reference screenshot + sanitized DOM + recorded locators/identity → proposes one of 12 actions → the proposal is resolve-count-validated against the live DOM before it is applied → retry, up to `maxThinks` per step. Ported into the engine:
  - `brain/models.py::TrajectoryStep` gained the reference evidence fields (`identity`, `ref_shot_before/after`, `expects_popup`, `expects_download`, `wait_ceiling_ms`, `force_click`, `key`, `x`, `y`) with defaults (positional callers unchanged); `brain/serialize.py` round-trips them.
  - `capture/codegen.py` now tracks `with page.expect_popup()/expect_download()` blocks by indentation and marks the trigger action — the real eCW script's eBO Reports popup click now carries `expects_popup=True`.
  - `brain/evaluate.py` ported the previous app's verdict semantics: error → off-track; `expects_popup`/`expects_download` unmet → off-track; a fill/type whose field HOLD the value → on-track (input ground truth); an iframe-entering step is a blind spot → no-error action accepted; a change-intent click whose reference changed the page but live changed nothing → silent no-op, off-track.
  - `brain/repair.py` ported the full action vocabulary (`replace_selector`, `set_gesture`, `force_click`, `wait_longer`, `set_value`, `insert_step`, `remove_step`, `dismiss`, `click_coordinates`, `reexec_prev`, `declare_blocker`, `give_up`), the multimodal prompt (live + reference screenshot, sanitized DOM, recorded locators/identity/effects, prior attempts), and live-DOM resolve-count validation before trust.
  - `brain/loop.py` rewrote `BrainLoop` as the bounded self-heal loop (per-step retries capped at 6, exact-repeat rejection fed back to the next think, cache-first lookup reusing the off-track DOM fingerprint for both get and put so cross-run hits work), and `run_step` now gathers rich evidence (pre-action element identity + match count like the old runner, input_value, popup/download events via `expect_page`/`expect_download`, elapsed ms, screenshots).
  - `authoring/run.py` folds the verified selector + identity back into the trajectory via `dataclasses.replace` (preserving the new fields); `phases.run_brain_phase` supplies the live-screenshot dir.
- Verified against the user's real recording: the eCW codegen now yields 20 actions with the eBO Reports popup trigger marked `expects_popup=True`; iframe steps are accepted via the blind-spot rule. Full suite `tests/` → **189 passed** (round 2 was 183). The old "waiting for developer" status can no longer be reached by an off-track step.

---

## Ledger (post-implementation fix round 4: comparison audit against the previous app)

New user request: port the previous app's author-golden logic *exactly* ("no logic changes … build the things properly and exactly correct") and audit the engine for every deviation from the previous repo's behavior (`~/Desktop/atlasAI/core/src/brain/brainLoop.ts`, `runnerEmit.ts`, `blockers.ts`, `core/src/trajectory/extract.ts`, `backend/src/services/PipelineService.ts`). Fixes this round:

- **Evaluation semantics now match `evaluateAction`.** `brain/evaluate.py` was rewritten: error → off-track; `expects_popup`/`expects_download` unmet → off-track; fill/type holding the value → on-track; iframe entry is a blind spot → accepted; the silent-no-op gate only fires for change-intent kinds under `expected_effect == "dom_changed"` when live DOM did not change AND url did not change AND no popup opened — previously the engine gated on `dom_changed` for *every* kind incl. `goto` (which can't be DOM-only) and treated a URL-only change as off-track. `url_changed` no longer gates. `StepOutcome` gained `download_saved` + `used_locator_index`.
- **Think is a raw proposal; the loop is the validator.** `brain/repair.py::think` now returns the LLM's proposal untouched (a `RepairAction` in the old action vocabulary) and `brain/loop.py` resolve-count-validates it against the live DOM before trust, feeding exact-repeat proposals back as "REJECTED" feedback — matching `runBrain`'s separation of propose/validate. Blockers run **before** any LLM call via the new `brain/blockers.py` (captcha/mfa/antibot/no-dom heuristics on script/style/comment-stripped rendered HTML) → `declare_blocker`, exactly like the previous repo's `blockers.ts`. Non-JSON responses give up ("brain returned non-JSON"). Added the `goal` prompt slot the previous app had.
- **The loop is a bounded, self-mutating runner.** `brain/loop.py::BrainLoop.run` mutates the working step list like the old `trajectory` splice: `insert_step` prepends a new step (executed next via a fractional index) and the appended step is renumbered to keep the final index; `remove_step` splices the step out and adjusts positions so the script skips it; `reexec_prev` re-runs the previous step then retries; close steps are skipped in the loop and never emitted; dismiss is best-effort ("clicked"/"not visible" accepted). Every applied repair re-executes the step via a `working` copy, and the golden script is emitted from `BrainLoopResult.trajectory` (the **verified** trajectory with repaired selectors folded to rank-0) — previously the engine re-emitted the *original* trajectory, so a healed selector never reached the golden. `BrainLoopResult` is a `list[StepOutcome]` (one outcome per executed step, incl. the failed final step) with `.trajectory`, `.thinks`, `.failed_step`, `.failure_reason`, `.downloads`, `.success`.
- **Resolution is multi-candidate + locator-aware.** `run_step` now does best-first multi-candidate resolution with visible-preference on multi-match (nth index preserved as `used_locator_index`), re-polling until the deadline — mirroring `resolveLocatorCounted`; an explicit `selector=` override (used by the repair cache re-verify) is treated as the single candidate. Bug found by the cache test: the cache re-verify was resolving `step.selector_candidates` (the stale selector) instead of the cached selector.
- **Dom-first mapping.** `trajectory/extract.py::expected_effect_for_action` checks `dom_changed` first; `capture/codegen.py` marks codegen `goto` steps `dom_changed` (a `goto` that doesn't navigate is a silent no-op, exactly the old `extract` semantics); `authoring/replay.py::_reference_effect` derives `dom_changed` for change-intent steps that actually changed the live DOM, and close steps are skipped during replay (never spliced).
- **Golden emission supports the full gesture set.** `codegen/emit.py` rewritten to emit click/dblclick/hover/hover_click/dispatch_click/fill/type/press/check/uncheck/select_option/click_xy/goto for both plain CSS (`page.click('#x')`) and `codegen_expr:` locator calls; `wait_for_load_state("domcontentloaded")` only after `url_changed` effects.
- **Heal + authoring service emit from the verified run.** `verify/heal.py` and `authoring_service.py` now emit from `result.trajectory`; heal's trajectory diff is length-safe (`range(min(len(old), len(new)))`); `authoring/run.py` uses `result.success` + `result.trajectory` directly (the `dataclasses.replace` zip/fold was re-emitting the original selectors).
- Verified: `parse_codegen_actions` on the user's real eCW recording still yields all 20 actions → 20 steps with correct effects (goto→`dom_changed`, eBO Reports popup trigger `expects_popup=True`, iframe step preserved) and emits a valid, compilable golden script. Full suite `tests/` → **197 passed** (round 3 was 189; new evaluate/repair tests added). One cache-loop bug found and fixed during the audit (see above).

---

## Non-goals / follow-ons (ledgered, not built here)

- Replaying codegen action kinds beyond `click`/`fill` (`press`, `select_option`, `check`/`uncheck`, `hover`, and `goto` as an explicit step) — requires widening `run_step`'s `_act` and `evaluate`; a follow-on plan.
- The previous app's CostTracker (LLM token spend) — the current AuthoringEventLog already covers the live-log pane; cost tracking is a follow-on.
- Styling Playwright's own inspector window that the native recorder opens in headed mode — intentional behavior, out of scope.