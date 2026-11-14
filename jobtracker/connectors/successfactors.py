"""SAP SuccessFactors "Career Site Builder" connector (public RSS feed).

Many large employers (Bombardier, Hatch, OPG, Teck, Nutrien, Celestica, BWXT,
ATS, Magellan, ...) host their careers on SuccessFactors sites such as
``jobs.hatch.com``. Those sites publish a PUBLIC, server-rendered RSS feed of
every open posting, including the full description and the posting date:

    GET https://{host}/services/rss/job/?locale=en_US&keywords=&rows=2000

One request returns the whole board, so polling is fast and exact. No login,
no JavaScript, no scraping of rendered pages.

``board_token`` is the career-site host, e.g. ``jobs.hatch.com``.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from jobtracker.connectors.base import Connector, RawJob
from jobtracker.connectors.http import HttpClient
from jobtracker.schemas.company import CompanyConfig

logger = logging.getLogger("jobtracker.connectors.successfactors")

_ROWS = 2000
_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S | re.I)
_CDATA_RE = re.compile(r"^\s*<!\[CDATA\[(.*)\]\]>\s*$", re.S)
# "Project Engineer (Nashville, TN, US)" -> title + location in parentheses.
_TITLE_LOC_RE = re.compile(r"^(?P<title>.*?)\s*\((?P<loc>[^()]*)\)\s*$")
# ".../job/Nashville-Project-Engineer-TN-37201/1420697600/" -> 1420697600
_JOB_ID_RE = re.compile(r"/job/[^/]+/(\d+)/?")


def _tag(item: str, name: str) -> str:
    match = re.search(rf"<{name}[^>]*>(.*?)</{name}>", item, re.S | re.I)
    if not match:
        return ""
    value = match.group(1)
    cdata = _CDATA_RE.match(value)
    return cdata.group(1) if cdata else html.unescape(value)


def _clean_link(link: str) -> str:
    """Drop the RSS tracking query string so the link is a stable job URL."""
    return html.unescape(link).split("?", 1)[0].strip()


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_feed(text: str) -> list[RawJob]:
    """Parse a SuccessFactors RSS document into RawJobs (pure; used by tests)."""
    jobs: list[RawJob] = []
    for item in _ITEM_RE.findall(text or ""):
        raw_title = html.unescape(_tag(item, "title")).strip()
        link = _clean_link(_tag(item, "link") or _tag(item, "guid"))
        if not raw_title or not link:
            continue
        title, location = raw_title, ""
        match = _TITLE_LOC_RE.match(raw_title)
        if match:
            title, location = match.group("title").strip(), match.group("loc").strip()
        id_match = _JOB_ID_RE.search(link)
        source_id = id_match.group(1) if id_match else link
        jobs.append(
            RawJob(
                source_job_id=source_id,
                title=title,
                location=location,
                description_html=_tag(item, "description"),
                application_url=link,
                source_url=link,
                posted_at=_parse_date(_tag(item, "pubDate")),
                extra={"successfactors": True},
            )
        )
    return jobs


class SuccessFactorsConnector(Connector):
    """Fetch a whole SuccessFactors career site from its public RSS feed."""

    ats = "successfactors"

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def fetch(self, company: CompanyConfig) -> list[RawJob]:
        host = (company.board_token or "").strip().strip("/")
        if not host:
            logger.warning("SuccessFactors company %s has no board_token host.", company.id)
            return []
        url = f"https://{host}/services/rss/job/"
        text = self.http.get_text(
            url, params={"locale": "en_US", "keywords": "", "rows": str(_ROWS)}
        )
        jobs = parse_feed(text)
        if not jobs:
            logger.info("SuccessFactors feed for %s returned no items.", company.id)
        return jobs
