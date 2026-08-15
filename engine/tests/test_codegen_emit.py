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
    assert "'#primary'" in text
    assert "'#fallback'" in text
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


@pytest.mark.skip(reason="wired up in Task 11")
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

    result = subprocess.run(
        [sys.executable, str(script_path), "--diagnostics-dir", str(tmp_path / "diag")],
        capture_output=True, text=True, timeout=30,
    )

    assert result.returncode != 0
    assert (tmp_path / "diag" / "step_0_failure.png").exists()
    assert (tmp_path / "diag" / "step_0_failure.json").exists()

