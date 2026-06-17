"""Shared per-API-key rate-limit enforcement for cost-sensitive endpoints.

`routes/chat.py` already enforces a per-API-key sliding-window limit inline.
Other expensive endpoints — image generation, audio transcription and payment
creation — historically had no per-key limit and were protected only by the
coarse IP-based middleware. This helper applies the same per-key guard so those
endpoints share one consistent, tested code path.

Failure semantics intentionally mirror ``routes/chat.py``: if rate limiting is
disabled, no api_key is available, or the manager is unavailable / errors, the
request is allowed (fail-open). Whether the underlying Redis primitive fails
open or closed during an outage is governed separately by
``RATE_LIMIT_FAIL_CLOSED`` (see ``services/rate_limiting.py``).
"""

import logging
import os
from typing import Any

from fastapi import HTTPException, Request

from src.services.rate_limiting import get_rate_limit_manager
from src.utils.rate_limit_headers import get_rate_limit_headers

logger = logging.getLogger(__name__)


def _rate_limiting_disabled(request: Request | None) -> bool:
    """Mirror the bypass conditions used in routes/chat.py."""
    if os.getenv("DISABLE_RATE_LIMITING", "false").lower() == "true":
        return True
    # Internal live-test calls are validated by the security middleware, which
    # sets request.state.is_live_test after checking X-Internal-Source + ADMIN_API_KEY.
    state = getattr(request, "state", None) if request is not None else None
    return bool(getattr(state, "is_live_test", False))


async def enforce_request_rate_limit(
    api_key: str | None,
    *,
    request: Request | None = None,
    tokens_used: int = 0,
) -> Any:
    """Enforce the per-API-key request rate limit.

    Raises ``HTTPException(429)`` (with standard RateLimit-* headers and
    Retry-After) when the key has exceeded its limit. Returns the
    ``RateLimitResult`` when allowed, or ``None`` when the check is skipped.
    """
    if not api_key or _rate_limiting_disabled(request):
        return None

    manager = get_rate_limit_manager()
    if manager is None:
        return None

    try:
        result = await manager.check_rate_limit(api_key, tokens_used=tokens_used)
    except Exception as exc:  # pragma: no cover - defensive; manager already fails open
        logger.warning("Rate limit check errored; allowing request (fail-open): %s", exc)
        return None

    if not result.allowed:
        headers = get_rate_limit_headers(result)
        retry_after = getattr(result, "retry_after", None)
        if retry_after:
            headers["Retry-After"] = str(retry_after)
        raise HTTPException(
            status_code=429,
            detail=result.reason or "Rate limit exceeded. Please slow down and try again.",
            headers=headers,
        )

    return result
