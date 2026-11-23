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
from jobtracker.connectors.workday_targeting import (
    MIN_STUDENT_SHARE,
    Query,
    TargetingPlan,
    build_plan,
    student_share,
)
from jobtracker.normalize.posting_date import parse_relative_posted
from jobtracker.schemas.company import CompanyConfig

logger = logging.getLogger("jobtracker.connectors.workday")

_PAGE_LIMIT = 20
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
        #: The targeting plan used by the last fetch (None before any fetch).
        self.last_plan: TargetingPlan | None = None

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

    def _new_detail_ok(self) -> bool:
        return self.new_detail_allowed()

    def _fetch_all_postings(self, coords: WorkdayCoordinates) -> list[dict]:
        """List the board's student postings in Canada/US (or the whole board).

        See :mod:`jobtracker.connectors.workday_targeting`: small boards are
        listed in full; larger ones are searched with the board's own student
        worker-type filter plus precise keywords, both restricted to Canada and
        the US. Results are unioned by ``externalPath``. The plan used is kept
        on ``self.last_plan`` so callers can cache it.
        """
        first = self._search(coords, Query({}, ""), offset=0)
        plan = build_plan(first)
        self.last_plan = plan
        if plan.full_board:
            return self._search_all(coords, Query({}, ""), first_page=first)

        seen: dict[str, dict] = {}
        for query in plan.queries():
            page = self._search(coords, query, offset=0)
            total = page.get("total")
            titles = [str(p.get("title") or "") for p in page.get("jobPostings") or []]
            if query.search_text and plan.skip_keyword(total, titles):
                logger.info(
                    "Workday %s: keyword %r returns %s mostly non-student postings; "
                    "skipped.",
                    coords.tenant,
                    query.search_text,
                    total,
                )
                continue
            for posting in self._search_all(
                coords, query, first_page=page, stop_when_not_student=bool(query.search_text)
            ):
                path = posting.get("externalPath")
                if path:
                    seen.setdefault(path, posting)
        return list(seen.values())

    def _search(self, coords: WorkdayCoordinates, query: Query, *, offset: int) -> dict:
        response = self.http.request(
            "POST",
            f"{coords.cxs_base}/jobs",
            json_body=query.body(limit=_PAGE_LIMIT, offset=offset),
        )
        # CORE RULE #1: a 401/403 (auth wall) must surface, not be worked
        # around, so the company stays a fallback stub. 404 = wrong site.
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _search_all(
        self,
        coords: WorkdayCoordinates,
        query: Query,
        *,
        first_page: dict | None = None,
        stop_when_not_student: bool = False,
    ) -> list[dict]:
        """Page through one search.

        ``stop_when_not_student`` (keyword searches): Workday ranks the best
        matches first, so paging stops once a page is mostly non-student roles.
        """
        postings: list[dict] = []
        offset = 0
        payload = first_page if first_page is not None else self._search(
            coords, query, offset=0
        )
        # Workday reports the result count only on the FIRST page; later pages
        # say "total": 0. Reading the total from every page stopped each search
        # after 40 postings, so larger searches silently lost the rest.
        total = payload.get("total")
        while True:
            page = payload.get("jobPostings") or []
            postings.extend(p for p in page if isinstance(p, dict))
            offset += len(page)
            titles = [str(p.get("title") or "") for p in page if isinstance(p, dict)]
            if (
                stop_when_not_student
                and offset > _PAGE_LIMIT
                and student_share(titles) < MIN_STUDENT_SHARE
            ):
                break
            if (
                not page
                or not isinstance(total, int)
                or offset >= total
                or offset >= _MAX_POSTINGS
            ):
                break
            payload = self._search(coords, query, offset=offset)
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

        if not self._new_detail_ok():
            return None  # left for the next run (detail limit)
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
        # The TRUE posting date matters for the freshness cutoff and the
        # "Posted X days ago" label. Workday's detail ``startDate`` is the
        # absolute posting date when present; otherwise the list posting only
        # carries a relative "Posted N Days Ago"/"Posted Today"/"Posted
        # Yesterday" string ("Posted 30+ Days Ago" counts as 30), which we turn
        # into an absolute date relative to the fetch time.
        posted_at = _parse_dt(info.get("startDate")) or parse_relative_posted(
            posting.get("postedOn") or info.get("postedOn")
        )

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
