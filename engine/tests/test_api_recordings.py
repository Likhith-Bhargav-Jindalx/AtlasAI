import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.capture.events import RecordedAction
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization
from atlas_engine.recording_service import PageCapture, persist_recording


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
    assert body["pages"][0]["has_video"] is True
    assert body["pages"][0]["duration_seconds"] == 2
    assert len(body["pages"][0]["steps"]) == 1
    assert body["pages"][0]["steps"][0]["caption"] == "Click #login-btn"
    assert body["pages"][0]["steps"][0]["offset_ms"] == 500


def test_get_recording_detail_includes_codegen_script(fixture_portal_url):
    from pathlib import Path

    from atlas_engine.config import settings
    from atlas_engine.db.models import Recording

    with session_scope() as session:
        org = Organization(name="Codegen Detail Org", slug=f"codegen-detail-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    start_url = f"{fixture_portal_url}/login.html"
    recording_id = persist_recording(org_id, start_url, [], codegen_script='page.goto("https://x")\n')

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        codegen_path = Path(settings.run_root) / recording.capture_blob_prefix / "codegen.py"
        assert codegen_path.exists()

    client = TestClient(app)
    response = client.get(f"/recordings/{recording_id}")
    assert response.status_code == 200
    assert response.json()["codegen_script"] == 'page.goto("https://x")\n'


def test_get_recording_detail_codegen_script_is_null_when_absent(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="No Codegen Detail Org", slug=f"no-codegen-detail-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])

    client = TestClient(app)
    response = client.get(f"/recordings/{recording_id}")
    assert response.status_code == 200
    assert response.json()["codegen_script"] is None


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


def test_list_recordings_handles_multi_page_recordings(tmp_path, fixture_portal_url):
    """Regression test: a recording with a popup/tab has more than one
    VideoAsset row. list_recordings must not assume exactly one."""
    with session_scope() as session:
        org = Organization(name="Multi Page Org", slug=f"multi-page-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    main_video = tmp_path / "main.webm"
    main_video.write_bytes(b"fake-webm-bytes")
    popup_video = tmp_path / "popup.webm"
    popup_video.write_bytes(b"fake-webm-bytes")

    start_url = f"{fixture_portal_url}/login.html"
    recording_id = persist_recording(
        org_id, start_url, [],
        video_path=main_video, video_started_at_ms=0, video_ended_at_ms=1000,
        additional_pages=[
            PageCapture(
                page_index=1, actions=[],
                video_path=popup_video, video_started_at_ms=0, video_ended_at_ms=1000,
            ),
        ],
    )

    client = TestClient(app)
    response = client.get(f"/recordings?org_id={org_id}")

    assert response.status_code == 200
    body = response.json()
    matching = [r for r in body if r["id"] == str(recording_id)]
    assert len(matching) == 1
    assert matching[0]["has_video"] is True


def test_list_extractions_endpoint_returns_stored_extractions(tmp_path, fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Extraction API Org", slug=f"extraction-api-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    start_url = f"{fixture_portal_url}/login.html"
    recording_id = persist_recording(
        org_id, start_url, [],
        page_extractions=[
            {"page_index": 0, "url": f"{fixture_portal_url}/extraction_target.html", "data": {"fields": {"mrn": "MRN-4821"}, "tables": []}},
        ],
    )

    client = TestClient(app)
    response = client.get(f"/recordings/{recording_id}/extractions")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["page_index"] == 0
    assert body[0]["data"]["fields"]["mrn"] == "MRN-4821"


def test_list_extractions_endpoint_404_for_unknown_recording():
    client = TestClient(app)
    response = client.get(f"/recordings/{uuid.uuid4()}/extractions")
    assert response.status_code == 404


def test_get_recording_detail_includes_latest_authoring_run_id(fixture_portal_url):
    from atlas_engine.db.models import AuthoringRun

    with session_scope() as session:
        org = Organization(name="Authoring Run Detail Org", slug=f"auth-run-detail-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])

    client = TestClient(app)
    # Before any authoring run
    detail_before = client.get(f"/recordings/{recording_id}").json()
    assert detail_before["latest_authoring_run_id"] is None

    with session_scope() as session:
        run = AuthoringRun(
            org_id=uuid.UUID(org_id),
            recording_id=recording_id,
            status="running",
            current_phase="replay",
        )
        session.add(run)
        session.flush()
        run_id = str(run.id)

    detail_after = client.get(f"/recordings/{recording_id}").json()
    assert detail_after["latest_authoring_run_id"] == run_id


def test_list_recordings_endpoint_filters_by_user_id(fixture_portal_url):
    from atlas_engine.db.models import User

    with session_scope() as session:
        org = Organization(name="API User Filter Org", slug=f"api-user-filter-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        user = User(org_id=org.id, email="c@example.com", password_hash="x", job_function="operations_agent")
        session.add(user)
        session.flush()
        user_id = str(user.id)

    start_url = f"{fixture_portal_url}/login.html"
    mine = persist_recording(org_id, start_url, [], user_id=user_id)
    persist_recording(org_id, start_url, [])  # someone else's / unattributed

    client = TestClient(app)
    response = client.get(f"/recordings?org_id={org_id}&user_id={user_id}")
    assert response.status_code == 200
    assert [r["id"] for r in response.json()] == [str(mine)]


def test_patch_recording_updates_title_and_description(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Patch Org", slug=f"patch-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])

    client = TestClient(app)
    response = client.patch(f"/recordings/{recording_id}", json={"title": "My flow", "description": "desc"})
    assert response.status_code == 200

    detail = client.get(f"/recordings/{recording_id}").json()
    assert detail["title"] == "My flow"
    assert detail["description"] == "desc"


def test_submit_recording_endpoint_creates_a_request(fixture_portal_url):
    from atlas_engine.auth.passwords import hash_password
    from atlas_engine.db.models import RecordingRequest, User

    with session_scope() as session:
        org = Organization(name="Submit API Org", slug=f"submit-api-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        agent = User(
            org_id=org.id, email=f"submit-api-{uuid.uuid4()}@example.com", password_hash=hash_password("pw"),
            job_function="operations_agent",
        )
        session.add(agent)
        session.flush()
        agent_id = str(agent.id)
        agent_email = agent.email

    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", [])

    client = TestClient(app)
    login_response = client.post("/auth/login", json={"email": agent_email, "password": "pw"})
    assert login_response.status_code == 200
    response = client.post(f"/recordings/{recording_id}/submit", json={"submitted_by": agent_id})
    assert response.status_code == 200
    assert "request_id" in response.json()

    with session_scope() as session:
        request = session.get(RecordingRequest, uuid.UUID(response.json()["request_id"]))
        assert request.title == "Untitled recording"

