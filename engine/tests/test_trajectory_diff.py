from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.verify.heal import compute_trajectory_diff


def test_compute_trajectory_diff_flags_changed_selector():
    old = ReferenceTrajectory(
        steps=(TrajectoryStep(0, "click", [{"selector": "#old-btn", "rank": 0}], None, "dom_changed"),)
    )
    new = ReferenceTrajectory(
        steps=(TrajectoryStep(0, "click", [{"selector": "#new-btn", "rank": 0}], None, "dom_changed"),)
    )

    diff = compute_trajectory_diff(old, new)

    assert diff == [{
        "step_index": 0,
        "field": "selector",
        "old_value": "#old-btn",
        "new_value": "#new-btn",
        "before_screenshot": None,
        "after_screenshot": None,
    }]


def test_compute_trajectory_diff_empty_when_identical():
    trajectory = ReferenceTrajectory(
        steps=(TrajectoryStep(0, "click", [{"selector": "#btn", "rank": 0}], None, "dom_changed"),)
    )

    assert compute_trajectory_diff(trajectory, trajectory) == []


def test_compute_trajectory_diff_attaches_before_and_after_screenshots():
    from atlas_engine.brain.evaluate import StepOutcome

    old = ReferenceTrajectory(steps=(
        TrajectoryStep(index=0, kind="click", selector_candidates=[{"selector": "#old-a", "rank": 0}], value=None, expected_effect="none"),
        TrajectoryStep(index=1, kind="click", selector_candidates=[{"selector": "#old-b", "rank": 0}], value=None, expected_effect="none"),
    ))
    new = ReferenceTrajectory(steps=(
        TrajectoryStep(index=0, kind="click", selector_candidates=[{"selector": "#old-a", "rank": 0}], value=None, expected_effect="none"),
        TrajectoryStep(index=1, kind="click", selector_candidates=[{"selector": "#new-b", "rank": 0}], value=None, expected_effect="none"),
    ))
    outcomes = [
        StepOutcome(step_index=0, on_track=True, url_after="u", dom_after="d", resolved_selector="#old-a", shot_after="/tmp/step0.png"),
        StepOutcome(step_index=1, on_track=True, url_after="u", dom_after="d", resolved_selector="#new-b", shot_after="/tmp/step1.png"),
    ]

    diff = compute_trajectory_diff(old, new, outcomes=outcomes)

    assert len(diff) == 1
    assert diff[0]["step_index"] == 1
    assert diff[0]["before_screenshot"] == "/tmp/step0.png"
    assert diff[0]["after_screenshot"] == "/tmp/step1.png"


def test_compute_trajectory_diff_without_outcomes_omits_screenshots():
    old = ReferenceTrajectory(steps=(
        TrajectoryStep(index=0, kind="click", selector_candidates=[{"selector": "#old-a", "rank": 0}], value=None, expected_effect="none"),
    ))
    new = ReferenceTrajectory(steps=(
        TrajectoryStep(index=0, kind="click", selector_candidates=[{"selector": "#new-a", "rank": 0}], value=None, expected_effect="none"),
    ))
    diff = compute_trajectory_diff(old, new)
    assert diff[0]["before_screenshot"] is None
    assert diff[0]["after_screenshot"] is None
