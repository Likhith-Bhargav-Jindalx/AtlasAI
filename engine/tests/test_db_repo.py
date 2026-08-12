import uuid

from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, Organization
from atlas_engine.db.repo import get_or_create_portal_profile, next_golden_script_version


def test_get_or_create_portal_profile_creates_once_and_reuses():
    with session_scope() as session:
        org = Organization(name="Repo Test Org", slug=f"repo-test-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = org.id

        first = get_or_create_portal_profile(session, str(org_id), "example.com")
        second = get_or_create_portal_profile(session, str(org_id), "example.com")

        assert first.id == second.id


def test_next_golden_script_version_increments():
    with session_scope() as session:
        org = Organization(name="Version Test Org", slug=f"version-test-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()

        profile = get_or_create_portal_profile(session, str(org.id), "versions.example.com")

        assert next_golden_script_version(session, profile.id) == 1

        session.add(
            GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=1, script_blob_ref="v1.py")
        )
        session.flush()

        assert next_golden_script_version(session, profile.id) == 2
