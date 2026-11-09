"""Source-health view + accuracy report (design 10).

Exposes the per-company source-health rows and an accuracy report that flags
data-quality issues the dashboard surfaces: jobs with a missing application
URL, unknown term, unknown sponsorship, low-confidence AI classifications,
sources not checked recently (stale), and detection mismatches
(removed-but-present / active-but-absent).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobtracker.db.models import AiAnalysisCache, Company, Job, SourceHealth

#: A source whose last check is older than this is considered stale.
DEFAULT_STALE_AFTER = timedelta(days=1)

#: AI analyses whose confidence is below this threshold are flagged for review.
DEFAULT_LOW_AI_CONFIDENCE = 0.5


@dataclass
class AccuracyReport:
    """Collections of job/company ids flagged by each accuracy check."""

    missing_application_url: list[str] = field(default_factory=list)
    unknown_term: list[str] = field(default_factory=list)
    unknown_sponsorship: list[str] = field(default_factory=list)
    low_confidence_ai: list[str] = field(default_factory=list)
    stale_sources: list[str] = field(default_factory=list)
    removed_but_present: list[str] = field(default_factory=list)
    active_but_absent: list[str] = field(default_factory=list)

    def total_issues(self) -> int:
        return (
            len(self.missing_application_url)
            + len(self.unknown_term)
            + len(self.unknown_sponsorship)
            + len(self.low_confidence_ai)
            + len(self.stale_sources)
            + len(self.removed_but_present)
            + len(self.active_but_absent)
        )


def _now() -> datetime:
    return datetime.now(UTC)


def source_health(session: Session) -> list[SourceHealth]:
    """Return every per-company source-health row."""
    return list(session.scalars(select(SourceHealth)))


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def accuracy_report(
    session: Session,
    *,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
    low_ai_confidence: float = DEFAULT_LOW_AI_CONFIDENCE,
) -> AccuracyReport:
    """Build the accuracy report across all jobs and sources.

    * ``missing_application_url`` -- active jobs with no application URL.
    * ``unknown_term`` -- active jobs whose term season is unknown/missing.
    * ``unknown_sponsorship`` -- active jobs whose sponsorship is unknown/missing.
    * ``low_confidence_ai`` -- active jobs whose AI relevance analysis (Phase 3)
      has a confidence below ``low_ai_confidence`` and is not a manual override,
      so a human should double-check the recommendation.
    * ``stale_sources`` -- sources not checked within ``stale_after``.
    * ``removed_but_present`` -- jobs marked removed that still have no
      ``removed_at`` timestamp (inconsistent state).
    * ``active_but_absent`` -- companies that are active + enabled but have no
      active jobs and no successful source check (likely a silent failure).
    """
    report = AccuracyReport()
    now = _now()
    cutoff = now - stale_after

    for job in session.scalars(select(Job)):
        is_active = job.status == "active"
        if is_active and not job.application_url:
            report.missing_application_url.append(job.job_id)
        if is_active and (job.term_season in (None, "", "unknown")):
            report.unknown_term.append(job.job_id)
        if is_active and (job.visa_sponsorship in (None, "", "unknown")):
            report.unknown_sponsorship.append(job.job_id)
        if job.status == "removed" and job.removed_at is None:
            report.removed_but_present.append(job.job_id)

    # Low-confidence AI classifications: a cached analysis exists but the model
    # was not confident, so flag the job for a human review (overrides excluded).
    flagged_low_ai: set[str] = set()
    for cache in session.scalars(select(AiAnalysisCache)):
        if cache.job_id is None or cache.overridden:
            continue
        if cache.confidence is not None and cache.confidence < low_ai_confidence:
            flagged_low_ai.add(cache.job_id)
    report.low_confidence_ai.extend(sorted(flagged_low_ai))

    for health in session.scalars(select(SourceHealth)):
        last = health.last_checked_at
        if last is None or _as_aware(last) < cutoff:
            report.stale_sources.append(health.company_id)

    for company in session.scalars(
        select(Company).where(Company.active.is_(True), Company.enabled.is_(True))
    ):
        active_jobs = session.scalar(
            select(Job.job_id)
            .where(Job.company_id == company.id, Job.status == "active")
            .limit(1)
        )
        health = session.get(SourceHealth, company.id)
        checked_ok = health is not None and health.last_successful_check_at is not None
        if active_jobs is None and not checked_ok:
            report.active_but_absent.append(company.id)

    return report
