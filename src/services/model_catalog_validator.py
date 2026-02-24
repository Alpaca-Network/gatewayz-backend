"""
Model catalog validation service to prevent unavailable models from appearing in catalog.

This service validates that models are actually available on their respective providers
before adding them to the catalog, preventing 404/400 errors from non-existent models.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Cache validation results for this duration to avoid excessive API calls
VALIDATION_CACHE_DURATION = timedelta(hours=1)

# In-memory validation cache: model_id -> {"available": bool, "checked_at": datetime}
_validation_cache: dict[str, dict[str, Any]] = {}


async def validate_model_availability(
    model_id: str, provider: str, source_gateway: str
) -> dict[str, Any]:
    """
    Validate that a model is actually available on its provider.

    Args:
        model_id: The model identifier
        provider: Provider name (e.g., "cerebras", "huggingface")
        source_gateway: Source gateway name (e.g., "cerebras", "huggingface")

    Returns:
        Dictionary with validation result:
        {
            "model_id": str,
            "provider": str,
            "available": bool,
            "checked_at": datetime,
            "error": str | None
        }
    """
    # Check cache first
    cache_key = f"{provider}:{model_id}"
    if cache_key in _validation_cache:
        cached = _validation_cache[cache_key]
        cache_age = datetime.now(UTC) - cached["checked_at"]
        if cache_age < VALIDATION_CACHE_DURATION:
            logger.debug(
                f"Using cached validation for {cache_key}: available={cached['available']}"
            )
            return cached

    # Perform validation based on provider
    result = {
        "model_id": model_id,
        "provider": provider,
        "available": False,
        "checked_at": datetime.now(UTC),
        "error": None,
    }

    try:
        if provider.lower() in ("cerebras", "cerebras-ai"):
            result = await _validate_cerebras_model(model_id)
        elif provider.lower() in ("huggingface", "hf", "hugging-face"):
            result = await _validate_huggingface_model(model_id)
        elif provider.lower() == "openrouter":
            result = await _validate_openrouter_model(model_id)
        else:
            # For providers we don't have validation for, assume available
            logger.warning(
                f"No validation implemented for provider '{provider}', assuming available"
            )
            result["available"] = True

        # Cache the result
        _validation_cache[cache_key] = result

        return result

    except Exception as e:
        logger.error(f"Failed to validate model {model_id} on {provider}: {e}")
        result["error"] = str(e)
        result["available"] = False  # Fail closed - don't add if validation fails
        return result


async def _validate_cerebras_model(model_id: str) -> dict[str, Any]:
    """Validate model availability on Cerebras"""
    try:
        from src.services.cerebras_client import get_cerebras_client

        client = get_cerebras_client()

        # Try to list models and check if our model is in the list
        try:
            models_response = client.models.list()
            available_models = []

            # Extract model IDs from response
            if hasattr(models_response, "data"):
                available_models = [
                    model.id if hasattr(model, "id") else str(model)
                    for model in models_response.data
                ]
            elif isinstance(models_response, list):
                available_models = [
                    model.get("id") if isinstance(model, dict) else str(model)
                    for model in models_response
                ]

            # Check if model is available (case-insensitive)
            model_id_lower = model_id.lower()
            is_available = any(
                model_id_lower == available_id.lower() for available_id in available_models
            )

            if not is_available:
                logger.warning(
                    f"Model '{model_id}' not found in Cerebras catalog. "
                    f"Available models: {', '.join(available_models[:10])}"
                )

            return {
                "model_id": model_id,
                "provider": "cerebras",
                "available": is_available,
                "checked_at": datetime.now(UTC),
                "error": None if is_available else "Model not found in Cerebras catalog",
            }

        except Exception as list_err:
            logger.error(f"Failed to list Cerebras models: {list_err}")
            # If listing fails, try making a test request (more expensive)
            return await _validate_model_by_test_request(model_id, "cerebras")

    except Exception as e:
        logger.error(f"Failed to validate Cerebras model {model_id}: {e}")
        return {
            "model_id": model_id,
            "provider": "cerebras",
            "available": False,
            "checked_at": datetime.now(UTC),
            "error": str(e),
        }


async def _validate_huggingface_model(model_id: str) -> dict[str, Any]:
    """Validate model availability on HuggingFace"""
    try:
        import httpx

        from src.config import Config

        # Strip :hf-inference suffix if present
        clean_model_id = model_id.replace(":hf-inference", "")

        # Check if model exists via HuggingFace Hub API
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://huggingface.co/api/models/{clean_model_id}",
                headers=(
                    {"Authorization": f"Bearer {Config.HUG_API_KEY}"} if Config.HUG_API_KEY else {}
                ),
                timeout=10.0,
            )

            if response.status_code == 200:
                # Model exists on HuggingFace Hub
                # Now check if it's available on the Inference Router
                return await _validate_hf_inference_availability(clean_model_id)
            elif response.status_code == 404:
                logger.warning(f"Model '{clean_model_id}' not found on HuggingFace Hub")
                return {
                    "model_id": model_id,
                    "provider": "huggingface",
                    "available": False,
                    "checked_at": datetime.now(UTC),
                    "error": "Model not found on HuggingFace Hub",
                }
            else:
                logger.warning(f"Unexpected status code {response.status_code} checking HF model")
                return {
                    "model_id": model_id,
                    "provider": "huggingface",
                    "available": False,
                    "checked_at": datetime.now(UTC),
                    "error": f"HTTP {response.status_code}",
                }

    except Exception as e:
        logger.error(f"Failed to validate HuggingFace model {model_id}: {e}")
        return {
            "model_id": model_id,
            "provider": "huggingface",
            "available": False,
            "checked_at": datetime.now(UTC),
            "error": str(e),
        }


async def _validate_hf_inference_availability(model_id: str) -> dict[str, Any]:
    """Check if HuggingFace model is available on the Inference Router"""
    try:
        import httpx

        from src.config import Config

        if not Config.HUG_API_KEY:
            # Can't validate without API key, assume available if on Hub
            return {
                "model_id": model_id,
                "provider": "huggingface",
                "available": True,
                "checked_at": datetime.now(UTC),
                "error": None,
            }

        # Try a minimal test request to the Inference Router
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://router.huggingface.co/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {Config.HUG_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": f"{model_id}:hf-inference",
                    "messages": [{"role": "user", "content": "test"}],
                    "max_tokens": 1,
                },
                timeout=10.0,
            )

            # 200 = available, 400/404 = not available on router
            if response.status_code == 200:
                return {
                    "model_id": model_id,
                    "provider": "huggingface",
                    "available": True,
                    "checked_at": datetime.now(UTC),
                    "error": None,
                }
            elif response.status_code in (400, 404):
                logger.warning(
                    f"Model '{model_id}' exists on HF Hub but not available on Inference Router"
                )
                return {
                    "model_id": model_id,
                    "provider": "huggingface",
                    "available": False,
                    "checked_at": datetime.now(UTC),
                    "error": "Model not available on HF Inference Router",
                }
            else:
                # Other errors - assume unavailable
                return {
                    "model_id": model_id,
                    "provider": "huggingface",
                    "available": False,
                    "checked_at": datetime.now(UTC),
                    "error": f"HTTP {response.status_code}",
                }

    except Exception as e:
        logger.error(f"Failed to validate HF Inference availability for {model_id}: {e}")
        return {
            "model_id": model_id,
            "provider": "huggingface",
            "available": False,
            "checked_at": datetime.now(UTC),
            "error": str(e),
        }


async def _validate_openrouter_model(model_id: str) -> dict[str, Any]:
    """Validate model availability on OpenRouter"""
    try:
        import httpx

        from src.config import Config

        if not Config.OPENROUTER_API_KEY:
            logger.warning("OpenRouter API key not configured, cannot validate model")
            return {
                "model_id": model_id,
                "provider": "openrouter",
                "available": False,
                "checked_at": datetime.now(UTC),
                "error": "API key not configured",
            }

        # Fetch OpenRouter model list
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {Config.OPENROUTER_API_KEY}"},
                timeout=10.0,
            )
            response.raise_for_status()

            models_data = response.json()
            available_models = [model.get("id") for model in models_data.get("data", [])]

            # Check if model is in the list
            is_available = model_id in available_models

            if not is_available:
                logger.warning(f"Model '{model_id}' not found in OpenRouter catalog")

            return {
                "model_id": model_id,
                "provider": "openrouter",
                "available": is_available,
                "checked_at": datetime.now(UTC),
                "error": None if is_available else "Model not found in OpenRouter catalog",
            }

    except Exception as e:
        logger.error(f"Failed to validate OpenRouter model {model_id}: {e}")
        return {
            "model_id": model_id,
            "provider": "openrouter",
            "available": False,
            "checked_at": datetime.now(UTC),
            "error": str(e),
        }


async def _validate_model_by_test_request(model_id: str, provider: str) -> dict[str, Any]:
    """
    Validate model by making a minimal test request.
    This is more expensive than checking catalogs, so use as fallback.
    """
    logger.info(f"Validating {model_id} on {provider} via test request")

    try:
        # Make minimal test request based on provider
        if provider == "cerebras":
            from src.services.cerebras_client import make_cerebras_request_openai

            try:
                make_cerebras_request_openai(
                    messages=[{"role": "user", "content": "test"}],
                    model=model_id,
                    max_tokens=1,
                )
                return {
                    "model_id": model_id,
                    "provider": provider,
                    "available": True,
                    "checked_at": datetime.now(UTC),
                    "error": None,
                }
            except Exception as e:
                error_str = str(e).lower()
                if "not found" in error_str or "does not exist" in error_str:
                    return {
                        "model_id": model_id,
                        "provider": provider,
                        "available": False,
                        "checked_at": datetime.now(UTC),
                        "error": "Model not found via test request",
                    }
                # Other errors don't necessarily mean unavailable
                raise

        # Add other providers as needed

        return {
            "model_id": model_id,
            "provider": provider,
            "available": False,
            "checked_at": datetime.now(UTC),
            "error": "Test request validation not implemented",
        }

    except Exception as e:
        logger.error(f"Test request validation failed for {model_id} on {provider}: {e}")
        return {
            "model_id": model_id,
            "provider": provider,
            "available": False,
            "checked_at": datetime.now(UTC),
            "error": str(e),
        }


async def validate_models_batch(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Validate a batch of models and filter out unavailable ones.

    Args:
        models: List of model dictionaries with 'id', 'provider_slug', 'source_gateway' keys

    Returns:
        List of validated models (only those that are available)
    """
    if not models:
        return []

    logger.info(f"Validating batch of {len(models)} models")

    # Validate all models concurrently
    validation_tasks = [
        validate_model_availability(
            model["id"], model.get("provider_slug", ""), model.get("source_gateway", "")
        )
        for model in models
    ]

    validation_results = await asyncio.gather(*validation_tasks, return_exceptions=True)

    # Filter out unavailable models
    validated_models = []
    for model, result in zip(models, validation_results):  # noqa: B905
        if isinstance(result, Exception):
            logger.error(f"Validation failed for model {model['id']}: {result}")
            continue

        if result.get("available"):
            validated_models.append(model)
        else:
            logger.warning(
                f"Excluding model {model['id']} from catalog: {result.get('error', 'not available')}"
            )

    logger.info(
        f"Validated {len(validated_models)}/{len(models)} models as available "
        f"({len(models) - len(validated_models)} excluded)"
    )

    return validated_models


def clear_validation_cache(model_id: str | None = None) -> None:
    """
    Clear the validation cache.

    Args:
        model_id: Specific model to clear, or None to clear all
    """
    if model_id:
        # Clear all cache entries containing this model_id
        keys_to_clear = [k for k in _validation_cache.keys() if model_id in k]
        for key in keys_to_clear:
            _validation_cache.pop(key, None)
        logger.debug(f"Cleared validation cache for {model_id}")
    else:
        _validation_cache.clear()
        logger.debug("Cleared all validation cache")
