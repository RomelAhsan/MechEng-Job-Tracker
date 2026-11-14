"""Workday connector (public CXS JSON endpoint).

Workday exposes a public careers API at::

    POST https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs

with a JSON body ``{"limit": N, "offset": M, "appliedFacets": {}, "searchText": ""}``.
The response is ``{"total": int, "jobPostings": [{title, externalPath,
locationsText, postedOn, bulletFields}, ...]}``. The list omits the description
body, so for every posting we GET the detail at
``.../wday/cxs/{tenant}/{site}{externalPath}`` and read ``jobPostingInfo``
(``jobDescription`` HTML, ``externalUrl`` canonical apply link, ``location``,
``startDate``).

CORE RULE #1: a 401/403 means the board is auth-walled. The shared HTTP client
does not retry those; we surface the error so the company stays a fallback stub
rather than attempting to bypass the auth wall.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from jobtracker.connectors.base import Connector, RawJob
from jobtracker.connectors.http import HttpClient
from jobtracker.schemas.company import CompanyConfig

logger = logging.getLogger("jobtracker.connectors.workday")

_PAGE_LIMIT = 20
#: Boards bigger than this are searched by student keywords instead of listed
#: in full (see ``_fetch_all_postings``).
_LARGE_BOARD = 300
#: Workday keyword queries that surface student roles (English + French).
_STUDENT_SEARCH_TERMS = ("intern", "co-op", "coop", "student", "stagiaire")
#: Hard ceiling on postings read from one query (Workday caps offsets ~2000).
_MAX_POSTINGS = 2000

# tenant.dc.myworkdayjobs.com/[wday/cxs/tenant/]{site}[/...]
_URL_RE = re.compile(
    r"https?://(?P<tenant>[a-z0-9-]+)\.(?P<dc>wd\d+)\.myworkdayjobs\.com"
    r"(?:/wday/cxs/[^/]+)?/(?P<site>[A-Za-z0-9_]+)",
    re.IGNORECASE,
)


class WorkdayCoordinates:
    """Parsed Workday ``tenant`` / ``data_center`` / ``site`` triple."""

    __slots__ = ("tenant", "data_center", "site")

    def __init__(self, tenant: str, data_center: str, site: str) -> None:
        self.tenant = tenant
        self.data_center = data_center
        self.site = site

    @property
    def base(self) -> str:
        return f"https://{self.tenant}.{self.data_center}.myworkdayjobs.com"

    @property
    def cxs_base(self) -> str:
        return f"{self.base}/wday/cxs/{self.tenant}/{self.site}"

    def public_url(self, external_path: str) -> str:
        """The public careers/apply URL for an ``externalPath``."""
        return with_locale(f"{self.base}/{self.site}{external_path}")


_LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


def with_locale(url: str, locale: str = "en-US") -> str:
    """Insert the ``/en-US/`` locale segment into a myworkdayjobs job URL.

    Workday's career-site router shows "An error occurred. Please contact
    Workday Support" / a 404 for ``{host}/{site}/job/...`` links opened in a
    browser; ``{host}/en-US/{site}/job/...`` resolves. URLs that already carry
    a locale, or are not myworkdayjobs URLs, are returned unchanged.
    """
    match = re.match(r"^(https?://[^/]*myworkdayjobs\.com)/([^/?#]+)(.*)$", url or "")
    if not match:
        return url
    host, first, rest = match.groups()
    if _LOCALE_RE.match(first):
        return url
    return f"{host}/{locale}/{first}{rest}"


def parse_workday_url(url: str) -> WorkdayCoordinates | None:
    """Extract ``tenant``/``dc``/``site`` from a myworkdayjobs URL, or ``None``."""
    if not url:
        return None
    match = _URL_RE.search(url)
    if not match:
        return None
    return WorkdayCoordinates(
        tenant=match.group("tenant"),
        data_center=match.group("dc"),
        site=match.group("site"),
    )


def _coordinates(company: CompanyConfig) -> WorkdayCoordinates | None:
    """Resolve coordinates from explicit fields first, then the careers URL."""
    if company.tenant and company.data_center and company.site:
        return WorkdayCoordinates(company.tenant, company.data_center, company.site)
    if company.careers_url:
        return parse_workday_url(company.careers_url)
    return None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class WorkdayConnector(Connector):
    """Fetch full-description postings from a public Workday CXS board."""

    ats = "workday"

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def fetch(self, company: CompanyConfig) -> list[RawJob]:
        coords = _coordinates(company)
        if coords is None:
            logger.warning(
                "Workday company %s is missing tenant/data_center/site "
                "(and careers_url is not a myworkdayjobs URL)",
                company.id,
            )
            return []

        postings = self._fetch_all_postings(coords)
        raw_jobs: list[RawJob] = []
        for posting in postings:
            raw = self._to_raw(coords, posting)
            if raw is not None:
                raw_jobs.append(raw)
        return raw_jobs

    def _fetch_all_postings(self, coords: WorkdayCoordinates) -> list[dict]:
        """List postings; switch to student-keyword search on very large boards.

        A global board (Thales, Airbus, GE Vernova: 2,000+ postings) would need
        one detail request per posting every poll, which is slow and almost
        entirely full-time roles the profile can never take. Above
        ``_LARGE_BOARD`` postings we instead run Workday's own keyword search
        for student terms and union the results, so only plausibly-student
        postings are fetched. Smaller boards are listed in full.
        """
        first = self._search(coords, "", offset=0)
        total = first.get("total") if isinstance(first, dict) else None
        if isinstance(total, int) and total > _LARGE_BOARD:
            seen: dict[str, dict] = {}
            for term in _STUDENT_SEARCH_TERMS:
                for posting in self._search_all(coords, term):
                    seen.setdefault(posting.get("externalPath", ""), posting)
            return [p for key, p in seen.items() if key]
        return self._search_all(coords, "", first_page=first)

    def _search(self, coords: WorkdayCoordinates, text: str, *, offset: int) -> dict:
        response = self.http.request(
            "POST",
            f"{coords.cxs_base}/jobs",
            json_body={
                "limit": _PAGE_LIMIT,
                "offset": offset,
                "appliedFacets": {},
                "searchText": text,
            },
        )
        # CORE RULE #1: a 401/403 (auth wall) must surface, not be worked
        # around, so the company stays a fallback stub. 404 = wrong site.
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _search_all(
        self, coords: WorkdayCoordinates, text: str, *, first_page: dict | None = None
    ) -> list[dict]:
        postings: list[dict] = []
        offset = 0
        payload = first_page if first_page is not None else self._search(
            coords, text, offset=0
        )
        while True:
            page = payload.get("jobPostings") or []
            postings.extend(p for p in page if isinstance(p, dict))
            total = payload.get("total")
            offset += len(page)
            if (
                not page
                or not isinstance(total, int)
                or offset >= total
                or offset >= _MAX_POSTINGS
            ):
                break
            payload = self._search(coords, text, offset=offset)
        return postings

    def _to_raw(
        self, coords: WorkdayCoordinates, posting: dict
    ) -> RawJob | None:
        external_path = posting.get("externalPath")
        if not external_path:
            return None

        # Already stored and still listed: reuse the stored details rather than
        # re-downloading the description (one request per posting per poll was
        # most of the poll time). Title comes from the fresh listing.
        # Local import: normalizer imports the connector package (cycle).
        from jobtracker.normalize.normalizer import split_location

        known = self.known_jobs.get(external_path)
        # Re-fetch once if the stored row predates the country fix (location
        # with no recognisable country), so it gets the detail's country.
        if known is not None and split_location(known.location)[2] is None:
            known = None
        if known is not None:
            return RawJob(
                source_job_id=known.source_job_id,
                title=posting.get("title") or known.title,
                location=known.location,
                description_html=known.description,
                application_url=with_locale(known.application_url),
                source_url=with_locale(known.application_url),
                posted_at=known.posted_at,
                extra={"_reused": True},
            )

        # CORE RULE #1: a board may list postings publicly but auth-wall the
        # per-posting detail endpoint. ``get_json`` does not raise on 401/403,
        # which would silently yield postings with empty descriptions; so we
        # issue the GET via ``request`` and treat a detail-level 401/403 as a
        # source failure too (surfaces to source_health, company stays a stub)
        # rather than emitting a half-empty posting that looks healthy.
        response = self.http.request("GET", f"{coords.cxs_base}{external_path}")
        if response.status_code in (401, 403):
            response.raise_for_status()
        detail = response.json()
        info = {}
        if isinstance(detail, dict):
            maybe = detail.get("jobPostingInfo")
            if isinstance(maybe, dict):
                info = maybe

        public_url = with_locale(
            info.get("externalUrl") or coords.public_url(external_path)
        )
        req_id = self._req_id(posting)
        source_job_id = info.get("id") or req_id or external_path

        location = info.get("location") or posting.get("locationsText") or ""
        # Workday usually gives only a city ("Markham", "Dallas"); the detail's
        # country descriptor is the reliable signal, so append it. Without this
        # every Workday job had an unknown country, which hid it from the
        # Canada/US eligibility rules.
        country = info.get("country")
        country_name = country.get("descriptor") if isinstance(country, dict) else None
        if country_name and country_name.lower() not in location.lower():
            location = f"{location}, {country_name}" if location else country_name
        posted_at = _parse_dt(info.get("startDate"))

        merged = {**posting, "_detail": info}
        return RawJob(
            source_job_id=str(source_job_id),
            title=info.get("title") or posting.get("title", "") or "",
            location=location,
            description_html=info.get("jobDescription") or "",
            application_url=public_url,
            source_url=public_url,
            posted_at=posted_at,
            employment_type_hint=info.get("timeType"),
            extra=merged,
        )

    @staticmethod
    def _req_id(posting: dict) -> str | None:
        bullets = posting.get("bulletFields")
        if isinstance(bullets, list) and bullets:
            return str(bullets[0])
        return None
