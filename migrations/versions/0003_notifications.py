"""FEAT-005: extend notification_log for email dedup + history.

Revision ID: 0003_notifications
Revises: 0002_phase2_accounts
Create Date: hand-authored

Adds the columns the email notifier needs on ``notification_log`` so it can
dedup by ``(user_id, job_id, channel, rule)`` and store a readable history
(company_id for source-level notifications, rule, subject, to_email). Portable
column types only so SQLite dev stays PostgreSQL-compatible.

NOTE for later features: this is the latest revision. FEAT-006 (AI cache) must
chain its migration as ``0004_*`` with ``down_revision = "0003_notifications"``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_notifications"
down_revision: str | None = "0002_phase2_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("notification_log") as batch:
        batch.add_column(sa.Column("company_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("rule", sa.String(), nullable=True))
        batch.add_column(sa.Column("subject", sa.String(), nullable=True))
        batch.add_column(sa.Column("to_email", sa.String(), nullable=True))
        batch.create_foreign_key(
            "fk_notification_log_company", "companies", ["company_id"], ["id"]
        )
    op.create_index(
        "ix_notification_log_user", "notification_log", ["user_id"]
    )
    op.create_index(
        "ix_notification_log_dedup",
        "notification_log",
        ["user_id", "job_id", "channel", "rule"],
    )


def downgrade() -> None:
    op.drop_index("ix_notification_log_dedup", table_name="notification_log")
    op.drop_index("ix_notification_log_user", table_name="notification_log")
    with op.batch_alter_table("notification_log") as batch:
        batch.drop_constraint("fk_notification_log_company", type_="foreignkey")
        batch.drop_column("to_email")
        batch.drop_column("subject")
        batch.drop_column("rule")
        batch.drop_column("company_id")
