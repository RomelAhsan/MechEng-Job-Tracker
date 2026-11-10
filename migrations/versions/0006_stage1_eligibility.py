"""FEAT-002: Stage-1 deterministic hard-eligibility verdict columns.

Revision ID: 0006_stage1_eligibility
Revises: 0005_resume
Create Date: hand-authored

Adds the persisted Stage-1 verdict + degree/experience columns to the ``jobs``
table and an index on ``eligible`` so the dashboard can hard-exclude
disqualifying jobs cheaply at SQL level. All adds are nullable so the plain
``op.add_column`` form works on SQLite (no table rebuild needed). Portable
column types only (Boolean / JSON / Integer) so SQLite dev stays
PostgreSQL-compatible.

This is the latest revision; later features chain their migration as ``0007_*``
with ``down_revision = "0006_stage1_eligibility"``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_stage1_eligibility"
down_revision: str | None = "0005_resume"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("eligible", sa.Boolean(), nullable=True))
    op.add_column("jobs", sa.Column("eligibility_reasons", sa.JSON(), nullable=True))
    op.add_column("jobs", sa.Column("eligibility_evidence", sa.JSON(), nullable=True))
    op.add_column(
        "jobs", sa.Column("eligibility_failed_rules", sa.JSON(), nullable=True)
    )
    op.add_column(
        "jobs", sa.Column("requires_advanced_degree", sa.Boolean(), nullable=True)
    )
    op.add_column(
        "jobs", sa.Column("requires_senior_experience", sa.Boolean(), nullable=True)
    )
    op.add_column(
        "jobs", sa.Column("min_years_experience", sa.Integer(), nullable=True)
    )
    op.create_index("ix_jobs_eligible", "jobs", ["eligible"])


def downgrade() -> None:
    op.drop_index("ix_jobs_eligible", table_name="jobs")
    op.drop_column("jobs", "min_years_experience")
    op.drop_column("jobs", "requires_senior_experience")
    op.drop_column("jobs", "requires_advanced_degree")
    op.drop_column("jobs", "eligibility_failed_rules")
    op.drop_column("jobs", "eligibility_evidence")
    op.drop_column("jobs", "eligibility_reasons")
    op.drop_column("jobs", "eligible")
