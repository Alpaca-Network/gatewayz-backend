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
- Anonymous users can ONLY use models known to be free (DB is_free flag)
- Maximum 3 requests per day per IP address
- No credit deduction (free models have $0 cost)
- IP fingerprinting to prevent simple bypasses
"""

import hashlib
import ipaddress
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Configuration
SIGNUP_URL = "https://www.gatewayz.ai/start"

# Keyless trial: models a visitor may call WITHOUT a key so they can try the API.
# Deliberately separate from the DB ``is_free`` flag — that flag also waives the
# credit charge for keyed users (routes/chat.py), so marking a paid model free
# there would make it free and uncapped for every customer. These models stay
# billable for keyed users; only anonymous callers get them, at
# ANONYMOUS_DAILY_LIMIT requests/day/IP and ANONYMOUS_MAX_OUTPUT_TOKENS each.
# Override with ANONYMOUS_TRIAL_MODELS="id1,id2" (empty string disables).
_DEFAULT_TRIAL_MODELS = "groq/openai/gpt-oss-20b"
ANONYMOUS_TRIAL_MODELS: tuple[str, ...] = tuple(
    m.strip()
    for m in os.environ.get("ANONYMOUS_TRIAL_MODELS", _DEFAULT_TRIAL_MODELS).split(",")
    if m.strip()
)
# gpt-oss spends part of the output budget on reasoning; 1024 leaves room for an answer.
ANONYMOUS_MAX_OUTPUT_TOKENS = 1024
# Keyless prompts are a trial, not a free bulk API: reject large inputs.
ANONYMOUS_MAX_INPUT_TOKENS = 4000
# Ceiling on ALL keyless requests per day, whatever the IP: rotating IPs or an
# IPv6 block must not turn the trial into unlimited free inference.
ANONYMOUS_GLOBAL_DAILY_LIMIT = int(os.environ.get("ANONYMOUS_GLOBAL_DAILY_LIMIT", "2000"))
_GLOBAL_BUCKET = "__all_anonymous__"
ANONYMOUS_DAILY_LIMIT = 3  # Maximum requests per day per IP
# Hardcoded fallback free model list — used only when DB cache is unavailable.
# The authoritative source is the models table (is_free=true column).
# New free models should be added to the DB, not to this list.
ANONYMOUS_ALLOWED_MODELS = [
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


def _bucket_ip(ip_address: str) -> str:
    """One bucket per IPv4 address, one per IPv6 /64.

    A single IPv6 customer usually holds a whole /64 (2^64 addresses), so keying
    on the exact address let one machine rotate through unlimited buckets.
    """
    try:
        addr = ipaddress.ip_address(ip_address.strip())
    except (ValueError, AttributeError):
        return ip_address
    if addr.version == 6:
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False).network_address) + "/64"
    return str(addr)


def _hash_ip(ip_address: str) -> str:
    """Hash IP address for privacy-preserving storage."""
    return hashlib.sha256(f"anon_rate:{_bucket_ip(ip_address)}".encode()).hexdigest()[:32]


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
        key for key, data in _anonymous_usage_cache.items() if data.get("date") != today
    ]
    for key in expired_keys:
        del _anonymous_usage_cache[key]

    _last_cleanup = now
    if expired_keys:
        logger.info(f"Cleaned up {len(expired_keys)} expired anonymous rate limit entries")


def get_anonymous_allowed_models_sample(n: int = 5) -> list[str]:
    """A sample of models anonymous users can actually call, for error messages.

    Only DB-verified free models on an enabled provider. Never the hardcoded
    ANONYMOUS_ALLOWED_MODELS fallback: those are OpenRouter ``:free`` ids, and
    naming them sent every keyless first try into an upstream credential error.
    Returns [] when nothing free is servable — the caller then says so.
    """
    try:
        from src.services.model_capabilities_cache import get_servable_free_models

        free = sorted(get_servable_free_models())
    except Exception:
        free = []
    trial = [m for m in ANONYMOUS_TRIAL_MODELS if m.lower() not in {f.lower() for f in free}]
    return (trial + free)[:n]


def is_anonymous_trial_model(model_id: str) -> bool:
    """True if model_id is on the keyless trial list (case-insensitive)."""
    return bool(model_id) and model_id.lower() in {m.lower() for m in ANONYMOUS_TRIAL_MODELS}


def drop_null_message_fields(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove null-valued optional fields from serialized chat messages.

    Anonymous requests skip the OpenAIChatAdapter that normalizes messages for
    keyed requests, so ``model_dump()`` output reached the provider with
    ``"name": None``; Groq rejects that ("messages.0.name: Value is not
    nullable"), which broke every keyless trial call. ``content`` is kept even
    when null: an assistant tool-call message legitimately carries it.
    """
    cleaned: list[dict[str, Any]] = []
    for m in messages:
        if isinstance(m, dict):
            m = {k: v for k, v in m.items() if v is not None or k == "content"}
        cleaned.append(m)
    return cleaned


