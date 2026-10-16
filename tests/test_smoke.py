"""Smoke test: the Alembic migration builds the full schema."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

EXPECTED_TABLES = {
    "companies",
    "jobs",
    "job_events",
    "source_health",
    "quarantine_items",
    "users",
    "saved_jobs",
    "application_status",
    "user_profiles",
    "notification_log",
}

_ROOT = Path(__file__).resolve().parent.parent


def _alembic_config(database_url: str) -> Config:
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def test_migration_builds_all_tables(tmp_path, monkeypatch):
    db_path = tmp_path / "smoke.db"
    database_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    cfg = _alembic_config(database_url)
    command.upgrade(cfg, "head")

    engine = create_engine(database_url, future=True)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    missing = EXPECTED_TABLES - tables
    assert not missing, f"missing tables: {missing}"


def test_migration_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "idem.db"
    database_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    cfg = _alembic_config(database_url)
    command.upgrade(cfg, "head")
    # Running again should be a no-op (already at head), not an error.
    command.upgrade(cfg, "head")

    engine = create_engine(database_url, future=True)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert EXPECTED_TABLES <= tables
