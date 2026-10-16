"""Lever connector tests (mocked transport, full-description capture)."""

from __future__ import annotations

import httpx
import respx

from jobtracker.connectors.http import HttpClient
from jobtracker.connectors.lever import LeverConnector
from jobtracker.normalize import normalize
from jobtracker.schemas.company import CompanyConfig
from jobtracker.settings import Settings
from tests.conftest import load_fixture

LIST_URL = "https://api.lever.co/v0/postings/acme"
DETAIL_URL = "https://api.lever.co/v0/postings/acme/lever-2"


def _client() -> HttpClient:
    return HttpClient(Settings(rate_limit_per_host=0.0), sleep=lambda _s: None)


def _company() -> CompanyConfig:
    return CompanyConfig(id="acme", name="Acme", ats="lever", board_token="acme")


@respx.mock
def test_lever_fetch_normalizes_and_fetches_full_description():
    respx.get(LIST_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("lever_postings.json"))
    )
    detail_route = respx.get(DETAIL_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("lever_posting_2.json"))
    )

    client = _client()
    raw_jobs = LeverConnector(client).fetch(_company())
    client.close()

    assert len(raw_jobs) == 2
    by_id = {r.source_job_id: r for r in raw_jobs}

    first = by_id["lever-1"]
    assert first.application_url == "https://jobs.lever.co/acme/lever-1/apply"
    assert first.source_url == "https://jobs.lever.co/acme/lever-1"
    assert first.department == "Engineering"
    assert first.team == "Product Design"
    assert first.extra

    # lever-2 omitted its description in the list -> detail call fires.
    assert detail_route.called
    second = by_id["lever-2"]
    assert "cnc" in second.description_html.lower()

    company = _company()
    canon_first = normalize(first, company)
    assert canon_first.city == "Kitchener"
    assert canon_first.province_state == "ON"
    assert canon_first.country == "Canada"
    assert "CAD" in canon_first.skills
    assert "GD&T" in canon_first.skills
    assert canon_first.requirements  # bullets parsed from lists

    canon_second = normalize(second, company)
    assert canon_second.description
    assert "DFM" in canon_second.skills
