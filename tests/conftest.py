"""Shared pytest fixtures: isolated SQLite engine + session."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from jobtracker.db import models  # noqa: F401  (register tables on metadata)
from jobtracker.db.base import Base


@pytest.fixture()
def engine() -> Iterator[Engine]:
    """In-memory SQLite engine with the schema created from metadata."""
    eng = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        Base.metadata.drop_all(eng)
        eng.dispose()


@pytest.fixture()
def session(engine: Engine) -> Iterator[Session]:
    """Session bound to the in-memory engine."""
    factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    with factory() as sess:
        yield sess
