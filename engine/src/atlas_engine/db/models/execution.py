import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Enum, ForeignKey, func, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atlas_engine.db import Base, PortableJSON

RUN_EXECUTION_LOCATIONS = ("desktop_app", "cloud", "external")
RUN_STATUSES = ("running", "succeeded", "failed", "healed")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    golden_script_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("golden_scripts.id", ondelete="CASCADE"))
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    execution_location: Mapped[str] = mapped_column(Enum(*RUN_EXECUTION_LOCATIONS, name="run_execution_location"))
    status: Mapped[str] = mapped_column(Enum(*RUN_STATUSES, name="run_status"), default="running")
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    result_blob_ref: Mapped[str | None] = mapped_column(default=None)
    cost_usage: Mapped[dict] = mapped_column(PortableJSON, default=dict)

    golden_script: Mapped["GoldenScript"] = relationship()


class UsageLedgerEntry(Base):
    __tablename__ = "usage_ledger"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"), default=None)
    recording_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recordings.id", ondelete="SET NULL"), default=None
    )
    provider: Mapped[str]
    model: Mapped[str]
    tokens_in: Mapped[int] = mapped_column(default=0)
    tokens_out: Mapped[int] = mapped_column(default=0)
    cost_usd: Mapped[Decimal] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
