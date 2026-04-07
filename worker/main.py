"""
Background Worker — IA Dev Squad

Responsabilidades:
  1. Consumir tasks da fila Redis (BRPOP)
  2. Executar o Orchestrator (DevAgent → ReviewerAgent → GitService)
  3. Atualizar o progresso em tempo real no Redis
  4. Notificar o usuário no Telegram ao concluir (sucesso ou falha)
  5. Criar subtasks no Jira por agente, atualizar status e time tracking

Executa em loop infinito como processo separado do FastAPI.
"""
import logging
import time
from datetime import datetime, timezone

import httpx

from app.core.config import settings, validate_required_settings
from app.core.database import SessionLocal
from app.core.redis_client import dequeue_task, get_progress, set_progress
from app.models.task import TaskStatus
from app.orchestrator.workflow import MAX_ITERATIONS, Orchestrator
from app.services.jira_service import JiraService
from app.services.task_service import TaskService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Telegram notifier ──────────────────────────────────────────────────────────

def _send_telegram(chat_id: str, text: str) -> None:
    """Send a message directly via the Bot API (no python-telegram-bot needed)."""
    if not settings.TELEGRAM_BOT_TOKEN or not chat_id:
        return
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        httpx.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10.0,
        )
    except Exception as exc:
        logger.warning(f"Telegram notify failed (chat_id={chat_id}): {exc}")


def _notify_success(chat_id: str, task_id: int, result: dict) -> None:
    review = result.get("review", {})
    security = result.get("security", {})
    plan = result.get("plan", {})
    approved_label = "Sim" if review.get("approved") else "Nao (requer revisao humana)"
    issues_count = review.get("issues_count", 0)
    iterations = result.get("iterations", 1)
    build_ok = result.get("build_success", False)
    tests_pass = result.get("tests_pass", False)
    test_count = result.get("test_count", 0)

    issues_lines = ""
    for issue in review.get("issues", [])[:5]:
        sev = issue.get("severity", "?").upper()
        issues_lines += f"\n  [{sev}] {issue.get('description', '')}"

    # Branch or PR link
    if result.get("pr_url"):
        branch_line = f"PR: {result['pr_url']}"
    elif result.get("branch"):
        branch_line = f"Branch: `{result['branch']}`"
    else:
        branch_line = "Branch: N/A (git nao configurado)"

    msg = (
        f"*Tarefa #{task_id} concluida!*\n\n"
        f"{branch_line}\n"
        f"Implementacao: {result['dev_summary']}\n\n"
        f"*Pipeline (7 agentes)*\n"
        f"Planner: {'Complexa' if plan.get('is_complex') else 'Simples'} ({plan.get('subtasks', 1)} subtask(s))\n"
        f"Iteracoes Dev/Review: {iterations}/{MAX_ITERATIONS}\n"
        f"Go build: {'OK' if build_ok else 'FALHOU'}\n"
        f"Go test: {'OK' if tests_pass else 'FALHOU'} ({test_count} testes)\n"
        f"Security: risk {security.get('risk_score', 'N/A')}/10 ({security.get('vulnerabilities', 0)} vulns)\n\n"
        f"*Code Review*\n"
        f"Aprovado: {approved_label}\n"
        f"Score: {review.get('score')}/10\n"
        f"Issues: {issues_count}"
        f"{issues_lines}\n\n"
        f"_{review.get('summary', '')}_"
    )
    _send_telegram(chat_id, msg)


def _notify_failure(chat_id: str, task_id: int, error: str) -> None:
    msg = (
        f"*Tarefa #{task_id} falhou*\n\n"
        f"Erro: `{error[:300]}`\n\n"
        f"Verifique os logs do worker para detalhes."
    )
    _send_telegram(chat_id, msg)


# ── Jira notifier ─────────────────────────────────────────────────────────────

_jira: JiraService | None = None


def _get_jira() -> JiraService:
    global _jira
    if _jira is None:
        _jira = JiraService()
    return _jira


