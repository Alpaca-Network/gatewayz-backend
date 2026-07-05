"""
Connection pooling manager for model provider clients.

This module provides persistent HTTP client connections with connection pooling,
keepalive, and optimized timeout settings to improve chat streaming performance.
"""

import asyncio
import hashlib
import logging
import os
import time
from collections import OrderedDict
from threading import Lock

import httpx
from openai import AsyncOpenAI, OpenAI

from src.config import Config

# Simplismart base URL constant (duplicated here to avoid circular import with simplismart_client.py)
SIMPLISMART_BASE_URL = "https://api.simplismart.live"

logger = logging.getLogger(__name__)

# Global connection pool instances with LRU tracking
_client_pool: OrderedDict[str, tuple[OpenAI, float]] = OrderedDict()  # (client, last_used)
_async_client_pool: OrderedDict[str, tuple[AsyncOpenAI, float]] = OrderedDict()
_pool_lock = Lock()
_cleanup_task = None

# Pool configuration
MAX_POOL_SIZE = int(os.getenv("CONNECTION_POOL_MAX_SIZE", "50"))

# Connection pool configuration
DEFAULT_LIMITS = httpx.Limits(
    max_connections=100,  # Maximum total connections
    max_keepalive_connections=20,  # Keep these many connections alive
    keepalive_expiry=30.0,  # Seconds to keep idle connections alive
)

DEFAULT_TIMEOUT = httpx.Timeout(
    connect=5.0,  # Connection timeout
    read=60.0,  # Read timeout for streaming
    write=10.0,  # Write timeout
    pool=5.0,  # Pool acquisition timeout
)

HUGGINGFACE_TIMEOUT = httpx.Timeout(
    connect=10.0,
    read=120.0,  # HuggingFace models can be slow
    write=10.0,
    pool=5.0,
)

# xAI timeout for reasoning models (Grok 4.x with extended thinking)
# xAI documentation recommends 3600s timeout for reasoning models
# See: https://docs.x.ai/docs/guides/reasoning
XAI_REASONING_TIMEOUT = httpx.Timeout(
    connect=10.0,
    read=600.0,  # 10 minutes for extended reasoning (conservative vs 3600s)
    write=10.0,
    pool=5.0,
)


def _normalize_base_url(base_url: str) -> str:
    """Normalize base URLs to avoid duplicate cache keys due to trailing slashes."""
    stripped = base_url.strip()
    return stripped[:-1] if stripped.endswith("/") else stripped


def _pool_prefix(provider: str, base_url: str) -> str:
    """Build a stable prefix for all clients of a provider/base_url combination."""
    return f"{provider.lower()}::{_normalize_base_url(base_url)}"


def _api_key_hash(api_key: str | None) -> str:
    """Hash API keys so rotations create distinct clients without storing secrets."""
    if not api_key:
        return "no-key"
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def _cache_key(provider: str, base_url: str, api_key: str | None) -> str:
    """Generate the full cache key including the hashed API key."""
    return f"{_pool_prefix(provider, base_url)}::{_api_key_hash(api_key)}"


def _evict_sync_clients(prefix: str):
    """Remove (and close) cached sync clients that match the prefix."""
    stale_keys = [key for key in _client_pool if key.startswith(prefix)]
    for stale_key in stale_keys:
        client_tuple = _client_pool.pop(stale_key, None)
        if client_tuple:
            try:
                client, _ = client_tuple  # Unpack the (client, timestamp) tuple
                client.close()
            except Exception as exc:
                logger.warning(f"Error closing client for {prefix}: {exc}")


def _evict_async_clients(prefix: str):
    """Remove cached async clients that match the prefix (no close call)."""
    stale_keys = [key for key in _async_client_pool if key.startswith(prefix)]
    for stale_key in stale_keys:
        _async_client_pool.pop(stale_key, None)


def _get_http_client(
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    limits: httpx.Limits = DEFAULT_LIMITS,
) -> httpx.Client:
    """Create an HTTP client with connection pooling and keepalive.

    Note: HTTP/2 is disabled by default to prevent "Bad file descriptor" errors
    when servers close idle connections. HTTP/1.1 with keepalive provides better
    stability for long-running services.
    """
    return httpx.Client(
        timeout=timeout,
        limits=limits,
        http2=False,  # Disable HTTP/2 to prevent stale connection issues
        follow_redirects=True,
    )


