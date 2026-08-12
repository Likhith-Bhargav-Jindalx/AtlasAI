import uuid

from sqlalchemy.orm import Session

from atlas_engine.db.models import GoldenScript, PortalProfile


def get_or_create_portal_profile(session: Session, org_id: str, domain: str) -> PortalProfile:
    """One row per (org, domain) — created once, reused across every future
    recording/authoring of that domain, never duplicated (architecture spec
    §6.3)."""
    profile = (
        session.query(PortalProfile)
        .filter_by(org_id=uuid.UUID(org_id), domain=domain)
        .one_or_none()
    )
    if profile is None:
        profile = PortalProfile(org_id=uuid.UUID(org_id), domain=domain)
        session.add(profile)
        session.flush()
    return profile


def next_golden_script_version(session: Session, portal_profile_id: uuid.UUID) -> int:
    latest = (
        session.query(GoldenScript.version)
        .filter_by(portal_profile_id=portal_profile_id)
        .order_by(GoldenScript.version.desc())
        .limit(1)
        .scalar()
    )
    return (latest or 0) + 1
