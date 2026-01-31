import logging
from datetime import datetime, timezone

import httpx

from src.cache import _near_models_cache
from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_pooled_client
from src.utils.model_name_validator import clean_model_name
from src.utils.security_validators import sanitize_for_logging

# Initialize logging
logger = logging.getLogger(__name__)

# Constants
MODALITY_TEXT_TO_TEXT = "text->text"

# Extended timeout for large models
NEAR_TIMEOUT = httpx.Timeout(
    connect=10.0,
    read=120.0,  # Near AI models can be slow (e.g., DeepSeek-V3.1)
    write=10.0,
    pool=5.0,
)


def get_near_client():
    """Get Near AI client using OpenAI-compatible interface with connection pooling

    Near AI is a decentralized AI infrastructure providing private, verifiable, and user-owned AI services
    Base URL: https://cloud-api.near.ai/v1
    """
    try:
        if not Config.NEAR_API_KEY:
            raise ValueError("Near AI API key not configured")

        # Use connection pool with extended timeout for large models
        return get_pooled_client(
            provider="near",
            base_url="https://cloud-api.near.ai/v1",
            api_key=Config.NEAR_API_KEY,
            timeout=NEAR_TIMEOUT,
        )
    except Exception as e:
        logger.error(f"Failed to initialize Near AI client: {e}")
        raise


