import uuid
from pathlib import Path

from atlas_engine.authoring.events import log_event
from atlas_engine.brain.evaluate import StepOutcome
from atlas_engine.brain.loop import BrainLoop
from atlas_engine.brain.models import ReferenceTrajectory
from atlas_engine.codegen.emit import emit_golden_script
from atlas_engine.config import settings
from atlas_engine.db import session_scope
from atlas_engine.db.models import CertificationRun, GoldenScript
from atlas_engine.db.repo import next_golden_script_version
from atlas_engine.llm.client import LlmClient
from atlas_engine.storage import LocalRunStore
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
) -> list[StepOutcome]:
    """Headed, LLM-driven supervised replay — BrainLoop already does
    perceive/act/evaluate/repair internally; this just runs it and turns
    its returned per-step outcomes into the run's event log. No developer
    fallback here: a step that's still off-track after BrainLoop's own
    repair attempt stays off-track, and the caller (the orchestrator)
    hard-fails the run."""
    outcomes = BrainLoop(page, llm=llm).run(trajectory)

    for step, outcome in zip(trajectory.steps, outcomes):
        if outcome.on_track:
            log_event(
                authoring_run_id, phase="brain", step_index=step.index,
                message=f"Step {step.index}: {step.kind} - OK",
            )
        else:
            log_event(
                authoring_run_id, phase="brain", step_index=step.index, level="warning",
                message=f"Step {step.index}: {step.kind} - still off-track after repair",
            )

    return outcomes


def run_golden_phase(
    trajectory: ReferenceTrajectory, start_url: str, org_id: uuid.UUID, portal_profile_id: uuid.UUID,
    recording_id: uuid.UUID, authoring_run_id: uuid.UUID,
) -> tuple[uuid.UUID, Path]:
    """Freezes the brain-verified trajectory into a deterministic .py
    script and records it as a draft GoldenScript — certifying (next
    phase) decides whether it becomes certified or partially_certified."""
    store = LocalRunStore(root=settings.ensure_run_root())
    run_dir = store.new_run_dir(prefix="goldens")
    script_text = emit_golden_script(trajectory, start_url=start_url)
    script_path = run_dir.path / "golden.py"
    script_path.write_text(script_text)

    with session_scope() as session:
        golden = GoldenScript(
            org_id=org_id,
            portal_profile_id=portal_profile_id,
            version=next_golden_script_version(session, portal_profile_id),
            script_blob_ref=str(script_path),
            status="draft",
            created_from_recording_id=recording_id,
        )
        session.add(golden)
        session.flush()
        golden_script_id = golden.id

    log_event(authoring_run_id, phase="golden", message=f"Emitted golden script v{golden.version}")
    return golden_script_id, script_path


def run_certify_phase(script_path: Path, authoring_run_id: uuid.UUID, *, runs: int = 3) -> tuple[int, int]:
    """Runs the frozen golden script headless, `runs` times from fresh
    processes, recording one CertificationRun row per attempt. Returns
    (passed, runs) — the orchestrator decides certified/partially_certified/
    repair-and-retry from this."""
    passed = 0
    for run_number in range(1, runs + 1):
        ok, failure_output = certify_golden_script_once(script_path)

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
