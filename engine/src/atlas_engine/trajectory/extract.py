from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.capture.events import RecordedAction


def expected_effect_for_action(action: RecordedAction) -> str:
    # DOM-first: the recorded DOM change is the ONLY signal the brain's
    # silent-no-op gate reads (a URL-only change never gates), so a step that
    # changed both is "dom_changed" — the faithful port of effects.domChanged.
    if action.dom_before != action.dom_after:
        return "dom_changed"
    if action.url_before != action.url_after:
        return "url_changed"
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
