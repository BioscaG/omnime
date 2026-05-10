"""Personal categories + career engine tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-10 02:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Personal categories ----------------------------------------
    op.create_table(
        "books",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("user_profile.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("author", sa.String(255)),
        sa.Column("status", sa.String(50), server_default="reading"),
        sa.Column("rating", sa.Float()),
        sa.Column("started_at", sa.Date()),
        sa.Column("finished_at", sa.Date()),
        sa.Column("takeaways", postgresql.JSONB()),
        sa.Column("quotes", postgresql.JSONB()),
        sa.Column("source", sa.String(255)),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("user_profile.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("rationale", sa.Text()),
        sa.Column("alternatives", postgresql.JSONB()),
        sa.Column("outcome", sa.Text()),
        sa.Column("status", sa.String(50), server_default="pending"),
        sa.Column("decided_at", sa.Date()),
        sa.Column("category", sa.String(100)),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "health_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("user_profile.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("category", sa.String(100)),
        sa.Column("date", sa.Date()),
        sa.Column("description", sa.Text()),
        sa.Column("severity", sa.String(50)),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "quotes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("user_profile.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(500)),
        sa.Column("author", sa.String(255)),
        sa.Column("tags", postgresql.JSONB()),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.add_column(
        "conversations",
        sa.Column("sentiment", sa.Float(), nullable=True),
    )

    # --- Career engine -----------------------------------------------
    op.create_table(
        "job_opportunities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("user_profile.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company", sa.String(255), nullable=False),
        sa.Column("role", sa.String(255), nullable=False),
        sa.Column("source", sa.String(255)),
        sa.Column("status", sa.String(50), server_default="discovered"),
        sa.Column("description", sa.Text()),
        sa.Column("location", sa.String(255)),
        sa.Column("remote", sa.Boolean(), server_default=sa.false()),
        sa.Column("salary_range", sa.String(100)),
        sa.Column("link", sa.String(500)),
        sa.Column("contact", sa.String(255)),
        sa.Column("notes", sa.Text()),
        sa.Column("applied_at", sa.Date()),
        sa.Column("next_step_at", sa.Date()),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "cv_variants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("user_profile.id", ondelete="CASCADE"), nullable=False),
        sa.Column("opportunity_id", sa.Integer(),
                  sa.ForeignKey("job_opportunities.id", ondelete="SET NULL")),
        sa.Column("label", sa.String(255)),
        sa.Column("body_markdown", sa.Text()),
        sa.Column("highlights", postgresql.JSONB()),
        sa.Column("style", sa.String(100)),
        sa.Column("score", sa.Float()),
        sa.Column("chosen", sa.Boolean(), server_default=sa.false()),
        sa.Column("feedback", sa.Text()),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("cv_variants")
    op.drop_table("job_opportunities")
    op.drop_column("conversations", "sentiment")
    op.drop_table("quotes")
    op.drop_table("health_events")
    op.drop_table("decisions")
    op.drop_table("books")
