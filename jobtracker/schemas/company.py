"""Pydantic v2 schema for a company / ATS-board configuration entry."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ATSType(StrEnum):
    """Supported and fallback applicant-tracking-system types.

    ``greenhouse``/``lever``/``ashby``/``smartrecruiters``/``workday``/
    ``workable``/``rss``/``sitemap``/``json_ld`` have real connectors. ``html``
    routes to the fail-soft fallback stub.
    """

    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    SMARTRECRUITERS = "smartrecruiters"
    WORKDAY = "workday"
    WORKABLE = "workable"
    #: SAP SuccessFactors Career Site Builder; board_token = career-site host.
    SUCCESSFACTORS = "successfactors"
    RSS = "rss"
    SITEMAP = "sitemap"
    JSON_LD = "json_ld"
    #: Workday board fetched via a free Apify Actor (for JS-locked sites whose
    #: tenant the direct connector cannot discover). Requires APIFY_TOKEN.
    APIFY_WORKDAY = "apify_workday"
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
    # US aerospace/defense employers whose roles require US citizenship (ITAR/
    # EAR) regardless of whether a given posting states it. When True, every
    # non-Canada-located role from this company is marked ineligible for the
    # Canadian-citizen profile (only Canada-located roles survive).
    itar_restricted: bool = False
    # Workday needs tenant + data_center (wd1/wd3/wd5...) + site id; these stay
    # optional so existing YAML without them remains valid. When absent the
    # connectors parse the identifiers from the ``*.myworkdayjobs.com``
    # ``careers_url`` instead.
    tenant: str | None = None
    data_center: str | None = None
    site: str | None = None

    @field_validator("id")
    @classmethod
    def _non_empty_id(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("company id must be a non-empty string")
        return value
