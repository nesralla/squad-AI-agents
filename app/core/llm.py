import logging
import time

import anthropic

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None

MAX_RETRIES = 3
RETRY_BASE_DELAY = 2  # seconds
RETRYABLE_ERRORS = (
    anthropic.APITimeoutError,
    anthropic.APIConnectionError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
)


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


def call_claude(system_prompt: str, user_prompt: str, max_tokens: int = 8000) -> str:
    client = get_client()
    last_exc: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=settings.LLM_MODEL,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return response.content[0].text
        except RETRYABLE_ERRORS as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    f"LLM call failed (attempt {attempt}/{MAX_RETRIES}): {exc}. "
                    f"Retrying in {delay}s..."
                )
                time.sleep(delay)
            else:
                logger.error(f"LLM call failed after {MAX_RETRIES} attempts: {exc}")

    raise last_exc
