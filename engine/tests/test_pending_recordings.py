import json
import uuid
from pathlib import Path

from atlas_engine.capture.events import RecordedAction
from atlas_engine.pending_recordings import PendingManifest, pending_root, write_pending_recording


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
