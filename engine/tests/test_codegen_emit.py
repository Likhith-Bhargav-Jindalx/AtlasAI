import json
import subprocess
import sys
import textwrap

import pytest

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
    assert 'params["username"]' in script
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
    assert 'page.get_by_role("textbox", name="Username").fill(params["codegen_expr_get_by_role_textbox_name_username"])' in script
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

    # This trajectory has fill/password steps, so (per Task 11) the
    # username/password steps are now swept into the login() block, which
    # reads credentials from env vars (portal_domain defaults to "unknown"
    # here, so SECRET_UNKNOWN_USERNAME/SECRET_UNKNOWN_PASSWORD) rather than
    # baking the recorded literal values into the script — supply them so
    # login actually succeeds and the flow reaches the dashboard, exactly
    # as it did when the values were baked in literally before this task.
    import os
    env = dict(os.environ)
    env["SECRET_UNKNOWN_USERNAME"] = "demo"
    env["SECRET_UNKNOWN_PASSWORD"] = "demo-pass"
    result = subprocess.run(
        [sys.executable, str(script_path)], capture_output=True, text=True, timeout=30, env=env,
    )

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
        '.get_by_role("searchbox", name="S").fill(params["codegen_expr_page1_locator_q_content_frame_get_by_role_searchbox_name_s"])' in script
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
    # Use !r for escaping, so simple strings get single quotes (Python's repr default)
    assert "default='01/01/2026'" in block
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


def test_argparse_block_escapes_special_characters_in_defaults():
    """Regression test: default values with quotes, backslashes, etc. must be
    properly escaped so the generated Python code is syntactically valid and
    cannot be code-injected. Uses !r formatting to produce safe string literals."""
    import textwrap
    from atlas_engine.codegen.emit import _build_argparse_and_main_params
    from atlas_engine.codegen.params import ClassifiedParams, CliParam
    # Create params with special characters that could break/inject if not escaped
    classified = ClassifiedParams(
        secrets=(),
        params=(
            CliParam(0, "msg", 'she said "hi"'),  # double-quote
            CliParam(1, "path", "C:\\Users\\test"),  # backslash
            CliParam(2, "quoted", "it's fine"),  # single-quote
            CliParam(3, "newline", "line1\nline2"),  # newline
        ),
    )
    block = _build_argparse_and_main_params(classified)
    # The generated code should be syntactically valid Python
    # Dedent to remove the leading spaces (block is indented for insertion into main())
    dedented = textwrap.dedent(block)
    compile(dedented, "<generated>", "exec")


def test_login_block_is_empty_when_there_is_no_password_step():
    from atlas_engine.codegen.emit import _build_login_block
    from atlas_engine.codegen.params import ClassifiedParams
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "click", [{"selector": "#btn", "rank": 0}], None, "none"),
    ))
    block, consumed = _build_login_block(trajectory, ClassifiedParams(secrets=(), params=()), success_signal=None)
    assert block == ""
    assert consumed == set()


def test_login_block_emits_retry_wrapper_and_success_signal_wait():
    from atlas_engine.codegen.emit import _build_login_block
    from atlas_engine.codegen.params import ClassifiedParams, SecretParam
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
        TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "hunter2", "none"),
        TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
    ))
    classified = ClassifiedParams(
        secrets=(SecretParam(0, "username", "SECRET_X_USERNAME"), SecretParam(1, "password", "SECRET_X_PASSWORD")),
        params=(),
    )
    block, consumed = _build_login_block(
        trajectory, classified, success_signal={"url_contains": "/dashboard"},
    )
    assert "def login(page, params):" in block
    assert "for attempt in range(1, 4):" in block
    assert 'params["username"]' in block
    assert 'params["password"]' in block
    assert '"/dashboard"' in block
    assert "def dismiss_post_login_continue(page):" in block
    assert consumed == {0, 1, 2}
    compile(block, "<login>", "exec")


