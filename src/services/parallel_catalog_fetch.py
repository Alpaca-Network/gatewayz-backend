"""
Parallel Catalog Fetching

Fetches model catalogs from multiple providers in parallel with:
- Concurrent execution using ThreadPoolExecutor (10 workers max)
- Circuit breaker integration for failing providers
- Timeout protection via Redis (10s) and Supabase (30s) timeouts
- Overall request timeout (30s) via asyncio.wait()
- Graceful degradation on failures

This replaces the sequential provider fetching that caused 499 timeouts.
"""

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from prometheus_client import Histogram

from src.services.models import get_cached_models
from src.utils.circuit_breaker import get_provider_circuit_breaker

logger = logging.getLogger(__name__)

PROVIDER_FETCH_DURATION = Histogram(
    "catalog_provider_fetch_duration_seconds",
    "Time spent fetching models from an individual provider",
    ["provider"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 15.0, 30.0),
)

# Per-provider Retry-After deadlines (unix timestamp).
# Populated when a 429 response carries a Retry-After header so the next
# fetch attempt is skipped until the deadline passes.
_retry_after: dict[str, float] = {}

# All supported providers
ALL_PROVIDERS = [
    "openrouter",
    "onerouter",
    "featherless",
    "deepinfra",
    "chutes",
    "groq",
    "fireworks",
    "together",
    "google-vertex",
    "cerebras",
    "nebius",
    "xai",
    "novita",
    "hug",
    "aimo",
    "near",
    "fal",
    "helicone",
    "anannas",
    "aihubmix",
    "vercel-ai-gateway",
    "alibaba",
    "simplismart",
    "openai",
    "anthropic",
    "clarifai",
    "sybil",
    "morpheus",
]

CATALOG_FETCH_WORKERS = int(os.environ.get("CATALOG_FETCH_WORKERS", "10"))

# Thread pool for parallel fetching
_executor: ThreadPoolExecutor | None = None


def get_executor() -> ThreadPoolExecutor:
    """Get or create thread pool executor."""
    global _executor
    if _executor is None:
        # Use configurable worker count to avoid overwhelming the database
        _executor = ThreadPoolExecutor(max_workers=CATALOG_FETCH_WORKERS, thread_name_prefix="catalog_fetch")
        logger.info(f"Created catalog fetch thread pool with {CATALOG_FETCH_WORKERS} workers")
    return _executor


def _validate_provider_response(
    provider: str,
    models: Any,
) -> list[dict[str, Any]]:
    """
    Validate and sanitise the model list returned for a provider (PV-L3).

    Accepts:
    - A plain list of model dicts.
    - A dict with a top-level "data" key whose value is a list (OpenAI-style
      envelope that some providers return directly).

    Each item must be a dict containing at minimum an "id" field.  Malformed
    items are dropped with a WARNING log rather than failing the whole batch.

    Args:
        provider: Provider slug (used only for log messages).
        models:   Raw value returned by get_cached_models().

    Returns:
        A (possibly empty) list of validated model dicts.
    """
    # Unwrap OpenAI-style envelope {"data": [...]}
    if isinstance(models, dict):
        models = models.get("data", [])

    if not isinstance(models, list):
        logger.warning(
            f"Provider {provider} returned unexpected type {type(models).__name__!r}; "
            "expected list — skipping entire batch"
        )
        return []

    valid: list[dict[str, Any]] = []
    for item in models:
        if not isinstance(item, dict) or not item.get("id"):
            logger.warning(
                f"Provider {provider}: dropping malformed model entry (missing 'id'): "
                f"{str(item)[:120]!r}"
            )
        else:
            valid.append(item)

    dropped = len(models) - len(valid)
    if dropped:
        logger.warning(
            f"Provider {provider}: dropped {dropped} malformed model(s) out of {len(models)}"
        )

    return valid


