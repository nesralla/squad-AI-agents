import enum

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.core.database import Base

EMBEDDING_DIM = 1024  # voyage-code-3 default


class MemoryType(str, enum.Enum):
    TASK_SOLUTION = "task_solution"
    REVIEW_PATTERN = "review_pattern"
    ERROR_FIX = "error_fix"


class AgentMemory(Base):
    __tablename__ = "agent_memories"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False, index=True)
    memory_type = Column(Enum(MemoryType), nullable=False, index=True)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(EMBEDDING_DIM))
    metadata_ = Column("metadata", JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
