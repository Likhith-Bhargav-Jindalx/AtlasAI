# Authoring Pipeline Orchestration Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the actual `replay -> extract -> brain -> golden -> certifying` orchestration that drives an `AuthoringRun` from start to finish in a dedicated background thread, including the developer-intervention pause/resume path in `replay` and the repair loop back into `brain` on partial certification.

**Architecture:** A new `atlas_engine.authoring` package. Each phase is a small, independently-testable function reusing existing engine internals (`brain.loop.run_step`/`BrainLoop`, `codegen.emit.emit_golden_script`, `verify.certify`, `capture.recorder.CaptureSession`) — nothing here reimplements replay/repair logic that already exists and is tested. The top-level `_AuthoringRunSession` (in `authoring/run.py`) mirrors `live_capture.py`'s `_LiveCaptureSession` exactly: one background thread owns every Playwright call for the run's lifetime; the outside world (a future API layer, not part of this plan) only ever communicates through DB rows (`AuthoringRun`/`AuthoringRunEvent`/`CertificationRun`) and two `threading.Event`s (resume, stop).

**Tech Stack:** Python 3.14, Playwright sync API, SQLAlchemy, pytest against a real headed Chromium and the fixture portal at `engine/tests/fixtures/fixture_portal/`.

**Spec:** `docs/superpowers/specs/2026-08-13-authoring-pipeline-rebuild-design.md`

## Global Constraints

