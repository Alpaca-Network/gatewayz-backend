import logging

from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_xai_pooled_client
from src.utils.model_name_validator import clean_model_name

# Initialize logging
logger = logging.getLogger(__name__)

# Grok models that support reasoning/thinking capabilities
# These models can use the reasoning parameter to enable/disable extended thinking
XAI_REASONING_MODELS = {
    "grok-3-mini",
    "grok-3-mini-beta",
    "grok-4",
    "grok-4-fast",
    "grok-4.1-fast",
    "grok-4-1-fast-reasoning",
    "grok-4.1-fast-reasoning",
}

# Models that explicitly do NOT use reasoning (faster responses)
XAI_NON_REASONING_MODELS = {
    "grok-4-1-fast-non-reasoning",
    "grok-4.1-fast-non-reasoning",
    "grok-2",
    "grok-2-1212",
    "grok-beta",
    "grok-vision-beta",
}


def is_xai_reasoning_model(model: str) -> bool:
    """Check if a model supports reasoning capabilities.

    Args:
        model: Model name/ID

    Returns:
        True if the model supports reasoning, False otherwise
    """
    model_lower = model.lower()
    # Extract the base model name (handle prefixed model IDs like "xai/grok-4")
    base_model = model_lower.split("/")[-1] if "/" in model_lower else model_lower

    # Check if explicitly non-reasoning first (takes precedence)
    for non_reasoning_model in XAI_NON_REASONING_MODELS:
        if base_model == non_reasoning_model or base_model.startswith(f"{non_reasoning_model}-"):
            return False

    # Check explicit reasoning models (exact match or with suffix)
    for reasoning_model in XAI_REASONING_MODELS:
        if base_model == reasoning_model or base_model.startswith(f"{reasoning_model}-"):
            return True

    # Default: only known grok reasoning models should return True
    return False


def get_xai_reasoning_params(model: str, enable_reasoning: bool | None = None) -> dict:
    """Get reasoning parameters for xAI models.

    Args:
        model: Model name/ID
        enable_reasoning: Override to enable/disable reasoning.
                         If None, uses model default.

    Returns:
        Dict with reasoning parameters to pass to the API
    """
    if not is_xai_reasoning_model(model):
        return {}

    # If explicitly set, use that value
    if enable_reasoning is not None:
        return {"reasoning": {"enabled": enable_reasoning}}

    # Default: enable reasoning for reasoning-capable models
    # This ensures reasoning tokens are streamed to the client
    return {"reasoning": {"enabled": True}}


def get_xai_client():
    """Get xAI client with connection pooling for better performance

    xAI provides Grok models through their official SDK.
    Falls back to OpenAI SDK with custom base URL if official SDK is not available.
    Base URL (for OpenAI SDK fallback): https://api.x.ai/v1
    """
    try:
        if not Config.XAI_API_KEY:
            raise ValueError("xAI API key not configured")

        # Try using the official xAI SDK first
        try:
            from xai_sdk import Client

            return Client(api_key=Config.XAI_API_KEY)
        except ImportError:
            # Fallback to pooled OpenAI client with xAI base URL for better performance
            logger.info("xAI SDK not available, using pooled OpenAI SDK with xAI base URL")
            return get_xai_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize xAI client: {e}")
        raise


def make_xai_request_openai(messages, model, **kwargs):
    """Make request to xAI using official SDK or OpenAI-compatible client

    Args:
        messages: List of message objects
        model: Model name (e.g., "grok-4", "grok-3", "grok-2", "grok-beta")
        **kwargs: Additional parameters like max_tokens, temperature, etc.
            - enable_reasoning: Optional bool to explicitly enable/disable reasoning
    """
    try:
        client = get_xai_client()

        # Extract and handle reasoning parameter
        enable_reasoning = kwargs.pop("enable_reasoning", None)
        reasoning_params = get_xai_reasoning_params(model, enable_reasoning)

        # Merge reasoning params with kwargs (kwargs takes precedence if reasoning already set)
        if reasoning_params and "reasoning" not in kwargs:
            kwargs.update(reasoning_params)
            logger.debug(f"xAI request for {model} with reasoning params: {reasoning_params}")

        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"xAI request failed: {e}")
        raise


