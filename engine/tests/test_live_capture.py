import uuid

import pytest

from atlas_engine.config import settings
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, Recording, VideoAsset
from atlas_engine.live_capture import _LiveCaptureSession, start_live_capture, stop_live_capture


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

        video_asset = session.query(VideoAsset).filter_by(recording_id=recording_id).one()
        assert video_asset.status == "ready"
        assert (settings.run_root / video_asset.storage_ref).stat().st_size > 0


def test_stop_live_capture_raises_for_an_unknown_session():
    with pytest.raises(ValueError, match="No active capture session"):
        stop_live_capture(uuid.uuid4())


def test_handle_disconnect_marks_capture_and_sets_stop_event():
    from atlas_engine.capture.recorder import CaptureSession

    session = _LiveCaptureSession("org-id", "https://example.com", None, "demo")
    capture = CaptureSession.__new__(CaptureSession)  # no real page needed for this check
    capture.browser_disconnected = False

    assert not session.stop_event.is_set()
    session._handle_disconnect(capture)

    assert capture.browser_disconnected is True
    assert session.stop_event.is_set()
