"""
Canopy Wave AI provider client module.

Canopy Wave provides OpenAI-compatible API endpoints for open-source LLM models
with fast inference and serverless deployment.

API Documentation: https://canopywave.com/docs/get-started/openai-compatible
Base URL: https://inference.canopywave.io/v1
Models Endpoint: https://inference.canopywave.io/v1/models

The Canopy Wave API is compatible with the OpenAI API format, making integration
straightforward. Authentication uses bearer token authentication via API keys.

Supported models include:
- zai/glm-4.6: GLM-4.6 355B MoE reasoning model (~32B active params), 200K context
  Input: ~$0.45/1M tokens, Output: ~$1.50/1M tokens
- DeepSeek V3.2, Kimi K2, and other open-source models

Features:
- OpenAI-compatible chat completions API
- Structured outputs with JSON schema
- Function/tool calling support
- Serverless endpoints with pay-per-token pricing
"""

import logging
from typing import Any, Iterator

import httpx
from openai import OpenAI
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_canopywave_pooled_client
from src.services.model_catalog_cache import cache_gateway_catalog

# Initialize logging
logger = logging.getLogger(__name__)


def get_canopywave_client() -> OpenAI:
    """Get Canopy Wave client with connection pooling for better performance.

    Canopy Wave provides OpenAI-compatible API endpoints for various models.

    Returns:
        OpenAI client configured for Canopy Wave API.

    Raises:
        ValueError: If CANOPYWAVE_API_KEY is not configured.
    """
    try:
        if not Config.CANOPYWAVE_API_KEY:
            raise ValueError("Canopy Wave API key not configured")

        # Use pooled client for ~10-20ms performance improvement per request
        return get_canopywave_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize Canopy Wave client: {e}")
        raise


def make_canopywave_request_openai(
    messages: list[dict[str, Any]], model: str, **kwargs: Any
) -> ChatCompletion:
    """Make request to Canopy Wave using OpenAI client.

    Args:
        messages: List of message objects with role and content.
        model: Model name to use (e.g., 'zai/glm-4.6').
        **kwargs: Additional parameters like max_tokens, temperature, etc.

    Returns:
        ChatCompletion response from Canopy Wave.

    Raises:
        Exception: If the request fails.
    """
    try:
        client = get_canopywave_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"Canopy Wave request failed: {e}")
        raise


def make_canopywave_request_openai_stream(
    messages: list[dict[str, Any]], model: str, **kwargs: Any
) -> Iterator[ChatCompletionChunk]:
    """Make streaming request to Canopy Wave using OpenAI client.

    Args:
        messages: List of message objects with role and content.
        model: Model name to use (e.g., 'zai/glm-4.6').
        **kwargs: Additional parameters like max_tokens, temperature, etc.

    Returns:
        Iterator of ChatCompletionChunk for streaming responses.

    Raises:
        Exception: If the streaming request fails.
    """
    try:
        client = get_canopywave_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"Canopy Wave streaming request failed: {e}")
        raise


def process_canopywave_response(response: ChatCompletion) -> dict[str, Any]:
    """Process Canopy Wave response to extract relevant data.

    Args:
        response: OpenAI-format ChatCompletion response object.

    Returns:
        Normalized response dictionary with id, object, created, model,
        choices, and usage fields.

    Raises:
        Exception: If response processing fails.
    """
    try:
        choices = []
        for choice in response.choices:
            msg = extract_message_with_tools(choice.message)

            choices.append(
                {
                    "index": choice.index,
                    "message": msg,
                    "finish_reason": choice.finish_reason,
                }
            )

        return {
            "id": response.id,
            "object": response.object,
            "created": response.created,
            "model": response.model,
            "choices": choices,
            "usage": (
                {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }
                if response.usage
                else {}
            ),
        }
    except Exception as e:
        logger.error(f"Failed to process Canopy Wave response: {e}")
        raise


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Safely convert a value to float, returning default on failure.

    Args:
        value: The value to convert to float.
        default: The default value to return if conversion fails.

    Returns:
        The float value or default if conversion fails.
    """
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        logger.warning(
            f"Failed to convert pricing value to float: {value!r}, using default {default}"
        )
        return default


def _cache_and_return(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Update cache with models and timestamp, then return models.

    Args:
        models: List of model dictionaries to cache.

    Returns:
        The same list of models.
    """
    # Cache models in Redis with automatic TTL and error tracking
    cache_gateway_catalog("canopywave", models)
    return models


