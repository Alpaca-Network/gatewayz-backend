#!/usr/bin/env python3
"""
Anonymous Rate Limiter Service

Provides rate limiting and model restrictions for anonymous (unauthenticated) users.
This prevents abuse while allowing limited free access for demos/evaluations.

Key Features:
- IP-based rate limiting (3 requests per day per IP)
- Model whitelist (only free models allowed)
- Redis-backed for distributed deployments
- Fallback to in-memory when Redis unavailable

Security Design:
- Anonymous users can ONLY use models ending with :free
- Maximum 3 requests per day per IP address
- No credit deduction (free models have $0 cost)
- IP fingerprinting to prevent simple bypasses
"""

import hashlib
import logging
import time
from datetime import datetime, UTC
from typing import Any

logger = logging.getLogger(__name__)

# Configuration
ANONYMOUS_DAILY_LIMIT = 3  # Maximum requests per day per IP
ANONYMOUS_ALLOWED_MODELS = [
    # Verified free models from OpenRouter (end with :free)
    "google/gemini-2.0-flash-exp:free",
    "google/gemma-2-9b-it:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "meta-llama/llama-3.1-8b-instruct:free",
    "mistralai/mistral-7b-instruct:free",
    "huggingfaceh4/zephyr-7b-beta:free",
    "openchat/openchat-7b:free",
    "nousresearch/nous-hermes-llama2-13b:free",
    "arcee-ai/trinity-mini:free",
]

# In-memory fallback store (used when Redis unavailable)
_anonymous_usage_cache: dict[str, dict[str, Any]] = {}
_cache_cleanup_interval = 3600  # Clean up every hour
_last_cleanup = time.time()


def _get_redis_client():
    """Get Redis client if available."""
    try:
        from src.config.redis_config import get_redis_client
        return get_redis_client()
    except Exception as e:
        logger.debug(f"Redis not available for anonymous rate limiting: {e}")
        return None


def _hash_ip(ip_address: str) -> str:
    """Hash IP address for privacy-preserving storage."""
    return hashlib.sha256(f"anon_rate:{ip_address}".encode()).hexdigest()[:32]


def _get_today_key() -> str:
    """Get today's date key for rate limiting."""
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _cleanup_memory_cache():
    """Clean up expired entries from in-memory cache."""
    global _last_cleanup, _anonymous_usage_cache

    now = time.time()
    if now - _last_cleanup < _cache_cleanup_interval:
        return

    today = _get_today_key()
    # Remove entries from previous days
    expired_keys = [
        key for key, data in _anonymous_usage_cache.items()
        if data.get("date") != today
    ]
    for key in expired_keys:
        del _anonymous_usage_cache[key]

    _last_cleanup = now
    if expired_keys:
        logger.info(f"Cleaned up {len(expired_keys)} expired anonymous rate limit entries")


def is_model_allowed_for_anonymous(model_id: str) -> bool:
    """
    Check if a model is allowed for anonymous users.

    Only models explicitly whitelisted AND ending with :free are allowed.
    This prevents anonymous users from accessing expensive models.

    Args:
        model_id: The model identifier

    Returns:
        True if model is allowed for anonymous access
    """
    if not model_id:
        return False

    # Must end with :free suffix
    if not model_id.endswith(":free"):
        return False

    # Must be in whitelist
    return model_id.lower() in [m.lower() for m in ANONYMOUS_ALLOWED_MODELS]


def get_anonymous_usage_count(ip_address: str) -> int:
    """
    Get the current usage count for an anonymous IP address.

    Args:
        ip_address: The client IP address

    Returns:
        Number of requests made today
    """
    ip_hash = _hash_ip(ip_address)
    today = _get_today_key()
    redis_key = f"anon_limit:{ip_hash}:{today}"

    # Try Redis first
    redis = _get_redis_client()
    if redis:
        try:
            count = redis.get(redis_key)
            return int(count) if count else 0
        except Exception as e:
            logger.warning(f"Redis error getting anonymous count: {e}")

    # Fallback to memory
    _cleanup_memory_cache()
    entry = _anonymous_usage_cache.get(ip_hash, {})
    if entry.get("date") == today:
        return entry.get("count", 0)
    return 0


def increment_anonymous_usage(ip_address: str) -> int:
    """
    Increment the usage count for an anonymous IP address.

    Args:
        ip_address: The client IP address

    Returns:
        New usage count after increment
    """
    ip_hash = _hash_ip(ip_address)
    today = _get_today_key()
    redis_key = f"anon_limit:{ip_hash}:{today}"

    # Try Redis first
    redis = _get_redis_client()
    if redis:
        try:
            # Use INCR with TTL (expire at end of day)
            pipe = redis.pipeline()
            pipe.incr(redis_key)
            pipe.expire(redis_key, 86400)  # 24 hours TTL
            results = pipe.execute()
            return results[0]
        except Exception as e:
            logger.warning(f"Redis error incrementing anonymous count: {e}")

    # Fallback to memory
    _cleanup_memory_cache()
    if ip_hash not in _anonymous_usage_cache or _anonymous_usage_cache[ip_hash].get("date") != today:
        _anonymous_usage_cache[ip_hash] = {"date": today, "count": 0}

    _anonymous_usage_cache[ip_hash]["count"] += 1
    return _anonymous_usage_cache[ip_hash]["count"]


