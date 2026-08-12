# Atlas Engine — Phase 1 Self-Healing & Versioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give an existing certified golden script the ability to be re-validated against a live portal, automatically repaired via the brain when a step has drifted, and promoted to production only after explicit human approval — with an action cache so a repair, once found, doesn't need the LLM again next time.

**Architecture:** Builds directly on the Phase 0 engine — no new services, no new tech stack. `heal_golden_script()` reloads a persisted `ReferenceTrajectory`, replays it live through the existing `BrainLoop` (now with an `ActionCacheStore` wired in), and — only if every step ends up on-track — emits and certifies a new `GoldenScript` version. A `HealEvent` row always starts `pending_approval`; nothing goes live without an explicit `approve_heal_event()` call. Per architecture spec §7.1–§7.3.

**Tech Stack:** Same as Phase 0 (Python, Playwright, SQLAlchemy, Alembic, FastAPI, Click, pytest) — no new dependencies.

## Global Constraints

- Everything from Phase 0's Global Constraints still applies (no LLM in shipped scripts, recording is ground truth, `org_id` everywhere, zero manual config, no test may require real credentials/portals/humans).
- A healed version never goes live automatically. Every `heal_events` row is created with `status = "pending_approval"`; only `approve_heal_event()` flips a version to `active`. (Architecture spec §7.3.)
- `action_cache.cache_key` excludes parameter values — it hashes instruction + URL pattern + DOM fingerprint only, so a script re-run with different input data still hits the same cache entry. (Architecture spec §7.1.)
- A repair is never trusted blindly: exactly as in Phase 0's `think()`, any selector a heal proposes is validated against the live DOM before use — this plan does not relax that guarantee anywhere.
- Every step ends with tests passing and a commit. Do not proceed to the next task with failing tests.

---

## File Structure

```
engine/src/atlas_engine/
├── db/
│   └── repo.py                      # NEW: get_or_create_portal_profile, next_golden_script_version
├── brain/
│   ├── evaluate.py                  # MODIFIED: StepOutcome gains resolved_selector
│   ├── loop.py                      # MODIFIED: run_step passes resolved_selector; cache-aware _repair
│   ├── serialize.py                 # NEW: trajectory_to_dict / trajectory_from_dict
│   └── cache.py                     # NEW: ActionCacheStore, compute_cache_key
├── llm/
│   └── factory.py                   # NEW: create_llm_client() from environment
├── verify/
│   └── heal.py                      # NEW: compute_trajectory_diff, heal_golden_script,
│                                     #      approve_heal_event, reject_heal_event
├── cli.py                           # MODIFIED: use db/repo helpers; persist Recording +
│                                     #      ReferenceTrajectory; add heal/approve-heal/reject-heal
└── api.py                           # MODIFIED: add /golden-scripts/{id}/heal,
                                      #      /heal-events/{id}/approve, /heal-events/{id}/reject

engine/tests/
├── fixtures/fixture_portal/
│   ├── dashboard_drifted.html       # NEW: the "portal changed" variant
│   └── server.py                    # MODIFIED: drift_mode toggle
├── conftest.py                      # MODIFIED: fixture_portal_server fixture (drift control)
├── test_brain_evaluate.py           # MODIFIED: pass resolved_selector to evaluate()
├── test_brain_repair.py             # MODIFIED: pass resolved_selector to evaluate()
├── test_db_repo.py                  # NEW
├── test_trajectory_serialize.py     # NEW
├── test_cli_persists_recording.py   # NEW
├── test_action_cache.py             # NEW
├── test_brain_loop_cache.py         # NEW
├── test_trajectory_diff.py          # NEW
├── test_llm_factory.py              # NEW
├── test_heal.py                     # NEW
├── test_heal_approval.py            # NEW
├── test_cli_heal.py                 # NEW
├── test_api_heal.py                 # NEW
└── test_end_to_end_heal.py          # NEW — the capstone: drift -> heal -> approve
```

---

### Task 1: DB repo helpers, and refactor the CLI to use them

**Files:**
- Create: `engine/src/atlas_engine/db/repo.py`
- Modify: `engine/src/atlas_engine/cli.py:91-124` (the inline get-or-create-profile / next-version logic in `author_from_recorded_actions`)
- Create: `engine/tests/test_db_repo.py`

**Interfaces:**
- Produces: `get_or_create_portal_profile(session: Session, org_id: str, domain: str) -> PortalProfile`; `next_golden_script_version(session: Session, portal_profile_id: uuid.UUID) -> int`. Both take an already-open `Session` (no `session_scope()` inside — callers control the transaction).

- [ ] **Step 1: Write the failing test**

`engine/tests/test_db_repo.py`:
```python
import uuid

from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, Organization
from atlas_engine.db.repo import get_or_create_portal_profile, next_golden_script_version


def test_get_or_create_portal_profile_creates_once_and_reuses():
    with session_scope() as session:
        org = Organization(name="Repo Test Org", slug=f"repo-test-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = org.id

        first = get_or_create_portal_profile(session, str(org_id), "example.com")
        second = get_or_create_portal_profile(session, str(org_id), "example.com")

        assert first.id == second.id


def test_next_golden_script_version_increments():
    with session_scope() as session:
        org = Organization(name="Version Test Org", slug=f"version-test-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()

        profile = get_or_create_portal_profile(session, str(org.id), "versions.example.com")

        assert next_golden_script_version(session, profile.id) == 1

        session.add(
            GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=1, script_blob_ref="v1.py")
        )
        session.flush()

        assert next_golden_script_version(session, profile.id) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db_repo.py -v`
Expected: `FAIL` with `ModuleNotFoundError: No module named 'atlas_engine.db.repo'`.

- [ ] **Step 3: Write `engine/src/atlas_engine/db/repo.py`**

```python
import uuid

from sqlalchemy.orm import Session

from atlas_engine.db.models import GoldenScript, PortalProfile


def get_or_create_portal_profile(session: Session, org_id: str, domain: str) -> PortalProfile:
    """One row per (org, domain) — created once, reused across every future
    recording/authoring of that domain, never duplicated (architecture spec
    §6.3)."""
    profile = (
        session.query(PortalProfile)
        .filter_by(org_id=uuid.UUID(org_id), domain=domain)
        .one_or_none()
    )
    if profile is None:
        profile = PortalProfile(org_id=uuid.UUID(org_id), domain=domain)
        session.add(profile)
        session.flush()
    return profile


def next_golden_script_version(session: Session, portal_profile_id: uuid.UUID) -> int:
    latest = (
        session.query(GoldenScript.version)
        .filter_by(portal_profile_id=portal_profile_id)
        .order_by(GoldenScript.version.desc())
        .limit(1)
        .scalar()
    )
    return (latest or 0) + 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_db_repo.py -v`
Expected: `PASS` on both tests.

- [ ] **Step 5: Refactor `author_from_recorded_actions` in `engine/src/atlas_engine/cli.py` to use these helpers**

Replace the existing inline block (currently around lines 91-124, inside the `with session_scope() as session:` at the end of `author_from_recorded_actions`):

```python
    domain = start_url.split("/")[2]
    with session_scope() as session:
        # Get-or-create: a portal profile is one row per (org, domain),
        # created once and reused across every future recording/authoring
        # of that domain — never duplicated (architecture spec §6.3).
        profile = (
            session.query(PortalProfile)
            .filter_by(org_id=uuid.UUID(org_id), domain=domain)
            .one_or_none()
        )
        if profile is None:
            profile = PortalProfile(org_id=uuid.UUID(org_id), domain=domain)
            session.add(profile)
            session.flush()

        latest_version = (
            session.query(GoldenScript.version)
            .filter_by(portal_profile_id=profile.id)
            .order_by(GoldenScript.version.desc())
            .limit(1)
            .scalar()
        )
        next_version = (latest_version or 0) + 1

        golden = GoldenScript(
            org_id=uuid.UUID(org_id),
            portal_profile_id=profile.id,
            version=next_version,
            script_blob_ref=str(script_path),
            status=status,
        )
```

with:

```python
    domain = start_url.split("/")[2]
    with session_scope() as session:
        profile = get_or_create_portal_profile(session, org_id, domain)

        golden = GoldenScript(
            org_id=uuid.UUID(org_id),
            portal_profile_id=profile.id,
            version=next_golden_script_version(session, profile.id),
            script_blob_ref=str(script_path),
            status=status,
        )
```

Add the import at the top of `cli.py`:
```python
from atlas_engine.db.repo import get_or_create_portal_profile, next_golden_script_version
```

- [ ] **Step 6: Run the full suite to confirm the refactor didn't break anything**

Run: `pytest -v`
Expected: all Phase 0 tests still `PASS` (37 passed at the end of Phase 0), plus the 2 new tests from this task.

- [ ] **Step 7: Commit**

```bash
git add engine/src/atlas_engine/db/repo.py engine/src/atlas_engine/cli.py engine/tests/test_db_repo.py
git commit -m "refactor: extract get-or-create-portal-profile and next-version into db/repo.py"
```

---

### Task 2: Trajectory serialization

**Files:**
- Create: `engine/src/atlas_engine/brain/serialize.py`
- Create: `engine/tests/test_trajectory_serialize.py`

