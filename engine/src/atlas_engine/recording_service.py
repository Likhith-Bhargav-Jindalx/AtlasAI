import json
import uuid
from datetime import datetime, timezone

from atlas_engine.brain.serialize import trajectory_to_dict
from atlas_engine.capture.events import RecordedAction
from atlas_engine.config import settings
from atlas_engine.db import session_scope
from atlas_engine.db.models import Recording, RecordingRequest, ReferenceTrajectory
from atlas_engine.db.repo import get_or_create_portal_profile
from atlas_engine.portal_profile.infer import infer_portal_profile
from atlas_engine.storage import LocalRunStore
from atlas_engine.trajectory.extract import extract_trajectory


def persist_recording(
    org_id: str,
    start_url: str,
    actions: list[RecordedAction],
    *,
    request_id: uuid.UUID | None = None,
    role: str = "demo",
) -> uuid.UUID:
    """Persists a captured recording — actions.json, the extracted
    trajectory, and the Recording/ReferenceTrajectory rows — without
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

        if request_id is not None:
            request = session.get(RecordingRequest, request_id)
            if request is not None and request.status == "open":
                request.status = "submitted"

        recording_id = recording.id

    return recording_id
