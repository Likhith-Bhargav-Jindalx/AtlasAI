"""add recording form v1 master data and fields

Revision ID: 1fd82625da65
Revises: 944557ce5eb2
Create Date: 2026-08-19 18:34:20.592546

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '1fd82625da65'
down_revision: Union[str, Sequence[str], None] = '944557ce5eb2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Seed values from docs/superpowers/specs/2026-08-19-recording-form-v1-design.md
# (source: atlas-recording-form-v1.md section A.2 and Section B `code`).
# Seeded per-organization, once for every existing org — a fresh org
# created after this migration gets none, matching "no admin UI yet."
_SYSTEMS = [
    ("Exa", "PMS/EHR"), ("eCW", "PMS/EHR"), ("Athena", "PMS/EHR"),
    ("Waystar", "Clearinghouse"), ("Stedi", "Clearinghouse"),
    ("Availity", "Multi-payer portal"),
    ("Cigna", "Payer portal"), ("UHC", "Payer portal"), ("UMR", "Payer portal"),
    ("Integranet", "Payer portal"), ("Humana Military", "Payer portal"),
    ("Tricare For Life", "Payer portal"), ("Community Health Choice", "Payer portal"),
    ("Medicare IVR", "Other"), ("NPPES", "Other"), ("Payer phone (no portal)", "Other"),
]
_PAYERS = [
    "Cigna", "UHC", "UMR", "Integranet", "Humana Military",
    "Tricare For Life", "Community Health Choice",
]
_CARC_CODES = [
    ("CO-16", "Claim/service lacks information needed for adjudication", True),
]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('carc_codes',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('code', sa.String(), nullable=False),
    sa.Column('description', sa.String(), nullable=True),
    sa.Column('is_multi_use', sa.Boolean(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'code')
    )
    op.create_table('clients',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'name')
    )
    op.create_table('payers',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'name')
    )
    op.create_table('systems',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('category', sa.String(), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'name')
    )
    # add_column does NOT auto-create the Postgres ENUM type a sa.Enum
    # column needs (unlike create_table) — must create it explicitly first,
    # or the ALTER TABLE fails with "type ... does not exist".
    sa.Enum('AR', 'EV', 'Generic', name='recording_request_lob').create(op.get_bind(), checkfirst=True)
    sa.Enum('Process', 'Scenario', name='recording_request_category').create(op.get_bind(), checkfirst=True)
    sa.Enum('Client-specific', 'Generic', name='recording_request_reuse_scope').create(op.get_bind(), checkfirst=True)

    op.add_column('recording_requests', sa.Column('lob', sa.Enum('AR', 'EV', 'Generic', name='recording_request_lob'), nullable=True))
    op.add_column('recording_requests', sa.Column('category', sa.Enum('Process', 'Scenario', name='recording_request_category'), nullable=True))
    op.add_column('recording_requests', sa.Column('action', sa.String(), nullable=True))
    op.add_column('recording_requests', sa.Column('system_id', sa.Uuid(), nullable=True))
    op.add_column('recording_requests', sa.Column('client_scope', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True))
    op.add_column('recording_requests', sa.Column('reuse_scope', sa.Enum('Client-specific', 'Generic', name='recording_request_reuse_scope'), nullable=True))
    op.create_foreign_key('fk_recording_requests_system_id', 'recording_requests', 'systems', ['system_id'], ['id'], ondelete='SET NULL')
    op.add_column('recordings', sa.Column('payer_scope', sa.String(), nullable=True))
    op.add_column('recordings', sa.Column('payer_ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True))
    op.add_column('recordings', sa.Column('other_system_ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True))
    op.add_column('recordings', sa.Column('carc_code_id', sa.Uuid(), nullable=True))
    op.add_column('recordings', sa.Column('code_free_text', sa.String(), nullable=True))
    op.add_column('recordings', sa.Column('sub_reason', sa.String(), nullable=True))
    op.add_column('recordings', sa.Column('resolution_path', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True))
    op.add_column('recordings', sa.Column('evidence_required', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True))
    op.add_column('recordings', sa.Column('report_id', sa.String(), nullable=True))
    op.add_column('recordings', sa.Column('date_param_logic', sa.String(), nullable=True))
    op.add_column('recordings', sa.Column('output_destination', sa.String(), nullable=True))
    op.add_column('recordings', sa.Column('post_processing', sa.String(), nullable=True))
    op.add_column('recordings', sa.Column('scenario_description', sa.String(), nullable=True))
    op.create_foreign_key('fk_recordings_carc_code_id', 'recordings', 'carc_codes', ['carc_code_id'], ['id'], ondelete='SET NULL')

    # Seed the doc's known values for every existing org.
    bind = op.get_bind()
    org_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM organizations")).fetchall()]
    systems_table = sa.table(
        'systems', sa.column('id', sa.Uuid()), sa.column('org_id', sa.Uuid()),
        sa.column('name', sa.String()), sa.column('category', sa.String()), sa.column('active', sa.Boolean()),
    )
    payers_table = sa.table(
        'payers', sa.column('id', sa.Uuid()), sa.column('org_id', sa.Uuid()),
        sa.column('name', sa.String()), sa.column('active', sa.Boolean()),
    )
    carc_codes_table = sa.table(
        'carc_codes', sa.column('id', sa.Uuid()), sa.column('org_id', sa.Uuid()),
        sa.column('code', sa.String()), sa.column('description', sa.String()),
        sa.column('is_multi_use', sa.Boolean()), sa.column('active', sa.Boolean()),
    )
    for org_id in org_ids:
        bind.execute(systems_table.insert(), [
            {"id": uuid.uuid4(), "org_id": org_id, "name": name, "category": category, "active": True}
            for name, category in _SYSTEMS
        ])
        bind.execute(payers_table.insert(), [
            {"id": uuid.uuid4(), "org_id": org_id, "name": name, "active": True}
            for name in _PAYERS
        ])
        bind.execute(carc_codes_table.insert(), [
            {"id": uuid.uuid4(), "org_id": org_id, "code": code, "description": description, "is_multi_use": is_multi_use, "active": True}
            for code, description, is_multi_use in _CARC_CODES
        ])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_recordings_carc_code_id', 'recordings', type_='foreignkey')
    op.drop_column('recordings', 'scenario_description')
    op.drop_column('recordings', 'post_processing')
    op.drop_column('recordings', 'output_destination')
    op.drop_column('recordings', 'date_param_logic')
    op.drop_column('recordings', 'report_id')
    op.drop_column('recordings', 'evidence_required')
    op.drop_column('recordings', 'resolution_path')
    op.drop_column('recordings', 'sub_reason')
    op.drop_column('recordings', 'code_free_text')
    op.drop_column('recordings', 'carc_code_id')
    op.drop_column('recordings', 'other_system_ids')
    op.drop_column('recordings', 'payer_ids')
    op.drop_column('recordings', 'payer_scope')
    op.drop_constraint('fk_recording_requests_system_id', 'recording_requests', type_='foreignkey')
    op.drop_column('recording_requests', 'reuse_scope')
    op.drop_column('recording_requests', 'client_scope')
    op.drop_column('recording_requests', 'system_id')
    op.drop_column('recording_requests', 'action')
    op.drop_column('recording_requests', 'category')
    op.drop_column('recording_requests', 'lob')
    op.drop_table('systems')
    op.drop_table('payers')
    op.drop_table('clients')
    op.drop_table('carc_codes')

    for enum_name in ('recording_request_lob', 'recording_request_category', 'recording_request_reuse_scope'):
        op.execute(f'DROP TYPE IF EXISTS {enum_name}')
