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
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from sqlalchemy.orm import Session

from jobtracker.connectors.base import KnownJob, RawJob
from jobtracker.connectors.fallback import REQUIRES_FALLBACK, FallbackConnector
from jobtracker.connectors.http import BlockedError, HttpClient
from jobtracker.connectors.registry import get_connector
from jobtracker.db.models import Company, Job, QuarantineItem, SourceHealth
from jobtracker.db.repositories import CompanyRepository
from jobtracker.detect.diff import detect_changes
from jobtracker.normalize.normalizer import QuarantineError, normalize
from jobtracker.schemas.company import CompanyConfig
from jobtracker.schemas.job import CanonicalJob
from jobtracker.services import screening_service
from jobtracker.settings import get_settings

logger = logging.getLogger("jobtracker.services.ingest")

_APIFY_ATS = "apify_workday"


def _cap_apify_companies(session: Session, companies: list) -> list:
    """Keep all fast (direct-API) companies; cap + rotate the slow Apify ones.

    Apify fetches run on a third party and block per run, so polling all of them
    every run would stall the poll. We poll at most
    ``apify_max_companies_per_run`` of them, choosing the least-recently-checked
    (oldest / never-checked ``SourceHealth.last_checked_at`` first) so every
    Apify company is still covered over successive polls.
    """
    settings = get_settings()
    cap = max(0, int(settings.apify_max_companies_per_run))
    apify = [c for c in companies if getattr(c, "ats", None) == _APIFY_ATS]
    others = [c for c in companies if getattr(c, "ats", None) != _APIFY_ATS]
    if len(apify) <= cap:
        return companies

    health = {
        cid: last
        for cid, last in session.query(
            SourceHealth.company_id, SourceHealth.last_checked_at
        ).all()
    }

    def _never_first(c):  # oldest / never-checked first
        last = health.get(c.id)
        return (last is not None, last)

    apify.sort(key=_never_first)
    return others + apify[:cap]


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


def _known_jobs(session: Session, company_id: str) -> dict[str, KnownJob]:
    """Active stored postings keyed by the '/job/...' path of their apply link."""
    return _known_maps(session, company_id)[0]


def _known_maps(
    session: Session, company_id: str
) -> tuple[dict[str, KnownJob], dict[str, KnownJob], dict[str, KnownJob]]:
    """Active stored postings with a description, keyed three ways.

    Returns ``(by '/job/...' path, by source_job_id, by apply URL)``. Removed
    postings are included too (by id/URL) so a posting that comes back after
    a missed check is not re-downloaded.
    """
    by_path: dict[str, KnownJob] = {}
    by_id: dict[str, KnownJob] = {}
    by_url: dict[str, KnownJob] = {}
    rows = session.query(Job).filter(
        Job.company_id == company_id, Job.status.in_(("active", "removed"))
    )
    for job in rows:
        if not job.description:
            continue
        known = KnownJob(
            source_job_id=job.source_job_id or "",
            title=job.title or "",
            location=job.location or "",
            description=job.description,
            application_url=job.application_url,
            posted_at=job.posted_at,
        )
        if job.source_job_id:
            by_id.setdefault(job.source_job_id, known)
        url = job.application_url or ""
        if url:
            by_url.setdefault(url, known)
        idx = url.find("/job/")
        if idx >= 0 and job.status == "active":
            by_path[url[idx:]] = known
    return by_path, by_id, by_url


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


def describe_error(exc: BaseException) -> str:
    """A short, plain-English reason a board could not be checked."""
    reason = getattr(exc, "reason", None)
    if isinstance(reason, str) and reason:
        return reason
    if isinstance(exc, EmptyBoardError):
        return str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 404:
            return "the job board address no longer works (HTTP 404)"
        if status in (401, 403):
            return f"the job site refused access (HTTP {status})"
        if status == 429:
            return "the job site is refusing our checker (too many requests)"
        if status >= 500:
            return f"the job site had a server error (HTTP {status})"
        return f"the job site answered HTTP {status}"
    if isinstance(exc, httpx.TimeoutException):
        return "the job site did not answer in time"
    if isinstance(exc, httpx.TransportError):
        return "could not connect to the job site"
    return f"the board could not be read ({type(exc).__name__})"


