"""
Provider Registry - Canonical registry of provider functions and routing.

Extracted from src/routes/chat.py to break the circular dependency where
chat_handler.py imports PROVIDER_ROUTING from chat.py, which itself imports
chat_handler.py.

The global injection loop is **not** performed here; chat.py still runs it
so that test patches against `src.routes.chat.make_openrouter_request_openai`
etc. continue to work.
"""

import logging

from fastapi import HTTPException

from src.services.providers.base import ProviderRouting

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider import error tracking
# ---------------------------------------------------------------------------
_provider_import_errors: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Safe provider import helper
# ---------------------------------------------------------------------------
def _safe_import_provider(provider_name: str, imports_list: list[str]) -> dict:
    """Safely import provider functions with error logging.

    Returns a dict with either:
    - Real functions if import succeeds
    - Sentinel functions that raise HTTPException if used
    """
    try:
        module_path = f"src.services.providers.{provider_name}_client"
        module = __import__(module_path, fromlist=imports_list)
        result = {}
        for import_name in imports_list:
            result[import_name] = getattr(module, import_name)
        logger.debug(f"Loaded {provider_name} provider client")
        return result
    except Exception as e:
        error_msg = (
            f"Failed to load {provider_name} provider client: {type(e).__name__}: {str(e)}"
        )
        logger.error(error_msg)
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


# ---------------------------------------------------------------------------
# Provider function registry
# ---------------------------------------------------------------------------
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
    "openai": [
        "make_openai_request",
        "process_openai_response",
        "make_openai_request_stream",
    ],
    "anthropic": [
        "make_anthropic_request",
        "process_anthropic_response",
        "make_anthropic_request_stream",
    ],
    "deepinfra": [
        "make_deepinfra_request_openai",
        "process_deepinfra_response",
        "make_deepinfra_request_openai_stream",
    ],
    "nebius": [
        "make_nebius_request_openai",
        "process_nebius_response",
        "make_nebius_request_openai_stream",
    ],
    "canopywave": [
        "make_canopywave_request_openai",
        "process_canopywave_response",
        "make_canopywave_request_openai_stream",
    ],
}

# ---------------------------------------------------------------------------
# Load all providers and build PROVIDER_ROUTING
# ---------------------------------------------------------------------------
from src.utils.provider_filter import is_provider_enabled

# Load providers and expose functions to this module's global namespace
_loaded_functions: dict[str, object] = {}
for _provider_name, _function_names in PROVIDER_FUNCTIONS.items():
    if not is_provider_enabled(_provider_name):
        logger.debug("Skipping disabled provider: %s", _provider_name)
        continue
    _provider_module = _safe_import_provider(_provider_name, _function_names)
    for _func_name in _function_names:
        _loaded_functions[_func_name] = _provider_module.get(_func_name)

# Also inject into module globals so the PROVIDER_ROUTING dict below can
# reference names directly (identical to how chat.py originally worked).
globals().update(_loaded_functions)

