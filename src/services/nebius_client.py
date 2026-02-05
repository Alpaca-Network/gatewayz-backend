"""Nebius AI client for API integration.

This module provides integration with Nebius AI models via their OpenAI-compatible API.
API documentation: https://docs.tokenfactory.nebius.com/

Nebius Token Factory provides access to various LLM models including DeepSeek, Llama, Qwen, etc.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src.cache import _nebius_models_cache
from src.utils.model_name_validator import clean_model_name

# Initialize logging
logger = logging.getLogger(__name__)

# Default models to use as fallback when API is unavailable
DEFAULT_NEBIUS_MODELS: list[dict[str, Any]] = [
    {
        "id": "deepseek-ai/DeepSeek-R1-0528",
        "name": "DeepSeek R1 0528",
        "owned_by": "deepseek-ai",
        "context_length": 65536,
    },
    {
        "id": "deepseek-ai/DeepSeek-V3-0324",
        "name": "DeepSeek V3 0324",
        "owned_by": "deepseek-ai",
        "context_length": 65536,
    },
    {
        "id": "meta-llama/Llama-3.3-70B-Instruct",
        "name": "Llama 3.3 70B Instruct",
        "owned_by": "meta-llama",
        "context_length": 131072,
    },
    {
        "id": "Qwen/Qwen3-235B-A22B",
        "name": "Qwen3 235B A22B",
        "owned_by": "Qwen",
        "context_length": 131072,
    },
    {
        "id": "mistralai/Mistral-Small-24B-Instruct-2501",
        "name": "Mistral Small 24B Instruct",
        "owned_by": "mistralai",
        "context_length": 32768,
    },
]

DEFAULT_SUPPORTED_PARAMETERS = [
    "max_tokens",
    "temperature",
    "top_p",
    "frequency_penalty",
    "presence_penalty",
    "stream",
    "stop",
]


def get_nebius_client():
    """Get Nebius client using OpenAI SDK with Nebius base URL.

    Nebius provides an OpenAI-compatible API at https://api.tokenfactory.nebius.com/v1/
    """
    try:
        from src.config import Config

        if not Config.NEBIUS_API_KEY:
            raise ValueError("Nebius API key not configured")

        from openai import OpenAI

        return OpenAI(
            base_url="https://api.tokenfactory.nebius.com/v1/",
            api_key=Config.NEBIUS_API_KEY,
        )
    except Exception as e:
        logger.error(f"Failed to initialize Nebius client: {e}")
        raise


def make_nebius_request_openai(messages, model, **kwargs):
    """Make request to Nebius using OpenAI-compatible client.

    Args:
        messages: List of message objects
        model: Model name (e.g., "deepseek-ai/DeepSeek-R1-0528")
        **kwargs: Additional parameters like max_tokens, temperature, etc.

    Returns:
        Response object from Nebius API
    """
    try:
        client = get_nebius_client()

        logger.debug(f"Nebius request - model: {model}, messages: {len(messages)}")

        response = client.chat.completions.create(model=model, messages=messages, **kwargs)

        return response
    except Exception as e:
        logger.error(f"Nebius request failed: {e}")
        raise


def make_nebius_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Nebius using OpenAI-compatible client.

    Args:
        messages: List of message objects
        model: Model name (e.g., "deepseek-ai/DeepSeek-R1-0528")
        **kwargs: Additional parameters like max_tokens, temperature, etc.

    Returns:
        Streaming response generator from Nebius API
    """
    try:
        client = get_nebius_client()

        logger.debug(f"Nebius streaming request - model: {model}, messages: {len(messages)}")

        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )

        return stream
    except Exception as e:
        logger.error(f"Nebius streaming request failed: {e}")
        raise


def process_nebius_response(response):
    """Process Nebius response to extract relevant data.

    Args:
        response: Response object from Nebius API

    Returns:
        Standardized response dictionary
    """
    try:
        from src.services.anthropic_transformer import extract_message_with_tools

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
        logger.error(f"Failed to process Nebius response: {e}")
        raise