**Interfaces:**
- Consumes: `ReferenceTrajectory`, `TrajectoryStep` (Phase 0).
- Produces: `trajectory_to_dict(trajectory: ReferenceTrajectory) -> dict`; `trajectory_from_dict(data: dict) -> ReferenceTrajectory`. Round-trips exactly (`trajectory_from_dict(trajectory_to_dict(t)) == t`).

- [ ] **Step 1: Write the failing test**

`engine/tests/test_trajectory_serialize.py`:
```python
from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.brain.serialize import trajectory_from_dict, trajectory_to_dict


def test_trajectory_round_trips_through_dict():
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#u", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "click", [{"selector": "#b", "rank": 0}], None, "dom_changed"),
        )
    )

    data = trajectory_to_dict(trajectory)
    restored = trajectory_from_dict(data)

    assert restored == trajectory
    assert data == {
        "steps": [
            {
                "index": 0, "kind": "fill",
                "selector_candidates": [{"selector": "#u", "rank": 0}],
                "value": "demo", "expected_effect": "none",
            },
            {
                "index": 1, "kind": "click",
                "selector_candidates": [{"selector": "#b", "rank": 0}],
                "value": None, "expected_effect": "dom_changed",
            },
        ]
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trajectory_serialize.py -v`
Expected: `FAIL` with `ModuleNotFoundError: No module named 'atlas_engine.brain.serialize'`.

- [ ] **Step 3: Write `engine/src/atlas_engine/brain/serialize.py`**

```python
from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep


def trajectory_to_dict(trajectory: ReferenceTrajectory) -> dict:
    return {
        "steps": [
            {
                "index": step.index,
                "kind": step.kind,
                "selector_candidates": step.selector_candidates,
                "value": step.value,
                "expected_effect": step.expected_effect,
            }
            for step in trajectory.steps
        ]
    }


def trajectory_from_dict(data: dict) -> ReferenceTrajectory:
    steps = tuple(
        TrajectoryStep(
            index=item["index"],
            kind=item["kind"],
            selector_candidates=item["selector_candidates"],
            value=item["value"],
            expected_effect=item["expected_effect"],
        )
        for item in data["steps"]
    )
    return ReferenceTrajectory(steps=steps)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_trajectory_serialize.py -v`
Expected: `PASS`.

- [ ] **Step 5: Commit**

```bash
git add engine/src/atlas_engine/brain/serialize.py engine/tests/test_trajectory_serialize.py
git commit -m "feat: add trajectory JSON serialization"
```

---

### Task 3: Persist Recording + ReferenceTrajectory during authoring

**Files:**
- Modify: `engine/src/atlas_engine/cli.py` (`author_from_recorded_actions`)
- Create: `engine/tests/test_cli_persists_recording.py`

**Interfaces:**
- Consumes: `trajectory_to_dict` (Task 2); `Recording`, `ReferenceTrajectory` models (Phase 0 schema, unused until now).
- Produces: `author_from_recorded_actions` now also creates a `Recording` row and a `ReferenceTrajectory` row (with a real, re-readable `trajectory_blob_ref`) every time it's called — this is what makes `heal_golden_script` (Task 9) possible, since healing needs to reload the exact trajectory a golden script was built from.

- [ ] **Step 1: Write the failing test**

`engine/tests/test_cli_persists_recording.py`:
```python
import json
import uuid
from pathlib import Path

from atlas_engine.capture.events import RecordedAction
from atlas_engine.cli import author_from_recorded_actions
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, Organization, Recording, ReferenceTrajectory


def test_author_from_recorded_actions_persists_recording_and_trajectory(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Persist Test Org", slug=f"persist-test-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    actions = [
        RecordedAction(0, "fill", [{"selector": "#username", "rank": 0}], "demo", f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/login.html", "", ""),
        RecordedAction(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/login.html", "", ""),
        RecordedAction(2, "click", [{"selector": "#login-btn", "rank": 0}], None, f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/dashboard.html", "", ""),
        RecordedAction(3, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, f"{fixture_portal_url}/dashboard.html", f"{fixture_portal_url}/dashboard.html", "x", "y"),
    ]

    result = author_from_recorded_actions(org_id, f"{fixture_portal_url}/login.html", actions)
    assert result["status"] == "certified"

    with session_scope() as session:
        golden = session.get(GoldenScript, uuid.UUID(result["golden_script_id"]))
        assert golden.created_from_recording_id is not None

        recording = session.get(Recording, golden.created_from_recording_id)
        assert recording.status == "captured"

        trajectory_row = (
            session.query(ReferenceTrajectory).filter_by(recording_id=recording.id).one()
        )
        assert trajectory_row.step_count == 4

        data = json.loads(Path(trajectory_row.trajectory_blob_ref).read_text())
        assert len(data["steps"]) == 4
        assert data["steps"][3]["selector_candidates"][0]["selector"] == "#generate-report-btn"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_persists_recording.py -v`
Expected: `FAIL` — `golden.created_from_recording_id` is `None` (Phase 0 never set it).

- [ ] **Step 3: Modify `author_from_recorded_actions` in `engine/src/atlas_engine/cli.py`**

Replace the function body from the start through the brain-loop/emission section with:

```python
def author_from_recorded_actions(
    org_id: str, start_url: str, actions: list[RecordedAction], *, llm: LlmClient | None = None
) -> dict:
    """Shared authoring logic used by both the CLI and the local API:
    persist the recording -> deterministic trajectory extraction -> persist
    the trajectory -> live brain validation -> emit -> certify.

    Persisting the recording and trajectory (not just the emitted script)
    is what makes on-demand healing possible later — heal_golden_script
    (Task 9 of the Phase 1 plan) reloads exactly this trajectory to
    re-validate a script months after it was authored.

    Returns {"status", "script_path", "golden_script_id"}.
    """
    store = LocalRunStore(root=settings.ensure_run_root())
    recording_run_dir = store.new_run_dir(prefix="recordings")
    recording_run_dir.write_json("actions.json", [vars(a) for a in actions])

    trajectory = extract_trajectory(actions)
    trajectory_path = recording_run_dir.path / "trajectory.json"
    trajectory_path.write_text(json.dumps(trajectory_to_dict(trajectory), indent=2))

    domain = start_url.split("/")[2]
    with session_scope() as session:
        profile = get_or_create_portal_profile(session, org_id, domain)

        recording = Recording(
            org_id=uuid.UUID(org_id),
            portal_profile_id=profile.id,
            status="captured",
            capture_blob_prefix=recording_run_dir.blob_ref,
        )
        session.add(recording)
        session.flush()

        session.add(
            ReferenceTrajectory(
                recording_id=recording.id,
                trajectory_blob_ref=str(trajectory_path),
                step_count=len(trajectory.steps),
            )
        )

        recording_id = recording.id
        portal_profile_id = profile.id

    goldens_run_dir = store.new_run_dir(prefix="goldens")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(start_url)
        outcomes = BrainLoop(page, llm=llm).run(trajectory)
        browser.close()

    all_on_track = all(outcome.on_track for outcome in outcomes)

    script_text = emit_golden_script(trajectory, start_url=start_url)
    script_path = goldens_run_dir.path / "golden.py"
    script_path.write_text(script_text)

    if not all_on_track:
        status = "draft"
    else:
        certification = certify_golden_script(script_path, runs=2)
        status = "certified" if certification.is_certified else "draft"

    with session_scope() as session:
        golden = GoldenScript(
            org_id=uuid.UUID(org_id),
            portal_profile_id=portal_profile_id,
            version=next_golden_script_version(session, portal_profile_id),
            script_blob_ref=str(script_path),
            status=status,
            created_from_recording_id=recording_id,
        )
        session.add(golden)
        session.flush()
        golden_script_id = str(golden.id)

    return {"status": status, "script_path": str(script_path), "golden_script_id": golden_script_id}
```

