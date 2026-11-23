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
    import json as _json

    listing = {
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
    }
    detail = {
        "jobDetail": {"data": {"job": {"jobId": "RTX111", "description": "<p>Full.</p>"}}}
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content)
        if body["ddoKey"] == "jobDetail":
            return httpx.Response(200, json=detail)
        return httpx.Response(200, json=listing)

    respx.post(WIDGETS).mock(side_effect=handler)


def _huge_phenom(total: int) -> None:
    """Mock a big Phenom board: one search page of ``total`` jobs + details.

    The search list is a single ``refineSearch`` page; every per-posting
    ``jobDetail`` POST returns a full description. A count cap must stop issuing
    those detail calls once the cap is reached, so this proves the add-time poll
    is bounded instead of fetching all ``total`` postings.
    """
    list_jobs = [
        {
            "jobId": f"BIG{i}",
            "title": f"Engineer {i}",
            "location": "Tucson, Arizona, United States",
            "dateCreated": 1767974400000,
            "descriptionTeaser": "teaser",
        }
        for i in range(total)
    ]

    def _handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        body = _json.loads(request.content.decode() or "{}")
        if body.get("ddoKey") == "refineSearch":
            return httpx.Response(
                200,
                json={
                    "refineSearch": {
                        "totalHits": total,
                        "data": {"jobs": list_jobs},
                    }
                },
            )
        job_id = body.get("jobId")
        return httpx.Response(
            200,
            json={
                "jobDetail": {
                    "data": {"job": {"jobId": job_id, "description": "<p>Full.</p>"}}
                }
            },
        )

    respx.post(WIDGETS).mock(side_effect=_handler)


@respx.mock
def test_add_company_poll_is_bounded_by_cap(
    engine: Engine, factory: sessionmaker, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The add-time poll fetches at most ADD_POLL_MAX_POSTINGS, still persists
    the YAML, and exits 0 even though the board advertises many more jobs."""
    from jobtracker.db.models import Job

    path = tmp_path / "companies.yaml"
    path.write_text("companies: []\n", encoding="utf-8")
    cap = 5
    settings = Settings(
        companies_file=str(path),
        rate_limit_per_host=0.0,
        add_poll_max_postings=cap,
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr("jobtracker.settings.get_settings", lambda: settings)
    # company_service and ingest_service bound ``get_settings`` at import; patch
    # their module-local names so the cap applies to the baseline fetch too.
    monkeypatch.setattr(
        "jobtracker.services.company_service.get_settings", lambda: settings
    )
    monkeypatch.setattr(
        "jobtracker.services.ingest_service.get_settings", lambda: settings
    )

    _huge_phenom(total=50)
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["add-company", "https://careers.rtx.com/global/en", "--name", "RTX",
         "--poll"],
    )
    assert result.exit_code == 0, result.output
    assert "bounded to at most 5" in result.output

    # The company (companies.yaml) is persisted regardless of the bound.
    companies = load_companies(str(path))
    assert [c.id for c in companies] == ["rtx"]

    # Only up to the cap were ingested, not all 50 advertised postings.
    session = factory()
    try:
        stored = session.query(Job).filter(Job.company_id == "rtx").count()
    finally:
        session.close()
    assert stored == cap


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