def plain_reason(text: str | None) -> str | None:
    """Turn a stored error into plain English (older rows hold raw repr text)."""
    if not text:
        return None
    if "Error(" not in text and "error '" not in text.lower():
        return text
    match = re.search(r"\b(\d{3})\b", text)
    status = int(match.group(1)) if match else None
    if status == 429:
        return "the job site is refusing our checker (too many requests from GitHub's servers)"
    if status and status >= 520:
        return "the job site's network is refusing connections from GitHub's servers"
    if status in (401, 403):
        return "the job site's bot protection is blocking our checker"
    if status == 404:
        return "the job board address no longer works (HTTP 404)"
    if status:
        return f"the job site answered HTTP {status}"
    if "Timeout" in text:
        return "the job site did not answer in time"
    return "the board could not be read"


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
    health.consecutive_failures = (health.consecutive_failures or 0) + 1
    if health.failing_since is None:
        health.failing_since = now


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
    session: Session,
    company: Company,
    http: HttpClient,
    *,
    max_postings: int | None = None,
) -> CompanyRunResult:
    config = _config_from_company(company)
    connector = get_connector(company.ats, http)
    (
        connector.known_jobs,
        connector.known_by_id,
        connector.known_by_url,
    ) = _known_maps(session, company.id)
    # Only the one-time add-time poll passes a cap; the scheduled poll leaves it
    # None so the connector fetches the full board.
    if max_postings is not None:
        connector.max_postings = max_postings
    connector.max_new_details = get_settings().max_new_details_per_run
    connector.new_details_fetched = 0
    raw_jobs = connector.fetch(config)

    # Unsupported ATS: the fallback stub returns [] and flags requires_fallback.
    if isinstance(connector, FallbackConnector):
        _record_fallback(session, company.id)
        logger.info("Company %s requires a fallback connector.", company.id)
        return CompanyRunResult(company_id=company.id, status=REQUIRES_FALLBACK)

    return _process_raw_jobs(session, company, config, raw_jobs)


class EmptyBoardError(RuntimeError):
    """A board that had postings suddenly returned none (likely a site change).

    Treated as a failed check: the stored postings are kept (and tagged
    "can't be checked") instead of all being marked removed.
    """


#: A board returning no postings while we still hold at least this many active
#: ones is treated as a failed check, not as "every posting closed".
EMPTY_BOARD_MIN_ACTIVE = 5


def _process_raw_jobs(
    session: Session,
    company: Company,
    config,
    raw_jobs: list,
    *,
    via: str = "direct",
) -> CompanyRunResult:
    """Normalize, diff, and screen one company's fetched postings."""
    if not raw_jobs:
        active = (
            session.query(Job)
            .filter(Job.company_id == company.id, Job.status == "active")
            .count()
        )
        if active >= EMPTY_BOARD_MIN_ACTIVE:
            raise EmptyBoardError(
                f"the board returned no postings although {active} were listed "
                "before; keeping them until it answers normally"
            )
    canonical: list[CanonicalJob] = []
    quarantined = 0
    for raw in raw_jobs:
        try:
            canonical.append(normalize(raw, config))
        except QuarantineError as exc:
            quarantine_raw(session, company.id, raw, exc.reason)
            quarantined += 1

    detection = detect_changes(session, company.id, canonical)
    health = session.get(SourceHealth, company.id)
    if health is not None:
        health.last_fetch_via = via
    session.flush()

    # Screen ONLY this company's new/changed jobs. (Passing None here used to
    # mean "screen the entire database", which re-screened ~8k jobs after every
    # company with no changes and made each poll take ~47 minutes.) The full
    # re-screen happens once per run in the separate `screen` step.
    screen_ids = detection.new + detection.changed
    if screen_ids:
        summary = screening_service.screen_jobs(session, job_ids=screen_ids)

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


def _is_blocked_error(exc: Exception) -> bool:
    """True when a board refused GitHub's servers (bot protection/throttling)."""
    if isinstance(exc, BlockedError):
        return True
    return _is_legacy_blocked(exc)


def _is_legacy_blocked(exc: Exception) -> bool:
    """True for the Cloudflare 521 Workday returns to blocked datacenter IPs."""
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) == 521