- Only one thread ever touches Playwright method calls for a given phase's browser session. Event/binding callbacks that fire from Playwright's own internal dispatcher thread (e.g. `CaptureSession`'s `expose_binding` handler) must do nothing but trivial thread-safe operations (list append) — this is already how `CaptureSession` is written; do not change that.
- `replay` and `brain` each launch their own **headed** (`headless=False`) browser, independently, exactly like `authoring_service.py`/`verify/heal.py` already launch one browser per call — one `with sync_playwright() as playwright: ... browser.close()` block per phase invocation, not a browser shared across phases.
- `certifying` runs the frozen `.py` golden script **headless**, via subprocess, exactly like the existing `certify_golden_script` already does.
- Every phase/step transition and certification-run result must append an `AuthoringRunEvent` row. Detail level is phase/step transitions only (not full DOM/prompt dumps) — matches the spec's confirmed log-detail choice.
- Stop cancels the run immediately regardless of phase, including while paused waiting for a developer, and marks `AuthoringRun.status = "cancelled"` with no golden script produced.
- This plan does **not** add any FastAPI endpoints or frontend code — purely the orchestration module and its own tests. A later plan wires `start_authoring_run`/`resume_authoring_run`/`stop_authoring_run` (this plan's public interface) into the API.
- Certification runs 3 times by default; on a non-fully-passing result the run loops back into `brain` for up to 2 repair attempts (matching `AuthoringRun.repair_attempt`) before finalizing as `certified` (all 3 passed) or `partially_certified` (some passed) — never a hard failure at this stage, only `replay`/`brain` themselves can produce `status = "failed"`.

---

### Task 1: Run-state and event-logging helpers

**Files:**
- Create: `engine/src/atlas_engine/authoring/__init__.py` (empty — makes this a package)
- Create: `engine/src/atlas_engine/authoring/state.py`
- Create: `engine/src/atlas_engine/authoring/events.py`
- Test: `engine/tests/test_authoring_state.py`

**Interfaces:**
- Produces: `update_run(authoring_run_id: uuid.UUID, **fields) -> None` — generic setter for any `AuthoringRun` column, raises `ValueError` if the run doesn't exist. `log_event(authoring_run_id: uuid.UUID, *, phase: str, message: str, step_index: int | None = None, level: str = "info") -> None` — appends one `AuthoringRunEvent` row.

- [ ] **Step 1: Write the failing tests**

```python
# engine/tests/test_authoring_state.py
import uuid

import pytest

from atlas_engine.authoring.events import log_event
from atlas_engine.authoring.state import update_run
from atlas_engine.db import session_scope
from atlas_engine.db.models import AuthoringRun, AuthoringRunEvent, Organization, PortalProfile, Recording


def _make_authoring_run(fixture_portal_url) -> uuid.UUID:
    with session_scope() as session:
        org = Organization(name="Authoring State Org", slug=f"authoring-state-org-{uuid.uuid4()}")
        profile = PortalProfile(org=org, domain="portal-state.example.com")
        recording = Recording(
            org=org, portal_profile=profile, status="captured", capture_blob_prefix="orgs/x/recordings/1/",
        )
        run = AuthoringRun(org=org, recording=recording, status="running", current_phase="replay")
        session.add_all([org, profile, recording, run])
        session.flush()
        return run.id


def test_update_run_sets_arbitrary_fields(fixture_portal_url):
    authoring_run_id = _make_authoring_run(fixture_portal_url)

    update_run(authoring_run_id, status="waiting_for_developer", repair_attempt=1)

    with session_scope() as session:
        run = session.get(AuthoringRun, authoring_run_id)
        assert run.status == "waiting_for_developer"
        assert run.repair_attempt == 1


def test_update_run_raises_for_unknown_run():
    with pytest.raises(ValueError, match="No such authoring run"):
        update_run(uuid.uuid4(), status="cancelled")


def test_log_event_appends_a_row(fixture_portal_url):
    authoring_run_id = _make_authoring_run(fixture_portal_url)

    log_event(authoring_run_id, phase="replay", step_index=2, level="warning", message="Step 2 failed")

    with session_scope() as session:
        events = session.query(AuthoringRunEvent).filter_by(authoring_run_id=authoring_run_id).all()
        assert len(events) == 1
        assert events[0].phase == "replay"
        assert events[0].step_index == 2
        assert events[0].level == "warning"
        assert events[0].message == "Step 2 failed"


def test_log_event_defaults_to_info_level_and_no_step_index(fixture_portal_url):
    authoring_run_id = _make_authoring_run(fixture_portal_url)

    log_event(authoring_run_id, phase="golden", message="Emitted golden script")

    with session_scope() as session:
        event = session.query(AuthoringRunEvent).filter_by(authoring_run_id=authoring_run_id).one()
        assert event.level == "info"
        assert event.step_index is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd engine && .venv/bin/pytest tests/test_authoring_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atlas_engine.authoring'`

- [ ] **Step 3: Write the implementation**

```python
# engine/src/atlas_engine/authoring/__init__.py
```//empty file

```python
# engine/src/atlas_engine/authoring/state.py
import uuid

from atlas_engine.db import session_scope
from atlas_engine.db.models import AuthoringRun


def update_run(authoring_run_id: uuid.UUID, **fields) -> None:
    """Generic setter for AuthoringRun columns — every phase uses this to
    record its own status/current_phase/repair_attempt/golden_script_id/
    ended_at transitions, so the DB row is always the single source of
    truth a future API layer polls."""
    with session_scope() as session:
        run = session.get(AuthoringRun, authoring_run_id)
        if run is None:
            raise ValueError(f"No such authoring run: {authoring_run_id}")
        for key, value in fields.items():
            setattr(run, key, value)
```

```python
# engine/src/atlas_engine/authoring/events.py
import uuid

from atlas_engine.db import session_scope
from atlas_engine.db.models import AuthoringRunEvent


def log_event(
    authoring_run_id: uuid.UUID, *, phase: str, message: str, step_index: int | None = None, level: str = "info",
) -> None:
    """Appends one row to the run's event log — the source the future
    /authoring-runs/{id} polling endpoint and the UI's scrolling log both
    read from. `seq` is a Postgres IDENTITY column, assigned automatically
    on insert; callers never set it."""
    with session_scope() as session:
        session.add(AuthoringRunEvent(
            authoring_run_id=authoring_run_id, phase=phase, step_index=step_index, level=level, message=message,
        ))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_authoring_state.py -v`
Expected: PASS (4/4)

- [ ] **Step 5: Commit**

```bash
git add engine/src/atlas_engine/authoring/__init__.py engine/src/atlas_engine/authoring/state.py engine/src/atlas_engine/authoring/events.py engine/tests/test_authoring_state.py
git commit -m "feat: add AuthoringRun state and event-logging helpers"
```

---

### Task 2: `certify_golden_script_once` with failure evidence

**Files:**
- Modify: `engine/src/atlas_engine/verify/certify.py`
- Test: `engine/tests/test_verify_certify.py`

**Interfaces:**
- Produces: `certify_golden_script_once(script_path: Path, *, args: list[str] | None = None) -> tuple[bool, str | None]` — runs the script once from a fresh process; returns `(True, None)` on success or `(False, combined_stdout_and_stderr)` on failure. Does not touch the existing `certify_golden_script`/`CertificationResult` — both keep working exactly as today for `authoring_service.py`/`verify/heal.py`.

- [ ] **Step 1: Write the failing tests**

Add to `engine/tests/test_verify_certify.py`:

```python
def test_certify_golden_script_once_returns_true_and_no_evidence_on_success(tmp_path, fixture_portal_url):
    from atlas_engine.verify.certify import certify_golden_script_once

    script_path = _write_fixture_golden(tmp_path, fixture_portal_url)

    passed, evidence = certify_golden_script_once(script_path)

    assert passed is True
    assert evidence is None


def test_certify_golden_script_once_returns_false_and_evidence_on_failure(tmp_path):
    from atlas_engine.verify.certify import certify_golden_script_once

    script_path = tmp_path / "broken.py"
    script_path.write_text("import sys\nprint('boom', file=sys.stderr)\nsys.exit(1)\n")

    passed, evidence = certify_golden_script_once(script_path)

    assert passed is False
    assert "boom" in evidence
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd engine && .venv/bin/pytest tests/test_verify_certify.py -v -k once`
Expected: FAIL — `ImportError: cannot import name 'certify_golden_script_once'`

- [ ] **Step 3: Add the function**

Append to `engine/src/atlas_engine/verify/certify.py`:

```python
def certify_golden_script_once(script_path: Path, *, args: list[str] | None = None) -> tuple[bool, str | None]:
    """Runs a golden script once from a fresh process. Unlike
    certify_golden_script (which only returns an aggregate pass count),
    this exposes per-run failure evidence — the authoring orchestrator
    needs one CertificationRun row per attempt, each with its own
    evidence when it fails."""
    result = subprocess.run(
        [sys.executable, str(script_path), *(args or [])],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode == 0:
        return True, None
    return False, (result.stdout or "") + (result.stderr or "")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_verify_certify.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add engine/src/atlas_engine/verify/certify.py engine/tests/test_verify_certify.py
git commit -m "feat: add certify_golden_script_once for per-run certification tracking"
```

---

### Task 3: Trajectory-splicing and resume/stop wait primitives

**Files:**
- Modify: `engine/src/atlas_engine/trajectory/extract.py`
- Create: `engine/src/atlas_engine/authoring/replay.py`
- Test: `engine/tests/test_trajectory_extract.py`
- Test: `engine/tests/test_authoring_replay.py`

**Interfaces:**
- Consumes: `RecordedAction` from `atlas_engine.capture.events`, `TrajectoryStep`/`ReferenceTrajectory` from `atlas_engine.brain.models`.
- Produces: `expected_effect_for_action(action: RecordedAction) -> str` (renamed from the private `_expected_effect`, now reusable outside `trajectory/extract.py`). `_wait_for_resume_or_stop(resume_event: threading.Event, stop_event: threading.Event, *, poll_interval: float = 0.5) -> str` — returns `"resumed"` or `"stopped"`. `_splice_developer_actions(steps: list[TrajectoryStep], failed_index: int, developer_actions: list[RecordedAction]) -> list[TrajectoryStep]` — replaces the step at `failed_index` with the developer's action(s), re-indexed sequentially.

- [ ] **Step 1: Rename the private helper to a public one**

In `engine/src/atlas_engine/trajectory/extract.py`, this function is currently used only internally:

```python
def _expected_effect(action: RecordedAction) -> str:
```

Rename it (and its one call site) to:

```python
def expected_effect_for_action(action: RecordedAction) -> str:
    if action.url_before != action.url_after:
        return "url_changed"
    if action.dom_before != action.dom_after:
        return "dom_changed"
    return "none"


def extract_trajectory(actions: list[RecordedAction]) -> ReferenceTrajectory:
    """Deterministic, no LLM — a pure function of the recorded actions.
    Calling this twice on the same input must produce identical output."""
    steps = tuple(
        TrajectoryStep(
            index=action.index,
            kind=action.kind,
            selector_candidates=action.selector_candidates,
            value=action.value,
            expected_effect=expected_effect_for_action(action),
        )
        for action in actions
    )
    return ReferenceTrajectory(steps=steps)
```

- [ ] **Step 2: Run the existing extract tests to confirm the rename didn't break anything**

Run: `cd engine && .venv/bin/pytest tests/test_trajectory_extract.py -v`
Expected: PASS (unchanged — nothing outside this file referenced the private name)

- [ ] **Step 3: Write the failing tests for the new primitives**

```python
# engine/tests/test_authoring_replay.py
import threading
import time

from atlas_engine.authoring.replay import _splice_developer_actions, _wait_for_resume_or_stop
from atlas_engine.brain.models import TrajectoryStep
from atlas_engine.capture.events import RecordedAction


def test_wait_for_resume_or_stop_returns_resumed_when_resume_event_set():
    resume_event = threading.Event()
    stop_event = threading.Event()

    def _resume_soon():
        time.sleep(0.05)
        resume_event.set()

    threading.Thread(target=_resume_soon).start()

    result = _wait_for_resume_or_stop(resume_event, stop_event, poll_interval=0.02)

    assert result == "resumed"


def test_wait_for_resume_or_stop_returns_stopped_when_stop_event_set():
    resume_event = threading.Event()
    stop_event = threading.Event()

    def _stop_soon():
        time.sleep(0.05)
        stop_event.set()

    threading.Thread(target=_stop_soon).start()

    result = _wait_for_resume_or_stop(resume_event, stop_event, poll_interval=0.02)

    assert result == "stopped"


def test_wait_for_resume_or_stop_clears_resume_event_before_returning():
    resume_event = threading.Event()
    stop_event = threading.Event()
    resume_event.set()

    result = _wait_for_resume_or_stop(resume_event, stop_event, poll_interval=0.02)

    assert result == "resumed"
    assert not resume_event.is_set()


def test_splice_developer_actions_replaces_the_failed_step():
    steps = [
        TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
        TrajectoryStep(1, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
        TrajectoryStep(2, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
    ]
    developer_actions = [
        RecordedAction(
            index=0, kind="click", selector_candidates=[{"selector": "#run-report-btn", "rank": 0}], value=None,
            url_before="https://x/dashboard.html", url_after="https://x/dashboard.html",
            dom_before="<p>before</p>", dom_after="<p>after</p>",
        ),
    ]

    result = _splice_developer_actions(steps, 2, developer_actions)

    assert len(result) == 3
    assert result[0].index == 0 and result[0].kind == "fill"
    assert result[1].index == 1 and result[1].kind == "click"
    assert result[2].index == 2
    assert result[2].selector_candidates == [{"selector": "#run-report-btn", "rank": 0}]
    assert result[2].expected_effect == "dom_changed"


def test_splice_developer_actions_supports_multiple_actions_for_one_failed_step():
    steps = [
        TrajectoryStep(0, "click", [{"selector": "#a", "rank": 0}], None, "none"),
        TrajectoryStep(1, "click", [{"selector": "#b", "rank": 0}], None, "none"),
    ]
    developer_actions = [
        RecordedAction(
            index=0, kind="click", selector_candidates=[{"selector": "#c1", "rank": 0}], value=None,
            url_before="https://x/", url_after="https://x/", dom_before="1", dom_after="2",
        ),
        RecordedAction(
            index=1, kind="click", selector_candidates=[{"selector": "#c2", "rank": 0}], value=None,
            url_before="https://x/", url_after="https://x/", dom_before="2", dom_after="2",
        ),
    ]

    result = _splice_developer_actions(steps, 1, developer_actions)

    assert [s.index for s in result] == [0, 1, 2]
    assert result[1].selector_candidates == [{"selector": "#c1", "rank": 0}]
    assert result[1].expected_effect == "dom_changed"
    assert result[2].selector_candidates == [{"selector": "#c2", "rank": 0}]
    assert result[2].expected_effect == "none"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd engine && .venv/bin/pytest tests/test_authoring_replay.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atlas_engine.authoring.replay'`

- [ ] **Step 5: Write the implementation**

```python
# engine/src/atlas_engine/authoring/replay.py
import dataclasses
import threading

from atlas_engine.brain.models import TrajectoryStep
from atlas_engine.capture.events import RecordedAction
from atlas_engine.trajectory.extract import expected_effect_for_action


def _wait_for_resume_or_stop(
    resume_event: threading.Event, stop_event: threading.Event, *, poll_interval: float = 0.5,
) -> str:
    """Blocks until either the developer clicks Resume or the run is
    stopped, whichever happens first. Polling (rather than a composite
    wait) because Python's threading has no built-in "wait for any of
    several events" primitive — poll_interval bounds how quickly a stop
    takes effect while paused."""
    while True:
        if stop_event.wait(timeout=poll_interval):
            return "stopped"
        if resume_event.is_set():
            resume_event.clear()
            return "resumed"


def _splice_developer_actions(
    steps: list[TrajectoryStep], failed_index: int, developer_actions: list[RecordedAction],
) -> list[TrajectoryStep]:
    """Replaces the step at failed_index with whatever the developer did
    instead — one action or several — and re-indexes every step in the
    result sequentially from 0, since the original per-step indices no
    longer describe a contiguous position once a step has been replaced
    by a different number of actions."""
    before = steps[:failed_index]
    after = steps[failed_index + 1:]
    converted = [
        TrajectoryStep(
            index=0,
            kind=action.kind,
            selector_candidates=action.selector_candidates,
            value=action.value,
            expected_effect=expected_effect_for_action(action),
        )
        for action in developer_actions
    ]
    combined = list(before) + converted + list(after)
    return [dataclasses.replace(step, index=i) for i, step in enumerate(combined)]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_authoring_replay.py -v`
Expected: PASS (6/6)

- [ ] **Step 7: Commit**

```bash
git add engine/src/atlas_engine/trajectory/extract.py engine/src/atlas_engine/authoring/replay.py engine/tests/test_trajectory_extract.py engine/tests/test_authoring_replay.py
git commit -m "feat: add trajectory-splicing and resume/stop wait primitives"
```

---

### Task 4: `run_replay_phase` — deterministic replay with developer-intervention pause

**Files:**
- Modify: `engine/src/atlas_engine/authoring/replay.py`
- Test: `engine/tests/test_authoring_replay.py`

**Interfaces:**
- Consumes: `_wait_for_resume_or_stop`, `_splice_developer_actions` from Task 3. `run_step` from `atlas_engine.brain.loop`. `log_event`, `update_run` from Task 1. `CaptureSession` from `atlas_engine.capture.recorder`.
- Produces: `run_replay_phase(page, trajectory: ReferenceTrajectory, authoring_run_id: uuid.UUID, resume_event: threading.Event, stop_event: threading.Event) -> ReferenceTrajectory | None` — returns the (possibly developer-corrected) trajectory, or `None` if the run was stopped while replaying or while paused.

- [ ] **Step 1: Write the failing tests**

Add to `engine/tests/test_authoring_replay.py`:

```python
import time
import uuid

from playwright.sync_api import sync_playwright

from atlas_engine.authoring.replay import run_replay_phase
from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.db import session_scope
from atlas_engine.db.models import AuthoringRun, AuthoringRunEvent, Organization, PortalProfile, Recording


def _make_authoring_run() -> uuid.UUID:
    with session_scope() as session:
        org = Organization(name="Replay Phase Org", slug=f"replay-phase-org-{uuid.uuid4()}")
        profile = PortalProfile(org=org, domain="portal-replay.example.com")
        recording = Recording(
            org=org, portal_profile=profile, status="captured", capture_blob_prefix="orgs/x/recordings/1/",
        )
        run = AuthoringRun(org=org, recording=recording, status="running", current_phase="replay")
        session.add_all([org, profile, recording, run])
        session.flush()
        return run.id


def test_run_replay_phase_completes_automatically_when_every_step_succeeds(fixture_portal_url):
    authoring_run_id = _make_authoring_run()
    start_url = f"{fixture_portal_url}/login.html"
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", "none"),
            TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
            TrajectoryStep(3, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
        )
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(start_url)
        result = run_replay_phase(
            page, trajectory, authoring_run_id, threading.Event(), threading.Event(),
        )
        browser.close()

    assert result is not None
    assert len(result.steps) == 4
    assert [s.index for s in result.steps] == [0, 1, 2, 3]

    with session_scope() as session:
        run = session.get(AuthoringRun, authoring_run_id)
        assert run.status == "running"  # replay itself doesn't finalize status
        events = session.query(AuthoringRunEvent).filter_by(authoring_run_id=authoring_run_id).all()
        assert len(events) == 4
        assert all(e.level == "info" for e in events)


def test_run_replay_phase_pauses_for_developer_then_stops_cleanly(fixture_portal_url, fixture_portal_server):
    authoring_run_id = _make_authoring_run()
    start_url = f"{fixture_portal_url}/login.html"
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", "none"),
            TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
            TrajectoryStep(3, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
        )
    )

    fixture_portal_server.set_drift_mode(True)
    try:
        resume_event = threading.Event()
        stop_event = threading.Event()
        result_holder = {}

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            page = browser.new_page()
            page.goto(start_url)

            def _run():
                result_holder["trajectory"] = run_replay_phase(
                    page, trajectory, authoring_run_id, resume_event, stop_event,
                )

            worker = threading.Thread(target=_run)
            worker.start()

            deadline = time.time() + 15
            paused = False
            while time.time() < deadline:
                with session_scope() as session:
                    run = session.get(AuthoringRun, authoring_run_id)
                    if run.status == "waiting_for_developer":
                        paused = True
                        break
                time.sleep(0.1)
            assert paused, "replay never paused for developer on the drifted selector"

            stop_event.set()
            worker.join(timeout=15)
            browser.close()

        assert result_holder["trajectory"] is None
    finally:
        fixture_portal_server.set_drift_mode(False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd engine && .venv/bin/pytest tests/test_authoring_replay.py -v -k run_replay_phase`
Expected: FAIL — `ImportError: cannot import name 'run_replay_phase'`

- [ ] **Step 3: Write the implementation**

Append to `engine/src/atlas_engine/authoring/replay.py` (needs new imports too — replace the top of the file):

```python
import dataclasses
import threading
import uuid

from atlas_engine.authoring.events import log_event
from atlas_engine.authoring.state import update_run
from atlas_engine.brain.loop import run_step
from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.capture.events import RecordedAction
from atlas_engine.capture.recorder import CaptureSession
from atlas_engine.trajectory.extract import expected_effect_for_action
```

Then append at the end of the file:

```python
def run_replay_phase(
    page, trajectory: ReferenceTrajectory, authoring_run_id: uuid.UUID,
    resume_event: threading.Event, stop_event: threading.Event,
) -> ReferenceTrajectory | None:
    """Deterministic, non-LLM re-run of the recorded trajectory, headed so
    a developer can watch and — on a step failure — intervene directly in
    the live browser. Returns the (possibly developer-corrected)
    trajectory, or None if stopped mid-replay or while paused."""
    steps = list(trajectory.steps)
    i = 0
    while i < len(steps):
        if stop_event.is_set():
            return None

        step = steps[i]
        try:
            outcome = run_step(page, step)
        except Exception:
            outcome = None

        if outcome is not None and outcome.on_track:
            log_event(
                authoring_run_id, phase="replay", step_index=step.index,
                message=f"Step {step.index}: {step.kind} - OK",
            )
            i += 1
            continue

        log_event(
            authoring_run_id, phase="replay", step_index=step.index, level="warning",
            message=f"Step {step.index}: {step.kind} failed, waiting for developer",
        )
        update_run(authoring_run_id, status="waiting_for_developer")

        capture = CaptureSession(page)
        capture.__enter__()
        wait_result = _wait_for_resume_or_stop(resume_event, stop_event)
        capture.__exit__(None, None, None)

        if wait_result == "stopped":
            return None

        developer_actions: list[RecordedAction] = capture.actions
        steps = _splice_developer_actions(steps, i, developer_actions)
        update_run(authoring_run_id, status="running")
        log_event(
            authoring_run_id, phase="replay", step_index=i,
            message=f"Developer resolved step {step.index} with {len(developer_actions)} action(s)",
        )
        i += len(developer_actions)

    return ReferenceTrajectory(steps=tuple(steps))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_authoring_replay.py -v`
Expected: PASS (8/8 — the 6 from Task 3 plus these 2)

- [ ] **Step 5: Run the full backend suite**

Run: `cd engine && .venv/bin/pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/authoring/replay.py engine/tests/test_authoring_replay.py
git commit -m "feat: add run_replay_phase with developer-intervention pause/resume"
```

---

### Task 5: `run_extract_phase` and `run_brain_phase`

**Files:**
- Create: `engine/src/atlas_engine/authoring/phases.py`
- Test: `engine/tests/test_authoring_phases.py`

**Interfaces:**
- Consumes: `extract_trajectory` is NOT reused here (it takes raw `RecordedAction`s; replay's output is already a `ReferenceTrajectory`) — extract in this pipeline is just re-wrapping, see Step 3. `BrainLoop` from `atlas_engine.brain.loop`. `log_event` from Task 1.
- Produces: `run_extract_phase(trajectory: ReferenceTrajectory) -> ReferenceTrajectory` (identity today — kept as its own named phase/function because the spec calls it out as a distinct pipeline stage, and it's the natural seam for any future validation logic without touching callers). `run_brain_phase(page, trajectory: ReferenceTrajectory, authoring_run_id: uuid.UUID, *, llm=None) -> list[StepOutcome]`.

- [ ] **Step 1: Write the failing tests**

```python
# engine/tests/test_authoring_phases.py
import uuid

from playwright.sync_api import sync_playwright

from atlas_engine.authoring.phases import run_brain_phase, run_extract_phase
from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.capture.recorder import CaptureSession
from atlas_engine.db import session_scope
from atlas_engine.db.models import AuthoringRun, AuthoringRunEvent, Organization, PortalProfile, Recording
from atlas_engine.llm.client import FakeLlmClient


def _make_authoring_run() -> uuid.UUID:
    with session_scope() as session:
        org = Organization(name="Phases Org", slug=f"phases-org-{uuid.uuid4()}")
        profile = PortalProfile(org=org, domain="portal-phases.example.com")
        recording = Recording(
            org=org, portal_profile=profile, status="captured", capture_blob_prefix="orgs/x/recordings/1/",
        )
        run = AuthoringRun(org=org, recording=recording, status="running", current_phase="extract")
        session.add_all([org, profile, recording, run])
        session.flush()
        return run.id


def test_run_extract_phase_returns_the_same_trajectory():
    trajectory = ReferenceTrajectory(
        steps=(TrajectoryStep(0, "click", [{"selector": "#a", "rank": 0}], None, "none"),)
    )

    result = run_extract_phase(trajectory)

    assert result == trajectory


def test_run_brain_phase_completes_on_track_when_nothing_has_drifted(fixture_portal_url):
    authoring_run_id = _make_authoring_run()
    start_url = f"{fixture_portal_url}/login.html"
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", "none"),
            TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
            TrajectoryStep(3, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
        )
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(start_url)
        outcomes = run_brain_phase(page, trajectory, authoring_run_id)
        browser.close()

    assert all(o.on_track for o in outcomes)
    with session_scope() as session:
        events = session.query(AuthoringRunEvent).filter_by(authoring_run_id=authoring_run_id).all()
        assert len(events) == 4
        assert all(e.level == "info" for e in events)


def test_run_brain_phase_repairs_a_drifted_selector_with_the_llm(fixture_portal_url, fixture_portal_server):
    authoring_run_id = _make_authoring_run()
    start_url = f"{fixture_portal_url}/login.html"
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", "none"),
            TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
            TrajectoryStep(3, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
        )
    )

    fixture_portal_server.set_drift_mode(True)
    try:
        fake_llm = FakeLlmClient(responses=[{"action": "replace_selector", "selector": "#run-report-btn"}])
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            page = browser.new_page()
            page.goto(start_url)
            outcomes = run_brain_phase(page, trajectory, authoring_run_id, llm=fake_llm)
            browser.close()

        assert all(o.on_track for o in outcomes)
        with session_scope() as session:
            events = session.query(AuthoringRunEvent).filter_by(authoring_run_id=authoring_run_id).all()
            assert len(events) == 4
            assert all(e.level == "info" for e in events)  # BrainLoop's internal repair already resolved it
    finally:
        fixture_portal_server.set_drift_mode(False)


def test_run_brain_phase_logs_a_warning_for_a_step_that_stays_off_track(fixture_portal_url, fixture_portal_server):
    authoring_run_id = _make_authoring_run()
    start_url = f"{fixture_portal_url}/login.html"
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", "none"),
            TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
            TrajectoryStep(3, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
        )
    )

    fixture_portal_server.set_drift_mode(True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            page = browser.new_page()
            page.goto(start_url)
            outcomes = run_brain_phase(page, trajectory, authoring_run_id)  # no llm -> can't repair
            browser.close()

        assert not all(o.on_track for o in outcomes)
        with session_scope() as session:
            events = session.query(AuthoringRunEvent).filter_by(authoring_run_id=authoring_run_id).all()
            assert any(e.level == "warning" for e in events)
    finally:
        fixture_portal_server.set_drift_mode(False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd engine && .venv/bin/pytest tests/test_authoring_phases.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atlas_engine.authoring.phases'`

- [ ] **Step 3: Write the implementation**

```python
# engine/src/atlas_engine/authoring/phases.py
import uuid

from atlas_engine.authoring.events import log_event
from atlas_engine.brain.evaluate import StepOutcome
from atlas_engine.brain.loop import BrainLoop
from atlas_engine.brain.models import ReferenceTrajectory
from atlas_engine.llm.client import LlmClient


def run_extract_phase(trajectory: ReferenceTrajectory) -> ReferenceTrajectory:
    """Named as its own pipeline phase (matching the spec's replay ->
    extract -> brain sequence) even though it's the identity function
    today — replay's output is already a ReferenceTrajectory. This is
    the seam for any future trajectory-validation logic without
    changing what callers pass to run_brain_phase."""
    return trajectory


def run_brain_phase(
    page, trajectory: ReferenceTrajectory, authoring_run_id: uuid.UUID, *, llm: LlmClient | None = None,
) -> list[StepOutcome]:
    """Headed, LLM-driven supervised replay — BrainLoop already does
    perceive/act/evaluate/repair internally; this just runs it and turns
    its returned per-step outcomes into the run's event log. No developer
    fallback here: a step that's still off-track after BrainLoop's own
    repair attempt stays off-track, and the caller (the orchestrator)
    hard-fails the run."""
    outcomes = BrainLoop(page, llm=llm).run(trajectory)

    for step, outcome in zip(trajectory.steps, outcomes):
        if outcome.on_track:
            log_event(
                authoring_run_id, phase="brain", step_index=step.index,
                message=f"Step {step.index}: {step.kind} - OK",
            )
        else:
            log_event(
                authoring_run_id, phase="brain", step_index=step.index, level="warning",
                message=f"Step {step.index}: {step.kind} - still off-track after repair",
            )

    return outcomes
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_authoring_phases.py -v`
Expected: PASS (4/4)

- [ ] **Step 5: Commit**

```bash
git add engine/src/atlas_engine/authoring/phases.py engine/tests/test_authoring_phases.py
git commit -m "feat: add run_extract_phase and run_brain_phase"
```

---

### Task 6: `run_golden_phase` and `run_certify_phase`

**Files:**
- Modify: `engine/src/atlas_engine/authoring/phases.py`
- Test: `engine/tests/test_authoring_phases.py`

**Interfaces:**
- Consumes: `emit_golden_script` from `atlas_engine.codegen.emit`. `certify_golden_script_once` from Task 2. `next_golden_script_version` from `atlas_engine.db.repo`. `log_event` from Task 1.
- Produces: `run_golden_phase(trajectory, start_url, org_id, portal_profile_id, recording_id, authoring_run_id) -> tuple[uuid.UUID, Path]` — creates a `GoldenScript` row with `status="draft"`, returns its id and the script's path. `run_certify_phase(script_path: Path, authoring_run_id: uuid.UUID, *, runs: int = 3) -> tuple[int, int]` — returns `(passed, runs)`.

- [ ] **Step 1: Write the failing tests**

Add to `engine/tests/test_authoring_phases.py`:

```python
from atlas_engine.authoring.phases import run_certify_phase, run_golden_phase
from atlas_engine.db.models import CertificationRun, GoldenScript
from atlas_engine.db.repo import get_or_create_portal_profile


def _make_authoring_run_for_golden(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Golden Phase Org", slug=f"golden-phase-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        profile = get_or_create_portal_profile(session, org_id, "portal-golden.example.com")
        portal_profile_id = profile.id
        recording = Recording(
            org_id=org.id, portal_profile_id=portal_profile_id, status="captured",
            capture_blob_prefix="orgs/x/recordings/1/",
        )
        session.add(recording)
        session.flush()
        recording_id = recording.id
        run = AuthoringRun(org_id=org.id, recording_id=recording_id, status="running", current_phase="golden")
        session.add(run)
        session.flush()
        return org.id, portal_profile_id, recording_id, run.id


def test_run_golden_phase_creates_a_draft_golden_script(fixture_portal_url):
    org_id, portal_profile_id, recording_id, authoring_run_id = _make_authoring_run_for_golden(fixture_portal_url)
    trajectory = ReferenceTrajectory(
        steps=(TrajectoryStep(0, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),)
    )

    golden_script_id, script_path = run_golden_phase(
        trajectory, f"{fixture_portal_url}/login.html", org_id, portal_profile_id, recording_id, authoring_run_id,
    )

    assert script_path.exists()
    assert "generate-report-btn" in script_path.read_text()
    with session_scope() as session:
        golden = session.get(GoldenScript, golden_script_id)
        assert golden.status == "draft"
        assert golden.created_from_recording_id == recording_id


def _write_script_under_run_root(text: str) -> "Path":
    """run_certify_phase computes failure-evidence blob_refs as paths
    relative to settings.run_root (matching every other blob_ref in this
    codebase) — so, like production's run_golden_phase, tests must place
    the script under a real LocalRunStore run_dir, not an arbitrary tmp
    directory, or a failing run would raise ValueError computing that
    relative path."""
    from pathlib import Path

    from atlas_engine.config import settings
    from atlas_engine.storage import LocalRunStore

    store = LocalRunStore(root=settings.ensure_run_root())
    run_dir = store.new_run_dir(prefix="test-goldens")
    script_path = run_dir.path / "golden.py"
    script_path.write_text(text)
    return script_path


def test_run_certify_phase_records_one_row_per_run_and_counts_passes(fixture_portal_url):
    _org_id, _profile_id, _recording_id, authoring_run_id = _make_authoring_run_for_golden(fixture_portal_url)
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", "none"),
            TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
            TrajectoryStep(3, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
        )
    )
    from atlas_engine.codegen.emit import emit_golden_script

    script_path = _write_script_under_run_root(
        emit_golden_script(trajectory, start_url=f"{fixture_portal_url}/login.html")
    )

    passed, total = run_certify_phase(script_path, authoring_run_id, runs=3)

    assert total == 3
    assert passed == 3
    with session_scope() as session:
        cert_runs = (
            session.query(CertificationRun)
            .filter_by(authoring_run_id=authoring_run_id)
            .order_by(CertificationRun.run_number)
            .all()
        )
        assert [c.run_number for c in cert_runs] == [1, 2, 3]
        assert all(c.status == "pass" for c in cert_runs)
        assert all(c.failure_evidence_blob_ref is None for c in cert_runs)


def test_run_certify_phase_records_failure_evidence_for_a_broken_script(fixture_portal_url):
    _org_id, _profile_id, _recording_id, authoring_run_id = _make_authoring_run_for_golden(fixture_portal_url)
    script_path = _write_script_under_run_root("import sys\nprint('boom', file=sys.stderr)\nsys.exit(1)\n")

    passed, total = run_certify_phase(script_path, authoring_run_id, runs=2)

    assert passed == 0
    assert total == 2
    with session_scope() as session:
        cert_runs = session.query(CertificationRun).filter_by(authoring_run_id=authoring_run_id).all()
        assert all(c.status == "fail" for c in cert_runs)
        assert all(c.failure_evidence_blob_ref is not None for c in cert_runs)
        evidence_path = settings.run_root / cert_runs[0].failure_evidence_blob_ref
        assert "boom" in evidence_path.read_text()
```

This test also needs `from atlas_engine.config import settings` added to the top of `engine/tests/test_authoring_phases.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd engine && .venv/bin/pytest tests/test_authoring_phases.py -v -k "golden_phase or certify_phase"`
Expected: FAIL — `ImportError: cannot import name 'run_golden_phase'`

- [ ] **Step 3: Write the implementation**

Add these imports to the top of `engine/src/atlas_engine/authoring/phases.py`:

```python
import uuid
from pathlib import Path

from atlas_engine.authoring.events import log_event
from atlas_engine.brain.evaluate import StepOutcome
from atlas_engine.brain.loop import BrainLoop
from atlas_engine.brain.models import ReferenceTrajectory
from atlas_engine.codegen.emit import emit_golden_script
from atlas_engine.config import settings
from atlas_engine.db import session_scope
from atlas_engine.db.models import CertificationRun, GoldenScript
from atlas_engine.db.repo import next_golden_script_version
from atlas_engine.llm.client import LlmClient
from atlas_engine.storage import LocalRunStore
from atlas_engine.verify.certify import certify_golden_script_once
```

Then append:

```python
def run_golden_phase(
    trajectory: ReferenceTrajectory, start_url: str, org_id: uuid.UUID, portal_profile_id: uuid.UUID,
    recording_id: uuid.UUID, authoring_run_id: uuid.UUID,
) -> tuple[uuid.UUID, Path]:
    """Freezes the brain-verified trajectory into a deterministic .py
    script and records it as a draft GoldenScript — certifying (next
    phase) decides whether it becomes certified or partially_certified."""
    store = LocalRunStore(root=settings.ensure_run_root())
    run_dir = store.new_run_dir(prefix="goldens")
    script_text = emit_golden_script(trajectory, start_url=start_url)
    script_path = run_dir.path / "golden.py"
    script_path.write_text(script_text)

    with session_scope() as session:
        golden = GoldenScript(
            org_id=org_id,
            portal_profile_id=portal_profile_id,
            version=next_golden_script_version(session, portal_profile_id),
            script_blob_ref=str(script_path),
            status="draft",
            created_from_recording_id=recording_id,
        )
        session.add(golden)
        session.flush()
        golden_script_id = golden.id

    log_event(authoring_run_id, phase="golden", message=f"Emitted golden script v{golden.version}")
    return golden_script_id, script_path


def run_certify_phase(script_path: Path, authoring_run_id: uuid.UUID, *, runs: int = 3) -> tuple[int, int]:
    """Runs the frozen golden script headless, `runs` times from fresh
    processes, recording one CertificationRun row per attempt. Returns
    (passed, runs) — the orchestrator decides certified/partially_certified/
    repair-and-retry from this."""
    passed = 0
    for run_number in range(1, runs + 1):
        ok, failure_output = certify_golden_script_once(script_path)

        failure_ref = None
        if not ok:
            evidence_path = script_path.parent / f"certify-{run_number}-failure.txt"
            evidence_path.write_text(failure_output or "")
            failure_ref = str(evidence_path.relative_to(settings.run_root))

        with session_scope() as session:
            session.add(CertificationRun(
                authoring_run_id=authoring_run_id, run_number=run_number,
                status="pass" if ok else "fail", failure_evidence_blob_ref=failure_ref,
            ))

        log_event(
            authoring_run_id, phase="certifying",
            message=f"Certification run {run_number}/{runs}: {'PASS' if ok else 'FAIL'}",
            level="info" if ok else "warning",
        )
        if ok:
            passed += 1

    return passed, runs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_authoring_phases.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Run the full backend suite**

Run: `cd engine && .venv/bin/pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/authoring/phases.py engine/tests/test_authoring_phases.py
git commit -m "feat: add run_golden_phase and run_certify_phase"
```

---

### Task 7: `_AuthoringRunSession` orchestrator and public start/resume/stop API

**Files:**
- Create: `engine/src/atlas_engine/authoring/run.py`
- Test: `engine/tests/test_authoring_run.py`

**Interfaces:**
- Consumes: everything from Tasks 1-6 (`update_run`, `log_event`, `run_replay_phase`, `run_extract_phase`, `run_brain_phase`, `run_golden_phase`, `run_certify_phase`).
- Produces: `start_authoring_run(recording_id: uuid.UUID, *, llm: LlmClient | None = None) -> uuid.UUID` (returns the new `AuthoringRun.id`, same id used everywhere — DB row, event log, registry key). `resume_authoring_run(authoring_run_id: uuid.UUID) -> None`. `stop_authoring_run(authoring_run_id: uuid.UUID, *, timeout: float = 30.0) -> None`. Both raise `ValueError` for an unknown/already-finished run id.

- [ ] **Step 1: Write the failing tests**

```python
# engine/tests/test_authoring_run.py
import time
import uuid

import pytest
from playwright.sync_api import sync_playwright

from atlas_engine.authoring.run import resume_authoring_run, start_authoring_run, stop_authoring_run
from atlas_engine.capture.recorder import CaptureSession
from atlas_engine.db import session_scope
from atlas_engine.db.models import AuthoringRun, CertificationRun, GoldenScript, Organization
from atlas_engine.recording_service import persist_recording


def _record_against_fixture(org_id: str, fixture_portal_url: str) -> uuid.UUID:
    start_url = f"{fixture_portal_url}/login.html"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(start_url)
        with CaptureSession(page) as capture:
            page.fill("#username", "demo")
            page.fill("#password", "demo-pass")
            page.click("#login-btn")
            page.wait_for_url("**/dashboard.html")
            page.click("#generate-report-btn")
        browser.close()
    return persist_recording(org_id, start_url, capture.actions)


def _wait_for_status(authoring_run_id, statuses, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with session_scope() as session:
            run = session.get(AuthoringRun, authoring_run_id)
            if run.status in statuses:
                return run.status
        time.sleep(0.5)
    raise TimeoutError(f"authoring run never reached one of {statuses}")


def test_start_authoring_run_completes_and_certifies(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Full Run Org", slug=f"full-run-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = _record_against_fixture(org_id, fixture_portal_url)

    authoring_run_id = start_authoring_run(recording_id)
    status = _wait_for_status(authoring_run_id, {"completed", "failed", "cancelled"})

    assert status == "completed"
    with session_scope() as session:
        run = session.get(AuthoringRun, authoring_run_id)
        assert run.current_phase == "done"
        golden = session.get(GoldenScript, run.golden_script_id)
        assert golden.status == "certified"
        cert_runs = session.query(CertificationRun).filter_by(authoring_run_id=authoring_run_id).all()
        assert len(cert_runs) == 3
        assert all(c.status == "pass" for c in cert_runs)


def test_stop_authoring_run_cancels_while_waiting_for_developer(fixture_portal_url, fixture_portal_server):
    with session_scope() as session:
        org = Organization(name="Stop Run Org", slug=f"stop-run-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = _record_against_fixture(org_id, fixture_portal_url)

    fixture_portal_server.set_drift_mode(True)
    try:
        authoring_run_id = start_authoring_run(recording_id)
        status = _wait_for_status(authoring_run_id, {"waiting_for_developer", "failed", "completed"})
        assert status == "waiting_for_developer"

        stop_authoring_run(authoring_run_id)

        with session_scope() as session:
            run = session.get(AuthoringRun, authoring_run_id)
            assert run.status == "cancelled"
            assert run.golden_script_id is None
    finally:
        fixture_portal_server.set_drift_mode(False)


def test_resume_authoring_run_raises_for_unknown_run():
    with pytest.raises(ValueError, match="No active authoring run"):
        resume_authoring_run(uuid.uuid4())


def test_stop_authoring_run_raises_for_unknown_run():
    with pytest.raises(ValueError, match="No active authoring run"):
        stop_authoring_run(uuid.uuid4())


def test_start_authoring_run_raises_for_unknown_recording():
    with pytest.raises(ValueError, match="No such recording"):
        start_authoring_run(uuid.uuid4())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd engine && .venv/bin/pytest tests/test_authoring_run.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atlas_engine.authoring.run'`

- [ ] **Step 3: Write the implementation**

```python
# engine/src/atlas_engine/authoring/run.py
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
from atlas_engine.brain.serialize import trajectory_from_dict
from atlas_engine.db import session_scope
from atlas_engine.db.models import AuthoringRun, GoldenScript, PortalProfile, Recording
from atlas_engine.db.models import ReferenceTrajectory as ReferenceTrajectoryModel
from atlas_engine.llm.client import LlmClient

_MAX_REPAIR_ATTEMPTS = 2
_CERTIFY_RUNS = 3

_sessions: dict[uuid.UUID, "_AuthoringRunSession"] = {}
_registry_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
        self.done_event.wait(timeout=timeout)

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

            trajectory = trajectory_from_dict(json.loads(Path(trajectory_blob_ref).read_text()))

            update_run(self.authoring_run_id, current_phase="replay")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=False)
                page = browser.new_page()
                page.goto(start_url)
                trajectory = run_replay_phase(
                    page, trajectory, self.authoring_run_id, self.resume_event, self.stop_event,
                )
                browser.close()

            if trajectory is None:
                update_run(self.authoring_run_id, status="cancelled", ended_at=_now())
                return

            update_run(self.authoring_run_id, current_phase="extract")
            trajectory = run_extract_phase(trajectory)

            while True:
                if self.stop_event.is_set():
                    update_run(self.authoring_run_id, status="cancelled", ended_at=_now())
                    return

                update_run(self.authoring_run_id, current_phase="brain")
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=False)
                    page = browser.new_page()
                    page.goto(start_url)
                    outcomes = run_brain_phase(page, trajectory, self.authoring_run_id, llm=self.llm)
                    browser.close()

                if not all(outcome.on_track for outcome in outcomes):
                    update_run(self.authoring_run_id, status="failed", ended_at=_now())
                    return

                update_run(self.authoring_run_id, current_phase="golden")
                golden_script_id, script_path = run_golden_phase(
                    trajectory, start_url, org_id, portal_profile_id, self.recording_id, self.authoring_run_id,
                )
                update_run(self.authoring_run_id, golden_script_id=golden_script_id)

                update_run(self.authoring_run_id, current_phase="certifying")
                passed, total = run_certify_phase(
                    script_path, self.authoring_run_id, runs=_CERTIFY_RUNS,
                )

                if passed == total:
                    self._finalize_golden(golden_script_id, "certified")
                    break

                with session_scope() as session:
                    run = session.get(AuthoringRun, self.authoring_run_id)
                    repair_attempt = run.repair_attempt

                if repair_attempt >= _MAX_REPAIR_ATTEMPTS:
                    self._finalize_golden(golden_script_id, "partially_certified")
                    break

                update_run(self.authoring_run_id, repair_attempt=repair_attempt + 1)
                log_event(
                    self.authoring_run_id, phase="certifying", level="warning",
                    message=(
                        f"Certification {passed}/{total} passed — retrying via brain "
                        f"(repair {repair_attempt + 1}/{_MAX_REPAIR_ATTEMPTS})"
                    ),
                )

            update_run(self.authoring_run_id, status="completed", current_phase="done", ended_at=_now())
        except Exception as exc:  # surfaced to the caller of stop(), and leaves the run in a diagnosable state
            self.error = exc
            update_run(self.authoring_run_id, status="failed", ended_at=_now())
        finally:
            self.done_event.set()

    def _finalize_golden(self, golden_script_id: uuid.UUID, status: str) -> None:
        with session_scope() as session:
            golden = session.get(GoldenScript, golden_script_id)
            golden.status = status
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
        session_obj = _sessions.pop(authoring_run_id, None)
    if session_obj is None:
        raise ValueError(f"No active authoring run: {authoring_run_id}")
    session_obj.stop(timeout=timeout)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd engine && .venv/bin/pytest tests/test_authoring_run.py -v`
Expected: PASS (5/5). The first test is slow (real replay + brain + 3 certify subprocess runs) — allow it to run to completion rather than interrupting.

- [ ] **Step 5: Run the full backend suite**

Run: `cd engine && .venv/bin/pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/authoring/run.py engine/tests/test_authoring_run.py
git commit -m "feat: add AuthoringRun orchestrator with start/resume/stop API"
```

---

### Task 8: Manual verification of the developer-intervention pause/resume path, and finish branch

**Files:** none (verification task)

There is no API or UI yet (later plans build those) — this step verifies the real pause/resume/splice mechanism with genuine human interaction in a live browser, the same way Stage A's multi-tab work verified real popup behavior before an API/UI existed for it.

- [ ] **Step 1: Write a throwaway verification script**

Create `engine/scratch_verify_replay_pause.py` (not committed — delete it in Step 4):

```python
import sys
import threading
import time
import uuid

sys.path.insert(0, "src")
sys.path.insert(0, "tests")

from playwright.sync_api import sync_playwright

from atlas_engine.authoring.run import resume_authoring_run, start_authoring_run
from atlas_engine.capture.recorder import CaptureSession
from atlas_engine.db import session_scope
from atlas_engine.db.models import AuthoringRun, GoldenScript, Organization
from atlas_engine.recording_service import persist_recording
from fixtures.fixture_portal.server import FixturePortalServer

server = FixturePortalServer(port=8731)
server.start()
start_url = f"{server.base_url}/login.html"

with session_scope() as session:
    org = Organization(name="Manual Verify Org", slug=f"manual-verify-org-{uuid.uuid4()}")
    session.add(org)
    session.flush()
    org_id = str(org.id)

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(start_url)
    with CaptureSession(page) as capture:
        page.fill("#username", "demo")
        page.fill("#password", "demo-pass")
        page.click("#login-btn")
        page.wait_for_url("**/dashboard.html")
        page.click("#generate-report-btn")
    browser.close()

recording_id = persist_recording(org_id, start_url, capture.actions)

server.set_drift_mode(True)
print("Drift mode ON — the recorded '#generate-report-btn' selector no longer exists.")
print(f"Starting authoring run for recording {recording_id}...")
authoring_run_id = start_authoring_run(recording_id)

deadline = time.time() + 30
while time.time() < deadline:
    with session_scope() as session:
        run = session.get(AuthoringRun, authoring_run_id)
        if run.status == "waiting_for_developer":
            break
    time.sleep(0.5)
else:
    print("FAILED: never reached waiting_for_developer")
    sys.exit(1)

print()
print("=" * 70)
print("A live Chromium window should now be open, paused on the dashboard.")
print("The recorded button (#generate-report-btn) no longer exists on this")
print("drifted page — the real button is now labeled 'Generate report' with")
print("id #run-report-btn. Click it now.")
print("=" * 70)
input("Press Enter once you've clicked the button in the live browser window...")

resume_authoring_run(authoring_run_id)

deadline = time.time() + 90
while time.time() < deadline:
    with session_scope() as session:
        run = session.get(AuthoringRun, authoring_run_id)
        if run.status in ("completed", "failed", "cancelled"):
            break
    time.sleep(0.5)

with session_scope() as session:
    run = session.get(AuthoringRun, authoring_run_id)
    print(f"Final status: {run.status}, phase: {run.current_phase}")
    if run.golden_script_id:
        golden = session.get(GoldenScript, run.golden_script_id)
        print(f"Golden script status: {golden.status}")
        print(f"Golden script content:\n{open(golden.script_blob_ref).read()}")

server.set_drift_mode(False)
server.stop()
```

- [ ] **Step 2: Run the script**

Run: `cd engine && .venv/bin/python scratch_verify_replay_pause.py`

When it prints the "click the button" prompt, a real headed Chromium window is open and paused. Click the "Generate report" button (id `#run-report-btn`) in that window, then press Enter in the terminal.

- [ ] **Step 3: Confirm the result**

Expected output: `Final status: completed`, `Golden script status: certified` (or `partially_certified`, which is also an acceptable outcome — either confirms the pause/resume/splice path worked; only `failed`/`cancelled` indicates a real problem worth investigating). The printed golden script's source should contain a `page.click(...)` call using the selector you actually clicked (`#run-report-btn`), not the original recorded one (`#generate-report-btn`) — this is the concrete proof the developer's live action was correctly captured and spliced into the trajectory.

- [ ] **Step 4: Delete the throwaway script**

```bash
rm engine/scratch_verify_replay_pause.py
```

- [ ] **Step 5: Finish the branch**

**REQUIRED SUB-SKILL:** Use superpowers:finishing-a-development-branch — run the full backend suite, present the merge/PR/keep menu, act on the answer.
