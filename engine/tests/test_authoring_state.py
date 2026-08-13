import uuid

import pytest

from atlas_engine.authoring.events import log_event
from atlas_engine.authoring.state import update_run
from atlas_engine.db import session_scope
from atlas_engine.db.models import AuthoringRun, AuthoringRunEvent, Organization, PortalProfile, Recording


def _make_authoring_run(fixture_portal_url) -> uuid.UUID:
    with session_scope() as session:
        org = Organization(name="Authoring State Org", slug=f"authoring-state-org-{uuid.uuid4()}")
        profile = PortalProfile(org=org, domain="portal-state.example.com")
        recording = Recording(
            org=org, portal_profile=profile, status="captured", capture_blob_prefix="orgs/x/recordings/1/",
        )
        run = AuthoringRun(org=org, recording=recording, status="running", current_phase="replay")
        session.add_all([org, profile, recording, run])
        session.flush()
        return run.id


def test_update_run_sets_arbitrary_fields(fixture_portal_url):
    authoring_run_id = _make_authoring_run(fixture_portal_url)

    update_run(authoring_run_id, status="waiting_for_developer", repair_attempt=1)

    with session_scope() as session:
        run = session.get(AuthoringRun, authoring_run_id)
        assert run.status == "waiting_for_developer"
        assert run.repair_attempt == 1


def test_update_run_raises_for_unknown_run():
    with pytest.raises(ValueError, match="No such authoring run"):
        update_run(uuid.uuid4(), status="cancelled")


def test_log_event_appends_a_row(fixture_portal_url):
    authoring_run_id = _make_authoring_run(fixture_portal_url)

    log_event(authoring_run_id, phase="replay", step_index=2, level="warning", message="Step 2 failed")

    with session_scope() as session:
        events = session.query(AuthoringRunEvent).filter_by(authoring_run_id=authoring_run_id).all()
        assert len(events) == 1
        assert events[0].phase == "replay"
        assert events[0].step_index == 2
        assert events[0].level == "warning"
        assert events[0].message == "Step 2 failed"


def test_log_event_defaults_to_info_level_and_no_step_index(fixture_portal_url):
    authoring_run_id = _make_authoring_run(fixture_portal_url)

    log_event(authoring_run_id, phase="golden", message="Emitted golden script")

    with session_scope() as session:
        event = session.query(AuthoringRunEvent).filter_by(authoring_run_id=authoring_run_id).one()
        assert event.level == "info"
        assert event.step_index is None
