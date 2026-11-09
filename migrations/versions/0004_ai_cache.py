"""FEAT-006: AI relevance analysis cache (Phase 3).

Revision ID: 0004_ai_cache
Revises: 0003_notifications
Create Date: hand-authored

Adds the ``ai_analysis_cache`` table used by the Phase 3 AI relevance engine.
Each row is keyed by ``(job_id, description_hash, profile_version)`` so a cached
analysis is reused only while both the job description and the user's profile
are unchanged; editing the profile bumps ``profile_version`` and a changed
posting changes ``description_hash``, either of which invalidates the cache.
Portable column types only so SQLite dev stays PostgreSQL-compatible.

This is the latest revision; later features chain their migration as ``0005_*``
with ``down_revision = "0004_ai_cache"``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_ai_cache"
down_revision: str | None = "0003_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_analysis_cache",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(), nullable=True),
        sa.Column("description_hash", sa.String(), nullable=True),
        sa.Column("profile_version", sa.Integer(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("provider", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "overridden", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.job_id"]),
        sa.UniqueConstraint(
            "job_id",
            "description_hash",
            "profile_version",
            name="uq_ai_cache_key",
        ),
    )
    op.create_index("ix_ai_cache_job", "ai_analysis_cache", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_cache_job", table_name="ai_analysis_cache")
    op.drop_table("ai_analysis_cache")
