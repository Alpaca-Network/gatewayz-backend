"""
Canonical Model Registry

This module provides a unified registry that aggregates model metadata from multiple providers,
enabling intelligent routing, failover, and cost optimization across all available providers.
"""

import logging
from typing import List, Optional, Dict, Any, Set, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import asyncio
from collections import defaultdict

from src.services.multi_provider_registry import (
    MultiProviderModel,
    ProviderConfig,
    MultiProviderRegistry,
)

logger = logging.getLogger(__name__)


@dataclass
class ModelHealthMetrics:
    """Health metrics for a specific model-provider combination"""

    provider: str
    model_id: str
    success_count: int = 0
    failure_count: int = 0
    last_success: Optional[datetime] = None
    last_failure: Optional[datetime] = None
    avg_latency_ms: float = 0.0
    circuit_breaker_state: str = "closed"  # closed, open, half-open
    failure_threshold: int = 5
    recovery_timeout: timedelta = field(default_factory=lambda: timedelta(minutes=5))

    @property
    def success_rate(self) -> float:
        """Calculate success rate"""
        total = self.success_count + self.failure_count
        return self.success_count / total if total > 0 else 0.0

    @property
    def is_healthy(self) -> bool:
        """Check if provider is healthy"""
        return self.circuit_breaker_state != "open"

    def record_success(self, latency_ms: float = 0.0):
        """Record successful request"""
        self.success_count += 1
        self.last_success = datetime.now()

        # Update average latency
        if latency_ms > 0:
            total_requests = self.success_count + self.failure_count
            self.avg_latency_ms = (
                (self.avg_latency_ms * (total_requests - 1) + latency_ms) / total_requests
            )

        # Reset circuit breaker if it was open
        if self.circuit_breaker_state == "open":
            if self.last_failure and datetime.now() - self.last_failure > self.recovery_timeout:
                self.circuit_breaker_state = "half-open"
        elif self.circuit_breaker_state == "half-open":
            self.circuit_breaker_state = "closed"
            self.failure_count = 0  # Reset failure count on recovery

    def record_failure(self):
        """Record failed request"""
        self.failure_count += 1
        self.last_failure = datetime.now()

        # Open circuit breaker if threshold exceeded
        if self.failure_count >= self.failure_threshold:
            self.circuit_breaker_state = "open"
            logger.warning(
                f"Circuit breaker opened for {self.provider}/{self.model_id} "
                f"after {self.failure_count} failures"
            )
        elif self.circuit_breaker_state == "half-open":
            # Failed during recovery, go back to open
            self.circuit_breaker_state = "open"


