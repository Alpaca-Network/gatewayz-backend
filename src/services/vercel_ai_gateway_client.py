import logging

import httpx

from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_pooled_client

# Initialize logging
logger = logging.getLogger(__name__)

# Standard timeout for Vercel AI Gateway
VERCEL_TIMEOUT = httpx.Timeout(
    connect=5.0,
    read=60.0,
    write=10.0,
    pool=5.0,
)


def get_vercel_ai_gateway_client():
    """Get Vercel AI Gateway client using OpenAI-compatible interface with connection pooling

    Vercel AI Gateway is a unified interface to multiple AI providers with automatic failover,
    caching, and analytics. It provides access to hundreds of models across different providers.

    Base URL: https://ai-gateway.vercel.sh/v1
    Documentation: https://vercel.com/docs/ai-gateway
    """
    try:
        api_key = Config.VERCEL_AI_GATEWAY_API_KEY
        if not api_key:
            raise ValueError(
                "Vercel AI Gateway API key not configured. Please set VERCEL_AI_GATEWAY_API_KEY environment variable."
            )

        # Use connection pool with standard timeout
        return get_pooled_client(
            provider="vercel-ai-gateway",
            base_url="https://ai-gateway.vercel.sh/v1",
            api_key=api_key,
            timeout=VERCEL_TIMEOUT,
        )
    except Exception as e:
        logger.error(f"Failed to initialize Vercel AI Gateway client: {e}")
        raise


