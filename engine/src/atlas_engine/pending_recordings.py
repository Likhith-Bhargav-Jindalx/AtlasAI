import json
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from atlas_engine.brain.serialize import trajectory_to_dict
from atlas_engine.capture.events import RecordedAction
from atlas_engine.config import settings
from atlas_engine.recording_service import PageCapture, _caption_for, _merge_actions_for_trajectory
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
    enough for upload_pending_recording to rebuild the exact PageCapture
    list persist_recording expects."""

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
    button; upload_pending_recording() is what actually calls
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
        action_count=len(actions) + sum(len(p.actions) for p in additional_pages or []),
        has_video=has_video,
        pages=pages_info,
    )
    manifest.save(pending_dir)

    return pending_id


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


def discard_pending_recording(pending_id: str) -> None:
    shutil.rmtree(get_pending_dir(pending_id))


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
