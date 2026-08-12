from contextlib import contextmanager

from sqlalchemy import JSON, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from atlas_engine.config import settings

# Renders as native JSONB on Postgres, plain JSON (stored as TEXT) on any
# other dialect — lets structural tests run against SQLite while the real
# deployment target (Postgres) keeps proper JSONB semantics.
PortableJSON = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


_engine = None
_SessionFactory = None


def get_engine(database_url: str | None = None):
    global _engine
    if _engine is None:
        _engine = create_engine(database_url or settings.database_url, future=True)
    return _engine


def get_session_factory(database_url: str | None = None) -> sessionmaker:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(database_url), expire_on_commit=False)
    return _SessionFactory


@contextmanager
def session_scope(database_url: str | None = None):
    session: Session = get_session_factory(database_url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
