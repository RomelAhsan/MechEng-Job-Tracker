"""Tests for the health service accuracy report (flags each category)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from jobtracker.db.models import AiAnalysisCache, Company, Job, SourceHealth
from jobtracker.services import health_service


def _company(session: Session, company_id: str, **kwargs: object) -> None:
    session.add(Company(id=company_id, name=company_id.title(), ats="greenhouse", **kwargs))


def test_accuracy_report_flags_each_category(session: Session) -> None:
    now = datetime.now(UTC)
    _company(session, "acme", enabled=True, active=True)
    _company(session, "silent", enabled=True, active=True)

    # Missing application URL + unknown term + unknown sponsorship.
    session.add(
        Job(
            job_id="j-missing",
            company_id="acme",
            source_job_id="1",
            title="Intern",
            status="active",
            application_url=None,
            term_season="unknown",
            visa_sponsorship="unknown",
        )
    )
    # A clean active job (should not be flagged).
    session.add(
        Job(
            job_id="j-clean",
            company_id="acme",
            source_job_id="2",
            title="Mechanical Intern",
            status="active",
            application_url="https://apply.example/2",
            term_season="spring",
            visa_sponsorship="provided",
        )
    )
    # Removed job missing its removed_at timestamp (inconsistent state).
    session.add(
        Job(
            job_id="j-removed",
            company_id="acme",
            source_job_id="3",
            title="Old Intern",
            status="removed",
            removed_at=None,
            application_url="https://apply.example/3",
        )
    )
    # Stale source: checked long ago.
    session.add(
        SourceHealth(
            company_id="acme",
            last_checked_at=now - timedelta(days=10),
            last_successful_check_at=now - timedelta(days=10),
        )
    )
    # "silent" is active+enabled with no active jobs and no successful check.
    session.flush()

    report = health_service.accuracy_report(session)

    assert "j-missing" in report.missing_application_url
    assert "j-missing" in report.unknown_term
    assert "j-missing" in report.unknown_sponsorship
    assert "j-clean" not in report.missing_application_url
    assert "j-removed" in report.removed_but_present
    assert "acme" in report.stale_sources
    assert "silent" in report.active_but_absent
    assert report.total_issues() > 0


def test_accuracy_report_flags_low_confidence_ai(session: Session) -> None:
    _company(session, "acme", enabled=True, active=True)
    for job_id in ("j-low", "j-high", "j-override", "j-none"):
        session.add(
            Job(
                job_id=job_id,
                company_id="acme",
                source_job_id=job_id,
                title="Mechanical Intern",
                status="active",
                application_url=f"https://apply.example/{job_id}",
                term_season="spring",
                visa_sponsorship="provided",
            )
        )
    # Low confidence -> flagged.
    session.add(AiAnalysisCache(job_id="j-low", confidence=0.2, overridden=False))
    # High confidence -> not flagged.
    session.add(AiAnalysisCache(job_id="j-high", confidence=0.9, overridden=False))
    # Low confidence but a manual override -> not flagged.
    session.add(AiAnalysisCache(job_id="j-override", confidence=0.1, overridden=True))
    # No confidence recorded -> not flagged.
    session.add(AiAnalysisCache(job_id="j-none", confidence=None, overridden=False))
    session.flush()

    report = health_service.accuracy_report(session)

    assert report.low_confidence_ai == ["j-low"]
    assert "j-high" not in report.low_confidence_ai
    assert "j-override" not in report.low_confidence_ai
    assert "j-none" not in report.low_confidence_ai


def test_source_health_view_returns_rows(session: Session) -> None:
    _company(session, "acme")
    session.add(SourceHealth(company_id="acme", last_status="ok", current_job_count=5))
    session.flush()

    rows = health_service.source_health(session)
    assert len(rows) == 1
    assert rows[0].company_id == "acme"
    assert rows[0].last_status == "ok"
