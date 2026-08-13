from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.capture.events import RecordedAction


def expected_effect_for_action(action: RecordedAction) -> str:
    if action.url_before != action.url_after:
        return "url_changed"
    if action.dom_before != action.dom_after:
        return "dom_changed"
    return "none"


def extract_trajectory(actions: list[RecordedAction]) -> ReferenceTrajectory:
    """Deterministic, no LLM — a pure function of the recorded actions.
    Calling this twice on the same input must produce identical output."""
    steps = tuple(
        TrajectoryStep(
            index=action.index,
            kind=action.kind,
            selector_candidates=action.selector_candidates,
            value=action.value,
            expected_effect=expected_effect_for_action(action),
        )
        for action in actions
    )
    return ReferenceTrajectory(steps=steps)