Add these imports at the top of `cli.py` (some may already be present from Phase 0/Task 1 — add only what's missing):
```python
import json

from atlas_engine.brain.serialize import trajectory_to_dict
from atlas_engine.db.models import GoldenScript, PortalProfile, Recording, ReferenceTrajectory
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_persists_recording.py -v`
Expected: `PASS`.

- [ ] **Step 5: Run the full suite to confirm nothing regressed**

Run: `pytest -v`
Expected: all prior tests still pass. `test_cli.py`, `test_api.py`, and `test_end_to_end.py` all call `author_from_recorded_actions` — they should be unaffected since the function's return shape is unchanged, only its side effects (now also writing Recording/ReferenceTrajectory rows) are new.

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/cli.py engine/tests/test_cli_persists_recording.py
git commit -m "feat: persist Recording and ReferenceTrajectory rows during authoring"
```

---

### Task 4: Extend StepOutcome with resolved_selector

**Files:**
- Modify: `engine/src/atlas_engine/brain/evaluate.py`
- Modify: `engine/src/atlas_engine/brain/loop.py` (`run_step`)
- Modify: `engine/tests/test_brain_evaluate.py` (add `resolved_selector` to every `evaluate()` call)
- Modify: `engine/tests/test_brain_repair.py` (add `resolved_selector` to the two direct `evaluate()` calls)

**Interfaces:**
- Produces: `StepOutcome` now has a required `resolved_selector: str` field — the selector that was actually used for this step, whether it was the original recorded one, a cached one, or an LLM-repaired one. `evaluate()` takes `resolved_selector` as a required keyword argument and passes it through unchanged. This is what lets `heal_golden_script` (Task 9) know what to put in a rebuilt trajectory.

- [ ] **Step 1: Update `engine/src/atlas_engine/brain/evaluate.py`**

```python
from dataclasses import dataclass

from atlas_engine.brain.models import TrajectoryStep


@dataclass(frozen=True)
class StepOutcome:
    step_index: int
    on_track: bool
    url_after: str
    dom_after: str
    resolved_selector: str


def evaluate(
    step: TrajectoryStep,
    *,
    url_before: str,
    url_after: str,
    dom_before: str,
    dom_after: str,
    resolved_selector: str,
) -> StepOutcome:
    """Deterministic — compares the step's expected effect (recorded at
    capture time) against what actually happened live. This is the check
    that decides whether the brain needs to think at all."""
    if step.expected_effect == "url_changed":
        on_track = url_before != url_after
    elif step.expected_effect == "dom_changed":
        on_track = dom_before != dom_after
    else:
        on_track = True

    return StepOutcome(
        step_index=step.index,
        on_track=on_track,
        url_after=url_after,
        dom_after=dom_after,
        resolved_selector=resolved_selector,
    )
```

- [ ] **Step 2: Update `run_step` in `engine/src/atlas_engine/brain/loop.py`**

Find the existing `run_step` function and replace it with:

```python
def run_step(
    page,
    step: TrajectoryStep,
    *,
    selector: str | None = None,
    force: bool = False,
    timeout_ms: float | None = _INITIAL_ATTEMPT_TIMEOUT_MS,
) -> StepOutcome:
    url_before = page.url
    dom_before = page.content()

    resolved_selector = selector or _first_selector(step)
    _act(page, resolved_selector, step, force=force, timeout_ms=timeout_ms)

    page.wait_for_load_state("domcontentloaded")
    url_after = page.url
    dom_after = page.content()

    return evaluate(
        step,
        url_before=url_before,
        url_after=url_after,
        dom_before=dom_before,
        dom_after=dom_after,
        resolved_selector=resolved_selector,
    )
```

(This only changes how `resolved_selector` is computed — as a named local reused for both the act call and the `evaluate()` call — and threads it through. Nothing else in `run_step` changes.)

- [ ] **Step 3: Update `engine/tests/test_brain_evaluate.py`**

Add `resolved_selector="#btn"` to all four `evaluate()` calls (they all use `_step(...)` with selector `#btn`). For example, the first one:

```python
def test_evaluate_on_track_when_url_changed_as_expected():
    outcome = evaluate(
        _step("url_changed"),
        url_before="https://x/login", url_after="https://x/dashboard",
        dom_before="<a/>", dom_after="<a/>",
        resolved_selector="#btn",
    )
    assert outcome.on_track is True
```

Apply the same `resolved_selector="#btn",` addition to the other three test functions in this file (`test_evaluate_off_track_when_nothing_changed_but_change_was_expected`, `test_evaluate_on_track_when_dom_changed_as_expected`, `test_evaluate_on_track_for_none_effect_regardless_of_change`).

- [ ] **Step 4: Update `engine/tests/test_brain_repair.py`**

In `test_think_validates_and_returns_a_selector_that_exists_on_the_live_page` and `test_think_coerces_a_selector_that_does_not_exist_on_the_page_to_give_up`, both call `evaluate(step, url_before=..., url_after=..., dom_before=..., dom_after=...)`. Add `resolved_selector="#does-not-exist"` to both calls (matching the step's original, broken selector — that's the selector that was actually tried and found off-track):

```python
        outcome = evaluate(
            step, url_before=page.url, url_after=page.url,
            dom_before=page.content(), dom_after=page.content(),
            resolved_selector="#does-not-exist",
        )
```

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: `PASS` on everything — Phase 0's 37 tests plus Phase 1's additions so far.

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/brain/evaluate.py engine/src/atlas_engine/brain/loop.py \
        engine/tests/test_brain_evaluate.py engine/tests/test_brain_repair.py
git commit -m "feat: add resolved_selector to StepOutcome so a repair's fix is knowable to callers"
```

---

### Task 5: Action cache module

**Files:**
- Create: `engine/src/atlas_engine/brain/cache.py`
- Create: `engine/tests/test_action_cache.py`

**Interfaces:**
- Consumes: `ActionCache` model (Phase 0 schema, unused until now); `db.repo.get_or_create_portal_profile` (Task 1, used only in this task's tests to set up a `GoldenScript` fixture row).
- Produces: `compute_cache_key(*, instruction: str, url_pattern: str, dom_fingerprint: str) -> str`; `ActionCacheStore` with `.get(golden_script_id, *, step_index, cache_key) -> list[dict] | None`, `.put(golden_script_id, *, step_index, cache_key, selector_candidates, method=None) -> None`, `.record_miss(golden_script_id, *, step_index, cache_key) -> None`.

- [ ] **Step 1: Write the failing test**

`engine/tests/test_action_cache.py`:
```python
import uuid

from atlas_engine.brain.cache import ActionCacheStore, compute_cache_key
from atlas_engine.db import session_scope
from atlas_engine.db.models import ActionCache, GoldenScript, Organization
from atlas_engine.db.repo import get_or_create_portal_profile


def _make_golden_script_id(slug_prefix: str) -> uuid.UUID:
    with session_scope() as session:
        org = Organization(name="Cache Test Org", slug=f"{slug_prefix}-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        profile = get_or_create_portal_profile(session, str(org.id), f"{slug_prefix}.example.com")
        golden = GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=1, script_blob_ref="v1.py")
        session.add(golden)
        session.flush()
        return golden.id


def test_compute_cache_key_excludes_parameter_values_but_is_stable():
    key_a = compute_cache_key(instruction="click login button", url_pattern="/login", dom_fingerprint="abc123")
    key_b = compute_cache_key(instruction="click login button", url_pattern="/login", dom_fingerprint="abc123")
    key_c = compute_cache_key(instruction="click login button", url_pattern="/login", dom_fingerprint="different")

    assert key_a == key_b
    assert key_a != key_c


def test_action_cache_store_put_then_get_round_trips():
    golden_script_id = _make_golden_script_id("cache-test-org")
    store = ActionCacheStore()
    key = compute_cache_key(instruction="click generate report", url_pattern="/dashboard.html", dom_fingerprint="xyz")

    assert store.get(golden_script_id, step_index=3, cache_key=key) is None

    store.put(
        golden_script_id, step_index=3, cache_key=key,
        selector_candidates=[{"selector": "#generate-report-btn", "rank": 0}], method="click",
    )

    cached = store.get(golden_script_id, step_index=3, cache_key=key)
    assert cached == [{"selector": "#generate-report-btn", "rank": 0}]


def test_action_cache_store_record_miss_increments_counter():
    golden_script_id = _make_golden_script_id("cache-miss-org")
    store = ActionCacheStore()
    key = compute_cache_key(instruction="x", url_pattern="/y", dom_fingerprint="z")
    store.put(golden_script_id, step_index=0, cache_key=key, selector_candidates=[{"selector": "#a", "rank": 0}])

    store.record_miss(golden_script_id, step_index=0, cache_key=key)
    store.record_miss(golden_script_id, step_index=0, cache_key=key)

    with session_scope() as session:
        row = (
            session.query(ActionCache)
            .filter_by(golden_script_id=golden_script_id, step_index=0, cache_key=key)
            .one()
        )
        assert row.miss_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_action_cache.py -v`
Expected: `FAIL` with `ModuleNotFoundError: No module named 'atlas_engine.brain.cache'`.

- [ ] **Step 3: Write `engine/src/atlas_engine/brain/cache.py`**

```python
import hashlib
import uuid
from datetime import datetime, timezone

from atlas_engine.db import session_scope
from atlas_engine.db.models import ActionCache


def compute_cache_key(*, instruction: str, url_pattern: str, dom_fingerprint: str) -> str:
    """Deliberately excludes parameter values — a script run with different
    input data still hits the same cache entry (architecture spec §7.1)."""
    raw = f"{instruction}|{url_pattern}|{dom_fingerprint}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ActionCacheStore:
    """DB-backed cache of resolved selectors per (golden_script, step,
    cache_key). A miss is never fatal — callers fall through to live
    re-resolution and write a fresh entry."""

    def get(self, golden_script_id: uuid.UUID, *, step_index: int, cache_key: str) -> list[dict] | None:
        with session_scope() as session:
            row = (
                session.query(ActionCache)
                .filter_by(golden_script_id=golden_script_id, step_index=step_index, cache_key=cache_key)
                .one_or_none()
            )
            return row.selector_candidates if row else None

    def put(
        self,
        golden_script_id: uuid.UUID,
        *,
        step_index: int,
        cache_key: str,
        selector_candidates: list[dict],
        method: str | None = None,
    ) -> None:
        with session_scope() as session:
            row = (
                session.query(ActionCache)
                .filter_by(golden_script_id=golden_script_id, step_index=step_index, cache_key=cache_key)
                .one_or_none()
            )
            if row is None:
                row = ActionCache(
                    golden_script_id=golden_script_id,
                    step_index=step_index,
                    cache_key=cache_key,
                    selector_candidates=selector_candidates,
                    method=method,
                )
                session.add(row)
            else:
                row.selector_candidates = selector_candidates
                row.method = method
            row.last_verified_at = datetime.now(timezone.utc).replace(tzinfo=None)

    def record_miss(self, golden_script_id: uuid.UUID, *, step_index: int, cache_key: str) -> None:
        with session_scope() as session:
            row = (
                session.query(ActionCache)
                .filter_by(golden_script_id=golden_script_id, step_index=step_index, cache_key=cache_key)
                .one_or_none()
            )
            if row is not None:
                row.miss_count += 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_action_cache.py -v`
Expected: `PASS` on all three tests. If storing `last_verified_at` raises a driver error about timezone-aware vs. naive datetimes, confirm the column is a naive `DateTime` (it is, per `db/schema.sql`) and that `.replace(tzinfo=None)` is applied before assignment — the value must be naive UTC, not timezone-aware, to match the column type.

- [ ] **Step 5: Commit**

```bash
git add engine/src/atlas_engine/brain/cache.py engine/tests/test_action_cache.py
git commit -m "feat: add DB-backed action cache for resolved selectors"
```

---

### Task 6: Wire the action cache into BrainLoop's repair path

**Files:**
- Modify: `engine/src/atlas_engine/brain/loop.py` (`BrainLoop.__init__`, `BrainLoop._repair`)
- Create: `engine/tests/test_brain_loop_cache.py`

**Interfaces:**
- Consumes: `ActionCacheStore`, `compute_cache_key` (Task 5).
- Produces: `BrainLoop(page, on_off_track=None, llm=None, action_cache: ActionCacheStore | None = None, golden_script_id: uuid.UUID | None = None)`. When both `action_cache` and `golden_script_id` are given, an off-track step first tries any cached selector (soft-miss: failure just falls through) before calling the LLM; a successful LLM repair writes a fresh cache entry. When either is `None` (the Phase 0 default), behavior is byte-for-byte unchanged from before this task.

- [ ] **Step 1: Write the failing test**

`engine/tests/test_brain_loop_cache.py`:
```python
import uuid

from playwright.sync_api import sync_playwright

from atlas_engine.brain.cache import ActionCacheStore
from atlas_engine.brain.loop import BrainLoop
from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, Organization
from atlas_engine.db.repo import get_or_create_portal_profile
from atlas_engine.llm.client import FakeLlmClient


def _make_golden_script_id() -> uuid.UUID:
    with session_scope() as session:
        org = Organization(name="Cache Loop Org", slug=f"cache-loop-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        profile = get_or_create_portal_profile(session, str(org.id), "cacheloop.example.com")
        golden = GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=1, script_blob_ref="v1.py")
        session.add(golden)
        session.flush()
        return golden.id


def test_brain_loop_uses_cached_selector_before_calling_llm_again(fixture_portal_url):
    golden_script_id = _make_golden_script_id()
    trajectory = ReferenceTrajectory(
        steps=(TrajectoryStep(0, "click", [{"selector": "#stale-selector", "rank": 0}], None, "dom_changed"),)
    )
    fake_llm = FakeLlmClient(responses=[{"action": "replace_selector", "selector": "#generate-report-btn"}])
    cache = ActionCacheStore()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(f"{fixture_portal_url}/dashboard.html")
        outcomes_1 = BrainLoop(
            page, llm=fake_llm, action_cache=cache, golden_script_id=golden_script_id
        ).run(trajectory)

        page.goto(f"{fixture_portal_url}/dashboard.html")  # fresh page, same DOM shape
        outcomes_2 = BrainLoop(
            page, llm=fake_llm, action_cache=cache, golden_script_id=golden_script_id
        ).run(trajectory)

        browser.close()

    assert outcomes_1[0].on_track is True
    assert outcomes_2[0].on_track is True
    assert len(fake_llm.calls) == 1  # the second run's repair came from the cache, not the LLM
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_brain_loop_cache.py -v`
Expected: `FAIL` — `BrainLoop.__init__()` doesn't accept `action_cache`/`golden_script_id` yet (`TypeError: unexpected keyword argument`).

- [ ] **Step 3: Modify `engine/src/atlas_engine/brain/loop.py`**

Add these imports at the top:
```python
import hashlib
import uuid
from urllib.parse import urlparse

from atlas_engine.brain.cache import ActionCacheStore, compute_cache_key
from atlas_engine.llm.sanitize import sanitize_dom
```

Replace the `BrainLoop` class with:

```python
class BrainLoop:
    """perceive -> act -> evaluate -> think only if off-track.

    Without `llm`, an off-track step is reported via `on_off_track` and
    left off-track (Task 8 of the Phase 0 plan — still used by tests that
    only exercise the happy path). With `llm`, an off-track step triggers
    exactly one bounded repair attempt via `think()`. With `action_cache`
    and `golden_script_id` both given, a repair is tried from the cache
    first — a cache miss is never fatal, it just falls through to the LLM.
    """

    def __init__(
        self,
        page,
        on_off_track: Callable[[TrajectoryStep, StepOutcome], None] | None = None,
        llm: LlmClient | None = None,
        action_cache: ActionCacheStore | None = None,
        golden_script_id: uuid.UUID | None = None,
    ):
        self.page = page
        self.on_off_track = on_off_track
        self.llm = llm
        self.action_cache = action_cache
        self.golden_script_id = golden_script_id

    def run(self, trajectory: ReferenceTrajectory) -> list[StepOutcome]:
        outcomes = []
        for step in trajectory.steps:
            try:
                outcome = run_step(self.page, step)
            except Exception:
                outcome = StepOutcome(
                    step_index=step.index, on_track=False,
                    url_after=self.page.url, dom_after=self.page.content(),
                    resolved_selector=_first_selector(step),
                )

            if not outcome.on_track:
                if self.on_off_track is not None:
                    self.on_off_track(step, outcome)
                outcome = self._repair(step, outcome)

            outcomes.append(outcome)
        return outcomes

    def _cache_key_for(self, step: TrajectoryStep) -> str:
        original_selector = step.selector_candidates[0]["selector"] if step.selector_candidates else ""
        instruction = f"{step.kind} {original_selector}"
        url_pattern = urlparse(self.page.url).path
        dom_fingerprint = hashlib.sha256(sanitize_dom(self.page.content()).encode("utf-8")).hexdigest()
        return compute_cache_key(instruction=instruction, url_pattern=url_pattern, dom_fingerprint=dom_fingerprint)

    def _repair(self, step: TrajectoryStep, outcome: StepOutcome) -> StepOutcome:
        cache_key = None
        if self.action_cache is not None and self.golden_script_id is not None:
            cache_key = self._cache_key_for(step)
            cached = self.action_cache.get(self.golden_script_id, step_index=step.index, cache_key=cache_key)
            if cached:
                try:
                    cached_outcome = run_step(self.page, step, selector=cached[0]["selector"], timeout_ms=1000)
                    if cached_outcome.on_track:
                        return cached_outcome
                except Exception:
                    pass
                self.action_cache.record_miss(self.golden_script_id, step_index=step.index, cache_key=cache_key)

        if self.llm is None:
            return outcome

        repair = think(self.llm, step, self.page, outcome)
        if repair.action == "replace_selector":
            result = run_step(self.page, step, selector=repair.selector, timeout_ms=None)
            if result.on_track and cache_key is not None:
                self.action_cache.put(
                    self.golden_script_id, step_index=step.index, cache_key=cache_key,
                    selector_candidates=[{"selector": result.resolved_selector, "rank": 0}], method=step.kind,
                )
            return result
        if repair.action == "force_click":
            return run_step(self.page, step, force=True, timeout_ms=None)
        return outcome
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_brain_loop_cache.py -v`
Expected: `PASS`. The first `BrainLoop.run()` call makes exactly one LLM call and writes a cache entry; the second call, on a fresh page with the identical DOM, hits that cache entry and needs no LLM call at all.

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: `PASS` on everything, including Task 8/9's original Phase 0 repair tests (`test_brain_repair.py`), which construct `BrainLoop` without `action_cache`/`golden_script_id` and must behave exactly as before.

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/brain/loop.py engine/tests/test_brain_loop_cache.py
git commit -m "feat: wire the action cache into BrainLoop's repair path"
```

---

### Task 7: Trajectory diff computation

**Files:**
- Create: `engine/src/atlas_engine/verify/heal.py` (diff function only for this task; `heal_golden_script` etc. come in Task 9)
- Create: `engine/tests/test_trajectory_diff.py`

**Interfaces:**
- Consumes: `ReferenceTrajectory`, `TrajectoryStep` (Phase 0).
- Produces: `compute_trajectory_diff(old: ReferenceTrajectory, new: ReferenceTrajectory) -> list[dict]` — one `{"step_index": int, "field": "selector", "old_value": str, "new_value": str}` entry per step whose primary selector changed; empty list if identical.

- [ ] **Step 1: Write the failing test**

`engine/tests/test_trajectory_diff.py`:
```python
from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.verify.heal import compute_trajectory_diff


def test_compute_trajectory_diff_flags_changed_selector():
    old = ReferenceTrajectory(
        steps=(TrajectoryStep(0, "click", [{"selector": "#old-btn", "rank": 0}], None, "dom_changed"),)
    )
    new = ReferenceTrajectory(
        steps=(TrajectoryStep(0, "click", [{"selector": "#new-btn", "rank": 0}], None, "dom_changed"),)
    )

    diff = compute_trajectory_diff(old, new)

    assert diff == [{"step_index": 0, "field": "selector", "old_value": "#old-btn", "new_value": "#new-btn"}]


def test_compute_trajectory_diff_empty_when_identical():
    trajectory = ReferenceTrajectory(
        steps=(TrajectoryStep(0, "click", [{"selector": "#btn", "rank": 0}], None, "dom_changed"),)
    )

    assert compute_trajectory_diff(trajectory, trajectory) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trajectory_diff.py -v`
Expected: `FAIL` with `ModuleNotFoundError: No module named 'atlas_engine.verify.heal'`.

- [ ] **Step 3: Write `engine/src/atlas_engine/verify/heal.py`** (diff function only — more is added in Task 9)

```python
from atlas_engine.brain.models import ReferenceTrajectory


def _primary_selector(trajectory: ReferenceTrajectory, index: int) -> str:
    candidates = trajectory.steps[index].selector_candidates
    return candidates[0]["selector"] if candidates else ""


def compute_trajectory_diff(old: ReferenceTrajectory, new: ReferenceTrajectory) -> list[dict]:
    diff = []
    for i in range(len(old.steps)):
        old_selector = _primary_selector(old, i)
        new_selector = _primary_selector(new, i)
        if old_selector != new_selector:
            diff.append(
                {"step_index": i, "field": "selector", "old_value": old_selector, "new_value": new_selector}
            )
    return diff
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_trajectory_diff.py -v`
Expected: `PASS`.

- [ ] **Step 5: Commit**

```bash
git add engine/src/atlas_engine/verify/heal.py engine/tests/test_trajectory_diff.py
git commit -m "feat: add trajectory diff computation for heal events"
```

---

### Task 8: LLM client factory

**Files:**
- Create: `engine/src/atlas_engine/llm/factory.py`
- Create: `engine/tests/test_llm_factory.py`

**Interfaces:**
- Consumes: `AzureOpenAiClient`, `LlmClient` (Phase 0).
- Produces: `create_llm_client() -> LlmClient | None` — reads `ATLAS_AZURE_OPENAI_ENDPOINT` / `ATLAS_AZURE_OPENAI_API_KEY` / `ATLAS_AZURE_OPENAI_MODEL` from the environment at call time (not cached — safe to call repeatedly, safe for tests to `monkeypatch.setenv`); returns `None` when unconfigured.

- [ ] **Step 1: Write the failing test**

`engine/tests/test_llm_factory.py`:
```python
from atlas_engine.llm.client import AzureOpenAiClient
from atlas_engine.llm.factory import create_llm_client


def test_create_llm_client_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv("ATLAS_AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("ATLAS_AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ATLAS_AZURE_OPENAI_MODEL", raising=False)

    assert create_llm_client() is None


def test_create_llm_client_returns_azure_client_when_configured(monkeypatch):
    monkeypatch.setenv("ATLAS_AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("ATLAS_AZURE_OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("ATLAS_AZURE_OPENAI_MODEL", "gpt-4o-mini")

    client = create_llm_client()

    assert isinstance(client, AzureOpenAiClient)
    assert client.endpoint == "https://example.openai.azure.com"
    assert client.deployment == "gpt-4o-mini"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm_factory.py -v`
Expected: `FAIL` with `ModuleNotFoundError: No module named 'atlas_engine.llm.factory'`.

- [ ] **Step 3: Write `engine/src/atlas_engine/llm/factory.py`**

```python
import os

from atlas_engine.llm.client import AzureOpenAiClient, LlmClient


def create_llm_client() -> LlmClient | None:
    """Reads Azure OpenAI config from the environment; returns None (no
    repair capability — an off-track step stays off-track) if unconfigured.
    Reads fresh on every call (no cached singleton) so tests can
    monkeypatch the environment freely. Kept provider-agnostic in shape
    even though only Azure is implemented today (architecture spec §11)."""
    endpoint = os.environ.get("ATLAS_AZURE_OPENAI_ENDPOINT")
    api_key = os.environ.get("ATLAS_AZURE_OPENAI_API_KEY")
    model = os.environ.get("ATLAS_AZURE_OPENAI_MODEL")

    if not (endpoint and api_key and model):
        return None

    return AzureOpenAiClient(endpoint=endpoint, api_key=api_key, deployment=model)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_llm_factory.py -v`
Expected: `PASS`.

- [ ] **Step 5: Commit**

```bash
git add engine/src/atlas_engine/llm/factory.py engine/tests/test_llm_factory.py
git commit -m "feat: add environment-driven LLM client factory"
```

---

### Task 9: heal_golden_script()

**Files:**
- Modify: `engine/src/atlas_engine/verify/heal.py` (add `heal_golden_script`)
- Create: `engine/tests/test_heal.py`

**Interfaces:**
- Consumes: `trajectory_from_dict` (Task 2), `ActionCacheStore` (Task 5), `BrainLoop` (Task 6), `compute_trajectory_diff` (Task 7), `next_golden_script_version` (Task 1), `emit_golden_script`/`certify_golden_script` (Phase 0).
- Produces: `heal_golden_script(golden_script_id: uuid.UUID, *, llm: LlmClient | None = None, trigger: str = "on_demand") -> dict`. Returns `{"status": "already_certified", ...}` if every step is still on-track live, or `{"status": "healed", "heal_event_id": str, "new_golden_script_id": str, "new_status": "certified"|"draft", "diff": list[dict]}` otherwise. The new version is **never** `active` — always `draft` or `certified`, pending approval (Task 10).

- [ ] **Step 1: Write the failing test**

`engine/tests/test_heal.py`:
```python
import uuid

from playwright.sync_api import sync_playwright

from atlas_engine.capture.recorder import CaptureSession
from atlas_engine.cli import author_from_recorded_actions
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, HealEvent, Organization
from atlas_engine.llm.client import FakeLlmClient
from atlas_engine.verify.heal import heal_golden_script


def _author_against_fixture(fixture_portal_url) -> tuple[str, uuid.UUID]:
    with session_scope() as session:
        org = Organization(name="Heal Test Org", slug=f"heal-test-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

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

    result = author_from_recorded_actions(org_id, start_url, capture.actions)
    assert result["status"] == "certified"
    return org_id, uuid.UUID(result["golden_script_id"])


def test_heal_golden_script_reports_already_certified_when_nothing_changed(fixture_portal_url):
    _org_id, golden_script_id = _author_against_fixture(fixture_portal_url)

    result = heal_golden_script(golden_script_id)

    assert result["status"] == "already_certified"


def test_heal_golden_script_raises_when_golden_script_has_no_recording():
    with session_scope() as session:
        from atlas_engine.db.models import Organization as Org
        from atlas_engine.db.repo import get_or_create_portal_profile

        org = Org(name="No Recording Org", slug=f"no-recording-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        profile = get_or_create_portal_profile(session, str(org.id), "norecording.example.com")
        golden = GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=1, script_blob_ref="v1.py")
        session.add(golden)
        session.flush()
        golden_id = golden.id

    import pytest
    with pytest.raises(ValueError, match="no linked recording"):
        heal_golden_script(golden_id)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_heal.py -v`
Expected: `FAIL` with `ImportError` (`heal_golden_script` doesn't exist in `verify.heal` yet).

- [ ] **Step 3: Add `heal_golden_script` to `engine/src/atlas_engine/verify/heal.py`**

Add these imports at the top of the file (alongside the existing `ReferenceTrajectory` import from Task 7):
```python
import json
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

from atlas_engine.brain.cache import ActionCacheStore
from atlas_engine.brain.loop import BrainLoop
from atlas_engine.brain.models import TrajectoryStep
from atlas_engine.brain.serialize import trajectory_from_dict
from atlas_engine.codegen.emit import emit_golden_script
from atlas_engine.config import settings
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, HealEvent, PortalProfile
from atlas_engine.db.models import ReferenceTrajectory as ReferenceTrajectoryModel
from atlas_engine.db.repo import next_golden_script_version
from atlas_engine.llm.client import LlmClient
from atlas_engine.storage import LocalRunStore
from atlas_engine.verify.certify import certify_golden_script
```

(Note: `ReferenceTrajectory` already refers to the dataclass from `atlas_engine.brain.models` imported by Task 7 — the DB model of the same name is aliased to `ReferenceTrajectoryModel` here to avoid a collision.)

Append this function to the file:

```python
def heal_golden_script(
    golden_script_id: uuid.UUID, *, llm: LlmClient | None = None, trigger: str = "on_demand"
) -> dict:
    """Re-validates an existing golden script's trajectory against a live
    session. If every step is still on-track, nothing changes. If a step
    has drifted, this rebuilds the trajectory from whatever selector
    actually worked live (original, cached, or LLM-repaired), emits and
    certifies a new draft version, and records a HealEvent — the new
    version is never made active here; only approve_heal_event (Task 10)
    can do that.
    """
    with session_scope() as session:
        golden = session.get(GoldenScript, golden_script_id)
        if golden is None:
            raise ValueError(f"No such golden script: {golden_script_id}")
        if golden.created_from_recording_id is None:
            raise ValueError("Golden script has no linked recording to re-validate against")

        profile = session.get(PortalProfile, golden.portal_profile_id)
        trajectory_row = (
            session.query(ReferenceTrajectoryModel)
            .filter_by(recording_id=golden.created_from_recording_id)
            .one()
        )

        org_id = golden.org_id
        portal_profile_id = golden.portal_profile_id
        created_from_recording_id = golden.created_from_recording_id
        start_url = profile.inferred_login_url or f"https://{profile.domain}"
        trajectory_blob_ref = trajectory_row.trajectory_blob_ref

    old_trajectory = trajectory_from_dict(json.loads(Path(trajectory_blob_ref).read_text()))

    cache = ActionCacheStore()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(start_url)
        outcomes = BrainLoop(page, llm=llm, action_cache=cache, golden_script_id=golden_script_id).run(
            old_trajectory
        )
        browser.close()

    if all(outcome.on_track for outcome in outcomes):
        return {"status": "already_certified", "heal_event_id": None, "golden_script_id": str(golden_script_id)}

    new_trajectory_steps = tuple(
        TrajectoryStep(
            index=step.index,
            kind=step.kind,
            selector_candidates=[{"selector": outcome.resolved_selector, "rank": 0}],
            value=step.value,
            expected_effect=step.expected_effect,
        )
        for step, outcome in zip(old_trajectory.steps, outcomes)
    )
    from atlas_engine.brain.models import ReferenceTrajectory as ReferenceTrajectoryData

    new_trajectory = ReferenceTrajectoryData(steps=new_trajectory_steps)
    diff = compute_trajectory_diff(old_trajectory, new_trajectory)

    store = LocalRunStore(root=settings.ensure_run_root())
    run_dir = store.new_run_dir(prefix="goldens")
    script_text = emit_golden_script(new_trajectory, start_url=start_url)
    script_path = run_dir.path / "golden.py"
    script_path.write_text(script_text)

    if all(outcome.on_track for outcome in outcomes):
        certification = certify_golden_script(script_path, runs=2)
        new_status = "certified" if certification.is_certified else "draft"
    else:
        new_status = "draft"

    with session_scope() as session:
        new_golden = GoldenScript(
            org_id=org_id,
            portal_profile_id=portal_profile_id,
            version=next_golden_script_version(session, portal_profile_id),
            parent_version_id=golden_script_id,
            script_blob_ref=str(script_path),
            status=new_status,
            created_from_recording_id=created_from_recording_id,
        )
        session.add(new_golden)
        session.flush()

        heal_event = HealEvent(
            org_id=org_id,
            golden_script_id=golden_script_id,
            proposed_version_id=new_golden.id,
            trigger=trigger,
            diff_summary={"changes": diff},
            status="pending_approval",
        )
        session.add(heal_event)
        session.flush()

        new_golden.created_from_heal_event_id = heal_event.id

        heal_event_id = heal_event.id
        new_golden_id = new_golden.id

    return {
        "status": "healed",
        "heal_event_id": str(heal_event_id),
        "new_golden_script_id": str(new_golden_id),
        "new_status": new_status,
        "diff": diff,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_heal.py -v`
Expected: `PASS` on both tests.

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: `PASS` on everything.

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/verify/heal.py engine/tests/test_heal.py
git commit -m "feat: add heal_golden_script — on-demand re-validation and repair"
```

---

### Task 10: Approve / reject heal events

**Files:**
- Modify: `engine/src/atlas_engine/verify/heal.py` (add `approve_heal_event`, `reject_heal_event`)
- Create: `engine/tests/test_heal_approval.py`

**Interfaces:**
- Consumes: `HealEvent`, `GoldenScript` models.
- Produces: `approve_heal_event(heal_event_id: uuid.UUID, *, approved_by: uuid.UUID | None = None) -> dict` (returns `{"heal_event_id", "deprecated_version", "active_version"}`; raises `ValueError` if the event isn't `pending_approval` or the proposed version isn't `certified`); `reject_heal_event(heal_event_id: uuid.UUID, *, resolved_by: uuid.UUID | None = None) -> dict` (returns `{"heal_event_id", "status": "rejected"}`).

- [ ] **Step 1: Write the failing test**

`engine/tests/test_heal_approval.py`:
```python
import uuid

from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, HealEvent, Organization
from atlas_engine.db.repo import get_or_create_portal_profile
from atlas_engine.verify.heal import approve_heal_event, reject_heal_event


def _make_pending_heal_event(slug_prefix: str, new_status: str = "certified"):
    with session_scope() as session:
        org = Organization(name="Approval Test Org", slug=f"{slug_prefix}-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        profile = get_or_create_portal_profile(session, str(org.id), f"{slug_prefix}.example.com")

        old_golden = GoldenScript(
            org_id=org.id, portal_profile_id=profile.id, version=1, script_blob_ref="v1.py", status="active"
        )
        session.add(old_golden)
        session.flush()

        new_golden = GoldenScript(
            org_id=org.id, portal_profile_id=profile.id, version=2, script_blob_ref="v2.py",
            status=new_status, parent_version_id=old_golden.id,
        )
        session.add(new_golden)
        session.flush()

        heal_event = HealEvent(
            org_id=org.id, golden_script_id=old_golden.id, proposed_version_id=new_golden.id,
            trigger="on_demand", status="pending_approval",
        )
        session.add(heal_event)
        session.flush()

        return heal_event.id, old_golden.id, new_golden.id


def test_approve_heal_event_activates_new_version_and_deprecates_old():
    heal_event_id, old_id, new_id = _make_pending_heal_event("approve-test-org")

    result = approve_heal_event(heal_event_id)

    assert result["deprecated_version"] == 1
    assert result["active_version"] == 2

    with session_scope() as session:
        assert session.get(GoldenScript, old_id).status == "deprecated"
        assert session.get(GoldenScript, new_id).status == "active"
        assert session.get(HealEvent, heal_event_id).status == "approved"


def test_approve_heal_event_rejects_a_version_that_is_not_certified():
    heal_event_id, _old_id, _new_id = _make_pending_heal_event("approve-draft-org", new_status="draft")

    import pytest
    with pytest.raises(ValueError, match="not certified"):
        approve_heal_event(heal_event_id)


def test_reject_heal_event_deprecates_proposed_version_without_activating_it():
    heal_event_id, old_id, new_id = _make_pending_heal_event("reject-test-org")

    reject_heal_event(heal_event_id)

    with session_scope() as session:
        assert session.get(GoldenScript, old_id).status == "active"  # unchanged
        assert session.get(GoldenScript, new_id).status == "deprecated"
        assert session.get(HealEvent, heal_event_id).status == "rejected"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_heal_approval.py -v`
Expected: `FAIL` with `ImportError` (`approve_heal_event`/`reject_heal_event` don't exist yet).

- [ ] **Step 3: Add both functions to `engine/src/atlas_engine/verify/heal.py`**

Add this import at the top:
```python
from datetime import datetime, timezone
```

Append:
```python
def approve_heal_event(heal_event_id: uuid.UUID, *, approved_by: uuid.UUID | None = None) -> dict:
    with session_scope() as session:
        heal_event = session.get(HealEvent, heal_event_id)
        if heal_event is None:
            raise ValueError(f"No such heal event: {heal_event_id}")
        if heal_event.status != "pending_approval":
            raise ValueError(f"Heal event {heal_event_id} is not pending approval (status={heal_event.status})")

        old_golden = session.get(GoldenScript, heal_event.golden_script_id)
        new_golden = session.get(GoldenScript, heal_event.proposed_version_id)

        if new_golden.status != "certified":
            raise ValueError(f"Cannot approve a version that is not certified (status={new_golden.status})")

        old_golden.status = "deprecated"
        new_golden.status = "active"
        heal_event.status = "approved"
        heal_event.resolved_by = approved_by
        heal_event.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)

        return {
            "heal_event_id": str(heal_event_id),
            "deprecated_version": old_golden.version,
            "active_version": new_golden.version,
        }


def reject_heal_event(heal_event_id: uuid.UUID, *, resolved_by: uuid.UUID | None = None) -> dict:
    with session_scope() as session:
        heal_event = session.get(HealEvent, heal_event_id)
        if heal_event is None:
            raise ValueError(f"No such heal event: {heal_event_id}")
        if heal_event.status != "pending_approval":
            raise ValueError(f"Heal event {heal_event_id} is not pending approval (status={heal_event.status})")

        new_golden = session.get(GoldenScript, heal_event.proposed_version_id)
        new_golden.status = "deprecated"
        heal_event.status = "rejected"
        heal_event.resolved_by = resolved_by
        heal_event.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)

        return {"heal_event_id": str(heal_event_id), "status": "rejected"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_heal_approval.py -v`
Expected: `PASS` on all three tests.

- [ ] **Step 5: Commit**

```bash
git add engine/src/atlas_engine/verify/heal.py engine/tests/test_heal_approval.py
git commit -m "feat: add heal event approve/reject workflow"
```

---

### Task 11: CLI commands — heal, approve-heal, reject-heal

**Files:**
- Modify: `engine/src/atlas_engine/cli.py`
- Create: `engine/tests/test_cli_heal.py`

**Interfaces:**
- Consumes: `heal_golden_script`, `approve_heal_event`, `reject_heal_event` (Tasks 9-10); `create_llm_client` (Task 8).
- Produces: three new Click commands: `atlas heal <golden-script-id>`, `atlas approve-heal <heal-event-id>`, `atlas reject-heal <heal-event-id>`.

- [ ] **Step 1: Write the failing test**

`engine/tests/test_cli_heal.py`:
```python
import uuid

from click.testing import CliRunner

from atlas_engine.cli import main
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, HealEvent, Organization
from atlas_engine.db.repo import get_or_create_portal_profile


def test_heal_command_reports_already_certified_for_an_unchanged_portal(fixture_portal_url):
    from atlas_engine.capture.recorder import CaptureSession
    from atlas_engine.cli import author_from_recorded_actions
    from playwright.sync_api import sync_playwright

    with session_scope() as session:
        org = Organization(name="CLI Heal Org", slug=f"cli-heal-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

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

    result = author_from_recorded_actions(org_id, start_url, capture.actions)
    golden_script_id = result["golden_script_id"]

    runner = CliRunner()
    cli_result = runner.invoke(main, ["heal", golden_script_id])

    assert cli_result.exit_code == 0, cli_result.output
    assert "already" in cli_result.output.lower()


def test_approve_heal_and_reject_heal_commands():
    with session_scope() as session:
        org = Organization(name="CLI Approve Org", slug=f"cli-approve-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        profile = get_or_create_portal_profile(session, str(org.id), "cli-approve.example.com")

        old_golden = GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=1, script_blob_ref="v1.py", status="active")
        session.add(old_golden)
        session.flush()
        new_golden = GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=2, script_blob_ref="v2.py", status="certified", parent_version_id=old_golden.id)
        session.add(new_golden)
        session.flush()
        heal_event = HealEvent(org_id=org.id, golden_script_id=old_golden.id, proposed_version_id=new_golden.id, trigger="on_demand", status="pending_approval")
        session.add(heal_event)
        session.flush()
        heal_event_id = str(heal_event.id)

    runner = CliRunner()
    result = runner.invoke(main, ["approve-heal", heal_event_id])

    assert result.exit_code == 0, result.output
    assert "v1" in result.output and "v2" in result.output


def test_reject_heal_command():
    with session_scope() as session:
        org = Organization(name="CLI Reject Org", slug=f"cli-reject-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        profile = get_or_create_portal_profile(session, str(org.id), "cli-reject.example.com")

        old_golden = GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=1, script_blob_ref="v1.py", status="active")
        session.add(old_golden)
        session.flush()
        new_golden = GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=2, script_blob_ref="v2.py", status="certified", parent_version_id=old_golden.id)
        session.add(new_golden)
        session.flush()
        heal_event = HealEvent(org_id=org.id, golden_script_id=old_golden.id, proposed_version_id=new_golden.id, trigger="on_demand", status="pending_approval")
        session.add(heal_event)
        session.flush()
        heal_event_id = str(heal_event.id)

    runner = CliRunner()
    result = runner.invoke(main, ["reject-heal", heal_event_id])

    assert result.exit_code == 0, result.output
    assert "rejected" in result.output.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_heal.py -v`
Expected: `FAIL` — `Error: No such command 'heal'.`

- [ ] **Step 3: Add the three commands to `engine/src/atlas_engine/cli.py`**

Add these imports at the top:
```python
from atlas_engine.llm.factory import create_llm_client
from atlas_engine.verify.heal import approve_heal_event, heal_golden_script, reject_heal_event
```

Append at the end of the file (before the `if __name__ == "__main__":` block):

```python
@main.command("heal")
@click.argument("golden_script_id")
def heal(golden_script_id: str) -> None:
    """Re-validate a golden script against its live portal and, if a step
    has drifted, propose a repaired version pending approval."""
    llm = create_llm_client()
    result = heal_golden_script(uuid.UUID(golden_script_id), llm=llm)
    if result["status"] == "already_certified":
        click.echo("Already on-track — nothing to heal.")
    else:
        click.echo(f"Heal event {result['heal_event_id']} created — new version status: {result['new_status']}")
        click.echo(f"Diff: {json.dumps(result['diff'], indent=2)}")


@main.command("approve-heal")
@click.argument("heal_event_id")
def approve_heal(heal_event_id: str) -> None:
    """Approve a pending heal event: activate its proposed version and
    deprecate the one it replaces."""
    result = approve_heal_event(uuid.UUID(heal_event_id))
    click.echo(f"Approved: v{result['deprecated_version']} -> v{result['active_version']} is now active")


@main.command("reject-heal")
@click.argument("heal_event_id")
def reject_heal(heal_event_id: str) -> None:
    """Reject a pending heal event: the proposed version is deprecated,
    the currently active version is left untouched."""
    reject_heal_event(uuid.UUID(heal_event_id))
    click.echo("Rejected — proposed version deprecated, active version unchanged")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_heal.py -v`
Expected: `PASS` on all three tests.

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: `PASS` on everything.

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/cli.py engine/tests/test_cli_heal.py
git commit -m "feat: add atlas heal / approve-heal / reject-heal commands"
```

---

### Task 12: API endpoints — heal, approve, reject

**Files:**
- Modify: `engine/src/atlas_engine/api.py`
- Create: `engine/tests/test_api_heal.py`

**Interfaces:**
- Consumes: same functions as Task 11.
- Produces: `POST /golden-scripts/{golden_script_id}/heal`, `POST /heal-events/{heal_event_id}/approve`, `POST /heal-events/{heal_event_id}/reject`.

- [ ] **Step 1: Write the failing test**

`engine/tests/test_api_heal.py`:
```python
import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, HealEvent, Organization
from atlas_engine.db.repo import get_or_create_portal_profile


def test_approve_and_reject_endpoints():
    with session_scope() as session:
        org = Organization(name="API Heal Org", slug=f"api-heal-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        profile = get_or_create_portal_profile(session, str(org.id), "api-heal.example.com")

        old_golden = GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=1, script_blob_ref="v1.py", status="active")
        session.add(old_golden)
        session.flush()
        new_golden = GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=2, script_blob_ref="v2.py", status="certified", parent_version_id=old_golden.id)
        session.add(new_golden)
        session.flush()
        heal_event = HealEvent(org_id=org.id, golden_script_id=old_golden.id, proposed_version_id=new_golden.id, trigger="on_demand", status="pending_approval")
        session.add(heal_event)
        session.flush()
        heal_event_id = str(heal_event.id)

    client = TestClient(app)
    response = client.post(f"/heal-events/{heal_event_id}/approve")

    assert response.status_code == 200
    body = response.json()
    assert body["deprecated_version"] == 1
    assert body["active_version"] == 2


def test_reject_endpoint():
    with session_scope() as session:
        org = Organization(name="API Reject Org", slug=f"api-reject-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        profile = get_or_create_portal_profile(session, str(org.id), "api-reject.example.com")

        old_golden = GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=1, script_blob_ref="v1.py", status="active")
        session.add(old_golden)
        session.flush()
        new_golden = GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=2, script_blob_ref="v2.py", status="certified", parent_version_id=old_golden.id)
        session.add(new_golden)
        session.flush()
        heal_event = HealEvent(org_id=org.id, golden_script_id=old_golden.id, proposed_version_id=new_golden.id, trigger="on_demand", status="pending_approval")
        session.add(heal_event)
        session.flush()
        heal_event_id = str(heal_event.id)

    client = TestClient(app)
    response = client.post(f"/heal-events/{heal_event_id}/reject")

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


def test_heal_endpoint_reports_already_certified(fixture_portal_url):
    from atlas_engine.capture.recorder import CaptureSession
    from atlas_engine.cli import author_from_recorded_actions
    from playwright.sync_api import sync_playwright

    with session_scope() as session:
        org = Organization(name="API Heal Endpoint Org", slug=f"api-heal-endpoint-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

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

    result = author_from_recorded_actions(org_id, start_url, capture.actions)

    client = TestClient(app)
    response = client.post(f"/golden-scripts/{result['golden_script_id']}/heal")

    assert response.status_code == 200
    assert response.json()["status"] == "already_certified"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_heal.py -v`
Expected: `FAIL` with `404 Not Found` on all three requests.

- [ ] **Step 3: Add the endpoints to `engine/src/atlas_engine/api.py`**

Add these imports at the top:
```python
import uuid

from atlas_engine.llm.factory import create_llm_client
from atlas_engine.verify.heal import approve_heal_event, heal_golden_script, reject_heal_event
```

Append:
```python
class HealResponse(BaseModel):
    status: str
    heal_event_id: str | None = None
    new_golden_script_id: str | None = None
    new_status: str | None = None
    diff: list[dict] | None = None
    golden_script_id: str | None = None


class ApprovalResponse(BaseModel):
    heal_event_id: str
    status: str | None = None
    deprecated_version: int | None = None
    active_version: int | None = None


@app.post("/golden-scripts/{golden_script_id}/heal", response_model=HealResponse)
def heal_endpoint(golden_script_id: str) -> HealResponse:
    llm = create_llm_client()
    result = heal_golden_script(uuid.UUID(golden_script_id), llm=llm)
    return HealResponse(**result)


@app.post("/heal-events/{heal_event_id}/approve", response_model=ApprovalResponse)
def approve_endpoint(heal_event_id: str) -> ApprovalResponse:
    result = approve_heal_event(uuid.UUID(heal_event_id))
    return ApprovalResponse(**result)


@app.post("/heal-events/{heal_event_id}/reject", response_model=ApprovalResponse)
def reject_endpoint(heal_event_id: str) -> ApprovalResponse:
    result = reject_heal_event(uuid.UUID(heal_event_id))
    return ApprovalResponse(**result)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_api_heal.py -v`
Expected: `PASS` on all three tests.

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: `PASS` on everything.

- [ ] **Step 6: Commit**

```bash
git add engine/src/atlas_engine/api.py engine/tests/test_api_heal.py
git commit -m "feat: add heal/approve/reject API endpoints"
```

---

### Task 13: Fixture portal drift mode + the capstone end-to-end test

**Files:**
- Create: `engine/tests/fixtures/fixture_portal/dashboard_drifted.html`
- Modify: `engine/tests/fixtures/fixture_portal/server.py`
- Modify: `engine/tests/conftest.py`
- Create: `engine/tests/test_end_to_end_heal.py`

**Interfaces:**
- Produces: `FixturePortalServer.set_drift_mode(enabled: bool) -> None` — when enabled, requests for `/dashboard.html` are served the drifted variant instead (the report button's id changes from `#generate-report-btn` to `#run-report-btn`). A new session-scoped fixture `fixture_portal_server` yields the server object itself (for drift control); `fixture_portal_url` is refactored to depend on it, so every existing test using `fixture_portal_url` keeps working unchanged.

- [ ] **Step 1: Write `engine/tests/fixtures/fixture_portal/dashboard_drifted.html`**

```html
<!doctype html>
<html>
<head><title>Fixture Portal — Dashboard (drifted)</title></head>
<body>
  <h1 id="welcome">Welcome, demo</h1>
  <button id="run-report-btn">Generate report</button>
  <p id="report-status"></p>
  <a id="download-link" href="/report.csv" style="display:none">Download report.csv</a>
  <script>
    document.getElementById("run-report-btn").addEventListener("click", function () {
      document.getElementById("report-status").textContent = "Report ready";
      document.getElementById("download-link").style.display = "inline";
    });
  </script>
</body>
</html>
```

- [ ] **Step 2: Modify `engine/tests/fixtures/fixture_portal/server.py`**

Replace the file's contents with:

```python
"""Stdlib-only HTTP server serving the fixture portal — no extra
dependencies, so the test suite never needs network access to run."""

import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

_DIR = Path(__file__).parent


class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(_DIR), **kwargs)

    def do_GET(self):
        if self.path == "/":
            self.path = "/login.html"
        if self.path == "/dashboard.html" and getattr(self.server, "drift_mode", False):
            self.path = "/dashboard_drifted.html"
        if self.path == "/report.csv":
            body = b"patient_id,report_status\n1,ready\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass  # silence per-request logging in test output


class FixturePortalServer:
    def __init__(self, port: int = 8731):
        self.port = port
        self._httpd = HTTPServer(("127.0.0.1", port), _Handler)
        self._httpd.drift_mode = False
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def set_drift_mode(self, enabled: bool) -> None:
        """When enabled, /dashboard.html serves the drifted variant — used
        to simulate a portal UI change for healing tests."""
        self._httpd.drift_mode = enabled

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
```

- [ ] **Step 3: Modify `engine/tests/conftest.py`**

Replace the `fixture_portal_url` fixture with:

```python
@pytest.fixture(scope="session")
def fixture_portal_server():
    server = FixturePortalServer(port=8731)
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="session")
def fixture_portal_url(fixture_portal_server):
    return fixture_portal_server.base_url
```

- [ ] **Step 4: Run the full suite to confirm the fixture refactor is backward-compatible**

Run: `pytest -v`
Expected: `PASS` on everything — every existing test that requests `fixture_portal_url` still works unchanged, since it's now just a thin fixture on top of `fixture_portal_server`.

- [ ] **Step 5: Write the capstone test**

`engine/tests/test_end_to_end_heal.py`:
```python
import uuid

from playwright.sync_api import sync_playwright

from atlas_engine.capture.recorder import CaptureSession
from atlas_engine.cli import author_from_recorded_actions
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, Organization
from atlas_engine.llm.client import FakeLlmClient
from atlas_engine.verify.heal import approve_heal_event, heal_golden_script


def test_portal_drift_is_detected_healed_and_approved(fixture_portal_url, fixture_portal_server):
    """The full loop the architecture spec describes: a portal's UI
    changes, the golden script starts failing, healing proposes a fix
    grounded in the live DOM, and only an explicit approval makes it live.
    """
    with session_scope() as session:
        org = Organization(name="Drift Org", slug=f"drift-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

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

    result = author_from_recorded_actions(org_id, start_url, capture.actions)
    assert result["status"] == "certified"
    golden_script_id = uuid.UUID(result["golden_script_id"])

    fixture_portal_server.set_drift_mode(True)
    try:
        fake_llm = FakeLlmClient(responses=[{"action": "replace_selector", "selector": "#run-report-btn"}])
        heal_result = heal_golden_script(golden_script_id, llm=fake_llm)

        assert heal_result["status"] == "healed"
        assert heal_result["new_status"] == "certified"
        assert any(change["new_value"] == "#run-report-btn" for change in heal_result["diff"])

        approval = approve_heal_event(uuid.UUID(heal_result["heal_event_id"]))
        assert approval["active_version"] == 2

        with session_scope() as session:
            old = session.get(GoldenScript, golden_script_id)
            new = session.get(GoldenScript, uuid.UUID(heal_result["new_golden_script_id"]))
            assert old.status == "deprecated"
            assert new.status == "active"
    finally:
        fixture_portal_server.set_drift_mode(False)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_end_to_end_heal.py -v`
Expected: `PASS`. This is the highest-value test in this plan — if it's green, the entire "portal changes -> detect -> heal -> approve -> new version live" loop from the architecture spec is proven to work, not just assumed.

- [ ] **Step 7: Run the entire suite one final time**

Run: `pytest -v`
Expected: `PASS` on every test file from both Phase 0 and Phase 1.

- [ ] **Step 8: Commit**

```bash
git add engine/tests/fixtures/fixture_portal/dashboard_drifted.html \
        engine/tests/fixtures/fixture_portal/server.py engine/tests/conftest.py \
        engine/tests/test_end_to_end_heal.py
git commit -m "test: add portal-drift fixture mode and the drift-heal-approve capstone test"
```

---

### Task 14: README updates

**Files:**
- Modify: `engine/README.md`

- [ ] **Step 1: Update the "What's next" section and add a "Healing a golden script" walkthrough**

Replace the "What's next" section's first bullet (about wiring the brain into authoring — now done) and the bullet about heal-and-approve (now also done) with an updated list, and add a new section above "What's next":

```markdown
## Healing a golden script

If a portal's UI changes, a certified golden script's steps can drift out
of date. Re-validate it against the live portal:

```bash
atlas heal <golden-script-id>
```

If every step still works, this reports "Already on-track — nothing to
heal." If something's off, it prints a diff and creates a new, unapproved
version — check `ATLAS_AZURE_OPENAI_ENDPOINT` / `_API_KEY` / `_MODEL` are
set in your environment first if you want an actual repair attempt rather
than just drift detection (`atlas_engine.llm.factory.create_llm_client`
returns `None`, and the step is simply left off-track, when they're unset).

Review the diff, then either:

```bash
atlas approve-heal <heal-event-id>   # activates the new version, deprecates the old one
atlas reject-heal <heal-event-id>    # discards the proposed version, nothing changes
```

A healed version is never active until you explicitly approve it.

## What's next

Phase 0 and Phase 1 together cover: record -> certified golden script ->
detect drift -> repair -> approve -> new version live, all provable without
real credentials or a real portal. Not built yet, each its own plan:

- The desktop EXE shell (PyInstaller + pywebview) and packaging.
- The cloud multi-tenant backend and the enterprise dashboard — including
  a UI for the approval queue this plan's CLI/API already support.
- The video/voice knowledge base.
```

- [ ] **Step 2: Commit**

```bash
git add engine/README.md
git commit -m "docs: document the heal/approve/reject workflow"
```

---

## Self-Review Notes

**Spec coverage:** action cache with parameter-value-excluding keys and soft-miss fallback (§7.1) → Tasks 5–6; repair persistence as a durable, auditable HealEvent rather than a silent cache overwrite (§7.2) → Tasks 7, 9; the approval gate — a healed version is never active until an explicit approve call (§7.3) → Task 10; the LLM client staying provider-agnostic and never required for the test suite (§11) → Task 8. Deferred on purpose: the enterprise dashboard's UI for the approval queue (Task 12 builds the API it will call, not the UI itself), the desktop EXE, the cloud backend, and the video/voice knowledge base.

**Placeholder scan:** no TBD/TODO markers; every step has literal code. The one open design note — Pace's own writeup (cited in the architecture spec §17) has no published self-repair-vs-escalate threshold to benchmark against — isn't re-litigated here; this plan's own threshold (repair once per off-track step, never guess past `give_up`, never auto-activate) is already fully implemented in Phase 0/1 code, not left as an open question.

**Type consistency check:** `StepOutcome`'s new `resolved_selector: str` field (Task 4) is populated by `run_step` and consumed identically by `heal_golden_script` (Task 9) and `BrainLoop._repair`'s cache-write path (Task 6). `ActionCacheStore.get/put/record_miss` signatures (Task 5) are used identically in `BrainLoop._repair` (Task 6) and `heal_golden_script` (Task 9). `compute_trajectory_diff(old, new) -> list[dict]` (Task 7) return shape (`{"step_index", "field", "old_value", "new_value"}`) matches exactly what Task 9 stores in `HealEvent.diff_summary["changes"]` and what Task 13's capstone test asserts against. `heal_golden_script`/`approve_heal_event`/`reject_heal_event` return-dict shapes (Task 9–10) are consumed identically by the CLI (Task 11) and API (Task 12) layers.
