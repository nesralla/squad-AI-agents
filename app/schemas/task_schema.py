from typing import Optional

from pydantic import BaseModel, Field

from app.models.task import TaskStatus


class TaskRequest(BaseModel):
    description: str = Field(..., min_length=10, max_length=10000)
    telegram_chat_id: Optional[str] = None
    telegram_message_id: Optional[int] = None
    jira_issue_key: Optional[str] = None
    jira_issue_url: Optional[str] = None
    jira_project_key: Optional[str] = None


class TaskQueuedResponse(BaseModel):
    """Returned immediately by POST /tasks — task is now in the Redis queue."""
    task_id: int
    status: str  # always "pending" at this point
    message: str


class TaskResponse(BaseModel):
    """Full task record from PostgreSQL — returned by GET /tasks/{id}."""
    id: int
    description: str
    status: TaskStatus
    branch_name: Optional[str] = None
    generated_code: Optional[str] = None
    review_feedback: Optional[str] = None
    jira_issue_key: Optional[str] = None
    jira_issue_url: Optional[str] = None

    class Config:
        from_attributes = True


class TaskProgressResponse(BaseModel):
    """Real-time progress stored in Redis — returned by GET /tasks/{id}/progress."""
    task_id: int
    step: str
    detail: str