def test_login_block_verifies_via_url_contains_when_no_locator_signal_given():
    from atlas_engine.codegen.emit import _build_login_block
    from atlas_engine.codegen.params import ClassifiedParams, SecretParam
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#password", "rank": 0}], "hunter2", "none"),
        TrajectoryStep(1, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
    ))
    classified = ClassifiedParams(secrets=(SecretParam(0, "password", "SECRET_X_PASSWORD"),), params=())
    block, _consumed = _build_login_block(
        trajectory, classified, success_signal={"url_contains": "/dashboard"},
    )
    assert '"/dashboard" in page.url' in block
    compile(block, "<login>", "exec")


def test_login_block_never_embeds_the_recorded_secret_literal():
    """The generated login block must read params[...] at runtime, never bake
    in the actual recorded credential text — that's the whole point of this
    feature (a real recording previously hardcoded a real username/password)."""
    from atlas_engine.codegen.emit import _build_login_block
    from atlas_engine.codegen.params import ClassifiedParams, SecretParam
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo_user_123", "none"),
        TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "s3cr3t-hunter2", "none"),
        TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
    ))
    classified = ClassifiedParams(
        secrets=(SecretParam(0, "username", "SECRET_X_USERNAME"), SecretParam(1, "password", "SECRET_X_PASSWORD")),
        params=(),
    )
    block, _consumed = _build_login_block(trajectory, classified, success_signal=None)
    assert "demo_user_123" not in block
    assert "s3cr3t-hunter2" not in block
    assert "atlas_engine" not in block


def test_login_block_replays_a_next_click_between_username_and_password():
    """A username -> 'Next' click -> password flow (common on portals that
    split login into two screens): the intervening click must be replayed
    as-is, and a step with expected_effect='url_changed' (which _emit_step
    renders as two lines) must stay correctly indented inside the login
    helper's function body."""
    from atlas_engine.codegen.emit import _build_login_block
    from atlas_engine.codegen.params import ClassifiedParams, SecretParam
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
        TrajectoryStep(1, "click", [{"selector": "#next-btn", "rank": 0}], None, "url_changed"),
        TrajectoryStep(2, "fill", [{"selector": "#password", "rank": 0}], "hunter2", "none"),
        TrajectoryStep(3, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
    ))
    classified = ClassifiedParams(
        secrets=(SecretParam(0, "username", "SECRET_X_USERNAME"), SecretParam(2, "password", "SECRET_X_PASSWORD")),
        params=(),
    )
    block, consumed = _build_login_block(trajectory, classified, success_signal=None)
    assert "#next-btn" in block
    assert consumed == {0, 1, 2, 3}
    compile(block, "<login>", "exec")


def test_login_block_reindents_hover_click_steps_without_indentation_errors():
    """hover/hover_click steps bake an extra literal 8-space indent into one
    of their emitted lines (_emit_locator_gesture's "hover"/"hover_click"
    branches hardcode "        page.wait_for_timeout(900)") on top of
    _emit_step's own uniform 8-space wrap — a pre-existing quirk of
    _emit_step's output (also visible outside any login block, for a
    top-level hover_click step in emit_golden_script), not something
    _build_login_block introduces. _reindent_for_login_body must normalize
    every line's indentation independently, or a hover_click submit/between
    step produces an IndentationError once spliced into the login body."""
    from atlas_engine.codegen.emit import _build_login_block
    from atlas_engine.codegen.params import ClassifiedParams, SecretParam
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
        TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "hunter2", "none"),
        TrajectoryStep(2, "hover_click", [{"selector": "#login-btn", "rank": 0}], None, "none"),
    ))
    classified = ClassifiedParams(
        secrets=(SecretParam(0, "username", "SECRET_X_USERNAME"), SecretParam(1, "password", "SECRET_X_PASSWORD")),
        params=(),
    )
    block, consumed = _build_login_block(trajectory, classified, success_signal=None)
    assert consumed == {0, 1, 2}
    compile(block, "<login>", "exec")


# ── Per-step try/except + dump_failure_context + selector fallback chains ────


def test_wrap_step_with_diagnostics_adds_try_except():
    from atlas_engine.codegen.emit import _wrap_step_with_diagnostics
    wrapped = _wrap_step_with_diagnostics(["        page.click('#btn')"], 3, "click #btn")
    text = "\n".join(wrapped)
    assert "try:" in text
    assert "except Exception as e:" in text
    assert "dump_failure_context(page, page, 3," in text
    assert "raise" in text


