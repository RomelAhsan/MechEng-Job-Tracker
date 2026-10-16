"""Connector contract: the :class:`RawJob` dataclass and :class:`Connector` ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from jobtracker.schemas.company import CompanyConfig


@dataclass
class RawJob:
    """A single posting as fetched from an ATS, before normalization.

    ``description_html`` holds the FULL job description body (connectors issue
    per-posting detail calls when the list endpoint omits it). ``extra`` keeps
    the raw provider payload so parsing failures can be debugged.
    """

    source_job_id: str
    title: str
    location: str = ""
    description_html: str = ""
    application_url: str | None = None
    source_url: str | None = None
    posted_at: datetime | None = None
    updated_at: datetime | None = None
    department: str | None = None
    team: str | None = None
    employment_type_hint: str | None = None
    extra: dict = field(default_factory=dict)


class Connector(ABC):
    """Abstract base for all ATS connectors."""

    #: ATS identifier this connector handles (matches ``ATSType`` value).
    ats: str

    @abstractmethod
    def fetch(self, company: CompanyConfig) -> list[RawJob]:
        """Fetch all postings for ``company`` as :class:`RawJob` records.

        Implementations must fetch the FULL description for each posting and
        must never raise for an empty board (return an empty list instead).
        """
        raise NotImplementedError
