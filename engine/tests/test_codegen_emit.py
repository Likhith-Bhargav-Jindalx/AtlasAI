import subprocess
import sys

from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.codegen.emit import emit_golden_script, _looks_session_random, _prefer_visible


def test_emit_golden_script_contains_no_llm_or_engine_imports():
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
        )
    )

    script = emit_golden_script(trajectory, start_url="https://example.com/login.html")

    assert "atlas_engine" not in script
    assert "openai" not in script.lower()
    assert "from playwright.sync_api import" in script
    # CSS selectors now get >> visible=true and emit via page.locator().first
    assert "visible=true" in script
    assert ".fill('demo')" in script
    assert ".click()" in script


def test_emitted_golden_script_is_syntactically_valid_python():
    trajectory = ReferenceTrajectory(
        steps=(TrajectoryStep(0, "click", [{"selector": "#btn", "rank": 0}], None, "none"),)
    )
    script = emit_golden_script(trajectory, start_url="https://example.com/")

    compile(script, "<golden>", "exec")  # raises SyntaxError if invalid


def test_emitted_golden_script_emits_codegen_locator_expressions_as_locator_calls():
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(
                0, "fill",
                [{"selector": 'codegen_expr:get_by_role("textbox", name="Username")', "rank": 0}],
                "demo", "none",
            ),
            TrajectoryStep(
                1, "click",
                [{"selector": 'codegen_expr:get_by_role("button", name="Log in")', "rank": 0}],
                None, "url_changed",
            ),
        )
    )

    script = emit_golden_script(trajectory, start_url="https://example.com/login.html")

    # codegen_expr selectors do NOT get >> visible=true (they use get_by_*)
    assert 'page.get_by_role("textbox", name="Username").fill(\'demo\')' in script
    assert 'page.get_by_role("button", name="Log in").click()' in script
    assert 'page.click(' not in script
    compile(script, "<golden>", "exec")


def test_emitted_golden_script_actually_runs_against_the_fixture_portal(fixture_portal_url, tmp_path):
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", "none"),
            TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
            TrajectoryStep(3, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
        )
    )
    script = emit_golden_script(trajectory, start_url=f"{fixture_portal_url}/login.html")

    script_path = tmp_path / "golden.py"
    script_path.write_text(script)

    result = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, result.stderr


def test_emitted_golden_script_handles_goto_and_popup_steps():
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "goto", [{"selector": "https://example.com/pwd", "rank": 0}], None, "url_changed"),
            TrajectoryStep(
                1, "click",
                [{"selector": 'codegen_expr:page.get_by_role("button", name="Next")', "rank": 0}],
                None, "none",
            ),
            TrajectoryStep(
                2, "click",
                [{"selector": 'codegen_expr:page1.get_by_role("link", name="Open")', "rank": 0}],
                None, "none",
            ),
            TrajectoryStep(
                3, "fill",
                [{"selector": 'codegen_expr:page1.locator("#q").content_frame.get_by_role("searchbox", name="S")', "rank": 0}],
                "13.10", "none",
            ),
        )
    )

    script = emit_golden_script(trajectory, start_url="https://example.com/login.html")

    assert "page.goto('https://example.com/pwd')" in script
    assert 'page.get_by_role("button", name="Next").click()' in script
    assert '_page(page, 1).get_by_role("link", name="Open").click()' in script
    assert (
        '_page(page, 1).locator("#q").content_frame'
        '.get_by_role("searchbox", name="S").fill(\'13.10\')' in script
    )
    assert "def _page(page, index):" in script
    compile(script, "<golden>", "exec")


def test_emitted_golden_script_runs_a_multi_page_flow(fixture_portal_url, tmp_path):
    """The golden script must open the popup and replay the popup step on it —
    headless, exactly as certification does."""
    trajectory = ReferenceTrajectory(
        steps=(
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
    )
    script = emit_golden_script(trajectory, start_url=f"{fixture_portal_url}/popup_trigger.html")

    script_path = tmp_path / "golden.py"
    script_path.write_text(script)

    result = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, result.stderr


# ── Tests for ported logic from emitGolden.ts ──────────────────────────


def test_looks_session_random_detects_digit_runs():
    """3+ consecutive digits → session-random (e.g. #ctl00_12345)."""
    assert _looks_session_random("ctl00_12345") is True
    assert _looks_session_random("input_123_field") is True


def test_looks_session_random_detects_long_alphanumeric_tails():
    """6+ alphanumeric tail with a digit → session-random."""
    assert _looks_session_random("search__input__id_jnm8z6qetrg") is True
    assert _looks_session_random("field_abc1de") is True


def test_looks_session_random_accepts_stable_ids():
    """Short, clean ids without digit runs are stable."""
    assert _looks_session_random("username") is False
    assert _looks_session_random("login-btn") is False
    assert _looks_session_random("nav_link") is False


def test_prefer_visible_appends_filter_to_css_selectors():
    assert "visible=true" in _prefer_visible("#btn", False)
    assert "visible=true" in _prefer_visible(".submit", False)


def test_prefer_visible_skips_force_click():
    """force_click steps interact with deliberately-hidden elements."""
    assert "visible=true" not in _prefer_visible("#btn", True)


def test_prefer_visible_skips_codegen_expr():
    """codegen_expr selectors use get_by_* which don't take >> filters."""
    result = _prefer_visible("codegen_expr:get_by_role('button')", False)
    assert "visible=true" not in result


def test_prefer_visible_inserts_before_nth_suffix():
    result = _prefer_visible("#list >> nth=2", False)
    assert result == "#list >> visible=true >> nth=2"


def test_best_selector_skips_session_random_ids():
    """When the primary ID is session-random, fall to the next candidate."""
    step = TrajectoryStep(
        0, "click",
        [
            {"selector": "#search__input__id_jnm8z6qetrg", "rank": 0},
            {"selector": ".search-input", "rank": 1},
        ],
        None, "none",
    )
    script = emit_golden_script(ReferenceTrajectory(steps=(step,)), start_url="https://x.com")
    # Should use .search-input (rank 1), not the session-random #id (rank 0)
    assert "search-input" in script
    assert "jnm8z6qetrg" not in script


def test_css_selector_visible_true_emits_locator_first():
    """CSS selectors with >> visible=true must use page.locator().first,
    not page.click() which doesn't support >> filters."""
    trajectory = ReferenceTrajectory(
        steps=(TrajectoryStep(0, "click", [{"selector": "#btn", "rank": 0}], None, "none"),)
    )
    script = emit_golden_script(trajectory, start_url="https://x.com")
    assert "page.locator('#btn >> visible=true').first.click()" in script
    compile(script, "<golden>", "exec")


def test_force_click_does_not_add_visible_true():
    """force_click steps should NOT get >> visible=true — they need to click
    a deliberately-hidden element."""
    trajectory = ReferenceTrajectory(
        steps=(TrajectoryStep(0, "click", [{"selector": "#hidden", "rank": 0}], None, "none", force_click=True),)
    )
    script = emit_golden_script(trajectory, start_url="https://x.com")
    assert "visible=true" not in script
    assert "page.click('#hidden', force=True)" in script
    compile(script, "<golden>", "exec")

