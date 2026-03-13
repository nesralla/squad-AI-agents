from sqlalchemy.orm import Session

from app.models.task import Task, TaskStatus
from app.schemas.task_schema import TaskRequest


class TaskService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, request: TaskRequest) -> Task:
        task = Task(
            description=request.description,
            status=TaskStatus.PENDING,
            telegram_chat_id=request.telegram_chat_id,
            telegram_message_id=request.telegram_message_id,
            jira_issue_key=request.jira_issue_key,
            jira_issue_url=request.jira_issue_url,
            jira_project_key=request.jira_project_key,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def update(self, task_id: int, **kwargs) -> Task:
        task = self._get_or_raise(task_id)
        for key, value in kwargs.items():
            setattr(task, key, value)
        self.db.commit()
        self.db.refresh(task)
        return task

    def get(self, task_id: int) -> Task | None:
        return self.db.query(Task).filter(Task.id == task_id).first()

    def get_by_jira_key(self, jira_issue_key: str) -> Task | None:
        """Find a task linked to a Jira issue (avoids duplicate processing)."""
        return (
            self.db.query(Task)
            .filter(Task.jira_issue_key == jira_issue_key)
            .first()
        )

    def _get_or_raise(self, task_id: int) -> Task:
        task = self.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        return task
