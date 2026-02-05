"""
Modelz API client for fetching model token data and filtering models.

Note: Modelz uses Alpaca Network's backend API (backend.alpacanetwork.ai) as
their model registry endpoint. This is the correct production API URL.
"""

import logging
import time
from typing import Any

import httpx
from fastapi import HTTPException

from src.cache import clear_modelz_cache, get_modelz_cache
from src.utils.model_name_validator import clean_model_name

logger = logging.getLogger(__name__)

# Modelz uses Alpaca Network's backend API for their model registry
MODELZ_BASE_URL = "https://backend.alpacanetwork.ai"


def _parse_modelz_response(data: Any) -> list[dict[str, Any]]:
    """
    Parse Modelz API response into a list of tokens.

    Handles different response formats that the API may return.

    Args:
        data: Raw response data from Modelz API

    Returns:
        List of token dictionaries
    """
    if isinstance(data, list):
        return data
    elif isinstance(data, dict) and "data" in data:
        return data["data"]
    elif isinstance(data, dict) and "tokens" in data:
        return data["tokens"]
    else:
        return [data] if data else []


def _extract_model_id(token: dict[str, Any]) -> str | None:
    """
    Extract model ID from a token dictionary.

    Checks various possible fields for the model identifier.

    Args:
        token: Token dictionary from Modelz API

    Returns:
        Model ID string or None if not found
    """
    model_id = (
        token.get("Token")
        or token.get("model_id")
        or token.get("modelId")
        or token.get("id")
        or token.get("name")
        or token.get("model")
    )
    if model_id and isinstance(model_id, str):
        return model_id.strip()
    return None


def _update_modelz_cache(tokens: list[dict[str, Any]]) -> None:
    """
    Update the Modelz cache with new token data.

    Args:
        tokens: List of token dictionaries to cache
    """
    cache = get_modelz_cache()
    cache["data"] = tokens
    cache["timestamp"] = time.time()
    logger.info(f"Cached {len(tokens)} Modelz tokens for {cache['ttl']}s")


async def get_modelz_client() -> httpx.AsyncClient:
    """Get an HTTP client for Modelz API requests."""
    return httpx.AsyncClient(
        timeout=30.0,
        headers={
            "User-Agent": "Gatewayz-Modelz-Client/1.0",
            "Accept": "application/json",
        },
    )


async def fetch_modelz_tokens(
    is_graduated: bool | None = None, use_cache: bool = True
) -> list[dict[str, Any]]:
    """
    Fetch model tokens from Modelz API with optional graduation filter and caching.

    Args:
        is_graduated: Filter for graduated (singularity) models:
                     - True: Only graduated/singularity models
                     - False: Only non-graduated models
                     - None: All models
        use_cache: Whether to use cached data if available

    Returns:
        List of model token data from Modelz
    """
    # Check cache first if requested
    if use_cache:
        cache = get_modelz_cache()
        current_time = time.time()

        # Check if cache is valid
        if (
            cache["data"] is not None
            and cache["timestamp"] is not None
            and (current_time - cache["timestamp"]) < cache["ttl"]
        ):
            logger.info(f"Using cached Modelz data (age: {current_time - cache['timestamp']:.1f}s)")
            cached_tokens = cache["data"]

            # Apply graduation filter to cached data if needed
            if is_graduated is not None:
                filtered_tokens = [
                    token for token in cached_tokens if token.get("isGraduated") == is_graduated
                ]
                logger.info(
                    f"Filtered cached data: {len(filtered_tokens)} tokens (is_graduated={is_graduated})"
                )
                return filtered_tokens

            return cached_tokens

    try:
        async with await get_modelz_client() as client:
            # Build URL with optional filter
            url = f"{MODELZ_BASE_URL}/api/tokens"
            params = {}

            if is_graduated is not None:
                params["isGraduated"] = str(is_graduated).lower()

            logger.info(f"Fetching Modelz tokens from API: {url} with params: {params}")

            response = await client.get(url, params=params)
            response.raise_for_status()

            data = response.json()
            tokens = _parse_modelz_response(data)

            # Cache the full dataset (without filters) for future use
            # Always cache when fetching from API, regardless of use_cache parameter
            _update_modelz_cache(tokens)

            logger.info(f"Successfully fetched {len(tokens)} tokens from Modelz API")
            return tokens

    except httpx.TimeoutException:
        logger.error("Timeout while fetching Modelz tokens")
        raise HTTPException(
            status_code=504, detail="Timeout while fetching data from Modelz API"
        ) from None
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error from Modelz API: {e.response.status_code} - {e.response.text}")
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Error fetching data from Modelz API: {e.response.text}",
        ) from e
    except Exception as e:
        logger.error(f"Unexpected error fetching Modelz tokens: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch data from Modelz API: {str(e)}"
        ) from e


async def get_modelz_model_ids(
    is_graduated: bool | None = None, use_cache: bool = True
) -> list[str]:
    """
    Get a list of model IDs that exist on Modelz.

    Args:
        is_graduated: Filter for graduated models (True/False/None)
        use_cache: Whether to use cached data if available

    Returns:
        List of model IDs from Modelz
    """
    tokens = await fetch_modelz_tokens(is_graduated, use_cache)

    model_ids = []
    for token in tokens:
        model_id = _extract_model_id(token)
        if model_id:
            model_ids.append(model_id)

    # Remove duplicates while preserving order
    unique_model_ids = list(dict.fromkeys(model_ids))
    logger.info(f"Extracted {len(unique_model_ids)} unique model IDs from Modelz")

    return unique_model_ids


