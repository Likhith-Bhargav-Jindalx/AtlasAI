import threading
import time
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

from atlas_engine.authoring.replay import run_replay_phase
from atlas_engine.authoring.run import apply_stored_codegen_selectors
from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.config import settings
from atlas_engine.db import session_scope
from atlas_engine.db.models import AuthoringRun, AuthoringRunEvent, Organization, PortalProfile, Recording


def _login_trajectory() -> ReferenceTrajectory:
    return ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", "none"),
            TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
            TrajectoryStep(3, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
        )
    )


def _make_authoring_run() -> uuid.UUID:
    with session_scope() as session:
        org = Organization(name="Replay Phase Org", slug=f"replay-phase-org-{uuid.uuid4()}")
        profile = PortalProfile(org=org, domain="portal-replay.example.com")
        recording = Recording(
            org=org, portal_profile=profile, status="captured", capture_blob_prefix="orgs/x/recordings/1/",
        )
        run = AuthoringRun(org=org, recording=recording, status="running", current_phase="replay")
        session.add_all([org, profile, recording, run])
        session.flush()
        return run.id


def test_run_replay_phase_completes_automatically_when_every_step_succeeds(fixture_portal_url):
    authoring_run_id = _make_authoring_run()
    start_url = f"{fixture_portal_url}/login.html"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(start_url)
        result = run_replay_phase(
            page, _login_trajectory(), authoring_run_id, threading.Event(), threading.Event(),
        )
        browser.close()

    assert result is not None
    assert len(result.steps) == 4
    assert [s.index for s in result.steps] == [0, 1, 2, 3]

    with session_scope() as session:
        run = session.get(AuthoringRun, authoring_run_id)
        assert run.status == "running"  # replay itself doesn't finalize status
        events = session.query(AuthoringRunEvent).filter_by(authoring_run_id=authoring_run_id).all()
        assert len(events) == 4
        assert all(e.level == "info" for e in events)


def test_run_replay_phase_captures_reference_evidence_per_step(fixture_portal_url):
    """The replay is INSTRUMENTED — every step gets reference screenshots and
    the resolved element's identity, the evidence the brain thinks over."""
    authoring_run_id = _make_authoring_run()
    start_url = f"{fixture_portal_url}/login.html"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(start_url)
        result = run_replay_phase(
            page, _login_trajectory(), authoring_run_id, threading.Event(), threading.Event(),
        )
        browser.close()

    assert result is not None
    for step in result.steps:
        assert step.ref_shot_before is not None and Path(step.ref_shot_before).exists()
        assert step.ref_shot_after is not None and Path(step.ref_shot_after).exists()
    # A fill/click resolves an element with identity — the login button's id.
    assert result.steps[2].identity is not None
    assert result.steps[2].identity.get("tag") == "button"


