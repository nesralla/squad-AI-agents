"""
Telegram bot — entry point for the IA Dev Squad.

Phase 1 flow (non-blocking):
  1. User sends a task description.
  2. Bot POSTs to /tasks → receives task_id immediately.
  3. Bot replies with the task_id and confirms the user will be notified.
  4. Worker runs in the background and calls the Telegram Bot API directly
     when the task finishes (success or failure).
  5. /status <task_id>  → queries real-time Redis progress + PostgreSQL state.
"""
import logging

import httpx
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Short timeout — the API now responds immediately
HTTP_TIMEOUT = 15.0


# ── Authorization helper ───────────────────────────────────────────────────────

def _is_authorized(chat_id: int) -> bool:
    allowed = settings.TELEGRAM_ALLOWED_CHAT_IDS
    return (not allowed) or (chat_id in allowed)


# ── Handlers ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*IA Dev Squad* ativo!\n\n"
        "Envie a descricao da tarefa que deseja implementar.\n"
        "O time de agentes ira:\n"
        "  1. Gerar o codigo GoLang\n"
        "  2. Revisar o codigo\n"
        "  3. Criar uma branch e fazer push no repositorio Git\n\n"
        "Voce sera notificado aqui quando a tarefa finalizar.\n\n"
        "Comandos:\n"
        "  /status <task\\_id> — consulta o progresso em tempo real",
        parse_mode="Markdown",
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.message.chat_id):
        await update.message.reply_text("Acesso nao autorizado.")
        return

    if not context.args:
        await update.message.reply_text("Uso: /status <task_id>")
        return

    task_id = context.args[0]
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            # Real-time progress from Redis
            prog_r = await client.get(f"{settings.API_BASE_URL}/tasks/{task_id}/progress")
            # Final state from PostgreSQL
            task_r = await client.get(f"{settings.API_BASE_URL}/tasks/{task_id}")

            lines: list[str] = [f"*Tarefa #{task_id}*"]

            if task_r.status_code == 200:
                task_data = task_r.json()
                lines.append(f"Status: `{task_data['status']}`")
                if task_data.get("branch_name"):
                    lines.append(f"Branch: `{task_data['branch_name']}`")

            if prog_r.status_code == 200:
                prog = prog_r.json()
                lines.append(f"Etapa atual: `{prog['step']}`")
                if prog.get("detail"):
                    lines.append(f"Detalhe: {prog['detail']}")
            elif task_r.status_code == 404:
                lines.append("Tarefa nao encontrada.")

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

        except Exception as exc:
            logger.exception(f"Status query failed: {exc}")
            await update.message.reply_text(f"Erro ao consultar status: {exc}")


async def handle_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    message_id = update.message.message_id
    text = update.message.text.strip()

    if not _is_authorized(chat_id):
        logger.warning(f"Unauthorized access from chat_id={chat_id}")
        await update.message.reply_text("Acesso nao autorizado.")
        return

    if not text:
        await update.message.reply_text("Envie uma descricao da tarefa.")
        return

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            response = await client.post(
                f"{settings.API_BASE_URL}/tasks",
                json={
                    "description": text,
                    "telegram_chat_id": str(chat_id),
                    "telegram_message_id": message_id,
                },
            )
            response.raise_for_status()
            result = response.json()

        except httpx.HTTPStatusError as exc:
            logger.error(f"API error {exc.response.status_code}: {exc.response.text}")
            await update.message.reply_text(
                f"Erro na API ({exc.response.status_code}). Verifique os logs do servidor."
            )
            return
        except Exception as exc:
            logger.exception(f"Unexpected error: {exc}")
            await update.message.reply_text(f"Erro inesperado: {exc}")
            return

    task_id = result["task_id"]
    await update.message.reply_text(
        f"*Tarefa \\#{task_id} recebida\\!*\n\n"
        f"O time de agentes esta trabalhando:\n"
        f"  DevAgent — gera o codigo GoLang\n"
        f"  ReviewerAgent — revisa o codigo\n"
        f"  GitService — faz push na branch\n\n"
        f"Voce sera notificado aqui quando finalizar\\.\n"
        f"Acompanhe com: /status {task_id}",
        parse_mode="MarkdownV2",
    )


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    if not settings.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")

    application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_task))

    logger.info("IA Dev Squad bot polling...")
    application.run_polling()


if __name__ == "__main__":
    main()