@dataclass
class CanonicalModel:
    """
    Represents a canonical model that aggregates metadata from all providers.
    """

    id: str  # Canonical model ID
    name: str
    description: Optional[str] = None

    # Aggregated capabilities
    providers: Dict[str, ProviderConfig] = field(default_factory=dict)
    context_lengths: Dict[str, int] = field(default_factory=dict)  # provider -> context_length
    modalities: Set[str] = field(default_factory=set)
    features: Set[str] = field(default_factory=set)

    # Cost tracking (min/max across providers)
    min_input_cost: Optional[float] = None
    max_input_cost: Optional[float] = None
    min_output_cost: Optional[float] = None
    max_output_cost: Optional[float] = None

    # Provider-specific model IDs
    provider_model_ids: Dict[str, str] = field(default_factory=dict)

    # Metadata
    tags: Set[str] = field(default_factory=set)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def add_provider(
        self,
        provider_name: str,
        provider_model_id: str,
        config: Optional[ProviderConfig] = None,
        context_length: Optional[int] = None,
        modalities: Optional[List[str]] = None,
        features: Optional[List[str]] = None,
        input_cost: Optional[float] = None,
        output_cost: Optional[float] = None,
    ):
        """Add or update a provider for this model"""

        # Create or update provider config
        if config:
            self.providers[provider_name] = config
        else:
            self.providers[provider_name] = ProviderConfig(
                name=provider_name,
                model_id=provider_model_id,
                priority=len(self.providers) + 1,  # Default priority based on order
                cost_per_1k_input=input_cost,
                cost_per_1k_output=output_cost,
                features=features or [],
            )

        # Store provider-specific model ID
        self.provider_model_ids[provider_name] = provider_model_id

        # Update context length
        if context_length:
            self.context_lengths[provider_name] = context_length

        # Aggregate modalities
        if modalities:
            self.modalities.update(modalities)

        # Aggregate features
        if features:
            self.features.update(features)

        # Update cost ranges
        if input_cost is not None:
            if self.min_input_cost is None or input_cost < self.min_input_cost:
                self.min_input_cost = input_cost
            if self.max_input_cost is None or input_cost > self.max_input_cost:
                self.max_input_cost = input_cost

        if output_cost is not None:
            if self.min_output_cost is None or output_cost < self.min_output_cost:
                self.min_output_cost = output_cost
            if self.max_output_cost is None or output_cost > self.max_output_cost:
                self.max_output_cost = output_cost

        self.updated_at = datetime.now()

    def to_multi_provider_model(self) -> MultiProviderModel:
        """Convert to MultiProviderModel for compatibility"""

        # Sort providers by priority
        sorted_providers = sorted(
            self.providers.values(),
            key=lambda p: p.priority
        )

        # Use max context length across all providers
        max_context = max(self.context_lengths.values()) if self.context_lengths else None

        return MultiProviderModel(
            id=self.id,
            name=self.name,
            description=self.description,
            providers=sorted_providers,
            context_length=max_context,
            modalities=list(self.modalities) if self.modalities else ["text"],
        )

    def get_cheapest_provider(self) -> Optional[str]:
        """Get the provider with lowest cost"""
        cheapest = None
        min_cost = float('inf')

        for name, config in self.providers.items():
            if config.cost_per_1k_input is not None:
                total_cost = (config.cost_per_1k_input or 0) + (config.cost_per_1k_output or 0)
                if total_cost < min_cost:
                    min_cost = total_cost
                    cheapest = name

        return cheapest

    def get_fastest_provider(self, health_metrics: Dict[str, ModelHealthMetrics]) -> Optional[str]:
        """Get the provider with lowest latency based on health metrics"""
        fastest = None
        min_latency = float('inf')

        for provider_name in self.providers:
            key = f"{provider_name}:{self.id}"
            if key in health_metrics:
                metrics = health_metrics[key]
                if metrics.is_healthy and metrics.avg_latency_ms < min_latency:
                    min_latency = metrics.avg_latency_ms
                    fastest = provider_name

        return fastest


