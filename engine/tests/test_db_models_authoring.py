from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas_engine.db import Base
from atlas_engine.db.models import AuthoringRun, GoldenScript, Organization, PortalProfile, Recording


def _sqlite_engine():
    return create_engine("sqlite:///:memory:")


def test_authoring_run_links_recording_and_golden_script():
    engine = _sqlite_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        org = Organization(name="Anka Health", slug="anka-health-authoring")
        profile = PortalProfile(org=org, domain="portal.example.com")
        recording = Recording(
            org=org, portal_profile=profile, status="captured", capture_blob_prefix="orgs/x/recordings/1/",
        )
        run = AuthoringRun(
            org=org, recording=recording, status="running", current_phase="replay",
        )
        session.add_all([org, profile, recording, run])
        session.commit()

        assert run.id is not None
        assert run.recording.id == recording.id
        assert run.golden_script_id is None
        assert run.repair_attempt == 0

        script = GoldenScript(org=org, portal_profile=profile, version=1, script_blob_ref="v1.py")
        session.add(script)
        session.flush()
        run.golden_script = script
        session.commit()

        assert run.golden_script.id == script.id
