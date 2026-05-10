"""Reminders table — proactive pings linked to memory items

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-10 17:30:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reminders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("user_profile.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=True,
                  comment="What the user was talking about when this reminder was set"),
        sa.Column("linked_kind", sa.String(40), nullable=True,
                  comment="idea | project | decision | contact | etc."),
        sa.Column("linked_id", sa.Integer(), nullable=True),
        sa.Column("due_at", sa.DateTime(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_reminders_user_due", "reminders", ["user_id", "due_at"])
    op.create_index(
        "ix_reminders_undelivered", "reminders",
        ["delivered_at", "due_at"],
        postgresql_where=sa.text("delivered_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_reminders_undelivered", table_name="reminders")
    op.drop_index("ix_reminders_user_due", table_name="reminders")
    op.drop_table("reminders")
