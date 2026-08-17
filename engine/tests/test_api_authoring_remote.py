import json
import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.auth.passwords import hash_password
from atlas_engine.capture.events import RecordedAction
from atlas_engine.db import session_scope
from atlas_engine.db.models import AuthoringRun, AuthoringRunEvent, CertificationRun, GoldenScript, Organization, User
from atlas_engine.recording_service import persist_recording


def _login_developer(email: str, password: str) -> tuple[TestClient, str, str]:
    with session_scope() as session:
        org = Organization(name="Authoring Remote Org", slug=f"authoring-remote-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        user = User(org_id=org.id, email=email, password_hash=hash_password(password), role="member", job_function="developer")
        session.add(user)
        session.flush()
        org_id, user_id = str(org.id), str(user.id)

    client = TestClient(app)
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return client, org_id, user_id


def test_export_for_authoring_requires_developer_job_function(fixture_portal_url):
    email = f"export-non-dev-{uuid.uuid4()}@example.com"
    with session_scope() as session:
        org = Organization(name="Non Dev Org", slug=f"non-dev-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        session.add(User(org_id=org.id, email=email, password_hash=hash_password("pw"), role="member", job_function="operations_agent"))
        org_id = str(org.id)

    actions = [RecordedAction(0, "click", [{"selector": "#login-btn", "rank": 0}], None, f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/dashboard.html", "", "")]
    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", actions)

    client = TestClient(app)
    client.post("/auth/login", json={"email": email, "password": "pw"})
    response = client.get(f"/recordings/{recording_id}/export-for-authoring")

    assert response.status_code == 403


def test_export_for_authoring_returns_a_real_export(fixture_portal_url):
    email = f"export-dev-{uuid.uuid4()}@example.com"
    client, org_id, _user_id = _login_developer(email, "test-password")

    actions = [RecordedAction(0, "click", [{"selector": "#login-btn", "rank": 0}], None, f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/dashboard.html", "", "")]
    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", actions)

    response = client.get(f"/recordings/{recording_id}/export-for-authoring")

    assert response.status_code == 200
    body = response.json()
    assert body["org"]["id"] == org_id
    assert len(body["trajectory_json"]["steps"]) == 1


def test_submit_authoring_result_persists_golden_script_and_certification_runs(fixture_portal_url):
    email = f"submit-authoring-{uuid.uuid4()}@example.com"
    client, org_id, _user_id = _login_developer(email, "test-password")

    actions = [RecordedAction(0, "click", [{"selector": "#login-btn", "rank": 0}], None, f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/dashboard.html", "", "")]
    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", actions)

    response = client.post(
        f"/recordings/{recording_id}/submit-authoring-result",
        data={
            "golden_status": "certified",
            "script_text": "print('hello from a locally-authored golden script')",
            "certification_runs": json.dumps([
                {"run_number": 1, "status": "pass"},
                {"run_number": 2, "status": "fail"},
            ]),
            "events": json.dumps([{"phase": "golden", "level": "info", "message": "Emitted golden script v1"}]),
            "repair_attempt": "0",
        },
        files=[("evidence_files", ("evidence-2.png", b"fake-png-bytes", "image/png"))],
    )

    assert response.status_code == 200
    golden_script_id = response.json()["golden_script_id"]
    authoring_run_id = response.json()["authoring_run_id"]

    with session_scope() as session:
        golden = session.get(GoldenScript, uuid.UUID(golden_script_id))
        assert golden.status == "certified"

        run = session.get(AuthoringRun, uuid.UUID(authoring_run_id))
        assert run.status == "completed"
        assert run.golden_script_id == golden.id

        events = session.query(AuthoringRunEvent).filter_by(authoring_run_id=run.id).all()
        assert len(events) == 1
        assert events[0].message == "Emitted golden script v1"

        cert_runs = {c.run_number: c for c in session.query(CertificationRun).filter_by(authoring_run_id=run.id).all()}
        assert cert_runs[1].status == "pass"
        assert cert_runs[1].failure_evidence_blob_ref is None
        assert cert_runs[2].status == "fail"
        assert cert_runs[2].failure_evidence_blob_ref is not None
