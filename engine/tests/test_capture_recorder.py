from playwright.sync_api import sync_playwright

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
