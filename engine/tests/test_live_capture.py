import uuid

import pytest

from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, Recording
from atlas_engine.live_capture import start_live_capture, stop_live_capture


def test_start_and_stop_live_capture_persists_a_recording(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Live Capture Org", slug=f"live-capture-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    start_url = f"{fixture_portal_url}/login.html"

    capture_session_id = start_live_capture(org_id, start_url)
    recording_id = stop_live_capture(capture_session_id)

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        assert recording.status == "captured"
        assert recording.role == "demo"


def test_stop_live_capture_raises_for_an_unknown_session():
    with pytest.raises(ValueError, match="No active capture session"):
        stop_live_capture(uuid.uuid4())
