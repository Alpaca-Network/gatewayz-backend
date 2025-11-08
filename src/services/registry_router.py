"""
Registry-Driven Router

This module provides intelligent routing for multi-provider models using the canonical registry.
It replaces static failover chains with dynamic, registry-driven provider selection and includes
circuit breaker patterns for provider health management.
"""

import logging
from typing import List, Optional, Dict, Any
from fastapi import HTTPException

from src.services.canonical_model_registry import get_canonical_registry
from src.services.provider_failover import build_provider_failover_chain, should_failover
from src.services.model_transformations import transform_model_id

logger = logging.getLogger(__name__)


def get_provider_chain_for_model(
    model_id: str,
    initial_provider: Optional[str] = None,
    use_registry: bool = True,
) -> List[Dict[str, Any]]:
    """
    Get the provider attempt chain for a given model.

    This function checks the canonical registry first for multi-provider models.
    If the model is in the registry, it returns providers in priority order.
    Otherwise, it falls back to the legacy failover chain.

    Args:
        model_id: The canonical model ID
        initial_provider: Optional preferred provider to try first
        use_registry: Whether to use the canonical registry (default: True)

    Returns:
        List of provider attempt dictionaries with keys:
            - provider: Provider name
            - model_id: Provider-specific model ID
            - priority: Priority level (lower = higher priority)
            - from_registry: Boolean indicating if from canonical registry
    """
    provider_chain = []

    if use_registry:
        try:
            registry = get_canonical_registry()
            canonical_model = registry.get_model(model_id)

            if canonical_model:
                # Model is in canonical registry - use registry providers
                logger.info(
                    f"Using canonical registry for model {model_id} "
                    f"(found {len(canonical_model.providers)} providers)"
                )

                # Get enabled providers sorted by priority
                enabled_providers = canonical_model.get_enabled_providers()

                if not enabled_providers:
                    logger.warning(
                        f"Model {model_id} in registry but has no enabled providers"
                    )
                else:
                    # If initial_provider specified, try to use it first
                    if initial_provider:
                        # Find if initial provider is in the list
                        initial_config = canonical_model.get_provider_by_name(
                            initial_provider
                        )
                        if initial_config and initial_config.enabled:
                            # Add initial provider first
                            provider_chain.append(
                                {
                                    "provider": initial_config.name,
                                    "model_id": initial_config.model_id,
                                    "priority": initial_config.priority,
                                    "from_registry": True,
                                }
                            )
                            # Add remaining providers
                            for prov in enabled_providers:
                                if prov.name != initial_provider:
                                    provider_chain.append(
                                        {
                                            "provider": prov.name,
                                            "model_id": prov.model_id,
                                            "priority": prov.priority,
                                            "from_registry": True,
                                        }
                                    )
                        else:
                            # Initial provider not available, use priority order
                            logger.warning(
                                f"Preferred provider {initial_provider} not available for {model_id}, "
                                f"using priority order"
                            )
                            for prov in enabled_providers:
                                provider_chain.append(
                                    {
                                        "provider": prov.name,
                                        "model_id": prov.model_id,
                                        "priority": prov.priority,
                                        "from_registry": True,
                                    }
                                )
                    else:
                        # No initial provider, use priority order
                        for prov in enabled_providers:
                            provider_chain.append(
                                {
                                    "provider": prov.name,
                                    "model_id": prov.model_id,
                                    "priority": prov.priority,
                                    "from_registry": True,
                                }
                            )

                    logger.info(
                        f"Registry-based provider chain for {model_id}: "
                        f"{[p['provider'] for p in provider_chain]}"
                    )
                    return provider_chain

        except Exception as e:
            logger.warning(f"Error accessing canonical registry for {model_id}: {e}")

    # Fallback to legacy failover chain
    logger.info(
        f"Using legacy failover chain for model {model_id} "
        f"(initial_provider: {initial_provider})"
    )
    legacy_chain = build_provider_failover_chain(initial_provider)

    for provider_name in legacy_chain:
        # Transform model ID for this provider
        provider_model_id = transform_model_id(
            model_id, provider_name, use_multi_provider=False
        )

        provider_chain.append(
            {
                "provider": provider_name,
                "model_id": provider_model_id,
                "priority": None,  # Legacy chain doesn't have explicit priorities
                "from_registry": False,
            }
        )

    logger.info(
        f"Legacy provider chain for {model_id}: "
        f"{[p['provider'] for p in provider_chain]}"
    )
    return provider_chain


