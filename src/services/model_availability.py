"""
Enhanced Model Availability Service

This service provides improved reliability for model availability by:
1. Implementing circuit breaker patterns
2. Providing fallback mechanisms
3. Caching availability status
4. Integrating with health monitoring
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, UTC
from enum import Enum
from typing import Any


from src.config.redis_config import get_redis_client
from src.services.model_health_monitor import HealthDataStore
logger = logging.getLogger(__name__)


class AvailabilityStatus(str, Enum):
    """Model availability status"""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"
    MAINTENANCE = "maintenance"
    UNKNOWN = "unknown"


class CircuitBreakerState(str, Enum):
    """Circuit breaker states"""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, requests blocked
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class ModelAvailability:
    """Model availability information"""

    model_id: str
    provider: str
    gateway: str
    status: AvailabilityStatus
    last_checked: datetime
    success_rate: float
    response_time_ms: float | None
    error_count: int
    circuit_breaker_state: CircuitBreakerState
    fallback_models: list[str]
    maintenance_until: datetime | None = None
    error_message: str | None = None


@dataclass
class AvailabilityConfig:
    """Configuration for availability monitoring"""

    check_interval: int = 60  # seconds
    failure_threshold: int = 5  # failures before circuit opens
    recovery_timeout: int = 300  # seconds before trying half-open
    success_threshold: int = 3  # successes to close circuit
    response_timeout: int = 30  # seconds
    cache_ttl: int = 300  # seconds


class CircuitBreaker:
    """Circuit breaker implementation for model availability"""

    def __init__(
        self, failure_threshold: int = 5, recovery_timeout: int = 300, success_threshold: int = 3
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold

        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
        self.state = CircuitBreakerState.CLOSED

    def can_execute(self) -> bool:
        """Check if request can be executed"""
        if self.state == CircuitBreakerState.CLOSED:
            return True
        elif self.state == CircuitBreakerState.OPEN:
            if (
                self.last_failure_time
                and (time.time() - self.last_failure_time) > self.recovery_timeout
            ):
                self.state = CircuitBreakerState.HALF_OPEN
                self.success_count = 0
                return True
            return False
        elif self.state == CircuitBreakerState.HALF_OPEN:
            return True
        return False

    def record_success(self):
        """Record successful request"""
        if self.state == CircuitBreakerState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self.state = CircuitBreakerState.CLOSED
                self.failure_count = 0
        elif self.state == CircuitBreakerState.CLOSED:
            self.failure_count = max(0, self.failure_count - 1)

    def record_failure(self):
        """Record failed request"""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.failure_count >= self.failure_threshold:
            self.state = CircuitBreakerState.OPEN
        elif self.state == CircuitBreakerState.HALF_OPEN:
            self.state = CircuitBreakerState.OPEN


class AvailabilityStateStore:
    """Durable storage for availability and circuit breaker state."""

    CIRCUIT_BREAKER_KEY = "model_availability:circuit_breakers"
    AVAILABILITY_KEY = "model_availability:availability"

    @staticmethod
    def _get_client():
        try:
            return get_redis_client()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to get Redis client for availability store: %s", exc)
            return None

    @staticmethod
    def _encode_datetime(value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    @staticmethod
    def _decode_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            logger.warning("Failed to parse datetime '%s' from availability store", value)
            return None

    def load_circuit_breakers(self) -> dict[str, dict[str, Any]]:
        client = self._get_client()
        if not client:
            return {}

        try:
            raw_breakers = client.hgetall(self.CIRCUIT_BREAKER_KEY)
        except Exception as exc:
            logger.warning("Failed to load circuit breaker state: %s", exc)
            return {}

        states: dict[str, dict[str, Any]] = {}
        for key, payload in raw_breakers.items():
            try:
                state = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Invalid circuit breaker payload encountered: %s", payload)
                continue

            normalized_key = key.decode("utf-8") if isinstance(key, bytes) else key
            states[normalized_key] = state
        return states

    def save_circuit_breaker(self, key: str, breaker: "CircuitBreaker") -> bool:
        client = self._get_client()
        if not client:
            return False

        payload = {
            "failure_threshold": breaker.failure_threshold,
            "recovery_timeout": breaker.recovery_timeout,
            "success_threshold": breaker.success_threshold,
            "failure_count": breaker.failure_count,
            "success_count": breaker.success_count,
            "last_failure_time": breaker.last_failure_time,
            "state": breaker.state.value,
        }

        try:
            client.hset(self.CIRCUIT_BREAKER_KEY, key, json.dumps(payload))
            return True
        except Exception as exc:
            logger.warning("Failed to persist circuit breaker %s: %s", key, exc)
            return False

    def load_availability(self) -> dict[str, "ModelAvailability"]:
        client = self._get_client()
        if not client:
            return {}

        try:
            raw_availability = client.hgetall(self.AVAILABILITY_KEY)
        except Exception as exc:
            logger.warning("Failed to load model availability cache: %s", exc)
            return {}

        availability: dict[str, ModelAvailability] = {}
        for key, payload in raw_availability.items():
            try:
                data = json.loads(payload)
                normalized_key = key.decode("utf-8") if isinstance(key, bytes) else key
                availability[normalized_key] = ModelAvailability(
                    model_id=data["model_id"],
                    provider=data["provider"],
                    gateway=data["gateway"],
                    status=AvailabilityStatus(data["status"]),
                    last_checked=self._decode_datetime(data.get("last_checked")) or datetime.now(UTC),
                    success_rate=data.get("success_rate", 0.0),
                    response_time_ms=data.get("response_time_ms"),
                    error_count=data.get("error_count", 0),
                    circuit_breaker_state=CircuitBreakerState(data.get("circuit_breaker_state", CircuitBreakerState.CLOSED.value)),
                    fallback_models=data.get("fallback_models", []),
                    maintenance_until=self._decode_datetime(data.get("maintenance_until")),
                    error_message=data.get("error_message"),
                )
            except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                logger.warning("Invalid availability payload encountered: %s", payload)
        return availability

    def save_availability(self, key: str, availability: "ModelAvailability") -> bool:
        client = self._get_client()
        if not client:
            return False

        payload = {
            "model_id": availability.model_id,
            "provider": availability.provider,
            "gateway": availability.gateway,
            "status": availability.status.value,
            "last_checked": self._encode_datetime(availability.last_checked),
            "success_rate": availability.success_rate,
            "response_time_ms": availability.response_time_ms,
            "error_count": availability.error_count,
            "circuit_breaker_state": availability.circuit_breaker_state.value,
            "fallback_models": availability.fallback_models,
            "maintenance_until": self._encode_datetime(availability.maintenance_until),
            "error_message": availability.error_message,
        }

        try:
            client.hset(self.AVAILABILITY_KEY, key, json.dumps(payload))
            return True
        except Exception as exc:
            logger.warning("Failed to persist availability for %s: %s", key, exc)
            return False

class ModelAvailabilityService:
    """Enhanced model availability service"""

    def __init__(
        self,
        health_store: HealthDataStore | None = None,
        state_store: AvailabilityStateStore | None = None,
    ):
        self.health_store = health_store or HealthDataStore()
        self.state_store = state_store or AvailabilityStateStore()
        self.availability_cache: dict[str, ModelAvailability] = {}
        self.circuit_breakers: dict[str, CircuitBreaker] = {}
        self.fallback_mappings: dict[str, list[str]] = {}
        self.config = AvailabilityConfig()
        self.monitoring_active = False

        # Load fallback mappings
        self._load_fallback_mappings()
        self._load_persistent_state()

    def _load_fallback_mappings(self):
        """Load fallback model mappings"""
        # Define fallback mappings for common models
        self.fallback_mappings = {
            "gpt-4": ["gpt-4-turbo", "gpt-3.5-turbo", "claude-3-opus", "claude-3-sonnet"],
            "gpt-4-turbo": ["gpt-4", "gpt-3.5-turbo", "claude-3-opus"],
            "gpt-3.5-turbo": ["gpt-4", "gpt-4-turbo", "claude-3-sonnet"],
            "claude-3-opus": ["gpt-4", "claude-3-sonnet", "gpt-4-turbo"],
            "claude-3-sonnet": ["claude-3-opus", "gpt-3.5-turbo", "gpt-4"],
            "llama-3-70b": ["llama-3-8b", "claude-3-sonnet", "gpt-3.5-turbo"],
            "llama-3-8b": ["llama-3-70b", "gpt-3.5-turbo", "claude-3-sonnet"],
        }

    def _load_persistent_state(self):
        """Restore cached availability and circuit breaker state from durable storage."""
        try:
            persisted_availability = self.state_store.load_availability()
            if persisted_availability:
                self.availability_cache.update(persisted_availability)

            persisted_breakers = self.state_store.load_circuit_breakers()
            for key, state in persisted_breakers.items():
                breaker = CircuitBreaker(
                    failure_threshold=state.get("failure_threshold", self.config.failure_threshold),
                    recovery_timeout=state.get("recovery_timeout", self.config.recovery_timeout),
                    success_threshold=state.get("success_threshold", self.config.success_threshold),
                )
                breaker.failure_count = state.get("failure_count", 0)
                breaker.success_count = state.get("success_count", 0)
                breaker.last_failure_time = state.get("last_failure_time")

                state_value = state.get("state", CircuitBreakerState.CLOSED.value)
                try:
                    breaker.state = CircuitBreakerState(state_value)
                except ValueError:
                    breaker.state = CircuitBreakerState.CLOSED

                self.circuit_breakers[key] = breaker
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to load persistent availability state: %s", exc)

    async def start_monitoring(self):
        """Start availability monitoring"""
        if self.monitoring_active:
            return

        self.monitoring_active = True
        logger.info("Starting model availability monitoring")

        # Start monitoring loop
        asyncio.create_task(self._monitoring_loop())

    async def stop_monitoring(self):
        """Stop availability monitoring"""
        self.monitoring_active = False
        logger.info("Stopped model availability monitoring")

    async def _monitoring_loop(self):
        """Main monitoring loop"""
        while self.monitoring_active:
            try:
                await self._check_model_availability()
                await asyncio.sleep(self.config.check_interval)
            except Exception as e:
                logger.error(f"Error in availability monitoring loop: {e}", exc_info=True)
                await asyncio.sleep(60)

    async def _check_model_availability(self):
        """Check availability of all models"""
        try:
            models_map = self.health_store.load_models()
            if models_map:
                models_health = list(models_map.values())
            else:
                # Fallback to in-memory monitor if durable store unavailable
                from src.services.model_health_monitor import health_monitor

                models_health = health_monitor.get_all_models_health()

            for model_health in models_health:
                await self._update_model_availability(model_health)

        except Exception as e:
            logger.error(f"Failed to check model availability: {e}")

    async def _update_model_availability(self, model_health):
        """Update availability for a specific model"""
        model_key = f"{model_health.gateway}:{model_health.model_id}"

        # Determine availability status
        if model_health.status.value == "healthy":
            availability_status = AvailabilityStatus.AVAILABLE
        elif model_health.status.value == "degraded":
            availability_status = AvailabilityStatus.DEGRADED
        elif model_health.status.value == "unhealthy":
            availability_status = AvailabilityStatus.UNAVAILABLE
        else:
            availability_status = AvailabilityStatus.UNKNOWN

        # Get or create circuit breaker
        if model_key not in self.circuit_breakers:
            self.circuit_breakers[model_key] = CircuitBreaker(
                failure_threshold=self.config.failure_threshold,
                recovery_timeout=self.config.recovery_timeout,
                success_threshold=self.config.success_threshold,
            )

        circuit_breaker = self.circuit_breakers[model_key]

        # Update circuit breaker based on health
        if availability_status == AvailabilityStatus.AVAILABLE:
            circuit_breaker.record_success()
        else:
            circuit_breaker.record_failure()

        try:
            self.state_store.save_circuit_breaker(model_key, circuit_breaker)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to persist circuit breaker state for %s: %s", model_key, exc)

        # Get fallback models
        fallback_models = self.fallback_mappings.get(model_health.model_id, [])

        # Create or update availability record
        availability = ModelAvailability(
            model_id=model_health.model_id,
            provider=model_health.provider,
            gateway=model_health.gateway,
            status=availability_status,
            last_checked=datetime.now(UTC),
            success_rate=model_health.success_rate,
            response_time_ms=model_health.response_time_ms,
            error_count=model_health.error_count,
            circuit_breaker_state=circuit_breaker.state,
            fallback_models=fallback_models,
            error_message=model_health.error_message,
        )

        self.availability_cache[model_key] = availability

        try:
            self.state_store.save_availability(model_key, availability)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to persist availability state for %s: %s", model_key, exc)

    def get_model_availability(
        self, model_id: str, gateway: str = None
    ) -> ModelAvailability | None:
        """Get availability for a specific model"""
        if gateway:
            model_key = f"{gateway}:{model_id}"
            return self.availability_cache.get(model_key)
        else:
            # Search across all gateways
            for _key, availability in self.availability_cache.items():
                if availability.model_id == model_id:
                    return availability
            return None

    def get_available_models(
        self, gateway: str = None, provider: str = None
    ) -> list[ModelAvailability]:
        """Get all available models"""
        available = []

        for availability in self.availability_cache.values():
            if availability.status == AvailabilityStatus.AVAILABLE:
                if gateway and availability.gateway != gateway:
                    continue
                if provider and availability.provider != provider:
                    continue
                available.append(availability)

        return available

    def get_fallback_models(self, model_id: str) -> list[str]:
        """Get fallback models for a given model"""
        return self.fallback_mappings.get(model_id, [])

    def is_model_available(self, model_id: str, gateway: str = None) -> bool:
        """Check if a model is available"""
        availability = self.get_model_availability(model_id, gateway)
        if not availability:
            return False

        # Check circuit breaker
        if availability.circuit_breaker_state == CircuitBreakerState.OPEN:
            return False

        # Check maintenance
        if availability.maintenance_until and availability.maintenance_until > datetime.now(UTC):
            return False

        return availability.status == AvailabilityStatus.AVAILABLE

    def get_best_available_model(self, preferred_model: str, gateway: str = None) -> str | None:
        """Get the best available model, with fallbacks"""
        # Check if preferred model is available
        if self.is_model_available(preferred_model, gateway):
            return preferred_model

        # Try fallback models
        fallback_models = self.get_fallback_models(preferred_model)
        for fallback in fallback_models:
            if self.is_model_available(fallback, gateway):
                return fallback

        # Find any available model from the same provider
        preferred_availability = self.get_model_availability(preferred_model, gateway)
        if preferred_availability:
            provider = preferred_availability.provider
            available_models = self.get_available_models(gateway, provider)
            if available_models:
                return available_models[0].model_id

        return None

    def get_availability_summary(self) -> dict[str, Any]:
        """Get availability summary"""
        total_models = len(self.availability_cache)
        available_models = len(
            [
                a
                for a in self.availability_cache.values()
                if a.status == AvailabilityStatus.AVAILABLE
            ]
        )
        degraded_models = len(
            [a for a in self.availability_cache.values() if a.status == AvailabilityStatus.DEGRADED]
        )
        unavailable_models = len(
            [
                a
                for a in self.availability_cache.values()
                if a.status == AvailabilityStatus.UNAVAILABLE
            ]
        )

        # Group by gateway
        gateway_stats = {}
        for availability in self.availability_cache.values():
            gateway = availability.gateway
            if gateway not in gateway_stats:
                gateway_stats[gateway] = {
                    "total": 0,
                    "available": 0,
                    "degraded": 0,
                    "unavailable": 0,
                }

            gateway_stats[gateway]["total"] += 1
            if availability.status == AvailabilityStatus.AVAILABLE:
                gateway_stats[gateway]["available"] += 1
            elif availability.status == AvailabilityStatus.DEGRADED:
                gateway_stats[gateway]["degraded"] += 1
            else:
                gateway_stats[gateway]["unavailable"] += 1

        return {
            "total_models": total_models,
            "available_models": available_models,
            "degraded_models": degraded_models,
            "unavailable_models": unavailable_models,
            "availability_percentage": (
                (available_models / total_models * 100) if total_models > 0 else 0
            ),
            "gateway_stats": gateway_stats,
            "monitoring_active": self.monitoring_active,
            "last_updated": datetime.now(UTC).isoformat(),
        }

    def set_maintenance_mode(self, model_id: str, gateway: str, until: datetime):
        """Set maintenance mode for a model"""
        model_key = f"{gateway}:{model_id}"
        if model_key in self.availability_cache:
            self.availability_cache[model_key].maintenance_until = until
            self.availability_cache[model_key].status = AvailabilityStatus.MAINTENANCE

    def clear_maintenance_mode(self, model_id: str, gateway: str):
        """Clear maintenance mode for a model"""
        model_key = f"{gateway}:{model_id}"
        if model_key in self.availability_cache:
            self.availability_cache[model_key].maintenance_until = None
            # Status will be updated by next health check


# Global availability service instance
availability_service = ModelAvailabilityService()