def get_http_client(
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    limits: httpx.Limits = DEFAULT_LIMITS,
) -> httpx.Client:
    """Get a pooled HTTP client with connection pooling and keepalive (public API)."""
    return _get_http_client(timeout=timeout, limits=limits)


def _get_async_http_client(
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    limits: httpx.Limits = DEFAULT_LIMITS,
) -> httpx.AsyncClient:
    """Create an async HTTP client with connection pooling and keepalive.

    Note: HTTP/2 is disabled by default to prevent "Bad file descriptor" errors
    when servers close idle connections. HTTP/1.1 with keepalive provides better
    stability for long-running services.
    """
    return httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        http2=False,  # Disable HTTP/2 to prevent stale connection issues
        follow_redirects=True,
    )


def get_pooled_client(
    provider: str,
    base_url: str,
    api_key: str,
    default_headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
) -> OpenAI:
    """
    Get or create a pooled OpenAI client for a specific provider.

    Args:
        provider: Provider name (e.g., 'openrouter', 'featherless')
        base_url: API base URL
        api_key: API key for authentication
        default_headers: Optional headers to include in all requests
        timeout: Optional custom timeout configuration

    Returns:
        OpenAI client with connection pooling enabled
    """
    prefix = _pool_prefix(provider, base_url)
    cache_key = _cache_key(provider, base_url, api_key)

    with _pool_lock:
        # Check if client exists and mark as recently used
        if cache_key in _client_pool:
            client, _ = _client_pool[cache_key]
            _client_pool[cache_key] = (client, time.time())
            _client_pool.move_to_end(cache_key)  # Mark as recently used
            return client

        # Evict oldest if at capacity
        if len(_client_pool) >= MAX_POOL_SIZE:
            oldest_key, (old_client, _) = _client_pool.popitem(last=False)
            try:
                old_client.close()
            except Exception as e:
                logger.warning(f"Error closing evicted client: {e}")
            logger.info(
                f"Evicted oldest client from pool: {oldest_key} (pool size: {len(_client_pool)})"
            )

        # API key rotated: evict any stale clients for this provider/base pair
        _evict_sync_clients(prefix)

        http_client = _get_http_client(
            timeout=timeout or DEFAULT_TIMEOUT,
            limits=DEFAULT_LIMITS,
        )

        client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            default_headers=default_headers or {},
            http_client=http_client,
            max_retries=2,  # Enable automatic retries
        )

        _client_pool[cache_key] = (client, time.time())
        logger.info(f"Created pooled client for {provider} (pool size: {len(_client_pool)})")

        return client


def get_pooled_async_client(
    provider: str,
    base_url: str,
    api_key: str,
    default_headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
) -> AsyncOpenAI:
    """
    Get or create a pooled AsyncOpenAI client for a specific provider.

    Args:
        provider: Provider name (e.g., 'openrouter', 'featherless')
        base_url: API base URL
        api_key: API key for authentication
        default_headers: Optional headers to include in all requests
        timeout: Optional custom timeout configuration

    Returns:
        AsyncOpenAI client with connection pooling enabled
    """
    prefix = _pool_prefix(provider, base_url)
    cache_key = _cache_key(provider, base_url, api_key) + "_async"

    with _pool_lock:
        # Check if client exists and mark as recently used
        if cache_key in _async_client_pool:
            client, _ = _async_client_pool[cache_key]
            _async_client_pool[cache_key] = (client, time.time())
            _async_client_pool.move_to_end(cache_key)
            return client

        # Evict oldest if at capacity
        if len(_async_client_pool) >= MAX_POOL_SIZE:
            oldest_key, (old_client, _) = _async_client_pool.popitem(last=False)
            try:
                # Async close in background (don't block)
                asyncio.create_task(old_client.close())
            except Exception as e:
                logger.warning(f"Error closing evicted async client: {e}")
            logger.info(
                f"Evicted oldest async client from pool: {oldest_key} (pool size: {len(_async_client_pool)})"
            )

        _evict_async_clients(prefix)

        http_client = _get_async_http_client(
            timeout=timeout or DEFAULT_TIMEOUT,
            limits=DEFAULT_LIMITS,
        )

        client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            default_headers=default_headers or {},
            http_client=http_client,
            max_retries=2,
        )

        _async_client_pool[cache_key] = (client, time.time())
        logger.info(
            f"Created pooled async client for {provider} (pool size: {len(_async_client_pool)})"
        )

        return client


