"""Workday student targeting, paging, and the empty-board guard."""

from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy.orm import Session

from jobtracker.connectors.http import HttpClient
from jobtracker.connectors.workday import WorkdayConnector
from jobtracker.connectors.workday_targeting import (
    Query,
    TargetingPlan,
    build_plan,
    public_search_urls,
)
from jobtracker.db.models import Company, Job
from jobtracker.schemas.company import CompanyConfig
from jobtracker.services import ingest_service
from jobtracker.settings import Settings

JOBS = "https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/Careers/jobs"

FACETS = [
    {
        "facetParameter": "workerSubType",
        "values": [
            {"id": "stu", "descriptor": "Co-op/Intern (Fixed Term)", "count": 30},
            {"id": "app", "descriptor": "Apprentice (Fixed Term)", "count": 9},
            {"id": "reg", "descriptor": "Regular", "count": 900},
        ],
    },
    {
        "facetParameter": "locationMainGroup",
        "values": [
            {
                "facetParameter": "locationCountry",
                "values": [
                    {"id": "ca", "descriptor": "Canada", "count": 80},
                    {"id": "us", "descriptor": "United States of America", "count": 120},
                    {"id": "fr", "descriptor": "France", "count": 700},
                ],
            }
        ],
    },
]


def test_build_plan_picks_student_and_country_filters() -> None:
    plan = build_plan({"total": 1000, "facets": FACETS})
    assert not plan.full_board
    assert plan.student == {"workerSubType": ["stu"]}  # apprentices left out
    assert plan.country == {"locationCountry": ["ca", "us"]}
    assert plan.reference_total == 200
    first = plan.queries()[0]
    assert first.applied_facets == {"workerSubType": ("stu",), "locationCountry": ("ca", "us")}


def test_small_board_is_listed_in_full() -> None:
    plan = build_plan({"total": 60, "facets": FACETS})
    assert plan.full_board
    assert plan.queries() == [Query({}, "")]


def test_keyword_skipped_when_results_are_mostly_full_time() -> None:
    full_time = ["Senior Engineer"] * 18 + ["Summer Intern", "Co-op Student"]
    assert TargetingPlan.skip_keyword(500, full_time)
    assert not TargetingPlan.skip_keyword(25, full_time)  # small: always kept
    assert not TargetingPlan.skip_keyword(500, ["Intern, Thermal"] * 10 + ["Engineer"] * 10)


def test_public_search_urls() -> None:
    plan = TargetingPlan(
        full_board=False,
        country={"locationCountry": ["ca"]},
        student={"workerSubType": ["stu"]},
        keywords=["stagiaire"],
    )
    urls = public_search_urls("https://acme.wd3.myworkdayjobs.com", "Careers", plan)
    assert urls == [
        "https://acme.wd3.myworkdayjobs.com/en-US/Careers?workerSubType=stu&locationCountry=ca",
        "https://acme.wd3.myworkdayjobs.com/en-US/Careers?q=stagiaire&locationCountry=ca",
    ]


def _page(paths: list[str], total: int) -> dict:
    return {
        "total": total,
        "jobPostings": [{"title": f"Intern {p}", "externalPath": f"/job/X/{p}"} for p in paths],
    }


@respx.mock
def test_paging_continues_when_later_pages_report_total_zero() -> None:
    """Workday reports the count only on page one; later pages say 0."""
    route = respx.post(JOBS)
    route.side_effect = [
        httpx.Response(200, json=_page([f"a{i}" for i in range(20)], 50)),
        httpx.Response(200, json=_page([f"b{i}" for i in range(20)], 0)),
        httpx.Response(200, json=_page([f"c{i}" for i in range(10)], 0)),
    ]
    connector = WorkdayConnector(HttpClient(Settings(rate_limit_per_host=0)))
    from jobtracker.connectors.workday import WorkdayCoordinates

    postings = connector._search_all(
        WorkdayCoordinates("acme", "wd3", "Careers"), Query({}, "")
    )
    assert len(postings) == 50
    assert route.call_count == 3


def test_empty_board_keeps_stored_postings(session: Session) -> None:
    session.add(Company(id="acme", name="Acme", ats="greenhouse", enabled=True, active=True))
    for i in range(6):
        session.add(
            Job(job_id=f"j{i}", company_id="acme", source_job_id=str(i), title="Intern",
                status="active")
        )
    session.flush()
    company = session.get(Company, "acme")
    config = CompanyConfig(id="acme", name="Acme", ats="greenhouse", board_token="acme")
    with pytest.raises(ingest_service.EmptyBoardError):
        ingest_service._process_raw_jobs(session, company, config, [])
    assert all(session.get(Job, f"j{i}").status == "active" for i in range(6))


def test_describe_error_is_plain_english() -> None:
    request = httpx.Request("GET", "https://x")
    not_found = httpx.HTTPStatusError(
        "404", request=request, response=httpx.Response(404, request=request)
    )
    assert "no longer works" in ingest_service.describe_error(not_found)
    assert "did not answer" in ingest_service.describe_error(httpx.ReadTimeout("t"))
    assert ingest_service.describe_error(RawJobError()).startswith("the board could not be read")


class RawJobError(Exception):
    pass
