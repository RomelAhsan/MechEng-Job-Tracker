"""Company management service (design 10).

Owns the lifecycle of tracked employers: seeding from ``companies.yaml``
(``sync_from_yaml``), adding a company by careers URL (``add_from_url``),
enable / disable / remove, and listing. YAML is the declarative seed; the
database is authoritative at runtime (upsert by ``id``).

All business logic lives here so the CLI and dashboard are thin callers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
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


@dataclass
class ResolveCandidate:
    """A candidate public-board URL probed by ``add_from_name``."""

    ats: str
    url: str


@dataclass
class AddByNameResult:
    """Summary of an ``add_from_name`` call (graceful: never raises).

    ``resolved`` is True when a public board returned real postings, its
    identity was confirmed to match the typed name, and the company was
    baseline-imported. When False, ``message`` explains why and the caller
    (CLI / dashboard) should suggest the add-by-URL fallback.

    ``needs_verification`` is True in the special case where a public board DID
    return postings for the typed slug but its identity could NOT be confirmed
    to belong to the typed company (a slug collision could attach an unrelated
    org's board). In that case ``candidate_ats`` / ``candidate_url`` name the
    board found so the caller can show a "verify this is correct" prompt and,
    only on explicit confirmation, re-call with ``confirm=True`` to import it.
    """

    name: str
    resolved: bool
    message: str
    add_result: AddResult | None = None
    tried: list[str] = field(default_factory=list)
    needs_verification: bool = False
    candidate_ats: str | None = None
    candidate_url: str | None = None


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


def name_candidates(name: str) -> list[ResolveCandidate]:
    """Build the ordered list of public-board URLs to probe for ``name``.

    We only probe ATSes whose public board is addressable by a plain slug:
    Greenhouse, Lever, Ashby, and Workable. Workday is intentionally excluded
    because its URL needs a verified ``tenant``/``data_center``/``site`` triple
    that cannot be guessed from a name (guessing returns 404/422); users add
    Workday employers via the careers-URL path instead.

    Two slug spellings are tried (``acme-robotics`` and ``acmerobotics``) to
    cover both common board-token conventions.
    """
    slug = _slugify(name)
    compact = slug.replace("-", "")
    slugs: list[str] = []
    for candidate in (slug, compact):
        if candidate and candidate not in slugs:
            slugs.append(candidate)

    candidates: list[ResolveCandidate] = []
    for s in slugs:
        candidates.append(ResolveCandidate("greenhouse", f"https://boards.greenhouse.io/{s}"))
        candidates.append(ResolveCandidate("lever", f"https://jobs.lever.co/{s}"))
        candidates.append(ResolveCandidate("ashby", f"https://jobs.ashbyhq.com/{s}"))
        candidates.append(ResolveCandidate("workable", f"https://apply.workable.com/{s}"))
    return candidates


@dataclass
class ProbeResult:
    """Outcome of probing one candidate board (count + identity signal)."""

    count: int
    board_token: str | None = None
    identity_confirmed: bool = False


def _name_tokens(value: str) -> set[str]:
    """Lowercase alphanumeric tokens of ``value`` (for board-identity checks)."""
    compact = "".join(ch.lower() if ch.isalnum() else " " for ch in value)
    return {t for t in compact.split() if t}


def _board_identity_matches(
    name: str, board_token: str | None, raw_jobs: list
) -> bool:
    """Heuristic: does the probed board plausibly belong to the typed ``name``?

    Deterministic and ATS-agnostic. The board TOKEN alone is NOT a valid
    identity signal here: we built the candidate URL from the typed slug, so a
    token that equals the slug is circular (this is exactly the slug-collision
    case the review flagged, e.g. typing "apple" hitting an unrelated
    ``boards.greenhouse.io/apple``). Identity is therefore confirmed only from
    board-derived content:

    * a name token appears in a posting's apply/source URL host or path
      (postings that link to the company's own ``careers.<name>.com`` domain or
      carry the name in the path tie the board to the typed company); or
    * a name token appears in a posting's company/employer metadata where the
      ATS exposes it (``extra`` keys like ``company``/``companyName``).

    When no such signal is present we return ``False`` so the caller asks the
    user to verify rather than silently importing a possibly-wrong board.
    """
    name_toks = {t for t in _name_tokens(name) if len(t) >= 3}
    if not name_toks:
        return False

    def _hit(text: str | None) -> bool:
        if not text:
            return False
        lowered = text.lower()
        return any(tok in lowered for tok in name_toks)

    for raw in raw_jobs:
        # URL-host/path signal.
        for url in (
            getattr(raw, "application_url", None),
            getattr(raw, "source_url", None),
        ):
            if _hit(url):
                return True
        # Board/employer metadata signal, where the ATS exposes it.
        extra = getattr(raw, "extra", None)
        if isinstance(extra, dict):
            for key in ("company", "companyName", "company_name", "employer"):
                value = extra.get(key)
                if isinstance(value, str) and _hit(value):
                    return True
    return False


def _probe_candidate(
    http: HttpClient, candidate: ResolveCandidate, name: str
) -> ProbeResult:
    """Fetch a candidate board and report count + whether identity is confirmed.

    Fail-soft: connectivity/parse errors mean "this slug is not a live board",
    so we return a zero-count result and move on rather than raising. When the
    board returns postings we also run :func:`_board_identity_matches` so the
    caller can refuse to silently import a slug-colliding, unrelated board.
    """
    detected = detect_ats(candidate.url)
    if not detected.board_token:
        return ProbeResult(count=0)
    config = CompanyConfig(
        id=_slugify(candidate.url),
        name=candidate.url,
        ats=detected.ats,
        board_token=detected.board_token,
        careers_url=candidate.url,
    )
    connector = get_connector(detected.ats, http)
    if isinstance(connector, FallbackConnector):
        return ProbeResult(count=0)
    try:
        raw_jobs = connector.fetch(config)
    except Exception as exc:  # noqa: BLE001 - probe captures the failure.
        logger.debug("Probe failed for %s: %r", candidate.url, exc)
        return ProbeResult(count=0)
    return ProbeResult(
        count=len(raw_jobs),
        board_token=detected.board_token,
        identity_confirmed=_board_identity_matches(
            name, detected.board_token, raw_jobs
        ),
    )


def add_from_name(
    session: Session,
    name: str,
    http: HttpClient,
    *,
    company_id: str | None = None,
    priority: str = "medium",
    confirm: bool = False,
) -> AddByNameResult:
    """Resolve a plain company name to a public board and baseline-import it.

    Probes the known public ATS URL patterns for the name's slug
    (:func:`name_candidates`), picks the FIRST that returns real postings, and
    reuses :func:`add_from_url` (``baseline=True`` so existing jobs are marked
    seen and never fire false 'new' notifications, per the SPEC ADD-COMPANY
    ASSISTANT rules).

    Board-identity safeguard: a non-empty board is only imported when its
    identity is confirmed to belong to the typed name (see
    :func:`_board_identity_matches`). A slug collision (e.g. typing "apple"
    attaching an unrelated ``boards.greenhouse.io/apple``) is NOT silently
    imported; instead the first such board is returned with
    ``needs_verification=True`` and a "verify this is correct" message. The
    caller can re-invoke with ``confirm=True`` to import it anyway.

    Never raises: when nothing resolves it returns ``resolved=False`` with a
    helpful message so the CLI/dashboard can tell the user to try the careers
    URL directly.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        return AddByNameResult(
            name=name,
            resolved=False,
            message="Please provide a company name.",
        )

    tried: list[str] = []
    unverified: ResolveCandidate | None = None
    for candidate in name_candidates(cleaned):
        tried.append(candidate.url)
        probe = _probe_candidate(http, candidate, cleaned)
        if probe.count <= 0:
            continue
        if not probe.identity_confirmed and not confirm:
            # A live board exists for this slug but we cannot confirm it is the
            # typed company. Remember the first such candidate and keep probing
            # in case a later candidate IS a confident match.
            if unverified is None:
                unverified = candidate
            continue
        add_result = add_from_url(
            session,
            candidate.url,
            http,
            company_id=company_id or _slugify(cleaned),
            name=cleaned,
            priority=priority,
        )
        return AddByNameResult(
            name=cleaned,
            resolved=True,
            message=(
                f"Resolved '{cleaned}' to {add_result.ats} board "
                f"{candidate.url}; baseline imported "
                f"{add_result.baseline_jobs} jobs."
            ),
            add_result=add_result,
            tried=tried,
        )

    if unverified is not None:
        return AddByNameResult(
            name=cleaned,
            resolved=False,
            needs_verification=True,
            candidate_ats=unverified.ats,
            candidate_url=unverified.url,
            message=(
                f"Found a public {unverified.ats} board at {unverified.url} for "
                f"'{cleaned}', but could not confirm it belongs to that company "
                "(board slugs can collide with an unrelated org). Verify this is "
                "the correct board, then confirm to import it, or use 'Add a "
                "company by careers URL' with the exact board link."
            ),
            tried=tried,
        )

    return AddByNameResult(
        name=cleaned,
        resolved=False,
        message=(
            f"Could not auto-resolve '{cleaned}' to a public job board "
            "(Greenhouse / Lever / Ashby / Workable). Many employers (e.g. "
            "Workday-based ones) need their exact careers URL. Try 'Add a "
            "company by careers URL' with the board link from the company's "
            "careers page."
        ),
        tried=tried,
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


def update_settings(
    session: Session,
    company_id: str,
    *,
    priority: str | None = None,
    check_interval_minutes: int | None = None,
) -> Company:
    """Edit a company's priority and/or check interval (dashboard controls)."""
    company = _require(session, company_id)
    if priority is not None:
        company.priority = priority
    if check_interval_minutes is not None:
        company.check_interval_minutes = check_interval_minutes
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
