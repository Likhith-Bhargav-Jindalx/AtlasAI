# Recording Video Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every recording (CLI `atlas record` and the live-capture API) captures a real screen video alongside the existing DOM-action trajectory, stores it, and exposes it — plus per-step timing offsets into that video — over HTTP, so the frontend can build a Guidde-style video player with synced step chapters instead of a placeholder.

**Architecture:** Playwright's native `browser.new_context(record_video_dir=...)` already does the recording — no custom encoding. A new `atlas_engine.capture.video` module wraps "open a context with video on" / "close it and get the file" so both capture call sites (CLI and live-capture) share identical logic. `persist_recording` (already the single place both call sites funnel through) is extended to move that video file into the recording's existing on-disk run directory and populate the `video_assets`/`video_steps` tables — which have existed in the schema since Phase 0 but have never been written to. Two new read endpoints expose a recording's step/timing metadata and the raw video bytes.

**Tech Stack:** Playwright sync API (`record_video_dir`), SQLAlchemy/Alembic (two new nullable columns), FastAPI (`FileResponse`).

## Global Constraints

- Only one thread ever touches Playwright sync objects for a given browser session (established rule from Phase 0 and live_capture.py) — the video helpers run on whichever thread already owns the browser/context, never a new one.
- `persist_recording` stays the single entrypoint recording data flows through — both `cli.py`'s `record` command and `live_capture.py` call it, so video wiring belongs there, not duplicated in each caller.
- No schema migration touches `recordings`, `video_assets`, or `video_steps` beyond the two additive nullable columns this plan needs — `video_assets`/`video_steps` already exist (Phase 0 migration `b3e268daf034`) and are otherwise untouched.
- Every new function gets a real test before being wired into a call site (TDD), except the mechanical CLI wiring in Task 5, which reuses already-tested helpers inside a function (`atlas record`) that blocks on `input()` and has never had a direct test in this codebase for that reason — noted explicitly in that task rather than skipped silently.

---

### Task 1: Schema — video timing columns

**Files:**
- Modify: `engine/src/atlas_engine/db/models/video.py`
- Create: `engine/migrations/versions/<new_revision>_add_video_timing_fields.py`

**Interfaces:**
- Produces: `VideoAsset.started_at_ms: int | None`, `VideoStep.offset_ms: int | None` — consumed by Task 3 (`persist_recording`) and Task 6 (`GET /recordings/{id}`).

- [ ] **Step 1: Add the two columns to the models**

Epoch milliseconds (~1.76×10¹² today) overflow a 32-bit `Integer` column — the default a bare `Mapped[int]` would get — so both new columns must be explicitly typed `BigInteger`. Add `BigInteger` to the existing `sqlalchemy` import line at the top of `engine/src/atlas_engine/db/models/video.py`:

```python
from sqlalchemy import BigInteger, Enum, ForeignKey, UniqueConstraint, func, Uuid
```

Add to `VideoAsset` (after `storage_ref`):

```python
    started_at_ms: Mapped[int | None] = mapped_column(BigInteger, default=None)
```

And to `VideoStep` (after `caption_text`):

```python
    offset_ms: Mapped[int | None] = mapped_column(BigInteger, default=None)
```

`started_at_ms` is the wall-clock time (epoch milliseconds) the video recording began; `offset_ms` is how many milliseconds into that video a given step occurred. Both nullable because a `VideoAsset`/`VideoStep` can in principle exist without precise timing (e.g. a future imported video).

- [ ] **Step 2: Generate and hand-verify the migration**

```bash
cd engine
.venv/bin/alembic revision --autogenerate -m "add video timing fields"
```

Open the generated file under `migrations/versions/`. It must contain exactly:

```python
def upgrade() -> None:
    op.add_column('video_assets', sa.Column('started_at_ms', sa.BigInteger(), nullable=True))
    op.add_column('video_steps', sa.Column('offset_ms', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column('video_steps', 'offset_ms')
    op.drop_column('video_assets', 'started_at_ms')
```

No enum types are involved (unlike the recording-requests migration), so no hand-fix for enum creation is expected here — but confirm the diff doesn't include unrelated changes before proceeding (autogenerate sometimes picks up incidental drift; if it does, trim the file down to just these two columns).

