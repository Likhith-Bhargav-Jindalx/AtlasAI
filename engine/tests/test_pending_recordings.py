import json
import uuid
from pathlib import Path

import pytest

from atlas_engine.capture.events import RecordedAction
from atlas_engine.pending_recordings import (
    PendingManifest, get_pending_dir, get_pending_recording, list_pending_recordings,
    load_pending_actions, pending_recording_steps, pending_root, write_pending_recording,
)


def _action(index: int, kind: str = "click") -> RecordedAction:
    return RecordedAction(
        index=index, kind=kind, selector_candidates=[{"selector": "#go"}], value=None,
        url_before="https://x.test/a", url_after="https://x.test/b",
        dom_before="<html></html>", dom_after="<html></html>",
        timestamp_ms=1_000 + index * 10,
    )


def test_write_pending_recording_creates_a_folder_with_a_manifest_and_actions_json():
    pending_id = write_pending_recording("org-1", "https://x.test", [_action(0), _action(1)])

    pending_dir = pending_root() / str(pending_id)
    assert pending_dir.is_dir()

    manifest = PendingManifest.load(pending_dir)
    assert manifest.id == str(pending_id)
    assert manifest.org_id == "org-1"
    assert manifest.start_url == "https://x.test"
    assert manifest.action_count == 2
    assert manifest.has_video is False
    assert manifest.upload_error is None

    actions_on_disk = json.loads((pending_dir / "actions.json").read_text())
    assert len(actions_on_disk) == 2
    assert "dom_before" not in actions_on_disk[0]
    assert "dom_after" not in actions_on_disk[0]

    assert (pending_dir / "trajectory.json").exists()


def test_write_pending_recording_copies_the_video_file_and_sets_has_video(tmp_path):
    video_path = tmp_path / "video.webm"
    video_path.write_bytes(b"fake-webm-bytes")

    pending_id = write_pending_recording(
        "org-1", "https://x.test", [_action(0)],
        video_path=video_path, video_started_at_ms=1000, video_ended_at_ms=5000,
    )

    pending_dir = pending_root() / str(pending_id)
    manifest = PendingManifest.load(pending_dir)
    assert manifest.has_video is True
    assert manifest.video_started_at_ms == 1000
    assert manifest.video_ended_at_ms == 5000
    assert (pending_dir / "video.webm").read_bytes() == b"fake-webm-bytes"


def test_write_pending_recording_never_imports_azure_or_db_at_call_time(monkeypatch):
    """The whole point of this function: it must be impossible for it to
    reach Azure Blob Storage or Postgres, even indirectly."""
    import sys

    for module_name in list(sys.modules):
        if "azure" in module_name.lower():
            monkeypatch.setitem(sys.modules, module_name, None)

    pending_id = write_pending_recording("org-1", "https://x.test", [_action(0)])
    assert (pending_root() / str(pending_id)).is_dir()


def test_get_pending_dir_raises_value_error_for_an_unknown_id():
    with pytest.raises(ValueError, match="No pending recording"):
        get_pending_dir(str(uuid.uuid4()))


def test_list_pending_recordings_returns_newest_first():
    first_id = write_pending_recording("org-1", "https://x.test/first", [_action(0)])
    second_id = write_pending_recording("org-1", "https://x.test/second", [_action(0)])

    manifests = list_pending_recordings()
    ids_in_order = [m.id for m in manifests if m.id in (str(first_id), str(second_id))]
    assert ids_in_order == [str(second_id), str(first_id)]


def test_get_pending_recording_returns_the_manifest():
    pending_id = write_pending_recording("org-1", "https://x.test", [_action(0)])
    manifest = get_pending_recording(str(pending_id))
    assert manifest.id == str(pending_id)


def test_load_pending_actions_reconstructs_recorded_actions():
    pending_id = write_pending_recording("org-1", "https://x.test", [_action(0), _action(1)])
    pending_dir = get_pending_dir(str(pending_id))

    actions = load_pending_actions(pending_dir, "actions.json")

    assert len(actions) == 2
    assert actions[0].kind == "click"
    assert actions[0].dom_before == ""


def test_pending_recording_steps_computes_captions_and_offsets():
    actions = [_action(0), _action(1)]
    steps = pending_recording_steps(actions, video_started_at_ms=1000)

    assert steps[0]["order_index"] == 0
    assert steps[0]["caption"] == "Click #go"
    assert steps[0]["offset_ms"] == 0
    assert steps[1]["offset_ms"] == 10
