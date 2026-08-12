# Multi-Tab Capture and Review Implementation Plan (Stage A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a live recording opens a new browser tab or popup (common for report viewers, modals implemented as new windows, etc. — confirmed on a real eCW flow), capture that tab's DOM actions and video too, and show every tab's video-plus-steps in the review/authoring screens. This stage is capture and review only — golden-script generation/replay for multi-tab flows is Stage B, a separate follow-on plan.

**Architecture:** `BrowserContext.on("page", ...)` fires reliably for every new page created in a context (verified empirically, unlike the "disconnected" event from the previous plan) — including, confirmed by a live probe, once for the main page itself, so the handler is only registered *after* the main page exists. The handler does nothing but append the raw `Page` object to a plain Python list — genuinely thread-safe, since it's not a Playwright API call — because that list is populated from Playwright's internal dispatcher thread, and every rule this project has learned the hard way says Playwright *methods* only get called from the one thread that's already doing all the other capture work. `_wait_for_stop_or_disconnect` (from the disconnect-resilience plan) gains an optional `on_tick` hook, invoked once per poll iteration *on the calling thread*, which is where the new pages actually get a `CaptureSession` attached. At finalize time, each extra page gets the same health-check-then-exit treatment the main page already gets, and its video is read (without touching `context.close()`, which only needs to happen once). `persist_recording` gains an `additional_pages` parameter carrying this data, and creates one `VideoAsset` per page (a new `page_index` column disambiguates them — the schema already had no unique constraint stopping multiple assets per recording, this was anticipated from Phase 0). The CLI's `atlas record` stays single-page-only for this stage — it blocks synchronously on `input()` with no polling loop to hook into, and the web console's live-recording flow is the primary interface this feature actually needs to work through.

**Tech Stack:** Playwright sync API (`BrowserContext.on("page", ...)`) — no new dependencies.

## Global Constraints

- Every Playwright *method* call happens on the one thread that owns this session's Playwright objects — confirmed empirically that `context.on("page", ...)` fires on Playwright's internal dispatcher thread, so its handler does nothing but a plain list append; all `CaptureSession`/page interaction for a newly-seen page happens later, on the main capturing thread, via `on_tick`.
- `atlas record` (CLI) does not gain multi-tab support in this plan — an explicit, documented scope cut, not an oversight. It keeps working exactly as it does today for a single page.
- Existing single-page recordings and their tests keep working unchanged — every new parameter (`additional_pages`, `on_tick`) is additive with a default that reproduces today's exact behavior.
- No test kills a real browser process (that risk class was already covered by the disconnect-resilience plan) — the real end-to-end test in Task 6 uses a fixture-portal page that opens a same-machine popup via `target="_blank"`, entirely within the test's own control.

---

### Task 1: Schema — `page_index` on `video_assets`

**Files:**
- Modify: `engine/src/atlas_engine/db/models/video.py`
- Create: `engine/migrations/versions/<new_revision>_add_page_index_to_video_assets.py`

**Interfaces:**
- Produces: `VideoAsset.page_index: int` (default `0`) — `0` is always the main/first page; `1`, `2`, ... are tabs/popups opened during the recording, in the order they opened.

- [ ] **Step 1: Add the column to the model**

In `engine/src/atlas_engine/db/models/video.py`, add to `VideoAsset` (after `kind`):

```python
    page_index: Mapped[int] = mapped_column(default=0)
```

- [ ] **Step 2: Generate and verify the migration**

```bash
cd engine
.venv/bin/alembic revision --autogenerate -m "add page_index to video_assets"
```

Open the generated file. It must contain exactly:

```python
def upgrade() -> None:
    op.add_column('video_assets', sa.Column('page_index', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('video_assets', 'page_index')
```

