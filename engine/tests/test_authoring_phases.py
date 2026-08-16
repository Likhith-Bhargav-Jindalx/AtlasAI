import uuid

from playwright.sync_api import sync_playwright

from atlas_engine.authoring.phases import run_brain_phase, run_certify_phase, run_extract_phase, run_golden_phase
from atlas_engine.brain.models import ReferenceTrajectory, TrajectoryStep
from atlas_engine.config import settings
from atlas_engine.db import session_scope
from atlas_engine.db.models import (
    AuthoringRun,
    AuthoringRunEvent,
    CertificationRun,
    GoldenScript,
    Organization,
    PageExtraction,
    PortalProfile,
    Recording,
)
from atlas_engine.db.repo import get_or_create_portal_profile
from atlas_engine.llm.client import FakeLlmClient
from atlas_engine.storage import get_store


def _make_authoring_run() -> uuid.UUID:
    with session_scope() as session:
        org = Organization(name="Phases Org", slug=f"phases-org-{uuid.uuid4()}")
        profile = PortalProfile(org=org, domain="portal-phases.example.com")
        recording = Recording(
            org=org, portal_profile=profile, status="captured", capture_blob_prefix="orgs/x/recordings/1/",
        )
        run = AuthoringRun(org=org, recording=recording, status="running", current_phase="extract")
        session.add_all([org, profile, recording, run])
        session.flush()
        return run.id


def test_run_extract_phase_returns_the_same_trajectory():
    trajectory = ReferenceTrajectory(
        steps=(TrajectoryStep(0, "click", [{"selector": "#a", "rank": 0}], None, "none"),)
    )

    result = run_extract_phase(trajectory)

    assert result == trajectory


def test_run_brain_phase_completes_on_track_when_nothing_has_drifted(fixture_portal_url):
    authoring_run_id = _make_authoring_run()
    start_url = f"{fixture_portal_url}/login.html"
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", "none"),
            TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
            TrajectoryStep(3, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
        )
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(start_url)
        outcomes = run_brain_phase(page, trajectory, authoring_run_id)
        browser.close()

    assert all(o.on_track for o in outcomes)
    with session_scope() as session:
        events = session.query(AuthoringRunEvent).filter_by(authoring_run_id=authoring_run_id).all()
        assert len(events) == 4
        assert all(e.level == "info" for e in events)


def test_run_brain_phase_repairs_a_drifted_selector_with_the_llm(fixture_portal_url, fixture_portal_server):
    authoring_run_id = _make_authoring_run()
    start_url = f"{fixture_portal_url}/login.html"
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", "none"),
            TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
            TrajectoryStep(3, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
        )
    )

    fixture_portal_server.set_drift_mode(True)
    try:
        fake_llm = FakeLlmClient(responses=[{"action": "replace_selector", "selector": "#run-report-btn"}])
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            page = browser.new_page()
            page.goto(start_url)
            outcomes = run_brain_phase(page, trajectory, authoring_run_id, llm=fake_llm)
            browser.close()

        assert all(o.on_track for o in outcomes)
        with session_scope() as session:
            events = session.query(AuthoringRunEvent).filter_by(authoring_run_id=authoring_run_id).all()
            assert len(events) == 4
            assert all(e.level == "info" for e in events)  # BrainLoop's internal repair already resolved it
    finally:
        fixture_portal_server.set_drift_mode(False)


