"""Lever connector.

``GET api.lever.co/v0/postings/{token}?mode=json`` returns a list of postings,
each with the full HTML ``description`` (plus ``lists`` of requirement bullets).
If a posting omits the body we fetch its single-posting endpoint so no
description is lost.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from jobtracker.connectors.base import Connector, RawJob
from jobtracker.connectors.http import HttpClient
from jobtracker.schemas.company import CompanyConfig

logger = logging.getLogger("jobtracker.connectors.lever")

_BASE = "https://api.lever.co/v0/postings"


def _parse_epoch_ms(value: object) -> datetime | None:
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000.0, tz=UTC)
    except (ValueError, OverflowError, OSError):
        return None


def _build_description_html(posting: dict) -> str:
    """Combine the main description with any ``lists`` (requirement groups)."""
    parts: list[str] = []
    description = posting.get("description") or posting.get("descriptionPlain")
    if description:
        parts.append(description)
    for block in posting.get("lists") or []:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        content = block.get("content")
        if text:
            parts.append(f"<h3>{text}</h3>")
        if content:
            parts.append(f"<ul>{content}</ul>")
    closing = posting.get("additional")
    if closing:
        parts.append(closing)
    return "\n".join(parts)


class LeverConnector(Connector):
    """Fetch full-description postings from a public Lever board."""

    ats = "lever"

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def _list_url(self, token: str) -> str:
        return f"{_BASE}/{token}"

    def _detail_url(self, token: str, posting_id: str) -> str:
        return f"{_BASE}/{token}/{posting_id}"

    def fetch(self, company: CompanyConfig) -> list[RawJob]:
        token = company.board_token
        if not token:
            logger.warning("Lever company %s has no board_token", company.id)
            return []

        payload = self.http.get_json(self._list_url(token), params={"mode": "json"})
        postings = payload if isinstance(payload, list) else []

        raw_jobs: list[RawJob] = []
        for posting in postings:
            raw_jobs.append(self._to_raw(token, posting))
        return raw_jobs

    def _to_raw(self, token: str, posting: dict) -> RawJob:
        description_html = _build_description_html(posting)
        if not description_html:
            # Detail call to recover the body when the list omits it.
            detail = self.http.get_json(self._detail_url(token, posting.get("id")))
            if isinstance(detail, dict):
                posting = {**posting, **detail}
                description_html = _build_description_html(posting)

        categories = posting.get("categories")
        location = ""
        department = None
        team = None
        if isinstance(categories, dict):
            location = categories.get("location") or ""
            department = categories.get("department")
            team = categories.get("team")

        apply_url = posting.get("applyUrl")
        hosted_url = posting.get("hostedUrl")
        return RawJob(
            source_job_id=str(posting.get("id", "")),
            title=posting.get("text", "") or "",
            location=location,
            description_html=description_html,
            application_url=apply_url or hosted_url,
            source_url=hosted_url or apply_url,
            posted_at=_parse_epoch_ms(posting.get("createdAt")),
            updated_at=_parse_epoch_ms(posting.get("updatedAt")),
            department=department,
            team=team,
            employment_type_hint=(
                categories.get("commitment") if isinstance(categories, dict) else None
            ),
            extra=posting,
        )
