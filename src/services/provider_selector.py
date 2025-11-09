"""
Provider Selector with Automatic Failover

This module implements intelligent provider selection and automatic failover
for multi-provider models. When a request fails, it automatically retries
with the next available provider using the canonical model registry.
"""

import logging
from typing import Optional, Any, Callable, Dict, List, Tuple
from collections import defaultdict
from datetime import datetime, timedelta
import time

from src.services.canonical_model_registry import (
    get_canonical_registry,
    CanonicalModelRegistry,
    ModelHealthMetrics,
)
from src.services.multi_provider_registry import ProviderConfig

logger = logging.getLogger(__name__)


class ProviderHealthTracker:
    """
    Track provider health and implement circuit breaker pattern.

    When a provider fails repeatedly, it can be temporarily disabled
    to avoid wasting time on dead providers.
    """

    def __init__(
        self,
        failure_threshold: int = 5,  # Failures before circuit opens
        timeout_seconds: int = 300,  # Time to wait before retry (5 minutes)
    ):
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds

        # Track failures per provider per model
        self._failures: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # Track when providers were disabled
        self._disabled_until: Dict[str, Dict[str, datetime]] = defaultdict(dict)

    def record_success(self, model_id: str, provider_name: str) -> None:
        """Record a successful request, resetting failure count"""
        if model_id in self._failures:
            self._failures[model_id][provider_name] = 0

        # Clear disabled state
        if model_id in self._disabled_until:
            if provider_name in self._disabled_until[model_id]:
                del self._disabled_until[model_id][provider_name]
                logger.info(f"Re-enabled {provider_name} for {model_id} after successful request")

    def record_failure(self, model_id: str, provider_name: str) -> bool:
        """
        Record a failed request.

        Returns:
            True if provider should be disabled (circuit opened), False otherwise
        """
        self._failures[model_id][provider_name] += 1
        failure_count = self._failures[model_id][provider_name]

        logger.warning(
            f"Provider {provider_name} failed for {model_id} "
            f"({failure_count}/{self.failure_threshold} failures)"
        )

        if failure_count >= self.failure_threshold:
            # Open circuit - disable provider temporarily
            disabled_until = datetime.now() + timedelta(seconds=self.timeout_seconds)
            self._disabled_until[model_id][provider_name] = disabled_until

            logger.error(
                f"Circuit breaker opened: Disabled {provider_name} for {model_id} "
                f"until {disabled_until.strftime('%H:%M:%S')} after {failure_count} failures"
            )
            return True

        return False

    def is_available(self, model_id: str, provider_name: str) -> bool:
        """Check if a provider is currently available (circuit closed)"""
        if model_id not in self._disabled_until:
            return True

        if provider_name not in self._disabled_until[model_id]:
            return True

        # Check if timeout has expired
        disabled_until = self._disabled_until[model_id][provider_name]
        if datetime.now() >= disabled_until:
            # Timeout expired, re-enable provider
            del self._disabled_until[model_id][provider_name]
            self._failures[model_id][provider_name] = 0
            logger.info(f"Circuit breaker closed: Re-enabled {provider_name} for {model_id}")
            return True

        return False


