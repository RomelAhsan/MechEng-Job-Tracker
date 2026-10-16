"""Tests for the fail-soft ingest service.

One failing company must not abort the run, and source_health must record both
successes and errors. All connectors are faked; no network is used.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from jobtracker.connectors.base import RawJob
from jobtracker.db.models import Company, Job, SourceHealth
from jobtracker.schemas.company import CompanyConfig
from jobtracker.services import ingest_service


class _OkConnector:
    ats = "greenhouse"

    def __init__(self, raw_jobs: list[RawJob]) -> None:
        self._raw_jobs = raw_jobs

    def fetch(self, company: CompanyConfig) -> list[RawJob]:
        return list(self._raw_jobs)


class _BoomConnector:
    ats = "greenhouse"

    def fetch(self, company: CompanyConfig) -> list[RawJob]:
        raise RuntimeError("boom: source unreachable")


def _raw(source_job_id: str, title: str) -> RawJob:
    return RawJob(
        source_job_id=source_job_id,
        title=title,
        location="Toronto, Ontario, Canada",
        description_html=f"<p>{title}</p>",
        application_url=f"https://apply.example/{source_job_id}",
        source_url=f"https://jobs.example/{source_job_id}",
    )


def _seed_two_companies(session: Session) -> None:
    session.add(Company(id="good", name="Good", ats="greenhouse", enabled=True, active=True))
    session.add(Company(id="bad", name="Bad", ats="greenhouse", enabled=True, active=True))
    # Commit the seed so a per-company rollback inside run() does not discard
    # the still-pending companies (matches the CLI, which syncs+commits first).
    session.commit()


def test_one_failure_does_not_abort_run(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_two_companies(session)

    # Companies are listed alphabetically (bad, good); hand out connectors in
    # that order so the failing source is isolated and the run still finishes.
    def dispatch(ats, http):  # noqa: ARG001
        return dispatch.queue.pop(0)

    dispatch.queue = [_BoomConnector(), _OkConnector([_raw("1", "Mechanical Intern")])]
    monkeypatch.setattr(ingest_service, "get_connector", dispatch)

    summary = ingest_service.run(session, http=object())

    assert len(summary.companies) == 2
    assert summary.errors == 1
    statuses = {c.company_id: c.status for c in summary.companies}
    assert statuses["good"] == "ok"
    assert statuses["bad"] == "error"
    # The good company's job persisted despite the bad company failing.
    assert session.query(Job).filter(Job.company_id == "good").count() == 1


def test_source_health_records_success_and_error(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_two_companies(session)

    def dispatch(ats, http):  # noqa: ARG001
        return dispatch.queue.pop(0)

    # Companies iterate alphabetically (bad, good).
    dispatch.queue = [_BoomConnector(), _OkConnector([_raw("1", "Mechanical Intern")])]
    monkeypatch.setattr(ingest_service, "get_connector", dispatch)

    ingest_service.run(session, http=object())

    good_health = session.get(SourceHealth, "good")
    bad_health = session.get(SourceHealth, "bad")
    assert good_health.last_status == "ok"
    assert good_health.last_successful_check_at is not None
    assert bad_health.last_status == "error"
    assert (bad_health.error_count or 0) >= 1
    assert bad_health.last_error is not None


def test_fallback_marks_requires_fallback(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session.add(Company(id="htmlco", name="HtmlCo", ats="html", enabled=True, active=True))
    session.commit()

    # Use the real registry here (html -> FallbackConnector).
    summary = ingest_service.run(session, http=object())

    assert len(summary.companies) == 1
    assert summary.companies[0].status == "requires_fallback"
    health = session.get(SourceHealth, "htmlco")
    assert health.last_status == "requires_fallback"
