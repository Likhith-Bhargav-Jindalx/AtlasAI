import uuid

from atlas_engine.db import session_scope
from atlas_engine.db.models import AuthoringRun


def update_run(authoring_run_id: uuid.UUID, **fields) -> None:
    """Generic setter for AuthoringRun columns — every phase uses this to
    record its own status/current_phase/repair_attempt/golden_script_id/
    ended_at transitions, so the DB row is always the single source of
    truth a future API layer polls."""
    with session_scope() as session:
        run = session.get(AuthoringRun, authoring_run_id)
        if run is None:
            raise ValueError(f"No such authoring run: {authoring_run_id}")
        for key, value in fields.items():
            setattr(run, key, value)
