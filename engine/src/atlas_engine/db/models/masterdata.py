import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, UniqueConstraint, func, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atlas_engine.db import Base

# Pure lookup tables backing the recording form's structured fields (see
# docs/superpowers/specs/2026-08-19-recording-form-v1-design.md). Seeded via
# migration with the doc's known values — no admin CRUD in v1, rows are
# added by a follow-up migration until that's worth building.


class System(Base):
    __tablename__ = "systems"
    __table_args__ = (UniqueConstraint("org_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    name: Mapped[str]
    category: Mapped[str | None] = mapped_column(default=None)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    org: Mapped["Organization"] = relationship()


class Payer(Base):
    __tablename__ = "payers"
    __table_args__ = (UniqueConstraint("org_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    name: Mapped[str]
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    org: Mapped["Organization"] = relationship()


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (UniqueConstraint("org_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    name: Mapped[str]
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    org: Mapped["Organization"] = relationship()


class CarcCode(Base):
    __tablename__ = "carc_codes"
    __table_args__ = (UniqueConstraint("org_id", "code"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    code: Mapped[str]
    description: Mapped[str | None] = mapped_column(default=None)
    # Drives whether the form must also ask for `sub_reason` (e.g. CO-16
    # covers several distinct root causes — missing NDC, invalid taxonomy,
    # etc. — that a bare code can't disambiguate on its own).
    is_multi_use: Mapped[bool] = mapped_column(default=False)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    org: Mapped["Organization"] = relationship()


# Backs the recording form's Client/Action/EHR dropdowns (Section A) — see
# docs/superpowers/specs/2026-11-24-recording-form-options-crud-design.md.
# Unlike System/Payer/Client/CarcCode above, this one DOES have admin CRUD
# (POST/PATCH in api.py), gated to owner/admin.
FORM_OPTION_CATEGORIES = ("client", "action", "ehr")


class FormOption(Base):
    __tablename__ = "form_options"
    __table_args__ = (UniqueConstraint("org_id", "lob", "category", "label"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    lob: Mapped[str]
    category: Mapped[str]
    label: Mapped[str]
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    org: Mapped["Organization"] = relationship()


# A named sub-scenario under one specific action (e.g. "Denial Correction" →
# "Bundled Denial" / "Timely Filing Denial") — see
# docs/superpowers/specs/2026-11-25-action-subscenarios-design.md. No
# org_id/lob columns: scope is always inherited through action_id, so there
# is nothing here that could drift out of sync with its parent FormOption.
class Subscenario(Base):
    __tablename__ = "subscenarios"
    __table_args__ = (UniqueConstraint("action_id", "label"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("form_options.id", ondelete="CASCADE"))
    label: Mapped[str]
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    action: Mapped["FormOption"] = relationship()
