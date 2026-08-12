import uuid

from playwright.sync_api import sync_playwright

from atlas_engine.capture.recorder import CaptureSession
from atlas_engine.cli import author_from_recorded_actions
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, Organization, PortalProfile
from atlas_engine.portal_profile.infer import infer_portal_profile


def test_full_pipeline_record_to_certified_golden_script(fixture_portal_url):
    """The core product loop, with zero LLM calls needed: a scripted
    session stands in for a human recording, and the fixture portal never
    changes, so the brain's happy path is exercised end to end."""
    with session_scope() as session:
        org = Organization(name="E2E Org", slug=f"e2e-org-{uuid.uuid4()}")
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

    assert len(capture.actions) == 4

    profile = infer_portal_profile(start_url, capture.actions)
    assert profile.login_url == start_url
    assert profile.success_signal == {"url_contains": "/dashboard.html"}

    with session_scope() as session:
        portal_profile = PortalProfile(
            org_id=uuid.UUID(org_id),
            domain=profile.domain,
            inferred_login_url=profile.login_url,
            inferred_success_signal=profile.success_signal,
            confidence_score=profile.confidence,
        )
        session.add(portal_profile)
        session.flush()

    result = author_from_recorded_actions(org_id, start_url, capture.actions)

    assert result["status"] == "certified"

    with session_scope() as session:
        golden = session.get(GoldenScript, uuid.UUID(result["golden_script_id"]))
        assert golden.status == "certified"
        assert "atlas_engine" not in open(golden.script_blob_ref).read()