If autogenerate omits `server_default='0'` (needed so the `NOT NULL` addition doesn't fail against any existing rows), add it by hand — every `VideoAsset` created before this migration was implicitly "page 0," so backfilling with `0` is exactly correct, not a placeholder.

- [ ] **Step 3: Apply to both databases**

```bash
.venv/bin/alembic upgrade head
ATLAS_DATABASE_URL=postgresql+psycopg://atlas:atlas_dev_password@localhost:5434/atlas_test .venv/bin/alembic upgrade head
```

- [ ] **Step 4: Commit**

```bash
git add src/atlas_engine/db/models/video.py migrations/versions/
git commit -m "feat: add page_index to video_assets for multi-tab recordings"
```

---

### Task 2: `video_path_for_page` — read a page's video path without closing anything

**Files:**
- Modify: `engine/src/atlas_engine/capture/video.py`
- Test: `engine/tests/test_capture_video.py`

**Interfaces:**
- Produces: `video_path_for_page(page: Page) -> Path | None` — the read-only half of what `finalize_video` already does, extracted so it can be called once per extra page without triggering `context.close()` (which must only happen once, after every page's video path has already been read).

- [ ] **Step 1: Write the failing test**

```python
# add to engine/tests/test_capture_video.py
def test_video_path_for_page_does_not_close_anything(fixture_portal_url):
    from atlas_engine.capture.video import video_path_for_page

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context, page, _started_at_ms, video_dir = start_capture_context(browser)
        try:
            page.goto(f"{fixture_portal_url}/login.html")

            path = video_path_for_page(page)

            assert path is not None
            # Context/page must still be usable — nothing was closed.
            assert page.title() is not None

            context.close()
            browser.close()
        finally:
            shutil.rmtree(video_dir, ignore_errors=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engine && .venv/bin/pytest tests/test_capture_video.py::test_video_path_for_page_does_not_close_anything -v`
Expected: FAIL — `ImportError: cannot import name 'video_path_for_page'`

- [ ] **Step 3: Implement**

```python
# add to engine/src/atlas_engine/capture/video.py
def video_path_for_page(page: Page) -> Path | None:
    """Reads a page's video path without touching its context — safe to
    call once per page when a context has more than one (e.g. a tab/popup
    opened during the recording). context.close() only needs to happen
    once, after every page's path has already been read this way."""
    return Path(page.video.path()) if page.video else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_capture_video.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add src/atlas_engine/capture/video.py tests/test_capture_video.py
git commit -m "feat: add video_path_for_page for reading a page's video without closing its context"
```

---

### Task 3: `persist_recording` accepts additional pages

**Files:**
- Modify: `engine/src/atlas_engine/recording_service.py`
- Test: `engine/tests/test_recording_service.py`

**Interfaces:**
- Produces: `PageCapture` dataclass (`page_index: int`, `actions: list[RecordedAction]`, `video_path: Path | None = None`, `video_started_at_ms: int | None = None`, `video_ended_at_ms: int | None = None`). `persist_recording` gains `additional_pages: list[PageCapture] | None = None` — one extra `VideoAsset`/set of `VideoStep`s per entry, each tagged with its own `page_index`. The main page's behavior (page_index always `0`) is internally refactored to share the same video-persisting logic, not duplicated.

- [ ] **Step 1: Write the failing test**

```python
# add to engine/tests/test_recording_service.py
def test_persist_recording_creates_a_video_asset_per_additional_page(tmp_path, fixture_portal_url):
    from atlas_engine.recording_service import PageCapture

    main_video = tmp_path / "main.webm"
    main_video.write_bytes(b"main-bytes")
    popup_video = tmp_path / "popup.webm"
    popup_video.write_bytes(b"popup-bytes")

    with session_scope() as session:
        org = Organization(name="Multi Page Org", slug=f"multi-page-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    start_url = f"{fixture_portal_url}/login.html"
    main_actions = [
        RecordedAction(0, "click", [{"selector": "#login-btn", "rank": 0}], None,
                        start_url, start_url, "", "", timestamp_ms=1000),
    ]
    popup_actions = [
        RecordedAction(0, "click", [{"selector": "#report-link", "rank": 0}], None,
                        start_url, start_url, "", "", timestamp_ms=5000),
    ]

    recording_id = persist_recording(
        org_id, start_url, main_actions,
        video_path=main_video, video_started_at_ms=0, video_ended_at_ms=2000,
        additional_pages=[
            PageCapture(
                page_index=1, actions=popup_actions,
                video_path=popup_video, video_started_at_ms=4000, video_ended_at_ms=6000,
            ),
        ],
    )

    with session_scope() as session:
        assets = (
            session.query(VideoAsset)
            .filter_by(recording_id=recording_id)
            .order_by(VideoAsset.page_index)
            .all()
        )
        assert [a.page_index for a in assets] == [0, 1]
        assert (settings.run_root / assets[0].storage_ref).read_bytes() == b"main-bytes"
        assert (settings.run_root / assets[1].storage_ref).read_bytes() == b"popup-bytes"

        popup_step = session.query(VideoStep).filter_by(video_asset_id=assets[1].id).one()
        assert popup_step.offset_ms == 1000  # action at 5000ms, popup video started at 4000ms
        assert popup_step.caption_text == "Click #report-link"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_recording_service.py::test_persist_recording_creates_a_video_asset_per_additional_page -v`
Expected: FAIL — `ImportError: cannot import name 'PageCapture'`

- [ ] **Step 3: Implement**

In `engine/src/atlas_engine/recording_service.py`, add near the top (after the imports):

```python
from dataclasses import dataclass


@dataclass
class PageCapture:
    """One extra tab/popup opened during a recording, beyond the main
    page persist_recording already handles. page_index must be unique
    per recording and greater than 0 (0 is always the main page)."""
    page_index: int
    actions: list[RecordedAction]
    video_path: Path | None = None
    video_started_at_ms: int | None = None
    video_ended_at_ms: int | None = None
```

Replace the whole `persist_recording` function:

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
    additional_pages: list[PageCapture] | None = None,
) -> uuid.UUID:
    """Persists a captured recording — actions.json, the extracted
    trajectory, one VideoAsset per page (main plus any tabs/popups opened
    during capture), and the Recording/ReferenceTrajectory rows — without
    running any authoring. This is the recording module's one entrypoint;
    authoring_service.author_from_recording is the only thing that
    consumes what this produces.
    """
    store = LocalRunStore(root=settings.ensure_run_root())
    run_dir = store.new_run_dir(prefix="recordings")
    run_dir.write_json("actions.json", [vars(a) for a in actions])

    trajectory = extract_trajectory(actions)
    trajectory_path = run_dir.path / "trajectory.json"
    trajectory_path.write_text(json.dumps(trajectory_to_dict(trajectory), indent=2))

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

        recording.duration_seconds = _persist_video_asset(
            session, org_id, recording.id, run_dir, "video.webm", 0,
            video_path, video_started_at_ms, video_ended_at_ms, actions,
        )

        for page_capture in (additional_pages or []):
            _persist_video_asset(
                session, org_id, recording.id, run_dir, f"video-page{page_capture.page_index}.webm",
                page_capture.page_index, page_capture.video_path,
                page_capture.video_started_at_ms, page_capture.video_ended_at_ms, page_capture.actions,
            )

        if request_id is not None:
            request = session.get(RecordingRequest, request_id)
            if request is not None and request.status == "open":
                request.status = "submitted"

        recording_id = recording.id

    return recording_id


def _persist_video_asset(
    session, org_id: str, recording_id: uuid.UUID, run_dir, filename: str, page_index: int,
    video_path: Path | None, video_started_at_ms: int | None, video_ended_at_ms: int | None,
    actions: list[RecordedAction],
) -> int | None:
    """Writes one page's video (if any) into storage and creates its
    VideoAsset/VideoStep rows. Returns the computed duration_seconds (or
    None if there was no usable video) so the main page's call site can
    also stamp it onto Recording.duration_seconds."""
    if video_path is None or not video_path.exists() or video_path.stat().st_size == 0:
        return None

    video_blob_ref = run_dir.write_bytes(filename, video_path.read_bytes())
    duration_seconds = None
    if video_started_at_ms is not None and video_ended_at_ms is not None:
        duration_seconds = max(0, round((video_ended_at_ms - video_started_at_ms) / 1000))

    video_asset = VideoAsset(
        org_id=uuid.UUID(org_id),
        recording_id=recording_id,
        kind="raw",
        storage_ref=video_blob_ref,
        page_index=page_index,
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

    return duration_seconds
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_recording_service.py -v`
Expected: PASS (all tests, including every pre-existing single-page test — `_persist_video_asset` must produce byte-for-byte the same `Recording`/`VideoAsset`/`VideoStep` state as the old inline code did for page_index 0)

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/atlas_engine/recording_service.py tests/test_recording_service.py
git commit -m "feat: persist_recording supports additional pages (tabs/popups)"
```

---

### Task 4: `get_recording_detail` returns one entry per page

**Files:**
- Modify: `engine/src/atlas_engine/recording_service.py`
- Test: `engine/tests/test_api_recordings.py` (the function is only tested indirectly via the API today — keep that pattern)

**Interfaces:**
- Produces: `get_recording_detail` now returns `{"id", "status", "role", "pages": [...]}` where each page entry is `{"page_index", "has_video", "video_asset_id", "duration_seconds", "steps"}`, sorted by `page_index`. Page `0` is always present, even if it has no video (matches today's behavior of always describing the main page, just nested one level deeper now). This is a breaking response-shape change — Task 5 updates the one existing test that depends on the old flat shape.

- [ ] **Step 1: Implement**

Replace `get_recording_detail` in `engine/src/atlas_engine/recording_service.py`:

```python
def get_recording_detail(recording_id: uuid.UUID) -> dict:
    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        if recording is None:
            raise ValueError(f"No such recording: {recording_id}")

        video_assets = (
            session.query(VideoAsset)
            .filter_by(recording_id=recording_id)
            .order_by(VideoAsset.page_index)
            .all()
        )
        by_page_index = {asset.page_index: asset for asset in video_assets}

        pages = []
        for page_index in sorted(by_page_index.keys() | {0}):
            video_asset = by_page_index.get(page_index)
            steps = []
            if video_asset is not None:
                steps = [
                    {"order_index": s.order_index, "caption": s.caption_text, "offset_ms": s.offset_ms}
                    for s in sorted(video_asset.steps, key=lambda s: s.order_index)
                ]
            pages.append({
                "page_index": page_index,
                "has_video": video_asset is not None,
                "video_asset_id": str(video_asset.id) if video_asset is not None else None,
                "duration_seconds": video_asset.duration_seconds if video_asset is not None else None,
                "steps": steps,
            })

        return {
            "id": str(recording.id),
            "status": recording.status,
            "role": recording.role,
            "pages": pages,
        }
```

- [ ] **Step 2: Run the full suite to see the now-outdated test fail**

Run: `.venv/bin/pytest tests/test_api_recordings.py -v`
Expected: FAIL — `test_get_recording_detail_includes_video_steps` still asserts the old flat shape (`body["has_video"]`); this is expected and gets fixed in Task 5, which also updates the API layer this test exercises.

- [ ] **Step 3: Commit**

```bash
git add src/atlas_engine/recording_service.py
git commit -m "feat: get_recording_detail returns one entry per page"
```

(A red test between Task 4 and Task 5 is expected here — the API response model and this test are both fixed together in Task 5, since the FastAPI response model has to change in lockstep with the shape this function returns or the endpoint won't even serialize.)

---

### Task 5: API — nested page response and per-page video endpoint

**Files:**
- Modify: `engine/src/atlas_engine/api.py`
- Modify: `engine/tests/test_api_recordings.py`
- Modify: `engine/tests/test_api_recording_video.py`

**Interfaces:**
- Produces: `RecordingDetailResponse` now has `pages: list[RecordingPageResponse]` instead of flat `has_video`/`video_asset_id`/`duration_seconds`/`steps`. `GET /recordings/{recording_id}/video` becomes `GET /recordings/{recording_id}/video/{page_index}`.

- [ ] **Step 1: Fix the now-outdated test from Task 4**

Replace the relevant assertions in `engine/tests/test_api_recordings.py`:

```python
    assert response.status_code == 200
    body = response.json()
    assert body["pages"][0]["has_video"] is True
    assert body["pages"][0]["duration_seconds"] == 2
    assert len(body["pages"][0]["steps"]) == 1
    assert body["pages"][0]["steps"][0]["caption"] == "Click #login-btn"
    assert body["pages"][0]["steps"][0]["offset_ms"] == 500
```

- [ ] **Step 2: Update the video-serving test for the new path**

In `engine/tests/test_api_recording_video.py`, change both request URLs from `f"/recordings/{recording_id}/video"` to `f"/recordings/{recording_id}/video/0"` (page 0 is always the main page these tests create).

- [ ] **Step 3: Run tests to verify they fail against the current endpoint code**

Run: `.venv/bin/pytest tests/test_api_recordings.py tests/test_api_recording_video.py -v`
Expected: the recordings-detail test now passes (Task 4 already made the service layer match); the video test FAILS — no route matches `/video/0` yet.

- [ ] **Step 4: Implement**

In `engine/src/atlas_engine/api.py`, replace the response models and the two endpoints:

```python
class RecordingStepResponse(BaseModel):
    order_index: int
    caption: str | None = None
    offset_ms: int | None = None


class RecordingPageResponse(BaseModel):
    page_index: int
    has_video: bool
    video_asset_id: str | None = None
    duration_seconds: int | None = None
    steps: list[RecordingStepResponse]


class RecordingDetailResponse(BaseModel):
    id: str
    status: str
    role: str
    pages: list[RecordingPageResponse]


@app.get("/recordings/{recording_id}", response_model=RecordingDetailResponse)
def get_recording_endpoint(recording_id: str) -> RecordingDetailResponse:
    try:
        detail = get_recording_detail(uuid.UUID(recording_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return RecordingDetailResponse(**detail)
```

```python
@app.get("/recordings/{recording_id}/video/{page_index}")
def get_recording_video_endpoint(recording_id: str, page_index: int):
    with session_scope() as session:
        video_asset = (
            session.query(VideoAsset)
            .filter_by(recording_id=uuid.UUID(recording_id), page_index=page_index)
            .one_or_none()
        )
        if video_asset is None:
            raise HTTPException(status_code=404, detail="No video for this recording page")
        storage_ref = video_asset.storage_ref

    video_path = settings.run_root / storage_ref
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file missing on disk")

    return FileResponse(video_path, media_type="video/webm")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_api_recordings.py tests/test_api_recording_video.py -v`
Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/atlas_engine/api.py tests/test_api_recordings.py tests/test_api_recording_video.py
git commit -m "feat: nest recording detail by page and serve video per page"
```

---

### Task 6: Live capture tracks and finalizes every tab that opens

**Files:**
- Create: `engine/tests/fixtures/fixture_portal/popup_trigger.html`
- Modify: `engine/tests/fixtures/fixture_portal/server.py` (only if the new static file needs no route change — check first; `SimpleHTTPRequestHandler` serves any file under `_DIR` automatically, so this file likely needs no server changes at all)
- Modify: `engine/src/atlas_engine/live_capture.py`
- Test: `engine/tests/test_live_capture.py`

**Interfaces:**
- Produces: `_wait_for_stop_or_disconnect` gains `on_tick: Callable[[], None] = lambda: None` — called once per poll iteration and once more after the loop exits, on the calling thread. `_LiveCaptureSession._run` registers `context.on("page", ...)` (after the main page exists), tracks new pages in a plain list, and uses `on_tick` to attach a `CaptureSession` to each one from the main thread. At finalize time, every extra page gets the same health-check-then-exit treatment as the main page, and becomes one `PageCapture` passed to `persist_recording`.

- [ ] **Step 1: Add a fixture page that opens a real popup**

```html
<!-- engine/tests/fixtures/fixture_portal/popup_trigger.html -->
<!doctype html>
<html>
<head><title>Fixture Portal — Popup Trigger</title></head>
<body>
  <h1>Popup trigger</h1>
  <a id="open-report" href="/dashboard.html" target="_blank">Open report</a>
</body>
</html>
```

Confirm no change is needed in `server.py` — `SimpleHTTPRequestHandler` already serves any file under its directory by path, so `/popup_trigger.html` works with zero routing changes. If that assumption turns out wrong when you run the test below, add the minimal routing needed (but check first — don't add speculative code).

- [ ] **Step 2: Write a focused test for the on_tick wiring itself**

There's no way to script "the human clicked the popup link" from outside the live-captured browser, so the real popup-capturing path is proven manually in Task 9 against a real recording. What's genuinely new and testable here is whether `_wait_for_stop_or_disconnect` correctly invokes `on_tick` on every poll iteration and once more at the end:

```python
# add to engine/tests/test_live_capture.py
def test_wait_for_stop_or_disconnect_calls_on_tick_every_iteration_and_at_the_end():
    from atlas_engine.live_capture import _wait_for_stop_or_disconnect

    stop_event = threading.Event()
    page = Mock()
    tick_count = {"n": 0}

    def on_tick():
        tick_count["n"] += 1
        if tick_count["n"] == 3:
            stop_event.set()

    disconnected = _wait_for_stop_or_disconnect(stop_event, page, poll_interval=0.01, on_tick=on_tick)

    assert disconnected is False
    assert tick_count["n"] == 4  # 3 ticks inside the loop, 1 more after it exits
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_live_capture.py -v -k on_tick`
Expected: FAIL — `on_tick` isn't a parameter yet.

- [ ] **Step 4: Implement `on_tick` on `_wait_for_stop_or_disconnect`**

```python
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
    return False
```

- [ ] **Step 5: Run the on_tick test to verify it passes**

Run: `.venv/bin/pytest tests/test_live_capture.py -v -k on_tick`
Expected: PASS

- [ ] **Step 6: Wire multi-page tracking into `_run`**

In `engine/src/atlas_engine/live_capture.py`, add `import time` and update the imports from `atlas_engine.capture.video` and `atlas_engine.recording_service`:

```python
import shutil
import threading
import time
import uuid

from playwright.sync_api import sync_playwright

from atlas_engine.capture.recorder import CaptureSession
from atlas_engine.capture.video import finalize_video, start_capture_context, video_path_for_page
from atlas_engine.recording_service import PageCapture, persist_recording
```

Replace `_run`:

```python
    def _run(self) -> None:
        video_dir = None
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=False)
                context, page, video_started_at_ms, video_dir = start_capture_context(browser)
                page.goto(self.start_url)

                new_pages: list = []
                context.on("page", lambda new_page: new_pages.append(new_page))

                extra_captures: list[tuple] = []

                def _attach_new_pages() -> None:
                    while new_pages:
                        new_page = new_pages.pop(0)
                        started_at_ms = int(time.time() * 1000)
                        capture = CaptureSession(new_page)
                        capture.__enter__()
                        extra_captures.append((new_page, capture, started_at_ms))

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

            self.recording_id = persist_recording(
                self.org_id, self.start_url, actions,
                request_id=self.request_id, role=self.role,
                video_path=video_path,
                video_started_at_ms=video_started_at_ms,
                video_ended_at_ms=video_ended_at_ms,
                additional_pages=additional_pages,
            )
        except Exception as exc:  # surfaced to the caller of stop()
            self.error = exc
        finally:
            if video_dir is not None:
                shutil.rmtree(video_dir, ignore_errors=True)
            self.done_event.set()
```

- [ ] **Step 7: Run the live-capture tests**

Run: `.venv/bin/pytest tests/test_live_capture.py -v`
Expected: PASS (all tests, including the two pre-existing real-browser tests — a session with zero popups must behave exactly as before)

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add tests/fixtures/fixture_portal/popup_trigger.html src/atlas_engine/live_capture.py tests/test_live_capture.py
git commit -m "feat: live capture tracks and persists every tab that opens during a recording"
```

---

### Task 7: Frontend — types for a page-nested recording detail

**Files:**
- Modify: `frontend/src/lib/recordings.ts`

**Interfaces:**
- Produces: `RecordingPage` (`page_index`, `has_video`, `video_asset_id`, `duration_seconds`, `steps`) replaces the flat fields on `RecordingDetail`, which now has `pages: RecordingPage[]`. `recordingVideoUrl(recordingId, pageIndex)` gains the required `pageIndex` parameter matching the new API path.

- [ ] **Step 1: Implement**

```typescript
// frontend/src/lib/recordings.ts
import { useMutation, useQuery } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface RecordingSummary {
  id: string
  status: string
  role: string
  request_id: string | null
  request_title: string | null
  portal_domain: string | null
  duration_seconds: number | null
  has_video: boolean
  created_at: string
}

export interface RecordingStep {
  order_index: number
  caption: string | null
  offset_ms: number | null
}

export interface RecordingPage {
  page_index: number
  has_video: boolean
  video_asset_id: string | null
  duration_seconds: number | null
  steps: RecordingStep[]
}

export interface RecordingDetail {
  id: string
  status: string
  role: string
  pages: RecordingPage[]
}

export function useRecordings(orgId: string, status?: string) {
  return useQuery({
    queryKey: ['recordings', orgId, status],
    queryFn: () => {
      const params = new URLSearchParams({ org_id: orgId })
      if (status) params.set('status', status)
      return apiFetch<RecordingSummary[]>(`/recordings?${params}`)
    },
  })
}

export function useRecordingDetail(recordingId: string | undefined) {
  return useQuery({
    queryKey: ['recording', recordingId],
    queryFn: () => apiFetch<RecordingDetail>(`/recordings/${recordingId}`),
    enabled: !!recordingId,
  })
}

export function useStartRecording() {
  return useMutation({
    mutationFn: (body: { org_id: string; start_url: string; request_id?: string; role?: string }) =>
      apiFetch<{ capture_session_id: string }>('/recordings/start', { method: 'POST', body: JSON.stringify(body) }),
  })
}

export function useStopRecording() {
  return useMutation({
    mutationFn: (captureSessionId: string) =>
      apiFetch<{ recording_id: string }>(`/recordings/${captureSessionId}/stop`, { method: 'POST' }),
  })
}

export function recordingVideoUrl(recordingId: string, pageIndex: number): string {
  const base = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
  return `${base}/recordings/${recordingId}/video/${pageIndex}`
}
```

- [ ] **Step 2: Confirm the build now fails where callers need updating**

Run: `cd frontend && npm run build`
Expected: FAIL — `VideoStepPlayer.tsx` (still called with a single `recordingId` prop, now needs `recordingId`+`pageIndex`) and `ReviewPage.tsx`/`AuthoringPage.tsx` (still read `recording.has_video`/`recording.steps`/`recording.duration_seconds` directly) no longer type-check against the new `RecordingDetail` shape. This is expected — Task 8 fixes every call site.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/recordings.ts
git commit -m "feat: recording detail types are nested per page"
```

---

### Task 8: Frontend — one video block per page

**Files:**
- Modify: `frontend/src/components/VideoStepPlayer.tsx`
- Modify: `frontend/src/pages/agent/ReviewPage.tsx`
- Modify: `frontend/src/pages/dev/AuthoringPage.tsx`

**Interfaces:**
- `VideoStepPlayer` gains a required `pageIndex: number` prop (passed straight through to `recordingVideoUrl`) — everything else about it is unchanged.
- `ReviewPage`/`AuthoringPage` render one `VideoStepPlayer` per entry in `recording.pages`, each under a small label ("Main window" for page 0, "Tab 2" / "Tab 3" ... for the rest), and their header stats (action count, duration) are computed from `recording.pages` instead of the old flat fields.

- [ ] **Step 1: Add `pageIndex` to `VideoStepPlayer`**

In `frontend/src/components/VideoStepPlayer.tsx`, update the props interface and the one place `recordingVideoUrl` is called:

```tsx
interface Props {
  recordingId: string
  pageIndex: number
  hasVideo: boolean
  steps: RecordingStep[]
  durationSeconds: number | null
}

export function VideoStepPlayer({ recordingId, pageIndex, hasVideo, steps, durationSeconds }: Props) {
```

```tsx
              src={recordingVideoUrl(recordingId, pageIndex)}
```

(Every other line in the file is unchanged — this is a two-line diff.)

- [ ] **Step 2: Update `ReviewPage`**

```tsx
// frontend/src/pages/agent/ReviewPage.tsx
import { useNavigate, useParams } from 'react-router-dom'
import { useRecordingDetail } from '../../lib/recordings'
import { VideoStepPlayer } from '../../components/VideoStepPlayer'
import { StatusPill } from '../../components/StatusPill'
import { formatDuration } from '../../lib/format'

export function ReviewPage() {
  const { recordingId } = useParams<{ recordingId: string }>()
  const navigate = useNavigate()
  const { data: recording, isLoading } = useRecordingDetail(recordingId)

  if (isLoading || !recording) return <div className="p-9 text-sm text-atlas-body">Loading…</div>

  const totalActions = recording.pages.reduce((sum, page) => sum + page.steps.length, 0)
  const mainPage = recording.pages[0]

  return (
    <div className="p-9 pb-16 flex flex-col gap-6 max-w-[1280px]">
      <div className="flex items-start justify-between gap-6">
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2.5">
            <StatusPill status={recording.status} />
            <span className="text-[12.5px] text-atlas-muted">
              {formatDuration(mainPage?.duration_seconds ?? null)} · {totalActions} action{totalActions === 1 ? '' : 's'}
              {recording.pages.length > 1 ? ` · ${recording.pages.length} tabs` : ''}
            </span>
          </div>
          <h1 className="font-display text-[28px] font-semibold tracking-tight m-0">Recording review</h1>
          <div className="text-[13.5px] text-atlas-body">
            {recording.role === 'demo' ? 'Demo recording' : 'Authoring recording'}
          </div>
        </div>
        <button
          onClick={() => navigate('/agent')}
          className="px-5 py-2.5 rounded-full bg-atlas-primary hover:bg-atlas-primaryHover text-white text-[13.5px] font-medium"
        >
          Done
        </button>
      </div>

      {recording.pages.map((page) => (
        <div key={page.page_index} className="flex flex-col gap-3">
          {recording.pages.length > 1 && (
            <div className="text-[12.5px] font-medium text-atlas-body">
              {page.page_index === 0 ? 'Main window' : `Tab ${page.page_index + 1}`}
            </div>
          )}
          <VideoStepPlayer
            recordingId={recording.id}
            pageIndex={page.page_index}
            hasVideo={page.has_video}
            steps={page.steps}
            durationSeconds={page.duration_seconds}
          />
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 3: Update `AuthoringPage`**

```tsx
// frontend/src/pages/dev/AuthoringPage.tsx
import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { useRecordingDetail } from '../../lib/recordings'
import { useAuthorRecording } from '../../lib/authoring'
import { VideoStepPlayer } from '../../components/VideoStepPlayer'
import { StatusPill } from '../../components/StatusPill'
import { formatDuration } from '../../lib/format'

export function AuthoringPage() {
  const { recordingId } = useParams<{ recordingId: string }>()
  const navigate = useNavigate()
  const { data: recording, isLoading } = useRecordingDetail(recordingId)
  const authorRecording = useAuthorRecording(recordingId!)

  if (isLoading || !recording) return <div className="p-9 text-sm text-atlas-body">Loading…</div>

  const totalActions = recording.pages.reduce((sum, page) => sum + page.steps.length, 0)
  const mainPage = recording.pages[0]

  return (
    <div className="flex flex-col h-screen box-border">
      <div className="flex-none px-7 py-[18px] border-b border-atlas-border bg-atlas-card flex items-center justify-between gap-5">
        <div className="flex items-center gap-3.5 min-w-0">
          <button
            onClick={() => navigate('/dev')}
            className="w-[30px] h-[30px] rounded-full border border-atlas-border flex items-center justify-center hover:bg-atlas-bg flex-none"
          >
            <ArrowLeft size={15} className="text-atlas-body" />
          </button>
          <div className="flex flex-col gap-0.5 min-w-0">
            <div className="text-[15px] font-semibold tracking-tight">Authoring workspace</div>
            <div className="text-xs text-atlas-muted">
              {formatDuration(mainPage?.duration_seconds ?? null)} · {totalActions} actions
              {recording.pages.length > 1 ? ` · ${recording.pages.length} tabs` : ''}
            </div>
          </div>
          {authorRecording.data && <StatusPill status={authorRecording.data.status} />}
        </div>
        <button
          onClick={() => authorRecording.mutate()}
          disabled={authorRecording.isPending}
          className="px-[18px] py-2.5 rounded-full bg-atlas-primary hover:bg-atlas-primaryHover text-white text-[13px] font-medium disabled:opacity-60"
        >
          {authorRecording.isPending ? 'Generating…' : authorRecording.data ? 'Regenerate golden script' : 'Generate golden script'}
        </button>
      </div>

      <div className="flex-1 overflow-auto p-7 flex flex-col gap-6">
        {recording.pages.map((page) => (
          <div key={page.page_index} className="flex flex-col gap-3">
            {recording.pages.length > 1 && (
              <div className="text-[12.5px] font-medium text-atlas-body">
                {page.page_index === 0 ? 'Main window' : `Tab ${page.page_index + 1}`}
              </div>
            )}
            <VideoStepPlayer
              recordingId={recording.id}
              pageIndex={page.page_index}
              hasVideo={page.has_video}
              steps={page.steps}
              durationSeconds={page.duration_seconds}
            />
          </div>
        ))}
        {authorRecording.data && (
          <div className="text-[13px] text-atlas-body">
            Golden script <span className="font-medium text-atlas-ink">{authorRecording.data.golden_script_id}</span> — {authorRecording.data.status}
          </div>
        )}
        {authorRecording.isError && (
          <div className="text-[13px] text-atlas-diffOldText">{(authorRecording.error as Error).message}</div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run the build and tests**

Run: `cd frontend && npm run build && npm run test`
Expected: clean build (no more type errors), all tests pass

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/VideoStepPlayer.tsx frontend/src/pages/agent/ReviewPage.tsx frontend/src/pages/dev/AuthoringPage.tsx
git commit -m "feat: render one video block per captured tab in review and authoring"
```

---

### Task 9: Manual verification against a real popup flow, and finish branch

**Files:** none (verification task)

- [ ] **Step 1: Restart both dev servers**

Backend needs the new migration and code; frontend needs the new build.

- [ ] **Step 2: Record a flow that opens a real popup**

As the operations agent, record against the fixture-portal-style scenario or (since you already have real access) redo the eCW flow through to the report-generation click that opens the new tab. Perform the flow, stop normally.

- [ ] **Step 3: Confirm both tabs show up in review**

Open the resulting recording. Confirm you see two labeled video blocks ("Main window" and "Tab 2"), each with its own real, playable video and its own step list, and that the previously-invisible modal/report content is now visible in the second block.

- [ ] **Step 4: Confirm a single-page recording still renders exactly as before**

Open any earlier, single-page recording (e.g. the "Manual QA test" or "Disconnect fix verification" ones already in the library) and confirm it still shows exactly one video block, unlabeled (since `recording.pages.length > 1` is false), with no visible change from before this plan.

- [ ] **Step 5: Finish the branch**

**REQUIRED SUB-SKILL:** Use superpowers:finishing-a-development-branch — run the full backend suite and the frontend build/test, present the merge/PR/keep menu, act on the answer.
