import hashlib
import uuid
from datetime import datetime, timezone

from atlas_engine.db import session_scope
from atlas_engine.db.models import ActionCache


def compute_cache_key(*, instruction: str, url_pattern: str, dom_fingerprint: str) -> str:
    """Deliberately excludes parameter values — a script run with different
    input data still hits the same cache entry (architecture spec §7.1)."""
    raw = f"{instruction}|{url_pattern}|{dom_fingerprint}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ActionCacheStore:
    """DB-backed cache of resolved selectors per (golden_script, step,
    cache_key). A miss is never fatal — callers fall through to live
    re-resolution and write a fresh entry."""

    def get(self, golden_script_id: uuid.UUID, *, step_index: int, cache_key: str) -> list[dict] | None:
        with session_scope() as session:
            row = (
                session.query(ActionCache)
                .filter_by(golden_script_id=golden_script_id, step_index=step_index, cache_key=cache_key)
                .one_or_none()
            )
            return row.selector_candidates if row else None

    def put(
        self,
        golden_script_id: uuid.UUID,
        *,
        step_index: int,
        cache_key: str,
        selector_candidates: list[dict],
        method: str | None = None,
    ) -> None:
        with session_scope() as session:
            row = (
                session.query(ActionCache)
                .filter_by(golden_script_id=golden_script_id, step_index=step_index, cache_key=cache_key)
                .one_or_none()
            )
            if row is None:
                row = ActionCache(
                    golden_script_id=golden_script_id,
                    step_index=step_index,
                    cache_key=cache_key,
                    selector_candidates=selector_candidates,
                    method=method,
                )
                session.add(row)
            else:
                row.selector_candidates = selector_candidates
                row.method = method
            row.last_verified_at = datetime.now(timezone.utc).replace(tzinfo=None)

    def record_miss(self, golden_script_id: uuid.UUID, *, step_index: int, cache_key: str) -> None:
        with session_scope() as session:
            row = (
                session.query(ActionCache)
                .filter_by(golden_script_id=golden_script_id, step_index=step_index, cache_key=cache_key)
                .one_or_none()
            )
            if row is not None:
                row.miss_count += 1
