"""ORM models using only portable SQLAlchemy types.

Portable types only (String, Text, Integer, Float, Boolean,
DateTime(timezone=True), JSON) so the SQLite dev database stays
PostgreSQL-compatible.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON as SA_JSON
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from jobtracker.db.base import Base


class Company(Base):
    """A tracked employer / ATS board (design 3.1)."""

    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    ats: Mapped[str] = mapped_column(String, nullable=False)
    board_token: Mapped[str | None] = mapped_column(String, nullable=True)
    careers_url: Mapped[str | None] = mapped_column(String, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[str | None] = mapped_column(String, nullable=True)
    check_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    locations: Mapped[list | None] = mapped_column(SA_JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: US aerospace/defense employer whose roles require US citizenship (ITAR).
    #: When True, non-Canada-located roles are marked ineligible at screen time.
    itar_restricted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Job(Base):
    """Canonical job posting schema (design 3.2)."""

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("company_id", "source_job_id", name="uq_jobs_company_source"),
        Index("ix_jobs_company_id", "company_id"),
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_classification", "classification"),
        Index("ix_jobs_term_season", "term_season"),
        Index("ix_jobs_posted_at", "posted_at"),
        Index("ix_jobs_first_seen_at", "first_seen_at"),
        Index("ix_jobs_eligible", "eligible"),
    )

    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    company_id: Mapped[str] = mapped_column(String, ForeignKey("companies.id"), nullable=False)
    source_job_id: Mapped[str] = mapped_column(String, nullable=False)

    title: Mapped[str | None] = mapped_column(String, nullable=True)
    normalized_title: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    city: Mapped[str | None] = mapped_column(String, nullable=True)
    province_state: Mapped[str | None] = mapped_column(String, nullable=True)
    country: Mapped[str | None] = mapped_column(String, nullable=True)
    remote_status: Mapped[str | None] = mapped_column(String, nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String, nullable=True)
    department: Mapped[str | None] = mapped_column(String, nullable=True)
    team: Mapped[str | None] = mapped_column(String, nullable=True)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    requirements: Mapped[list | None] = mapped_column(SA_JSON, nullable=True)
    preferred_qualifications: Mapped[list | None] = mapped_column(SA_JSON, nullable=True)
    skills: Mapped[list | None] = mapped_column(SA_JSON, nullable=True)
    description_hash: Mapped[str | None] = mapped_column(String, nullable=True)

    application_url: Mapped[str | None] = mapped_column(String, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    source_type: Mapped[str | None] = mapped_column(String, nullable=True)

    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[str | None] = mapped_column(String, nullable=True)
    term_season: Mapped[str | None] = mapped_column(String, nullable=True)
    term_start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    term_end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_label: Mapped[str | None] = mapped_column(String, nullable=True)

    citizenship_requirement: Mapped[str | None] = mapped_column(String, nullable=True)
    work_authorization_requirement: Mapped[str | None] = mapped_column(String, nullable=True)
    visa_sponsorship: Mapped[str | None] = mapped_column(String, nullable=True)
    sponsorship_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    sponsorship_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)

    classification: Mapped[str | None] = mapped_column(String, nullable=True)
    fit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    classification_reasons: Mapped[list | None] = mapped_column(SA_JSON, nullable=True)

    ai_analysis: Mapped[dict | None] = mapped_column(SA_JSON, nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(SA_JSON, nullable=True)
    eligibility_status: Mapped[str | None] = mapped_column(String, nullable=True)

    # --- Stage-1 deterministic hard-eligibility verdict (FEAT-002) ---
    # ``eligible`` is the persisted pass/fail of the deterministic hard filter:
    # False => removed everywhere the user browses, None => un-screened (still
    # visible), True => survived to the AI stage. ``*_reasons/evidence`` carry
    # the human-readable rationale and exact matched quotes; ``failed_rules``
    # holds the machine-readable rule ids. The degree/experience fields are the
    # persisted outputs of the degree_experience extractor used by the verdict
    # and surfaced in the UI.
    eligible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: Which screening level decided the job: "auto" (level-1 intern/co-op in
    #: Canada, no AI needed) or "ai" (ambiguous; shown once the AI confirms).
    screening_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    eligibility_reasons: Mapped[list | None] = mapped_column(SA_JSON, nullable=True)
    eligibility_evidence: Mapped[list | None] = mapped_column(SA_JSON, nullable=True)
    eligibility_failed_rules: Mapped[list | None] = mapped_column(SA_JSON, nullable=True)
    requires_advanced_degree: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    requires_senior_experience: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    min_years_experience: Mapped[int | None] = mapped_column(Integer, nullable=True)


class JobEvent(Base):
    """Append-only detection audit log (design 3.3)."""

    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String, ForeignKey("jobs.job_id"), nullable=False)
    company_id: Mapped[str] = mapped_column(String, ForeignKey("companies.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    old_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    new_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SourceHealth(Base):
    """One row per company tracking fetch health (design 3.4)."""

    __tablename__ = "source_health"

    company_id: Mapped[str] = mapped_column(
        String, ForeignKey("companies.id"), primary_key=True
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_successful_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_change_detected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_job_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_jobs_last_run: Mapped[int | None] = mapped_column(Integer, nullable=True)
    removed_jobs_last_run: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String, nullable=True)


class QuarantineItem(Base):
    """Payloads that failed validation (design 3.5)."""

    __tablename__ = "quarantine_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[str] = mapped_column(String, ForeignKey("companies.id"), nullable=False)
    source_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(SA_JSON, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


# --- Phase 2 multi-user accounts and personalization (design 3.6) ---


class User(Base):
    """A registered dashboard user (Phase 2).

    ``role`` is ``admin`` or ``member``; the first registered user becomes the
    admin (owner of the shared company registry). Passwords are bcrypt-hashed
    and stored only in ``password_hash`` (never plaintext).
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SavedJob(Base):
    """A job hearted/saved by a user (Phase 2)."""

    __tablename__ = "saved_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_saved_jobs_user_job"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String, ForeignKey("jobs.job_id"), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApplicationStatus(Base):
    """A user's application-pipeline state for one job (Phase 2).

    ``status`` is one of saved|researching|applying|submitted|interviewing|
    offer|rejected|closed.
    """

    __tablename__ = "application_status"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_application_status_user_job"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String, ForeignKey("jobs.job_id"), nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserProfile(Base):
    """Per-user profile JSON + version (Phase 2).

    ``data`` holds the serialized :class:`jobtracker.schemas.user_profile.UserProfile`.
    ``profile_version`` is bumped on every edit so Phase 3 AI caches invalidate.
    ``notification_prefs`` holds per-user notification settings (channel, email,
    opt-ins) used by FEAT-005 email subscriptions.
    """

    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    profile_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data: Mapped[dict | None] = mapped_column(SA_JSON, nullable=True)
    notification_prefs: Mapped[dict | None] = mapped_column(SA_JSON, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SavedFilter(Base):
    """A user's named saved filter / subscription (Phase 2 -> FEAT-005).

    Stores the serialized :class:`JobFilters` as ``filter_json``. When
    ``notify`` is set, FEAT-005 emails matching new postings via ``channel``.
    """

    __tablename__ = "saved_filters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    filter_json: Mapped[dict | None] = mapped_column(SA_JSON, nullable=True)
    notify: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    channel: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NotificationLog(Base):
    """Delivery + dedup history for notifications (FEAT-005).

    One row per email actually sent (or written to the outbox). The
    ``(user_id, job_id, channel, rule)`` tuple is the dedup key: the notifier
    never emails the same job for the same rule to the same user twice. ``rule``
    is the SPEC notification rule that fired (e.g. ``strong_match``,
    ``source_failure``), and for source-level notifications ``job_id`` is NULL
    and ``company_id`` identifies the affected source. ``to_email`` records the
    destination for history, and ``status`` is ``sent`` or ``outbox``.
    """

    __tablename__ = "notification_log"
    __table_args__ = (
        Index("ix_notification_log_user", "user_id"),
        Index("ix_notification_log_dedup", "user_id", "job_id", "channel", "rule"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String, ForeignKey("jobs.job_id"), nullable=True)
    company_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("companies.id"), nullable=True
    )
    channel: Mapped[str | None] = mapped_column(String, nullable=True)
    rule: Mapped[str | None] = mapped_column(String, nullable=True)
    subject: Mapped[str | None] = mapped_column(String, nullable=True)
    to_email: Mapped[str | None] = mapped_column(String, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)


# --- Phase 3 AI relevance engine (design 3.7) ---


class AiAnalysisCache(Base):
    """Cached AI relevance analysis for a job, keyed for invalidation (Phase 3).

    The cache key is ``(job_id, description_hash, profile_version)`` so a cached
    row is reused only while the job description AND the user's profile are
    unchanged; editing the profile (which bumps ``profile_version``) or a
    changed description invalidates it and triggers a fresh analysis. ``result``
    holds the parsed strict-JSON SPEC fields; ``confidence`` mirrors the parsed
    confidence for quick ranking. Token usage is stored for cost visibility.
    ``overridden`` marks a manual override of the AI classification.
    """

    __tablename__ = "ai_analysis_cache"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "description_hash",
            "profile_version",
            name="uq_ai_cache_key",
        ),
        Index("ix_ai_cache_job", "job_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("jobs.job_id"), nullable=True
    )
    description_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    profile_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[dict | None] = mapped_column(SA_JSON, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    overridden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --- Phase 4 Resume Studio (design 3.8) ---


class MasterResume(Base):
    """A user's uploaded master resume (Phase 4, SPEC CORE RULE #7/#8).

    The ORIGINAL uploaded file is preserved untouched: its bytes are stored
    base64-encoded in ``original_base64`` (a portable Text column, so the SQLite
    dev DB stays PostgreSQL-compatible and no native BLOB handling is needed).
    ``parsed_sections`` holds the structured SPEC sections extracted by
    :mod:`jobtracker.resume.parser`; ``raw_text`` is the extracted plain text.
    Tailored resumes are always generated from this grounded structure, never
    from invented content.
    """

    __tablename__ = "master_resumes"
    __table_args__ = (Index("ix_master_resumes_user", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String, nullable=True)
    original_base64: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_sections: Mapped[dict | None] = mapped_column(SA_JSON, nullable=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GeneratedResume(Base):
    """A tailored resume generated for one saved job (Phase 4).

    Stores the Markdown source, the structured JSON sections, and the ATS PDF
    (base64 in a portable Text column). ``change_log`` records what was
    reordered/emphasized and ``missing_keywords`` lists job keywords the user
    lacks evidence for (suggestions only, never auto-inserted). Every stored
    draft has passed the deterministic ``verify_claims`` fact-preservation gate.
    """

    __tablename__ = "generated_resumes"
    __table_args__ = (
        Index("ix_generated_resumes_user", "user_id"),
        Index("ix_generated_resumes_job", "job_id"),
        UniqueConstraint(
            "user_id", "job_id", "master_resume_id", name="uq_generated_resume_key"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String, ForeignKey("jobs.job_id"), nullable=True)
    master_resume_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("master_resumes.id"), nullable=True
    )
    markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    json_sections: Mapped[dict | None] = mapped_column(SA_JSON, nullable=True)
    pdf_base64: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_log: Mapped[list | None] = mapped_column(SA_JSON, nullable=True)
    missing_keywords: Mapped[list | None] = mapped_column(SA_JSON, nullable=True)
    matched_keywords: Mapped[list | None] = mapped_column(SA_JSON, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