def test_run_replay_phase_does_not_pause_for_developer_on_a_failed_step(fixture_portal_url, fixture_portal_server):
    """The recorded #generate-report-btn is drifted away, so the step fails —
    but the replay does NOT wait for the developer (no status change, no
    pause). It captures the failure's evidence, continues, and finishes; the
    brain phase repairs it later."""
    authoring_run_id = _make_authoring_run()
    start_url = f"{fixture_portal_url}/login.html"

    fixture_portal_server.set_drift_mode(True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            page = browser.new_page()
            page.goto(start_url)
            result = run_replay_phase(
                page, _login_trajectory(), authoring_run_id, threading.Event(), threading.Event(),
            )
            browser.close()
    finally:
        fixture_portal_server.set_drift_mode(False)

    assert result is not None
    assert len(result.steps) == 4  # the failed step is still carried for the brain
    assert result.steps[3].ref_shot_after is not None  # failure evidence still captured

    with session_scope() as session:
        run = session.get(AuthoringRun, authoring_run_id)
        assert run.status == "running"  # never entered waiting_for_developer


def test_run_replay_phase_returns_none_when_stopped_mid_replay(fixture_portal_url):
    authoring_run_id = _make_authoring_run()
    start_url = f"{fixture_portal_url}/login.html"
    stop_event = threading.Event()
    stop_event.set()  # stopped before the replay starts

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(start_url)
        result = run_replay_phase(
            page, _login_trajectory(), authoring_run_id, threading.Event(), stop_event,
        )
        browser.close()

    assert result is None


def test_run_replay_phase_executes_codegen_popup_steps_on_the_popup(fixture_portal_url):
    """A codegen-driven trajectory that opens a popup and clicks inside it
    must replay end to end — popup steps act on the popup, never the main page."""
    authoring_run_id = _make_authoring_run()
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

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/popup_trigger.html")
        result = run_replay_phase(
            page, trajectory, authoring_run_id, threading.Event(), threading.Event(),
        )
        browser.close()

    assert result is not None
    assert len(result.steps) == 2
    with session_scope() as session:
        run = session.get(AuthoringRun, authoring_run_id)
        assert run.status == "running"


def test_apply_stored_codegen_selectors_rebuilds_the_trajectory_from_the_script():
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "click", [{"selector": "#old-btn", "rank": 1}], None, "none"),
            TrajectoryStep(1, "fill", [{"selector": "#old-input", "rank": 1}], "demo", "none"),
        )
    )
    prefix = f"recordings/{uuid.uuid4()}"
    codegen_dir = Path(settings.ensure_run_root()) / prefix
    codegen_dir.mkdir(parents=True, exist_ok=True)
    (codegen_dir / "codegen.py").write_text(
        'page.goto("https://portal.example/login")\n'
        'page.locator("text=Submit").click()\n'
        'page.locator("[id=\\"u\\"]").fill("demo")\n'
    )

    enriched, applied = apply_stored_codegen_selectors(trajectory, prefix)

    assert applied is True
    assert [s.kind for s in enriched.steps] == ["goto", "click", "fill"]
    assert enriched.steps[0].selector_candidates == [{"selector": "https://portal.example/login", "rank": 0}]
    assert enriched.steps[1].selector_candidates == [
        {"selector": 'codegen_expr:page.locator("text=Submit")', "rank": 0}
    ]
    assert enriched.steps[2].selector_candidates == [
        {"selector": 'codegen_expr:page.locator("[id=\\"u\\"]")', "rank": 0}
    ]
    assert enriched.steps[2].value == "demo"


def test_apply_stored_codegen_selectors_is_a_noop_without_a_script():
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "click", [{"selector": "#old-btn", "rank": 1}], None, "none"),
        )
    )
    prefix = f"recordings/{uuid.uuid4()}"

    enriched, applied = apply_stored_codegen_selectors(trajectory, prefix)

    assert applied is False
    assert enriched is trajectory


def test_apply_stored_codegen_selectors_is_a_noop_for_an_unsupported_script():
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "click", [{"selector": "#old-btn", "rank": 1}], None, "none"),
        )
    )
    prefix = f"recordings/{uuid.uuid4()}"
    codegen_dir = Path(settings.ensure_run_root()) / prefix
    codegen_dir.mkdir(parents=True, exist_ok=True)
    (codegen_dir / "codegen.py").write_text('expect(page.locator("text=Welcome")).to_be_visible()\n')

    enriched, applied = apply_stored_codegen_selectors(trajectory, prefix)

    assert applied is False
    assert enriched is trajectory


def test_run_replay_phase_captures_expects_popup_trigger_from_codegen(fixture_portal_url):
    """A codegen script whose trigger click lives inside `with
    page.expect_popup()` rebuilds into a step that REQUIRES the popup — the
    replay then proves it opened and marks the step on-track."""
    from atlas_engine.capture.codegen import build_codegen_trajectory, parse_codegen_actions

    codegen = (
        f'page.goto("{fixture_portal_url}/popup_trigger.html")\n'
        "with page.expect_popup() as page1_info:\n"
        '    page.get_by_role("link", name="Open report").click()\n'
        "page1 = page1_info.value\n"
        'page1.get_by_role("button", name="Generate report").click()\n'
    )
    trajectory = build_codegen_trajectory(parse_codegen_actions(codegen))
    assert trajectory is not None
    assert trajectory.steps[1].expects_popup is True
    assert trajectory.steps[2].expects_popup is False

    authoring_run_id = _make_authoring_run()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(f"{fixture_portal_url}/popup_trigger.html")
        result = run_replay_phase(
            page, trajectory, authoring_run_id, threading.Event(), threading.Event(),
        )
        browser.close()

    assert result is not None
    assert len(result.steps) == 3
    assert result.steps[1].ref_shot_after is not None  # popup trigger evidence captured