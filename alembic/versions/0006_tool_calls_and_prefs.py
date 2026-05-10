"""Tool-call audit log + user preferences (pattern learning)

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-10 16:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user_profile.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tool_name", sa.String(80), nullable=False),
        sa.Column("args", postgresql.JSONB(), nullable=True),
        sa.Column("ok", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("result_preview", sa.Text(), nullable=True),
        sa.Column("turn_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tool_calls_user_time", "tool_calls", ["user_id", "created_at"])
    op.create_index("ix_tool_calls_user_name", "tool_calls", ["user_id", "tool_name"])

    op.create_table(
        "user_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user_profile.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False, comment="response_style|cadence|tool_preference|general"),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), server_default="0.5"),
        sa.Column("evidence_count", sa.Integer(), server_default="1"),
        sa.Column("last_seen_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "kind", "key", name="uq_user_pref"),
    )


def downgrade() -> None:
    op.drop_table("user_preferences")
    op.drop_index("ix_tool_calls_user_name", table_name="tool_calls")
    op.drop_index("ix_tool_calls_user_time", table_name="tool_calls")
    op.drop_table("tool_calls")