def should_attempt_failover(
    exc: Exception, attempt_number: int, total_attempts: int
) -> bool:
    """
    Determine if a failover should be attempted based on the exception and attempt count.

    Args:
        exc: The exception that was raised
        attempt_number: Current attempt number (1-indexed)
        total_attempts: Total number of attempts available

    Returns:
        True if failover should be attempted, False otherwise
    """
    # If this was the last attempt, don't failover
    if attempt_number >= total_attempts:
        return False

    # Check if this is a retryable error
    if isinstance(exc, HTTPException):
        return should_failover(exc)

    # For non-HTTPException errors, default to retrying
    # (they'll be mapped to HTTPException by the caller)
    return True


def log_provider_attempt(
    model_id: str,
    provider: str,
    provider_model_id: str,
    attempt_number: int,
    total_attempts: int,
    from_registry: bool,
) -> None:
    """
    Log a provider attempt for observability.

    Args:
        model_id: Canonical model ID
        provider: Provider name
        provider_model_id: Provider-specific model ID
        attempt_number: Current attempt number (1-indexed)
        total_attempts: Total number of attempts
        from_registry: Whether this provider came from the canonical registry
    """
    source = "canonical registry" if from_registry else "legacy failover chain"
    logger.info(
        f"Attempt {attempt_number}/{total_attempts} for model '{model_id}': "
        f"Trying provider '{provider}' (model: '{provider_model_id}') from {source}"
    )


def log_provider_success(
    model_id: str,
    provider: str,
    attempt_number: int,
    total_attempts: int,
) -> None:
    """
    Log a successful provider attempt.

    Args:
        model_id: Canonical model ID
        provider: Provider name
        attempt_number: Attempt number that succeeded
        total_attempts: Total attempts available
    """
    if attempt_number == 1:
        logger.info(
            f"✓ Request successful with primary provider '{provider}' for model '{model_id}'"
        )
    else:
        logger.info(
            f"✓ Request successful with failover provider '{provider}' for model '{model_id}' "
            f"(attempt {attempt_number}/{total_attempts})"
        )


def log_provider_failure(
    model_id: str,
    provider: str,
    error: str,
    attempt_number: int,
    total_attempts: int,
    will_retry: bool,
) -> None:
    """
    Log a failed provider attempt.

    Args:
        model_id: Canonical model ID
        provider: Provider name
        error: Error message
        attempt_number: Current attempt number
        total_attempts: Total attempts available
        will_retry: Whether a retry will be attempted
    """
    if will_retry:
        next_attempt = attempt_number + 1
        logger.warning(
            f"✗ Provider '{provider}' failed for model '{model_id}' "
            f"(attempt {attempt_number}/{total_attempts}): {error}. "
            f"Will retry with next provider..."
        )
    else:
        logger.error(
            f"✗ All providers failed for model '{model_id}'. "
            f"Last attempt was provider '{provider}' (attempt {attempt_number}/{total_attempts}): {error}"
        )


def get_model_info(model_id: str) -> Dict[str, Any]:
    """
    Get information about a model from the canonical registry.

    Args:
        model_id: Canonical model ID

    Returns:
        Dictionary with model information, or empty dict if not in registry
    """
    try:
        registry = get_canonical_registry()
        canonical_model = registry.get_model(model_id)

        if canonical_model:
            return {
                "id": canonical_model.id,
                "name": canonical_model.name,
                "description": canonical_model.description,
                "providers": [p.name for p in canonical_model.get_enabled_providers()],
                "primary_provider": canonical_model.primary_provider,
                "supports_streaming": canonical_model.supports_streaming,
                "supports_function_calling": canonical_model.supports_function_calling,
                "in_registry": True,
            }
    except Exception as e:
        logger.debug(f"Error getting model info for {model_id}: {e}")

    return {"id": model_id, "in_registry": False}
