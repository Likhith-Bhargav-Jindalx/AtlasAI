import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.capture.events import RecordedAction
from atlas_engine.pending_recordings import get_pending_dir, write_pending_recording


def _action(index: int = 0) -> RecordedAction:
    return RecordedAction(
        index=index, kind="click", selector_candidates=[{"selector": "#go"}], value=None,
        url_before="https://x.test/a", url_after="https://x.test/b",
        dom_before="<html></html>", dom_after="<html></html>", timestamp_ms=1000,
    )


def test_list_pending_recordings_endpoint_includes_a_newly_written_one():
    pending_id = write_pending_recording("org-1", "https://x.test", [_action()])

    client = TestClient(app)
    response = client.get("/pending-recordings")

    assert response.status_code == 200
    assert any(item["id"] == str(pending_id) for item in response.json())


def test_get_pending_recording_endpoint_returns_steps():
    pending_id = write_pending_recording("org-1", "https://x.test", [_action()], video_started_at_ms=1000)

    client = TestClient(app)
    response = client.get(f"/pending-recordings/{pending_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(pending_id)
    assert body["pages"][0]["steps"][0]["caption"] == "Click #go"


def test_get_pending_recording_endpoint_404s_for_an_unknown_id():
    client = TestClient(app)
    response = client.get(f"/pending-recordings/{uuid.uuid4()}")
    assert response.status_code == 404


def test_get_pending_recording_video_endpoint_streams_the_local_file(tmp_path):
    video_path = tmp_path / "video.webm"
    video_path.write_bytes(b"fake-webm-bytes")
    pending_id = write_pending_recording(
        "org-1", "https://x.test", [_action()], video_path=video_path,
        video_started_at_ms=0, video_ended_at_ms=1000,
    )

    client = TestClient(app)
    response = client.get(f"/pending-recordings/{pending_id}/video/0")

    assert response.status_code == 200
    assert response.content == b"fake-webm-bytes"


def test_discard_pending_recording_endpoint_removes_it():
    pending_id = write_pending_recording("org-1", "https://x.test", [_action()])
    pending_dir = get_pending_dir(str(pending_id))

    client = TestClient(app)
    response = client.post(f"/pending-recordings/{pending_id}/discard")

    assert response.status_code == 204
    assert not pending_dir.exists()


def test_upload_pending_recording_endpoint_persists_and_returns_recording_id():
    from atlas_engine.db import session_scope
    from atlas_engine.db.models import Organization, Recording

    with session_scope() as session:
        org = Organization(name="Pending API Org", slug=f"pending-api-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    pending_id = write_pending_recording(org_id, "https://x.test", [_action()])

    client = TestClient(app)
    response = client.post(f"/pending-recordings/{pending_id}/upload")

    assert response.status_code == 200
    recording_id = response.json()["recording_id"]
    with session_scope() as session:
        assert session.get(Recording, uuid.UUID(recording_id)).status == "captured"


def test_upload_pending_recording_endpoint_surfaces_the_real_error(monkeypatch):
    pending_id = write_pending_recording("org-1", "https://x.test", [_action()])

    def _raise(*args, **kwargs):
        raise RuntimeError("blob upload exploded")

    monkeypatch.setattr("atlas_engine.recording_service.persist_recording", _raise)

    client = TestClient(app)
    response = client.post(f"/pending-recordings/{pending_id}/upload")

    assert response.status_code == 500
    assert "blob upload exploded" in response.json()["detail"]