class CanonicalModelRegistry(MultiProviderRegistry):
    """
    Enhanced registry that aggregates models from multiple provider catalogs
    and maintains a canonical view of all available models.
    """

    def __init__(self):
        super().__init__()
        self._canonical_models: Dict[str, CanonicalModel] = {}
        self._health_metrics: Dict[str, ModelHealthMetrics] = {}
        self._provider_catalogs: Dict[str, Dict[str, Any]] = {}
        self._model_aliases: Dict[str, str] = {}  # alias -> canonical_id
        logger.info("Initialized CanonicalModelRegistry")

    def register_canonical_model(self, model: CanonicalModel) -> None:
        """Register a canonical model"""
        self._canonical_models[model.id] = model

        # Also register as MultiProviderModel for compatibility
        multi_provider_model = model.to_multi_provider_model()
        super().register_model(multi_provider_model)

        logger.info(
            f"Registered canonical model: {model.id} with "
            f"{len(model.providers)} providers"
        )

    def add_alias(self, alias: str, canonical_id: str) -> None:
        """Add an alias for a canonical model ID"""
        self._model_aliases[alias] = canonical_id
        logger.debug(f"Added alias {alias} -> {canonical_id}")

    def resolve_model_id(self, model_id: str) -> str:
        """Resolve a model ID to its canonical form"""
        return self._model_aliases.get(model_id, model_id)

    def get_canonical_model(self, model_id: str) -> Optional[CanonicalModel]:
        """Get a canonical model by ID (supports aliases)"""
        canonical_id = self.resolve_model_id(model_id)
        return self._canonical_models.get(canonical_id)

    def ingest_provider_catalog(
        self,
        provider_name: str,
        catalog: List[Dict[str, Any]],
        model_id_mapper: Optional[callable] = None,
    ) -> int:
        """
        Ingest a provider's model catalog and merge with canonical registry.

        Args:
            provider_name: Name of the provider
            catalog: List of model dictionaries from provider
            model_id_mapper: Optional function to map provider model IDs to canonical IDs

        Returns:
            Number of models ingested
        """

        # Store catalog for reference
        self._provider_catalogs[provider_name] = {
            m.get("id", m.get("model_id", "unknown")): m for m in catalog
        }

        count = 0
        for model_data in catalog:
            provider_model_id = model_data.get("id", model_data.get("model_id"))
            if not provider_model_id:
                continue

            # Map to canonical ID if mapper provided
            if model_id_mapper:
                canonical_id = model_id_mapper(provider_model_id)
            else:
                canonical_id = provider_model_id

            # Skip if no canonical mapping
            if not canonical_id:
                continue

            # Get or create canonical model
            if canonical_id not in self._canonical_models:
                self._canonical_models[canonical_id] = CanonicalModel(
                    id=canonical_id,
                    name=model_data.get("name", canonical_id),
                    description=model_data.get("description"),
                )

            canonical_model = self._canonical_models[canonical_id]

            # Add provider information
            canonical_model.add_provider(
                provider_name=provider_name,
                provider_model_id=provider_model_id,
                context_length=model_data.get("context_length"),
                modalities=model_data.get("modalities", ["text"]),
                features=model_data.get("features", []),
                input_cost=model_data.get("pricing", {}).get("input"),
                output_cost=model_data.get("pricing", {}).get("output"),
            )

            # Add any aliases from the provider
            if "aliases" in model_data:
                for alias in model_data["aliases"]:
                    self.add_alias(alias, canonical_id)

            count += 1

        logger.info(f"Ingested {count} models from {provider_name} catalog")

        # Update MultiProviderRegistry for compatibility
        for canonical_model in self._canonical_models.values():
            if provider_name in canonical_model.providers:
                multi_provider_model = canonical_model.to_multi_provider_model()
                super().register_model(multi_provider_model)

        return count

    def select_providers_with_failover(
        self,
        model_id: str,
        max_providers: int = 3,
        selection_strategy: str = "priority",  # priority, cost, latency, balanced
        required_features: Optional[List[str]] = None,
    ) -> List[Tuple[str, ProviderConfig]]:
        """
        Select multiple providers for a model with failover support.

        Args:
            model_id: The model to select providers for
            max_providers: Maximum number of providers to return
            selection_strategy: Strategy for selecting providers
            required_features: Required features

        Returns:
            Ordered list of (provider_name, config) tuples
        """

        canonical_id = self.resolve_model_id(model_id)
        canonical_model = self._canonical_models.get(canonical_id)

        if not canonical_model:
            logger.warning(f"Model {model_id} not found in canonical registry")
            return []

        # Filter providers by features and health
        candidates = []
        for provider_name, config in canonical_model.providers.items():
            # Check features
            if required_features and not all(
                f in config.features for f in required_features
            ):
                continue

            # Check health
            health_key = f"{provider_name}:{canonical_id}"
            if health_key in self._health_metrics:
                metrics = self._health_metrics[health_key]
                if not metrics.is_healthy:
                    logger.debug(f"Skipping unhealthy provider {provider_name} for {model_id}")
                    continue

            candidates.append((provider_name, config))

        if not candidates:
            return []

        # Sort by strategy
        if selection_strategy == "cost":
            candidates.sort(key=lambda x: (
                (x[1].cost_per_1k_input or float('inf')) +
                (x[1].cost_per_1k_output or float('inf'))
            ))
        elif selection_strategy == "latency":
            def get_latency(item):
                provider_name, _ = item
                health_key = f"{provider_name}:{canonical_id}"
                if health_key in self._health_metrics:
                    return self._health_metrics[health_key].avg_latency_ms
                return float('inf')
            candidates.sort(key=get_latency)
        elif selection_strategy == "balanced":
            # Balance between cost, latency, and success rate
            def get_score(item):
                provider_name, config = item
                cost_score = (
                    (config.cost_per_1k_input or 0) +
                    (config.cost_per_1k_output or 0)
                ) / 10  # Normalize

                health_key = f"{provider_name}:{canonical_id}"
                if health_key in self._health_metrics:
                    metrics = self._health_metrics[health_key]
                    latency_score = metrics.avg_latency_ms / 1000  # Normalize to seconds
                    success_score = 1 - metrics.success_rate  # Invert so lower is better
                else:
                    latency_score = 1.0
                    success_score = 0.5

                return cost_score + latency_score + success_score

            candidates.sort(key=get_score)
        else:  # priority (default)
            candidates.sort(key=lambda x: x[1].priority)

        # Return up to max_providers
        return candidates[:max_providers]

    def record_request_outcome(
        self,
        model_id: str,
        provider: str,
        success: bool,
        latency_ms: float = 0.0
    ):
        """Record the outcome of a request for health tracking"""

        canonical_id = self.resolve_model_id(model_id)
        health_key = f"{provider}:{canonical_id}"

        if health_key not in self._health_metrics:
            self._health_metrics[health_key] = ModelHealthMetrics(
                provider=provider,
                model_id=canonical_id,
            )

        metrics = self._health_metrics[health_key]

        if success:
            metrics.record_success(latency_ms)
            logger.debug(
                f"Recorded success for {provider}/{canonical_id} "
                f"(latency: {latency_ms:.2f}ms, rate: {metrics.success_rate:.2%})"
            )
        else:
            metrics.record_failure()
            logger.debug(
                f"Recorded failure for {provider}/{canonical_id} "
                f"(total failures: {metrics.failure_count}, state: {metrics.circuit_breaker_state})"
            )

    def get_health_metrics(self, model_id: str, provider: Optional[str] = None) -> Dict[str, ModelHealthMetrics]:
        """Get health metrics for a model"""

        canonical_id = self.resolve_model_id(model_id)

        if provider:
            health_key = f"{provider}:{canonical_id}"
            return {provider: self._health_metrics.get(health_key)} if health_key in self._health_metrics else {}

        # Return all providers' metrics for this model
        result = {}
        for key, metrics in self._health_metrics.items():
            if metrics.model_id == canonical_id:
                result[metrics.provider] = metrics

        return result

    def get_all_canonical_models(self) -> List[CanonicalModel]:
        """Get all canonical models"""
        return list(self._canonical_models.values())

    def export_catalog(self) -> Dict[str, Any]:
        """Export the complete catalog with all metadata"""

        catalog = {
            "models": [],
            "aliases": self._model_aliases,
            "providers": list(self._provider_catalogs.keys()),
            "health_metrics": {},
            "generated_at": datetime.now().isoformat(),
        }

        for model in self._canonical_models.values():
            model_data = {
                "id": model.id,
                "name": model.name,
                "description": model.description,
                "providers": {
                    name: {
                        "model_id": config.model_id,
                        "priority": config.priority,
                        "cost_input": config.cost_per_1k_input,
                        "cost_output": config.cost_per_1k_output,
                        "features": config.features,
                        "enabled": config.enabled,
                    }
                    for name, config in model.providers.items()
                },
                "context_lengths": model.context_lengths,
                "modalities": list(model.modalities),
                "features": list(model.features),
                "cost_range": {
                    "input": [model.min_input_cost, model.max_input_cost],
                    "output": [model.min_output_cost, model.max_output_cost],
                },
                "tags": list(model.tags),
            }
            catalog["models"].append(model_data)

        # Add health metrics summary
        for key, metrics in self._health_metrics.items():
            catalog["health_metrics"][key] = {
                "success_rate": metrics.success_rate,
                "avg_latency_ms": metrics.avg_latency_ms,
                "circuit_breaker": metrics.circuit_breaker_state,
                "last_success": metrics.last_success.isoformat() if metrics.last_success else None,
                "last_failure": metrics.last_failure.isoformat() if metrics.last_failure else None,
            }

        return catalog


# Global canonical registry instance
_canonical_registry = CanonicalModelRegistry()


def get_canonical_registry() -> CanonicalModelRegistry:
    """Get the global canonical model registry instance"""
    return _canonical_registry