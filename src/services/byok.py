"""BYOK routing/billing logic — Phase 5 of the direct-supply pivot.

Helpers the inference paths use:
  * ``byok_enabled`` — deployment flag gating all BYOK work (zero hot-path cost off).
  * ``resolve_byok_key`` — a customer's decrypted own key for a provider, or None.
  * ``resolve_provider_key`` — prefer a customer's own key, else the platform key.
  * ``byok_routing_fee`` — the fee charged (instead of credit debit) when a
    request is served on a customer's key.

Key injection uses a provider-scoped context variable (``byok_context``): the
handler sets it around a provider call, and the central key-resolution points
(``gateway_registry.get_provider_api_key`` and the OpenRouter pooled client) read
it via ``get_byok_key_for`` and substitute the customer's key. This avoids
threading an ``api_key`` argument through every provider client. The context is
copied into ``asyncio.to_thread`` workers automatically, so sync provider clients
see it too.

See docs/BUSINESS_PIVOT_DIRECT_SUPPLY.md Phase 5.
"""

import contextvars
import logging
import os

logger = logging.getLogger(__name__)

# (provider_slug, api_key) currently in effect for the running request, or None.
_byok_context: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "byok_context", default=None
)


def byok_enabled() -> bool:
    """True if BYOK is enabled for this deployment (env BYOK_ENABLED, default off).

    Read via Config so .env files and test overrides apply; falls back to the raw
    env var if Config is unavailable.
    """
    try:
        from src.config import Config

        return bool(getattr(Config, "BYOK_ENABLED", False))
    except Exception:
        return os.getenv("BYOK_ENABLED", "false").strip().lower() in ("1", "true", "yes")


def set_byok_context(provider_slug: str, api_key: str):
    """Bind *api_key* as the BYOK key for *provider_slug* for the current context.

    Returns a token to pass to :func:`reset_byok_context` in a ``finally`` block.
    """
    return _byok_context.set((provider_slug, api_key))


def reset_byok_context(token) -> None:
    """Undo a prior :func:`set_byok_context` using its token."""
    try:
        _byok_context.reset(token)
    except (ValueError, LookupError):
        # Token from a different context (e.g. reset in a worker thread). Clearing
        # is best-effort; a stale value cannot leak across requests because the
        # context is per-request.
        _byok_context.set(None)


def get_byok_key_for(provider_slug: str) -> str | None:
    """Return the BYOK key bound for *provider_slug* in the current context, else None.

    Provider-scoped so a key bound for one provider is never used for another
    (e.g. during failover to a different upstream).
    """
    ctx = _byok_context.get()
    if ctx and ctx[0] == provider_slug:
        return ctx[1]
    return None


def resolve_byok_key(user_id: int | None, provider_slug: str) -> str | None:
    """Return the customer's decrypted own key for *provider_slug*, or None.

    Never raises — a lookup/decryption failure logs and returns None so inference
    falls back to the platform key.
    """
    if user_id is None:
        return None
    try:
        from src.db.user_provider_keys import get_decrypted_provider_key

        return get_decrypted_provider_key(user_id, provider_slug) or None
    except Exception as e:  # never let BYOK lookup break inference
        logger.warning("BYOK resolve failed for %s: %s", provider_slug, e)
        return None


def resolve_provider_key(user_id: int | None, provider_slug: str) -> tuple[str | None, bool]:
    """Resolve the API key to use for an upstream call to *provider_slug*.

    Returns ``(key, is_byok)``:
      * the customer's decrypted BYOK key when they have one (is_byok=True), else
      * the platform key from the gateway registry (is_byok=False), else
      * ``(None, False)`` when neither exists.
    """
    byok = resolve_byok_key(user_id, provider_slug)
    if byok:
        return byok, True

    try:
        from src.services.gateway_registry import get_provider_api_key

        return get_provider_api_key(provider_slug), False
    except Exception:
        return None, False


def byok_routing_fee(upstream_cost: float) -> float:
    """Fee charged for serving a request on a customer's own key.

    ``upstream_cost`` is the computed provider-side cost of the request (already
    paid on the customer's upstream account). We bill only a fraction of it as a
    routing fee. Rate is env-driven (``BYOK_ROUTING_FEE_RATE``), default 0.0,
    clamped to [0, 0.5].
    """
    try:
        rate = max(0.0, min(0.5, float(os.getenv("BYOK_ROUTING_FEE_RATE", "0.0"))))
    except (TypeError, ValueError):
        rate = 0.0
    if upstream_cost <= 0:
        return 0.0
    return round(upstream_cost * rate, 6)
