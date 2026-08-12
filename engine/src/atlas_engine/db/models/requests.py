import uuid
from datetime import datetime

from sqlalchemy import Enum, ForeignKey, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atlas_engine.db import Base

RECORDING_REQUEST_STATUSES = ("open", "submitted", "fulfilled", "cancelled")


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

    org: Mapped["Organization"] = relationship()
    requester: Mapped["User"] = relationship(foreign_keys=[requested_by])
    claimant: Mapped["User | None"] = relationship(foreign_keys=[claimed_by])
    portal_profile: Mapped["PortalProfile"] = relationship()
    fulfilled_golden_script: Mapped["GoldenScript | None"] = relationship(foreign_keys=[fulfilled_golden_script_id])
