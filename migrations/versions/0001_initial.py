"""Initial schema: all Phase 0/1 tables plus multi-user scaffolds.

Revision ID: 0001_initial
Revises:
Create Date: hand-authored

Portable column types only so SQLite dev stays PostgreSQL-compatible.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("ats", sa.String(), nullable=False),
        sa.Column("board_token", sa.String(), nullable=True),
        sa.Column("careers_url", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.String(), nullable=True),
        sa.Column("check_interval_minutes", sa.Integer(), nullable=True),
        sa.Column("locations", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "jobs",
        sa.Column("job_id", sa.String(), primary_key=True),
        sa.Column("company_id", sa.String(), nullable=False),
        sa.Column("source_job_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("normalized_title", sa.String(), nullable=True),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("city", sa.String(), nullable=True),
        sa.Column("province_state", sa.String(), nullable=True),
        sa.Column("country", sa.String(), nullable=True),
        sa.Column("remote_status", sa.String(), nullable=True),
        sa.Column("employment_type", sa.String(), nullable=True),
        sa.Column("department", sa.String(), nullable=True),
        sa.Column("team", sa.String(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("requirements", sa.JSON(), nullable=True),
        sa.Column("preferred_qualifications", sa.JSON(), nullable=True),
        sa.Column("skills", sa.JSON(), nullable=True),
        sa.Column("description_hash", sa.String(), nullable=True),
        sa.Column("application_url", sa.String(), nullable=True),
        sa.Column("source_url", sa.String(), nullable=True),
        sa.Column("source_type", sa.String(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("term_season", sa.String(), nullable=True),
        sa.Column("term_start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("term_end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_months", sa.Integer(), nullable=True),
        sa.Column("duration_label", sa.String(), nullable=True),
        sa.Column("citizenship_requirement", sa.String(), nullable=True),
        sa.Column("work_authorization_requirement", sa.String(), nullable=True),
        sa.Column("visa_sponsorship", sa.String(), nullable=True),
        sa.Column("sponsorship_confidence", sa.Float(), nullable=True),
        sa.Column("sponsorship_evidence", sa.Text(), nullable=True),
        sa.Column("classification", sa.String(), nullable=True),
        sa.Column("fit_score", sa.Integer(), nullable=True),
        sa.Column("classification_confidence", sa.Float(), nullable=True),
        sa.Column("classification_reasons", sa.JSON(), nullable=True),
        sa.Column("ai_analysis", sa.JSON(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("eligibility_status", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.UniqueConstraint("company_id", "source_job_id", name="uq_jobs_company_source"),
    )
    op.create_index("ix_jobs_company_id", "jobs", ["company_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_classification", "jobs", ["classification"])
    op.create_index("ix_jobs_term_season", "jobs", ["term_season"])
    op.create_index("ix_jobs_posted_at", "jobs", ["posted_at"])
    op.create_index("ix_jobs_first_seen_at", "jobs", ["first_seen_at"])

    op.create_table(
        "job_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("company_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("old_hash", sa.String(), nullable=True),
        sa.Column("new_hash", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.job_id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
    )

    op.create_table(
        "source_health",
        sa.Column("company_id", sa.String(), primary_key=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_change_detected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_job_count", sa.Integer(), nullable=True),
        sa.Column("new_jobs_last_run", sa.Integer(), nullable=True),
        sa.Column("removed_jobs_last_run", sa.Integer(), nullable=True),
        sa.Column("error_count", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_status", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
    )

    op.create_table(
        "quarantine_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.String(), nullable=False),
        sa.Column("source_job_id", sa.String(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
    )

    # --- Multi-user scaffolds (nullable, unused, not wired to auth) ---
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "saved_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.job_id"]),
    )

    op.create_table(
        "application_status",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.job_id"]),
    )

    op.create_table(
        "user_profiles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("profile_version", sa.Integer(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )

    op.create_table(
        "notification_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.String(), nullable=True),
        sa.Column("channel", sa.String(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.job_id"]),
    )


def downgrade() -> None:
    op.drop_table("notification_log")
    op.drop_table("user_profiles")
    op.drop_table("application_status")
    op.drop_table("saved_jobs")
    op.drop_table("users")
    op.drop_table("quarantine_items")
    op.drop_table("source_health")
    op.drop_table("job_events")
    op.drop_index("ix_jobs_first_seen_at", table_name="jobs")
    op.drop_index("ix_jobs_posted_at", table_name="jobs")
    op.drop_index("ix_jobs_term_season", table_name="jobs")
    op.drop_index("ix_jobs_classification", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_company_id", table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("companies")
