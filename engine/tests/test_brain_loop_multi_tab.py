"""run_step must target the right tab via step.page_index alone — not only
when the selector carries a codegen_expr `pageN.` prefix. A plain-CSS step
recorded on a popup page (page_index=1) has no page-targeting today without
this: it silently resolves against the main page instead."""

from playwright.sync_api import sync_playwright

from atlas_engine.brain.loop import run_step
from atlas_engine.brain.models import TrajectoryStep


def test_run_step_targets_the_popup_page_for_a_plain_css_selector_via_page_index(fixture_portal_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/popup_trigger.html")

        open_step = TrajectoryStep(
            index=0, kind="click",
            selector_candidates=[{"selector": "#open-report", "rank": 0}],
            value=None, expected_effect="none", expects_popup=True,
        )
        run_step(page, open_step, timeout_ms=5000)

        popup_step = TrajectoryStep(
            index=1, kind="click",
            selector_candidates=[{"selector": "#generate-report-btn", "rank": 0}],
            value=None, expected_effect="dom_changed", page_index=1,
        )
        outcome = run_step(page, popup_step, timeout_ms=5000)

        browser.close()

    assert outcome.on_track is True
    assert outcome.url_after.endswith("/dashboard.html")


def test_run_step_does_not_crash_when_the_step_has_no_selector_candidates(fixture_portal_url):
    """A real site's clickable element often has neither an id nor a
    data-testid (confirmed via a live recording against a real external
    login page) -- selector_candidates is then []. run_step must still
    report this as an off-track outcome for the brain to repair, not raise
    an unguarded IndexError out of _first_selector before any of its own
    error handling even runs."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        step = TrajectoryStep(
            index=0, kind="click", selector_candidates=[], value=None, expected_effect="none",
        )
        outcome = run_step(page, step, timeout_ms=2000)

        browser.close()

    assert outcome.on_track is False


def test_run_step_replays_a_cmd_click_so_a_same_window_link_opens_a_new_tab(fixture_portal_url):
    """Confirmed via a real recording: a plain link (no target=_blank) only
    opens a new tab when the recorded click actually held Meta/Control --
    without reproducing that modifier, replay performs an ordinary click and
    the link navigates the SAME page instead of opening a popup at all."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/popup_trigger.html")
        page.evaluate("""() => {
            const a = document.createElement('a');
            a.id = 'same-window-link';
            a.href = '/dashboard.html';
            a.textContent = 'Open report (same window unless cmd+clicked)';
            document.body.appendChild(a);
        }""")

        step = TrajectoryStep(
            index=0, kind="click",
            selector_candidates=[{"selector": "#same-window-link", "rank": 0}],
            value=None, expected_effect="none", modifiers=("Meta",),
        )
        run_step(page, step, timeout_ms=5000)

        assert page.context.pages[-1].url.endswith("/dashboard.html")
        assert page.url.endswith("/popup_trigger.html")  # the ORIGINAL page never navigated

        browser.close()
