import uuid

from atlas_engine.authoring.events import log_event
from atlas_engine.brain.evaluate import StepOutcome
from atlas_engine.brain.loop import BrainLoop
from atlas_engine.brain.models import ReferenceTrajectory
from atlas_engine.llm.client import LlmClient


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
