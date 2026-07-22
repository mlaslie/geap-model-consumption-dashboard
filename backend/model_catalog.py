"""
Model catalog discovery: list available Gemini models from the Vertex AI API.
"""
import logging
from typing import List

from backend.config import settings

logger = logging.getLogger(__name__)

# Conservative name-based filter: exclude obvious non-text/generation models.
_FILTER_KEYWORDS = ("embedding", "image", "veo", "imagen")


class ModelCatalogError(Exception):
    """Raised when model catalog discovery fails."""
    pass


def list_available_models() -> List[str]:
    """
    Discovers available Gemini models from the Vertex AI API using the
    google-genai client (same pattern as ai_assistant).

    Returns a sorted, deduplicated list of short model IDs (no publisher path)
    filtered to those starting with 'gemini', excluding obvious non-generation
    variants (embedding, image, veo, imagen) by name.

    Raises:
        ModelCatalogError: if the SDK is not installed, BIGQUERY_PROJECT_ID is
            unset, or the API list call fails.
    """
    try:
        from google import genai
    except ImportError as exc:
        raise ModelCatalogError(
            "google-genai SDK is not installed; cannot discover available models."
        ) from exc

    if not settings.BIGQUERY_PROJECT_ID:
        raise ModelCatalogError(
            "BIGQUERY_PROJECT_ID is not set; cannot initialize Vertex AI client "
            "for model catalog discovery."
        )

    logger.info(
        "Model catalog: listing Gemini models via Vertex AI "
        "(project=%s, location=%s)",
        settings.BIGQUERY_PROJECT_ID,
        settings.VERTEX_REGION,
    )

    try:
        client = genai.Client(
            vertexai=True,
            project=settings.BIGQUERY_PROJECT_ID,
            location=settings.VERTEX_REGION,
        )
    except Exception as exc:
        logger.error(
            "Model catalog: failed to initialize Vertex AI client: %s", exc
        )
        raise ModelCatalogError(
            f"Failed to initialize Vertex AI client: {type(exc).__name__}"
        ) from exc

    try:
        # The SDK iterator handles pagination automatically.
        raw_names = [m.name for m in client.models.list()]
    except Exception as exc:
        logger.error("Model catalog: models.list() API call failed: %s", exc)
        raise ModelCatalogError(
            f"Model list API call failed: {type(exc).__name__}"
        ) from exc

    import backend.bq_client as bq_client

    seen: set = set()
    result: List[str] = []
    for raw in raw_names:
        # bq_client.normalize_model_name strips "publishers/google/models/" prefix
        # and lowercases.  Also handle bare "models/<name>" paths.
        normalized = bq_client.normalize_model_name(raw)
        if normalized.startswith("models/"):
            normalized = normalized[len("models/"):]

        if not normalized.startswith("gemini"):
            continue
        if any(kw in normalized for kw in _FILTER_KEYWORDS):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)

    result.sort()
    logger.info(
        "Model catalog: discovered %d Gemini model(s): %s", len(result), result
    )
    return result
