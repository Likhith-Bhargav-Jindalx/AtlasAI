from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas_engine.db import Base
from atlas_engine.db.models import (
    ActionCache,
    GoldenScript,
    HealEvent,
    Organization,
    PortalProfile,
    Recording,
    User,
)


def _sqlite_engine():
    # SQLite stands in for structural tests only (no ENUM/JSONB/partial-index
    # behavior) — this test asserts relationships and cascades wire up
    # correctly, not Postgres-specific column types. Task 9 exercises the
    # real Postgres schema end to end.
    return create_engine("sqlite:///:memory:")


def test_organization_user_portal_profile_and_golden_script_chain():
    engine = _sqlite_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        org = Organization(name="Anka Health", slug="anka-health")
        user = User(org=org, email="likhith@ankahealth.ai", password_hash="x", role="owner")
        profile = PortalProfile(org=org, domain="portal.example.com", creator=user)
        recording = Recording(
            org=org,
            portal_profile=profile,
            user=user,
            status="captured",
            capture_blob_prefix="orgs/x/recordings/1/",
        )
        script_v1 = GoldenScript(
            org=org,
            portal_profile=profile,
            version=1,
            script_blob_ref="orgs/x/goldens/1/v1.py",
            status="certified",
            created_from_recording=recording,
        )
        session.add_all([org, user, profile, recording, script_v1])
        session.commit()

        assert script_v1.id is not None
        assert script_v1.portal_profile.domain == "portal.example.com"
        assert script_v1.created_from_recording.id == recording.id


def test_golden_script_versioning_via_parent_version_id():
    engine = _sqlite_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        org = Organization(name="Anka Health", slug="anka-health-2")
        profile = PortalProfile(org=org, domain="portal2.example.com")
        v1 = GoldenScript(
            org=org, portal_profile=profile, version=1,
            script_blob_ref="v1.py", status="deprecated",
        )
        v2 = GoldenScript(
            org=org, portal_profile=profile, version=2,
            script_blob_ref="v2.py", status="active", parent_version=v1,
        )
        session.add_all([org, profile, v1, v2])
        session.commit()

        assert v1.id is not None
        assert v2.id is not None
        assert v2.parent_version.id == v1.id


def test_heal_event_and_golden_script_circular_reference():
    engine = _sqlite_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        org = Organization(name="Anka Health", slug="anka-health-3")
        profile = PortalProfile(org=org, domain="portal3.example.com")
        v1 = GoldenScript(org=org, portal_profile=profile, version=1, script_blob_ref="v1.py")
        session.add_all([org, profile, v1])
        session.flush()

        heal = HealEvent(org=org, golden_script=v1, trigger="runtime_auto", status="pending_approval")
        session.add(heal)
        session.flush()

        v2 = GoldenScript(
            org=org, portal_profile=profile, version=2, script_blob_ref="v2.py",
            parent_version=v1, created_from_heal_event=heal,
        )
        heal.proposed_version = v2
        session.add(v2)
        session.commit()

        assert v2.id is not None
        assert heal.id is not None
        assert v2.created_from_heal_event.id == heal.id
        assert heal.proposed_version.id == v2.id


def test_action_cache_unique_constraint():
    engine = _sqlite_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        org = Organization(name="Anka Health", slug="anka-health-4")
        profile = PortalProfile(org=org, domain="portal4.example.com")
        script = GoldenScript(org=org, portal_profile=profile, version=1, script_blob_ref="v1.py")
        session.add_all([org, profile, script])
        session.flush()

        cache_row = ActionCache(
            golden_script=script,
            step_index=0,
            cache_key="hash-abc",
            selector_candidates=[{"selector": "#login", "method": "click", "rank": 0}],
        )
        session.add(cache_row)
        session.commit()

        assert cache_row.id is not None
        assert cache_row.selector_candidates[0]["selector"] == "#login"


def test_golden_script_accepts_partially_certified_status():
    engine = _sqlite_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        org = Organization(name="Anka Health", slug="anka-health-partial-cert")
        profile = PortalProfile(org=org, domain="portal-partial.example.com")
        script = GoldenScript(
            org=org, portal_profile=profile, version=1, script_blob_ref="v1.py", status="partially_certified",
        )
        session.add_all([org, profile, script])
        session.commit()

        assert script.status == "partially_certified"


def test_page_extraction_belongs_to_recording():
    from atlas_engine.db.models import PageExtraction

    engine = _sqlite_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        org = Organization(name="Anka Health", slug="anka-health-extraction")
        profile = PortalProfile(org=org, domain="portal-extraction.example.com")
        recording = Recording(
            org=org, portal_profile=profile, status="captured", capture_blob_prefix="orgs/x/recordings/4/",
        )
        session.add_all([org, profile, recording])
        session.flush()

        extraction = PageExtraction(
            recording=recording, page_index=0, url="https://portal-extraction.example.com/report",
            data={"headings": ["Patient Report"], "fields": {"Patient ID": "12345"}},
        )
        session.add(extraction)
        session.commit()

        assert extraction.id is not None
        assert extraction.recording.id == recording.id
        assert extraction.data["fields"]["Patient ID"] == "12345"
        assert extraction.extracted_at is not None
