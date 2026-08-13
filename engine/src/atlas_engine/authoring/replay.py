import dataclasses
import threading

from atlas_engine.brain.models import TrajectoryStep
from atlas_engine.capture.events import RecordedAction
from atlas_engine.trajectory.extract import expected_effect_for_action


def _wait_for_resume_or_stop(
    resume_event: threading.Event, stop_event: threading.Event, *, poll_interval: float = 0.5,
) -> str:
    """Blocks until either the developer clicks Resume or the run is
    stopped, whichever happens first. Polling (rather than a composite
    wait) because Python's threading has no built-in "wait for any of
    several events" primitive — poll_interval bounds how quickly a stop
    takes effect while paused."""
    while True:
        if stop_event.wait(timeout=poll_interval):
            return "stopped"
        if resume_event.is_set():
            resume_event.clear()
            return "resumed"


def _splice_developer_actions(
    steps: list[TrajectoryStep], failed_index: int, developer_actions: list[RecordedAction],
) -> list[TrajectoryStep]:
    """Replaces the step at failed_index with whatever the developer did
    instead — one action or several — and re-indexes every step in the
    result sequentially from 0, since the original per-step indices no
    longer describe a contiguous position once a step has been replaced
    by a different number of actions."""
    before = steps[:failed_index]
    after = steps[failed_index + 1:]
    converted = [
        TrajectoryStep(
            index=0,
            kind=action.kind,
            selector_candidates=action.selector_candidates,
            value=action.value,
            expected_effect=expected_effect_for_action(action),
        )
        for action in developer_actions
    ]
    combined = list(before) + converted + list(after)
    return [dataclasses.replace(step, index=i) for i, step in enumerate(combined)]
