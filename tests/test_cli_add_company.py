"""Tests for ``add-company`` persisting a validated entry to companies.yaml.

The add flow must (a) upsert the detected company into ``config/companies.yaml``
in the existing shape and (b) stay idempotent (re-adding updates, never
duplicates). HTTP is mocked with respx so no real board is contacted.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
import respx
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from jobtracker import cli
from jobtracker.config import load_companies
from jobtracker.settings import Settings

WIDGETS = "https://careers.rtx.com/widgets"


@pytest.fixture()
def factory(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker]:
    bound = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    monkeypatch.setattr(cli, "create_session_factory", lambda: bound)
    yield bound


@pytest.fixture()
def companies_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    path = tmp_path / "companies.yaml"
    path.write_text("companies: []\n", encoding="utf-8")
    settings = Settings(companies_file=str(path), rate_limit_per_host=0.0)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    # company_service / http client read get_settings() too.
    monkeypatch.setattr("jobtracker.settings.get_settings", lambda: settings)
    return str(path)


def _mock_phenom() -> None:
    route = respx.post(WIDGETS)
    route.side_effect = [
        httpx.Response(
            200,
            json={
                "refineSearch": {
                    "totalHits": 1,
                    "data": {
                        "jobs": [
                            {
                                "jobId": "RTX111",
                                "title": "Intern",
                                "location": "Tucson, Arizona, United States",
                                "dateCreated": 1767974400000,
                                "descriptionTeaser": "teaser",
                            }
                        ]
                    },
                }
            },
        ),
        httpx.Response(
            200,
            json={
                "jobDetail": {
                    "data": {"job": {"jobId": "RTX111", "description": "<p>Full.</p>"}}
                }
            },
        ),
    ]


@respx.mock
def test_add_company_persists_yaml_idempotently(
    factory: sessionmaker, companies_file: str
) -> None:
    runner = CliRunner()
    _mock_phenom()
    first = runner.invoke(
        cli.app,
        ["add-company", "https://careers.rtx.com/global/en", "--name", "RTX"],
    )
    assert first.exit_code == 0, first.output
    companies = load_companies(companies_file)
    assert [c.id for c in companies] == ["rtx"]
    assert companies[0].ats == "phenom"
    assert companies[0].board_token == "careers.rtx.com"

    # Re-adding the same company updates in place; no duplicate entry.
    _mock_phenom()
    second = runner.invoke(
        cli.app,
        ["add-company", "https://careers.rtx.com/global/en", "--name", "RTX"],
    )
    assert second.exit_code == 0, second.output
    companies2 = load_companies(companies_file)
    assert [c.id for c in companies2] == ["rtx"]
