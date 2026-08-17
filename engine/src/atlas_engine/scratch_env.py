"""Repoints atlas_engine.db/storage's process-global state at a fresh,
zero-setup local SQLite database + local temp directory, for the lifetime
of the calling process. Used by the desktop exe's remote-submit mode: the
authoring pipeline's existing, unmodified code (atlas_engine.authoring.*,
atlas_engine.verify.certify) runs against this scratch store exactly as it
would against a real deployment — those modules only ever talk to
session_scope()/get_store(), never anything Postgres- or Azure-specific —
and the final result is submitted to the deployed backend separately (see
desktop/local_authoring.py). Nothing written here is meant to be durable;
it's discarded when the process exits.

Alembic's migration history uses raw ALTER-constraint operations SQLite
can't run (`NotImplementedError: No support for ALTER of constraints in
SQLite dialect`), so this bypasses migrations entirely and builds the
schema directly from the current ORM models via Base.metadata.create_all
— correct for a fresh, throwaway database that's never migrated forward,
wrong for anything meant to persist across a schema change.
"""

import tempfile
from pathlib import Path

from sqlalchemy import event, func, select

import atlas_engine.db as db_module
from atlas_engine.config import settings
from atlas_engine.db import Base
from atlas_engine.db.models import AuthoringRunEvent

_seq_listener_registered = False


def _assign_seq_for_sqlite(mapper, connection, target: AuthoringRunEvent) -> None:
    """AuthoringRunEvent.seq is a Postgres IDENTITY column — auto-assigned
    by a native sequence there, which SQLite has no equivalent for on a
    non-primary-key column (SQLite's own autoincrement rowid magic only
    applies to the table's actual INTEGER PRIMARY KEY, and seq isn't it
    here). Without this, every insert fails with a NOT NULL constraint
    violation on seq. Registered only in the scratch environment — the
    real deployment's Postgres database is never affected, and this
    process never touches a real Postgres-backed AuthoringRunEvent."""
    if target.seq is not None:
        return
    current_max = connection.execute(
        select(func.max(AuthoringRunEvent.seq)).where(AuthoringRunEvent.authoring_run_id == target.authoring_run_id)
    ).scalar()
    target.seq = (current_max or 0) + 1


def activate_scratch_environment() -> Path:
    """Call once, early — before any session_scope()/get_store() call in
    this process, and before importing anything that might call them at
    import time. Not safe to call more than once per process, or from a
    process that also needs its *real* database/storage settings for
    something else: this mutates process-global state and never restores
    it, which is fine for the desktop exe's remote-submit mode (the only
    thing in that mode that ever touches atlas_engine.db/storage directly
    is local authoring) but would be wrong to call from, say, a test
    process that also exercises the normal local-mode code path.

    Returns the scratch directory (informational only — callers don't
    need to clean it up; it lives under the OS temp dir and is
    process-lifetime-scoped, not something this module tracks further).
    """
    global _seq_listener_registered
    if not _seq_listener_registered:
        event.listen(AuthoringRunEvent, "before_insert", _assign_seq_for_sqlite)
        _seq_listener_registered = True

    scratch_dir = Path(tempfile.mkdtemp(prefix="atlas-scratch-"))
    run_root = scratch_dir / "runs"
    run_root.mkdir()

    settings.database_url = f"sqlite:///{scratch_dir / 'scratch.db'}"
    settings.run_root = run_root
    settings.azure_storage_connection_string = None
    settings.require_azure_storage = False

    db_module._engine = None
    db_module._SessionFactory = None
    Base.metadata.create_all(db_module.get_engine())

    return scratch_dir