def test_dump_failure_context_helper_is_embedded_in_header():
    from atlas_engine.codegen.emit import _DUMP_FAILURE_CONTEXT_HELPER
    assert "def dump_failure_context(" in _DUMP_FAILURE_CONTEXT_HELPER
    assert "page.screenshot(path=" in _DUMP_FAILURE_CONTEXT_HELPER
    assert "json.dump(" in _DUMP_FAILURE_CONTEXT_HELPER
    compile(_DUMP_FAILURE_CONTEXT_HELPER, "<helper>", "exec")


def test_emit_step_with_fallback_tries_every_ranked_candidate_in_turn():
    """Global Constraint / spec §3: a step with N ranked selector candidates
    must try each in turn at runtime (not just _best_selector's single
    pick) — a candidate that breaks between authoring and a later run
    shouldn't fail the whole step if an earlier-recorded alternate still
    resolves."""
    from atlas_engine.codegen.emit import _emit_step_with_fallback
    step = TrajectoryStep(
        0, "click",
        [{"selector": "#primary", "rank": 0}, {"selector": "#fallback", "rank": 1}],
        None, "none",
    )
    lines = _emit_step_with_fallback(step, pre_action_ms=0, settle_after_nav_ms=0)
    text = "\n".join(lines)
    # I2: every fallback candidate gets _prefer_visible applied — the
    # hidden-duplicate trap is exactly when a repair path manufactures a
    # second candidate, and `>> visible=true` is supported by page.locator().
    assert "'#primary >> visible=true'" in text
    assert "'#fallback >> visible=true'" in text
    assert "for _sel in [" in text
    assert "_last_err" in text
    compile(textwrap.dedent("\n".join(f"    {l}" for l in lines)), "<step>", "exec")


def test_emit_step_with_fallback_is_a_single_call_with_only_one_candidate():
    """No fallback machinery at all when there's nothing to fall back to —
    keeps output unchanged for the (overwhelmingly common) single-candidate
    case."""
    from atlas_engine.codegen.emit import _emit_step_with_fallback
    step = TrajectoryStep(0, "click", [{"selector": "#only", "rank": 0}], None, "none")
    lines = _emit_step_with_fallback(step, pre_action_ms=0, settle_after_nav_ms=0)
    text = "\n".join(lines)
    assert "for _sel in [" not in text
    assert "#only" in text


def test_fallback_applies_visible_preference_unless_force_click():
    """I2: the multi-candidate fallback branch must apply _prefer_visible to
    every usable candidate (hidden-duplicate trap — exactly when a repair
    path manufactures a second candidate), but never to force_click steps
    (those interact with a deliberately-hidden element)."""
    from atlas_engine.codegen.emit import _emit_step_with_fallback

    normal = TrajectoryStep(
        0, "click",
        [{"selector": "#a", "rank": 0}, {"selector": "#b", "rank": 1}],
        None, "none",
    )
    force = TrajectoryStep(
        1, "click",
        [{"selector": "#c", "rank": 0}, {"selector": "#d", "rank": 1}],
        None, "none", force_click=True,
    )
    normal_text = "\n".join(_emit_step_with_fallback(normal, pre_action_ms=0, settle_after_nav_ms=0))
    force_text = "\n".join(_emit_step_with_fallback(force, pre_action_ms=0, settle_after_nav_ms=0))

    assert "'#a >> visible=true'" in normal_text
    assert "'#b >> visible=true'" in normal_text
    assert "'#c >> visible=true'" not in force_text
    assert "'#d >> visible=true'" not in force_text
    compile(textwrap.dedent("\n".join(f"    {l}" for l in normal_text.split("\n"))), "<step>", "exec")


