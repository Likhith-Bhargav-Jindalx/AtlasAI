from playwright.sync_api import sync_playwright

from atlas_engine.brain.loop import BrainLoop
from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep


def test_brain_loop_runs_login_and_report_flow_with_zero_off_track_steps(fixture_portal_url):
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", "none"),
            TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
            TrajectoryStep(3, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
        )
    )

    off_track_calls = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        outcomes = BrainLoop(page, on_off_track=off_track_calls.append).run(trajectory)

        browser.close()

    assert [o.on_track for o in outcomes] == [True, True, True, True]
    assert off_track_calls == []
