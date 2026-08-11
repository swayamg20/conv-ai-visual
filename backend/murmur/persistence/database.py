"""Database configuration and session lifecycle for Murmur."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

SOURCE_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL_ENV = "MURMUR_DATABASE_URL"
DATA_DIR_ENV = "MURMUR_DATA_DIR"


def _default_data_dir() -> Path:
    """Choose the source-tree runtime directory, or the launch directory when installed."""
    project_root = SOURCE_ROOT if (SOURCE_ROOT / "pyproject.toml").is_file() else Path.cwd()
    return project_root / "var"


def _resolve_data_dir() -> Path:
    configured = os.getenv(DATA_DIR_ENV)
    return Path(configured).expanduser().resolve() if configured else _default_data_dir().resolve()


def get_data_dir() -> Path:
    """Return the writable runtime-data directory and create it if needed."""
    data_dir = _resolve_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_database_url() -> str:
    """Return the configured database URL, defaulting to ignored local state."""
    configured_url = os.getenv(DATABASE_URL_ENV)
    if configured_url:
        return configured_url

    return f"sqlite:///{_resolve_data_dir() / 'murmur.db'}"


def create_database_engine(database_url: str | None = None) -> Engine:
    """Create a SQLModel engine with SQLite-safe defaults when applicable."""
    url = database_url or get_database_url()
    kwargs: dict = {"echo": False}

    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if url in {"sqlite://", "sqlite:///:memory:"}:
            kwargs["poolclass"] = StaticPool

    database_engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):
        event.listen(database_engine, "connect", _configure_sqlite_connection)

    return database_engine


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    """Apply SQLite pragmas for integrity and predictable small-scale concurrency."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")

        # In-memory databases cannot use WAL. File-backed local databases can.
        database_path = cursor.execute("PRAGMA database_list").fetchone()[2]
        if database_path:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


engine = create_database_engine()


def get_session() -> Session:
    """Return a short-lived synchronous database session."""
    return Session(engine)


def session_scope() -> Iterator[Session]:
    """Yield a session for dependency-style callers and always close it."""
    with get_session() as session:
        yield session


def init_db() -> None:
    """Create all registered tables at an explicit application lifecycle boundary."""
    # Importing the declarations here keeps database configuration independent while
    # ensuring callers cannot accidentally initialize incomplete metadata.
    from murmur.persistence import models as _models  # noqa: F401

    if engine.url.get_backend_name() == "sqlite" and engine.url.database not in {None, ":memory:"}:
        Path(engine.url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)
