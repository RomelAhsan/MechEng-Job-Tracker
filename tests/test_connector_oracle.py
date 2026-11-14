"""Oracle ORC connector tests (requisition list + per-job detail body)."""

from __future__ import annotations

import os

import httpx
import pytest
import respx

from jobtracker.connectors.http import HttpClient
from jobtracker.connectors.oracle import (
    OracleConnector,
    parse_oracle_url,
    parse_requisitions,
)
from jobtracker.schemas.company import CompanyConfig
from jobtracker.settings import Settings
from tests.conftest import load_fixture

ORIGIN = "https://ibqbjb.fa.ocs.oraclecloud.com"
SITE = "Honeywell"
LIST_URL = f"{ORIGIN}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
DETAIL_URL = f"{ORIGIN}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"


def _client() -> HttpClient:
    return HttpClient(Settings(rate_limit_per_host=0.0), sleep=lambda _s: None)


def _company(**kw: object) -> CompanyConfig:
    base: dict = {
        "id": "honeywell",
        "name": "Honeywell",
        "ats": "oracle",
        "board_token": f"{ORIGIN}|{SITE}",
    }
    base.update(kw)
    return CompanyConfig(**base)


def _detail_side_effect(request: httpx.Request) -> httpx.Response:
    finder = request.url.params.get("finder", "")
    if "REQ-1001" in finder:
        return httpx.Response(200, json=load_fixture("oracle_detail_1001.json"))
    if "REQ-1002" in finder:
        return httpx.Response(200, json=load_fixture("oracle_detail_1002.json"))
    return httpx.Response(200, json={"items": []})


def test_parse_oracle_url() -> None:
    parsed = parse_oracle_url(
        "https://ibqbjb.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/"
        "sites/Honeywell/requisitions"
    )
    assert parsed == ("https://ibqbjb.fa.ocs.oraclecloud.com", "Honeywell")
    assert parse_oracle_url("https://example.com/careers") is None


def test_parse_requisitions_helper() -> None:
    reqs = parse_requisitions(load_fixture("oracle_reqs_page1.json"))
    assert [r["Id"] for r in reqs] == ["REQ-1001", "REQ-1002"]


@respx.mock
def test_oracle_lists_and_fetches_detail_body() -> None:
    respx.get(LIST_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("oracle_reqs_page1.json"))
    )
    detail = respx.get(DETAIL_URL)
    detail.side_effect = _detail_side_effect

    client = _client()
    raw_jobs = OracleConnector(client).fetch(_company())
    client.close()

    assert len(raw_jobs) == 2
    assert detail.call_count == 2
    by_id = {r.source_job_id: r for r in raw_jobs}
    first = by_id["REQ-1001"]
    assert "Build hardware" in first.description_html
    assert "GD&T" in first.description_html
    assert first.application_url == (
        f"{ORIGIN}/hcmUI/CandidateExperience/en/sites/{SITE}/job/REQ-1001"
    )
    assert "Mayfield Heights" in first.location
    assert first.posted_at is not None


@respx.mock
def test_oracle_empty_board_returns_empty_list() -> None:
    respx.get(LIST_URL).mock(
        return_value=httpx.Response(
            200, json={"items": [{"TotalJobsCount": 0, "requisitionList": []}]}
        )
    )
    client = _client()
    assert OracleConnector(client).fetch(_company()) == []
    client.close()


@respx.mock
def test_oracle_auth_wall_surfaces() -> None:
    respx.get(LIST_URL).mock(return_value=httpx.Response(401, json={"error": "nope"}))
    client = _client()
    try:
        OracleConnector(client).fetch(_company())
    except httpx.HTTPError:
        pass
    else:  # pragma: no cover - a 401 must not silently return jobs
        raise AssertionError("auth-walled board must surface, not return jobs")
    finally:
        client.close()


def test_oracle_resolves_coordinates_from_careers_url() -> None:
    company = CompanyConfig(
        id="honeywell",
        name="Honeywell",
        ats="oracle",
        careers_url=(
            f"{ORIGIN}/hcmUI/CandidateExperience/en/sites/{SITE}/requisitions"
        ),
    )
    coords = OracleConnector(_client())._coordinates(company)
    assert coords == (ORIGIN, SITE)


@pytest.mark.skipif(
    not os.getenv("JOBTRACKER_LIVE_TESTS"),
    reason="live Oracle ORC board fetch; set JOBTRACKER_LIVE_TESTS=1 to run",
)
def test_oracle_live_honeywell() -> None:
    client = HttpClient(Settings())
    try:
        raw_jobs = OracleConnector(client).fetch(_company())
    finally:
        client.close()
    assert raw_jobs, "live Honeywell Oracle board returned no jobs"
    assert any(r.description_html.strip() for r in raw_jobs)
