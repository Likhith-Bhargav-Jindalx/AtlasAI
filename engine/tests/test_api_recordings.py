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
