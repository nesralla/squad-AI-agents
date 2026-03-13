import logging
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db, init_db
from app.core.redis_client import enqueue_task, get_progress, queue_length
from app.schemas.task_schema import TaskProgressResponse, TaskQueuedResponse, TaskRequest, TaskResponse
from app.models.memory import AgentMemory  # noqa: F401 — register model for create_all
from app.services.jira_service import JiraService
from app.services.task_service import TaskService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# Enable pgvector extension and create tables on startup
init_db()

app = FastAPI(
    title="IA Dev Squad",
    description=(
        "Autonomous AI agents that generate, review and push GoLang code "
        "from task descriptions received via Telegram or Jira."
    ),
    version="2.0.0",
)

DbDep = Annotated[Session, Depends(get_db)]

_404 = {404: {"description": "Resource not found"}}


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "ok",
        "queue_length": queue_length(),
        "jira_enabled": settings.JIRA_ENABLED,
    }


# ── Tasks ──────────────────────────────────────────────────────────────────────

@app.post("/tasks", response_model=TaskQueuedResponse)
def create_task(request: TaskRequest, db: DbDep):
    """
    Create a task and push it onto the Redis queue.
    Returns immediately — the worker processes it asynchronously and
    notifies the user on Telegram when done.
    """
    service = TaskService(db)
    task = service.create(request)
    enqueue_task(task.id)

    return TaskQueuedResponse(
        task_id=task.id,
        status="pending",
        message=(
            f"Tarefa #{task.id} recebida e enfileirada. "
            "Voce sera notificado no Telegram quando o time de agentes finalizar."
        ),
    )


@app.get("/tasks/{task_id}", response_model=TaskResponse, responses=_404)
def get_task(task_id: int, db: DbDep):
    """Retrieve the full task record from PostgreSQL (final state)."""
    service = TaskService(db)
    task = service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.get("/tasks/{task_id}/progress", response_model=TaskProgressResponse, responses=_404)
def get_task_progress(task_id: int):
    """
    Return real-time execution progress from Redis.
    Available while the worker is running; expires 24 h after last update.
    """
    progress = get_progress(task_id)
    if not progress:
        raise HTTPException(
            status_code=404,
            detail="No progress data found. Task may not have started yet or data has expired.",
        )
    return TaskProgressResponse(
        task_id=task_id,
        step=progress["step"],
        detail=progress.get("detail", ""),
    )


# ── Jira ──────────────────────────────────────────────────────────────────────

@app.get("/jira/status")
def jira_status():
    """Check if Jira integration is configured and reachable."""
    jira = JiraService()
    configured = jira.is_configured()
    return {
        "enabled": settings.JIRA_ENABLED,
        "configured": configured,
        "project_key": settings.JIRA_PROJECT_KEY or None,
        "label_trigger": settings.JIRA_LABEL_TRIGGER,
        "poll_interval": settings.JIRA_POLL_INTERVAL,
    }


@app.post("/jira/sync")
def jira_sync(db: DbDep):
    """
    Manually trigger a Jira poll cycle.
    Fetches new issues with the trigger label and creates tasks.
    """
    jira = JiraService()
    if not jira.is_configured():
        raise HTTPException(
            status_code=400,
            detail="Jira is not configured. Set JIRA_ENABLED=true and provide credentials in .env",
        )

    issues = jira.get_new_issues()
    created = []

    task_service = TaskService(db)
    for issue in issues:
        existing = task_service.get_by_jira_key(issue["key"])
        if existing:
            continue

        description = jira.build_task_description(issue)
        request = TaskRequest(
            description=description,
            jira_issue_key=issue["key"],
            jira_issue_url=issue["url"],
            jira_project_key=settings.JIRA_PROJECT_KEY,
        )
        task = task_service.create(request)
        enqueue_task(task.id)

        jira.transition_issue(issue["key"], settings.JIRA_STATUS_IN_PROGRESS)

        created.append({
            "task_id": task.id,
            "jira_key": issue["key"],
            "summary": issue["summary"],
        })

    return {
        "synced": len(created),
        "issues_found": len(issues),
        "tasks_created": created,
    }
