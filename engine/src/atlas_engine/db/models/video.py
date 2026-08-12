import uuid
from datetime import datetime

from sqlalchemy import Enum, ForeignKey, UniqueConstraint, func, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atlas_engine.db import Base, PortableJSON

VIDEO_ASSET_KINDS = ("raw", "narrated_edit")
VIDEO_ASSET_STATUSES = ("processing", "ready", "failed")


class VideoAsset(Base):
    __tablename__ = "video_assets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    recording_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Enum(*VIDEO_ASSET_KINDS, name="video_asset_kind"), default="raw")
    storage_ref: Mapped[str]
    transcript_ref: Mapped[str | None] = mapped_column(default=None)
    duration_seconds: Mapped[int | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(Enum(*VIDEO_ASSET_STATUSES, name="video_asset_status"), default="processing")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    steps: Mapped[list["VideoStep"]] = relationship(back_populates="video_asset", cascade="all, delete-orphan")


class VideoStep(Base):
    __tablename__ = "video_steps"
    __table_args__ = (UniqueConstraint("video_asset_id", "order_index"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    video_asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("video_assets.id", ondelete="CASCADE"))
    order_index: Mapped[int]
    screenshot_ref: Mapped[str | None] = mapped_column(default=None)
    dom_action_event: Mapped[dict | None] = mapped_column(PortableJSON, default=None)
    caption_text: Mapped[str | None] = mapped_column(default=None)
    narration_audio_ref: Mapped[str | None] = mapped_column(default=None)
    narration_transcript: Mapped[str | None] = mapped_column(default=None)
    duration_ms: Mapped[int | None] = mapped_column(default=None)

    video_asset: Mapped["VideoAsset"] = relationship(back_populates="steps")
