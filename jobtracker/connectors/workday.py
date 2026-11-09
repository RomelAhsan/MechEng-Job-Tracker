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
        return f"{self.base}/{self.site}{external_path}"


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
        url = f"{coords.cxs_base}/jobs"
        postings: list[dict] = []
        offset = 0
        while True:
            response = self.http.request(
                "POST",
                url,
                json_body={
                    "limit": _PAGE_LIMIT,
                    "offset": offset,
                    "appliedFacets": {},
                    "searchText": "",
                },
            )
            # CORE RULE #1: a 401/403 (auth wall) must surface, not be worked
            # around, so the company stays a fallback stub. 404 = wrong site.
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                break
            page = payload.get("jobPostings") or []
            postings.extend(p for p in page if isinstance(p, dict))
            total = payload.get("total")
            offset += len(page)
            if not page or not isinstance(total, int) or offset >= total:
                break
        return postings

    def _to_raw(
        self, coords: WorkdayCoordinates, posting: dict
    ) -> RawJob | None:
        external_path = posting.get("externalPath")
        if not external_path:
            return None

        detail = self.http.get_json(f"{coords.cxs_base}{external_path}")
        info = {}
        if isinstance(detail, dict):
            maybe = detail.get("jobPostingInfo")
            if isinstance(maybe, dict):
                info = maybe

        public_url = info.get("externalUrl") or coords.public_url(external_path)
        req_id = self._req_id(posting)
        source_job_id = info.get("id") or req_id or external_path

        location = info.get("location") or posting.get("locationsText") or ""
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
