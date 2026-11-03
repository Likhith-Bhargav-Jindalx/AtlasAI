from playwright.sync_api import sync_playwright

from atlas_engine.brain.loop import _resolve_locator


def test_resolve_locator_prefers_the_interactive_element_when_a_heading_also_matches_text(fixture_portal_url):
    """the-internet.herokuapp.com/login's `text=Login` ambiguity, reproduced:
    an `<h2>Login Page</h2>` heading and the real submit button's `<i>Login</i>`
    icon both match a substring text selector and are both visible. Picking
    the first DOM match (the heading) is a silent no-op -- prefer the
    clickable control instead."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/ambiguous_login.html")

        loc, used_index, match_count = _resolve_locator(page, [{"selector": "text=Login", "rank": 0}], 2000)
        tag = loc.evaluate("el => el.closest('button') ? 'button' : el.tagName.toLowerCase()")

        browser.close()

    assert match_count == 2
    assert tag == "button"
