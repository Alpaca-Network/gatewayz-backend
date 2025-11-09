"""
Registry Migration Utilities

This module provides utilities for migrating existing provider catalogs
to the canonical model registry and for registering new providers.
"""

import logging
import asyncio
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass

from src.services.canonical_model_registry import (
    get_canonical_registry,
    CanonicalModel,
    CanonicalModelRegistry,
)
from src.services.multi_provider_registry import ProviderConfig

logger = logging.getLogger(__name__)


@dataclass
class ProviderMigrationConfig:
    """Configuration for migrating a provider to the canonical registry"""

    provider_name: str
    fetch_catalog_fn: Callable[[], List[Dict[str, Any]]]
    model_id_mapper: Optional[Callable[[str], str]] = None
    default_features: List[str] = None
    cost_multiplier: float = 1.0  # Apply to all costs from this provider

    def __post_init__(self):
        if self.default_features is None:
            self.default_features = ["streaming"]


class RegistryMigrator:
    """
    Utility class for migrating provider catalogs to the canonical registry.
    """

    def __init__(self):
        self.registry: CanonicalModelRegistry = get_canonical_registry()
        self.migration_stats = {
            "providers_migrated": [],
            "models_added": 0,
            "models_updated": 0,
            "errors": [],
        }

    async def migrate_provider(self, config: ProviderMigrationConfig) -> Dict[str, Any]:
        """
        Migrate a single provider's catalog to the canonical registry.

        Args:
            config: Migration configuration for the provider

        Returns:
            Migration statistics
        """

        stats = {
            "provider": config.provider_name,
            "models_processed": 0,
            "models_added": 0,
            "models_updated": 0,
            "errors": [],
        }

        try:
            # Fetch provider catalog
            logger.info(f"Fetching catalog for provider {config.provider_name}")
            catalog = await asyncio.to_thread(config.fetch_catalog_fn)

            if not catalog:
                logger.warning(f"Empty catalog for provider {config.provider_name}")
                return stats

            logger.info(f"Processing {len(catalog)} models from {config.provider_name}")

            # Process each model
            for model_data in catalog:
                try:
                    provider_model_id = model_data.get("id", model_data.get("model_id"))
                    if not provider_model_id:
                        continue

                    stats["models_processed"] += 1

                    # Map to canonical ID
                    if config.model_id_mapper:
                        canonical_id = config.model_id_mapper(provider_model_id)
                    else:
                        canonical_id = self._default_model_id_mapper(
                            provider_model_id, config.provider_name
                        )

                    if not canonical_id:
                        continue

                    # Get or create canonical model
                    canonical_model = self.registry.get_canonical_model(canonical_id)
                    is_new = canonical_model is None

                    if not canonical_model:
                        canonical_model = CanonicalModel(
                            id=canonical_id,
                            name=model_data.get("name", canonical_id),
                            description=model_data.get("description"),
                        )

                    # Extract costs with multiplier
                    input_cost = None
                    output_cost = None
                    if "pricing" in model_data:
                        pricing = model_data["pricing"]
                        if isinstance(pricing, dict):
                            input_cost = pricing.get("input")
                            output_cost = pricing.get("output")
                        if input_cost is not None:
                            input_cost *= config.cost_multiplier
                        if output_cost is not None:
                            output_cost *= config.cost_multiplier

                    # Extract features
                    features = model_data.get("features", config.default_features)
                    if "supports_streaming" in model_data and model_data["supports_streaming"]:
                        if "streaming" not in features:
                            features.append("streaming")

                    # Add provider to canonical model
                    canonical_model.add_provider(
                        provider_name=config.provider_name,
                        provider_model_id=provider_model_id,
                        context_length=model_data.get("context_length", model_data.get("max_tokens")),
                        modalities=model_data.get("modalities", ["text"]),
                        features=features,
                        input_cost=input_cost,
                        output_cost=output_cost,
                    )

                    # Register the model
                    self.registry.register_canonical_model(canonical_model)

                    if is_new:
                        stats["models_added"] += 1
                    else:
                        stats["models_updated"] += 1

                    # Add any aliases
                    if "aliases" in model_data:
                        for alias in model_data["aliases"]:
                            self.registry.add_alias(alias, canonical_id)

                except Exception as e:
                    error_msg = f"Failed to process model {provider_model_id}: {e}"
                    logger.error(error_msg)
                    stats["errors"].append(error_msg)

            logger.info(
                f"Completed migration for {config.provider_name}: "
                f"added={stats['models_added']}, updated={stats['models_updated']}"
            )

        except Exception as e:
            error_msg = f"Failed to migrate provider {config.provider_name}: {e}"
            logger.error(error_msg)
            stats["errors"].append(error_msg)

        # Update global stats
        self.migration_stats["providers_migrated"].append(config.provider_name)
        self.migration_stats["models_added"] += stats["models_added"]
        self.migration_stats["models_updated"] += stats["models_updated"]
        self.migration_stats["errors"].extend(stats["errors"])

        return stats

    async def migrate_all_providers(
        self, configs: List[ProviderMigrationConfig]
    ) -> Dict[str, Any]:
        """
        Migrate multiple provider catalogs to the canonical registry.

        Args:
            configs: List of migration configurations

        Returns:
            Combined migration statistics
        """

        logger.info(f"Starting migration for {len(configs)} providers")

        # Run migrations concurrently
        tasks = [self.migrate_provider(config) for config in configs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                provider_name = configs[i].provider_name
                error_msg = f"Migration failed for {provider_name}: {result}"
                logger.error(error_msg)
                self.migration_stats["errors"].append(error_msg)

        logger.info(
            f"Migration completed: providers={len(self.migration_stats['providers_migrated'])}, "
            f"models_added={self.migration_stats['models_added']}, "
            f"models_updated={self.migration_stats['models_updated']}, "
            f"errors={len(self.migration_stats['errors'])}"
        )

        return self.migration_stats

    def _default_model_id_mapper(self, provider_model_id: str, provider_name: str) -> str:
        """
        Default model ID mapper that attempts to create a canonical ID.

        Args:
            provider_model_id: Provider-specific model ID
            provider_name: Name of the provider

        Returns:
            Canonical model ID
        """

        # Remove provider-specific prefixes
        canonical_id = provider_model_id

        # Remove common prefixes
        prefixes_to_remove = [
            "accounts/fireworks/models/",
            "models/",
            "@cf/",
            "openai/",
            "anthropic/",
            "google/",
            "meta-llama/",
            "mistralai/",
        ]

        for prefix in prefixes_to_remove:
            if canonical_id.startswith(prefix):
                canonical_id = canonical_id[len(prefix):]
                break

        # Handle special cases
        if provider_name == "openrouter":
            # OpenRouter uses org/model format which is good for canonical
            pass
        elif provider_name == "together":
            # Together uses full paths, extract model name
            parts = canonical_id.split("/")
            if len(parts) > 1:
                canonical_id = parts[-1]

        return canonical_id.lower()


def create_provider_migration_configs() -> List[ProviderMigrationConfig]:
    """
    Create migration configurations for all known providers.

    Returns:
        List of provider migration configurations
    """

    configs = []

    # OpenRouter migration
    try:
        from src.services.openrouter_client import fetch_openrouter_models

        configs.append(
            ProviderMigrationConfig(
                provider_name="openrouter",
                fetch_catalog_fn=lambda: fetch_openrouter_models(return_dict=False),
                default_features=["streaming"],
            )
        )
    except ImportError:
        logger.warning("OpenRouter client not available for migration")

    # Together migration
    try:
        from src.services.together_client import fetch_together_models

        configs.append(
            ProviderMigrationConfig(
                provider_name="together",
                fetch_catalog_fn=fetch_together_models,
                default_features=["streaming"],
            )
        )
    except ImportError:
        logger.warning("Together client not available for migration")

    # Fireworks migration
    try:
        from src.services.fireworks_client import fetch_fireworks_models

        configs.append(
            ProviderMigrationConfig(
                provider_name="fireworks",
                fetch_catalog_fn=fetch_fireworks_models,
                default_features=["streaming"],
            )
        )
    except ImportError:
        logger.warning("Fireworks client not available for migration")

    # DeepInfra migration
    try:
        from src.services.deepinfra_client import fetch_deepinfra_models

        configs.append(
            ProviderMigrationConfig(
                provider_name="deepinfra",
                fetch_catalog_fn=fetch_deepinfra_models,
                default_features=["streaming"],
            )
        )
    except ImportError:
        logger.warning("DeepInfra client not available for migration")

    # HuggingFace migration
    try:
        from src.services.huggingface_client import fetch_huggingface_models

        configs.append(
            ProviderMigrationConfig(
                provider_name="huggingface",
                fetch_catalog_fn=fetch_huggingface_models,
                default_features=["streaming"],
            )
        )
    except ImportError:
        logger.warning("HuggingFace client not available for migration")

    # Featherless migration
    try:
        from src.services.featherless_client import fetch_featherless_models

        configs.append(
            ProviderMigrationConfig(
                provider_name="featherless",
                fetch_catalog_fn=fetch_featherless_models,
                default_features=["streaming"],
            )
        )
    except ImportError:
        logger.warning("Featherless client not available for migration")

    return configs


async def run_full_migration() -> Dict[str, Any]:
    """
    Run a full migration of all available provider catalogs.

    Returns:
        Migration statistics
    """

    migrator = RegistryMigrator()
    configs = create_provider_migration_configs()

    if not configs:
        logger.warning("No provider configurations available for migration")
        return {"error": "No providers to migrate"}

    return await migrator.migrate_all_providers(configs)


def validate_multi_provider_routing(model_id: str, min_providers: int = 2) -> Dict[str, Any]:
    """
    Validate that a model can be routed through multiple providers.

    Args:
        model_id: The model to validate
        min_providers: Minimum number of providers required

    Returns:
        Validation results
    """

    registry = get_canonical_registry()

    # Resolve and get model
    canonical_id = registry.resolve_model_id(model_id)
    canonical_model = registry.get_canonical_model(canonical_id)

    if not canonical_model:
        return {
            "valid": False,
            "reason": f"Model {model_id} not found in registry",
        }

    enabled_providers = [
        name for name, config in canonical_model.providers.items()
        if config.enabled
    ]

    if len(enabled_providers) < min_providers:
        return {
            "valid": False,
            "reason": f"Model has only {len(enabled_providers)} providers, needs {min_providers}",
            "providers": enabled_providers,
        }

    # Test provider selection
    test_results = []
    for strategy in ["priority", "cost", "latency", "balanced"]:
        providers = registry.select_providers_with_failover(
            model_id=canonical_id,
            max_providers=3,
            selection_strategy=strategy,
        )

        test_results.append({
            "strategy": strategy,
            "selected_providers": [p[0] for p in providers],
        })

    return {
        "valid": True,
        "canonical_id": canonical_id,
        "total_providers": len(canonical_model.providers),
        "enabled_providers": enabled_providers,
        "test_results": test_results,
        "features": list(canonical_model.features),
        "modalities": list(canonical_model.modalities),
    }


if __name__ == "__main__":
    # Example usage
    import sys

    if len(sys.argv) > 1:
        model_to_validate = sys.argv[1]
        result = validate_multi_provider_routing(model_to_validate)
        print(f"Validation for {model_to_validate}:")
        import json
        print(json.dumps(result, indent=2))
    else:
        print("Running full provider migration...")
        result = asyncio.run(run_full_migration())
        print(f"Migration result: {result}")