import uuid
from pathlib import Path

from atlas_engine.authoring.events import log_event
from atlas_engine.brain.loop import BrainLoop
from atlas_engine.brain.models import ReferenceTrajectory
from atlas_engine.codegen.emit import emit_golden_script
from atlas_engine.config import settings
from atlas_engine.db import session_scope
from atlas_engine.db.models import CertificationRun, GoldenScript
from atlas_engine.db.repo import next_golden_script_version
from atlas_engine.llm.client import LlmClient
from atlas_engine.storage import get_store
from atlas_engine.verify.certify import certify_golden_script_once


def run_extract_phase(trajectory: ReferenceTrajectory) -> ReferenceTrajectory:
    """Named as its own pipeline phase (matching the spec's replay ->
    extract -> brain sequence) even though it's the identity function
    today — replay's output is already a ReferenceTrajectory. This is
    the seam for any future trajectory-validation logic without
    changing what callers pass to run_brain_phase."""
    return trajectory


def run_brain_phase(
    page, trajectory: ReferenceTrajectory, authoring_run_id: uuid.UUID, *, llm: LlmClient | None = None,
    shot_dir: str | None = None, pre_action_ms: int = 0, settle_after_nav_ms: int = 0,
):
    """Headed, LLM-driven supervised replay — BrainLoop already does
    perceive/act/evaluate/think/validate/repair internally (multimodal, using
    the reference screenshots the replay phase captured); this just runs it
    and turns its per-step outcomes into the run's event log. No developer
    fallback here: a step that's still off-track after BrainLoop's bounded
    repair attempts fails the run (BrainLoop stops, exactly like the old app),
    and the caller (the orchestrator) hard-fails it. Returns the
    BrainLoopResult — use `result.success` and `result.trajectory` (the
    verified trajectory, repairs folded in), not a zip of the input steps."""
    if shot_dir is None:
        shot_dir = str(Path(settings.ensure_run_root()) / "authoring" / str(authoring_run_id) / "brain")
    result = BrainLoop(
        page, llm=llm, pacing_pre_action_ms=pre_action_ms, pacing_settle_after_nav_ms=settle_after_nav_ms,
    ).run(trajectory, shot_dir=shot_dir)

    for outcome in result:
        if outcome.on_track:
            log_event(
                authoring_run_id, phase="brain", step_index=outcome.step_index,
                message=f"Step {outcome.step_index}: OK",
            )
        else:
            log_event(
                authoring_run_id, phase="brain", step_index=outcome.step_index, level="warning",
                message=f"Step {outcome.step_index}: still off-track after repair",
            )

    return result


def run_golden_phase(
    trajectory: ReferenceTrajectory, start_url: str, org_id: uuid.UUID, portal_profile_id: uuid.UUID,
    recording_id: uuid.UUID, authoring_run_id: uuid.UUID,
    *, pre_action_ms: int = 0, settle_after_nav_ms: int = 0,
) -> tuple[uuid.UUID, Path]:
    """Freezes the brain-verified trajectory into a deterministic .py
    script and records it as a draft GoldenScript — certifying (next
    phase) decides whether it becomes certified or partially_certified."""
    store = get_store()
    run_dir = store.new_run_dir(prefix="goldens")
    script_text = emit_golden_script(
        trajectory, start_url=start_url, pre_action_ms=pre_action_ms, settle_after_nav_ms=settle_after_nav_ms,
    )
    script_blob_ref = run_dir.write_text("golden.py", script_text)
    script_path = run_dir.path / "golden.py"

    with session_scope() as session:
        golden = GoldenScript(
            org_id=org_id,
            portal_profile_id=portal_profile_id,
            version=next_golden_script_version(session, portal_profile_id),
            script_blob_ref=script_blob_ref,
            status="draft",
            created_from_recording_id=recording_id,
        )
        session.add(golden)
        session.flush()
        golden_script_id = golden.id

    log_event(authoring_run_id, phase="golden", message=f"Emitted golden script v{golden.version}")
    return golden_script_id, script_path


def run_certify_phase(
    script_path: Path, authoring_run_id: uuid.UUID, *, runs: int = 3, run_number_offset: int = 0,
) -> tuple[int, int]:
    """Runs the frozen golden script headless, `runs` times from fresh
    processes, recording one CertificationRun row per attempt. Returns
    (passed, runs) — the orchestrator decides certified/partially_certified/
    repair-and-retry from this.

    `run_number_offset` lets a caller that invokes this more than once for
    the same authoring_run_id (e.g. the orchestrator's repair loop) avoid
    colliding with CertificationRun's UniqueConstraint("authoring_run_id",
    "run_number") — each retry passes a different offset so its rows get
    fresh run_numbers instead of re-using 1..runs."""
    passed = 0
    for i in range(1, runs + 1):
        run_number = run_number_offset + i
        ok, failure_output, _certify_result = certify_golden_script_once(script_path)

        failure_ref = None
        if not ok:
            evidence_path = script_path.parent / f"certify-{run_number}-failure.txt"
            evidence_path.write_text(failure_output or "")
            failure_ref = str(evidence_path.relative_to(settings.run_root))

        with session_scope() as session:
            session.add(CertificationRun(
                authoring_run_id=authoring_run_id, run_number=run_number,
                status="pass" if ok else "fail", failure_evidence_blob_ref=failure_ref,
            ))

        log_event(
            authoring_run_id, phase="certifying",
            message=f"Certification run {run_number}/{runs}: {'PASS' if ok else 'FAIL'}",
            level="info" if ok else "warning",
        )
        if ok:
            passed += 1

    return passed, runs
