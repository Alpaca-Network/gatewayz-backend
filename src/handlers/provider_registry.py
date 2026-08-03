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
        error_msg = f"Failed to load {provider_name} provider client: {type(e).__name__}: {str(e)}"
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
    "xai": ["make_xai_request_openai", "process_xai_response", "make_xai_request_openai_stream"],
    "cerebras": [
        "make_cerebras_request_openai",
        "process_cerebras_response",
        "make_cerebras_request_openai_stream",
    ],
    "google_vertex": [
        "make_google_vertex_request_openai",
        "process_google_vertex_response",
        "make_google_vertex_request_openai_stream",
    ],
    "alibaba_cloud": [
        "make_alibaba_cloud_request_openai",
        "process_alibaba_cloud_response",
        "make_alibaba_cloud_request_openai_stream",
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
}


# ---------------------------------------------------------------------------
# OpenAI-compatible providers served by the config-driven adapter
# ---------------------------------------------------------------------------
def _safe_adapter_routing(slug: str) -> ProviderRouting:
    """Build a PROVIDER_ROUTING entry from the openai_compat adapter registry.

    Mirrors _safe_import_provider: on import/config failure the entry is
    populated with sentinels that raise an informative 503 when called.
    """
    try:
        from src.services.providers.adapter_configs import ADAPTERS

        adapter = ADAPTERS[slug]
        logger.debug(f"Loaded {slug} provider adapter")
        return {
            "request": adapter.request,
            "process": adapter.process,
            "stream": adapter.stream,
        }
    except Exception as e:
        error_msg = f"Failed to load {slug} provider adapter: {type(e).__name__}: {str(e)}"
        logger.error(error_msg)
        _provider_import_errors[slug] = str(e)
        err_text = str(e)

        def make_error_raiser(func_name: str):
            def sync_error(*args, **kwargs):
                raise HTTPException(
                    status_code=503,
                    detail=f"Provider '{slug}' is unavailable: {func_name} failed to load. Error: {err_text[:100]}",
                )

            return sync_error

        return {
            "request": make_error_raiser("request"),
            "process": make_error_raiser("process"),
            "stream": make_error_raiser("stream"),
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
    "google-vertex": {
        "request": _loaded_functions.get("make_google_vertex_request_openai"),
        "process": _loaded_functions.get("process_google_vertex_response"),
        "stream": _loaded_functions.get("make_google_vertex_request_openai_stream"),
    },
    "alibaba-cloud": {
        "request": _loaded_functions.get("make_alibaba_cloud_request_openai"),
        "process": _loaded_functions.get("process_alibaba_cloud_response"),
        "stream": _loaded_functions.get("make_alibaba_cloud_request_openai_stream"),
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
    # OpenAI-compatible providers consolidated onto the config-driven adapter
    # (src/services/providers/openai_compat.py + adapter_configs.py).
    "deepinfra": _safe_adapter_routing("deepinfra"),
    "together": _safe_adapter_routing("together"),
    "fireworks": _safe_adapter_routing("fireworks"),
    "groq": _safe_adapter_routing("groq"),
    "zai": _safe_adapter_routing("zai"),
    # Tier-2 providers (Task 18)
    "deepseek": _safe_adapter_routing("deepseek"),
    "moonshot": _safe_adapter_routing("moonshot"),
    "minimax": _safe_adapter_routing("minimax"),
    "xiaomi": _safe_adapter_routing("xiaomi"),
}

# Strip disabled providers from routing so they are completely unreachable
PROVIDER_ROUTING = {
    slug: funcs for slug, funcs in PROVIDER_ROUTING.items() if is_provider_enabled(slug)
}
