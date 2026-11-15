"""Tests for the company service: sync, add-with-baseline, remove-keeps-history.

All connector interaction is faked; no network is used.
"""

from __future__ import annotations

import httpx
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


class _RaisingConnector:
    """Connector whose ``fetch`` always raises a given exception."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def fetch(self, company: CompanyConfig) -> list[RawJob]:
        raise self._exc


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://board.example/jobs")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


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


@pytest.mark.parametrize(
    "exc",
    [
        _http_status_error(403),  # Phenom / Oracle ORC auth wall to the runner.
        _http_status_error(429),  # rate limited.
        _http_status_error(521),  # Cloudflare "web server is down" (Workday).
        _http_status_error(503),  # transient server error.
        httpx.ConnectError("no route"),  # transport error.
    ],
)
def test_add_from_url_succeeds_when_board_unreachable(
    session: Session, monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    """A positively-detected board that is unreachable at add time still adds.

    Phenom (careers.rtx.com) and Oracle ORC return 403, and Workday returns a
    Cloudflare 521, to the GitHub Actions runner. The company must still be
    persisted (with a zero-job baseline, so nothing is falsely new); the
    scheduled poll imports its jobs later.
    """
    monkeypatch.setattr(
        company_service, "get_connector", lambda ats, http: _RaisingConnector(exc)
    )
    monkeypatch.setattr(
        company_service,
        "detect_ats",
        lambda url: type("D", (), {"ats": "workday", "board_token": "acme/wd1/ext"})(),
    )

    result = company_service.add_from_url(
        session, "https://acme.wd1.myworkdayjobs.com/ext", http=object(), name="Acme"
    )
    session.flush()

    # The add succeeded with a zero-job baseline and the row is persisted.
    assert result.baseline_jobs == 0
    assert result.ats == "workday"
    company = session.get(Company, "acme")
    assert company is not None
    assert company.enabled is True
    assert company.active is True
    # No jobs and (critically) no false-new events.
    assert session.query(Job).count() == 0
    assert session.query(JobEvent).count() == 0


def test_add_from_url_reraises_non_reachability_errors(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A genuine bug (not a reachability failure) is NOT swallowed."""
    monkeypatch.setattr(
        company_service,
        "get_connector",
        lambda ats, http: _RaisingConnector(ValueError("parser bug")),
    )
    monkeypatch.setattr(
        company_service,
        "detect_ats",
        lambda url: type("D", (), {"ats": "greenhouse", "board_token": "acme"})(),
    )

    with pytest.raises(ValueError, match="parser bug"):
        company_service.add_from_url(
            session, "https://boards.greenhouse.io/acme", http=object(), name="Acme"
        )


def test_add_from_url_fails_clearly_when_ats_not_determinable(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """detect_ats returning the html fallback keeps its clear failure path."""
    monkeypatch.setattr(
        company_service,
        "detect_ats",
        lambda url: type("D", (), {"ats": "html", "board_token": None})(),
    )

    with pytest.raises(ValueError, match="Could not detect a supported ATS"):
        company_service.add_from_url(
            session, "https://example.com/careers", http=object(), name="Example"
        )
    # Nothing was persisted for the unresolved add.
    assert session.query(Company).count() == 0


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
