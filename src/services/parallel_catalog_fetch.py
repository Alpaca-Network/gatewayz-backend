"""
Parallel Catalog Fetching

Fetches model catalogs from multiple providers in parallel with:
- Concurrent execution using ThreadPoolExecutor
- Circuit breaker integration for failing providers
- Timeout protection per provider (enforced via nested executor)
- Graceful degradation on failures

This replaces the sequential provider fetching that caused 499 timeouts.
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, wait, FIRST_COMPLETED
from typing import Any

from src.services.models import get_cached_models
from src.utils.circuit_breaker import get_provider_circuit_breaker

logger = logging.getLogger(__name__)

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

# Thread pool for parallel fetching
_executor: ThreadPoolExecutor | None = None


def get_executor() -> ThreadPoolExecutor:
    """Get or create thread pool executor."""
    global _executor
    if _executor is None:
        # Use max 10 workers to avoid overwhelming the database
        _executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="catalog_fetch")
        logger.info("Created catalog fetch thread pool with 10 workers")
    return _executor


def _fetch_models_sync(provider: str) -> list[dict[str, Any]]:
    """
    Synchronous helper to fetch models for a provider.
    This is wrapped by fetch_provider_with_circuit_breaker for timeout enforcement.
    """
    return get_cached_models(provider) or []


def fetch_provider_with_circuit_breaker(
    provider: str,
    timeout: float = 15.0,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Fetch models for a single provider with circuit breaker protection and timeout.

    Args:
        provider: Provider slug
        timeout: Maximum time to wait for this provider (ENFORCED)

    Returns:
        Tuple of (provider, models) - models is empty list on failure
    """
    breaker = get_provider_circuit_breaker()

    # Check circuit breaker first
    if breaker.should_skip(provider):
        logger.info(f"Skipping {provider} (circuit breaker open)")
        return provider, []

    start_time = time.time()
    timeout_executor = None

    try:
        # Use a separate single-thread executor to enforce timeout
        # IMPORTANT: Don't use context manager - it waits for threads to complete!
        # Instead, use shutdown(wait=False) on timeout to return immediately
        timeout_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"fetch_{provider}")
        future = timeout_executor.submit(_fetch_models_sync, provider)

        try:
            # Wait with timeout - this will raise TimeoutError if exceeded
            models = future.result(timeout=timeout)
            elapsed = time.time() - start_time

            # Success - clean up executor properly
            timeout_executor.shutdown(wait=True)

            if models:
                breaker.record_success(provider)
                logger.debug(f"Fetched {len(models)} models from {provider} in {elapsed:.2f}s")
                return provider, models
            else:
                # Empty result is not necessarily a failure
                logger.debug(f"No models from {provider} (elapsed: {elapsed:.2f}s)")
                return provider, []

        except FuturesTimeoutError:
            elapsed = time.time() - start_time
            breaker.record_failure(provider, f"timeout after {timeout}s")
            logger.warning(f"Provider {provider} timed out after {elapsed:.2f}s (limit: {timeout}s)")
            # Cancel the future and shutdown WITHOUT waiting
            # This allows us to return immediately instead of blocking
            future.cancel()
            timeout_executor.shutdown(wait=False)
            return provider, []

    except Exception as e:
        elapsed = time.time() - start_time
        breaker.record_failure(provider, str(e))
        logger.warning(f"Error fetching {provider} (elapsed: {elapsed:.2f}s): {e}")
        # Clean up executor if it was created
        if timeout_executor:
            timeout_executor.shutdown(wait=False)
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
                logger.warning(f"Error getting result for {provider}: {e}")
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
        timeout_per_provider=15.0,  # Per-provider timeout enforced with nested executor
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
