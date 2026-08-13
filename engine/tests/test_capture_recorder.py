from playwright.sync_api import sync_playwright

from atlas_engine.capture.extraction import attach_extraction_button
from atlas_engine.capture.recorder import CaptureSession


def test_capture_session_records_click_and_fill(fixture_portal_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            page.fill("#username", "demo")
            page.fill("#password", "demo-pass")
            page.click("#login-btn")
            page.wait_for_url("**/dashboard.html")

        browser.close()

    kinds = [action.kind for action in capture.actions]
    assert kinds == ["fill", "fill", "click"]

    username_fill = capture.actions[0]
    assert username_fill.value == "demo"
    assert any(candidate["selector"] == "#username" for candidate in username_fill.selector_candidates)

    login_click = capture.actions[2]
    assert login_click.url_before.endswith("/login.html")
    assert "welcome" in login_click.dom_after or "Welcome" in login_click.dom_after


def test_capture_session_records_password_value_for_later_scrubbing(fixture_portal_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        with CaptureSession(page) as capture:
            page.fill("#password", "demo-pass")

        browser.close()

    password_fill = capture.actions[0]
    assert password_fill.value == "demo-pass"
    assert any(c["selector"] == "#password" for c in password_fill.selector_candidates)


def test_exit_after_browser_disconnected_finalizes_without_querying_the_page(fixture_portal_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")

        capture = CaptureSession(page)
        capture.__enter__()
        page.fill("#username", "demo")  # leaves one action pending

        capture.mark_browser_disconnected()
        assert capture.browser_disconnected is True

        # Must not hang or raise trying to query a page we're pretending is
        # already gone — and must not need the page to still work at all.
        capture.__exit__(None, None, None)

        browser.close()

    assert len(capture.actions) == 1
    action = capture.actions[0]
    assert action.url_after == action.url_before
    assert action.dom_after == action.dom_before


def test_capture_session_ignores_clicks_on_the_extraction_button(fixture_portal_url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        grabbed_pages = []
        attach_extraction_button(context, grabbed_pages.append)

        page.goto(f"{fixture_portal_url}/login.html")
        page.wait_for_selector("#__atlasGrabBtn")

        with CaptureSession(page) as capture:
            page.click("#__atlasGrabBtn")
            page.fill("#username", "demo")  # a real action, to prove capture still works

        browser.close()

    # The button click really happened (it notified Python)...
    assert grabbed_pages == [page]
    # ...but it must never show up as a recorded trajectory step, since the
    # button only exists during live capture and can never be replayed.
    assert not any(
        candidate["selector"] == "#__atlasGrabBtn"
        for action in capture.actions
        for candidate in action.selector_candidates
    )
    assert [action.kind for action in capture.actions] == ["fill"]
