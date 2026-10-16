"""Greenhouse connector.

``GET boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`` returns all
jobs with the full HTML description inline (``content``). If a job is missing
``content`` we fetch its detail endpoint so no description is lost.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime
from typing import Any

from jobtracker.connectors.base import Connector, RawJob
from jobtracker.connectors.http import HttpClient
from jobtracker.schemas.company import CompanyConfig

logger = logging.getLogger("jobtracker.connectors.greenhouse")

_BASE = "https://boards-api.greenhouse.io/v1/boards"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class GreenhouseConnector(Connector):
    """Fetch full-description postings from a public Greenhouse board."""

    ats = "greenhouse"

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def _list_url(self, token: str) -> str:
        return f"{_BASE}/{token}/jobs"

    def _detail_url(self, token: str, job_id: Any) -> str:
        return f"{_BASE}/{token}/jobs/{job_id}"

    def fetch(self, company: CompanyConfig) -> list[RawJob]:
        token = company.board_token
        if not token:
            logger.warning("Greenhouse company %s has no board_token", company.id)
            return []

        payload = self.http.get_json(self._list_url(token), params={"content": "true"})
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []

        raw_jobs: list[RawJob] = []
        for job in jobs:
            raw_jobs.append(self._to_raw(token, job))
        return raw_jobs

    def _to_raw(self, token: str, job: dict) -> RawJob:
        content = job.get("content")
        if not content:
            # List omitted the body: issue a per-posting detail call.
            detail = self.http.get_json(self._detail_url(token, job.get("id")))
            if isinstance(detail, dict):
                job = {**job, **detail}
                content = job.get("content") or ""

        location = ""
        loc = job.get("location")
        if isinstance(loc, dict):
            location = loc.get("name") or ""

        # Greenhouse HTML-entity-encodes the content field; unescape to real HTML.
        description_html = html.unescape(content) if content else ""

        url = job.get("absolute_url")
        return RawJob(
            source_job_id=str(job.get("id", "")),
            title=job.get("title", "") or "",
            location=location,
            description_html=description_html,
            application_url=url,
            source_url=url,
            updated_at=_parse_dt(job.get("updated_at")),
            posted_at=_parse_dt(job.get("first_published")),
            department=self._first_department(job),
            extra=job,
        )

    @staticmethod
    def _first_department(job: dict) -> str | None:
        departments = job.get("departments")
        if isinstance(departments, list) and departments:
            first = departments[0]
            if isinstance(first, dict):
                return first.get("name")
        return None
