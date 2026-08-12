from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep


def trajectory_to_dict(trajectory: ReferenceTrajectory) -> dict:
    return {
        "steps": [
            {
                "index": step.index,
                "kind": step.kind,
                "selector_candidates": step.selector_candidates,
                "value": step.value,
                "expected_effect": step.expected_effect,
            }
            for step in trajectory.steps
        ]
    }


def trajectory_from_dict(data: dict) -> ReferenceTrajectory:
    steps = tuple(
        TrajectoryStep(
            index=item["index"],
            kind=item["kind"],
            selector_candidates=item["selector_candidates"],
            value=item["value"],
            expected_effect=item["expected_effect"],
        )
        for item in data["steps"]
    )
    return ReferenceTrajectory(steps=steps)