def fetch_provider_with_circuit_breaker(
    provider: str,
    timeout: float = 15.0,  # Not enforced here - relies on DB/Redis timeouts
) -> tuple[str, list[dict[str, Any]]]:
    """
    Fetch models for a single provider with circuit breaker protection.

    Timeout is handled at multiple levels:
    - Redis timeout: 10s
    - Supabase timeout: 30s
    - Overall parallel fetch timeout: 30s (in fetch_all_providers_parallel)

    Args:
        provider: Provider slug
        timeout: Soft timeout for logging (not enforced - DB timeouts handle this)

    Returns:
        Tuple of (provider, models) - models is empty list on failure
    """
    breaker = get_provider_circuit_breaker()

    # PV-M7: Check circuit breaker before attempting any fetch. If the circuit is open
    # the provider has been failing recently; skip it immediately to avoid adding latency
    # and to give the downstream service time to recover.
    if breaker.should_skip(provider):
        logger.debug(f"Skipping {provider} (circuit breaker open)")
        return provider, []

    # Honour any Retry-After deadline received from a previous 429 response
    retry_deadline = _retry_after.get(provider)
    if retry_deadline is not None:
        remaining = retry_deadline - time.time()
        if remaining > 0:
            logger.info(
                f"Skipping {provider} (Retry-After: {remaining:.1f}s remaining)"
            )
            return provider, []
        else:
            # Deadline has passed — clear it
            _retry_after.pop(provider, None)

    start_time = time.time()

    try:
        # Direct call - relies on Redis (10s) and Supabase (30s) timeouts
        # No nested executor to avoid thread exhaustion
        models = get_cached_models(provider) or []
        elapsed = time.time() - start_time

        # Log if it took longer than expected
        if elapsed > timeout:
            logger.warning(f"Provider {provider} took {elapsed:.2f}s (soft limit: {timeout}s)")

        PROVIDER_FETCH_DURATION.labels(provider=provider).observe(elapsed)

        # Validate response structure before propagating (PV-L3).
        models = _validate_provider_response(provider, models)

        if models:
            breaker.record_success(provider)
            logger.info(f"Provider {provider} fetched in {elapsed:.3f}s, {len(models)} models")
            return provider, models
        else:
            # Empty result is not necessarily a failure
            logger.info(f"Provider {provider} fetched in {elapsed:.3f}s, 0 models")
            return provider, []

    except Exception as e:
        elapsed = time.time() - start_time
        breaker.record_failure(provider, str(e))

        # Categorize error type for structured logging
        error_str = str(e)
        status_code = getattr(e, "status_code", None) or getattr(e, "status", None)
        if isinstance(e, TimeoutError) or "timeout" in error_str.lower():
            category = "timeout"
        elif isinstance(e, ConnectionError) or "connection" in error_str.lower():
            category = "connection_error"
        elif status_code == 429 or "429" in error_str:
            category = "rate_limited"
            # PV-L5: Respect Retry-After header when the exception carries one.
            # Try both e.headers (httpx/requests style) and e.response.headers.
            retry_after_raw = (
                (getattr(e, "headers", None) or {}).get("Retry-After")
                or (
                    getattr(getattr(e, "response", None), "headers", None) or {}
                ).get("Retry-After")
            )
            if retry_after_raw:
                try:
                    retry_after_secs = float(retry_after_raw)
                    _retry_after[provider] = time.time() + retry_after_secs
                    logger.warning(
                        f"Provider {provider} rate-limited; "
                        f"Retry-After={retry_after_secs:.0f}s — "
                        f"skipping until deadline"
                    )
                except (ValueError, TypeError):
                    logger.warning(
                        f"Provider {provider} rate-limited; "
                        f"Retry-After header present but unparseable: {retry_after_raw!r}"
                    )
            else:
                logger.warning(f"Provider {provider} rate-limited (no Retry-After header)")
        elif status_code in (401, 403) or any(c in error_str for c in ("401", "403", "unauthorized", "forbidden")):
            category = "auth_failure"
        elif (status_code is not None and 500 <= status_code < 600) or any(c in error_str for c in ("500", "502", "503", "504")):
            category = "server_error"
        else:
            category = "unknown"

        logger.warning(f"Provider {provider} fetch failed: {category} - {e} (elapsed: {elapsed:.2f}s)")
        return provider, []


