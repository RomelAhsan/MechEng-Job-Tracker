"""Migration smoke test: init-db reaches head on a fresh DB (Phase 2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).resolve().parent.parent


def _alembic_config() -> Config:
    # env.py reads the DB URL from application settings (DATABASE_URL), so the
    # caller sets that env var; alembic.ini's url is ignored.
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def test_upgrade_head_on_fresh_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "p2.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = _alembic_config()

    command.upgrade(cfg, "head")

    engine = create_engine(url, future=True)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        # Phase 2 tables / columns are present.
        assert "saved_filters" in tables
        user_cols = {c["name"] for c in inspector.get_columns("users")}
        assert {"password_hash", "role"} <= user_cols
        app_cols = {c["name"] for c in inspector.get_columns("application_status")}
        assert {"note", "deadline"} <= app_cols
        profile_cols = {c["name"] for c in inspector.get_columns("user_profiles")}
        assert "notification_prefs" in profile_cols
    finally:
        engine.dispose()


def test_head_is_phase2_revision() -> None:
    script = ScriptDirectory.from_config(_alembic_config())
    assert script.get_current_head() == "0002_phase2_accounts"


def test_downgrade_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"sqlite:///{tmp_path / 'p2d.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = _alembic_config()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
