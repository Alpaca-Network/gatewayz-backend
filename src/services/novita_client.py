"""Novita AI client for API integration.

This module provides integration with Novita AI models via OpenAI-compatible API.
"""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from datetime import timezone

from src.cache import _novita_models_cache

# Initialize logging
logger = logging.getLogger(__name__)

DEFAULT_NOVITA_MODELS: list[dict[str, Any]] = [
    {
        "id": "qwen3-235b-thinking",
        "name": "Qwen3 235B Thinking",
        "owned_by": "alibaba",
        "context_length": 32768,
    },
    {
        "id": "qwen3-max",
        "name": "Qwen3 Max",
        "owned_by": "alibaba",
        "context_length": 32768,
    },
    {
        "id": "deepseek-v3",
        "name": "DeepSeek V3",
        "owned_by": "deepseek",
        "context_length": 64000,
    },
    {
        "id": "llama-3.3-70b",
        "name": "Llama 3.3 70B",
        "owned_by": "meta",
        "context_length": 8192,
    },
    {
        "id": "mistral-nemo",
        "name": "Mistral Nemo",
        "owned_by": "mistral",
        "context_length": 8192,
    },
]


def fetch_models_from_novita():
    """Fetch and normalize models from Novita.

    Novita provides an OpenAI-compatible API endpoint that lists available models.
    We fetch from that endpoint and normalize the response to match our catalog format.
    Falls back to a static catalog if the live fetch fails.
    """

    def _cache_and_return(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
        _novita_models_cache["data"] = models
        _novita_models_cache["timestamp"] = datetime.now(timezone.utc)
        return models

    try:
        from src.config import Config

        if not Config.NOVITA_API_KEY:
            logger.warning("NOVITA_API_KEY not configured, using static catalog")
            fallback = _fallback_novita_models("api_key_missing")
            return _cache_and_return(fallback) if fallback else None

    except Exception as exc:
        logger.error(f"Failed to load config for Novita: {exc}")
        fallback = _fallback_novita_models("config_load_failed")
        return _cache_and_return(fallback) if fallback else None

    try:
        from openai import OpenAI

        client = OpenAI(base_url="https://api.novita.ai/v3/openai", api_key=Config.NOVITA_API_KEY)
        response = client.models.list()
    except Exception as exc:
        logger.error(f"Novita models.list() failed: {exc}")
        fallback = _fallback_novita_models("api_error")
        return _cache_and_return(fallback) if fallback else None

    raw_models = _extract_models_from_response(response)
    normalized_models = [
        model for model in (_normalize_novita_model(entry) for entry in raw_models) if model
    ]

    if not normalized_models:
        logger.warning("Novita API returned zero models; falling back to static catalog")
        fallback = _fallback_novita_models("empty_response")
        return _cache_and_return(fallback) if fallback else None

    logger.info("Fetched %s Novita models from live API", len(normalized_models))
    return _cache_and_return(normalized_models)


def _fallback_novita_models(reason: str) -> list[dict[str, Any]] | None:
    logger.warning("Using static Novita model catalog (%s)", reason)
    normalized = [
        model for model in (_normalize_novita_model(entry) for entry in DEFAULT_NOVITA_MODELS) if model
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
    ):
        if hasattr(model, attr):
            data[attr] = getattr(model, attr)

    if not data:
        data["value"] = str(model)

    return data


def _normalize_novita_model(model: Any) -> dict[str, Any] | None:
    payload = _coerce_model_to_dict(model)
    raw_id = payload.get("id") or payload.get("name")
    if not raw_id:
        return None

    model_id = _cleanup_model_id(str(raw_id))
    provider_slug = (
        payload.get("provider_slug")
        or payload.get("provider")
        or payload.get("owned_by")
        or (model_id.split("/")[0] if "/" in model_id else "novita")
    )
    provider_slug = str(provider_slug).lstrip("@").lower() if provider_slug else "novita"

    display_name = payload.get("display_name") or payload.get("name") or model_id
    description = payload.get("description") or f"Novita hosted model '{display_name}'."
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
        "id": model_id,
        "slug": model_id,
        "canonical_slug": model_id,
        "hugging_face_id": payload.get("hugging_face_id"),
        "name": display_name,
        "created": payload.get("created"),
        "description": description,
        "context_length": context_length,
        "architecture": normalized_architecture,
        "pricing": _normalize_pricing(payload.get("pricing")),
        "top_provider": None,
        "per_request_limits": payload.get("limits") or payload.get("rate_limits"),
        "supported_parameters": _extract_supported_parameters(payload),
        "default_parameters": payload.get("default_parameters") or {},
        "provider_slug": provider_slug,
        "provider_site_url": payload.get("provider_site_url") or "https://www.novita.ai/",
        "model_logo_url": payload.get("model_logo_url"),
        "source_gateway": "novita",
        "tags": payload.get("tags") or [],
        "raw_novita": payload,
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

    if isinstance(supported, list | tuple | set):
        params.extend(str(item) for item in supported if item)

    if isinstance(capabilities, dict):
        for key, value in capabilities.items():
            if key:
                params.append(str(key))
            if isinstance(value, dict):
                params.extend(str(inner) for inner in value.keys() if inner)
            elif isinstance(value, list | tuple | set):
                params.extend(str(inner) for inner in value if inner)

    if not params:
        params = ["max_tokens", "temperature", "top_p", "top_k", "stream"]

    # Deduplicate while preserving order
    seen = set()
    deduped: list[str] = []
    for item in params:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


def _cleanup_model_id(model_id: str) -> str:
    cleaned = model_id.strip()
    if cleaned.startswith("@"):
        cleaned = cleaned.lstrip("@")
    if cleaned.startswith("novita/"):
        cleaned = cleaned.split("novita/", 1)[1]
    if cleaned.startswith("models/"):
        cleaned = cleaned.split("models/", 1)[1]
    if cleaned.startswith("api/"):
        cleaned = cleaned.split("api/", 1)[1]
    if cleaned.startswith("@novita/"):
        cleaned = cleaned.split("@novita/", 1)[1]
    return cleaned
