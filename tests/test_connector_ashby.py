"""Ashby connector tests (mocked transport, isListed + full-description)."""

from __future__ import annotations

import httpx
import respx

from jobtracker.connectors.ashby import AshbyConnector
from jobtracker.connectors.http import HttpClient
from jobtracker.normalize import normalize
from jobtracker.schemas.company import CompanyConfig
from jobtracker.settings import Settings
from tests.conftest import load_fixture

LIST_URL = "https://api.ashbyhq.com/posting-api/job-board/acme"
DETAIL_URL = "https://api.ashbyhq.com/posting-api/job-board/acme/ashby-2"


def _client() -> HttpClient:
    return HttpClient(Settings(rate_limit_per_host=0.0), sleep=lambda _s: None)


def _company() -> CompanyConfig:
    return CompanyConfig(id="acme", name="Acme", ats="ashby", board_token="acme")


@respx.mock
def test_ashby_fetch_skips_unlisted_and_fetches_full_description():
    respx.get(LIST_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("ashby_job_board.json"))
    )
    detail_route = respx.get(DETAIL_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("ashby_job_2.json"))
    )

    client = _client()
    raw_jobs = AshbyConnector(client).fetch(_company())
    client.close()

    # ashby-3 is isListed=false and must be excluded.
    ids = {r.source_job_id for r in raw_jobs}
    assert ids == {"ashby-1", "ashby-2"}
    by_id = {r.source_job_id: r for r in raw_jobs}

    first = by_id["ashby-1"]
    assert first.application_url == "https://jobs.ashbyhq.com/acme/ashby-1/application"
    assert first.source_url == "https://jobs.ashbyhq.com/acme/ashby-1"
    assert first.extra

    # ashby-2 omitted descriptionHtml in the list -> detail call fires.
    assert detail_route.called
    second = by_id["ashby-2"]
    assert "thermal" in second.description_html.lower()

    company = _company()
    canon_first = normalize(first, company)
    assert canon_first.city == "Vancouver"
    assert canon_first.province_state == "BC"
    assert canon_first.country == "Canada"
    assert "MATLAB" in canon_first.skills

    canon_second = normalize(second, company)
    assert canon_second.description
    assert "heat transfer" in canon_second.skills
    assert canon_second.remote_status == "remote"
