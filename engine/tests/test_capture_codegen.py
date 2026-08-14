import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

from atlas_engine.capture.codegen import (
    CodegenAction,
    attach_codegen_recorder,
    build_codegen_trajectory,
    parse_codegen_actions,
    strip_extraction_markers,
)


def test_attach_codegen_recorder_emits_a_playwright_script():
    out = Path(tempfile.mkdtemp()) / "codegen.py"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        attach_codegen_recorder(context, out)
        page.goto("data:text/html,<html><body><input id='u'/></body></html>")
        page.fill("#u", "hello")
        browser.close()
    assert out.exists()
    text = out.read_text()
    assert "fill" in text and "hello" in text


def test_attach_codegen_recorder_never_raises_on_failure():
    # A non-channel-owning stub context (an int) must fail inside the
    # wrapper, not propagate — the recording continues without a script.
    out = Path(tempfile.mkdtemp()) / "codegen.py"
    attach_codegen_recorder(object(), out)  # type: ignore[arg-type]


def test_strip_extraction_markers_removes_grab_button_lines():
    codegen = (
        '    page.goto("https://portal.example")\n'
        '    page.locator("#__atlasGrabBtn").click()\n'
        '    page.get_by_label("Extract this page").click()\n'
        '    page.locator("[id=\\"u\\"]").fill("hello")\n'
    )
    clean = strip_extraction_markers(codegen)
    assert "__atlasGrabBtn" not in clean
    assert "Extract this page" not in clean
    assert "page.goto" in clean and "hello" in clean


def test_parse_codegen_actions_classifies_action_lines():
    codegen = '''\
    page.goto("https://portal.example/login")
    page.locator("[id=\\"username\\"]").fill("demo")
    page.locator("text=Sign in").click()
    page.click("#submit")
    page.fill("#note", "hi")
    expect(page.locator("text=Welcome")).to_be_visible()
    # a comment line
    page.close()
'''
    actions = parse_codegen_actions(codegen)
    assert [a.kind for a in actions] == ["goto", "fill", "click", "click", "fill"]
    assert actions[0].selector == "https://portal.example/login"
    assert actions[1].selector == 'codegen_expr:page.locator("[id=\\"username\\"]")'
    assert actions[1].value == "demo"
    assert actions[2].selector == 'codegen_expr:page.locator("text=Sign in")'
    assert actions[3].selector == "#submit"  # bare page.click -> plain CSS
    assert actions[4].selector == "#note"


def test_parse_codegen_actions_picks_up_method_values():
    codegen = '''\
    page.locator("#pw").press("Enter")
    page.locator("select#role").select_option("admin")
    page.get_by_role("button", name="Save").click()
'''
    actions = parse_codegen_actions(codegen)
    assert actions[0].kind == "press"
    assert actions[0].value == "Enter"
    assert actions[0].selector == 'codegen_expr:page.locator("#pw")'
    assert actions[1].kind == "select_option"
    assert actions[1].value == "admin"
    assert actions[2].kind == "click"
    assert actions[2].selector == 'codegen_expr:page.get_by_role("button", name="Save")'


def test_parse_codegen_actions_preserves_full_locator_chains():
    """Chained locators (locator(...).get_by_role(...).filter(has_text=...)),
    get_by_text with kwargs, mixed chains, popups and iframes must keep their
    WHOLE expression — a greedy regex would truncate the chain and the replay
    would crash parsing a CSS selector."""
    codegen = '''\
    page.locator("#providerLicense").get_by_role("button").filter(has_text=re.compile(r"^$")).click()
    page.get_by_text("Menu", exact=True).click()
    page.locator("#pane6").get_by_text("Reports").click()
    page1.get_by_role("searchbox", name="Search content").fill("13.10")
    page1.locator("iframe[name=\\"f\\"]").content_frame.get_by_role("button", name="OK").click()
'''
    actions = parse_codegen_actions(codegen)
    assert actions[0].selector == (
        'codegen_expr:page.locator("#providerLicense").get_by_role("button")'
        '.filter(has_text=re.compile(r"^$"))'
    )
    assert actions[1].selector == 'codegen_expr:page.get_by_text("Menu", exact=True)'
    assert actions[2].selector == 'codegen_expr:page.locator("#pane6").get_by_text("Reports")'
    assert actions[3].page_var == "page1" and actions[3].page_index == 1
    assert actions[3].value == "13.10"
    assert actions[3].selector == 'codegen_expr:page1.get_by_role("searchbox", name="Search content")'
    assert actions[4].page_index == 1
    assert actions[4].selector == (
        'codegen_expr:page1.locator("iframe[name=\\"f\\"]").content_frame'
        '.get_by_role("button", name="OK")'
    )


