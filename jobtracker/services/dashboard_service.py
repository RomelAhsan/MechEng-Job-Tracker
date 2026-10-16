"""Read-side query service backing the Streamlit dashboard (design 11).

The Streamlit pages are a thin presentation layer: ALL querying, filtering,
and derivation logic lives here (and in the other services), so page code only
calls these functions and renders their results. Nothing in this module imports
Streamlit.

Functions return plain dataclasses / dicts so the view layer never touches the
ORM session directly beyond passing it in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobtracker.db.models import Company, Job, QuarantineItem, SourceHealth

#: Classifications surfaced on the "Relevant For Me" page.
RELEVANT_CLASSIFICATIONS: tuple[str, ...] = ("strong_match", "possible_match")

#: A job first seen within this window is flagged with a "new" badge.
DEFAULT_NEW_WINDOW = timedelta(days=3)


@dataclass
class JobView:
    """Flattened, presentation-ready view of a job row joined to its company."""

    job_id: str
    company_id: str
    company_name: str
    title: str
    location: str
    country: str | None
    remote_status: str
    employment_type: str
    status: str
    classification: str | None
    fit_score: int | None
    term_season: str
    duration_months: int | None
    duration_label: str
    eligibility_status: str | None
    visa_sponsorship: str
    citizenship_requirement: str
    concerns: list[str]
    classification_reasons: list[str]
    application_url: str | None
    source_url: str | None
    source_type: str | None
    posted_at: datetime | None
    first_seen_at: datetime | None
    is_new: bool
    description: str
    requirements: list[str]
    preferred_qualifications: list[str]
    skills: list[str]


@dataclass
class JobFilters:
    """Filter criteria shared by the job-listing pages.

    Every field is optional; ``None``/empty means "no constraint". All filtering
    happens in :func:`list_jobs` so the view layer only collects user input.
    """

    classifications: list[str] | None = None
    term_seasons: list[str] | None = None
    countries: list[str] | None = None
    locations_query: str | None = None
    remote_statuses: list[str] | None = None
    employment_types: list[str] | None = None
    eligibility_statuses: list[str] | None = None
    sponsorship_statuses: list[str] | None = None
    min_fit_score: int | None = None
    max_fit_score: int | None = None
    freshness_days: int | None = None
    include_removed: bool = False
    only_new: bool = False


@dataclass
class FilterOptions:
    """Distinct values present in the DB, for populating filter widgets."""

    classifications: list[str] = field(default_factory=list)
    term_seasons: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    remote_statuses: list[str] = field(default_factory=list)
    employment_types: list[str] = field(default_factory=list)
    eligibility_statuses: list[str] = field(default_factory=list)
    sponsorship_statuses: list[str] = field(default_factory=list)


@dataclass
class CompanyView:
    """Presentation view of a company plus its source-health summary."""

    id: str
    name: str
    ats: str
    board_token: str | None
    careers_url: str | None
    enabled: bool
    active: bool
    priority: str
    check_interval_minutes: int | None
    active_job_count: int
    last_status: str | None
    last_checked_at: datetime | None
    error_count: int


@dataclass
class QuarantineView:
    """Presentation view of a quarantined payload."""

    id: int
    company_id: str
    source_job_id: str | None
    reason: str | None
    created_at: datetime | None
    resolved: bool


def _now() -> datetime:
    return datetime.now(UTC)


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _is_new(first_seen_at: datetime | None, *, window: timedelta, now: datetime) -> bool:
    if first_seen_at is None:
        return False
    return _as_aware(first_seen_at) >= now - window


def _derive_concerns(job: Job) -> list[str]:
    """Surface eligibility/term caveats from persisted fields (not hidden).

    Mirrors the hard-filter concerns the fit-score layer flags, derived from the
    screening fields already persisted on the row so the view layer stays thin.
    """
    concerns: list[str] = []
    if job.status in ("removed", "expired"):
        concerns.append(f"posting {job.status}")
    if job.eligibility_status == "likely_ineligible_sponsorship_required":
        concerns.append("likely ineligible: sponsorship required")
    if (job.term_season or "unknown") == "unknown":
        concerns.append("term season unknown")
    if (job.visa_sponsorship or "unknown") == "unknown":
        concerns.append("sponsorship unknown")
    return concerns


def _to_view(job: Job, company_name: str, *, now: datetime, new_window: timedelta) -> JobView:
    reasons = list(job.classification_reasons or [])
    concerns = _derive_concerns(job)
    return JobView(
        job_id=job.job_id,
        company_id=job.company_id,
        company_name=company_name,
        title=job.title or "",
        location=job.location or "",
        country=job.country,
        remote_status=job.remote_status or "unknown",
        employment_type=job.employment_type or "unknown",
        status=job.status or "active",
        classification=job.classification,
        fit_score=job.fit_score,
        term_season=job.term_season or "unknown",
        duration_months=job.duration_months,
        duration_label=job.duration_label or "",
        eligibility_status=job.eligibility_status,
        visa_sponsorship=job.visa_sponsorship or "unknown",
        citizenship_requirement=job.citizenship_requirement or "",
        concerns=concerns,
        classification_reasons=reasons,
        application_url=job.application_url,
        source_url=job.source_url,
        source_type=job.source_type,
        posted_at=job.posted_at,
        first_seen_at=job.first_seen_at,
        is_new=_is_new(job.first_seen_at, window=new_window, now=now),
        description=job.description or "",
        requirements=list(job.requirements or []),
        preferred_qualifications=list(job.preferred_qualifications or []),
        skills=list(job.skills or []),
    )


def _matches(view: JobView, filters: JobFilters, *, now: datetime) -> bool:
    f = filters
    if f.classifications and view.classification not in f.classifications:
        return False
    if f.term_seasons and view.term_season not in f.term_seasons:
        return False
    if f.countries and (view.country or "") not in f.countries:
        return False
    if f.locations_query and f.locations_query.lower() not in view.location.lower():
        return False
    if f.remote_statuses and view.remote_status not in f.remote_statuses:
        return False
    if f.employment_types and view.employment_type not in f.employment_types:
        return False
    if f.eligibility_statuses and (view.eligibility_status or "") not in f.eligibility_statuses:
        return False
    if f.sponsorship_statuses and view.visa_sponsorship not in f.sponsorship_statuses:
        return False
    if f.min_fit_score is not None and (view.fit_score or 0) < f.min_fit_score:
        return False
    if f.max_fit_score is not None and (view.fit_score or 0) > f.max_fit_score:
        return False
    if f.only_new and not view.is_new:
        return False
    if f.freshness_days is not None:
        cutoff = now - timedelta(days=f.freshness_days)
        seen = view.first_seen_at
        if seen is None or _as_aware(seen) < cutoff:
            return False
    return True


def _rank_key(view: JobView) -> tuple[int, datetime]:
    """Sort by fit score (desc) then first-seen (desc)."""
    score = view.fit_score if view.fit_score is not None else -1
    seen = (
        _as_aware(view.first_seen_at)
        if view.first_seen_at is not None
        else datetime.min.replace(tzinfo=UTC)
    )
    return score, seen


def list_jobs(
    session: Session,
    filters: JobFilters | None = None,
    *,
    new_window: timedelta = DEFAULT_NEW_WINDOW,
) -> list[JobView]:
    """Return job views matching ``filters``, newest-and-best first.

    By default only ``active`` jobs are returned; set ``include_removed`` to
    also include removed/expired postings (the All Internships archive toggle).
    Results are ordered by fit score (desc) then first-seen (desc).
    """
    filters = filters or JobFilters()
    now = _now()
    stmt = select(Job, Company.name).join(Company, Company.id == Job.company_id)
    if not filters.include_removed:
        stmt = stmt.where(Job.status == "active")

    views = [
        _to_view(job, name, now=now, new_window=new_window)
        for job, name in session.execute(stmt)
    ]
    views = [v for v in views if _matches(v, filters, now=now)]
    views.sort(key=_rank_key, reverse=True)
    return views


def list_relevant_jobs(
    session: Session,
    filters: JobFilters | None = None,
    *,
    new_window: timedelta = DEFAULT_NEW_WINDOW,
) -> list[JobView]:
    """Active strong + possible matches for the 'Relevant For Me' page."""
    filters = filters or JobFilters()
    if filters.classifications:
        filters.classifications = [
            c for c in filters.classifications if c in RELEVANT_CLASSIFICATIONS
        ]
    else:
        filters.classifications = list(RELEVANT_CLASSIFICATIONS)
    filters.include_removed = False
    return list_jobs(session, filters, new_window=new_window)


def filter_options(session: Session) -> FilterOptions:
    """Collect the distinct filter values present across all jobs."""
    options = FilterOptions()
    seen: dict[str, set[str]] = {
        "classifications": set(),
        "term_seasons": set(),
        "countries": set(),
        "remote_statuses": set(),
        "employment_types": set(),
        "eligibility_statuses": set(),
        "sponsorship_statuses": set(),
    }
    for job in session.scalars(select(Job)):
        if job.classification:
            seen["classifications"].add(job.classification)
        seen["term_seasons"].add(job.term_season or "unknown")
        if job.country:
            seen["countries"].add(job.country)
        seen["remote_statuses"].add(job.remote_status or "unknown")
        seen["employment_types"].add(job.employment_type or "unknown")
        if job.eligibility_status:
            seen["eligibility_statuses"].add(job.eligibility_status)
        seen["sponsorship_statuses"].add(job.visa_sponsorship or "unknown")

    options.classifications = sorted(seen["classifications"])
    options.term_seasons = sorted(seen["term_seasons"])
    options.countries = sorted(seen["countries"])
    options.remote_statuses = sorted(seen["remote_statuses"])
    options.employment_types = sorted(seen["employment_types"])
    options.eligibility_statuses = sorted(seen["eligibility_statuses"])
    options.sponsorship_statuses = sorted(seen["sponsorship_statuses"])
    return options


def list_company_views(session: Session) -> list[CompanyView]:
    """Return companies with their source-health summary for the Companies page."""
    views: list[CompanyView] = []
    for company in session.scalars(select(Company).order_by(Company.id)):
        active_count = len(
            session.scalars(
                select(Job.job_id).where(
                    Job.company_id == company.id, Job.status == "active"
                )
            ).all()
        )
        health = session.get(SourceHealth, company.id)
        views.append(
            CompanyView(
                id=company.id,
                name=company.name,
                ats=company.ats,
                board_token=company.board_token,
                careers_url=company.careers_url,
                enabled=company.enabled,
                active=company.active,
                priority=company.priority or "medium",
                check_interval_minutes=company.check_interval_minutes,
                active_job_count=active_count,
                last_status=health.last_status if health else None,
                last_checked_at=health.last_checked_at if health else None,
                error_count=(health.error_count or 0) if health else 0,
            )
        )
    return views


def list_quarantine(session: Session, *, include_resolved: bool = False) -> list[QuarantineView]:
    """Return quarantined payloads for the Source Health page."""
    stmt = select(QuarantineItem).order_by(QuarantineItem.id.desc())
    if not include_resolved:
        stmt = stmt.where(QuarantineItem.resolved.is_(False))
    return [
        QuarantineView(
            id=item.id,
            company_id=item.company_id,
            source_job_id=item.source_job_id,
            reason=item.reason,
            created_at=item.created_at,
            resolved=item.resolved,
        )
        for item in session.scalars(stmt)
    ]
