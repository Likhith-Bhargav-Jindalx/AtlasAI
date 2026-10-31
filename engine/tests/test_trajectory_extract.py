from atlas_engine.capture.events import RecordedAction
from atlas_engine.trajectory.extract import extract_trajectory


def _action(index, kind, value, url_before, url_after, dom_before, dom_after, page_index=0, operator_note=None, modifiers=None):
    return RecordedAction(
        index=index,
        kind=kind,
        selector_candidates=[{"selector": f"#field-{index}", "rank": 1}],
        value=value,
        url_before=url_before,
        url_after=url_after,
        dom_before=dom_before,
        dom_after=dom_after,
        page_index=page_index,
        operator_note=operator_note,
        modifiers=modifiers or [],
    )


def test_extract_trajectory_is_pure_and_deterministic():
    actions = [
        _action(0, "fill", "demo", "https://x/login", "https://x/login", "<a/>", "<a/>"),
        _action(1, "click", None, "https://x/login", "https://x/dashboard", "<a/>", "<b/>"),
    ]

    trajectory_1 = extract_trajectory(actions)
    trajectory_2 = extract_trajectory(actions)

    assert trajectory_1 == trajectory_2
    assert len(trajectory_1.steps) == 2


def test_extract_trajectory_infers_expected_effect_per_step():
    actions = [
        _action(0, "fill", "demo", "https://x/login", "https://x/login", "<a/>", "<a/>"),
        _action(1, "click", None, "https://x/login", "https://x/dashboard", "<a/>", "<b/>"),
        _action(2, "click", None, "https://x/dashboard", "https://x/dashboard", "<b/>", "<c/>"),
    ]

    trajectory = extract_trajectory(actions)

    assert trajectory.steps[0].expected_effect == "none"  # fill with no navigation/DOM change
    assert trajectory.steps[1].expected_effect == "dom_changed"  # a navigation changes the DOM too
    assert trajectory.steps[2].expected_effect == "dom_changed"


def test_extract_trajectory_carries_selector_candidates_and_value_forward():
    actions = [_action(0, "fill", "demo-pass", "https://x/login", "https://x/login", "<a/>", "<a/>")]

    trajectory = extract_trajectory(actions)

    assert trajectory.steps[0].selector_candidates == [{"selector": "#field-0", "rank": 1}]
    assert trajectory.steps[0].value == "demo-pass"


def test_extract_trajectory_stamps_page_index_from_the_recorded_action():
    actions = [
        _action(0, "click", None, "https://x/a", "https://x/a", "<a/>", "<a/>", page_index=0),
        _action(1, "click", None, "https://x/a", "https://x/a", "<a/>", "<b/>", page_index=1),
    ]

    trajectory = extract_trajectory(actions)

    assert trajectory.steps[0].page_index == 0
    assert trajectory.steps[1].page_index == 1


def test_extract_trajectory_stamps_operator_note_from_the_recorded_action():
    actions = [
        _action(0, "click", None, "https://x/a", "https://x/a", "<a/>", "<a/>", operator_note="per payer instructions"),
        _action(1, "click", None, "https://x/a", "https://x/a", "<a/>", "<b/>"),
    ]

    trajectory = extract_trajectory(actions)

    assert trajectory.steps[0].operator_note == "per payer instructions"
    assert trajectory.steps[1].operator_note is None


def test_extract_trajectory_stamps_modifiers_from_the_recorded_action():
    actions = [
        _action(0, "click", None, "https://x/a", "https://x/a", "<a/>", "<a/>", modifiers=["Meta"]),
        _action(1, "click", None, "https://x/a", "https://x/a", "<a/>", "<b/>"),
    ]

    trajectory = extract_trajectory(actions)

    assert trajectory.steps[0].modifiers == ("Meta",)
    assert trajectory.steps[1].modifiers == ()
