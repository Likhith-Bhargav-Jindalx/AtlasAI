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


AUTHORING_RUN_EVENT_LEVELS = ("info", "warning", "error")


class AuthoringRunEvent(Base):
    __tablename__ = "authoring_run_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    authoring_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("authoring_runs.id", ondelete="CASCADE"))
    timestamp: Mapped[datetime] = mapped_column(server_default=func.now())
    phase: Mapped[str] = mapped_column(Enum(*AUTHORING_RUN_PHASES, name="authoring_run_event_phase"))
    step_index: Mapped[int | None] = mapped_column(default=None)
    level: Mapped[str] = mapped_column(Enum(*AUTHORING_RUN_EVENT_LEVELS, name="authoring_run_event_level"), default="info")
    message: Mapped[str]

    authoring_run: Mapped["AuthoringRun"] = relationship()


CERTIFICATION_RUN_STATUSES = ("pass", "fail")


class CertificationRun(Base):
    __tablename__ = "certification_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    authoring_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("authoring_runs.id", ondelete="CASCADE"))
    run_number: Mapped[int]
    status: Mapped[str] = mapped_column(Enum(*CERTIFICATION_RUN_STATUSES, name="certification_run_status"))
    failure_evidence_blob_ref: Mapped[str | None] = mapped_column(default=None)

    authoring_run: Mapped["AuthoringRun"] = relationship()
