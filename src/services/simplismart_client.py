"""
Simplismart AI provider client module.

Simplismart provides OpenAI-compatible API endpoints for various LLM models
including Llama, Gemma, Qwen, DeepSeek, Mixtral, and more.

API Documentation: https://docs.simplismart.ai/overview
Base URL: https://api.simplismart.live

Supported models:
- meta-llama/Meta-Llama-3.1-8B-Instruct
- meta-llama/Meta-Llama-3.1-70B-Instruct
- meta-llama/Llama-3.3-70B-Instruct
- meta-llama/Llama-4-Maverick-17B-Instruct (preview)
- deepseek-ai/DeepSeek-R1-Distill-Llama-70B
- deepseek-ai/DeepSeek-R1-Distill-Qwen-32B
- google/gemma-3-1b-it
- google/gemma-3-4b-it
- google/gemma-3-27b-it
- Qwen/Qwen2.5-14B-Instruct
- Qwen/Qwen2.5-32B-Instruct
- mistralai/Mixtral-8x7B-Instruct-v0.1-FP8
- mistralai/Devstral-Small-2505
"""

import logging

from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_simplismart_pooled_client

# Initialize logging
logger = logging.getLogger(__name__)

# Simplismart base URL
SIMPLISMART_BASE_URL = "https://api.simplismart.live"

# Simplismart model catalog - models available via the API
SIMPLISMART_MODELS = {
    # Llama 3.1 series
    "meta-llama/Meta-Llama-3.1-8B-Instruct": {
        "name": "Meta Llama 3.1 8B Instruct",
        "context_length": 131072,
        "description": "Meta's Llama 3.1 8B parameter instruction-tuned model",
    },
    "meta-llama/Meta-Llama-3.1-70B-Instruct": {
        "name": "Meta Llama 3.1 70B Instruct",
        "context_length": 131072,
        "description": "Meta's Llama 3.1 70B parameter instruction-tuned model",
    },
    # Llama 3.3 series
    "meta-llama/Llama-3.3-70B-Instruct": {
        "name": "Meta Llama 3.3 70B Instruct",
        "context_length": 131072,
        "description": "Meta's Llama 3.3 70B parameter instruction-tuned model",
    },
    # Llama 4 series (preview)
    "meta-llama/Llama-4-Maverick-17B-Instruct": {
        "name": "Meta Llama 4 Maverick 17B Instruct",
        "context_length": 131072,
        "description": "Meta's Llama 4 Maverick 17B parameter instruction-tuned model (preview)",
    },
    # DeepSeek R1 Distill series
    "deepseek-ai/DeepSeek-R1-Distill-Llama-70B": {
        "name": "DeepSeek R1 Distill Llama 70B",
        "context_length": 65536,
        "description": "DeepSeek R1 distilled into Llama 70B architecture",
    },
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B": {
        "name": "DeepSeek R1 Distill Qwen 32B",
        "context_length": 65536,
        "description": "DeepSeek R1 distilled into Qwen 32B architecture",
    },
    # Gemma 3 series
    "google/gemma-3-1b-it": {
        "name": "Google Gemma 3 1B IT",
        "context_length": 8192,
        "description": "Google's Gemma 3 1B instruction-tuned model",
    },
    "google/gemma-3-4b-it": {
        "name": "Google Gemma 3 4B IT",
        "context_length": 8192,
        "description": "Google's Gemma 3 4B instruction-tuned model",
    },
    "google/gemma-3-27b-it": {
        "name": "Google Gemma 3 27B IT",
        "context_length": 8192,
        "description": "Google's Gemma 3 27B instruction-tuned model",
    },
    # Qwen 2.5 series
    "Qwen/Qwen2.5-14B-Instruct": {
        "name": "Qwen 2.5 14B Instruct",
        "context_length": 32768,
        "description": "Alibaba's Qwen 2.5 14B instruction-tuned model",
    },
    "Qwen/Qwen2.5-32B-Instruct": {
        "name": "Qwen 2.5 32B Instruct",
        "context_length": 32768,
        "description": "Alibaba's Qwen 2.5 32B instruction-tuned model",
    },
    # Mixtral series
    "mistralai/Mixtral-8x7B-Instruct-v0.1-FP8": {
        "name": "Mixtral 8x7B Instruct FP8",
        "context_length": 32768,
        "description": "Mistral's Mixtral 8x7B MoE instruction model (FP8 quantized)",
    },
    # Devstral series
    "mistralai/Devstral-Small-2505": {
        "name": "Devstral Small 2505",
        "context_length": 32768,
        "description": "Mistral's Devstral Small coding assistant model",
    },
}

