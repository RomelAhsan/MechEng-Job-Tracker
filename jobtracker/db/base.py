"""Declarative base and engine/session factory built from settings."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from jobtracker.settings import Settings, get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def create_db_engine(settings: Settings | None = None) -> Engine:
    """Create a SQLAlchemy engine from settings."""
    settings = settings or get_settings()
    connect_args: dict = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(settings.database_url, future=True, connect_args=connect_args)


def create_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    """Create a configured session factory bound to an engine."""
    engine = engine or create_db_engine()
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