async def check_model_exists_on_modelz(
    model_id: str, is_graduated: bool | None = None, use_cache: bool = True
) -> bool:
    """
    Check if a specific model exists on Modelz.

    Args:
        model_id: The model ID to check
        is_graduated: Filter for graduated models (True/False/None)
        use_cache: Whether to use cached data if available

    Returns:
        True if model exists on Modelz, False otherwise
    """
    model_ids = await get_modelz_model_ids(is_graduated, use_cache)
    return model_id in model_ids


async def get_modelz_model_details(
    model_id: str, use_cache: bool = True
) -> dict[str, Any] | None:
    """
    Get detailed information about a specific model from Modelz.

    Args:
        model_id: The model ID to fetch details for
        use_cache: Whether to use cached data if available

    Returns:
        Model details from Modelz or None if not found
    """
    tokens = await fetch_modelz_tokens(use_cache=use_cache)

    for token in tokens:
        token_model_id = _extract_model_id(token)
        if token_model_id and token_model_id == model_id.strip():
            return token

    return None


async def refresh_modelz_cache() -> dict[str, Any]:
    """
    Force refresh the Modelz cache by fetching fresh data from the API.

    Returns:
        Cache status information
    """
    try:
        logger.info("Force refreshing Modelz cache")

        # Clear existing cache
        clear_modelz_cache()

        # Fetch fresh data (this will populate the cache)
        tokens = await fetch_modelz_tokens(use_cache=False)

        # Get cache after refresh to verify it was populated
        cache = get_modelz_cache()

        # Verify cache was populated
        if cache["data"] is None or cache["timestamp"] is None:
            logger.error("Cache was not properly populated after refresh")
            return {
                "status": "error",
                "message": "Cache was not properly populated after refresh",
                "cache_size": 0,
                "timestamp": None,
                "ttl": cache["ttl"],
            }

        return {
            "status": "success",
            "message": f"Modelz cache refreshed with {len(tokens)} tokens",
            "cache_size": len(tokens),
            "timestamp": cache["timestamp"],
            "ttl": cache["ttl"],
        }

    except Exception as e:
        logger.error(f"Failed to refresh Modelz cache: {str(e)}")
        return {"status": "error", "message": f"Failed to refresh Modelz cache: {str(e)}"}


def fetch_models_from_modelz() -> list[dict[str, Any]]:
    """
    Fetch models from Modelz and normalize to catalog format.

    This is a synchronous function for use with the model catalog sync service.
    It fetches model tokens from Modelz API and transforms them into the standard
    catalog format expected by the model sync system.

    Returns:
        List of normalized model dictionaries for the catalog
    """
    try:
        logger.info("Fetching models from Modelz for catalog sync...")

        # Use synchronous httpx client
        with httpx.Client(
            timeout=30.0,
            headers={
                "User-Agent": "Gatewayz-Modelz-Client/1.0",
                "Accept": "application/json",
            },
        ) as client:
            url = f"{MODELZ_BASE_URL}/api/tokens"
            response = client.get(url)
            response.raise_for_status()

            data = response.json()
            tokens = _parse_modelz_response(data)

        # Normalize tokens to catalog format
        normalized_models = []
        for token in tokens:
            model_id = _extract_model_id(token)
            if not model_id:
                continue

            # Build the model slug with provider prefix
            slug = f"modelz/{model_id}"

            # Generate display name from model ID
            raw_display_name = model_id.replace("-", " ").replace("_", " ").title()
            # Clean malformed model names (remove company prefix, parentheses, etc.)
            display_name = clean_model_name(raw_display_name)

            # Extract context length if available
            context_length = token.get("context_length") or token.get("contextLength") or 4096

            normalized_model = {
                "id": slug,
                "slug": slug,
                "canonical_slug": slug,
                "name": display_name,
                "description": f"Modelz model: {model_id}",
                "context_length": context_length,
                "architecture": {
                    "modality": "text->text",
                    "input_modalities": ["text"],
                    "output_modalities": ["text"],
                },
                "pricing": {
                    "prompt": token.get("prompt_price") or "0",
                    "completion": token.get("completion_price") or "0",
                    "request": "0",
                    "image": "0",
                },
                "provider_slug": "modelz",
                "source_gateway": "modelz",
                "provider_site_url": "https://modelz.ai",
                "is_graduated": token.get("isGraduated", False),
            }

            normalized_models.append(normalized_model)

        # Update the cache using the shared utility for consistency
        _update_modelz_cache(tokens)

        logger.info(f"Successfully fetched {len(normalized_models)} models from Modelz")
        return normalized_models

    except httpx.TimeoutException:
        logger.error("Timeout while fetching Modelz models for catalog sync")
        return []
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error from Modelz API: {e.response.status_code}")
        return []
    except Exception as e:
        logger.error(f"Failed to fetch models from Modelz: {str(e)}")
        return []


def get_modelz_cache_status() -> dict[str, Any]:
    """
    Get the current status of the Modelz cache.

    Returns:
        Cache status information
    """
    cache = get_modelz_cache()
    current_time = time.time()

    if cache["data"] is None or cache["timestamp"] is None:
        return {
            "status": "empty",
            "message": "Modelz cache is empty",
            "cache_size": 0,
            "timestamp": None,
            "ttl": cache["ttl"],
            "age_seconds": None,
            "is_valid": False,
        }

    age_seconds = current_time - cache["timestamp"]
    is_valid = age_seconds < cache["ttl"]

    return {
        "status": "valid" if is_valid else "expired",
        "message": f"Modelz cache is {'valid' if is_valid else 'expired'}",
        "cache_size": len(cache["data"]) if cache["data"] else 0,
        "timestamp": cache["timestamp"],
        "ttl": cache["ttl"],
        "age_seconds": round(age_seconds, 1),
        "is_valid": is_valid,
    }