def check_anonymous_rate_limit(ip_address: str) -> dict[str, Any]:
    """
    Check if an anonymous request should be allowed.

    Args:
        ip_address: The client IP address

    Returns:
        Dict with:
            - allowed: bool - whether request is allowed
            - remaining: int - remaining requests today
            - limit: int - daily limit
            - reason: str - if not allowed, the reason
    """
    current_count = get_anonymous_usage_count(ip_address)
    remaining = max(0, ANONYMOUS_DAILY_LIMIT - current_count)

    if current_count >= ANONYMOUS_DAILY_LIMIT:
        return {
            "allowed": False,
            "remaining": 0,
            "limit": ANONYMOUS_DAILY_LIMIT,
            "reason": f"Anonymous daily limit exceeded ({ANONYMOUS_DAILY_LIMIT} requests/day). Please sign up for an account to continue."
        }

    return {
        "allowed": True,
        "remaining": remaining,
        "limit": ANONYMOUS_DAILY_LIMIT,
        "reason": None
    }


def validate_anonymous_request(ip_address: str, model_id: str) -> dict[str, Any]:
    """
    Full validation for an anonymous request.

    Checks both:
    1. Model is allowed for anonymous users
    2. Rate limit not exceeded

    Args:
        ip_address: The client IP address
        model_id: The requested model

    Returns:
        Dict with:
            - allowed: bool
            - reason: str if not allowed
            - model_allowed: bool
            - rate_limit_allowed: bool
            - remaining_requests: int
    """
    # Check model whitelist first (fast fail)
    model_allowed = is_model_allowed_for_anonymous(model_id)
    if not model_allowed:
        allowed_models_str = ", ".join(ANONYMOUS_ALLOWED_MODELS[:3]) + "..."
        return {
            "allowed": False,
            "reason": f"Model '{model_id}' is not available for anonymous users. Anonymous access is limited to free models: {allowed_models_str}. Please sign up for an account to access this model.",
            "model_allowed": False,
            "rate_limit_allowed": True,  # Didn't check, but irrelevant
            "remaining_requests": ANONYMOUS_DAILY_LIMIT
        }

    # Check rate limit
    rate_check = check_anonymous_rate_limit(ip_address)
    if not rate_check["allowed"]:
        return {
            "allowed": False,
            "reason": rate_check["reason"],
            "model_allowed": True,
            "rate_limit_allowed": False,
            "remaining_requests": 0
        }

    return {
        "allowed": True,
        "reason": None,
        "model_allowed": True,
        "rate_limit_allowed": True,
        "remaining_requests": rate_check["remaining"]
    }


def record_anonymous_request(ip_address: str, model_id: str) -> dict[str, Any]:
    """
    Record a successful anonymous request.

    Call this AFTER the request completes successfully.

    Args:
        ip_address: The client IP address
        model_id: The model that was used

    Returns:
        Dict with remaining requests info
    """
    new_count = increment_anonymous_usage(ip_address)
    remaining = max(0, ANONYMOUS_DAILY_LIMIT - new_count)

    logger.info(
        f"Anonymous request recorded: ip_hash={_hash_ip(ip_address)[:8]}..., "
        f"model={model_id}, count={new_count}/{ANONYMOUS_DAILY_LIMIT}"
    )

    return {
        "count": new_count,
        "remaining": remaining,
        "limit": ANONYMOUS_DAILY_LIMIT
    }


def get_anonymous_stats() -> dict[str, Any]:
    """Get statistics about anonymous usage (for monitoring)."""
    redis = _get_redis_client()

    if redis:
        try:
            today = _get_today_key()
            pattern = f"anon_limit:*:{today}"
            keys = list(redis.scan_iter(pattern))
            total_requests = 0
            for key in keys:
                count = redis.get(key)
                if count:
                    total_requests += int(count)

            return {
                "unique_ips_today": len(keys),
                "total_requests_today": total_requests,
                "storage": "redis"
            }
        except Exception as e:
            logger.warning(f"Error getting Redis stats: {e}")

    # Memory fallback stats
    today = _get_today_key()
    today_entries = {
        k: v for k, v in _anonymous_usage_cache.items()
        if v.get("date") == today
    }
    total = sum(v.get("count", 0) for v in today_entries.values())

    return {
        "unique_ips_today": len(today_entries),
        "total_requests_today": total,
        "storage": "memory"
    }
