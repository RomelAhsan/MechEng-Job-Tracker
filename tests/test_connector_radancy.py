"""Radancy / TalentBrew connector tests (search fragment + per-job JSON-LD)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest
import respx

from jobtracker.connectors.http import HttpClient
from jobtracker.connectors.radancy import (
    RadancyConnector,
    parse_radancy_host,
    parse_results_fragment,
)
from jobtracker.schemas.company import CompanyConfig
from jobtracker.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures"
HOST = "careers.l3harris.com"
SEARCH_URL = f"https://{HOST}/en/search-jobs/results"
JOB_1 = f"https://{HOST}/en/job/melbourne/mechanical-engineer/4832/90001"
JOB_2 = f"https://{HOST}/en/job/rochester/systems-engineer/4832/90002"


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _json(name: str) -> object:
    return json.loads(_text(name))


def _client() -> HttpClient:
    return HttpClient(Settings(rate_limit_per_host=0.0), sleep=lambda _s: None)


def _company(**kw: object) -> CompanyConfig:
    base: dict = {
        "id": "l3harris",
        "name": "L3Harris Technologies",
        "ats": "radancy",
        "board_token": HOST,
    }
    base.update(kw)
    return CompanyConfig(**base)


def test_parse_radancy_host() -> None:
    assert parse_radancy_host("https://careers.l3harris.com/en") == HOST
    assert parse_radancy_host("careers.l3harris.com/en/search-jobs") == HOST
    assert parse_radancy_host("CAREERS.L3HARRIS.COM") == HOST
    assert parse_radancy_host(None) is None


def test_parse_results_fragment_helper() -> None:
    fragment = _json("radancy_results_page1.json")["results"]
    jobs = parse_results_fragment(fragment, HOST)
    assert [j[0] for j in jobs] == [JOB_1, JOB_2]
    assert jobs[0][1] == "Mechanical Engineer"
    assert jobs[0][2] == "Melbourne, FL"
    assert jobs[0][3] == "90001"


@respx.mock
def test_radancy_lists_and_fetches_detail_body() -> None:
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_json("radancy_results_page1.json"))
    )
    respx.get(JOB_1).mock(return_value=httpx.Response(200, text=_text("radancy_job_1.html")))
    respx.get(JOB_2).mock(return_value=httpx.Response(200, text=_text("radancy_job_2.html")))

    client = _client()
    raw_jobs = RadancyConnector(client).fetch(_company())
    client.close()

    assert len(raw_jobs) == 2
    by_title = {r.title: r for r in raw_jobs}
    mech = by_title["Mechanical Engineer"]
    assert "Design mechanical hardware" in mech.description_html
    assert mech.application_url == JOB_1
    assert "Melbourne" in mech.location
    assert mech.posted_at is not None
    assert all(r.posted_at is not None for r in raw_jobs)


@respx.mock
def test_radancy_empty_board_returns_empty_list() -> None:
    empty = {
        "filters": "",
        "results": "<section id=\"search-results\" data-total-pages=\"1\" "
        "data-total-results=\"0\"></section>",
        "hasJobs": False,
        "hasContent": False,
    }
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=empty))
    client = _client()
    assert RadancyConnector(client).fetch(_company()) == []
    client.close()


@respx.mock
def test_radancy_auth_wall_surfaces() -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(403, json={"error": "nope"}))
    client = _client()
    try:
        RadancyConnector(client).fetch(_company())
    except httpx.HTTPError:
        pass
    else:  # pragma: no cover - a 403 must not silently return jobs
        raise AssertionError("auth-walled board must surface, not return jobs")
    finally:
        client.close()


@respx.mock
def test_radancy_pagination_stops_at_total_pages() -> None:
    """Search is requested once per page and stops at data-total-pages."""
    page1 = _json("radancy_results_page1.json")
    search = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=page1))
    respx.get(JOB_1).mock(return_value=httpx.Response(200, text=_text("radancy_job_1.html")))
    respx.get(JOB_2).mock(return_value=httpx.Response(200, text=_text("radancy_job_2.html")))

    client = _client()
    raw_jobs = RadancyConnector(client).fetch(_company())
    client.close()

    # data-total-pages='1' -> exactly one search request, no page 2.
    assert search.call_count == 1
    assert len(raw_jobs) == 2


@pytest.mark.skipif(
    not os.getenv("JOBTRACKER_LIVE_TESTS"),
    reason="live Radancy board fetch; set JOBTRACKER_LIVE_TESTS=1 to run",
)
def test_radancy_live_l3harris() -> None:
    client = HttpClient(Settings())
    try:
        raw_jobs = RadancyConnector(client).fetch(_company())
    finally:
        client.close()
    assert raw_jobs, "live L3Harris Radancy board returned no jobs"
    assert any(r.description_html.strip() for r in raw_jobs)
