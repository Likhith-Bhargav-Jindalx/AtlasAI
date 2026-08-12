import threading
import uuid
from unittest.mock import Mock

import pytest

from atlas_engine.config import settings
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, Recording, VideoAsset
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

        video_asset = session.query(VideoAsset).filter_by(recording_id=recording_id).one()
        assert video_asset.status == "ready"
        assert (settings.run_root / video_asset.storage_ref).stat().st_size > 0


def test_stop_live_capture_raises_for_an_unknown_session():
    with pytest.raises(ValueError, match="No active capture session"):
        stop_live_capture(uuid.uuid4())


def test_wait_for_stop_or_disconnect_detects_a_dead_page():
    from atlas_engine.live_capture import _wait_for_stop_or_disconnect

    stop_event = threading.Event()  # never set
    page = Mock()
    page.title.side_effect = Exception("Target page, context or browser has been closed")

    disconnected = _wait_for_stop_or_disconnect(stop_event, page, poll_interval=0.01)

    assert disconnected is True


def test_wait_for_stop_or_disconnect_returns_false_on_explicit_stop():
    from atlas_engine.live_capture import _wait_for_stop_or_disconnect

    stop_event = threading.Event()
    stop_event.set()  # already stopped, as if /stop had just been called
    page = Mock()

    disconnected = _wait_for_stop_or_disconnect(stop_event, page, poll_interval=0.01)

    assert disconnected is False
    page.title.assert_not_called()


def test_wait_for_stop_or_disconnect_calls_on_tick_every_iteration_and_at_the_end():
    from atlas_engine.live_capture import _wait_for_stop_or_disconnect

    stop_event = threading.Event()
    page = Mock()
    tick_count = {"n": 0}

    def on_tick():
        tick_count["n"] += 1
        if tick_count["n"] == 3:
            stop_event.set()

    disconnected = _wait_for_stop_or_disconnect(stop_event, page, poll_interval=0.01, on_tick=on_tick)

    assert disconnected is False
    assert tick_count["n"] == 4  # 3 ticks inside the loop, 1 more after it exits
