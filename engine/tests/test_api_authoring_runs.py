import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.db import session_scope
from atlas_engine.db.models import (
    AuthoringRun,
    AuthoringRunEvent,
    CertificationRun,
    GoldenScript,
    Organization,
    PortalProfile,
    Recording,
)


def _make_authoring_run_with_details():
    with session_scope() as session:
        org = Organization(name="API Authoring Org", slug=f"api-authoring-org-{uuid.uuid4()}")
        profile = PortalProfile(org=org, domain="portal-api-authoring.example.com")
        recording = Recording(
            org=org, portal_profile=profile, status="captured", capture_blob_prefix="orgs/x/recordings/1/",
        )
        golden = GoldenScript(
            org=org, portal_profile=profile, version=1, script_blob_ref=__file__, status="certified",
        )
        session.add_all([org, profile, recording, golden])
        session.flush()
        run = AuthoringRun(
            org=org, recording=recording, golden_script=golden, status="completed",
            current_phase="done", repair_attempt=1,
        )
        session.add(run)
        session.flush()
        session.add(AuthoringRunEvent(authoring_run=run, phase="replay", step_index=0, message="Step 0: click - OK"))
        session.add(AuthoringRunEvent(authoring_run=run, phase="certifying", level="warning", message="retrying"))
        session.add(CertificationRun(authoring_run=run, run_number=1, status="pass"))
        session.add(CertificationRun(authoring_run=run, run_number=2, status="fail", failure_evidence_blob_ref="x.txt"))
        session.flush()
        return run.id


def test_get_authoring_run_returns_full_detail():
    authoring_run_id = _make_authoring_run_with_details()

    client = TestClient(app)
    response = client.get(f"/authoring-runs/{authoring_run_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["current_phase"] == "done"
    assert body["repair_attempt"] == 1
    assert len(body["events"]) == 2
    assert body["events"][0]["phase"] == "replay"
    assert body["events"][0]["message"] == "Step 0: click - OK"
    assert body["events"][1]["level"] == "warning"
    assert body["golden_script"]["status"] == "certified"
    assert len(body["certification_runs"]) == 2
    assert body["certification_runs"][0]["status"] == "pass"
    assert body["certification_runs"][1]["status"] == "fail"
    assert body["certification_runs"][1]["failure_evidence_blob_ref"] == "x.txt"


def test_get_authoring_run_has_null_golden_script_before_golden_phase():
    with session_scope() as session:
        org = Organization(name="No Golden Org", slug=f"no-golden-org-{uuid.uuid4()}")
        profile = PortalProfile(org=org, domain="portal-no-golden.example.com")
        recording = Recording(
            org=org, portal_profile=profile, status="captured", capture_blob_prefix="orgs/x/recordings/2/",
        )
        session.add_all([org, profile, recording])
        session.flush()
        run = AuthoringRun(org=org, recording=recording, status="running", current_phase="replay")
        session.add(run)
        session.flush()
        authoring_run_id = run.id

    client = TestClient(app)
    response = client.get(f"/authoring-runs/{authoring_run_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["golden_script"] is None
    assert body["events"] == []
    assert body["certification_runs"] == []


def test_get_authoring_run_404_for_unknown_id():
    client = TestClient(app)
    response = client.get(f"/authoring-runs/{uuid.uuid4()}")
    assert response.status_code == 404


def test_resume_authoring_run_endpoint_404_for_unknown_run():
    client = TestClient(app)
    response = client.post(f"/authoring-runs/{uuid.uuid4()}/resume")
    assert response.status_code == 404
