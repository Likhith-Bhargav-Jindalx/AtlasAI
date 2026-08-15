import json
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

from atlas_engine.brain.loop import BrainLoop
from atlas_engine.brain.serialize import trajectory_from_dict
from atlas_engine.codegen.emit import emit_golden_script
from atlas_engine.config import settings
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, PortalProfile, Recording, RecordingRequest
from atlas_engine.db.models import ReferenceTrajectory as ReferenceTrajectoryModel
from atlas_engine.db.repo import next_golden_script_version
from atlas_engine.llm.client import LlmClient
from atlas_engine.storage import LocalRunStore
from atlas_engine.verify.certify import certify_golden_script


def author_from_recording(recording_id: uuid.UUID, *, llm: LlmClient | None = None) -> dict:
    """Loads an already-persisted recording's trajectory and runs the
    authoring pipeline against it. Fully decoupled from recording_service:
    this works identically whether the recording was made by the person
    calling this function or by someone else entirely.
    """
    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        if recording is None:
            raise ValueError(f"No such recording: {recording_id}")

        profile = session.get(PortalProfile, recording.portal_profile_id)
        trajectory_row = (
            session.query(ReferenceTrajectoryModel).filter_by(recording_id=recording_id).one()
        )

        org_id = recording.org_id
        portal_profile_id = recording.portal_profile_id
        start_url = profile.inferred_login_url or f"https://{profile.domain}"
        trajectory_blob_ref = trajectory_row.trajectory_blob_ref
        request_id = recording.request_id
        pre_action_ms = profile.pacing_pre_action_ms or 0
        settle_after_nav_ms = profile.pacing_settle_after_nav_ms or 0

    trajectory = trajectory_from_dict(json.loads(Path(trajectory_blob_ref).read_text()))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(start_url)
        result = BrainLoop(
            page, llm=llm, pacing_pre_action_ms=pre_action_ms, pacing_settle_after_nav_ms=settle_after_nav_ms,
        ).run(trajectory)
        browser.close()

    # The golden must freeze the VERIFIED trajectory — a repaired selector
    # (LLM/cached) is folded into result.trajectory, so emitting the original
    # would silently ship the stale one that just failed.
    trajectory = result.trajectory
    all_on_track = result.success

    store = LocalRunStore(root=settings.ensure_run_root())
    run_dir = store.new_run_dir(prefix="goldens")
    script_text = emit_golden_script(
        trajectory, start_url=start_url, pre_action_ms=pre_action_ms, settle_after_nav_ms=settle_after_nav_ms,
    )
    script_path = run_dir.path / "golden.py"
    script_path.write_text(script_text)

    if not all_on_track:
        status = "draft"
    else:
        certification = certify_golden_script(script_path, runs=2)
        status = "certified" if certification.is_certified else "draft"

    with session_scope() as session:
        golden = GoldenScript(
            org_id=org_id,
            portal_profile_id=portal_profile_id,
            version=next_golden_script_version(session, portal_profile_id),
            script_blob_ref=str(script_path),
            status=status,
            created_from_recording_id=recording_id,
        )
        session.add(golden)
        session.flush()
        golden_script_id = golden.id

        if status == "certified" and request_id is not None:
            request = session.get(RecordingRequest, request_id)
            if request is not None:
                request.status = "fulfilled"
                request.fulfilled_golden_script_id = golden_script_id

    return {"status": status, "script_path": str(script_path), "golden_script_id": str(golden_script_id)}
