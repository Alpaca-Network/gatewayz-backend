"""
Gateway Health Check Service

Provides comprehensive health checking for all gateway providers
with auto-fix capabilities for cache refresh.
"""

import asyncio
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from src.config import Config
from src.services.model_catalog_cache import get_cached_gateway_catalog, get_gateway_cache_metadata
from src.services.prometheus_metrics import (
    record_auto_fix_attempt,
    record_fallback_activation,
    record_gateway_recovery,
    record_zero_model_event,
    set_gateway_model_count,
    track_gateway_health_check,
)

logger = logging.getLogger(__name__)


# Helper function to create a cache-like dict for Redis-backed catalogs
def _get_redis_cache_wrapper(provider_slug: str) -> dict:
    """
    Create a dict-like wrapper for Redis-backed provider caches.
    This maintains compatibility with existing health check code.
    """
    return {
        "data": get_cached_gateway_catalog(provider_slug),
        "timestamp": get_gateway_cache_metadata(provider_slug).get("timestamp"),
        "provider": provider_slug,
    }


# Create cache wrappers for all providers (lazy-loaded via property access)
class _CacheWrapper:
    """Lazy wrapper that fetches cache data from Redis on access"""

    def __init__(self, provider_slug: str):
        self.provider_slug = provider_slug

    def get(self, key: str, default=None):
        """Dict-like get method"""
        cache_data = _get_redis_cache_wrapper(self.provider_slug)
        return cache_data.get(key, default)

    def __getitem__(self, key: str):
        """Dict-like bracket access"""
        cache_data = _get_redis_cache_wrapper(self.provider_slug)
        return cache_data[key]

    def __setitem__(self, key: str, value):
        """Dict-like assignment (no-op for health checks - they only read)"""
        logger.debug(
            f"Cache write attempted for {self.provider_slug}.{key} (ignored in Redis mode)"
        )


# Create cache wrapper instances for all providers
_models_cache = _CacheWrapper("openrouter")
_featherless_models_cache = _CacheWrapper("featherless")
_chutes_models_cache = _CacheWrapper("chutes")
_groq_models_cache = _CacheWrapper("groq")
_fireworks_models_cache = _CacheWrapper("fireworks")
_together_models_cache = _CacheWrapper("together")
_deepinfra_models_cache = _CacheWrapper("deepinfra")
_cerebras_models_cache = _CacheWrapper("cerebras")
_xai_models_cache = _CacheWrapper("xai")
_nebius_models_cache = _CacheWrapper("nebius")
_novita_models_cache = _CacheWrapper("novita")
_huggingface_models_cache = _CacheWrapper("huggingface")
_aimo_models_cache = _CacheWrapper("aimo")
_near_models_cache = _CacheWrapper("near")
_fal_models_cache = _CacheWrapper("fal")
_aihubmix_models_cache = _CacheWrapper("aihubmix")
_anannas_models_cache = _CacheWrapper("anannas")
_onerouter_models_cache = _CacheWrapper("onerouter")
_google_vertex_models_cache = _CacheWrapper("google-vertex")
_cloudflare_workers_ai_models_cache = _CacheWrapper("cloudflare-workers-ai")
_vercel_ai_gateway_models_cache = _CacheWrapper("vercel-ai-gateway")
_helicone_models_cache = _CacheWrapper("helicone")
_openai_models_cache = _CacheWrapper("openai")
_anthropic_models_cache = _CacheWrapper("anthropic")
_clarifai_models_cache = _CacheWrapper("clarifai")
_alibaba_models_cache = _CacheWrapper("alibaba")
_simplismart_models_cache = _CacheWrapper("simplismart")
_modelz_cache = _CacheWrapper("modelz")
_canopywave_models_cache = _CacheWrapper("canopywave")
_sybil_models_cache = _CacheWrapper("sybil")
_morpheus_models_cache = _CacheWrapper("morpheus")

