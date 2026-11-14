"""Shared pytest fixtures: isolated SQLite engine + session."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from jobtracker.db import models  # noqa: F401  (register tables on metadata)
from jobtracker.db.base import Base

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> object:
    """Load and parse a JSON fixture from ``tests/fixtures``."""
    with (FIXTURES_DIR / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


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


@pytest.fixture(autouse=True)
def _no_live_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests independent of a developer's local .env.

    Environment variables override .env in pydantic-settings, so blanking them
    here means no test ever sees a real Gemini/Apify key (no network, and the
    strict 'AI must confirm' gate stays off unless a test turns it on).
    """
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("APIFY_TOKEN", "")
