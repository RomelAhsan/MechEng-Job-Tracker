"""Workable connector (public widget/accounts JSON endpoint).

Workable boards that opt into public embedding expose a token-less JSON API::

    POST https://apply.workable.com/api/v2/accounts/{account}/jobs

returning ``{"results": [{shortcode, title, location, ...}], "total": int}``.
The list omits the full description, so for each posting we GET the detail at
``.../api/v2/accounts/{account}/jobs/{shortcode}`` and read ``description`` /
``requirements`` / ``benefits``.

Not every Workable account is public: the token-gated ``spi/v3`` API returns
401 for those. When the account exposes no public JSON (any non-dict / error
response), we fail soft and return ``[]`` so the company routes to the
``requires_fallback`` stub instead of crashing the run.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from jobtracker.connectors.base import Connector, RawJob
from jobtracker.connectors.http import HttpClient
from jobtracker.schemas.company import CompanyConfig

logger = logging.getLogger("jobtracker.connectors.workable")

_BASE = "https://apply.workable.com/api/v2/accounts"

# apply.workable.com/{account}  or  {account}.workable.com
_URL_RE = re.compile(
    r"https?://(?:apply\.workable\.com/(?P<path>[a-z0-9-]+)"
    r"|(?P<sub>[a-z0-9-]+)\.workable\.com)",
    re.IGNORECASE,
)


def parse_workable_account(url: str) -> str | None:
    """Extract the Workable account slug from a careers URL, or ``None``."""
    if not url:
        return None
    match = _URL_RE.search(url)
    if not match:
        return None
    path = match.group("path")
    if path:
        return path
    sub = match.group("sub")
    if sub and sub not in ("apply", "www"):
        return sub
    return None


def _account(company: CompanyConfig) -> str | None:
    if company.board_token:
        return company.board_token
    if company.careers_url:
        return parse_workable_account(company.careers_url)
    return None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _assemble_description(detail: dict) -> str:
    parts: list[str] = []
    for key in ("description", "requirements", "benefits"):
        value = detail.get(key)
        if value:
            parts.append(value)
    return "\n".join(parts)


def _location(entry: dict) -> str:
    loc = entry.get("location")
    if isinstance(loc, dict):
        bits = [loc.get("city"), loc.get("region"), loc.get("country")]
        return ", ".join(bit for bit in bits if bit)
    if isinstance(loc, str):
        return loc
    return ""


class WorkableConnector(Connector):
    """Fetch full-description postings from a public Workable account."""

    ats = "workable"

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def fetch(self, company: CompanyConfig) -> list[RawJob]:
        account = _account(company)
        if not account:
            logger.warning("Workable company %s has no account token", company.id)
            return []

        postings = self._fetch_all_postings(account)
        raw_jobs: list[RawJob] = []
        for posting in postings:
            raw = self._to_raw(account, posting)
            if raw is not None:
                raw_jobs.append(raw)
        return raw_jobs

    def _fetch_all_postings(self, account: str) -> list[dict]:
        url = f"{_BASE}/{account}/jobs"
        postings: list[dict] = []
        token: str | None = None
        while True:
            body: dict = {}
            if token:
                body["token"] = token
            payload = self.http.post_json(url, body)
            if not isinstance(payload, dict):
                # No public JSON API -> route to fallback.
                return []
            page = payload.get("results") or []
            postings.extend(p for p in page if isinstance(p, dict))
            token = payload.get("nextPage") or payload.get("token")
            if not page or not token:
                break
        return postings

    def _to_raw(self, account: str, posting: dict) -> RawJob | None:
        shortcode = posting.get("shortcode")
        if not shortcode:
            return None

        detail = self.http.get_json(f"{_BASE}/{account}/jobs/{shortcode}")
        detail = detail if isinstance(detail, dict) else {}
        description_html = _assemble_description(detail)

        apply_url = (
            detail.get("application_url")
            or detail.get("url")
            or posting.get("url")
            or posting.get("shortlink")
        )
        source_url = posting.get("url") or posting.get("shortlink") or apply_url

        merged = {**posting, "_detail": detail}
        return RawJob(
            source_job_id=str(shortcode),
            title=posting.get("title", "") or "",
            location=_location(posting),
            description_html=description_html,
            application_url=apply_url or source_url,
            source_url=source_url,
            posted_at=_parse_dt(posting.get("published_on") or posting.get("created_at")),
            department=posting.get("department"),
            employment_type_hint=posting.get("type"),
            extra=merged,
        )
