"""SmartRecruiters connector tests (pagination + per-posting detail body)."""

from __future__ import annotations

import httpx
import respx

from jobtracker.connectors.http import HttpClient
from jobtracker.connectors.smartrecruiters import SmartRecruitersConnector
from jobtracker.normalize import normalize
from jobtracker.schemas.company import CompanyConfig
from jobtracker.settings import Settings
from tests.conftest import load_fixture

LIST_URL = "https://api.smartrecruiters.com/v1/companies/Acme/postings"
DETAIL_SR1 = "https://api.smartrecruiters.com/v1/companies/Acme/postings/sr-1"
DETAIL_SR2 = "https://api.smartrecruiters.com/v1/companies/Acme/postings/sr-2"


def _client() -> HttpClient:
    return HttpClient(Settings(rate_limit_per_host=0.0), sleep=lambda _s: None)


def _company() -> CompanyConfig:
    return CompanyConfig(
        id="acme", name="Acme", ats="smartrecruiters", board_token="Acme"
    )


@respx.mock
def test_smartrecruiters_fetch_paginates_and_fetches_detail_body():
    respx.get(LIST_URL).mock(
        return_value=httpx.Response(
            200, json=load_fixture("smartrecruiters_postings_page1.json")
        )
    )
    d1 = respx.get(DETAIL_SR1).mock(
        return_value=httpx.Response(
            200, json=load_fixture("smartrecruiters_posting_sr1.json")
        )
    )
    d2 = respx.get(DETAIL_SR2).mock(
        return_value=httpx.Response(
            200, json=load_fixture("smartrecruiters_posting_sr2.json")
        )
    )

    client = _client()
    raw_jobs = SmartRecruitersConnector(client).fetch(_company())
    client.close()

    assert len(raw_jobs) == 2
    # The list omits the body: a per-posting detail call must fire for each.
    assert d1.called and d2.called
    by_id = {r.source_job_id: r for r in raw_jobs}

    first = by_id["sr-1"]
    assert first.application_url == "https://jobs.smartrecruiters.com/Acme/sr-1/apply"
    assert first.source_url == "https://jobs.smartrecruiters.com/Acme/sr-1"
    assert "product design" in first.description_html.lower()
    assert first.extra and "_detail" in first.extra  # raw payload incl. detail

    company = _company()
    canon_first = normalize(first, company)
    assert canon_first.city == "Mississauga"
    assert canon_first.province_state == "ON"
    assert canon_first.country == "Canada"
    assert "SolidWorks" in canon_first.skills
    assert "FEA" in canon_first.skills
    assert canon_first.description  # full description captured

    canon_second = normalize(by_id["sr-2"], company)
    assert canon_second.country == "United States"
    assert "CNC" in canon_second.skills
