"""
Usage Limits Configuration
Centralized configuration for daily usage limits.
"""

import os

# Daily Usage Limits
DAILY_USAGE_LIMIT = 1.0  # $1 maximum usage per day for all users
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
