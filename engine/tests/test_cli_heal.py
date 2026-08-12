import uuid

from click.testing import CliRunner

from atlas_engine.cli import main
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, HealEvent, Organization
from atlas_engine.db.repo import get_or_create_portal_profile


def test_heal_command_reports_already_certified_for_an_unchanged_portal(fixture_portal_url):
    from atlas_engine.capture.recorder import CaptureSession
    from atlas_engine.cli import author_from_recorded_actions
    from playwright.sync_api import sync_playwright

    with session_scope() as session:
        org = Organization(name="CLI Heal Org", slug=f"cli-heal-org-{uuid.uuid4()}")
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
    golden_script_id = result["golden_script_id"]

    runner = CliRunner()
    cli_result = runner.invoke(main, ["heal", golden_script_id])

    assert cli_result.exit_code == 0, cli_result.output
    assert "already" in cli_result.output.lower()


def test_approve_heal_and_reject_heal_commands():
    with session_scope() as session:
        org = Organization(name="CLI Approve Org", slug=f"cli-approve-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        profile = get_or_create_portal_profile(session, str(org.id), "cli-approve.example.com")

        old_golden = GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=1, script_blob_ref="v1.py", status="active")
        session.add(old_golden)
        session.flush()
        new_golden = GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=2, script_blob_ref="v2.py", status="certified", parent_version_id=old_golden.id)
        session.add(new_golden)
        session.flush()
        heal_event = HealEvent(org_id=org.id, golden_script_id=old_golden.id, proposed_version_id=new_golden.id, trigger="on_demand", status="pending_approval")
        session.add(heal_event)
        session.flush()
        heal_event_id = str(heal_event.id)

    runner = CliRunner()
    result = runner.invoke(main, ["approve-heal", heal_event_id])

    assert result.exit_code == 0, result.output
    assert "v1" in result.output and "v2" in result.output


def test_reject_heal_command():
    with session_scope() as session:
        org = Organization(name="CLI Reject Org", slug=f"cli-reject-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        profile = get_or_create_portal_profile(session, str(org.id), "cli-reject.example.com")

        old_golden = GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=1, script_blob_ref="v1.py", status="active")
        session.add(old_golden)
        session.flush()
        new_golden = GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=2, script_blob_ref="v2.py", status="certified", parent_version_id=old_golden.id)
        session.add(new_golden)
        session.flush()
        heal_event = HealEvent(org_id=org.id, golden_script_id=old_golden.id, proposed_version_id=new_golden.id, trigger="on_demand", status="pending_approval")
        session.add(heal_event)
        session.flush()
        heal_event_id = str(heal_event.id)

    runner = CliRunner()
    result = runner.invoke(main, ["reject-heal", heal_event_id])

    assert result.exit_code == 0, result.output
    assert "rejected" in result.output.lower()
