"""Shared test isolation for backend persistence."""

import os

import pytest
from sqlmodel import SQLModel

os.environ["MURMUR_DATABASE_URL"] = "sqlite:///:memory:"

from murmur.persistence import init_db
from murmur.persistence.database import engine
from murmur.persistence.repositories.tools import ToolRepo


@pytest.fixture(autouse=True)
def isolated_database():
    """Rebuild the in-memory schema so every test starts from known state."""
    SQLModel.metadata.drop_all(engine)
    init_db()
    ToolRepo._invalidate_schema_cache()
    yield
