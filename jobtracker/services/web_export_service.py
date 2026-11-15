"""Static JSON feed for the always-on React site (GitHub Pages).

The hosted site is a client-rendered React SPA served as static files, so it
cannot run any server code. This module produces the one data file it reads:
``jobs.json``, containing the user's final eligible "Relevant For Me" set.

The eligible set is NOT recomputed here. We call the existing
:func:`dashboard_service.list_relevant_jobs`, which is the single source of
truth: Stage-1 deterministic hard filter (full-time/new-grad, US-citizen-only /
ITAR / clearance, advanced/completed degree, senior / N+years, explicitly
incompatible term or duration all removed) INTERSECT the Stage-2 AI-eligible
gate, with the documented no-key fallback to the Stage-1 survivors so the feed
is never empty just because no Gemini key is configured. The React app only
renders and client-filters this set; it never re-implements the screening.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jobtracker.db.models import Company, Job, SourceHealth
from jobtracker.normalize.posting_date import RELATIVE_LOWER_BOUND_DAYS
from jobtracker.services import dashboard_service
from jobtracker.services.dashboard_service import JobView

logger = logging.getLogger("jobtracker.services.web_export")

#: Schema version of the emitted feed, so the front-end can guard against a
#: stale/incompatible shape after a future change.
FEED_VERSION = 1

#: Freshness cutoff: a job older than this is hidden from the eligible feed.
#: "Older" is measured from the best available date (true posting date first,
#: then how long it has sat on our page). This is a hard hide (not a toggle);
#: a job whose age cannot be determined at all is KEPT rather than hidden.
MAX_AGE = timedelta(days=14)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def best_posted_date(view: JobView) -> datetime | None:
    """Best available date for freshness, in priority order.

    (1) the TRUE posting date from the source (``posted_at``), else
    (2) how long the posting has sat on our page (``first_seen_at``). Returns
    ``None`` when neither is known (age is then indeterminate).
    """
    for value in (view.posted_at, view.first_seen_at):
        if value is not None:
            return _as_aware(value)
    return None


def posted_days_ago(view: JobView, *, now: datetime | None = None) -> int | None:
    """Whole days since the best-available posting date (>= 0), or ``None``.

    Used both for the freshness cutoff and the "Posted X days ago" label on
    every card. Prefers the true posting date over the fetch time, matching the
    user's request to measure time since the ACTUAL posting.
    """
    date = best_posted_date(view)
    if date is None:
        return None
    now = now or datetime.now(UTC)
    days = (now - date).days
    return days if days > 0 else 0


def posted_days_ago_is_lower_bound(view: JobView, *, now: datetime | None = None) -> bool:
    """True when the posting age is only known as a lower bound ("30+ days").

    Workday's "Posted 30+ Days Ago" string resolves to a true posting date of
    now - :data:`RELATIVE_LOWER_BOUND_DAYS` (so the 14-day freshness cutoff
    still hides it), but the exact age is unknown. We recognize that case here,
    from the posting-date-derived day count, so the website can render
    "Posted 30+ days ago" rather than a precise but wrong "Posted 30 days ago".
    See :mod:`jobtracker.normalize.posting_date` for why the lower-bound flag
    is derived at the presentation layer instead of threaded through the DB.

    Only applies when the true posting date drives the count; a job whose age
    comes from ``first_seen_at`` (no source posting date) is an exact value.
    """
    if view.posted_at is None:
        return False
    days = posted_days_ago(view, now=now)
    return days is not None and days >= RELATIVE_LOWER_BOUND_DAYS


def is_fresh(view: JobView, *, now: datetime | None = None) -> bool:
    """True when a job is within the freshness window (or its age is unknown).

    A job older than :data:`MAX_AGE` by its best-available date is stale and
    hidden. A job with no determinable date is KEPT (age indeterminate).
    """
    date = best_posted_date(view)
    if date is None:
        return True
    now = now or datetime.now(UTC)
    return now - date <= MAX_AGE


#: SPEC fields of a cached AI analysis that we surface as "eligibility
#: reasoning" on an AI-approved card. Kept to the human-readable ones.
_AI_REASONING_FIELDS = (
    "recommendation",
    "relevance_score",
    "confidence",
    "explanation",
    "mechanical_relevance",
    "term_assessment",
    "duration_assessment",
    "location_assessment",
    "work_authorization_assessment",
    "matched_skills",
    "missing_skills",
    "concerns",
)


def _ai_reasoning(result: dict | None) -> dict | None:
    """Extract the human-readable AI reasoning from a cached analysis result.

    Returns ``None`` when the job has no cached AI analysis (e.g. not yet
    screened by AI, or running in the no-key Stage-1 fallback), so the front-end
    only shows the "AI eligibility reasoning" button on genuinely AI-assessed
    cards.
    """
    if not result:
        return None
    reasoning = {k: result[k] for k in _AI_REASONING_FIELDS if k in result}
    return reasoning or None


def _job_to_dict(view: JobView, ai_result: dict | None) -> dict[str, object]:
    """Serialize one eligible job into the flat shape the React app consumes."""
    return {
        "job_id": view.job_id,
        "title": view.title,
        "company": view.company_name,
        "location": view.location,
        "country": view.country,
        "remote_status": view.remote_status,
        "employment_type": view.employment_type,
        "term_season": view.term_season,
        "duration_months": view.duration_months,
        "duration_label": view.duration_label,
        "fit_score": view.fit_score,
        "eligibility_reasons": list(view.eligibility_reasons or []),
        "visa_sponsorship": view.visa_sponsorship,
        "citizenship_requirement": view.citizenship_requirement,
        "concerns": list(view.concerns or []),
        "skills": list(view.skills or []),
        "application_url": view.application_url,
        "source_url": view.source_url,
        "posted_at": _iso(view.posted_at),
        "posted_days_ago": posted_days_ago(view),
        "posted_days_ago_is_lower_bound": posted_days_ago_is_lower_bound(view),
        "first_seen_at": _iso(view.first_seen_at),
        "first_seen_ago": view.first_seen_ago,
        "source_last_checked_at": _iso(view.source_last_checked_at),
        "source_last_checked_ago": view.source_last_checked_ago,
        "is_new": view.is_new,
        # None unless the job was actually assessed by the AI; drives the
        # "AI eligibility reasoning" button on the card.
        "ai_reasoning": _ai_reasoning(ai_result),
    }


def _latest_source_check(session: Session) -> datetime | None:
    """Most recent successful source poll across all companies, if any."""
    stmt = select(SourceHealth.last_checked_at).where(
        SourceHealth.last_checked_at.is_not(None)
    )
    checks = [c for c in session.scalars(stmt) if c is not None]
    return max(checks) if checks else None


@dataclass
class WebFeed:
    """The assembled feed plus a couple of summary counts for logging/tests."""

    payload: dict[str, object]
    job_count: int


#: ATS types that are honest "stub" placeholders with no working connector --
#: these employers are NOT actually fetched, so they must not be counted or
#: advertised as "tracked".
_STUB_ATS = {"html"}


def _tracked_companies(session: Session) -> list[dict[str, object]]:
    """The actively-tracked employers we actually pull jobs from.

    Uses the EXACT same predicate as the ``tracked_company_count`` summary
    (active + enabled + a real connector, i.e. not an ``html`` stub) so the
    list and the count never disagree. Each item carries the company name, its
    ATS, and the number of ACTIVE jobs currently held for that company. Sorted
    by name (case-insensitive) for stable, human-friendly output.
    """
    rows = session.execute(
        select(Company.id, Company.name, Company.ats).where(
            Company.active.is_(True),
            Company.enabled.is_(True),
            Company.ats.not_in(_STUB_ATS),
        )
    ).all()

    companies: list[dict[str, object]] = []
    for company_id, name, ats in rows:
        job_count = session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.company_id == company_id, Job.status == "active")
        )
        companies.append(
            {"name": name, "ats": ats, "job_count": int(job_count or 0)}
        )

    companies.sort(key=lambda c: str(c["name"]).casefold())
    return companies


def build_feed(session: Session, *, user_id: int | None = None) -> WebFeed:
    """Build the static feed payload from the eligible "Relevant For Me" set."""
    from jobtracker.services import ai_service

    views = dashboard_service.list_relevant_jobs(session, user_id=user_id)

    # Freshness cutoff: hide (do not delete) any job older than MAX_AGE by its
    # best-available date. A job whose age cannot be determined is kept.
    now = datetime.now(UTC)
    fresh_views = [v for v in views if is_fresh(v, now=now)]

    jobs: list[dict[str, object]] = []
    for v in fresh_views:
        cached = ai_service.get_cached(session, v.job_id, user_id=user_id)
        jobs.append(_job_to_dict(v, cached.result if cached else None))

    # List only companies we ACTUALLY pull jobs from: active, enabled, and
    # backed by a real connector (not an `html` stub). The old count included
    # all 153 rows, which misleadingly advertised employers we cannot fetch.
    # The LIST and the count share the exact same predicate so they can never
    # disagree (tracked_company_count == len(tracked_companies)).
    tracked_companies = _tracked_companies(session)

    payload: dict[str, object] = {
        "version": FEED_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_last_checked_at": _iso(_latest_source_check(session)),
        "tracked_company_count": len(tracked_companies),
        "tracked_companies": tracked_companies,
        "job_count": len(jobs),
        # Ambiguous jobs held back until the AI confirms them (never shown
        # unconfirmed when an AI key is configured).
        "pending_ai_count": dashboard_service.pending_ai_count(session),
        "jobs": jobs,
    }
    return WebFeed(payload=payload, job_count=len(jobs))


def export_web(
    session: Session,
    *,
    output: str = "web/public/data/jobs.json",
    user_id: int | None = None,
) -> Path:
    """Write the eligible-set feed to ``output`` as pretty JSON.

    Always writes a valid file -- when there are no eligible jobs (empty DB or
    nothing matched) the feed still has ``"jobs": []`` so the deploy pipeline
    and the front-end both have a well-formed file to work with.
    """
    feed = build_feed(session, user_id=user_id)
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(feed.payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("Wrote web feed with %d eligible jobs to %s", feed.job_count, out_path)
    return out_path
