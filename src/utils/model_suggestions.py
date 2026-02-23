"""
Model Suggestions Utility

Fuzzy matching for model names to suggest correct models when users make typos.
Uses difflib for fast, built-in fuzzy string matching.

Usage:
    from src.utils.model_suggestions import find_similar_models, get_available_models

    # Find similar models for a typo
    similar = await find_similar_models("gpt-5", max_suggestions=5)
    # Returns: ["gpt-4", "gpt-4-turbo", "gpt-3.5-turbo"]

    # Get all available models
    models = await get_available_models()
"""

import asyncio
import logging
from difflib import get_close_matches

logger = logging.getLogger(__name__)


async def get_available_models() -> list[str]:
    """
    Get list of all available model IDs.

    Returns:
        List of model IDs

    Note:
        This function imports from the models service to avoid circular imports.
        It runs the synchronous function in a thread pool.
    """
    try:
        # Import here to avoid circular dependency
        from src.services.models import get_all_models

        # Run in thread pool to avoid blocking
        all_models = await asyncio.to_thread(get_all_models)

        # Extract model IDs
        model_ids = [model.get("id", "") for model in all_models if model.get("id")]

        return model_ids
    except Exception as e:
        logger.error(f"Error fetching available models: {e}")
        # Return empty list if fetch fails
        return []


async def find_similar_models(
    requested_model: str,
    available_models: list[str] | None = None,
    max_suggestions: int = 5,
    cutoff: float = 0.6,
) -> list[str]:
    """
    Find similar model names using fuzzy matching.

    Uses difflib's get_close_matches for fast fuzzy string matching.
    Matches are case-insensitive and based on character sequence similarity.

    Args:
        requested_model: The model ID that was requested (possibly with typos)
        available_models: Optional list of available models (fetched if not provided)
        max_suggestions: Maximum number of suggestions to return (default: 5)
        cutoff: Similarity threshold 0-1 (default: 0.6, higher = more strict)

    Returns:
        List of similar model IDs sorted by similarity

    Examples:
        >>> await find_similar_models("gpt-5")
        ["gpt-4", "gpt-4-turbo", "gpt-3.5-turbo"]

        >>> await find_similar_models("claud-3")
        ["claude-3-opus", "claude-3-sonnet", "claude-3-haiku"]

        >>> await find_similar_models("lama-2")
        ["llama-2-70b", "llama-2-13b", "llama-2-7b"]
    """
    # Fetch available models if not provided
    if available_models is None:
        available_models = await get_available_models()

    if not available_models:
        logger.warning("No available models found for fuzzy matching")
        return []

    # Normalize the requested model (lowercase for matching)
    requested_lower = requested_model.lower()

    # Also check against lowercase versions of available models
    # But return the original casing
    model_mapping = {model.lower(): model for model in available_models}
    lowercase_models = list(model_mapping.keys())

    # Get close matches using difflib
    matches_lower = get_close_matches(
        requested_lower,
        lowercase_models,
        n=max_suggestions,
        cutoff=cutoff,
    )

    # Convert back to original casing
    matches = [model_mapping[match] for match in matches_lower]

    logger.debug(
        f"Found {len(matches)} similar models for '{requested_model}': {matches[:3]}"
    )

    return matches


async def find_similar_models_by_provider(
    requested_model: str,
    provider: str,
    max_suggestions: int = 5,
    cutoff: float = 0.6,
) -> list[str]:
    """
    Find similar models from a specific provider.

    Args:
        requested_model: The model ID that was requested
        provider: Provider slug (e.g., "openrouter", "cerebras")
        max_suggestions: Maximum number of suggestions
        cutoff: Similarity threshold 0-1

    Returns:
        List of similar model IDs from the specified provider
    """
    try:
        # Import here to avoid circular dependency
        from src.services.models import get_all_models

        # Get all models
        all_models = await asyncio.to_thread(get_all_models)

        # Filter by provider
        provider_models = [
            model.get("id", "")
            for model in all_models
            if model.get("id")
            and (
                model.get("provider_slug") == provider
                or model.get("source_gateway") == provider
            )
        ]

        # Find similar models within this provider
        return await find_similar_models(
            requested_model,
            available_models=provider_models,
            max_suggestions=max_suggestions,
            cutoff=cutoff,
        )
    except Exception as e:
        logger.error(f"Error finding similar models for provider {provider}: {e}")
        return []


