"""
Provider Loader

This module handles loading and registering all LLM provider clients.
It replaces the manual import and registration code in chat.py.

Usage:
    from src.services.provider_loader import load_all_providers

    # Load all providers (call once at startup)
    load_all_providers()

    # Use providers via registry
    from src.services.provider_registry import get_provider_registry
    registry = get_provider_registry()
    provider = registry.get("openrouter")
"""

import logging
from typing import Any, Dict

from fastapi import HTTPException

from src.config.providers import PROVIDER_CONFIGS, get_module_name
from src.services.provider_registry import (
    ProviderConfig,
    get_provider_registry,
)

logger = logging.getLogger(__name__)


def _safe_import_provider(provider_name: str, module_name: str) -> Dict[str, Any]:
    """
    Safely import provider functions with error logging.

    Args:
        provider_name: Display name of the provider (e.g., "openrouter")
        module_name: Python module name (e.g., "openrouter" or "google_vertex")

    Returns:
        dict: Dictionary with function references, or sentinel error functions if import fails
    """
    try:
        module_path = f"src.services.{module_name}_client"

        # Determine which functions to import based on the provider
        imports_list = [
            f"make_{module_name}_request_openai",
            f"process_{module_name}_response",
            f"make_{module_name}_request_openai_stream",
        ]

        # Try to import the async streaming function if available
        async_stream_func_name = f"make_{module_name}_request_openai_stream_async"

        module = __import__(module_path, fromlist=imports_list + [async_stream_func_name])
        result = {}

        # Import standard functions
        for import_name in imports_list:
            result[import_name] = getattr(module, import_name)

        # Try to import async streaming function
        try:
            result[async_stream_func_name] = getattr(module, async_stream_func_name)
        except AttributeError:
            result[async_stream_func_name] = None

        logger.debug(f"✓ Loaded {provider_name} provider client")
        return result

    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        logger.error(f"⚠  Failed to load {provider_name} provider client: {error_msg}")

        # Record the error in registry
        get_provider_registry().record_import_error(provider_name, error_msg)

        # Return sentinel functions that raise informative errors when called
        def make_error_raiser(prov_name: str, func_name: str, error: str):
            def error_func(*args, **kwargs):
                raise HTTPException(
                    status_code=503,
                    detail=f"Provider '{prov_name}' is unavailable: {func_name} failed to load. Error: {str(error)[:100]}"
                )
            return error_func

        return {
            import_name: make_error_raiser(provider_name, import_name, error_msg)
            for import_name in imports_list + [async_stream_func_name]
        }


def load_provider(provider_name: str, config: Dict[str, Any]) -> bool:
    """
    Load and register a single provider.

    Args:
        provider_name: Name of the provider (e.g., "openrouter")
        config: Provider configuration dict

    Returns:
        bool: True if successfully loaded, False otherwise
    """
    try:
        module_name = config.get("module_name", provider_name.replace("-", "_"))
        timeout = config.get("timeout", 30)
        supports_async_streaming = config.get("supports_async_streaming", False)

        # Import provider functions
        provider_funcs = _safe_import_provider(provider_name, module_name)

        # Create provider configuration
        provider_config = ProviderConfig(
            name=provider_name,
            make_request=provider_funcs[f"make_{module_name}_request_openai"],
            process_response=provider_funcs[f"process_{module_name}_response"],
            make_request_stream=provider_funcs[f"make_{module_name}_request_openai_stream"],
            make_request_stream_async=provider_funcs.get(
                f"make_{module_name}_request_openai_stream_async"
            ),
            timeout=timeout,
            supports_async_streaming=supports_async_streaming,
        )

        # Register with global registry
        get_provider_registry().register(provider_config)

        return True

    except Exception as e:
        logger.error(f"Failed to load provider {provider_name}: {e}")
        return False


def load_all_providers() -> None:
    """
    Load and register all providers from configuration.

    This should be called once at application startup.
    Replaces the manual provider imports in chat.py (lines 123-405).
    """
    logger.info("🔄 Loading all LLM providers...")

    loaded_count = 0
    failed_count = 0

    for provider_name, config in PROVIDER_CONFIGS.items():
        success = load_provider(provider_name, config)
        if success:
            loaded_count += 1
        else:
            failed_count += 1

    registry = get_provider_registry()
    logger.info(
        f"✓ Provider loading complete: {loaded_count} loaded, {failed_count} failed"
    )
    logger.info(f"📋 Available providers: {', '.join(registry.list_providers())}")

    # Log any import errors
    import_errors = registry.get_import_errors()
    if import_errors:
        logger.warning(f"⚠  {len(import_errors)} provider(s) failed to load:")
        for provider, error in import_errors.items():
            logger.warning(f"   - {provider}: {error[:100]}")


def get_provider_or_error(provider_name: str) -> ProviderConfig:
    """
    Get a provider configuration or raise an error if not available.

    Args:
        provider_name: Name of the provider

    Returns:
        ProviderConfig: Provider configuration

    Raises:
        HTTPException: If provider is not available
    """
    registry = get_provider_registry()
    provider = registry.get(provider_name)

    if provider is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider: {provider_name}. Available providers: {', '.join(registry.list_providers())}"
        )

    return provider
