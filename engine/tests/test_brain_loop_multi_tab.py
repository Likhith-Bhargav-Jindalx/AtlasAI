"""run_step must target the right tab via step.page_index alone — not only
when the selector carries a codegen_expr `pageN.` prefix. A plain-CSS step
recorded on a popup page (page_index=1) has no page-targeting today without
this: it silently resolves against the main page instead."""

from playwright.sync_api import sync_playwright

from atlas_engine.brain.loop import BrainLoop, run_step
from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep


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


def test_run_step_opens_a_manually_triggered_tab_and_registers_it(fixture_portal_url):
    """A tab the operator opened themselves (Cmd+T, or already open at
    recording start) has no app-side cause that will spawn it during
    replay -- unlike a popup, run_step must manufacture it itself."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_context().new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        registry = {0: page}
        step = TrajectoryStep(
            index=0, kind="tab_opened", selector_candidates=[],
            value=f"{fixture_portal_url}/dashboard.html", expected_effect="none",
            page_index=1, tab_origin="manual",
        )
        outcome = run_step(page, step, timeout_ms=5000, page_registry=registry)

        assert outcome.on_track is True
        assert 1 in registry
        assert registry[1].url.endswith("/dashboard.html")

        browser.close()


def test_run_step_opens_a_preexisting_tab_the_same_way_as_a_manual_one(fixture_portal_url):
    """A tab already open when the recording started is replayed the exact
    same way as one opened mid-recording -- neither has an app-side cause
    that will spawn it, so run_step must manufacture both itself. Only the
    origin label differs; the execution path does not branch on it."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_context().new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        registry = {0: page}
        step = TrajectoryStep(
            index=0, kind="tab_opened", selector_candidates=[],
            value=f"{fixture_portal_url}/dashboard.html", expected_effect="none",
            page_index=1, tab_origin="preexisting",
        )
        outcome = run_step(page, step, timeout_ms=5000, page_registry=registry)

        assert outcome.on_track is True
        assert 1 in registry
        assert registry[1].url.endswith("/dashboard.html")

        browser.close()


def test_run_step_treats_a_popup_origin_tab_opened_step_as_a_no_op(fixture_portal_url):
    """A popup is spawned as a side effect of a DIFFERENT step (the
    triggering click) -- the tab_opened step recorded for it carries no
    action of its own; manufacturing a page here would create a spurious
    extra tab alongside the real popup."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        registry = {0: page}
        pages_before = len(page.context.pages)
        step = TrajectoryStep(
            index=0, kind="tab_opened", selector_candidates=[], value="https://example.com/",
            expected_effect="none", page_index=1, tab_origin="popup",
        )
        outcome = run_step(page, step, timeout_ms=5000, page_registry=registry)

        assert outcome.on_track is True
        assert len(page.context.pages) == pages_before
        assert 1 not in registry

        browser.close()


def test_run_step_closes_a_registered_tab(fixture_portal_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_context().new_page()
        page.goto(f"{fixture_portal_url}/login.html")
        extra_page = page.context.new_page()
        extra_page.goto(f"{fixture_portal_url}/dashboard.html")

        registry = {0: page, 1: extra_page}
        step = TrajectoryStep(
            index=0, kind="tab_closed", selector_candidates=[], value=None,
            expected_effect="none", page_index=1,
        )
        outcome = run_step(page, step, timeout_ms=5000, page_registry=registry)

        assert outcome.on_track is True
        assert extra_page.is_closed()
        assert 1 not in registry

        browser.close()


def test_brain_loop_tracks_page_identity_through_a_close_so_a_later_tab_resolves_correctly(fixture_portal_url):
    """context.pages is POSITIONAL -- a closed page drops out of the list,
    shifting every later index down by one. A later tab opened after an
    earlier one closed would then resolve to the WRONG page under
    position-based lookup. BrainLoop must resolve pages by identity
    (tracked from explicit tab_opened/tab_closed steps) instead."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_context().new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        steps = (
            TrajectoryStep(
                0, "tab_opened", [], f"{fixture_portal_url}/dashboard.html", "none",
                page_index=1, tab_origin="manual",
            ),
            TrajectoryStep(1, "tab_closed", [], None, "none", page_index=1),
            TrajectoryStep(
                2, "tab_opened", [], f"{fixture_portal_url}/popup_trigger.html", "none",
                page_index=2, tab_origin="manual",
            ),
            TrajectoryStep(
                3, "click", [{"selector": "#open-report", "rank": 0}], None, "none",
                page_index=2, expects_popup=True,
            ),
        )
        trajectory = ReferenceTrajectory(steps=steps)

        loop = BrainLoop(page)
        result = loop.run(trajectory)

        browser.close()

    assert result.success is True
