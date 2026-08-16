import uuid

from atlas_engine.authoring_service import author_from_recording
from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, Organization, PortalProfile, Recording, RecordingRequest, User
from atlas_engine.recording_service import create_recording_request, persist_recording


def _record_against_fixture(org_id: str, fixture_portal_url: str, request_id=None, role="demo") -> uuid.UUID:
    from playwright.sync_api import sync_playwright

    from atlas_engine.capture.recorder import CaptureSession

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

    return persist_recording(org_id, start_url, capture.actions, request_id=request_id, role=role)


def test_author_from_recording_produces_a_certified_script(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Authoring Service Org", slug=f"authoring-service-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = _record_against_fixture(org_id, fixture_portal_url)

    result = author_from_recording(recording_id)

    assert result["status"] == "certified"

    with session_scope() as session:
        golden = session.get(GoldenScript, uuid.UUID(result["golden_script_id"]))
        assert golden.created_from_recording_id == recording_id


def test_author_from_recording_works_directly_on_a_demo_role_recording(fixture_portal_url):
    """Proves the future path already works today: authoring doesn't care
    that this recording was tagged "demo" (an ops agent's walkthrough)
    rather than "authoring" (a developer's own) — no code change will be
    needed to skip the developer re-record step later."""
    with session_scope() as session:
        org = Organization(name="Direct Author Org", slug=f"direct-author-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = _record_against_fixture(org_id, fixture_portal_url, role="demo")

    result = author_from_recording(recording_id)

    assert result["status"] == "certified"


def test_author_from_recording_fulfills_a_linked_request_on_success(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Fulfill Org", slug=f"fulfill-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        agent = User(org_id=org.id, email=f"agent-{uuid.uuid4()}@example.com", password_hash="x", role="member")
        session.add(agent)
        session.flush()
        org_id = str(org.id)
        agent_id = str(agent.id)

    request_id = create_recording_request(org_id, agent_id, "http://placeholder/", "Fulfillment test")
    recording_id = _record_against_fixture(org_id, fixture_portal_url, request_id=request_id, role="authoring")

    result = author_from_recording(recording_id)
    assert result["status"] == "certified"

    with session_scope() as session:
        request = session.get(RecordingRequest, request_id)
        assert request.status == "fulfilled"
        assert request.fulfilled_golden_script_id == uuid.UUID(result["golden_script_id"])


def test_author_from_recording_threads_portal_info_to_emit_golden_script(fixture_portal_url, monkeypatch):
    """C3: author_from_recording is one of three production call sites that
    never threaded portal_domain/portal_name/success_signal to
    emit_golden_script — leaving secrets collapsed to SECRET_UNKNOWN_*. The
    portal identity + success signal must reach emission so the golden
    script's env-var names and login signal are portal-correct."""
    import atlas_engine.authoring_service as as_module

    with session_scope() as session:
        org = Organization(name="Threaded Author Org", slug=f"threaded-author-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    recording_id = _record_against_fixture(org_id, fixture_portal_url)

    with session_scope() as session:
        recording = session.get(Recording, recording_id)
        profile = session.get(PortalProfile, recording.portal_profile_id)
        profile.inferred_success_signal = {"url_contains": "/dashboard.html"}
        profile.confidence_score = 0.9
        expected_domain = profile.domain
        expected_name = profile.display_name or profile.domain
        session.add(profile)

    captured = {}

    def spy_emit(*args, **kwargs):
        captured.update(kwargs)
        return "def run_flow(playwright, page, params, diagnostics_dir):\n    return {}\n"

    class _FakeResult:
        def __init__(self, trajectory):
            self.success = True
            self.trajectory = trajectory

    class _FakeBrainLoop:
        def __init__(self, page, **kwargs):
            pass

        def run(self, trajectory):
            return _FakeResult(trajectory)

    class _FakeCert:
        is_certified = True

    monkeypatch.setattr(as_module, "emit_golden_script", spy_emit)
    monkeypatch.setattr(as_module, "BrainLoop", _FakeBrainLoop)
    monkeypatch.setattr(as_module, "certify_golden_script", lambda *a, **k: _FakeCert())

    result = author_from_recording(recording_id)

    assert result["status"] == "certified"
    assert captured["portal_domain"] == expected_domain
    assert captured["portal_name"] == expected_name
    assert captured["success_signal"] == {"url_contains": "/dashboard.html"}
