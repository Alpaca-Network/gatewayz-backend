"""
Sybil AI provider client module.

Sybil provides OpenAI-compatible API endpoints for various LLM models with
fast inference on GPU infrastructure.

API Documentation: https://docs.sybil.com/
Base URL: https://api.sybil.com/v1
Models Endpoint: https://api.sybil.com/v1/models

The Sybil API is compatible with the OpenAI API format, making integration
straightforward. Authentication uses bearer token authentication via API keys.

Available models (as of API response):
- deepseek-ai/DeepSeek-V3-0324: $0.0000019/prompt, $0.000002/completion, 163,840 context
- mistralai/Mistral-7B-Instruct-v0.3: $0.000001/prompt, $0.000002/completion, 32,768 context
- moonshotai/Kimi-K2-Thinking: $0.000001/prompt, $0.000002/completion, 262,144 context
- Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8: $0.00000125/prompt, $0.000002/completion, 262,144 context
- zai-org/GLM-4.5: $0.00000140/prompt, $0.000002/completion, 131,072 context
- zai-org/GLM-4.6: $0.00000150/prompt, $0.000002/completion, 202,752 context
- distilbert/distilbert-base-uncased: Embedding model, 512 dimensions
- Qwen/Qwen3-Embedding-8B: Embedding model, 4,096 dimensions

All pricing is per token (already in per-token format, not per 1M).
"""

import logging

import httpx

from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_sybil_pooled_client
from src.utils.model_name_validator import clean_model_name

# Initialize logging
logger = logging.getLogger(__name__)

# Sybil base URL
SYBIL_BASE_URL = "https://api.sybil.com/v1"
SYBIL_MODELS_ENDPOINT = f"{SYBIL_BASE_URL}/models"


def get_sybil_client():
    """Get Sybil client with connection pooling for better performance.

    Sybil provides OpenAI-compatible API endpoints for various models.
    """
    try:
        if not Config.SYBIL_API_KEY:
            raise ValueError("Sybil API key not configured")

        # Use pooled client for ~10-20ms performance improvement per request
        return get_sybil_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize Sybil client: {e}")
        raise


def make_sybil_request_openai(messages, model, **kwargs):
    """Make request to Sybil using OpenAI client.

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_sybil_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"Sybil request failed: {e}")
        raise


def make_sybil_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Sybil using OpenAI client.

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_sybil_client()
        stream = client.chat.completions.create(model=model, messages=messages, stream=True, **kwargs)
        return stream
    except Exception as e:
        logger.error(f"Sybil streaming request failed: {e}")
        raise


def process_sybil_response(response):
    """Process Sybil response to extract relevant data.

    Args:
        response: OpenAI-format response object

    Returns:
        Normalized response dictionary
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
        logger.error(f"Failed to process Sybil response: {e}")
        raise


def fetch_models_from_sybil():
    """Fetch available models from Sybil API.

    Returns a list of model info dictionaries in catalog format.
    Fetches models dynamically from the Sybil /v1/models endpoint.

    The API returns models with pricing information and capabilities.
    """
    try:
        # Fetch models from API endpoint
        with httpx.Client(timeout=30.0) as client:
            headers = {"Authorization": f"Bearer {Config.SYBIL_API_KEY}"}
            response = client.get(SYBIL_MODELS_ENDPOINT, headers=headers)
            response.raise_for_status()
            data = response.json()

        models = []
        api_models = data.get("data", [])

        for model in api_models:
            model_id = model.get("id")
            if not model_id:
                continue

            # Extract pricing information
            pricing_info = model.get("pricing", {})
            prompt_price = float(pricing_info.get("prompt", 0))
            completion_price = float(pricing_info.get("completion", 0))

            # Extract capabilities
            capabilities = model.get("capabilities", {})
            context_length = capabilities.get("context_length", 0)

            # Determine model type
            model_type = model.get("type", "chat")
            is_embedding = model_type == "embedding"

            # Get and clean model name (remove colons, parentheses, etc.)
            raw_name = model.get("name", model_id)
            clean_name = clean_model_name(raw_name)

            model_data = {
                "id": model_id,
                "name": clean_name,
                "description": model.get("description", ""),
                "provider": "sybil",
                "provider_name": "Sybil",
                "provider_slug": "sybil",
                "source_gateway": "sybil",
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
                embedding_info = capabilities.get("embedding", {})
                model_data["embedding_dimensions"] = embedding_info.get("dimensions", 0)

            # Add features/capabilities
            features = []
            if capabilities.get("tools"):
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

        logger.info(f"Fetched {len(models)} models from Sybil")
        return models
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error fetching models from Sybil: {e.response.status_code} - {e.response.text}")
        return []
    except Exception as e:
        logger.error(f"Failed to fetch models from Sybil: {e}")
        return []


def is_sybil_model(model_id: str) -> bool:
    """Check if a model ID is available on Sybil.

    Args:
        model_id: The model ID to check

    Returns:
        True if model is available on Sybil

    Note: This performs a live check against the Sybil API.
    For better performance, consider caching the model list.
    """
    try:
        models = fetch_models_from_sybil()
        return any(m.get("id") == model_id for m in models)
    except Exception as e:
        logger.error(f"Error checking if model is on Sybil: {e}")
        return False
