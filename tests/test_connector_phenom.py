"""Phenom connector tests (refineSearch list + per-job jobDetail body)."""

from __future__ import annotations

import os

import httpx
import pytest
import respx

from jobtracker.connectors.http import HttpClient
from jobtracker.connectors.phenom import (
    PhenomConnector,
    parse_phenom_host,
    parse_widgets,
)
from jobtracker.schemas.company import CompanyConfig
from jobtracker.settings import Settings
from tests.conftest import load_fixture

WIDGETS = "https://careers.rtx.com/widgets"


def _client() -> HttpClient:
    return HttpClient(Settings(rate_limit_per_host=0.0), sleep=lambda _s: None)


def _company(**kw: object) -> CompanyConfig:
    base: dict = {
        "id": "rtx",
        "name": "RTX",
        "ats": "phenom",
        "board_token": "careers.rtx.com",
    }
    base.update(kw)
    return CompanyConfig(**base)


def test_parse_phenom_host_variants() -> None:
    assert parse_phenom_host("https://careers.rtx.com/global/en") == "careers.rtx.com"
    assert parse_phenom_host("careers.rtx.com") == "careers.rtx.com"
    assert parse_phenom_host("careers.rtx.com/jobs") == "careers.rtx.com"
    assert parse_phenom_host(None) is None


def test_parse_widgets_maps_posted_date_from_ms() -> None:
    jobs = parse_widgets(load_fixture("phenom_refine_page1.json"), "careers.rtx.com")
    assert [j.source_job_id for j in jobs] == ["RTX111", "RTX222"]
    first = jobs[0]
    assert first.title == "Mechanical Design Engineer Intern"
    assert first.application_url == "https://careers.rtx.com/job/RTX111"
    assert first.posted_at is not None
    assert first.posted_at.year == 2026


@respx.mock
def test_phenom_lists_and_fetches_detail_body() -> None:
    route = respx.post(WIDGETS)
    route.side_effect = [
        httpx.Response(200, json=load_fixture("phenom_refine_page1.json")),
        httpx.Response(200, json=load_fixture("phenom_detail_rtx111.json")),
        httpx.Response(200, json=load_fixture("phenom_detail_rtx222.json")),
    ]

    client = _client()
    raw_jobs = PhenomConnector(client).fetch(_company())
    client.close()

    assert len(raw_jobs) == 2
    # One list POST + one detail POST per job.
    assert route.call_count == 3
    by_id = {r.source_job_id: r for r in raw_jobs}
    assert "SolidWorks" in by_id["RTX111"].description_html
    assert "CFD" in by_id["RTX222"].description_html
    assert by_id["RTX111"].posted_at is not None


@respx.mock
def test_phenom_empty_board_returns_empty_list() -> None:
    respx.post(WIDGETS).mock(
        return_value=httpx.Response(
            200, json={"refineSearch": {"totalHits": 0, "data": {"jobs": []}}}
        )
    )
    client = _client()
    assert PhenomConnector(client).fetch(_company()) == []
    client.close()


@respx.mock
def test_phenom_auth_wall_surfaces() -> None:
    respx.post(WIDGETS).mock(return_value=httpx.Response(403, json={"error": "nope"}))
    client = _client()
    try:
        PhenomConnector(client).fetch(_company())
    except httpx.HTTPError:
        pass
    else:  # pragma: no cover - a 403 must not silently return jobs
        raise AssertionError("auth-walled board must surface, not return jobs")
    finally:
        client.close()


def test_phenom_resolves_host_from_careers_url() -> None:
    company = CompanyConfig(
        id="rtx",
        name="RTX",
        ats="phenom",
        careers_url="https://careers.rtx.com/global/en/home",
    )
    assert parse_phenom_host(company.board_token or company.careers_url) == (
        "careers.rtx.com"
    )


@pytest.mark.skipif(
    not os.getenv("JOBTRACKER_LIVE_TESTS"),
    reason="live Phenom board fetch; set JOBTRACKER_LIVE_TESTS=1 to run",
)
def test_phenom_live_rtx() -> None:
    client = HttpClient(Settings())
    try:
        raw_jobs = PhenomConnector(client).fetch(_company())
    finally:
        client.close()
    assert raw_jobs, "live RTX Phenom board returned no jobs"
    assert any(r.description_html.strip() for r in raw_jobs)
