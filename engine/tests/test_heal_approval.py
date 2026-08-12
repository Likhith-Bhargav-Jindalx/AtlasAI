import uuid

import pytest

from atlas_engine.db import session_scope
from atlas_engine.db.models import GoldenScript, HealEvent, Organization
from atlas_engine.db.repo import get_or_create_portal_profile
from atlas_engine.verify.heal import approve_heal_event, reject_heal_event


def _make_pending_heal_event(slug_prefix: str, new_status: str = "certified"):
    with session_scope() as session:
        org = Organization(name="Approval Test Org", slug=f"{slug_prefix}-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        profile = get_or_create_portal_profile(session, str(org.id), f"{slug_prefix}.example.com")

        old_golden = GoldenScript(
            org_id=org.id, portal_profile_id=profile.id, version=1, script_blob_ref="v1.py", status="active"
        )
        session.add(old_golden)
        session.flush()

        new_golden = GoldenScript(
            org_id=org.id, portal_profile_id=profile.id, version=2, script_blob_ref="v2.py",
            status=new_status, parent_version_id=old_golden.id,
        )
        session.add(new_golden)
        session.flush()

        heal_event = HealEvent(
            org_id=org.id, golden_script_id=old_golden.id, proposed_version_id=new_golden.id,
            trigger="on_demand", status="pending_approval",
        )
        session.add(heal_event)
        session.flush()

        return heal_event.id, old_golden.id, new_golden.id


def test_approve_heal_event_activates_new_version_and_deprecates_old():
    heal_event_id, old_id, new_id = _make_pending_heal_event("approve-test-org")

    result = approve_heal_event(heal_event_id)

    assert result["deprecated_version"] == 1
    assert result["active_version"] == 2

    with session_scope() as session:
        assert session.get(GoldenScript, old_id).status == "deprecated"
        assert session.get(GoldenScript, new_id).status == "active"
        assert session.get(HealEvent, heal_event_id).status == "approved"


def test_approve_heal_event_rejects_a_version_that_is_not_certified():
    heal_event_id, _old_id, _new_id = _make_pending_heal_event("approve-draft-org", new_status="draft")

    with pytest.raises(ValueError, match="not certified"):
        approve_heal_event(heal_event_id)


def test_reject_heal_event_deprecates_proposed_version_without_activating_it():
    heal_event_id, old_id, new_id = _make_pending_heal_event("reject-test-org")

    reject_heal_event(heal_event_id)

    with session_scope() as session:
        assert session.get(GoldenScript, old_id).status == "active"  # unchanged
        assert session.get(GoldenScript, new_id).status == "deprecated"
        assert session.get(HealEvent, heal_event_id).status == "rejected"
