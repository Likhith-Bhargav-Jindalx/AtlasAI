from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas_engine.db import Base
from atlas_engine.db.models import (
    GoldenScript,
    Organization,
    PortalProfile,
    Recording,
    RecordingRequest,
    User,
)


def _sqlite_engine():
    return create_engine("sqlite:///:memory:")


def test_recording_request_links_org_user_and_portal_profile():
    engine = _sqlite_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        org = Organization(name="Requests Org", slug="requests-org")
        agent = User(
            org=org, email="agent@example.com", password_hash="x", role="member",
            job_function="operations_agent",
        )
        profile = PortalProfile(org=org, domain="portal.example.com")
        request = RecordingRequest(
            org=org, requester=agent, portal_profile=profile,
            target_url="https://portal.example.com/login", title="Check eligibility",
        )
        session.add_all([org, agent, profile, request])
        session.commit()

        assert request.id is not None
        assert request.status == "open"
        assert request.requester.email == "agent@example.com"


def test_recording_links_to_request_with_role():
    engine = _sqlite_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        org = Organization(name="Requests Org 2", slug="requests-org-2")
        agent = User(org=org, email="agent2@example.com", password_hash="x", role="member")
        profile = PortalProfile(org=org, domain="portal2.example.com")
        request = RecordingRequest(
            org=org, requester=agent, portal_profile=profile,
            target_url="https://portal2.example.com", title="X",
        )
        session.add_all([org, agent, profile, request])
        session.flush()

        recording = Recording(
            org=org, portal_profile=profile, status="captured",
            capture_blob_prefix="orgs/x/recordings/1/", request_id=request.id, role="demo",
        )
        session.add(recording)
        session.commit()

        assert recording.role == "demo"
        assert recording.request_id == request.id


def test_recording_request_fulfilled_golden_script_reference():
    engine = _sqlite_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        org = Organization(name="Requests Org 3", slug="requests-org-3")
        agent = User(org=org, email="agent3@example.com", password_hash="x", role="member")
        profile = PortalProfile(org=org, domain="portal3.example.com")
        request = RecordingRequest(
            org=org, requester=agent, portal_profile=profile,
            target_url="https://portal3.example.com", title="X",
        )
        session.add_all([org, agent, profile, request])
        session.flush()

        golden = GoldenScript(org=org, portal_profile=profile, version=1, script_blob_ref="v1.py", status="certified")
        session.add(golden)
        session.flush()

        request.status = "fulfilled"
        request.fulfilled_golden_script_id = golden.id
        session.commit()

        assert request.fulfilled_golden_script.id == golden.id
