"""
Usage Limits Configuration
Centralized configuration for daily usage limits.
"""

# Daily Usage Limits
DAILY_USAGE_LIMIT = 1.0  # $1 maximum usage per day for all users
DAILY_LIMIT_RESET_HOUR = 0  # Reset at midnight UTC (hour 0)

# Credit Allocation Rules
MIN_CREDIT_ALLOCATION = 0.0  # Minimum credits that can be allocated
MAX_CREDIT_ALLOCATION_PAID = 10000.0  # Maximum for paid users

# Usage Tracking
TRACK_DAILY_USAGE = True  # Enable daily usage tracking
ENFORCE_DAILY_LIMITS = True  # Enforce daily usage limits

# Legacy trial constants — kept for import compatibility, no longer used
TRIAL_DURATION_DAYS = 0
TRIAL_DAILY_LIMIT = 0.0
TRIAL_CREDITS_AMOUNT = 0.0
MAX_CREDIT_ALLOCATION_TRIAL = 0.0

# Alert Thresholds
DAILY_USAGE_WARNING_THRESHOLD = 0.80  # Warn at 80% of daily limit
DAILY_USAGE_CRITICAL_THRESHOLD = 0.95  # Critical at 95% of daily limit
