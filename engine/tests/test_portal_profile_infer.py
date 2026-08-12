from atlas_engine.capture.events import RecordedAction
from atlas_engine.portal_profile.infer import infer_portal_profile


def _action(index, kind, value, selector, url_before, url_after):
    return RecordedAction(
        index=index, kind=kind, selector_candidates=[{"selector": selector, "rank": 0}],
        value=value, url_before=url_before, url_after=url_after, dom_before="", dom_after="",
    )


def test_infer_portal_profile_finds_login_url_and_success_signal():
    actions = [
        _action(0, "fill", "demo", "#username", "https://portal.example.com/login", "https://portal.example.com/login"),
        _action(1, "fill", "demo-pass", "#password", "https://portal.example.com/login", "https://portal.example.com/login"),
        _action(2, "click", None, "#login-btn", "https://portal.example.com/login", "https://portal.example.com/dashboard"),
    ]

    profile = infer_portal_profile("https://portal.example.com/login", actions)

    assert profile.domain == "portal.example.com"
    assert profile.login_url == "https://portal.example.com/login"
    assert profile.success_signal == {"url_contains": "/dashboard"}
    assert profile.confidence == 1.0


def test_infer_portal_profile_falls_back_when_no_password_field_seen():
    actions = [_action(0, "click", None, "#continue-btn", "https://portal.example.com/start", "https://portal.example.com/start")]

    profile = infer_portal_profile("https://portal.example.com/start", actions)

    assert profile.login_url == "https://portal.example.com/start"
    assert profile.success_signal is None
    assert profile.confidence == 0.5
