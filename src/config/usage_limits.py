"""
Usage Limits Configuration
Centralized configuration for daily usage limits.

Daily cap policy (OFF by default -- default behaviour is unchanged):

* ``ENFORCE_DAILY_LIMITS`` (bool, default false): master switch. Nothing below
  has any effect unless this is true.
* ``DAILY_USAGE_LIMIT_USD`` (float, default 1.0): the per-user daily cap in USD.
  Non-numeric or non-positive values fall back to 1.0.
* ``DAILY_LIMIT_APPLIES_TO`` (``free_only`` | ``all``, default ``free_only``):
  ``free_only`` caps only users with ``purchased_credits <= 0`` and no active
  paid subscription, so paying customers are never blocked; ``all`` caps every
  non-admin user (the legacy behaviour). Unknown values fall back to
  ``free_only``.

Read by ``src/services/billing/daily_usage_limiter.py``
(``check_daily_limit_preflight`` and ``enforce_daily_usage_limit``).
"""

import os


def _float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, "").strip())
    except ValueError:
        return default
    return value if value > 0 else default


# Daily Usage Limits
DAILY_USAGE_LIMIT = _float_env("DAILY_USAGE_LIMIT_USD", 1.0)  # USD per user per day
DAILY_LIMIT_APPLIES_TO = (
    os.getenv("DAILY_LIMIT_APPLIES_TO", "free_only").strip().lower()
    if os.getenv("DAILY_LIMIT_APPLIES_TO", "free_only").strip().lower() in ("free_only", "all")
    else "free_only"
)
DAILY_LIMIT_RESET_HOUR = 0  # Reset at midnight UTC (hour 0)

# Credit Allocation Rules
MIN_CREDIT_ALLOCATION = 0.0  # Minimum credits that can be allocated
MAX_CREDIT_ALLOCATION_PAID = 10000.0  # Maximum for paid users

# Usage Tracking
TRACK_DAILY_USAGE = True  # Enable daily usage tracking
# The $1/day cap is a legacy trial-era constant that applies to ALL users, paying
# customers included. Now that the ledger records real (sub-cent) amounts the cap
# would become effective, so it is opt-in: set ENFORCE_DAILY_LIMITS=true to enable.
ENFORCE_DAILY_LIMITS = os.getenv("ENFORCE_DAILY_LIMITS", "false").strip().lower() in (
    "1",
    "true",
    "yes",
)

# Legacy trial constants — kept for import compatibility, no longer used
TRIAL_DURATION_DAYS = 0
TRIAL_DAILY_LIMIT = 0.0
TRIAL_CREDITS_AMOUNT = 0.0
MAX_CREDIT_ALLOCATION_TRIAL = 0.0

# Alert Thresholds
DAILY_USAGE_WARNING_THRESHOLD = 0.80  # Warn at 80% of daily limit
DAILY_USAGE_CRITICAL_THRESHOLD = 0.95  # Critical at 95% of daily limit
