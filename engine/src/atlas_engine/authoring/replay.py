import dataclasses
import uuid
from pathlib import Path

from atlas_engine.authoring.events import log_event
from atlas_engine.brain.loop import run_step
from atlas_engine.brain.models import ReferenceTrajectory
from atlas_engine.config import settings


def run_replay_phase(
    page, trajectory: ReferenceTrajectory, authoring_run_id: uuid.UUID,
    resume_event, stop_event,
) -> ReferenceTrajectory | None:
    """INSTRUMENTED reference replay — the engine's equivalent of the old
    app's transformForReplay. Re-runs the recorded trajectory headed so the
    operator can watch, capturing per-step reference evidence — pre/post
    screenshots and the resolved element's identity — that the brain later
    thinks over. A failed step does NOT pause for the developer: it is logged,
    its evidence is still captured, and the replay CONTINUES — the brain phase
    repairs off-track steps automatically, exactly like the previous app.
    Returns the enriched trajectory, or None if the run was stopped
    mid-replay."""
    ref_dir = Path(settings.ensure_run_root()) / "authoring" / str(authoring_run_id) / "ref"
    enriched: list = []
    for step in trajectory.steps:
        if stop_event.is_set():
            return None

        if step.kind == "close":
            continue  # page closes are teardown, not flow — never carried forward

        try:
            outcome = run_step(page, step, shot_dir=str(ref_dir))
        except Exception as exc:  # keep the step — the brain repairs it
            log_event(
                authoring_run_id, phase="replay", step_index=step.index, level="warning",
                message=f"Step {step.index}: {step.kind} failed ({str(exc)[:200]}) — will be repaired by the brain",
            )
            enriched.append(step)
            continue

        if outcome.on_track:
            log_event(
                authoring_run_id, phase="replay", step_index=step.index,
                message=f"Step {step.index}: {step.kind} - OK",
            )
        else:
            log_event(
                authoring_run_id, phase="replay", step_index=step.index, level="warning",
                message=f"Step {step.index}: {step.kind} off-track — will be repaired by the brain",
            )

        enriched.append(
            dataclasses.replace(
                step,
                ref_shot_before=outcome.shot_before or step.ref_shot_before,
                ref_shot_after=outcome.shot_after or step.ref_shot_after,
                identity=outcome.resolved_identity or step.identity,
                expected_effect=_reference_effect(step, outcome),
                wait_ceiling_ms=max(30000, (outcome.elapsed_ms or 0) * 10),
            )
        )

    return ReferenceTrajectory(steps=tuple(enriched))


# Change-intent gestures whose PURPOSE is to change the page — for these the
# reference's observed DOM change becomes the brain's silent-no-op gate signal
# (`expected_effect == "dom_changed"`). Mirrors the recorded effects.domChanged.
_CHANGE_INTENT_KINDS = {"click", "dblclick", "hover_click", "dispatch_click", "goto"}


def _reference_effect(step, outcome) -> str:
    """What the REFERENCE run actually did — the recorded-effects proxy the
    brain's silent-no-op gate reads. A change-intent step (click/nav) that
    changed the live DOM is `dom_changed` (the faithful port of
    effects.domChanged); everything else keeps the recorded expected_effect
    (`url_changed`/`none` — url-only changes never gate)."""
    if step.kind in _CHANGE_INTENT_KINDS and bool(outcome.dom_before) and outcome.dom_before != outcome.dom_after:
        return "dom_changed"
    return step.expected_effect
