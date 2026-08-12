from collections.abc import Callable

from atlas_engine.brain.evaluate import StepOutcome, evaluate
from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep


def _first_selector(step: TrajectoryStep) -> str:
    ranked = sorted(step.selector_candidates, key=lambda c: c["rank"])
    return ranked[0]["selector"]


def run_step(page, step: TrajectoryStep) -> StepOutcome:
    url_before = page.url
    dom_before = page.content()

    selector = _first_selector(step)
    if step.kind == "click":
        page.click(selector)
    elif step.kind == "fill":
        page.fill(selector, step.value or "")
    else:
        raise ValueError(f"Unsupported step kind: {step.kind}")

    page.wait_for_load_state("domcontentloaded")
    url_after = page.url
    dom_after = page.content()

    return evaluate(step, url_before=url_before, url_after=url_after, dom_before=dom_before, dom_after=dom_after)


class BrainLoop:
    """perceive -> act -> evaluate -> (think only if off-track).

    `on_off_track`, when a step evaluates as off-track, is called with the
    step and its outcome so a caller can attempt a repair. Task 8 leaves
    this a plain hook; Task 9 wires it to the LLM repair loop.
    """

    def __init__(self, page, on_off_track: Callable[[TrajectoryStep, StepOutcome], None] | None = None):
        self.page = page
        self.on_off_track = on_off_track

    def run(self, trajectory: ReferenceTrajectory) -> list[StepOutcome]:
        outcomes = []
        for step in trajectory.steps:
            outcome = run_step(self.page, step)
            outcomes.append(outcome)
            if not outcome.on_track and self.on_off_track is not None:
                self.on_off_track(step, outcome)
        return outcomes
