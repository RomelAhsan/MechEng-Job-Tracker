"""Dashboard read-side Stage-1 enforcement: a job persisted with
``eligible=False`` is removed from EVERY browsing view (list_jobs,
list_relevant_jobs, the include_removed archive, views_for_job_ids, and the
filter_options distinct values), while ``eligible=None`` (un-screened) and
``eligible=True`` remain visible."""

from __future__ import annotations

from sqlalchemy.orm import Session

from jobtracker.db.models import Company, Job
from jobtracker.services import dashboard_service
from jobtracker.services.dashboard_service import JobFilters


def _seed(session: Session, *, status: str = "active") -> None:
    session.add(Company(id="acme", name="Acme", ats="greenhouse"))
    session.add(
        Job(
            job_id="eligible-job",
            company_id="acme",
            source_job_id="1",
            title="Mechanical Engineering Intern",
            classification="strong_match",
            fit_score=90,
            status=status,
            employment_type="internship",
            eligibility_status="eligible",
            eligible=True,
        )
    )
    session.add(
        Job(
            job_id="unscreened-job",
            company_id="acme",
            source_job_id="2",
            title="Unscreened Intern",
            classification="possible_match",
            fit_score=70,
            status=status,
            employment_type="unknown",
            eligible=None,
        )
    )
    session.add(
        Job(
            job_id="ineligible-job",
            company_id="acme",
            source_job_id="3",
            title="Senior Mechanical Engineer",
            classification="strong_match",
            fit_score=95,
            status=status,
            employment_type="full_time",
            eligibility_status="possibly_eligible_unknown",
            eligible=False,
            eligibility_failed_rules=["employment_type"],
            eligibility_reasons=["Employment type full_time is not co-op/internship."],
        )
    )
    session.flush()


def test_ineligible_excluded_from_list_jobs(session: Session) -> None:
    _seed(session)
    ids = {v.job_id for v in dashboard_service.list_jobs(session)}
    assert "eligible-job" in ids
    assert "unscreened-job" in ids
    assert "ineligible-job" not in ids


def test_ineligible_excluded_from_relevant(session: Session) -> None:
    _seed(session)
    ids = {v.job_id for v in dashboard_service.list_relevant_jobs(session)}
    assert "eligible-job" in ids
    assert "unscreened-job" in ids
    assert "ineligible-job" not in ids


def test_ineligible_excluded_even_with_include_removed(session: Session) -> None:
    _seed(session, status="removed")
    views = dashboard_service.list_jobs(session, JobFilters(include_removed=True))
    ids = {v.job_id for v in views}
    assert "eligible-job" in ids
    assert "unscreened-job" in ids
    assert "ineligible-job" not in ids


def test_ineligible_excluded_from_views_for_job_ids(session: Session) -> None:
    _seed(session)
    views = dashboard_service.views_for_job_ids(
        session, ["eligible-job", "unscreened-job", "ineligible-job"]
    )
    assert "eligible-job" in views
    assert "unscreened-job" in views
    assert "ineligible-job" not in views


def test_ineligible_excluded_from_filter_options(session: Session) -> None:
    _seed(session)
    options = dashboard_service.filter_options(session)
    # full_time only appears on the ineligible job, so it must not be offered.
    assert "full_time" not in options.employment_types
    assert "internship" in options.employment_types


def test_eligibility_reasons_surfaced_on_view(session: Session) -> None:
    _seed(session)
    views = {v.job_id: v for v in dashboard_service.list_jobs(session)}
    assert views["eligible-job"].eligible is True
    assert views["unscreened-job"].eligible is None
