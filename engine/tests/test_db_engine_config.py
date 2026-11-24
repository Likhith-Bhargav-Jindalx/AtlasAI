from atlas_engine.db import get_engine


def test_engine_has_pool_pre_ping_enabled():
    # Azure Postgres Flexible Server (and most managed Postgres) silently
    # drops idle connections. Without pool_pre_ping, SQLAlchemy reuses a
    # dead pooled connection and the first query after an idle period fails
    # with "server closed the connection unexpectedly" instead of
    # transparently reconnecting.
    engine = get_engine()
    assert engine.pool._pre_ping is True
