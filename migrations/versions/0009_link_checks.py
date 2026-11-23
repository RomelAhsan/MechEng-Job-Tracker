"""Link checks, rule audits, and source failure streaks.

Revision ID: 0009_link_checks
Revises: 0008_screening_tier
Create Date: hand-authored

Adds to ``jobs``: ``link_status`` / ``link_checked_at`` / ``link_check_reason``
(the "is this posting still open?" check) and ``rule_audit`` (AI spot-check of
a rule decision). Adds to ``source_health``: ``consecutive_failures``,
``failing_since`` and ``last_fetch_via``. All nullable, so plain add_column
works on SQLite.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_link_checks"
down_revision: str | None = "0008_screening_tier"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("link_status", sa.String(), nullable=True))
    op.add_column(
        "jobs", sa.Column("link_checked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("jobs", sa.Column("link_check_reason", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("rule_audit", sa.JSON(), nullable=True))
    op.add_column(
        "source_health", sa.Column("consecutive_failures", sa.Integer(), nullable=True)
    )
    op.add_column(
        "source_health",
        sa.Column("failing_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("source_health", sa.Column("last_fetch_via", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("source_health", "last_fetch_via")
    op.drop_column("source_health", "failing_since")
    op.drop_column("source_health", "consecutive_failures")
    op.drop_column("jobs", "rule_audit")
    op.drop_column("jobs", "link_check_reason")
    op.drop_column("jobs", "link_checked_at")
    op.drop_column("jobs", "link_status")
