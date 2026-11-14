"""Oracle Cloud Recruiting (ORC) connector (public ``recruitingCE`` REST API).

Employers on Oracle HCM (Honeywell, ...) expose a public candidate-experience
REST API on their Oracle Cloud tenant. The list endpoint is::

    GET {origin}/hcmRestApi/resources/latest/recruitingCEJobRequisitions
        ?onlyData=true
        &expand=requisitionList.secondaryLocations
        &finder=findReqs;siteNumber={SITE},limit=100,offset={N},sortBy=POSTING_DATES_DESC

whose ``items[0]`` carries ``TotalJobsCount`` and ``requisitionList[]``. Each
requisition has ``Id``, ``Title``, ``PostedDate``, ``PrimaryLocation``,
``PrimaryLocationCountry``, ``ShortDescriptionStr``, ``WorkplaceType``. The FULL
record (with the description HTML) comes from::

    GET {origin}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails
        ?expand=all&onlyData=true
        &finder=ById;Id="{id}",siteNumber={SITE}

returning ``items[0]`` with ``ExternalDescriptionStr`` /
``ExternalQualificationsStr`` / ``ExternalResponsibilitiesStr``.

The public apply/source URL is
``{origin}/hcmUI/CandidateExperience/en/sites/{SITE}/job/{Id}``.

``board_token`` is ``"{origin}|{siteNumber}"``. GOTCHAS honored: always send
``expand`` (without it ``requisitionList`` is omitted and the board looks
empty); the site number comes ONLY from the careers URL (a wrong one silently
returns the default site's jobs); dedupe by ``Id`` across pages.

CORE RULE #1: a 401/403 (auth wall) is surfaced via ``raise_for_status``.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from jobtracker.connectors.base import Connector, RawJob
from jobtracker.connectors.http import HttpClient
from jobtracker.schemas.company import CompanyConfig

logger = logging.getLogger("jobtracker.connectors.oracle")

#: Requisitions requested per list page.
_PAGE_LIMIT = 100
#: Hard ceiling on requisitions read from one board.
_MAX_REQS = 3000

_REST_BASE = "/hcmRestApi/resources/latest"
# https://{host}/hcmUI/CandidateExperience/{lang}/sites/{SITE}[/...]
_URL_RE = re.compile(
    r"(?P<origin>https?://[^/]+)/hcmUI/CandidateExperience/"
    r"[^/]+/sites/(?P<site>[^/?#]+)",
    re.IGNORECASE,
)


def parse_oracle_url(url: str | None) -> tuple[str, str] | None:
    """Extract ``(origin, site_number)`` from an Oracle CE careers URL."""
    if not url:
        return None
    match = _URL_RE.search(url)
    if not match:
        return None
    return match.group("origin"), match.group("site")


def _split_token(board_token: str | None) -> tuple[str, str] | None:
    """Split a ``"{origin}|{site}"`` board_token into its parts."""
    if not board_token or "|" not in board_token:
        return None
    origin, site = board_token.split("|", 1)
    origin, site = origin.strip(), site.strip()
    if not origin or not site:
        return None
    return origin, site


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    # PostedDate is sometimes a plain date ("2026-01-15").
    try:
        return datetime.fromisoformat(f"{text}T00:00:00+00:00")
    except ValueError:
        return None


def _location(req: dict) -> str:
    primary = req.get("PrimaryLocation")
    country = req.get("PrimaryLocationCountry")
    parts = [str(p).strip() for p in (primary, country) if p]
    seen: list[str] = []
    for part in parts:
        if part and part.lower() not in " ".join(seen).lower():
            seen.append(part)
    return ", ".join(seen)


def parse_requisitions(payload: object) -> list[dict]:
    """Return ``items[0].requisitionList`` from a list payload (pure helper)."""
    if not isinstance(payload, dict):
        return []
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return []
    first = items[0]
    reqs = first.get("requisitionList") if isinstance(first, dict) else None
    return [r for r in reqs if isinstance(r, dict)] if isinstance(reqs, list) else []


def _total_jobs(payload: object) -> int | None:
    if not isinstance(payload, dict):
        return None
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return None
    first = items[0]
    total = first.get("TotalJobsCount") if isinstance(first, dict) else None
    return total if isinstance(total, int) else None


class OracleConnector(Connector):
    """Fetch full-description postings from a public Oracle ORC board."""

    ats = "oracle"

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def _coordinates(self, company: CompanyConfig) -> tuple[str, str] | None:
        return _split_token(company.board_token) or parse_oracle_url(
            company.careers_url
        )

    def fetch(self, company: CompanyConfig) -> list[RawJob]:
        coords = self._coordinates(company)
        if coords is None:
            logger.warning(
                "Oracle company %s is missing a valid board_token/careers_url "
                "(expected '{origin}|{site}' or a CandidateExperience URL).",
                company.id,
            )
            return []
        origin, site = coords

        raw_jobs: list[RawJob] = []
        seen: set[str] = set()
        offset = 0
        total: int | None = None
        while offset < _MAX_REQS:
            payload = self._list(origin, site, offset)
            if total is None:
                total = _total_jobs(payload)
            reqs = parse_requisitions(payload)
            if not reqs:
                break
            for req in reqs:
                req_id = req.get("Id")
                if req_id is None:
                    continue
                req_id = str(req_id)
                if req_id in seen:
                    continue
                seen.add(req_id)
                raw_jobs.append(self._to_raw(origin, site, req))
            offset += len(reqs)
            if isinstance(total, int) and offset >= total:
                break
        if not raw_jobs:
            logger.info("Oracle board for %s returned no jobs.", company.id)
        return raw_jobs

    def _list(self, origin: str, site: str, offset: int) -> dict:
        finder = (
            f"findReqs;siteNumber={site},limit={_PAGE_LIMIT},"
            f"offset={offset},sortBy=POSTING_DATES_DESC"
        )
        response = self.http.request(
            "GET",
            f"{origin}{_REST_BASE}/recruitingCEJobRequisitions",
            params={
                "onlyData": "true",
                "expand": "requisitionList.secondaryLocations",
                "finder": finder,
            },
        )
        # CORE RULE #1: surface auth walls rather than returning empty.
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _to_raw(self, origin: str, site: str, req: dict) -> RawJob:
        req_id = str(req.get("Id"))
        detail = self._detail(origin, site, req_id)
        description = _description_html(detail or req)
        public_url = f"{origin}/hcmUI/CandidateExperience/en/sites/{site}/job/{req_id}"
        posted = (
            _parse_dt(req.get("PostedDate"))
            or _parse_dt(req.get("ExternalPostedStartDate"))
            or _parse_dt((detail or {}).get("PostedDate"))
        )
        merged = {**req}
        if detail:
            merged["_detail"] = detail
        return RawJob(
            source_job_id=req_id,
            title=str(req.get("Title") or (detail or {}).get("Title") or ""),
            location=_location(req) or _location(detail or {}),
            description_html=description,
            application_url=public_url,
            source_url=public_url,
            posted_at=posted,
            employment_type_hint=req.get("WorkplaceType"),
            extra=merged,
        )

    def _detail(self, origin: str, site: str, req_id: str) -> dict | None:
        finder = f'ById;Id="{req_id}",siteNumber={site}'
        response = self.http.request(
            "GET",
            f"{origin}{_REST_BASE}/recruitingCEJobRequisitionDetails",
            params={"expand": "all", "onlyData": "true", "finder": finder},
        )
        if response.status_code in (401, 403):
            response.raise_for_status()
        payload = response.json()
        items = payload.get("items") if isinstance(payload, dict) else None
        if isinstance(items, list) and items and isinstance(items[0], dict):
            return items[0]
        return None


def _description_html(record: dict) -> str:
    """Concatenate the external description/qualifications/responsibilities."""
    parts = [
        record.get("ExternalDescriptionStr"),
        record.get("ExternalResponsibilitiesStr"),
        record.get("ExternalQualificationsStr"),
    ]
    joined = "\n".join(p for p in parts if isinstance(p, str) and p.strip())
    if joined.strip():
        return joined
    short = record.get("ShortDescriptionStr")
    return short if isinstance(short, str) else ""
