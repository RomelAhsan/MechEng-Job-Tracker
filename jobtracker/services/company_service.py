"""Company management service (design 10).

Owns the lifecycle of tracked employers: seeding from ``companies.yaml``
(``sync_from_yaml``), adding a company by careers URL (``add_from_url``),
enable / disable / remove, and listing. YAML is the declarative seed; the
database is authoritative at runtime (upsert by ``id``).

All business logic lives here so the CLI and dashboard are thin callers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from jobtracker.connectors.fallback import FallbackConnector
from jobtracker.connectors.http import HttpClient
from jobtracker.connectors.registry import detect_ats, get_connector
from jobtracker.db.models import Company
from jobtracker.db.repositories import CompanyRepository
from jobtracker.detect.diff import detect_changes
from jobtracker.normalize.normalizer import QuarantineError, normalize
from jobtracker.schemas.company import CompanyConfig
from jobtracker.services.ingest_service import quarantine_raw

logger = logging.getLogger("jobtracker.services.company")


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class SyncResult:
    """Summary of a YAML -> DB sync."""

    created: list[str]
    updated: list[str]


@dataclass
class AddResult:
    """Summary of an ``add_from_url`` call."""

    company_id: str
    ats: str
    board_token: str | None
    baseline_jobs: int


@dataclass
class TestSourceResult:
    """Outcome of a ``test_source`` connectivity probe."""

    company_id: str
    ats: str
    ok: bool
    fetched: int = 0
    error: str | None = None


def _apply_config(company: Company, config: CompanyConfig) -> None:
    """Copy declarative config fields onto a Company row (identity excluded)."""
    company.name = config.name
    company.ats = config.ats
    company.board_token = config.board_token
    company.careers_url = config.careers_url
    company.enabled = config.enabled
    company.priority = config.priority
    company.check_interval_minutes = config.check_interval_minutes
    company.locations = list(config.locations)
    company.notes = config.notes


def sync_from_yaml(session: Session, companies: list[CompanyConfig]) -> SyncResult:
    """Upsert the declared companies into the database, keyed by ``id``.

    Existing rows are updated in place (preserving ``active`` history); new
    rows are inserted. Returns the created and updated ids.
    """
    repo = CompanyRepository(session)
    created: list[str] = []
    updated: list[str] = []
    now = _now()

    for config in companies:
        existing = repo.get(config.id)
        if existing is None:
            company = Company(id=config.id, active=True, created_at=now, updated_at=now)
            _apply_config(company, config)
            repo.add(company)
            created.append(config.id)
        else:
            _apply_config(existing, config)
            existing.updated_at = now
            updated.append(config.id)

    session.flush()
    logger.info("Synced companies: %d created, %d updated", len(created), len(updated))
    return SyncResult(created=created, updated=updated)


def add_from_url(
    session: Session,
    url: str,
    http: HttpClient,
    *,
    company_id: str | None = None,
    name: str | None = None,
    priority: str = "medium",
) -> AddResult:
    """Detect the ATS from a careers URL, test it, save, and baseline-import.

    The baseline import runs the full detection pass with ``baseline=True`` so
    the company's existing postings are marked seen rather than false-new.
    """
    detected = detect_ats(url)
    slug = company_id or _slugify(name or detected.board_token or url)
    display = name or slug

    repo = CompanyRepository(session)
    now = _now()
    company = repo.get(slug)
    if company is None:
        company = Company(id=slug, active=True, created_at=now)
        repo.add(company)
    company.name = display
    company.ats = detected.ats
    company.board_token = detected.board_token
    company.careers_url = url
    company.enabled = True
    company.active = True
    company.priority = priority
    company.updated_at = now
    session.flush()

    config = _config_from_company(company)
    connector = get_connector(company.ats, http)
    raw_jobs = connector.fetch(config)

    canonical_jobs = []
    for raw in raw_jobs:
        try:
            canonical_jobs.append(normalize(raw, config))
        except QuarantineError as exc:
            quarantine_raw(session, company.id, raw, exc.reason)

    detection = detect_changes(session, company.id, canonical_jobs, baseline=True)
    session.flush()

    logger.info(
        "Added company %s (ats=%s, token=%s); baseline imported %d jobs",
        slug,
        company.ats,
        company.board_token,
        detection.current_job_count,
    )
    return AddResult(
        company_id=slug,
        ats=company.ats,
        board_token=company.board_token,
        baseline_jobs=detection.current_job_count,
    )


def enable(session: Session, company_id: str) -> Company:
    """Re-enable a company for ingestion."""
    company = _require(session, company_id)
    company.enabled = True
    company.active = True
    company.updated_at = _now()
    session.flush()
    return company


def disable(session: Session, company_id: str) -> Company:
    """Disable a company (kept in the database, history retained)."""
    company = _require(session, company_id)
    company.enabled = False
    company.updated_at = _now()
    session.flush()
    return company


def remove(session: Session, company_id: str) -> Company:
    """Remove a company: mark inactive + disabled; historical jobs retained."""
    company = _require(session, company_id)
    company.active = False
    company.enabled = False
    company.updated_at = _now()
    session.flush()
    return company


def test_source(
    session: Session, company_id: str, http: HttpClient
) -> TestSourceResult:
    """Probe a company's source without persisting: fetch and report count.

    Fail-soft: connectivity/parsing errors are captured on the result rather
    than raised, and the fallback stub reports ``ok=False`` (needs a connector).
    """
    company = _require(session, company_id)
    config = _config_from_company(company)
    connector = get_connector(company.ats, http)
    try:
        raw_jobs = connector.fetch(config)
    except Exception as exc:  # noqa: BLE001 - probe captures the failure.
        return TestSourceResult(
            company_id=company_id, ats=company.ats, ok=False, error=repr(exc)
        )

    if isinstance(connector, FallbackConnector):
        return TestSourceResult(
            company_id=company_id,
            ats=company.ats,
            ok=False,
            error="requires_fallback: no connector for this ATS yet",
        )
    return TestSourceResult(
        company_id=company_id, ats=company.ats, ok=True, fetched=len(raw_jobs)
    )


def list_companies(
    session: Session, *, enabled_only: bool = False, active_only: bool = False
) -> list[Company]:
    """List tracked companies (optionally filtered)."""
    return CompanyRepository(session).list(
        enabled_only=enabled_only, active_only=active_only
    )


def _require(session: Session, company_id: str) -> Company:
    company = CompanyRepository(session).get(company_id)
    if company is None:
        raise ValueError(f"Unknown company id '{company_id}'.")
    return company


def _config_from_company(company: Company) -> CompanyConfig:
    """Build a CompanyConfig view of a stored row for connector/normalize use."""
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


def _slugify(value: str) -> str:
    """Produce a filesystem/identifier-safe slug from arbitrary text."""
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    parts = [p for p in cleaned.split("-") if p]
    return "-".join(parts) or "company"
