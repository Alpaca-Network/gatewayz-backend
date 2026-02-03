"""
Circuit Breaker Status and Management Endpoints

Provides endpoints for monitoring and managing circuit breaker states:
- GET /circuit-breakers: List all circuit breaker states
- GET /circuit-breakers/{provider}: Get state for specific provider
- POST /circuit-breakers/{provider}/reset: Manually reset a circuit breaker
- POST /circuit-breakers/reset-all: Reset all circuit breakers

Related Issues: #1043, #1039
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status

from src.services.circuit_breaker import (
    get_all_circuit_breakers,
    get_circuit_breaker,
    reset_all_circuit_breakers,
    reset_circuit_breaker,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/circuit-breakers",
    tags=["circuit-breakers", "monitoring"],
)


@router.get("", response_model=dict[str, Any])
async def get_all_circuit_breaker_states():
    """
    Get current state of all circuit breakers.

    Returns a dictionary mapping provider names to their circuit breaker states.

    Example response:
    ```json
    {
        "openrouter": {
            "provider": "openrouter",
            "state": "closed",
            "failure_count": 0,
            "success_count": 15,
            "failure_rate": 0.0,
            "recent_requests": 15,
            "opened_at": null,
            "seconds_until_retry": 0
        },
        "groq": {
            "provider": "groq",
            "state": "open",
            "failure_count": 5,
            "success_count": 0,
            "failure_rate": 1.0,
            "recent_requests": 5,
            "opened_at": "2026-02-03T10:30:00Z",
            "seconds_until_retry": 45
        }
    }
    ```
    """
    try:
        states = get_all_circuit_breakers()
        return {
            "circuit_breakers": states,
            "total_count": len(states),
            "open_count": sum(1 for s in states.values() if s["state"] == "open"),
            "half_open_count": sum(1 for s in states.values() if s["state"] == "half_open"),
            "closed_count": sum(1 for s in states.values() if s["state"] == "closed"),
        }
    except Exception as e:
        logger.error(f"Failed to get circuit breaker states: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve circuit breaker states: {str(e)}"
        )


@router.get("/{provider}", response_model=dict[str, Any])
async def get_circuit_breaker_state(provider: str):
    """
    Get current state of a specific provider's circuit breaker.

    Args:
        provider: Provider name (e.g., 'openrouter', 'groq')

    Returns:
        Circuit breaker state information

    Example response:
    ```json
    {
        "provider": "openrouter",
        "state": "closed",
        "failure_count": 0,
        "success_count": 15,
        "failure_rate": 0.0,
        "recent_requests": 15,
        "opened_at": null,
        "seconds_until_retry": 0
    }
    ```
    """
    try:
        breaker = get_circuit_breaker(provider)
        return breaker.get_state()
    except Exception as e:
        logger.error(f"Failed to get circuit breaker state for {provider}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve circuit breaker state: {str(e)}"
        )


@router.post("/{provider}/reset", response_model=dict[str, Any])
async def reset_provider_circuit_breaker(provider: str):
    """
    Manually reset a circuit breaker to CLOSED state.

    This can be used when you know a provider has recovered and want to
    immediately resume sending traffic to it.

    Args:
        provider: Provider name (e.g., 'openrouter', 'groq')

    Returns:
        Success status and new circuit breaker state

    Example response:
    ```json
    {
        "success": true,
        "message": "Circuit breaker for 'openrouter' has been reset",
        "state": {
            "provider": "openrouter",
            "state": "closed",
            "failure_count": 0,
            "success_count": 0,
            ...
        }
    }
    ```
    """
    try:
        success = reset_circuit_breaker(provider)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Circuit breaker for provider '{provider}' not found"
            )

        breaker = get_circuit_breaker(provider)
        new_state = breaker.get_state()

        logger.info(f"Circuit breaker for '{provider}' was manually reset")

        return {
            "success": True,
            "message": f"Circuit breaker for '{provider}' has been reset",
            "state": new_state,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to reset circuit breaker for {provider}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset circuit breaker: {str(e)}"
        )


@router.post("/reset-all", response_model=dict[str, Any])
async def reset_all_provider_circuit_breakers():
    """
    Manually reset all circuit breakers to CLOSED state.

    This is a bulk operation that resets all provider circuit breakers.
    Use with caution - only reset when you're confident all providers have recovered.

    Returns:
        Success status and count of reset circuit breakers

    Example response:
    ```json
    {
        "success": true,
        "message": "All circuit breakers have been reset",
        "reset_count": 3,
        "states": {
            "openrouter": {...},
            "groq": {...},
            "together": {...}
        }
    }
    ```
    """
    try:
        reset_all_circuit_breakers()

        states = get_all_circuit_breakers()

        logger.warning(f"All circuit breakers ({len(states)}) were manually reset")

        return {
            "success": True,
            "message": "All circuit breakers have been reset",
            "reset_count": len(states),
            "states": states,
        }
    except Exception as e:
        logger.error(f"Failed to reset all circuit breakers: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset circuit breakers: {str(e)}"
        )
