import json
import uuid
from pathlib import Path

from atlas_engine.capture.events import RecordedAction
from atlas_engine.config import settings
from atlas_engine.db import session_scope
from atlas_engine.db.models import (
    Organization,
    PortalProfile,
    Recording,
    ReferenceTrajectory,
    VideoAsset,
    VideoStep,
)
from atlas_engine.recording_service import persist_recording


def test_persist_recording_creates_recording_and_trajectory(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Persist Service Org", slug=f"persist-service-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    actions = [
        RecordedAction(0, "fill", [{"selector": "#username", "rank": 0}], "demo", f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/login.html", "", ""),
        RecordedAction(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/login.html", "", ""),
        RecordedAction(2, "click", [{"selector": "#login-btn", "rank": 0}], None, f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/dashboard.html", "", ""),
    ]

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", actions)

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        assert recording.status == "captured"
        assert recording.role == "demo"
        assert recording.request_id is None

        trajectory_row = session.query(ReferenceTrajectory).filter_by(recording_id=recording_id).one()
        data = json.loads(Path(trajectory_row.trajectory_blob_ref).read_text())
        assert len(data["steps"]) == 3

        profile = session.get(PortalProfile, recording.portal_profile_id)
        assert profile.inferred_login_url == f"{fixture_portal_url}/login.html"
        assert profile.inferred_success_signal == {"url_contains": "/dashboard.html"}


def test_persist_recording_reuses_portal_profile_across_two_recordings(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Reuse Profile Org", slug=f"reuse-profile-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    actions = [
        RecordedAction(0, "click", [{"selector": "#login-btn", "rank": 0}], None, f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/dashboard.html", "", ""),
    ]

    recording_id_1 = persist_recording(org_id, f"{fixture_portal_url}/login.html", actions)
    recording_id_2 = persist_recording(org_id, f"{fixture_portal_url}/login.html", actions)

    with session_scope() as session:
        profile_1 = session.get(Recording, recording_id_1).portal_profile_id
        profile_2 = session.get(Recording, recording_id_2).portal_profile_id
        assert profile_1 == profile_2


def test_persist_recording_advances_open_request_to_submitted(fixture_portal_url):
    from atlas_engine.db.models import RecordingRequest, User
    from atlas_engine.db.repo import get_or_create_portal_profile

    with session_scope() as session:
        org = Organization(name="Request Advance Org", slug=f"request-advance-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        agent = User(org_id=org.id, email=f"agent-{uuid.uuid4()}@example.com", password_hash="x", role="member")
        session.add(agent)
        session.flush()
        profile = get_or_create_portal_profile(session, str(org.id), fixture_portal_url.split("//")[1])
        request = RecordingRequest(
            org_id=org.id, requested_by=agent.id, portal_profile_id=profile.id,
            target_url=f"{fixture_portal_url}/login.html", title="Test request",
        )
        session.add(request)
        session.flush()
        org_id = str(org.id)
        request_id = request.id

    actions = [
        RecordedAction(0, "click", [{"selector": "#login-btn", "rank": 0}], None, f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/dashboard.html", "", ""),
    ]

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", actions, request_id=request_id, role="demo")

    with session_scope() as session:
        assert session.get(Recording, recording_id).request_id == request_id
        assert session.get(RecordingRequest, request_id).status == "submitted"


def test_persist_recording_with_video_creates_video_asset_and_steps(tmp_path, fixture_portal_url):
    video_file = tmp_path / "session.webm"
    video_file.write_bytes(b"fake-webm-bytes")

    with session_scope() as session:
        org = Organization(name="Video Org", slug=f"video-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    start_url = f"{fixture_portal_url}/login.html"
    actions = [
        RecordedAction(
            0, "click", [{"selector": "#login-btn", "rank": 0}], None,
            start_url, f"{fixture_portal_url}/dashboard.html", "", "",
            timestamp_ms=1000,
        ),
    ]

    recording_id = persist_recording(
        org_id, start_url, actions,
        video_path=video_file, video_started_at_ms=500, video_ended_at_ms=2500,
    )

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        assert recording.duration_seconds == 2

        video_asset = session.query(VideoAsset).filter_by(recording_id=recording_id).one()
        assert video_asset.status == "ready"
        assert video_asset.started_at_ms == 500
        assert (settings.run_root / video_asset.storage_ref).read_bytes() == b"fake-webm-bytes"

        step = session.query(VideoStep).filter_by(video_asset_id=video_asset.id).one()
        assert step.offset_ms == 500  # action at 1000ms, video started at 500ms
        assert step.caption_text == "Click #login-btn"


def test_persist_recording_without_video_creates_no_video_asset(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="No Video Org", slug=f"no-video-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])

    with session_scope() as session:
        assert session.query(VideoAsset).filter_by(recording_id=recording_id).one_or_none() is None


def test_persist_recording_ignores_an_empty_video_file(tmp_path, fixture_portal_url):
    video_file = tmp_path / "session.webm"
    video_file.touch()  # exists, but zero bytes — as if the browser died before any frame was muxed

    with session_scope() as session:
        org = Organization(name="Empty Video Org", slug=f"empty-video-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = persist_recording(
        org_id, f"{fixture_portal_url}/login.html", [],
        video_path=video_file, video_started_at_ms=0, video_ended_at_ms=1000,
    )

    with session_scope() as session:
        assert session.query(VideoAsset).filter_by(recording_id=recording_id).one_or_none() is None
        recording = session.get(Recording, recording_id)
        assert recording.duration_seconds is None


def test_create_list_and_claim_recording_request(fixture_portal_url):
    from atlas_engine.db.models import RecordingRequest, User
    from atlas_engine.recording_service import (
        claim_recording_request,
        create_recording_request,
        list_recording_requests,
    )

    with session_scope() as session:
        org = Organization(name="Request Flow Org", slug=f"request-flow-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        agent = User(org_id=org.id, email=f"agent-{uuid.uuid4()}@example.com", password_hash="x", role="member")
        developer = User(org_id=org.id, email=f"dev-{uuid.uuid4()}@example.com", password_hash="x", role="member")
        session.add_all([agent, developer])
        session.flush()
        org_id = str(org.id)
        agent_id = str(agent.id)
        developer_id = developer.id

    request_id = create_recording_request(
        org_id, agent_id, f"{fixture_portal_url}/login.html", "Check eligibility", "See the demo recording"
    )

    open_requests = list_recording_requests(org_id, status="open")
    assert any(r["id"] == str(request_id) for r in open_requests)
    assert all(r["claimed_by"] is None for r in open_requests if r["id"] == str(request_id))

    claim_recording_request(request_id, developer_id)

    with session_scope() as session:
        request = session.get(RecordingRequest, request_id)
        assert request.claimed_by == developer_id
        assert request.claimed_at is not None
