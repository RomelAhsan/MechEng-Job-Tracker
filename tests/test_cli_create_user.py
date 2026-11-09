"""Tests for the ``create-user`` CLI bootstrap command.

Unattended deployments (the scheduled GitHub Actions poll, a hosted worker)
have nobody to fill in the dashboard registration form, yet notifications are
per-user: with no account the notifier has no recipient and silently sends
nothing. ``create-user`` bootstraps that account, so the behaviour that matters
is (a) it really creates the account and (b) re-running it on every scheduled
poll is harmless.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from jobtracker import cli
from jobtracker.services import auth_service

_PASSWORD = "correct-horse-battery"


@pytest.fixture()
def factory(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker]:
    """Point the CLI's session factory at the isolated in-memory engine."""
    bound = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    monkeypatch.setattr(cli, "create_session_factory", lambda: bound)
    yield bound


def test_create_user_creates_the_account(factory: sessionmaker) -> None:
    result = CliRunner().invoke(
        cli.app,
        ["create-user", "--email", "Me@Example.com", "--password", _PASSWORD],
    )

    assert result.exit_code == 0, result.output
    with factory() as session:
        user = auth_service.get_by_email(session, "me@example.com")
        assert user is not None
        # First account owns the deployment, so it must be the admin.
        assert user.role == "admin"


def test_create_user_is_idempotent(factory: sessionmaker) -> None:
    args = ["create-user", "--email", "me@example.com", "--password", _PASSWORD]

    first = CliRunner().invoke(cli.app, args)
    second = CliRunner().invoke(cli.app, args)

    assert first.exit_code == 0, first.output
    # Re-running must not fail the scheduled poll it is wired into.
    assert second.exit_code == 0, second.output
    assert "already exists" in second.output
    with factory() as session:
        assert auth_service.user_count(session) == 1


def test_create_user_reads_credentials_from_environment(
    factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CI passes credentials as env vars rather than argv (keeps them masked)."""
    monkeypatch.setenv("JOBTRACKER_USER_EMAIL", "env@example.com")
    monkeypatch.setenv("JOBTRACKER_USER_PASSWORD", _PASSWORD)

    result = CliRunner().invoke(cli.app, ["create-user"])

    assert result.exit_code == 0, result.output
    with factory() as session:
        assert auth_service.get_by_email(session, "env@example.com") is not None