def make_vercel_ai_gateway_request_openai(messages, model, **kwargs):
    """Make request to Vercel AI Gateway using OpenAI client

    Args:
        messages: List of message objects
        model: Model name (e.g., "gpt-4", "claude-3-sonnet")
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_vercel_ai_gateway_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"Vercel AI Gateway request failed: {e}")
        raise


def make_vercel_ai_gateway_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Vercel AI Gateway using OpenAI client

    Args:
        messages: List of message objects
        model: Model name (e.g., "gpt-4", "claude-3-sonnet")
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_vercel_ai_gateway_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"Vercel AI Gateway streaming request failed: {e}")
        raise


def process_vercel_ai_gateway_response(response):
    """Process Vercel AI Gateway response to extract relevant data"""
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
        logger.error(f"Failed to process Vercel AI Gateway response: {e}")
        raise


# Cache for Vercel AI Gateway model pricing from public API
_vercel_pricing_cache: dict = {"data": None, "timestamp": None}
VERCEL_PRICING_CACHE_TTL = 3600  # 1 hour


def fetch_vercel_pricing_from_public_api() -> dict | None:
    """Fetch pricing data from Vercel AI Gateway's public models API

    The API is available at https://ai-gateway.vercel.sh/v1/models (no auth required)
    Pricing is returned per-token, so we convert to per-1M format.

    Returns:
        dict mapping model_id to pricing info, or None if fetch fails
    """
    import time

    # Check cache first
    if _vercel_pricing_cache["data"] is not None and _vercel_pricing_cache["timestamp"]:
        if time.time() - _vercel_pricing_cache["timestamp"] < VERCEL_PRICING_CACHE_TTL:
            return _vercel_pricing_cache["data"]

    try:
        response = httpx.get(
            "https://ai-gateway.vercel.sh/v1/models",
            timeout=10.0,
        )

        if response.status_code != 200:
            logger.warning(f"Vercel AI Gateway public API returned status {response.status_code}")
            return None

        data = response.json()
        models = data.get("data", [])

        pricing_map = {}
        for model in models:
            model_id = model.get("id")
            if not model_id:
                continue

            pricing = model.get("pricing", {})
            if pricing:
                # Vercel returns pricing per-token (e.g., "0.000003")
                # Convert to per-1M tokens for consistency with our format
                input_price = pricing.get("input", "0")
                output_price = pricing.get("output", "0")

                try:
                    # Convert per-token to per-1M tokens
                    prompt_per_1m = float(input_price) * 1_000_000
                    completion_per_1m = float(output_price) * 1_000_000

                    pricing_map[model_id] = {
                        "prompt": str(prompt_per_1m),
                        "completion": str(completion_per_1m),
                        "request": "0",
                        "image": str(float(pricing.get("image", "0")) if pricing.get("image") else "0"),
                    }

                    # Also store without provider prefix
                    if "/" in model_id:
                        model_name = model_id.split("/")[-1]
                        if model_name not in pricing_map:
                            pricing_map[model_name] = pricing_map[model_id]
                except (ValueError, TypeError) as e:
                    logger.debug(f"Failed to parse pricing for {model_id}: {e}")
                    continue

        # Update cache
        _vercel_pricing_cache["data"] = pricing_map
        _vercel_pricing_cache["timestamp"] = time.time()

        logger.info(f"Fetched pricing for {len(pricing_map)} models from Vercel AI Gateway public API")
        return pricing_map

    except (httpx.RequestError, httpx.TimeoutException) as e:
        logger.warning(f"Failed to fetch Vercel AI Gateway pricing from public API: {e}")
        return None
    except Exception as e:
        logger.error(f"Error parsing Vercel AI Gateway pricing data: {e}")
        return None


def fetch_model_pricing_from_vercel(model_id: str):
    """Fetch pricing information for a specific model from Vercel AI Gateway

    Vercel AI Gateway provides a public API at https://ai-gateway.vercel.sh/v1/models
    that returns model information including pricing.

    Args:
        model_id: Model identifier (e.g., "openai/gpt-4", "claude-3-sonnet")

    Returns:
        dict with 'prompt' and 'completion' pricing per 1M tokens, or None if not available
    """
    try:
        # Fetch pricing from public API (no auth required)
        pricing_map = fetch_vercel_pricing_from_public_api()

        if pricing_map:
            # Try exact match first
            if model_id in pricing_map:
                return pricing_map[model_id]

            # Try without provider prefix
            model_name = model_id.split("/")[-1] if "/" in model_id else model_id
            if model_name in pricing_map:
                return pricing_map[model_name]

            # Try with common provider prefixes
            for prefix in ["anthropic", "openai", "google", "meta-llama", "amazon", "deepseek"]:
                prefixed_id = f"{prefix}/{model_name}"
                if prefixed_id in pricing_map:
                    return pricing_map[prefixed_id]

        # Fallback: use provider-specific pricing lookup
        return get_provider_pricing_for_vercel_model(model_id)

    except Exception as e:
        logger.error(f"Failed to fetch pricing for Vercel model {model_id}: {e}")
        return None


def get_provider_pricing_for_vercel_model(model_id: str):
    """Get pricing for a Vercel model by looking up the underlying provider's pricing

    Vercel routes models to providers like OpenAI, Google, Anthropic, etc.
    We can determine pricing by identifying the provider and looking up their rates.

    Args:
        model_id: Model identifier (e.g., "openai/gpt-4", "anthropic/claude-3-sonnet")

    Returns:
        dict with 'prompt' and 'completion' pricing per 1M tokens
    """
    try:
        # Cross-reference with known provider pricing from the system
        # This leverages the existing pricing infrastructure
        try:
            from src.services.pricing import get_model_pricing

            # Try the full model ID first
            pricing = get_model_pricing(model_id)
            if pricing and pricing.get("found"):
                return {
                    "prompt": pricing.get("prompt", "0"),
                    "completion": pricing.get("completion", "0"),
                }

            # Try without the provider prefix
            model_name_only = model_id.split("/")[-1] if "/" in model_id else model_id
            pricing = get_model_pricing(model_name_only)
            if pricing and pricing.get("found"):
                return {
                    "prompt": pricing.get("prompt", "0"),
                    "completion": pricing.get("completion", "0"),
                }
        except ImportError:
            logger.debug("pricing module not available for cross-reference")

        return None

    except Exception as e:
        logger.debug(f"Failed to get provider pricing for {model_id}: {e}")
        return None