def _notify_jira_success(
    jira_issue_key: str, task_id: int, result: dict,
    started_at: datetime | None = None,
) -> None:
    """Post pipeline results as a Jira comment, transition Em Analise → Done."""
    jira = _get_jira()
    if not jira.is_configured():
        return

    try:
        jira.comment_pipeline_result(jira_issue_key, result, started_at=started_at)

        # Try intermediate status "Em Analise" (optional — may not exist in all boards)
        review_ok = jira.transition_issue(jira_issue_key, settings.JIRA_STATUS_REVIEW)
        if not review_ok:
            logger.info(f"[Task {task_id}] Jira review status skipped (not available in workflow).")

        # Transition to Done (tries from whatever current status is)
        done_ok = jira.transition_issue(jira_issue_key, settings.JIRA_STATUS_DONE)
        if done_ok:
            logger.info(f"[Task {task_id}] Jira {jira_issue_key} transitioned to Done.")
        else:
            logger.warning(f"[Task {task_id}] Jira {jira_issue_key} could not transition to Done.")
    except Exception as exc:
        logger.warning(f"[Task {task_id}] Jira notification failed: {exc}")


def _notify_jira_failure(jira_issue_key: str, task_id: int, error: str) -> None:
    """Post failure comment on Jira issue (leave status as In Progress for retry)."""
    jira = _get_jira()
    if not jira.is_configured():
        return

    try:
        jira.comment_failure(jira_issue_key, error)
        logger.info(f"[Task {task_id}] Jira {jira_issue_key} failure comment posted.")
    except Exception as exc:
        logger.warning(f"[Task {task_id}] Jira failure notification failed: {exc}")


# ── Task processor ─────────────────────────────────────────────────────────────

def process_task(task_id: int) -> None:
    db = SessionLocal()
    started_at = datetime.now(timezone.utc)

    try:
        task_service = TaskService(db)
        task = task_service.get(task_id)

        if not task:
            logger.error(f"[Task {task_id}] Not found in database — skipping.")
            return

        if task.status == TaskStatus.COMPLETED:
            logger.warning(f"[Task {task_id}] Already completed — skipping duplicate.")
            return

        logger.info(f"[Task {task_id}] Starting execution...")
        set_progress(task_id, "running", "Iniciando agentes...")

        # ── Jira setup: assign, comment start, create subtasks ──
        jira = _get_jira()
        jira_key = task.jira_issue_key
        if jira_key and jira.is_configured():
            jira.assign_issue(jira_key)
            jira.comment_start(jira_key)
            jira.create_agent_subtasks(jira_key)

        # ── Build Orchestrator with Jira callbacks ──
        on_complete = None
        on_fail = None
        if jira_key and jira.is_configured():
            on_complete = jira.complete_subtask
            on_fail = jira.fail_subtask

        orchestrator = Orchestrator(
            db,
            on_agent_complete=on_complete,
            on_agent_fail=on_fail,
        )
        result = orchestrator.execute(task_id)

        set_progress(task_id, "completed", "Finalizado com sucesso.")
        logger.info(f"[Task {task_id}] Completed. Branch: {result.get('branch')}")

        # ── Notify Telegram ──
        if task.telegram_chat_id:
            _notify_success(task.telegram_chat_id, task_id, result)

        # ── Notify Jira ──
        if jira_key:
            _notify_jira_success(jira_key, task_id, result, started_at=started_at)

    except Exception as exc:
        logger.exception(f"[Task {task_id}] Execution failed: {exc}")
        set_progress(task_id, "failed", str(exc)[:200])

        # Re-fetch task to get notification targets
        try:
            task_service = TaskService(db)
            task = task_service.get(task_id)
            if task and task.telegram_chat_id:
                _notify_failure(task.telegram_chat_id, task_id, str(exc))
            if task and task.jira_issue_key:
                _notify_jira_failure(task.jira_issue_key, task_id, str(exc))
        except Exception:
            logger.exception(f"[Task {task_id}] Could not send failure notification.")

    finally:
        db.close()


# ── Main loop ──────────────────────────────────────────────────────────────────

def main() -> None:
    validate_required_settings()

    logger.info("=" * 60)
    logger.info("IA Dev Squad — Worker started.")
    logger.info("Waiting for tasks on the Redis queue...")
    logger.info("=" * 60)

    consecutive_errors = 0

    while True:
        try:
            task_id = dequeue_task(timeout=5)

            if task_id is not None:
                consecutive_errors = 0
                logger.info(f"[Queue] Dequeued task {task_id}")
                process_task(task_id)
            # else: timeout, loop again silently

        except KeyboardInterrupt:
            logger.info("Worker stopped by user.")
            break

        except Exception as exc:
            consecutive_errors += 1
            wait = min(2 ** consecutive_errors, 30)  # exponential backoff, max 30s
            logger.exception(f"Worker loop error (attempt {consecutive_errors}): {exc}")
            logger.info(f"Retrying in {wait}s...")
            time.sleep(wait)


if __name__ == "__main__":
    main()
