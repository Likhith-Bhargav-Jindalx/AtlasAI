"""initial schema

Revision ID: b3e268daf034
Revises:
Create Date: 2026-08-12 17:58:47.558342

Table creation order below is hand-ordered by foreign-key dependency —
Alembic's autogenerate topological sort gives up on ordering the *entire*
graph once it hits the golden_scripts <-> heal_events cycle (see the
SAWarning at generation time), not just that one pair, so the raw
autogenerate output was unusable as-is. Column/constraint definitions are
autogenerate's original output, verified against atlas_engine.db.models;
only statement order (and the one deferred FK) was hand-fixed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b3e268daf034'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('organizations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('slug', sa.String(), nullable=False),
    sa.Column('plan', sa.Enum('internal', 'team', 'enterprise', name='org_plan'), nullable=False),
    sa.Column('settings', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('email', sa.String(), nullable=False),
    sa.Column('password_hash', sa.String(), nullable=False),
    sa.Column('role', sa.Enum('owner', 'admin', 'member', name='user_role'), nullable=False),
    sa.Column('display_name', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'email')
    )
    op.create_table('portal_profiles',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('domain', sa.String(), nullable=False),
    sa.Column('display_name', sa.String(), nullable=True),
    sa.Column('inferred_login_url', sa.String(), nullable=True),
    sa.Column('inferred_success_signal', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('confidence_score', sa.Numeric(), nullable=True),
    sa.Column('metadata', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('first_recorded_at', sa.DateTime(), nullable=True),
    sa.Column('last_recorded_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'domain')
    )
    op.create_table('secrets_refs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('portal_profile_id', sa.Uuid(), nullable=True),
    sa.Column('label', sa.String(), nullable=False),
    sa.Column('secret_provider', sa.Enum('env', 'azure_keyvault', name='secret_provider'), nullable=False),
    sa.Column('secret_key_ref', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['portal_profile_id'], ['portal_profiles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('portal_profile_id', 'label')
    )
    op.create_table('recordings',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('portal_profile_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('status', sa.Enum('capturing', 'captured', 'failed', 'discarded', name='recording_status'), nullable=False),
    sa.Column('capture_blob_prefix', sa.String(), nullable=False),
    sa.Column('has_audio', sa.Boolean(), nullable=False),
    sa.Column('duration_seconds', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['portal_profile_id'], ['portal_profiles.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('reference_trajectories',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('recording_id', sa.Uuid(), nullable=False),
    sa.Column('trajectory_blob_ref', sa.String(), nullable=False),
    sa.Column('step_count', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['recording_id'], ['recordings.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('golden_scripts',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('portal_profile_id', sa.Uuid(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('parent_version_id', sa.Uuid(), nullable=True),
    sa.Column('script_blob_ref', sa.String(), nullable=False),
    sa.Column('input_schema', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('output_schema', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.Enum('draft', 'certified', 'active', 'deprecated', name='golden_script_status'), nullable=False),
    sa.Column('created_from_recording_id', sa.Uuid(), nullable=True),
    sa.Column('created_from_heal_event_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    # created_from_heal_event_id's FK is added below via op.create_foreign_key,
    # AFTER heal_events is created — golden_scripts and heal_events reference
    # each other, so one of the two constraints has to be deferred.
    sa.ForeignKeyConstraint(['created_from_recording_id'], ['recordings.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['parent_version_id'], ['golden_scripts.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['portal_profile_id'], ['portal_profiles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('portal_profile_id', 'version')
    )
    op.create_table('heal_events',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('golden_script_id', sa.Uuid(), nullable=False),
    sa.Column('proposed_version_id', sa.Uuid(), nullable=True),
    sa.Column('trigger', sa.Enum('runtime_auto', 'on_demand', name='heal_trigger'), nullable=False),
    sa.Column('failure_evidence_blob_ref', sa.String(), nullable=True),
    sa.Column('diff_summary', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('status', sa.Enum('pending_approval', 'approved', 'rejected', 'auto_approved', name='heal_status'), nullable=False),
    sa.Column('resolved_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('resolved_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['golden_script_id'], ['golden_scripts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['proposed_version_id'], ['golden_scripts.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['resolved_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_foreign_key(
        'fk_golden_scripts_heal_event',
        'golden_scripts', 'heal_events',
        ['created_from_heal_event_id'], ['id'],
        ondelete='SET NULL',
    )
    op.create_table('action_cache',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('golden_script_id', sa.Uuid(), nullable=False),
    sa.Column('step_index', sa.Integer(), nullable=False),
    sa.Column('cache_key', sa.String(), nullable=False),
    sa.Column('selector_candidates', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('method', sa.String(), nullable=True),
    sa.Column('last_verified_at', sa.DateTime(), nullable=True),
    sa.Column('miss_count', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['golden_script_id'], ['golden_scripts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('golden_script_id', 'step_index', 'cache_key')
    )
    op.create_table('runs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('golden_script_id', sa.Uuid(), nullable=False),
    sa.Column('triggered_by', sa.Uuid(), nullable=True),
    sa.Column('execution_location', sa.Enum('desktop_app', 'cloud', 'external', name='run_execution_location'), nullable=False),
    sa.Column('status', sa.Enum('running', 'succeeded', 'failed', 'healed', name='run_status'), nullable=False),
    sa.Column('started_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.Column('result_blob_ref', sa.String(), nullable=True),
    sa.Column('cost_usage', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.ForeignKeyConstraint(['golden_script_id'], ['golden_scripts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['triggered_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('usage_ledger',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('run_id', sa.Uuid(), nullable=True),
    sa.Column('recording_id', sa.Uuid(), nullable=True),
    sa.Column('provider', sa.String(), nullable=False),
    sa.Column('model', sa.String(), nullable=False),
    sa.Column('tokens_in', sa.Integer(), nullable=False),
    sa.Column('tokens_out', sa.Integer(), nullable=False),
    sa.Column('cost_usd', sa.Numeric(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['recording_id'], ['recordings.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('video_assets',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('recording_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.Enum('raw', 'narrated_edit', name='video_asset_kind'), nullable=False),
    sa.Column('storage_ref', sa.String(), nullable=False),
    sa.Column('transcript_ref', sa.String(), nullable=True),
    sa.Column('duration_seconds', sa.Integer(), nullable=True),
    sa.Column('status', sa.Enum('processing', 'ready', 'failed', name='video_asset_status'), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['recording_id'], ['recordings.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('video_steps',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('video_asset_id', sa.Uuid(), nullable=False),
    sa.Column('order_index', sa.Integer(), nullable=False),
    sa.Column('screenshot_ref', sa.String(), nullable=True),
    sa.Column('dom_action_event', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('caption_text', sa.String(), nullable=True),
    sa.Column('narration_audio_ref', sa.String(), nullable=True),
    sa.Column('narration_transcript', sa.String(), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['video_asset_id'], ['video_assets.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('video_asset_id', 'order_index')
    )
    op.create_table('audit_log',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('action', sa.String(), nullable=False),
    sa.Column('resource_type', sa.String(), nullable=False),
    sa.Column('resource_id', sa.Uuid(), nullable=True),
    sa.Column('phi_flag', sa.Boolean(), nullable=False),
    sa.Column('metadata', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('audit_log')
    op.drop_table('video_steps')
    op.drop_table('video_assets')
    op.drop_table('usage_ledger')
    op.drop_table('runs')
    op.drop_table('action_cache')
    op.drop_constraint('fk_golden_scripts_heal_event', 'golden_scripts', type_='foreignkey')
    op.drop_table('heal_events')
    op.drop_table('golden_scripts')
    op.drop_table('reference_trajectories')
    op.drop_table('recordings')
    op.drop_table('secrets_refs')
    op.drop_table('portal_profiles')
    op.drop_table('users')
    op.drop_table('organizations')
    # sa.Enum on a create_table call creates a standalone Postgres TYPE that
    # drop_table does not remove on its own — without this, downgrade then
    # upgrade fails with "type already exists".
    for enum_name in (
        'org_plan', 'user_role', 'secret_provider', 'recording_status',
        'golden_script_status', 'heal_trigger', 'heal_status',
        'run_execution_location', 'run_status', 'video_asset_kind',
        'video_asset_status',
    ):
        op.execute(f'DROP TYPE IF EXISTS {enum_name}')
