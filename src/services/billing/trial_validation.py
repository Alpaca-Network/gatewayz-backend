"""
Trial Validation — DISABLED

Trial system has been removed. All users must purchase credits.
These functions are kept as no-ops to avoid breaking callers.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def clear_trial_cache(api_key: str | None = None) -> None:
    pass


def get_trial_cache_stats() -> dict[str, Any]:
    return {"cached_trials": 0, "ttl_seconds": 0}


def invalidate_trial_cache(api_key: str) -> None:
    pass


def validate_trial_access(api_key: str) -> dict[str, Any]:
    """Always returns non-trial, valid access. Trial system is disabled."""
    return {"is_valid": True, "is_trial": False, "message": "Paid access only"}


def _validate_trial_access_uncached(api_key: str, retry_count: int = 0) -> dict[str, Any]:
    return validate_trial_access(api_key)


def track_trial_usage(
    api_key: str,
    tokens_used: int,
    requests_used: int = 1,
    model_id: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> bool:
    """No-op — trial usage tracking is disabled."""
    return False
