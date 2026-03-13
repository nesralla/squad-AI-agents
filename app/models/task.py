import enum

from sqlalchemy import Column, DateTime, Enum, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    REVIEW_REQUESTED = "review_requested"
    COMPLETED = "completed"
    FAILED = "failed"


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    description = Column(Text, nullable=False)
    status = Column(Enum(TaskStatus), default=TaskStatus.PENDING, nullable=False)

    branch_name = Column(String(255), nullable=True)
    generated_code = Column(Text, nullable=True)   # JSON string from DevAgent
    review_feedback = Column(Text, nullable=True)  # JSON string from ReviewerAgent

    telegram_chat_id = Column(String(100), nullable=True)
    telegram_message_id = Column(Integer, nullable=True)

    # Jira integration fields
    jira_issue_key = Column(String(50), nullable=True, index=True)   # e.g. "PROJ-123"
    jira_issue_url = Column(String(500), nullable=True)
    jira_project_key = Column(String(20), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