# Gateway configuration with API endpoints
GATEWAY_CONFIG = {
    "openrouter": {
        "name": "OpenRouter",
        "url": "https://openrouter.ai/api/v1/models",
        "api_key_env": "OPENROUTER_API_KEY",
        "api_key": Config.OPENROUTER_API_KEY,
        "cache": _models_cache,
        "min_expected_models": 100,
        "header_type": "bearer",
    },
    "featherless": {
        "name": "Featherless",
        "url": "https://api.featherless.ai/v1/models",
        "api_key_env": "FEATHERLESS_API_KEY",
        "api_key": Config.FEATHERLESS_API_KEY,
        "cache": _featherless_models_cache,
        "min_expected_models": 10,
        "header_type": "bearer",
    },
    "chutes": {
        "name": "Chutes",
        "url": "https://llm.chutes.ai/v1/models",
        "api_key_env": "CHUTES_API_KEY",
        "api_key": getattr(Config, "CHUTES_API_KEY", None),
        "cache": _chutes_models_cache,
        "min_expected_models": 5,
        "header_type": "bearer",
    },
    "groq": {
        "name": "Groq",
        "url": "https://api.groq.com/openai/v1/models",
        "api_key_env": "GROQ_API_KEY",
        "api_key": os.environ.get("GROQ_API_KEY"),
        "cache": _groq_models_cache,
        "min_expected_models": 5,
        "header_type": "bearer",
    },
    "fireworks": {
        "name": "Fireworks",
        "url": "https://api.fireworks.ai/inference/v1/models",
        "api_key_env": "FIREWORKS_API_KEY",
        "api_key": os.environ.get("FIREWORKS_API_KEY"),
        "cache": _fireworks_models_cache,
        "min_expected_models": 10,
        "header_type": "bearer",
    },
    "together": {
        "name": "Together",
        "url": "https://api.together.xyz/v1/models",
        "api_key_env": "TOGETHER_API_KEY",
        "api_key": os.environ.get("TOGETHER_API_KEY"),
        "cache": _together_models_cache,
        "min_expected_models": 20,
        "header_type": "bearer",
    },
    "deepinfra": {
        "name": "DeepInfra",
        "url": "https://api.deepinfra.com/models/list",
        "api_key_env": "DEEPINFRA_API_KEY",
        "api_key": Config.DEEPINFRA_API_KEY,
        "cache": _deepinfra_models_cache,
        "min_expected_models": 50,
        "header_type": "bearer",
    },
    "cerebras": {
        "name": "Cerebras",
        "url": "https://api.cerebras.ai/v1/models",
        "api_key_env": "CEREBRAS_API_KEY",
        "api_key": Config.CEREBRAS_API_KEY,
        "cache": _cerebras_models_cache,
        "min_expected_models": 2,
        "header_type": "bearer",
    },
    "xai": {
        "name": "xAI",
        "url": "https://api.x.ai/v1/models",
        "api_key_env": "XAI_API_KEY",
        "api_key": Config.XAI_API_KEY,
        "cache": _xai_models_cache,
        "min_expected_models": 2,
        "header_type": "bearer",
    },
    "nebius": {
        "name": "Nebius",
        "url": "https://api.studio.nebius.ai/v1/models",
        "api_key_env": "NEBIUS_API_KEY",
        "api_key": Config.NEBIUS_API_KEY,
        "cache": _nebius_models_cache,
        "min_expected_models": 5,
        "header_type": "bearer",
    },
    "novita": {
        "name": "Novita",
        "url": "https://api.novita.ai/v3/openai/models",
        "api_key_env": "NOVITA_API_KEY",
        "api_key": Config.NOVITA_API_KEY,
        "cache": _novita_models_cache,
        "min_expected_models": 5,
        "header_type": "bearer",
    },
    "huggingface": {
        "name": "Hugging Face",
        "url": "https://huggingface.co/api/models",
        "api_key_env": "HUG_API_KEY",
        "api_key": Config.HUG_API_KEY,
        "cache": _huggingface_models_cache,
        "min_expected_models": 100,
        "header_type": "bearer",
    },
    "aimo": {
        "name": "AIMO",
        "url": "https://beta.aimo.network/api/v1/models",
        "api_key_env": "AIMO_API_KEY",
        "api_key": getattr(Config, "AIMO_API_KEY", None),
        "cache": _aimo_models_cache,
        "min_expected_models": 5,
        "header_type": "bearer",
    },
    "near": {
        "name": "NEAR",
        "url": "https://cloud-api.near.ai/v1/models",
        "api_key_env": "NEAR_API_KEY",
        "api_key": Config.NEAR_API_KEY,
        "cache": _near_models_cache,
        "min_expected_models": 4,
        "header_type": "bearer",
    },
    "fal": {
        "name": "Fal.ai",
        "url": None,  # Fal uses static catalog, no direct API endpoint
        "api_key_env": "FAL_KEY",
        "api_key": getattr(Config, "FAL_KEY", "static_catalog"),
        "cache": _fal_models_cache,
        "min_expected_models": 50,
        "header_type": "bearer",
    },
    "aihubmix": {
        "name": "AiHubMix",
        "url": "https://aihubmix.com/v1/models",
        "api_key_env": "AIHUBMIX_API_KEY",
        "api_key": Config.AIHUBMIX_API_KEY,
        "cache": _aihubmix_models_cache,
        "min_expected_models": 5,
        "header_type": "aihubmix",
    },
    "anannas": {
        "name": "Anannas",
        "url": "https://api.anannas.ai/v1/models",
        "api_key_env": "ANANNAS_API_KEY",
        "api_key": Config.ANANNAS_API_KEY,
        "cache": _anannas_models_cache,
        "min_expected_models": 5,
        "header_type": "bearer",
    },
    "onerouter": {
        "name": "Infron AI",
        "url": "https://api.infron.ai/v1/models",
        "api_key_env": "ONEROUTER_API_KEY",
        "api_key": Config.ONEROUTER_API_KEY,
        "cache": _onerouter_models_cache,
        "min_expected_models": 100,
        "header_type": "bearer",
    },
    "google-vertex": {
        "name": "Google Vertex AI",
        "url": None,  # Google Vertex uses service account, not REST endpoint
        "api_key_env": "GOOGLE_APPLICATION_CREDENTIALS",
        "api_key": getattr(Config, "GOOGLE_APPLICATION_CREDENTIALS", None),
        "cache": _google_vertex_models_cache,
        "min_expected_models": 10,
        "header_type": "google",
    },
    "cloudflare-workers-ai": {
        "name": "Cloudflare Workers AI",
        "url": None,  # Cloudflare uses account-specific URLs
        "api_key_env": "CLOUDFLARE_API_TOKEN",
        "api_key": getattr(Config, "CLOUDFLARE_API_TOKEN", None),
        "cache": _cloudflare_workers_ai_models_cache,
        "min_expected_models": 10,
        "header_type": "bearer",
    },
    "vercel-ai-gateway": {
        "name": "Vercel AI Gateway",
        "url": None,  # Vercel AI Gateway uses proxy, not direct endpoint
        "api_key_env": "VERCEL_API_TOKEN",
        "api_key": getattr(Config, "VERCEL_API_TOKEN", None),
        "cache": _vercel_ai_gateway_models_cache,
        "min_expected_models": 5,
        "header_type": "bearer",
    },
    "helicone": {
        "name": "Helicone",
        "url": None,  # Helicone is a proxy/observability layer
        "api_key_env": "HELICONE_API_KEY",
        "api_key": getattr(Config, "HELICONE_API_KEY", None),
        "cache": _helicone_models_cache,
        "min_expected_models": 5,
        "header_type": "bearer",
    },
    "openai": {
        "name": "OpenAI",
        "url": "https://api.openai.com/v1/models",
        "api_key_env": "OPENAI_API_KEY",
        "api_key": getattr(Config, "OPENAI_API_KEY", None),
        "cache": _openai_models_cache,
        "min_expected_models": 10,
        "header_type": "bearer",
    },
    "anthropic": {
        "name": "Anthropic",
        "url": None,  # Anthropic doesn't have /models endpoint
        "api_key_env": "ANTHROPIC_API_KEY",
        "api_key": getattr(Config, "ANTHROPIC_API_KEY", None),
        "cache": _anthropic_models_cache,
        "min_expected_models": 3,
        "header_type": "bearer",
    },
    "clarifai": {
        "name": "Clarifai",
        "url": None,  # Clarifai uses custom API
        "api_key_env": "CLARIFAI_API_KEY",
        "api_key": getattr(Config, "CLARIFAI_API_KEY", None),
        "cache": _clarifai_models_cache,
        "min_expected_models": 5,
        "header_type": "bearer",
    },
    "alibaba": {
        "name": "Alibaba Cloud",
        "url": None,  # Alibaba uses Dashscope API
        "api_key_env": "ALIBABA_API_KEY",
        "api_key": getattr(Config, "ALIBABA_API_KEY", None),
        "cache": _alibaba_models_cache,
        "min_expected_models": 5,
        "header_type": "bearer",
    },
    "simplismart": {
        "name": "SimpliSmart",
        "url": "https://api.simplismart.ai/v1/models",
        "api_key_env": "SIMPLISMART_API_KEY",
        "api_key": getattr(Config, "SIMPLISMART_API_KEY", None),
        "cache": _simplismart_models_cache,
        "min_expected_models": 5,
        "header_type": "bearer",
    },
    "modelz": {
        "name": "Modelz",
        "url": None,  # Modelz uses custom deployment API
        "api_key_env": "MODELZ_API_KEY",
        "api_key": getattr(Config, "MODELZ_API_KEY", None),
        "cache": _modelz_cache,
        "min_expected_models": 3,
        "header_type": "bearer",
    },
    "canopywave": {
        "name": "Canopy Wave",
        "url": "https://inference.canopywave.io/v1/models",
        "api_key_env": "CANOPYWAVE_API_KEY",
        "api_key": getattr(Config, "CANOPYWAVE_API_KEY", None),
        "cache": _canopywave_models_cache,
        "min_expected_models": 5,
        "header_type": "bearer",
    },
    "sybil": {
        "name": "Sybil",
        "url": None,  # Sybil uses static catalog
        "api_key_env": "SYBIL_API_KEY",
        "api_key": getattr(Config, "SYBIL_API_KEY", None),
        "cache": _sybil_models_cache,
        "min_expected_models": 3,
        "header_type": "bearer",
    },
    "morpheus": {
        "name": "Morpheus",
        "url": None,  # Morpheus uses custom API
        "api_key_env": "MORPHEUS_API_KEY",
        "api_key": getattr(Config, "MORPHEUS_API_KEY", None),
        "cache": _morpheus_models_cache,
        "min_expected_models": 3,
        "header_type": "bearer",
    },
}


