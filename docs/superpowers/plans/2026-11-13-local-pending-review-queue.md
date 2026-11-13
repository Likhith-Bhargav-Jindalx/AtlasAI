# Local Pending-Review Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple "Stop and review" from Azure Blob upload and Postgres entirely — a recording lands in a local-only pending folder first, and the agent explicitly chooses to Upload or Discard it after reviewing, so an upload failure can never again cause a recording to be lost.

**Architecture:** `live_capture.py`'s default `persist_fn` changes from `recording_service.persist_recording` (local write + Azure upload + Postgres row, all synchronous) to a new `pending_recordings.write_pending_recording` (local write only, into `ATLAS_RUN_ROOT/pending_recordings/<uuid>/`). A new local-only FastAPI router lists/reads/serves video for pending recordings straight off disk, and exposes `upload` (which now calls today's `persist_recording` on demand) and `discard`. Frontend gets two new pages (`PendingRecordingsPage`, `PendingReviewPage`) and the Stop button's navigation target changes to the new pending-review page instead of the existing (Postgres-backed) `ReviewPage`.

A separate, unrelated frozen-browser teardown timeout fix was originally folded into this plan and has been dropped: Playwright's sync API is thread-affine, so bounding `page.title()`/`context.close()`/`browser.close()` with a `ThreadPoolExecutor`-based timeout doesn't work — every call fails instantly with `greenlet.error: cannot switch to a different thread` instead of being bounded, as confirmed while implementing it (see the spec's Non-goals section). Today's existing outer `stop()` timeout remains the only safety net for a frozen browser; nothing below depends on that gap being closed.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, pytest, Playwright (sync API), React, TypeScript, Vite, TanStack Query, Vitest + Testing Library.

## Global Constraints

- No Alembic migration, no new `recording_status` enum value — pending recordings never get a Postgres row until Upload (per spec: `docs/superpowers/specs/2026-11-13-local-pending-review-queue-design.md`).
- `/recordings/submit-capture` and everything downstream of a recording actually reaching Postgres (Section B form, submit-for-authoring) are unchanged.
- No automatic background retry/upload — Upload is manual, agent-triggered only.
- This plan assumes PR #21 (`fix/recording-upload-retry-and-slow-tests` — the upload retry-count bump and stable staging path) is merged into `main` first, or this branch is rebased onto it, since `upload_pending_recording` (Task 2) calls `recording_service.persist_recording` unchanged and benefits from that fix automatically. The branch `design/local-pending-review-queue` this plan's commits land on was created from `main` *before* PR #21, specifically to keep that already-open PR free of this larger feature's commits — rebase it onto `main` once PR #21 merges, before starting Task 1 (not yet done as of this plan's execution; proceeding without it was an explicit judgment call, not an oversight).
- Follow existing code style: no module/function docstrings beyond explaining non-obvious *why*, mirror naming conventions already used in `recording_service.py`/`live_capture.py`/`api.py`.

---

### Task 1: `pending_recordings.py` — manifest + local-only writer

**Files:**
- Create: `engine/src/atlas_engine/pending_recordings.py`
- Test: `engine/tests/test_pending_recordings.py`

**Interfaces:**
- Consumes: `atlas_engine.recording_service.PageCapture` (existing dataclass, unchanged), `atlas_engine.capture.events.RecordedAction`, `atlas_engine.config.settings`.
- Produces: `pending_root() -> Path`; `PendingManifest` dataclass with fields `id, org_id, start_url, role, created_at, request_id, user_id, video_started_at_ms, video_ended_at_ms, action_count, has_video, pages, upload_error` and `.load(pending_dir)` / `.save(pending_dir)`; `write_pending_recording(org_id, start_url, actions, *, request_id=None, role="demo", user_id=None, video_path=None, video_started_at_ms=None, video_ended_at_ms=None, additional_pages=None, page_extractions=None, codegen_script=None, capture_artifacts=None) -> uuid.UUID`. Used by Task 2, 3, 4, 5.

- [ ] **Step 1: Write the failing tests**

```python
import json
import uuid
from pathlib import Path

from atlas_engine.capture.events import RecordedAction
from atlas_engine.pending_recordings import PendingManifest, pending_root, write_pending_recording


def _action(index: int, kind: str = "click") -> RecordedAction:
    return RecordedAction(
        index=index, kind=kind, selector_candidates=[{"selector": "#go"}], value=None,
        url_before="https://x.test/a", url_after="https://x.test/b",
        dom_before="<html></html>", dom_after="<html></html>",
        timestamp_ms=1_000 + index * 10,
    )


def test_write_pending_recording_creates_a_folder_with_a_manifest_and_actions_json():
    pending_id = write_pending_recording("org-1", "https://x.test", [_action(0), _action(1)])

    pending_dir = pending_root() / str(pending_id)
    assert pending_dir.is_dir()

    manifest = PendingManifest.load(pending_dir)
    assert manifest.id == str(pending_id)
    assert manifest.org_id == "org-1"
    assert manifest.start_url == "https://x.test"
    assert manifest.action_count == 2
    assert manifest.has_video is False
    assert manifest.upload_error is None

    actions_on_disk = json.loads((pending_dir / "actions.json").read_text())
    assert len(actions_on_disk) == 2
    assert "dom_before" not in actions_on_disk[0]
    assert "dom_after" not in actions_on_disk[0]

    assert (pending_dir / "trajectory.json").exists()


def test_write_pending_recording_copies_the_video_file_and_sets_has_video(tmp_path):
    video_path = tmp_path / "video.webm"
    video_path.write_bytes(b"fake-webm-bytes")

    pending_id = write_pending_recording(
        "org-1", "https://x.test", [_action(0)],
        video_path=video_path, video_started_at_ms=1000, video_ended_at_ms=5000,
    )

    pending_dir = pending_root() / str(pending_id)
    manifest = PendingManifest.load(pending_dir)
    assert manifest.has_video is True
    assert manifest.video_started_at_ms == 1000
    assert manifest.video_ended_at_ms == 5000
    assert (pending_dir / "video.webm").read_bytes() == b"fake-webm-bytes"


def test_write_pending_recording_never_imports_azure_or_db_at_call_time(monkeypatch):
    """The whole point of this function: it must be impossible for it to
    reach Azure Blob Storage or Postgres, even indirectly."""
    import sys

    for module_name in list(sys.modules):
        if "azure" in module_name.lower():
            monkeypatch.setitem(sys.modules, module_name, None)

    pending_id = write_pending_recording("org-1", "https://x.test", [_action(0)])
    assert (pending_root() / str(pending_id)).is_dir()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd engine && .venv/bin/python -m pytest tests/test_pending_recordings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atlas_engine.pending_recordings'`

- [ ] **Step 3: Write the implementation**

```python
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from atlas_engine.brain.serialize import trajectory_to_dict
from atlas_engine.capture.events import RecordedAction
from atlas_engine.config import settings
from atlas_engine.recording_service import PageCapture, _merge_actions_for_trajectory
from atlas_engine.trajectory.extract import extract_trajectory


def pending_root() -> Path:
    root = settings.ensure_run_root() / "pending_recordings"
    root.mkdir(parents=True, exist_ok=True)
    return root


@dataclass
class PendingManifest:
    """One pending recording's metadata, stored as manifest.json next to
    its actions.json/trajectory.json/video.webm in its own folder under
    pending_root(). `pages` holds one dict per additional (non-main) tab,
    each shaped {"page_index": int, "has_video": bool,
    "video_started_at_ms": int | None, "video_ended_at_ms": int | None} —
    enough for upload_pending_recording (Task 4) to rebuild the exact
    PageCapture list persist_recording expects."""

    id: str
    org_id: str
    start_url: str
    role: str
    created_at: str
    request_id: str | None = None
    user_id: str | None = None
    video_started_at_ms: int | None = None
    video_ended_at_ms: int | None = None
    action_count: int = 0
    has_video: bool = False
    pages: list[dict] = field(default_factory=list)
    upload_error: str | None = None

    @classmethod
    def load(cls, pending_dir: Path) -> "PendingManifest":
        return cls(**json.loads((pending_dir / "manifest.json").read_text()))

    def save(self, pending_dir: Path) -> None:
        (pending_dir / "manifest.json").write_text(json.dumps(asdict(self), indent=2))


def _write_actions_json(pending_dir: Path, filename: str, actions: list[RecordedAction]) -> None:
    (pending_dir / filename).write_text(json.dumps(
        [{k: v for k, v in vars(a).items() if k not in ("dom_before", "dom_after")} for a in actions],
        indent=2,
    ))


def write_pending_recording(
    org_id: str,
    start_url: str,
    actions: list[RecordedAction],
    *,
    request_id: uuid.UUID | None = None,
    role: str = "demo",
    user_id: str | None = None,
    video_path: Path | None = None,
    video_started_at_ms: int | None = None,
    video_ended_at_ms: int | None = None,
    additional_pages: list[PageCapture] | None = None,
    page_extractions: list[dict] | None = None,
    codegen_script: str | None = None,
    capture_artifacts: list[tuple[str, Path]] | None = None,
) -> uuid.UUID:
    """Writes a captured recording to a local-only pending folder — no
    Postgres row, no Azure upload, so nothing here can fail on a network
    problem. This is live_capture.py's default persist_fn for the Stop
    button; upload_pending_recording() (Task 4) is what actually calls
    recording_service.persist_recording() later, once the agent reviews
    and explicitly chooses to upload. Same keyword arguments as
    persist_recording — the one exception is capture_artifacts, which,
    like persist_recording, writes each artifact to its own named file in
    the recording's folder (trace.zip / network.har today)."""
    pending_id = uuid.uuid4()
    pending_dir = pending_root() / str(pending_id)
    pending_dir.mkdir(parents=True)

    _write_actions_json(pending_dir, "actions.json", actions)

    pages_info = []
    for page_capture in additional_pages or []:
        if page_capture.actions:
            _write_actions_json(pending_dir, f"actions-page{page_capture.page_index}.json", page_capture.actions)

        has_page_video = bool(
            page_capture.video_path and page_capture.video_path.exists()
            and page_capture.video_path.stat().st_size > 0
        )
        if has_page_video:
            (pending_dir / f"video-page{page_capture.page_index}.webm").write_bytes(
                page_capture.video_path.read_bytes()
            )
        pages_info.append({
            "page_index": page_capture.page_index,
            "has_video": has_page_video,
            "video_started_at_ms": page_capture.video_started_at_ms,
            "video_ended_at_ms": page_capture.video_ended_at_ms,
        })

    trajectory = extract_trajectory(_merge_actions_for_trajectory(actions, additional_pages))
    (pending_dir / "trajectory.json").write_text(json.dumps(trajectory_to_dict(trajectory), indent=2))

    if codegen_script is not None:
        (pending_dir / "codegen.py").write_text(codegen_script)

    for filename, artifact_path in capture_artifacts or []:
        if artifact_path.exists() and artifact_path.is_file() and artifact_path.stat().st_size > 0:
            (pending_dir / filename).write_bytes(artifact_path.read_bytes())

    if page_extractions:
        for i, extraction in enumerate(page_extractions):
            (pending_dir / f"extraction-{i}.json").write_text(json.dumps(extraction, indent=2))

    has_video = bool(video_path and video_path.exists() and video_path.stat().st_size > 0)
    if has_video:
        (pending_dir / "video.webm").write_bytes(video_path.read_bytes())

    manifest = PendingManifest(
        id=str(pending_id),
        org_id=org_id,
        start_url=start_url,
        role=role,
        created_at=datetime.now(timezone.utc).isoformat(),
        request_id=str(request_id) if request_id else None,
        user_id=user_id,
        video_started_at_ms=video_started_at_ms,
        video_ended_at_ms=video_ended_at_ms,
        action_count=len(actions),
        has_video=has_video,
        pages=pages_info,
    )
    manifest.save(pending_dir)

    return pending_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd engine && .venv/bin/python -m pytest tests/test_pending_recordings.py -v`
Expected: PASS, all 3 tests

- [ ] **Step 5: Commit**

```bash
cd /Users/siddharthsingh/Downloads/code/AtlasAI
git add engine/src/atlas_engine/pending_recordings.py engine/tests/test_pending_recordings.py
git commit -m "feat: add pending_recordings module — local-only recording writer, no Azure/Postgres"
```

---

### Task 2: List and read a pending recording's detail

**Files:**
- Modify: `engine/src/atlas_engine/pending_recordings.py`
- Test: `engine/tests/test_pending_recordings.py`

**Interfaces:**
- Consumes: `atlas_engine.recording_service._caption_for` (existing pure function, unchanged).
- Produces: `get_pending_dir(pending_id: str) -> Path` (raises `ValueError` if missing); `list_pending_recordings() -> list[PendingManifest]`; `get_pending_recording(pending_id: str) -> PendingManifest`; `pending_recording_steps(actions: list[RecordedAction], video_started_at_ms: int | None) -> list[dict]` (each `{"order_index": int, "caption": str, "offset_ms": int | None}`); `load_pending_actions(pending_dir: Path, filename: str) -> list[RecordedAction]`. Used by Task 4 and Task 6.

- [ ] **Step 1: Write the failing tests**

Add to `engine/tests/test_pending_recordings.py`:

```python
import pytest

from atlas_engine.pending_recordings import (
    get_pending_dir, get_pending_recording, list_pending_recordings,
    load_pending_actions, pending_recording_steps,
)


def test_get_pending_dir_raises_value_error_for_an_unknown_id():
    with pytest.raises(ValueError, match="No pending recording"):
        get_pending_dir(str(uuid.uuid4()))


def test_list_pending_recordings_returns_newest_first():
    first_id = write_pending_recording("org-1", "https://x.test/first", [_action(0)])
    second_id = write_pending_recording("org-1", "https://x.test/second", [_action(0)])

    manifests = list_pending_recordings()
    ids_in_order = [m.id for m in manifests if m.id in (str(first_id), str(second_id))]
    assert ids_in_order == [str(second_id), str(first_id)]


def test_get_pending_recording_returns_the_manifest():
    pending_id = write_pending_recording("org-1", "https://x.test", [_action(0)])
    manifest = get_pending_recording(str(pending_id))
    assert manifest.id == str(pending_id)


def test_load_pending_actions_reconstructs_recorded_actions():
    pending_id = write_pending_recording("org-1", "https://x.test", [_action(0), _action(1)])
    pending_dir = get_pending_dir(str(pending_id))

    actions = load_pending_actions(pending_dir, "actions.json")

    assert len(actions) == 2
    assert actions[0].kind == "click"
    assert actions[0].dom_before == ""


def test_pending_recording_steps_computes_captions_and_offsets():
    actions = [_action(0), _action(1)]
    steps = pending_recording_steps(actions, video_started_at_ms=1000)

    assert steps[0]["order_index"] == 0
    assert steps[0]["caption"] == "Click #go"
    assert steps[0]["offset_ms"] == 0
    assert steps[1]["offset_ms"] == 10
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd engine && .venv/bin/python -m pytest tests/test_pending_recordings.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_pending_dir' from 'atlas_engine.pending_recordings'`

- [ ] **Step 3: Write the implementation**

Add to `engine/src/atlas_engine/pending_recordings.py` (add `from atlas_engine.recording_service import PageCapture, _caption_for, _merge_actions_for_trajectory` to the existing import line):

```python
def get_pending_dir(pending_id: str) -> Path:
    pending_dir = pending_root() / pending_id
    if not pending_dir.is_dir():
        raise ValueError(f"No pending recording: {pending_id}")
    return pending_dir


def list_pending_recordings() -> list[PendingManifest]:
    manifests = []
    for child in pending_root().iterdir():
        if not child.is_dir():
            continue
        try:
            manifests.append(PendingManifest.load(child))
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    manifests.sort(key=lambda m: m.created_at, reverse=True)
    return manifests


def get_pending_recording(pending_id: str) -> PendingManifest:
    return PendingManifest.load(get_pending_dir(pending_id))


def load_pending_actions(pending_dir: Path, filename: str) -> list[RecordedAction]:
    path = pending_dir / filename
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    return [RecordedAction(**{**item, "dom_before": "", "dom_after": ""}) for item in raw]


def pending_recording_steps(actions: list[RecordedAction], video_started_at_ms: int | None) -> list[dict]:
    steps = []
    for action in actions:
        offset_ms = None
        if video_started_at_ms is not None:
            offset_ms = max(0, action.timestamp_ms - video_started_at_ms)
        steps.append({"order_index": action.index, "caption": _caption_for(action), "offset_ms": offset_ms})
    return steps
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd engine && .venv/bin/python -m pytest tests/test_pending_recordings.py -v`
Expected: PASS, all tests in the file

- [ ] **Step 5: Commit**

```bash
cd /Users/siddharthsingh/Downloads/code/AtlasAI
git add engine/src/atlas_engine/pending_recordings.py engine/tests/test_pending_recordings.py
git commit -m "feat: add list/read/caption helpers for pending recordings"
```

---

### Task 3: Discard a pending recording

**Files:**
- Modify: `engine/src/atlas_engine/pending_recordings.py`
- Test: `engine/tests/test_pending_recordings.py`

**Interfaces:**
- Produces: `discard_pending_recording(pending_id: str) -> None` (raises `ValueError` if the id doesn't exist). Used by Task 6.

- [ ] **Step 1: Write the failing tests**

Add to `engine/tests/test_pending_recordings.py`:

```python
from atlas_engine.pending_recordings import discard_pending_recording


def test_discard_pending_recording_removes_the_folder():
    pending_id = write_pending_recording("org-1", "https://x.test", [_action(0)])
    pending_dir = get_pending_dir(str(pending_id))
    assert pending_dir.is_dir()

    discard_pending_recording(str(pending_id))

    assert not pending_dir.exists()


def test_discard_pending_recording_raises_for_an_unknown_id():
    with pytest.raises(ValueError, match="No pending recording"):
        discard_pending_recording(str(uuid.uuid4()))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd engine && .venv/bin/python -m pytest tests/test_pending_recordings.py -v`
Expected: FAIL — `ImportError: cannot import name 'discard_pending_recording'`

- [ ] **Step 3: Write the implementation**

Add to `engine/src/atlas_engine/pending_recordings.py` (add `import shutil` to the top of the file):

```python
def discard_pending_recording(pending_id: str) -> None:
    shutil.rmtree(get_pending_dir(pending_id))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd engine && .venv/bin/python -m pytest tests/test_pending_recordings.py -v`
Expected: PASS, all tests in the file

- [ ] **Step 5: Commit**

```bash
cd /Users/siddharthsingh/Downloads/code/AtlasAI
git add engine/src/atlas_engine/pending_recordings.py engine/tests/test_pending_recordings.py
git commit -m "feat: add discard_pending_recording"
```

---

### Task 4: Upload a pending recording

**Files:**
- Modify: `engine/src/atlas_engine/pending_recordings.py`
- Test: `engine/tests/test_pending_recordings.py`

**Interfaces:**
- Consumes: `atlas_engine.recording_service.persist_recording` (existing function, unchanged — imported lazily inside the function body to keep `pending_recordings.py` free of a module-load-time dependency on `recording_service`, matching how `live_capture.py` already injects `persist_fn` rather than importing `persist_recording` for its own sake).
- Produces: `upload_pending_recording(pending_id: str) -> uuid.UUID` (the real `Recording.id`). On failure, re-raises whatever `persist_recording` raised, after recording it on the manifest's `upload_error` field and leaving the pending folder in place. Used by Task 6.

- [ ] **Step 1: Write the failing tests**

Add to `engine/tests/test_pending_recordings.py`:

```python
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, Recording, VideoAsset
from atlas_engine.pending_recordings import upload_pending_recording


def _make_org() -> str:
    with session_scope() as session:
        org = Organization(name="Pending Upload Org", slug=f"pending-upload-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        return str(org.id)


def test_upload_pending_recording_persists_to_postgres_and_removes_the_pending_folder():
    org_id = _make_org()
    pending_id = write_pending_recording(org_id, "https://x.test", [_action(0), _action(1)])
    pending_dir = get_pending_dir(str(pending_id))

    recording_id = upload_pending_recording(str(pending_id))

    assert not pending_dir.exists()
    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        assert recording.status == "captured"


def test_upload_pending_recording_with_video_creates_a_video_asset(tmp_path):
    org_id = _make_org()
    video_path = tmp_path / "video.webm"
    video_path.write_bytes(b"fake-webm-bytes")
    pending_id = write_pending_recording(
        org_id, "https://x.test", [_action(0)],
        video_path=video_path, video_started_at_ms=1000, video_ended_at_ms=5000,
    )

    recording_id = upload_pending_recording(str(pending_id))

    with session_scope() as session:
        video_asset = session.query(VideoAsset).filter_by(recording_id=recording_id).one()
        assert video_asset.page_index == 0


def test_upload_pending_recording_keeps_the_folder_and_records_the_error_on_failure(monkeypatch):
    org_id = _make_org()
    pending_id = write_pending_recording(org_id, "https://x.test", [_action(0)])
    pending_dir = get_pending_dir(str(pending_id))

    def _raise(*args, **kwargs):
        raise RuntimeError("blob upload exploded")

    monkeypatch.setattr("atlas_engine.recording_service.persist_recording", _raise)

    with pytest.raises(RuntimeError, match="blob upload exploded"):
        upload_pending_recording(str(pending_id))

    assert pending_dir.exists()
    manifest = get_pending_recording(str(pending_id))
    assert "blob upload exploded" in manifest.upload_error
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd engine && .venv/bin/python -m pytest tests/test_pending_recordings.py -v`
Expected: FAIL — `ImportError: cannot import name 'upload_pending_recording'`

- [ ] **Step 3: Write the implementation**

Add to `engine/src/atlas_engine/pending_recordings.py`:

```python
def upload_pending_recording(pending_id: str) -> uuid.UUID:
    from atlas_engine.recording_service import persist_recording

    pending_dir = get_pending_dir(pending_id)
    manifest = PendingManifest.load(pending_dir)

    actions = load_pending_actions(pending_dir, "actions.json")
    main_video_path = pending_dir / "video.webm"
    main_video_path = main_video_path if main_video_path.exists() else None

    additional_pages = [
        PageCapture(
            page_index=page_info["page_index"],
            actions=load_pending_actions(pending_dir, f"actions-page{page_info['page_index']}.json"),
            video_path=(
                pending_dir / f"video-page{page_info['page_index']}.webm" if page_info["has_video"] else None
            ),
            video_started_at_ms=page_info["video_started_at_ms"],
            video_ended_at_ms=page_info["video_ended_at_ms"],
        )
        for page_info in manifest.pages
    ]

    page_extractions = []
    i = 0
    while (pending_dir / f"extraction-{i}.json").exists():
        page_extractions.append(json.loads((pending_dir / f"extraction-{i}.json").read_text()))
        i += 1

    codegen_path = pending_dir / "codegen.py"
    codegen_script = codegen_path.read_text() if codegen_path.exists() else None

    capture_artifacts = [
        (filename, pending_dir / filename)
        for filename in ("trace.zip", "network.har")
        if (pending_dir / filename).exists()
    ]

    try:
        recording_id = persist_recording(
            manifest.org_id, manifest.start_url, actions,
            request_id=uuid.UUID(manifest.request_id) if manifest.request_id else None,
            role=manifest.role, user_id=manifest.user_id,
            video_path=main_video_path,
            video_started_at_ms=manifest.video_started_at_ms,
            video_ended_at_ms=manifest.video_ended_at_ms,
            additional_pages=additional_pages or None,
            page_extractions=page_extractions or None,
            codegen_script=codegen_script,
            capture_artifacts=capture_artifacts or None,
        )
    except Exception as exc:
        manifest.upload_error = f"{type(exc).__name__}: {exc}"
        manifest.save(pending_dir)
        raise

    shutil.rmtree(pending_dir)
    return recording_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd engine && .venv/bin/python -m pytest tests/test_pending_recordings.py -v`
Expected: PASS, all tests in the file

- [ ] **Step 5: Commit**

```bash
cd /Users/siddharthsingh/Downloads/code/AtlasAI
git add engine/src/atlas_engine/pending_recordings.py engine/tests/test_pending_recordings.py
git commit -m "feat: add upload_pending_recording, calling persist_recording on demand"
```

---

### Task 5: Wire `live_capture.py` to the new default and update existing tests

**Files:**
- Modify: `engine/src/atlas_engine/live_capture.py`
- Modify: `engine/tests/test_live_capture.py`

**Interfaces:**
- Consumes: `atlas_engine.pending_recordings.write_pending_recording` (Task 1).
- Produces: `_LiveCaptureSession.result_id` (renamed from `recording_id`, since it's no longer a `Recording.id` by default); `start_live_capture(...)`'s and `stop_live_capture(...)`'s default behavior now returns a pending id instead of a `Recording.id`. Used by Task 6.

- [ ] **Step 1: Update the existing tests that assume the old default**

`test_start_and_stop_live_capture_persists_a_recording`, `test_stop_live_capture_stores_a_native_codegen_script`, and `test_start_live_capture_stores_user_id` in `engine/tests/test_live_capture.py` currently rely on the *default* `persist_fn` writing straight to Postgres. That's intentionally no longer the default — but `persist_recording` itself is unchanged, so each of these three tests keeps testing exactly what it tested before by passing it explicitly. For each of the three, change:

```python
    capture_session_id = start_live_capture(org_id, start_url)
```

to:

```python
    from atlas_engine.recording_service import persist_recording

    capture_session_id = start_live_capture(org_id, start_url, persist_fn=persist_recording)
```

(adjust the exact call signature per test — some pass additional kwargs like `user_id=...`; keep those, just add `persist_fn=persist_recording`.)

- [ ] **Step 2: Add a new test for the new default**

Add to `engine/tests/test_live_capture.py`:

```python
def test_start_and_stop_live_capture_defaults_to_a_local_pending_folder(fixture_portal_url):
    from atlas_engine.pending_recordings import get_pending_dir

    org_id = str(uuid.uuid4())
    start_url = f"{fixture_portal_url}/login.html"

    capture_session_id = start_live_capture(org_id, start_url)
    result_id = stop_live_capture(capture_session_id)

    pending_dir = get_pending_dir(str(result_id))
    assert (pending_dir / "actions.json").exists()
    with session_scope() as session:
        assert session.get(Recording, result_id) is None
```

(`uuid` and `session_scope`/`Recording` are already imported at the top of the file for the other tests.)

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd engine && .venv/bin/python -m pytest tests/test_live_capture.py -v`
Expected: the new test FAILs (current default still writes to Postgres); the three updated tests still PASS unchanged (they're just now explicit about what was already the default) — confirming they correctly isolate `persist_recording`'s own behavior from `live_capture`'s default.

- [ ] **Step 4: Change the default**

In `engine/src/atlas_engine/live_capture.py`, add to the imports:

```python
from atlas_engine.pending_recordings import write_pending_recording
```

Replace every default reference to `persist_fn=persist_recording` with `persist_fn=write_pending_recording`:

```python
    def __init__(
        self, org_id: str, start_url: str, request_id: uuid.UUID | None, role: str, user_id: str | None = None,
        persist_fn=write_pending_recording,
    ):
```

and:

```python
def start_live_capture(
    org_id: str, start_url: str, *, request_id: uuid.UUID | None = None, role: str = "demo",
    user_id: str | None = None, persist_fn=write_pending_recording,
) -> uuid.UUID:
```

Remove the now-unused `from atlas_engine.recording_service import PageCapture, persist_recording` import if `PageCapture` is still needed elsewhere in the file (check — it's used by `_attach_new_pages`'s `CaptureSession`/`extra_captures` list construction via `CaptureSession`, not `PageCapture` directly, so confirm with `grep -n "PageCapture\|persist_recording" engine/src/atlas_engine/live_capture.py` before removing; keep `PageCapture` imported if it's still referenced, drop only `persist_recording`).

Rename the attribute used to store the persist result, throughout the class:

```python
        self.recording_id: uuid.UUID | None = None
```

becomes

```python
        self.result_id: uuid.UUID | None = None
```

and every `self.recording_id = self.persist_fn(...)` / `return self.recording_id` in `_run`/`stop` becomes `self.result_id = self.persist_fn(...)` / `return self.result_id`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd engine && .venv/bin/python -m pytest tests/test_live_capture.py -v`
Expected: PASS, all tests in the file

- [ ] **Step 6: Run the full existing live-capture-adjacent test files to check for any other reference to `.recording_id`**

Run: `cd engine && grep -rn "\.recording_id\b" src/atlas_engine/live_capture.py tests/test_live_capture.py`
Expected: no remaining references outside what was just renamed. If any turn up in other files importing `_LiveCaptureSession` directly, rename those call sites too and re-run that file's tests.

- [ ] **Step 7: Commit**

```bash
cd /Users/siddharthsingh/Downloads/code/AtlasAI
git add engine/src/atlas_engine/live_capture.py engine/tests/test_live_capture.py
git commit -m "feat: default live capture's Stop to the local pending folder instead of Postgres/Azure"
```

---

### Task 6: API endpoints

**Files:**
- Modify: `engine/src/atlas_engine/api.py`
- Modify: `engine/tests/test_api_live_recording.py`
- Create: `engine/tests/test_api_pending_recordings.py`

**Interfaces:**
- Consumes: everything produced by Tasks 1–5.
- Produces: `GET /pending-recordings`, `GET /pending-recordings/{pending_id}`, `GET /pending-recordings/{pending_id}/video/{page_index}`, `POST /pending-recordings/{pending_id}/upload`, `POST /pending-recordings/{pending_id}/discard`; `StopRecordingResponse.pending_id` (renamed from `.recording_id`). Used by Task 7 (frontend).

- [ ] **Step 1: Update the existing stop-endpoint test for the field rename and new default**

In `engine/tests/test_api_live_recording.py`, replace `test_start_and_stop_recording_endpoints`:

```python
def test_start_and_stop_recording_endpoints(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="API Live Recording Org", slug=f"api-live-recording-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    client = TestClient(app)

    start_response = client.post(
        "/recordings/start",
        json={"org_id": org_id, "start_url": f"{fixture_portal_url}/login.html"},
    )
    assert start_response.status_code == 200
    capture_session_id = start_response.json()["capture_session_id"]

    stop_response = client.post(f"/recordings/{capture_session_id}/stop")
    assert stop_response.status_code == 200
    pending_id = stop_response.json()["pending_id"]

    detail_response = client.get(f"/pending-recordings/{pending_id}")
    assert detail_response.status_code == 200
    assert detail_response.json()["org_id"] == org_id
```

- [ ] **Step 2: Write the failing tests for the new endpoints**

Create `engine/tests/test_api_pending_recordings.py`:

```python
import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.capture.events import RecordedAction
from atlas_engine.pending_recordings import get_pending_dir, write_pending_recording


def _action(index: int = 0) -> RecordedAction:
    return RecordedAction(
        index=index, kind="click", selector_candidates=[{"selector": "#go"}], value=None,
        url_before="https://x.test/a", url_after="https://x.test/b",
        dom_before="<html></html>", dom_after="<html></html>", timestamp_ms=1000,
    )


def test_list_pending_recordings_endpoint_includes_a_newly_written_one():
    pending_id = write_pending_recording("org-1", "https://x.test", [_action()])

    client = TestClient(app)
    response = client.get("/pending-recordings")

    assert response.status_code == 200
    assert any(item["id"] == str(pending_id) for item in response.json())


def test_get_pending_recording_endpoint_returns_steps():
    pending_id = write_pending_recording("org-1", "https://x.test", [_action()], video_started_at_ms=1000)

    client = TestClient(app)
    response = client.get(f"/pending-recordings/{pending_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(pending_id)
    assert body["pages"][0]["steps"][0]["caption"] == "Click #go"


def test_get_pending_recording_endpoint_404s_for_an_unknown_id():
    client = TestClient(app)
    response = client.get(f"/pending-recordings/{uuid.uuid4()}")
    assert response.status_code == 404


def test_get_pending_recording_video_endpoint_streams_the_local_file(tmp_path):
    video_path = tmp_path / "video.webm"
    video_path.write_bytes(b"fake-webm-bytes")
    pending_id = write_pending_recording(
        "org-1", "https://x.test", [_action()], video_path=video_path,
        video_started_at_ms=0, video_ended_at_ms=1000,
    )

    client = TestClient(app)
    response = client.get(f"/pending-recordings/{pending_id}/video/0")

    assert response.status_code == 200
    assert response.content == b"fake-webm-bytes"


def test_discard_pending_recording_endpoint_removes_it():
    pending_id = write_pending_recording("org-1", "https://x.test", [_action()])
    pending_dir = get_pending_dir(str(pending_id))

    client = TestClient(app)
    response = client.post(f"/pending-recordings/{pending_id}/discard")

    assert response.status_code == 204
    assert not pending_dir.exists()


def test_upload_pending_recording_endpoint_persists_and_returns_recording_id():
    from atlas_engine.db import session_scope
    from atlas_engine.db.models import Organization, Recording

    with session_scope() as session:
        org = Organization(name="Pending API Org", slug=f"pending-api-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    pending_id = write_pending_recording(org_id, "https://x.test", [_action()])

    client = TestClient(app)
    response = client.post(f"/pending-recordings/{pending_id}/upload")

    assert response.status_code == 200
    recording_id = response.json()["recording_id"]
    with session_scope() as session:
        assert session.get(Recording, uuid.UUID(recording_id)).status == "captured"


def test_upload_pending_recording_endpoint_surfaces_the_real_error(monkeypatch):
    pending_id = write_pending_recording("org-1", "https://x.test", [_action()])

    def _raise(*args, **kwargs):
        raise RuntimeError("blob upload exploded")

    monkeypatch.setattr("atlas_engine.recording_service.persist_recording", _raise)

    client = TestClient(app)
    response = client.post(f"/pending-recordings/{pending_id}/upload")

    assert response.status_code == 500
    assert "blob upload exploded" in response.json()["detail"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd engine && .venv/bin/python -m pytest tests/test_api_pending_recordings.py tests/test_api_live_recording.py::test_start_and_stop_recording_endpoints -v`
Expected: FAIL — 404s/`KeyError: 'pending_id'` since none of the new routes or the rename exist yet

- [ ] **Step 4: Implement the endpoints**

In `engine/src/atlas_engine/api.py`, add to the imports:

```python
from atlas_engine.pending_recordings import (
    discard_pending_recording,
    get_pending_dir,
    get_pending_recording,
    list_pending_recordings,
    load_pending_actions,
    pending_recording_steps,
    upload_pending_recording,
)
```

Replace `StopRecordingResponse`:

```python
class StopRecordingResponse(BaseModel):
    pending_id: str
```

Replace `stop_recording_endpoint`'s return:

```python
    return StopRecordingResponse(recording_id=str(recording_id))
```

with:

```python
    return StopRecordingResponse(pending_id=str(result_id))
```

(and rename the local variable `recording_id` to `result_id` throughout that function body for clarity, matching Task 5's rename — `recording_id = stop_live_capture(uuid.UUID(capture_session_id))` becomes `result_id = stop_live_capture(uuid.UUID(capture_session_id))`.)

Add the new endpoints, right after `stop_recording_endpoint`:

```python
class PendingPageStep(BaseModel):
    order_index: int
    caption: str
    offset_ms: int | None


class PendingPageDetail(BaseModel):
    page_index: int
    has_video: bool
    duration_seconds: int | None
    steps: list[PendingPageStep]


class PendingRecordingSummary(BaseModel):
    id: str
    org_id: str
    user_id: str | None
    start_url: str
    role: str
    request_id: str | None
    created_at: str
    duration_seconds: int | None
    action_count: int
    has_video: bool
    upload_error: str | None


class PendingRecordingDetail(PendingRecordingSummary):
    pages: list[PendingPageDetail]


def _duration_seconds(started_at_ms: int | None, ended_at_ms: int | None) -> int | None:
    if started_at_ms is None or ended_at_ms is None:
        return None
    return max(0, round((ended_at_ms - started_at_ms) / 1000))


def _pending_summary(manifest) -> PendingRecordingSummary:
    return PendingRecordingSummary(
        id=manifest.id, org_id=manifest.org_id, user_id=manifest.user_id,
        start_url=manifest.start_url, role=manifest.role, request_id=manifest.request_id,
        created_at=manifest.created_at,
        duration_seconds=_duration_seconds(manifest.video_started_at_ms, manifest.video_ended_at_ms),
        action_count=manifest.action_count, has_video=manifest.has_video,
        upload_error=manifest.upload_error,
    )


@app.get("/pending-recordings", response_model=list[PendingRecordingSummary])
def list_pending_recordings_endpoint() -> list[PendingRecordingSummary]:
    return [_pending_summary(manifest) for manifest in list_pending_recordings()]


@app.get("/pending-recordings/{pending_id}", response_model=PendingRecordingDetail)
def get_pending_recording_endpoint(pending_id: str) -> PendingRecordingDetail:
    try:
        manifest = get_pending_recording(pending_id)
        pending_dir = get_pending_dir(pending_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    main_actions = load_pending_actions(pending_dir, "actions.json")
    pages = [PendingPageDetail(
        page_index=0, has_video=manifest.has_video,
        duration_seconds=_duration_seconds(manifest.video_started_at_ms, manifest.video_ended_at_ms),
        steps=[PendingPageStep(**step) for step in pending_recording_steps(main_actions, manifest.video_started_at_ms)],
    )]
    for page_info in manifest.pages:
        page_actions = load_pending_actions(pending_dir, f"actions-page{page_info['page_index']}.json")
        pages.append(PendingPageDetail(
            page_index=page_info["page_index"], has_video=page_info["has_video"],
            duration_seconds=_duration_seconds(page_info["video_started_at_ms"], page_info["video_ended_at_ms"]),
            steps=[
                PendingPageStep(**step)
                for step in pending_recording_steps(page_actions, page_info["video_started_at_ms"])
            ],
        ))

    return PendingRecordingDetail(**_pending_summary(manifest).model_dump(), pages=pages)


@app.get("/pending-recordings/{pending_id}/video/{page_index}")
def get_pending_recording_video_endpoint(pending_id: str, page_index: int):
    try:
        pending_dir = get_pending_dir(pending_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    filename = "video.webm" if page_index == 0 else f"video-page{page_index}.webm"
    video_path = pending_dir / filename
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="No video for this pending recording page")
    return FileResponse(video_path, media_type="video/webm")


class UploadPendingRecordingResponse(BaseModel):
    recording_id: str


@app.post("/pending-recordings/{pending_id}/upload", response_model=UploadPendingRecordingResponse)
def upload_pending_recording_endpoint(pending_id: str) -> UploadPendingRecordingResponse:
    try:
        recording_id = upload_pending_recording(pending_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        # Same reasoning as stop_recording_endpoint: this endpoint's only
        # caller is the single agent reviewing their own pending queue, who
        # needs the real error (e.g. the Azure failure this whole feature
        # exists to make non-destructive) rather than a content-free 500.
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")
    return UploadPendingRecordingResponse(recording_id=str(recording_id))


@app.post("/pending-recordings/{pending_id}/discard", status_code=204)
def discard_pending_recording_endpoint(pending_id: str) -> None:
    try:
        discard_pending_recording(pending_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd engine && .venv/bin/python -m pytest tests/test_api_pending_recordings.py tests/test_api_live_recording.py -v`
Expected: PASS, every test in both files

- [ ] **Step 6: Run the full backend test suite's non-Playwright-heavy subset to check for any other caller of the renamed field**

Run: `cd engine && grep -rn '"recording_id"\]' tests/ src/ | grep -v test_api_pending_recordings.py`
Expected: no remaining reads of a `/recordings/.../stop` response's `"recording_id"` key outside what was just updated. If the frontend's `useStopRecording` type shows up here first (it's TypeScript, this grep is Python-only, so it won't) — that's handled in Task 10.

- [ ] **Step 7: Commit**

```bash
cd /Users/siddharthsingh/Downloads/code/AtlasAI
git add engine/src/atlas_engine/api.py engine/tests/test_api_live_recording.py engine/tests/test_api_pending_recordings.py
git commit -m "feat: add pending-recordings API endpoints, rename stop response to pending_id"
```

---

### Task 7: Frontend `lib/pendingRecordings.ts`

**Files:**
- Create: `frontend/src/lib/pendingRecordings.ts`

**Interfaces:**
- Consumes: `apiFetch` from `frontend/src/lib/api.ts` (existing, unchanged).
- Produces: `PendingRecordingSummary`, `PendingRecordingDetail`, `PendingPageDetail`, `PendingPageStep` types; `usePendingRecordings()`, `usePendingRecordingDetail(pendingId)`, `useUploadPendingRecording(pendingId)`, `useDiscardPendingRecording(pendingId)` hooks; `pendingRecordingVideoUrl(pendingId, pageIndex)`. Used by Tasks 8, 9, 10.

- [ ] **Step 1: Write the file**

(No backend to stand up for a `.ts` lib file with no logic beyond `fetch` wiring — this task has no test of its own; Tasks 8/9's component tests exercise it.)

```typescript
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from './api'

export interface PendingPageStep {
  order_index: number
  caption: string
  offset_ms: number | null
}

export interface PendingPageDetail {
  page_index: number
  has_video: boolean
  duration_seconds: number | null
  steps: PendingPageStep[]
}

export interface PendingRecordingSummary {
  id: string
  org_id: string
  user_id: string | null
  start_url: string
  role: string
  request_id: string | null
  created_at: string
  duration_seconds: number | null
  action_count: number
  has_video: boolean
  upload_error: string | null
}

export interface PendingRecordingDetail extends PendingRecordingSummary {
  pages: PendingPageDetail[]
}

export function usePendingRecordings() {
  return useQuery({
    queryKey: ['pending-recordings'],
    queryFn: () => apiFetch<PendingRecordingSummary[]>('/pending-recordings'),
  })
}

export function usePendingRecordingDetail(pendingId: string | undefined) {
  return useQuery({
    queryKey: ['pending-recording', pendingId],
    queryFn: () => apiFetch<PendingRecordingDetail>(`/pending-recordings/${pendingId}`),
    enabled: !!pendingId,
  })
}

export function useUploadPendingRecording(pendingId: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => apiFetch<{ recording_id: string }>(`/pending-recordings/${pendingId}/upload`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['pending-recordings'] }),
  })
}

export function useDiscardPendingRecording(pendingId: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => apiFetch<void>(`/pending-recordings/${pendingId}/discard`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['pending-recordings'] }),
  })
}

export function pendingRecordingVideoUrl(pendingId: string, pageIndex: number): string {
  const base = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
  return `${base}/pending-recordings/${pendingId}/video/${pageIndex}`
}
```

- [ ] **Step 2: Commit**

```bash
cd /Users/siddharthsingh/Downloads/code/AtlasAI
git add frontend/src/lib/pendingRecordings.ts
git commit -m "feat: add frontend hooks for the pending-recordings API"
```

---

### Task 8: `PendingRecordingsPage`

**Files:**
- Create: `frontend/src/pages/agent/PendingRecordingsPage.tsx`
- Create: `frontend/src/pages/agent/PendingRecordingsPage.test.tsx`

**Interfaces:**
- Consumes: `usePendingRecordings` (Task 7). Mirrors `AgentLibraryPage.tsx`'s layout and `apiFetch`-mock test pattern from `AgentLibraryPage.test.tsx`.
- Produces: `PendingRecordingsPage` component, navigating to `/agent/pending/:pendingId` on card click. Used by Task 10's route wiring.

- [ ] **Step 1: Write the failing test**

```typescript
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../../auth/AuthContext'
import { PendingRecordingsPage } from './PendingRecordingsPage'

const PENDING = [
  {
    id: 'pending-1', org_id: 'org-1', user_id: 'agent-1', start_url: 'https://x.test',
    role: 'demo', request_id: null, created_at: '2026-11-13T10:00:00+00:00',
    duration_seconds: 42, action_count: 5, has_video: true, upload_error: null,
  },
]

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/auth/me') {
      return Promise.resolve(jsonResponse({ user_id: 'agent-1', org_id: 'org-1', email: 'a@x.com', role: 'member', job_function: 'operations_agent' }))
    }
    if (path === '/pending-recordings') return Promise.resolve(jsonResponse(PENDING))
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <MemoryRouter initialEntries={['/agent/pending']}>
          <PendingRecordingsPage />
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  )
}

describe('PendingRecordingsPage', () => {
  it('lists pending recordings from the local queue', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByText('https://x.test')).toBeInTheDocument())
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/agent/PendingRecordingsPage.test.tsx`
Expected: FAIL — cannot find module `./PendingRecordingsPage`

- [ ] **Step 3: Write the component**

```typescript
import { useNavigate } from 'react-router-dom'
import { VideoOff } from 'lucide-react'
import { usePendingRecordings } from '../../lib/pendingRecordings'
import { formatDuration, formatRelativeTime } from '../../lib/format'

export function PendingRecordingsPage() {
  const navigate = useNavigate()
  const { data: pending, isLoading } = usePendingRecordings()

  if (isLoading) return <div className="p-9 text-sm text-atlas-body">Loading…</div>

  return (
    <div className="p-9 pb-16 flex flex-col gap-7 max-w-[1180px]">
      <div className="flex flex-col gap-1.5">
        <h1 className="font-display text-[30px] font-semibold tracking-tight m-0">Pending uploads</h1>
        <div className="text-sm text-atlas-body">
          Recordings waiting on this machine — review, then upload or discard.
        </div>
      </div>

      <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-5">
        {(pending ?? []).map((p) => (
          <button
            key={p.id}
            onClick={() => navigate(`/agent/pending/${p.id}`)}
            className="text-left bg-atlas-card border border-atlas-border rounded-2xl overflow-hidden hover:shadow-lg hover:-translate-y-0.5 transition-all"
          >
            <div className="aspect-video bg-[#e8eae8] relative flex items-center justify-center">
              {!p.has_video && <VideoOff size={24} className="text-atlas-muted" />}
              <div className="absolute right-2.5 bottom-2.5 text-[11px] text-white bg-black/55 px-2 py-0.5 rounded-full tabular-nums">
                {formatDuration(p.duration_seconds)}
              </div>
            </div>
            <div className="p-4 flex flex-col gap-2.5">
              <div className="text-[14.5px] font-medium tracking-tight">{p.start_url}</div>
              {p.upload_error && (
                <div className="text-[12px] text-atlas-danger">Last upload failed — tap to retry</div>
              )}
              <div className="flex items-center justify-between gap-2 pt-1.5 border-t border-atlas-rowBorder">
                <div className="text-[12px] text-atlas-muted">{formatRelativeTime(p.created_at)}</div>
                <div className="text-[12px] text-atlas-muted">{p.action_count} actions</div>
              </div>
            </div>
          </button>
        ))}
      </div>

      {(pending ?? []).length === 0 && (
        <div className="text-sm text-atlas-body">Nothing pending — every recording on this machine is uploaded.</div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/pages/agent/PendingRecordingsPage.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/siddharthsingh/Downloads/code/AtlasAI
git add frontend/src/pages/agent/PendingRecordingsPage.tsx frontend/src/pages/agent/PendingRecordingsPage.test.tsx
git commit -m "feat: add PendingRecordingsPage listing the local pending queue"
```

---

### Task 9: `PendingReviewPage`

**Files:**
- Create: `frontend/src/pages/agent/PendingReviewPage.tsx`
- Create: `frontend/src/pages/agent/PendingReviewPage.test.tsx`

**Interfaces:**
- Consumes: `usePendingRecordingDetail`, `useUploadPendingRecording`, `useDiscardPendingRecording`, `pendingRecordingVideoUrl` (Task 7); `VideoStepPlayer` (existing component, unchanged — it already takes a `recordingId`/`pageIndex` pair and builds its own video `<src>` via a URL-building function, so this page passes it a small per-page wrapper, see Step 3).
- Produces: `PendingReviewPage` component. On successful Upload, navigates to `/agent/recordings/:recording_id` (the existing `ReviewPage`). On Discard, navigates to `/agent/pending`. Used by Task 10's route wiring.

- [ ] **Step 1: Write the failing test**

```typescript
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../../auth/AuthContext'
import { PendingReviewPage } from './PendingReviewPage'

const PENDING_DETAIL = {
  id: 'pending-1', org_id: 'org-1', user_id: 'agent-1', start_url: 'https://x.test',
  role: 'demo', request_id: null, created_at: '2026-11-13T10:00:00+00:00',
  duration_seconds: 42, action_count: 1, has_video: false, upload_error: null,
  pages: [{ page_index: 0, has_video: false, duration_seconds: 42, steps: [{ order_index: 0, caption: 'Click #go', offset_ms: 0 }] }],
}

function jsonResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => data }
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    const path = String(url).replace('http://localhost:8000', '')
    if (path === '/auth/me') {
      return Promise.resolve(jsonResponse({ user_id: 'agent-1', org_id: 'org-1', email: 'a@x.com', role: 'member', job_function: 'operations_agent' }))
    }
    if (path === '/pending-recordings/pending-1' && (init?.method ?? 'GET') === 'GET') {
      return Promise.resolve(jsonResponse(PENDING_DETAIL))
    }
    if (path === '/pending-recordings/pending-1/upload' && init?.method === 'POST') {
      return Promise.resolve(jsonResponse({ recording_id: 'rec-9' }))
    }
    return Promise.reject(new Error(`unexpected fetch: ${path}`))
  }))
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <MemoryRouter initialEntries={['/agent/pending/pending-1']}>
          <Routes>
            <Route path="/agent/pending/:pendingId" element={<PendingReviewPage />} />
            <Route path="/agent/recordings/:recordingId" element={<div>Review page for rec-9</div>} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  )
}

describe('PendingReviewPage', () => {
  it('uploads and navigates to the real review page on success', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByText('Click #go')).toBeInTheDocument())

    fireEvent.click(screen.getByText('Upload'))

    await waitFor(() => expect(screen.getByText('Review page for rec-9')).toBeInTheDocument())
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/agent/PendingReviewPage.test.tsx`
Expected: FAIL — cannot find module `./PendingReviewPage`

- [ ] **Step 3: Write the component**

```typescript
import { useNavigate, useParams } from 'react-router-dom'
import {
  pendingRecordingVideoUrl, useDiscardPendingRecording, usePendingRecordingDetail, useUploadPendingRecording,
} from '../../lib/pendingRecordings'
import { formatDuration } from '../../lib/format'

export function PendingReviewPage() {
  const { pendingId } = useParams<{ pendingId: string }>()
  const navigate = useNavigate()
  const { data: pending, isLoading } = usePendingRecordingDetail(pendingId)
  const upload = useUploadPendingRecording(pendingId)
  const discard = useDiscardPendingRecording(pendingId)

  if (isLoading || !pending) return <div className="p-9 text-sm text-atlas-body">Loading…</div>

  const mainPage = pending.pages[0]

  async function handleUpload() {
    const result = await upload.mutateAsync()
    navigate(`/agent/recordings/${result.recording_id}`)
  }

  async function handleDiscard() {
    await discard.mutateAsync()
    navigate('/agent/pending')
  }

  return (
    <div className="p-9 pb-16 flex flex-col gap-6 max-w-[1280px]">
      <div className="flex items-start justify-between gap-6">
        <div className="flex flex-col gap-2">
          <h1 className="font-display text-[28px] font-semibold tracking-tight m-0">Review before upload</h1>
          <div className="text-[13.5px] text-atlas-body">
            {pending.start_url} · {pending.action_count} action{pending.action_count === 1 ? '' : 's'}
            {mainPage ? ` · ${formatDuration(mainPage.duration_seconds)}` : ''}
          </div>
          <div className="text-[12.5px] text-atlas-body">
            Only on this machine until uploaded — nobody else can see this recording yet.
          </div>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={handleDiscard}
            disabled={upload.isPending || discard.isPending}
            className="px-5 py-2.5 rounded-full border border-atlas-border text-[13.5px] font-medium disabled:opacity-60"
          >
            {discard.isPending ? 'Discarding…' : 'Discard'}
          </button>
          <button
            onClick={handleUpload}
            disabled={upload.isPending || discard.isPending}
            className="px-5 py-2.5 rounded-full bg-atlas-primary hover:bg-atlas-primaryHover text-white text-[13.5px] font-medium disabled:opacity-60"
          >
            {upload.isPending ? 'Uploading…' : 'Upload'}
          </button>
        </div>
      </div>

      {upload.isError && (
        <div className="text-[13px] text-atlas-danger">{(upload.error as Error).message}</div>
      )}

      {mainPage?.has_video && (
        <video src={pendingRecordingVideoUrl(pending.id, 0)} controls className="rounded-2xl overflow-hidden border border-atlas-border bg-black aspect-video w-full max-w-[720px]" />
      )}

      <div className="flex flex-col gap-2">
        {mainPage?.steps.map((step) => (
          <div key={step.order_index} className="text-[13.5px] text-atlas-ink py-1.5 border-b border-atlas-rowBorder">
            {step.caption}
          </div>
        ))}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/pages/agent/PendingReviewPage.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/siddharthsingh/Downloads/code/AtlasAI
git add frontend/src/pages/agent/PendingReviewPage.tsx frontend/src/pages/agent/PendingReviewPage.test.tsx
git commit -m "feat: add PendingReviewPage with Upload/Discard actions"
```

---

### Task 10: Wire navigation, nav entry, and routes

**Files:**
- Modify: `frontend/src/pages/agent/LiveRecordingPage.tsx`
- Modify: `frontend/src/layout/AppLayout.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/lib/recordings.ts` (`useStopRecording`'s return type)

**Interfaces:**
- Consumes: `PendingRecordingsPage` (Task 8), `PendingReviewPage` (Task 9).
- Produces: the fully wired end-to-end flow — Stop click now lands on `/agent/pending/:pendingId`.

- [ ] **Step 1: Update `useStopRecording`'s type**

In `frontend/src/lib/recordings.ts`, replace:

```typescript
export function useStopRecording() {
  return useMutation({
    mutationFn: (captureSessionId: string) =>
      apiFetch<{ recording_id: string }>(`/recordings/${captureSessionId}/stop`, { method: 'POST' }),
  })
}
```

with:

```typescript
export function useStopRecording() {
  return useMutation({
    mutationFn: (captureSessionId: string) =>
      apiFetch<{ pending_id: string }>(`/recordings/${captureSessionId}/stop`, { method: 'POST' }),
  })
}
```

- [ ] **Step 2: Update `LiveRecordingPage.handleStop`**

In `frontend/src/pages/agent/LiveRecordingPage.tsx`, replace:

```typescript
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
```

with:

```typescript
  async function handleStop() {
    if (!captureSessionId) return
    setStopError(null)
    try {
      const result = await stopRecording.mutateAsync(captureSessionId)
      navigate(`/agent/pending/${result.pending_id}`)
    } catch (err) {
      setStopError(err instanceof ApiError ? err.message : 'Could not stop the recording. If you closed the browser window, wait a moment and try again.')
    }
  }
```

- [ ] **Step 3: Add the nav entry**

In `frontend/src/layout/AppLayout.tsx`, replace:

```typescript
  agent: [
    { to: '/agent', label: 'Request inbox' },
    { to: '/agent/library', label: 'Recording library' },
  ],
```

with:

```typescript
  agent: [
    { to: '/agent', label: 'Request inbox' },
    { to: '/agent/library', label: 'Recording library' },
    { to: '/agent/pending', label: 'Pending uploads' },
  ],
```

- [ ] **Step 4: Add the routes**

In `frontend/src/App.tsx`, add the two imports alongside the existing agent page imports:

```typescript
import { PendingRecordingsPage } from './pages/agent/PendingRecordingsPage'
import { PendingReviewPage } from './pages/agent/PendingReviewPage'
```

and add the two routes alongside the existing `/agent/...` routes:

```typescript
          <Route path="/agent/pending" element={<PendingRecordingsPage />} />
          <Route path="/agent/pending/:pendingId" element={<PendingReviewPage />} />
```

- [ ] **Step 5: Run the full frontend test suite**

Run: `cd frontend && npx vitest run`
Expected: PASS, every test file — in particular `LiveRecordingPage` has no existing test file to update (confirmed via `ls frontend/src/pages/agent/*.test.tsx` showing only `AgentLibraryPage.test.tsx`, `ReviewPage.test.tsx`, and the two new ones from Tasks 8–9), so there's nothing else to touch here.

- [ ] **Step 6: Manually verify the flow once, end to end**

Run: use the `run` skill (or start the backend with `cd engine && .venv/bin/python -m atlas_engine.main` and the frontend with `cd frontend && npm run dev`) and walk through: start a recording → Stop and review → confirm you land on `/agent/pending/<id>` showing the review page, not a Postgres-backed one → click Upload → confirm you land on `/agent/recordings/<id>` with the recording's status showing `captured`.
Expected: the full loop works, and killing network access to Azure Blob during the Stop step (e.g. by pointing `ATLAS_AZURE_STORAGE_CONNECTION_STRING` at an unreachable host) no longer breaks Stop at all — only a later Upload click is affected, and failing there leaves the recording in `/agent/pending` instead of losing it.

- [ ] **Step 7: Commit**

```bash
cd /Users/siddharthsingh/Downloads/code/AtlasAI
git add frontend/src/lib/recordings.ts frontend/src/pages/agent/LiveRecordingPage.tsx frontend/src/layout/AppLayout.tsx frontend/src/App.tsx
git commit -m "feat: wire Stop and review into the local pending queue end to end"
```