def harden_anonymous_request(req: Any) -> None:
    """Strip request options a keyless caller may not use (mutates ``req``).

    Output is capped; one completion only; no tools (Groq's built-in tools such
    as browser_search are billed separately); no provider override (it builds a
    failover chain to other vendors); no web search (a paid vendor call that
    would also forward the visitor's prompt).
    """
    req.max_tokens = clamp_anonymous_max_tokens(getattr(req, "max_tokens", None))
    for field, value in (
        ("n", 1),
        ("tools", None),
        ("tool_choice", None),
        ("provider", None),
        ("auto_web_search", False),
    ):
        if hasattr(req, field):
            setattr(req, field, value)


_ip_diag_logged = 0


def log_client_ip_headers(request: Any, resolved_ip: str) -> None:
    """Log the forwarding headers of the first few anonymous requests per process.

    Diagnostic for choosing TRUSTED_CLIENT_IP_HEADER: on 2026-11-12 the
    rightmost X-Forwarded-For entry turned out to be a Railway edge address
    (152.233.47.x), so every keyless visitor on that edge shared one quota.
    """
    global _ip_diag_logged
    if _ip_diag_logged >= 20 or request is None:
        return
    _ip_diag_logged += 1
    try:
        headers = request.headers
        logger.info(
            "anon-ip-diag resolved=%s x-real-ip=%s x-forwarded-for=%s peer=%s",
            resolved_ip,
            headers.get("x-real-ip"),
            headers.get("x-forwarded-for"),
            request.client.host if getattr(request, "client", None) else None,
        )
    except Exception:
        pass


def clamp_anonymous_max_tokens(requested: int | None) -> int:
    """Cap an anonymous request's output budget at ANONYMOUS_MAX_OUTPUT_TOKENS."""
    if requested is None or requested <= 0:
        return ANONYMOUS_MAX_OUTPUT_TOKENS
    return min(requested, ANONYMOUS_MAX_OUTPUT_TOKENS)


def is_model_allowed_for_anonymous(model_id: str) -> bool:
    """
    Check if a model is allowed for anonymous users.

    Only models known to be free (DB is_free flag, or the hardcoded fallback
    list when the DB cache is unavailable) are allowed. This prevents
    anonymous users from accessing paid models.

    A ``:free`` suffix used to be a hard prerequisite here, but the catalog's
    actual free models aren't guaranteed to carry that suffix (production's
    current free set — e.g. "unsloth/gemma-2-9b-it" — doesn't), which made
    this reject every real free model and leave anonymous access fully
    unusable. is_free_model() is the source of truth; it already only
    matches models genuinely known to be free.

    Args:
        model_id: The model identifier

    Returns:
        True if model is allowed for anonymous access
    """
    if not model_id:
        return False

    if is_anonymous_trial_model(model_id):
        return True

    # Check DB-backed free model list first, then fall back to hardcoded whitelist
    try:
        from src.services.model_capabilities_cache import is_free_model

        return is_free_model(model_id)
    except Exception:
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
    if (
        ip_hash not in _anonymous_usage_cache
        or _anonymous_usage_cache[ip_hash].get("date") != today
    ):
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
            "reason": f"Anonymous daily limit exceeded ({ANONYMOUS_DAILY_LIMIT} requests/day). Please sign up for an account to continue.",
        }

    return {"allowed": True, "remaining": remaining, "limit": ANONYMOUS_DAILY_LIMIT, "reason": None}


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
        sample = get_anonymous_allowed_models_sample(3)
        if sample:
            reason = (
                f"Model '{model_id}' needs an API key. Without a key you can try "
                f"{', '.join(sample)} ({ANONYMOUS_DAILY_LIMIT} requests/day). Create a key at "
                f"{SIGNUP_URL} to use any model in the catalog."
            )
        else:
            reason = (
                f"Model '{model_id}' needs an API key. No models are open to anonymous use "
                f"right now. Create a key at {SIGNUP_URL}; the catalog is at "
                f"https://api.gatewayz.ai/v1/models."
            )
        return {
            "allowed": False,
            "reason": reason,
            "model_allowed": False,
            "rate_limit_allowed": True,  # Didn't check, but irrelevant
            "remaining_requests": ANONYMOUS_DAILY_LIMIT,
        }

    # Check rate limit
    rate_check = check_anonymous_rate_limit(ip_address)
    if not rate_check["allowed"]:
        return {
            "allowed": False,
            "reason": rate_check["reason"],
            "model_allowed": True,
            "rate_limit_allowed": False,
            "remaining_requests": 0,
        }

    return {
        "allowed": True,
        "reason": None,
        "model_allowed": True,
        "rate_limit_allowed": True,
        "remaining_requests": rate_check["remaining"],
    }


