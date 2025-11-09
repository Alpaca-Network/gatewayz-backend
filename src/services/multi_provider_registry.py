"""
Multi-Provider Model Registry

This module provides support for models that can be accessed through multiple providers
with automatic failover, priority-based selection, and cost optimization.

The registry maintains a canonical view of models with their provider configurations,
enabling intelligent routing and failover across multiple providers for the same logical model.
"""

import logging
from typing import List, Optional, Dict, Any, Union
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class ProviderConfig:
    """Configuration for a single provider for a model"""

    name: str  # Provider name (e.g., "google-vertex", "openrouter")
    model_id: str  # Provider-specific model ID
    priority: int = 1  # Lower number = higher priority (1 is highest)
    requires_credentials: bool = False  # Whether this provider needs user credentials
    cost_per_1k_input: Optional[float] = None  # Cost in credits per 1k input tokens
    cost_per_1k_output: Optional[float] = None  # Cost in credits per 1k output tokens
    enabled: bool = True  # Whether this provider is currently enabled
    max_tokens: Optional[int] = None  # Max tokens supported by this provider
    features: List[str] = field(default_factory=list)  # Supported features (e.g., "streaming", "function_calling")
    availability: bool = True  # Current availability status
    last_checked: Optional[datetime] = None  # Last time availability was checked
    response_time_ms: Optional[int] = None  # Average response time in milliseconds

    def __post_init__(self):
        """Validate the configuration"""
        if self.priority < 1:
            raise ValueError(f"Priority must be >= 1, got {self.priority}")


@dataclass
class MultiProviderModel:
    """A model that can be accessed through multiple providers"""

    id: str  # Canonical model ID (what users specify)
    name: str  # Display name
    providers: List[ProviderConfig]  # List of provider configurations
    description: Optional[str] = None
    context_length: Optional[int] = None
    modalities: List[str] = field(default_factory=lambda: ["text"])
    categories: List[str] = field(default_factory=list)  # Model categories (e.g., "chat", "reasoning", "coding")
    capabilities: List[str] = field(default_factory=list)  # Model capabilities (e.g., "function_calling", "multimodal")
    tags: List[str] = field(default_factory=list)  # Custom tags for filtering
    created_at: Optional[datetime] = None  # When this model entry was created
    updated_at: Optional[datetime] = None  # When this model entry was last updated

    def __post_init__(self):
        """Validate and sort providers by priority"""
        if not self.providers:
            raise ValueError(f"Model {self.id} must have at least one provider")

        # Sort providers by priority (lower number = higher priority)
        self.providers.sort(key=lambda p: p.priority)

        # Set timestamps if not provided
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.updated_at is None:
            self.updated_at = datetime.now()

        logger.debug(
            f"Model {self.id} configured with {len(self.providers)} providers: "
            f"{[p.name for p in self.providers]}"
        )

    def get_enabled_providers(self) -> List[ProviderConfig]:
        """Get list of enabled providers, sorted by priority"""
        return [p for p in self.providers if p.enabled]

    def get_primary_provider(self) -> Optional[ProviderConfig]:
        """Get the highest priority enabled provider"""
        enabled = self.get_enabled_providers()
        return enabled[0] if enabled else None

    def get_provider_by_name(self, name: str) -> Optional[ProviderConfig]:
        """Get a specific provider configuration by name"""
        for provider in self.providers:
            if provider.name == name:
                return provider
        return None

    def supports_provider(self, provider_name: str) -> bool:
        """Check if this model supports a specific provider"""
        return any(p.name == provider_name and p.enabled for p in self.providers)


