import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from atlas_engine.brain.serialize import trajectory_to_dict
from atlas_engine.capture.events import RecordedAction
from atlas_engine.config import settings
from atlas_engine.db import session_scope
from atlas_engine.db.models import (
    AuthoringRun,
    PageExtraction,
    PortalProfile,
    Recording,
    RecordingRequest,
    ReferenceTrajectory,
    VideoAsset,
    VideoStep,
)
from atlas_engine.db.repo import get_or_create_portal_profile
from atlas_engine.portal_profile.infer import infer_portal_profile
from atlas_engine.storage import LocalRunStore
from atlas_engine.trajectory.extract import extract_trajectory


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
    page_extractions: list[dict] | None = None,
    codegen_script: str | None = None,
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

    if codegen_script is not None:
        (run_dir.path / "codegen.py").write_text(codegen_script)

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

        for extraction in (page_extractions or []):
            session.add(
                PageExtraction(
                    recording_id=recording.id,
                    page_index=extraction["page_index"],
                    url=extraction["url"],
                    data=extraction["data"],
                    extracted_at=datetime.now(timezone.utc),
                )
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


def list_recordings(org_id: str, status: str | None = None) -> list[dict]:
    with session_scope() as session:
        query = session.query(Recording).filter_by(org_id=uuid.UUID(org_id))
        if status is not None:
            query = query.filter_by(status=status)

        results = []
        for recording in query.order_by(Recording.created_at.desc()).all():
            video_asset = (
                session.query(VideoAsset)
                .filter_by(recording_id=recording.id, page_index=0)
                .one_or_none()
            )
            request = session.get(RecordingRequest, recording.request_id) if recording.request_id else None
            profile = session.get(PortalProfile, recording.portal_profile_id)
            results.append({
                "id": str(recording.id),
                "status": recording.status,
                "role": recording.role,
                "request_id": str(recording.request_id) if recording.request_id else None,
                "request_title": request.title if request is not None else None,
                "portal_domain": profile.domain if profile is not None else None,
                "duration_seconds": recording.duration_seconds,
                "has_video": video_asset is not None,
                "created_at": recording.created_at.isoformat(),
            })
        return results


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

        codegen_script = None
        run_root = Path(settings.ensure_run_root())
        codegen_path = run_root / recording.capture_blob_prefix / "codegen.py"
        if not codegen_path.exists():
            codegen_path = Path(recording.capture_blob_prefix) / "codegen.py"

        if codegen_path.exists() and codegen_path.stat().st_size > 0:
            codegen_script = codegen_path.read_text()
        else:
            traj_path = run_root / recording.capture_blob_prefix / "trajectory.json"
            if not traj_path.exists():
                traj_path = Path(recording.capture_blob_prefix) / "trajectory.json"
            if traj_path.exists() and traj_path.stat().st_size > 0:
                try:
                    from atlas_engine.brain.serialize import trajectory_from_dict
                    from atlas_engine.codegen.emit import emit_golden_script
                    traj_dict = json.loads(traj_path.read_text())
                    traj = trajectory_from_dict(traj_dict)
                    if traj.steps:
                        profile = session.get(PortalProfile, recording.portal_profile_id)
                        inferred_url = profile.inferred_login_url if profile else "http://localhost/"
                        start_url = inferred_url or "http://localhost/"
                        codegen_script = emit_golden_script(traj, start_url=start_url)
                        try:
                            codegen_path.write_text(codegen_script)
                        except Exception:
                            pass
                except Exception:
                    pass

        latest_run = (
            session.query(AuthoringRun)
            .filter_by(recording_id=recording.id)
            .order_by(AuthoringRun.created_at.desc())
            .first()
        )
        latest_authoring_run_id = str(latest_run.id) if latest_run is not None else None

        return {
            "id": str(recording.id),
            "status": recording.status,
            "role": recording.role,
            "latest_authoring_run_id": latest_authoring_run_id,
            "codegen_script": codegen_script,
            "pages": pages,
        }


def list_page_extractions(recording_id: uuid.UUID) -> list[dict]:
    with session_scope() as session:
        extractions = (
            session.query(PageExtraction)
            .filter_by(recording_id=recording_id)
            .order_by(PageExtraction.extracted_at)
            .all()
        )
        return [
            {
                "id": str(e.id),
                "page_index": e.page_index,
                "url": e.url,
                "data": e.data,
                "extracted_at": e.extracted_at.isoformat(),
            }
            for e in extractions
        ]


def _caption_for(action: RecordedAction) -> str:
    selector = action.selector_candidates[0]["selector"] if action.selector_candidates else "the page"
    if action.kind == "click":
        return f"Click {selector}"
    if action.kind == "fill":
        return f"Fill {selector}"
    return f"Navigate to {action.url_after}"


def create_recording_request(
    org_id: str, requested_by: str, target_url: str, title: str, description: str | None = None
) -> uuid.UUID:
    domain = target_url.split("/")[2]
    with session_scope() as session:
        profile = get_or_create_portal_profile(session, org_id, domain)
        request = RecordingRequest(
            org_id=uuid.UUID(org_id),
            requested_by=uuid.UUID(requested_by),
            portal_profile_id=profile.id,
            target_url=target_url,
            title=title,
            description=description,
        )
        session.add(request)
        session.flush()
        return request.id


def list_recording_requests(org_id: str, status: str | None = None) -> list[dict]:
    with session_scope() as session:
        query = session.query(RecordingRequest).filter_by(org_id=uuid.UUID(org_id))
        if status is not None:
            query = query.filter_by(status=status)
        return [
            {
                "id": str(r.id),
                "title": r.title,
                "target_url": r.target_url,
                "status": r.status,
                "claimed_by": str(r.claimed_by) if r.claimed_by else None,
            }
            for r in query.order_by(RecordingRequest.created_at.desc()).all()
        ]


def claim_recording_request(request_id: uuid.UUID, developer_user_id: uuid.UUID) -> None:
    with session_scope() as session:
        request = session.get(RecordingRequest, request_id)
        if request is None:
            raise ValueError(f"No such recording request: {request_id}")
        request.claimed_by = developer_user_id
        request.claimed_at = datetime.now(timezone.utc).replace(tzinfo=None)
