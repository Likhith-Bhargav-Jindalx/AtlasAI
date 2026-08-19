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
    # Opt-in pacing for slow/flaky portals — absent (None) means no pacing,
    # byte-for-byte the same replay/brain/codegen behavior as before this
    # feature existed. Mirrors the previous app's portal.pacing config.
    pacing_pre_action_ms: Mapped[int | None] = mapped_column(default=None)
    pacing_settle_after_nav_ms: Mapped[int | None] = mapped_column(default=None)
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
    title: Mapped[str | None] = mapped_column(default=None)
    description: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(Enum(*RECORDING_STATUSES, name="recording_status"), default="capturing")
    capture_blob_prefix: Mapped[str]
    has_audio: Mapped[bool] = mapped_column(default=False)
    duration_seconds: Mapped[int | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(default=None)

    # --- Recording form v1, Section B (see requests.py's module docstring
    # for the design doc) — filled in on submit-after-stop, once the SME
    # has full context of what actually happened. Every field is optional
    # since which ones apply depends on the request's lob/action. ---
    payer_scope: Mapped[str | None] = mapped_column(default=None)
    payer_ids: Mapped[list | None] = mapped_column(PortableJSON, default=None)
    other_system_ids: Mapped[list | None] = mapped_column(PortableJSON, default=None)
    carc_code_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("carc_codes.id", ondelete="SET NULL"), default=None)
    # Free-text fallback for a rejection code with no CARC/RARC entry (e.g.
    # a clearinghouse-specific rejection) — see the doc's `code` field notes.
    code_free_text: Mapped[str | None] = mapped_column(default=None)
    sub_reason: Mapped[str | None] = mapped_column(default=None)
    resolution_path: Mapped[list | None] = mapped_column(PortableJSON, default=None)
    evidence_required: Mapped[list | None] = mapped_column(PortableJSON, default=None)
    report_id: Mapped[str | None] = mapped_column(default=None)
    date_param_logic: Mapped[str | None] = mapped_column(default=None)
    output_destination: Mapped[str | None] = mapped_column(default=None)
    post_processing: Mapped[str | None] = mapped_column(default=None)
    scenario_description: Mapped[str | None] = mapped_column(default=None)

    org: Mapped["Organization"] = relationship()
    portal_profile: Mapped["PortalProfile"] = relationship()
    user: Mapped["User | None"] = relationship(foreign_keys=[user_id])
    carc_code: Mapped["CarcCode | None"] = relationship()


class ReferenceTrajectory(Base):
    __tablename__ = "reference_trajectories"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recording_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"))
    trajectory_blob_ref: Mapped[str]
    step_count: Mapped[int | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class PageExtraction(Base):
    __tablename__ = "page_extractions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recording_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"))
    page_index: Mapped[int] = mapped_column(default=0)
    url: Mapped[str]
    # Extracted table/field data can be genuinely large (a full report's
    # worth of rows) and is never queried relationally -- stored as a blob,
    # not inline JSONB. See recording_service.persist_recording and
    # authoring/phases._persist_certify_result for the two write sites.
    data_blob_ref: Mapped[str]
    extracted_at: Mapped[datetime] = mapped_column(server_default=func.now())

    recording: Mapped["Recording"] = relationship()
