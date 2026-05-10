"""Per-domain browser recipes + site notes for learning across sessions

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-10 04:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "browser_recipes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user_profile.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("goal_template", sa.Text(), nullable=False),
        sa.Column("steps", postgresql.JSONB(), nullable=False),
        sa.Column("uses", sa.Integer(), server_default="1"),
        sa.Column("successes", sa.Integer(), server_default="1"),
        sa.Column("last_used_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_recipes_user_domain", "browser_recipes", ["user_id", "domain"])

    op.create_table(
        "browser_site_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user_profile.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(50), server_default="observation"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "domain", "note", name="uq_site_notes_unique"),
    )


def downgrade() -> None:
    op.drop_table("browser_site_notes")
    op.drop_index("ix_recipes_user_domain", table_name="browser_recipes")
    op.drop_table("browser_recipes")
