import threading
import uuid
from pathlib import Path
from unittest.mock import Mock

import pytest

from atlas_engine.config import settings
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, Recording, User, VideoAsset
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


def test_start_live_capture_uses_a_custom_persist_fn(fixture_portal_url):
    """The desktop exe's remote-submit mode passes submit_capture_to_remote
    as persist_fn instead of the default persist_recording — this proves
    the substitution actually takes effect end to end (the real capture
    still runs; only what happens with the finished artifacts differs),
    not just that the parameter is accepted."""
    with session_scope() as session:
        org = Organization(name="Custom Persist Org", slug=f"custom-persist-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    start_url = f"{fixture_portal_url}/login.html"
    calls = []

    def fake_persist(org_id_arg, start_url_arg, actions, **kwargs):
        calls.append((org_id_arg, start_url_arg))
        return uuid.uuid4()

    capture_session_id = start_live_capture(org_id, start_url, persist_fn=fake_persist)
    recording_id = stop_live_capture(capture_session_id)

    assert calls == [(org_id, start_url)]
    with session_scope() as session:
        # The fake never touched the database — proves the default,
        # DB-writing persist_recording was genuinely not called.
        assert session.get(Recording, recording_id) is None


def test_stop_live_capture_stores_a_native_codegen_script(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Codegen Capture Org", slug=f"codegen-capture-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    start_url = f"{fixture_portal_url}/login.html"

    capture_session_id = start_live_capture(org_id, start_url)
    recording_id = stop_live_capture(capture_session_id)

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        codegen_path = Path(settings.run_root) / recording.capture_blob_prefix / "codegen.py"

    assert codegen_path.exists()
    text = codegen_path.read_text()
    assert text.strip()  # the recorder emitted the recorded navigation at least
    assert "__atlasGrabBtn" not in text
    assert "Extract this page" not in text


def test_stop_live_capture_raises_for_an_unknown_session():
    with pytest.raises(ValueError, match="No active capture session"):
        stop_live_capture(uuid.uuid4())


def test_stop_live_capture_stays_retryable_after_a_timeout(fixture_portal_url):
    """Regression: a slow persist_fn (e.g. a real Azure Blob upload over a
    real network) can legitimately outlast a short /stop timeout. The first
    call must time out WITHOUT losing the session, so a second /stop call
    for the same capture_session_id (the user clicking "Stop and review"
    again after seeing an error) finds it still registered and just keeps
    waiting, instead of raising "No active capture session" for a recording
    that was never actually lost -- the exact bug this reproduces."""
    with session_scope() as session:
        org = Organization(name="Retry Capture Org", slug=f"retry-capture-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    start_url = f"{fixture_portal_url}/login.html"
    release_persist = threading.Event()

    def slow_persist(org_id_arg, start_url_arg, actions, **kwargs):
        release_persist.wait(timeout=10)
        return uuid.uuid4()

    capture_session_id = start_live_capture(org_id, start_url, persist_fn=slow_persist)

    with pytest.raises(TimeoutError):
        stop_live_capture(capture_session_id, timeout=0.5)

    release_persist.set()
    recording_id = stop_live_capture(capture_session_id, timeout=10)
    assert recording_id is not None


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
    page = Mock()  # page.title() succeeds -- browser is genuinely still alive

    disconnected = _wait_for_stop_or_disconnect(stop_event, page, poll_interval=0.01)

    assert disconnected is False
    # A stop_event that's already set must still be verified against a real
    # liveness probe before trusting "not disconnected" -- see the test
    # below for the race this guards against.
    page.title.assert_called_once()


def test_wait_for_stop_or_disconnect_detects_disconnect_even_if_stop_event_was_already_set():
    """Reproduces the reported hang: a human closes the browser window and
    only then clicks Stop, so stop_event is already set by the time this
    function is called. The old implementation trusted stop_event blindly
    and never probed the page, so callers (CaptureSession.__exit__,
    finalize_video) went on to make live Playwright calls against an
    already-dead page -- which is exactly what hung for minutes in
    production instead of failing fast."""
    from atlas_engine.live_capture import _wait_for_stop_or_disconnect

    stop_event = threading.Event()
    stop_event.set()
    page = Mock()
    page.title.side_effect = Exception("Target page, context or browser has been closed")

    disconnected = _wait_for_stop_or_disconnect(stop_event, page, poll_interval=0.01)

    assert disconnected is True


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


def test_resolve_page_index_identifies_main_and_extra_pages():
    from atlas_engine.live_capture import _resolve_page_index

    main_page = object()
    extra_page_1 = object()
    extra_page_2 = object()
    extra_captures = [(extra_page_1, None, None), (extra_page_2, None, None)]

    assert _resolve_page_index(main_page, main_page, extra_captures) == 0
    assert _resolve_page_index(extra_page_1, main_page, extra_captures) == 1
    assert _resolve_page_index(extra_page_2, main_page, extra_captures) == 2
    assert _resolve_page_index(object(), main_page, extra_captures) is None


def test_start_live_capture_stores_user_id(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Live Capture User Org", slug=f"live-capture-user-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        user = User(org_id=org.id, email="live@example.com", password_hash="x", job_function="operations_agent")
        session.add(user)
        session.flush()
        user_id = str(user.id)

    capture_session_id = start_live_capture(org_id, f"{fixture_portal_url}/login.html", user_id=user_id)
    recording_id = stop_live_capture(capture_session_id)

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        assert str(recording.user_id) == user_id
