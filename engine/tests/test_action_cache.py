import uuid

from atlas_engine.brain.cache import ActionCacheStore, compute_cache_key
from atlas_engine.db import session_scope
from atlas_engine.db.models import ActionCache, GoldenScript, Organization
from atlas_engine.db.repo import get_or_create_portal_profile


def _make_golden_script_id(slug_prefix: str) -> uuid.UUID:
    with session_scope() as session:
        org = Organization(name="Cache Test Org", slug=f"{slug_prefix}-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        profile = get_or_create_portal_profile(session, str(org.id), f"{slug_prefix}.example.com")
        golden = GoldenScript(org_id=org.id, portal_profile_id=profile.id, version=1, script_blob_ref="v1.py")
        session.add(golden)
        session.flush()
        return golden.id


def test_compute_cache_key_excludes_parameter_values_but_is_stable():
    key_a = compute_cache_key(instruction="click login button", url_pattern="/login", dom_fingerprint="abc123")
    key_b = compute_cache_key(instruction="click login button", url_pattern="/login", dom_fingerprint="abc123")
    key_c = compute_cache_key(instruction="click login button", url_pattern="/login", dom_fingerprint="different")

    assert key_a == key_b
    assert key_a != key_c


def test_action_cache_store_put_then_get_round_trips():
    golden_script_id = _make_golden_script_id("cache-test-org")
    store = ActionCacheStore()
    key = compute_cache_key(instruction="click generate report", url_pattern="/dashboard.html", dom_fingerprint="xyz")

    assert store.get(golden_script_id, step_index=3, cache_key=key) is None

    store.put(
        golden_script_id, step_index=3, cache_key=key,
        selector_candidates=[{"selector": "#generate-report-btn", "rank": 0}], method="click",
    )

    cached = store.get(golden_script_id, step_index=3, cache_key=key)
    assert cached == [{"selector": "#generate-report-btn", "rank": 0}]


def test_action_cache_store_record_miss_increments_counter():
    golden_script_id = _make_golden_script_id("cache-miss-org")
    store = ActionCacheStore()
    key = compute_cache_key(instruction="x", url_pattern="/y", dom_fingerprint="z")
    store.put(golden_script_id, step_index=0, cache_key=key, selector_candidates=[{"selector": "#a", "rank": 0}])

    store.record_miss(golden_script_id, step_index=0, cache_key=key)
    store.record_miss(golden_script_id, step_index=0, cache_key=key)

    with session_scope() as session:
        row = (
            session.query(ActionCache)
            .filter_by(golden_script_id=golden_script_id, step_index=0, cache_key=key)
            .one()
        )
        assert row.miss_count == 2
