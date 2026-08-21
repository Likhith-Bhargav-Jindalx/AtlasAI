"""add ehr and specific_to to recording_requests

Revision ID: 409a85346913
Revises: 1fd82625da65
Create Date: 2026-08-21 12:08:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '409a85346913'
down_revision: Union[str, Sequence[str], None] = '1fd82625da65'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('recording_requests', sa.Column('ehr', sa.String(), nullable=True))
    op.add_column('recording_requests', sa.Column('specific_to', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('recording_requests', 'specific_to')
    op.drop_column('recording_requests', 'ehr')