def build_headers(gateway_config: dict[str, Any]) -> dict[str, str]:
    """Build authentication headers based on gateway type"""
    api_key = gateway_config.get("api_key")
    if not api_key:
        return {}

    header_type = gateway_config.get("header_type", "bearer")

    if header_type == "bearer":
        return {"Authorization": f"Bearer {api_key}"}
    elif header_type == "google":
        # Google uses API key as query parameter, not header
        return {}
    elif header_type == "aihubmix":
        # AiHubMix uses Authorization header and APP-Code for referral code
        headers = {"Authorization": f"Bearer {api_key}"}
        app_code = os.environ.get("AIHUBMIX_APP_CODE")
        if app_code:
            headers["APP-Code"] = app_code
        return headers
    else:
        return {}


async def test_gateway_endpoint(gateway_name: str, config: dict[str, Any]) -> tuple[bool, str, int]:
    """
    Test a gateway endpoint directly via HTTP (async)

    Returns:
        (success: bool, message: str, model_count: int)
    """
    try:
        url = config["url"]

        # Skip if URL is None (cache-only gateways)
        if url is None:
            return False, "No direct endpoint (cache-only gateway)", 0

        if not config["api_key"]:
            return False, f"API key not configured ({config['api_key_env']})", 0

        headers = build_headers(config)

        # Google uses API key as query parameter
        if config.get("header_type") == "google":
            url = f"{url}?key={config['api_key']}"

        # Make async HTTP request with timeout
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=30.0)

        if response.status_code != 200:
            return False, f"HTTP {response.status_code}: {response.text[:100]}", 0

        # Parse response
        data = response.json()

        # Extract model count (different APIs have different structures)
        if isinstance(data, list):
            model_count = len(data)
        elif isinstance(data, dict) and "data" in data:
            model_count = len(data.get("data", []))
        elif isinstance(data, dict) and "models" in data:
            # Google API uses 'models' key
            model_count = len(data.get("models", []))
        else:
            model_count = 0

        if model_count == 0:
            return False, "API returned 0 models", 0

        return True, f"OK - {model_count} models available", model_count

    except httpx.TimeoutException:
        return False, "Request timeout (30s)", 0
    except httpx.HTTPError as e:
        return False, f"HTTP error: {str(e)[:100]}", 0
    except Exception as e:
        return False, f"Error: {str(e)[:100]}", 0


