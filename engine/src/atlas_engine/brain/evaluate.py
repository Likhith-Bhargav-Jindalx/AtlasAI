import os
import re
from dataclasses import dataclass

from atlas_engine.brain.models import TrajectoryStep

_REDACTED_RE = re.compile(r"<(?:REDACTED|SECRET):([A-Za-z0-9_]+)>")


def _rematerialize(val: str | None) -> str:
    if not val:
        return ""
    m = _REDACTED_RE.fullmatch(val.strip())
    if m:
        env_var = m.group(1)
        return os.environ.get(env_var, "")
    return _REDACTED_RE.sub(lambda match: os.environ.get(match.group(1), ""), val)


# Gestures whose PURPOSE is to change the page — for these, "the reference
# changed the page, live changed nothing" is a silent no-op worth flagging.
# Cursor-movement presses and hovers legitimately change nothing, so they are
# deliberately excluded (demanding a DOM change there would loop forever).
# Mirrors the previous app's brain loop exactly.
_CHANGE_INTENT_KINDS = {"click", "dblclick", "hover_click", "dispatch_click", "goto"}


@dataclass(frozen=True)
class StepOutcome:
    step_index: int
    on_track: bool
    url_after: str
    dom_after: str
    resolved_selector: str
    dom_before: str = ""
    error: str | None = None
    input_value: str | None = None
    popup_opened: bool = False
    download_started: bool = False
    download_saved: str | None = None
    enters_iframe: bool = False
    resolved_identity: dict | None = None
    match_count: int | None = None
    used_locator_index: int | None = None
    shot_before: str | None = None
    shot_after: str | None = None
    elapsed_ms: int | None = None


def evaluate(
    step: TrajectoryStep,
    *,
    url_before: str,
    url_after: str,
    dom_before: str,
    dom_after: str,
    resolved_selector: str,
    error: str | None = None,
    input_value: str | None = None,
    popup_opened: bool = False,
    download_started: bool = False,
    download_saved: str | None = None,
    enters_iframe: bool = False,
    resolved_identity: dict | None = None,
    match_count: int | None = None,
    used_locator_index: int | None = None,
    shot_before: str | None = None,
    shot_after: str | None = None,
    elapsed_ms: int | None = None,
) -> StepOutcome:
    """Deterministic on-track check — a byte-for-byte port of the previous
    app's brain `evaluate`:
      - an error is always off-track;
      - a step recorded inside expect_popup/expect_download is off-track unless
        the popup/download actually happened;
      - a fill/type is on-track the moment its field HOLDS the target value
        (gesture-level ground truth, even if the page digest didn't change);
      - an action that enters an iframe is a blind spot — we can't see the
        frame's DOM from page.content(), so a no-error action there is accepted
        (a genuine failure surfaces at the next step);
      - a change-intent click/goto whose REFERENCE run changed the page
        (`expected_effect == "dom_changed"` proxies the recorded
        effects.domChanged) but whose live run changed nothing (no DOM, no URL,
        no popup) is a silent no-op and off-track.
    Anything else — including a recorded-only-URL change — passes: the previous
    app never REQUIRES a URL change, it only treats the recorded dom change as
    the reference for the silent-no-op gate."""
    actual_value = _rematerialize(step.value)
    if error:
        on_track = False
    elif step.expects_popup and not popup_opened:
        on_track = False
    elif step.expects_download and not download_started:
        on_track = False
    elif (
        step.kind in ("fill", "type")
        and step.value is not None
        and (input_value == step.value or (actual_value and input_value == actual_value))
    ):
        on_track = True
    elif enters_iframe:
        on_track = True
    else:
        url_changed = url_before != url_after
        recorded_dom_changed = step.expected_effect == "dom_changed"
        on_track = not (
            step.kind in _CHANGE_INTENT_KINDS
            and recorded_dom_changed
            and dom_before == dom_after
            and not url_changed
            and not popup_opened
        )

    return StepOutcome(
        step_index=step.index,
        on_track=on_track,
        url_after=url_after,
        dom_after=dom_after,
        resolved_selector=resolved_selector,
        dom_before=dom_before,
        error=error,
        input_value=input_value,
        popup_opened=popup_opened,
        download_started=download_started,
        download_saved=download_saved,
        enters_iframe=enters_iframe,
        resolved_identity=resolved_identity,
        match_count=match_count,
        used_locator_index=used_locator_index,
        shot_before=shot_before,
        shot_after=shot_after,
        elapsed_ms=elapsed_ms,
    )
