from datetime import datetime, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


def utcnow() -> datetime:
    """Naive UTC. All timestamps in the DB are naive UTC; convert at the edges."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, pool_pre_ping=True, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _fk(dbapi_conn, _):  # enforce foreign keys on SQLite
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from . import models  # noqa: F401

    if engine.dialect.name == "postgresql":
        with engine.begin() as c:
            c.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(engine)
    # ponytail: create_all never alters existing tables; hand-add new columns here until there's a real migration tool
    from sqlalchemy import inspect
    if "source_text" not in {c["name"] for c in inspect(engine).get_columns("transcription_segments")}:
        with engine.begin() as c:
            c.exec_driver_sql("ALTER TABLE transcription_segments ADD COLUMN source_text TEXT")
