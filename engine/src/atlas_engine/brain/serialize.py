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
                "identity": step.identity,
                "ref_shot_before": step.ref_shot_before,
                "ref_shot_after": step.ref_shot_after,
                "expects_popup": step.expects_popup,
                "expects_download": step.expects_download,
                "wait_ceiling_ms": step.wait_ceiling_ms,
                "force_click": step.force_click,
                "key": step.key,
                "x": step.x,
                "y": step.y,
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
            identity=item.get("identity"),
            ref_shot_before=item.get("ref_shot_before"),
            ref_shot_after=item.get("ref_shot_after"),
            expects_popup=item.get("expects_popup", False),
            expects_download=item.get("expects_download", False),
            wait_ceiling_ms=item.get("wait_ceiling_ms"),
            force_click=item.get("force_click", False),
            key=item.get("key"),
            x=item.get("x"),
            y=item.get("y"),
        )
        for item in data["steps"]
    )
    return ReferenceTrajectory(steps=steps)
