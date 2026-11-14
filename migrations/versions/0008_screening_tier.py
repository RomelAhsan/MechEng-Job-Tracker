"""Persist which screening level decided each job.

Revision ID: 0008_screening_tier
Revises: 0007_company_itar
Create Date: hand-authored

Adds ``jobs.screening_tier``: "auto" for level-1 student roles in Canada that
need no AI check, "ai" for ambiguous survivors that are shown only after the AI
confirms them. Nullable, so a plain add_column works on SQLite.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_screening_tier"
down_revision: str | None = "0007_company_itar"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("screening_tier", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "screening_tier")
