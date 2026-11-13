"""Company-level ITAR restriction flag.

Revision ID: 0007_company_itar
Revises: 0006_stage1_eligibility
Create Date: hand-authored

Adds ``companies.itar_restricted``. US aerospace/defense employers (SpaceX,
Lockheed, Northrop, ...) require US citizenship by law on essentially every
role, whether or not an individual posting states it. When this flag is set,
screening marks every non-Canada-located role from that company ineligible for
the Canadian-citizen profile (only Canada-located roles survive) -- a
deterministic rule that does not depend on per-posting text or the AI stage.

Added with a server_default of 0 so existing rows backfill to False on SQLite
without a table rebuild.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_company_itar"
down_revision: str | None = "0006_stage1_eligibility"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column(
            "itar_restricted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("companies", "itar_restricted")
