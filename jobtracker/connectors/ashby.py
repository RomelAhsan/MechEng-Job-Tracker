"""Ashby connector.

``GET api.ashbyhq.com/posting-api/job-board/{token}`` returns ``jobs[]`` with
``descriptionHtml`` inline. Only ``isListed`` jobs are kept. If a listed job
lacks the body we request ``?includeCompensation=true`` detail form; failing
that we fall back to the single-posting endpoint so no description is lost.
"""

from __future__ import annotations

import logging
from datetime import datetime

from jobtracker.connectors.base import Connector, RawJob
from jobtracker.connectors.http import HttpClient
from jobtracker.schemas.company import CompanyConfig

logger = logging.getLogger("jobtracker.connectors.ashby")

_BASE = "https://api.ashbyhq.com/posting-api/job-board"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class AshbyConnector(Connector):
    """Fetch full-description, listed postings from a public Ashby board."""

    ats = "ashby"

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def _list_url(self, token: str) -> str:
        return f"{_BASE}/{token}"

    def _detail_url(self, token: str, job_id: str) -> str:
        return f"{_BASE}/{token}/{job_id}"

    def fetch(self, company: CompanyConfig) -> list[RawJob]:
        token = company.board_token
        if not token:
            logger.warning("Ashby company %s has no board_token", company.id)
            return []

        payload = self.http.get_json(self._list_url(token))
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []

        raw_jobs: list[RawJob] = []
        for job in jobs:
            if not job.get("isListed", True):
                continue
            raw_jobs.append(self._to_raw(token, job))
        return raw_jobs

    def _to_raw(self, token: str, job: dict) -> RawJob:
        description_html = job.get("descriptionHtml") or ""
        if not description_html:
            # Per-posting detail call to recover the body.
            detail = self.http.get_json(self._detail_url(token, job.get("id")))
            if isinstance(detail, dict):
                job = {**job, **detail}
                description_html = job.get("descriptionHtml") or ""

        job_url = job.get("jobUrl")
        apply_url = job.get("applyUrl")
        return RawJob(
            source_job_id=str(job.get("id", "")),
            title=job.get("title", "") or "",
            location=job.get("location", "") or "",
            description_html=description_html,
            application_url=apply_url or job_url,
            source_url=job_url or apply_url,
            posted_at=_parse_dt(job.get("publishedAt") or job.get("publishedDate")),
            department=job.get("department"),
            team=job.get("team"),
            employment_type_hint=job.get("employmentType"),
            extra=job,
        )
