import uuid

from atlas_engine.authoring_export import export_recording_for_authoring
from atlas_engine.capture.events import RecordedAction
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization
from atlas_engine.recording_service import persist_recording


def test_export_recording_for_authoring_returns_everything_the_pipeline_needs(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Export Authoring Org", slug=f"export-authoring-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    actions = [
        RecordedAction(0, "fill", [{"selector": "#username", "rank": 0}], "demo", f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/login.html", "", ""),
        RecordedAction(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/login.html", "", ""),
        RecordedAction(2, "click", [{"selector": "#login-btn", "rank": 0}], None, f"{fixture_portal_url}/login.html", f"{fixture_portal_url}/dashboard.html", "", ""),
    ]
    recording_id = persist_recording(org_id, f"{fixture_portal_url}/login.html", actions)

    export = export_recording_for_authoring(recording_id)

    assert export["org"]["id"] == org_id
    assert export["portal_profile"]["inferred_login_url"] == f"{fixture_portal_url}/login.html"
    assert export["recording"]["role"] == "demo"
    assert len(export["trajectory_json"]["steps"]) == 3
    # No codegen script was recorded for this actions-only capture.
    assert export["codegen_script"] is None


def test_export_recording_for_authoring_raises_for_an_unknown_recording():
    import pytest

    with pytest.raises(ValueError, match="No such recording"):
        export_recording_for_authoring(uuid.uuid4())
