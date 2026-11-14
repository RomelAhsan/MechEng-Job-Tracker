"""Connector registry and ATS auto-detection from a careers URL."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from jobtracker.connectors.apify_workday import ApifyWorkdayConnector
from jobtracker.connectors.ashby import AshbyConnector
from jobtracker.connectors.base import Connector
from jobtracker.connectors.fallback import FallbackConnector
from jobtracker.connectors.feed import JsonLdConnector, RssConnector, SitemapConnector
from jobtracker.connectors.greenhouse import GreenhouseConnector
from jobtracker.connectors.http import HttpClient
from jobtracker.connectors.lever import LeverConnector
from jobtracker.connectors.smartrecruiters import SmartRecruitersConnector
from jobtracker.connectors.successfactors import SuccessFactorsConnector
from jobtracker.connectors.workable import WorkableConnector, parse_workable_account
from jobtracker.connectors.workday import WorkdayConnector, parse_workday_url
from jobtracker.schemas.company import ATSType

# ats value -> connector class (fallback-eligible types are not listed here).
_SUPPORTED: dict[str, type[Connector]] = {
    ATSType.GREENHOUSE.value: GreenhouseConnector,
    ATSType.LEVER.value: LeverConnector,
    ATSType.ASHBY.value: AshbyConnector,
    ATSType.SMARTRECRUITERS.value: SmartRecruitersConnector,
    ATSType.WORKDAY.value: WorkdayConnector,
    ATSType.WORKABLE.value: WorkableConnector,
    ATSType.SUCCESSFACTORS.value: SuccessFactorsConnector,
    ATSType.RSS.value: RssConnector,
    ATSType.SITEMAP.value: SitemapConnector,
    ATSType.JSON_LD.value: JsonLdConnector,
    ATSType.APIFY_WORKDAY.value: ApifyWorkdayConnector,
}


def get_connector(ats: str, http: HttpClient) -> Connector:
    """Return the connector for ``ats``; unsupported types get the fallback."""
    connector_cls = _SUPPORTED.get(ats)
    if connector_cls is None:
        return FallbackConnector()
    return connector_cls(http)


def is_supported(ats: str) -> bool:
    """True when ``ats`` has a real connector (not the fallback stub)."""
    return ats in _SUPPORTED


@dataclass
class DetectedATS:
    """Result of :func:`detect_ats`: detected type + extracted token."""

    ats: str
    board_token: str | None


# host-substring + path patterns for each supported ATS.
_GREENHOUSE_HOSTS = ("greenhouse.io", "boards.greenhouse.io", "boards-api.greenhouse.io")
_LEVER_HOSTS = ("lever.co", "jobs.lever.co", "api.lever.co")
_ASHBY_HOSTS = ("ashbyhq.com", "jobs.ashbyhq.com", "api.ashbyhq.com")
_SMARTRECRUITERS_HOSTS = ("smartrecruiters.com",)
_WORKDAY_HOSTS = ("myworkdayjobs.com",)
_WORKABLE_HOSTS = ("workable.com",)


def _first_path_segment(path: str) -> str | None:
    segments = [seg for seg in path.split("/") if seg]
    return segments[0] if segments else None


def _greenhouse_token(parts: list[str]) -> str | None:
    # .../boards/{token}/jobs  or  .../{token}
    if "boards" in parts:
        idx = parts.index("boards")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    if "job-boards" in parts:
        idx = parts.index("job-boards")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return parts[0] if parts else None


def _smartrecruiters_token(parts: list[str]) -> str | None:
    # api: .../companies/{id}/postings ; careers: jobs.smartrecruiters.com/{id}
    if "companies" in parts:
        idx = parts.index("companies")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return parts[0] if parts else None


def detect_ats(url: str) -> DetectedATS:
    """Detect the ATS type and board token from a careers ``url``.

    Returns an ``html`` fallback :class:`DetectedATS` when no supported ATS
    domain is recognized.
    """
    split = urlsplit(url if "//" in url else f"https://{url}")
    host = (split.netloc or split.path).lower()
    path = split.path if split.netloc else ""
    parts = [seg for seg in re.split(r"/+", path) if seg]

    if any(h in host for h in _GREENHOUSE_HOSTS):
        return DetectedATS(ATSType.GREENHOUSE.value, _greenhouse_token(parts))
    if any(h in host for h in _LEVER_HOSTS):
        return DetectedATS(ATSType.LEVER.value, _first_path_segment(path))
    if any(h in host for h in _ASHBY_HOSTS):
        return DetectedATS(ATSType.ASHBY.value, _first_path_segment(path))
    if any(h in host for h in _SMARTRECRUITERS_HOSTS):
        return DetectedATS(ATSType.SMARTRECRUITERS.value, _smartrecruiters_token(parts))
    if any(h in host for h in _WORKDAY_HOSTS):
        coords = parse_workday_url(url if "//" in url else f"https://{url}")
        token = f"{coords.tenant}/{coords.data_center}/{coords.site}" if coords else None
        return DetectedATS(ATSType.WORKDAY.value, token)
    if any(h in host for h in _WORKABLE_HOSTS):
        account = parse_workable_account(url if "//" in url else f"https://{url}")
        return DetectedATS(ATSType.WORKABLE.value, account)
    if host.endswith(".rss") or path.endswith(".rss") or "/rss" in path or "/feed" in path:
        return DetectedATS(ATSType.RSS.value, url)
    if "sitemap" in path and path.endswith(".xml"):
        return DetectedATS(ATSType.SITEMAP.value, url)

    return DetectedATS(ATSType.HTML.value, None)
