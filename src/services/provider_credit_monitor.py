"""
Provider credit monitoring service for tracking and alerting on provider account balance.

This service monitors provider account credits to prevent service degradation from
credit exhaustion. It sends alerts when credits fall below thresholds and helps
ensure seamless failover before providers run out of credits.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, UTC
from typing import Any

from src.config import Config

logger = logging.getLogger(__name__)

# Credit thresholds for alerting (in USD)
CREDIT_THRESHOLDS = {
    "critical": 5.0,  # Alert immediately
    "warning": 20.0,  # Alert but not urgent
    "info": 50.0,  # Informational alert
}

# Time to cache credit balance checks (to avoid excessive API calls)
CACHE_DURATION_MINUTES = 15

# In-memory cache for credit balance
_credit_balance_cache: dict[str, dict[str, Any]] = {}


async def check_openrouter_credits() -> dict[str, Any]:
    """
    Check OpenRouter account credit balance.

    Returns:
        Dictionary with balance information:
        {
            "provider": "openrouter",
            "balance": 123.45,
            "status": "healthy" | "warning" | "critical" | "unknown",
            "checked_at": datetime,
            "cached": bool
        }
    """
    provider = "openrouter"

    # Check cache first
    if provider in _credit_balance_cache:
        cached = _credit_balance_cache[provider]
        cache_age = datetime.now(UTC) - cached["checked_at"]
        if cache_age < timedelta(minutes=CACHE_DURATION_MINUTES):
            logger.debug(f"Using cached credit balance for {provider}: ${cached['balance']:.2f}")
            return {**cached, "cached": True}

    # Fetch fresh balance
    try:
        import httpx

        if not Config.OPENROUTER_API_KEY:
            logger.warning(f"{provider}: API key not configured")
            return {
                "provider": provider,
                "balance": None,
                "status": "unknown",
                "checked_at": datetime.now(UTC),
                "cached": False,
                "error": "API key not configured"
            }

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {Config.OPENROUTER_API_KEY}"},
                timeout=10.0
            )
            response.raise_for_status()
            data = response.json()

            # OpenRouter returns balance in credits object
            balance = data.get("data", {}).get("limit_remaining")

            if balance is None:
                logger.warning(f"{provider}: Could not parse balance from API response")
                return {
                    "provider": provider,
                    "balance": None,
                    "status": "unknown",
                    "checked_at": datetime.now(UTC),
                    "cached": False,
                    "error": "Could not parse balance"
                }

            # Determine status based on thresholds
            status = _determine_credit_status(balance)

            result = {
                "provider": provider,
                "balance": balance,
                "status": status,
                "checked_at": datetime.now(UTC),
                "cached": False
            }

            # Update cache
            _credit_balance_cache[provider] = result

            # Log status
            if status == "critical":
                logger.error(f"{provider} credit balance CRITICAL: ${balance:.2f}")
            elif status == "warning":
                logger.warning(f"{provider} credit balance WARNING: ${balance:.2f}")
            else:
                logger.info(f"{provider} credit balance: ${balance:.2f}")

            return result

    except httpx.HTTPStatusError as e:
        logger.error(f"Failed to check {provider} credits (HTTP {e.response.status_code}): {e}")
        return {
            "provider": provider,
            "balance": None,
            "status": "unknown",
            "checked_at": datetime.now(UTC),
            "cached": False,
            "error": f"HTTP {e.response.status_code}"
        }
    except Exception as e:
        logger.error(f"Failed to check {provider} credits: {e}")
        return {
            "provider": provider,
            "balance": None,
            "status": "unknown",
            "checked_at": datetime.now(UTC),
            "cached": False,
            "error": str(e)
        }


def _determine_credit_status(balance: float) -> str:
    """Determine credit status based on balance thresholds."""
    if balance <= CREDIT_THRESHOLDS["critical"]:
        return "critical"
    elif balance <= CREDIT_THRESHOLDS["warning"]:
        return "warning"
    elif balance <= CREDIT_THRESHOLDS["info"]:
        return "info"
    else:
        return "healthy"


async def check_all_provider_credits() -> dict[str, dict[str, Any]]:
    """
    Check credit balance for all monitored providers.

    Returns:
        Dictionary mapping provider name to balance information
    """
    results = {}

    # Check OpenRouter
    results["openrouter"] = await check_openrouter_credits()

    # TODO: Add other providers with credit-based billing
    # results["portkey"] = await check_portkey_credits()
    # results["featherless"] = await check_featherless_credits()

    return results


async def send_low_credit_alert(provider: str, balance: float, status: str) -> None:
    """
    Send alert notification for low provider credits.

    Args:
        provider: Provider name
        balance: Current balance
        status: Status level (critical, warning, info)
    """
    try:
        # Import here to avoid circular dependencies
        from src.utils.sentry_context import capture_provider_error

        message = f"{provider.upper()} credit balance is {status.upper()}: ${balance:.2f}"

        if status == "critical":
            # For critical alerts, also try to send email notification
            try:
                from src.services.notification import send_email

                await send_email(
                    to_email=Config.ADMIN_EMAIL if hasattr(Config, 'ADMIN_EMAIL') else None,
                    subject=f"URGENT: {provider} credits critically low",
                    body=f"""
                    <h2>Provider Credit Alert</h2>
                    <p><strong>Provider:</strong> {provider}</p>
                    <p><strong>Balance:</strong> ${balance:.2f}</p>
                    <p><strong>Status:</strong> {status}</p>
                    <p><strong>Action Required:</strong> Add credits immediately to prevent service disruption</p>
                    <p><a href="https://openrouter.ai/settings/credits">Add Credits</a></p>
                    """,
                )
                logger.info(f"Sent email alert for {provider} low credits")
            except Exception as email_err:
                logger.warning(f"Failed to send email alert for {provider}: {email_err}")

        # Log to Sentry for monitoring
        error = Exception(message)
        capture_provider_error(
            error,
            provider=provider,
            endpoint='credit_monitor',
            extra_context={
                'balance': balance,
                'status': status,
                'thresholds': CREDIT_THRESHOLDS
            }
        )

    except Exception as e:
        logger.error(f"Failed to send alert for {provider} low credits: {e}")


def clear_credit_cache(provider: str | None = None) -> None:
    """
    Clear the credit balance cache.

    Args:
        provider: Specific provider to clear, or None to clear all
    """
    if provider:
        _credit_balance_cache.pop(provider, None)
        logger.debug(f"Cleared credit cache for {provider}")
    else:
        _credit_balance_cache.clear()
        logger.debug("Cleared all credit caches")
