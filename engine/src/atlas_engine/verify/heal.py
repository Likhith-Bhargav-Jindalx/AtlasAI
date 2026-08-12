from atlas_engine.brain.models import ReferenceTrajectory


def _primary_selector(trajectory: ReferenceTrajectory, index: int) -> str:
    candidates = trajectory.steps[index].selector_candidates
    return candidates[0]["selector"] if candidates else ""


def compute_trajectory_diff(old: ReferenceTrajectory, new: ReferenceTrajectory) -> list[dict]:
    diff = []
    for i in range(len(old.steps)):
        old_selector = _primary_selector(old, i)
        new_selector = _primary_selector(new, i)
        if old_selector != new_selector:
            diff.append(
                {"step_index": i, "field": "selector", "old_value": old_selector, "new_value": new_selector}
            )
    return diff
