"""Add memory lifecycle, audit log, goals and weekly reviews

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-10 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("importance", sa.Float(), server_default="0.5", nullable=False),
    )
    op.add_column(
        "projects",
        sa.Column("last_referenced_at", sa.DateTime(), nullable=True),
    )

    op.add_column(
        "ideas",
        sa.Column("importance", sa.Float(), server_default="0.5", nullable=False),
    )
    op.add_column(
        "ideas",
        sa.Column("last_referenced_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user_profile.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("entity_type", sa.String(100)),
        sa.Column("entity_id", sa.Integer()),
        sa.Column("details", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_audit_user_created", "audit_log", ["user_id", "created_at"]
    )

    op.create_table(
        "goals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user_profile.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("cadence", sa.String(100)),
        sa.Column("status", sa.String(50), server_default="active"),
        sa.Column("streak", sa.Integer(), server_default="0"),
        sa.Column("longest_streak", sa.Integer(), server_default="0"),
        sa.Column("last_check_in", sa.Date()),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "weekly_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user_profile.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("wins", postgresql.JSONB()),
        sa.Column("stuck", postgresql.JSONB()),
        sa.Column("goals_next_week", postgresql.JSONB()),
        sa.Column("reflection", sa.Text()),
        sa.Column("mood", sa.Float()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("weekly_reviews")
    op.drop_table("goals")
    op.drop_index("ix_audit_user_created", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_column("ideas", "last_referenced_at")
    op.drop_column("ideas", "importance")
    op.drop_column("projects", "last_referenced_at")
    op.drop_column("projects", "importance")
