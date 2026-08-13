import threading
import time

from atlas_engine.authoring.replay import _splice_developer_actions, _wait_for_resume_or_stop
from atlas_engine.brain.models import TrajectoryStep
from atlas_engine.capture.events import RecordedAction


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
