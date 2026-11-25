"""add subscenario column to recording_requests

Revision ID: f7aeb04bdb00
Revises: 73bf19dedc93
Create Date: 2026-11-25 18:05:54.956371

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7aeb04bdb00'
down_revision: Union[str, Sequence[str], None] = '73bf19dedc93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("recording_requests", sa.Column("subscenario", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("recording_requests", "subscenario")