def fetch_models_from_nebius():
    """Fetch and normalize models from Nebius Token Factory API.

    Nebius provides an OpenAI-compatible /v1/models endpoint that lists available models.
    We fetch from that endpoint and normalize the response to match our catalog format.
    Falls back to a static catalog if the live fetch fails.
    """

    def _cache_and_return(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
        _nebius_models_cache["data"] = models
        _nebius_models_cache["timestamp"] = datetime.now(timezone.utc)
        return models

    try:
        from src.config import Config

        if not Config.NEBIUS_API_KEY:
            logger.warning("NEBIUS_API_KEY not configured, using static catalog")
            fallback = _fallback_nebius_models("api_key_missing")
            return _cache_and_return(fallback) if fallback else None

    except Exception as exc:
        logger.error(f"Failed to load config for Nebius: {exc}")
        fallback = _fallback_nebius_models("config_load_failed")
        return _cache_and_return(fallback) if fallback else None

    try:
        client = get_nebius_client()
        response = client.models.list()
    except Exception as exc:
        logger.error(f"Nebius models.list() failed: {exc}")
        fallback = _fallback_nebius_models("api_error")
        return _cache_and_return(fallback) if fallback else None

    raw_models = _extract_models_from_response(response)
    normalized_models = [
        model for model in (_normalize_nebius_model(entry) for entry in raw_models) if model
    ]

    if not normalized_models:
        logger.warning("Nebius API returned zero models; falling back to static catalog")
        fallback = _fallback_nebius_models("empty_response")
        return _cache_and_return(fallback) if fallback else None

    logger.info("Fetched %s Nebius models from live API", len(normalized_models))
    return _cache_and_return(normalized_models)


def _fallback_nebius_models(reason: str) -> list[dict[str, Any]] | None:
    logger.warning("Using fallback Nebius model catalog (%s)", reason)

    # Try database fallback first (dynamic, from last successful sync)
    try:
        from src.services.models import get_fallback_models_from_db

        db_fallback = get_fallback_models_from_db("nebius")
        if db_fallback:
            normalized = [
                model for model in (_normalize_nebius_model(entry) for entry in db_fallback) if model
            ]
            if normalized:
                logger.info(f"Using {len(normalized)} Nebius models from database fallback")
                return normalized
    except Exception as e:
        logger.warning(f"Failed to get database fallback for Nebius: {e}")

    # Static fallback as last resort
    logger.warning("Database fallback empty, using static fallback for Nebius")
    normalized = [
        model for model in (_normalize_nebius_model(entry) for entry in DEFAULT_NEBIUS_MODELS) if model
    ]
    return normalized or None


def _extract_models_from_response(response: Any) -> list[Any]:
    """
    Coerce whatever the OpenAI SDK returns (SyncPage, list, dict, etc.)
    into a plain list of model payloads.
    """

    if response is None:
        return []

    for attr in ("data", "models", "items"):
        if hasattr(response, attr):
            data = getattr(response, attr)
            return _coerce_sequence(data)

    if isinstance(response, dict):
        for key in ("data", "models", "items"):
            if key in response:
                return _coerce_sequence(response[key])

    return _coerce_sequence(response)


def _coerce_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    try:
        return list(value)
    except TypeError:
        return [value]


def _coerce_model_to_dict(model: Any) -> dict[str, Any]:
    if isinstance(model, dict):
        return dict(model)

    for attr in ("model_dump", "dict"):
        if hasattr(model, attr):
            try:
                payload = getattr(model, attr)()
                if isinstance(payload, dict):
                    return payload
            except Exception:  # pragma: no cover - defensive
                continue

    data: dict[str, Any] = {}
    for attr in (
        "id",
        "object",
        "created",
        "owned_by",
        "name",
        "display_name",
        "description",
        "context_length",
        "max_context_length",
        "max_sequence_length",
        "pricing",
        "capabilities",
        "metadata",
        "default_parameters",
        "provider",
        "provider_slug",
        "limits",
        "rate_limits",
        "tags",
        "status",
    ):
        if hasattr(model, attr):
            data[attr] = getattr(model, attr)

    if not data:
        data["value"] = str(model)

    return data


def _normalize_nebius_model(model: Any) -> dict[str, Any] | None:
    payload = _coerce_model_to_dict(model)
    raw_id = payload.get("id") or payload.get("name")
    if not raw_id:
        return None

    provider_model_id = str(raw_id).strip()

    # Extract provider from model ID (e.g., "deepseek-ai/DeepSeek-R1" -> "deepseek-ai")
    provider_slug = (
        payload.get("provider_slug")
        or payload.get("provider")
        or payload.get("owned_by")
        or (provider_model_id.split("/")[0] if "/" in provider_model_id else "nebius")
    )
    provider_slug = str(provider_slug).lstrip("@").lower() if provider_slug else "nebius"

    # Generate display name from model ID
    raw_display_name = payload.get("display_name") or payload.get("name")
    if not raw_display_name:
        # Convert "deepseek-ai/DeepSeek-R1-0528" to "DeepSeek R1 0528"
        if "/" in provider_model_id:
            raw_display_name = provider_model_id.split("/")[-1].replace("-", " ")
        else:
            raw_display_name = provider_model_id.replace("-", " ")

    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)

    description = payload.get("description") or f"Nebius hosted model '{display_name}'."
    context_length = (
        payload.get("context_length")
        or payload.get("max_context_length")
        or payload.get("max_sequence_length")
        or 0
    )

    architecture = payload.get("architecture") if isinstance(payload.get("architecture"), dict) else {}
    normalized_architecture = {
        "modality": architecture.get("modality") or "text->text",
        "input_modalities": architecture.get("input_modalities") or ["text"],
        "output_modalities": architecture.get("output_modalities") or ["text"],
        "tokenizer": architecture.get("tokenizer"),
        "instruct_type": architecture.get("instruct_type") or "chat",
    }

    normalized = {
        "id": provider_model_id,
        "slug": provider_model_id,
        "canonical_slug": provider_model_id,
        "hugging_face_id": payload.get("hugging_face_id") or provider_model_id,
        "name": display_name,
        "created": payload.get("created"),
        "description": description,
        "context_length": context_length,
        "architecture": normalized_architecture,
        "pricing": _normalize_pricing(payload.get("pricing")),
        "per_request_limits": payload.get("limits") or payload.get("rate_limits"),
        "supported_parameters": _extract_supported_parameters(payload),
        "default_parameters": payload.get("default_parameters") or {},
        "provider_slug": provider_slug,
        "provider_site_url": payload.get("provider_site_url") or "https://tokenfactory.nebius.com/",
        "model_logo_url": payload.get("model_logo_url"),
        "source_gateway": "nebius",
        "tags": payload.get("tags") or [],
        "raw_nebius": payload,
    }

    return normalized


