import asyncio
import importlib
import json
import logging
import secrets
import time
import uuid
from contextvars import ContextVar
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

import src.db.activity as activity_module
import src.db.api_keys as api_keys_module
import src.db.chat_history as chat_history_module
import src.db.plans as plans_module
import src.db.rate_limits as rate_limits_module
import src.db.users as users_module
from src.config import Config
from src.db.chat_completion_requests_enhanced import save_chat_completion_request_with_cost
from src.schemas import ProxyRequest, ResponseRequest
from src.security.deps import get_api_key, get_optional_api_key
from src.services.passive_health_monitor import capture_model_health
from src.services.prometheus_metrics import (
    credits_used,
    model_inference_duration,
    model_inference_requests,
    record_free_model_usage,
    tokens_used,
    track_time_to_first_chunk,
)
from src.services.redis_metrics import get_redis_metrics
from src.services.stream_normalizer import (
    StreamNormalizer,
    create_done_sse,
    create_error_sse_chunk,
)
from src.utils.exceptions import APIExceptions
from src.utils.performance_tracker import PerformanceTracker
from src.utils.rate_limit_headers import get_rate_limit_headers
from src.utils.sentry_context import capture_provider_error
from src.services.anonymous_rate_limiter import (
    validate_anonymous_request,
    record_anonymous_request,
    ANONYMOUS_ALLOWED_MODELS,
)
from src.utils.ai_tracing import AITracer, AIRequestType

# Optional Traceloop integration - gracefully handle if not installed
try:
    from src.config.traceloop_config import set_association_properties as set_traceloop_properties
except ImportError:
    # Traceloop not available - provide no-op function
    def set_traceloop_properties(**kwargs):
        pass


from src.services.connection_pool import get_butter_pooled_async_client

# Unified chat handler and adapters for chat unification
from src.handlers.chat_handler import ChatInferenceHandler
from src.adapters.chat import OpenAIChatAdapter

# Request correlation ID for distributed tracing
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
# Braintrust tracing - use centralized service for proper project association
# The key fix is using logger.start_span() instead of standalone start_span()
try:
    from src.services.braintrust_service import (
        create_span,
        flush as braintrust_flush,
        is_available as check_braintrust_available,
        NoopSpan,
    )

    # Import traced decorator from braintrust SDK for route decoration
    from braintrust import traced

    # Wrapper to maintain backward compatibility with existing code
    def start_span(name=None, span_type=None, **kwargs):
        """Create a span using the centralized service."""
        return create_span(name=name, span_type=span_type or "llm", **kwargs)

    BRAINTRUST_AVAILABLE = True
except ImportError:
    BRAINTRUST_AVAILABLE = False

    def check_braintrust_available():
        return False

    # Create no-op decorators and functions when braintrust is not available
    def traced(name=None, type=None):
        def decorator(func):
            return func

        return decorator

    class NoopSpan:
        def log(self, *args, **kwargs):
            pass

        def end(self):
            pass

    # Alias for backward compatibility
    MockSpan = NoopSpan

    def start_span(name=None, span_type=None, **kwargs):
        return NoopSpan()

    def current_span():
        return NoopSpan()

    def braintrust_flush():
        pass


# Import provider clients with graceful error handling
# This prevents a single provider's import failure from breaking the entire chat endpoint
_provider_import_errors = {}


# Helper function to safely import provider clients
def _safe_import_provider(provider_name, imports_list):
    """Safely import provider functions with error logging

    Returns a dict with either:
    - Real functions if import succeeds
    - Sentinel functions that raise HTTPException if used
    """
    try:
        module_path = f"src.services.{provider_name}_client"
        module = __import__(module_path, fromlist=imports_list)
        result = {}
        for import_name in imports_list:
            result[import_name] = getattr(module, import_name)
        logging.getLogger(__name__).debug(f"✓ Loaded {provider_name} provider client")
        return result
    except Exception as e:
        error_msg = (
            f"⚠  Failed to load {provider_name} provider client: {type(e).__name__}: {str(e)}"
        )
        logging.getLogger(__name__).error(error_msg)
        _provider_import_errors[provider_name] = str(e)

        # Return sentinel functions that raise informative errors when called
        def make_error_raiser(prov_name, func_name, error):
            async def async_error(*args, **kwargs):
                raise HTTPException(
                    status_code=503,
                    detail=f"Provider '{prov_name}' is unavailable: {func_name} failed to load. Error: {str(error)[:100]}",
                )

            def sync_error(*args, **kwargs):
                raise HTTPException(
                    status_code=503,
                    detail=f"Provider '{prov_name}' is unavailable: {func_name} failed to load. Error: {str(error)[:100]}",
                )

            # Return the sync version by default (async handling is done elsewhere)
            return sync_error

        return {
            import_name: make_error_raiser(provider_name, import_name, e)
            for import_name in imports_list
        }


# Load all provider clients using registry pattern
# Define provider functions to import (reduces boilerplate from ~280 lines to ~60 lines)
PROVIDER_FUNCTIONS = {
    "openrouter": [
        "make_openrouter_request_openai",
        "process_openrouter_response",
        "make_openrouter_request_openai_stream",
        "make_openrouter_request_openai_stream_async",
    ],
    "featherless": [
        "make_featherless_request_openai",
        "process_featherless_response",
        "make_featherless_request_openai_stream",
    ],
    "fireworks": [
        "make_fireworks_request_openai",
        "process_fireworks_response",
        "make_fireworks_request_openai_stream",
    ],
    "together": [
        "make_together_request_openai",
        "process_together_response",
        "make_together_request_openai_stream",
    ],
    "huggingface": [
        "make_huggingface_request_openai",
        "process_huggingface_response",
        "make_huggingface_request_openai_stream",
    ],
    "aimo": [
        "make_aimo_request_openai",
        "process_aimo_response",
        "make_aimo_request_openai_stream",
    ],
    "xai": ["make_xai_request_openai", "process_xai_response", "make_xai_request_openai_stream"],
    "cerebras": [
        "make_cerebras_request_openai",
        "process_cerebras_response",
        "make_cerebras_request_openai_stream",
    ],
    "chutes": [
        "make_chutes_request_openai",
        "process_chutes_response",
        "make_chutes_request_openai_stream",
    ],
    "google_vertex": [
        "make_google_vertex_request_openai",
        "process_google_vertex_response",
        "make_google_vertex_request_openai_stream",
    ],
    "near": [
        "make_near_request_openai",
        "process_near_response",
        "make_near_request_openai_stream",
    ],
    "vercel_ai_gateway": [
        "make_vercel_ai_gateway_request_openai",
        "process_vercel_ai_gateway_response",
        "make_vercel_ai_gateway_request_openai_stream",
    ],
    "helicone": [
        "make_helicone_request_openai",
        "process_helicone_response",
        "make_helicone_request_openai_stream",
    ],
    "aihubmix": [
        "make_aihubmix_request_openai",
        "process_aihubmix_response",
        "make_aihubmix_request_openai_stream",
    ],
    "anannas": [
        "make_anannas_request_openai",
        "process_anannas_response",
        "make_anannas_request_openai_stream",
    ],
    "alpaca_network": [
        "make_alpaca_network_request_openai",
        "process_alpaca_network_response",
        "make_alpaca_network_request_openai_stream",
    ],
    "alibaba_cloud": [
        "make_alibaba_cloud_request_openai",
        "process_alibaba_cloud_response",
        "make_alibaba_cloud_request_openai_stream",
    ],
    "clarifai": [
        "make_clarifai_request_openai",
        "process_clarifai_response",
        "make_clarifai_request_openai_stream",
    ],
    "groq": [
        "make_groq_request_openai",
        "process_groq_response",
        "make_groq_request_openai_stream",
    ],
    "cloudflare_workers_ai": [
        "make_cloudflare_workers_ai_request_openai",
        "process_cloudflare_workers_ai_response",
        "make_cloudflare_workers_ai_request_openai_stream",
    ],
    "morpheus": [
        "make_morpheus_request_openai",
        "process_morpheus_response",
        "make_morpheus_request_openai_stream",
    ],
    "onerouter": [
        "make_onerouter_request_openai",
        "process_onerouter_response",
        "make_onerouter_request_openai_stream",
    ],
    "simplismart": [
        "make_simplismart_request_openai",
        "process_simplismart_response",
        "make_simplismart_request_openai_stream",
    ],
    "sybil": [
        "make_sybil_request_openai",
        "process_sybil_response",
        "make_sybil_request_openai_stream",
    ],
    "nosana": [
        "make_nosana_request_openai",
        "process_nosana_response",
        "make_nosana_request_openai_stream",
    ],
    "zai": [
        "make_zai_request_openai",
        "process_zai_response",
        "make_zai_request_openai_stream",
    ],
}

# Load all providers and expose functions to global namespace
_current_globals = globals()
for provider_name, function_names in PROVIDER_FUNCTIONS.items():
    provider_module = _safe_import_provider(provider_name, function_names)
    for func_name in function_names:
        _current_globals[func_name] = provider_module.get(func_name)

# Provider routing registry - maps provider names (with hyphens) to their functions
# This eliminates the need for massive if-elif chains (~750 lines reduced to ~50 lines)
PROVIDER_ROUTING = {
    "featherless": {
        "request": make_featherless_request_openai,
        "process": process_featherless_response,
        "stream": make_featherless_request_openai_stream,
    },
    "fireworks": {
        "request": make_fireworks_request_openai,
        "process": process_fireworks_response,
        "stream": make_fireworks_request_openai_stream,
    },
    "together": {
        "request": make_together_request_openai,
        "process": process_together_response,
        "stream": make_together_request_openai_stream,
    },
    "huggingface": {
        "request": make_huggingface_request_openai,
        "process": process_huggingface_response,
        "stream": make_huggingface_request_openai_stream,
    },
    "aimo": {
        "request": make_aimo_request_openai,
        "process": process_aimo_response,
        "stream": make_aimo_request_openai_stream,
    },
    "xai": {
        "request": make_xai_request_openai,
        "process": process_xai_response,
        "stream": make_xai_request_openai_stream,
    },
    "cerebras": {
        "request": make_cerebras_request_openai,
        "process": process_cerebras_response,
        "stream": make_cerebras_request_openai_stream,
    },
    "chutes": {
        "request": make_chutes_request_openai,
        "process": process_chutes_response,
        "stream": make_chutes_request_openai_stream,
    },
    "near": {
        "request": make_near_request_openai,
        "process": process_near_response,
        "stream": make_near_request_openai_stream,
    },
    "google-vertex": {
        "request": make_google_vertex_request_openai,
        "process": process_google_vertex_response,
        "stream": make_google_vertex_request_openai_stream,
    },
    "vercel-ai-gateway": {
        "request": make_vercel_ai_gateway_request_openai,
        "process": process_vercel_ai_gateway_response,
        "stream": make_vercel_ai_gateway_request_openai_stream,
    },
    "helicone": {
        "request": make_helicone_request_openai,
        "process": process_helicone_response,
        "stream": make_helicone_request_openai_stream,
    },
    "aihubmix": {
        "request": make_aihubmix_request_openai,
        "process": process_aihubmix_response,
        "stream": make_aihubmix_request_openai_stream,
    },
    "anannas": {
        "request": make_anannas_request_openai,
        "process": process_anannas_response,
        "stream": make_anannas_request_openai_stream,
    },
    "alpaca-network": {
        "request": make_alpaca_network_request_openai,
        "process": process_alpaca_network_response,
        "stream": make_alpaca_network_request_openai_stream,
    },
    "alibaba-cloud": {
        "request": make_alibaba_cloud_request_openai,
        "process": process_alibaba_cloud_response,
        "stream": make_alibaba_cloud_request_openai_stream,
    },
    "clarifai": {
        "request": make_clarifai_request_openai,
        "process": process_clarifai_response,
        "stream": make_clarifai_request_openai_stream,
    },
    "groq": {
        "request": make_groq_request_openai,
        "process": process_groq_response,
        "stream": make_groq_request_openai_stream,
    },
    "cloudflare-workers-ai": {
        "request": make_cloudflare_workers_ai_request_openai,
        "process": process_cloudflare_workers_ai_response,
        "stream": make_cloudflare_workers_ai_request_openai_stream,
    },
    "morpheus": {
        "request": make_morpheus_request_openai,
        "process": process_morpheus_response,
        "stream": make_morpheus_request_openai_stream,
    },
    "onerouter": {
        "request": make_onerouter_request_openai,
        "process": process_onerouter_response,
        "stream": make_onerouter_request_openai_stream,
    },
    "simplismart": {
        "request": make_simplismart_request_openai,
        "process": process_simplismart_response,
        "stream": make_simplismart_request_openai_stream,
    },
    "sybil": {
        "request": make_sybil_request_openai,
        "process": process_sybil_response,
        "stream": make_sybil_request_openai_stream,
    },
    "nosana": {
        "request": make_nosana_request_openai,
        "process": process_nosana_response,
        "stream": make_nosana_request_openai_stream,
    },
    "zai": {
        "request": make_zai_request_openai,
        "process": process_zai_response,
        "stream": make_zai_request_openai_stream,
    },
}

import src.services.rate_limiting as rate_limiting_service
import src.services.trial_validation as trial_module
from src.services.model_transformations import detect_provider_from_model_id, transform_model_id
from src.services.pricing import calculate_cost
from src.services.provider_failover import (
    build_provider_failover_chain,
    enforce_model_failover_rules,
    filter_by_circuit_breaker,
    map_provider_error,
    should_failover,
)
from src.utils.security_validators import sanitize_for_logging
from src.utils.token_estimator import estimate_message_tokens


# Backwards compatibility wrappers for test patches
def increment_api_key_usage(*args, **kwargs):
    return api_keys_module.increment_api_key_usage(*args, **kwargs)


def enforce_plan_limits(*args, **kwargs):
    return plans_module.enforce_plan_limits(*args, **kwargs)


def create_rate_limit_alert(*args, **kwargs):
    return rate_limits_module.create_rate_limit_alert(*args, **kwargs)


def update_rate_limit_usage(*args, **kwargs):
    return rate_limits_module.update_rate_limit_usage(*args, **kwargs)


def get_user(*args, **kwargs):
    return users_module.get_user(*args, **kwargs)


def deduct_credits(*args, **kwargs):
    return users_module.deduct_credits(*args, **kwargs)


def log_api_usage_transaction(*args, **kwargs):
    return users_module.log_api_usage_transaction(*args, **kwargs)


def record_usage(*args, **kwargs):
    return users_module.record_usage(*args, **kwargs)


def create_chat_session(*args, **kwargs):
    return chat_history_module.create_chat_session(*args, **kwargs)


def save_chat_message(*args, **kwargs):
    return chat_history_module.save_chat_message(*args, **kwargs)


def get_chat_session(*args, **kwargs):
    return chat_history_module.get_chat_session(*args, **kwargs)


def log_activity(*args, **kwargs):
    return activity_module.log_activity(*args, **kwargs)


def validate_and_adjust_max_tokens(optional: dict, model: str) -> None:
    """
    Validate and adjust max_tokens for models with minimum token requirements.

    Google Gemini models require max_tokens >= 16. This function automatically
    adjusts the value if it's below the minimum to prevent API errors.

    Args:
        optional: Dictionary of optional parameters (modified in-place)
        model: The model ID being used
    """
    if "max_tokens" not in optional or optional["max_tokens"] is None:
        return

    model_lower = model.lower()

    # Check if this is a Gemini model that requires min tokens >= 16
    if "gemini" in model_lower or "google" in model_lower:
        min_tokens = 16
        if optional["max_tokens"] < min_tokens:
            logger.warning(
                f"Adjusting max_tokens from {optional['max_tokens']} to {min_tokens} "
                f"for Gemini model {model} (minimum requirement)"
            )
            optional["max_tokens"] = min_tokens


def get_provider_from_model(*args, **kwargs):
    return activity_module.get_provider_from_model(*args, **kwargs)


def get_rate_limit_manager(*args, **kwargs):
    return rate_limiting_service.get_rate_limit_manager(*args, **kwargs)


def validate_trial_access(*args, **kwargs):
    return trial_module.validate_trial_access(*args, **kwargs)


def track_trial_usage(*args, **kwargs):
    return trial_module.track_trial_usage(*args, **kwargs)


logger = logging.getLogger(__name__)
router = APIRouter()

# Log module initialization to help debug route loading
logger.info("🔄 Chat module initialized - router created")
logger.info(f"   Router type: {type(router)}")

DEFAULT_PROVIDER_TIMEOUT = 30
PROVIDER_TIMEOUTS = {
    "huggingface": 120,
    "near": 120,  # Large models like Qwen3-30B need extended timeout
}

