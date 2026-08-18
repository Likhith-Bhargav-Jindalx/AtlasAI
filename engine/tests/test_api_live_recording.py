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


def test_stop_recording_endpoint_404s_for_an_unknown_session():
    client = TestClient(app)
    response = client.post(f"/recordings/{uuid.uuid4()}/stop")
    assert response.status_code == 404
    assert "No active capture session" in response.json()["detail"]


def test_stop_recording_endpoint_surfaces_the_real_error_instead_of_a_bare_500(monkeypatch):
    """A browser-launch/navigation failure inside the live-capture
    background thread only ever surfaces when stop() re-raises
    self.error -- this must reach the caller as a real, readable detail
    message (e.g. what actually broke), not the content-free
    {"error": "internal_server_error"} the global exception handler
    returns for every other unhandled failure."""
    def _raise_browser_launch_failure(capture_session_id, timeout=120.0):
        raise RuntimeError("BrowserType.launch: Executable doesn't exist at /path/to/chromium")

    monkeypatch.setattr("atlas_engine.api.stop_live_capture", _raise_browser_launch_failure)

    client = TestClient(app)
    response = client.post(f"/recordings/{uuid.uuid4()}/stop")
    assert response.status_code == 500
    assert "BrowserType.launch: Executable doesn't exist" in response.json()["detail"]
