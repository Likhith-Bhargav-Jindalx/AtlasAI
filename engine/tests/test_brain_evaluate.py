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
        resolved_selector="#btn",
    )
    assert outcome.on_track is True


def test_evaluate_on_track_when_only_the_url_changed_even_without_a_prior_url_effect():
    """A URL-only live change always passes — the previous app never REQUIRES
    a URL change; it only treats the recorded DOM change as the reference for
    the silent-no-op gate. So a step recorded as dom_changed that live changed
    only the URL is still on-track (the page DID change)."""
    outcome = evaluate(
        _step("dom_changed"),
        url_before="https://x/login", url_after="https://x/dashboard",
        dom_before="<a/>", dom_after="<a/>",
        resolved_selector="#btn",
    )
    assert outcome.on_track is True


def test_evaluate_off_track_on_silent_no_op_when_dom_change_was_expected():
    """The silent-no-op gate: the reference changed the page here
    (expected_effect == "dom_changed" proxies the recorded effects.domChanged),
    but live changed NOTHING — no DOM, no URL, no popup."""
    outcome = evaluate(
        _step("dom_changed"),
        url_before="https://x/login", url_after="https://x/login",
        dom_before="<a/>", dom_after="<a/>",
        resolved_selector="#btn",
    )
    assert outcome.on_track is False


def test_evaluate_on_track_when_a_popup_opened_for_a_silent_no_op():
    outcome = evaluate(
        _step("dom_changed"),
        url_before="https://x/login", url_after="https://x/login",
        dom_before="<a/>", dom_after="<a/>",
        resolved_selector="#btn",
        popup_opened=True,
    )
    assert outcome.on_track is True


def test_evaluate_on_track_when_dom_changed_as_expected():
    outcome = evaluate(
        _step("dom_changed"),
        url_before="https://x/dashboard", url_after="https://x/dashboard",
        dom_before="<a/>", dom_after="<b/>",
        resolved_selector="#btn",
    )
    assert outcome.on_track is True


def test_evaluate_on_track_for_none_effect_regardless_of_change():
    outcome = evaluate(
        _step("none"),
        url_before="https://x/login", url_after="https://x/login",
        dom_before="<a/>", dom_after="<a/>",
        resolved_selector="#btn",
    )
    assert outcome.on_track is True


def test_evaluate_off_track_when_hover_has_no_dom_change_effect():
    """Cursor-movement gestures (hover) legitimately change nothing — the gate
    must not loop them forever, even when the reference shows a change."""
    step = TrajectoryStep(
        index=0, kind="hover", selector_candidates=[{"selector": "#btn", "rank": 1}],
        value=None, expected_effect="dom_changed",
    )
    outcome = evaluate(
        step,
        url_before="https://x/login", url_after="https://x/login",
        dom_before="<a/>", dom_after="<a/>",
        resolved_selector="#btn",
    )
    assert outcome.on_track is True


def test_evaluate_off_track_on_error():
    outcome = evaluate(
        _step("dom_changed"),
        url_before="https://x/login", url_after="https://x/login",
        dom_before="<a/>", dom_after="<a/>",
        resolved_selector="#btn",
        error="locator timeout",
    )
    assert outcome.on_track is False


def test_evaluate_off_track_when_popup_was_expected_but_none_opened():
    step = TrajectoryStep(
        index=0, kind="click", selector_candidates=[{"selector": "#btn", "rank": 1}],
        value=None, expected_effect="none", expects_popup=True,
    )
    outcome = evaluate(
        step,
        url_before="https://x/login", url_after="https://x/login",
        dom_before="<a/>", dom_after="<b/>",
        resolved_selector="#btn",
    )
    assert outcome.on_track is False


def test_evaluate_on_track_inside_an_iframe_is_a_blind_spot():
    """An action inside an iframe changes content page.content() can't see —
    where we're blind, a no-error action is accepted (mirrors the old app)."""
    step = TrajectoryStep(
        index=0, kind="click", selector_candidates=[{"selector": "#btn", "rank": 1}],
        value=None, expected_effect="dom_changed",
    )
    outcome = evaluate(
        step,
        url_before="https://x/login", url_after="https://x/login",
        dom_before="<a/>", dom_after="<a/>",
        resolved_selector="#btn",
        enters_iframe=True,
    )
    assert outcome.on_track is True


def test_evaluate_fill_is_on_track_when_the_field_holds_the_value():
    step = TrajectoryStep(
        index=0, kind="fill", selector_candidates=[{"selector": "#u", "rank": 1}],
        value="demo", expected_effect="none",
    )
    outcome = evaluate(
        step,
        url_before="https://x/login", url_after="https://x/login",
        dom_before="<a/>", dom_after="<a/>",
        resolved_selector="#u",
        input_value="demo",
    )
    assert outcome.on_track is True