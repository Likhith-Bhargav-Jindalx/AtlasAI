from collections.abc import Callable

from atlas_engine.brain.evaluate import StepOutcome, evaluate
from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.brain.repair import think
from atlas_engine.llm.client import LlmClient

# The recorded selector is expected to match immediately on replay; a short
# timeout here means a genuinely stale selector fails fast into repair
# instead of waiting out Playwright's 30s default on every off-track step.
_INITIAL_ATTEMPT_TIMEOUT_MS = 3000


def _first_selector(step: TrajectoryStep) -> str:
    ranked = sorted(step.selector_candidates, key=lambda c: c["rank"])
    return ranked[0]["selector"]


def _act(page, selector: str, step: TrajectoryStep, *, force: bool = False, timeout_ms: float | None = None) -> None:
    if step.kind == "click":
        page.click(selector, force=force, timeout=timeout_ms)
    elif step.kind == "fill":
        page.fill(selector, step.value or "", timeout=timeout_ms)
    else:
        raise ValueError(f"Unsupported step kind: {step.kind}")


def run_step(
    page,
    step: TrajectoryStep,
    *,
    selector: str | None = None,
    force: bool = False,
    timeout_ms: float | None = _INITIAL_ATTEMPT_TIMEOUT_MS,
) -> StepOutcome:
    url_before = page.url
    dom_before = page.content()

    _act(page, selector or _first_selector(step), step, force=force, timeout_ms=timeout_ms)

    page.wait_for_load_state("domcontentloaded")
    url_after = page.url
    dom_after = page.content()

    return evaluate(step, url_before=url_before, url_after=url_after, dom_before=dom_before, dom_after=dom_after)


class BrainLoop:
    """perceive -> act -> evaluate -> think only if off-track.

    Without `llm`, an off-track step is reported via `on_off_track` and
    left off-track (Task 8 behavior, still used by tests that only exercise
    the happy path). With `llm`, an off-track step triggers exactly one
    bounded repair attempt via `think()` before moving on.
    """

    def __init__(
        self,
        page,
        on_off_track: Callable[[TrajectoryStep, StepOutcome], None] | None = None,
        llm: LlmClient | None = None,
    ):
        self.page = page
        self.on_off_track = on_off_track
        self.llm = llm

    def run(self, trajectory: ReferenceTrajectory) -> list[StepOutcome]:
        outcomes = []
        for step in trajectory.steps:
            try:
                outcome = run_step(self.page, step)
            except Exception:
                # The recorded selector didn't even resolve — treat as
                # maximally off-track and let repair take over below.
                outcome = StepOutcome(step_index=step.index, on_track=False, url_after=self.page.url, dom_after=self.page.content())

            if not outcome.on_track:
                if self.on_off_track is not None:
                    self.on_off_track(step, outcome)
                if self.llm is not None:
                    outcome = self._repair(step, outcome)

            outcomes.append(outcome)
        return outcomes

    def _repair(self, step: TrajectoryStep, outcome: StepOutcome) -> StepOutcome:
        repair = think(self.llm, step, self.page, outcome)
        if repair.action == "replace_selector":
            return run_step(self.page, step, selector=repair.selector, timeout_ms=None)
        if repair.action == "force_click":
            return run_step(self.page, step, force=True, timeout_ms=None)
        return outcome  # wait_longer / give_up: nothing more Task 9 attempts automatically
