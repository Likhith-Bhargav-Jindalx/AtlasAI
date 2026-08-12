from playwright.sync_api import sync_playwright

from atlas_engine.brain.evaluate import evaluate
from atlas_engine.brain.models import TrajectoryStep
from atlas_engine.brain.repair import RepairAction, think
from atlas_engine.llm.client import FakeLlmClient


def test_think_validates_and_returns_a_selector_that_exists_on_the_live_page(fixture_portal_url):
    step = TrajectoryStep(
        index=0, kind="click", selector_candidates=[{"selector": "#does-not-exist", "rank": 0}],
        value=None, expected_effect="dom_changed",
    )
    fake_llm = FakeLlmClient(responses=[{"action": "replace_selector", "selector": "#generate-report-btn"}])

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/dashboard.html")

        outcome = evaluate(
            step, url_before=page.url, url_after=page.url,
            dom_before=page.content(), dom_after=page.content(),
        )
        result = think(fake_llm, step, page, outcome)

        browser.close()

    assert result == RepairAction(action="replace_selector", selector="#generate-report-btn")
    assert len(fake_llm.calls) == 1


def test_think_coerces_a_selector_that_does_not_exist_on_the_page_to_give_up(fixture_portal_url):
    step = TrajectoryStep(
        index=0, kind="click", selector_candidates=[{"selector": "#does-not-exist", "rank": 0}],
        value=None, expected_effect="dom_changed",
    )
    fake_llm = FakeLlmClient(responses=[{"action": "replace_selector", "selector": "#also-does-not-exist"}])

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/dashboard.html")

        outcome = evaluate(
            step, url_before=page.url, url_after=page.url,
            dom_before=page.content(), dom_after=page.content(),
        )
        result = think(fake_llm, step, page, outcome)

        browser.close()

    assert result.action == "give_up"


def test_brain_loop_repairs_a_broken_selector_and_the_retry_succeeds(fixture_portal_url):
    from atlas_engine.brain.loop import BrainLoop
    from atlas_engine.brain.models import ReferenceTrajectory

    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "click", [{"selector": "#stale-selector", "rank": 0}], None, "dom_changed"),
        )
    )
    fake_llm = FakeLlmClient(responses=[{"action": "replace_selector", "selector": "#generate-report-btn"}])

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/dashboard.html")

        outcomes = BrainLoop(page, llm=fake_llm).run(trajectory)

        browser.close()

    assert outcomes[0].on_track is True
    assert len(fake_llm.calls) == 1
