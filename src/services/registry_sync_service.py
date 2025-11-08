"""
Registry Synchronization Service

This module synchronizes the canonical model registry with provider-specific catalogs.
It periodically fetches model lists from providers and updates the canonical registry
to ensure pricing, availability, and other metadata stays fresh.

Key Features:
- Automatic synchronization with provider APIs
- Detection of new models across providers
- Price and capability updates
- Provider availability monitoring
- Minimal disruption to existing catalog fetchers
"""

import logging
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime, timezone

from src.services.canonical_model_registry import (
    CanonicalModel,
    CanonicalModelRegistry,
    get_canonical_registry,
)
from src.services.multi_provider_registry import ProviderConfig

logger = logging.getLogger(__name__)


class RegistrySyncService:
    """
    Service for synchronizing the canonical registry with provider catalogs.

    This service acts as a bridge between existing provider fetchers (like
    fetch_models_from_openrouter, etc.) and the canonical registry, ensuring
    that multi-provider models stay up-to-date with the latest metadata.
    """

    def __init__(self, registry: Optional[CanonicalModelRegistry] = None):
        self.registry = registry or get_canonical_registry()
        self._provider_fetchers: Dict[str, Callable] = {}
        self._last_sync: Dict[str, datetime] = {}
        logger.info("Initialized RegistrySyncService")

    def register_provider_fetcher(
        self, provider: str, fetcher: Callable[[], List[Dict[str, Any]]]
    ) -> None:
        """
        Register a provider catalog fetcher function.

        Args:
            provider: Provider name (e.g., "openrouter", "google-vertex")
            fetcher: Function that returns a list of model dictionaries
        """
        self._provider_fetchers[provider] = fetcher
        logger.info(f"Registered fetcher for provider: {provider}")

    def sync_provider_catalog(
        self, provider: str, models: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Sync a provider's catalog with the canonical registry.

        This method takes a provider's model list and updates the canonical registry.
        It can either use provided models or fetch them using registered fetchers.

        Args:
            provider: Provider name
            models: Optional pre-fetched model list. If None, will use registered fetcher

        Returns:
            Dictionary with sync statistics
        """
        stats = {
            "provider": provider,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "models_processed": 0,
            "canonical_models_updated": 0,
            "new_canonical_models": 0,
            "providers_added": 0,
            "errors": [],
        }

        try:
            # Fetch models if not provided
            if models is None:
                if provider not in self._provider_fetchers:
                    error_msg = f"No fetcher registered for provider: {provider}"
                    logger.warning(error_msg)
                    stats["errors"].append(error_msg)
                    return stats

                try:
                    models = self._provider_fetchers[provider]()
                except Exception as e:
                    error_msg = f"Failed to fetch models from {provider}: {e}"
                    logger.error(error_msg)
                    stats["errors"].append(error_msg)
                    return stats

            if not models:
                logger.warning(f"No models returned from {provider}")
                return stats

            stats["models_processed"] = len(models)
            logger.info(f"Syncing {len(models)} models from {provider}")

            # Process each model from the provider
            for model_data in models:
                try:
                    self._process_provider_model(provider, model_data, stats)
                except Exception as e:
                    error_msg = f"Error processing model {model_data.get('id')}: {e}"
                    logger.warning(error_msg)
                    stats["errors"].append(error_msg)
                    continue

            # Update last sync timestamp
            self._last_sync[provider] = datetime.now(timezone.utc)

            logger.info(
                f"Completed sync for {provider}: "
                f"{stats['canonical_models_updated']} updated, "
                f"{stats['new_canonical_models']} new, "
                f"{stats['providers_added']} provider configs added"
            )

        except Exception as e:
            error_msg = f"Sync failed for {provider}: {e}"
            logger.error(error_msg, exc_info=True)
            stats["errors"].append(error_msg)

        return stats

    def _process_provider_model(
        self, provider: str, model_data: Dict[str, Any], stats: Dict[str, Any]
    ) -> None:
        """
        Process a single model from a provider catalog.

        This method determines if the model should be added to the canonical registry
        and creates/updates the appropriate CanonicalModel and ProviderConfig.
        """
        # Extract model metadata
        provider_model_id = model_data.get("id")
        if not provider_model_id:
            return

        # Try to map to canonical model ID (for now, use provider model ID)
        # In the future, this could use a more sophisticated mapping
        canonical_id = self._get_canonical_id(provider, provider_model_id, model_data)

        # Check if canonical model exists
        canonical_model = self.registry.get_model(canonical_id)

        if canonical_model:
            # Update existing canonical model with provider info
            self._update_canonical_model_provider(
                canonical_model, provider, provider_model_id, model_data
            )
            stats["canonical_models_updated"] += 1
        else:
            # Create new canonical model
            canonical_model = self._create_canonical_model(
                canonical_id, provider, provider_model_id, model_data
            )
            self.registry.register_model(canonical_model)
            stats["new_canonical_models"] += 1

        stats["providers_added"] += 1

    def _get_canonical_id(
        self, provider: str, provider_model_id: str, model_data: Dict[str, Any]
    ) -> str:
        """
        Determine the canonical model ID for a provider-specific model.

        This implements the logic for normalizing model IDs across providers.
        For example, both "google-vertex:gemini-1.5-pro" and
        "openrouter:google/gemini-pro-1.5" should map to "gemini-1.5-pro".

        Args:
            provider: Provider name
            provider_model_id: Provider-specific model ID
            model_data: Full model metadata

        Returns:
            Canonical model ID
        """
        # For now, use simple normalization
        # Strip provider prefixes and common variations
        canonical_id = provider_model_id

        # Remove common prefixes
        prefixes_to_strip = ["@", "google/", "openai/", "anthropic/", "meta-llama/"]
        for prefix in prefixes_to_strip:
            if canonical_id.startswith(prefix):
                canonical_id = canonical_id[len(prefix):]

        # Remove provider-specific path formats (e.g., "accounts/fireworks/models/")
        if "models/" in canonical_id:
            parts = canonical_id.split("models/")
            if len(parts) > 1:
                canonical_id = parts[-1]

        return canonical_id

    def _create_canonical_model(
        self, canonical_id: str, provider: str, provider_model_id: str, model_data: Dict[str, Any]
    ) -> CanonicalModel:
        """
        Create a new CanonicalModel from provider model data.

        Args:
            canonical_id: The canonical model ID
            provider: Provider name
            provider_model_id: Provider-specific model ID
            model_data: Full model metadata from provider

        Returns:
            New CanonicalModel instance
        """
        # Extract metadata
        name = model_data.get("name", canonical_id)
        description = model_data.get("description")
        context_length = model_data.get("context_length")

        # Parse architecture for modalities
        modalities = ["text"]  # Default
        if "architecture" in model_data:
            arch = model_data["architecture"]
            if isinstance(arch, dict):
                input_mods = arch.get("input_modalities", [])
                output_mods = arch.get("output_modalities", [])
                modalities = list(set(input_mods + output_mods))

        # Create provider config
        provider_config = self._create_provider_config(
            provider, provider_model_id, model_data
        )

        # Create canonical model
        return CanonicalModel(
            id=canonical_id,
            name=name,
            description=description,
            context_length=context_length,
            modalities=modalities,
            providers=[provider_config],
            architecture=model_data.get("architecture"),
        )

    def _update_canonical_model_provider(
        self,
        canonical_model: CanonicalModel,
        provider: str,
        provider_model_id: str,
        model_data: Dict[str, Any],
    ) -> None:
        """
        Update or add provider configuration to an existing canonical model.

        Args:
            canonical_model: The canonical model to update
            provider: Provider name
            provider_model_id: Provider-specific model ID
            model_data: Full model metadata from provider
        """
        # Create provider config
        provider_config = self._create_provider_config(
            provider, provider_model_id, model_data
        )

        # Add or update provider in canonical model
        canonical_model.add_provider(provider_config)

    def _create_provider_config(
        self, provider: str, provider_model_id: str, model_data: Dict[str, Any]
    ) -> ProviderConfig:
        """
        Create a ProviderConfig from provider model data.

        Args:
            provider: Provider name
            provider_model_id: Provider-specific model ID
            model_data: Full model metadata from provider

        Returns:
            ProviderConfig instance
        """
        # Extract pricing
        pricing = model_data.get("pricing", {})
        cost_per_1k_input = None
        cost_per_1k_output = None

        if pricing:
            try:
                # OpenRouter format: pricing.prompt and pricing.completion (in credits per token)
                # Convert to credits per 1k tokens
                if "prompt" in pricing:
                    cost_per_1k_input = float(pricing["prompt"]) * 1000
                if "completion" in pricing:
                    cost_per_1k_output = float(pricing["completion"]) * 1000
            except (ValueError, TypeError):
                pass

        # Extract supported parameters/features
        features = []
        supported_params = model_data.get("supported_parameters", [])

        if "stream" in supported_params or "streaming" in supported_params:
            features.append("streaming")
        if "tools" in supported_params or "functions" in supported_params:
            features.append("function_calling")
            features.append("tools")

        # Check architecture for multimodal
        arch = model_data.get("architecture", {})
        if isinstance(arch, dict):
            modality = arch.get("modality", "")
            if "image" in modality.lower():
                features.append("vision")
                features.append("multimodal")
            if "audio" in modality.lower():
                features.append("audio")
                features.append("multimodal")

        # Determine max tokens
        max_tokens = model_data.get("context_length")
        if max_tokens:
            # Some providers return max output tokens separately
            max_output = model_data.get("max_output_tokens")
            if max_output and max_output < max_tokens:
                max_tokens = max_output

        # Determine priority (higher for free/cheaper models)
        priority = 2  # Default priority
        if cost_per_1k_input is not None and cost_per_1k_input == 0:
            priority = 1  # Free models get higher priority

        return ProviderConfig(
            name=provider,
            model_id=provider_model_id,
            priority=priority,
            requires_credentials=False,  # Will be set per provider config
            cost_per_1k_input=cost_per_1k_input,
            cost_per_1k_output=cost_per_1k_output,
            max_tokens=max_tokens,
            features=features,
            enabled=True,
        )

    def sync_all_providers(self) -> Dict[str, Any]:
        """
        Sync all registered providers.

        Returns:
            Dictionary with overall sync statistics
        """
        overall_stats = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "providers_synced": 0,
            "total_models": 0,
            "total_errors": 0,
            "provider_results": {},
        }

        for provider in self._provider_fetchers.keys():
            logger.info(f"Syncing provider: {provider}")
            stats = self.sync_provider_catalog(provider)
            overall_stats["provider_results"][provider] = stats
            overall_stats["providers_synced"] += 1
            overall_stats["total_models"] += stats.get("models_processed", 0)
            overall_stats["total_errors"] += len(stats.get("errors", []))

        return overall_stats

    def get_last_sync_time(self, provider: str) -> Optional[datetime]:
        """Get the last sync time for a provider"""
        return self._last_sync.get(provider)

    def get_sync_status(self) -> Dict[str, Any]:
        """Get current sync status for all providers"""
        return {
            "registered_providers": list(self._provider_fetchers.keys()),
            "last_sync": {
                provider: timestamp.isoformat()
                for provider, timestamp in self._last_sync.items()
            },
            "registry_stats": self.registry.get_statistics(),
        }


# Global sync service instance
_sync_service = RegistrySyncService()


def get_sync_service() -> RegistrySyncService:
    """Get the global registry sync service instance"""
    return _sync_service
