"""Tests for add-company-by-name resolution (SPEC ADD-COMPANY ASSISTANT).

All board probes are MOCKED: no live network is used. We verify that a name
resolves to the first public board returning real postings, baseline-imports it
(existing jobs marked seen, NO false-new events), and that an unresolvable name
fails gracefully with a helpful message.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from jobtracker.connectors.base import RawJob
from jobtracker.connectors.fallback import FallbackConnector
from jobtracker.db.models import Company, Job, JobEvent
from jobtracker.schemas.company import CompanyConfig
from jobtracker.services import company_service


def _raw(source_job_id: str, title: str) -> RawJob:
    return RawJob(
        source_job_id=source_job_id,
        title=title,
        location="Toronto, Ontario, Canada",
        description_html=f"<p>{title} description</p>",
        application_url=f"https://apply.example/{source_job_id}",
        source_url=f"https://jobs.example/{source_job_id}",
    )


class _ConditionalConnector:
    """Returns postings only when the config URL matches ``live_url``.

    Simulates probing several candidate boards where only one slug is a real
    live board (the rest return no postings / would 404).
    """

    def __init__(self, live_url: str, raw_jobs: list[RawJob]) -> None:
        self._live_url = live_url
        self._raw_jobs = raw_jobs

    def fetch(self, company: CompanyConfig) -> list[RawJob]:
        if company.careers_url == self._live_url:
            return list(self._raw_jobs)
        return []


def test_name_candidates_cover_public_boards() -> None:
    candidates = company_service.name_candidates("Acme Robotics")
    urls = [c.url for c in candidates]
    # Both slug spellings and all four public ATS patterns are probed.
    assert "https://boards.greenhouse.io/acme-robotics" in urls
    assert "https://jobs.lever.co/acme-robotics" in urls
    assert "https://jobs.ashbyhq.com/acme-robotics" in urls
    assert "https://apply.workable.com/acme-robotics" in urls
    assert "https://boards.greenhouse.io/acmerobotics" in urls
    # Workday is intentionally NOT probed (needs a verified tenant/site triple).
    assert not any("myworkdayjobs.com" in u for u in urls)


def test_add_from_name_resolves_and_baseline_imports(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    live_url = "https://jobs.lever.co/toyota"
    raw_jobs = [_raw("1", "Mechanical Intern"), _raw("2", "Design Intern")]
    connector = _ConditionalConnector(live_url, raw_jobs)
    monkeypatch.setattr(company_service, "get_connector", lambda ats, http: connector)

    result = company_service.add_from_name(session, "Toyota", http=object())
    session.flush()

    assert result.resolved is True
    assert result.add_result is not None
    assert result.add_result.ats == "lever"
    assert result.add_result.baseline_jobs == 2
    assert "lever" in result.message
    # Greenhouse was probed before the live Lever board resolved.
    assert result.tried[0].startswith("https://boards.greenhouse.io/")
    assert live_url in result.tried

    # Company row created and jobs baseline-imported with NO false-new events.
    assert session.get(Company, "toyota") is not None
    assert session.query(Job).count() == 2
    assert session.query(JobEvent).count() == 0


def test_add_from_name_unresolvable_fails_gracefully(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every probe returns no postings -> nothing resolves.
    connector = _ConditionalConnector("https://never.matches/", [])
    monkeypatch.setattr(company_service, "get_connector", lambda ats, http: connector)

    result = company_service.add_from_name(session, "Totally Unknown Co", http=object())

    assert result.resolved is False
    assert result.add_result is None
    assert "Could not auto-resolve" in result.message
    assert "careers URL" in result.message
    # It did try the public boards before giving up.
    assert result.tried
    # Nothing was written to the database.
    assert session.query(Company).count() == 0
    assert session.query(Job).count() == 0


def test_add_from_name_empty_name_is_graceful(session: Session) -> None:
    result = company_service.add_from_name(session, "   ", http=object())
    assert result.resolved is False
    assert "company name" in result.message.lower()


def test_add_from_name_skips_fallback_connectors(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A fallback connector means "no real connector" -> probe counts as 0.
    monkeypatch.setattr(
        company_service, "get_connector", lambda ats, http: FallbackConnector()
    )
    result = company_service.add_from_name(session, "Some Co", http=object())
    assert result.resolved is False
