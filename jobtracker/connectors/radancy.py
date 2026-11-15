"""Radancy / TalentBrew connector (public ``/en/search-jobs/results`` search).

Large employers on Radancy's TalentBrew careers platform (L3Harris, ...) expose
a public search endpoint that returns JSON wrapping an HTML results fragment::

    GET https://{host}/en/search-jobs/results
        ?ActiveFacetID=0
        &CurrentPage={N}
        &RecordsPerPage=50
        &SearchResultsModuleName=Search Results

The response is ``{"filters": ..., "results": "<html fragment>", "hasJobs":
bool, "hasContent": bool}``. The ``results`` fragment's
``<section id="search-results">`` carries ``data-total-pages`` /
``data-total-results``, and each job card is::

    <li>
      <a href="/en/job/{city}/{slug}/4832/{jobId}" data-job-id="{id}">
        <h2>{title}</h2>
        ...
        <span class="results-facet job-location ...">{location}</span>
      </a>
    </li>

Each job PAGE at ``https://{host}{href}`` carries exactly one
``<script type="application/ld+json">`` schema.org ``JobPosting`` with
``datePosted`` (YYYY-MM-DD), ``title``, ``description`` (HTML) and
``jobLocation``. We reuse :func:`jobtracker.connectors.feed._iter_json_ld` and
:func:`jobtracker.connectors.feed._job_posting_to_raw` to pull the detail.

``board_token`` is the careers host, e.g. ``careers.l3harris.com``.

CORE RULE #1: a 401/403 (auth wall) is surfaced via ``raise_for_status`` so the
company stays a fallback stub rather than silently returning empty descriptions.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from jobtracker.connectors.base import Connector, RawJob
from jobtracker.connectors.feed import _iter_json_ld, _job_posting_to_raw
from jobtracker.connectors.http import HttpClient
from jobtracker.schemas.company import CompanyConfig

logger = logging.getLogger("jobtracker.connectors.radancy")

#: Jobs requested per search page.
_RECORDS_PER_PAGE = 50
#: Hard ceiling on search pages read from one board (bounds huge boards).
_MAX_PAGES = 60


def parse_radancy_host(url: str | None) -> str | None:
    """Return the careers host for a Radancy careers ``url`` (or ``None``)."""
    if not url:
        return None
    candidate = url.strip()
    match = re.match(r"^https?://([^/]+)", candidate)
    if match:
        return match.group(1).lower()
    # A bare host ("careers.l3harris.com") or "careers.l3harris.com/path".
    host = candidate.split("/", 1)[0].strip().lower()
    return host or None


def parse_results_fragment(
    html: str, host: str
) -> list[tuple[str, str, str, str | None]]:
    """Parse a results HTML fragment into ``(url, title, location, job_id)``.

    Pure helper (used by tests): reads every ``<li><a data-job-id=...>`` job
    card, resolving the relative ``href`` against ``https://{host}``.
    """
    soup = BeautifulSoup(html, "html.parser")
    base = f"https://{host}"
    jobs: list[tuple[str, str, str, str | None]] = []
    for anchor in soup.select("a[data-job-id]"):
        href = anchor.get("href")
        if not href:
            continue
        url = urljoin(base, href)
        title_tag = anchor.find("h2")
        title = title_tag.get_text(strip=True) if title_tag else ""
        location_tag = anchor.select_one(".job-location")
        location = location_tag.get_text(strip=True) if location_tag else ""
        job_id = anchor.get("data-job-id")
        jobs.append((url, title, location, job_id))
    return jobs


def _total_pages(html: str) -> int | None:
    """Read ``data-total-pages`` from the results ``<section>`` (or ``None``)."""
    soup = BeautifulSoup(html, "html.parser")
    section = soup.find("section", id="search-results")
    if section is None:
        return None
    raw = section.get("data-total-pages")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


class RadancyConnector(Connector):
    """Fetch full-description postings from a public Radancy TalentBrew board."""

    ats = "radancy"

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def fetch(self, company: CompanyConfig) -> list[RawJob]:
        host = parse_radancy_host(company.board_token or company.careers_url)
        if not host:
            logger.warning(
                "Radancy company %s has no careers host (board_token/careers_url).",
                company.id,
            )
            return []

        search_url = f"https://{host}/en/search-jobs/results"
        # ``max_postings`` (set only for the one-time add-time poll) caps how
        # many postings we fetch full descriptions for, so a large board does
        # not issue a per-posting detail request for thousands of jobs and hold
        # the add. The scheduled poll leaves it ``None`` and fetches everything.
        cap = self.max_postings if isinstance(self.max_postings, int) else None
        raw_jobs: list[RawJob] = []
        seen: set[str] = set()
        page = 1
        total_pages: int | None = None
        while page <= _MAX_PAGES:
            fragment = self._search(search_url, page)
            if total_pages is None:
                total_pages = _total_pages(fragment)
            listings = parse_results_fragment(fragment, host)
            if not listings:
                break
            for url, title, location, _job_id in listings:
                if url in seen:
                    continue
                seen.add(url)
                raw = self._detail(url, title, location)
                if raw is not None:
                    raw_jobs.append(raw)
                if cap is not None and len(raw_jobs) >= cap:
                    logger.info(
                        "Radancy board for %s reached the add-time cap of %d "
                        "postings; the scheduled poll will fetch the rest.",
                        company.id,
                        cap,
                    )
                    return raw_jobs
            if isinstance(total_pages, int) and page >= total_pages:
                break
            page += 1
        if not raw_jobs:
            logger.info("Radancy board for %s returned no jobs.", company.id)
        return raw_jobs

    def _search(self, url: str, page: int) -> str:
        response = self.http.request(
            "GET",
            url,
            params={
                "ActiveFacetID": "0",
                "CurrentPage": page,
                "RecordsPerPage": _RECORDS_PER_PAGE,
                "SearchResultsModuleName": "Search Results",
            },
        )
        # CORE RULE #1: surface auth walls rather than returning empty.
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results") if isinstance(payload, dict) else None
        return results if isinstance(results, str) else ""

    def _detail(self, url: str, title: str, location: str) -> RawJob | None:
        """Fetch a job page and build a RawJob from its JobPosting JSON-LD.

        Falls back to the listing title/location when the page carries no
        schema.org ``JobPosting`` block.
        """
        response = self.http.request("GET", url)
        # CORE RULE #1: surface auth walls rather than returning empty.
        response.raise_for_status()
        html = response.text
        for posting in _iter_json_ld(html):
            raw = _job_posting_to_raw({**posting, "url": posting.get("url") or url})
            if raw is None:
                continue
            if not raw.title:
                raw.title = title
            if not raw.location:
                raw.location = location
            return raw
        # No JSON-LD: still surface the listing so the board is not empty.
        # ``posted_at`` is left ``None`` here because the only date source on a
        # Radancy board is the JobPosting ``datePosted`` in the page's JSON-LD;
        # the results fragment job cards carry only title/location/job-id (no
        # date attribute) and a no-JSON-LD page exposes no posting date to
        # parse. An undated job is KEPT by the freshness cutoff rather than
        # dropped, so this edge case (every live L3Harris page has JSON-LD)
        # loses only the precise age label, not the posting itself.
        return RawJob(
            source_job_id=url,
            title=title,
            location=location,
            application_url=url,
            source_url=url,
        )
