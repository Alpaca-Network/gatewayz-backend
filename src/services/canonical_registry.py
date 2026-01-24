"""
Canonical Model Registry

This module implements a canonical registry that aggregates model metadata from all provider fetchers
and creates a unified view of multi-provider models. It replaces static provider chains with
dynamic, registry-driven provider selection and failover.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from src.services.models import (
    get_all_models_parallel,
)
from src.services.multi_provider_registry import MultiProviderModel, ProviderConfig, get_registry
from src.utils.security_validators import sanitize_for_logging

logger = logging.getLogger(__name__)


@dataclass
class CanonicalModel:
    """
    Canonical representation of a logical model that aggregates metadata across multiple providers.

    This represents a single "logical model" (e.g., "gpt-4o") that can be accessed through
    multiple providers (e.g., OpenRouter, Vertex AI, Together, etc.) with different costs,
    features, and availability.
    """

    # Core metadata - aggregated from all providers
    id: str  # Canonical model ID (what users specify)
    name: str  # Display name
    description: str | None = None
    created: str | None = None  # ISO date string

    # Architecture metadata - consistent across providers
    modality: str | None = None
    input_modalities: list[str] = field(default_factory=lambda: ["text"])
    output_modalities: list[str] = field(default_factory=lambda: ["text"])
    context_length: int | None = None
    tokenizer: str | None = None
    instruct_type: str | None = None

    # Provider configurations
    providers: list[ProviderConfig] = field(default_factory=list)

    # Capabilities and features - aggregated
    supported_parameters: list[str] = field(default_factory=list)
    default_parameters: dict[str, Any] = field(default_factory=dict)
    features: set[str] = field(default_factory=set)  # Available features across providers

    # Pricing information - best/worst across providers
    best_pricing: dict[str, float] | None = None  # Lowest costs
    pricing_range: dict[str, dict[str, float]] | None = None  # min/max pricing

    def __post_init__(self):
        """Aggregate metadata and sort providers by priority"""
        self.providers.sort(key=lambda p: p.priority)

        # Aggregate pricing if providers have pricing
        self._aggregate_pricing()

        logger.debug(
            f"CanonicalModel {self.id} created with {len(self.providers)} providers: "
            + ", ".join([f"{p.name}(priority={p.priority})" for p in self.providers])
        )

    def _aggregate_pricing(self):
        """Aggregate pricing information across providers"""
        if not any(p.cost_per_1k_input or p.cost_per_1k_output for p in self.providers):
            return

        pricing_data = {
            "input": {"min": float("inf"), "max": 0, "providers": []},
            "output": {"min": float("inf"), "max": 0, "providers": []},
        }

        for provider in self.providers:
            if provider.cost_per_1k_input is not None:
                pricing_data["input"]["min"] = min(
                    pricing_data["input"]["min"], provider.cost_per_1k_input
                )
                pricing_data["input"]["max"] = max(
                    pricing_data["input"]["max"], provider.cost_per_1k_input
                )
                pricing_data["input"]["providers"].append(provider.name)

            if provider.cost_per_1k_output is not None:
                pricing_data["output"]["min"] = min(
                    pricing_data["output"]["min"], provider.cost_per_1k_output
                )
                pricing_data["output"]["max"] = max(
                    pricing_data["output"]["max"], provider.cost_per_1k_output
                )
                pricing_data["output"]["providers"].append(provider.name)

        # Set aggregated data
        if pricing_data["input"]["providers"]:
            self.best_pricing = {
                "input": pricing_data["input"]["min"],
                "output": (
                    pricing_data["output"]["min"] if pricing_data["output"]["providers"] else None
                ),
            }
            self.pricing_range = pricing_data

    def get_enabled_providers(self) -> list[ProviderConfig]:
        """Get list of enabled providers, sorted by priority"""
        return [p for p in self.providers if p.enabled]

    def get_primary_provider(self) -> ProviderConfig | None:
        """Get the highest priority enabled provider"""
        enabled = self.get_enabled_providers()
        return enabled[0] if enabled else None

    def supports_provider(self, provider_name: str) -> bool:
        """Check if this model supports a specific provider"""
        return any(p.name == provider_name and p.enabled for p in self.providers)

    def add_provider(self, provider_config: ProviderConfig):
        """Add a provider configuration"""
        # Check if provider already exists
        existing = self.get_provider_by_name(provider_config.name)
        if existing:
            logger.debug(f"Provider {provider_config.name} already exists for {self.id}, replacing")
            self.providers.remove(existing)

        self.providers.append(provider_config)
        self.providers.sort(key=lambda p: p.priority)
        self._aggregate_pricing()

    def get_provider_by_name(self, name: str) -> ProviderConfig | None:
        """Get provider configuration by name"""
        for provider in self.providers:
            if provider.name == name:
                return provider
        return None

    @property
    def available_providers(self) -> list[str]:
        """Get list of available provider names"""
        return [p.name for p in self.get_enabled_providers()]


class CanonicalModelRegistry:
    """
    Registry that maintains canonical representations of models aggregated across providers.

    This registry:
    1. Ingests provider catalog data from all fetchers
    2. Creates logical model groupings by matching model IDs across providers
    3. Maintains multi-provider configurations for each logical model
    4. Provides unified access to model metadata and provider selection
    """

    def __init__(self):
        self._canonical_models: dict[str, CanonicalModel] = {}
        self._bridge_map: dict[str, str] = {}  # provider_model_id -> canonical_model_id
        self._multi_provider_registry = get_registry()

        logger.info("Initialized CanonicalModelRegistry")

    def ingest_provider_catalogs(self):
        """Ingest catalogs from all providers and create canonical models"""
        logger.info("Starting provider catalog ingestion...")

        # Get all models from all providers
        all_models = get_all_models_parallel()

        if not all_models:
            logger.warning("No models retrieved from any provider")
            return

        logger.info(f"Retrieved {len(all_models)} model definitions from all providers")

        # Group models by logical identifier
        logical_groups: dict[str, list[dict]] = defaultdict(list)

        for model in all_models:
            logical_id = self._normalize_model_id_for_grouping(model.get("id", ""))
            if logical_id:
                logical_groups[logical_id].append(model)

        logger.info(f"Grouped into {len(logical_groups)} logical models")

        # Create canonical models for multi-provider groupings
        for logical_id, model_variants in logical_groups.items():
            try:
                if len(model_variants) > 1:
                    self._create_canonical_model(logical_id, model_variants)
                else:
                    # Single provider model - still create canonical for consistency
                    self._create_canonical_model(logical_id, model_variants)
            except Exception as e:
                logger.error(
                    f"Failed to create canonical model for '{logical_id}': {sanitize_for_logging(str(e))}",
                    exc_info=True,
                )

        logger.info(f"Created {len(self._canonical_models)} canonical models")

    def _normalize_model_id_for_grouping(self, model_id: str) -> str | None:
        """Normalize model IDs for logical grouping (e.g., model name without provider prefixes)"""
        if not model_id:
            return None

        # Remove common provider prefixes and extract base model name
        model_id = model_id.lower().strip()

        # Remove provider prefixes
        prefixes = [
            "anthropic/",
            "claude-",
            "openai/",
            "gpt-",
            "chatgpt-",
            "google/",
            "vertex/",
            "gemini-",
            "anthropic/",
            "claude-",
            "meta/",
            "llama-",
            "mistral/",
            "mistralai/",
            # Add more patterns as needed
        ]

        base_name = model_id
        for prefix in prefixes:
            if base_name.startswith(prefix):
                base_name = base_name[len(prefix) :]

        # Handle specific model families
        model_mapping = {
            # GPT models
            "gpt4": ["gpt-4", "gpt4", "gpt-4o", "gpt4o"],
            "gpt-4o": ["gpt-4o", "gpt4o", "gpt-4o-mini", "gpt4o-mini"],
            "gpt-4": ["gpt-4", "gpt4", "gpt-4-turbo", "gpt4-turbo"],
            "gpt-3.5": ["gpt-3.5", "gpt-3.5-turbo"],
            # Claude models
            "claude-3.5": ["claude-3-5-sonnet", "claude-3.5-sonnet"],
            "claude-3": ["claude-3", "claude-3-opus", "claude-3-sonnet", "claude-3-haiku"],
            "claude-2": ["claude-2", "claude-2.1"],
            # Gemini models
            "gemini-1.5": ["gemini-1.5-pro", "gemini-1.5-flash", "gemini-1.5-flash-8b"],
            "gemini-1.0": ["gemini-1.0-pro", "gemini-1.0-flash"],
            # Llama models
            "llama-3.1": ["llama-3.1", "llama3.1"],
            "llama-3": ["llama-3", "llama3"],
            "llama-2": ["llama-2", "llama2"],
            # Mistral models
            "mistral-7b": ["mistral-7b", "mistralai/mistral-7b-instruct"],
            "mistral-8x7b": ["mistral-8x7b", "mixtral-8x7b"],
            "mixtral": ["mixtral", "mistralai/mixtral"],
        }

        # Check for exact matches in mapping
        for canonical, variants in model_mapping.items():
            if any(base_name.startswith(variant.lower()) for variant in variants):
                return canonical

        # Default - use base name with version numbers simplified
        return base_name.replace("/", "-").replace("_", "-")

    def _create_canonical_model(self, logical_id: str, model_variants: list[dict]):
        """Create a canonical model from provider variants"""
        # Use the first variant as the base for metadata
        primary_variant = model_variants[0]

        # Create provider configurations
        provider_configs = []
        for variant in model_variants:
            provider_config = self._extract_provider_config(variant)
            if provider_config:
                provider_configs.append(provider_config)

        if not provider_configs:
            logger.warning(f"No valid provider configs for {logical_id}")
            return

        # Create canonical model
        canonical = CanonicalModel(
            id=logical_id,
            name=primary_variant.get("name", logical_id),
            description=primary_variant.get(
                "description", f"Model {logical_id} available on multiple providers"
            ),
            context_length=primary_variant.get("context_length"),
            modality=primary_variant.get("architecture", {}).get("modality", "text->text"),
            input_modalities=primary_variant.get("architecture", {}).get(
                "input_modalities", ["text"]
            ),
            output_modalities=primary_variant.get("architecture", {}).get(
                "output_modalities", ["text"]
            ),
            tokenizer=primary_variant.get("architecture", {}).get("tokenizer"),
            instruct_type=primary_variant.get("architecture", {}).get("instruct_type"),
            supported_parameters=primary_variant.get("supported_parameters", []),
            default_parameters=primary_variant.get("default_parameters", {}),
            providers=provider_configs,
        )

        # Store canonical model
        self._canonical_models[logical_id] = canonical

        # Register in multi-provider registry for backwards compatibility
        multi_provider_model = MultiProviderModel(
            id=logical_id,
            name=canonical.name,
            description=canonical.description,
            providers=provider_configs,
        )
        self._multi_provider_registry.register_model(multi_provider_model)

        # Create bridge mapping from provider-specific IDs to canonical
        for variant in model_variants:
            provider_id = variant.get("id", "")
            if provider_id:
                self._bridge_map[provider_id] = logical_id

    def _extract_provider_config(self, model_variant: dict) -> ProviderConfig | None:
        """Extract ProviderConfig from a model variant"""
        source_gateway = model_variant.get("source_gateway")
        if not source_gateway:
            return None

        # Determine priority based on provider reliability/routing
        priority_map = {
            "onerouter": 1,  # Highest priority - Infron AI (main router)
            "openrouter": 2,  # Fallback router
            "google-vertex": 3,  # Fast, reliable
            "together": 4,  # Good coverage
            "fireworks": 5,  # Solid provider
            "featherless": 6,  # Many models
            "deepinfra": 7,  # Good performance
            # Add more priorities as needed
        }

        priority = priority_map.get(source_gateway, 10)

        # Extract pricing
        pricing = model_variant.get("pricing", {})
        cost_per_1k_input = self._parse_pricing(pricing.get("prompt"))
        cost_per_1k_output = self._parse_pricing(pricing.get("completion"))

        # Features (these could be expanded with model capabilities)
        features = []
        if model_variant.get("supported_parameters"):
            features.append("parameters")
        if model_variant.get("context_length", 0) > 128000:
            features.append("long_context")

        return ProviderConfig(
            name=source_gateway,
            model_id=model_variant.get("id", ""),
            priority=priority,
            requires_credentials=(source_gateway not in ["openrouter"]),  # OpenAI agents, etc.
            cost_per_1k_input=cost_per_1k_input,
            cost_per_1k_output=cost_per_1k_output,
            features=features,
            max_tokens=model_variant.get("context_length"),
        )

    def _parse_pricing(self, pricing_str: str | None) -> float | None:
        """Parse pricing string to float"""
        if not pricing_str:
            return None
        try:
            return float(pricing_str)
        except (ValueError, TypeError):
            return None

    def get_canonical_model(self, model_id: str) -> CanonicalModel | None:
        """Get canonical model by ID"""
        return self._canonical_models.get(model_id)

    def get_canonical_id(self, provider_model_id: str) -> str | None:
        """Get canonical model ID for a provider-specific model ID"""
        return self._bridge_map.get(provider_model_id)

    def list_canonical_models(self) -> list[CanonicalModel]:
        """List all canonical models"""
        return list(self._canonical_models.values())

    def get_provider_model_id(self, canonical_id: str, provider_name: str) -> str | None:
        """Get provider-specific model ID for a canonical model"""
        canonical = self.get_canonical_model(canonical_id)
        if not canonical:
            return None

        provider_config = canonical.get_provider_by_name(provider_name)
        if provider_config:
            return provider_config.model_id

        return None

    def search_models(self, query: str, limit: int = 50) -> list[CanonicalModel]:
        """Search canonical models by name or description"""
        query = query.lower()
        results = []

        for model in self._canonical_models.values():
            if (
                query in model.id.lower()
                or query in model.name.lower()
                or (model.description and query in model.description.lower())
            ):
                results.append(model)
                if len(results) >= limit:
                    break

        return results


# Global registry instance
_canonical_registry = CanonicalModelRegistry()


def get_canonical_registry() -> CanonicalModelRegistry:
    """Get the global canonical model registry instance"""
    return _canonical_registry


def initialize_canonical_registry():
    """Initialize the canonical registry by ingesting all provider catalogs"""
    registry = get_canonical_registry()
    registry.ingest_provider_catalogs()
    logger.info("Canonical model registry initialized with ingested catalogs")