class _WorkdayFallback:
    """Check Workday boards that refuse GitHub's servers through Apify.

    Boards that answered with a block are collected while the Workday boards
    are polled. :meth:`start` then launches ONE Apify listing run over all of
    them that are due (student roles in Canada/US only, using the cached
    filters in ``config/workday_filters.json``). :meth:`collect` waits for it
    at the end of the poll, fetches full details only for postings we have
    not stored yet (a second small run), and yields each board's postings so
    they are reconciled like a normal fetch: new postings are added and
    stored postings missing from the listing are marked closed.
    """

    def __init__(self) -> None:
        self._blocked: list[Company] = []
        self._included: list[Company] = []
        self._listing_run: str | None = None
        self._connector = None
        self._plans: dict | None = None

    def _get(self):
        if self._connector is None:
            from jobtracker.connectors.apify_workday import ApifyWorkdayConnector

            self._connector = ApifyWorkdayConnector()
        return self._connector

    def note_blocked(self, company: Company, exc: Exception) -> bool:
        """Remember a Workday board that refused us; True if it qualifies."""
        if getattr(company, "ats", None) != "workday" or not _is_blocked_error(exc):
            return False
        self._blocked.append(company)
        return True

    # Kept for callers/tests that used the old one-run-per-board API.
    def try_start(self, company: Company, exc: Exception) -> bool:
        return self.note_blocked(company, exc)

    def start(self) -> None:
        """Start one listing run over every blocked board that is due."""
        if not self._blocked:
            return
        try:
            connector = self._get()
            if not connector.can_start():
                return
            if self._plans is None:
                from jobtracker.connectors.workday_targeting import load_plans

                self._plans = load_plans()
            due = [c for c in self._blocked if connector.due(c.id)]
            urls: list[str] = []
            for company in due:
                urls.extend(
                    connector.search_urls(
                        _config_from_company(company), self._plans.get(company.id)
                    )
                )
            if not urls:
                return
            run_id = connector.start_listing(urls)
        except Exception:  # noqa: BLE001 - the fallback must never break a poll.
            logger.exception("Could not start the Apify Workday sweep")
            return
        if run_id:
            self._listing_run = run_id
            self._included = due

    def collect(self, session: Session):
        """Yield ``(company, raw_jobs)`` for each board the sweep covered."""
        if not self._listing_run:
            return
        import time

        from jobtracker.connectors.workday import _coordinates

        settings = get_settings()
        connector = self._get()
        deadline = time.monotonic() + int(settings.apify_fallback_wait_secs)
        items = connector.wait_items(
            self._listing_run, wait_secs=max(1, int(deadline - time.monotonic()))
        )
        if items is None:
            return

        by_board: dict[tuple[str, str], dict[str, dict]] = {}
        for item in items:
            key = connector.board_key(item.get("url") or item.get("externalUrl"))
            meta = item.get("__metadata")
            if key is None and isinstance(meta, dict):
                key = connector.board_key(meta.get("sourceUrl"))
            path = connector.item_path(item)
            if key and path:
                by_board.setdefault(key, {}).setdefault(path, item)

        pending: list[tuple[Company, list[RawJob], list[str]]] = []
        detail_urls: list[str] = []
        for company in self._included:
            coords = _coordinates(_config_from_company(company))
            if coords is None:
                continue
            listing = by_board.get((coords.base.lower(), coords.site.lower()), {})
            by_path, _by_id, _by_url = _known_maps(session, company.id)
            raws: list[RawJob] = []
            missing: list[str] = []
            for path, item in listing.items():
                known = by_path.get(path)
                if known is not None:
                    raws.append(
                        RawJob(
                            source_job_id=known.source_job_id,
                            title=str(item.get("title") or known.title),
                            location=known.location,
                            description_html=known.description,
                            application_url=known.application_url,
                            source_url=known.application_url,
                            posted_at=known.posted_at,
                            extra={"_reused": True, "apify": True},
                        )
                    )
                else:
                    missing.append(coords.public_url(path))
            pending.append((company, raws, missing))
            detail_urls.extend(missing)

        details: dict[str, RawJob] = {}
        limit = max(1, int(settings.apify_max_new_details_per_sweep))
        if len(detail_urls) > limit:
            logger.info(
                "Apify sweep: %d new postings; fetching %d now, the rest next sweep.",
                len(detail_urls),
                limit,
            )
            detail_urls = detail_urls[:limit]
        if detail_urls:
            run_id = connector.start_details(detail_urls)
            remaining = max(30, int(deadline - time.monotonic()))
            for item in (connector.wait_items(run_id, wait_secs=remaining) or []) if run_id else []:
                raw = connector.detail_to_raw(item)
                path = connector.item_path(item)
                if raw is not None and path:
                    details[path] = raw

        connector.mark_fetched([c.id for c, _r, _m in pending])
        for company, raws, missing in pending:
            for url in missing:
                path = url[url.index("/job/"):]
                if path in details:
                    raws.append(details[path])
            logger.info(
                "Apify sweep: %s has %d student postings in Canada/US (%d new).",
                company.id,
                len(raws),
                sum(1 for u in missing if u[u.index("/job/"):] in details),
            )
            yield company, raws