class MultiProviderRegistry:
    """
    Registry for multi-provider models with provider selection and failover logic.

    This class maintains a registry of models that can be accessed through multiple
    providers and provides methods for intelligent provider selection based on
    priority, availability, cost, and features.

    The registry serves as the canonical source of truth for model information,
    aggregating metadata from all available providers for each logical model.
    """

    def __init__(self):
        self._models: Dict[str, MultiProviderModel] = {}
        self._provider_metadata: Dict[str, Dict[str, Any]] = {}  # Additional metadata per provider
        logger.info("Initialized MultiProviderRegistry")

    def register_model(self, model: MultiProviderModel) -> None:
        """Register a multi-provider model"""
        # Update the model's timestamp
        model.updated_at = datetime.now()
        self._models[model.id] = model
        logger.info(
            f"Registered multi-provider model: {model.id} with "
            f"{len(model.providers)} providers"
        )

    def register_models(self, models: List[MultiProviderModel]) -> None:
        """Register multiple models at once"""
        for model in models:
            self.register_model(model)

    def update_model(self, model_id: str, updates: Dict[str, Any]) -> bool:
        """
        Update an existing model with new information.
        
        Args:
            model_id: The model ID to update
            updates: Dictionary of fields to update
            
        Returns:
            True if model was updated, False if not found
        """
        model = self.get_model(model_id)
        if not model:
            return False
            
        # Update fields
        for key, value in updates.items():
            if hasattr(model, key):
                setattr(model, key, value)
                
        # Update timestamp
        model.updated_at = datetime.now()
        logger.info(f"Updated model {model_id} with {len(updates)} fields")
        return True

    def get_model(self, model_id: str) -> Optional[MultiProviderModel]:
        """Get a multi-provider model by ID"""
        return self._models.get(model_id)

    def has_model(self, model_id: str) -> bool:
        """Check if a model is registered"""
        return model_id in self._models

    def get_all_models(self) -> List[MultiProviderModel]:
        """Get all registered models"""
        return list(self._models.values())

    def get_models_by_category(self, category: str) -> List[MultiProviderModel]:
        """Get all models belonging to a specific category"""
        return [model for model in self._models.values() if category in model.categories]

    def get_models_by_provider(self, provider_name: str) -> List[MultiProviderModel]:
        """Get all models available through a specific provider"""
        return [
            model for model in self._models.values() 
            if any(p.name == provider_name and p.enabled for p in model.providers)
        ]

    def get_models_by_capability(self, capability: str) -> List[MultiProviderModel]:
        """Get all models that support a specific capability"""
        return [model for model in self._models.values() if capability in model.capabilities]

    def get_models_by_tag(self, tag: str) -> List[MultiProviderModel]:
        """Get all models with a specific tag"""
        return [model for model in self._models.values() if tag in model.tags]

    def select_provider(
        self,
        model_id: str,
        preferred_provider: Optional[str] = None,
        required_features: Optional[List[str]] = None,
        max_cost: Optional[float] = None,
        exclude_unavailable: bool = True,
    ) -> Optional[ProviderConfig]:
        """
        Select the best provider for a model based on criteria.

        Args:
            model_id: The model to select a provider for
            preferred_provider: If specified, try to use this provider first
            required_features: List of required features (e.g., ["streaming"])
            max_cost: Maximum acceptable cost per 1k tokens
            exclude_unavailable: Whether to exclude providers marked as unavailable

        Returns:
            The selected provider configuration, or None if no suitable provider found
        """
        model = self.get_model(model_id)
        if not model:
            logger.warning(f"Model {model_id} not found in multi-provider registry")
            return None

        # Get enabled providers
        candidates = model.get_enabled_providers()
        if not candidates:
            logger.error(f"No enabled providers for model {model_id}")
            return None

        # Filter by availability
        if exclude_unavailable:
            candidates = [p for p in candidates if p.availability]
            if not candidates:
                logger.warning(f"No available providers for model {model_id}")
                # Fall back to all enabled providers if none are marked available
                candidates = model.get_enabled_providers()

        # Filter by required features
        if required_features:
            candidates = [
                p for p in candidates
                if all(feature in p.features for feature in required_features)
            ]
            if not candidates:
                logger.warning(
                    f"No providers for {model_id} support required features: {required_features}"
                )
                return None

        # Filter by cost
        if max_cost is not None:
            candidates = [
                p for p in candidates
                if p.cost_per_1k_input is None or p.cost_per_1k_input <= max_cost
            ]
            if not candidates:
                logger.warning(
                    f"No providers for {model_id} within cost limit: {max_cost}"
                )
                return None

        # If preferred provider specified and available, use it
        if preferred_provider:
            for provider in candidates:
                if provider.name == preferred_provider:
                    logger.info(
                        f"Selected preferred provider {preferred_provider} for {model_id}"
                    )
                    return provider
            logger.warning(
                f"Preferred provider {preferred_provider} not available for {model_id}, "
                f"falling back to priority-based selection"
            )

        # Sort by multiple criteria: priority first, then response time, then cost
        def provider_sort_key(provider: ProviderConfig) -> tuple:
            # Primary sort: priority (lower is better)
            priority = provider.priority
            
            # Secondary sort: response time (lower is better, None treated as high)
            response_time = provider.response_time_ms or 999999
            
            # Tertiary sort: cost (lower is better, None treated as high)
            cost = (provider.cost_per_1k_input or 0) + (provider.cost_per_1k_output or 0)
            
            return (priority, response_time, cost)

        candidates.sort(key=provider_sort_key)

        # Return highest priority provider
        selected = candidates[0]
        logger.info(
            f"Selected provider {selected.name} (priority {selected.priority}) for {model_id}"
        )
        return selected

    def get_fallback_providers(
        self,
        model_id: str,
        exclude_provider: Optional[str] = None,
        exclude_unavailable: bool = True,
    ) -> List[ProviderConfig]:
        """
        Get ordered list of fallback providers for a model.

        Args:
            model_id: The model to get fallbacks for
            exclude_provider: Provider to exclude (typically the one that just failed)
            exclude_unavailable: Whether to exclude providers marked as unavailable

        Returns:
            List of provider configurations ordered by priority and performance
        """
        model = self.get_model(model_id)
        if not model:
            return []

        providers = model.get_enabled_providers()

        # Exclude specified provider
        if exclude_provider:
            providers = [p for p in providers if p.name != exclude_provider]

        # Filter by availability
        if exclude_unavailable:
            providers = [p for p in providers if p.availability]

        # Sort by multiple criteria: priority first, then response time, then cost
        def provider_sort_key(provider: ProviderConfig) -> tuple:
            # Primary sort: priority (lower is better)
            priority = provider.priority
            
            # Secondary sort: response time (lower is better, None treated as high)
            response_time = provider.response_time_ms or 999999
            
            # Tertiary sort: cost (lower is better, None treated as high)
            cost = (provider.cost_per_1k_input or 0) + (provider.cost_per_1k_output or 0)
            
            return (priority, response_time, cost)

        providers.sort(key=provider_sort_key)
        return providers

    def update_provider_availability(
        self, 
        model_id: str, 
        provider_name: str, 
        available: bool, 
        response_time_ms: Optional[int] = None
    ) -> bool:
        """
        Update the availability status of a provider for a model.

        Args:
            model_id: The model ID
            provider_name: The provider name
            available: Whether the provider is available
            response_time_ms: Optional response time in milliseconds

        Returns:
            True if updated successfully, False if model/provider not found
        """
        model = self.get_model(model_id)
        if not model:
            return False

        provider = model.get_provider_by_name(provider_name)
        if not provider:
            return False

        provider.availability = available
        provider.last_checked = datetime.now()
        if response_time_ms is not None:
            provider.response_time_ms = response_time_ms

        logger.debug(
            f"Updated provider {provider_name} availability for {model_id}: "
            f"available={available}, response_time={response_time_ms}ms"
        )
        return True

    def disable_provider(self, model_id: str, provider_name: str) -> bool:
        """
        Temporarily disable a provider for a model (e.g., after repeated failures).

        Returns:
            True if provider was disabled, False if not found
        """
        model = self.get_model(model_id)
        if not model:
            return False

        provider = model.get_provider_by_name(provider_name)
        if provider:
            provider.enabled = False
            provider.availability = False
            provider.last_checked = datetime.now()
            logger.warning(f"Disabled provider {provider_name} for model {model_id}")
            return True

        return False

    def enable_provider(self, model_id: str, provider_name: str) -> bool:
        """
        Re-enable a previously disabled provider.

        Returns:
            True if provider was enabled, False if not found
        """
        model = self.get_model(model_id)
        if not model:
            return False

        provider = model.get_provider_by_name(provider_name)
        if provider:
            provider.enabled = True
            provider.availability = True
            provider.last_checked = datetime.now()
            logger.info(f"Enabled provider {provider_name} for model {model_id}")
            return True

        return False

    def get_provider_stats(self, model_id: str) -> Dict[str, Dict[str, Any]]:
        """
        Get statistics for all providers of a model.

        Returns:
            Dictionary mapping provider names to their stats
        """
        model = self.get_model(model_id)
        if not model:
            return {}

        stats = {}
        for provider in model.providers:
            stats[provider.name] = {
                "enabled": provider.enabled,
                "availability": provider.availability,
                "priority": provider.priority,
                "cost_per_1k_input": provider.cost_per_1k_input,
                "cost_per_1k_output": provider.cost_per_1k_output,
                "response_time_ms": provider.response_time_ms,
                "last_checked": provider.last_checked,
                "max_tokens": provider.max_tokens,
                "features": provider.features,
            }
        return stats


# Global registry instance
_registry = MultiProviderRegistry()


def get_registry() -> MultiProviderRegistry:
    """Get the global multi-provider registry instance"""
    return _registry
