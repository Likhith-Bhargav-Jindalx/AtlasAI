from dataclasses import dataclass
from typing import Literal

from atlas_engine.brain.evaluate import StepOutcome
from atlas_engine.brain.models import TrajectoryStep
from atlas_engine.llm.client import LlmClient
from atlas_engine.llm.sanitize import sanitize_dom

_VALID_ACTIONS = {"replace_selector", "force_click", "wait_longer", "give_up"}


@dataclass(frozen=True)
class RepairAction:
    action: Literal["replace_selector", "force_click", "wait_longer", "give_up"]
    selector: str | None = None


def _build_prompt(step: TrajectoryStep, sanitized_dom: str) -> str:
    original_selector = step.selector_candidates[0]["selector"] if step.selector_candidates else None
    return (
        "A recorded Playwright step is off-track. The step was a "
        f"'{step.kind}' using selector '{original_selector}', expecting effect "
        f"'{step.expected_effect}'. Here is the current, sanitized page DOM "
        f"(text content stripped, structure and attributes intact):\n\n{sanitized_dom}\n\n"
        "Respond in JSON with one of: "
        '{"action": "replace_selector", "selector": "<css selector present in the DOM above>"}, '
        '{"action": "force_click"}, {"action": "wait_longer"}, or {"action": "give_up"}.'
    )


def think(llm: LlmClient, step: TrajectoryStep, page, outcome: StepOutcome) -> RepairAction:
    """Bounded, DOM-validated repair proposal — the only place in the
    engine an LLM call happens. Any proposed selector that doesn't
    actually exist on the live page is coerced to give_up rather than
    trusted blindly."""
    sanitized_dom = sanitize_dom(page.content())
    prompt = _build_prompt(step, sanitized_dom)
    response = llm.complete(prompt, json_mode=True, temperature=0.0)

    action = response.get("action")
    if action not in _VALID_ACTIONS:
        return RepairAction(action="give_up")

    if action == "replace_selector":
        selector = response.get("selector")
        if not selector or page.locator(selector).count() == 0:
            return RepairAction(action="give_up")
        return RepairAction(action="replace_selector", selector=selector)

    return RepairAction(action=action)