def make_near_request_openai(messages, model, **kwargs):
    """Make request to Near AI using OpenAI client

    Args:
        messages: List of message objects
        model: Model name (e.g., "deepseek-ai/DeepSeek-V3.1", "deepseek-ai/DeepSeek-R1")
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_near_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"Near AI request failed: {e}")
        raise


def make_near_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Near AI using OpenAI client

    Args:
        messages: List of message objects
        model: Model name (e.g., "deepseek-ai/DeepSeek-V3.1", "deepseek-ai/DeepSeek-R1")
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_near_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"Near AI streaming request failed: {e}")
        raise


def process_near_response(response):
    """Process Near AI response to extract relevant data"""
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
        logger.error(f"Failed to process Near AI response: {e}")
        raise


# ============================================================================
# Model Catalog Functions
# ============================================================================


def normalize_near_model(near_model: dict) -> dict:
    """Normalize Near AI catalog entries to resemble OpenRouter model shape

    Near AI features:
    - Private, verifiable AI infrastructure
    - Decentralized execution
    - User-owned AI services
    - Cryptographic verification and on-chain auditing
    """
    from src.services.pricing_lookup import enrich_model_with_pricing

    model_id = near_model.get("modelId")
    if not model_id:
        # Fallback to 'id' for backward compatibility
        model_id = near_model.get("id")
        if not model_id:
            logger.warning(
                "Near AI model missing 'modelId' field: %s", sanitize_for_logging(str(near_model))
            )
            return None

    slug = f"near/{model_id}"
    provider_slug = "near"

    # Extract metadata from Near AI API response
    metadata = near_model.get("metadata") or {}
    raw_display_name = (
        metadata.get("displayName")
        or near_model.get("display_name")
        or model_id.replace("-", " ").replace("_", " ").title()
    )
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)
    near_model.get("owned_by", "Near Protocol")

    # Highlight security features in description
    base_description = (
        metadata.get("description")
        or near_model.get("description")
        or f"Near AI hosted model {model_id}."
    )
    security_features = " Security: Private AI inference with decentralized execution, cryptographic verification, and on-chain auditing."
    description = f"{base_description}{security_features}"

    context_length = (
        metadata.get("contextLength")
        or metadata.get("context_length")
        or near_model.get("context_length")
        or 0
    )

    pricing = {
        "prompt": None,
        "completion": None,
        "request": None,
        "image": None,
        "web_search": None,
        "internal_reasoning": None,
    }

    # Extract pricing from Near AI API response
    # FIXED: Near AI provides pricing as inputCostPerToken and outputCostPerToken with amount and scale
    # Scale is in powers of 10 (e.g., -9 means 10^-9 = per token)
    # Database stores per-token pricing, so just use amount × 10^scale
    input_cost = near_model.get("inputCostPerToken", {})
    output_cost = near_model.get("outputCostPerToken", {})

    if input_cost and isinstance(input_cost, dict):
        input_amount = input_cost.get("amount", 0)
        input_scale = input_cost.get("scale", -9)  # Default scale is -9 (per token)
        # Per-token price = amount × 10^scale
        if input_amount > 0:
            pricing["prompt"] = str(input_amount * (10 ** input_scale))

    if output_cost and isinstance(output_cost, dict):
        output_amount = output_cost.get("amount", 0)
        output_scale = output_cost.get("scale", -9)  # Default scale is -9 (per token)
        # Per-token price = amount × 10^scale
        if output_amount > 0:
            pricing["completion"] = str(output_amount * (10 ** output_scale))

    # Fallback to old pricing format for backward compatibility
    if not pricing["prompt"] and not pricing["completion"]:
        pricing_info = near_model.get("pricing", {})
        if pricing_info:
            pricing["prompt"] = (
                str(pricing_info.get("prompt")) if pricing_info.get("prompt") is not None else None
            )
            pricing["completion"] = (
                str(pricing_info.get("completion"))
                if pricing_info.get("completion") is not None
                else None
            )

    architecture = {
        "modality": metadata.get("modality", MODALITY_TEXT_TO_TEXT),
        "input_modalities": metadata.get("input_modalities") or ["text"],
        "output_modalities": metadata.get("output_modalities") or ["text"],
        "tokenizer": metadata.get("tokenizer"),
        "instruct_type": metadata.get("instruct_type"),
    }

    normalized = {
        "id": slug,
        "slug": slug,
        "canonical_slug": slug,
        "hugging_face_id": metadata.get("huggingface_repo"),
        "name": display_name,
        "created": near_model.get("created"),
        "description": description,
        "context_length": context_length,
        "architecture": architecture,
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": metadata.get("supported_parameters", []),
        "default_parameters": metadata.get("default_parameters", {}),
        "provider_slug": provider_slug,
        "provider_site_url": "https://near.ai",
        "model_logo_url": None,
        "source_gateway": "near",
        "raw_near": near_model,
        # Mark all Near AI models as private
        "is_private": True,  # NEAR models support private inference
        "tags": ["Private"],
        # Highlight security features as metadata
        "security_features": {
            "private_inference": True,
            "decentralized": True,
            "verifiable": True,
            "on_chain_auditing": True,
            "user_owned": True,
        },
    }

    return enrich_model_with_pricing(normalized, "near")


def fetch_models_from_near():
    """Fetch models from Near AI API

    Note: Near AI is a decentralized AI infrastructure providing private, verifiable, and user-owned AI services.
    Models are fetched from the OpenAI-compatible /models endpoint.
    If the API doesn't return models, fallback to known Near AI models.
    """
    from src.services.failover_service import get_fallback_models_from_db

    try:
        if not Config.NEAR_API_KEY:
            logger.error("Near AI API key not configured")
            return None

        headers = {
            "Authorization": f"Bearer {Config.NEAR_API_KEY}",
            "Content-Type": "application/json",
        }

        try:
            # Try to fetch models from Near AI
            # Note: Using Near AI's model list endpoint which includes pricing
            response = httpx.get(
                "https://cloud-api.near.ai/v1/model/list",
                headers=headers,
                timeout=20.0,
            )
            response.raise_for_status()

            payload = response.json()
            raw_models = payload.get("models", [])

            if raw_models:
                # Normalize models
                normalized_models = [normalize_near_model(model) for model in raw_models if model]

                _near_models_cache["data"] = normalized_models
                _near_models_cache["timestamp"] = datetime.now(timezone.utc)

                logger.info(f"Fetched {len(normalized_models)} Near AI models from API")
                return _near_models_cache["data"]
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.warning(
                "Near AI API request failed: %s. Using fallback model list.",
                sanitize_for_logging(str(e)),
            )

        # Fallback strategy:
        # 1. First, try to get models from database (most recent successful sync)
        # 2. If database is empty, use hardcoded static fallback as last resort
        logger.info("Using fallback Near AI model list")

        # Try database fallback first (dynamic, from last successful sync)
        db_fallback_models = get_fallback_models_from_db("near")
        if db_fallback_models:
            normalized_models = [normalize_near_model(model) for model in db_fallback_models if model]
            normalized_models = [m for m in normalized_models if m]  # Filter out None

            if normalized_models:
                _near_models_cache["data"] = normalized_models
                _near_models_cache["timestamp"] = datetime.now(timezone.utc)
                logger.info(f"Using {len(normalized_models)} Near AI models from database fallback")
                return _near_models_cache["data"]

        # Static fallback as last resort (if database is empty)
        # Reference: https://cloud.near.ai/models for current available models
        # Pricing from https://cloud-api.near.ai/v1/model/list (as of 2026-01)
        logger.warning("Database fallback empty, using static fallback for Near AI")
        fallback_models = [
            {
                "id": "deepseek-ai/DeepSeek-V3.1",
                "modelId": "deepseek-ai/DeepSeek-V3.1",
                "owned_by": "DeepSeek",
                "inputCostPerToken": {"amount": 1.05, "scale": -6},  # $1.05 per million tokens
                "outputCostPerToken": {"amount": 3.10, "scale": -6},  # $3.10 per million tokens
                "metadata": {"contextLength": 128000},
            },
            {
                "id": "openai/gpt-oss-120b",
                "modelId": "openai/gpt-oss-120b",
                "owned_by": "OpenAI",
                "inputCostPerToken": {"amount": 0.15, "scale": -6},  # $0.15 per million tokens
                "outputCostPerToken": {"amount": 0.55, "scale": -6},  # $0.55 per million tokens
                "metadata": {"contextLength": 131000},
            },
            {
                "id": "Qwen/Qwen3-30B-A3B-Instruct-2507",
                "modelId": "Qwen/Qwen3-30B-A3B-Instruct-2507",
                "owned_by": "Qwen",
                "inputCostPerToken": {"amount": 0.15, "scale": -6},  # $0.15 per million tokens
                "outputCostPerToken": {"amount": 0.55, "scale": -6},  # $0.55 per million tokens
                "metadata": {"contextLength": 262144},
            },
            {
                "id": "zai-org/GLM-4.6",
                "modelId": "zai-org/GLM-4.6",
                "owned_by": "Zhipu AI",
                "inputCostPerToken": {"amount": 0.85, "scale": -6},  # $0.85 per million tokens
                "outputCostPerToken": {"amount": 3.30, "scale": -6},  # $3.30 per million tokens
                "metadata": {"contextLength": 200000},
            },
            {
                "id": "zai-org/GLM-4.7",
                "modelId": "zai-org/GLM-4.7",
                "owned_by": "Zhipu AI",
                "inputCostPerToken": {"amount": 0.85, "scale": -6},  # $0.85 per million tokens
                "outputCostPerToken": {"amount": 3.30, "scale": -6},  # $3.30 per million tokens
                "metadata": {"contextLength": 131072},
            },
        ]

        normalized_models = [normalize_near_model(model) for model in fallback_models if model]

        _near_models_cache["data"] = normalized_models
        _near_models_cache["timestamp"] = datetime.now(timezone.utc)

        logger.info(f"Using {len(normalized_models)} static fallback Near AI models")
        return _near_models_cache["data"]
    except Exception as e:
        logger.error(f"Failed to fetch models from Near AI: {e}")
        return []