def test_fallback_emits_a_params_reference_for_a_fill_step_with_multiple_candidates():
    """C1 through the multi-candidate branch: a fill step that goes through
    the fallback loop must emit params[...] inside the loop's fill() call,
    not the recorded literal — otherwise batch rows never reach a fallback
    fill either."""
    from atlas_engine.codegen.emit import _emit_step_with_fallback
    step = TrajectoryStep(
        0, "fill",
        [{"selector": "#startDate", "rank": 0}, {"selector": "[data-date]", "rank": 1}],
        "02/02/2026", "none",
    )
    lines = _emit_step_with_fallback(
        step, pre_action_ms=0, settle_after_nav_ms=0, value_expr='params["start_date"]',
    )
    text = "\n".join(lines)
    assert "for _sel in [" in text
    assert 'fill(params["start_date"])' in text
    assert "02/02/2026" not in text
    compile(textwrap.dedent("\n".join(f"    {l}" for l in lines)), "<step>", "exec")


def test_iframe_steps_resolve_dynamically_instead_of_a_baked_in_frame_name():
    """A codegen-recorded iframe[name="..."] reference is almost always a
    session-random BI-viewer-generated name (the exact bug this session
    found live against eCW) — must never be baked into the emitted script
    verbatim. Emission falls back to find_frame_with_element, matching by
    the step's OWN target locator (role=textbox etc.), never by frame name."""
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(
            0, "click",
            [{"selector": 'codegen_expr:page1.locator("iframe[name=\\"i1F008FA3F9314C30BF326A2A49A5D68B\\"]").content_frame.get_by_role("textbox").first', "rank": 0}],
            None, "none",
        ),
    ))
    script = emit_golden_script(trajectory, start_url="https://x.com")
    assert "i1F008FA3F9314C30BF326A2A49A5D68B" not in script
    assert "find_frame_with_element(" in script
    # Regression guard for the bug found (and fixed) during this task: the
    # helper's `matches` predicate MUST exclude the main frame. The call
    # site always probes with the literal selector '*' (any element in any
    # frame matches), so if `matches` only checked
    # `query_selector(selector) is not None` it would return True for
    # pg.frames[0] (the main page) immediately — defeating the entire
    # point of searching for the nested iframe. Assert the actual fixed
    # predicate text is present so this can't silently regress back to the
    # brief's original (buggy) one-liner.
    assert "fr == pg.main_frame" in script or "fr is pg.main_frame" in script
    assert "_frame.get_by_role(\"textbox\").first.click()" in script
    compile(script, "<golden>", "exec")


def test_uses_static_iframe_reference_detects_session_random_iframe_name_chains():
    """Direct unit test for the standalone regex-matching helper, independent
    of the full emit_golden_script integration test above."""
    from atlas_engine.codegen.emit import _uses_static_iframe_reference

    # Matches: a codegen locator chain that dives into an iframe via a
    # baked-in iframe[name="..."] selector.
    assert _uses_static_iframe_reference(
        'page1.locator("iframe[name=\\"i1F008FA3F9314C30BF326A2A49A5D68B\\"]").content_frame.get_by_role("textbox").first'
    ) is True

    # Does NOT match: a plain page-root chain with no frame traversal at all.
    assert _uses_static_iframe_reference(
        'page.get_by_role("button", name="Log in")'
    ) is False

    # Does NOT match: a content_frame traversal via a stable, non-iframe-name
    # selector (e.g. "#q") — this is a legitimate static reference that
    # should keep being emitted literally, not routed through dynamic
    # frame resolution.
    assert _uses_static_iframe_reference(
        'page1.locator("#q").content_frame.get_by_role("searchbox", name="S")'
    ) is False


def test_dynamic_frame_step_reindents_hover_click_steps_without_indentation_errors():
    """Mirrors test_login_block_reindents_hover_click_steps_without_indentation_errors:
    hover/hover_click steps bake an extra literal 8-space indent into one of
    their emitted lines (_emit_locator_gesture's "hover"/"hover_click"
    branches hardcode "        page.wait_for_timeout(900)") on top of
    _emit_step's own uniform 8-space wrap. _emit_dynamic_frame_step must
    strip that baked-in indent the same way _emit_step_with_fallback and
    _reindent_for_login_body already do, or a hover/hover_click step routed
    through the dynamic-frame path produces an IndentationError at
    compile() time."""
    for kind in ("hover", "hover_click"):
        trajectory = ReferenceTrajectory(steps=(
            TrajectoryStep(
                0, kind,
                [{"selector": 'codegen_expr:page.locator("iframe[name=\\"i1F008FA3F9314C30BF326A2A49A5D68B\\"]").content_frame.get_by_role("textbox").first', "rank": 0}],
                None, "none",
            ),
        ))
        script = emit_golden_script(trajectory, start_url="https://x.com")
        assert "i1F008FA3F9314C30BF326A2A49A5D68B" not in script
        assert "find_frame_with_element(" in script
        compile(script, "<golden>", "exec")


