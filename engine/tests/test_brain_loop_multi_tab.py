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
