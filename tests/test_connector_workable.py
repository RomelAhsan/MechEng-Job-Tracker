"""Workable connector tests (public widget JSON + per-posting detail body)."""

from __future__ import annotations

import httpx
import respx

from jobtracker.connectors.http import HttpClient
from jobtracker.connectors.workable import WorkableConnector, parse_workable_account
from jobtracker.schemas.company import CompanyConfig
from jobtracker.settings import Settings
from tests.conftest import load_fixture

BASE = "https://apply.workable.com/api/v2/accounts/acme"
JOBS_URL = f"{BASE}/jobs"
DETAIL_ABC = f"{BASE}/jobs/ABC123"
DETAIL_DEF = f"{BASE}/jobs/DEF456"


def _client() -> HttpClient:
    return HttpClient(Settings(rate_limit_per_host=0.0), sleep=lambda _s: None)


def _company(**kw: object) -> CompanyConfig:
    base: dict = {"id": "acme", "name": "Acme", "ats": "workable", "board_token": "acme"}
    base.update(kw)
    return CompanyConfig(**base)


def test_parse_workable_account() -> None:
    assert parse_workable_account("https://acme.workable.com/jobs") == "acme"
    assert parse_workable_account("https://apply.workable.com/acme/") == "acme"
    assert parse_workable_account("https://example.com") is None


@respx.mock
def test_workable_fetch_with_detail_body() -> None:
    respx.post(JOBS_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("workable_jobs.json"))
    )
    da = respx.get(DETAIL_ABC).mock(
        return_value=httpx.Response(200, json=load_fixture("workable_detail_abc123.json"))
    )
    dd = respx.get(DETAIL_DEF).mock(
        return_value=httpx.Response(200, json=load_fixture("workable_detail_def456.json"))
    )

    client = _client()
    raw_jobs = WorkableConnector(client).fetch(_company())
    client.close()

    assert len(raw_jobs) == 2
    assert da.called and dd.called
    by_id = {r.source_job_id: r for r in raw_jobs}
    first = by_id["ABC123"]
    assert first.title == "Robotics Hardware Engineer"
    assert "actuators" in first.description_html
    assert "SolidWorks" in first.description_html  # requirements assembled in
    assert first.application_url == "https://apply.workable.com/acme/j/ABC123/apply/"
    assert first.source_url == "https://apply.workable.com/acme/j/ABC123/"
    assert "Toronto" in first.location


@respx.mock
def test_workable_no_public_api_returns_empty() -> None:
    # A non-dict / error body means no public JSON -> route to fallback.
    respx.post(JOBS_URL).mock(return_value=httpx.Response(200, json=[]))
    client = _client()
    assert WorkableConnector(client).fetch(_company()) == []
    client.close()


def test_workable_missing_account_returns_empty() -> None:
    client = _client()
    assert WorkableConnector(client).fetch(
        CompanyConfig(id="acme", name="Acme", ats="workable")
    ) == []
    client.close()
