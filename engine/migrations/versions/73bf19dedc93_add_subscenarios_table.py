"""add subscenarios table

Revision ID: 73bf19dedc93
Revises: 38bff1f43e0e
Create Date: 2026-11-25 17:56:57.744966

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '73bf19dedc93'
down_revision: Union[str, Sequence[str], None] = '38bff1f43e0e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "subscenarios",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("action_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["action_id"], ["form_options.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("action_id", "label"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("subscenarios")
