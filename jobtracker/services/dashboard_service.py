"""Read-side query service backing the Streamlit dashboard (design 11).

The Streamlit pages are a thin presentation layer: ALL querying, filtering,
and derivation logic lives here (and in the other services), so page code only
calls these functions and renders their results. Nothing in this module imports
Streamlit.

Functions return plain dataclasses / dicts so the view layer never touches the
ORM session directly beyond passing it in.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobtracker.db.models import Company, Job, QuarantineItem, SourceHealth
from jobtracker.extract.work_auth import INELIGIBLE_ITAR

#: Classifications surfaced on the "Relevant For Me" page.
RELEVANT_CLASSIFICATIONS: tuple[str, ...] = ("strong_match", "possible_match")

#: Eligibility statuses that are filtered out of EVERY dashboard view entirely
#: (not merely flagged). A Canadian citizen cannot work ITAR / US-citizen-only
#: roles, so they are excluded regardless of ``include_removed`` and regardless
#: of the eligibility-status filter widget selection.
HARD_EXCLUDED_ELIGIBILITY: tuple[str, ...] = (INELIGIBLE_ITAR,)

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
    #: Stage-1 deterministic hard-filter verdict (FEAT-002). ``eligible`` is
    #: None when un-screened, True when it survived to the AI stage; a False
    #: verdict is filtered out of every browsing view so it is never surfaced
    #: here. ``eligibility_reasons``/``evidence`` explain the verdict and
    #: ``eligibility_failed_rules`` lists the machine-readable rule ids.
    eligible: bool | None
    eligibility_reasons: list[str]
    eligibility_evidence: list[str]
    eligibility_failed_rules: list[str]
    requires_advanced_degree: bool | None
    requires_senior_experience: bool | None
    min_years_experience: int | None
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
    #: Presentation-ready freshness fields (computed in the service, not the
    #: view): how long ago the job was first seen, and how long ago its source
    #: was last polled. ``source_last_checked_at`` comes from SourceHealth.
    first_seen_ago: str | None
    source_last_checked_at: datetime | None
    source_last_checked_ago: str | None
    description: str
    requirements: list[str]
    preferred_qualifications: list[str]
    skills: list[str]
    #: Per-user personalization (Phase 2). Populated only when a ``user_id`` is
    #: passed to the listing functions; otherwise ``is_saved`` is False and
    #: ``application_status`` is None so single-profile callers are unchanged.
    is_saved: bool = False
    application_status: str | None = None


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
    #: Per-user scoping (Phase 2): keep only jobs the current user has saved.
    #: Ignored unless the listing call is passed a ``user_id``.
    only_saved: bool = False


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
    last_successful_check_at: datetime | None
    last_change_detected_at: datetime | None
    last_checked_ago: str | None
    new_jobs_last_run: int | None
    removed_jobs_last_run: int | None
    current_job_count: int | None
    last_error: str | None
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


def humanize_ago(value: datetime | None, *, now: datetime | None = None) -> str | None:
    """Return a compact relative-time label (e.g. ``"3h ago"``) or ``None``.

    Presentation-ready freshness helper kept in the service so the view layer
    stays thin. Negative deltas (clock skew / future timestamps) render as
    ``"just now"``.
    """
    if value is None:
        return None
    now = now or _now()
    delta = now - _as_aware(value)
    seconds = delta.total_seconds()
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


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


def _to_view(
    job: Job,
    company_name: str,
    *,
    now: datetime,
    new_window: timedelta,
    source_last_checked_at: datetime | None = None,
    is_saved: bool = False,
    application_status: str | None = None,
) -> JobView:
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
        eligible=job.eligible,
        eligibility_reasons=list(job.eligibility_reasons or []),
        eligibility_evidence=list(job.eligibility_evidence or []),
        eligibility_failed_rules=list(job.eligibility_failed_rules or []),
        requires_advanced_degree=job.requires_advanced_degree,
        requires_senior_experience=job.requires_senior_experience,
        min_years_experience=job.min_years_experience,
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
        first_seen_ago=humanize_ago(job.first_seen_at, now=now),
        source_last_checked_at=source_last_checked_at,
        source_last_checked_ago=humanize_ago(source_last_checked_at, now=now),
        description=job.description or "",
        requirements=list(job.requirements or []),
        preferred_qualifications=list(job.preferred_qualifications or []),
        skills=list(job.skills or []),
        is_saved=is_saved,
        application_status=application_status,
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


def matches_filters(view: JobView, filters: JobFilters, *, now: datetime | None = None) -> bool:
    """Public, pure predicate: does ``view`` satisfy ``filters``?

    This is the single source of truth for "does a job match this filter set",
    reused by the notification service so subscription matching never diverges
    from the dashboard's read-side filtering. ``only_saved`` is a per-user view
    toggle applied by the listing functions, not a job-intrinsic predicate, so
    it is intentionally ignored here.
    """
    return _matches(view, filters, now=now or _now())


def filters_from_dict(data: dict | None) -> JobFilters:
    """Build a :class:`JobFilters` from a stored subscription ``filter_json``.

    Unknown keys are ignored so older/newer stored filters never crash the
    notifier. Missing keys fall back to the dataclass defaults.
    """
    data = dict(data or {})
    allowed = {f.name for f in fields(JobFilters)}
    kwargs = {k: v for k, v in data.items() if k in allowed}
    return JobFilters(**kwargs)


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
    user_id: int | None = None,
) -> list[JobView]:
    """Return job views matching ``filters``, newest-and-best first.

    By default only ``active`` jobs are returned; set ``include_removed`` to
    also include removed/expired postings (the All Internships archive toggle).
    Results are ordered by fit score (desc) then first-seen (desc).

    When ``user_id`` is given, each view is enriched with the user's
    ``is_saved`` flag and ``application_status``. When ``None`` (the default)
    the behaviour is identical to the Phase-1 single-profile dashboard.
    """
    filters = filters or JobFilters()
    now = _now()
    stmt = select(Job, Company.name).join(Company, Company.id == Job.company_id)
    if not filters.include_removed:
        stmt = stmt.where(Job.status == "active")
    # Hard ITAR / US-citizen-only exclusion: applied for every view, including
    # the archive (include_removed) and independent of the eligibility_statuses
    # filter widget, so these jobs can never be surfaced.
    stmt = stmt.where(
        Job.eligibility_status.not_in(HARD_EXCLUDED_ELIGIBILITY)
        | Job.eligibility_status.is_(None)
    )
    # Stage-1 deterministic hard filter: a job whose persisted verdict is False
    # (full_time/new_grad, degree/experience requirement, US-citizen-only /
    # clearance, incompatible stated term/duration, removed) is dropped
    # everywhere the user browses, independent of include_removed and the
    # eligibility widget. eligible IS NULL (un-screened) and True stay visible.
    stmt = stmt.where(Job.eligible.isnot(False))

    last_checked = {
        company_id: checked_at
        for company_id, checked_at in session.execute(
            select(SourceHealth.company_id, SourceHealth.last_checked_at)
        )
    }

    saved_ids: set[str] = set()
    status_by_job: dict[str, str] = {}
    if user_id is not None:
        # Imported lazily to avoid a service import cycle at module load.
        from jobtracker.services import saved_job_service

        saved_ids = saved_job_service.list_saved_job_ids(session, user_id)
        status_by_job = saved_job_service.status_map(session, user_id)

    views = [
        _to_view(
            job,
            name,
            now=now,
            new_window=new_window,
            source_last_checked_at=last_checked.get(job.company_id),
            is_saved=job.job_id in saved_ids,
            application_status=status_by_job.get(job.job_id),
        )
        for job, name in session.execute(stmt)
    ]
    views = [v for v in views if _matches(v, filters, now=now)]
    if filters.only_saved:
        views = [v for v in views if v.is_saved]
    views.sort(key=_rank_key, reverse=True)
    return views


def views_for_job_ids(
    session: Session,
    job_ids: list[str],
    *,
    new_window: timedelta = DEFAULT_NEW_WINDOW,
) -> dict[str, JobView]:
    """Return ``{job_id: JobView}`` for the given ids (any status).

    Used by the notification service to evaluate specific newly-detected /
    changed / removed jobs against subscriptions. The ITAR hard exclusion is
    still applied so ineligible roles are never surfaced in a notification.
    """
    if not job_ids:
        return {}
    now = _now()
    stmt = (
        select(Job, Company.name)
        .join(Company, Company.id == Job.company_id)
        .where(Job.job_id.in_(job_ids))
        .where(
            Job.eligibility_status.not_in(HARD_EXCLUDED_ELIGIBILITY)
            | Job.eligibility_status.is_(None)
        )
        .where(Job.eligible.isnot(False))
    )
    last_checked = {
        company_id: checked_at
        for company_id, checked_at in session.execute(
            select(SourceHealth.company_id, SourceHealth.last_checked_at)
        )
    }
    return {
        job.job_id: _to_view(
            job,
            name,
            now=now,
            new_window=new_window,
            source_last_checked_at=last_checked.get(job.company_id),
        )
        for job, name in session.execute(stmt)
    }


def list_relevant_jobs(
    session: Session,
    filters: JobFilters | None = None,
    *,
    new_window: timedelta = DEFAULT_NEW_WINDOW,
    user_id: int | None = None,
) -> list[JobView]:
    """Active strong + possible matches for the 'Relevant For Me' page.

    Delegates to :func:`list_jobs`, which enforces the persisted Stage-1
    ``eligible`` verdict (jobs with ``eligible IS False`` are dropped at SQL
    level). FEAT-003 then layers the Stage-2 AI gate on top of these Stage-1
    survivors: ``relevant`` = Stage-1 survivors INTERSECT AI-eligible.

    The gate reads ONLY the CACHED AI analysis (via
    :func:`ai_service.get_cached` -- no network, no new analysis at read time):

    * a survivor with a cached verdict that is AI-not-eligible is hidden
      entirely (user choice i);
    * a survivor with a cached AI-eligible verdict is kept;
    * a survivor with NO cached analysis (AI not yet run, no key, or a prior
      provider error) is KEPT, so a no-key deployment yields exactly the
      Stage-1 survivors and the page is NEVER empty.
    """
    filters = filters or JobFilters()
    if filters.classifications:
        intersection = [
            c for c in filters.classifications if c in RELEVANT_CLASSIFICATIONS
        ]
        # A caller-supplied classification filter is INTERSECTED with the
        # relevant set so this page can never surface a non-relevant
        # classification. If that intersection is empty (the caller passed only
        # non-relevant values), fall back to the full relevant set -- never to
        # "no classification constraint", which would surface everything.
        filters.classifications = intersection or list(RELEVANT_CLASSIFICATIONS)
    else:
        filters.classifications = list(RELEVANT_CLASSIFICATIONS)
    filters.include_removed = False
    survivors = list_jobs(session, filters, new_window=new_window, user_id=user_id)

    # Imported lazily to avoid a service import cycle at module load.
    from jobtracker.screen.ai_verdict import ai_eligibility
    from jobtracker.services import ai_service

    def verdict_lookup(job_id: str) -> bool | None:
        cached = ai_service.get_cached(
            session, job_id, user_id=user_id
        )
        if cached is None or not cached.available or not cached.result:
            return None
        return ai_eligibility(cached.result)

    kept_ids = set(
        apply_ai_eligibility_gate([v.job_id for v in survivors], verdict_lookup)
    )
    return [v for v in survivors if v.job_id in kept_ids]


def list_saved_job_views(
    session: Session,
    user_id: int,
    *,
    new_window: timedelta = DEFAULT_NEW_WINDOW,
) -> list[JobView]:
    """Return the user's saved jobs as views, including removed postings.

    Used by the Saved / Application Pipeline page so hearted jobs remain
    visible even after a posting is removed from its board.
    """
    filters = JobFilters(include_removed=True, only_saved=True)
    return list_jobs(session, filters, new_window=new_window, user_id=user_id)


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
    # Mirror the dashboard hard filters: a job the user can never browse to
    # (ITAR / US-citizen-only, or eligible=False from Stage-1) must not seed a
    # selectable filter value either. eligible IS NULL / True remain.
    stmt = select(Job).where(Job.eligible.isnot(False))
    for job in session.scalars(stmt):
        if job.eligibility_status in HARD_EXCLUDED_ELIGIBILITY:
            continue
        if job.classification:
            seen["classifications"].add(job.classification)
        seen["term_seasons"].add(job.term_season or "unknown")
        if job.country:
            seen["countries"].add(job.country)
        seen["remote_statuses"].add(job.remote_status or "unknown")
        seen["employment_types"].add(job.employment_type or "unknown")
        if job.eligibility_status and job.eligibility_status not in HARD_EXCLUDED_ELIGIBILITY:
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


def stage1_survivor_ids(
    session: Session,
    *,
    company_id: str | None = None,
    job_ids: list[str] | None = None,
) -> list[str]:
    """Return the job ids of active Stage-1 survivors (shared with Stage 2).

    A Stage-1 survivor is an ACTIVE job that was NOT hard-dropped by the
    deterministic filter: ``eligible IS True`` or ``eligible IS NULL`` (un-screened),
    and whose eligibility status is not the ITAR / US-citizen-only hard
    exclusion. This is the SAME survivor definition the dashboard enforces at
    SQL level in :func:`list_jobs`, exposed here so the AI stage (Stage 2) only
    ever runs on the few hundred survivors -- never the full job corpus.

    Optionally scope to a single ``company_id`` (used by the scheduler to AI
    analyze only the just-polled company) and/or an explicit ``job_ids`` subset.
    """
    stmt = (
        select(Job.job_id)
        .where(Job.status == "active")
        .where(Job.eligible.isnot(False))
        .where(
            Job.eligibility_status.not_in(HARD_EXCLUDED_ELIGIBILITY)
            | Job.eligibility_status.is_(None)
        )
    )
    if company_id is not None:
        stmt = stmt.where(Job.company_id == company_id)
    if job_ids is not None:
        stmt = stmt.where(Job.job_id.in_(job_ids))
    return list(session.scalars(stmt))


def apply_ai_eligibility_gate(
    job_ids: list[str],
    verdict_lookup,
) -> list[str]:
    """Pure Stage-2 post-filter over Stage-1 survivor ids with a never-empty fallback.

    ``verdict_lookup(job_id)`` must return ``True`` / ``False`` for a cached AI
    verdict, or ``None`` when there is NO cached analysis for that job. The
    behaviour (user choice i):

    * no cached analysis (``None``) -> KEEP the job (Stage-1 inclusion fallback,
      covers the no-key deployment and any prior provider error);
    * cached verdict True (AI-eligible) -> KEEP;
    * cached verdict False (AI not-eligible) -> DROP entirely (hidden, not
      labeled).

    Taking an injected lookup keeps this unit-testable without a real provider.
    """
    kept: list[str] = []
    for job_id in job_ids:
        verdict = verdict_lookup(job_id)
        if verdict is False:
            continue
        kept.append(job_id)
    return kept


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
        now = _now()
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
                last_successful_check_at=(
                    health.last_successful_check_at if health else None
                ),
                last_change_detected_at=(
                    health.last_change_detected_at if health else None
                ),
                last_checked_ago=humanize_ago(
                    health.last_checked_at if health else None, now=now
                ),
                new_jobs_last_run=health.new_jobs_last_run if health else None,
                removed_jobs_last_run=health.removed_jobs_last_run if health else None,
                current_job_count=health.current_job_count if health else None,
                last_error=health.last_error if health else None,
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
