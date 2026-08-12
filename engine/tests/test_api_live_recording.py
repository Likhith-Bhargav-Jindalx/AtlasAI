import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, Recording


def test_start_and_stop_recording_endpoints(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="API Live Recording Org", slug=f"api-live-recording-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    client = TestClient(app)

    start_response = client.post(
        "/recordings/start",
        json={"org_id": org_id, "start_url": f"{fixture_portal_url}/login.html"},
    )
    assert start_response.status_code == 200
    capture_session_id = start_response.json()["capture_session_id"]

    stop_response = client.post(f"/recordings/{capture_session_id}/stop")
    assert stop_response.status_code == 200
    recording_id = stop_response.json()["recording_id"]

    with session_scope() as session:
        recording = session.get(Recording, uuid.UUID(recording_id))
        assert recording.status == "captured"
