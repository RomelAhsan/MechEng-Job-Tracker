"""Dashboard read-side ITAR exclusion: ITAR / US-citizen-only jobs are filtered
out of EVERY view entirely (not merely flagged), including the archive and
regardless of the eligibility-status filter widget."""

from __future__ import annotations

from sqlalchemy.orm import Session

from jobtracker.db.models import Company, Job
from jobtracker.extract.work_auth import ELIGIBLE, INELIGIBLE_ITAR
from jobtracker.services import dashboard_service
from jobtracker.services.dashboard_service import JobFilters


def _seed(session: Session, *, status: str = "active") -> None:
    session.add(Company(id="acme", name="Acme", ats="greenhouse"))
    session.add(
        Job(
            job_id="ok-job",
            company_id="acme",
            source_job_id="1",
            title="Mechanical Engineering Intern",
            classification="strong_match",
            fit_score=90,
            status=status,
            eligibility_status=ELIGIBLE,
        )
    )
    session.add(
        Job(
            job_id="itar-job",
            company_id="acme",
            source_job_id="2",
            title="Defense Mechanical Engineer",
            classification="strong_match",
            fit_score=95,
            status=status,
            eligibility_status=INELIGIBLE_ITAR,
            citizenship_requirement="must be a U.S. person",
        )
    )
    session.flush()


def test_itar_job_excluded_from_list_jobs(session: Session) -> None:
    _seed(session)
    ids = {v.job_id for v in dashboard_service.list_jobs(session)}
    assert "ok-job" in ids
    assert "itar-job" not in ids


def test_itar_job_excluded_from_relevant(session: Session) -> None:
    _seed(session)
    ids = {v.job_id for v in dashboard_service.list_relevant_jobs(session)}
    assert "ok-job" in ids
    assert "itar-job" not in ids


def test_itar_job_excluded_even_with_include_removed(session: Session) -> None:
    # Even in the archive view, ITAR jobs must never surface.
    _seed(session, status="removed")
    views = dashboard_service.list_jobs(session, JobFilters(include_removed=True))
    ids = {v.job_id for v in views}
    assert "ok-job" in ids
    assert "itar-job" not in ids


def test_itar_job_excluded_despite_eligibility_filter(session: Session) -> None:
    # Selecting the status explicitly must not defeat the exclusion.
    _seed(session)
    views = dashboard_service.list_jobs(
        session,
        JobFilters(eligibility_statuses=[INELIGIBLE_ITAR]),
    )
    assert all(v.job_id != "itar-job" for v in views)


def test_itar_not_a_selectable_filter_option(session: Session) -> None:
    _seed(session)
    options = dashboard_service.filter_options(session)
    assert INELIGIBLE_ITAR not in options.eligibility_statuses
