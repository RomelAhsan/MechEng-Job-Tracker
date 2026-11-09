"""Generic best-effort connectors: RSS, sitemap, and schema.org JSON-LD.

These cover employers that publish jobs through a standard feed rather than a
first-class ATS API:

* :class:`RssConnector` parses an RSS/Atom feed of job postings via feedparser.
* :class:`SitemapConnector` reads a careers ``sitemap.xml``, visits each listed
  job URL, and extracts a schema.org ``JobPosting`` JSON-LD block.
* :class:`JsonLdConnector` fetches a single careers/listing page and extracts
  every embedded ``JobPosting`` JSON-LD block.

Every produced :class:`RawJob` carries a direct ``application_url`` +
``source_url`` (SPEC CORE RULE #3). All three fail soft to ``[]`` when the feed
is absent, empty, or unparseable, so the run never crashes.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

import feedparser
from bs4 import BeautifulSoup

from jobtracker.connectors.base import Connector, RawJob
from jobtracker.connectors.http import HttpClient
from jobtracker.schemas.company import CompanyConfig

logger = logging.getLogger("jobtracker.connectors.feed")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _feed_url(company: CompanyConfig) -> str | None:
    return company.board_token or company.careers_url


def _iter_json_ld(html: str) -> list[dict]:
    """Return every schema.org JobPosting dict embedded in ``html``."""
    soup = BeautifulSoup(html, "html.parser")
    postings: list[dict] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        postings.extend(_collect_job_postings(data))
    return postings


def _collect_job_postings(data: object) -> list[dict]:
    found: list[dict] = []
    if isinstance(data, list):
        for item in data:
            found.extend(_collect_job_postings(item))
    elif isinstance(data, dict):
        graph = data.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                found.extend(_collect_job_postings(item))
        if data.get("@type") == "JobPosting":
            found.append(data)
    return found


def _job_posting_url(posting: dict) -> str | None:
    url = posting.get("url")
    if url:
        return url
    apply_info = posting.get("hiringOrganization")
    if isinstance(apply_info, dict):
        same_as = apply_info.get("sameAs")
        if isinstance(same_as, str):
            return same_as
    direct = posting.get("directApply")
    if isinstance(direct, str):
        return direct
    return None


def _job_posting_location(posting: dict) -> str:
    loc = posting.get("jobLocation")
    entries = loc if isinstance(loc, list) else [loc]
    bits: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        address = entry.get("address")
        if isinstance(address, dict):
            for key in ("addressLocality", "addressRegion", "addressCountry"):
                value = address.get(key)
                if isinstance(value, dict):
                    value = value.get("name")
                if value:
                    bits.append(str(value))
    return ", ".join(bits)


def _job_posting_to_raw(posting: dict) -> RawJob | None:
    url = _job_posting_url(posting)
    if not url:
        return None
    identifier = posting.get("identifier")
    if isinstance(identifier, dict):
        identifier = identifier.get("value")
    source_job_id = str(identifier or url)
    return RawJob(
        source_job_id=source_job_id,
        title=posting.get("title", "") or "",
        location=_job_posting_location(posting),
        description_html=posting.get("description", "") or "",
        application_url=url,
        source_url=url,
        posted_at=_parse_dt(posting.get("datePosted")),
        employment_type_hint=posting.get("employmentType"),
        extra=posting,
    )


class RssConnector(Connector):
    """Parse an RSS/Atom feed of job postings (best effort)."""

    ats = "rss"

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def fetch(self, company: CompanyConfig) -> list[RawJob]:
        url = _feed_url(company)
        if not url:
            logger.warning("RSS company %s has no feed url", company.id)
            return []
        try:
            text = self.http.get_text(url)
        except Exception:  # noqa: BLE001 - best effort, never crash the run
            logger.info("RSS feed unavailable for %s", company.id)
            return []
        parsed = feedparser.parse(text)
        raw_jobs: list[RawJob] = []
        for entry in parsed.entries:
            link = entry.get("link")
            if not link:
                continue
            raw_jobs.append(
                RawJob(
                    source_job_id=str(entry.get("id") or link),
                    title=entry.get("title", "") or "",
                    description_html=(
                        entry.get("summary") or entry.get("description") or ""
                    ),
                    application_url=link,
                    source_url=link,
                    posted_at=_parse_dt(entry.get("published")),
                    extra=dict(entry),
                )
            )
        return raw_jobs


class SitemapConnector(Connector):
    """Read a careers sitemap.xml and extract JSON-LD JobPostings per URL."""

    ats = "sitemap"

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def fetch(self, company: CompanyConfig) -> list[RawJob]:
        url = _feed_url(company)
        if not url:
            logger.warning("Sitemap company %s has no sitemap url", company.id)
            return []
        try:
            text = self.http.get_text(url)
        except Exception:  # noqa: BLE001 - best effort, never crash the run
            logger.info("Sitemap unavailable for %s", company.id)
            return []

        soup = BeautifulSoup(text, "xml")
        locs = [loc.get_text(strip=True) for loc in soup.find_all("loc")]
        raw_jobs: list[RawJob] = []
        for job_url in locs:
            if not job_url:
                continue
            try:
                page = self.http.get_text(job_url)
            except Exception:  # noqa: BLE001 - skip unreachable job pages
                continue
            for posting in _iter_json_ld(page):
                raw = _job_posting_to_raw({**posting, "url": posting.get("url") or job_url})
                if raw is not None:
                    raw_jobs.append(raw)
        return raw_jobs


class JsonLdConnector(Connector):
    """Fetch a careers listing page and extract schema.org JobPostings."""

    ats = "json_ld"

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def fetch(self, company: CompanyConfig) -> list[RawJob]:
        url = _feed_url(company)
        if not url:
            logger.warning("JSON-LD company %s has no listing url", company.id)
            return []
        try:
            page = self.http.get_text(url)
        except Exception:  # noqa: BLE001 - best effort, never crash the run
            logger.info("JSON-LD page unavailable for %s", company.id)
            return []
        raw_jobs: list[RawJob] = []
        for posting in _iter_json_ld(page):
            raw = _job_posting_to_raw(posting)
            if raw is not None:
                raw_jobs.append(raw)
        return raw_jobs
