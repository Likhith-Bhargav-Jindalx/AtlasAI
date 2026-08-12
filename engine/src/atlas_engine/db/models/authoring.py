import uuid
from datetime import datetime

from sqlalchemy import Enum, ForeignKey, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atlas_engine.db import Base

AUTHORING_RUN_STATUSES = ("running", "waiting_for_developer", "completed", "failed", "cancelled")
AUTHORING_RUN_PHASES = ("replay", "extract", "brain", "golden", "certifying", "done")


class AuthoringRun(Base):
    __tablename__ = "authoring_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    recording_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"))
    golden_script_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("golden_scripts.id", ondelete="SET NULL"), default=None
    )
    status: Mapped[str] = mapped_column(Enum(*AUTHORING_RUN_STATUSES, name="authoring_run_status"), default="running")
    current_phase: Mapped[str] = mapped_column(Enum(*AUTHORING_RUN_PHASES, name="authoring_run_phase"), default="replay")
    repair_attempt: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(default=None)

    org: Mapped["Organization"] = relationship()
    recording: Mapped["Recording"] = relationship()
    golden_script: Mapped["GoldenScript | None"] = relationship()
