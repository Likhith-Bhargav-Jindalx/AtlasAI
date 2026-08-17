import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Enum, ForeignKey, UniqueConstraint, func, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atlas_engine.db import Base

VIDEO_ASSET_KINDS = ("raw", "narrated_edit")
VIDEO_ASSET_STATUSES = ("processing", "ready", "failed")


class VideoAsset(Base):
    __tablename__ = "video_assets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    recording_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Enum(*VIDEO_ASSET_KINDS, name="video_asset_kind"), default="raw")
    page_index: Mapped[int] = mapped_column(default=0)
    storage_ref: Mapped[str]
    started_at_ms: Mapped[int | None] = mapped_column(BigInteger, default=None)
    transcript_ref: Mapped[str | None] = mapped_column(default=None)
    duration_seconds: Mapped[int | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(Enum(*VIDEO_ASSET_STATUSES, name="video_asset_status"), default="processing")
    # Points at one JSON blob holding every step's dom_action_event for this
    # video, keyed by order_index -- consolidated here instead of one JSONB
    # column per VideoStep row: this data has no current reader (nothing
    # queries it relationally), so keeping it out of Postgres entirely, and
    # out of per-row overhead, costs nothing functionally. None for videos
    # captured before this existed.
    dom_events_blob_ref: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    steps: Mapped[list["VideoStep"]] = relationship(back_populates="video_asset", cascade="all, delete-orphan")


class VideoStep(Base):
    __tablename__ = "video_steps"
    __table_args__ = (UniqueConstraint("video_asset_id", "order_index"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    video_asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("video_assets.id", ondelete="CASCADE"))
    order_index: Mapped[int]
    screenshot_ref: Mapped[str | None] = mapped_column(default=None)
    caption_text: Mapped[str | None] = mapped_column(default=None)
    offset_ms: Mapped[int | None] = mapped_column(BigInteger, default=None)
    narration_audio_ref: Mapped[str | None] = mapped_column(default=None)
    narration_transcript: Mapped[str | None] = mapped_column(default=None)
    duration_ms: Mapped[int | None] = mapped_column(default=None)

    video_asset: Mapped["VideoAsset"] = relationship(back_populates="steps")
