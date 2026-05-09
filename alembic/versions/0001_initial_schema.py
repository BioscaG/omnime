"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2025-01-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_profile",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("name", sa.String(255)),
        sa.Column("bio", sa.Text()),
        sa.Column("communication_style", sa.Text()),
        sa.Column("personality_traits", postgresql.JSONB()),
        sa.Column("preferences", postgresql.JSONB()),
        sa.Column("living_profile", sa.Text()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user_profile.id", ondelete="CASCADE")),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("role", sa.String(255)),
        sa.Column("technologies", postgresql.JSONB()),
        sa.Column("status", sa.String(50), server_default="active"),
        sa.Column("start_date", sa.Date()),
        sa.Column("end_date", sa.Date()),
        sa.Column("key_achievements", postgresql.JSONB()),
        sa.Column("challenges", sa.Text()),
        sa.Column("collaborators", postgresql.JSONB()),
        sa.Column("links", postgresql.JSONB()),
        sa.Column("details", sa.Text()),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "work_experience",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user_profile.id", ondelete="CASCADE")),
        sa.Column("company", sa.String(255), nullable=False),
        sa.Column("role", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("start_date", sa.Date()),
        sa.Column("end_date", sa.Date()),
        sa.Column("achievements", postgresql.JSONB()),
        sa.Column("technologies", postgresql.JSONB()),
        sa.Column("location", sa.String(255)),
        sa.Column("remote", sa.Boolean(), server_default=sa.false()),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "education",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user_profile.id", ondelete="CASCADE")),
        sa.Column("institution", sa.String(255), nullable=False),
        sa.Column("degree", sa.String(255)),
        sa.Column("field", sa.String(255)),
        sa.Column("start_date", sa.Date()),
        sa.Column("end_date", sa.Date()),
        sa.Column("grade", sa.String(50)),
        sa.Column("achievements", postgresql.JSONB()),
        sa.Column("courses", postgresql.JSONB()),
        sa.Column("thesis", sa.Text()),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "skills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user_profile.id", ondelete="CASCADE")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(100)),
        sa.Column("proficiency", sa.String(50)),
        sa.Column("years_experience", sa.Float()),
        sa.Column("context", sa.Text()),
        sa.Column("last_used", sa.Date()),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user_profile.id", ondelete="CASCADE")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("relationship", sa.String(100)),
        sa.Column("organization", sa.String(255)),
        sa.Column("email", sa.String(255)),
        sa.Column("phone", sa.String(50)),
        sa.Column("notes", sa.Text()),
        sa.Column("last_interaction", sa.Date()),
        sa.Column("context", sa.Text()),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "achievements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user_profile.id", ondelete="CASCADE")),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("date", sa.Date()),
        sa.Column("category", sa.String(100)),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id")),
        sa.Column("impact", sa.Text()),
        sa.Column("evidence", postgresql.JSONB()),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "life_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user_profile.id", ondelete="CASCADE")),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("date", sa.Date()),
        sa.Column("category", sa.String(100)),
        sa.Column("location", sa.String(255)),
        sa.Column("people_involved", postgresql.JSONB()),
        sa.Column("lessons_learned", sa.Text()),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "ideas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user_profile.id", ondelete="CASCADE")),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("category", sa.String(100)),
        sa.Column("tags", postgresql.JSONB()),
        sa.Column("status", sa.String(50), server_default="raw"),
        sa.Column("related_project_id", sa.Integer(), sa.ForeignKey("projects.id")),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user_profile.id", ondelete="CASCADE")),
        sa.Column("message_text", sa.Text(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("intent", sa.String(50)),
        sa.Column("entities_extracted", postgresql.JSONB()),
        sa.Column("telegram_message_id", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_conversations_user_created", "conversations", ["user_id", "created_at"])

    op.create_table(
        "memory_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user_profile.id", ondelete="CASCADE")),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("key_updates", postgresql.JSONB()),
        sa.Column("topics", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user_profile.id", ondelete="CASCADE")),
        sa.Column("filename", sa.String(500)),
        sa.Column("file_type", sa.String(100)),
        sa.Column("telegram_file_id", sa.String(255)),
        sa.Column("extracted_text", sa.Text()),
        sa.Column("summary", sa.Text()),
        sa.Column("tags", postgresql.JSONB()),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    for table in [
        "files",
        "memory_summaries",
        "conversations",
        "ideas",
        "life_events",
        "achievements",
        "contacts",
        "skills",
        "education",
        "work_experience",
        "projects",
        "user_profile",
    ]:
        op.drop_table(table)
