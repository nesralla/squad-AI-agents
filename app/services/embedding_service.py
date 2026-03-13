import logging

import voyageai

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: voyageai.Client | None = None


def _get_client() -> voyageai.Client:
    global _client
    if _client is None:
        _client = voyageai.Client(api_key=settings.VOYAGE_API_KEY)
    return _client


def generate_embedding(text: str, input_type: str = "document") -> list[float] | None:
    """Generate a single embedding vector. Returns None if Voyage is not configured."""
    if not settings.VOYAGE_API_KEY:
        return None
    try:
        client = _get_client()
        result = client.embed(
            [text[:8000],],  # Voyage has a token limit; truncate long texts
            model=settings.EMBEDDING_MODEL,
            input_type=input_type,
        )
        return result.embeddings[0]
    except Exception as exc:
        logger.warning(f"Embedding generation failed: {exc}")
        return None


def generate_query_embedding(text: str) -> list[float] | None:
    """Generate an embedding optimised for similarity queries."""
    return generate_embedding(text, input_type="query")
