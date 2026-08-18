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
    """Mirrors production's run_golden_phase by placing the script under a
    real LocalRunStore run_dir rather than an arbitrary tmp directory —
    not strictly required anymore (run_certify_phase's failure-evidence
    writes go through get_store() independently of where script_path
    lives), but keeps this test's setup representative of the real thing."""
    from pathlib import Path

    from atlas_engine.config import settings
    from atlas_engine.storage import LocalRunStore

    store = LocalRunStore(root=settings.ensure_run_root())
    run_dir = store.new_run_dir(prefix="test-goldens")
    script_path = run_dir.path / "golden.py"
    script_path.write_text(text)
    return script_path


def test_run_certify_phase_records_one_row_per_run_and_counts_passes(fixture_portal_url):
    # The recorded username/password are baked in as --username/--password
    # argparse defaults — no env var needed for the certify subprocess to
    # log in.
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

    passed, total, _persisted = run_certify_phase(script_path, authoring_run_id, runs=3)

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

    passed, total, _persisted = run_certify_phase(script_path, authoring_run_id, runs=2)

    assert passed == 0
    assert total == 2
    with session_scope() as session:
        cert_runs = session.query(CertificationRun).filter_by(authoring_run_id=authoring_run_id).all()
        assert all(c.status == "fail" for c in cert_runs)
        assert all(c.failure_evidence_blob_ref is not None for c in cert_runs)
        evidence_path = settings.run_root / cert_runs[0].failure_evidence_blob_ref
        assert "boom" in evidence_path.read_text()


def test_run_certify_phase_records_failure_evidence_when_script_lives_outside_run_root(fixture_portal_url, tmp_path):
    """Regression: production's golden scripts live under a LocalRunStore
    run_dir (itself under settings.run_root) or, when Azure Blob storage
    is configured, under a completely separate staging temp directory --
    AzureBlobRunStore's local files are never actually under run_root at
    all. The failure-evidence write must not assume script_path.relative_to
    (settings.run_root) succeeds; tmp_path here stands in for that
    Azure-staging case (some path with no relation to run_root)."""
    _org_id, _profile_id, _recording_id, authoring_run_id = _make_authoring_run_for_golden(fixture_portal_url)
    script_path = tmp_path / "golden.py"
    script_path.write_text("import sys\nprint('boom', file=sys.stderr)\nsys.exit(1)\n")

    passed, total, _persisted = run_certify_phase(script_path, authoring_run_id, runs=1)

    assert passed == 0
    assert total == 1
    with session_scope() as session:
        cert_runs = session.query(CertificationRun).filter_by(authoring_run_id=authoring_run_id).all()
        assert cert_runs[0].failure_evidence_blob_ref is not None
        blob_ref = cert_runs[0].failure_evidence_blob_ref

    assert get_store().read_text(blob_ref) is not None
    assert "boom" in get_store().read_text(blob_ref)


def test_run_certify_phase_stops_promptly_when_stop_event_fires_mid_run(fixture_portal_url):
    """A Stop click during certifying must interrupt the loop promptly --
    previously a plain subprocess.run(timeout=1200) blocked this call
    uninterruptibly for up to 20 minutes per run, with no way for the
    orchestrator's stop_event to take effect until all `runs` attempts
    finished on their own. A run cancelled this way must not be recorded
    as a failed CertificationRun -- it was stopped on purpose, not broken."""
    import threading
    import time

    _org_id, _profile_id, _recording_id, authoring_run_id = _make_authoring_run_for_golden(fixture_portal_url)
    script_path = _write_script_under_run_root("import time\ntime.sleep(30)\n")

    stop_event = threading.Event()
    threading.Timer(0.7, stop_event.set).start()

    start = time.monotonic()
    passed, total, persisted = run_certify_phase(script_path, authoring_run_id, runs=3, stop_event=stop_event)
    elapsed = time.monotonic() - start

    assert elapsed < 5
    assert passed == 0
    assert total == 3
    assert persisted is False
    with session_scope() as session:
        cert_runs = session.query(CertificationRun).filter_by(authoring_run_id=authoring_run_id).all()
        assert cert_runs == []


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

    store = get_store()
    with session_scope() as session:
        extractions = session.query(PageExtraction).filter_by(recording_id=recording_id).all()
        assert len(extractions) == 1
        data_blob_ref = extractions[0].data_blob_ref

    assert store.read_json(data_blob_ref) == {"fields": {"a": "1"}, "tables": []}
    assert store.exists(f"authoring/{authoring_run_id}/results/report.csv")