def _normalize_pricing(pricing: dict[str, Any] | None) -> dict[str, str | None]:
    pricing = pricing or {}

    def _stringify(value: Any) -> str | None:
        if value is None:
            return None
        try:
            return str(value)
        except Exception:  # pragma: no cover - defensive
            return None

    prompt = (
        pricing.get("prompt")
        or pricing.get("input")
        or pricing.get("prompt_price")
        or pricing.get("prompt_tokens")
    )
    completion = (
        pricing.get("completion")
        or pricing.get("output")
        or pricing.get("completion_price")
        or pricing.get("completion_tokens")
    )

    return {
        "prompt": _stringify(prompt),
        "completion": _stringify(completion),
        "request": _stringify(pricing.get("request")),
        "image": _stringify(pricing.get("image")),
        "web_search": _stringify(pricing.get("web_search")),
        "internal_reasoning": _stringify(pricing.get("internal_reasoning")),
    }


def _extract_supported_parameters(payload: dict[str, Any]) -> list[str]:
    supported = payload.get("supported_parameters")
    capabilities = payload.get("capabilities")

    params: list[str] = []

    if isinstance(supported, (list, tuple, set)):
        params.extend(str(item) for item in supported if item)

    if isinstance(capabilities, dict):
        for key, value in capabilities.items():
            if key:
                params.append(str(key))
            if isinstance(value, dict):
                params.extend(str(inner) for inner in value.keys() if inner)
            elif isinstance(value, (list, tuple, set)):
                params.extend(str(inner) for inner in value if inner)

    if not params:
        params = DEFAULT_SUPPORTED_PARAMETERS.copy()

    # Deduplicate while preserving order
    seen = set()
    deduped: list[str] = []
    for item in params:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped
