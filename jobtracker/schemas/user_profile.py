"""Pydantic v2 schema for the single-user profile from ``config/user.yaml``."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class UserProfile(BaseModel):
    """User profile block (see SPEC "USER PROFILE").

    In Phase 1 a single implicit user is loaded from ``config/user.yaml``.
    ``profile_version`` is tracked so Phase 3 AI caches can invalidate.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    email: str = ""
    university: str = "University of Waterloo"
    program: str = "Mechanical Engineering"
    level: str = "undergraduate"
    year: int = 1
    expected_graduation_year: int | None = None

    citizenship: list[str] = Field(default_factory=list)
    work_authorization: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    acceptable_locations_with_sponsorship: list[str] = Field(default_factory=list)

    available_terms: list[str] = Field(default_factory=list)
    preferred_duration_months: int | None = None
    minimum_duration_months: int | None = None
    maximum_duration_months: int | None = None

    interests: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)

    profile_version: int = 1
