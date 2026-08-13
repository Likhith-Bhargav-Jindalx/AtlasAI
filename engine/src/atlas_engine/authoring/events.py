import uuid

from atlas_engine.db import session_scope
from atlas_engine.db.models import AuthoringRunEvent


def log_event(
    authoring_run_id: uuid.UUID, *, phase: str, message: str, step_index: int | None = None, level: str = "info",
) -> None:
    """Appends one row to the run's event log — the source the future
    /authoring-runs/{id} polling endpoint and the UI's scrolling log both
    read from. `seq` is a Postgres IDENTITY column, assigned automatically
    on insert; callers never set it."""
    with session_scope() as session:
        session.add(AuthoringRunEvent(
            authoring_run_id=authoring_run_id, phase=phase, step_index=step_index, level=level, message=message,
        ))