- [ ] **Step 3: Apply to both databases**

```bash
.venv/bin/alembic upgrade head
ATLAS_DATABASE_URL=postgresql+psycopg://atlas:atlas_dev_password@localhost:5434/atlas_test .venv/bin/alembic upgrade head
```

- [ ] **Step 4: Commit**

```bash
git add src/atlas_engine/db/models/video.py migrations/versions/
git commit -m "feat: add video timing columns to video_assets/video_steps"
```

---

### Task 2: `capture/video.py` — shared video-recording context helpers

**Files:**
- Create: `engine/src/atlas_engine/capture/video.py`
- Test: `engine/tests/test_capture_video.py`

**Interfaces:**
- Produces: `start_capture_context(browser: Browser) -> tuple[BrowserContext, Page, int, Path]` (context, page, `started_at_ms`, temp video directory), `finalize_video(context: BrowserContext, page: Page) -> tuple[Path | None, int]` (video file path or `None`, `ended_at_ms`). Consumed by Task 4 (`live_capture.py`) and Task 5 (`cli.py`).

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/test_capture_video.py
import shutil

from playwright.sync_api import sync_playwright

from atlas_engine.capture.video import finalize_video, start_capture_context


def test_start_and_finalize_video_produces_a_playable_file(fixture_portal_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context, page, started_at_ms, video_dir = start_capture_context(browser)
        try:
            page.goto(f"{fixture_portal_url}/login.html")
            page.fill("#username", "demo")

            video_path, ended_at_ms = finalize_video(context, page)
            browser.close()

            assert video_path is not None
            assert video_path.exists()
            assert video_path.stat().st_size > 0
            assert ended_at_ms >= started_at_ms
        finally:
            shutil.rmtree(video_dir, ignore_errors=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_capture_video.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atlas_engine.capture.video'`

- [ ] **Step 3: Write the implementation**

```python
# engine/src/atlas_engine/capture/video.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_capture_video.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/atlas_engine/capture/video.py tests/test_capture_video.py
git commit -m "feat: add shared video-recording context helpers"
```

---

### Task 3: `persist_recording` stores the video and populates `video_assets`/`video_steps`

**Files:**
- Modify: `engine/src/atlas_engine/recording_service.py`
- Test: `engine/tests/test_recording_service.py`

**Interfaces:**
- Consumes: `start_capture_context`/`finalize_video` outputs (via new kwargs, not directly).
- Produces: `persist_recording(org_id, start_url, actions, *, request_id=None, role="demo", video_path: Path | None = None, video_started_at_ms: int | None = None, video_ended_at_ms: int | None = None) -> uuid.UUID` — same return type as today, new kwargs default to `None` so every existing caller keeps working unchanged. When a video is present, sets `Recording.duration_seconds`, creates one `VideoAsset` (`kind="raw"`, `status="ready"`) and one `VideoStep` per recorded action with `caption_text` and `offset_ms` filled in.

- [ ] **Step 1: Write the failing test**

```python
# add to engine/tests/test_recording_service.py
import uuid
from pathlib import Path

from atlas_engine.capture.events import RecordedAction
from atlas_engine.config import settings
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, Recording, VideoAsset, VideoStep
from atlas_engine.recording_service import persist_recording


def test_persist_recording_with_video_creates_video_asset_and_steps(tmp_path, fixture_portal_url):
    video_file = tmp_path / "session.webm"
    video_file.write_bytes(b"fake-webm-bytes")

    with session_scope() as session:
        org = Organization(name="Video Org", slug=f"video-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    start_url = f"{fixture_portal_url}/login.html"
    actions = [
        RecordedAction(
            0, "click", [{"selector": "#login-btn", "rank": 0}], None,
            start_url, f"{fixture_portal_url}/dashboard.html", "", "",
            timestamp_ms=1000,
        ),
    ]

    recording_id = persist_recording(
        org_id, start_url, actions,
        video_path=video_file, video_started_at_ms=500, video_ended_at_ms=2500,
    )

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        assert recording.duration_seconds == 2

        video_asset = session.query(VideoAsset).filter_by(recording_id=recording_id).one()
        assert video_asset.status == "ready"
        assert video_asset.started_at_ms == 500
        assert (settings.run_root / video_asset.storage_ref).read_bytes() == b"fake-webm-bytes"

        step = session.query(VideoStep).filter_by(video_asset_id=video_asset.id).one()
        assert step.offset_ms == 500  # action at 1000ms, video started at 500ms
        assert step.caption_text == "Click #login-btn"


def test_persist_recording_without_video_creates_no_video_asset(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="No Video Org", slug=f"no-video-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])

    with session_scope() as session:
        assert session.query(VideoAsset).filter_by(recording_id=recording_id).one_or_none() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_recording_service.py -v`
Expected: the video test fails with `TypeError: persist_recording() got an unexpected keyword argument 'video_path'`; the no-video test currently passes already (confirm it does, as a baseline) — it's included to lock in the negative case going forward.

- [ ] **Step 3: Implement**

In `engine/src/atlas_engine/recording_service.py`, add imports:

```python
from pathlib import Path

from atlas_engine.db.models import Recording, RecordingRequest, ReferenceTrajectory, VideoAsset, VideoStep
```

(replacing the existing narrower `from atlas_engine.db.models import Recording, RecordingRequest, ReferenceTrajectory` line)

Change the signature and body:

```python
def persist_recording(
    org_id: str,
    start_url: str,
    actions: list[RecordedAction],
    *,
    request_id: uuid.UUID | None = None,
    role: str = "demo",
    video_path: Path | None = None,
    video_started_at_ms: int | None = None,
    video_ended_at_ms: int | None = None,
) -> uuid.UUID:
    """Persists a captured recording — actions.json, the extracted
    trajectory, an optional video, and the Recording/ReferenceTrajectory/
    VideoAsset/VideoStep rows — without running any authoring. This is
    the recording module's one entrypoint; authoring_service.
    author_from_recording is the only thing that consumes what this
    produces.
    """
    store = LocalRunStore(root=settings.ensure_run_root())
    run_dir = store.new_run_dir(prefix="recordings")
    run_dir.write_json("actions.json", [vars(a) for a in actions])

    trajectory = extract_trajectory(actions)
    trajectory_path = run_dir.path / "trajectory.json"
    trajectory_path.write_text(json.dumps(trajectory_to_dict(trajectory), indent=2))

    video_blob_ref = None
    duration_seconds = None
    if video_path is not None and video_path.exists():
        video_blob_ref = run_dir.write_bytes("video.webm", video_path.read_bytes())
        if video_started_at_ms is not None and video_ended_at_ms is not None:
            duration_seconds = max(0, round((video_ended_at_ms - video_started_at_ms) / 1000))

    profile_data = infer_portal_profile(start_url, actions)

    with session_scope() as session:
        profile = get_or_create_portal_profile(session, org_id, profile_data.domain)
        if profile.inferred_login_url is None:
            profile.inferred_login_url = profile_data.login_url
        if profile.inferred_success_signal is None and profile_data.success_signal is not None:
            profile.inferred_success_signal = profile_data.success_signal
        if profile.confidence_score is None and profile_data.confidence is not None:
            profile.confidence_score = profile_data.confidence

        recording = Recording(
            org_id=uuid.UUID(org_id),
            portal_profile_id=profile.id,
            status="captured",
            capture_blob_prefix=run_dir.blob_ref,
            request_id=request_id,
            role=role,
            duration_seconds=duration_seconds,
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

        if video_blob_ref is not None:
            video_asset = VideoAsset(
                org_id=uuid.UUID(org_id),
                recording_id=recording.id,
                kind="raw",
                storage_ref=video_blob_ref,
                started_at_ms=video_started_at_ms,
                duration_seconds=duration_seconds,
                status="ready",
            )
            session.add(video_asset)
            session.flush()

            for action in actions:
                offset_ms = None
                if video_started_at_ms is not None:
                    offset_ms = max(0, action.timestamp_ms - video_started_at_ms)
                session.add(
                    VideoStep(
                        video_asset_id=video_asset.id,
                        order_index=action.index,
                        dom_action_event={
                            "kind": action.kind,
                            "selector_candidates": action.selector_candidates,
                            "value": action.value,
                        },
                        caption_text=_caption_for(action),
                        offset_ms=offset_ms,
                    )
                )

        if request_id is not None:
            request = session.get(RecordingRequest, request_id)
            if request is not None and request.status == "open":
                request.status = "submitted"

        recording_id = recording.id

    return recording_id


def _caption_for(action: RecordedAction) -> str:
    selector = action.selector_candidates[0]["selector"] if action.selector_candidates else "the page"
    if action.kind == "click":
        return f"Click {selector}"
    if action.kind == "fill":
        return f"Fill {selector}"
    return f"Navigate to {action.url_after}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_recording_service.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite (this function is load-bearing for every existing caller)**

Run: `.venv/bin/pytest -q`
Expected: all pass — the new kwargs are optional and default to the prior no-video behavior.

- [ ] **Step 6: Commit**

```bash
git add src/atlas_engine/recording_service.py tests/test_recording_service.py
git commit -m "feat: persist_recording stores video and populates video_assets/video_steps"
```

---

### Task 4: Wire video capture into live recording (`live_capture.py`)

**Files:**
- Modify: `engine/src/atlas_engine/live_capture.py`
- Modify: `engine/tests/test_live_capture.py`

**Interfaces:**
- Consumes: `start_capture_context`, `finalize_video` (Task 2), `persist_recording`'s new kwargs (Task 3).

- [ ] **Step 1: Extend the existing test's assertions (this is a change to already-passing behavior, so extend rather than write a new failing test first)**

```python
# engine/tests/test_live_capture.py — replace the body of
# test_start_and_stop_live_capture_persists_a_recording with:
def test_start_and_stop_live_capture_persists_a_recording(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Live Capture Org", slug=f"live-capture-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    start_url = f"{fixture_portal_url}/login.html"

    capture_session_id = start_live_capture(org_id, start_url)
    recording_id = stop_live_capture(capture_session_id)

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        assert recording.status == "captured"
        assert recording.role == "demo"

        video_asset = session.query(VideoAsset).filter_by(recording_id=recording_id).one()
        assert video_asset.status == "ready"
        assert (settings.run_root / video_asset.storage_ref).stat().st_size > 0
```

Add the needed imports at the top: `from atlas_engine.config import settings` and `from atlas_engine.db.models import Organization, Recording, VideoAsset` (extending the existing import line).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_live_capture.py -v`
Expected: FAIL — `VideoAsset` row doesn't exist yet, because `live_capture.py` doesn't request video.

- [ ] **Step 3: Implement**

In `engine/src/atlas_engine/live_capture.py`, add imports:

```python
import shutil

from atlas_engine.capture.video import finalize_video, start_capture_context
```

Replace the body of `_run`:

```python
    def _run(self) -> None:
        video_dir = None
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=False)
                context, page, video_started_at_ms, video_dir = start_capture_context(browser)
                page.goto(self.start_url)
                with CaptureSession(page) as capture:
                    self.stop_event.wait()
                actions = capture.actions
                video_path, video_ended_at_ms = finalize_video(context, page)
                browser.close()

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

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_live_capture.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/atlas_engine/live_capture.py tests/test_live_capture.py
git commit -m "feat: capture video during live recording sessions"
```

---

### Task 5: Wire video capture into `atlas record` (CLI)

**Files:**
- Modify: `engine/src/atlas_engine/cli.py`

**Interfaces:**
- Consumes: `start_capture_context`, `finalize_video` (Task 2) — same helpers Task 4 already exercises in tests. No new test in this task: `atlas record` blocks on `input()` waiting for a human to press Enter in a real terminal, and has never had a direct test in this codebase for that reason (see `author_from_recorded_actions` and other CLI commands built around it — they're tested via the non-blocking functions they call, not by invoking the blocking command itself). The logic being added here is a mechanical reuse of helpers already covered by Task 2's and Task 4's tests, so no new coverage gap is introduced.

- [ ] **Step 1: Implement**

In `engine/src/atlas_engine/cli.py`, add imports:

```python
import shutil

from atlas_engine.capture.video import finalize_video, start_capture_context
```

Replace the `record` command body (keep the existing `@main.command("record")` decorator and its options exactly as they are today — only the function body changes):

```python
@main.command("record")
@click.argument("url")
@click.option("--org-id", required=True)
@click.option("--request-id", default=None)
@click.option("--role", default="demo", type=click.Choice(["demo", "authoring"]))
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

        video_path, video_ended_at_ms = finalize_video(context, page)
        browser.close()

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

- [ ] **Step 2: Run the full suite to confirm nothing else broke**

Run: `.venv/bin/pytest -q`
Expected: all pass (no test exercises this command directly, per the note above, but this proves nothing else regressed).

- [ ] **Step 3: Manual smoke check**

Run: `.venv/bin/atlas record "https://example.com" --org-id <a real org id from your dev DB>`, click around briefly, press Enter. Confirm the command prints a recording ID and does not error. This substitutes for automated coverage on this specific blocking command, consistent with its existing untested status.

- [ ] **Step 4: Commit**

```bash
git add src/atlas_engine/cli.py
git commit -m "feat: capture video during atlas record CLI sessions"
```

---

### Task 6: `GET /recordings/{recording_id}` — detail with step/timing metadata

**Files:**
- Modify: `engine/src/atlas_engine/recording_service.py`
- Modify: `engine/src/atlas_engine/api.py`
- Test: `engine/tests/test_api_recordings.py`

**Interfaces:**
- Produces: `get_recording_detail(recording_id: uuid.UUID) -> dict` with keys `id, status, role, duration_seconds, has_video, video_asset_id, steps` (`steps` is `[]` when there's no video). Raises `ValueError` for an unknown id (mirrors `claim_recording_request`'s existing error convention). `GET /recordings/{recording_id}` returns this as JSON, `404` on `ValueError`.

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/test_api_recordings.py
import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.capture.events import RecordedAction
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization
from atlas_engine.recording_service import persist_recording


def test_get_recording_detail_includes_video_steps(tmp_path, fixture_portal_url):
    video_file = tmp_path / "session.webm"
    video_file.write_bytes(b"fake-webm-bytes")

    with session_scope() as session:
        org = Organization(name="Detail Org", slug=f"detail-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    start_url = f"{fixture_portal_url}/login.html"
    actions = [
        RecordedAction(0, "click", [{"selector": "#login-btn", "rank": 0}], None,
                        start_url, f"{fixture_portal_url}/dashboard.html", "", "", timestamp_ms=1000),
    ]
    recording_id = persist_recording(
        org_id, start_url, actions,
        video_path=video_file, video_started_at_ms=500, video_ended_at_ms=2500,
    )

    client = TestClient(app)
    response = client.get(f"/recordings/{recording_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["has_video"] is True
    assert body["duration_seconds"] == 2
    assert len(body["steps"]) == 1
    assert body["steps"][0]["caption"] == "Click #login-btn"
    assert body["steps"][0]["offset_ms"] == 500


def test_get_recording_detail_404_for_unknown_id():
    client = TestClient(app)
    response = client.get(f"/recordings/{uuid.uuid4()}")
    assert response.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_api_recordings.py -v`
Expected: FAIL with `404 Not Found` from FastAPI's own "no matching route" (there's no `GET /recordings/{recording_id}` route yet).

- [ ] **Step 3: Implement `get_recording_detail`**

Add to `engine/src/atlas_engine/recording_service.py`:

```python
def get_recording_detail(recording_id: uuid.UUID) -> dict:
    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        if recording is None:
            raise ValueError(f"No such recording: {recording_id}")

        video_asset = session.query(VideoAsset).filter_by(recording_id=recording_id).one_or_none()
        steps = []
        if video_asset is not None:
            steps = [
                {"order_index": s.order_index, "caption": s.caption_text, "offset_ms": s.offset_ms}
                for s in sorted(video_asset.steps, key=lambda s: s.order_index)
            ]

        return {
            "id": str(recording.id),
            "status": recording.status,
            "role": recording.role,
            "duration_seconds": recording.duration_seconds,
            "has_video": video_asset is not None,
            "video_asset_id": str(video_asset.id) if video_asset is not None else None,
            "steps": steps,
        }
```

- [ ] **Step 4: Implement the endpoint**

In `engine/src/atlas_engine/api.py`, extend the existing recording_service import line to:

```python
from atlas_engine.recording_service import (
    claim_recording_request,
    create_recording_request,
    get_recording_detail,
    list_recording_requests,
)
```

Then add:

```python
class RecordingStepResponse(BaseModel):
    order_index: int
    caption: str | None = None
    offset_ms: int | None = None


class RecordingDetailResponse(BaseModel):
    id: str
    status: str
    role: str
    duration_seconds: int | None = None
    has_video: bool
    video_asset_id: str | None = None
    steps: list[RecordingStepResponse]


@app.get("/recordings/{recording_id}", response_model=RecordingDetailResponse)
def get_recording_endpoint(recording_id: str) -> RecordingDetailResponse:
    try:
        detail = get_recording_detail(uuid.UUID(recording_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return RecordingDetailResponse(**detail)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_api_recordings.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/atlas_engine/recording_service.py src/atlas_engine/api.py tests/test_api_recordings.py
git commit -m "feat: add GET /recordings/{id} detail endpoint with video step timing"
```

---

### Task 7: `GET /recordings/{recording_id}/video` — serve the raw video file

**Files:**
- Modify: `engine/src/atlas_engine/api.py`
- Test: `engine/tests/test_api_recording_video.py`

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/test_api_recording_video.py
import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization
from atlas_engine.recording_service import persist_recording


def test_get_recording_video_returns_the_file(tmp_path, fixture_portal_url):
    video_file = tmp_path / "session.webm"
    video_file.write_bytes(b"fake-webm-bytes")

    with session_scope() as session:
        org = Organization(name="Video Serve Org", slug=f"video-serve-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = persist_recording(
        org_id, f"{fixture_portal_url}/login.html", [],
        video_path=video_file, video_started_at_ms=0, video_ended_at_ms=1000,
    )

    client = TestClient(app)
    response = client.get(f"/recordings/{recording_id}/video")

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/webm"
    assert response.content == b"fake-webm-bytes"


def test_get_recording_video_404_when_no_video(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="No Video Serve Org", slug=f"no-video-serve-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])

    client = TestClient(app)
    response = client.get(f"/recordings/{recording_id}/video")

    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api_recording_video.py -v`
Expected: FAIL — no matching route.

- [ ] **Step 3: Implement**

In `engine/src/atlas_engine/api.py`, add a new import line and extend the existing `db.models` import (currently just `User`):

```python
from fastapi.responses import FileResponse

from atlas_engine.config import settings
from atlas_engine.db.models import User, VideoAsset
```

Then add:

```python
@app.get("/recordings/{recording_id}/video")
def get_recording_video_endpoint(recording_id: str):
    with session_scope() as session:
        video_asset = session.query(VideoAsset).filter_by(recording_id=uuid.UUID(recording_id)).one_or_none()
        if video_asset is None:
            raise HTTPException(status_code=404, detail="No video for this recording")
        storage_ref = video_asset.storage_ref

    video_path = settings.run_root / storage_ref
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file missing on disk")

    return FileResponse(video_path, media_type="video/webm")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_api_recording_video.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/atlas_engine/api.py tests/test_api_recording_video.py
git commit -m "feat: serve recording video files over HTTP"
```

---

### Task 8: README + finish branch

**Files:**
- Modify: `engine/README.md`

- [ ] **Step 1: Add a "Video" section**

Insert after the "Users, login, and live recording" section:

```markdown
## Video

Every recording made through `atlas record` or the live-recording API now
captures a real screen video (Playwright's native `record_video_dir`) in
addition to the DOM-action trajectory. It's stored next to the recording's
other artifacts and exposed over HTTP:

```
GET /recordings/{recording_id}          -> status, duration, and a per-step
                                            list with a caption and an
                                            offset_ms into the video, for
                                            building a chaptered player
GET /recordings/{recording_id}/video    -> the raw .webm file
```

`video_assets`/`video_steps` existed in the schema since Phase 0 but were
never written to until now — `kind="raw"` is the only kind produced today;
`kind="narrated_edit"` (an edited, voice-narrated cut) stays a future hook,
per the original architecture spec.
```

- [ ] **Step 2: Commit**

```bash
git add engine/README.md
git commit -m "docs: document recording video capture and endpoints"
```

- [ ] **Step 3: Finish the branch**

**REQUIRED SUB-SKILL:** Use superpowers:finishing-a-development-branch — run the full suite, present the merge/PR/keep menu, act on the answer.