def fetch_models_from_canopywave() -> list[dict[str, Any]]:
    """Fetch available models from Canopy Wave API.

    Returns a list of model info dictionaries in catalog format.
    Fetches models dynamically from the Canopy Wave /v1/models endpoint.

    The API returns models with pricing information and capabilities.

    Returns:
        List of model dictionaries with id, name, provider, pricing, etc.
        Returns empty list if API key is not configured or on error.
    """
    try:
        # Check for API key before making request to avoid 401 errors
        if not Config.CANOPYWAVE_API_KEY:
            logger.warning("Canopy Wave API key not configured, skipping model fetch")
            return []

        # Build models endpoint URL from config
        models_endpoint = f"{Config.CANOPYWAVE_BASE_URL}/models"

        # Fetch models from API endpoint
        with httpx.Client(timeout=30.0) as client:
            headers = {"Authorization": f"Bearer {Config.CANOPYWAVE_API_KEY}"}
            response = client.get(models_endpoint, headers=headers)
            response.raise_for_status()
            data = response.json()

        models = []
        api_models = data.get("data", [])

        for model in api_models:
            model_id = model.get("id")
            if not model_id:
                continue

            # Extract pricing information with safe float conversion
            pricing_info = model.get("pricing", {})
            prompt_price = _safe_float(pricing_info.get("prompt", 0))
            completion_price = _safe_float(pricing_info.get("completion", 0))

            # Extract context length from model info
            context_length = model.get("context_length", 0)
            if not context_length:
                # Try to get from capabilities if available
                capabilities = model.get("capabilities", {})
                context_length = capabilities.get("context_length", 0)

            # Determine model type
            model_type = model.get("type", "chat")
            is_embedding = model_type == "embedding"

            model_data = {
                "id": model_id,
                "name": model.get("name", model_id),
                "description": model.get("description", ""),
                "provider": "canopywave",
                "provider_name": "Canopy Wave",
                "provider_slug": "canopywave",
                "source_gateway": "canopywave",
                "type": model_type,
                "context_length": context_length,
                "pricing": {
                    "prompt": str(prompt_price),
                    "completion": str(completion_price),
                    "request": "0",
                    "image": "0",
                },
            }

            # Add embedding-specific metadata
            if is_embedding:
                embedding_info = model.get("capabilities", {}).get("embedding", {})
                model_data["embedding_dimensions"] = embedding_info.get("dimensions", 0)

            # Add features/capabilities
            capabilities = model.get("capabilities", {})
            features = []
            if capabilities.get("tools") or capabilities.get("function_calling"):
                features.append("tools")
            if capabilities.get("json_mode"):
                features.append("json")
            if capabilities.get("structured_outputs"):
                features.append("structured_outputs")
            if capabilities.get("web_search"):
                features.append("web_search")
            if capabilities.get("reasoning"):
                features.append("reasoning")

            if features:
                model_data["features"] = features

            models.append(model_data)

        logger.info(f"Fetched {len(models)} models from Canopy Wave")
        return _cache_and_return(models)
    except httpx.HTTPStatusError as e:
        logger.error(
            f"HTTP error fetching models from Canopy Wave: {e.response.status_code} - {e.response.text}"
        )
        return []
    except Exception as e:
        logger.error(f"Failed to fetch models from Canopy Wave: {e}")
        return []


def is_canopywave_model(model_id: str) -> bool:
    """Check if a model ID is available on Canopy Wave.

    Args:
        model_id: The model ID to check

    Returns:
        True if model is available on Canopy Wave

    Note: This performs a live check against the Canopy Wave API.
    For better performance, consider caching the model list.
    """
    try:
        models = fetch_models_from_canopywave()
        return any(m.get("id") == model_id for m in models)
    except Exception as e:
        logger.error(f"Error checking if model is on Canopy Wave: {e}")
        return False
