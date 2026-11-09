"""FEAT-007: Resume Studio tables (Phase 4).

Revision ID: 0005_resume
Revises: 0004_ai_cache
Create Date: hand-authored

Adds the ``master_resumes`` and ``generated_resumes`` tables for the Phase 4
Resume Studio. The ORIGINAL uploaded file is preserved untouched as a
base64-encoded Text blob (portable - no native BLOB handling) so the SQLite dev
DB stays PostgreSQL-compatible. Tailored resumes are generated from the parsed,
grounded structure and always pass the deterministic ``verify_claims``
fact-preservation gate before storage.

This is the latest revision; later features chain their migration as ``0006_*``
with ``down_revision = "0005_resume"``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_resume"
down_revision: str | None = "0004_ai_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "master_resumes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("original_filename", sa.String(), nullable=True),
        sa.Column("content_type", sa.String(), nullable=True),
        sa.Column("original_base64", sa.Text(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("parsed_sections", sa.JSON(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("ix_master_resumes_user", "master_resumes", ["user_id"])

    op.create_table(
        "generated_resumes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.String(), nullable=True),
        sa.Column("master_resume_id", sa.Integer(), nullable=True),
        sa.Column("markdown", sa.Text(), nullable=True),
        sa.Column("json_sections", sa.JSON(), nullable=True),
        sa.Column("pdf_base64", sa.Text(), nullable=True),
        sa.Column("change_log", sa.JSON(), nullable=True),
        sa.Column("missing_keywords", sa.JSON(), nullable=True),
        sa.Column("matched_keywords", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.job_id"]),
        sa.ForeignKeyConstraint(["master_resume_id"], ["master_resumes.id"]),
        sa.UniqueConstraint(
            "user_id", "job_id", "master_resume_id", name="uq_generated_resume_key"
        ),
    )
    op.create_index("ix_generated_resumes_user", "generated_resumes", ["user_id"])
    op.create_index("ix_generated_resumes_job", "generated_resumes", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_generated_resumes_job", table_name="generated_resumes")
    op.drop_index("ix_generated_resumes_user", table_name="generated_resumes")
    op.drop_table("generated_resumes")
    op.drop_index("ix_master_resumes_user", table_name="master_resumes")
    op.drop_table("master_resumes")
