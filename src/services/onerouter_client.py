import logging

import httpx

from src.services.model_catalog_cache import cache_gateway_catalog
from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_onerouter_pooled_client
from src.services.pricing_lookup import get_model_pricing
from src.utils.sentry_context import capture_provider_error

# Initialize logging
logger = logging.getLogger(__name__)


def get_onerouter_client():
    """Get Infron AI client with connection pooling for better performance

    Infron AI (formerly OneRouter) provides OpenAI-compatible API endpoints with automatic fallbacks,
    prompt caching (enabled by default), and multimodal support.
    """
    try:
        if not Config.ONEROUTER_API_KEY:
            raise ValueError("Infron AI API key not configured")

        # Use pooled client for ~10-20ms performance improvement per request
        return get_onerouter_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize Infron AI client: {e}")
        capture_provider_error(e, provider='onerouter', endpoint='client_init')
        raise


def make_onerouter_request_openai(messages, model, **kwargs):
    """Make request to Infron AI using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use (e.g., 'claude-3-5-sonnet@20240620')
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        from src.utils.provider_timing import ProviderTimingContext

        client = get_onerouter_client()

        with ProviderTimingContext("onerouter", model, "non_stream"):
            response = client.chat.completions.create(model=model, messages=messages, **kwargs)

        return response
    except Exception as e:
        logger.error(f"Infron AI request failed: {e}")
        capture_provider_error(
            e,
            provider='onerouter',
            model=model,
            endpoint='/chat/completions'
        )
        raise


def make_onerouter_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Infron AI using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        from src.utils.provider_timing import ProviderTimingContext

        client = get_onerouter_client()

        with ProviderTimingContext("onerouter", model, "stream"):
            stream = client.chat.completions.create(
                model=model, messages=messages, stream=True, **kwargs
            )

        return stream
    except Exception as e:
        logger.error(f"Infron AI streaming request failed: {e}")
        capture_provider_error(
            e,
            provider='onerouter',
            model=model,
            endpoint='/chat/completions (stream)'
        )
        raise


def process_onerouter_response(response):
    """Process Infron AI response to extract relevant data"""
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
        logger.error(f"Failed to process Infron AI response: {e}")
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
            "https://app.infron.ai/api/display_models/",
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

            # Parse modalities (handle null values from API)
            input_modalities_str = model.get("input_modalities") or "Text"
            output_modalities_str = model.get("output_modalities") or "Text"
            input_modalities = [m.strip().lower() for m in input_modalities_str.split(",")]
            output_modalities = [m.strip().lower() for m in output_modalities_str.split(",")]

            # Determine modality type
            has_image_input = any(m in ["images", "image"] for m in input_modalities)
            modality = "text+image->text" if has_image_input else "text->text"

            # Parse context lengths, using 128000 as default if not provided
            input_token_limit = model.get("input_token_limit")
            output_token_limit = model.get("output_token_limit")
            context_length = _parse_token_limit(input_token_limit) if input_token_limit else 128000
            max_completion_tokens = _parse_token_limit(output_token_limit) if output_token_limit else 4096

            pricing_map[model_id] = {
                "prompt": prompt_price,
                "completion": completion_price,
                "context_length": context_length,
                "max_completion_tokens": max_completion_tokens,
                "input_modalities": input_modalities,
                "output_modalities": output_modalities,
                "modality": modality,
            }
        return pricing_map
    except Exception as e:
        logger.warning(f"Failed to fetch display_models pricing: {e}")
        return {}


def fetch_models_from_onerouter():
    """Fetch models from Infron AI API

    Infron AI (formerly OneRouter) provides access to multiple AI models through their API.
    This function fetches the complete model list from the authenticated /v1/models
    endpoint and enriches it with pricing data from the display_models endpoint.

    Models are cached with a 1-hour TTL to reduce API calls and improve performance.
    """

    def _cache_and_return(models: list[dict]) -> list[dict]:
        """Cache models and return them"""
        # Cache models in Redis with automatic TTL and error tracking
        cache_gateway_catalog("onerouter", models)
        return models

    try:
        logger.info("Fetching models from Infron AI API...")

        if not Config.ONEROUTER_API_KEY:
            logger.warning("Infron AI API key not configured, skipping model fetch")
            return _cache_and_return([])

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {Config.ONEROUTER_API_KEY}",
        }

        # Use the authenticated /v1/models endpoint for complete model list
        response = httpx.get(
            "https://api.infron.ai/v1/models",
            headers=headers,
            timeout=15.0,
            follow_redirects=True
        )
        response.raise_for_status()

        models_data = response.json()
        models = models_data.get("data", [])

        # Fetch pricing data from display_models to enrich the model list
        pricing_map = _fetch_display_models_pricing()

        if pricing_map:
            logger.debug(f"Loaded pricing data for {len(pricing_map)} models from display_models")

        # Transform to our standard format
        # The /v1/models endpoint returns OpenAI-compatible format with fields:
        # id, object, created, owned_by
        transformed_models = []
        enriched_count = 0
        manual_pricing_count = 0
        filtered_count = 0
        for model in models:
            # Use the model id directly (this is what's used for API calls)
            model_id = model.get("id", "")
            if not model_id:
                continue

            # Get pricing/context info from display_models if available
            # Note: We assume the 'id' from /v1/models matches 'invoke_name' from display_models
            pricing_info = pricing_map.get(model_id, {})
            pricing_source = "api"
            if pricing_info:
                enriched_count += 1
                prompt_price = pricing_info.get("prompt", "0")
                completion_price = pricing_info.get("completion", "0")
            else:
                # Fallback to manual_pricing.json for models not in display_models API
                manual_pricing = get_model_pricing("onerouter", model_id)
                if manual_pricing:
                    prompt_price = manual_pricing.get("prompt", "0")
                    completion_price = manual_pricing.get("completion", "0")
                    pricing_source = "manual"
                    manual_pricing_count += 1
                else:
                    # Filter out models without valid pricing to prevent them appearing as free
                    logger.debug(f"Filtering out Infron AI model {model_id} - no pricing available")
                    filtered_count += 1
                    continue

            # Validate we have non-zero pricing (don't show free models)
            try:
                if float(prompt_price) == 0 and float(completion_price) == 0:
                    logger.debug(f"Filtering out Infron AI model {model_id} - zero pricing")
                    filtered_count += 1
                    continue
            except (ValueError, TypeError):
                # Keep model if we can't parse pricing (assume it's valid)
                pass

            context_length = pricing_info.get("context_length", 128000)
            max_completion_tokens = pricing_info.get("max_completion_tokens", 4096)
            modality = pricing_info.get("modality", "text->text")
            input_modalities = pricing_info.get("input_modalities", ["text"])
            output_modalities = pricing_info.get("output_modalities", ["text"])

            # Build a readable name from the model ID
            model_name = model_id.replace("-", " ").replace("_", " ").title()

            # Build the full model ID with onerouter prefix for consistent display
            # This ensures models are grouped under "Infron AI" in the UI
            full_model_id = f"onerouter/{model_id}"

            transformed_model = {
                "id": full_model_id,
                "slug": model_id,
                "canonical_slug": model_id,
                "name": model_name,
                "description": f"Infron AI model: {model_name}",
                "context_length": context_length,
                "max_completion_tokens": max_completion_tokens,
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
                "pricing_source": pricing_source,
                "provider_slug": "onerouter",
                "source_gateway": "onerouter",
            }
            transformed_models.append(transformed_model)

        logger.info(
            f"Successfully fetched {len(transformed_models)} models from Infron AI "
            f"({enriched_count} from API, {manual_pricing_count} from manual pricing, "
            f"{filtered_count} filtered for zero/missing pricing)"
        )
        return _cache_and_return(transformed_models)

    except httpx.HTTPStatusError as e:
        logger.error(
            f"HTTP error fetching Infron AI models: {e.response.status_code} - "
            f"{e.response.text[:200] if e.response.text else 'No response body'}"
        )
        capture_provider_error(
            e,
            provider='onerouter',
            endpoint='/v1/models'
        )
        return _cache_and_return([])
    except Exception as e:
        logger.error(f"Failed to fetch models from Infron AI: {type(e).__name__}: {e}")
        capture_provider_error(
            e,
            provider='onerouter',
            endpoint='/v1/models'
        )
        return _cache_and_return([])
