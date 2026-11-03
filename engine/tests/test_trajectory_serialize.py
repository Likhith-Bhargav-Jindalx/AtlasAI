from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.brain.serialize import trajectory_from_dict, trajectory_to_dict


def test_trajectory_round_trips_through_dict():
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(
                0, "fill", [{"selector": "#u", "rank": 0}], "demo", "none",
                identity={"tag": "input", "id": "u"}, ref_shot_before="refs/0-pre.png",
                ref_shot_after="refs/0-post.png",
            ),
            TrajectoryStep(
                1, "click", [{"selector": "#b", "rank": 0}], None, "dom_changed",
                expects_popup=True, page_index=1, operator_note="per payer instructions",
                modifiers=("Meta",), tab_origin="popup",
            ),
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
                "identity": {"tag": "input", "id": "u"},
                "ref_shot_before": "refs/0-pre.png",
                "ref_shot_after": "refs/0-post.png",
                "expects_popup": False, "expects_download": False,
                "wait_ceiling_ms": None, "force_click": False,
                "key": None, "x": None, "y": None, "page_index": 0, "operator_note": None,
                "modifiers": [], "tab_origin": None,
            },
            {
                "index": 1, "kind": "click",
                "selector_candidates": [{"selector": "#b", "rank": 0}],
                "value": None, "expected_effect": "dom_changed",
                "identity": None, "ref_shot_before": None, "ref_shot_after": None,
                "expects_popup": True, "expects_download": False,
                "wait_ceiling_ms": None, "force_click": False,
                "key": None, "x": None, "y": None, "page_index": 1,
                "operator_note": "per payer instructions",
                "modifiers": ["Meta"], "tab_origin": "popup",
            },
        ]
    }


def test_trajectory_from_dict_tolerates_old_records_without_new_fields():
    data = {
        "steps": [
            {
                "index": 0, "kind": "click",
                "selector_candidates": [{"selector": "#a", "rank": 0}],
                "value": None, "expected_effect": "dom_changed",
            }
        ]
    }

    restored = trajectory_from_dict(data)

    assert restored.steps[0].index == 0
    assert restored.steps[0].identity is None
    assert restored.steps[0].expects_popup is False
    assert restored.steps[0].force_click is False
    assert restored.steps[0].page_index == 0
    assert restored.steps[0].operator_note is None
    assert restored.steps[0].modifiers == ()
    assert restored.steps[0].tab_origin is None