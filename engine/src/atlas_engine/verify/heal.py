import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from atlas_engine.brain.cache import ActionCacheStore
from atlas_engine.brain.loop import BrainLoop
from atlas_engine.brain.models import ReferenceTrajectory
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


def _primary_selector(trajectory: ReferenceTrajectory, index: int) -> str:
    candidates = trajectory.steps[index].selector_candidates
    return candidates[0]["selector"] if candidates else ""


def compute_trajectory_diff(old: ReferenceTrajectory, new: ReferenceTrajectory, outcomes: list | None = None) -> list[dict]:
    shot_after_by_index: dict[int, str | None] = {}
    if outcomes is not None:
        shot_after_by_index = {o.step_index: o.shot_after for o in outcomes}

    diff = []
    for i in range(min(len(old.steps), len(new.steps))):
        old_selector = _primary_selector(old, i)
        new_selector = _primary_selector(new, i)
        if old_selector != new_selector:
            diff.append({
                "step_index": i,
                "field": "selector",
                "old_value": old_selector,
                "new_value": new_selector,
                "before_screenshot": shot_after_by_index.get(i - 1) if outcomes is not None else None,
                "after_screenshot": shot_after_by_index.get(i) if outcomes is not None else None,
            })
    return diff


def heal_golden_script(
    golden_script_id: uuid.UUID, *, llm: LlmClient | None = None, trigger: str = "on_demand"
) -> dict:
    """Re-validates an existing golden script's trajectory against a live
    session. If every step is still on-track, nothing changes. If a step
    has drifted, this rebuilds the trajectory from whatever selector
    actually worked live (original, cached, or LLM-repaired), emits and
    certifies a new draft version, and records a HealEvent — the new
    version is never made active here; only approve_heal_event can do
    that.
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

    store = LocalRunStore(root=settings.ensure_run_root())
    shot_run_dir = store.new_run_dir(prefix="heal-shots")
    shot_dir = str(shot_run_dir.path)

    cache = ActionCacheStore()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(start_url)
        result = BrainLoop(page, llm=llm, action_cache=cache, golden_script_id=golden_script_id).run(
            old_trajectory, shot_dir=shot_dir
        )
        browser.close()

    # BrainLoop.run() applies any successful repair internally and returns the
    # VERIFIED trajectory — so the diff below is built from what actually
    # worked live (original selector on a clean run, corrected selector after
    # a repair), which is what distinguishes "was already fine" from "needed
    # and got a repair".
    new_trajectory = result.trajectory
    diff = compute_trajectory_diff(old_trajectory, new_trajectory, outcomes=result)
    all_on_track = result.success

    if not diff and all_on_track:
        return {"status": "already_certified", "heal_event_id": None, "golden_script_id": str(golden_script_id)}

    store = LocalRunStore(root=settings.ensure_run_root())
    run_dir = store.new_run_dir(prefix="goldens")
    script_text = emit_golden_script(new_trajectory, start_url=start_url)
    script_path = run_dir.path / "golden.py"
    script_path.write_text(script_text)

    if all_on_track:
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


def list_heal_events(org_id: str, status: str | None = None) -> list[dict]:
    with session_scope() as session:
        query = session.query(HealEvent).filter_by(org_id=uuid.UUID(org_id))
        if status is not None:
            query = query.filter_by(status=status)

        results = []
        for event in query.order_by(HealEvent.created_at.desc()).all():
            golden_script = session.get(GoldenScript, event.golden_script_id)
            profile = session.get(PortalProfile, golden_script.portal_profile_id) if golden_script else None
            results.append({
                "id": str(event.id),
                "golden_script_id": str(event.golden_script_id),
                "portal_domain": profile.domain if profile is not None else None,
                "trigger": event.trigger,
                "status": event.status,
                "diff_summary": event.diff_summary,
                "created_at": event.created_at.isoformat(),
            })
        return results
