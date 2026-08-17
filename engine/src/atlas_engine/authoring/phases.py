import uuid
from pathlib import Path

from atlas_engine.authoring.events import log_event
from atlas_engine.brain.loop import BrainLoop
from atlas_engine.brain.models import ReferenceTrajectory
from atlas_engine.codegen.emit import emit_golden_script
from atlas_engine.config import settings
from atlas_engine.db import session_scope
from atlas_engine.db.models import CertificationRun, GoldenScript, PageExtraction
from atlas_engine.db.repo import next_golden_script_version
from atlas_engine.llm.client import LlmClient
from atlas_engine.recording_service import build_extraction_specs, list_page_extractions
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
    *, portal_domain: str, portal_name: str, confidence: float | None = None,
    success_signal: dict | None = None,
    pre_action_ms: int = 0, settle_after_nav_ms: int = 0,
) -> tuple[uuid.UUID, Path]:
    """Freezes the brain-verified trajectory into a deterministic .py
    script and records it as a draft GoldenScript — certifying (next
    phase) decides whether it becomes certified or partially_certified."""
    store = get_store()
    run_dir = store.new_run_dir(prefix="goldens")
    extraction_specs = build_extraction_specs(trajectory, list_page_extractions(recording_id))
    script_text = emit_golden_script(
        trajectory, start_url=start_url,
        portal_domain=portal_domain, portal_name=portal_name, confidence=confidence,
        success_signal=success_signal,
        pre_action_ms=pre_action_ms, settle_after_nav_ms=settle_after_nav_ms,
        extraction_specs=extraction_specs,
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
    script_path: Path, authoring_run_id: uuid.UUID, *, recording_id: uuid.UUID | None = None,
    runs: int = 3, run_number_offset: int = 0, already_persisted: bool = False,
) -> tuple[int, int, bool]:
    """Runs the frozen golden script headless, `runs` times from fresh
    processes, recording one CertificationRun row per attempt. Returns
    (passed, runs, persisted) — the orchestrator decides certified/
    partially_certified/repair-and-retry from (passed, runs), and threads
    `persisted` back as `already_persisted` on a repair-loop retry so the
    retry never persists a second set of PageExtraction/download rows (I4).
    On the FIRST successful run only (the remaining runs are a determinism
    check — no need to persist duplicate copies of the same deterministic
    output), persists any extractions into PageExtraction rows and uploads
    any downloaded files via get_store() under
    authoring/<authoring_run_id>/results/.

    `run_number_offset` lets a caller that invokes this more than once for
    the same authoring_run_id (e.g. the orchestrator's repair loop) avoid
    colliding with CertificationRun's UniqueConstraint("authoring_run_id",
    "run_number") — each retry passes a different offset so its rows get
    fresh run_numbers instead of re-using 1..runs."""
    passed = 0
    persisted = already_persisted
    for i in range(1, runs + 1):
        run_number = run_number_offset + i
        # Every standalone run gets a fresh diagnostics directory. Without
        # this, the emitted script's default `diagnostics/` path is shared by
        # all certification attempts and later failures overwrite the first
        # run's screenshot/HTML evidence.
        diagnostics_dir = script_path.parent / f"certify-{run_number}"
        ok, failure_output, certify_result = certify_golden_script_once(
            script_path, diagnostics_dir=diagnostics_dir,
        )

        failure_ref = None
        if not ok:
            # Goes through the store's own write_text (same as
            # _persist_certify_result's authoring/<run_id>/results/ files
            # below) rather than a raw Path.write_text +
            # relative_to(settings.run_root) — that assumed every run_dir
            # lives under settings.run_root, true for LocalRunStore but not
            # AzureBlobRunStore, whose local staging files live under a
            # separate temp directory and are never actually uploaded by a
            # bare filesystem write.
            failure_ref = get_store().write_text(
                f"authoring/{authoring_run_id}/certify-{run_number}-failure.txt", failure_output or "",
            )

        # C2: the CertificationRun row is recorded FIRST — a persistence
        # failure below must never leave a passing run without its row, and
        # the persist is try/except-wrapped so a broken store/file can't
        # crash the whole authoring run.
        with session_scope() as session:
            session.add(CertificationRun(
                authoring_run_id=authoring_run_id, run_number=run_number,
                status="pass" if ok else "fail", failure_evidence_blob_ref=failure_ref,
            ))

        if ok and not persisted and certify_result and recording_id is not None:
            try:
                _persist_certify_result(certify_result, authoring_run_id, recording_id)
                persisted = True
            except Exception as exc:
                log_event(
                    authoring_run_id, phase="certifying", level="warning",
                    message=f"Failed to persist certification results: {exc}",
                )

        log_event(
            authoring_run_id, phase="certifying",
            message=f"Certification run {run_number}/{runs}: {'PASS' if ok else 'FAIL'}",
            level="info" if ok else "warning",
        )
        if ok:
            passed += 1

    return passed, runs, persisted


def _persist_certify_result(certify_result: dict, authoring_run_id: uuid.UUID, recording_id: uuid.UUID) -> None:
    """Persists a certified golden script run's extractions (PageExtraction
    rows) and uploads its downloaded files to blob storage, under the
    authoring run's own results/ prefix."""
    store = get_store()
    for i, extraction in enumerate((certify_result.get("extractions") or {}).values()):
        data_blob_ref = store.write_json(f"authoring/{authoring_run_id}/results/extraction-{i}.json", extraction.get("data", {}))
        with session_scope() as session:
            session.add(PageExtraction(
                recording_id=recording_id,
                page_index=0,
                url=extraction.get("url", ""),
                data_blob_ref=data_blob_ref,
            ))
    for downloaded in certify_result.get("downloaded_files") or []:
        local_path = Path(downloaded["path"])
        if not local_path.exists():
            continue
        blob_ref = f"authoring/{authoring_run_id}/results/{downloaded['filename']}"
        store.write_bytes(blob_ref, local_path.read_bytes())
