from dataclasses import dataclass
from pathlib import Path

from atlas_engine.brain.blockers import detect_blockers
from atlas_engine.brain.evaluate import StepOutcome
from atlas_engine.brain.models import TrajectoryStep
from atlas_engine.llm.client import LlmClient
from atlas_engine.llm.sanitize import sanitize_dom

# The full correction vocabulary of the old app's brain. Every proposal is a
# STRICT-JSON action; the loop validates (resolve-count) before applying and
# re-feeds rejected proposals to the next think round.
_VALID_ACTIONS = {
    "replace_selector",
    "set_gesture",
    "force_click",
    "wait_longer",
    "set_value",
    "insert_step",
    "remove_step",
    "dismiss",
    "click_coordinates",
    "reexec_prev",
    "declare_blocker",
    "give_up",
}

_GESTURES = ["click", "hover_click", "hover", "dblclick", "dispatch_click", "type", "fill", "press", "check", "select"]


@dataclass(frozen=True)
class RepairAction:
    action: str
    selector: dict | str | None = None
    gesture: str | None = None
    key: str | None = None
    value: str | None = None
    label: str | None = None
    reason: str | None = None
    ceiling_ms: int | None = None
    x: float | None = None
    y: float | None = None
    kind: str | None = None
    evidence: str | None = None


def _read_shot(path: str | None) -> bytes | None:
    if not path:
        return None
    try:
        return Path(path).read_bytes()
    except OSError:
        return None


def _build_prompt(
    step: TrajectoryStep, sanitized_dom: str, outcome: StepOutcome, attempts: list[str], goal: str | None = None,
) -> str:
    dom_changed = bool(outcome.dom_before) and outcome.dom_before != outcome.dom_after
    gestures = "|".join(_GESTURES)
    parts = [
        "You supervise a Playwright replay of a RECORDED human flow on a live portal. A step is off-track.",
        "Ground every decision in (a) what the human's recording did here and (b) the LIVE page evidence.",
        "Never invent selectors — anything you propose must be visible in the DOM snapshot. Respond with ONE",
        "STRICT-JSON action:",
        '- {"action":"replace_selector","selector":{"kind":"css|role|text|testid|label|title|placeholder","value":"...","role":"<for kind=role>"}}  the recorded selector no longer matches; point at the SAME element the recording targeted.',
        f'- {{"action":"set_gesture","gesture":"{gestures}","key":"<press only>"}}  right element, wrong gesture (hover-reveal -> hover_click; single click only selects -> dblclick; SPA ignores clicks -> dispatch_click; autocomplete needs keystrokes -> type).',
        '- {"action":"force_click"}  element resolves but is reported hidden forever, yet IS the real control.',
        '- {"action":"wait_longer","ceilingMs":120000}  nothing wrong - content still loading.',
        '- {"action":"insert_step","gesture":"click|hover","selector":{...},"label":"..."}  a required reveal/opener the replay is missing (menu opener, tab, expander). Runs BEFORE retrying this step.',
        '- {"action":"remove_step","reason":"..."}  the recorded action was a fumble/no-op and blocks progress.',
        '- {"action":"dismiss","selector":{...}}  an UNEXPECTED modal/interstitial (not in the recording) blocks the page; click its close/accept control, then retry.',
        '- {"action":"set_value","value":"..."}  a fill value is wrong for this run.',
        '- {"action":"click_coordinates","x":123,"y":456}  LAST resort (canvas/no-DOM widget): click the target seen in the live screenshot.',
        '- {"action":"reexec_prev"}  the PREVIOUS step silently failed; re-run it, then this one.',
        '- {"action":"declare_blocker","kind":"captcha|mfa|antibot|other","evidence":"..."}  a human-only gate.',
        '- {"action":"give_up","reason":"..."}  nothing applicable.',
        "Prefer the smallest correct fix. The recording is ground truth for INTENT; the live page for REALITY.",
        "",
    ]
    if goal:
        parts.append(f"GOAL: {goal}")
    parts += [
        "",
        f"STEP {step.index} — recorded human action: gesture={step.kind}"
        + (f" value={step.value!r}" if step.value is not None else "")
        + (f" key={step.key!r}" if step.key else ""),
        f"Recorded locators (best-first): {step.selector_candidates}",
        f"Recorded element identity: {step.identity}",
        f"Recorded expected effects: expected_effect={step.expected_effect} expectsPopup={step.expects_popup} expectsDownload={step.expects_download}",
        "",
        "LIVE evidence this attempt: "
        + (f"error={outcome.error}" if outcome.error else "no error")
        + f" matchCount={outcome.match_count} elapsedMs={outcome.elapsed_ms} urlAfter={outcome.url_after!r} "
        + f"domChanged={dom_changed} popupOpened={outcome.popup_opened} downloadStarted={outcome.download_started} inputValue={outcome.input_value!r}",
    ]
    if attempts:
        parts += ["", "Already tried on THIS step (do not repeat):"] + [f"- {a}" for a in attempts]
    parts += [
        "",
        "Image 1 = LIVE page now."
        + (" Image 2 = REFERENCE (how this step looked right after succeeding in the recording)."
           if step.ref_shot_after else ""),
        "",
        "Sanitized LIVE DOM (structure/attrs only):",
        sanitized_dom[:60000],
    ]
    return "\n".join(parts)


def think(
    llm: LlmClient, step: TrajectoryStep, page, outcome: StepOutcome, *, attempts: list[str] | None = None,
    goal: str | None = None,
) -> RepairAction:
    """Bounded repair proposal — the only place in the engine an LLM call
    happens. Multimodal: the live screenshot and (when captured) the reference
    screenshot ground the model in both the recording and the live page. This
    is a faithful port of the old app's brain `think`:
      - a human-only gate (captcha/mfa/antibot/no-dom) is declared FIRST,
        before any LLM call;
      - non-JSON from the model is a give_up, never a crash;
      - selector validation does NOT happen here — the loop resolve-counts each
        proposed selector and re-feeds a REJECTED proposal to the next round
        (exactly like the old app).
    """
    dom_html = page.content()
    blocker = detect_blockers(dom_html)
    if blocker:
        return RepairAction(action="declare_blocker", kind=blocker.kind, evidence=blocker.detail)

    sanitized_dom = sanitize_dom(dom_html)
    prompt = _build_prompt(step, sanitized_dom, outcome, list(attempts or []), goal)

    live_shot = _read_shot(outcome.shot_after)
    ref_shot = _read_shot(step.ref_shot_after)
    images = []
    if live_shot:
        images.append(live_shot)
    if ref_shot:
        images.append(ref_shot)

    try:
        response = llm.complete(prompt, images=images or None, json_mode=True, temperature=0.0)
    except Exception:
        return RepairAction(action="give_up", reason="brain returned non-JSON")

    action = response.get("action")
    return RepairAction(
        action=action or "give_up",
        selector=response.get("selector"),
        gesture=response.get("gesture"),
        key=response.get("key"),
        value=response.get("value"),
        label=response.get("label"),
        reason=response.get("reason"),
        ceiling_ms=response.get("ceilingMs"),
        x=response.get("x"),
        y=response.get("y"),
        kind=response.get("kind"),
        evidence=response.get("evidence"),
    )