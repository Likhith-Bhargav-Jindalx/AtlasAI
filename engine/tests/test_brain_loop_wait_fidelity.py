"""Regression tests for run_step's page-load/frame-settle waiting matching
the previous app (core/src/brain/runnerEmit.ts::exec_entry/_settle_frames)
exactly. These three behaviors were found to have drifted from the
reference during the port:
  1. goto waited only for "domcontentloaded" instead of the reference's
     default "load".
  2. _settle_frames was gated on frame count (> 1) instead of being called
     unconditionally for every non-navigation step.
  3. _settle_frames itself required only 2 stable polls at 300ms instead of
     the reference's 3 stable polls at 500ms (settles too eagerly).
"""

from unittest.mock import MagicMock

from playwright.sync_api import sync_playwright

from atlas_engine.brain.loop import _settle_frames, run_step
from atlas_engine.brain.models import TrajectoryStep


def test_goto_waits_for_load_not_just_domcontentloaded(fixture_portal_url):
    """Matches runnerEmit.ts's exec_entry: `pg.goto(url, timeout=ceiling)` —
    no wait_until override, so Playwright's default "load" applies. The
    ported code must not narrow this to "domcontentloaded"."""
    step = TrajectoryStep(
        index=0, kind="goto",
        selector_candidates=[{"selector": f"{fixture_portal_url}/dashboard.html", "rank": 0}],
        value=None, expected_effect="url_changed",
    )
    fake_page = MagicMock()
    fake_page.context.pages = [fake_page]
    fake_page.url = f"{fixture_portal_url}/login.html"
    fake_page.content.return_value = "<html></html>"

    run_step(fake_page, step)

    fake_page.goto.assert_called_once()
    _args, kwargs = fake_page.goto.call_args
    assert kwargs.get("wait_until") == "load"


def test_settle_frames_requires_three_stable_polls_like_the_reference():
    """runnerEmit.ts's _settle_frames returns once the frame-URL signature
    has been unchanged for 3 consecutive 500ms polls. The ported version
    must use the same thresholds, not 2 polls at 300ms."""
    fake_page = MagicMock()
    call_count = {"n": 0}

    class _Frame:
        def __init__(self, url):
            self.url = url

    def frames_property():
        # Same single frame every time — signature never changes, so the
        # function must poll exactly 3 times (to reach stable == 3) before
        # returning, regardless of how many times `frames` is read.
        call_count["n"] += 1
        return [_Frame("https://example.com/")]

    type(fake_page).frames = property(lambda self: frames_property())

    wait_calls = []
    fake_page.wait_for_timeout.side_effect = lambda ms: wait_calls.append(ms)

    _settle_frames(fake_page, timeout_ms=5000)

    assert wait_calls == [500, 500, 500]


def test_settle_frames_is_called_unconditionally_even_with_a_single_frame(monkeypatch, fixture_portal_url):
    """runnerEmit.ts calls _settle_frames() before resolving on every
    non-goto/close/click_xy step — never gated on frame count. A portal
    whose reloading iframe hasn't been counted yet is exactly the case this
    protects; gating on len(frames) > 1 can skip it."""
    calls = []
    import atlas_engine.brain.loop as loop_module

    real_settle_frames = loop_module._settle_frames

    def spy(pg, timeout_ms=3000):
        calls.append(timeout_ms)
        return real_settle_frames(pg, timeout_ms=min(timeout_ms, 200))

    monkeypatch.setattr(loop_module, "_settle_frames", spy)

    step = TrajectoryStep(
        index=0, kind="click",
        selector_candidates=[{"selector": "#login-btn", "rank": 0}],
        value=None, expected_effect="none",
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")
        assert len(page.frames) == 1  # the exact case the old gate would have skipped
        run_step(page, step)
        browser.close()

    assert len(calls) == 1
