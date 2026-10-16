"""Fail-soft ingestion orchestration (design 10).

For each enabled + active company: pick a connector via the registry, fetch
(fail-soft), normalize, route invalid payloads to quarantine, run the
new/changed/removed detection + persistence, update ``source_health``, and
screen the new/changed jobs. Each company is wrapped in ``try/except`` so one
source failing never aborts the whole run; every failure is recorded against
``source_health``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from jobtracker.connectors.base import RawJob
from jobtracker.connectors.fallback import REQUIRES_FALLBACK, FallbackConnector
from jobtracker.connectors.http import HttpClient
from jobtracker.connectors.registry import get_connector
from jobtracker.db.models import Company, QuarantineItem, SourceHealth
from jobtracker.db.repositories import CompanyRepository
from jobtracker.detect.diff import detect_changes
from jobtracker.normalize.normalizer import QuarantineError, normalize
from jobtracker.schemas.company import CompanyConfig
from jobtracker.schemas.job import CanonicalJob
from jobtracker.services import screening_service

logger = logging.getLogger("jobtracker.services.ingest")


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class CompanyRunResult:
    """Per-company outcome of an ingestion pass."""

    company_id: str
    status: str  # "ok" | "error" | "requires_fallback"
    fetched: int = 0
    new: int = 0
    changed: int = 0
    removed: int = 0
    quarantined: int = 0
    screened: int = 0
    error: str | None = None


@dataclass
class RunSummary:
    """Aggregate outcome of an ingestion run across companies."""

    companies: list[CompanyRunResult] = field(default_factory=list)

    @property
    def total_new(self) -> int:
        return sum(c.new for c in self.companies)

    @property
    def total_changed(self) -> int:
        return sum(c.changed for c in self.companies)

    @property
    def errors(self) -> int:
        return sum(1 for c in self.companies if c.status == "error")


def _config_from_company(company: Company) -> CompanyConfig:
    return CompanyConfig(
        id=company.id,
        name=company.name,
        ats=company.ats,
        board_token=company.board_token,
        careers_url=company.careers_url,
        enabled=company.enabled,
        priority=company.priority or "medium",
        check_interval_minutes=company.check_interval_minutes,
        locations=list(company.locations or []),
        notes=company.notes,
    )


def quarantine_raw(
    session: Session, company_id: str, raw: RawJob, reason: str
) -> None:
    """Store a payload that failed the validation gate instead of dropping it."""
    session.add(
        QuarantineItem(
            company_id=company_id,
            source_job_id=raw.source_job_id or None,
            reason=reason,
            raw_payload=raw.extra or None,
            created_at=_now(),
        )
    )


def _record_error(session: Session, company_id: str, message: str) -> None:
    health = session.get(SourceHealth, company_id)
    if health is None:
        health = SourceHealth(company_id=company_id)
        session.add(health)
    now = _now()
    health.last_checked_at = now
    health.error_count = (health.error_count or 0) + 1
    health.last_error = message
    health.last_status = "error"


def _record_fallback(session: Session, company_id: str) -> None:
    health = session.get(SourceHealth, company_id)
    if health is None:
        health = SourceHealth(company_id=company_id)
        session.add(health)
    now = _now()
    health.last_checked_at = now
    health.last_successful_check_at = now
    health.current_job_count = 0
    health.new_jobs_last_run = 0
    health.removed_jobs_last_run = 0
    health.last_status = REQUIRES_FALLBACK


def _ingest_company(
    session: Session, company: Company, http: HttpClient
) -> CompanyRunResult:
    config = _config_from_company(company)
    connector = get_connector(company.ats, http)
    raw_jobs = connector.fetch(config)

    # Unsupported ATS: the fallback stub returns [] and flags requires_fallback.
    if isinstance(connector, FallbackConnector):
        _record_fallback(session, company.id)
        logger.info("Company %s requires a fallback connector.", company.id)
        return CompanyRunResult(company_id=company.id, status=REQUIRES_FALLBACK)

    canonical: list[CanonicalJob] = []
    quarantined = 0
    for raw in raw_jobs:
        try:
            canonical.append(normalize(raw, config))
        except QuarantineError as exc:
            quarantine_raw(session, company.id, raw, exc.reason)
            quarantined += 1

    detection = detect_changes(session, company.id, canonical)
    session.flush()

    screen_ids = detection.new + detection.changed
    summary = screening_service.screen_jobs(session, job_ids=screen_ids or None)

    return CompanyRunResult(
        company_id=company.id,
        status="ok",
        fetched=len(raw_jobs),
        new=len(detection.new),
        changed=len(detection.changed),
        removed=len(detection.removed),
        quarantined=quarantined,
        screened=summary.screened if screen_ids else 0,
    )


def run(
    session: Session,
    http: HttpClient,
    *,
    company_ids: list[str] | None = None,
) -> RunSummary:
    """Run a fail-soft ingestion pass over enabled + active companies.

    When ``company_ids`` is given the run is limited to those ids (still
    requiring enabled + active). One company failing is isolated: the error is
    recorded to ``source_health`` and the run continues.
    """
    repo = CompanyRepository(session)
    companies = repo.list(enabled_only=True, active_only=True)
    if company_ids is not None:
        wanted = set(company_ids)
        companies = [c for c in companies if c.id in wanted]

    summary = RunSummary()
    for company in companies:
        try:
            result = _ingest_company(session, company, http)
            session.commit()
        except Exception as exc:  # noqa: BLE001 - fail-soft: isolate per company.
            session.rollback()
            logger.exception("Ingestion failed for company %s", company.id)
            _record_error(session, company.id, repr(exc))
            session.commit()
            result = CompanyRunResult(
                company_id=company.id, status="error", error=repr(exc)
            )
        summary.companies.append(result)

    logger.info(
        "Ingestion run complete: %d companies, %d new, %d changed, %d errors",
        len(summary.companies),
        summary.total_new,
        summary.total_changed,
        summary.errors,
    )
    return summary
