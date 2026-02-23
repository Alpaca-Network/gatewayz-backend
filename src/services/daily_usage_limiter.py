"""
Daily Usage Limiter Service
Tracks and enforces daily usage limits for all users.
"""

import logging
from datetime import datetime, timedelta, UTC
from typing import Any

from src.config.supabase_config import get_supabase_client
from src.config.usage_limits import (
    DAILY_LIMIT_RESET_HOUR,
    DAILY_USAGE_CRITICAL_THRESHOLD,
    DAILY_USAGE_LIMIT,
    DAILY_USAGE_WARNING_THRESHOLD,
    ENFORCE_DAILY_LIMITS,
    TRACK_DAILY_USAGE,
)

logger = logging.getLogger(__name__)


class DailyUsageLimitExceeded(Exception):
    """Raised when a user exceeds their daily usage limit."""
    pass


def get_daily_reset_time() -> datetime:
    """Get the next daily reset time (midnight UTC)."""
    now = datetime.now(UTC)
    next_reset = now.replace(hour=DAILY_LIMIT_RESET_HOUR, minute=0, second=0, microsecond=0)

    # If we've already passed today's reset time, get tomorrow's
    if now >= next_reset:
        next_reset += timedelta(days=1)

    return next_reset


def get_daily_usage(user_id: int) -> float:
    """
    Get the total usage for a user in the current day.

    Returns:
        Total amount spent today (positive number)
    """
    if not TRACK_DAILY_USAGE:
        return 0.0

    try:
        client = get_supabase_client()

        # Get start of current day (UTC midnight)
        now = datetime.now(UTC)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Query credit transactions for today
        result = client.table("credit_transactions").select("amount").eq(
            "user_id", user_id
        ).gte("created_at", start_of_day.isoformat()).lt("amount", 0).execute()

        if not result.data:
            return 0.0

        # Sum up all negative transactions (usage) and convert to positive
        total_usage = sum(abs(txn.get("amount", 0)) for txn in result.data)

        logger.debug(f"User {user_id} daily usage: ${total_usage:.4f}")
        return total_usage

    except Exception as e:
        logger.error(f"Failed to get daily usage for user {user_id}: {e}")
        # Fail open - don't block requests if we can't check usage
        return 0.0


def check_daily_usage_limit(user_id: int, requested_amount: float) -> dict[str, Any]:
    """
    Check if a user can make a request without exceeding daily limit.

    Args:
        user_id: The user's ID
        requested_amount: The cost of the requested operation

    Returns:
        dict with:
            - allowed: bool
            - remaining: float (remaining daily budget)
            - used: float (already used today)
            - limit: float (daily limit)
            - reset_time: datetime (when limit resets)
            - warning_level: str ('ok', 'warning', 'critical', 'exceeded')
    """
    if not ENFORCE_DAILY_LIMITS:
        return {
            "allowed": True,
            "remaining": float('inf'),
            "used": 0.0,
            "limit": float('inf'),
            "reset_time": get_daily_reset_time(),
            "warning_level": "ok",
        }

    try:
        current_usage = get_daily_usage(user_id)
        remaining = DAILY_USAGE_LIMIT - current_usage
        usage_percent = current_usage / DAILY_USAGE_LIMIT if DAILY_USAGE_LIMIT > 0 else 0

        # Determine warning level
        if usage_percent >= 1.0:
            warning_level = "exceeded"
        elif usage_percent >= DAILY_USAGE_CRITICAL_THRESHOLD:
            warning_level = "critical"
        elif usage_percent >= DAILY_USAGE_WARNING_THRESHOLD:
            warning_level = "warning"
        else:
            warning_level = "ok"

        # Check if request would exceed limit
        would_exceed = (current_usage + requested_amount) > DAILY_USAGE_LIMIT

        result = {
            "allowed": not would_exceed,
            "remaining": max(0, remaining),
            "used": current_usage,
            "limit": DAILY_USAGE_LIMIT,
            "reset_time": get_daily_reset_time(),
            "warning_level": warning_level,
        }

        if would_exceed:
            logger.warning(
                f"User {user_id} would exceed daily limit: "
                f"used=${current_usage:.4f}, requested=${requested_amount:.4f}, "
                f"limit=${DAILY_USAGE_LIMIT:.2f}"
            )
        elif warning_level in ("warning", "critical"):
            logger.info(
                f"User {user_id} approaching daily limit: "
                f"used=${current_usage:.4f} ({usage_percent*100:.1f}%), "
                f"limit=${DAILY_USAGE_LIMIT:.2f}"
            )

        return result

    except Exception as e:
        logger.error(f"Error checking daily usage limit for user {user_id}: {e}")
        # Fail open - allow request if we can't check
        return {
            "allowed": True,
            "remaining": DAILY_USAGE_LIMIT,
            "used": 0.0,
            "limit": DAILY_USAGE_LIMIT,
            "reset_time": get_daily_reset_time(),
            "warning_level": "ok",
            "error": str(e),
        }


def enforce_daily_usage_limit(user_id: int, requested_amount: float) -> None:
    """
    Enforce daily usage limit - raises exception if limit would be exceeded.

    Args:
        user_id: The user's ID
        requested_amount: The cost of the requested operation

    Raises:
        DailyUsageLimitExceeded: If the request would exceed the daily limit
    """
    result = check_daily_usage_limit(user_id, requested_amount)

    if not result["allowed"]:
        reset_time = result["reset_time"]
        raise DailyUsageLimitExceeded(
            f"Daily usage limit exceeded. "
            f"Used: ${result['used']:.4f}, "
            f"Limit: ${result['limit']:.2f}. "
            f"Resets at: {reset_time.isoformat()}"
        )
