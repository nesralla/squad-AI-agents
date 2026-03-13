"""
Redis client — single shared connection + helpers for:
  - Task queue  : LPUSH/BRPOP
  - Task progress: SETEX/GET  (real-time step tracking visible via API)
"""
import json
import logging

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Key constants ──────────────────────────────────────────────────────────────
TASK_QUEUE_KEY = "ia_squad:task_queue"
_PROGRESS_PREFIX = "ia_squad:task:progress:"
_PROGRESS_TTL = 86_400  # 24 h

_client: redis.Redis | None = None


# ── Connection ─────────────────────────────────────────────────────────────────

def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


# ── Queue helpers ──────────────────────────────────────────────────────────────

def enqueue_task(task_id: int) -> None:
    """Push a task_id onto the left end of the queue (LPUSH)."""
    get_redis().lpush(TASK_QUEUE_KEY, json.dumps({"task_id": task_id}))
    logger.info(f"[Queue] Task {task_id} enqueued.")


def dequeue_task(timeout: int = 5) -> int | None:
    """
    Blocking pop from the right end (BRPOP). Returns task_id or None on timeout.
    The worker calls this in a loop.
    """
    result = get_redis().brpop(TASK_QUEUE_KEY, timeout=timeout)
    if result:
        _, raw = result
        return json.loads(raw)["task_id"]
    return None


def queue_length() -> int:
    return get_redis().llen(TASK_QUEUE_KEY)


# ── Progress helpers ───────────────────────────────────────────────────────────

def set_progress(task_id: int, step: str, detail: str = "") -> None:
    """Store the current execution step in Redis (TTL = 24 h)."""
    key = f"{_PROGRESS_PREFIX}{task_id}"
    get_redis().setex(key, _PROGRESS_TTL, json.dumps({"step": step, "detail": detail}))
    logger.debug(f"[Progress] Task {task_id}: {step} — {detail}")


def get_progress(task_id: int) -> dict | None:
    """Return the last recorded progress for a task, or None if expired / not found."""
    val = get_redis().get(f"{_PROGRESS_PREFIX}{task_id}")
    return json.loads(val) if val else None


def clear_progress(task_id: int) -> None:
    get_redis().delete(f"{_PROGRESS_PREFIX}{task_id}")