def clear_connection_pools():
    """Clear all connection pools. Useful for testing or graceful shutdown."""
    with _pool_lock:
        # Close all sync clients
        for client, _ in _client_pool.values():
            try:
                client.close()
            except Exception as e:
                logger.warning(f"Error closing client: {e}")
        _client_pool.clear()

        # Close all async clients
        for client, _ in _async_client_pool.values():
            try:
                # AsyncOpenAI clients need to be closed in an async context
                # Schedule close task if in event loop
                try:
                    asyncio.create_task(client.close())
                except RuntimeError:
                    # No event loop running, just clear reference
                    pass
            except Exception as e:
                logger.warning(f"Error closing async client: {e}")
        _async_client_pool.clear()

        logger.info("Cleared all connection pools")


def get_pool_stats() -> dict[str, int]:
    """Get statistics about current connection pools."""
    with _pool_lock:
        return {
            "sync_clients": len(_client_pool),
            "async_clients": len(_async_client_pool),
            "total_clients": len(_client_pool) + len(_async_client_pool),
        }


# ---------------------------------------------------------------------------
# Generic DB-driven pooled client (Phase 3)
# ---------------------------------------------------------------------------


def get_provider_pooled_client(slug: str) -> OpenAI:
    """Return a pooled OpenAI-compat client for *slug*, configured from the DB registry.

    Resolves base_url, api_key, custom headers, and timeout from the gateway
    registry (populated by the ``providers`` table).  Raises ``ValueError``
    when the provider is missing, has no API key, or has no base_url.
    """
    from src.services.gateway_registry import get_gateway_registry, get_provider_api_key

    registry = get_gateway_registry()
    entry = registry.get(slug)
    if not entry:
        raise ValueError(f"Provider '{slug}' not found in gateway registry")

    api_key = get_provider_api_key(slug)
    if not api_key:
        raise ValueError(f"{slug} API key not configured (env var: {entry.get('api_key_env_var')})")

    base_url = entry.get("base_url")
    if not base_url:
        raise ValueError(f"No base_url configured for provider '{slug}'")

    # Resolve templated URLs (e.g. Cloudflare {CLOUDFLARE_ACCOUNT_ID})
    import re as _re

    placeholders = _re.findall(r"\{(\w+)\}", base_url)
    for placeholder in placeholders:
        value = getattr(Config, placeholder, None) or os.environ.get(placeholder)
        if not value:
            raise ValueError(
                f"Template variable '{{{placeholder}}}' in base_url for '{slug}' "
                f"is not configured in Config or environment"
            )
        base_url = base_url.replace(f"{{{placeholder}}}", value)

    # Resolve timeout
    timeout = None
    timeout_ms = entry.get("custom_timeout_ms")
    if timeout_ms:
        timeout = httpx.Timeout(
            connect=10.0,
            read=timeout_ms / 1000.0,
            write=10.0,
            pool=5.0,
        )

    # Resolve custom headers
    raw_headers = entry.get("default_headers") or {}
    headers = {}
    for key, value in raw_headers.items():
        # Resolve sentinel placeholders (e.g. __OPENROUTER_SITE_URL__)
        if isinstance(value, str) and value.startswith("__") and value.endswith("__"):
            attr_name = value.strip("_")
            headers[key] = getattr(Config, attr_name, "") or ""
        else:
            headers[key] = value

    return get_pooled_client(
        provider=slug,
        base_url=base_url,
        api_key=api_key,
        default_headers=headers if headers else None,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Provider-specific helper functions (thin wrappers for backward compat)
# ---------------------------------------------------------------------------
def get_openrouter_pooled_client() -> OpenAI:
    """Get pooled client for OpenRouter.

    Honours a bring-your-own-key override bound for "openrouter" in the current
    request context. get_pooled_client caches per api_key, so a customer key gets
    its own pooled client with no cross-tenant leakage.
    """
    api_key = Config.OPENROUTER_API_KEY
    try:
        from src.services.byok import get_byok_key_for

        byok = get_byok_key_for("openrouter")
        if byok:
            api_key = byok
    except Exception:
        pass

    if not api_key:
        raise ValueError("OpenRouter API key not configured")

    return get_pooled_client(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        default_headers={
            "HTTP-Referer": Config.OPENROUTER_SITE_URL,
            "X-Title": Config.OPENROUTER_SITE_NAME,
        },
    )


def get_featherless_pooled_client() -> OpenAI:
    """Get pooled client for Featherless.ai."""
    if not Config.FEATHERLESS_API_KEY:
        raise ValueError("Featherless API key not configured")

    return get_pooled_client(
        provider="featherless",
        base_url="https://api.featherless.ai/v1",
        api_key=Config.FEATHERLESS_API_KEY,
    )


def get_fireworks_pooled_client() -> OpenAI:
    """Get pooled client for Fireworks.ai."""
    if not Config.FIREWORKS_API_KEY:
        raise ValueError("Fireworks API key not configured")

    return get_pooled_client(
        provider="fireworks",
        base_url="https://api.fireworks.ai/inference/v1",
        api_key=Config.FIREWORKS_API_KEY,
    )


def get_together_pooled_client() -> OpenAI:
    """Get pooled client for Together.ai."""
    if not Config.TOGETHER_API_KEY:
        raise ValueError("Together API key not configured")

    return get_pooled_client(
        provider="together",
        base_url="https://api.together.xyz/v1",
        api_key=Config.TOGETHER_API_KEY,
    )


def get_huggingface_pooled_client() -> OpenAI:
    """Get pooled client for HuggingFace (with extended timeout)."""
    if not Config.HUGGINGFACE_API_KEY:
        raise ValueError("HuggingFace API key not configured")

    return get_pooled_client(
        provider="huggingface",
        base_url="https://router.huggingface.co/v1",
        api_key=Config.HUGGINGFACE_API_KEY,
        timeout=HUGGINGFACE_TIMEOUT,
    )


def get_xai_pooled_client() -> OpenAI:
    """Get pooled client for X.AI.

    Uses extended timeout for Grok reasoning models which can take
    significantly longer to respond due to extended thinking capabilities.
    See: https://docs.x.ai/docs/guides/reasoning
    """
    if not Config.XAI_API_KEY:
        raise ValueError("X.AI API key not configured")

    return get_pooled_client(
        provider="xai",
        base_url="https://api.x.ai/v1",
        api_key=Config.XAI_API_KEY,
        timeout=XAI_REASONING_TIMEOUT,
    )


def get_deepinfra_pooled_client() -> OpenAI:
    """Get pooled client for DeepInfra."""
    if not Config.DEEPINFRA_API_KEY:
        raise ValueError("DeepInfra API key not configured")

    return get_pooled_client(
        provider="deepinfra",
        base_url="https://api.deepinfra.com/v1/openai",
        api_key=Config.DEEPINFRA_API_KEY,
    )


def get_chutes_pooled_client() -> OpenAI:
    """Get pooled client for Chutes.ai."""
    if not Config.CHUTES_API_KEY:
        raise ValueError("Chutes API key not configured")

    return get_pooled_client(
        provider="chutes",
        base_url="https://llm.chutes.ai/v1",
        api_key=Config.CHUTES_API_KEY,
    )


def get_clarifai_pooled_client() -> OpenAI:
    """Get pooled client for Clarifai.

    Uses Clarifai's OpenAI-compatible API endpoint.
    See: https://docs.clarifai.com/compute/inference/open-ai/
    """
    if not Config.CLARIFAI_API_KEY:
        raise ValueError("Clarifai API key not configured")

    return get_pooled_client(
        provider="clarifai",
        base_url="https://api.clarifai.com/v2/ext/openai/v1",
        api_key=Config.CLARIFAI_API_KEY,
    )


def get_groq_pooled_client() -> OpenAI:
    """Get pooled client for Groq."""
    if not Config.GROQ_API_KEY:
        raise ValueError("Groq API key not configured")

    return get_pooled_client(
        provider="groq",
        base_url="https://api.groq.com/openai/v1",
        api_key=Config.GROQ_API_KEY,
    )


def get_zai_pooled_client() -> OpenAI:
    """Get pooled client for Z.AI (Zhipu AI).

    Z.AI provides OpenAI-compatible API endpoints for the GLM model family.
    See: https://docs.z.ai
    """
    if not Config.ZAI_API_KEY:
        raise ValueError("Z.AI API key not configured")

    return get_pooled_client(
        provider="zai",
        base_url="https://api.z.ai/api/paas/v4/",
        api_key=Config.ZAI_API_KEY,
    )


def get_cloudflare_workers_ai_pooled_client() -> OpenAI:
    """Get pooled client for Cloudflare Workers AI.

    Cloudflare Workers AI provides an OpenAI-compatible API endpoint.
    See: https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/
    """
    if not Config.CLOUDFLARE_API_TOKEN:
        raise ValueError("Cloudflare API token not configured")
    if not Config.CLOUDFLARE_ACCOUNT_ID:
        raise ValueError("Cloudflare Account ID not configured")

    # Cloudflare's OpenAI-compatible base URL includes the account ID
    base_url = f"https://api.cloudflare.com/client/v4/accounts/{Config.CLOUDFLARE_ACCOUNT_ID}/ai/v1"

    return get_pooled_client(
        provider="cloudflare-workers-ai",
        base_url=base_url,
        api_key=Config.CLOUDFLARE_API_TOKEN,
    )


def get_akash_pooled_client() -> OpenAI:
    """Get pooled client for Akash ML.

    Akash ML provides an OpenAI-compatible API endpoint.
    See: https://api.akashml.com/v1
    """
    if not Config.AKASH_API_KEY:
        raise ValueError("Akash API key not configured")

    return get_pooled_client(
        provider="akash",
        base_url="https://api.akashml.com/v1",
        api_key=Config.AKASH_API_KEY,
    )


def get_morpheus_pooled_client() -> OpenAI:
    """Get pooled client for Morpheus AI Gateway.

    Morpheus provides an OpenAI-compatible API endpoint for decentralized AI.
    See: https://api.mor.org/api/v1
    """
    if not Config.MORPHEUS_API_KEY:
        raise ValueError("Morpheus API key not configured")

    return get_pooled_client(
        provider="morpheus",
        base_url="https://api.mor.org/api/v1",
        api_key=Config.MORPHEUS_API_KEY,
    )


def get_openai_pooled_client() -> OpenAI:
    """Get pooled client for OpenAI direct API.

    OpenAI provides the official API for GPT models.
    See: https://platform.openai.com/docs/api-reference
    """
    if not Config.OPENAI_API_KEY:
        raise ValueError("OpenAI API key not configured")

    return get_pooled_client(
        provider="openai",
        base_url="https://api.openai.com/v1",
        api_key=Config.OPENAI_API_KEY,
    )


def get_anthropic_pooled_client() -> OpenAI:
    """Get pooled client for Anthropic direct API (OpenAI-compatible endpoint).

    Anthropic provides an OpenAI-compatible API endpoint for Claude models.
    Note: For native Anthropic API, use the anthropic SDK directly.
    See: https://docs.anthropic.com/en/api/openai-sdk
    """
    if not Config.ANTHROPIC_API_KEY:
        raise ValueError("Anthropic API key not configured")

    return get_pooled_client(
        provider="anthropic",
        base_url="https://api.anthropic.com/v1",
        api_key=Config.ANTHROPIC_API_KEY,
    )


def get_simplismart_pooled_client() -> OpenAI:
    """Get pooled client for Simplismart AI.

    Simplismart provides an OpenAI-compatible API endpoint for various LLM models
    including Llama, Gemma, Qwen, DeepSeek, Mixtral, and more.
    See: https://docs.simplismart.ai/overview
    """
    if not Config.SIMPLISMART_API_KEY:
        raise ValueError("Simplismart API key not configured")

    return get_pooled_client(
        provider="simplismart",
        base_url=SIMPLISMART_BASE_URL,
        api_key=Config.SIMPLISMART_API_KEY,
    )


# Sybil base URL constant
SYBIL_BASE_URL = "https://api.sybil.com/v1"


def get_sybil_pooled_client() -> OpenAI:
    """Get pooled client for Sybil AI.

    Sybil provides an OpenAI-compatible API endpoint for fast, open-source models
    with GPU infrastructure for efficient inference.
    See: https://docs.sybil.com/
    """
    if not Config.SYBIL_API_KEY:
        raise ValueError("Sybil API key not configured")

    return get_pooled_client(
        provider="sybil",
        base_url=SYBIL_BASE_URL,
        api_key=Config.SYBIL_API_KEY,
    )


def get_canopywave_pooled_client() -> OpenAI:
    """Get pooled client for Canopy Wave AI.

    Canopy Wave provides an OpenAI-compatible API endpoint for open-source models
    with serverless GPU infrastructure for efficient inference.
    See: https://canopywave.com/docs/get-started/openai-compatible
    """
    if not Config.CANOPYWAVE_API_KEY:
        raise ValueError("Canopy Wave API key not configured")

    return get_pooled_client(
        provider="canopywave",
        base_url=Config.CANOPYWAVE_BASE_URL,
        api_key=Config.CANOPYWAVE_API_KEY,
    )


# Nosana base URL constant
NOSANA_BASE_URL = "https://dashboard.k8s.prd.nos.ci/api/v1"


def get_nosana_pooled_client() -> OpenAI:
    """Get pooled client for Nosana GPU Computing Network.

    Nosana provides a distributed GPU computing network with OpenAI-compatible
    API endpoints for AI model inference.
    See: https://learn.nosana.com/api
    """
    if not Config.NOSANA_API_KEY:
        raise ValueError("Nosana API key not configured")

    return get_pooled_client(
        provider="nosana",
        base_url=NOSANA_BASE_URL,
        api_key=Config.NOSANA_API_KEY,
    )


# =============================================================================
# CONNECTION PRE-WARMING
# =============================================================================
# PERF: Pre-warm connections to frequently used providers on app startup.
# This eliminates the cold-start penalty (~100-200ms) for TLS handshake,
# HTTP/2 connection setup, and provider authentication on first requests.


def warmup_provider_connections() -> dict[str, str]:
    """
    Pre-warm connections to frequently used providers.

    This function creates pooled clients for high-traffic providers,
    establishing TCP connections, completing TLS handshakes, and
    setting up HTTP/2 multiplexing before any user requests arrive.

    Only warms enabled providers (per ENABLED_PROVIDERS).

    Returns:
        Dict mapping provider names to their warmup status ("ok" or error message)
    """
    from src.utils.provider_filter import is_provider_enabled

    # Fallback warmup list (used if registry unavailable)
    _FALLBACK_WARMUP = [
        ("openai", get_openai_pooled_client),
        ("anthropic", get_anthropic_pooled_client),
        ("openrouter", get_openrouter_pooled_client),
        ("fireworks", get_fireworks_pooled_client),
        ("together", get_together_pooled_client),
        ("groq", get_groq_pooled_client),
        ("featherless", get_featherless_pooled_client),
        ("xai", get_xai_pooled_client),
    ]

    # Try DB-driven warmup: warm all providers that have a base_url
    try:
        from src.services.gateway_registry import get_gateway_registry

        registry = get_gateway_registry()
        slugs_to_warm = [
            slug
            for slug, entry in registry.items()
            if entry.get("base_url") and entry.get("api_key_env_var")
        ]
    except Exception:
        slugs_to_warm = None

    results = {}

    if slugs_to_warm is not None:
        for slug in slugs_to_warm:
            if not is_provider_enabled(slug):
                continue
            try:
                get_provider_pooled_client(slug)
                results[slug] = "ok"
                logger.info(f"Warmed up connection to {slug}")
            except ValueError as e:
                results[slug] = f"skipped: {e}"
                logger.debug(f"Skipping {slug} warmup: {e}")
            except Exception as e:
                results[slug] = f"error: {e}"
                logger.warning(f"Failed to warm up {slug}: {e}")
    else:
        for provider_name, get_client_fn in _FALLBACK_WARMUP:
            if not is_provider_enabled(provider_name):
                continue
            try:
                get_client_fn()
                results[provider_name] = "ok"
                logger.info(f"Warmed up connection to {provider_name}")
            except ValueError as e:
                results[provider_name] = f"skipped: {e}"
                logger.debug(f"Skipping {provider_name} warmup: {e}")
            except Exception as e:
                results[provider_name] = f"error: {e}"
                logger.warning(f"Failed to warm up {provider_name}: {e}")

    return results


async def warmup_provider_connections_async() -> dict[str, str]:
    """
    Async wrapper for pre-warming connections.

    Runs the synchronous warmup in a thread pool to avoid blocking
    the event loop during startup.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, warmup_provider_connections)
