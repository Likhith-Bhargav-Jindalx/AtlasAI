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

    assert diff == [{"step_index": 0, "field": "selector", "old_value": "#old-btn", "new_value": "#new-btn"}]


def test_compute_trajectory_diff_empty_when_identical():
    trajectory = ReferenceTrajectory(
        steps=(TrajectoryStep(0, "click", [{"selector": "#btn", "rank": 0}], None, "dom_changed"),)
    )

    assert compute_trajectory_diff(trajectory, trajectory) == []
