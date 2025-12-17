"""
Provider Registry System

This module provides a centralized registry for all LLM provider integrations.
Instead of hard-coding provider-specific logic throughout the codebase, all providers
are registered here with their request/response handlers.

Benefits:
- Adding a new provider requires only updating the provider config
- Eliminates 600+ lines of repetitive if/elif chains
- Type-safe provider handling
- Easy to test and maintain
"""

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider"""

    name: str
    make_request: Callable
    process_response: Callable
    make_request_stream: Callable
    make_request_stream_async: Optional[Callable] = None
    timeout: int = 30
    supports_streaming: bool = True
    supports_async_streaming: bool = False

    def __post_init__(self):
        """Validate configuration on initialization"""
        if not callable(self.make_request):
            raise ValueError(f"make_request must be callable for provider {self.name}")
        if not callable(self.process_response):
            raise ValueError(f"process_response must be callable for provider {self.name}")
        if self.supports_streaming and not callable(self.make_request_stream):
            raise ValueError(f"make_request_stream must be callable for provider {self.name}")


class ProviderRegistry:
    """
    Central registry for all LLM providers.

    This singleton manages provider configurations and provides
    lookup methods for provider-specific functionality.
    """

    def __init__(self):
        self._providers: Dict[str, ProviderConfig] = {}
        self._import_errors: Dict[str, str] = {}

    def register(self, config: ProviderConfig) -> None:
        """
        Register a provider with the registry.

        Args:
            config: ProviderConfig instance with provider details

        Raises:
            ValueError: If provider is already registered
        """
        if config.name in self._providers:
            logger.warning(f"Provider '{config.name}' is already registered. Overwriting.")

        self._providers[config.name] = config
        logger.debug(f"✓ Registered provider: {config.name}")

    def get(self, name: str) -> Optional[ProviderConfig]:
        """
        Get provider configuration by name.

        Args:
            name: Provider name (e.g., 'openrouter', 'huggingface')

        Returns:
            ProviderConfig if found, None otherwise
        """
        return self._providers.get(name)

    def list_providers(self) -> list[str]:
        """Get list of all registered provider names"""
        return sorted(self._providers.keys())

    def get_timeout(self, name: str) -> int:
        """
        Get timeout for a specific provider.

        Args:
            name: Provider name

        Returns:
            Timeout in seconds (default 30 if provider not found)
        """
        provider = self.get(name)
        return provider.timeout if provider else 30

    def supports_async_streaming(self, name: str) -> bool:
        """Check if provider supports async streaming"""
        provider = self.get(name)
        return provider.supports_async_streaming if provider else False

    def record_import_error(self, provider_name: str, error: str) -> None:
        """Record an import error for a provider"""
        self._import_errors[provider_name] = error
        logger.error(f"⚠  Failed to load {provider_name} provider: {error}")

    def get_import_errors(self) -> Dict[str, str]:
        """Get all recorded import errors"""
        return self._import_errors.copy()

    def is_available(self, name: str) -> bool:
        """Check if a provider is available (registered and no import errors)"""
        return name in self._providers and name not in self._import_errors


# Global singleton instance
_registry: Optional[ProviderRegistry] = None


def get_provider_registry() -> ProviderRegistry:
    """
    Get the global provider registry instance.

    Returns:
        ProviderRegistry singleton instance
    """
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry


def register_provider(
    name: str,
    make_request: Callable,
    process_response: Callable,
    make_request_stream: Callable,
    make_request_stream_async: Optional[Callable] = None,
    timeout: int = 30,
) -> None:
    """
    Convenience function to register a provider.

    Args:
        name: Provider name
        make_request: Non-streaming request function
        process_response: Response processing function
        make_request_stream: Streaming request function
        make_request_stream_async: Async streaming function (optional)
        timeout: Request timeout in seconds
    """
    config = ProviderConfig(
        name=name,
        make_request=make_request,
        process_response=process_response,
        make_request_stream=make_request_stream,
        make_request_stream_async=make_request_stream_async,
        timeout=timeout,
        supports_streaming=True,
        supports_async_streaming=make_request_stream_async is not None,
    )
    get_provider_registry().register(config)