# Butter.dev provider configuration for caching proxy
# Maps provider names to their API key config attribute and base URL
BUTTER_PROVIDER_CONFIG = {
    "openrouter": {
        "api_key_attr": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
    },
    "featherless": {
        "api_key_attr": "FEATHERLESS_API_KEY",
        "base_url": "https://api.featherless.ai/v1",
    },
    "together": {
        "api_key_attr": "TOGETHER_API_KEY",
        "base_url": "https://api.together.xyz/v1",
    },
    "fireworks": {
        "api_key_attr": "FIREWORKS_API_KEY",
        "base_url": "https://api.fireworks.ai/inference/v1",
    },
    "groq": {
        "api_key_attr": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
    },
    "cerebras": {
        "api_key_attr": "CEREBRAS_API_KEY",
        "base_url": "https://api.cerebras.ai/v1",
    },
    "deepinfra": {
        "api_key_attr": "DEEPINFRA_API_KEY",
        "base_url": "https://api.deepinfra.com/v1/openai",
    },
    "xai": {
        "api_key_attr": "XAI_API_KEY",
        "base_url": "https://api.x.ai/v1",
    },
    "openai": {
        "api_key_attr": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
    },
    "huggingface": {
        "api_key_attr": "HF_API_KEY",
        "base_url": "https://api-inference.huggingface.co/v1",
    },
    "chutes": {
        "api_key_attr": "CHUTES_API_KEY",
        "base_url": "https://llm.chutes.ai/v1",
    },
    "onerouter": {
        "api_key_attr": "ONEROUTER_API_KEY",
        "base_url": "https://llm.infron.ai/v1",
    },
    "aihubmix": {
        "api_key_attr": "AIHUBMIX_API_KEY",
        "base_url": "https://aihubmix.com/v1",
    },
    "near": {
        "api_key_attr": "NEAR_API_KEY",
        "base_url": "https://cloud-api.near.ai/v1",
    },
    "morpheus": {
        "api_key_attr": "MORPHEUS_API_KEY",
        "base_url": "https://api.mor.org/api/v1",
    },
    "simplismart": {
        "api_key_attr": "SIMPLISMART_API_KEY",
        "base_url": "https://api.simplismart.live",
    },
    "sybil": {
        "api_key_attr": "SYBIL_API_KEY",
        "base_url": "https://api.sybil.com/v1",
    },
    "nosana": {
        "api_key_attr": "NOSANA_API_KEY",
        "base_url": "https://dashboard.k8s.prd.nos.ci/api/v1",
    },
    "akash": {
        "api_key_attr": "AKASH_API_KEY",
        "base_url": "https://api.akashml.com/v1",
    },
    "anannas": {
        "api_key_attr": "ANANNAS_API_KEY",
        "base_url": "https://api.anannas.ai/v1",
    },
    "helicone": {
        "api_key_attr": "HELICONE_API_KEY",
        "base_url": "https://ai-gateway.helicone.ai/v1",
    },
    "aimo": {
        "api_key_attr": "AIMO_API_KEY",
        "base_url": "https://beta.aimo.network/api/v1",
    },
}


async def make_butter_proxied_stream(
    messages: list,
    model: str,
    provider: str,
    **kwargs,
):
    """
    Make a streaming request through Butter.dev caching proxy.

    This routes the request through Butter.dev which can cache responses
    for identical prompts, reducing costs and latency.

    Args:
        messages: Chat messages
        model: Model name
        provider: Target provider (e.g., 'openrouter', 'together')
        **kwargs: Additional arguments (temperature, max_tokens, etc.)

    Returns:
        Async stream iterator

    Raises:
        ValueError: If provider is not configured for Butter
    """
    provider_config = BUTTER_PROVIDER_CONFIG.get(provider)
    if not provider_config:
        raise ValueError(f"Provider '{provider}' is not configured for Butter.dev caching")

    api_key = getattr(Config, provider_config["api_key_attr"], None)
    if not api_key:
        raise ValueError(f"API key not configured for provider '{provider}'")

    base_url = provider_config["base_url"]

    # Get the Butter-proxied async client
    client = get_butter_pooled_async_client(
        target_provider=provider,
        target_api_key=api_key,
        target_base_url=base_url,
    )

    logger.info(f"Butter.dev: Routing {provider}/{model} through cache proxy")

    # Build request parameters
    request_params = {
        "model": model,
        "messages": messages,
        "stream": True,
    }

    # Add optional parameters
    if kwargs.get("temperature") is not None:
        request_params["temperature"] = kwargs["temperature"]
    if kwargs.get("max_tokens") is not None:
        request_params["max_tokens"] = kwargs["max_tokens"]
    if kwargs.get("top_p") is not None:
        request_params["top_p"] = kwargs["top_p"]
    if kwargs.get("frequency_penalty") is not None:
        request_params["frequency_penalty"] = kwargs["frequency_penalty"]
    if kwargs.get("presence_penalty") is not None:
        request_params["presence_penalty"] = kwargs["presence_penalty"]
    if kwargs.get("stop") is not None:
        request_params["stop"] = kwargs["stop"]
    if kwargs.get("tools") is not None:
        request_params["tools"] = kwargs["tools"]
    if kwargs.get("tool_choice") is not None:
        request_params["tool_choice"] = kwargs["tool_choice"]

    # Make the streaming request
    stream = await client.chat.completions.create(**request_params)

    return stream


# Auto-routing constants
# Using "router" prefix to avoid confusion with OpenRouter's "openrouter/auto" model
AUTO_ROUTE_MODEL_PREFIX = "router"
AUTO_ROUTE_DEFAULT_MODEL = "openai/gpt-4o-mini"

# Code router constants
CODE_ROUTER_PREFIX = "router:code"
CODE_ROUTER_DEFAULT_MODEL = "zai/glm-4.7"  # Fallback model


def mask_key(k: str) -> str:
    return f"...{k[-4:]}" if k and len(k) >= 4 else "****"


def is_free_model(model_id: str) -> bool:
    """Check if the model is a free model (OpenRouter free models end with :free suffix).

    Args:
        model_id: The model identifier (e.g., "google/gemini-2.0-flash-exp:free")

    Returns:
        True if the model is free, False otherwise
    """
    if not model_id:
        return False
    return model_id.endswith(":free")


def validate_trial_with_free_model_bypass(
    trial: dict,
    model_id: str,
    request_id: str,
    api_key: str | None,
    logger_instance,
) -> dict:
    """Validate trial access with free model bypass logic.

    Allows expired trials and trials with exceeded limits to access free models.
    Records metrics and logs for free model access.

    Args:
        trial: Trial validation result from validate_trial_access()
        model_id: The requested model identifier
        request_id: Request correlation ID for logging
        api_key: The API key (will be masked in logs)
        logger_instance: Logger to use for logging

    Returns:
        Updated trial dict with is_valid=True if free model bypass applies

    Raises:
        HTTPException: If trial is invalid and model is not free
    """
    model_is_free = is_free_model(model_id)

    # IMPORTANT: Work with a copy to avoid mutating the cached trial dict.
    # The trial dict may be cached in _trial_cache and mutating it would corrupt
    # the cache, potentially allowing expired trials to access premium models.
    trial = trial.copy()

    if not trial.get("is_valid", False):
        if trial.get("is_trial") and trial.get("is_expired"):
            if model_is_free:
                # Allow expired trial to use free model - log and track this access
                logger_instance.info(
                    "Expired trial accessing free model (request_id=%s, api_key=%s, model=%s, trial_end_date=%s)",
                    request_id,
                    mask_key(api_key) if api_key else "unknown",
                    model_id,
                    trial.get("trial_end_date", "unknown"),
                    extra={"request_id": request_id, "free_model_bypass": True},
                )
                record_free_model_usage("expired_trial", model_id)
                # Mark trial as valid for this request (free model access)
                trial["is_valid"] = True
                trial["free_model_bypass"] = True
            else:
                raise HTTPException(
                    status_code=403,
                    detail=trial["error"],
                    headers={
                        "X-Trial-Expired": "true",
                        "X-Trial-End-Date": trial.get("trial_end_date", ""),
                    },
                )
        elif trial.get("is_trial"):
            # Trial limits exceeded (tokens/requests/credits)
            if model_is_free:
                # Allow trial with exceeded limits to use free model
                logger_instance.info(
                    "Trial with exceeded limits accessing free model (request_id=%s, api_key=%s, model=%s, error=%s)",
                    request_id,
                    mask_key(api_key) if api_key else "unknown",
                    model_id,
                    trial.get("error", "unknown"),
                    extra={"request_id": request_id, "free_model_bypass": True},
                )
                record_free_model_usage("active_trial", model_id)
                # Mark trial as valid for this request (free model access)
                trial["is_valid"] = True
                trial["free_model_bypass"] = True
            else:
                headers = {}
                for k in ("remaining_tokens", "remaining_requests", "remaining_credits"):
                    if k in trial:
                        headers[f"X-Trial-{k.replace('_', '-').title()}"] = str(trial[k])
                raise HTTPException(status_code=429, detail=trial["error"], headers=headers)
        else:
            raise APIExceptions.forbidden(detail=trial.get("error", "Access denied"))
    elif model_is_free:
        # Track free model usage for valid trials and paid users too
        if trial.get("is_trial"):
            record_free_model_usage("active_trial", model_id)
        else:
            record_free_model_usage("paid", model_id)

    return trial


async def _to_thread(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)


async def _ensure_plan_capacity(user_id: int, environment_tag: str) -> dict[str, Any]:
    """Run a lightweight plan-limit precheck before making upstream calls."""
    plan_check = await _to_thread(enforce_plan_limits, user_id, 0, environment_tag)
    if not plan_check.get("allowed", False):
        raise APIExceptions.plan_limit_exceeded(reason=plan_check.get("reason", "unknown"))
    return plan_check


def _fallback_get_user(api_key: str):
    try:
        supabase_module = importlib.import_module("src.config.supabase_config")
        client = supabase_module.get_supabase_client()
        result = client.table("users").select("*").eq("api_key", api_key).execute()
        if result.data:
            logging.getLogger(__name__).debug("Fallback user lookup succeeded for %s", api_key)
            return result.data[0]
        logging.getLogger(__name__).debug(
            "Fallback lookup found no data; table snapshot=%s",
            client.table("users").select("*").execute().data,
        )
    except Exception as exc:
        logging.getLogger(__name__).debug(
            "Fallback user lookup error for %s: %s", mask_key(api_key), exc
        )
        return None
    return None


async def _handle_credits_and_usage(
    api_key: str,
    user: dict,
    model: str,
    trial: dict,
    total_tokens: int,
    prompt_tokens: int,
    completion_tokens: int,
    elapsed_ms: int,
    is_streaming: bool = False,
) -> float:
    """
    Centralized credit/trial handling logic.

    This is a thin wrapper around the shared credit_handler module to maintain
    backward compatibility while ensuring consistent billing across all endpoints.

    Args:
        is_streaming: Whether this is a streaming request (affects retry behavior)

    Returns: cost (float)
    """
    from src.services.credit_handler import handle_credits_and_usage

    return await handle_credits_and_usage(
        api_key=api_key,
        user=user,
        model=model,
        trial=trial,
        total_tokens=total_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        elapsed_ms=elapsed_ms,
        endpoint="/v1/chat/completions",
        is_streaming=is_streaming,
    )


async def _handle_credits_and_usage_with_fallback(
    api_key: str,
    user: dict,
    model: str,
    trial: dict,
    total_tokens: int,
    prompt_tokens: int,
    completion_tokens: int,
    elapsed_ms: int,
) -> tuple[float, bool]:
    """
    Credit handling for streaming background tasks with fallback on failure.

    This wrapper is specifically designed for streaming requests where the response
    has already been sent to the client. It:
    1. Attempts credit deduction with full retry logic
    2. On failure, logs for reconciliation and returns (cost, False)
    3. Never raises - failures are tracked for manual reconciliation

    Returns: tuple[float, bool] - (cost, success)
    """
    from src.services.credit_handler import handle_credits_and_usage_with_fallback

    return await handle_credits_and_usage_with_fallback(
        api_key=api_key,
        user=user,
        model=model,
        trial=trial,
        total_tokens=total_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        elapsed_ms=elapsed_ms,
        endpoint="/v1/chat/completions",
        is_streaming=True,
    )


async def _record_inference_metrics_and_health(
    provider: str,
    model: str,
    elapsed_seconds: float,
    prompt_tokens: int,
    completion_tokens: int,
    cost: float,
    success: bool = True,
    error_message: str | None = None,
):
    """
    Record Prometheus metrics, Redis metrics, and passive health monitoring.

    This centralizes metrics recording for both streaming and non-streaming requests.
    """
    try:
        # Record Prometheus metrics
        status = "success" if success else "error"

        # Request count
        model_inference_requests.labels(provider=provider, model=model, status=status).inc()

        # Duration
        model_inference_duration.labels(provider=provider, model=model).observe(elapsed_seconds)

        # Token usage
        if prompt_tokens > 0:
            tokens_used.labels(provider=provider, model=model, token_type="input").inc(
                prompt_tokens
            )

        if completion_tokens > 0:
            tokens_used.labels(provider=provider, model=model, token_type="output").inc(
                completion_tokens
            )

        # Credits consumed
        if cost > 0:
            credits_used.labels(provider=provider, model=model).inc(cost)

        # Record Redis metrics (real-time dashboards)
        redis_metrics = get_redis_metrics()
        await redis_metrics.record_request(
            provider=provider,
            model=model,
            latency_ms=int(elapsed_seconds * 1000),
            success=success,
            cost=cost,
            tokens_input=prompt_tokens,
            tokens_output=completion_tokens,
            error_message=error_message,
        )

        # Passive health monitoring (background task)
        response_time_ms = int(elapsed_seconds * 1000)
        health_status = "success" if success else "error"

        # Create usage dict for health monitoring
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

        # Call passive health monitor in background (non-blocking)
        # Note: capture_model_health is already async, so we just create a task for it
        asyncio.create_task(
            capture_model_health(
                provider=provider,
                model=model,
                response_time_ms=response_time_ms,
                status=health_status,
                error_message=error_message,
                usage=usage,
            )
        )

        logger.debug(
            f"Recorded metrics for {provider}/{model}: "
            f"duration={elapsed_seconds:.3f}s, "
            f"tokens={prompt_tokens}+{completion_tokens}, "
            f"cost=${cost:.4f}, "
            f"status={status}"
        )

    except Exception as e:
        # Never let metrics recording break the main flow
        logger.warning(f"Failed to record inference metrics: {e}", exc_info=True)


async def _process_stream_completion_background(
    user,
    api_key,
    model,
    trial,
    environment_tag,
    session_id,
    messages,
    accumulated_content,
    prompt_tokens,
    completion_tokens,
    total_tokens,
    elapsed,
    provider,
    is_anonymous=False,
    request_id=None,
    client_ip=None,
    api_key_id=None,
):
    """
    Background task for post-stream processing (100-200ms faster [DONE] event!)

    This runs asynchronously after the stream completes, allowing the [DONE]
    event to be sent immediately without waiting for database operations.

    For anonymous users, only metrics recording is performed (no credits, usage tracking, or history).
    """
    try:
        # Add distributed tracing for streaming completion
        async with AITracer.trace_inference(
            provider=provider,
            model=model,
            request_type=AIRequestType.CHAT_COMPLETION,
            operation_name=f"stream_completion_{provider}_{model}",
        ) as trace_ctx:
            # Calculate cost for tracing
            cost = calculate_cost(model, prompt_tokens, completion_tokens)

            # Set token usage and cost on trace span
            trace_ctx.set_token_usage(
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                total_tokens=total_tokens,
            )
            trace_ctx.set_cost(cost)

            # Set response model (for streaming, use requested model as we don't capture response model)
            trace_ctx.set_response_model(
                response_model=model,
                finish_reason="stop",  # Streaming completed successfully
            )

            # Set user info if authenticated
            if not is_anonymous and user:
                trace_ctx.set_user_info(
                    user_id=str(user.get("id")),
                    tier="trial" if trial.get("is_trial") else "paid",
                )

            # Add streaming-specific event
            trace_ctx.add_event(
                "stream_completed",
                {
                    "content_length": len(accumulated_content),
                    "elapsed_seconds": elapsed,
                },
            )

            # Skip user-specific operations for anonymous requests
            if is_anonymous:
                logger.info("Skipping user-specific post-processing for anonymous request")

                # Record anonymous usage for rate limiting (IMPORTANT: prevents abuse)
                if client_ip:
                    try:
                        record_anonymous_request(client_ip, model)
                    except Exception as e:
                        logger.warning(f"Failed to record anonymous request: {e}")

                # Record Prometheus metrics and passive health monitoring (allowed for anonymous)
                cost = calculate_cost(model, prompt_tokens, completion_tokens)
                await _record_inference_metrics_and_health(
                    provider=provider,
                    model=model,
                    elapsed_seconds=elapsed,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost=cost,
                    success=True,
                    error_message=None,
                )
                # Capture health metrics (passive monitoring)
                try:
                    await capture_model_health(
                        provider=provider,
                        model=model,
                        response_time_ms=elapsed * 1000,
                        status="success",
                        usage={
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "total_tokens": total_tokens,
                        },
                    )
                except Exception as e:
                    logger.debug(f"Failed to capture health metric: {e}")
                return

            # Handle credits and usage (centralized helper with fallback for streaming)
            # Use the fallback handler which:
            # 1. Has built-in retry logic with exponential backoff
            # 2. Logs failures for reconciliation instead of crashing
            # 3. Records metrics for monitoring credit deduction reliability
            cost, credit_deduction_success = await _handle_credits_and_usage_with_fallback(
                api_key=api_key,
                user=user,
                model=model,
                trial=trial,
                total_tokens=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                elapsed_ms=int(elapsed * 1000),
            )

            if not credit_deduction_success:
                logger.warning(
                    f"Credit deduction failed for streaming request. "
                    f"User: {user.get('id')}, Model: {model}, Cost: ${cost:.6f}. "
                    f"Logged for reconciliation."
                )

            # Increment API key usage counter
            await _to_thread(increment_api_key_usage, api_key)

            # Record Prometheus metrics and passive health monitoring
            await _record_inference_metrics_and_health(
                provider=provider,
                model=model,
                elapsed_seconds=elapsed,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=cost,
                success=True,
                error_message=None,
            )

            # Log activity
            try:
                provider_name = get_provider_from_model(model)
                speed = total_tokens / elapsed if elapsed > 0 else 0
                await _to_thread(
                    log_activity,
                    user_id=user["id"],
                    model=model,
                    provider=provider_name,
                    tokens=total_tokens,
                    cost=cost,
                    speed=speed,
                    finish_reason="stop",
                    app="API",
                    metadata={
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "endpoint": "/v1/chat/completions",
                        "stream": True,
                        "session_id": session_id,
                        "gateway": provider,
                    },
                )
            except Exception as e:
                logger.error(
                    f"Failed to log activity for user {user['id']}, model {model}: {e}",
                    exc_info=True,
                )

            # Save chat history
            # Validate session_id before attempting to save
            if session_id:
                if session_id < -2147483648 or session_id > 2147483647:
                    logger.warning(
                        "Invalid session_id %s in streaming response: out of PostgreSQL integer range. Skipping history save.",
                        sanitize_for_logging(str(session_id)),
                    )
                    session_id = None

            if session_id:
                try:
                    session = await _to_thread(get_chat_session, session_id, user["id"])
                    if session:
                        last_user = None
                        for m in reversed(messages):
                            if m.get("role") == "user":
                                last_user = m
                                break
                        if last_user:
                            user_content = last_user.get("content", "")
                            if isinstance(user_content, list):
                                text_parts = []
                                for item in user_content:
                                    if isinstance(item, dict) and item.get("type") == "text":
                                        text_parts.append(item.get("text", ""))
                                user_content = (
                                    " ".join(text_parts) if text_parts else "[multimodal content]"
                                )

                            await _to_thread(
                                save_chat_message,
                                session_id,
                                "user",
                                user_content,
                                model,
                                0,
                                user["id"],
                            )

                        if accumulated_content:
                            await _to_thread(
                                save_chat_message,
                                session_id,
                                "assistant",
                                accumulated_content,
                                model,
                                total_tokens,
                                user["id"],
                            )
                except Exception as e:
                    logger.error(
                        f"Failed to save chat history for session {session_id}, user {user['id']}: {e}",
                        exc_info=True,
                    )

            # Capture health metrics (passive monitoring)
            try:
                await capture_model_health(
                    provider=provider,
                    model=model,
                    response_time_ms=elapsed * 1000,
                    status="success",
                    usage={
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                    },
                )
            except Exception as e:
                logger.debug(f"Failed to capture health metric: {e}")

            # Save chat completion request metadata to database with cost tracking
            if request_id:
                try:
                    # Calculate cost breakdown for analytics
                    from src.services.pricing import get_model_pricing

                    pricing_info = get_model_pricing(model)
                    input_cost = prompt_tokens * pricing_info.get("prompt", 0)
                    output_cost = completion_tokens * pricing_info.get("completion", 0)
                    total_cost = input_cost + output_cost

                    await _to_thread(
                        save_chat_completion_request_with_cost,
                        request_id=request_id,
                        model_name=model,
                        input_tokens=prompt_tokens,
                        output_tokens=completion_tokens,
                        processing_time_ms=int(elapsed * 1000),
                        cost_usd=total_cost,
                        input_cost_usd=input_cost,
                        output_cost_usd=output_cost,
                        pricing_source="calculated",
                        status="completed",
                        error_message=None,
                        user_id=user["id"] if user else None,
                        provider_name=provider,
                        model_id=None,
                        api_key_id=api_key_id,
                        is_anonymous=is_anonymous,
                    )
                except Exception as e:
                    logger.debug(f"Failed to save chat completion request: {e}")

    except Exception as e:
        logger.error(f"Background stream processing error: {e}", exc_info=True)


