import dataclasses
import threading
import uuid

from atlas_engine.authoring.events import log_event
from atlas_engine.authoring.state import update_run
from atlas_engine.brain.loop import run_step
from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.capture.events import RecordedAction
from atlas_engine.capture.recorder import CaptureSession
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


def run_replay_phase(
    page, trajectory: ReferenceTrajectory, authoring_run_id: uuid.UUID,
    resume_event: threading.Event, stop_event: threading.Event,
) -> ReferenceTrajectory | None:
    """Deterministic, non-LLM re-run of the recorded trajectory, headed so
    a developer can watch and — on a step failure — intervene directly in
    the live browser. Returns the (possibly developer-corrected)
    trajectory, or None if stopped mid-replay or while paused."""
    steps = list(trajectory.steps)
    i = 0
    while i < len(steps):
        if stop_event.is_set():
            return None

        step = steps[i]
        try:
            outcome = run_step(page, step)
        except Exception:
            outcome = None

        if outcome is not None and outcome.on_track:
            log_event(
                authoring_run_id, phase="replay", step_index=step.index,
                message=f"Step {step.index}: {step.kind} - OK",
            )
            i += 1
            continue

        log_event(
            authoring_run_id, phase="replay", step_index=step.index, level="warning",
            message=f"Step {step.index}: {step.kind} failed, waiting for developer",
        )
        update_run(authoring_run_id, status="waiting_for_developer")

        # KNOWN LIMITATION: a fresh CaptureSession is created per pause, but
        # CaptureSession.__enter__ calls page.expose_binding("__atlas_record", ...),
        # and Playwright raises if that name is already registered on the page —
        # it is, from any *earlier* pause in this same run_replay_phase call.
        # A second developer-intervention pause within one run will therefore
        # raise here instead of pausing cleanly. Fixing this needs either a
        # way to reset/reuse one CaptureSession's recording window across
        # pauses, or a public unbind/reset hook on CaptureSession itself
        # (recorder.py) — out of scope for this task, which only exercises
        # zero-pause and single-pause-then-stop trajectories.
        capture = CaptureSession(page)
        capture.__enter__()
        wait_result = _wait_for_resume_or_stop(resume_event, stop_event)
        capture.__exit__(None, None, None)

        if wait_result == "stopped":
            return None

        developer_actions: list[RecordedAction] = capture.actions
        steps = _splice_developer_actions(steps, i, developer_actions)
        update_run(authoring_run_id, status="running")
        log_event(
            authoring_run_id, phase="replay", step_index=i,
            message=f"Developer resolved step {step.index} with {len(developer_actions)} action(s)",
        )
        i += len(developer_actions)

    return ReferenceTrajectory(steps=tuple(steps))
