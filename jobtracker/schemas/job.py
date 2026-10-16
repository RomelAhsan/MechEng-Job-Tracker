"""Pydantic v2 schema for the canonical job and its enums.

Mirrors the canonical job schema in ``JobAggregateSPEC.md``. These are the
in-memory representation produced by the normalizer; the ORM model in
``jobtracker.db.models`` is the persisted form.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(StrEnum):
    """Lifecycle status of a posting."""

    ACTIVE = "active"
    REMOVED = "removed"
    EXPIRED = "expired"


class TermSeason(StrEnum):
    """Co-op/internship term season."""

    SPRING = "spring"
    SUMMER = "summer"
    FALL = "fall"
    WINTER = "winter"
    UNKNOWN = "unknown"


class EmploymentType(StrEnum):
    """Employment type inferred from title/description."""

    INTERNSHIP = "internship"
    CO_OP = "co-op"
    NEW_GRAD = "new_grad"
    FULL_TIME = "full_time"
    UNKNOWN = "unknown"


class RemoteStatus(StrEnum):
    """On-site / hybrid / remote classification."""

    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class Classification(StrEnum):
    """Mechanical-engineering relevance classification."""

    STRONG_MATCH = "strong_match"
    POSSIBLE_MATCH = "possible_match"
    WEAK_MATCH = "weak_match"
    NOT_RELEVANT = "not_relevant"


class VisaSponsorship(StrEnum):
    """Whether visa sponsorship is provided."""

    PROVIDED = "provided"
    NOT_PROVIDED = "not_provided"
    UNKNOWN = "unknown"


class CanonicalJob(BaseModel):
    """Canonical job posting (see SPEC "CANONICAL JOB SCHEMA")."""

    model_config = ConfigDict(use_enum_values=True)

    job_id: str = ""
    source_job_id: str = ""
    company_id: str = ""
    company_name: str = ""

    title: str = ""
    normalized_title: str = ""
    location: str = ""
    city: str | None = None
    province_state: str | None = None
    country: str | None = None
    remote_status: RemoteStatus = RemoteStatus.UNKNOWN
    employment_type: EmploymentType = EmploymentType.UNKNOWN
    department: str | None = None
    team: str | None = None

    description: str = ""
    requirements: list[str] = Field(default_factory=list)
    preferred_qualifications: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    description_hash: str = ""

    application_url: str | None = None
    source_url: str | None = None
    source_type: str = ""

    posted_at: datetime | None = None
    updated_at: datetime | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    removed_at: datetime | None = None

    status: JobStatus = JobStatus.ACTIVE
    term_season: TermSeason = TermSeason.UNKNOWN
    term_start_date: datetime | None = None
    term_end_date: datetime | None = None
    duration_months: int | None = None
    duration_label: str = ""

    citizenship_requirement: str = ""
    work_authorization_requirement: str = ""
    visa_sponsorship: VisaSponsorship = VisaSponsorship.UNKNOWN
    sponsorship_confidence: float = 0.0
    sponsorship_evidence: str = ""

    classification: Classification | None = None
    fit_score: float = 0.0
    classification_confidence: float = 0.0
    classification_reasons: list[str] = Field(default_factory=list)

    ai_analysis: dict | None = None
    raw_payload: dict | None = None
    eligibility_status: str | None = None
