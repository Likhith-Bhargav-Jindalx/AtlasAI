import uuid

from click.testing import CliRunner

from atlas_engine.cli import main
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, RecordingRequest, User


def _make_org_and_users(slug_prefix: str) -> tuple[str, str, str]:
    with session_scope() as session:
        org = Organization(name="CLI Requests Org", slug=f"{slug_prefix}-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        agent = User(org_id=org.id, email=f"agent-{uuid.uuid4()}@example.com", password_hash="x", role="member")
        developer = User(org_id=org.id, email=f"dev-{uuid.uuid4()}@example.com", password_hash="x", role="member")
        session.add_all([agent, developer])
        session.flush()
        return str(org.id), str(agent.id), str(developer.id)


def test_request_recording_list_and_claim_commands(fixture_portal_url):
    org_id, agent_id, developer_id = _make_org_and_users("cli-requests")
    runner = CliRunner()

    create_result = runner.invoke(
        main,
        [
            "request-recording",
            "--org-id", org_id,
            "--requested-by", agent_id,
            "--target-url", f"{fixture_portal_url}/login.html",
            "--title", "Check eligibility",
        ],
    )
    assert create_result.exit_code == 0, create_result.output
    request_id = create_result.output.strip().splitlines()[-1].split(": ")[-1]

    list_result = runner.invoke(main, ["list-requests", "--org-id", org_id, "--status", "open"])
    assert list_result.exit_code == 0, list_result.output
    assert "Check eligibility" in list_result.output

    claim_result = runner.invoke(main, ["claim-request", request_id, "--developer-id", developer_id])
    assert claim_result.exit_code == 0, claim_result.output

    with session_scope() as session:
        request = session.get(RecordingRequest, uuid.UUID(request_id))
        assert request.claimed_by == uuid.UUID(developer_id)


def test_author_command_authors_from_an_existing_recording(fixture_portal_url):
    from playwright.sync_api import sync_playwright

    from atlas_engine.capture.recorder import CaptureSession
    from atlas_engine.recording_service import persist_recording

    org_id, _agent_id, _developer_id = _make_org_and_users("cli-author")
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

    recording_id = persist_recording(org_id, start_url, capture.actions)

    runner = CliRunner()
    result = runner.invoke(main, ["author", str(recording_id)])

    assert result.exit_code == 0, result.output
    assert "CERTIFIED" in result.output