# Provider routing registry - maps provider names (with hyphens) to their functions.
# This eliminates the need for massive if-elif chains (~750 lines reduced to ~50 lines).
PROVIDER_ROUTING: dict[str, ProviderRouting] = {
    "featherless": {
        "request": _loaded_functions.get("make_featherless_request_openai"),
        "process": _loaded_functions.get("process_featherless_response"),
        "stream": _loaded_functions.get("make_featherless_request_openai_stream"),
    },
    "fireworks": {
        "request": _loaded_functions.get("make_fireworks_request_openai"),
        "process": _loaded_functions.get("process_fireworks_response"),
        "stream": _loaded_functions.get("make_fireworks_request_openai_stream"),
    },
    "together": {
        "request": _loaded_functions.get("make_together_request_openai"),
        "process": _loaded_functions.get("process_together_response"),
        "stream": _loaded_functions.get("make_together_request_openai_stream"),
    },
    "huggingface": {
        "request": _loaded_functions.get("make_huggingface_request_openai"),
        "process": _loaded_functions.get("process_huggingface_response"),
        "stream": _loaded_functions.get("make_huggingface_request_openai_stream"),
    },
    "aimo": {
        "request": _loaded_functions.get("make_aimo_request_openai"),
        "process": _loaded_functions.get("process_aimo_response"),
        "stream": _loaded_functions.get("make_aimo_request_openai_stream"),
    },
    "xai": {
        "request": _loaded_functions.get("make_xai_request_openai"),
        "process": _loaded_functions.get("process_xai_response"),
        "stream": _loaded_functions.get("make_xai_request_openai_stream"),
    },
    "cerebras": {
        "request": _loaded_functions.get("make_cerebras_request_openai"),
        "process": _loaded_functions.get("process_cerebras_response"),
        "stream": _loaded_functions.get("make_cerebras_request_openai_stream"),
    },
    "chutes": {
        "request": _loaded_functions.get("make_chutes_request_openai"),
        "process": _loaded_functions.get("process_chutes_response"),
        "stream": _loaded_functions.get("make_chutes_request_openai_stream"),
    },
    "near": {
        "request": _loaded_functions.get("make_near_request_openai"),
        "process": _loaded_functions.get("process_near_response"),
        "stream": _loaded_functions.get("make_near_request_openai_stream"),
    },
    "google-vertex": {
        "request": _loaded_functions.get("make_google_vertex_request_openai"),
        "process": _loaded_functions.get("process_google_vertex_response"),
        "stream": _loaded_functions.get("make_google_vertex_request_openai_stream"),
    },
    "alpaca-network": {
        "request": _loaded_functions.get("make_alpaca_network_request_openai"),
        "process": _loaded_functions.get("process_alpaca_network_response"),
        "stream": _loaded_functions.get("make_alpaca_network_request_openai_stream"),
    },
    "alibaba-cloud": {
        "request": _loaded_functions.get("make_alibaba_cloud_request_openai"),
        "process": _loaded_functions.get("process_alibaba_cloud_response"),
        "stream": _loaded_functions.get("make_alibaba_cloud_request_openai_stream"),
    },
    "clarifai": {
        "request": _loaded_functions.get("make_clarifai_request_openai"),
        "process": _loaded_functions.get("process_clarifai_response"),
        "stream": _loaded_functions.get("make_clarifai_request_openai_stream"),
    },
    "groq": {
        "request": _loaded_functions.get("make_groq_request_openai"),
        "process": _loaded_functions.get("process_groq_response"),
        "stream": _loaded_functions.get("make_groq_request_openai_stream"),
    },
    "cloudflare-workers-ai": {
        "request": _loaded_functions.get("make_cloudflare_workers_ai_request_openai"),
        "process": _loaded_functions.get("process_cloudflare_workers_ai_response"),
        "stream": _loaded_functions.get("make_cloudflare_workers_ai_request_openai_stream"),
    },
    "morpheus": {
        "request": _loaded_functions.get("make_morpheus_request_openai"),
        "process": _loaded_functions.get("process_morpheus_response"),
        "stream": _loaded_functions.get("make_morpheus_request_openai_stream"),
    },
    "simplismart": {
        "request": _loaded_functions.get("make_simplismart_request_openai"),
        "process": _loaded_functions.get("process_simplismart_response"),
        "stream": _loaded_functions.get("make_simplismart_request_openai_stream"),
    },
    "sybil": {
        "request": _loaded_functions.get("make_sybil_request_openai"),
        "process": _loaded_functions.get("process_sybil_response"),
        "stream": _loaded_functions.get("make_sybil_request_openai_stream"),
    },
    "nosana": {
        "request": _loaded_functions.get("make_nosana_request_openai"),
        "process": _loaded_functions.get("process_nosana_response"),
        "stream": _loaded_functions.get("make_nosana_request_openai_stream"),
    },
    "zai": {
        "request": _loaded_functions.get("make_zai_request_openai"),
        "process": _loaded_functions.get("process_zai_response"),
        "stream": _loaded_functions.get("make_zai_request_openai_stream"),
    },
    "openai": {
        "request": _loaded_functions.get("make_openai_request"),
        "process": _loaded_functions.get("process_openai_response"),
        "stream": _loaded_functions.get("make_openai_request_stream"),
    },
    "anthropic": {
        "request": _loaded_functions.get("make_anthropic_request"),
        "process": _loaded_functions.get("process_anthropic_response"),
        "stream": _loaded_functions.get("make_anthropic_request_stream"),
    },
    "deepinfra": {
        "request": _loaded_functions.get("make_deepinfra_request_openai"),
        "process": _loaded_functions.get("process_deepinfra_response"),
        "stream": _loaded_functions.get("make_deepinfra_request_openai_stream"),
    },
    "nebius": {
        "request": _loaded_functions.get("make_nebius_request_openai"),
        "process": _loaded_functions.get("process_nebius_response"),
        "stream": _loaded_functions.get("make_nebius_request_openai_stream"),
    },
    "canopywave": {
        "request": _loaded_functions.get("make_canopywave_request_openai"),
        "process": _loaded_functions.get("process_canopywave_response"),
        "stream": _loaded_functions.get("make_canopywave_request_openai_stream"),
    },
}

# Strip disabled providers from routing so they are completely unreachable
PROVIDER_ROUTING = {
    slug: funcs for slug, funcs in PROVIDER_ROUTING.items() if is_provider_enabled(slug)
}
