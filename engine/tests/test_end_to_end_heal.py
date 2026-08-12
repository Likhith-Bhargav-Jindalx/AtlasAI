import uuid

from playwright.sync_api import sync_playwright

from atlas_engine.capture.recorder import CaptureSession
from atlas_engine.cli import author_from_recorded_actions
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, Organization
from atlas_engine.llm.client import FakeLlmClient
from atlas_engine.verify.heal import approve_heal_event, heal_golden_script


def test_portal_drift_is_detected_healed_and_approved(fixture_portal_url, fixture_portal_server):
    """The full loop the architecture spec describes: a portal's UI
    changes, the golden script starts failing, healing proposes a fix
    grounded in the live DOM, and only an explicit approval makes it live.
    """
    with session_scope() as session:
        org = Organization(name="Drift Org", slug=f"drift-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    start_url = f"{fixture_portal_url}/login.html"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(start_url)
        with CaptureSession(page) as capture:
            page.fill("#username", "demo")
            page.fill("#password", "demo-pass")
            page.click("#login-btn")
            page.wait_for_url("**/dashboard.html")
            page.click("#generate-report-btn")
        browser.close()

    result = author_from_recorded_actions(org_id, start_url, capture.actions)
    assert result["status"] == "certified"
    golden_script_id = uuid.UUID(result["golden_script_id"])

    fixture_portal_server.set_drift_mode(True)
    try:
        fake_llm = FakeLlmClient(responses=[{"action": "replace_selector", "selector": "#run-report-btn"}])
        heal_result = heal_golden_script(golden_script_id, llm=fake_llm)

        assert heal_result["status"] == "healed"
        assert heal_result["new_status"] == "certified"
        assert any(change["new_value"] == "#run-report-btn" for change in heal_result["diff"])

        approval = approve_heal_event(uuid.UUID(heal_result["heal_event_id"]))
        assert approval["active_version"] == 2

        with session_scope() as session:
            old = session.get(GoldenScript, golden_script_id)
            new = session.get(GoldenScript, uuid.UUID(heal_result["new_golden_script_id"]))
            assert old.status == "deprecated"
            assert new.status == "active"
    finally:
        fixture_portal_server.set_drift_mode(False)