def run(
    session: Session,
    http: HttpClient,
    *,
    company_ids: list[str] | None = None,
    max_postings_per_company: int | None = None,
) -> RunSummary:
    """Run a fail-soft ingestion pass over enabled + active companies.

    When ``company_ids`` is given the run is limited to those ids (still
    requiring enabled + active). One company failing is isolated: the error is
    recorded to ``source_health`` and the run continues.

    ``max_postings_per_company`` bounds how many postings each connector fetches
    in this run (``None`` = unbounded). It is set ONLY for the one-time add-time
    poll fired right after ``add-company --poll`` so a huge board cannot hold the
    add; the scheduled poller leaves it ``None`` and fetches the full board.
    """
    repo = CompanyRepository(session)
    companies = repo.list(enabled_only=True, active_only=True)
    if company_ids is not None:
        wanted = set(company_ids)
        companies = [c for c in companies if c.id in wanted]
    else:
        # Apify-backed (third-party, slow) companies are capped PER RUN and
        # rotated by least-recently-checked, so a backlog of slow fetches is
        # spread across polls instead of stalling one run. Direct-API companies
        # (greenhouse/lever/...) are fast and always run.
        companies = _cap_apify_companies(session, companies)

    summary = RunSummary()
    # Workday first: boards that refuse GitHub's servers are then fetched in
    # ONE Apify sweep that runs while the other boards are polled.
    workday = [c for c in companies if getattr(c, "ats", "") == "workday"]
    others = [c for c in companies if getattr(c, "ats", "") != "workday"]
    fallback = _WorkdayFallback()

    def _poll(company: Company) -> None:
        try:
            result = _ingest_company(
                session, company, http, max_postings=max_postings_per_company
            )
            session.commit()
        except Exception as exc:  # noqa: BLE001 - fail-soft: isolate per company.
            session.rollback()
            reason = describe_error(exc)
            if fallback.note_blocked(company, exc):
                logger.warning("Workday refused GitHub for %s: %s", company.id, reason)
            else:
                logger.exception("Ingestion failed for company %s", company.id)
            _record_error(session, company.id, reason)
            session.commit()
            result = CompanyRunResult(company_id=company.id, status="error", error=reason)
        summary.companies.append(result)

    import time

    deadline = time.monotonic() + max(60, int(get_settings().poll_time_budget_secs))
    skipped: list[str] = []
    for company in workday:
        _poll(company)
    fallback.start()
    for company in others:
        if time.monotonic() > deadline:
            skipped.append(company.id)
            continue
        _poll(company)
    if skipped:
        logger.warning(
            "Time budget reached; %d companies left for the next run: %s",
            len(skipped),
            ", ".join(skipped[:20]),
        )

    for company, raw_jobs in fallback.collect(session):
        try:
            result = _process_raw_jobs(
                session, company, _config_from_company(company), raw_jobs, via="apify"
            )
            session.commit()
            summary.companies = [
                r for r in summary.companies if r.company_id != company.id
            ] + [result]
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.warning("Apify sweep result for %s not used: %s", company.id, exc)

    logger.info(
        "Ingestion run complete: %d companies, %d new, %d changed, %d errors",
        len(summary.companies),
        summary.total_new,
        summary.total_changed,
        summary.errors,
    )
    return summary