# Model ID aliases for user convenience
SIMPLISMART_MODEL_ALIASES = {
    # Llama 3.1 aliases
    "llama-3.1-8b": "meta-llama/Meta-Llama-3.1-8B-Instruct",
    "llama-3.1-8b-instruct": "meta-llama/Meta-Llama-3.1-8B-Instruct",
    "meta-llama-3.1-8b": "meta-llama/Meta-Llama-3.1-8B-Instruct",
    "llama-3.1-70b": "meta-llama/Meta-Llama-3.1-70B-Instruct",
    "llama-3.1-70b-instruct": "meta-llama/Meta-Llama-3.1-70B-Instruct",
    "meta-llama-3.1-70b": "meta-llama/Meta-Llama-3.1-70B-Instruct",
    # Llama 3.3 aliases
    "llama-3.3-70b": "meta-llama/Llama-3.3-70B-Instruct",
    "llama-3.3-70b-instruct": "meta-llama/Llama-3.3-70B-Instruct",
    "meta-llama-3.3-70b": "meta-llama/Llama-3.3-70B-Instruct",
    # Llama 4 aliases
    "llama-4-maverick": "meta-llama/Llama-4-Maverick-17B-Instruct",
    "llama-4-maverick-17b": "meta-llama/Llama-4-Maverick-17B-Instruct",
    # DeepSeek aliases
    "deepseek-r1-distill-llama-70b": "deepseek-ai/DeepSeek-R1-Distill-Llama-70B",
    "deepseek-r1-distill-qwen-32b": "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B",
    # Gemma aliases
    "gemma-3-1b": "google/gemma-3-1b-it",
    "gemma-3-4b": "google/gemma-3-4b-it",
    "gemma-3-27b": "google/gemma-3-27b-it",
    # Qwen aliases
    "qwen-2.5-14b": "Qwen/Qwen2.5-14B-Instruct",
    "qwen-2.5-32b": "Qwen/Qwen2.5-32B-Instruct",
    "qwen2.5-14b": "Qwen/Qwen2.5-14B-Instruct",
    "qwen2.5-32b": "Qwen/Qwen2.5-32B-Instruct",
    # Mixtral aliases
    "mixtral-8x7b": "mistralai/Mixtral-8x7B-Instruct-v0.1-FP8",
    "mixtral-8x7b-instruct": "mistralai/Mixtral-8x7B-Instruct-v0.1-FP8",
    # Devstral aliases
    "devstral-small": "mistralai/Devstral-Small-2505",
}


def get_simplismart_client():
    """Get Simplismart client with connection pooling for better performance.

    Simplismart provides OpenAI-compatible API endpoints for various models.
    """
    try:
        if not Config.SIMPLISMART_API_KEY:
            raise ValueError("Simplismart API key not configured")

        # Use pooled client for ~10-20ms performance improvement per request
        return get_simplismart_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize Simplismart client: {e}")
        raise


def resolve_simplismart_model(model_id: str) -> str:
    """Resolve model ID to Simplismart-specific format.

    Args:
        model_id: Input model ID (can be alias or full name)

    Returns:
        Simplismart-compatible model ID
    """
    # Check aliases first (case-insensitive)
    lower_model = model_id.lower()
    if lower_model in SIMPLISMART_MODEL_ALIASES:
        resolved = SIMPLISMART_MODEL_ALIASES[lower_model]
        logger.debug(f"Resolved Simplismart model alias '{model_id}' -> '{resolved}'")
        return resolved

    # Check if model exists in catalog
    if model_id in SIMPLISMART_MODELS:
        return model_id

    # Try case-insensitive match against catalog
    for catalog_model in SIMPLISMART_MODELS:
        if catalog_model.lower() == lower_model:
            return catalog_model

    # Return as-is and let Simplismart handle validation
    logger.debug(f"Using model ID as-is for Simplismart: {model_id}")
    return model_id


def make_simplismart_request_openai(messages, model, **kwargs):
    """Make request to Simplismart using OpenAI client.

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_simplismart_client()
        resolved_model = resolve_simplismart_model(model)
        response = client.chat.completions.create(model=resolved_model, messages=messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"Simplismart request failed: {e}")
        raise


def make_simplismart_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Simplismart using OpenAI client.

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_simplismart_client()
        resolved_model = resolve_simplismart_model(model)
        stream = client.chat.completions.create(
            model=resolved_model, messages=messages, stream=True, **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"Simplismart streaming request failed: {e}")
        raise


def process_simplismart_response(response):
    """Process Simplismart response to extract relevant data.

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
        logger.error(f"Failed to process Simplismart response: {e}")
        raise


def fetch_models_from_simplismart():
    """Fetch available models from Simplismart.

    Returns a list of model info dictionaries in catalog format.
    """
    try:
        models = []
        for model_id, model_info in SIMPLISMART_MODELS.items():
            models.append(
                {
                    "id": model_id,
                    "name": model_info["name"],
                    "description": model_info.get("description", ""),
                    "context_length": model_info.get("context_length", 8192),
                    "provider": "simplismart",
                    "provider_name": "Simplismart",
                }
            )
        logger.info(f"Fetched {len(models)} models from Simplismart")
        return models
    except Exception as e:
        logger.error(f"Failed to fetch models from Simplismart: {e}")
        return []


def is_simplismart_model(model_id: str) -> bool:
    """Check if a model ID is available on Simplismart.

    Args:
        model_id: The model ID to check

    Returns:
        True if model is available on Simplismart
    """
    lower_model = model_id.lower()

    # Check aliases
    if lower_model in SIMPLISMART_MODEL_ALIASES:
        return True

    # Check catalog directly
    if model_id in SIMPLISMART_MODELS:
        return True

    # Case-insensitive catalog check
    for catalog_model in SIMPLISMART_MODELS:
        if catalog_model.lower() == lower_model:
            return True

    return False
