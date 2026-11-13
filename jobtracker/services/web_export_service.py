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
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobtracker.db.models import Company, SourceHealth
from jobtracker.services import dashboard_service
from jobtracker.services.dashboard_service import JobView

logger = logging.getLogger("jobtracker.services.web_export")

#: Schema version of the emitted feed, so the front-end can guard against a
#: stale/incompatible shape after a future change.
FEED_VERSION = 1


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


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


def build_feed(session: Session, *, user_id: int | None = None) -> WebFeed:
    """Build the static feed payload from the eligible "Relevant For Me" set."""
    from jobtracker.services import ai_service

    views = dashboard_service.list_relevant_jobs(session, user_id=user_id)

    jobs: list[dict[str, object]] = []
    for v in views:
        cached = ai_service.get_cached(session, v.job_id, user_id=user_id)
        jobs.append(_job_to_dict(v, cached.result if cached else None))

    # Count only companies we ACTUALLY pull jobs from: active, enabled, and
    # backed by a real connector (not an `html` stub). The old count included
    # all 153 rows, which misleadingly advertised employers we cannot fetch.
    tracked = len(
        list(
            session.scalars(
                select(Company.id).where(
                    Company.active.is_(True),
                    Company.enabled.is_(True),
                    Company.ats.not_in(_STUB_ATS),
                )
            )
        )
    )

    payload: dict[str, object] = {
        "version": FEED_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_last_checked_at": _iso(_latest_source_check(session)),
        "tracked_company_count": tracked,
        "job_count": len(jobs),
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
