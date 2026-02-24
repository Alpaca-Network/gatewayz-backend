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


# Provider-specific helper functions
def get_openrouter_pooled_client() -> OpenAI:
    """Get pooled client for OpenRouter."""
    if not Config.OPENROUTER_API_KEY:
        raise ValueError("OpenRouter API key not configured")

    return get_pooled_client(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key=Config.OPENROUTER_API_KEY,
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


def get_onerouter_pooled_client() -> OpenAI:
    """Get pooled client for Infron AI (formerly OneRouter)."""
    if not Config.ONEROUTER_API_KEY:
        raise ValueError("Infron AI API key not configured")

    return get_pooled_client(
        provider="onerouter",
        base_url="https://llm.infron.ai/v1",
        api_key=Config.ONEROUTER_API_KEY,
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


# Butter.dev timeout (slightly shorter since caching should be fast)
# Only configure timeout when Butter.dev is enabled
BUTTER_DEV_TIMEOUT = httpx.Timeout(
    connect=5.0,
    read=float(Config.BUTTER_DEV_TIMEOUT) if Config.BUTTER_DEV_ENABLED else 30.0,
    write=10.0,
    pool=5.0,
)


def get_butter_pooled_client(
    target_provider: str,
    target_api_key: str,
    target_base_url: str | None = None,
) -> OpenAI:
    """
    Get a pooled client that routes through Butter.dev caching proxy.

    Butter.dev is a caching layer for LLM APIs that identifies patterns in
    responses and serves cached responses to reduce costs and latency.

    Args:
        target_provider: The actual provider to route to (e.g., 'openrouter')
        target_api_key: The API key for the target provider
        target_base_url: Optional base URL override (Butter.dev needs this to route correctly)

    Returns:
        OpenAI client configured to route through Butter.dev

    Raises:
        ValueError: If Butter.dev is not enabled

    See: https://butter.dev

    Note:
        Butter.dev acts as a transparent proxy. It uses the target provider's
        API key and routes the request through its caching layer before
        forwarding to the actual provider.
    """
    if not Config.BUTTER_DEV_ENABLED:
        raise ValueError("Butter.dev is not enabled (set BUTTER_DEV_ENABLED=true)")

    # Use a unique cache key that includes the target provider
    # This ensures we don't accidentally share connections between providers
    cache_provider = f"butter-{target_provider}"

    # Butter.dev headers to help with routing and analytics
    default_headers = {
        "X-Butter-Target-Provider": target_provider,
    }

    # If we have a target base URL, include it so Butter.dev knows where to route
    if target_base_url:
        default_headers["X-Butter-Target-Base-URL"] = target_base_url

    return get_pooled_client(
        provider=cache_provider,
        base_url=Config.BUTTER_DEV_BASE_URL,
        api_key=target_api_key,  # Use the target provider's API key
        default_headers=default_headers,
        timeout=BUTTER_DEV_TIMEOUT,
    )


def get_butter_pooled_async_client(
    target_provider: str,
    target_api_key: str,
    target_base_url: str | None = None,
) -> AsyncOpenAI:
    """
    Get a pooled async client that routes through Butter.dev caching proxy.

    Async version of get_butter_pooled_client for use with async request handlers.

    Args:
        target_provider: The actual provider to route to
        target_api_key: The API key for the target provider
        target_base_url: Optional base URL override

    Returns:
        AsyncOpenAI client configured to route through Butter.dev

    Raises:
        ValueError: If Butter.dev is not enabled
    """
    if not Config.BUTTER_DEV_ENABLED:
        raise ValueError("Butter.dev is not enabled (set BUTTER_DEV_ENABLED=true)")

    cache_provider = f"butter-{target_provider}"

    default_headers = {
        "X-Butter-Target-Provider": target_provider,
    }

    if target_base_url:
        default_headers["X-Butter-Target-Base-URL"] = target_base_url

    return get_pooled_async_client(
        provider=cache_provider,
        base_url=Config.BUTTER_DEV_BASE_URL,
        api_key=target_api_key,
        default_headers=default_headers,
        timeout=BUTTER_DEV_TIMEOUT,
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

    Returns:
        Dict mapping provider names to their warmup status ("ok" or error message)
    """
    # Providers to pre-warm, ordered by typical traffic volume
    providers_to_warm = [
        ("openai", get_openai_pooled_client),
        ("anthropic", get_anthropic_pooled_client),
        ("openrouter", get_openrouter_pooled_client),
        ("fireworks", get_fireworks_pooled_client),
        ("together", get_together_pooled_client),
        ("groq", get_groq_pooled_client),
        ("featherless", get_featherless_pooled_client),
        ("xai", get_xai_pooled_client),
    ]

    results = {}

    for provider_name, get_client_fn in providers_to_warm:
        try:
            # Creating the client establishes the connection
            client = get_client_fn()
            # The client is now in the pool with an active connection
            results[provider_name] = "ok"
            logger.info(f"✅ Warmed up connection to {provider_name}")
        except ValueError as e:
            # API key not configured - expected for some providers
            results[provider_name] = f"skipped: {e}"
            logger.debug(f"⏭️  Skipping {provider_name} warmup: {e}")
        except Exception as e:
            # Connection error - log but don't fail startup
            results[provider_name] = f"error: {e}"
            logger.warning(f"⚠️  Failed to warm up {provider_name}: {e}")

    return results


async def warmup_provider_connections_async() -> dict[str, str]:
    """
    Async wrapper for pre-warming connections.

    Runs the synchronous warmup in a thread pool to avoid blocking
    the event loop during startup.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, warmup_provider_connections)
