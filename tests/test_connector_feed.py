"""RSS / sitemap / JSON-LD generic connector tests (local fixtures only)."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from jobtracker.connectors.feed import JsonLdConnector, RssConnector, SitemapConnector
from jobtracker.connectors.http import HttpClient
from jobtracker.schemas.company import CompanyConfig
from jobtracker.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures"


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _client() -> HttpClient:
    return HttpClient(Settings(rate_limit_per_host=0.0), sleep=lambda _s: None)


def _company(ats: str, url: str) -> CompanyConfig:
    return CompanyConfig(id="acme", name="Acme", ats=ats, board_token=url)


@respx.mock
def test_rss_connector_parses_entries_with_direct_links() -> None:
    feed_url = "https://careers.acme.example/feed.rss"
    respx.get(feed_url).mock(
        return_value=httpx.Response(
            200, text=_text("jobs_feed.rss"), headers={"content-type": "application/rss+xml"}
        )
    )
    client = _client()
    jobs = RssConnector(client).fetch(_company("rss", feed_url))
    client.close()

    assert len(jobs) == 2
    assert all(j.application_url and j.source_url for j in jobs)
    titles = {j.title for j in jobs}
    assert "Senior Mechanical Engineer" in titles
    first = next(j for j in jobs if j.title == "Senior Mechanical Engineer")
    assert first.application_url == "https://careers.acme.example/jobs/101"


@respx.mock
def test_rss_connector_missing_feed_fails_soft() -> None:
    feed_url = "https://careers.acme.example/feed.rss"
    respx.get(feed_url).mock(return_value=httpx.Response(404, text="nope"))
    client = _client()
    assert RssConnector(client).fetch(_company("rss", feed_url)) == []
    client.close()


@respx.mock
def test_sitemap_connector_visits_each_job_and_extracts_json_ld() -> None:
    sitemap_url = "https://careers.acme.example/sitemap.xml"
    respx.get(sitemap_url).mock(
        return_value=httpx.Response(
            200, text=_text("careers_sitemap.xml"), headers={"content-type": "application/xml"}
        )
    )
    respx.get("https://careers.acme.example/jobs/201").mock(
        return_value=httpx.Response(200, text=_text("job_page_201.html"))
    )
    respx.get("https://careers.acme.example/jobs/202").mock(
        return_value=httpx.Response(200, text=_text("job_page_202.html"))
    )

    client = _client()
    jobs = SitemapConnector(client).fetch(_company("sitemap", sitemap_url))
    client.close()

    assert len(jobs) == 2
    by_title = {j.title: j for j in jobs}
    design = by_title["Design Engineer"]
    assert design.application_url == "https://careers.acme.example/jobs/201"
    assert "Design mechanical components" in design.description_html
    assert "Waterloo" in design.location


@respx.mock
def test_json_ld_connector_extracts_graph_postings() -> None:
    listing_url = "https://careers.acme.example/open-roles"
    respx.get(listing_url).mock(
        return_value=httpx.Response(200, text=_text("careers_listing_jsonld.html"))
    )
    client = _client()
    jobs = JsonLdConnector(client).fetch(_company("json_ld", listing_url))
    client.close()

    assert len(jobs) == 2  # Organization node ignored
    titles = {j.title for j in jobs}
    assert titles == {"Robotics Engineer", "CAD Designer"}
    assert all(j.application_url and j.source_url for j in jobs)


@respx.mock
def test_json_ld_connector_missing_page_fails_soft() -> None:
    listing_url = "https://careers.acme.example/open-roles"
    respx.get(listing_url).mock(side_effect=httpx.ConnectError("boom"))
    client = _client()
    assert JsonLdConnector(client).fetch(_company("json_ld", listing_url)) == []
    client.close()
