"""
Provider credit monitoring service for tracking and alerting on provider account balance.

This service monitors provider account credits to prevent service degradation from
credit exhaustion. It sends alerts when credits fall below thresholds and helps
ensure seamless failover before providers run out of credits.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
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

# Sliding-window 402 frequency tracker per provider
# Key: provider name, Value: list of 402 timestamps
_402_tracker: dict[str, list[datetime]] = {}
_402_WINDOW_MINUTES = 15  # Sliding window for 402 tracking
_402_CRITICAL_THRESHOLD = 10  # Critical if >=10 402s in window
_402_WARNING_THRESHOLD = 3  # Warning if >=3 402s in window

# Providers monitored via 402-frequency (no public balance API)
MONITORED_402_PROVIDERS = {"together", "deepinfra", "fireworks", "groq"}


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
                "error": "API key not configured",
            }

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {Config.OPENROUTER_API_KEY}"},
                timeout=10.0,
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
                    "error": "Could not parse balance",
                }

            # Determine status based on thresholds
            status = _determine_credit_status(balance)

            result = {
                "provider": provider,
                "balance": balance,
                "status": status,
                "checked_at": datetime.now(UTC),
                "cached": False,
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
            "error": f"HTTP {e.response.status_code}",
        }
    except Exception as e:
        logger.error(f"Failed to check {provider} credits: {e}")
        return {
            "provider": provider,
            "balance": None,
            "status": "unknown",
            "checked_at": datetime.now(UTC),
            "cached": False,
            "error": str(e),
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


_402_MAX_ENTRIES_PER_PROVIDER = 100  # Hard cap to bound memory

def record_provider_402(provider: str) -> None:
    """Record a 402 Payment Required response from a provider.

    Call this from provider clients when they receive HTTP 402.
    The credit monitor uses 402 frequency as a proxy signal for
    credit exhaustion on providers without a public balance API.

    Only records for providers in ``MONITORED_402_PROVIDERS`` to
    prevent unbounded key creation.
    """
    provider = provider.lower()
    if provider not in MONITORED_402_PROVIDERS:
        return  # Ignore providers we don't monitor via 402 frequency
    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=_402_WINDOW_MINUTES)
    if provider not in _402_tracker:
        _402_tracker[provider] = []
    # Prune stale entries first, then append
    _402_tracker[provider] = [t for t in _402_tracker[provider] if t > cutoff]
    _402_tracker[provider].append(now)


def check_provider_402_status(provider: str) -> dict[str, Any]:
    """Check the 402-frequency status for a provider.

    Returns a result dict compatible with ``check_openrouter_credits()``
    so it can be merged into ``check_all_provider_credits()``.
    """
    provider = provider.lower()
    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=_402_WINDOW_MINUTES)

    # Prune old entries
    timestamps = _402_tracker.get(provider, [])
    timestamps = [t for t in timestamps if t > cutoff]
    _402_tracker[provider] = timestamps

    count = len(timestamps)
    if count >= _402_CRITICAL_THRESHOLD:
        status = "critical"
    elif count >= _402_WARNING_THRESHOLD:
        status = "warning"
    elif count > 0:
        status = "info"
    else:
        status = "healthy"

    return {
        "provider": provider,
        "balance": None,  # Not available for 402-monitored providers
        "status": status,
        "checked_at": now,
        "cached": False,
        "monitoring_method": "402_frequency",
        "recent_402_count": count,
        "window_minutes": _402_WINDOW_MINUTES,
    }


async def check_all_provider_credits() -> dict[str, dict[str, Any]]:
    """
    Check credit balance for all monitored providers.

    Uses direct balance API for OpenRouter and 402-frequency monitoring
    for Together, DeepInfra, Fireworks, and Groq.

    Returns:
        Dictionary mapping provider name to balance information
    """
    results = {}

    # OpenRouter: direct balance API
    results["openrouter"] = await check_openrouter_credits()

    # Top providers without balance APIs: monitor via 402 frequency
    for provider in MONITORED_402_PROVIDERS:
        results[provider] = check_provider_402_status(provider)

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
                    to_email=Config.ADMIN_EMAIL if hasattr(Config, "ADMIN_EMAIL") else None,
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
            endpoint="credit_monitor",
            extra_context={"balance": balance, "status": status, "thresholds": CREDIT_THRESHOLDS},
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