# ── C2 + I4: persist ordering/safety, and cross-repair-attempt dedup ─────────


def test_run_certify_phase_records_certification_run_even_if_result_persist_fails(
    fixture_portal_url, tmp_path, monkeypatch,
):
    """C2: persist happens AFTER the CertificationRun insert and is
    try/except-wrapped — a store failure must neither crash the whole
    authoring run nor leave the passing run without its CertificationRun
    row."""
    import atlas_engine.authoring.phases as phases_module

    with session_scope() as session:
        org = Organization(name="Certify Persist Fail Org", slug=f"certify-persist-fail-org-{uuid.uuid4()}")
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

    monkeypatch.setattr(
        phases_module, "certify_golden_script_once",
        lambda script_path, **kw: (
            True, None,
            {
                "extractions": {"click_1": {"url": "https://x.com", "data": {"fields": {"a": "1"}, "tables": []}}},
                "downloaded_files": [],
            },
        ),
    )
    monkeypatch.setattr(
        phases_module, "_persist_certify_result",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("store boom")),
    )

    script_path = tmp_path / "golden.py"
    script_path.write_text("print('{}')")

    passed, total, persisted = run_certify_phase(
        script_path, authoring_run_id, recording_id=recording_id, runs=1,
    )

    assert passed == 1
    assert persisted is False
    with session_scope() as session:
        cert_runs = session.query(CertificationRun).filter_by(authoring_run_id=authoring_run_id).all()
        assert len(cert_runs) == 1
        assert cert_runs[0].status == "pass"


def test_run_certify_phase_persists_print_pdf_entries_using_the_filename_key(
    fixture_portal_url, tmp_path, monkeypatch,
):
    """C2 regression: print-to-PDF captures reach certification as
    downloaded_files entries carrying 'filename' (the emitted script's
    contract after the name->filename rename) — _persist_certify_result must
    upload them without a KeyError (pre-fix, the 'name' key crashed the WHOLE
    authoring run whenever a print-to-PDF capture succeeded)."""
    import atlas_engine.authoring.phases as phases_module

    with session_scope() as session:
        org = Organization(name="Certify Print Pdf Org", slug=f"certify-print-pdf-org-{uuid.uuid4()}")
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

    (tmp_path / "My Report.pdf").write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(
        phases_module, "certify_golden_script_once",
        lambda script_path, **kw: (
            True, None,
            {
                "extractions": {},
                "downloaded_files": [{"filename": "My Report.pdf", "path": str(tmp_path / "My Report.pdf")}],
            },
        ),
    )

    script_path = tmp_path / "golden.py"
    script_path.write_text("print('{}')")

    run_certify_phase(script_path, authoring_run_id, recording_id=recording_id, runs=1)

    store = get_store()
    assert store.exists(f"authoring/{authoring_run_id}/results/My Report.pdf")


def test_run_certify_phase_with_already_persisted_does_not_duplicate_extraction_rows(
    fixture_portal_url, tmp_path, monkeypatch,
):
    """I4: two repair attempts for the same authoring_run_id/recording_id,
    both with a successful first run, must persist PageExtraction rows only
    ONCE — the second call (already_persisted=True) must not write a second
    set of rows."""
    import atlas_engine.authoring.phases as phases_module

    with session_scope() as session:
        org = Organization(name="Certify Dedup Org", slug=f"certify-dedup-org-{uuid.uuid4()}")
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

    monkeypatch.setattr(
        phases_module, "certify_golden_script_once",
        lambda script_path, **kw: (
            True, None,
            {
                "extractions": {"click_1": {"url": "https://x.com", "data": {"fields": {"a": "1"}, "tables": []}}},
                "downloaded_files": [],
            },
        ),
    )

    script_path = tmp_path / "golden.py"
    script_path.write_text("print('{}')")

    # Repair attempt 1: successful first run -> persists.
    _passed1, _total1, persisted1 = run_certify_phase(
        script_path, authoring_run_id, recording_id=recording_id, runs=1,
    )
    assert persisted1 is True
    # Repair attempt 2: already_persisted=True -> must NOT persist again.
    _passed2, _total2, persisted2 = run_certify_phase(
        script_path, authoring_run_id, recording_id=recording_id,
        runs=1, run_number_offset=1, already_persisted=True,
    )
    assert persisted2 is True

    with session_scope() as session:
        extractions = session.query(PageExtraction).filter_by(recording_id=recording_id).all()
        assert len(extractions) == 1