async def stream_generator(
    stream,
    user,
    api_key,
    model,
    trial,
    environment_tag,
    session_id,
    messages,
    rate_limit_mgr=None,
    provider="openrouter",
    tracker=None,
    is_anonymous=False,
    is_async_stream=False,  # PERF: Flag to indicate if stream is async
    request_id=None,
    api_key_id=None,
    client_ip=None,
):
    """Generate SSE stream from OpenAI stream response (OPTIMIZED: background post-processing)

    Args:
        is_async_stream: If True, stream is an async iterator and will be consumed with
                        `async for` instead of `for`. This prevents blocking the event
                        loop while waiting for chunks from slow AI providers.
    """
    accumulated_content = ""
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    start_time = time.monotonic()
    # NOTE: Previously had dead code: `rate_limit_mgr is not None and not trial.get("is_trial", False)`
    # This expression evaluated but was never assigned or used - removed as no-op
    streaming_ctx = None
    first_chunk_sent = False  # TTFC tracking
    ttfc_start = time.monotonic()  # TTFC tracking

    # Initialize normalizer
    normalizer = StreamNormalizer(provider=provider, model=model)

    try:
        # Track streaming duration if tracker is provided
        if tracker:
            streaming_ctx = tracker.streaming()
            streaming_ctx.__enter__()

        chunk_count = 0

        # PERF: Use async iteration for async streams to avoid blocking the event loop
        # This is critical for reducing perceived TTFC as it allows the server to handle
        # other requests while waiting for the AI provider to start streaming

        # Sentinel value to signal iterator exhaustion (PEP 479 compliance)
        # StopIteration cannot be raised into a Future, so we use a sentinel instead
        _STREAM_EXHAUSTED = object()

        def _safe_next(iterator):
            """Wrapper for next() that returns a sentinel instead of raising StopIteration.

            This is necessary because StopIteration cannot be raised into a Future
            (PEP 479), which causes issues when using asyncio.to_thread(next, iterator).
            """
            try:
                return next(iterator)
            except StopIteration:
                return _STREAM_EXHAUSTED

        async def iterate_stream():
            """Helper to support both sync and async iteration"""
            if is_async_stream:
                async for chunk in stream:
                    yield chunk
            else:
                # Use non-blocking iteration for sync streams to avoid blocking the event loop
                iterator = iter(stream)
                while True:
                    try:
                        # Run the blocking next() call in a thread using safe wrapper
                        # to avoid "StopIteration interacts badly with generators" error
                        chunk = await asyncio.to_thread(_safe_next, iterator)
                        if chunk is _STREAM_EXHAUSTED:
                            break
                        yield chunk
                    except Exception as e:
                        logger.error(f"Error during sync stream iteration: {e}")
                        raise e

        async for chunk in iterate_stream():
            chunk_count += 1

            # TTFC: Track time to first chunk for performance monitoring
            if not first_chunk_sent:
                ttfc = time.monotonic() - ttfc_start
                first_chunk_sent = True
                # Record TTFC metric
                track_time_to_first_chunk(provider=provider, model=model, ttfc=ttfc)
                # Log TTFC for debugging slow streams with enhanced context
                if ttfc > 2.0:
                    severity = "CRITICAL" if ttfc > 10.0 else "WARNING"
                    logger.warning(
                        f"⚠️ [TTFC {severity}] Slow first chunk: {ttfc:.2f}s for {provider}/{model} "
                        f"(threshold: 2.0s, timeout: {Config.GOOGLE_VERTEX_TIMEOUT if provider == 'google-vertex' else 'N/A'}s)"
                    )

                    # Sentry alerting for critical TTFC (>10s)
                    if ttfc > 10.0:
                        try:
                            import sentry_sdk

                            sentry_sdk.capture_message(
                                f"Critical TTFC: {ttfc:.2f}s for {provider}/{model}",
                                level="warning",
                                extras={
                                    "ttfc_seconds": ttfc,
                                    "provider": provider,
                                    "model": model,
                                    "threshold": 10.0,
                                    "severity": "CRITICAL",
                                    "timeout_config": Config.GOOGLE_VERTEX_TIMEOUT
                                    if provider == "google-vertex"
                                    else None,
                                },
                            )
                        except Exception as sentry_error:
                            logger.debug(f"Failed to send Sentry alert for TTFC: {sentry_error}")
                else:
                    logger.info(f"✓ [TTFC] First chunk in {ttfc:.2f}s for {provider}/{model}")

            logger.debug(f"[STREAM] Processing chunk {chunk_count} for model {model}")

            normalized_chunk = normalizer.normalize_chunk(chunk)

            # Check for usage in chunk (some providers send it in final chunk)
            if hasattr(chunk, "usage") and chunk.usage:
                prompt_tokens = chunk.usage.prompt_tokens
                completion_tokens = chunk.usage.completion_tokens
                total_tokens = chunk.usage.total_tokens

            if normalized_chunk:
                yield normalized_chunk.to_sse()
            else:
                logger.debug(f"[STREAM] Chunk {chunk_count} resulted in no normalized output")

        accumulated_content = normalizer.get_accumulated_content()
        logger.info(
            f"[STREAM] Stream completed with {chunk_count} chunks, accumulated content length: {len(accumulated_content)}"
        )

        # DEFENSIVE: Detect empty streams and log as error
        if chunk_count == 0:
            logger.error(
                f"[EMPTY STREAM] Provider {provider} returned zero chunks for model {model}. "
                f"This indicates a provider routing or model ID transformation issue."
            )
            yield create_error_sse_chunk(
                error_message=f"Provider returned empty stream for model {model}. Please try again or contact support.",
                error_type="empty_stream_error",
                provider=provider,
                model=model,
            )
            yield create_done_sse()
            return
        elif accumulated_content == "" and chunk_count > 0:
            logger.warning(
                f"[EMPTY CONTENT] Provider {provider} returned {chunk_count} chunks but no content for model {model}."
            )

        # If no usage was provided, estimate based on content
        # WARNING: This estimation may result in inaccurate billing!
        if total_tokens == 0:
            # Rough estimate: 1 token ≈ 4 characters
            completion_tokens = max(1, len(accumulated_content) // 4)

            # Calculate prompt tokens, handling both string and multimodal content
            prompt_chars = 0
            for m in messages:
                content = m.get("content", "")
                if isinstance(content, str):
                    prompt_chars += len(content)
                elif isinstance(content, list):
                    # For multimodal content, extract text parts
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            prompt_chars += len(item.get("text", ""))
            prompt_tokens = max(1, prompt_chars // 4)
            total_tokens = prompt_tokens + completion_tokens

            # Log warning about token estimation (potential billing inaccuracy)
            logger.warning(
                f"[TOKEN_ESTIMATION] Provider {provider} did not return usage data for model {model}. "
                f"Using character-based estimation: prompt_tokens={prompt_tokens}, "
                f"completion_tokens={completion_tokens}, total_tokens={total_tokens}. "
                f"Content length: {len(accumulated_content)} chars. "
                f"This may result in inaccurate billing."
            )

            # Track metric for monitoring
            try:
                from src.services.prometheus_metrics import get_or_create_metric, Counter

                token_estimation_counter = get_or_create_metric(
                    Counter,
                    "gatewayz_token_estimation_total",
                    "Count of requests where token usage was estimated (not provided by provider)",
                    ["provider", "model"],
                )
                token_estimation_counter.labels(provider=provider, model=model).inc()
            except Exception:
                pass  # Metrics not available

        elapsed = max(0.001, time.monotonic() - start_time)

        # OPTIMIZATION: Quick plan limit check (critical - must be synchronous)
        # Skip plan limit check for anonymous users (user is None)
        if not is_anonymous and user is not None:
            post_plan = await _to_thread(
                enforce_plan_limits, user["id"], total_tokens, environment_tag
            )
            if not post_plan.get("allowed", False):
                yield create_error_sse_chunk(
                    error_message=f"Plan limit exceeded: {post_plan.get('reason', 'unknown')}",
                    error_type="plan_limit_exceeded",
                )
                yield create_done_sse()
                return

        # OPTIMIZATION: Send [DONE] immediately, process credits/logging in background!
        # This makes the stream complete 100-200ms faster for the client
        yield create_done_sse()

        # Schedule background processing (non-blocking)
        asyncio.create_task(
            _process_stream_completion_background(
                user=user,
                api_key=api_key,
                model=model,
                trial=trial,
                environment_tag=environment_tag,
                session_id=session_id,
                messages=messages,
                accumulated_content=accumulated_content,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                elapsed=elapsed,
                provider=provider,
                is_anonymous=is_anonymous,
                request_id=request_id,
                client_ip=client_ip,
                api_key_id=api_key_id,
            )
        )

    except Exception as e:
        logger.error(f"Streaming error: {e}", exc_info=True)

        # Extract meaningful error message for the client
        error_str = str(e).lower()
        error_message = "Streaming error occurred"
        error_type = "stream_error"

        # Check for rate limit errors
        if "rate limit" in error_str or "429" in error_str or "too many" in error_str:
            error_message = "Rate limit exceeded. Please wait a moment and try again."
            error_type = "rate_limit_error"
        # Check for authentication errors
        elif "401" in error_str or "unauthorized" in error_str or "authentication" in error_str:
            error_message = "Authentication failed. Please check your API key or sign in again."
            error_type = "auth_error"
        # Check for provider/upstream errors
        elif (
            "upstream" in error_str
            or "provider" in error_str
            or "503" in error_str
            or "502" in error_str
        ):
            error_message = f"Provider temporarily unavailable: {str(e)[:200]}"
            error_type = "provider_error"
        # Check for timeout errors
        elif "timeout" in error_str or "timed out" in error_str:
            error_message = "Request timed out. The model may be overloaded. Please try again."
            error_type = "timeout_error"
        # Check for model not found errors
        elif "not found" in error_str or "404" in error_str:
            error_message = f"Model or resource not found: {str(e)[:200]}"
            error_type = "not_found_error"
        # For other errors, include a sanitized version of the error message
        else:
            # Include the actual error message but truncate it for safety
            sanitized_msg = str(e)[:300].replace("\n", " ").replace("\r", " ")
            error_message = f"Streaming error: {sanitized_msg}"

        # Save failed request to database
        if request_id:
            try:
                # Calculate elapsed time from stream start
                error_elapsed = time.monotonic() - start_time

                # Save failed streaming request with cost tracking (costs are 0 for failed requests)
                await _to_thread(
                    save_chat_completion_request_with_cost,
                    request_id=request_id,
                    model_name=model,
                    input_tokens=prompt_tokens,  # Use tokens accumulated so far
                    output_tokens=completion_tokens,  # May be partial
                    processing_time_ms=int(error_elapsed * 1000),
                    cost_usd=0.0,
                    input_cost_usd=0.0,
                    output_cost_usd=0.0,
                    pricing_source="error",
                    status="failed",
                    error_message=f"{error_type}: {error_message}",
                    user_id=user["id"] if user else None,
                    provider_name=provider,
                    model_id=None,
                    api_key_id=api_key_id,
                    is_anonymous=is_anonymous,
                )
            except Exception as save_err:
                logger.debug(f"Failed to save failed streaming request: {save_err}")

        yield create_error_sse_chunk(
            error_message=error_message,
            error_type=error_type,
            provider=provider if "provider" in dir() else None,
            model=model if "model" in dir() else None,
        )
        yield create_done_sse()
    finally:
        # Record streaming duration
        if streaming_ctx:
            streaming_ctx.__exit__(None, None, None)
        # Record performance percentages if tracker is provided
        if tracker:
            tracker.record_percentages()


# Log route registration for debugging
logger.info("📍 Registering /chat/completions endpoint")


@router.post("/chat/completions", tags=["chat"])
@traced(name="chat_completions", type="llm")
async def chat_completions(
    req: ProxyRequest,
    background_tasks: BackgroundTasks,
    api_key: str | None = Depends(get_optional_api_key),
    session_id: int | None = Query(None, description="Chat session ID to save messages to"),
    request: Request = None,
):
    # === 0) Setup / sanity ===
    # Generate request correlation ID for distributed tracing
    request_id = str(uuid.uuid4())
    request_id_var.set(request_id)

    # Never print keys; log masked
    if Config.IS_TESTING and request:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            parts = auth_header.split(" ", 1)
            if len(parts) == 2:
                api_key = parts[1].strip()
            else:
                logger.warning(
                    f"Malformed Authorization header in testing mode: {auth_header[:20]}..."
                )

    # Determine if this is an authenticated or anonymous request
    is_anonymous = api_key is None

    logger.info(
        "chat_completions start (request_id=%s, api_key=%s, model=%s, anonymous=%s)",
        request_id,
        mask_key(api_key) if api_key else "anonymous",
        req.model,
        is_anonymous,
        extra={"request_id": request_id},
    )

    # Start Braintrust span for this request (uses logger.start_span() for project association)
    span = start_span(name=f"chat_{req.model}", span_type="llm")

    # Initialize performance tracker
    tracker = PerformanceTracker(endpoint="/v1/chat/completions")

    try:
        # === 1) User + plan/trial prechecks (OPTIMIZED: parallelized DB calls) ===
        with tracker.stage("auth_validation"):
            if is_anonymous:
                # Anonymous user - validate model whitelist and rate limits
                # Get client IP for rate limiting
                client_ip = "unknown"
                if request:
                    # Parse X-Forwarded-For header with defensive bounds checking
                    forwarded_for = request.headers.get("X-Forwarded-For", "")
                    if forwarded_for:
                        parts = forwarded_for.split(",")
                        if parts:  # Defensive check (split always returns at least [''])
                            client_ip = parts[0].strip()

                    if not client_ip:
                        client_ip = request.headers.get("X-Real-IP", "")
                    if not client_ip and hasattr(request, "client") and request.client:
                        client_ip = request.client.host or "unknown"

                # Validate anonymous request (model whitelist + rate limit)
                anon_validation = validate_anonymous_request(client_ip, req.model)
                if not anon_validation["allowed"]:
                    logger.warning(
                        "Anonymous request denied (request_id=%s, ip=%s, model=%s, reason=%s)",
                        request_id,
                        client_ip[:16] + "..." if len(client_ip) > 16 else client_ip,
                        req.model,
                        anon_validation["reason"][:50],
                    )
                    # Return appropriate error based on failure type
                    if not anon_validation.get("model_allowed", True):
                        raise HTTPException(
                            status_code=403,
                            detail={
                                "error": {
                                    "message": anon_validation["reason"],
                                    "type": "model_not_allowed",
                                    "code": "anonymous_model_restricted",
                                    "allowed_models": ANONYMOUS_ALLOWED_MODELS[:5],
                                }
                            },
                        )
                    else:
                        raise HTTPException(
                            status_code=429,
                            detail={
                                "error": {
                                    "message": anon_validation["reason"],
                                    "type": "rate_limit_exceeded",
                                    "code": "anonymous_daily_limit",
                                }
                            },
                        )

                user = None
                api_key_id = None
                trial = {"is_valid": True, "is_trial": False, "is_anonymous": True}
                environment_tag = "live"
                logger.info(
                    "Processing anonymous chat request (request_id=%s, ip=%s, model=%s, remaining=%d)",
                    request_id,
                    client_ip[:16] + "..." if len(client_ip) > 16 else client_ip,
                    req.model,
                    anon_validation.get("remaining_requests", 0),
                )

                # Track anonymous request (no API key)
                try:
                    from src.services.prometheus_metrics import api_key_tracking_failures

                    api_key_tracking_failures.labels(reason="anonymous").inc()
                except ImportError:
                    pass
            else:
                # Authenticated user - perform full validation
                # Step 1: Get user first (required for subsequent checks)
                user = await _to_thread(get_user, api_key)
                if not user and Config.IS_TESTING:
                    logger.debug("Fallback user lookup invoked for %s", mask_key(api_key))
                    user = await _to_thread(_fallback_get_user, api_key)
                if not user:
                    logger.warning(
                        "Invalid API key or user not found for key %s", mask_key(api_key)
                    )
                    raise APIExceptions.invalid_api_key()

                # Get API key ID for tracking (if available) - with retry logic
                from src.utils.api_key_lookup import get_api_key_id_with_retry

                api_key_id = await get_api_key_id_with_retry(
                    api_key, max_retries=3, retry_delay=0.1
                )
                if api_key_id is None:
                    logger.warning(
                        "Could not retrieve API key ID for tracking (request_id=%s, key=%s)",
                        request_id,
                        mask_key(api_key),
                    )
                else:
                    # Track successful API key tracking
                    try:
                        from src.services.prometheus_metrics import api_key_tracking_success

                        api_key_tracking_success.labels(request_type="authenticated").inc()
                    except ImportError:
                        pass

                environment_tag = user.get("environment_tag", "live")

                # Set Traceloop association properties for customer tracking (OpenLLMetry)
                # This enables model popularity tracking by user/customer ID
                set_traceloop_properties(
                    user_id=str(user.get("id", "")),
                    api_key_id=str(api_key_id) if api_key_id else None,
                    model=req.model,
                )

                # Step 2: Only validate trial access (plan limits checked after token usage known)
                trial = await _to_thread(validate_trial_access, api_key)

        # Validate trial access with free model bypass (only for authenticated users)
        if not is_anonymous:
            trial = validate_trial_with_free_model_bypass(
                trial=trial,
                model_id=req.model,
                request_id=request_id,
                api_key=api_key,
                logger_instance=logger,
            )

        # Fast-fail requests that would exceed plan limits before hitting any upstream provider
        # (only for authenticated users)
        if not is_anonymous:
            await _ensure_plan_capacity(user["id"], environment_tag)

        rate_limit_mgr = get_rate_limit_manager()
        should_release_concurrency = not trial.get("is_trial", False) and not is_anonymous

        # Pre-check plan limits before making any upstream calls (only for authenticated users)
        if not is_anonymous:
            pre_plan = await _to_thread(enforce_plan_limits, user["id"], 0, environment_tag)
            if not pre_plan.get("allowed", False):
                raise APIExceptions.plan_limit_exceeded(reason=pre_plan.get("reason", "unknown"))

        # Allow disabling rate limiting for testing (DEV ONLY)
        import os

        disable_rate_limiting = os.getenv("DISABLE_RATE_LIMITING", "false").lower() == "true"

        # Initialize rate limit variables
        rl_pre = None
        rl_final = None

        # Rate limiting (only for authenticated non-trial users)
        if (
            not is_anonymous
            and not trial.get("is_trial", False)
            and rate_limit_mgr
            and not disable_rate_limiting
        ):
            rl_pre = await rate_limit_mgr.check_rate_limit(api_key, tokens_used=0)
            if not rl_pre.allowed:
                await _to_thread(
                    create_rate_limit_alert,
                    api_key,
                    "rate_limit_exceeded",
                    {
                        "reason": rl_pre.reason,
                        "retry_after": rl_pre.retry_after,
                        "remaining_requests": rl_pre.remaining_requests,
                        "remaining_tokens": rl_pre.remaining_tokens,
                    },
                )
                raise APIExceptions.rate_limited(
                    retry_after=rl_pre.retry_after, reason=rl_pre.reason
                )

        # Credit check (only for authenticated non-trial users)
        if not is_anonymous and not trial.get("is_trial", False) and user.get("credits", 0.0) <= 0:
            raise APIExceptions.payment_required(credits=user.get("credits", 0.0))

        # Pre-check plan limits before streaming (fail fast) - only for authenticated users
        if not is_anonymous:
            pre_plan = await _to_thread(enforce_plan_limits, user["id"], 0, environment_tag)
            if not pre_plan.get("allowed", False):
                raise APIExceptions.plan_limit_exceeded(reason=pre_plan.get("reason", "unknown"))

        # === 2) Build upstream request ===
        with tracker.stage("request_parsing"):
            messages = [m.model_dump() for m in req.messages]

        # === 2.1) Inject conversation history if session_id provided ===
        # Chat history is only available for authenticated users
        # Validate session_id is within PostgreSQL integer range (-2147483648 to 2147483647)
        if session_id and not is_anonymous:
            # Validate session_id is within valid PostgreSQL integer range
            if session_id < -2147483648 or session_id > 2147483647:
                logger.warning(
                    "Invalid session_id %s: out of PostgreSQL integer range. Ignoring session history.",
                    sanitize_for_logging(str(session_id)),
                )
                session_id = None  # Ignore invalid session_id

        if session_id and not is_anonymous:
            try:
                # Fetch the session with its message history
                session = await _to_thread(get_chat_session, session_id, user["id"])

                if session and session.get("messages"):
                    # Transform DB messages to OpenAI format and prepend to current messages
                    history_messages = [
                        {"role": msg["role"], "content": msg["content"]}
                        for msg in session["messages"]
                    ]

                    # Prepend history to incoming messages
                    messages = history_messages + messages

                    logger.info(
                        "Injected %d messages from session %s",
                        len(history_messages),
                        sanitize_for_logging(str(session_id)),
                    )
                else:
                    logger.debug(
                        "No history found for session %s or session doesn't exist",
                        sanitize_for_logging(str(session_id)),
                    )

            except Exception as e:
                # Don't fail the request if history fetch fails
                logger.warning(
                    "Failed to fetch chat history for session %s: %s",
                    sanitize_for_logging(str(session_id)),
                    sanitize_for_logging(str(e)),
                )
        elif session_id and is_anonymous:
            logger.debug("Ignoring session_id for anonymous request")

        # === 2.1.5) Auto Web Search - start search in parallel to hide latency ===
        web_search_task = None  # Will hold the async task if search is triggered

        # Get auto_web_search setting (default to "auto")
        auto_web_search = getattr(req, "auto_web_search", "auto")
        web_search_threshold = getattr(req, "web_search_threshold", None)
        if web_search_threshold is None:
            web_search_threshold = 0.5

        # Determine if we should perform web search (classifier is ~0.06ms, negligible)
        should_search = False
        search_query = None

        if auto_web_search is True:
            # Explicit enable - always search
            should_search = True
            logger.debug("Auto web search explicitly enabled")
        elif auto_web_search == "auto":
            # Auto mode - use query classifier
            try:
                from src.services.query_classifier import should_auto_search

                should_search, web_search_classification = should_auto_search(
                    messages=messages,
                    threshold=web_search_threshold,
                    enabled=True,
                )
                if should_search:
                    logger.info(
                        "Auto web search triggered: confidence=%.2f, reason=%s",
                        web_search_classification.confidence,
                        web_search_classification.reason,
                    )
            except Exception as e:
                logger.warning("Query classification failed, skipping auto search: %s", str(e))
                should_search = False

        # Start web search task in parallel (non-blocking) to hide latency
        if should_search:
            # Extract search query
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        search_query = content
                    elif isinstance(content, list):
                        text_parts = [
                            p.get("text", "") if isinstance(p, dict) else str(p)
                            for p in content
                            if (isinstance(p, dict) and p.get("type") == "text")
                            or isinstance(p, str)
                        ]
                        search_query = " ".join(text_parts)
                    break

            if search_query and len(search_query.strip()) > 0:
                # Start search as background task - runs in parallel with provider detection
                from src.services.tools import execute_tool

                logger.info("Starting parallel web search for: %s...", search_query[:50])
                web_search_task = asyncio.create_task(
                    execute_tool(
                        "web_search",
                        {
                            "query": search_query,
                            "max_results": 5,
                            "include_answer": True,
                            "search_depth": "basic",
                        },
                    )
                )

        # === 2.2) Plan limit pre-check with estimated tokens (only for authenticated users) ===
        estimated_tokens = estimate_message_tokens(messages, getattr(req, "max_tokens", None))
        if not is_anonymous:
            pre_plan = await _to_thread(
                enforce_plan_limits, user["id"], estimated_tokens, environment_tag
            )
            if not pre_plan.get("allowed", False):
                raise HTTPException(
                    status_code=429,
                    detail=f"Plan limit exceeded: {pre_plan.get('reason', 'unknown')}",
                )

        # Store original model for response and routing logic
        original_model = req.model
        is_auto_route = original_model and original_model.lower().startswith(
            AUTO_ROUTE_MODEL_PREFIX
        )

        # === 2.3) Prompt-Level Routing (if model="auto") ===
        # This is a fail-open router - if it fails or times out, it returns a default cheap model
        router_decision = None
        if is_auto_route:
            with tracker.stage("prompt_routing"):
                try:
                    from src.services.prompt_router import (
                        is_auto_route_request,
                        parse_auto_route_options,
                        route_request,
                    )
                    from src.schemas.router import UserRouterPreferences

                    if is_auto_route_request(original_model):
                        tier, optimization = parse_auto_route_options(original_model)

                        # Build user preferences (could be loaded from DB in future)
                        user_preferences = UserRouterPreferences(
                            default_optimization=optimization,
                            enabled=True,
                        )

                        # Get conversation ID for sticky routing (use session_id if available)
                        conversation_id = str(session_id) if session_id else None

                        # Route the request (fail-open, < 2ms target)
                        router_decision = route_request(
                            messages=messages,
                            tools=getattr(req, "tools", None),
                            response_format=getattr(req, "response_format", None),
                            user_preferences=user_preferences,
                            conversation_id=conversation_id,
                            tier=tier,
                        )

                        # Update model with routed selection
                        req.model = router_decision.selected_model

                        logger.info(
                            "Prompt router selected model: %s (category=%s, confidence=%.2f, time=%.2fms, reason=%s)",
                            router_decision.selected_model,
                            router_decision.classification.category.value
                            if router_decision.classification
                            else "unknown",
                            router_decision.classification.confidence
                            if router_decision.classification
                            else 0,
                            router_decision.decision_time_ms,
                            router_decision.reason,
                        )

                except Exception as e:
                    # Fail open - log warning and use default model
                    logger.warning(
                        "Prompt router failed, falling back to default: %s",
                        str(e),
                    )
                    # Use default model since original was an auto-route request
                    req.model = AUTO_ROUTE_DEFAULT_MODEL

        # === 2.4) Code-Optimized Routing (if model="router:code" or "router:code:<mode>") ===
        # Specialized router for code-related tasks with 2026 benchmark-optimized model selection
        code_router_decision = None
        is_code_route = original_model and original_model.lower().startswith(CODE_ROUTER_PREFIX)

        if is_code_route:
            with tracker.stage("code_routing"):
                try:
                    from src.services.code_router import (
                        parse_router_model_string,
                        route_code_prompt,
                        get_routing_metadata,
                    )

                    # Parse the router mode from model string (normalize case)
                    is_code_router, router_mode = parse_router_model_string(
                        original_model.lower()
                    )

                    if is_code_router:
                        # Extract last user message for classification
                        last_user_message = ""
                        for msg in reversed(messages):
                            if msg.get("role") == "user":
                                content = msg.get("content", "")
                                if isinstance(content, str):
                                    last_user_message = content
                                elif isinstance(content, list):
                                    # Handle multi-part messages
                                    for part in content:
                                        if isinstance(part, dict) and part.get("type") == "text":
                                            last_user_message = part.get("text", "")
                                            break
                                break

                        # Validate: skip routing if no valid user message found
                        if not last_user_message or not last_user_message.strip():
                            logger.warning(
                                "Code router: no valid user message found, using default model"
                            )
                            try:
                                from src.services.prometheus_metrics import track_code_router_fallback
                                track_code_router_fallback(reason="empty_message")
                            except ImportError:
                                # Prometheus metrics are optional - silently skip if not available
                                pass
                            req.model = CODE_ROUTER_DEFAULT_MODEL
                        else:
                            # Extract context from messages
                            from src.services.code_classifier import get_classifier
                            classifier = get_classifier()
                            context = classifier.extract_context_from_messages(messages)

                            # Route the code prompt
                            code_router_decision = route_code_prompt(
                                prompt=last_user_message,
                                mode=router_mode,
                                context=context,
                                user_default_model=user.get("default_model") if user else None,
                            )

                            # Update model with routed selection
                            req.model = code_router_decision["model_id"]

                            logger.info(
                                "Code router selected model: %s (tier=%d, category=%s, confidence=%.2f, time=%.2fms, mode=%s)",
                                code_router_decision["model_id"],
                                code_router_decision["tier"],
                                code_router_decision["task_category"],
                                code_router_decision["confidence"],
                                code_router_decision["routing_latency_ms"],
                                code_router_decision["mode"],
                            )

                except Exception as e:
                    # Fail open - log warning and use default model
                    logger.warning(
                        "Code router failed, falling back to default: %s",
                        str(e),
                    )
                    try:
                        from src.services.prometheus_metrics import track_code_router_fallback
                        track_code_router_fallback(reason="exception")
                    except ImportError:
                        # Prometheus metrics are optional - silently skip if not available
                        pass
                    # Use default code model since original was a code-route request
                    req.model = CODE_ROUTER_DEFAULT_MODEL

        with tracker.stage("request_preparation"):
            optional = {}
            for name in (
                "max_tokens",
                "temperature",
                "top_p",
                "frequency_penalty",
                "presence_penalty",
                "tools",
            ):
                val = getattr(req, name, None)
                if val is not None:
                    optional[name] = val

            # Validate and adjust max_tokens for models with minimum requirements
            validate_and_adjust_max_tokens(optional, original_model)

            # Auto-detect provider if not specified
            req_provider_missing = req.provider is None or (
                isinstance(req.provider, str) and not req.provider
            )
            provider = (req.provider or "onerouter").lower()

            # Normalize provider aliases
            if provider == "hug":
                provider = "huggingface"

            provider_locked = not req_provider_missing

            # Use routed model for provider detection when code routing is active
            model_for_provider_detection = req.model if is_code_route and req.model else original_model
            override_provider = detect_provider_from_model_id(model_for_provider_detection)
            if override_provider:
                override_provider = override_provider.lower()
                if override_provider == "hug":
                    override_provider = "huggingface"
                # FAL models are for image/video generation only, not chat completions
                if override_provider == "fal":
                    logger.warning(
                        "FAL model '%s' requested via chat completions endpoint - "
                        "FAL models only support image/video generation",
                        sanitize_for_logging(original_model),
                    )
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Model '{original_model}' is a FAL image/video generation model and "
                            "does not support chat completions. Please use the /v1/images/generations "
                            "endpoint for image generation, or choose a chat-compatible model. "
                            "FAL models support: text-to-image, text-to-video, image-to-video, and "
                            "other media generation tasks. See https://fal.ai/models for details."
                        ),
                    )
                if provider_locked and override_provider != provider:
                    logger.info(
                        "Skipping provider override for model %s: request locked provider to '%s'",
                        sanitize_for_logging(original_model),
                        sanitize_for_logging(provider),
                    )
                else:
                    if override_provider != provider:
                        logger.info(
                            f"Provider override applied for model {original_model}: '{provider}' -> '{override_provider}'"
                        )
                        provider = override_provider
                    # Mark provider as determined even if it matches the default
                    # This prevents the fallback logic from incorrectly routing to wrong providers
                    req_provider_missing = False

            if req_provider_missing:
                # Try to detect provider from model ID using the transformation module
                # Use routed model when code routing is active
                detected_provider = detect_provider_from_model_id(model_for_provider_detection)
                if detected_provider:
                    provider = detected_provider
                    # Normalize provider aliases
                    if provider == "hug":
                        provider = "huggingface"
                    logger.info(
                        "Auto-detected provider '%s' for model %s",
                        sanitize_for_logging(provider),
                        sanitize_for_logging(original_model),
                    )
                else:
                    # Fallback to checking cached models
                    from src.services.models import get_cached_models

                    # Try each provider with transformation
                    for test_provider in [
                        "huggingface",
                        "featherless",
                        "fireworks",
                        "together",
                        "google-vertex",
                    ]:
                        transformed = transform_model_id(original_model, test_provider)
                        provider_models = get_cached_models(test_provider) or []
                        if any(m.get("id") == transformed for m in provider_models):
                            provider = test_provider
                            logger.info(
                                f"Auto-detected provider '{provider}' for model {original_model} (transformed to {transformed})"
                            )
                            break
                    # Otherwise default to onerouter (already set)

            # Use the routed model (from code router or other routing logic) instead of original
            # This ensures that routing decisions are actually applied downstream
            effective_model = req.model if req.model else original_model

            provider_chain = build_provider_failover_chain(provider)
            provider_chain = enforce_model_failover_rules(effective_model, provider_chain)
            provider_chain = filter_by_circuit_breaker(effective_model, provider_chain)
            model = effective_model

        # Diagnostic logging for tools parameter
        if "tools" in optional:
            logger.info(
                "Tools parameter detected: tools_count=%d, provider=%s, model=%s",
                len(optional["tools"]) if isinstance(optional["tools"], list) else 0,
                sanitize_for_logging(provider),
                sanitize_for_logging(original_model),
            )
            logger.debug("Tools content: %s", sanitize_for_logging(str(optional["tools"])[:500]))

        # === 2.5) Await web search results if task was started (runs in parallel, minimal added latency) ===
        if web_search_task is not None:
            try:
                # Wait for search with timeout (5s max to avoid blocking too long)
                search_result = await asyncio.wait_for(web_search_task, timeout=5.0)

                if search_result.success and search_result.result:
                    results = search_result.result.get("results", [])
                    answer = search_result.result.get("answer")

                    if results or answer:
                        context_parts = ["[Web Search Results]"]

                        if answer:
                            context_parts.append(f"\nSummary: {answer}")

                        if results:
                            context_parts.append("\nSources:")
                            for i, item in enumerate(results[:5], 1):
                                title = item.get("title", "Untitled")
                                content_snippet = item.get("content", "")
                                url = item.get("url", "")

                                if len(content_snippet) > 300:
                                    content_snippet = content_snippet[:297] + "..."

                                context_parts.append(f"\n{i}. {title}")
                                if content_snippet:
                                    context_parts.append(f"   {content_snippet}")
                                if url:
                                    context_parts.append(f"   {url}")

                        context_parts.append("\n[End of Search Results]\n")
                        search_context = "\n".join(context_parts)

                        # Prepend search context as a system message
                        search_system_message = {
                            "role": "system",
                            "content": (
                                f"The following web search results were retrieved to help answer "
                                f"the user's query. Use this information to provide accurate, "
                                f"up-to-date responses. Cite sources when appropriate.\n\n{search_context}"
                            ),
                        }

                        # Insert after any existing system messages
                        insert_index = 0
                        for i, msg in enumerate(messages):
                            if msg.get("role") == "system":
                                insert_index = i + 1
                            else:
                                break

                        messages.insert(insert_index, search_system_message)

                        logger.info(
                            "Auto web search augmented messages with %d results (context_length=%d)",
                            len(results),
                            len(search_context),
                        )
                else:
                    logger.warning(
                        "Auto web search returned no results: %s",
                        search_result.error or "empty results",
                    )

            except TimeoutError:
                logger.warning("Auto web search timed out after 5s, continuing without results")
                web_search_task.cancel()
            except Exception as e:
                logger.warning(
                    "Auto web search failed, continuing without augmentation: %s", str(e)
                )

        # === 3) Call upstream (streaming or non-streaming) ===
        if req.stream:
            # Streaming path
            # Use unified handler for authenticated streaming requests
            if not is_anonymous:
                try:
                    logger.info(
                        f"[Unified Handler] Processing authenticated streaming request for model {original_model}"
                    )

                    # Convert external OpenAI format to internal format
                    adapter = OpenAIChatAdapter()
                    internal_request = adapter.to_internal_request(
                        {"messages": messages, "model": original_model, "stream": True, **optional}
                    )

                    # Create unified handler with user context
                    handler = ChatInferenceHandler(api_key, background_tasks)

                    # Process stream through unified pipeline
                    internal_stream = handler.process_stream(internal_request)

                    # Convert internal stream to SSE format
                    sse_stream = adapter.from_internal_stream(internal_stream)

                    # Prepare response headers
                    stream_headers = {}
                    if rl_pre is not None:
                        stream_headers.update(get_rate_limit_headers(rl_pre))

                    # PERF: Add timing headers
                    if tracker:
                        prep_time_ms = tracker.get_total_duration() * 1000
                        stream_headers["X-Prep-Time-Ms"] = f"{prep_time_ms:.1f}"
                    stream_headers["X-Provider"] = "unified"
                    stream_headers["X-Model"] = original_model
                    stream_headers["X-Requested-Model"] = original_model

                    # SSE streaming headers to prevent buffering by proxies/nginx
                    stream_headers["X-Accel-Buffering"] = "no"
                    stream_headers["Cache-Control"] = "no-cache, no-transform"
                    stream_headers["Connection"] = "keep-alive"

                    logger.info(
                        f"[Unified Handler] Returning SSE streaming response for model {original_model}"
                    )

                    return StreamingResponse(
                        sse_stream,
                        media_type="text/event-stream",
                        headers=stream_headers,
                    )

                except Exception as exc:
                    # Map any errors to HTTPException
                    logger.error(
                        f"[Unified Handler] Streaming error: {type(exc).__name__}: {exc}",
                        exc_info=True,
                    )
                    if isinstance(exc, HTTPException):
                        raise
                    # Map provider-specific errors
                    from src.services.provider_failover import map_provider_error

                    http_exc = map_provider_error(
                        "onerouter",  # Default provider for error mapping
                        original_model,
                        exc,
                    )
                    raise http_exc
            else:
                # Anonymous users: keep existing provider routing logic
                last_http_exc = None
                for idx, attempt_provider in enumerate(provider_chain):
                    attempt_model = transform_model_id(original_model, attempt_provider)
                    if attempt_model != original_model:
                        logger.info(
                            f"Transformed model ID from '{original_model}' to '{attempt_model}' for provider {attempt_provider}"
                        )

                    request_model = attempt_model
                    is_async_stream = False  # Default to sync, only OpenRouter uses async currently
                    try:
                        # Registry-based provider dispatch (replaces ~400 lines of if-elif chains)
                        # Note: Streaming tracing is handled in stream_generator to capture final token counts
                        if attempt_provider == "fal":
                            # FAL models are for image/video generation, not chat completions
                            raise HTTPException(
                                status_code=400,
                                detail={
                                    "error": {
                                        "message": f"Model '{request_model}' is a FAL.ai image/video generation model "
                                        "and is not available through the chat completions endpoint. "
                                        "Please use the /v1/images/generations endpoint with provider='fal' instead.",
                                        "type": "invalid_request_error",
                                        "code": "model_not_supported_for_chat",
                                    }
                                },
                            )
                        elif attempt_provider in PROVIDER_ROUTING:
                            # Use registry for all registered providers
                            stream_func = PROVIDER_ROUTING[attempt_provider]["stream"]
                            stream = await _to_thread(
                                stream_func, messages, request_model, **optional
                            )
                        else:
                            # Default to OpenRouter with async streaming for performance
                            try:
                                stream = await make_openrouter_request_openai_stream_async(
                                    messages, request_model, **optional
                                )
                                is_async_stream = True
                                logger.debug(
                                    f"Using async streaming for OpenRouter model {request_model}"
                                )
                            except Exception as async_err:
                                # Fallback to sync streaming if async fails
                                logger.warning(
                                    f"Async streaming failed, falling back to sync: {async_err}"
                                )
                                stream = await _to_thread(
                                    make_openrouter_request_openai_stream,
                                    messages,
                                    request_model,
                                    **optional,
                                )
                                is_async_stream = False

                        provider = attempt_provider
                        model = request_model
                        # Get rate limit headers if available (pre-stream check)
                        stream_headers = {}
                        if rl_pre is not None:
                            stream_headers.update(get_rate_limit_headers(rl_pre))

                        # PERF: Add timing headers for debugging stream startup latency
                        if tracker:
                            prep_time_ms = tracker.get_total_duration() * 1000
                            stream_headers["X-Prep-Time-Ms"] = f"{prep_time_ms:.1f}"
                        stream_headers["X-Provider"] = provider
                        stream_headers["X-Model"] = model
                        stream_headers["X-Requested-Model"] = original_model

                        # SSE streaming headers to prevent buffering by proxies/nginx
                        stream_headers["X-Accel-Buffering"] = "no"
                        stream_headers["Cache-Control"] = "no-cache, no-transform"
                        stream_headers["Connection"] = "keep-alive"

                        return StreamingResponse(
                            stream_generator(
                                stream,
                                user,
                                api_key,
                                model,
                                trial,
                                environment_tag,
                                session_id,
                                messages,
                                rate_limit_mgr,
                                provider,
                                tracker,
                                is_anonymous,
                                is_async_stream=is_async_stream,
                                request_id=request_id,
                                api_key_id=api_key_id,
                                client_ip=client_ip if is_anonymous else None,
                            ),
                            media_type="text/event-stream",
                            headers=stream_headers,
                        )
                    except Exception as exc:
                        if isinstance(exc, httpx.TimeoutException | asyncio.TimeoutError):
                            logger.warning("Upstream timeout (%s): %s", attempt_provider, exc)
                            # Capture timeout to Sentry
                            capture_provider_error(
                                exc,
                                provider=attempt_provider,
                                model=request_model,
                                endpoint="/v1/chat/completions",
                                request_id=request_id_var.get(),
                            )
                        elif isinstance(exc, httpx.RequestError):
                            logger.warning("Upstream network error (%s): %s", attempt_provider, exc)
                            # Capture network error to Sentry
                            capture_provider_error(
                                exc,
                                provider=attempt_provider,
                                model=request_model,
                                endpoint="/v1/chat/completions",
                                request_id=request_id_var.get(),
                            )
                        elif isinstance(exc, httpx.HTTPStatusError):
                            logger.debug(
                                "Upstream HTTP error (%s): %s",
                                attempt_provider,
                                exc.response.status_code,
                            )
                            # Capture HTTP errors to Sentry (except 4xx client errors)
                            if exc.response.status_code >= 500:
                                capture_provider_error(
                                    exc,
                                    provider=attempt_provider,
                                    model=request_model,
                                    endpoint="/v1/chat/completions",
                                    request_id=request_id_var.get(),
                                )
                        else:
                            logger.error(
                                "Unexpected upstream error (%s): %s", attempt_provider, exc
                            )
                            # Capture unexpected errors to Sentry
                            capture_provider_error(
                                exc,
                                provider=attempt_provider,
                                model=request_model,
                                endpoint="/v1/chat/completions",
                                request_id=request_id_var.get(),
                            )
                        http_exc = map_provider_error(attempt_provider, request_model, exc)

                        last_http_exc = http_exc
                        if idx < len(provider_chain) - 1 and should_failover(http_exc):
                            next_provider = provider_chain[idx + 1]
                            logger.warning(
                                "Provider '%s' failed with status %s (%s). Falling back to '%s'.",
                                attempt_provider,
                                http_exc.status_code,
                                http_exc.detail,
                                next_provider,
                            )
                            continue

                        # If this is a 402 (Payment Required) error and we've exhausted the chain,
                        # rebuild the chain allowing payment failover to try alternative providers
                        if http_exc.status_code == 402 and idx == len(provider_chain) - 1:
                            extended_chain = build_provider_failover_chain(provider)
                            extended_chain = enforce_model_failover_rules(
                                original_model, extended_chain, allow_payment_failover=True
                            )
                            extended_chain = filter_by_circuit_breaker(
                                original_model, extended_chain
                            )
                            # Find providers we haven't tried yet
                            new_providers = [p for p in extended_chain if p not in provider_chain]
                            if new_providers:
                                logger.warning(
                                    "Provider '%s' returned 402 (Payment Required). "
                                    "Extending failover chain with: %s",
                                    attempt_provider,
                                    new_providers,
                                )
                                provider_chain.extend(new_providers)
                                continue

                        raise http_exc

                raise last_http_exc or HTTPException(status_code=502, detail="Upstream error")

        # Non-streaming response
        start = time.monotonic()
        processed = None
        last_http_exc = None

        # Use unified handler for authenticated non-streaming requests
        if not is_anonymous:
            try:
                logger.info(
                    f"[Unified Handler] Processing authenticated non-streaming request for model {original_model}"
                )

                # Convert external OpenAI format to internal format
                adapter = OpenAIChatAdapter()
                internal_request = adapter.to_internal_request(
                    {"messages": messages, "model": original_model, "stream": False, **optional}
                )

                # Create unified handler with user context
                handler = ChatInferenceHandler(api_key, background_tasks)

                # Wrap with AITracer for gen_ai.* telemetry + track duration for Prometheus
                inference_start = time.time()
                async with AITracer.trace_inference(
                    provider="onerouter",  # Will be updated after response
                    model=original_model,
                    request_type=AIRequestType.CHAT_COMPLETION,
                    operation_name=f"unified_handler/{original_model}",
                ) as trace_ctx:
                    # Process request through unified pipeline
                    internal_response = await handler.process(internal_request)

                    # Convert internal response back to OpenAI format
                    processed = adapter.from_internal_response(internal_response)

                    # Extract values for postprocessing
                    provider = internal_response.provider_used or "onerouter"
                    model = internal_response.model or original_model

                    # Set trace attributes with actual values from response
                    usage = processed.get("usage", {}) or {}
                    input_tokens = usage.get("prompt_tokens", 0)
                    output_tokens = usage.get("completion_tokens", 0)
                    trace_ctx.set_token_usage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=usage.get("total_tokens", 0),
                    )
                    trace_ctx.set_response_model(
                        response_model=model,
                        finish_reason=processed.get("choices", [{}])[0].get("finish_reason")
                        if processed.get("choices")
                        else None,
                        response_id=processed.get("id"),
                    )
                    if optional:
                        trace_ctx.set_model_parameters(
                            temperature=optional.get("temperature"),
                            max_tokens=optional.get("max_tokens"),
                            top_p=optional.get("top_p"),
                            frequency_penalty=optional.get("frequency_penalty"),
                            presence_penalty=optional.get("presence_penalty"),
                        )
                    if user:
                        trace_ctx.set_user_info(
                            user_id=str(user.get("id")),
                            tier="trial" if trial.get("is_trial") else "paid",
                        )

                # Record Prometheus metrics for model popularity tracking
                inference_duration = time.time() - inference_start
                model_inference_requests.labels(
                    provider=provider, model=model, status="success"
                ).inc()
                model_inference_duration.labels(provider=provider, model=model).observe(
                    inference_duration
                )
                if input_tokens > 0:
                    tokens_used.labels(provider=provider, model=model, token_type="input").inc(
                        input_tokens
                    )
                if output_tokens > 0:
                    tokens_used.labels(provider=provider, model=model, token_type="output").inc(
                        output_tokens
                    )

                logger.info(
                    f"[Unified Handler] Successfully processed request: provider={provider}, model={model}"
                )

            except Exception as exc:
                # Record error metric for model popularity tracking
                error_provider = provider if "provider" in locals() else "onerouter"
                error_model = model if "model" in locals() else original_model
                model_inference_requests.labels(
                    provider=error_provider, model=error_model, status="error"
                ).inc()

                # Map any errors to HTTPException
                logger.error(f"[Unified Handler] Error: {type(exc).__name__}: {exc}", exc_info=True)
                if isinstance(exc, HTTPException):
                    raise
                # Map provider-specific errors
                from src.services.provider_failover import map_provider_error

                http_exc = map_provider_error(error_provider, error_model, exc)
                raise http_exc
        else:
            # Anonymous users: keep existing provider routing logic
            for idx, attempt_provider in enumerate(provider_chain):
                attempt_model = transform_model_id(original_model, attempt_provider)
                if attempt_model != original_model:
                    logger.info(
                        f"Transformed model ID from '{original_model}' to '{attempt_model}' for provider {attempt_provider}"
                    )

                request_model = attempt_model
                request_timeout = PROVIDER_TIMEOUTS.get(attempt_provider, DEFAULT_PROVIDER_TIMEOUT)
                if request_timeout != DEFAULT_PROVIDER_TIMEOUT:
                    logger.debug(
                        "Using extended timeout %ss for provider %s",
                        request_timeout,
                        attempt_provider,
                    )

                try:
                    # Registry-based provider dispatch (replaces ~400 lines of if-elif chains)
                    # Wrap provider calls with distributed tracing for Tempo
                    async with AITracer.trace_inference(
                        provider=attempt_provider,
                        model=request_model,
                        request_type=AIRequestType.CHAT_COMPLETION,
                    ) as trace_ctx:
                        if attempt_provider == "fal":
                            # FAL models are for image/video generation, not chat completions
                            raise HTTPException(
                                status_code=400,
                                detail={
                                    "error": {
                                        "message": f"Model '{request_model}' is a FAL.ai image/video generation model "
                                        "and is not available through the chat completions endpoint. "
                                        "Please use the /v1/images/generations endpoint with provider='fal' instead.",
                                        "type": "invalid_request_error",
                                        "code": "model_not_supported_for_chat",
                                    }
                                },
                            )
                        elif attempt_provider in PROVIDER_ROUTING:
                            # Use registry for all registered providers
                            request_func = PROVIDER_ROUTING[attempt_provider]["request"]
                            process_func = PROVIDER_ROUTING[attempt_provider]["process"]
                            resp_raw = await asyncio.wait_for(
                                _to_thread(request_func, messages, request_model, **optional),
                                timeout=request_timeout,
                            )
                            processed = await _to_thread(process_func, resp_raw)
                        else:
                            # Default to OpenRouter
                            resp_raw = await asyncio.wait_for(
                                _to_thread(
                                    make_openrouter_request_openai,
                                    messages,
                                    request_model,
                                    **optional,
                                ),
                                timeout=request_timeout,
                            )
                            processed = await _to_thread(process_openrouter_response, resp_raw)

                        # Extract token usage from response for tracing
                        usage = processed.get("usage", {}) or {}
                        trace_prompt_tokens = usage.get("prompt_tokens", 0)
                        trace_completion_tokens = usage.get("completion_tokens", 0)
                        trace_total_tokens = usage.get("total_tokens", 0)

                        # Calculate cost for tracing
                        trace_cost = calculate_cost(
                            request_model, trace_prompt_tokens, trace_completion_tokens
                        )

                        # Set token usage and cost on trace span
                        trace_ctx.set_token_usage(
                            input_tokens=trace_prompt_tokens,
                            output_tokens=trace_completion_tokens,
                            total_tokens=trace_total_tokens,
                        )
                        trace_ctx.set_cost(trace_cost)

                        # Set actual response model (may differ from requested model)
                        response_model = processed.get("model", request_model)
                        # Extract finish reason from first choice if available
                        choices = processed.get("choices", [])
                        finish_reason = choices[0].get("finish_reason") if choices else None
                        response_id = processed.get("id")
                        trace_ctx.set_response_model(
                            response_model=response_model,
                            finish_reason=finish_reason,
                            response_id=response_id,
                        )

                        # Set model parameters if available
                        if optional:
                            trace_ctx.set_model_parameters(
                                temperature=optional.get("temperature"),
                                max_tokens=optional.get("max_tokens"),
                                top_p=optional.get("top_p"),
                                frequency_penalty=optional.get("frequency_penalty"),
                                presence_penalty=optional.get("presence_penalty"),
                            )

                        # Set user info if authenticated
                        if not is_anonymous and user:
                            trace_ctx.set_user_info(
                                user_id=str(user.get("id")),
                                tier="trial" if trial.get("is_trial") else "paid",
                            )

                    provider = attempt_provider
                    model = request_model
                    break
                except Exception as exc:
                    if isinstance(exc, httpx.TimeoutException | asyncio.TimeoutError):
                        logger.warning("Upstream timeout (%s): %s", attempt_provider, exc)
                    elif isinstance(exc, httpx.RequestError):
                        logger.warning("Upstream network error (%s): %s", attempt_provider, exc)
                    elif isinstance(exc, httpx.HTTPStatusError):
                        logger.debug(
                            "Upstream HTTP error (%s): %s",
                            attempt_provider,
                            exc.response.status_code,
                        )
                    else:
                        logger.error("Unexpected upstream error (%s): %s", attempt_provider, exc)
                    http_exc = map_provider_error(attempt_provider, request_model, exc)

                    last_http_exc = http_exc
                    if idx < len(provider_chain) - 1 and should_failover(http_exc):
                        next_provider = provider_chain[idx + 1]
                        logger.warning(
                            "Provider '%s' failed with status %s (%s). Falling back to '%s'.",
                            attempt_provider,
                            http_exc.status_code,
                            http_exc.detail,
                            next_provider,
                        )
                        continue

                    # If this is a 402 (Payment Required) error and we've exhausted the chain,
                    # rebuild the chain allowing payment failover to try alternative providers
                    if http_exc.status_code == 402 and idx == len(provider_chain) - 1:
                        extended_chain = build_provider_failover_chain(provider)
                        extended_chain = enforce_model_failover_rules(
                            original_model, extended_chain, allow_payment_failover=True
                        )
                        extended_chain = filter_by_circuit_breaker(original_model, extended_chain)
                        # Find providers we haven't tried yet
                        new_providers = [p for p in extended_chain if p not in provider_chain]
                        if new_providers:
                            logger.warning(
                                "Provider '%s' returned 402 (Payment Required). "
                                "Extending failover chain with: %s",
                                attempt_provider,
                                new_providers,
                            )
                            provider_chain.extend(new_providers)
                            continue

                    raise http_exc

        if processed is None:
            raise last_http_exc or HTTPException(status_code=502, detail="Upstream error")

        elapsed = max(0.001, time.monotonic() - start)

        # === 4) Usage, pricing, final checks ===
        usage = processed.get("usage", {}) or {}
        total_tokens = usage.get("total_tokens", 0)
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        # Plan limits and usage tracking (only for authenticated users)
        if not is_anonymous:
            post_plan = await _to_thread(
                enforce_plan_limits, user["id"], total_tokens, environment_tag
            )
            if not post_plan.get("allowed", False):
                raise HTTPException(
                    status_code=429,
                    detail=f"Plan limit exceeded: {post_plan.get('reason', 'unknown')}",
                )

            if trial.get("is_trial") and not trial.get("is_expired"):
                try:
                    await _to_thread(
                        track_trial_usage,
                        api_key,
                        total_tokens,
                        1,
                        model_id=request_model,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                    )
                except Exception as e:
                    logger.warning("Failed to track trial usage: %s", e)

            if should_release_concurrency and rate_limit_mgr and not disable_rate_limiting:
                try:
                    await rate_limit_mgr.release_concurrency(api_key)
                except Exception as exc:
                    logger.debug(
                        "Failed to release concurrency before final check for %s: %s",
                        mask_key(api_key),
                        exc,
                    )
                rl_final = await rate_limit_mgr.check_rate_limit(api_key, tokens_used=total_tokens)
                if not rl_final.allowed:
                    await _to_thread(
                        create_rate_limit_alert,
                        api_key,
                        "rate_limit_exceeded",
                        {
                            "reason": rl_final.reason,
                            "retry_after": rl_final.retry_after,
                            "remaining_requests": rl_final.remaining_requests,
                            "remaining_tokens": rl_final.remaining_tokens,
                            "tokens_requested": total_tokens,
                        },
                    )
                    raise HTTPException(
                        status_code=429,
                        detail=f"Rate limit exceeded: {rl_final.reason}",
                        headers=(
                            {"Retry-After": str(rl_final.retry_after)}
                            if rl_final.retry_after
                            else None
                        ),
                    )

        # Credit/usage tracking (only for authenticated users)
        if not is_anonymous:
            cost = await _handle_credits_and_usage(
                api_key=api_key,
                user=user,
                model=model,
                trial=trial,
                total_tokens=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                elapsed_ms=int(elapsed * 1000),
                is_streaming=False,
            )
            await _to_thread(increment_api_key_usage, api_key)
        else:
            cost = calculate_cost(model, prompt_tokens, completion_tokens)

        # Record Prometheus metrics and passive health monitoring (allowed for anonymous)
        await _record_inference_metrics_and_health(
            provider=provider,
            model=model,
            elapsed_seconds=elapsed,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=cost,
            success=True,
            error_message=None,
        )

        # === 4.5) Log activity for tracking and analytics (only for authenticated users) ===
        if not is_anonymous:
            try:
                provider_name = get_provider_from_model(model)
                speed = total_tokens / elapsed if elapsed > 0 else 0
                await _to_thread(
                    log_activity,
                    user_id=user["id"],
                    model=model,
                    provider=provider_name,
                    tokens=total_tokens,
                    cost=cost if not trial.get("is_trial", False) else 0.0,
                    speed=speed,
                    finish_reason=(processed.get("choices") or [{}])[0].get(
                        "finish_reason", "stop"
                    ),
                    app="API",
                    metadata={
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "endpoint": "/v1/chat/completions",
                        "session_id": session_id,
                        "gateway": provider,  # Track which gateway was used
                    },
                )
            except Exception as e:
                logger.error(
                    f"Failed to log activity for user {user['id']}, model {model}: {e}",
                    exc_info=True,
                )

        # === 5) History (use the last user message in this request only) ===
        # Chat history is only saved for authenticated users
        # Validate session_id before attempting to save
        if session_id and not is_anonymous:
            # Re-validate session_id in case it was modified during request processing
            if session_id < -2147483648 or session_id > 2147483647:
                logger.warning(
                    "Invalid session_id %s during history save: out of PostgreSQL integer range. Skipping history save.",
                    sanitize_for_logging(str(session_id)),
                )
                session_id = None

        if session_id and not is_anonymous:
            try:
                session = await _to_thread(get_chat_session, session_id, user["id"])
                if session:
                    # save last user turn in this call
                    last_user = None
                    for m in reversed(messages):
                        if m.get("role") == "user":
                            last_user = m
                            break
                    if last_user:
                        await _to_thread(
                            save_chat_message,
                            session_id,
                            "user",
                            last_user.get("content", ""),
                            model,
                            0,
                            user["id"],
                        )

                    # Safely extract assistant content (handle None values in choices)
                    choices = processed.get("choices") or [{}]
                    first_choice = choices[0] if choices else {}
                    message = first_choice.get("message") or {}
                    assistant_content = message.get("content", "")
                    if assistant_content:
                        await _to_thread(
                            save_chat_message,
                            session_id,
                            "assistant",
                            assistant_content,
                            model,
                            total_tokens,
                            user["id"],
                        )
                else:
                    logger.warning("Session %s not found for user %s", session_id, user["id"])
            except Exception as e:
                logger.error(
                    f"Failed to save chat history for session {session_id}, user {user['id']}: {e}",
                    exc_info=True,
                )

        # === 6) Attach gateway usage (non-sensitive) ===
        processed.setdefault("gateway_usage", {})
        processed["gateway_usage"].update(
            {
                "tokens_charged": total_tokens,
                "request_ms": int(elapsed * 1000),
            }
        )
        if not trial.get("is_trial", False):
            # If you can cheaply re-fetch balance, do it here; otherwise omit
            processed["gateway_usage"]["cost_usd"] = round(cost, 6)

        # === 6.1) Attach code router metadata if code routing was used ===
        if code_router_decision:
            try:
                from src.services.code_router import get_routing_metadata
                routing_metadata = get_routing_metadata(code_router_decision)
                processed["routing_metadata"] = routing_metadata
            except Exception as e:
                logger.debug(f"Failed to attach code routing metadata: {e}")

        # === 7) Log to Braintrust ===
        try:
            logger.info(
                f"[Braintrust] Starting log for request_id={request_id}, model={model}, "
                f"available={check_braintrust_available()}, span_type={type(span).__name__}"
            )
            # Safely convert messages to dicts, filtering out None values and sanitizing content
            messages_for_log = []
            for m in req.messages:
                if m is None:
                    continue
                msg_dict = m.model_dump() if hasattr(m, "model_dump") else m
                if msg_dict is None:
                    continue
                # Sanitize content to avoid NoneType subscript errors in Braintrust SDK
                if isinstance(msg_dict, dict) and "content" in msg_dict:
                    content = msg_dict.get("content")
                    if content is None:
                        msg_dict["content"] = ""
                    elif isinstance(content, list):
                        # Filter out None items and sanitize nested dicts in content list
                        sanitized_content = []
                        for item in content:
                            if item is None:
                                continue
                            if isinstance(item, dict):
                                # Deep sanitize dict items (e.g., {"type": "text", "text": None})
                                sanitized_item = {}
                                for k, v in item.items():
                                    if v is None:
                                        sanitized_item[k] = "" if k in ("text", "content") else v
                                    else:
                                        sanitized_item[k] = v
                                sanitized_content.append(sanitized_item)
                            else:
                                sanitized_content.append(item)
                        msg_dict["content"] = sanitized_content
                messages_for_log.append(msg_dict)
            # Safely extract output content for Braintrust logging
            bt_choices = processed.get("choices") or []
            bt_first_choice = bt_choices[0] if bt_choices else None
            bt_message = (
                bt_first_choice.get("message") if isinstance(bt_first_choice, dict) else None
            )
            bt_content = bt_message.get("content") if isinstance(bt_message, dict) else None
            # Handle case where content is None, a string, or a list (multimodal)
            if bt_content is None:
                bt_output = ""
            elif isinstance(bt_content, str):
                bt_output = bt_content
            elif isinstance(bt_content, list):
                # Extract text from multimodal content, filtering empty strings
                texts = []
                for item in bt_content:
                    if item is None:
                        continue
                    if isinstance(item, dict):
                        text = item.get("text")
                        if text is not None:
                            texts.append(str(text))
                    else:
                        texts.append(str(item))
                bt_output = " ".join(t for t in texts if t)
            else:
                bt_output = str(bt_content)
            # Safely get user_id and environment for anonymous users (user=None)
            bt_user_id = user["id"] if user else "anonymous"
            bt_environment = user.get("environment_tag", "live") if user else "live"
            bt_is_trial = trial.get("is_trial", False) if trial else False
            logger.info(
                f"[Braintrust] Logging span: user_id={bt_user_id}, model={model}, tokens={total_tokens}"
            )
            span.log(
                input=messages_for_log,
                output=bt_output,
                metrics={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    "latency_ms": int(elapsed * 1000),
                    "cost_usd": cost if not bt_is_trial else 0.0,
                },
                metadata={
                    "model": model,
                    "provider": provider,
                    "user_id": bt_user_id,
                    "session_id": session_id,
                    "is_trial": bt_is_trial,
                    "environment": bt_environment,
                },
            )
            span.end()
            # Flush to ensure data is sent to Braintrust
            braintrust_flush()
            logger.info(
                f"[Braintrust] Successfully logged and flushed span for request_id={request_id}"
            )
        except Exception as e:
            logger.warning(f"[Braintrust] Failed to log to Braintrust: {e}", exc_info=True)

        # Capture health metrics (passive monitoring) - run as background task
        background_tasks.add_task(
            capture_model_health,
            provider=provider,
            model=model,
            response_time_ms=elapsed * 1000,
            status="success",
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
        )

        # Save chat completion request metadata to database with cost tracking - run as background task
        # Calculate cost breakdown for analytics
        from src.services.pricing import get_model_pricing

        pricing_info = get_model_pricing(model)
        input_cost = prompt_tokens * pricing_info.get("prompt", 0)
        output_cost = completion_tokens * pricing_info.get("completion", 0)

        background_tasks.add_task(
            save_chat_completion_request_with_cost,
            request_id=request_id,
            model_name=model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            processing_time_ms=int(elapsed * 1000),
            cost_usd=cost,
            input_cost_usd=input_cost,
            output_cost_usd=output_cost,
            pricing_source="calculated",
            status="completed",
            error_message=None,
            user_id=user["id"] if not is_anonymous else None,
            provider_name=provider,
            model_id=None,
            api_key_id=api_key_id,
            is_anonymous=is_anonymous,
        )

        # Prepare headers including rate limit information
        headers = {}
        if rl_final is not None:
            headers.update(get_rate_limit_headers(rl_final))

        return JSONResponse(content=processed, headers=headers)

    except HTTPException as http_exc:
        # Save failed request for HTTPException errors (rate limits, auth errors, etc.)
        if request_id:
            try:
                # Calculate elapsed time
                error_elapsed = time.monotonic() - start if "start" in dir() else 0

                # Save failed request to database with cost tracking (costs are 0 for failed requests)
                await _to_thread(
                    save_chat_completion_request_with_cost,
                    request_id=request_id,
                    model_name=(
                        model
                        if "model" in dir()
                        else original_model
                        if "original_model" in dir()
                        else "unknown"
                    ),
                    input_tokens=prompt_tokens if "prompt_tokens" in dir() else 0,
                    output_tokens=0,  # No output on error
                    processing_time_ms=int(error_elapsed * 1000),
                    cost_usd=0.0,
                    input_cost_usd=0.0,
                    output_cost_usd=0.0,
                    pricing_source="error",
                    status="failed",
                    error_message=f"HTTP {http_exc.status_code}: {http_exc.detail}",
                    user_id=user["id"] if user and "user" in dir() else None,
                    provider_name=provider if "provider" in dir() else None,
                    model_id=None,
                    api_key_id=api_key_id if "api_key_id" in dir() else None,
                    is_anonymous=is_anonymous if "is_anonymous" in dir() else False,
                )
            except Exception as save_err:
                logger.debug(f"Failed to save failed request metadata: {save_err}")
        raise
    except Exception as e:
        logger.exception(
            f"[{request_id}] Unhandled server error: {type(e).__name__}",
            extra={"request_id": request_id, "error_type": type(e).__name__},
        )

        # Save failed request for unexpected errors
        if request_id:
            try:
                # Calculate elapsed time
                error_elapsed = time.monotonic() - start if "start" in dir() else 0

                # Save failed request to database with cost tracking (costs are 0 for failed requests)
                await _to_thread(
                    save_chat_completion_request_with_cost,
                    request_id=request_id,
                    model_name=(
                        model
                        if "model" in dir()
                        else original_model
                        if "original_model" in dir()
                        else "unknown"
                    ),
                    input_tokens=prompt_tokens if "prompt_tokens" in dir() else 0,
                    output_tokens=0,  # No output on error
                    processing_time_ms=int(error_elapsed * 1000),
                    cost_usd=0.0,
                    input_cost_usd=0.0,
                    output_cost_usd=0.0,
                    pricing_source="error",
                    status="failed",
                    error_message=f"{type(e).__name__}: {str(e)[:500]}",
                    user_id=user["id"] if user and "user" in dir() else None,
                    provider_name=provider if "provider" in dir() else None,
                    model_id=None,
                    api_key_id=api_key_id if "api_key_id" in dir() else None,
                    is_anonymous=is_anonymous if "is_anonymous" in dir() else False,
                )
            except Exception as save_err:
                logger.debug(f"Failed to save failed request metadata: {save_err}")

        # Don't leak internal details, but include request ID for support
        raise HTTPException(
            status_code=500, detail=f"Internal server error (request ID: {request_id})"
        )