def test_parse_codegen_actions_skips_structural_lines():
    codegen = '''\
    with page.expect_popup() as page1_info:
        page.get_by_text("eBO Reports").click()
    page1 = page1_info.value
    page1.goto("https://reports.example/")
    page1.close()
    browser.close()
'''
    actions = parse_codegen_actions(codegen)
    assert [a.kind for a in actions] == ["click", "goto"]
    assert actions[0].selector == 'codegen_expr:page.get_by_text("eBO Reports")'
    assert actions[1].selector == "https://reports.example/"
    assert actions[1].page_index == 1


def test_build_codegen_trajectory_maps_actions_to_steps():
    actions = [
        CodegenAction("goto", "https://x/login"),
        CodegenAction("click", 'codegen_expr:page.get_by_role("button", name="Next")'),
        CodegenAction("fill", 'codegen_expr:page.get_by_role("textbox", name="PW")', "secret"),
        CodegenAction("click", 'codegen_expr:page1.get_by_role("link", name="Open")'),
        CodegenAction("press", 'codegen_expr:page.locator("#field")', "Enter"),
    ]
    trajectory = build_codegen_trajectory(actions)
    assert trajectory is not None
    steps = trajectory.steps
    assert [s.kind for s in steps] == ["goto", "click", "fill", "click", "press"]
    assert steps[0].expected_effect == "dom_changed"
    assert steps[0].selector_candidates == [{"selector": "https://x/login", "rank": 0}]
    assert steps[2].value == "secret"
    assert steps[3].selector_candidates == [
        {"selector": 'codegen_expr:page1.get_by_role("link", name="Open")', "rank": 0}
    ]
    assert steps[3].expected_effect == "none"
    assert steps[4].value == "Enter"


def test_build_codegen_trajectory_returns_none_for_unsupported_only():
    assert build_codegen_trajectory([CodegenAction("hover", "text=Menu")]) is None
    assert build_codegen_trajectory([]) is None


def test_codegen_expr_selector_is_executed_as_a_playwright_locator(fixture_portal_url):
    """get_by_* selectors are not CSS — they must be re-evaluated as locator
    calls during replay, or the run crashes parsing a CSS selector."""
    from atlas_engine.brain.loop import run_step
    from atlas_engine.brain.models import TrajectoryStep

    step = TrajectoryStep(
        index=0,
        kind="fill",
        selector_candidates=[
            {"selector": 'codegen_expr:page.get_by_role("textbox", name="Username")', "rank": 0}
        ],
        value="demo",
        expected_effect="none",
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")
        outcome = run_step(page, step)
        filled = page.input_value("#username")
        browser.close()

    assert outcome.on_track
    assert filled == "demo"


def test_goto_step_is_executed_as_a_navigation(fixture_portal_url):
    from atlas_engine.brain.loop import run_step
    from atlas_engine.brain.models import TrajectoryStep

    step = TrajectoryStep(
        index=0,
        kind="goto",
        selector_candidates=[{"selector": f"{fixture_portal_url}/dashboard.html", "rank": 0}],
        value=None,
        expected_effect="url_changed",
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/login.html")
        outcome = run_step(page, step)
        browser.close()

    assert outcome.on_track


def test_popup_page_step_acts_on_the_popup(fixture_portal_url):
    """A codegen `page1.*` step must run against the popup the app opened,
    not the main page — otherwise the click never lands."""
    from atlas_engine.brain.loop import run_step
    from atlas_engine.brain.models import TrajectoryStep

    trajectory_steps = (
        TrajectoryStep(
            0, "click",
            [{"selector": 'codegen_expr:page.get_by_role("link", name="Open report")', "rank": 0}],
            None, "none",
        ),
        TrajectoryStep(
            1, "click",
            [{"selector": 'codegen_expr:page1.get_by_role("button", name="Generate report")', "rank": 0}],
            None, "dom_changed",
        ),
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/popup_trigger.html")
        run_step(page, trajectory_steps[0])
        outcome = run_step(page, trajectory_steps[1])
        popup = page.context.pages[1]
        status = popup.locator("#report-status").inner_text()
        browser.close()

    assert outcome.on_track
    assert status == "Report ready"


def test_mixed_chain_locator_is_executed_as_a_playwright_locator(fixture_portal_url):
    """The recorded `locator("#id").get_by_role(...)...` chain must be
    re-evaluated as a locator, NOT truncated to a bare CSS selector."""
    from atlas_engine.brain.loop import run_step
    from atlas_engine.brain.models import TrajectoryStep

    step = TrajectoryStep(
        0, "click",
        [{"selector": 'codegen_expr:page.locator("body").get_by_role("button", name="Generate report")', "rank": 0}],
        None, "dom_changed",
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/dashboard.html")
        outcome = run_step(page, step)
        status = page.locator("#report-status").inner_text()
        browser.close()

    assert outcome.on_track
    assert status == "Report ready"