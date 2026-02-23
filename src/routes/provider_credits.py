"""
Provider credit balance endpoints for monitoring provider account balances.

These endpoints allow administrators to check provider credit balances
and receive alerts before credits run out.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.security.deps import require_admin
from datetime import UTC

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/provider-credits", tags=["provider-credits"])


class ProviderCreditBalance(BaseModel):
    """Provider credit balance information"""

    provider: str = Field(..., description="Provider name")
    balance: float | None = Field(None, description="Current balance in USD")
    status: str = Field(..., description="Status: healthy, warning, critical, unknown")
    checked_at: str = Field(..., description="When the balance was checked (ISO 8601)")
    cached: bool = Field(False, description="Whether this is cached data")
    error: str | None = Field(None, description="Error message if check failed")


class ProviderCreditsResponse(BaseModel):
    """Response containing all provider credit balances"""

    providers: dict[str, ProviderCreditBalance] = Field(
        ..., description="Map of provider name to credit balance"
    )
    timestamp: str = Field(..., description="Response timestamp (ISO 8601)")


@router.get("/balance", response_model=ProviderCreditsResponse)
async def get_provider_credit_balances(
    current_user: dict = Depends(require_admin),
) -> ProviderCreditsResponse:
    """
    Get credit balances for all monitored providers.

    This endpoint checks the current credit balance for all providers
    that support credit-based billing (e.g., OpenRouter).

    **Requires admin role**

    Returns:
        ProviderCreditsResponse with balance information for each provider
    """
    from datetime import datetime

    from src.services.provider_credit_monitor import check_all_provider_credits

    try:
        balances = await check_all_provider_credits()

        # Convert to response format
        providers = {}
        for provider, info in balances.items():
            providers[provider] = ProviderCreditBalance(
                provider=info["provider"],
                balance=info.get("balance"),
                status=info["status"],
                checked_at=info["checked_at"].isoformat(),
                cached=info.get("cached", False),
                error=info.get("error"),
            )

        return ProviderCreditsResponse(
            providers=providers, timestamp=datetime.now(UTC).isoformat()
        )

    except Exception as e:
        logger.error(f"Failed to get provider credit balances: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve credit balances")


@router.get("/balance/{provider}", response_model=ProviderCreditBalance)
async def get_provider_credit_balance(
    provider: str,
    current_user: dict = Depends(require_admin),
) -> ProviderCreditBalance:
    """
    Get credit balance for a specific provider.

    **Requires admin role**

    Args:
        provider: Provider name (e.g., "openrouter")

    Returns:
        ProviderCreditBalance with balance information
    """
    from src.services.provider_credit_monitor import check_openrouter_credits

    try:
        if provider.lower() == "openrouter":
            info = await check_openrouter_credits()
        else:
            raise HTTPException(
                status_code=400, detail=f"Provider '{provider}' not supported for credit monitoring"
            )

        return ProviderCreditBalance(
            provider=info["provider"],
            balance=info.get("balance"),
            status=info["status"],
            checked_at=info["checked_at"].isoformat(),
            cached=info.get("cached", False),
            error=info.get("error"),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get credit balance for {provider}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve balance for {provider}")


@router.post("/balance/clear-cache")
async def clear_provider_credit_cache(
    provider: str | None = None,
    current_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """
    Clear the provider credit balance cache.

    This forces a fresh check on the next balance query.

    **Requires admin role**

    Args:
        provider: Optional provider name to clear cache for. If not provided, clears all.

    Returns:
        Success message
    """
    from src.services.provider_credit_monitor import clear_credit_cache

    try:
        clear_credit_cache(provider)

        return {
            "success": True,
            "message": f"Cleared credit cache for {provider if provider else 'all providers'}",
        }

    except Exception as e:
        logger.error(f"Failed to clear credit cache: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear cache")
