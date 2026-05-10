"""Encrypted credential vault + browser session log

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-10 03:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user_profile.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("site", sa.String(255), nullable=False),
        sa.Column("login_url", sa.String(500)),
        sa.Column("username_enc", sa.Text()),
        sa.Column("password_enc", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "site", name="uq_credentials_user_site"),
    )

    op.create_table(
        "browser_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user_profile.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(50), server_default="running"),
        sa.Column("steps_taken", sa.Integer(), server_default="0"),
        sa.Column("final_url", sa.String(1000)),
        sa.Column("transcript", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime()),
    )


def downgrade() -> None:
    op.drop_table("browser_sessions")
    op.drop_table("credentials")
