"""Tests for the shared HTTP client, registry/detection, and fallback stub."""

from __future__ import annotations

import httpx
import pytest
import respx

from jobtracker.connectors import (
    REQUIRES_FALLBACK,
    FallbackConnector,
    detect_ats,
    get_connector,
    is_supported,
)
from jobtracker.connectors.http import HttpClient, PerHostRateLimiter
from jobtracker.schemas.company import ATSType, CompanyConfig
from jobtracker.settings import Settings


def _client(**overrides) -> HttpClient:
    settings = Settings(
        http_max_retries=overrides.get("max_retries", 3),
        rate_limit_per_host=overrides.get("rate", 0.0),
    )
    # A no-op sleep keeps backoff tests instant.
    return HttpClient(settings, sleep=lambda _s: None)


@respx.mock
def test_retry_then_success_on_500():
    route = respx.get("https://api.test/jobs").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(503),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    client = _client()
    data = client.get_json("https://api.test/jobs")
    assert data == {"ok": True}
    assert route.call_count == 3
    client.close()


@respx.mock
def test_retry_exhausted_raises():
    respx.get("https://api.test/jobs").mock(return_value=httpx.Response(503))
    client = _client(max_retries=2)
    with pytest.raises(httpx.HTTPStatusError):
        client.get_json("https://api.test/jobs")
    client.close()


@respx.mock
def test_retry_on_transport_error_then_success():
    route = respx.get("https://api.test/jobs").mock(
        side_effect=[
            httpx.ConnectError("boom"),
            httpx.Response(200, json=[1, 2]),
        ]
    )
    client = _client()
    assert client.get_json("https://api.test/jobs") == [1, 2]
    assert route.call_count == 2
    client.close()


@respx.mock
def test_no_retry_on_404():
    route = respx.get("https://api.test/jobs").mock(return_value=httpx.Response(404))
    client = _client()
    with pytest.raises(httpx.HTTPStatusError):
        client.request("GET", "https://api.test/jobs").raise_for_status()
    assert route.call_count == 1
    client.close()


def test_rate_limiter_waits_between_calls():
    waited: list[float] = []
    limiter = PerHostRateLimiter(2.0, sleep=waited.append)
    limiter.acquire("example.com")
    limiter.acquire("example.com")
    assert waited and waited[0] > 0


@pytest.mark.parametrize(
    "url, ats, token",
    [
        ("https://boards.greenhouse.io/stripe", "greenhouse", "stripe"),
        ("https://boards-api.greenhouse.io/v1/boards/stripe/jobs", "greenhouse", "stripe"),
        ("https://jobs.lever.co/leverdemo", "lever", "leverdemo"),
        ("https://jobs.ashbyhq.com/Ashby", "ashby", "Ashby"),
        ("https://jobs.smartrecruiters.com/BoschGroup", "smartrecruiters", "BoschGroup"),
        (
            "https://api.smartrecruiters.com/v1/companies/BoschGroup/postings",
            "smartrecruiters",
            "BoschGroup",
        ),
    ],
)
def test_detect_ats_supported(url, ats, token):
    detected = detect_ats(url)
    assert detected.ats == ats
    assert detected.board_token == token


def test_detect_ats_unknown_falls_back_to_html():
    detected = detect_ats("https://careers.example.com/jobs")
    assert detected.ats == ATSType.HTML.value
    assert detected.board_token is None


def test_get_connector_supported_vs_fallback():
    client = _client()
    assert type(get_connector("greenhouse", client)).__name__ == "GreenhouseConnector"
    # workable/rss now have real connectors; only html routes to the fallback.
    assert type(get_connector("workable", client)).__name__ == "WorkableConnector"
    assert type(get_connector("rss", client)).__name__ == "RssConnector"
    assert isinstance(get_connector("html", client), FallbackConnector)
    assert is_supported("lever") is True
    assert is_supported("html") is False
    client.close()


@pytest.mark.parametrize("ats", ["workable", "rss", "sitemap", "json_ld", "html"])
def test_fallback_marks_requires_fallback_without_raising(ats):
    company = CompanyConfig(id="x", name="X", ats=ats)
    connector = FallbackConnector()
    jobs = connector.fetch(company)
    assert jobs == []
    assert connector.last_status == REQUIRES_FALLBACK


@respx.mock
def test_connectors_do_no_real_network_io():
    """With the transport mocked, any un-stubbed request must be refused.

    respx raises on unmatched routes, so a passing call proves the connector
    only hit the mocked transport and performed no real network I/O.
    """
    from jobtracker.connectors.greenhouse import GreenhouseConnector

    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": []})
    )
    client = _client()
    connector = GreenhouseConnector(client)
    company = CompanyConfig(id="acme", name="Acme", ats="greenhouse", board_token="acme")
    assert connector.fetch(company) == []
    client.close()
