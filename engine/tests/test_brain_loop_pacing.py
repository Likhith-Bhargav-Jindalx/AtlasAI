"""Tests for the opt-in per-portal pacing feature (preActionMs/settleAfterNavMs),
ported from the previous app's portal.pacing config (core/src/generate/py/pyEmit.ts
_pace()/settleMs, core/src/core/types.ts's pacing field). Absent/zero means no
pacing — byte-for-byte the same behavior as before this feature existed.
"""

from unittest.mock import MagicMock

from atlas_engine.brain.loop import BrainLoop, run_step
from atlas_engine.brain.models import TrajectoryStep


def _click_step() -> TrajectoryStep:
    return TrajectoryStep(
        index=0, kind="click",
        selector_candidates=[{"selector": "#login-btn", "rank": 0}],
        value=None, expected_effect="none",
    )


def _goto_step(fixture_portal_url: str) -> TrajectoryStep:
    return TrajectoryStep(
        index=0, kind="goto",
        selector_candidates=[{"selector": f"{fixture_portal_url}/dashboard.html", "rank": 0}],
        value=None, expected_effect="url_changed",
    )


def _fake_page(url: str = "https://example.com/") -> MagicMock:
    page = MagicMock()
    page.context.pages = [page]
    page.url = url
    page.content.return_value = "<html></html>"
    return page


def test_run_step_pauses_pre_action_ms_before_acting_when_configured():
    page = _fake_page()

    run_step(page, _click_step(), pre_action_ms=1234)

    page.wait_for_timeout.assert_any_call(1234)


def test_run_step_does_not_pause_when_pre_action_ms_not_configured():
    page = _fake_page()

    run_step(page, _click_step())

    assert 1234 not in [c.args[0] for c in page.wait_for_timeout.call_args_list]


def test_act_settles_networkidle_after_goto_when_settle_after_nav_ms_configured(fixture_portal_url):
    page = _fake_page(url=f"{fixture_portal_url}/login.html")

    run_step(page, _goto_step(fixture_portal_url), settle_after_nav_ms=9999)

    page.wait_for_load_state.assert_any_call("networkidle", timeout=9999)


def test_act_skips_networkidle_settle_when_settle_after_nav_ms_not_configured(fixture_portal_url):
    page = _fake_page(url=f"{fixture_portal_url}/login.html")

    run_step(page, _goto_step(fixture_portal_url))

    calls = [c for c in page.wait_for_load_state.call_args_list if c.args and c.args[0] == "networkidle"]
    assert calls == []


def test_networkidle_settle_timeout_is_swallowed(fixture_portal_url):
    """A slow/never-idle portal must not fail the step — the settle wait is
    a best-effort pause, exactly like the previous app's try/except."""
    page = _fake_page(url=f"{fixture_portal_url}/login.html")
    page.wait_for_load_state.side_effect = TimeoutError("never idle")

    outcome = run_step(page, _goto_step(fixture_portal_url), settle_after_nav_ms=100)

    assert outcome.error is None


def test_brain_loop_threads_pacing_into_run_step(monkeypatch):
    calls = []
    import atlas_engine.brain.loop as loop_module

    real_run_step = loop_module.run_step

    def spy(page, step, **kwargs):
        calls.append(kwargs)
        return real_run_step(page, step, **kwargs)

    monkeypatch.setattr(loop_module, "run_step", spy)

    page = _fake_page()
    BrainLoop(page, pacing_pre_action_ms=555, pacing_settle_after_nav_ms=7777)._execute(_click_step(), None)

    assert calls[0]["pre_action_ms"] == 555
    assert calls[0]["settle_after_nav_ms"] == 7777


def test_brain_loop_defaults_to_no_pacing():
    page = _fake_page()

    BrainLoop(page)._execute(_click_step(), None)

    assert 1234 not in [c.args[0] for c in page.wait_for_timeout.call_args_list]