def test_gateway_cache(gateway_name: str, config: dict[str, Any]) -> tuple[bool, str, int, list]:
    """
    Test gateway using cached models from the application

    Returns:
        (success: bool, message: str, model_count: int, models: List)
    """
    try:
        cache = config.get("cache")
        if not cache:
            return False, "No cache configured", 0, []

        # Check cache data
        cached_models = cache.get("data")
        cache_timestamp = cache.get("timestamp")

        if not cached_models:
            return False, "Cache is empty", 0, []

        model_count = len(cached_models) if isinstance(cached_models, list) else 0

        if model_count == 0:
            return False, "Cache has 0 models", 0, []

        # Check cache age
        if cache_timestamp:
            cache_age = (datetime.now(UTC) - cache_timestamp).total_seconds()
            age_hours = cache_age / 3600
            age_str = f"{age_hours:.1f}h old" if age_hours >= 1 else f"{cache_age:.0f}s old"
        else:
            age_str = "unknown age"

        # Check if model count meets minimum threshold
        min_expected = config.get("min_expected_models", 1)
        if model_count < min_expected:
            return (
                False,
                f"Only {model_count} models (expected ≥{min_expected}), {age_str}",
                model_count,
                cached_models,
            )

        return True, f"{model_count} models cached, {age_str}", model_count, cached_models

    except Exception as e:
        return False, f"Cache check error: {str(e)[:100]}", 0, []


