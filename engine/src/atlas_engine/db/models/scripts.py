import uuid
from datetime import datetime

from sqlalchemy import Enum, ForeignKey, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atlas_engine.db import Base, PortableJSON

GOLDEN_SCRIPT_STATUSES = ("draft", "certified", "partially_certified", "active", "deprecated")
HEAL_TRIGGERS = ("runtime_auto", "on_demand")
HEAL_STATUSES = ("pending_approval", "approved", "rejected", "auto_approved")


class GoldenScript(Base):
    __tablename__ = "golden_scripts"
    __table_args__ = (UniqueConstraint("portal_profile_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    portal_profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("portal_profiles.id", ondelete="CASCADE"))
    version: Mapped[int]
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("golden_scripts.id", ondelete="SET NULL"), default=None
    )
    script_blob_ref: Mapped[str]
    input_schema: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    output_schema: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    status: Mapped[str] = mapped_column(Enum(*GOLDEN_SCRIPT_STATUSES, name="golden_script_status"), default="draft")
    created_from_recording_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recordings.id", ondelete="SET NULL"), default=None
    )
    created_from_heal_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("heal_events.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    org: Mapped["Organization"] = relationship()
    portal_profile: Mapped["PortalProfile"] = relationship()
    parent_version: Mapped["GoldenScript | None"] = relationship(remote_side=[id])
    created_from_recording: Mapped["Recording | None"] = relationship()
    # NOT the inverse of HealEvent.proposed_version below — that's a second,
    # independent FK (proposed_version_id) pointing the other way. These are
    # two separate many-to-one relationships that happen to link the same
    # two tables, not one bidirectional relationship, so no back_populates.
    # post_update=True breaks the insert-order cycle with HealEvent.proposed_version:
    # SQLAlchemy inserts both rows with this FK left NULL, then issues a
    # follow-up UPDATE once both exist — the ORM-level equivalent of
    # db/schema.sql's ALTER TABLE ... ADD CONSTRAINT after both tables exist.
    created_from_heal_event: Mapped["HealEvent | None"] = relationship(
        foreign_keys=[created_from_heal_event_id], post_update=True
    )


class HealEvent(Base):
    __tablename__ = "heal_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    golden_script_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("golden_scripts.id", ondelete="CASCADE"))
    proposed_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("golden_scripts.id", ondelete="SET NULL"), default=None
    )
    trigger: Mapped[str] = mapped_column(Enum(*HEAL_TRIGGERS, name="heal_trigger"))
    failure_evidence_blob_ref: Mapped[str | None] = mapped_column(default=None)
    # A per-step diff (old vs. new selector/gesture/wait) -- stored as a
    # blob, not inline JSONB, since it's never queried relationally, only
    # ever read whole. See verify/heal.heal_golden_script.
    diff_summary_blob_ref: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(Enum(*HEAL_STATUSES, name="heal_status"), default="pending_approval")
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(default=None)

    org: Mapped["Organization"] = relationship()
    golden_script: Mapped["GoldenScript"] = relationship(foreign_keys=[golden_script_id])
    proposed_version: Mapped["GoldenScript | None"] = relationship(foreign_keys=[proposed_version_id])


class ActionCache(Base):
    __tablename__ = "action_cache"
    __table_args__ = (UniqueConstraint("golden_script_id", "step_index", "cache_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    golden_script_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("golden_scripts.id", ondelete="CASCADE"))
    step_index: Mapped[int]
    cache_key: Mapped[str]
    selector_candidates: Mapped[list] = mapped_column(PortableJSON)
    method: Mapped[str | None] = mapped_column(default=None)
    last_verified_at: Mapped[datetime | None] = mapped_column(default=None)
    miss_count: Mapped[int] = mapped_column(default=0)

    golden_script: Mapped["GoldenScript"] = relationship()
