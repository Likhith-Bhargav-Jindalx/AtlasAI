import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization
from atlas_engine.recording_service import persist_recording


def test_get_recording_video_returns_the_file(tmp_path, fixture_portal_url):
    video_file = tmp_path / "session.webm"
    video_file.write_bytes(b"fake-webm-bytes")

    with session_scope() as session:
        org = Organization(name="Video Serve Org", slug=f"video-serve-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = persist_recording(
        org_id, f"{fixture_portal_url}/login.html", [],
        video_path=video_file, video_started_at_ms=0, video_ended_at_ms=1000,
    )

    client = TestClient(app)
    response = client.get(f"/recordings/{recording_id}/video/0")

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/webm"
    assert response.content == b"fake-webm-bytes"


def test_get_recording_video_404_when_no_video(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="No Video Serve Org", slug=f"no-video-serve-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])

    client = TestClient(app)
    response = client.get(f"/recordings/{recording_id}/video/0")

    assert response.status_code == 404
