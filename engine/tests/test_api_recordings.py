import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.capture.events import RecordedAction
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization
from atlas_engine.recording_service import persist_recording


def test_get_recording_detail_includes_video_steps(tmp_path, fixture_portal_url):
    video_file = tmp_path / "session.webm"
    video_file.write_bytes(b"fake-webm-bytes")

    with session_scope() as session:
        org = Organization(name="Detail Org", slug=f"detail-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    start_url = f"{fixture_portal_url}/login.html"
    actions = [
        RecordedAction(0, "click", [{"selector": "#login-btn", "rank": 0}], None,
                        start_url, f"{fixture_portal_url}/dashboard.html", "", "", timestamp_ms=1000),
    ]
    recording_id = persist_recording(
        org_id, start_url, actions,
        video_path=video_file, video_started_at_ms=500, video_ended_at_ms=2500,
    )

    client = TestClient(app)
    response = client.get(f"/recordings/{recording_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["has_video"] is True
    assert body["duration_seconds"] == 2
    assert len(body["steps"]) == 1
    assert body["steps"][0]["caption"] == "Click #login-btn"
    assert body["steps"][0]["offset_ms"] == 500


def test_get_recording_detail_404_for_unknown_id():
    client = TestClient(app)
    response = client.get(f"/recordings/{uuid.uuid4()}")
    assert response.status_code == 404


def test_list_recordings_returns_newest_first_and_filters_by_status(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="List Recordings Org", slug=f"list-recordings-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    start_url = f"{fixture_portal_url}/login.html"
    older_id = persist_recording(org_id, start_url, [])
    newer_id = persist_recording(org_id, start_url, [])

    with session_scope() as session:
        from atlas_engine.db.models import Recording
        session.get(Recording, older_id).status = "discarded"

    client = TestClient(app)
    response = client.get(f"/recordings?org_id={org_id}")

    assert response.status_code == 200
    ids = [r["id"] for r in response.json()]
    assert ids[0] == str(newer_id)
    assert str(older_id) in ids

    filtered = client.get(f"/recordings?org_id={org_id}&status=discarded")
    filtered_ids = [r["id"] for r in filtered.json()]
    assert filtered_ids == [str(older_id)]
