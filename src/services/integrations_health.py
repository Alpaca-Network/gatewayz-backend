"""Health checks for every external integration the backend depends on
(gatewayz-backend Phase A, A4).

``check_all()`` is the one function ``GET /admin/status`` calls to answer
"is email/Privy/the chain RPC/the database/redis/Stripe/Sentry working right
now". Each integration's check is independent and wrapped in its own
try/except -- mirroring the sub-block degradation contract already used by
``GET /admin/wayz/status`` (see ``src/routes/admin_wayz.py``): one broken
check must never take the others down with it, and this function itself
never raises.

Results are cached in-process for ``_CACHE_TTL_SECONDS`` so a status page
that gets polled every few seconds doesn't hammer Resend/Supabase/the chain
RPC on every request.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Literal

from src.config.config import Config

logger = logging.getLogger(__name__)

Status = Literal["ok", "degraded", "down", "not_configured"]

_CACHE_TTL_SECONDS = 30
_DEFAULT_TIMEOUT_SECONDS = 3

# {name: check_result} plus the monotonic time it was computed.
_cache: dict[str, Any] | None = None
_cache_computed_at: float = 0.0


def _result(
    status: Status, latency_ms: int | None = None, detail: str | None = None
) -> dict[str, Any]:
    return {"status": status, "latency_ms": latency_ms, "detail": detail}


def _check_resend(timeout_s: float) -> dict[str, Any]:
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        return _result("not_configured")

    start = time.monotonic()
    try:
        import httpx

        response = httpx.get(
            "https://api.resend.com/domains",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
        )
    except Exception as e:
        logger.info(f"integrations_health: resend check failed: {e}")
        return _result("down", detail=type(e).__name__)

    latency_ms = int((time.monotonic() - start) * 1000)
    if response.status_code == 200:
        return _result("ok", latency_ms)
    if response.status_code in (401, 403):
        return _result("degraded", latency_ms, "suspended_or_invalid_key")
    return _result("down", latency_ms, f"http_{response.status_code}")


def _check_privy() -> dict[str, Any]:
    if not Config.PRIVY_APP_ID or not Config.PRIVY_VERIFICATION_KEY:
        return _result("not_configured")

    from src.security.privy_token import _normalize_pem, privy_verification_mode

    start = time.monotonic()
    try:
        from cryptography.hazmat.primitives import serialization

        serialization.load_pem_public_key(_normalize_pem(Config.PRIVY_VERIFICATION_KEY).encode())
    except Exception as e:
        logger.info(f"integrations_health: privy key parse failed: {e}")
        return _result("down", int((time.monotonic() - start) * 1000), type(e).__name__)

    latency_ms = int((time.monotonic() - start) * 1000)
    return _result("ok", latency_ms, privy_verification_mode())


def _check_fuji_rpc(timeout_s: float) -> dict[str, Any]:
    if not Config.WAYZ_STAKING_CONTRACT_ADDRESS:
        return _result("not_configured")

    start = time.monotonic()
    try:
        from web3 import Web3

        w3 = Web3(
            Web3.HTTPProvider(
                Config.AVALANCHE_FUJI_RPC_URL,
                request_kwargs={"timeout": timeout_s},
            )
        )
        block_number = w3.eth.block_number
    except Exception as e:
        logger.info(f"integrations_health: fuji_rpc check failed: {e}")
        return _result("down", int((time.monotonic() - start) * 1000), type(e).__name__)

    latency_ms = int((time.monotonic() - start) * 1000)
    return _result("ok", latency_ms, f"block={block_number}")


def _check_supabase(timeout_s: float) -> dict[str, Any]:
    if not Config.SUPABASE_URL or not Config.SUPABASE_KEY:
        return _result("not_configured")

    start = time.monotonic()
    try:
        import src.config.supabase_config as supabase_config

        client = supabase_config.get_supabase_client()
        client.table("users").select("id").limit(1).execute()
    except Exception as e:
        logger.info(f"integrations_health: supabase check failed: {e}")
        return _result("down", int((time.monotonic() - start) * 1000), type(e).__name__)

    latency_ms = int((time.monotonic() - start) * 1000)
    return _result("ok", latency_ms)


def _check_redis(timeout_s: float) -> dict[str, Any]:
    if not Config.REDIS_ENABLED:
        return _result("not_configured")

    start = time.monotonic()
    try:
        from src.config.redis_config import get_redis_client

        client = get_redis_client()
        if client is None:
            return _result("down", detail="unavailable")
        client.ping()
    except Exception as e:
        logger.info(f"integrations_health: redis check failed: {e}")
        return _result("down", int((time.monotonic() - start) * 1000), type(e).__name__)

    latency_ms = int((time.monotonic() - start) * 1000)
    return _result("ok", latency_ms)


def _check_stripe() -> dict[str, Any]:
    api_key = os.environ.get("STRIPE_SECRET_KEY")
    if not api_key:
        return _result("not_configured")
    # A live balance check is deliberately skipped here -- presence of the
    # key is enough for the status page, and avoids an extra outbound call
    # on every poll for an integration that isn't in the critical path.
    return _result("ok", detail="configured")


def _check_sentry() -> dict[str, Any]:
    return _result("ok" if Config.SENTRY_DSN else "not_configured")


def check_all(timeout_s: float = _DEFAULT_TIMEOUT_SECONDS) -> dict[str, dict[str, Any]]:
    """Return ``{name: {status, latency_ms, detail}}`` for every integration.

    Cached in-process for 30s. Never raises -- every individual check is
    already wrapped in its own try/except, but this is one more layer of
    protection so a bug in a single check can't take the whole status
    endpoint down.
    """
    global _cache, _cache_computed_at

    now = time.monotonic()
    if _cache is not None and (now - _cache_computed_at) < _CACHE_TTL_SECONDS:
        return _cache

    checks: dict[str, Any] = {}
    for name, fn in (
        ("resend", lambda: _check_resend(timeout_s)),
        ("privy", _check_privy),
        ("fuji_rpc", lambda: _check_fuji_rpc(timeout_s)),
        ("supabase", lambda: _check_supabase(timeout_s)),
        ("redis", lambda: _check_redis(timeout_s)),
        ("stripe", _check_stripe),
        ("sentry", _check_sentry),
    ):
        try:
            checks[name] = fn()
        except Exception as e:
            logger.warning(f"integrations_health: '{name}' check raised unexpectedly: {e}")
            checks[name] = _result("down", detail=type(e).__name__)

    _cache = checks
    _cache_computed_at = now
    return checks
