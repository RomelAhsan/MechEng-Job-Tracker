"""Phenom People connector (public ``/widgets`` search API).

Large employers on Phenom's CMS (RTX / Raytheon, Pratt & Whitney Canada, ...)
expose a public search endpoint at::

    POST https://{careers-host}/widgets

The list is one POST with ``ddoKey=refineSearch``::

    {"ddoKey": "refineSearch", "jobs": true, "from": 0, "size": 500,
     "sort": {"order": "desc", "field": "dateCreated"}}

whose response carries ``.refineSearch.totalHits`` and
``.refineSearch.data.jobs[]``. Each job has ``jobId``, ``reqId``, ``title``,
``city``/``state``/``country``/``location``, ``applyUrl``, ``postedDate``,
``dateCreated`` (epoch ms) and only a ``descriptionTeaser``. The FULL
description comes from a second POST::

    {"ddoKey": "jobDetail", "jobId": "<id>"}

returning ``.jobDetail.data.job`` with the full description HTML.

``board_token`` is the careers host, e.g. ``careers.rtx.com``.

CORE RULE #1: a 401/403 (auth wall) is surfaced via ``raise_for_status`` so the
company stays a fallback stub rather than silently returning empty descriptions.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from jobtracker.connectors.base import Connector, RawJob
from jobtracker.connectors.http import HttpClient
from jobtracker.normalize.posting_date import parse_relative_posted
from jobtracker.schemas.company import CompanyConfig

logger = logging.getLogger("jobtracker.connectors.phenom")

#: Jobs requested per ``refineSearch`` page.
_PAGE_SIZE = 500
#: Phenom caps the search window at ``from + size < 10000``.
_MAX_WINDOW = 10000


def parse_phenom_host(url: str | None) -> str | None:
    """Return the careers host for a Phenom careers ``url`` (or ``None``)."""
    if not url:
        return None
    candidate = url.strip()
    match = re.match(r"^https?://([^/]+)", candidate)
    if match:
        return match.group(1).lower()
    # A bare host ("careers.rtx.com") or "careers.rtx.com/path".
    host = candidate.split("/", 1)[0].strip().lower()
    return host or None


def _parse_ms(value: object) -> datetime | None:
    """Parse an epoch-milliseconds value (int or numeric string) to UTC."""
    if value is None:
        return None
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _posted_at(job: dict, *, now: datetime | None = None) -> datetime | None:
    """Best available TRUE posting date for a Phenom job.

    Prefers the millisecond ``dateCreated``/``postedDate`` fields, then an ISO
    string, then a relative "Posted N Days Ago" style string.
    """
    for key in ("dateCreated", "postedDate", "postedOn"):
        parsed = _parse_ms(job.get(key))
        if parsed is not None:
            return parsed
    for key in ("postedDate", "postedOn", "dateCreated"):
        parsed = _parse_iso(job.get(key))
        if parsed is not None:
            return parsed
    for key in ("postedDate", "postedOn"):
        relative = parse_relative_posted(job.get(key), now=now)
        if relative is not None:
            return relative
    return None


def _location(job: dict) -> str:
    """Compose a human location from the Phenom job fields."""
    explicit = job.get("location")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    parts = [
        str(job.get(key)).strip()
        for key in ("city", "state", "country")
        if job.get(key)
    ]
    return ", ".join(p for p in parts if p)


def _public_url(host: str, job: dict) -> str | None:
    """Public Phenom job URL, falling back to the raw ``applyUrl``."""
    job_id = job.get("jobId")
    if job_id:
        return f"https://{host}/job/{job_id}"
    apply_url = job.get("applyUrl")
    return apply_url if isinstance(apply_url, str) and apply_url else None


def parse_widgets(payload: object, host: str = "") -> list[RawJob]:
    """Parse a ``refineSearch`` payload into RawJobs (pure; used by tests).

    Descriptions come from the per-job ``jobDetail`` call in :meth:`fetch`;
    this helper maps the list fields and uses the teaser as a placeholder body.
    """
    if not isinstance(payload, dict):
        return []
    refine = payload.get("refineSearch")
    data = refine.get("data") if isinstance(refine, dict) else None
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        return []

    raw_jobs: list[RawJob] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        job_id = job.get("jobId")
        if job_id is None:
            continue
        url = _public_url(host, job)
        raw_jobs.append(
            RawJob(
                source_job_id=str(job_id),
                title=str(job.get("title") or ""),
                location=_location(job),
                description_html=str(job.get("descriptionTeaser") or ""),
                application_url=url,
                source_url=url,
                posted_at=_posted_at(job),
                extra={"phenom": True, "reqId": job.get("reqId")},
            )
        )
    return raw_jobs


class PhenomConnector(Connector):
    """Fetch full-description postings from a public Phenom ``/widgets`` API."""

    ats = "phenom"

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def fetch(self, company: CompanyConfig) -> list[RawJob]:
        host = parse_phenom_host(company.board_token or company.careers_url)
        if not host:
            logger.warning(
                "Phenom company %s has no careers host (board_token/careers_url).",
                company.id,
            )
            return []

        url = f"https://{host}/widgets"
        raw_jobs: list[RawJob] = []
        seen: set[str] = set()
        offset = 0
        while offset + _PAGE_SIZE < _MAX_WINDOW:
            page = self._search(url, offset)
            refine = page.get("refineSearch") if isinstance(page, dict) else None
            total = refine.get("totalHits") if isinstance(refine, dict) else None
            parsed = parse_widgets(page, host)
            if not parsed:
                break
            for raw in parsed:
                if raw.source_job_id in seen:
                    continue
                seen.add(raw.source_job_id)
                self._attach_detail(url, host, raw)
                raw_jobs.append(raw)
            offset += _PAGE_SIZE
            if not isinstance(total, int) or offset >= total:
                break
        if not raw_jobs:
            logger.info("Phenom board for %s returned no jobs.", company.id)
        return raw_jobs

    def _search(self, url: str, offset: int) -> dict:
        response = self.http.request(
            "POST",
            url,
            json_body={
                "ddoKey": "refineSearch",
                "jobs": True,
                "from": offset,
                "size": _PAGE_SIZE,
                "sort": {"order": "desc", "field": "dateCreated"},
            },
        )
        # CORE RULE #1: a 401/403 (auth wall) must surface, not be worked around.
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _attach_detail(self, url: str, host: str, raw: RawJob) -> None:
        """Fetch and attach the FULL description for one job."""
        response = self.http.request(
            "POST", url, json_body={"ddoKey": "jobDetail", "jobId": raw.source_job_id}
        )
        if response.status_code in (401, 403):
            response.raise_for_status()
        detail = response.json()
        job = {}
        if isinstance(detail, dict):
            node = detail.get("jobDetail")
            data = node.get("data") if isinstance(node, dict) else None
            maybe = data.get("job") if isinstance(data, dict) else None
            if isinstance(maybe, dict):
                job = maybe
        description = (
            job.get("description")
            or job.get("jobDescription")
            or job.get("descriptionTeaser")
        )
        if isinstance(description, str) and description.strip():
            raw.description_html = description
        detail_posted = _posted_at(job) if job else None
        if detail_posted is not None:
            raw.posted_at = detail_posted
        if job:
            raw.extra["_detail"] = job