def test_emit_dynamic_frame_step_direct():
    """Direct unit test for the standalone emission helper, independent of
    the full emit_golden_script integration tests above (per the plan's
    global constraint: every new emission helper needs its own direct unit
    test asserting on generated text)."""
    from atlas_engine.codegen.emit import _emit_dynamic_frame_step

    step = TrajectoryStep(
        0, "click",
        [{"selector": "unused-by-this-helper", "rank": 0}],
        None, "none",
    )
    expr = 'page1.locator("iframe[name=\\"i1F008FA3F9314C30BF326A2A49A5D68B\\"]").content_frame.get_by_role("textbox").first'
    lines = _emit_dynamic_frame_step(expr, step)
    text = "\n".join(lines)

    assert "find_frame_with_element(" in text
    assert "i1F008FA3F9314C30BF326A2A49A5D68B" not in text
    assert "_page(page, 1)" in text
    assert '_frame.get_by_role("textbox").first.click()' in text

    # Wrapped at the depth _emit_step actually uses (uniform 8-space prefix
    # on every returned line), the result must compile cleanly.
    body = "\n".join(f"        {line}" for line in lines)
    script = "def run():\n" + body + "\n"
    compile(script, "<golden>", "exec")


def test_plain_hover_step_through_default_emission_path_compiles():
    """Root-cause regression test: _emit_step (the DEFAULT emission path used
    for ordinary steps that don't go through login/fallback/dynamic-frame
    wrapping) must produce compilable output for hover/hover_click, matching
    every other step kind. Prior to this fix, _emit_locator_gesture and
    _emit_css_step both hardcode "        page.wait_for_timeout(900)") on top of
    each caller's own uniform prefix -- always producing an IndentationError.
    """
    from atlas_engine.codegen.emit import _emit_step
    for kind in ("hover", "hover_click"):
        step = TrajectoryStep(0, kind, [{"selector": "#target", "rank": 0}], None, "none")
        body = _emit_step(step)
        # Wrap in a function to test compilability (body is indented for use in run())
        script = "def run(page):\n" + body + "\n"
        compile(script, f"<step-{kind}>", "exec")


def test_plain_hover_step_via_codegen_expr_through_default_emission_path_compiles():
    """Same root cause, the codegen_expr / _emit_locator_gesture path (used
    when a step's selector is a get_by_role(...)-style chain rather than a
    plain CSS string)."""
    from atlas_engine.codegen.emit import _emit_step
    for kind in ("hover", "hover_click"):
        step = TrajectoryStep(
            0, kind,
            [{"selector": 'codegen_expr:page.get_by_role("button", name="Menu")', "rank": 0}],
            None, "none",
        )
        body = _emit_step(step)
        # Wrap in a function to test compilability (body is indented for use in run())
        script = "def run(page):\n" + body + "\n"
        compile(script, f"<step-{kind}-expr>", "exec")


def test_print_capture_context_setup_snippet_is_valid_python():
    from atlas_engine.codegen.emit import _PRINT_CAPTURE_HELPER, _PRINT_CAPTURE_INIT_SCRIPT
    compile(_PRINT_CAPTURE_HELPER, "<helper>", "exec")
    assert "window.print = fire" in _PRINT_CAPTURE_INIT_SCRIPT


def test_extract_step_emission_runs_serializer_across_frames_and_accumulates_result():
    from atlas_engine.codegen.emit import _emit_extract_step
    lines = _emit_extract_step()
    text = "\n".join(lines)
    assert "_fr.evaluate(" in text
    assert 'result["extractions"][f"click_{_extract_n}"]' in text
    body = "\n".join(f"    {l}" for l in lines)
    script = "def run():\n" + body + "\n"
    compile(script, "<extract>", "exec")


