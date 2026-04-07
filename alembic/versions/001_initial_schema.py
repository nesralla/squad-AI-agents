"""Initial schema — tasks and agent_memories tables.

Revision ID: 001
Revises: None
Create Date: 2026-04-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "running", "review_requested", "completed", "failed", name="taskstatus"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("branch_name", sa.String(255), nullable=True),
        sa.Column("generated_code", sa.Text(), nullable=True),
        sa.Column("review_feedback", sa.Text(), nullable=True),
        sa.Column("telegram_chat_id", sa.String(100), nullable=True),
        sa.Column("telegram_message_id", sa.Integer(), nullable=True),
        sa.Column("jira_issue_key", sa.String(50), nullable=True, index=True),
        sa.Column("jira_issue_url", sa.String(500), nullable=True),
        sa.Column("jira_project_key", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "agent_memories",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=False, index=True),
        sa.Column(
            "memory_type",
            sa.Enum("task_solution", "review_pattern", "error_fix", name="memorytype"),
            nullable=False,
            index=True,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1024)),
        sa.Column("metadata", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("agent_memories")
    op.drop_table("tasks")
    op.execute("DROP TYPE IF EXISTS memorytype")
    op.execute("DROP TYPE IF EXISTS taskstatus")