def clear_gateway_cache(gateway_name: str, config: dict[str, Any]) -> bool:
    """Clear the cache for a gateway to force refresh"""
    try:
        cache = config.get("cache")
        if cache:
            cache["data"] = None
            cache["timestamp"] = None
            logger.info(f"Cleared cache for {gateway_name}")
            return True
        return False
    except Exception as e:
        logger.error(f"Failed to clear cache for {gateway_name}: {e}")
        return False


async def check_single_gateway(
    gateway_name: str, config: dict[str, Any], auto_fix: bool = True, verbose: bool = False
) -> dict[str, Any]:
    """
    Check a single gateway (async)

    Returns gateway result dictionary
    """
    gateway_display_name = config["name"]

    if verbose:
        logger.info(f"Testing: {gateway_display_name} ({gateway_name})")

    gateway_result = {
        "name": gateway_display_name,
        "configured": bool(config["api_key"]),
        "endpoint_test": {},
        "cache_test": {},
        "auto_fix_attempted": False,
        "auto_fix_successful": False,
        "final_status": "unknown",
    }

    # Check if API key is configured
    if not config["api_key"]:
        if verbose:
            logger.warning(f"API key not configured: {config['api_key_env']}")
        gateway_result["final_status"] = "unconfigured"
        return gateway_result

    # Track health check start time for metrics
    check_start_time = time.time()

    # Test 1: Direct endpoint test (async)
    endpoint_success, endpoint_msg, endpoint_count = await test_gateway_endpoint(
        gateway_name, config
    )
    gateway_result["endpoint_test"] = {
        "success": endpoint_success,
        "message": endpoint_msg,
        "model_count": endpoint_count,
    }

    # Record zero-model event if endpoint returned 0 models
    if endpoint_count == 0 and config.get("url"):  # Only for gateways with API endpoints
        if "timeout" in endpoint_msg.lower():
            record_zero_model_event(gateway_name, "timeout")
        elif "error" in endpoint_msg.lower():
            record_zero_model_event(gateway_name, "error")
        else:
            record_zero_model_event(gateway_name, "api_empty")

    if verbose:
        status_icon = "✅" if endpoint_success else "❌"
        logger.info(f"  Endpoint: {status_icon} {endpoint_msg}")

    # Test 2: Cache test (sync, but fast)
    cache_success, cache_msg, cache_count, cached_models = test_gateway_cache(gateway_name, config)
    gateway_result["cache_test"] = {
        "success": cache_success,
        "message": cache_msg,
        "model_count": cache_count,
        "models": cached_models,
    }

    # Update model count gauge
    set_gateway_model_count(gateway_name, cache_count)

    # Record cache-related zero-model events
    if cache_count == 0:
        record_zero_model_event(gateway_name, "cache_empty")
    elif not cache_success and "expected" in cache_msg.lower():
        record_zero_model_event(gateway_name, "below_threshold")

    if verbose:
        status_icon = "✅" if cache_success else "❌"
        logger.info(f"  Cache: {status_icon} {cache_msg}")

    # Determine if gateway is healthy
    is_healthy = endpoint_success or cache_success

    # Track if this is a recovery (was unhealthy, now healthy)
    # This would need state tracking across checks, simplified here
    was_previously_unhealthy = gateway_result.get("_previous_status") == "unhealthy"  # noqa: F841

    # Auto-fix if needed and enabled
    if not is_healthy and auto_fix:
        if verbose:
            logger.info(f"  Attempting auto-fix for {gateway_name}...")
        gateway_result["auto_fix_attempted"] = True

        # Clear cache to force refresh
        if clear_gateway_cache(gateway_name, config):
            # Re-test cache after clearing
            cache_success_retry, cache_msg_retry, cache_count_retry, _ = test_gateway_cache(
                gateway_name, config
            )
            if cache_success_retry:
                gateway_result["auto_fix_successful"] = True
                is_healthy = True
                record_auto_fix_attempt(gateway_name, success=True)
                record_gateway_recovery(gateway_name)
                if verbose:
                    logger.info("  ✅ Auto-fix successful")
            else:
                record_auto_fix_attempt(gateway_name, success=False)
                # Record fallback activation when auto-fix fails
                record_fallback_activation(gateway_name, "database")

    # Set final status
    gateway_result["final_status"] = "healthy" if is_healthy else "unhealthy"

    # Track health check duration and status
    check_duration = time.time() - check_start_time
    track_gateway_health_check(gateway_name, gateway_result["final_status"], check_duration)

    return gateway_result


