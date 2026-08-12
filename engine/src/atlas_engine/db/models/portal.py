import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Enum, ForeignKey, UniqueConstraint, func, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atlas_engine.db import Base, PortableJSON

SECRET_PROVIDERS = ("env", "azure_keyvault")
RECORDING_STATUSES = ("capturing", "captured", "failed", "discarded")
RECORDING_ROLES = ("demo", "authoring")


class PortalProfile(Base):
    __tablename__ = "portal_profiles"
    __table_args__ = (UniqueConstraint("org_id", "domain"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    domain: Mapped[str]
    display_name: Mapped[str | None] = mapped_column(default=None)
    inferred_login_url: Mapped[str | None] = mapped_column(default=None)
    inferred_success_signal: Mapped[dict | None] = mapped_column(PortableJSON, default=None)
    confidence_score: Mapped[Decimal | None] = mapped_column(default=None)
    metadata_: Mapped[dict] = mapped_column("metadata", PortableJSON, default=dict)
    first_recorded_at: Mapped[datetime | None] = mapped_column(default=None)
    last_recorded_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    org: Mapped["Organization"] = relationship()
    creator: Mapped["User | None"] = relationship(foreign_keys=[created_by])


class SecretsRef(Base):
    __tablename__ = "secrets_refs"
    __table_args__ = (UniqueConstraint("portal_profile_id", "label"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    portal_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("portal_profiles.id", ondelete="CASCADE"), default=None
    )
    label: Mapped[str]
    secret_provider: Mapped[str] = mapped_column(Enum(*SECRET_PROVIDERS, name="secret_provider"), default="env")
    secret_key_ref: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    portal_profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("portal_profiles.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), default=None)
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recording_requests.id", ondelete="SET NULL"), default=None
    )
    role: Mapped[str] = mapped_column(Enum(*RECORDING_ROLES, name="recording_role"), default="demo")
    status: Mapped[str] = mapped_column(Enum(*RECORDING_STATUSES, name="recording_status"), default="capturing")
    capture_blob_prefix: Mapped[str]
    has_audio: Mapped[bool] = mapped_column(default=False)
    duration_seconds: Mapped[int | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(default=None)

    org: Mapped["Organization"] = relationship()
    portal_profile: Mapped["PortalProfile"] = relationship()
    user: Mapped["User | None"] = relationship(foreign_keys=[user_id])


class ReferenceTrajectory(Base):
    __tablename__ = "reference_trajectories"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recording_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"))
    trajectory_blob_ref: Mapped[str]
    step_count: Mapped[int | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
