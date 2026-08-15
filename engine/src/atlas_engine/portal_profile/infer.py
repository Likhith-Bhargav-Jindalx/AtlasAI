from dataclasses import dataclass
from urllib.parse import urlparse

from atlas_engine.capture.events import RecordedAction


@dataclass(frozen=True)
class InferredProfile:
    domain: str
    login_url: str | None
    success_signal: dict | None
    confidence: float


def _is_password_action(action: RecordedAction) -> bool:
    return any("password" in c["selector"].lower() for c in action.selector_candidates)


def infer_portal_profile(start_url: str, actions: list[RecordedAction]) -> InferredProfile:
    domain = urlparse(start_url).netloc

    # login_url is always where the recording BEGAN — the orchestrator's one
    # entry navigation before replay/brain starts. A login flow can span
    # several real pages (username page -> password page -> dashboard, e.g.
    # eCW); using a LATER page's URL (e.g. the password action's url_before)
    # as the replay entry point skips the flow's own server-side
    # session/step validation, bouncing back to the first page.
    login_url = start_url

    login_action = next((a for a in actions if _is_password_action(a)), None)

    success_signal = None
    if login_action is not None:
        later_actions = [a for a in actions if a.index > login_action.index]
        navigated = next((a for a in later_actions if a.url_before != a.url_after), None)
        if navigated is not None:
            success_signal = {"url_contains": urlparse(navigated.url_after).path}

    if login_action is not None and success_signal is not None:
        confidence = 1.0
    elif login_action is not None or success_signal is not None:
        confidence = 0.5
    else:
        confidence = 0.5 if login_url else 0.0

    return InferredProfile(domain=domain, login_url=login_url, success_signal=success_signal, confidence=confidence)
