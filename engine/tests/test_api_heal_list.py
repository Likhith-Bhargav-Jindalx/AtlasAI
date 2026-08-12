import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, HealEvent, Organization
from atlas_engine.db.repo import get_or_create_portal_profile


def test_list_heal_events_filters_by_status(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Heal List Org", slug=f"heal-list-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        profile = get_or_create_portal_profile(session, str(org.id), fixture_portal_url.split("//")[1])
        script = GoldenScript(
            org_id=org.id, portal_profile_id=profile.id, version=1,
            script_blob_ref="unused", status="active",
        )
        session.add(script)
        session.flush()
        pending = HealEvent(org_id=org.id, golden_script_id=script.id, trigger="on_demand", status="pending_approval")
        approved = HealEvent(org_id=org.id, golden_script_id=script.id, trigger="on_demand", status="approved")
        session.add_all([pending, approved])
        session.flush()
        org_id = str(org.id)
        pending_id = pending.id

    client = TestClient(app)
    response = client.get(f"/heal-events?org_id={org_id}&status=pending_approval")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == str(pending_id)
    assert body[0]["portal_domain"] is not None