def reserve_anonymous_request(ip_address: str, model_id: str = "") -> dict[str, Any]:
    """Atomically reserve one anonymous request BEFORE inference.

    INCR first, then compare: concurrent requests each get a distinct count, so
    N parallel requests cannot all pass a stale read, and a client that cancels
    mid-stream has already been counted. Reservations are not refunded.

    Returns dict with ``allowed``, ``remaining``, ``limit``, ``count``, ``reason``.
    """
    global_count = increment_anonymous_usage(_GLOBAL_BUCKET)
    if global_count > ANONYMOUS_GLOBAL_DAILY_LIMIT:
        logger.warning("Anonymous global daily cap reached (%d)", ANONYMOUS_GLOBAL_DAILY_LIMIT)
        return {
            "allowed": False,
            "remaining": 0,
            "limit": ANONYMOUS_DAILY_LIMIT,
            "count": global_count,
            "reason": (
                "The keyless trial is at capacity for today. Create a key at "
                f"{SIGNUP_URL} to keep going."
            ),
        }
    new_count = increment_anonymous_usage(ip_address)
    if new_count > ANONYMOUS_DAILY_LIMIT:
        return {
            "allowed": False,
            "remaining": 0,
            "limit": ANONYMOUS_DAILY_LIMIT,
            "count": new_count,
            "reason": f"Anonymous daily limit exceeded ({ANONYMOUS_DAILY_LIMIT} requests/day). Please sign up for an account to continue.",
        }
    return {
        "allowed": True,
        "remaining": max(0, ANONYMOUS_DAILY_LIMIT - new_count),
        "limit": ANONYMOUS_DAILY_LIMIT,
        "count": new_count,
        "reason": None,
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

    return {"count": new_count, "remaining": remaining, "limit": ANONYMOUS_DAILY_LIMIT}


def get_anonymous_rate_limit_headers(limit: int, remaining: int) -> dict[str, str]:
    """
    Build standard rate limit headers for anonymous 429 responses.

    Returns both IETF draft standard (RateLimit-*) and legacy (X-RateLimit-*)
    headers, consistent with Layer 1 middleware headers.

    Args:
        limit: The daily request limit
        remaining: Remaining requests (usually 0 when blocking)

    Returns:
        Dict of HTTP header name → value
    """
    now = datetime.now(UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    next_midnight = midnight + timedelta(days=1)
    seconds_until_reset = int((next_midnight - now).total_seconds())

    return {
        "RateLimit-Limit": str(limit),
        "RateLimit-Remaining": str(remaining),
        "RateLimit-Reset": str(seconds_until_reset),
        "Retry-After": str(seconds_until_reset),
        "X-RateLimit-Limit-Requests": str(limit),
        "X-RateLimit-Remaining-Requests": str(remaining),
        "X-RateLimit-Reset-Requests": str(int(next_midnight.timestamp())),
        "X-RateLimit-Reason": "anonymous_daily_limit",
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
                "storage": "redis",
            }
        except Exception as e:
            logger.warning(f"Error getting Redis stats: {e}")

    # Memory fallback stats
    today = _get_today_key()
    today_entries = {k: v for k, v in _anonymous_usage_cache.items() if v.get("date") == today}
    total = sum(v.get("count", 0) for v in today_entries.values())

    return {
        "unique_ips_today": len(today_entries),
        "total_requests_today": total,
        "storage": "memory",
    }