async def fetch_all_providers_parallel(
    providers: list[str] | None = None,
    timeout_per_provider: float = 15.0,  # Reduced from 30s - faster failure detection
    overall_timeout: float = 30.0,  # Reduced from 45s - faster overall response
) -> dict[str, list[dict[str, Any]]]:
    """
    Fetch catalogs from all providers in parallel.

    Args:
        providers: List of providers to fetch (defaults to ALL_PROVIDERS)
        timeout_per_provider: Max time per provider fetch (enforced per provider)
        overall_timeout: Max total time for all fetches

    Returns:
        Dict mapping provider names to their model lists
    """
    providers = providers or ALL_PROVIDERS
    executor = get_executor()
    results: dict[str, list[dict[str, Any]]] = {}

    start_time = time.time()
    logger.info(f"Starting parallel fetch for {len(providers)} providers (timeout: {overall_timeout}s)")

    # Create futures for all providers
    # Use get_running_loop() instead of deprecated get_event_loop()
    loop = asyncio.get_running_loop()
    tasks = []

    for provider in providers:
        future = loop.run_in_executor(
            executor,
            fetch_provider_with_circuit_breaker,
            provider,
            timeout_per_provider,
        )
        tasks.append((provider, future))

    # Wait for all with overall timeout
    try:
        # Gather all results with timeout
        pending_futures = [task[1] for task in tasks]
        done, pending = await asyncio.wait(
            pending_futures,
            timeout=overall_timeout,
            return_when=asyncio.ALL_COMPLETED,
        )

        # Process completed tasks
        for provider, future in tasks:
            try:
                if future in done:
                    provider_name, models = future.result()
                    results[provider_name] = models
                else:
                    # Task didn't complete in time (shouldn't happen with per-provider timeout)
                    logger.warning(f"Provider {provider} still pending after {overall_timeout}s overall timeout")
                    results[provider] = []
                    # Record as failure for circuit breaker
                    breaker = get_provider_circuit_breaker()
                    breaker.record_failure(provider, "overall_timeout")
            except Exception as e:
                error_str = str(e)
                status_code = getattr(e, "status_code", None) or getattr(e, "status", None)
                if isinstance(e, TimeoutError) or "timeout" in error_str.lower():
                    category = "timeout"
                elif isinstance(e, ConnectionError) or "connection" in error_str.lower():
                    category = "connection_error"
                elif status_code == 429 or "429" in error_str:
                    category = "rate_limited"
                elif status_code in (401, 403) or any(c in error_str for c in ("401", "403", "unauthorized", "forbidden")):
                    category = "auth_failure"
                elif (status_code is not None and 500 <= status_code < 600) or any(c in error_str for c in ("500", "502", "503", "504")):
                    category = "server_error"
                else:
                    category = "unknown"
                logger.warning(f"Provider {provider} fetch failed: {category} - {e}")
                results[provider] = []

        # Cancel any remaining pending tasks
        for future in pending:
            future.cancel()

        # Log how many didn't complete
        if pending:
            logger.warning(f"{len(pending)} provider fetches did not complete within timeout")

    except asyncio.TimeoutError:
        logger.error(f"Overall timeout ({overall_timeout}s) exceeded for parallel fetch")
        # Return whatever we have
        for provider, _ in tasks:
            if provider not in results:
                results[provider] = []

    elapsed = time.time() - start_time
    total_models = sum(len(models) for models in results.values())
    successful_providers = sum(1 for models in results.values() if models)

    logger.info(
        f"Parallel fetch complete: {successful_providers}/{len(providers)} providers, "
        f"{total_models} total models in {elapsed:.2f}s"
    )

    return results


def merge_provider_results(results: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """
    Merge results from multiple providers into a single list.

    Args:
        results: Dict mapping provider names to model lists

    Returns:
        Combined list of all models
    """
    all_models = []
    for provider, models in results.items():
        all_models.extend(models)

    logger.debug(f"Merged {len(all_models)} models from {len(results)} providers")
    return all_models


async def fetch_and_merge_all_providers(
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """
    Convenience function to fetch all providers and merge results.

    Args:
        timeout: Overall timeout for all fetches

    Returns:
        List of all models from all providers
    """
    results = await fetch_all_providers_parallel(
        providers=ALL_PROVIDERS,
        timeout_per_provider=15.0,  # Soft limit - actual timeout via Redis/Supabase
        overall_timeout=timeout,
    )
    return merge_provider_results(results)


def get_circuit_breaker_status() -> dict[str, Any]:
    """Get status of all circuit breakers for monitoring."""
    breaker = get_provider_circuit_breaker()
    return {
        "open_circuits": breaker.get_open_circuits(),
        "all_status": breaker.get_all_status(),
    }
