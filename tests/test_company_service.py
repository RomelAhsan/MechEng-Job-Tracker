"""Tests for the company service: sync, add-with-baseline, remove-keeps-history.

All connector interaction is faked; no network is used.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from jobtracker.connectors.base import RawJob
from jobtracker.db.models import Company, Job, JobEvent
from jobtracker.schemas.company import CompanyConfig
from jobtracker.services import company_service


class _FakeConnector:
    """Connector returning canned RawJobs; records the config it was given."""

    ats = "greenhouse"

    def __init__(self, raw_jobs: list[RawJob]) -> None:
        self._raw_jobs = raw_jobs

    def fetch(self, company: CompanyConfig) -> list[RawJob]:
        return list(self._raw_jobs)


def _raw(source_job_id: str, title: str) -> RawJob:
    return RawJob(
        source_job_id=source_job_id,
        title=title,
        location="Toronto, Ontario, Canada",
        description_html=f"<p>{title} description</p>",
        application_url=f"https://apply.example/{source_job_id}",
        source_url=f"https://jobs.example/{source_job_id}",
    )


def _config(company_id: str = "acme") -> CompanyConfig:
    return CompanyConfig(
        id=company_id,
        name="Acme",
        ats="greenhouse",
        board_token="acme",
        careers_url="https://boards.greenhouse.io/acme",
        enabled=True,
        priority="high",
    )


def test_sync_from_yaml_upserts(session: Session) -> None:
    result = company_service.sync_from_yaml(session, [_config()])
    assert result.created == ["acme"]
    assert result.updated == []

    # Second sync with a changed name updates in place, preserving the row.
    changed = _config()
    changed.name = "Acme Robotics"
    result2 = company_service.sync_from_yaml(session, [changed])
    assert result2.created == []
    assert result2.updated == ["acme"]
    assert session.get(Company, "acme").name == "Acme Robotics"


def test_add_from_url_baseline_suppresses_false_new(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    connector = _FakeConnector([_raw("1", "Mechanical Intern"), _raw("2", "Design Intern")])
    monkeypatch.setattr(
        company_service, "get_connector", lambda ats, http: connector
    )
    monkeypatch.setattr(
        company_service,
        "detect_ats",
        lambda url: type("D", (), {"ats": "greenhouse", "board_token": "acme"})(),
    )

    result = company_service.add_from_url(
        session, "https://boards.greenhouse.io/acme", http=object(), name="Acme"
    )
    session.flush()

    assert result.baseline_jobs == 2
    # Baseline import inserts rows but emits NO 'new' events (no false-new).
    assert session.query(Job).count() == 2
    assert session.query(JobEvent).count() == 0


def test_remove_keeps_history(session: Session) -> None:
    company_service.sync_from_yaml(session, [_config()])
    session.add(
        Job(
            job_id="job-1",
            company_id="acme",
            source_job_id="1",
            title="Mechanical Intern",
            status="active",
        )
    )
    session.flush()

    company_service.remove(session, "acme")

    company = session.get(Company, "acme")
    assert company.active is False
    assert company.enabled is False
    # Historical job rows are retained.
    assert session.get(Job, "job-1") is not None


def test_enable_disable(session: Session) -> None:
    company_service.sync_from_yaml(session, [_config()])
    company_service.disable(session, "acme")
    assert session.get(Company, "acme").enabled is False
    company_service.enable(session, "acme")
    company = session.get(Company, "acme")
    assert company.enabled is True
    assert company.active is True
