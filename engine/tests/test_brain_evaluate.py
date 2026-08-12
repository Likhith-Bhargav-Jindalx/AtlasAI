from atlas_engine.brain.evaluate import evaluate
from atlas_engine.brain.models import TrajectoryStep


def _step(expected_effect):
    return TrajectoryStep(
        index=0, kind="click", selector_candidates=[{"selector": "#btn", "rank": 1}],
        value=None, expected_effect=expected_effect,
    )


def test_evaluate_on_track_when_url_changed_as_expected():
    outcome = evaluate(
        _step("url_changed"),
        url_before="https://x/login", url_after="https://x/dashboard",
        dom_before="<a/>", dom_after="<a/>",
    )
    assert outcome.on_track is True


def test_evaluate_off_track_when_nothing_changed_but_change_was_expected():
    outcome = evaluate(
        _step("url_changed"),
        url_before="https://x/login", url_after="https://x/login",
        dom_before="<a/>", dom_after="<a/>",
    )
    assert outcome.on_track is False


def test_evaluate_on_track_when_dom_changed_as_expected():
    outcome = evaluate(
        _step("dom_changed"),
        url_before="https://x/dashboard", url_after="https://x/dashboard",
        dom_before="<a/>", dom_after="<b/>",
    )
    assert outcome.on_track is True


def test_evaluate_on_track_for_none_effect_regardless_of_change():
    outcome = evaluate(
        _step("none"),
        url_before="https://x/login", url_after="https://x/login",
        dom_before="<a/>", dom_after="<a/>",
    )
    assert outcome.on_track is True
