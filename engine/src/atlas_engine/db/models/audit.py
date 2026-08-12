import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, func, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from atlas_engine.db import Base, PortableJSON


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    action: Mapped[str]
    resource_type: Mapped[str]
    resource_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), default=None)
    phi_flag: Mapped[bool] = mapped_column(default=False)
    metadata_: Mapped[dict] = mapped_column("metadata", PortableJSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
