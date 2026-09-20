"""Deployment-facing database configuration contracts."""

from __future__ import annotations

import pytest
from murmur.persistence.database import create_database_engine, get_sqlite_journal_mode


@pytest.mark.parametrize("configured", ["DELETE", "delete", " DELETE "])
def test_file_backed_sqlite_can_use_delete_journal_mode(
    configured: str,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MURMUR_SQLITE_JOURNAL_MODE", configured)
    database_path = tmp_path / "murmur.db"
    database_engine = create_database_engine(f"sqlite:///{database_path}")
    try:
        with database_engine.connect() as connection:
            journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
        assert journal_mode.casefold() == "delete"
    finally:
        database_engine.dispose()


def test_file_backed_sqlite_keeps_wal_as_the_local_default(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MURMUR_SQLITE_JOURNAL_MODE", raising=False)
    database_engine = create_database_engine(f"sqlite:///{tmp_path / 'murmur.db'}")
    try:
        with database_engine.connect() as connection:
            journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
        assert journal_mode.casefold() == "wal"
    finally:
        database_engine.dispose()


def test_sqlite_journal_mode_rejects_unbounded_pragma_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MURMUR_SQLITE_JOURNAL_MODE", "OFF; DROP TABLE users")

    with pytest.raises(
        ValueError,
        match=r"^MURMUR_SQLITE_JOURNAL_MODE must be one of: DELETE, WAL$",
    ):
        get_sqlite_journal_mode()
