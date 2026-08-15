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


# ── Opt-in per-portal pacing (preActionMs/settleAfterNavMs) ────────────────
# Ported from the previous app's portal.pacing config (pyEmit.ts). Absent
# (the default, 0) means byte-for-byte the same script as before this
# feature existed — no new tables, no new emitted lines.


def _one_step_trajectory() -> ReferenceTrajectory:
    return ReferenceTrajectory(
        steps=(TrajectoryStep(0, "click", [{"selector": "#btn", "rank": 0}], None, "none"),)
    )


def test_emit_golden_script_is_unchanged_when_pacing_not_configured():
    trajectory = _one_step_trajectory()
    baseline = emit_golden_script(trajectory, start_url="https://x.com")
    with_defaults = emit_golden_script(trajectory, start_url="https://x.com", pre_action_ms=0, settle_after_nav_ms=0)
    assert baseline == with_defaults
    assert "PACING_MS" not in baseline
    assert "_pace(" not in baseline
    assert "networkidle" not in baseline


def test_emit_golden_script_emits_pace_helper_and_calls_when_pre_action_ms_configured():
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "click", [{"selector": "#login-btn", "rank": 0}], None, "none"),
        )
    )
    script = emit_golden_script(trajectory, start_url="https://x.com", pre_action_ms=800)

    assert "PACING_MS = 800" in script
    assert "def _pace(pg):" in script
    # Called before EVERY step's action, unconditionally (matches pyEmit.ts).
    assert script.count("_pace(page)") == 2
    compile(script, "<golden>", "exec")


def test_emit_golden_script_settles_networkidle_after_goto_when_configured():
    trajectory = ReferenceTrajectory(
        steps=(TrajectoryStep(0, "goto", [{"selector": "https://example.com/x", "rank": 0}], None, "url_changed"),)
    )
    script = emit_golden_script(trajectory, start_url="https://x.com", settle_after_nav_ms=15000)

    assert 'page.wait_for_load_state("networkidle", timeout=15000)' in script
    compile(script, "<golden>", "exec")


def test_emit_golden_script_settle_only_applies_to_goto_steps():
    trajectory = ReferenceTrajectory(
        steps=(TrajectoryStep(0, "click", [{"selector": "#btn", "rank": 0}], None, "none"),)
    )
    script = emit_golden_script(trajectory, start_url="https://x.com", settle_after_nav_ms=15000)

    assert "networkidle" not in script
    compile(script, "<golden>", "exec")


def test_emitted_golden_script_with_pacing_actually_runs_against_the_fixture_portal(fixture_portal_url, tmp_path):
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", "none"),
            TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
        )
    )
    script = emit_golden_script(
        trajectory, start_url=f"{fixture_portal_url}/login.html",
        pre_action_ms=50, settle_after_nav_ms=2000,
    )

    script_path = tmp_path / "golden.py"
    script_path.write_text(script)

    result = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, result.stderr


def test_review_header_includes_portal_name_and_confidence():
    from atlas_engine.codegen.emit import _build_review_header
    from atlas_engine.codegen.params import ClassifiedParams
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "click", [{"selector": "#btn", "rank": 0}], None, "none"),
    ))
    header = _build_review_header(
        trajectory, portal_name="TxbsirECW", confidence=0.9,
        classified=ClassifiedParams(secrets=(), params=()),
    )
    assert "portal: TxbsirECW" in header
    assert "confidence: 0.9" in header


def test_review_header_warns_about_phi_free_params_with_no_default():
    from atlas_engine.codegen.emit import _build_review_header
    from atlas_engine.codegen.params import ClassifiedParams, CliParam
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#dob", "rank": 0}], "", "none"),
    ))
    classified = ClassifiedParams(secrets=(), params=(CliParam(0, "dob", ""),))
    header = _build_review_header(trajectory, portal_name="X", confidence=1.0, classified=classified)
    assert "fill step(s) have no captured/parameterized value" in header
    assert "0" in header


def test_review_header_warns_about_popup_steps():
    from atlas_engine.codegen.emit import _build_review_header
    from atlas_engine.codegen.params import ClassifiedParams
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "click", [{"selector": "#open", "rank": 0}], None, "none", expects_popup=True),
    ))
    header = _build_review_header(
        trajectory, portal_name="X", confidence=1.0, classified=ClassifiedParams(secrets=(), params=()),
    )
    assert "opens at least one new page/popup" in header


def test_review_header_omits_warning_lines_when_nothing_to_warn_about():
    from atlas_engine.codegen.emit import _build_review_header
    from atlas_engine.codegen.params import ClassifiedParams, CliParam
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#x", "rank": 0}], "has-a-value", "none"),
    ))
    classified = ClassifiedParams(secrets=(), params=(CliParam(0, "x", "has-a-value"),))
    header = _build_review_header(trajectory, portal_name="X", confidence=1.0, classified=classified)
    assert "no captured/parameterized value" not in header
    assert "opens at least one new page/popup" not in header


# ── Argparse CLI surface + secrets from env ──────────────────────────────────


def test_argparse_block_declares_one_cli_flag_per_param_with_recorded_default():
    from atlas_engine.codegen.emit import _build_argparse_and_main_params
    from atlas_engine.codegen.params import ClassifiedParams, CliParam
    classified = ClassifiedParams(secrets=(), params=(CliParam(0, "start-date", "01/01/2026"),))
    block = _build_argparse_and_main_params(classified)
    assert '"--start-date"' in block
    assert 'default="01/01/2026"' in block
    assert 'params["start_date"] = args.start_date' in block


def test_argparse_block_reads_secrets_from_env_not_cli():
    from atlas_engine.codegen.emit import _build_argparse_and_main_params
    from atlas_engine.codegen.params import ClassifiedParams, SecretParam
    classified = ClassifiedParams(
        secrets=(SecretParam(0, "username", "SECRET_X_USERNAME"), SecretParam(1, "password", "SECRET_X_PASSWORD")),
        params=(),
    )
    block = _build_argparse_and_main_params(classified)
    assert 'os.environ.get("SECRET_X_USERNAME"' in block
    assert 'os.environ.get("SECRET_X_PASSWORD"' in block
    assert '"--username"' not in block
    assert '"--password"' not in block


def test_argparse_block_always_declares_headless_and_diagnostics_dir():
    from atlas_engine.codegen.emit import _build_argparse_and_main_params
    from atlas_engine.codegen.params import ClassifiedParams
    block = _build_argparse_and_main_params(ClassifiedParams(secrets=(), params=()))
    assert '"--headless"' in block
    assert '"--headed"' in block
    assert '"--diagnostics-dir"' in block
    assert '"--batch"' in block

