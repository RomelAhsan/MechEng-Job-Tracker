"""Workday connector tests (offset pagination + per-posting detail body)."""

from __future__ import annotations

import httpx
import respx

from jobtracker.connectors.http import HttpClient
from jobtracker.connectors.workday import WorkdayConnector, parse_workday_url
from jobtracker.normalize import normalize
from jobtracker.schemas.company import CompanyConfig
from jobtracker.settings import Settings
from tests.conftest import load_fixture

CXS = "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/AcmeCareers"
JOBS_URL = f"{CXS}/jobs"
DETAIL_1 = f"{CXS}/job/US-CA-Santa-Clara/Mechanical-Design-Engineer-Intern_JR1001"
DETAIL_2 = f"{CXS}/job/US-TX-Austin/Thermal-Systems-Engineer_JR1002"
DETAIL_3 = f"{CXS}/job/US-MI-Detroit/Manufacturing-Engineer_JR1003"


def _client() -> HttpClient:
    return HttpClient(Settings(rate_limit_per_host=0.0), sleep=lambda _s: None)


def _company(**kw: object) -> CompanyConfig:
    base: dict = {
        "id": "acme",
        "name": "Acme",
        "ats": "workday",
        "tenant": "acme",
        "data_center": "wd5",
        "site": "AcmeCareers",
    }
    base.update(kw)
    return CompanyConfig(**base)


def test_parse_workday_url_variants() -> None:
    a = parse_workday_url("https://acme.wd5.myworkdayjobs.com/AcmeCareers")
    assert (a.tenant, a.data_center, a.site) == ("acme", "wd5", "AcmeCareers")
    b = parse_workday_url(
        "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs"
    )
    assert (b.tenant, b.data_center, b.site) == (
        "nvidia",
        "wd5",
        "NVIDIAExternalCareerSite",
    )
    assert parse_workday_url("https://example.com/careers") is None


@respx.mock
def test_workday_paginates_and_fetches_detail_body() -> None:
    route = respx.post(JOBS_URL)
    route.side_effect = [
        httpx.Response(200, json=load_fixture("workday_jobs_page1.json")),
        httpx.Response(200, json=load_fixture("workday_jobs_page2.json")),
    ]
    d1 = respx.get(DETAIL_1).mock(
        return_value=httpx.Response(200, json=load_fixture("workday_detail_jr1001.json"))
    )
    d2 = respx.get(DETAIL_2).mock(
        return_value=httpx.Response(200, json=load_fixture("workday_detail_jr1002.json"))
    )
    d3 = respx.get(DETAIL_3).mock(
        return_value=httpx.Response(200, json=load_fixture("workday_detail_jr1003.json"))
    )

    client = _client()
    raw_jobs = WorkdayConnector(client).fetch(_company())
    client.close()

    assert len(raw_jobs) == 3
    assert d1.called and d2.called and d3.called
    # Paged across offset: two POSTs to the jobs endpoint.
    assert route.call_count == 2

    by_id = {r.source_job_id: r for r in raw_jobs}
    first = by_id["aaaa1111bbbb2222cccc3333"]
    assert first.title == "Mechanical Design Engineer Intern"
    assert "SolidWorks" in first.description_html
    # Canonical public apply URL from externalUrl.
    assert first.application_url == (
        "https://acme.wd5.myworkdayjobs.com/AcmeCareers/job/"
        "US-CA-Santa-Clara/Mechanical-Design-Engineer-Intern_JR1001"
    )
    assert first.source_url == first.application_url
    assert first.extra and "_detail" in first.extra

    canon = normalize(first, _company())
    assert canon.description  # full description captured
    assert "SolidWorks" in canon.skills


@respx.mock
def test_workday_application_url_falls_back_to_constructed_public_url() -> None:
    respx.post(JOBS_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("workday_jobs_page1.json"))
    )
    # Detail without externalUrl: connector builds the public URL itself.
    respx.get(DETAIL_1).mock(
        return_value=httpx.Response(
            200,
            json={"jobPostingInfo": {"id": "x1", "title": "T", "jobDescription": "d"}},
        )
    )
    respx.get(DETAIL_2).mock(
        return_value=httpx.Response(
            200,
            json={"jobPostingInfo": {"id": "x2", "title": "T2", "jobDescription": "d2"}},
        )
    )

    client = _client()
    raw_jobs = WorkdayConnector(client).fetch(_company())
    client.close()

    urls = {r.application_url for r in raw_jobs}
    assert (
        "https://acme.wd5.myworkdayjobs.com/AcmeCareers/job/"
        "US-CA-Santa-Clara/Mechanical-Design-Engineer-Intern_JR1001" in urls
    )


@respx.mock
def test_workday_empty_board_returns_empty_list() -> None:
    respx.post(JOBS_URL).mock(
        return_value=httpx.Response(200, json={"total": 0, "jobPostings": []})
    )
    client = _client()
    raw_jobs = WorkdayConnector(client).fetch(_company())
    client.close()
    assert raw_jobs == []


@respx.mock
def test_workday_auth_walled_raises_not_swallowed() -> None:
    # 401 is NOT retried or bypassed; it surfaces so the company stays a stub.
    respx.post(JOBS_URL).mock(return_value=httpx.Response(401, json={"error": "nope"}))
    client = _client()
    try:
        WorkdayConnector(client).fetch(_company())
    except httpx.HTTPError:
        pass  # surfaced to the fail-soft ingest layer, as intended
    else:  # pragma: no cover - a 401 must not silently return jobs
        raise AssertionError("auth-walled board must surface, not return jobs")
    finally:
        client.close()


@respx.mock
def test_workday_detail_auth_wall_surfaces() -> None:
    """A board that lists publicly but auth-walls detail GETs is a source error.

    The list POST succeeds, but the per-posting detail endpoint returns 401; we
    must surface it (CORE RULE #1) rather than emit a posting with an empty
    description that looks healthy.
    """
    respx.post(JOBS_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("workday_jobs_page1.json"))
    )
    respx.get(DETAIL_1).mock(return_value=httpx.Response(401, json={"error": "nope"}))

    client = _client()
    try:
        WorkdayConnector(client).fetch(_company())
    except httpx.HTTPError:
        pass  # surfaced to the fail-soft ingest layer, as intended
    else:  # pragma: no cover - a detail 401 must not silently return jobs
        raise AssertionError("detail auth-wall must surface, not return jobs")
    finally:
        client.close()


@respx.mock
def test_workday_resolves_coordinates_from_careers_url() -> None:
    respx.post(JOBS_URL).mock(
        return_value=httpx.Response(200, json={"total": 0, "jobPostings": []})
    )
    company = CompanyConfig(
        id="acme",
        name="Acme",
        ats="workday",
        careers_url="https://acme.wd5.myworkdayjobs.com/AcmeCareers",
    )
    client = _client()
    assert WorkdayConnector(client).fetch(company) == []
    client.close()


def test_workday_missing_coordinates_returns_empty() -> None:
    client = _client()
    company = CompanyConfig(id="acme", name="Acme", ats="workday")
    assert WorkdayConnector(client).fetch(company) == []
    client.close()