class ProviderSelector:
    """
    Intelligent provider selector with automatic failover using the canonical registry.

    This class handles provider selection and implements automatic failover
    when requests fail. It uses the canonical model registry to find available
    providers and tries them in priority order until one succeeds.
    """

    def __init__(self):
        self.registry: CanonicalModelRegistry = get_canonical_registry()
        self.health_tracker = ProviderHealthTracker()
        logger.info("Initialized ProviderSelector with canonical registry and automatic failover")

    def execute_with_failover(
        self,
        model_id: str,
        execute_fn: Callable[[str, str], Any],  # Function that takes (provider_name, model_id)
        preferred_provider: Optional[str] = None,
        required_features: Optional[List[str]] = None,
        selection_strategy: str = "priority",  # priority, cost, latency, balanced
        max_retries: int = 3,
        record_metrics: bool = True,
    ) -> Dict[str, Any]:
        """
        Execute a request with automatic failover to alternative providers.

        Args:
            model_id: The model to use (supports aliases)
            execute_fn: Function to execute the request. Takes (provider_name, provider_model_id) and returns response
            preferred_provider: Optional preferred provider to try first
            required_features: Optional list of required features
            selection_strategy: Strategy for selecting providers (priority, cost, latency, balanced)
            max_retries: Maximum number of providers to try
            record_metrics: Whether to record health metrics

        Returns:
            Dict with:
                - success: bool - Whether the request succeeded
                - response: Any - The response from the provider (if successful)
                - provider: str - The provider that handled the request
                - error: str - Error message (if failed)
                - attempts: List[Dict] - List of attempts made
                - canonical_model_id: str - The resolved canonical model ID

        Raises:
            Exception: If all providers fail
        """

        # Resolve model ID through canonical registry (handles aliases)
        canonical_id = self.registry.resolve_model_id(model_id)
        canonical_model = self.registry.get_canonical_model(canonical_id)

        if not canonical_model:
            # Fallback to multi-provider registry for backward compatibility
            model = self.registry.get_model(model_id)
            if not model:
                return {
                    "success": False,
                    "response": None,
                    "provider": None,
                    "error": f"Model {model_id} not found in registry",
                    "attempts": [],
                    "canonical_model_id": None,
                }

            # Use legacy multi-provider selection
            return self._execute_with_legacy_failover(
                model_id=model_id,
                execute_fn=execute_fn,
                preferred_provider=preferred_provider,
                required_features=required_features,
                max_retries=max_retries,
            )

        # Use canonical registry for provider selection
        providers_with_configs = self.registry.select_providers_with_failover(
            model_id=canonical_id,
            max_providers=max_retries,
            selection_strategy=selection_strategy,
            required_features=required_features,
        )

        if not providers_with_configs:
            return {
                "success": False,
                "response": None,
                "provider": None,
                "error": f"No suitable providers found for {model_id}",
                "attempts": [],
                "canonical_model_id": canonical_id,
            }

        # If preferred provider specified, reorder to put it first
        if preferred_provider:
            preferred_found = False
            reordered = []
            others = []

            for provider_name, config in providers_with_configs:
                if provider_name == preferred_provider:
                    reordered.insert(0, (provider_name, config))
                    preferred_found = True
                else:
                    others.append((provider_name, config))

            if preferred_found:
                providers_with_configs = reordered + others
                logger.info(f"Prioritizing preferred provider {preferred_provider} for {model_id}")

        # Filter out providers that are circuit-broken (using legacy tracker for compatibility)
        providers_to_try = []
        for provider_name, config in providers_with_configs:
            if self.health_tracker.is_available(canonical_id, provider_name):
                providers_to_try.append((provider_name, config))

        if not providers_to_try:
            return {
                "success": False,
                "response": None,
                "provider": None,
                "error": f"All providers for {model_id} are currently unavailable (circuit breakers open)",
                "attempts": [],
                "canonical_model_id": canonical_id,
            }

        # Try providers in order
        attempts = []
        last_error = None

        for i, (provider_name, provider_config) in enumerate(providers_to_try):
            provider_model_id = canonical_model.provider_model_ids.get(
                provider_name, provider_config.model_id
            )

            attempt_info = {
                "provider": provider_name,
                "model_id": provider_model_id,
                "priority": provider_config.priority,
                "attempt_number": i + 1,
                "cost_input": provider_config.cost_per_1k_input,
                "cost_output": provider_config.cost_per_1k_output,
            }

            start_time = time.time()

            try:
                logger.info(
                    f"Attempt {i + 1}/{len(providers_to_try)}: "
                    f"Trying {provider_name} for {canonical_id} "
                    f"(provider model ID: {provider_model_id})"
                )

                # Execute request with this provider
                response = execute_fn(provider_name, provider_model_id)

                # Calculate latency
                latency_ms = (time.time() - start_time) * 1000

                # Success!
                self.health_tracker.record_success(canonical_id, provider_name)

                # Record metrics in canonical registry
                if record_metrics:
                    self.registry.record_request_outcome(
                        model_id=canonical_id,
                        provider=provider_name,
                        success=True,
                        latency_ms=latency_ms,
                    )

                attempt_info["success"] = True
                attempt_info["duration_ms"] = latency_ms
                attempts.append(attempt_info)

                logger.info(
                    f"✓ Request successful with {provider_name} for {canonical_id} "
                    f"(attempt {i + 1}/{len(providers_to_try)}, latency: {latency_ms:.2f}ms)"
                )

                return {
                    "success": True,
                    "response": response,
                    "provider": provider_name,
                    "provider_model_id": provider_model_id,
                    "error": None,
                    "attempts": attempts,
                    "canonical_model_id": canonical_id,
                }

            except Exception as e:
                # Calculate latency even for failures
                latency_ms = (time.time() - start_time) * 1000

                # Request failed with this provider
                last_error = str(e)
                attempt_info["success"] = False
                attempt_info["error"] = last_error
                attempt_info["duration_ms"] = latency_ms
                attempts.append(attempt_info)

                logger.warning(
                    f"✗ Request failed with {provider_name} for {canonical_id}: {last_error}"
                )

                # Record failure and check if circuit breaker should open
                should_disable = self.health_tracker.record_failure(canonical_id, provider_name)

                # Record metrics in canonical registry
                if record_metrics:
                    self.registry.record_request_outcome(
                        model_id=canonical_id,
                        provider=provider_name,
                        success=False,
                        latency_ms=latency_ms,
                    )

                if should_disable:
                    # Disable this provider in the registry temporarily
                    self.registry.disable_provider(canonical_id, provider_name)

                # Continue to next provider
                continue

        # All providers failed
        logger.error(
            f"All {len(attempts)} providers failed for {canonical_id}. Last error: {last_error}"
        )

        return {
            "success": False,
            "response": None,
            "provider": None,
            "error": f"All providers failed. Last error: {last_error}",
            "attempts": attempts,
            "canonical_model_id": canonical_id,
        }

    def _execute_with_legacy_failover(
        self,
        model_id: str,
        execute_fn: Callable[[str, str], Any],
        preferred_provider: Optional[str] = None,
        required_features: Optional[List[str]] = None,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """
        Legacy failover method for models not in canonical registry.
        Maintains backward compatibility with existing multi-provider registry.
        """

        model = self.registry.get_model(model_id)
        if not model:
            return {
                "success": False,
                "response": None,
                "provider": None,
                "error": f"Model {model_id} not found in multi-provider registry",
                "attempts": [],
                "canonical_model_id": None,
            }

        # Select primary provider
        primary = self.registry.select_provider(
            model_id=model_id,
            preferred_provider=preferred_provider,
            required_features=required_features,
        )

        if not primary:
            return {
                "success": False,
                "response": None,
                "provider": None,
                "error": f"No suitable provider found for {model_id}",
                "attempts": [],
                "canonical_model_id": None,
            }

        # Get list of providers to try (primary + fallbacks)
        providers_to_try = [primary]
        fallbacks = self.registry.get_fallback_providers(
            model_id=model_id,
            exclude_provider=primary.name,
        )
        providers_to_try.extend(fallbacks[:max_retries - 1])

        # Filter out providers that are circuit-broken
        providers_to_try = [
            p for p in providers_to_try
            if self.health_tracker.is_available(model_id, p.name)
        ]

        if not providers_to_try:
            return {
                "success": False,
                "response": None,
                "provider": None,
                "error": f"All providers for {model_id} are currently unavailable",
                "attempts": [],
                "canonical_model_id": None,
            }

        # Try providers in order
        attempts = []
        last_error = None

        for i, provider in enumerate(providers_to_try):
            attempt_info = {
                "provider": provider.name,
                "model_id": provider.model_id,
                "priority": provider.priority,
                "attempt_number": i + 1,
            }

            try:
                logger.info(
                    f"Legacy: Attempt {i + 1}/{len(providers_to_try)}: "
                    f"Trying {provider.name} for {model_id}"
                )

                # Execute request with this provider
                response = execute_fn(provider.name, provider.model_id)

                # Success!
                self.health_tracker.record_success(model_id, provider.name)

                attempt_info["success"] = True
                attempts.append(attempt_info)

                logger.info(f"✓ Legacy: Request successful with {provider.name}")

                return {
                    "success": True,
                    "response": response,
                    "provider": provider.name,
                    "provider_model_id": provider.model_id,
                    "error": None,
                    "attempts": attempts,
                    "canonical_model_id": None,
                }

            except Exception as e:
                # Request failed
                last_error = str(e)
                attempt_info["success"] = False
                attempt_info["error"] = last_error
                attempts.append(attempt_info)

                logger.warning(f"✗ Legacy: Request failed with {provider.name}: {last_error}")

                # Record failure
                should_disable = self.health_tracker.record_failure(model_id, provider.name)

                if should_disable:
                    self.registry.disable_provider(model_id, provider.name)

                continue

        # All providers failed
        logger.error(f"Legacy: All providers failed for {model_id}")

        return {
            "success": False,
            "response": None,
            "provider": None,
            "error": f"All providers failed. Last error: {last_error}",
            "attempts": attempts,
            "canonical_model_id": None,
        }

    def get_model_providers(self, model_id: str) -> Optional[List[str]]:
        """Get list of provider names available for a model"""

        # Check canonical registry first
        canonical_id = self.registry.resolve_model_id(model_id)
        canonical_model = self.registry.get_canonical_model(canonical_id)

        if canonical_model:
            return list(canonical_model.providers.keys())

        # Fall back to multi-provider registry
        model = self.registry.get_model(model_id)
        if not model:
            return None

        return [p.name for p in model.get_enabled_providers()]

    def check_provider_health(self, model_id: str, provider_name: str) -> Dict[str, Any]:
        """
        Check the health status of a provider for a model.

        Returns:
            Dict with health information including metrics from canonical registry
        """

        # Check canonical registry first
        canonical_id = self.registry.resolve_model_id(model_id)
        canonical_model = self.registry.get_canonical_model(canonical_id)

        if canonical_model:
            if provider_name not in canonical_model.providers:
                return {"available": False, "reason": "Provider not found"}

            provider_config = canonical_model.providers[provider_name]
            if not provider_config.enabled:
                return {"available": False, "reason": "Provider disabled in configuration"}

            # Check circuit breaker
            is_available = self.health_tracker.is_available(canonical_id, provider_name)
            if not is_available:
                return {"available": False, "reason": "Circuit breaker open (too many failures)"}

            # Get health metrics from canonical registry
            health_metrics = self.registry.get_health_metrics(canonical_id, provider_name)

            health_info = {
                "available": True,
                "reason": "Provider healthy",
                "canonical_model_id": canonical_id,
            }

            if provider_name in health_metrics:
                metrics = health_metrics[provider_name]
                health_info.update({
                    "success_rate": f"{metrics.success_rate:.2%}",
                    "avg_latency_ms": metrics.avg_latency_ms,
                    "circuit_state": metrics.circuit_breaker_state,
                    "success_count": metrics.success_count,
                    "failure_count": metrics.failure_count,
                })

            return health_info

        # Fall back to multi-provider registry
        model = self.registry.get_model(model_id)
        if not model:
            return {"available": False, "reason": "Model not found"}

        provider = model.get_provider_by_name(provider_name)
        if not provider:
            return {"available": False, "reason": "Provider not found"}

        if not provider.enabled:
            return {"available": False, "reason": "Provider disabled in configuration"}

        is_available = self.health_tracker.is_available(model_id, provider_name)
        if not is_available:
            return {"available": False, "reason": "Circuit breaker open (too many failures)"}

        return {"available": True, "reason": "Provider healthy (legacy)"}

    def get_provider_recommendations(
        self,
        model_id: str,
        optimize_for: str = "balanced",  # cost, latency, reliability, balanced
        required_features: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get provider recommendations for a model based on optimization criteria.

        Args:
            model_id: The model to get recommendations for
            optimize_for: What to optimize for (cost, latency, reliability, balanced)
            required_features: Required features

        Returns:
            List of provider recommendations with scores and reasons
        """

        canonical_id = self.registry.resolve_model_id(model_id)
        canonical_model = self.registry.get_canonical_model(canonical_id)

        if not canonical_model:
            return []

        recommendations = []

        # Map optimization to selection strategy
        strategy_map = {
            "cost": "cost",
            "latency": "latency",
            "reliability": "priority",  # Use priority as proxy for reliability
            "balanced": "balanced",
        }

        selection_strategy = strategy_map.get(optimize_for, "balanced")

        # Get ordered providers
        providers = self.registry.select_providers_with_failover(
            model_id=canonical_id,
            max_providers=10,  # Get more for recommendations
            selection_strategy=selection_strategy,
            required_features=required_features,
        )

        # Get health metrics
        health_metrics = self.registry.get_health_metrics(canonical_id)

        for i, (provider_name, config) in enumerate(providers):
            recommendation = {
                "rank": i + 1,
                "provider": provider_name,
                "model_id": canonical_model.provider_model_ids.get(provider_name, config.model_id),
                "priority": config.priority,
                "features": config.features,
                "reasons": [],
                "scores": {},
            }

            # Cost analysis
            if config.cost_per_1k_input is not None:
                total_cost = (config.cost_per_1k_input or 0) + (config.cost_per_1k_output or 0)
                recommendation["cost_per_1k"] = {
                    "input": config.cost_per_1k_input,
                    "output": config.cost_per_1k_output,
                    "total": total_cost,
                }
                recommendation["scores"]["cost"] = 1.0 / (1.0 + total_cost)  # Lower cost = higher score

                if optimize_for == "cost" and i == 0:
                    recommendation["reasons"].append(f"Lowest cost: ${total_cost:.2f} per 1K tokens")
                elif total_cost == 0:
                    recommendation["reasons"].append("Free tier available")

            # Health/reliability analysis
            health_key = f"{provider_name}:{canonical_id}"
            if health_key in health_metrics:
                metrics = health_metrics[health_key]
                recommendation["health"] = {
                    "success_rate": f"{metrics.success_rate:.2%}",
                    "avg_latency_ms": metrics.avg_latency_ms,
                    "circuit_state": metrics.circuit_breaker_state,
                }
                recommendation["scores"]["reliability"] = metrics.success_rate

                if metrics.success_rate >= 0.99:
                    recommendation["reasons"].append(f"Excellent reliability: {metrics.success_rate:.1%}")
                elif metrics.avg_latency_ms < 500:
                    recommendation["reasons"].append(f"Fast response: {metrics.avg_latency_ms:.0f}ms")

            # Feature analysis
            if required_features and all(f in config.features for f in required_features):
                recommendation["reasons"].append(f"Supports all required features")

            # Priority analysis
            if config.priority == 1:
                recommendation["reasons"].append("Highest priority provider")

            # Circuit breaker status
            if not self.health_tracker.is_available(canonical_id, provider_name):
                recommendation["warning"] = "Currently unavailable (circuit breaker open)"
                recommendation["scores"]["availability"] = 0.0
            else:
                recommendation["scores"]["availability"] = 1.0

            # Calculate overall score
            scores = recommendation["scores"]
            if optimize_for == "balanced":
                recommendation["overall_score"] = sum(scores.values()) / len(scores) if scores else 0
            else:
                recommendation["overall_score"] = scores.get(
                    "reliability" if optimize_for == "reliability" else optimize_for, 0
                )

            recommendations.append(recommendation)

        # Sort by overall score
        recommendations.sort(key=lambda x: x["overall_score"], reverse=True)

        # Update ranks after sorting
        for i, rec in enumerate(recommendations):
            rec["rank"] = i + 1

        return recommendations


# Global selector instance
_selector = ProviderSelector()


def get_selector() -> ProviderSelector:
    """Get the global provider selector instance"""
    return _selector