def test_download_step_emission_uses_context_scoped_event_not_page_scoped():
    from atlas_engine.codegen.emit import _emit_download_step
    lines = _emit_download_step(["page1.get_by_role(\"link\", name=\"Excel data\").click()"])
    text = "\n".join(lines)
    assert 'context.expect_event("download"' in text
    assert "page.expect_download(" not in text
    assert 'result["downloaded_files"].append(' in text
    body = "\n".join(f"    {l}" for l in lines)
    script = "def run():\n" + body + "\n"
    compile(script, "<download>", "exec")


def test_emitted_script_with_failing_step_writes_diagnostics(tmp_path, fixture_portal_url):
    """End-to-end: a step that fails must produce step_N_failure.png and
    step_N_failure.json in the diagnostics dir, and the script must exit
    non-zero (raise propagates) rather than silently continuing."""
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "click", [{"selector": "#does-not-exist", "rank": 0}], None, "none"),
    ))
    script = emit_golden_script(
        trajectory, start_url=f"{fixture_portal_url}/login.html",
        portal_domain="x.com", portal_name="X", confidence=1.0, success_signal=None,
    )
    script_path = tmp_path / "golden.py"
    script_path.write_text(script)

    # Playwright's default actionability timeout on the doomed click is
    # 30000ms by itself (confirmed by direct measurement: ~31s wall clock
    # for this exact script to fail and finish writing diagnostics) — a
    # subprocess timeout of exactly 30s leaves no headroom and reliably
    # kills the process before it can exit non-zero on its own, which is
    # a harness timing issue, not a correctness one. Give it real headroom.
    result = subprocess.run(
        [sys.executable, str(script_path), "--diagnostics-dir", str(tmp_path / "diag")],
        capture_output=True, text=True, timeout=45,
    )

    assert result.returncode != 0
    assert (tmp_path / "diag" / "step_0_failure.png").exists()
    assert (tmp_path / "diag" / "step_0_failure.json").exists()


# ── Task 11: full assembly — login, diagnostics, batch mode, result contract ──


def test_emit_golden_script_produces_a_runnable_script_with_login_and_result_contract(fixture_portal_url, tmp_path):
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
        TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", "none"),
        TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
        TrajectoryStep(3, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
    ))
    script = emit_golden_script(
        trajectory, start_url=f"{fixture_portal_url}/login.html",
        portal_domain="fixture.test", portal_name="FixturePortal", confidence=0.9,
        success_signal={"url_contains": "/dashboard.html"},
    )
    script_path = tmp_path / "golden.py"
    script_path.write_text(script)

    import os
    env = dict(os.environ)
    env["SECRET_FIXTURE_TEST_USERNAME"] = "demo"
    env["SECRET_FIXTURE_TEST_PASSWORD"] = "demo-pass"
    result = subprocess.run(
        [sys.executable, str(script_path), "--diagnostics-dir", str(tmp_path / "diag")],
        capture_output=True, text=True, timeout=30, env=env,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert "fileCount" in payload
    assert "demo-pass" not in script  # the real secret is never in the file text


def test_emit_golden_script_omits_login_and_secrets_with_no_password_step():
    """Corrected Global Constraint (see plan's preflight ruling): the
    structural machinery (header banner, diagnostics, print-capture,
    argparse surface, result contract) is ALWAYS present — only the
    login block and secret env-var reads are conditional on the
    trajectory actually having a password step."""
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "click", [{"selector": "#btn", "rank": 0}], None, "none"),
    ))
    script = emit_golden_script(trajectory, start_url="https://x.com", portal_domain="x.com", portal_name="X")
    assert "def login(" not in script
    assert "SECRET_" not in script
    assert "def dump_failure_context(" in script  # always present
    assert '"--headless"' in script  # always present
    compile(script, "<golden>", "exec")