def extract_model_family(model_id: str) -> str:
    """
    Extract the model family from a model ID.

    Examples:
        "gpt-4-turbo" -> "gpt-4"
        "claude-3-opus-20240229" -> "claude-3"
        "llama-2-70b-chat" -> "llama-2"

    Args:
        model_id: Full model ID

    Returns:
        Model family identifier
    """
    # Remove provider prefix if present
    if "/" in model_id:
        model_id = model_id.split("/", 1)[1]

    # Common patterns to extract family
    parts = model_id.lower().split("-")

    if len(parts) >= 2:
        # For GPT-4, Claude-3, etc, take first two parts
        return f"{parts[0]}-{parts[1]}"
    else:
        # Single word model
        return parts[0]


async def find_models_in_same_family(
    model_id: str,
    max_suggestions: int = 5,
) -> list[str]:
    """
    Find other models in the same family.

    Useful for suggesting alternatives when a specific variant is unavailable.

    Args:
        model_id: Model ID to find family members for
        max_suggestions: Maximum number of suggestions

    Returns:
        List of models in the same family

    Examples:
        >>> await find_models_in_same_family("gpt-4-turbo")
        ["gpt-4", "gpt-4-32k", "gpt-4-vision"]

        >>> await find_models_in_same_family("claude-3-opus")
        ["claude-3-sonnet", "claude-3-haiku"]
    """
    try:
        # Get model family
        family = extract_model_family(model_id)

        # Get all available models
        all_models = await get_available_models()

        # Filter models in same family
        family_models = [
            model
            for model in all_models
            if extract_model_family(model) == family and model != model_id
        ]

        # Return up to max_suggestions
        return family_models[:max_suggestions]
    except Exception as e:
        logger.error(f"Error finding models in family for {model_id}: {e}")
        return []


async def suggest_alternatives(
    requested_model: str,
    provider: str | None = None,
    include_family: bool = True,
    include_fuzzy: bool = True,
    max_total: int = 5,
) -> list[str]:
    """
    Comprehensive model suggestion function.

    Combines multiple suggestion strategies:
    1. Exact family members (e.g., other GPT-4 variants)
    2. Fuzzy matching across all models
    3. Provider-specific suggestions

    Args:
        requested_model: The model that was requested
        provider: Optional provider to filter by
        include_family: Include models from same family
        include_fuzzy: Include fuzzy matches
        max_total: Maximum total suggestions to return

    Returns:
        List of suggested model IDs (deduplicated and ordered by relevance)
    """
    suggestions = []

    try:
        # 1. Try to find models in the same family first
        if include_family:
            family_models = await find_models_in_same_family(
                requested_model, max_suggestions=3
            )
            suggestions.extend(family_models)

        # 2. Add fuzzy matches
        if include_fuzzy:
            if provider:
                # Provider-specific fuzzy matching
                fuzzy_matches = await find_similar_models_by_provider(
                    requested_model, provider, max_suggestions=5
                )
            else:
                # Global fuzzy matching
                fuzzy_matches = await find_similar_models(
                    requested_model, max_suggestions=5
                )

            suggestions.extend(fuzzy_matches)

        # Remove duplicates while preserving order
        seen = set()
        unique_suggestions = []
        for model in suggestions:
            if model not in seen:
                seen.add(model)
                unique_suggestions.append(model)

        # Return up to max_total suggestions
        return unique_suggestions[:max_total]

    except Exception as e:
        logger.error(f"Error generating model suggestions: {e}")
        return []


# Convenience function for backwards compatibility
find_similar_model_names = find_similar_models
