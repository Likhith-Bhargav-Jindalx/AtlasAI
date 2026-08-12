import uuid
from datetime import datetime

from sqlalchemy import Enum, ForeignKey, UniqueConstraint, func, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atlas_engine.db import Base, PortableJSON

ORG_PLANS = ("internal", "team", "enterprise")
USER_ROLES = ("owner", "admin", "member")
JOB_FUNCTIONS = ("operations_agent", "developer")


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str]
    slug: Mapped[str] = mapped_column(unique=True)
    plan: Mapped[str] = mapped_column(Enum(*ORG_PLANS, name="org_plan"), default="internal")
    settings: Mapped[dict] = mapped_column(PortableJSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    users: Mapped[list["User"]] = relationship(back_populates="org", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("org_id", "email"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    email: Mapped[str]
    password_hash: Mapped[str]
    role: Mapped[str] = mapped_column(Enum(*USER_ROLES, name="user_role"), default="member")
    job_function: Mapped[str | None] = mapped_column(Enum(*JOB_FUNCTIONS, name="job_function"), default=None)
    display_name: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    org: Mapped["Organization"] = relationship(back_populates="users")
