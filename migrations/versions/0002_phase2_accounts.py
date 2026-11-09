"""Phase 2: user accounts and personalization.

Revision ID: 0002_phase2_accounts
Revises: 0001_initial
Create Date: hand-authored

Adds the authentication/role columns to ``users``, extends ``saved_jobs`` and
``application_status``, adds ``user_profiles.notification_prefs``, and creates
the ``saved_filters`` subscription table used by FEAT-005. Portable column
types only so SQLite dev stays PostgreSQL-compatible.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_phase2_accounts"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # users: authentication + role
    op.add_column("users", sa.Column("password_hash", sa.String(), nullable=True))
    op.add_column("users", sa.Column("role", sa.String(), nullable=True))

    # saved_jobs: optional note + uniqueness per (user, job). batch_alter_table
    # is used so the unique constraint applies cleanly on SQLite (which cannot
    # ALTER TABLE ADD CONSTRAINT) as well as PostgreSQL.
    with op.batch_alter_table("saved_jobs") as batch:
        batch.add_column(sa.Column("note", sa.Text(), nullable=True))
        batch.create_unique_constraint(
            "uq_saved_jobs_user_job", ["user_id", "job_id"]
        )

    # application_status: note, deadline + uniqueness per (user, job)
    with op.batch_alter_table("application_status") as batch:
        batch.add_column(sa.Column("note", sa.Text(), nullable=True))
        batch.add_column(sa.Column("deadline", sa.DateTime(timezone=True), nullable=True))
        batch.create_unique_constraint(
            "uq_application_status_user_job", ["user_id", "job_id"]
        )

    # user_profiles: per-user notification preferences
    op.add_column(
        "user_profiles", sa.Column("notification_prefs", sa.JSON(), nullable=True)
    )

    # saved_filters / subscriptions table (FEAT-005 foundation)
    op.create_table(
        "saved_filters",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("filter_json", sa.JSON(), nullable=True),
        sa.Column("notify", sa.Boolean(), nullable=False),
        sa.Column("channel", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )


def downgrade() -> None:
    op.drop_table("saved_filters")
    op.drop_column("user_profiles", "notification_prefs")

    with op.batch_alter_table("application_status") as batch:
        batch.drop_constraint("uq_application_status_user_job", type_="unique")
        batch.drop_column("deadline")
        batch.drop_column("note")

    with op.batch_alter_table("saved_jobs") as batch:
        batch.drop_constraint("uq_saved_jobs_user_job", type_="unique")
        batch.drop_column("note")

    op.drop_column("users", "role")
    op.drop_column("users", "password_hash")