@router.post("/responses", tags=["chat"])
@traced(name="unified_responses", type="llm")
async def unified_responses(
    req: ResponseRequest,
    background_tasks: BackgroundTasks,
    api_key: str = Depends(get_api_key),
    session_id: int | None = Query(None, description="Chat session ID to save messages to"),
    request: Request = None,
):
    """
    Unified response API endpoint (OpenAI v1/responses compatible).
    This is the newer, more flexible alternative to v1/chat/completions.

    Key differences:
    - Uses 'input' instead of 'messages'
    - Returns 'output' instead of 'choices'
    - Supports response_format for structured JSON output
    - Future-ready for multimodal input/output
    """
    # Generate request correlation ID for distributed tracing
    request_id = str(uuid.uuid4())
    request_id_var.set(request_id)

    if Config.IS_TESTING and request:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            parts = auth_header.split(" ", 1)
            if len(parts) == 2:
                api_key = parts[1].strip()
            else:
                logger.warning(
                    f"Malformed Authorization header in testing mode: {auth_header[:20]}..."
                )

    logger.info(
        "unified_responses start (request_id=%s, api_key=%s, model=%s)",
        request_id,
        mask_key(api_key),
        req.model,
        extra={"request_id": request_id},
    )

    # Start Braintrust span for this request (uses logger.start_span() for project association)
    span = start_span(name=f"responses_{req.model}", span_type="llm")

    rate_limit_mgr = None
    should_release_concurrency = False
    stream_release_handled = False

    try:
        # === 1) User + plan/trial prechecks ===
        user = await _to_thread(get_user, api_key)
        if not user and not Config.IS_TESTING:
            logger.debug("Fallback user lookup invoked for %s", mask_key(api_key))
            user = await _to_thread(_fallback_get_user, api_key)
        if not user:
            logger.warning("Invalid API key or user not found for key %s", mask_key(api_key))
            raise APIExceptions.invalid_api_key()

        # Get API key ID for tracking (if available) - with retry logic
        from src.utils.api_key_lookup import get_api_key_id_with_retry

        api_key_id = await get_api_key_id_with_retry(api_key, max_retries=3, retry_delay=0.1)
        if api_key_id is None:
            logger.warning(
                "Could not retrieve API key ID for tracking (request_id=%s, key=%s)",
                request_id,
                mask_key(api_key),
            )

        environment_tag = user.get("environment_tag", "live")

        trial = await _to_thread(validate_trial_access, api_key)

        # Validate trial access with free model bypass
        trial = validate_trial_with_free_model_bypass(
            trial=trial,
            model_id=req.model,
            request_id=request_id,
            api_key=api_key,
            logger_instance=logger,
        )

        rate_limit_mgr = get_rate_limit_manager()

        # Pre-check plan limits before making any provider calls to avoid unnecessary work
        pre_plan = await _to_thread(enforce_plan_limits, user["id"], 0, environment_tag)
        if not pre_plan.get("allowed", False):
            raise HTTPException(
                status_code=429,
                detail=f"Plan limit exceeded: {pre_plan.get('reason', 'unknown')}",
            )
        else:
            logger.debug(
                "Plan pre-check passed for user %s (env=%s): %s",
                sanitize_for_logging(str(user.get("id"))),
                environment_tag,
                pre_plan,
            )

        if not trial.get("is_trial", False):
            rl_pre = await rate_limit_mgr.check_rate_limit(api_key, tokens_used=0)
            if not rl_pre.allowed:
                await _to_thread(
                    create_rate_limit_alert,
                    api_key,
                    "rate_limit_exceeded",
                    {
                        "reason": rl_pre.reason,
                        "retry_after": rl_pre.retry_after,
                        "remaining_requests": rl_pre.remaining_requests,
                        "remaining_tokens": rl_pre.remaining_tokens,
                    },
                )
                raise APIExceptions.rate_limited(
                    retry_after=rl_pre.retry_after, reason=rl_pre.reason
                )

        if not trial.get("is_trial", False) and user.get("credits", 0.0) <= 0:
            raise APIExceptions.payment_required(credits=user.get("credits", 0.0))

        # === 2) Transform 'input' to 'messages' format for upstream ===
        messages = []
        try:
            for inp_msg in req.input:
                # Convert InputMessage to standard message format
                if isinstance(inp_msg.content, str):
                    messages.append({"role": inp_msg.role, "content": inp_msg.content})
                elif isinstance(inp_msg.content, list):
                    # Multimodal content - transform to OpenAI format
                    transformed_content = []
                    for item in inp_msg.content:
                        if isinstance(item, dict):
                            # Map input types to OpenAI chat format
                            if item.get("type") == "input_text":
                                transformed_content.append(
                                    {"type": "text", "text": item.get("text", "")}
                                )
                            elif item.get("type") == "output_text":
                                # Transform Responses API output_text to standard text format
                                # This handles cases where clients send assistant messages with
                                # output_text content type from previous response conversations
                                transformed_content.append(
                                    {"type": "text", "text": item.get("text", "")}
                                )
                            elif item.get("type") == "input_image_url":
                                transformed_content.append(
                                    {"type": "image_url", "image_url": item.get("image_url", {})}
                                )
                            elif item.get("type") in ("text", "image_url"):
                                # Already in correct format
                                transformed_content.append(item)
                            else:
                                logger.warning(
                                    f"Unknown content type: {item.get('type')}, skipping"
                                )
                                # Skip unknown types instead of passing them through to avoid
                                # provider API errors like "Unexpected content chunk type"
                        else:
                            logger.warning(f"Invalid content item (not a dict): {type(item)}")

                    messages.append({"role": inp_msg.role, "content": transformed_content})
                else:
                    logger.error(f"Invalid content type: {type(inp_msg.content)}")
                    raise APIExceptions.bad_request(
                        detail=f"Invalid content type: {type(inp_msg.content)}"
                    )
        except Exception as e:
            logger.error(f"Error transforming input to messages: {e}, input: {req.input}")
            raise APIExceptions.bad_request(detail=f"Invalid input format: {str(e)}")

        # === 2.1) Inject conversation history if session_id provided ===
        # Validate session_id is within PostgreSQL integer range (-2147483648 to 2147483647)
        if session_id:
            # Validate session_id is within valid PostgreSQL integer range
            if session_id < -2147483648 or session_id > 2147483647:
                logger.warning(
                    "Invalid session_id %s for /v1/responses: out of PostgreSQL integer range. Ignoring session history.",
                    sanitize_for_logging(str(session_id)),
                )
                session_id = None

        if session_id:
            try:
                session = await _to_thread(get_chat_session, session_id, user["id"])
                if session and session.get("messages"):
                    history_messages = [
                        {"role": msg["role"], "content": msg["content"]}
                        for msg in session["messages"]
                    ]
                    messages = history_messages + messages
                    logger.info(
                        "Injected %d messages from session %s",
                        len(history_messages),
                        sanitize_for_logging(str(session_id)),
                    )
            except Exception as e:
                logger.warning(
                    "Failed to fetch chat history for session %s: %s",
                    sanitize_for_logging(str(session_id)),
                    sanitize_for_logging(str(e)),
                )

        # Plan limit pre-check for unified responses
        estimated_tokens = estimate_message_tokens(messages, getattr(req, "max_tokens", None))
        pre_plan = await _to_thread(
            enforce_plan_limits, user["id"], estimated_tokens, environment_tag
        )
        if not pre_plan.get("allowed", False):
            raise HTTPException(
                status_code=429, detail=f"Plan limit exceeded: {pre_plan.get('reason', 'unknown')}"
            )

        # Store original model for response
        original_model = req.model

        optional = {}
        for name in (
            "max_tokens",
            "temperature",
            "top_p",
            "frequency_penalty",
            "presence_penalty",
            "tools",
        ):
            val = getattr(req, name, None)
            if val is not None:
                optional[name] = val

        # Validate and adjust max_tokens for models with minimum requirements
        validate_and_adjust_max_tokens(optional, original_model)

        # Add response_format if specified
        if req.response_format:
            if req.response_format.type == "json_object":
                optional["response_format"] = {"type": "json_object"}
            elif req.response_format.type == "json_schema" and req.response_format.json_schema:
                optional["response_format"] = {
                    "type": "json_schema",
                    "json_schema": req.response_format.json_schema,
                }

        # Auto-detect provider if not specified
        req_provider_missing = req.provider is None or (
            isinstance(req.provider, str) and not req.provider
        )
        provider = (req.provider or "openrouter").lower()

        # Normalize provider aliases
        if provider == "hug":
            provider = "huggingface"

        provider_locked = not req_provider_missing

        override_provider = detect_provider_from_model_id(original_model)
        if override_provider:
            override_provider = override_provider.lower()
            if override_provider == "hug":
                override_provider = "huggingface"
            # FAL models are for image/video generation only, not chat completions
            if override_provider == "fal":
                logger.warning(
                    "FAL model '%s' requested via responses endpoint - "
                    "FAL models only support image/video generation",
                    sanitize_for_logging(original_model),
                )
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Model '{original_model}' is a FAL image/video generation model and "
                        "does not support chat completions. Please use the /v1/images/generations "
                        "endpoint for image generation, or choose a chat-compatible model. "
                        "FAL models support: text-to-image, text-to-video, image-to-video, and "
                        "other media generation tasks. See https://fal.ai/models for details."
                    ),
                )
            if provider_locked and override_provider != provider:
                logger.info(
                    "Skipping provider override for model %s: request locked provider to '%s'",
                    sanitize_for_logging(original_model),
                    sanitize_for_logging(provider),
                )
            else:
                if override_provider != provider:
                    logger.info(
                        f"Provider override applied for model {original_model}: '{provider}' -> '{override_provider}'"
                    )
                    provider = override_provider
                # Mark provider as determined even if it matches the default
                # This prevents the fallback logic from incorrectly routing to wrong providers
                req_provider_missing = False

        if req_provider_missing:
            # Try to detect provider from model ID using the transformation module
            detected_provider = detect_provider_from_model_id(original_model)
            if detected_provider:
                provider = detected_provider
                logger.info(
                    "Auto-detected provider '%s' for model %s",
                    sanitize_for_logging(provider),
                    sanitize_for_logging(original_model),
                )
            else:
                # Fallback to checking cached models
                from src.services.models import get_cached_models

                # Try each provider with transformation
                for test_provider in [
                    "huggingface",
                    "featherless",
                    "fireworks",
                    "together",
                    "google-vertex",
                ]:
                    transformed = transform_model_id(original_model, test_provider)
                    provider_models = get_cached_models(test_provider) or []
                    if any(m.get("id") == transformed for m in provider_models):
                        provider = test_provider
                        logger.info(
                            "Auto-detected provider '%s' for model %s (transformed to %s)",
                            sanitize_for_logging(provider),
                            sanitize_for_logging(original_model),
                            sanitize_for_logging(transformed),
                        )
                        break

        provider_chain = build_provider_failover_chain(provider)
        provider_chain = enforce_model_failover_rules(original_model, provider_chain)
        provider_chain = filter_by_circuit_breaker(original_model, provider_chain)
        model = original_model

        # Diagnostic logging for tools parameter
        if "tools" in optional:
            logger.info(
                "Tools parameter detected (unified_responses): tools_count=%d, provider=%s, model=%s",
                len(optional["tools"]) if isinstance(optional["tools"], list) else 0,
                sanitize_for_logging(provider),
                sanitize_for_logging(original_model),
            )
            logger.debug("Tools content: %s", sanitize_for_logging(str(optional["tools"])[:500]))

        # === 3) Call upstream (streaming or non-streaming) ===
        if req.stream:
            last_http_exc = None
            for idx, attempt_provider in enumerate(provider_chain):
                attempt_model = transform_model_id(original_model, attempt_provider)
                if attempt_model != original_model:
                    logger.info(
                        f"Transformed model ID from '{original_model}' to '{attempt_model}' for provider {attempt_provider}"
                    )

                request_model = attempt_model
                http_exc = None
                try:
                    # Registry-based provider dispatch
                    if attempt_provider == "fal":
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "error": {
                                    "message": f"Model '{request_model}' is a FAL.ai image/video generation model "
                                    "and is not available through the chat completions endpoint. "
                                    "Please use the /v1/images/generations endpoint with provider='fal' instead.",
                                    "type": "invalid_request_error",
                                    "code": "model_not_supported_for_chat",
                                }
                            },
                        )
                    elif attempt_provider in PROVIDER_ROUTING:
                        stream_func = PROVIDER_ROUTING[attempt_provider]["stream"]
                        stream = await _to_thread(stream_func, messages, request_model, **optional)
                    else:
                        stream = await _to_thread(
                            make_openrouter_request_openai_stream,
                            messages,
                            request_model,
                            **optional,
                        )

                    async def response_stream_generator(stream=stream, request_model=request_model):
                        """Transform chat/completions stream to OpenAI Responses API format.

                        OpenAI Responses API uses SSE with event: and data: fields.
                        Events emitted:
                        - response.created: Initial response object
                        - response.output_item.added: New output item started
                        - response.output_text.delta: Text content delta
                        - response.output_item.done: Output item completed
                        - response.completed: Final response with usage
                        """
                        sequence_number = 0
                        # Generate stable response ID upfront for consistency across events
                        response_id = f"resp_{secrets.token_hex(12)}"
                        created_timestamp = int(time.time())
                        model_name = request_model
                        has_sent_created = False
                        has_error = False  # Track if any errors occurred during streaming
                        usage_data = None
                        # Track multiple choices (n > 1) separately by index
                        # Each choice gets its own item_id, accumulated_content, etc.
                        items_by_index: dict[int, dict] = {}  # choice_index -> item state

                        async for chunk_data in stream_generator(
                            stream,
                            user,
                            api_key,
                            request_model,
                            trial,
                            environment_tag,
                            session_id,
                            messages,
                            rate_limit_mgr,
                            provider=attempt_provider,
                            tracker=None,
                            is_anonymous=False,  # /v1/responses requires authentication
                            is_async_stream=False,
                            request_id=request_id,
                            api_key_id=api_key_id,
                        ):
                            if chunk_data.startswith("data: "):
                                data_str = chunk_data[6:].strip()
                                if data_str == "[DONE]":
                                    # Ensure response.created is always sent first
                                    if not has_sent_created:
                                        created_event = {
                                            "type": "response.created",
                                            "sequence_number": sequence_number,
                                            "response": {
                                                "id": response_id,
                                                "object": "response",
                                                "created_at": created_timestamp,
                                                "model": model_name,
                                                "status": "in_progress",
                                                "output": [],
                                            },
                                        }
                                        yield f"event: response.created\ndata: {json.dumps(created_event)}\n\n"
                                        sequence_number += 1
                                        has_sent_created = True

                                    # Emit done events only for items that were announced as added
                                    for idx in sorted(items_by_index.keys()):
                                        item_state = items_by_index[idx]
                                        # Only emit done events for items that had item_added sent
                                        if not item_state["item_added_sent"]:
                                            continue
                                        done_event = {
                                            "type": "response.output_text.done",
                                            "sequence_number": sequence_number,
                                            "response_id": response_id,
                                            "item_id": item_state["item_id"],
                                            "output_index": idx,
                                            "content_index": 0,
                                            "text": item_state["content"],
                                        }
                                        yield f"event: response.output_text.done\ndata: {json.dumps(done_event)}\n\n"
                                        sequence_number += 1

                                        item_done_event = {
                                            "type": "response.output_item.done",
                                            "sequence_number": sequence_number,
                                            "response_id": response_id,
                                            "output_index": idx,
                                            "item": {
                                                "id": item_state["item_id"],
                                                "type": "message",
                                                "role": "assistant",
                                                "status": "completed",
                                                "content": [
                                                    {
                                                        "type": "output_text",
                                                        "text": item_state["content"],
                                                    }
                                                ],
                                            },
                                        }
                                        yield f"event: response.output_item.done\ndata: {json.dumps(item_done_event)}\n\n"
                                        sequence_number += 1

                                    # Build output list only from items that were announced
                                    output_list = [
                                        {
                                            "id": items_by_index[idx]["item_id"],
                                            "type": "message",
                                            "role": "assistant",
                                            "status": "completed",
                                            "content": [
                                                {
                                                    "type": "output_text",
                                                    "text": items_by_index[idx]["content"],
                                                }
                                            ],
                                        }
                                        for idx in sorted(items_by_index.keys())
                                        if items_by_index[idx]["item_added_sent"]
                                    ]

                                    # Emit response.completed with appropriate status
                                    response_status = "failed" if has_error else "completed"
                                    completed_event = {
                                        "type": "response.completed",
                                        "sequence_number": sequence_number,
                                        "response": {
                                            "id": response_id,
                                            "object": "response",
                                            "created_at": created_timestamp,
                                            "model": model_name,
                                            "status": response_status,
                                            "output": output_list,
                                        },
                                    }
                                    # Add usage if available
                                    if usage_data:
                                        completed_event["response"]["usage"] = usage_data
                                    yield f"event: response.completed\ndata: {json.dumps(completed_event)}\n\n"
                                    continue

                                try:
                                    chunk_json = json.loads(data_str)

                                    # Extract model name from chunk if available
                                    if chunk_json.get("model"):
                                        model_name = chunk_json["model"]

                                    # Extract usage if present (some providers include it in final chunk)
                                    if chunk_json.get("usage"):
                                        usage_data = chunk_json["usage"]

                                    # Check for errors first (handles cases where both error and empty choices exist)
                                    if chunk_json.get("error"):
                                        # Transform error to Responses API error event
                                        # Handle both dict and string error formats
                                        error_field = chunk_json["error"]
                                        if isinstance(error_field, dict):
                                            error_message = error_field.get(
                                                "message", "Unknown error"
                                            )
                                        else:
                                            error_message = str(error_field)
                                        error_event = {
                                            "type": "error",
                                            "sequence_number": sequence_number,
                                            "error": {
                                                "type": "server_error",
                                                "message": error_message,
                                            },
                                        }
                                        yield f"event: error\ndata: {json.dumps(error_event)}\n\n"
                                        sequence_number += 1
                                        has_error = True
                                    elif "choices" in chunk_json and chunk_json["choices"]:
                                        for choice in chunk_json["choices"]:
                                            choice_index = choice.get("index", 0)

                                            # Emit response.created on first chunk
                                            if not has_sent_created:
                                                created_event = {
                                                    "type": "response.created",
                                                    "sequence_number": sequence_number,
                                                    "response": {
                                                        "id": response_id,
                                                        "object": "response",
                                                        "created_at": created_timestamp,
                                                        "model": model_name,
                                                        "status": "in_progress",
                                                        "output": [],
                                                    },
                                                }
                                                yield f"event: response.created\ndata: {json.dumps(created_event)}\n\n"
                                                sequence_number += 1
                                                has_sent_created = True

                                            # Initialize item state for this choice if not seen before
                                            if choice_index not in items_by_index:
                                                items_by_index[choice_index] = {
                                                    "item_id": f"item_{secrets.token_hex(8)}",
                                                    "content": "",
                                                    "item_added_sent": False,
                                                }

                                            item_state = items_by_index[choice_index]

                                            # Emit response.output_item.added on first content for this choice
                                            if (
                                                not item_state["item_added_sent"]
                                                and "delta" in choice
                                            ):
                                                item_added_event = {
                                                    "type": "response.output_item.added",
                                                    "sequence_number": sequence_number,
                                                    "response_id": response_id,
                                                    "output_index": choice_index,
                                                    "item": {
                                                        "id": item_state["item_id"],
                                                        "type": "message",
                                                        "role": choice["delta"].get(
                                                            "role", "assistant"
                                                        ),
                                                        "status": "in_progress",
                                                        "content": [],
                                                    },
                                                }
                                                yield f"event: response.output_item.added\ndata: {json.dumps(item_added_event)}\n\n"
                                                sequence_number += 1
                                                item_state["item_added_sent"] = True

                                            # Emit response.output_text.delta for content
                                            if "delta" in choice and "content" in choice["delta"]:
                                                delta_content = choice["delta"]["content"]
                                                if delta_content:
                                                    item_state["content"] += delta_content
                                                    delta_event = {
                                                        "type": "response.output_text.delta",
                                                        "sequence_number": sequence_number,
                                                        "response_id": response_id,
                                                        "item_id": item_state["item_id"],
                                                        "output_index": choice_index,
                                                        "content_index": 0,
                                                        "delta": delta_content,
                                                    }
                                                    yield f"event: response.output_text.delta\ndata: {json.dumps(delta_event)}\n\n"
                                                    sequence_number += 1
                                except json.JSONDecodeError:
                                    # Emit as error event for malformed JSON
                                    error_event = {
                                        "type": "error",
                                        "sequence_number": sequence_number,
                                        "error": {
                                            "type": "invalid_response",
                                            "message": f"Malformed response chunk: {data_str[:100]}",
                                        },
                                    }
                                    yield f"event: error\ndata: {json.dumps(error_event)}\n\n"
                                    sequence_number += 1
                                    has_error = True
                            elif chunk_data.startswith("event:") or chunk_data.strip() == "":
                                # Pass through SSE event lines and empty lines (SSE formatting)
                                yield chunk_data
                            else:
                                # Unknown format - emit as error
                                error_event = {
                                    "type": "error",
                                    "sequence_number": sequence_number,
                                    "error": {
                                        "type": "invalid_response",
                                        "message": "Unexpected chunk format",
                                    },
                                }
                                yield f"event: error\ndata: {json.dumps(error_event)}\n\n"
                                sequence_number += 1
                                has_error = True

                    stream_release_handled = True
                    provider = attempt_provider
                    model = request_model

                    # SSE streaming headers to prevent buffering by proxies/nginx
                    stream_headers = {
                        "X-Accel-Buffering": "no",
                        "Cache-Control": "no-cache, no-transform",
                        "Connection": "keep-alive",
                    }

                    return StreamingResponse(
                        response_stream_generator(),
                        media_type="text/event-stream",
                        headers=stream_headers,
                    )
                except Exception as exc:
                    http_exc = map_provider_error(attempt_provider, request_model, exc)

                if http_exc is None:
                    continue

                last_http_exc = http_exc
                if idx < len(provider_chain) - 1 and should_failover(http_exc):
                    next_provider = provider_chain[idx + 1]
                    logger.warning(
                        "Provider '%s' failed with status %s (%s). Falling back to '%s'.",
                        attempt_provider,
                        http_exc.status_code,
                        http_exc.detail,
                        next_provider,
                    )
                    continue

                # If this is a 402 (Payment Required) error and we've exhausted the chain,
                # rebuild the chain allowing payment failover to try alternative providers
                if http_exc.status_code == 402 and idx == len(provider_chain) - 1:
                    extended_chain = build_provider_failover_chain(provider)
                    extended_chain = enforce_model_failover_rules(
                        original_model, extended_chain, allow_payment_failover=True
                    )
                    extended_chain = filter_by_circuit_breaker(original_model, extended_chain)
                    # Find providers we haven't tried yet
                    new_providers = [p for p in extended_chain if p not in provider_chain]
                    if new_providers:
                        logger.warning(
                            "Provider '%s' returned 402 (Payment Required). "
                            "Extending failover chain with: %s",
                            attempt_provider,
                            new_providers,
                        )
                        provider_chain.extend(new_providers)
                        continue

                raise http_exc

            raise last_http_exc or HTTPException(status_code=502, detail="Upstream error")

        # Non-streaming response
        start = time.monotonic()
        processed = None
        model = original_model
        provider = "onerouter"  # Default, will be updated by handler

        try:
            logger.info(
                f"[Unified Handler] Processing Responses API request for model {original_model}"
            )

            # Convert to OpenAI format for adapter
            adapter = OpenAIChatAdapter()
            internal_request = adapter.to_internal_request(
                {"messages": messages, "model": original_model, "stream": False, **optional}
            )

            # Create unified handler with user context
            handler = ChatInferenceHandler(api_key, background_tasks)

            # Process request through unified pipeline
            internal_response = await handler.process(internal_request)

            # Convert internal response back to OpenAI format
            processed = adapter.from_internal_response(internal_response)

            # Extract values for postprocessing (maintain compatibility)
            provider = internal_response.provider_used or "onerouter"
            model = internal_response.model or original_model

            logger.info(
                f"[Unified Handler] Successfully processed Responses request: provider={provider}, model={model}"
            )

        except Exception as exc:
            # Map any errors to HTTPException
            logger.error(f"[Unified Handler] Error: {type(exc).__name__}: {exc}", exc_info=True)
            if isinstance(exc, HTTPException):
                raise
            # Map provider-specific errors
            http_exc = map_provider_error(
                provider if "provider" in locals() else "onerouter",
                model if "model" in locals() else original_model,
                exc,
            )
            raise http_exc

        if processed is None:
            raise HTTPException(status_code=502, detail="Upstream error")

        elapsed = max(0.001, time.monotonic() - start)

        # === 4) Usage, pricing, final checks ===
        usage = processed.get("usage", {}) or {}
        total_tokens = usage.get("total_tokens", 0)
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        post_plan = await _to_thread(enforce_plan_limits, user["id"], total_tokens, environment_tag)
        if not post_plan.get("allowed", False):
            raise HTTPException(
                status_code=429, detail=f"Plan limit exceeded: {post_plan.get('reason', 'unknown')}"
            )

        if trial.get("is_trial") and not trial.get("is_expired"):
            try:
                await _to_thread(
                    track_trial_usage,
                    api_key,
                    total_tokens,
                    1,
                    model_id=request_model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
            except Exception as e:
                logger.warning("Failed to track trial usage: %s", e)

        if not trial.get("is_trial", False):
            rl_final = await rate_limit_mgr.check_rate_limit(api_key, tokens_used=total_tokens)
            if not rl_final.allowed:
                await _to_thread(
                    create_rate_limit_alert,
                    api_key,
                    "rate_limit_exceeded",
                    {
                        "reason": rl_final.reason,
                        "retry_after": rl_final.retry_after,
                        "remaining_requests": rl_final.remaining_requests,
                        "remaining_tokens": rl_final.remaining_tokens,
                        "tokens_requested": total_tokens,
                    },
                )
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded: {rl_final.reason}",
                    headers=(
                        {"Retry-After": str(rl_final.retry_after)} if rl_final.retry_after else None
                    ),
                )

        cost = await _handle_credits_and_usage(
            api_key=api_key,
            user=user,
            model=model,
            trial=trial,
            total_tokens=total_tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            elapsed_ms=int(elapsed * 1000),
            is_streaming=False,
        )

        await _to_thread(increment_api_key_usage, api_key)

        # Record Prometheus metrics and passive health monitoring
        await _record_inference_metrics_and_health(
            provider=provider,
            model=model,
            elapsed_seconds=elapsed,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=cost,
            success=True,
            error_message=None,
        )

        # === 4.5) Log activity for tracking and analytics ===
        try:
            provider_name = get_provider_from_model(model)
            speed = total_tokens / elapsed if elapsed > 0 else 0
            await _to_thread(
                log_activity,
                user_id=user["id"],
                model=model,
                provider=provider_name,
                tokens=total_tokens,
                cost=cost if not trial.get("is_trial", False) else 0.0,
                speed=speed,
                finish_reason=(processed.get("choices") or [{}])[0].get("finish_reason", "stop"),
                app="API",
                metadata={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "endpoint": "/v1/responses",
                    "session_id": session_id,
                    "gateway": provider,  # Track which gateway was used
                },
            )
        except Exception as e:
            logger.error(
                f"Failed to log activity for user {user['id']}, model {model}: {e}", exc_info=True
            )

        # === 5) History ===
        # Validate session_id before attempting to save
        if session_id:
            if session_id < -2147483648 or session_id > 2147483647:
                logger.warning(
                    "Invalid session_id %s during /v1/responses history save: out of PostgreSQL integer range. Skipping history save.",
                    sanitize_for_logging(str(session_id)),
                )
                session_id = None

        if session_id:
            try:
                session = await _to_thread(get_chat_session, session_id, user["id"])
                if session:
                    last_user = None
                    for m in reversed(messages):
                        if m.get("role") == "user":
                            last_user = m
                            break
                    if last_user:
                        # Extract text content from multimodal content if needed
                        user_content = last_user.get("content", "")
                        if isinstance(user_content, list):
                            # Extract text from multimodal content, filtering empty strings
                            text_parts = []
                            for item in user_content:
                                if isinstance(item, dict) and item.get("type") == "text":
                                    text = item.get("text")
                                    if text is not None:
                                        text_parts.append(str(text))
                            user_content = (
                                " ".join(t for t in text_parts if t)
                                if text_parts
                                else "[multimodal content]"
                            )

                        await _to_thread(
                            save_chat_message,
                            session_id,
                            "user",
                            user_content,
                            model,
                            0,
                            user["id"],
                        )

                    # Safely extract assistant content (handle None values in choices)
                    choices = processed.get("choices") or [{}]
                    first_choice = choices[0] if choices else {}
                    message = first_choice.get("message") or {}
                    assistant_content = message.get("content", "")
                    if assistant_content:
                        await _to_thread(
                            save_chat_message,
                            session_id,
                            "assistant",
                            assistant_content,
                            model,
                            total_tokens,
                            user["id"],
                        )
            except Exception as e:
                logger.error(
                    f"Failed to save chat history for session {session_id}, user {user['id']}: {e}",
                    exc_info=True,
                )

        # === 6) Transform response format: choices -> output ===
        output = []
        for choice in processed.get("choices", []):
            output_item = {
                "index": choice.get("index", 0),
                "finish_reason": choice.get("finish_reason"),
            }

            # Transform message to response format
            if "message" in choice:
                msg = choice["message"]
                output_item["role"] = msg.get("role", "assistant")
                output_item["content"] = msg.get("content", "")

                # Include function/tool calls if present
                if "function_call" in msg:
                    output_item["function_call"] = msg["function_call"]
                if "tool_calls" in msg:
                    output_item["tool_calls"] = msg["tool_calls"]

            output.append(output_item)

        response = {
            "id": processed.get("id"),
            "object": "response",
            "created": processed.get("created"),
            "model": processed.get("model"),
            "output": output,
            "usage": usage,
        }

        # Add gateway usage metadata
        response["gateway_usage"] = {
            "tokens_charged": total_tokens,
            "request_ms": int(elapsed * 1000),
        }
        if not trial.get("is_trial", False):
            response["gateway_usage"]["cost_usd"] = round(cost, 6)

        # === 7) Log to Braintrust ===
        try:
            logger.info(
                f"[Braintrust] Starting log for request_id={request_id}, model={model}, "
                f"endpoint=/v1/responses, available={check_braintrust_available()}, span_type={type(span).__name__}"
            )
            # Convert input messages to loggable format, safely handling None values
            input_messages = []
            for inp_msg in req.input:
                if inp_msg is None:
                    continue
                content = inp_msg.content
                if content is None:
                    content = ""
                elif isinstance(content, list):
                    # Safely extract text from multimodal content, filtering None items
                    text_parts = []
                    for item in content:
                        if item is None:
                            continue
                        if isinstance(item, dict):
                            # Extract text field if present
                            text = item.get("text")
                            if text is not None:
                                text_parts.append(str(text))
                        elif isinstance(item, str):
                            text_parts.append(item)
                    content = " ".join(text_parts) if text_parts else ""
                elif not isinstance(content, str):
                    content = str(content)
                input_messages.append({"role": inp_msg.role, "content": content})

            # Safely extract output content for Braintrust logging
            bt_output = ""
            output_list = response.get("output")
            if isinstance(output_list, list) and len(output_list) > 0:
                first_output = output_list[0]
                if isinstance(first_output, dict):
                    bt_content = first_output.get("content")
                    # Handle case where content is None, a string, or a list (multimodal)
                    if bt_content is None:
                        bt_output = ""
                    elif isinstance(bt_content, str):
                        bt_output = bt_content
                    elif isinstance(bt_content, list):
                        # Extract text from multimodal content, filtering empty strings
                        texts = []
                        for item in bt_content:
                            if item is None:
                                continue
                            if isinstance(item, dict):
                                text = item.get("text")
                                if text is not None:
                                    texts.append(str(text))
                            else:
                                texts.append(str(item))
                        bt_output = " ".join(t for t in texts if t)
                    else:
                        bt_output = str(bt_content)

            # Safely get user_id and environment for anonymous users (user=None)
            bt_user_id = user["id"] if user else "anonymous"
            bt_environment = user.get("environment_tag", "live") if user else "live"
            bt_is_trial = trial.get("is_trial", False) if trial else False
            logger.info(
                f"[Braintrust] Logging span: user_id={bt_user_id}, model={model}, tokens={total_tokens}"
            )
            span.log(
                input=input_messages,
                output=bt_output,
                metrics={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    "latency_ms": int(elapsed * 1000),
                    "cost_usd": cost if not bt_is_trial else 0.0,
                },
                metadata={
                    "model": model,
                    "provider": provider,
                    "user_id": bt_user_id,
                    "session_id": session_id,
                    "is_trial": bt_is_trial,
                    "environment": bt_environment,
                    "endpoint": "/v1/responses",
                },
            )
            span.end()
            # Flush to ensure data is sent to Braintrust
            braintrust_flush()
            logger.info(
                f"[Braintrust] Successfully logged and flushed span for request_id={request_id}"
            )
        except Exception as e:
            logger.warning(f"[Braintrust] Failed to log to Braintrust: {e}", exc_info=True)

        # Save chat completion request metadata to database with cost tracking - run as background task
        # Calculate cost breakdown for analytics
        from src.services.pricing import get_model_pricing

        pricing_info = get_model_pricing(model)
        input_cost = prompt_tokens * pricing_info.get("prompt", 0)
        output_cost = completion_tokens * pricing_info.get("completion", 0)

        background_tasks.add_task(
            save_chat_completion_request_with_cost,
            request_id=request_id,
            model_name=model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            processing_time_ms=int(elapsed * 1000),
            cost_usd=cost,
            input_cost_usd=input_cost,
            output_cost_usd=output_cost,
            pricing_source="calculated",
            status="completed",
            error_message=None,
            user_id=user["id"],
            provider_name=provider,
            model_id=None,
            api_key_id=api_key_id,
            is_anonymous=False,  # /v1/responses requires authentication
        )

        return response

    except HTTPException as http_exc:
        # Save failed request for HTTPException errors
        if request_id:
            try:
                # Calculate elapsed time
                error_elapsed = time.monotonic() - start if "start" in dir() else 0

                # Save failed request to database with cost tracking (costs are 0 for failed requests)
                await _to_thread(
                    save_chat_completion_request_with_cost,
                    request_id=request_id,
                    model_name=(
                        model
                        if "model" in dir()
                        else original_model
                        if "original_model" in dir()
                        else "unknown"
                    ),
                    input_tokens=prompt_tokens if "prompt_tokens" in dir() else 0,
                    output_tokens=0,  # No output on error
                    processing_time_ms=int(error_elapsed * 1000),
                    cost_usd=0.0,
                    input_cost_usd=0.0,
                    output_cost_usd=0.0,
                    pricing_source="error",
                    status="failed",
                    error_message=f"HTTP {http_exc.status_code}: {http_exc.detail}",
                    user_id=user["id"] if user and "user" in dir() else None,
                    provider_name=provider if "provider" in dir() else None,
                    model_id=None,
                    api_key_id=api_key_id if "api_key_id" in dir() else None,
                    is_anonymous=False,  # /v1/responses requires authentication
                )
            except Exception as save_err:
                logger.debug(f"Failed to save failed request metadata: {save_err}")
        raise
    except Exception as e:
        logger.exception("Unhandled server error in unified_responses")

        # Save failed request for unexpected errors
        if request_id:
            try:
                # Calculate elapsed time
                error_elapsed = time.monotonic() - start if "start" in dir() else 0

                # Save failed request to database with cost tracking (costs are 0 for failed requests)
                await _to_thread(
                    save_chat_completion_request_with_cost,
                    request_id=request_id,
                    model_name=(
                        model
                        if "model" in dir()
                        else original_model
                        if "original_model" in dir()
                        else "unknown"
                    ),
                    input_tokens=prompt_tokens if "prompt_tokens" in dir() else 0,
                    output_tokens=0,  # No output on error
                    processing_time_ms=int(error_elapsed * 1000),
                    cost_usd=0.0,
                    input_cost_usd=0.0,
                    output_cost_usd=0.0,
                    pricing_source="error",
                    status="failed",
                    error_message=f"{type(e).__name__}: {str(e)[:500]}",
                    user_id=user["id"] if user and "user" in dir() else None,
                    provider_name=provider if "provider" in dir() else None,
                    model_id=None,
                    api_key_id=api_key_id if "api_key_id" in dir() else None,
                    is_anonymous=False,  # /v1/responses requires authentication
                )
            except Exception as save_err:
                logger.debug(f"Failed to save failed request metadata: {save_err}")

        raise APIExceptions.internal_error(operation="unified_responses")
    finally:
        if (
            should_release_concurrency
            and rate_limit_mgr
            and (not req.stream or not stream_release_handled)
        ):
            try:
                await rate_limit_mgr.release_concurrency(api_key)
            except Exception as exc:
                logger.debug("Failed to release concurrency for %s: %s", mask_key(api_key), exc)


# Log successful module load - this should appear in startup logs if chat.py loads correctly
logger.info("✅ Chat module fully loaded - all routes registered successfully")
logger.info(f"   Total routes in router: {len(router.routes)}")

# Log any provider import errors that occurred during safe imports
if _provider_import_errors:
    logger.warning(f"⚠  Provider import warnings ({len(_provider_import_errors)} failed):")
    for provider_name, error_msg in _provider_import_errors.items():
        logger.warning(f"     - {provider_name}: {error_msg}")
else:
    logger.info("✓ All provider clients loaded successfully")
