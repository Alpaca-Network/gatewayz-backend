"""
Provider Failover Service

Automatically routes requests to alternative providers when primary provider fails.
Uses database to track model availability across providers and provider health.
"""

import logging
from datetime import datetime, timezone

from src.db.failover_db import get_providers_for_model
from src.db.model_health import record_model_call

logger = logging.getLogger(__name__)


class ProviderFailoverError(Exception):
    """Raised when all providers fail"""
    def __init__(self, message: str, attempts: list[dict], last_error: Exception):
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


async def route_with_failover(
    model: str,
    request_data: dict,
    user_preferences: dict | None = None,
    max_attempts: int = 3
) -> dict:
    """
    Route a request with automatic provider failover

    Args:
        model: Canonical model ID (e.g., "gpt-4")
        request_data: Request payload (messages, temperature, etc.)
        user_preferences: Optional user preferences (preferred_provider, max_cost, etc.)
        max_attempts: Maximum number of providers to try

    Returns:
        Response dict with metadata about which provider was used

    Raises:
        ProviderFailoverError: If all providers fail

    Example:
        response = await route_with_failover(
            model="gpt-4",
            request_data={"messages": [...]},
            user_preferences={"preferred_provider": "openrouter"}
        )

        # Response includes:
        # {
        #   "choices": [...],
        #   "usage": {...},
        #   "_gatewayz_metadata": {
        #     "provider_used": "featherless",
        #     "attempt_number": 2,
        #     "failed_providers": ["openrouter"]
        #   }
        # }
    """
    # Get all providers that have this model (from database)
    available_providers = get_providers_for_model(
        model_id=model,
        active_only=True,
        healthy_only=False  # Include degraded providers for fallback
    )

    if not available_providers:
        raise ValueError(f"Model '{model}' not available on any provider. "
                        f"Run 'python scripts/sync_models.py' to populate database.")

    # Apply user preferences (move preferred provider to front)
    if user_preferences and user_preferences.get("preferred_provider"):
        preferred = user_preferences["preferred_provider"]
        available_providers.sort(
            key=lambda p: (0 if p["provider_slug"] == preferred else 1,
                          0 if p["provider_health_status"] == "healthy" else 1,
                          p["provider_response_time_ms"] or 9999)
        )

    # Limit attempts
    providers_to_try = available_providers[:max_attempts]

    logger.info(f"Routing '{model}' with failover across {len(providers_to_try)} providers")

    attempts = []
    last_error = None

    for idx, provider in enumerate(providers_to_try):
        provider_slug = provider["provider_slug"]
        provider_model_id = provider["provider_model_id"]

        try:
            logger.info(f"Attempt {idx + 1}/{len(providers_to_try)}: "
                       f"Trying '{model}' on {provider_slug} (as '{provider_model_id}')")

            # Track attempt start time
            start_time = datetime.now(timezone.utc)

            # Make the actual provider request
            response = await _call_provider(
                provider_slug=provider_slug,
                provider_model_id=provider_model_id,
                request_data=request_data
            )

            # Track success
            end_time = datetime.now(timezone.utc)
            response_time_ms = int((end_time - start_time).total_seconds() * 1000)

            await record_model_call(
                provider=provider_slug,
                model=provider_model_id,
                success=True,
                response_time_ms=response_time_ms,
                input_tokens=response.get("usage", {}).get("prompt_tokens", 0),
                output_tokens=response.get("usage", {}).get("completion_tokens", 0)
            )

            logger.info(f"✓ Success on {provider_slug} ({response_time_ms}ms)")

            # Add metadata
            response["_gatewayz_metadata"] = {
                "provider_used": provider_slug,
                "provider_model_id": provider_model_id,
                "canonical_model_id": model,
                "attempt_number": idx + 1,
                "failed_providers": [a["provider"] for a in attempts],
                "response_time_ms": response_time_ms,
                "pricing": {
                    "prompt": provider["pricing_prompt"],
                    "completion": provider["pricing_completion"]
                }
            }

            return response

        except Exception as e:
            logger.warning(f"✗ Provider {provider_slug} failed for '{model}': {str(e)[:100]}")

            # Track failure
            await record_model_call(
                provider=provider_slug,
                model=provider_model_id,
                success=False,
                error_message=str(e)
            )

            attempts.append({
                "provider": provider_slug,
                "provider_model_id": provider_model_id,
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

            last_error = e

            # Continue to next provider
            continue

    # All providers failed
    raise ProviderFailoverError(
        f"All {len(providers_to_try)} providers failed for model '{model}'",
        attempts=attempts,
        last_error=last_error
    )


async def _call_provider(
    provider_slug: str,
    provider_model_id: str,
    request_data: dict
) -> dict:
    """
    Internal function to call a specific provider

    This would be implemented to route to the actual provider clients
    (openrouter_client, cerebras_client, etc.)

    For now, this is a placeholder that imports and routes dynamically.
    """
    # Import provider clients dynamically
    provider_clients = {
        "openrouter": "src.services.openrouter_client",
        "cerebras": "src.services.cerebras_client",
        "featherless": "src.services.featherless_client",
        "deepinfra": "src.services.deepinfra_client",
        "xai": "src.services.xai_client",
        "portkey": "src.services.portkey_client",
        # Add more as needed
    }

    if provider_slug not in provider_clients:
        raise NotImplementedError(f"Provider '{provider_slug}' not yet integrated with failover")

    # Dynamic import (TODO: optimize with cached imports)
    # For now, raise NotImplementedError to indicate this needs integration
    raise NotImplementedError(
        f"Failover integration for '{provider_slug}' pending. "
        f"Need to integrate with existing provider client."
    )

    # Future implementation would be:
    # module = importlib.import_module(provider_clients[provider_slug])
    # return await module.chat_completion(model=provider_model_id, **request_data)


def explain_failover_for_model(model: str) -> dict:
    """
    Get failover information for a model (for debugging/monitoring)

    Args:
        model: Canonical model ID

    Returns:
        Dict with failover configuration for this model

    Example:
        info = explain_failover_for_model("gpt-4")
        # Returns:
        # {
        #   "model": "gpt-4",
        #   "providers_available": 3,
        #   "failover_order": [
        #     {"provider": "openrouter", "health": "healthy", "priority": 1},
        #     {"provider": "featherless", "health": "healthy", "priority": 2},
        #     {"provider": "portkey", "health": "degraded", "priority": 3}
        #   ],
        #   "recommendation": "Primary: openrouter, Fallback: featherless"
        # }
    """
    providers = get_providers_for_model(model, active_only=True)

    if not providers:
        return {
            "model": model,
            "providers_available": 0,
            "failover_order": [],
            "recommendation": "Model not available on any provider"
        }

    failover_order = []
    for idx, p in enumerate(providers[:5]):  # Show top 5
        failover_order.append({
            "priority": idx + 1,
            "provider": p["provider_slug"],
            "provider_model_id": p["provider_model_id"],
            "health": p["provider_health_status"],
            "response_time_ms": p["provider_response_time_ms"],
            "pricing_prompt": p["pricing_prompt"],
            "success_rate": p["success_rate"]
        })

    recommendation = "Not available"
    if len(providers) >= 1:
        primary = providers[0]["provider_slug"]
        if len(providers) >= 2:
            fallback = providers[1]["provider_slug"]
            recommendation = f"Primary: {primary}, Fallback: {fallback}"
        else:
            recommendation = f"Primary: {primary} (no fallback available)"

    return {
        "model": model,
        "providers_available": len(providers),
        "failover_order": failover_order,
        "recommendation": recommendation
    }


def get_fallback_models_from_db(provider: str) -> list[dict]:
    """
    Get fallback models from database for a provider (stub for backward compatibility).

    Args:
        provider: Provider name

    Returns:
        Empty list (stub implementation)
    """
    logger.warning(f"get_fallback_models_from_db called for {provider} but not implemented")
    return []
