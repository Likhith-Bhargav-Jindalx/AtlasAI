"""add can_manage_form_options to users

Revision ID: 38bff1f43e0e
Revises: 9d9411eb3c65
Create Date: 2026-11-24 16:06:16.124684

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '38bff1f43e0e'
down_revision: Union[str, Sequence[str], None] = '9d9411eb3c65'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("can_manage_form_options", sa.Boolean(), nullable=False, server_default=sa.text("false")))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "can_manage_form_options")
