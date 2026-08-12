from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.brain.serialize import trajectory_from_dict, trajectory_to_dict


def test_trajectory_round_trips_through_dict():
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#u", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "click", [{"selector": "#b", "rank": 0}], None, "dom_changed"),
        )
    )

    data = trajectory_to_dict(trajectory)
    restored = trajectory_from_dict(data)

    assert restored == trajectory
    assert data == {
        "steps": [
            {
                "index": 0, "kind": "fill",
                "selector_candidates": [{"selector": "#u", "rank": 0}],
                "value": "demo", "expected_effect": "none",
            },
            {
                "index": 1, "kind": "click",
                "selector_candidates": [{"selector": "#b", "rank": 0}],
                "value": None, "expected_effect": "dom_changed",
            },
        ]
    }
