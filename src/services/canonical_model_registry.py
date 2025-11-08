"""
Canonical Model Registry

This module provides a unified registry for models that can be accessed across multiple
providers. It maintains canonical model definitions and keeps them synchronized with
provider-specific catalogs.

Key Features:
- Single source of truth for model metadata (capabilities, pricing, context length)
- Multi-provider routing with priority-based failover
- Automatic synchronization with provider catalogs
- Circuit breaker pattern for provider health
- Backward-compatible with existing single-provider routing

Architecture:
- CanonicalModel: Represents a logical model with all its provider configurations
- CanonicalModelRegistry: Central registry maintaining all canonical models
- RegistrySyncService: Keeps registry in sync with provider fetchers
"""

import logging
from typing import List, Optional, Dict, Any, Set
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.services.multi_provider_registry import (
    MultiProviderModel,
    ProviderConfig,
    MultiProviderRegistry,
    get_registry as get_multi_provider_registry,
)

logger = logging.getLogger(__name__)


@dataclass
class CanonicalModel:
    """
    A canonical model definition that aggregates metadata from multiple providers.

    This represents a logical model (e.g., "gpt-4") that may be available through
    multiple providers (e.g., OpenRouter, Azure OpenAI, etc.)
    """

    id: str  # Canonical ID (what users specify, e.g., "gpt-4", "claude-3-opus")
    name: str  # Display name
    description: Optional[str] = None
    context_length: Optional[int] = None
    modalities: List[str] = field(default_factory=lambda: ["text"])

    # Provider-specific configurations
    providers: List[ProviderConfig] = field(default_factory=list)

    # Metadata
    architecture: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Capabilities
    supports_streaming: bool = False
    supports_function_calling: bool = False
    supports_vision: bool = False
    supports_audio: bool = False

    # Computed fields
    primary_provider: Optional[str] = None  # Cached primary provider name

    def __post_init__(self):
        """Initialize computed fields and validate"""
        if not self.providers:
            logger.warning(f"Canonical model {self.id} has no providers configured")
        else:
            # Sort by priority
            self.providers.sort(key=lambda p: p.priority)
            # Cache primary provider
            enabled = [p for p in self.providers if p.enabled]
            self.primary_provider = enabled[0].name if enabled else None

        # Update timestamps
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)

        # Infer capabilities from providers
        self._infer_capabilities()

    def _infer_capabilities(self):
        """Infer model capabilities from provider configurations"""
        for provider in self.providers:
            if "streaming" in provider.features:
                self.supports_streaming = True
            if "function_calling" in provider.features or "tools" in provider.features:
                self.supports_function_calling = True
            if "vision" in provider.features or "multimodal" in provider.features:
                self.supports_vision = True
            if "audio" in provider.features:
                self.supports_audio = True

    def get_enabled_providers(self) -> List[ProviderConfig]:
        """Get list of enabled providers sorted by priority"""
        return [p for p in self.providers if p.enabled]

    def get_provider_by_name(self, name: str) -> Optional[ProviderConfig]:
        """Get provider configuration by name"""
        for provider in self.providers:
            if provider.name == name:
                return provider
        return None

    def add_provider(self, provider: ProviderConfig) -> None:
        """Add or update a provider configuration"""
        # Remove existing provider with same name
        self.providers = [p for p in self.providers if p.name != provider.name]
        self.providers.append(provider)
        # Re-sort and update primary
        self.__post_init__()

    def to_multi_provider_model(self) -> MultiProviderModel:
        """Convert to MultiProviderModel for compatibility"""
        return MultiProviderModel(
            id=self.id,
            name=self.name,
            description=self.description,
            context_length=self.context_length,
            modalities=self.modalities,
            providers=self.providers,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "context_length": self.context_length,
            "modalities": self.modalities,
            "providers": [
                {
                    "name": p.name,
                    "model_id": p.model_id,
                    "priority": p.priority,
                    "enabled": p.enabled,
                    "cost_per_1k_input": p.cost_per_1k_input,
                    "cost_per_1k_output": p.cost_per_1k_output,
                    "features": p.features,
                }
                for p in self.providers
            ],
            "primary_provider": self.primary_provider,
            "supports_streaming": self.supports_streaming,
            "supports_function_calling": self.supports_function_calling,
            "supports_vision": self.supports_vision,
            "supports_audio": self.supports_audio,
            "architecture": self.architecture,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class CanonicalModelRegistry:
    """
    Central registry for canonical models with provider synchronization support.

    This class maintains the single source of truth for all models and their
    provider configurations. It integrates with the existing MultiProviderRegistry
    for backward compatibility.
    """

    def __init__(self):
        self._models: Dict[str, CanonicalModel] = {}
        self._provider_model_index: Dict[str, Dict[str, str]] = {}  # {provider: {provider_model_id: canonical_id}}
        self._multi_provider_registry = get_multi_provider_registry()
        logger.info("Initialized CanonicalModelRegistry")

    def register_model(self, model: CanonicalModel) -> None:
        """
        Register a canonical model in the registry.

        Args:
            model: The canonical model to register
        """
        self._models[model.id] = model

        # Update provider index for fast lookups
        for provider in model.providers:
            if provider.name not in self._provider_model_index:
                self._provider_model_index[provider.name] = {}
            self._provider_model_index[provider.name][provider.model_id] = model.id

        # Also register in multi-provider registry for backward compatibility
        self._multi_provider_registry.register_model(model.to_multi_provider_model())

        logger.info(
            f"Registered canonical model: {model.id} with {len(model.providers)} providers "
            f"(primary: {model.primary_provider})"
        )

    def register_models(self, models: List[CanonicalModel]) -> None:
        """Register multiple models at once"""
        for model in models:
            self.register_model(model)

    def get_model(self, model_id: str) -> Optional[CanonicalModel]:
        """Get a canonical model by ID"""
        return self._models.get(model_id)

    def get_model_by_provider_id(
        self, provider: str, provider_model_id: str
    ) -> Optional[CanonicalModel]:
        """
        Get a canonical model by provider-specific model ID.

        Args:
            provider: Provider name (e.g., "openrouter", "google-vertex")
            provider_model_id: Provider-specific model ID

        Returns:
            The canonical model if found, None otherwise
        """
        canonical_id = self._provider_model_index.get(provider, {}).get(provider_model_id)
        if canonical_id:
            return self._models.get(canonical_id)
        return None

    def has_model(self, model_id: str) -> bool:
        """Check if a model is registered"""
        return model_id in self._models

    def get_all_models(self) -> List[CanonicalModel]:
        """Get all registered canonical models"""
        return list(self._models.values())

    def get_models_by_provider(self, provider: str) -> List[CanonicalModel]:
        """Get all models available through a specific provider"""
        result = []
        for model in self._models.values():
            if any(p.name == provider and p.enabled for p in model.providers):
                result.append(model)
        return result

    def get_multi_provider_models(self) -> List[CanonicalModel]:
        """Get models that are available through multiple providers"""
        return [m for m in self._models.values() if len(m.get_enabled_providers()) > 1]

    def search_models(
        self,
        query: Optional[str] = None,
        provider: Optional[str] = None,
        modality: Optional[str] = None,
        min_context_length: Optional[int] = None,
        supports_streaming: Optional[bool] = None,
        supports_function_calling: Optional[bool] = None,
    ) -> List[CanonicalModel]:
        """
        Search for models based on various criteria.

        Args:
            query: Text search in ID, name, or description
            provider: Filter by provider availability
            modality: Filter by modality (e.g., "text", "image")
            min_context_length: Minimum context length required
            supports_streaming: Filter by streaming support
            supports_function_calling: Filter by function calling support

        Returns:
            List of matching canonical models
        """
        results = list(self._models.values())

        if query:
            query_lower = query.lower()
            results = [
                m for m in results
                if query_lower in m.id.lower()
                or query_lower in m.name.lower()
                or (m.description and query_lower in m.description.lower())
            ]

        if provider:
            results = [
                m for m in results
                if any(p.name == provider and p.enabled for p in m.providers)
            ]

        if modality:
            results = [m for m in results if modality in m.modalities]

        if min_context_length is not None:
            results = [
                m for m in results
                if m.context_length and m.context_length >= min_context_length
            ]

        if supports_streaming is not None:
            results = [m for m in results if m.supports_streaming == supports_streaming]

        if supports_function_calling is not None:
            results = [m for m in results if m.supports_function_calling == supports_function_calling]

        return results

    def update_provider_config(
        self, model_id: str, provider: str, updates: Dict[str, Any]
    ) -> bool:
        """
        Update provider configuration for a model.

        Args:
            model_id: Canonical model ID
            provider: Provider name
            updates: Dictionary of fields to update

        Returns:
            True if updated, False if model or provider not found
        """
        model = self.get_model(model_id)
        if not model:
            return False

        provider_config = model.get_provider_by_name(provider)
        if not provider_config:
            return False

        # Update fields
        for key, value in updates.items():
            if hasattr(provider_config, key):
                setattr(provider_config, key, value)

        model.updated_at = datetime.now(timezone.utc)
        logger.info(f"Updated provider config for {model_id}/{provider}: {updates}")
        return True

    def get_statistics(self) -> Dict[str, Any]:
        """Get registry statistics"""
        total_models = len(self._models)
        multi_provider_models = len(self.get_multi_provider_models())
        providers_set: Set[str] = set()

        for model in self._models.values():
            for provider in model.providers:
                providers_set.add(provider.name)

        return {
            "total_models": total_models,
            "multi_provider_models": multi_provider_models,
            "single_provider_models": total_models - multi_provider_models,
            "total_providers": len(providers_set),
            "providers": sorted(list(providers_set)),
        }


# Global canonical registry instance
_canonical_registry = CanonicalModelRegistry()


def get_canonical_registry() -> CanonicalModelRegistry:
    """Get the global canonical model registry instance"""
    return _canonical_registry