def test_emit_golden_script_compiles_with_a_multi_candidate_fallback_step():
    """Regression test for Bug 1 (per-line .strip() destroying nested
    control-flow structure): a step with 2+ ranked selector candidates goes
    through _emit_step_with_fallback's multi-candidate branch, which emits a
    genuinely nested `for _sel in [...]: try: ... except: ...` block at
    three distinct relative depths. Flattening every line to zero indent
    (the brief's original per-line .strip()) turns that into a `for` with no
    indented body — a SyntaxError/IndentationError at compile() time. The
    fix (textwrap.dedent, preserving relative indentation) must produce
    valid, compilable Python when assembled through the full
    emit_golden_script pipeline."""
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(
            0, "click",
            [{"selector": "#primary", "rank": 0}, {"selector": "#fallback", "rank": 1}],
            None, "none",
        ),
    ))
    script = emit_golden_script(trajectory, start_url="https://x.com", portal_domain="x.com", portal_name="X")
    assert "for _sel in [" in script
    compile(script, "<golden>", "exec")


def test_emit_golden_script_compiles_with_settle_after_nav_ms_goto_step():
    """Regression test for Bug 1's second scenario: a `goto` step with
    settle_after_nav_ms > 0 makes _emit_step append its own nested
    try/except (waiting for networkidle, swallowing a timeout) after the
    goto line. This must also survive the full emit_golden_script assembly
    (dedent + diagnostics wrap + main() splice) and still compile."""
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "goto", [{"selector": "https://example.com/x", "rank": 0}], None, "url_changed"),
    ))
    script = emit_golden_script(
        trajectory, start_url="https://x.com", portal_domain="x.com", portal_name="X",
        settle_after_nav_ms=15000,
    )
    assert 'page.wait_for_load_state("networkidle", timeout=15000)' in script
    compile(script, "<golden>", "exec")


def test_emit_golden_script_always_navigates_to_start_url_even_with_login_block():
    """Regression test for Bug 2: login() never navigates anywhere itself —
    it assumes the page is already at the login page. The assembled
    run_flow must always call page.goto(start_url) BEFORE calling login(),
    never skip the goto just because a login block exists (an
    about:blank page has no #username/#password to fill)."""
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
        TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", "none"),
        TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
    ))
    script = emit_golden_script(
        trajectory, start_url="https://example.com/login.html",
        portal_domain="x.com", portal_name="X",
    )
    assert "def login(" in script
    goto_index = script.index("page.goto('https://example.com/login.html')")
    # Search for the actual CALL site ("    login(page, params)" at
    # run_flow's 4-space indent), not script.index("login(page, params)")
    # alone — that substring also matches inside the earlier function
    # DEFINITION line "def login(page, params):" emitted in the header,
    # which would make this assertion pass even if the call were missing.
    login_call_index = script.index("\n    login(page, params)\n")
    assert goto_index < login_call_index
    compile(script, "<golden>", "exec")


# ── C1: CLI params/batch rows must reach emitted steps (not just the login block) ──


def test_non_secret_fill_step_emits_a_params_reference_not_the_recorded_literal():
    """A fill step classified as a CliParam (not a secret) must emit a
    reference to params[...] at runtime, not the recorded literal value —
    otherwise --start-date on the CLI (or a batch row) silently does
    nothing, since the emitted line never reads it."""
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#startDate", "rank": 0}], "02/02/2026", "none"),
    ))
    script = emit_golden_script(trajectory, start_url="https://x.com")
    # The FILL step itself must read params[...] at runtime — the recorded
    # literal may legitimately still appear as the argparse default value,
    # so assert on the emitted fill line specifically, not the whole script.
    fill_line = next(l for l in script.splitlines() if "start_date" in l and "fill" in l)
    assert 'params["start_date"]' in fill_line
    assert "02/02/2026" not in fill_line
    compile(script, "<golden>", "exec")


