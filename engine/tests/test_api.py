import uuid

from fastapi.testclient import TestClient

from atlas_engine.api import app
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization


def test_health_endpoint():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_author_endpoint_returns_certified_script(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="API Test Org", slug=f"api-test-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    actions = [
        {"index": 0, "kind": "fill", "selector_candidates": [{"selector": "#username", "rank": 0}], "value": "demo", "url_before": f"{fixture_portal_url}/login.html", "url_after": f"{fixture_portal_url}/login.html", "dom_before": "", "dom_after": ""},
        {"index": 1, "kind": "fill", "selector_candidates": [{"selector": "#password", "rank": 0}], "value": "demo-pass", "url_before": f"{fixture_portal_url}/login.html", "url_after": f"{fixture_portal_url}/login.html", "dom_before": "", "dom_after": ""},
        {"index": 2, "kind": "click", "selector_candidates": [{"selector": "#login-btn", "rank": 0}], "value": None, "url_before": f"{fixture_portal_url}/login.html", "url_after": f"{fixture_portal_url}/dashboard.html", "dom_before": "", "dom_after": ""},
        {"index": 3, "kind": "click", "selector_candidates": [{"selector": "#generate-report-btn", "rank": 0}], "value": None, "url_before": f"{fixture_portal_url}/dashboard.html", "url_after": f"{fixture_portal_url}/dashboard.html", "dom_before": "x", "dom_after": "y"},
    ]

    client = TestClient(app)
    response = client.post(
        "/author",
        json={"org_id": org_id, "start_url": f"{fixture_portal_url}/login.html", "actions": actions},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "certified"
    assert body["script_path"]
