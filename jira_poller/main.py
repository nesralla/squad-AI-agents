"""
Jira Poller — Background service that monitors Jira Cloud for new issues.

Flow:
  1. Poll Jira every N seconds for issues with label "ai-squad" in "To Do"
  2. For each new issue:
     a. Check if already processed (by jira_issue_key in PostgreSQL)
     b. If new: create Task, enqueue to Redis, transition Jira to "In Progress"
  3. Loop forever (same pattern as worker/main.py)

Runs as a separate container alongside the worker.
"""
import logging
import time

import httpx

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.redis_client import enqueue_task
from app.schemas.task_schema import TaskRequest
from app.services.jira_service import JiraService
from app.services.task_service import TaskService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def poll_jira_once(jira: JiraService) -> int:
    """
    Single poll cycle: fetch new issues, create tasks, enqueue.
    Returns the number of new tasks created.
    """
    issues = jira.get_new_issues()
    if not issues:
        return 0

    created = 0
    db = SessionLocal()
    try:
        task_service = TaskService(db)

        for issue in issues:
            issue_key = issue["key"]

            # Skip if already processed
            existing = task_service.get_by_jira_key(issue_key)
            if existing:
                logger.debug(f"[Jira] {issue_key} already exists as Task #{existing.id} — skipping.")
                continue

            # Build description from Jira fields
            description = jira.build_task_description(issue)

            logger.info(f"[Jira] New issue {issue_key}: {issue['summary']}")

            # Create task in our system
            request = TaskRequest(
                description=description,
                jira_issue_key=issue_key,
                jira_issue_url=issue["url"],
                jira_project_key=settings.JIRA_PROJECT_KEY,
            )
            task = task_service.create(request)
            enqueue_task(task.id)

            logger.info(f"[Jira] Created Task #{task.id} from {issue_key}, enqueued.")

            # Transition Jira issue to "In Progress"
            jira.transition_issue(issue_key, settings.JIRA_STATUS_IN_PROGRESS)

            created += 1

    except Exception as exc:
        logger.exception(f"[Jira] Error processing issues: {exc}")
    finally:
        db.close()

    return created


def main() -> None:
    jira = JiraService()

    if not jira.is_configured():
        logger.error(
            "Jira integration is not configured. "
            "Set JIRA_ENABLED=true and provide JIRA_BASE_URL, JIRA_USER_EMAIL, "
            "JIRA_API_TOKEN, and JIRA_PROJECT_KEY in .env"
        )
        logger.info("Jira Poller exiting — not configured.")
        return

    interval = settings.JIRA_POLL_INTERVAL
    logger.info("=" * 60)
    logger.info("IA Dev Squad — Jira Poller started.")
    logger.info(f"Project: {settings.JIRA_PROJECT_KEY}")
    logger.info(f"Label trigger: {settings.JIRA_LABEL_TRIGGER}")
    logger.info(f"Poll interval: {interval}s")
    logger.info(f"Base URL: {settings.JIRA_BASE_URL}")
    logger.info("=" * 60)

    consecutive_errors = 0

    while True:
        try:
            created = poll_jira_once(jira)
            consecutive_errors = 0

            if created > 0:
                logger.info(f"[Jira] Poll cycle: {created} new task(s) created.")

        except KeyboardInterrupt:
            logger.info("Jira Poller stopped by user.")
            break

        except Exception as exc:
            consecutive_errors += 1
            wait = min(2 ** consecutive_errors, 120)
            logger.exception(f"Jira Poller error (attempt {consecutive_errors}): {exc}")
            logger.info(f"Retrying in {wait}s...")
            time.sleep(wait)
            continue

        time.sleep(interval)


if __name__ == "__main__":
    main()
