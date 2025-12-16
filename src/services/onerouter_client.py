import logging
from datetime import datetime, timezone

import httpx

from src.cache import _onerouter_models_cache
from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_onerouter_pooled_client
from src.utils.sentry_context import capture_provider_error

# Initialize logging
logger = logging.getLogger(__name__)


def get_onerouter_client():
    """Get OneRouter client with connection pooling for better performance

    OneRouter provides OpenAI-compatible API endpoints with automatic fallbacks,
    prompt caching (enabled by default), and multimodal support.
    """
    try:
        if not Config.ONEROUTER_API_KEY:
            raise ValueError("OneRouter API key not configured")

        # Use pooled client for ~10-20ms performance improvement per request
        return get_onerouter_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize OneRouter client: {e}")
        capture_provider_error(e, provider='onerouter', endpoint='client_init')
        raise


def make_onerouter_request_openai(messages, model, **kwargs):
    """Make request to OneRouter using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use (e.g., 'claude-3-5-sonnet@20240620')
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_onerouter_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"OneRouter request failed: {e}")
        capture_provider_error(
            e,
            provider='onerouter',
            model=model,
            endpoint='/chat/completions'
        )
        raise


def make_onerouter_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to OneRouter using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_onerouter_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"OneRouter streaming request failed: {e}")
        capture_provider_error(
            e,
            provider='onerouter',
            model=model,
            endpoint='/chat/completions (stream)'
        )
        raise


def process_onerouter_response(response):
    """Process OneRouter response to extract relevant data"""
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
        logger.error(f"Failed to process OneRouter response: {e}")
        capture_provider_error(
            e,
            provider='onerouter',
            endpoint='response_processing'
        )
        raise


def _parse_token_limit(value) -> int:
    """Parse token limit from various formats (string with commas, int, float, etc.)"""
    if value is None:
        return 4096
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            # Remove commas and convert to int
            return int(value.replace(",", ""))
        except ValueError:
            return 4096
    return 4096


def _parse_pricing(value) -> str:
    """Parse pricing value from various formats"""
    if value is None:
        return "0"
    if isinstance(value, str):
        # Remove $ sign and commas, then return as string
        return value.replace("$", "").replace(",", "").strip()
    return str(value)


def _fetch_display_models_pricing() -> dict:
    """Fetch pricing info from display_models endpoint and return as a lookup dict."""
    try:
        response = httpx.get(
            "https://app.onerouter.pro/api/display_models/",
            headers={"Content-Type": "application/json"},
            timeout=10.0,
            follow_redirects=True
        )
        response.raise_for_status()
        models = response.json().get("data", [])

        pricing_map = {}
        for model in models:
            model_id = model.get("invoke_name") or model.get("name", "")
            if not model_id:
                continue

            # Parse pricing (sale price if available, otherwise retail)
            prompt_price = _parse_pricing(model.get("sale_input_cost"))
            completion_price = _parse_pricing(model.get("sale_output_cost"))

            # If sale price is 0, use retail price
            try:
                if float(prompt_price) == 0:
                    prompt_price = _parse_pricing(model.get("retail_input_cost"))
            except ValueError:
                # Keep original price if parsing fails (e.g., malformed value)
                pass
            try:
                if float(completion_price) == 0:
                    completion_price = _parse_pricing(model.get("retail_output_cost"))
            except ValueError:
                # Keep original price if parsing fails (e.g., malformed value)
                pass

            pricing_map[model_id] = {
                "prompt": prompt_price,
                "completion": completion_price,
                "context_length": _parse_token_limit(model.get("input_token_limit")),
                "max_completion_tokens": _parse_token_limit(model.get("output_token_limit")),
            }
        return pricing_map
    except Exception as e:
        logger.warning(f"Failed to fetch display_models pricing: {e}")
        return {}


def fetch_models_from_onerouter():
    """Fetch models from OneRouter API

    OneRouter provides access to multiple AI models through their API.
    This function fetches the complete model list from the authenticated /v1/models
    endpoint and enriches it with pricing data from the display_models endpoint.

    Models are cached with a 1-hour TTL to reduce API calls and improve performance.
    """

    def _cache_and_return(models: list[dict]) -> list[dict]:
        """Cache models and return them"""
        _onerouter_models_cache["data"] = models
        _onerouter_models_cache["timestamp"] = datetime.now(timezone.utc)
        return models

    try:
        logger.info("Fetching models from OneRouter API...")

        if not Config.ONEROUTER_API_KEY:
            logger.warning("OneRouter API key not configured, skipping model fetch")
            return _cache_and_return([])

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {Config.ONEROUTER_API_KEY}",
        }

        # Use the authenticated /v1/models endpoint for complete model list
        response = httpx.get(
            "https://api.onerouter.pro/v1/models",
            headers=headers,
            timeout=15.0,
            follow_redirects=True
        )
        response.raise_for_status()

        models_data = response.json()
        models = models_data.get("data", [])

        # Fetch pricing data from display_models to enrich the model list
        pricing_map = _fetch_display_models_pricing()

        # Transform to our standard format
        # The /v1/models endpoint returns OpenAI-compatible format with fields:
        # id, object, created, owned_by
        transformed_models = []
        for model in models:
            # Use the model id directly (this is what's used for API calls)
            model_id = model.get("id", "")
            if not model_id:
                continue

            # Get pricing/context info from display_models if available
            pricing_info = pricing_map.get(model_id, {})
            context_length = pricing_info.get("context_length", 128000)
            max_completion_tokens = pricing_info.get("max_completion_tokens", 4096)
            prompt_price = pricing_info.get("prompt", "0")
            completion_price = pricing_info.get("completion", "0")

            # Build a readable name from the model ID
            model_name = model_id.replace("-", " ").replace("_", " ").title()

            # Build the full model ID with onerouter prefix for consistent display
            # This ensures models are grouped under "OneRouter" in the UI
            full_model_id = f"onerouter/{model_id}"

            transformed_model = {
                "id": full_model_id,
                "slug": model_id,
                "canonical_slug": model_id,
                "name": model_name,
                "description": f"OneRouter model: {model_name}",
                "context_length": context_length,
                "max_completion_tokens": max_completion_tokens,
                "architecture": {
                    "modality": "text->text",
                    "input_modalities": ["text"],
                    "output_modalities": ["text"],
                },
                "pricing": {
                    "prompt": prompt_price,
                    "completion": completion_price,
                    "request": "0",
                    "image": "0",
                },
                "provider_slug": "onerouter",
                "source_gateway": "onerouter",
            }
            transformed_models.append(transformed_model)

        logger.info(f"Successfully fetched {len(transformed_models)} models from OneRouter")
        return _cache_and_return(transformed_models)

    except httpx.HTTPStatusError as e:
        logger.error(
            f"HTTP error fetching OneRouter models: {e.response.status_code} - "
            f"{e.response.text[:200] if e.response.text else 'No response body'}"
        )
        capture_provider_error(
            e,
            provider='onerouter',
            endpoint='/v1/models'
        )
        return _cache_and_return([])
    except Exception as e:
        logger.error(f"Failed to fetch models from OneRouter: {type(e).__name__}: {e}")
        capture_provider_error(
            e,
            provider='onerouter',
            endpoint='/v1/models'
        )
        return _cache_and_return([])
