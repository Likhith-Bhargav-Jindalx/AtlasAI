"""move page extraction, video step dom event, and heal diff data to blob storage

Revision ID: 944557ce5eb2
Revises: 3e50ac7f06d1
Create Date: 2026-08-17 16:48:10.271230

"""
import json
from collections import defaultdict
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '944557ce5eb2'
down_revision: Union[str, Sequence[str], None] = '3e50ac7f06d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Deliberately not importing atlas_engine.storage at module level —
    # this migration must still be importable (and every *other* migration
    # runnable) even in an environment where atlas_engine isn't installed
    # (e.g. a bare `alembic` invocation against a pyproject-less checkout).
    # Imported lazily, inside upgrade(), only when there's actually data to
    # migrate.
    conn = op.get_bind()

    op.add_column('page_extractions', sa.Column('data_blob_ref', sa.String(), nullable=True))
    op.add_column('video_assets', sa.Column('dom_events_blob_ref', sa.String(), nullable=True))
    op.add_column('heal_events', sa.Column('diff_summary_blob_ref', sa.String(), nullable=True))

    extraction_rows = conn.execute(sa.text("SELECT id, data FROM page_extractions")).fetchall()
    video_step_rows = conn.execute(
        sa.text("SELECT video_asset_id, order_index, dom_action_event FROM video_steps WHERE dom_action_event IS NOT NULL")
    ).fetchall()
    heal_event_rows = conn.execute(
        sa.text("SELECT id, diff_summary FROM heal_events WHERE diff_summary IS NOT NULL")
    ).fetchall()

    if extraction_rows or video_step_rows or heal_event_rows:
        from atlas_engine.storage import get_store  # noqa: PLC0415 -- see comment above

        store = get_store()

        for row in extraction_rows:
            blob_ref = store.write_json(f"migrations/944557ce5eb2/page-extractions/{row.id}.json", row.data)
            conn.execute(
                sa.text("UPDATE page_extractions SET data_blob_ref = :ref WHERE id = :id"),
                {"ref": blob_ref, "id": row.id},
            )

        events_by_video: dict = defaultdict(dict)
        for row in video_step_rows:
            events_by_video[row.video_asset_id][str(row.order_index)] = row.dom_action_event
        for video_asset_id, events in events_by_video.items():
            blob_ref = store.write_json(f"migrations/944557ce5eb2/video-dom-events/{video_asset_id}.json", events)
            conn.execute(
                sa.text("UPDATE video_assets SET dom_events_blob_ref = :ref WHERE id = :id"),
                {"ref": blob_ref, "id": video_asset_id},
            )

        for row in heal_event_rows:
            blob_ref = store.write_json(f"migrations/944557ce5eb2/heal-events/{row.id}.json", row.diff_summary)
            conn.execute(
                sa.text("UPDATE heal_events SET diff_summary_blob_ref = :ref WHERE id = :id"),
                {"ref": blob_ref, "id": row.id},
            )

    op.drop_column('heal_events', 'diff_summary')
    op.drop_column('page_extractions', 'data')
    op.drop_column('video_steps', 'dom_action_event')
    op.alter_column('page_extractions', 'data_blob_ref', nullable=False)


def downgrade() -> None:
    """Schema-only — the blob content this migration wrote isn't pulled
    back into Postgres. Acceptable for this migration: it exists to get
    large/never-queried JSON out of the database, and a downgrade that
    faithfully restored it would defeat that purpose. Existing blob refs
    are left as unreachable orphans in blob storage, not deleted (a schema
    downgrade shouldn't also destroy data)."""
    op.add_column('video_steps', sa.Column('dom_action_event', sa.JSON(), autoincrement=False, nullable=True))
    op.add_column('page_extractions', sa.Column('data', sa.JSON(), autoincrement=False, nullable=False, server_default='{}'))
    op.add_column('heal_events', sa.Column('diff_summary', sa.JSON(), autoincrement=False, nullable=True))
    op.alter_column('page_extractions', 'data', server_default=None)

    op.drop_column('heal_events', 'diff_summary_blob_ref')
    op.drop_column('video_assets', 'dom_events_blob_ref')
    op.drop_column('page_extractions', 'data_blob_ref')
