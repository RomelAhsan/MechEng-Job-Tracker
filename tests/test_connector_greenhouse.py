"""Greenhouse connector tests (mocked transport, full-description capture)."""

from __future__ import annotations

import httpx
import respx

from jobtracker.connectors.greenhouse import GreenhouseConnector
from jobtracker.connectors.http import HttpClient
from jobtracker.normalize import normalize
from jobtracker.schemas.company import CompanyConfig
from jobtracker.settings import Settings
from tests.conftest import load_fixture

LIST_URL = "https://boards-api.greenhouse.io/v1/boards/acme/jobs"
DETAIL_URL = "https://boards-api.greenhouse.io/v1/boards/acme/jobs/102"


def _client() -> HttpClient:
    return HttpClient(Settings(rate_limit_per_host=0.0), sleep=lambda _s: None)


def _company() -> CompanyConfig:
    return CompanyConfig(id="acme", name="Acme", ats="greenhouse", board_token="acme")


@respx.mock
def test_greenhouse_fetch_normalizes_and_fetches_full_description():
    respx.get(LIST_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("greenhouse_jobs.json"))
    )
    detail_route = respx.get(DETAIL_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("greenhouse_job_102.json"))
    )

    client = _client()
    raw_jobs = GreenhouseConnector(client).fetch(_company())
    client.close()

    assert len(raw_jobs) == 2
    by_id = {r.source_job_id: r for r in raw_jobs}

    # Job 101 had inline content.
    first = by_id["101"]
    assert first.application_url == "https://boards.greenhouse.io/acme/jobs/101"
    assert first.source_url == "https://boards.greenhouse.io/acme/jobs/101"
    assert first.extra  # raw payload stored
    assert first.department == "Hardware"

    # Job 102 omitted content in the list -> detail call must have fired.
    assert detail_route.called
    second = by_id["102"]
    assert "prototyping" in second.description_html.lower()

    company = _company()
    canon_first = normalize(first, company)
    assert canon_first.city == "Toronto"
    assert canon_first.province_state == "ON"
    assert canon_first.country == "Canada"
    assert "SolidWorks" in canon_first.skills
    assert canon_first.raw_payload is not None

    canon_second = normalize(second, company)
    assert "prototyping" in [s.lower() for s in canon_second.skills]
    assert canon_second.description  # full description captured via detail call
