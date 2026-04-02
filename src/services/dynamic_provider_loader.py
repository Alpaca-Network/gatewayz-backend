"""
Dynamic provider module loader.

Replaces hardcoded PROVIDER_FETCH_FUNCTIONS dispatch with DB-driven
importlib lookups.  Falls back to the static dict on ImportError.
"""

import importlib
import logging
import time
from typing import Callable

logger = logging.getLogger(__name__)

_ALLOWED_MODULE_PREFIX = "src.services."

# Module-level cache: slug -> loaded function (avoids repeated importlib calls)
# Uses a (function, timestamp) tuple so entries can expire.
_fetch_models_cache: dict[str, tuple[Callable | None, float]] = {}
_LOADER_CACHE_TTL = 300  # 5 minutes — also cleared by refresh_registry_cache()


def _derive_function_name(slug: str, prefix: str = "fetch_models_from_") -> str:
    """Derive the conventional function name from a provider slug.

    E.g. ``"cloudflare-workers-ai"`` -> ``"fetch_models_from_cloudflare_workers_ai"``
    """
    return prefix + slug.replace("-", "_")


def _load_function(module_path: str, function_name: str) -> Callable | None:
    """Import *module_path* and return *function_name* from it."""
    if not module_path.startswith(_ALLOWED_MODULE_PREFIX):
        logger.error(
            "Rejecting dynamic import: module path %r is outside allowed prefix %r",
            module_path,
            _ALLOWED_MODULE_PREFIX,
        )
        return None
    try:
        module = importlib.import_module(module_path)
        candidate = getattr(module, function_name, None)
        if candidate is not None and not callable(candidate):
            logger.warning(
                "Dynamic import resolved non-callable: %s.%s",
                module_path,
                function_name,
            )
            return None
        return candidate
    except ImportError as exc:
        logger.warning("Dynamic import failed: %s.%s — %s", module_path, function_name, exc)
        return None


def get_fetch_models_function(slug: str) -> Callable | None:
    """Return the ``fetch_models_from_*`` function for *slug*.

    Lookup order:
    1. Module-level cache (if fresh).
    2. DB fields via gateway registry (``fetch_module_path`` / ``fetch_function_name``).
    3. Hardcoded ``_FALLBACK_FETCH_FUNCTIONS`` fallback.
    """
    now = time.monotonic()
    cached = _fetch_models_cache.get(slug)
    if cached is not None:
        func, ts = cached
        if (now - ts) < _LOADER_CACHE_TTL:
            return func

    func = _resolve_from_registry(slug)

    if func is None:
        func = _fallback_fetch_functions().get(slug)

    _fetch_models_cache[slug] = (func, now)
    return func


def invalidate_loader_cache() -> None:
    """Clear the loaded-function caches (e.g. after a registry refresh)."""
    _fetch_models_cache.clear()


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _resolve_from_registry(slug: str) -> Callable | None:
    """Try to dynamically import the fetch_models_from_* function using DB-provided module path."""
    try:
        from src.services.gateway_registry import get_gateway_registry

        registry = get_gateway_registry()
        entry = registry.get(slug)
        if not entry:
            return None

        module_path = entry.get("fetch_module_path")
        if not module_path:
            return None

        # Use explicit function name if set (e.g. huggingface), otherwise derive
        func_name = entry.get("fetch_function_name") or _derive_function_name(slug)
        return _load_function(module_path, func_name)
    except Exception as exc:
        logger.warning("Registry lookup failed for %s: %s", slug, exc)
        return None


def _fallback_fetch_functions() -> dict[str, Callable]:
    """Lazy import of the hardcoded fallback dict."""
    try:
        from src.services.model_catalog_sync import _FALLBACK_FETCH_FUNCTIONS

        return _FALLBACK_FETCH_FUNCTIONS
    except ImportError:
        return {}
