import subprocess
import sys

from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.codegen.emit import emit_golden_script


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
    assert "page.fill('#username', 'demo')" in script
    assert "page.click('#login-btn')" in script


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
