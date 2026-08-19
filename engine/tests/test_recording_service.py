import json
import uuid
from pathlib import Path

import pytest

from atlas_engine.capture.events import RecordedAction
from atlas_engine.config import settings
from atlas_engine.db import session_scope
from atlas_engine.db.models import (
    Organization,
    PageExtraction,
    PortalProfile,
    Recording,
    RecordingRequest,
    ReferenceTrajectory,
    User,
    VideoAsset,
    VideoStep,
)
from atlas_engine.recording_service import (
    build_extraction_specs,
    create_recording_request,
    list_page_extractions,
    persist_recording,
    submit_recording_for_authoring,
    update_recording_details,
)


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
        data = json.loads((settings.run_root / trajectory_row.trajectory_blob_ref).read_text())
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


def test_persist_recording_creates_a_video_asset_per_additional_page(tmp_path, fixture_portal_url):
    from atlas_engine.recording_service import PageCapture

    main_video = tmp_path / "main.webm"
    main_video.write_bytes(b"main-bytes")
    popup_video = tmp_path / "popup.webm"
    popup_video.write_bytes(b"popup-bytes")

    with session_scope() as session:
        org = Organization(name="Multi Page Org", slug=f"multi-page-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    start_url = f"{fixture_portal_url}/login.html"
    main_actions = [
        RecordedAction(0, "click", [{"selector": "#login-btn", "rank": 0}], None,
                        start_url, start_url, "", "", timestamp_ms=1000),
    ]
    popup_actions = [
        RecordedAction(0, "click", [{"selector": "#report-link", "rank": 0}], None,
                        start_url, start_url, "", "", timestamp_ms=5000),
    ]

    recording_id = persist_recording(
        org_id, start_url, main_actions,
        video_path=main_video, video_started_at_ms=0, video_ended_at_ms=2000,
        additional_pages=[
            PageCapture(
                page_index=1, actions=popup_actions,
                video_path=popup_video, video_started_at_ms=4000, video_ended_at_ms=6000,
            ),
        ],
    )

    with session_scope() as session:
        assets = (
            session.query(VideoAsset)
            .filter_by(recording_id=recording_id)
            .order_by(VideoAsset.page_index)
            .all()
        )
        assert [a.page_index for a in assets] == [0, 1]
        assert (settings.run_root / assets[0].storage_ref).read_bytes() == b"main-bytes"
        assert (settings.run_root / assets[1].storage_ref).read_bytes() == b"popup-bytes"

        popup_step = session.query(VideoStep).filter_by(video_asset_id=assets[1].id).one()
        assert popup_step.offset_ms == 1000  # action at 5000ms, popup video started at 4000ms
        assert popup_step.caption_text == "Click #report-link"

        recording = session.get(Recording, recording_id)
        prefix = Path(settings.run_root) / recording.capture_blob_prefix

    main_actions_stored = json.loads((prefix / "actions.json").read_text())
    assert len(main_actions_stored) == 1
    assert main_actions_stored[0]["selector_candidates"] == [{"selector": "#login-btn", "rank": 0}]

    popup_actions_stored = json.loads((prefix / "actions-page1.json").read_text())
    assert len(popup_actions_stored) == 1
    assert popup_actions_stored[0]["selector_candidates"] == [{"selector": "#report-link", "rank": 0}]
    assert popup_actions_stored[0]["kind"] == "click"
    assert "dom_before" not in popup_actions_stored[0]
    assert "dom_after" not in popup_actions_stored[0]


def test_persist_recording_writes_no_extra_actions_file_when_a_popup_page_has_no_actions(tmp_path, fixture_portal_url):
    from atlas_engine.recording_service import PageCapture

    main_video = tmp_path / "main.webm"
    main_video.write_bytes(b"main-bytes")
    popup_video = tmp_path / "popup.webm"
    popup_video.write_bytes(b"popup-bytes")

    with session_scope() as session:
        org = Organization(name="Empty Popup Actions Org", slug=f"empty-popup-actions-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    start_url = f"{fixture_portal_url}/login.html"
    recording_id = persist_recording(
        org_id, start_url, [],
        video_path=main_video, video_started_at_ms=0, video_ended_at_ms=2000,
        additional_pages=[
            PageCapture(page_index=1, actions=[], video_path=popup_video, video_started_at_ms=4000, video_ended_at_ms=6000),
        ],
    )

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        prefix = Path(settings.run_root) / recording.capture_blob_prefix

    assert not (prefix / "actions-page1.json").exists()


def test_persist_recording_stores_codegen_script(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Codegen Org", slug=f"codegen-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    start_url = f"{fixture_portal_url}/login.html"
    recording_id = persist_recording(
        org_id, start_url, [],
        codegen_script='    page.goto("https://portal.example/login")\n    page.fill("#u", "demo")\n',
    )

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        codegen_path = Path(settings.run_root) / recording.capture_blob_prefix / "codegen.py"
        assert codegen_path.read_text() == (
            '    page.goto("https://portal.example/login")\n    page.fill("#u", "demo")\n'
        )


def test_persist_recording_stores_rich_capture_trace_and_har_artifacts(tmp_path, fixture_portal_url):
    """Trace/HAR are captured with the recording and retained next to its
    native codegen so authoring has the same reference evidence as the old
    pipeline, without introducing a duplicate persistence path."""
    with session_scope() as session:
        org = Organization(name="Rich Capture Org", slug=f"rich-capture-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    trace = tmp_path / "trace.zip"
    har = tmp_path / "network.har"
    trace.write_bytes(b"trace-bytes")
    har.write_text('{"log": {"entries": []}}')

    recording_id = persist_recording(
        org_id, f"{fixture_portal_url}/login.html", [],
        capture_artifacts=[("trace.zip", trace), ("network.har", har)],
    )

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        prefix = Path(settings.run_root) / recording.capture_blob_prefix
    assert (prefix / "trace.zip").read_bytes() == b"trace-bytes"
    assert (prefix / "network.har").read_text() == '{"log": {"entries": []}}'


def test_persist_recording_excludes_dom_snapshots_from_actions_json(fixture_portal_url):
    """actions.json is never read back by anything (confirmed: only
    trajectory.json, itself derived from the same actions, is) -- dom_before/
    dom_after are full-page-HTML snapshots that dominate this blob's size for
    zero benefit. Every other field must still be present."""
    with session_scope() as session:
        org = Organization(name="Slim Actions Org", slug=f"slim-actions-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    actions = [
        RecordedAction(
            0, "click", [{"selector": "#login-btn", "rank": 0}], None,
            f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/dashboard.html",
            "<html>huge before snapshot</html>", "<html>huge after snapshot</html>",
        ),
    ]

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", actions)

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        prefix = Path(settings.run_root) / recording.capture_blob_prefix
    stored = json.loads((prefix / "actions.json").read_text())
    assert len(stored) == 1
    assert "dom_before" not in stored[0]
    assert "dom_after" not in stored[0]
    assert stored[0]["kind"] == "click"
    assert stored[0]["selector_candidates"] == [{"selector": "#login-btn", "rank": 0}]
    assert stored[0]["url_after"] == f"{fixture_portal_url}/dashboard.html"


def test_persist_recording_without_codegen_writes_no_file(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="No Codegen Org", slug=f"no-codegen-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        codegen_path = Path(settings.run_root) / recording.capture_blob_prefix / "codegen.py"
        assert not codegen_path.exists()


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


def test_persist_recording_stores_page_extractions(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Extraction Org", slug=f"extraction-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    start_url = f"{fixture_portal_url}/login.html"
    recording_id = persist_recording(
        org_id, start_url, [],
        page_extractions=[
            {"page_index": 0, "url": f"{fixture_portal_url}/extraction_target.html", "data": {"fields": {"mrn": "MRN-4821"}, "tables": []}},
            {"page_index": 1, "url": f"{fixture_portal_url}/details.html", "data": {"fields": {"dob": "1990-01-01"}, "tables": []}},
        ],
    )

    extractions = list_page_extractions(recording_id)
    assert len(extractions) == 2
    # Verify first extraction
    assert extractions[0]["page_index"] == 0
    assert extractions[0]["url"] == f"{fixture_portal_url}/extraction_target.html"
    assert extractions[0]["data"]["fields"]["mrn"] == "MRN-4821"
    # Verify second extraction
    assert extractions[1]["page_index"] == 1
    assert extractions[1]["url"] == f"{fixture_portal_url}/details.html"
    assert extractions[1]["data"]["fields"]["dob"] == "1990-01-01"
    # Verify ordering is maintained (deterministic insertion order)
    assert extractions[0]["extracted_at"] <= extractions[1]["extracted_at"]


def test_list_page_extractions_returns_empty_list_when_none_exist(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="No Extraction Org", slug=f"no-extraction-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])

    assert list_page_extractions(recording_id) == []


def test_build_extraction_specs_aligns_recorded_data_with_extract_steps():
    """build_extraction_specs must compile a recording's captured extraction
    data (fields/tables spec + read modes, as SERIALIZER discovers them) into
    the ExtractionSpec mapping the golden emits _extract_<name>(scope) from —
    keyed by the extract step's index so mid-flow checkpoints land correctly."""
    from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep

    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "click", [{"selector": "#open", "rank": 0}], None, "none"),
        TrajectoryStep(1, "extract", [], None, "none"),
        TrajectoryStep(2, "click", [{"selector": "#next", "rank": 0}], None, "none"),
        TrajectoryStep(3, "extract", [], None, "none"),
    ))
    page_extractions = [
        {
            "page_index": 0,
            "url": "https://portal.test/extraction_target.html",
            "data": {
                "fields": {"mrn": "MRN-4821"},
                "tables": [],
                "spec": {
                    "fields": [
                        {"name": "mrn", "selector": "[data-lbl=MRN]", "read": "text"},
                    ],
                    "tables": [],
                },
            },
        },
        {
            "page_index": 1,
            "url": "https://portal.test/details.html",
            "data": {
                "fields": {"dob": "1990-01-01"},
                "tables": [],
                "spec": {
                    "fields": [
                        {"name": "dob", "selector": "#dob", "read": "attr", "attr": "value"},
                    ],
                    "tables": [],
                },
            },
        },
    ]

    specs = build_extraction_specs(trajectory, page_extractions)

    # The two extract steps (indexes 1 and 3) get their specs in order.
    assert set(specs) == {1, 3}
    assert specs[1]["fields"][0]["selector"] == "[data-lbl=MRN]"
    assert specs[3]["fields"][0]["read"] == "attr"
    assert specs[3]["fields"][0]["attr"] == "value"
    # Default names are derived from the step index.
    assert specs[1].get("name") == "extract_1"
    assert specs[3].get("name") == "extract_3"


def test_build_extraction_specs_skips_rows_without_spec_and_mismatched_counts():
    """A recorded extraction with no spec (legacy rows) must be skipped, and
    extra rows beyond the extract-step count must not misalign later specs."""
    from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep

    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "extract", [], None, "none"),
    ))
    page_extractions = [
        {"page_index": 0, "url": "u1", "data": {"fields": {"a": "1"}, "tables": []}},  # no spec
        {"page_index": 1, "url": "u2", "data": {"spec": {"fields": [{"name": "b", "selector": "#b", "read": "text"}], "tables": []}}},
    ]

    specs = build_extraction_specs(trajectory, page_extractions)

    assert specs == {}
    assert len(specs) == 0


def test_get_recording_detail_synthesizes_codegen_script_when_missing(fixture_portal_url):
    from atlas_engine.recording_service import get_recording_detail

    with session_scope() as session:
        org = Organization(name="Codegen Auto Org", slug=f"codegen-auto-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    actions = [
        RecordedAction(0, "fill", [{"selector": "#username", "rank": 0}], "user1", f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/login.html", "", ""),
        RecordedAction(1, "click", [{"selector": "#login-btn", "rank": 0}], None, f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/dashboard.html", "", ""),
    ]

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", actions)

    detail = get_recording_detail(recording_id)
    assert detail["codegen_script"] is not None
    assert "#username" in detail["codegen_script"]
    assert "#login-btn" in detail["codegen_script"]


def test_persist_recording_stores_user_id(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="User Id Org", slug=f"user-id-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        user = User(org_id=org.id, email="agent@example.com", password_hash="x", job_function="operations_agent")
        session.add(user)
        session.flush()
        user_id = str(user.id)

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [], user_id=user_id)

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        assert str(recording.user_id) == user_id


def test_list_recordings_filters_by_user_id(fixture_portal_url):
    from atlas_engine.recording_service import list_recordings

    with session_scope() as session:
        org = Organization(name="Filter User Org", slug=f"filter-user-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        agent_a = User(org_id=org.id, email="a@example.com", password_hash="x", job_function="operations_agent")
        agent_b = User(org_id=org.id, email="b@example.com", password_hash="x", job_function="operations_agent")
        session.add_all([agent_a, agent_b])
        session.flush()
        agent_a_id, agent_b_id = str(agent_a.id), str(agent_b.id)

    start_url = f"{fixture_portal_url}/login.html"
    recording_a = persist_recording(org_id, start_url, [], user_id=agent_a_id)
    persist_recording(org_id, start_url, [], user_id=agent_b_id)

    results = list_recordings(org_id, user_id=agent_a_id)
    assert [r["id"] for r in results] == [str(recording_a)]



def test_update_recording_details_persists_title_and_description(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Update Details Org", slug=f"update-details-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])
    update_recording_details(recording_id, title="Check eligibility", description="Walks the eligibility flow")

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        assert recording.title == "Check eligibility"
        assert recording.description == "Walks the eligibility flow"


def test_submit_recording_for_authoring_creates_a_linked_request(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Submit Org", slug=f"submit-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        agent = User(org_id=org.id, email="submitter@example.com", password_hash="x", job_function="operations_agent")
        session.add(agent)
        session.flush()
        agent_id = agent.id

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])
    update_recording_details(recording_id, title="Check eligibility")

    request_id = submit_recording_for_authoring(recording_id, agent_id)

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        assert recording.request_id == request_id
        request = session.get(RecordingRequest, request_id)
        assert request.status == "submitted"
        assert request.title == "Check eligibility"


def test_persist_recording_marks_status_failed_and_reraises_when_an_upload_fails(monkeypatch, fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Upload Failure Org", slug=f"upload-failure-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    actions = [
        RecordedAction(0, "click", [{"selector": "#login-btn", "rank": 0}], None, f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/dashboard.html", "", ""),
    ]

    from atlas_engine.storage.local import RunDir

    def _boom(self, name, data):
        raise ConnectionError("simulated network failure")

    monkeypatch.setattr(RunDir, "write_json", _boom)

    with pytest.raises(ConnectionError, match="simulated network failure"):
        persist_recording(org_id, f"{fixture_portal_url}/login.html", actions)

    with session_scope() as session:
        recording = session.query(Recording).filter_by(org_id=uuid.UUID(org_id)).one()
        assert recording.status == "failed"


def test_submit_recording_for_authoring_rejects_already_linked_recording(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Submit Twice Org", slug=f"submit-twice-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        agent = User(org_id=org.id, email="submitter2@example.com", password_hash="x", job_function="operations_agent")
        session.add(agent)
        session.flush()
        agent_id = agent.id

    start_url = f"{fixture_portal_url}/login.html"
    request_id = create_recording_request(org_id, str(agent_id), start_url, "Existing request")
    recording_id = persist_recording(org_id, start_url, [], request_id=request_id)

    with pytest.raises(ValueError, match="already linked"):
        submit_recording_for_authoring(recording_id, agent_id)


def test_create_recording_request_persists_section_a_fields(fixture_portal_url):
    from atlas_engine.db.models import System

    with session_scope() as session:
        org = Organization(name="Section A Org", slug=f"section-a-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        agent = User(org_id=org.id, email="secta@example.com", password_hash="x", job_function="operations_agent")
        system = System(org_id=org.id, name="Availity", category="Multi-payer portal")
        session.add_all([agent, system])
        session.flush()
        agent_id, system_id = str(agent.id), str(system.id)

    request_id = create_recording_request(
        org_id, agent_id, f"{fixture_portal_url}/login.html", "Status Check on Availity",
        lob="AR", category="Process", action="Status Check", system_id=system_id,
        client_scope=["generic"], reuse_scope="Generic",
    )

    with session_scope() as session:
        request = session.get(RecordingRequest, request_id)
        assert request.lob == "AR"
        assert request.category == "Process"
        assert request.action == "Status Check"
        assert str(request.system_id) == system_id
        assert request.client_scope == ["generic"]
        assert request.reuse_scope == "Generic"


def test_list_recording_requests_filters_by_lob_system_and_client(fixture_portal_url):
    from atlas_engine.db.models import System
    from atlas_engine.recording_service import list_recording_requests

    with session_scope() as session:
        org = Organization(name="Filter Section A Org", slug=f"filter-section-a-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        agent = User(org_id=org.id, email="filtersecta@example.com", password_hash="x", job_function="operations_agent")
        availity = System(org_id=org.id, name="Availity")
        ecw = System(org_id=org.id, name="eCW")
        session.add_all([agent, availity, ecw])
        session.flush()
        agent_id, availity_id, ecw_id = str(agent.id), str(availity.id), str(ecw.id)

    start_url = f"{fixture_portal_url}/login.html"
    ar_availity_client1 = create_recording_request(
        org_id, agent_id, start_url, "AR on Availity for client 1",
        lob="AR", system_id=availity_id, client_scope=["client-1"],
    )
    ev_ecw_client2 = create_recording_request(
        org_id, agent_id, start_url, "EV on eCW",
        lob="EV", system_id=ecw_id, client_scope=["client-2"],
    )

    by_lob = list_recording_requests(org_id, lob="AR")
    assert [r["id"] for r in by_lob] == [str(ar_availity_client1)]

    by_system = list_recording_requests(org_id, system_id=availity_id)
    assert [r["id"] for r in by_system] == [str(ar_availity_client1)]

    by_client = list_recording_requests(org_id, client_id="client-1")
    assert [r["id"] for r in by_client] == [str(ar_availity_client1)]

    by_other_client = list_recording_requests(org_id, client_id="client-2")
    assert [r["id"] for r in by_other_client] == [str(ev_ecw_client2)]

    by_unknown_client = list_recording_requests(org_id, client_id="no-such-client")
    assert by_unknown_client == []


def test_update_recording_details_persists_section_b_fields(fixture_portal_url):
    from atlas_engine.db.models import CarcCode

    with session_scope() as session:
        org = Organization(name="Section B Org", slug=f"section-b-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        carc = CarcCode(org_id=org.id, code="CO-16", is_multi_use=True)
        session.add(carc)
        session.flush()
        carc_id = str(carc.id)

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])

    update_recording_details(
        recording_id,
        payer_scope="Payer-specific", payer_ids=["payer-1"], other_system_ids=["system-1"],
        carc_code_id=carc_id, sub_reason="Missing NDC", resolution_path=["Corrected claim"],
        evidence_required=["Medical records"], report_id="RPT-123", date_param_logic="prior calendar month",
        output_destination="SFTP", post_processing="None", scenario_description="Denial follow-up",
    )

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        assert recording.payer_scope == "Payer-specific"
        assert recording.payer_ids == ["payer-1"]
        assert recording.other_system_ids == ["system-1"]
        assert str(recording.carc_code_id) == carc_id
        assert recording.sub_reason == "Missing NDC"
        assert recording.resolution_path == ["Corrected claim"]
        assert recording.evidence_required == ["Medical records"]
        assert recording.report_id == "RPT-123"
        assert recording.date_param_logic == "prior calendar month"
        assert recording.output_destination == "SFTP"
        assert recording.post_processing == "None"
        assert recording.scenario_description == "Denial follow-up"


def test_list_recordings_filters_by_lob_action_and_payer(fixture_portal_url):
    from atlas_engine.recording_service import list_recordings
    with session_scope() as session:
        org = Organization(name="Filter Recordings Section Org", slug=f"filter-recordings-section-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        agent = User(org_id=org.id, email="filterrec@example.com", password_hash="x", job_function="operations_agent")
        session.add(agent)
        session.flush()
        agent_id = str(agent.id)

    start_url = f"{fixture_portal_url}/login.html"
    ar_request_id = create_recording_request(org_id, agent_id, start_url, "AR request", lob="AR", action="Status Check")
    ev_request_id = create_recording_request(org_id, agent_id, start_url, "EV request", lob="EV", action="Portal Verification")

    ar_recording_id = persist_recording(org_id, start_url, [], request_id=ar_request_id)
    ev_recording_id = persist_recording(org_id, start_url, [], request_id=ev_request_id)
    update_recording_details(ar_recording_id, payer_ids=["payer-9"])

    by_lob = list_recordings(org_id, lob="AR")
    assert [r["id"] for r in by_lob] == [str(ar_recording_id)]

    by_action = list_recordings(org_id, action="Portal Verification")
    assert [r["id"] for r in by_action] == [str(ev_recording_id)]

    by_payer = list_recordings(org_id, payer_id="payer-9")
    assert [r["id"] for r in by_payer] == [str(ar_recording_id)]


def test_master_data_list_functions_return_only_active_rows_for_the_org(fixture_portal_url):
    from atlas_engine.db.models import CarcCode, Client, Payer, System
    from atlas_engine.recording_service import list_carc_codes, list_clients, list_payers, list_systems

    with session_scope() as session:
        org = Organization(name="Master Data Org", slug=f"master-data-org-{uuid.uuid4()}")
        other_org = Organization(name="Other Master Data Org", slug=f"other-master-data-org-{uuid.uuid4()}")
        session.add_all([org, other_org])
        session.flush()
        org_id = str(org.id)
        session.add_all([
            System(org_id=org.id, name="Active System", active=True),
            System(org_id=org.id, name="Inactive System", active=False),
            System(org_id=other_org.id, name="Other Org System", active=True),
            Payer(org_id=org.id, name="Cigna", active=True),
            Client(org_id=org.id, name="Acme Clinic", active=True),
            CarcCode(org_id=org.id, code="CO-16", is_multi_use=True, active=True),
        ])

    assert [s["name"] for s in list_systems(org_id)] == ["Active System"]
    assert [p["name"] for p in list_payers(org_id)] == ["Cigna"]
    assert [c["name"] for c in list_clients(org_id)] == ["Acme Clinic"]
    codes = list_carc_codes(org_id)
    assert len(codes) == 1
    assert codes[0]["code"] == "CO-16"
    assert codes[0]["is_multi_use"] is True