def test_run_brain_phase_logs_a_warning_for_a_step_that_stays_off_track(fixture_portal_url, fixture_portal_server):
    authoring_run_id = _make_authoring_run()
    start_url = f"{fixture_portal_url}/login.html"
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", "none"),
            TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
            TrajectoryStep(3, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
        )
    )

    fixture_portal_server.set_drift_mode(True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            page = browser.new_page()
            page.goto(start_url)
            outcomes = run_brain_phase(page, trajectory, authoring_run_id)  # no llm -> can't repair
            browser.close()

        assert not all(o.on_track for o in outcomes)
        with session_scope() as session:
            events = session.query(AuthoringRunEvent).filter_by(authoring_run_id=authoring_run_id).all()
            assert any(e.level == "warning" for e in events)
    finally:
        fixture_portal_server.set_drift_mode(False)


def _make_authoring_run_for_golden(fixture_portal_url):
    with session_scope() as session:
        org = Organization(name="Golden Phase Org", slug=f"golden-phase-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        profile = get_or_create_portal_profile(session, org_id, "portal-golden.example.com")
        portal_profile_id = profile.id
        recording = Recording(
            org_id=org.id, portal_profile_id=portal_profile_id, status="captured",
            capture_blob_prefix="orgs/x/recordings/1/",
        )
        session.add(recording)
        session.flush()
        recording_id = recording.id
        run = AuthoringRun(org_id=org.id, recording_id=recording_id, status="running", current_phase="golden")
        session.add(run)
        session.flush()
        return org.id, portal_profile_id, recording_id, run.id


def test_run_golden_phase_creates_a_draft_golden_script(fixture_portal_url):
    org_id, portal_profile_id, recording_id, authoring_run_id = _make_authoring_run_for_golden(fixture_portal_url)
    trajectory = ReferenceTrajectory(
        steps=(TrajectoryStep(0, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),)
    )

    golden_script_id, script_path = run_golden_phase(
        trajectory, f"{fixture_portal_url}/login.html", org_id, portal_profile_id, recording_id, authoring_run_id,
        portal_domain="portal-golden.example.com", portal_name="Golden Portal",
    )

    assert script_path.exists()
    assert "generate-report-btn" in script_path.read_text()
    with session_scope() as session:
        golden = session.get(GoldenScript, golden_script_id)
        assert golden.status == "draft"
        assert golden.created_from_recording_id == recording_id


def test_run_golden_phase_passes_portal_info_to_emit_golden_script(fixture_portal_url, monkeypatch):
    captured = {}
    import atlas_engine.authoring.phases as phases_module
    real_emit = phases_module.emit_golden_script

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real_emit(*args, **kwargs)

    monkeypatch.setattr(phases_module, "emit_golden_script", spy)

    _org_id, _profile_id, recording_id, authoring_run_id = _make_authoring_run_for_golden(fixture_portal_url)
    trajectory = ReferenceTrajectory(steps=(
        TrajectoryStep(0, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
    ))

    run_golden_phase(
        trajectory, f"{fixture_portal_url}/dashboard.html", _org_id, _profile_id, recording_id, authoring_run_id,
        portal_domain="fixture.test", portal_name="Fixture", confidence=0.8,
        success_signal={"url_contains": "/dashboard.html"},
    )

    assert captured["portal_domain"] == "fixture.test"
    assert captured["portal_name"] == "Fixture"
    assert captured["confidence"] == 0.8
    assert captured["success_signal"] == {"url_contains": "/dashboard.html"}


def _write_script_under_run_root(text: str) -> "Path":
    """run_certify_phase computes failure-evidence blob_refs as paths
    relative to settings.run_root (matching every other blob_ref in this
    codebase) — so, like production's run_golden_phase, tests must place
    the script under a real LocalRunStore run_dir, not an arbitrary tmp
    directory, or a failing run would raise ValueError computing that
    relative path."""
    from pathlib import Path

    from atlas_engine.config import settings
    from atlas_engine.storage import LocalRunStore

    store = LocalRunStore(root=settings.ensure_run_root())
    run_dir = store.new_run_dir(prefix="test-goldens")
    script_path = run_dir.path / "golden.py"
    script_path.write_text(text)
    return script_path


def test_run_certify_phase_records_one_row_per_run_and_counts_passes(fixture_portal_url, monkeypatch):
    # emit_golden_script now genuinely requires SECRET_* env vars at runtime
    # for the login block (Task 11) — this test doesn't pass portal_domain
    # explicitly, so it gets the default "unknown", making the required
    # names SECRET_UNKNOWN_USERNAME/SECRET_UNKNOWN_PASSWORD. The subprocess
    # certify_golden_script_once launches inherits the current process's
    # environment, so setting these here (matching the trajectory's own
    # recorded fill values) lets the fixture portal's login.html accept
    # them exactly as it did before Task 11.
    monkeypatch.setenv("SECRET_UNKNOWN_USERNAME", "demo")
    monkeypatch.setenv("SECRET_UNKNOWN_PASSWORD", "demo-pass")

    _org_id, _profile_id, _recording_id, authoring_run_id = _make_authoring_run_for_golden(fixture_portal_url)
    trajectory = ReferenceTrajectory(
        steps=(
            TrajectoryStep(0, "fill", [{"selector": "#username", "rank": 0}], "demo", "none"),
            TrajectoryStep(1, "fill", [{"selector": "#password", "rank": 0}], "demo-pass", "none"),
            TrajectoryStep(2, "click", [{"selector": "#login-btn", "rank": 0}], None, "url_changed"),
            TrajectoryStep(3, "click", [{"selector": "#generate-report-btn", "rank": 0}], None, "dom_changed"),
        )
    )
    from atlas_engine.codegen.emit import emit_golden_script

    script_path = _write_script_under_run_root(
        emit_golden_script(trajectory, start_url=f"{fixture_portal_url}/login.html")
    )

    passed, total = run_certify_phase(script_path, authoring_run_id, runs=3)

    assert total == 3
    assert passed == 3
    with session_scope() as session:
        cert_runs = (
            session.query(CertificationRun)
            .filter_by(authoring_run_id=authoring_run_id)
            .order_by(CertificationRun.run_number)
            .all()
        )
        assert [c.run_number for c in cert_runs] == [1, 2, 3]
        assert all(c.status == "pass" for c in cert_runs)
        assert all(c.failure_evidence_blob_ref is None for c in cert_runs)


def test_run_certify_phase_records_failure_evidence_for_a_broken_script(fixture_portal_url):
    _org_id, _profile_id, _recording_id, authoring_run_id = _make_authoring_run_for_golden(fixture_portal_url)
    script_path = _write_script_under_run_root("import sys\nprint('boom', file=sys.stderr)\nsys.exit(1)\n")

    passed, total = run_certify_phase(script_path, authoring_run_id, runs=2)

    assert passed == 0
    assert total == 2
    with session_scope() as session:
        cert_runs = session.query(CertificationRun).filter_by(authoring_run_id=authoring_run_id).all()
        assert all(c.status == "fail" for c in cert_runs)
        assert all(c.failure_evidence_blob_ref is not None for c in cert_runs)
        evidence_path = settings.run_root / cert_runs[0].failure_evidence_blob_ref
        assert "boom" in evidence_path.read_text()


def test_run_certify_phase_persists_extractions_and_uploads_downloads_on_first_success(
    fixture_portal_url, tmp_path, monkeypatch,
):
    import atlas_engine.authoring.phases as phases_module
    monkeypatch.setattr(
        phases_module, "certify_golden_script_once",
        lambda script_path, **kw: (
            True, None,
            {
                "fileCount": 1, "filesTransferred": ["report.csv"],
                "downloaded_files": [{"filename": "report.csv", "path": str(tmp_path / "report.csv")}],
                "extractions": {"click_1": {"url": "https://x.com", "data": {"fields": {"a": "1"}, "tables": []}}},
            },
        ),
    )
    (tmp_path / "report.csv").write_text("a,b\n1,2\n")

    with session_scope() as session:
        org = Organization(name="Certify Persist Org", slug=f"certify-persist-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)
        recording = Recording(
            org_id=org.id,
            portal_profile_id=get_or_create_portal_profile(session, org_id, "x.com").id,
            status="captured", capture_blob_prefix="orgs/x/recordings/1/",
        )
        session.add(recording)
        session.flush()
        recording_id = recording.id
        authoring_run = AuthoringRun(org_id=org.id, recording_id=recording_id, status="running", current_phase="certifying")
        session.add(authoring_run)
        session.flush()
        authoring_run_id = authoring_run.id

    script_path = tmp_path / "golden.py"
    script_path.write_text("print('{}')")

    run_certify_phase(script_path, authoring_run_id, recording_id=recording_id, runs=1)

    with session_scope() as session:
        extractions = session.query(PageExtraction).filter_by(recording_id=recording_id).all()
        assert len(extractions) == 1
        assert extractions[0].data == {"fields": {"a": "1"}, "tables": []}

    store = get_store()
    assert store.exists(f"authoring/{authoring_run_id}/results/report.csv")