def test_batch_mode_actually_applies_different_row_values_to_the_fixture_portal(fixture_portal_url, tmp_path):
    """End-to-end regression for C1: a --batch run with 2 rows carrying
    DIFFERENT values for a classified CLI param must actually apply those
    different values when filling the field — not silently replay the same
    recorded literal for every row (which would make every batch row's
    result identical despite being labeled with different "inputs")."""
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
        TrajectoryStep(1, "extract", [], None, "none"),
    ))
    script = emit_golden_script(trajectory, start_url=f"{fixture_portal_url}/login.html")
    script_path = tmp_path / "golden.py"
    script_path.write_text(script)

    batch_file = tmp_path / "batch.json"
    batch_file.write_text(json.dumps([{"username": "alice"}, {"username": "bob"}]))

    result = subprocess.run(
        [sys.executable, str(script_path), "--batch", str(batch_file)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    rows = payload["batch"]
    assert len(rows) == 2
    row0_username = rows[0]["extractions"]["click_1"]["data"]["fields"]["username"]
    row1_username = rows[1]["extractions"]["click_1"]["data"]["fields"]["username"]
    assert row0_username == "alice"
    assert row1_username == "bob"
    assert row0_username != row1_username


# ── I1: batch mode must attribute each row's print-to-PDF captures to ITS result ──


def test_batch_mode_attributes_print_pdfs_to_the_row_that_produced_them(tmp_path):
    """I1: _PRINT_CAPTURES is module-global, so the batch loop must flush AND
    clear it after every row and attach that row's PDFs to ITS result —
    otherwise print captures pile up un-attributed (the pre-fix batch branch
    flushed once at the end into a variable nobody reads, so every row's
    downloaded_files stayed empty)."""
    from pathlib import Path

    print_page = tmp_path / "print.html"
    print_page.write_text(
        "<!doctype html><html><head><title>My Report</title></head>"
        "<body><input id='username' name='username'>"
        "<script>window.print()</script></body></html>"
    )
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
    ))
    script = emit_golden_script(trajectory, start_url=print_page.as_uri())
    script_path = tmp_path / "golden.py"
    script_path.write_text(script)

    batch_file = tmp_path / "batch.json"
    batch_file.write_text(json.dumps([{"username": "alice"}, {"username": "bob"}]))

    result = subprocess.run(
        [sys.executable, str(script_path), "--batch", str(batch_file),
         "--diagnostics-dir", str(tmp_path / "diag")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    rows = payload["batch"]
    assert len(rows) == 2
    row0_pdfs = rows[0]["downloaded_files"]
    row1_pdfs = rows[1]["downloaded_files"]
    # Each row's PDFs are ATTRIBUTED to that row (pre-fix, the batch branch
    # flushed once into a variable nobody read, so every row's
    # downloaded_files was empty). Row 0 gets its own single print capture;
    # row 1 may re-print on the inter-row re-navigation too, so it gets >= 1.
    assert len(row0_pdfs) >= 1
    assert len(row1_pdfs) >= 1
    assert row0_pdfs[0]["filename"] == "My Report.pdf"
    for pdf in row0_pdfs + row1_pdfs:
        assert "filename" in pdf
        assert Path(pdf["path"]).exists()


def test_print_capture_helper_clears_captures_after_flushing():
    """I1/C2: _flush_print_pdfs must (a) emit its saved entries with the
    same 'filename' key the rest of the downloaded_files contract uses (the
    pre-fix 'name' key made _persist_certify_result KeyError whenever a
    print-to-PDF capture reached certification) and (b) clear
    _PRINT_CAPTURES after flushing so per-row batch flushes don't
    re-attribute the same captures."""
    from atlas_engine.codegen.emit import _PRINT_CAPTURE_HELPER
    assert 'saved.append({"filename": name, "path": dest})' in _PRINT_CAPTURE_HELPER
    assert "_PRINT_CAPTURES.clear()" in _PRINT_CAPTURE_HELPER
    compile(_PRINT_CAPTURE_HELPER, "<helper>", "exec")


def test_emit_golden_script_defaults_portal_domain_and_name_for_existing_callers():
    """Regression test for Bug 3: portal_domain/portal_name must have
    defaults so pre-existing call sites (that don't care about portal
    identity) keep working without a TypeError."""
    trajectory = ReferenceTrajectory(
        steps=(TrajectoryStep(0, "click", [{"selector": "#btn", "rank": 0}], None, "none"),)
    )
    script = emit_golden_script(trajectory, start_url="https://example.com/")
    compile(script, "<golden>", "exec")

