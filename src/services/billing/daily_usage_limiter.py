"""
Daily Usage Limiter Service
Tracks and enforces daily usage limits for all users.
"""

import logging
from datetime import UTC, datetime, timedelta
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


class DailyUsageUnavailable(Exception):
    """Raised when today's usage cannot be determined (DB/RPC failure)."""


_PAGE_SIZE = 1000  # PostgREST default max rows per request


def get_daily_usage(user_id: int) -> float:
    """
    Get the total usage for a user in the current UTC day.

    Uses the ``get_daily_usage_total`` RPC (a single SQL aggregate). If the RPC is
    not deployed yet, falls back to a PAGINATED sum -- the old un-paginated select
    silently stopped at PostgREST's 1000-row cap and undercounted heavy users.

    Raises:
        DailyUsageUnavailable: if usage cannot be determined. Callers choose
        their own failure policy (see check_daily_limit_preflight and
        check_daily_usage_limit); this function never reports a false 0.0.
    """
    if not TRACK_DAILY_USAGE:
        return 0.0

    start_of_day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        client = get_supabase_client()
        try:
            res = client.rpc(
                "get_daily_usage_total",
                {"p_user_id": user_id, "p_since": start_of_day.isoformat()},
            ).execute()
            data = res.data
            if isinstance(data, list):
                data = data[0] if data else 0
            if isinstance(data, dict):
                data = next(iter(data.values()), 0)
            return float(data or 0)
        except Exception as rpc_exc:
            logger.info("get_daily_usage_total RPC unavailable (%s); paginating", rpc_exc)

        total = 0.0
        offset = 0
        while True:
            page = (
                client.table("credit_transactions")
                .select("amount")
                .eq("user_id", user_id)
                .eq("transaction_type", "api_usage")
                .gte("created_at", start_of_day.isoformat())
                .lt("amount", 0)
                .order("id")
                .range(offset, offset + _PAGE_SIZE - 1)
                .execute()
            ).data or []
            total += sum(abs(float(t.get("amount") or 0)) for t in page)
            if len(page) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE
        return total
    except Exception as e:
        logger.error(f"Failed to get daily usage for user {user_id}: {e}")
        raise DailyUsageUnavailable(str(e)) from e


def check_daily_limit_preflight(user_id: int) -> None:
    """
    Cheap PRE-inference gate: refuse before spending upstream money.

    Call once per request, before calling the provider (integration point:
    chat_handler / chat routes, right after auth and before the upstream call).

    Raises:
        DailyUsageLimitExceeded: today's usage already >= the daily limit.
        DailyUsageUnavailable: usage lookup failed. POLICY: FAIL CLOSED here.
            Nothing has been served yet, so refusing costs one retry; failing
            open would give free unmetered inference whenever the ledger read
            hiccups. (The post-inference enforce path stays fail-open: a request
            already served must never lose its charge.) Map to HTTP 503.
    Admin-tier users bypass, mirroring deduct_credits.
    """
    if not ENFORCE_DAILY_LIMITS:
        return
    try:
        from src.db.plans import is_admin_tier_user

        if is_admin_tier_user(user_id):
            return
    except Exception as e:  # unknown tier -> treat as normal user
        logger.warning(f"Admin check failed in daily-limit preflight: {e}")

    used = get_daily_usage(user_id)  # may raise DailyUsageUnavailable (fail closed)
    if used >= DAILY_USAGE_LIMIT:
        raise DailyUsageLimitExceeded(
            f"Daily usage limit exceeded. Used: ${used:.4f}, "
            f"Limit: ${DAILY_USAGE_LIMIT:.2f}. "
            f"Resets at: {get_daily_reset_time().isoformat()}"
        )


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
            "remaining": float("inf"),
            "used": 0.0,
            "limit": float("inf"),
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
        # Post-inference path: fail OPEN so a served request is never left uncharged.
        # The pre-inference gate (check_daily_limit_preflight) fails closed.
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
