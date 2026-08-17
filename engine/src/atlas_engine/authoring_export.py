"""Bridges a recording's data between two separate databases/stores — the
shared deployed backend and the desktop exe's local scratch environment —
so the authoring pipeline's unmodified code can run against a real,
populated Recording row in the scratch database.

export_recording_for_authoring runs wherever the recording actually lives
(the deployed backend, via GET /recordings/{id}/export-for-authoring).
import_recording_for_authoring runs against whatever database
session_scope()/get_store() currently resolve to — the scratch
environment, once atlas_engine.scratch_env.activate_scratch_environment()
has been called — and recreates the minimal rows/blobs the authoring
pipeline actually reads.
"""

import uuid

from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, PortalProfile, Recording, ReferenceTrajectory
from atlas_engine.storage import get_store


def export_recording_for_authoring(recording_id: uuid.UUID) -> dict:
    """Server-side: gathers everything the authoring pipeline needs to run
    against this recording, from wherever it's actually persisted."""
    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        if recording is None:
            raise ValueError(f"No such recording: {recording_id}")
        profile = session.get(PortalProfile, recording.portal_profile_id)
        org = session.get(Organization, recording.org_id)
        trajectory_row = session.query(ReferenceTrajectory).filter_by(recording_id=recording_id).one()

        data = {
            "org": {"id": str(org.id), "name": org.name, "slug": org.slug},
            "portal_profile": {
                "domain": profile.domain,
                "display_name": profile.display_name,
                "inferred_login_url": profile.inferred_login_url,
                "inferred_success_signal": profile.inferred_success_signal,
                "confidence_score": float(profile.confidence_score) if profile.confidence_score is not None else None,
                "pacing_pre_action_ms": profile.pacing_pre_action_ms,
                "pacing_settle_after_nav_ms": profile.pacing_settle_after_nav_ms,
            },
            "recording": {"role": recording.role},
            "trajectory_blob_ref": trajectory_row.trajectory_blob_ref,
            "capture_blob_prefix": recording.capture_blob_prefix,
        }

    store = get_store()
    data["trajectory_json"] = store.read_json(data["trajectory_blob_ref"])
    codegen_blob_ref = f"{data['capture_blob_prefix']}/codegen.py"
    data["codegen_script"] = store.read_text(codegen_blob_ref) if store.exists(codegen_blob_ref) else None
    return data


def import_recording_for_authoring(export_data: dict) -> uuid.UUID:
    """Desktop-side: recreates the minimal Organization/PortalProfile/
    Recording/ReferenceTrajectory rows and blob files this export
    describes, in whatever database/store session_scope()/get_store()
    currently resolve to (the scratch environment, once activated) — so
    the authoring pipeline's existing code can run against a real,
    populated Recording row exactly as it would against the original.
    Deliberately doesn't try to recreate a matching RecordingRequest row
    (request_id stays unset on the scratch Recording) — the authoring
    pipeline only uses it to mark a request "fulfilled" on success, which
    is meaningless for a request that doesn't exist in this database;
    that update happens for real when the result is submitted back to the
    deployed backend, against the *real* request row there.

    Returns the new (scratch-local) recording id to author against —
    unrelated to the id on the deployed backend the data came from.
    """
    store = get_store()

    with session_scope() as session:
        org = Organization(name=export_data["org"]["name"], slug=export_data["org"]["slug"])
        session.add(org)
        session.flush()

        p = export_data["portal_profile"]
        profile = PortalProfile(
            org_id=org.id,
            domain=p["domain"],
            display_name=p["display_name"],
            inferred_login_url=p["inferred_login_url"],
            inferred_success_signal=p["inferred_success_signal"],
            confidence_score=p["confidence_score"],
            pacing_pre_action_ms=p["pacing_pre_action_ms"],
            pacing_settle_after_nav_ms=p["pacing_settle_after_nav_ms"],
        )
        session.add(profile)
        session.flush()

        run_dir = store.new_run_dir(prefix="recordings")
        trajectory_blob_ref = run_dir.write_json("trajectory.json", export_data["trajectory_json"])
        if export_data.get("codegen_script"):
            run_dir.write_text("codegen.py", export_data["codegen_script"])

        recording = Recording(
            org_id=org.id,
            portal_profile_id=profile.id,
            status="captured",
            capture_blob_prefix=run_dir.blob_ref,
            role=export_data["recording"]["role"],
        )
        session.add(recording)
        session.flush()

        session.add(ReferenceTrajectory(recording_id=recording.id, trajectory_blob_ref=trajectory_blob_ref))

        recording_id = recording.id

    return recording_id
