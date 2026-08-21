import uuid
from datetime import datetime

from sqlalchemy import Enum, ForeignKey, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atlas_engine.db import Base, PortableJSON

RECORDING_REQUEST_STATUSES = ("open", "submitted", "fulfilled", "cancelled")

# Recording form v1 (docs/superpowers/specs/2026-08-19-recording-form-v1-design.md)
# — Section A branch keys. "Generic" replaces the source doc's separate
# PA/PP/Reporting-Admin lobs; which one a Generic `action` actually came
# from is tracked in frontend schema data, not here, since it only affects
# which Section B fields show — not anything this table needs to know.
RECORDING_REQUEST_LOBS = ("AR", "EV", "Generic")
RECORDING_REQUEST_CATEGORIES = ("Process", "Scenario")
RECORDING_REQUEST_REUSE_SCOPES = ("Client-specific", "Generic")


class RecordingRequest(Base):
    __tablename__ = "recording_requests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    portal_profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("portal_profiles.id", ondelete="CASCADE"))
    target_url: Mapped[str]
    title: Mapped[str]
    description: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(
        Enum(*RECORDING_REQUEST_STATUSES, name="recording_request_status"), default="open"
    )
    claimed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    claimed_at: Mapped[datetime | None] = mapped_column(default=None)
    fulfilled_golden_script_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("golden_scripts.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # --- Recording form v1, Section A (see module docstring above) ---
    lob: Mapped[str | None] = mapped_column(Enum(*RECORDING_REQUEST_LOBS, name="recording_request_lob"), default=None)
    category: Mapped[str | None] = mapped_column(
        Enum(*RECORDING_REQUEST_CATEGORIES, name="recording_request_category"), default=None
    )
    action: Mapped[str | None] = mapped_column(default=None)
    system_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("systems.id", ondelete="SET NULL"), default=None)
    # List of client UUIDs (as strings), or ["generic"] meaning "all clients"
    # — a sentinel rather than a real Client row, since "applies to every
    # client" isn't itself a client.
    client_scope: Mapped[list | None] = mapped_column(PortableJSON, default=None)
    reuse_scope: Mapped[str | None] = mapped_column(
        Enum(*RECORDING_REQUEST_REUSE_SCOPES, name="recording_request_reuse_scope"), default=None
    )
    # Recording form v2 — EHR system name (plain text, not a FK) and
    # whether the recording is specific to an EHR or a Payor.
    ehr: Mapped[str | None] = mapped_column(default=None)
    specific_to: Mapped[str | None] = mapped_column(default=None)

    org: Mapped["Organization"] = relationship()
    requester: Mapped["User"] = relationship(foreign_keys=[requested_by])
    claimant: Mapped["User | None"] = relationship(foreign_keys=[claimed_by])
    portal_profile: Mapped["PortalProfile"] = relationship()
    fulfilled_golden_script: Mapped["GoldenScript | None"] = relationship(foreign_keys=[fulfilled_golden_script_id])
    system: Mapped["System | None"] = relationship()
