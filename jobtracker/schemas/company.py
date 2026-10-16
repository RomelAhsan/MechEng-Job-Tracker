"""Pydantic v2 schema for a company / ATS-board configuration entry."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ATSType(StrEnum):
    """Supported and fallback applicant-tracking-system types.

    ``greenhouse``/``lever``/``ashby``/``smartrecruiters`` have real connectors.
    The remaining values route to the fail-soft fallback stub.
    """

    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    SMARTRECRUITERS = "smartrecruiters"
    WORKABLE = "workable"
    RSS = "rss"
    SITEMAP = "sitemap"
    JSON_LD = "json_ld"
    HTML = "html"


class Priority(StrEnum):
    """Seeding priority heuristic."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CompanyConfig(BaseModel):
    """A tracked employer / ATS board as declared in ``companies.yaml``."""

    model_config = ConfigDict(use_enum_values=True)

    id: str
    name: str
    ats: ATSType
    board_token: str | None = None
    careers_url: str | None = None
    enabled: bool = True
    priority: Priority = Priority.MEDIUM
    check_interval_minutes: int | None = None
    locations: list[str] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("id")
    @classmethod
    def _non_empty_id(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("company id must be a non-empty string")
        return value
