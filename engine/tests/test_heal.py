import uuid

import pytest
from playwright.sync_api import sync_playwright

from atlas_engine.capture.recorder import CaptureSession
from atlas_engine.cli import author_from_recorded_actions
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, Organization
from atlas_engine.db.repo import get_or_create_portal_profile
from atlas_engine.verify.heal import heal_golden_script


def _author_against_fixture(fixture_portal_url) -> tuple[str, uuid.UUID]:
    with session_scope() as session:
        org = Organization(name="Heal Test Org", slug=f"heal-test-org-{uuid.uuid4()}")
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
    return org_id, uuid.UUID(result["golden_script_id"])


def test_heal_golden_script_reports_already_certified_when_nothing_changed(fixture_portal_url):
    _org_id, golden_script_id = _author_against_fixture(fixture_portal_url)

    result = heal_golden_script(golden_script_id)

    assert result["status"] == "already_certified"


def test_heal_golden_script_raises_when_golden_script_has_no_recording():
    with session_scope() as session:
        org = Organization(name="No Recording Org", slug=f"no-recording-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        profile = get_or_create_portal_profile(session, str(org.id), "norecording.example.com")
        golden = GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=1, script_blob_ref="v1.py")
        session.add(golden)
        session.flush()
        golden_id = golden.id

    with pytest.raises(ValueError, match="no linked recording"):
        heal_golden_script(golden_id)
