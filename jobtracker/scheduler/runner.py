"""Scheduler core: pure interval computation + the foreground runner.

The scheduling *decisions* (which company gets polled, how often) live in pure
functions — :func:`compute_interval` and :func:`build_jobs` — so they can be
unit-tested without real timers, threads, or network. :func:`run_scheduler`
wires those decisions into an APScheduler ``BlockingScheduler`` for the
``python -m jobtracker scheduler`` worker command.

Why not instant? ATS boards do not push to us; we discover a new posting only
by polling. So we poll high-priority boards every ~15 minutes, and cap every
company at ``scheduler_max_interval_minutes`` (default 60) so nothing waits
longer than ~1 hour, then surface accurate ``posted_at`` / ``first_seen_at`` /
``last_checked_at`` timestamps on the dashboard.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from jobtracker.connectors.http import HttpClient
from jobtracker.db.base import create_session_factory
from jobtracker.db.models import Company
from jobtracker.db.repositories import CompanyRepository
from jobtracker.services import ingest_service
from jobtracker.settings import Settings, get_settings

logger = logging.getLogger("jobtracker.scheduler")


def _priority_default(priority: str | None, settings: Settings) -> int:
    """Return the per-priority default interval (minutes) from settings."""
    normalized = (priority or "medium").lower()
    if normalized == "high":
        return settings.scheduler_high_interval_minutes
    if normalized == "low":
        return settings.scheduler_low_interval_minutes
    return settings.scheduler_medium_interval_minutes


def compute_interval(company: Company, settings: Settings) -> int:
    """Return the effective poll interval (minutes) for ``company``.

    The interval is the company's explicit ``check_interval_minutes`` when set,
    otherwise the per-priority default. The result is always capped at
    ``settings.scheduler_max_interval_minutes`` so NO company ever waits longer
    than ~1 hour, and clamped to a minimum of 1 minute.
    """
    explicit = company.check_interval_minutes
    base = explicit if explicit and explicit > 0 else _priority_default(
        company.priority, settings
    )
    capped = min(base, settings.scheduler_max_interval_minutes)
    return max(1, capped)


@dataclass(frozen=True)
class ScheduledJob:
    """A per-company polling job description (no timers attached).

    This is the pure, testable output of :func:`build_jobs`; the runner turns
    each into an APScheduler interval job.
    """

    company_id: str
    interval_minutes: int


def build_jobs(companies: list[Company], settings: Settings) -> list[ScheduledJob]:
    """Compute the per-company polling plan for enabled + active companies.

    Companies that are disabled or inactive are skipped. The returned list is
    ordered by company id for determinism (useful in tests).
    """
    jobs = [
        ScheduledJob(
            company_id=company.id,
            interval_minutes=compute_interval(company, settings),
        )
        for company in companies
        if company.enabled and company.active
    ]
    jobs.sort(key=lambda job: job.company_id)
    return jobs


def poll_company(
    session_factory: sessionmaker[Session],
    company_id: str,
) -> None:
    """Run a single fail-soft ingestion pass for one company.

    Wrapped so that one company failing never stops the scheduler: any
    exception is logged and swallowed (``ingest_service.run`` is already
    fail-soft per company and records failures to ``source_health``).
    """
    started = time.monotonic()
    try:
        session = session_factory()
        try:
            with HttpClient() as http:
                summary = ingest_service.run(session, http, company_ids=[company_id])
        finally:
            session.close()
        result = summary.companies[0] if summary.companies else None
        duration = time.monotonic() - started
        if result is not None:
            logger.info(
                "Polled company %s: status=%s fetched=%d new=%d changed=%d "
                "removed=%d (%.2fs)",
                company_id,
                result.status,
                result.fetched,
                result.new,
                result.changed,
                result.removed,
                duration,
            )
        else:
            logger.info(
                "Polled company %s: no result (not enabled/active) (%.2fs)",
                company_id,
                duration,
            )
    except Exception:  # noqa: BLE001 - fail-soft: never crash the scheduler.
        logger.exception("Scheduled poll failed for company %s", company_id)


def run_scheduler(settings: Settings | None = None) -> None:
    """Run the foreground polling scheduler until interrupted.

    Performs an immediate first poll of every enabled + active company on
    startup so the dashboard is populated promptly, then schedules each company
    on its effective interval. Uses a ``BlockingScheduler`` so it can run as a
    dedicated worker process (e.g. a Fly.io ``worker`` process).
    """
    from apscheduler.schedulers.blocking import BlockingScheduler

    settings = settings or get_settings()
    session_factory = create_session_factory()

    read_session = session_factory()
    try:
        companies = CompanyRepository(read_session).list(
            enabled_only=True, active_only=True
        )
        plan = build_jobs(companies, settings)
    finally:
        read_session.close()

    if not plan:
        logger.warning("Scheduler has no enabled + active companies to poll.")
        return

    logger.info(
        "Starting scheduler for %d companies (max interval %d min).",
        len(plan),
        settings.scheduler_max_interval_minutes,
    )

    # Immediate first poll of all companies so the page populates promptly.
    for job in plan:
        poll_company(session_factory, job.company_id)

    scheduler = BlockingScheduler(timezone="UTC")
    for job in plan:
        scheduler.add_job(
            poll_company,
            trigger="interval",
            minutes=job.interval_minutes,
            args=[session_factory, job.company_id],
            id=f"poll:{job.company_id}",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        logger.info(
            "Scheduled company %s every %d min.", job.company_id, job.interval_minutes
        )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):  # pragma: no cover - interactive stop
        logger.info("Scheduler stopped.")
        scheduler.shutdown(wait=False)
