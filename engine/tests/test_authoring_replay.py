import threading
import time
import uuid

from playwright.sync_api import sync_playwright

from atlas_engine.authoring.replay import _splice_developer_actions, _wait_for_resume_or_stop, run_replay_phase
from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.capture.events import RecordedAction
from atlas_engine.db import session_scope
from atlas_engine.db.models import AuthoringRun, AuthoringRunEvent, Organization, PortalProfile, Recording


def test_wait_for_resume_or_stop_returns_resumed_when_resume_event_set():
    resume_event = threading.Event()
    stop_event = threading.Event()

    def _resume_soon():
        time.sleep(0.05)
        resume_event.set()

    threading.Thread(target=_resume_soon).start()

    result = _wait_for_resume_or_stop(resume_event, stop_event, poll_interval=0.02)

    assert result == "resumed"


def test_wait_for_resume_or_stop_returns_stopped_when_stop_event_set():
    resume_event = threading.Event()
    stop_event = threading.Event()

    def _stop_soon():
        time.sleep(0.05)
        stop_event.set()

    threading.Thread(target=_stop_soon).start()

    result = _wait_for_resume_or_stop(resume_event, stop_event, poll_interval=0.02)

    assert result == "stopped"


def test_wait_for_resume_or_stop_clears_resume_event_before_returning():
    resume_event = threading.Event()
    stop_event = threading.Event()
    resume_event.set()

    result = _wait_for_resume_or_stop(resume_event, stop_event, poll_interval=0.02)

    assert result == "resumed"
    assert not resume_event.is_set()


def test_splice_developer_actions_replaces_the_failed_step():
    steps = [
        TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
        TrajectoryStep(1, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
        TrajectoryStep(2, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
    ]
    developer_actions = [
        RecordedAction(
            index=0, kind="click", selector_candidates=[{"selector": "#run-report-btn", "rank": 0}], value=None,
            url_before="https://x/dashboard.html", url_after="https://x/dashboard.html",
            dom_before="<p>before</p>", dom_after="<p>after</p>",
        ),
    ]

    result = _splice_developer_actions(steps, 2, developer_actions)

    assert len(result) == 3
    assert result[0].index == 0 and result[0].kind == "fill"
    assert result[1].index == 1 and result[1].kind == "click"
    assert result[2].index == 2
    assert result[2].selector_candidates == [{"selector": "#run-report-btn", "rank": 0}]
    assert result[2].expected_effect == "dom_changed"


def test_splice_developer_actions_supports_multiple_actions_for_one_failed_step():
    steps = [
        TrajectoryStep(0, "click", [{"selector": "#a", "rank": 0}], None, "none"),
        TrajectoryStep(1, "click", [{"selector": "#b", "rank": 0}], None, "none"),
    ]
    developer_actions = [
        RecordedAction(
            index=0, kind="click", selector_candidates=[{"selector": "#c1", "rank": 0}], value=None,
            url_before="https://x/", url_after="https://x/", dom_before="1", dom_after="2",
        ),
        RecordedAction(
            index=1, kind="click", selector_candidates=[{"selector": "#c2", "rank": 0}], value=None,
            url_before="https://x/", url_after="https://x/", dom_before="2", dom_after="2",
        ),
    ]

    result = _splice_developer_actions(steps, 1, developer_actions)

    assert [s.index for s in result] == [0, 1, 2]
    assert result[1].selector_candidates == [{"selector": "#c1", "rank": 0}]
    assert result[1].expected_effect == "dom_changed"
    assert result[2].selector_candidates == [{"selector": "#c2", "rank": 0}]
    assert result[2].expected_effect == "none"


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
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", "none"),
            TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
            TrajectoryStep(3, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
        )
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(start_url)
        result = run_replay_phase(
            page, trajectory, authoring_run_id, threading.Event(), threading.Event(),
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


def test_run_replay_phase_pauses_for_developer_then_stops_cleanly(fixture_portal_url, fixture_portal_server):
    authoring_run_id = _make_authoring_run()
    start_url = f"{fixture_portal_url}/login.html"
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", "none"),
            TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
            TrajectoryStep(3, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
        )
    )

    fixture_portal_server.set_drift_mode(True)
    try:
        resume_event = threading.Event()
        stop_event = threading.Event()
        result_holder = {}

        # Playwright's sync API pins its dispatcher greenlet to whichever OS
        # thread calls sync_playwright() — calling page methods from any
        # other thread raises greenlet.error: "cannot switch to a different
        # thread". So the entire browser lifecycle (including the
        # run_replay_phase call, which drives the page via run_step) has to
        # live on the worker thread; the main thread only ever touches the
        # database and the two threading.Events, never `page` or `browser`.
        def _run():
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=False)
                page = browser.new_page()
                page.goto(start_url)
                result_holder["trajectory"] = run_replay_phase(
                    page, trajectory, authoring_run_id, resume_event, stop_event,
                )
                browser.close()

        worker = threading.Thread(target=_run)
        worker.start()

        deadline = time.time() + 15
        paused = False
        while time.time() < deadline:
            with session_scope() as session:
                run = session.get(AuthoringRun, authoring_run_id)
                if run.status == "waiting_for_developer":
                    paused = True
                    break
            time.sleep(0.1)
        assert paused, "replay never paused for developer on the drifted selector"

        stop_event.set()
        worker.join(timeout=15)

        assert result_holder["trajectory"] is None
    finally:
        fixture_portal_server.set_drift_mode(False)
