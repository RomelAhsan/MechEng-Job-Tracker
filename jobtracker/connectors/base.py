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


@dataclass
class KnownJob:
    """A posting already stored for this company (lets connectors skip work).

    Connectors that need one extra request per posting (Workday) reuse these
    stored details for postings that are still listed, instead of
    re-downloading every description on every poll.
    """

    source_job_id: str
    title: str
    location: str
    description: str
    application_url: str | None
    posted_at: datetime | None


class Connector(ABC):
    """Abstract base for all ATS connectors."""

    #: ATS identifier this connector handles (matches ``ATSType`` value).
    ats: str
    #: Stored postings keyed by the "/job/..." path of their apply link. Set by
    #: the ingest service before ``fetch``; connectors may ignore it.
    known_jobs: dict[str, KnownJob] = {}
    #: The same stored postings keyed by ``source_job_id`` and by apply URL, for
    #: connectors that need one detail request per posting (Oracle, Radancy,
    #: Phenom): a posting that is still listed reuses its stored description.
    known_by_id: dict[str, KnownJob] = {}
    known_by_url: dict[str, KnownJob] = {}
    #: Optional cap on how many postings ``fetch`` should return for a single
    #: call (``None`` = unbounded). Set by the ingest service only for the
    #: one-time add-time poll so a huge board cannot hold the add; connectors
    #: that paginate or issue a per-posting detail request should stop once this
    #: many postings have been collected. The scheduled poll leaves it ``None``.
    max_postings: int | None = None
    #: Most NEW postings to download details for in one fetch (None = no
    #: limit). New postings past the limit are left out of this fetch; they
    #: are not stored yet, so leaving them out removes nothing.
    max_new_details: int | None = None
    #: How many new-posting detail downloads this fetch has made.
    new_details_fetched: int = 0

    def new_detail_allowed(self) -> bool:
        """Count one new-posting download; False once the limit is reached."""
        if self.max_new_details is not None and self.new_details_fetched >= self.max_new_details:
            return False
        self.new_details_fetched += 1
        return True

    @abstractmethod
    def fetch(self, company: CompanyConfig) -> list[RawJob]:
        """Fetch all postings for ``company`` as :class:`RawJob` records.

        Implementations must fetch the FULL description for each posting and
        must never raise for an empty board (return an empty list instead).
        """
        raise NotImplementedError
