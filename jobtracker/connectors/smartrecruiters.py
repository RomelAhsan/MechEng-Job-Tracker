"""SmartRecruiters connector.

``GET api.smartrecruiters.com/v1/companies/{id}/postings`` is paginated via
``offset``/``limit``/``totalFound`` and the list entries DO NOT include the job
description body. For every posting we issue a per-posting detail call
(``.../postings/{postingId}``) and assemble the full description from the job-ad
sections so no career-page context is lost.
"""

from __future__ import annotations

import logging
from datetime import datetime

from jobtracker.connectors.base import Connector, RawJob
from jobtracker.connectors.http import HttpClient
from jobtracker.schemas.company import CompanyConfig

logger = logging.getLogger("jobtracker.connectors.smartrecruiters")

_BASE = "https://api.smartrecruiters.com/v1/companies"
_PAGE_LIMIT = 100


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _assemble_description(detail: dict) -> str:
    """Build full HTML from a posting detail's job-ad sections."""
    job_ad = detail.get("jobAd")
    if not isinstance(job_ad, dict):
        return ""
    sections = job_ad.get("sections")
    if not isinstance(sections, dict):
        return ""
    parts: list[str] = []
    # SPEC-relevant order: company, job description, qualifications, additional.
    for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
        section = sections.get(key)
        if isinstance(section, dict):
            title = section.get("title")
            text = section.get("text")
            if title:
                parts.append(f"<h3>{title}</h3>")
            if text:
                parts.append(text)
    return "\n".join(parts)


class SmartRecruitersConnector(Connector):
    """Fetch full-description postings from a public SmartRecruiters board."""

    ats = "smartrecruiters"

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def _list_url(self, company_id: str) -> str:
        return f"{_BASE}/{company_id}/postings"

    def _detail_url(self, company_id: str, posting_id: str) -> str:
        return f"{_BASE}/{company_id}/postings/{posting_id}"

    def fetch(self, company: CompanyConfig) -> list[RawJob]:
        identifier = company.board_token
        if not identifier:
            logger.warning("SmartRecruiters company %s has no board_token", company.id)
            return []

        postings = self._fetch_all_postings(identifier)
        raw_jobs: list[RawJob] = []
        for posting in postings:
            raw_jobs.append(self._to_raw(identifier, posting))
        return raw_jobs

    def _fetch_all_postings(self, identifier: str) -> list[dict]:
        postings: list[dict] = []
        offset = 0
        while True:
            payload = self.http.get_json(
                self._list_url(identifier),
                params={"offset": offset, "limit": _PAGE_LIMIT},
            )
            if not isinstance(payload, dict):
                break
            page = payload.get("content") or []
            postings.extend(page)
            total = payload.get("totalFound", len(postings))
            offset += len(page)
            if not page or offset >= total:
                break
        return postings

    def _to_raw(self, identifier: str, posting: dict) -> RawJob:
        posting_id = posting.get("id")
        # The list omits the body: always fetch the detail for the full text.
        detail = self.http.get_json(self._detail_url(identifier, posting_id))
        detail = detail if isinstance(detail, dict) else {}
        description_html = _assemble_description(detail)

        location = ""
        loc = posting.get("location")
        if isinstance(loc, dict):
            location = loc.get("fullLocation") or self._compose_location(loc)

        apply_url = detail.get("applyUrl") or posting.get("applyUrl")
        source_url = f"https://jobs.smartrecruiters.com/{identifier}/{posting_id}"

        function = posting.get("function")
        department = function.get("label") if isinstance(function, dict) else None
        type_of_employment = posting.get("typeOfEmployment")
        employment_hint = (
            type_of_employment.get("label")
            if isinstance(type_of_employment, dict)
            else None
        )

        merged = {**posting, "_detail": detail}
        return RawJob(
            source_job_id=str(posting_id or ""),
            title=posting.get("name", "") or "",
            location=location,
            description_html=description_html,
            application_url=apply_url or source_url,
            source_url=source_url,
            posted_at=_parse_dt(posting.get("releasedDate")),
            department=department,
            employment_type_hint=employment_hint,
            extra=merged,
        )

    @staticmethod
    def _compose_location(loc: dict) -> str:
        bits = [loc.get("city"), loc.get("region"), loc.get("country")]
        return ", ".join(bit for bit in bits if bit)
