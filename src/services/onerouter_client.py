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


def fetch_models_from_onerouter():
    """Fetch models from OneRouter API

    OneRouter provides access to multiple AI models through their API.
    This function fetches the list of available models from the public
    display_models endpoint which does not require authentication.

    Models are cached with a 1-hour TTL to reduce API calls and improve performance.
    """

    def _cache_and_return(models: list[dict]) -> list[dict]:
        """Cache models and return them"""
        _onerouter_models_cache["data"] = models
        _onerouter_models_cache["timestamp"] = datetime.now(timezone.utc)
        return models

    try:
        logger.info("Fetching models from OneRouter API...")
        headers = {
            "Content-Type": "application/json",
        }

        # Use the public display_models endpoint (no auth required)
        # This endpoint returns comprehensive model information including pricing
        response = httpx.get(
            "https://app.onerouter.pro/api/display_models/",
            headers=headers,
            timeout=15.0,
            follow_redirects=True
        )
        response.raise_for_status()

        models_data = response.json()
        models = models_data.get("data", [])

        # Transform to our standard format
        transformed_models = []
        for model in models:
            # Use invoke_name as the model ID (this is what's used for API calls)
            model_id = model.get("invoke_name") or model.get("name", "")
            if not model_id:
                continue

            # Parse context lengths from string format (e.g., "131,072")
            input_token_limit = _parse_token_limit(model.get("input_token_limit"))
            output_token_limit = _parse_token_limit(model.get("output_token_limit"))

            # Parse input/output modalities (handle null values from API)
            input_modalities_str = model.get("input_modalities") or "Text"
            output_modalities_str = model.get("output_modalities") or "Text"
            input_modalities = [m.strip().lower() for m in input_modalities_str.split(",")]
            output_modalities = [m.strip().lower() for m in output_modalities_str.split(",")]

            # Determine modality type
            has_image_input = any(m in ["images", "image"] for m in input_modalities)
            modality = "text+image->text" if has_image_input else "text->text"

            # Parse pricing (sale price if available, otherwise retail)
            prompt_price = _parse_pricing(model.get("sale_input_cost"))
            completion_price = _parse_pricing(model.get("sale_output_cost"))

            # If sale price is 0 (handles "0", "0.0", "0.00", etc.), use retail price
            try:
                if float(prompt_price) == 0:
                    prompt_price = _parse_pricing(model.get("retail_input_cost"))
            except ValueError:
                logger.warning(f"Could not parse prompt_price '{prompt_price}' as float for model '{model_id}'; skipping sale-to-retail fallback")
            try:
                if float(completion_price) == 0:
                    completion_price = _parse_pricing(model.get("retail_output_cost"))
            except ValueError:
                pass

            transformed_model = {
                "id": model_id,
                "slug": model_id,
                "canonical_slug": model_id,
                "name": model.get("name", model_id),
                "description": f"OneRouter model: {model.get('name', model_id)}",
                "context_length": input_token_limit,
                "max_completion_tokens": output_token_limit,
                "architecture": {
                    "modality": modality,
                    "input_modalities": input_modalities,
                    "output_modalities": output_modalities,
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
            endpoint='/api/display_models'
        )
        return _cache_and_return([])
    except Exception as e:
        logger.error(f"Failed to fetch models from OneRouter: {type(e).__name__}: {e}")
        capture_provider_error(
            e,
            provider='onerouter',
            endpoint='/api/display_models'
        )
        return _cache_and_return([])