async def run_comprehensive_check(
    auto_fix: bool = True, verbose: bool = False, gateway: str | None = None
) -> dict[str, Any]:
    """
    Run comprehensive check on all gateways (async, parallel execution)

    Args:
        auto_fix: Whether to attempt automatic fixes for failing gateways
        verbose: Whether to log detailed output
        gateway: Optional specific gateway to check

    Returns:
        Dictionary with test results
    """
    if gateway:
        gateway_key = gateway.lower()
        if gateway_key not in GATEWAY_CONFIG:
            raise ValueError(
                f"Unknown gateway: {gateway}. Available: {', '.join(GATEWAY_CONFIG.keys())}"
            )
        gateways_to_check = {gateway_key: GATEWAY_CONFIG[gateway_key]}
    else:
        gateways_to_check = GATEWAY_CONFIG

    results = {
        "timestamp": datetime.now(UTC).isoformat(),
        "total_gateways": len(gateways_to_check),
        "healthy": 0,
        "unhealthy": 0,
        "fixed": 0,
        "unconfigured": 0,
        "gateways": {},
    }

    # Run all gateway checks in parallel
    tasks = []
    gateway_names = []
    for gateway_name, config in gateways_to_check.items():
        tasks.append(check_single_gateway(gateway_name, config, auto_fix, verbose))
        gateway_names.append(gateway_name)

    # Wait for all checks to complete with a timeout
    try:
        gateway_results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True), timeout=60.0
        )
    except TimeoutError:
        logger.error("Gateway health check timed out after 60 seconds")
        # Return partial results
        gateway_results = [{"final_status": "timeout", "name": "Timeout"} for _ in gateway_names]

    # Process results
    for gateway_name, gateway_result in zip(gateway_names, gateway_results, strict=False):
        if isinstance(gateway_result, Exception):
            logger.error(f"Error checking {gateway_name}: {gateway_result}")
            gateway_result = {
                "name": GATEWAY_CONFIG[gateway_name]["name"],
                "final_status": "error",
                "error": str(gateway_result),
            }

        results["gateways"][gateway_name] = gateway_result

        # Update counters
        status = gateway_result.get("final_status", "unknown")
        if status == "healthy":
            results["healthy"] += 1
            if gateway_result.get("auto_fix_successful"):
                results["fixed"] += 1
        elif status == "unhealthy":
            results["unhealthy"] += 1
        elif status == "unconfigured":
            results["unconfigured"] += 1

    return results


# Stub functions for backward compatibility
# TODO: Implement proper gateway error tracking if needed
def set_gateway_error(gateway_name: str, error_message: str) -> None:
    """
    Set error state for a gateway (stub for backward compatibility).

    Args:
        gateway_name: Name of the gateway
        error_message: Error message to log
    """
    logger.warning(f"Gateway {gateway_name} error: {error_message}")


def clear_gateway_error(gateway_name: str) -> None:
    """
    Clear error state for a gateway (stub for backward compatibility).

    Args:
        gateway_name: Name of the gateway
    """
    logger.debug(f"Gateway {gateway_name} error cleared")
