from dataclasses import dataclass

from atlas_engine.brain.models import TrajectoryStep


@dataclass(frozen=True)
class StepOutcome:
    step_index: int
    on_track: bool
    url_after: str
    dom_after: str
    resolved_selector: str


def evaluate(
    step: TrajectoryStep,
    *,
    url_before: str,
    url_after: str,
    dom_before: str,
    dom_after: str,
    resolved_selector: str,
) -> StepOutcome:
    """Deterministic — compares the step's expected effect (recorded at
    capture time) against what actually happened live. This is the check
    that decides whether the brain needs to think at all."""
    if step.expected_effect == "url_changed":
        on_track = url_before != url_after
    elif step.expected_effect == "dom_changed":
        on_track = dom_before != dom_after
    else:
        on_track = True

    return StepOutcome(
        step_index=step.index,
        on_track=on_track,
        url_after=url_after,
        dom_after=dom_after,
        resolved_selector=resolved_selector,
    )