def make_xai_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to xAI using official SDK or OpenAI-compatible client

    Args:
        messages: List of message objects
        model: Model name (e.g., "grok-4", "grok-3", "grok-2", "grok-beta")
        **kwargs: Additional parameters like max_tokens, temperature, etc.
            - enable_reasoning: Optional bool to explicitly enable/disable reasoning
    """
    try:
        client = get_xai_client()

        # Extract and handle reasoning parameter
        enable_reasoning = kwargs.pop("enable_reasoning", None)
        reasoning_params = get_xai_reasoning_params(model, enable_reasoning)

        # Merge reasoning params with kwargs (kwargs takes precedence if reasoning already set)
        if reasoning_params and "reasoning" not in kwargs:
            kwargs.update(reasoning_params)
            logger.debug(
                f"xAI streaming request for {model} with reasoning params: {reasoning_params}"
            )

        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"xAI streaming request failed: {e}")
        raise


def process_xai_response(response):
    """Process xAI response to extract relevant data"""
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
        logger.error(f"Failed to process xAI response: {e}")
        raise


def fetch_models_from_xai():
    """Fetch models from xAI API

    xAI does not provide a public API to list available models.
    Tries database fallback first (from last successful sync),
    then returns a hardcoded list of known xAI Grok models.
    """
    logger.info("xAI does not provide a public model listing API, using fallback models")

    # Try database fallback first (dynamic, from last successful sync)
    try:
        from src.services.models import get_fallback_models_from_db

        db_fallback = get_fallback_models_from_db("xai")
        if db_fallback:
            normalized_models = []
            for db_model in db_fallback:
                provider_model_id = db_model.get("id") or db_model.get("model_id")
                if not provider_model_id:
                    continue

                # Get and clean model name from database
                raw_name = db_model.get("name") or provider_model_id.replace("-", " ").title()
                cleaned_name = clean_model_name(raw_name)

                model = {
                    "id": provider_model_id,
                    "slug": provider_model_id,
                    "canonical_slug": provider_model_id,
                    "name": cleaned_name,
                    "description": db_model.get("description") or f"xAI Grok model: {provider_model_id}",
                    "context_length": db_model.get("context_length") or 131072,
                    "architecture": db_model.get("metadata", {}).get("architecture") or {
                        "modality": "text->text",
                        "input_modalities": ["text"],
                        "output_modalities": ["text"],
                    },
                    "pricing": db_model.get("pricing") or {
                        "prompt": "5", "completion": "15", "request": "0", "image": "0",
                    },
                    "provider_slug": "xai",
                    "source_gateway": "xai",
                }
                normalized_models.append(model)

            if normalized_models:
                logger.info(f"Using {len(normalized_models)} xAI models from database fallback")
                return normalized_models
    except Exception as e:
        logger.warning(f"Failed to get database fallback for xAI: {e}")

    # Static fallback as last resort
    logger.warning("Database fallback empty, using static fallback for xAI")

    # Hardcoded list of known xAI Grok models
    # These are the models available through xAI's API
    return [
        {
            "id": "grok-beta",
            "slug": "grok-beta",
            "canonical_slug": "grok-beta",
            "name": "Grok Beta",
            "description": "xAI's Grok model (beta version) - A conversational AI assistant",
            "context_length": 131072,
            "architecture": {
                "modality": "text->text",
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
            "pricing": {
                "prompt": "5",
                "completion": "15",
                "request": "0",
                "image": "0",
            },
            "provider_slug": "xai",
            "source_gateway": "xai",
        },
        {
            "id": "grok-2",
            "slug": "grok-2",
            "canonical_slug": "grok-2",
            "name": "Grok 2",
            "description": "xAI's Grok 2 model - Advanced conversational AI",
            "context_length": 131072,
            "architecture": {
                "modality": "text->text",
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
            "pricing": {
                "prompt": "5",
                "completion": "15",
                "request": "0",
                "image": "0",
            },
            "provider_slug": "xai",
            "source_gateway": "xai",
        },
        {
            "id": "grok-2-1212",
            "slug": "grok-2-1212",
            "canonical_slug": "grok-2-1212",
            "name": "Grok 2 1212",
            "description": "xAI's Grok 2 model from December 2024",
            "context_length": 131072,
            "architecture": {
                "modality": "text->text",
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
            "pricing": {
                "prompt": "5",
                "completion": "15",
                "request": "0",
                "image": "0",
            },
            "provider_slug": "xai",
            "source_gateway": "xai",
        },
        {
            "id": "grok-vision-beta",
            "slug": "grok-vision-beta",
            "canonical_slug": "grok-vision-beta",
            "name": "Grok Vision Beta",
            "description": "xAI's Grok model with vision capabilities (beta)",
            "context_length": 8192,
            "architecture": {
                "modality": "text+image->text",
                "input_modalities": ["text", "image"],
                "output_modalities": ["text"],
            },
            "pricing": {
                "prompt": "5",
                "completion": "15",
                "request": "0",
                "image": "0",
            },
            "provider_slug": "xai",
            "source_gateway": "xai",
        },
    ]
