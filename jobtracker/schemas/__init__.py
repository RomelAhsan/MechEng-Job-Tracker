"""Pydantic v2 schemas for jobs, companies, and the user profile."""

from __future__ import annotations

from jobtracker.schemas.company import ATSType, CompanyConfig, Priority
from jobtracker.schemas.job import (
    CanonicalJob,
    Classification,
    EmploymentType,
    JobStatus,
    RemoteStatus,
    TermSeason,
    VisaSponsorship,
)
from jobtracker.schemas.user_profile import UserProfile

__all__ = [
    "ATSType",
    "CanonicalJob",
    "Classification",
    "CompanyConfig",
    "EmploymentType",
    "JobStatus",
    "Priority",
    "RemoteStatus",
    "TermSeason",
    "UserProfile",
    "VisaSponsorship",
]
