"""
Provider Health Tracker Service

This service runs as a background task to populate provider health metrics
for Grafana dashboards. It leverages the existing model_health_monitor data
and updates Prometheus metrics every 30-60 seconds.

INTEGRATION POINTS:
- Uses existing model_health_monitor.health_data
- Updates existing Prometheus metrics (provider_availability, provider_error_rate, etc.)
- Calculates composite metric: gatewayz_provider_health_score
- NO new database tables needed - uses in-memory data from model health monitor

DESIGN:
- Lightweight background task (runs every 30s)
- Aggregates model health data by provider
- Updates 4 Prometheus metrics per provider
- Graceful error handling (never crashes the app)
"""

import asyncio
import logging
from typing import Any

from src.services.prometheus_metrics import (
    provider_availability,
    provider_error_rate,
    provider_response_time,
    gatewayz_provider_health_score,
)

logger = logging.getLogger(__name__)


class ProviderHealthTracker:
    """
    Background service that updates provider health metrics for Grafana dashboards.

    This service:
    1. Reads data from existing model_health_monitor
    2. Aggregates health by provider
    3. Updates Prometheus metrics
    4. Calculates composite health scores

    NO DATABASE WRITES - purely metric updates.
    """

    def __init__(self, update_interval: int = 30):
        """
        Initialize provider health tracker.

        Args:
            update_interval: Seconds between metric updates (default: 30s)
        """
        self.update_interval = update_interval
        self.running = False
        self._task: asyncio.Task | None = None

    async def start(self):
        """Start the background health tracking task."""
        if self.running:
            logger.warning("Provider health tracker is already running")
            return

        self.running = True
        logger.info(f"Starting provider health tracker (update interval: {self.update_interval}s)")

        # Start background task
        self._task = asyncio.create_task(self._tracking_loop())

    async def stop(self):
        """Stop the background health tracking task."""
        self.running = False
        logger.info("Stopping provider health tracker...")

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        logger.info("Provider health tracker stopped")

    async def _tracking_loop(self):
        """Main tracking loop - updates metrics every update_interval seconds."""
        while self.running:
            try:
                await self._update_provider_metrics()
                await asyncio.sleep(self.update_interval)
            except Exception as e:
                logger.error(f"Error in provider health tracking loop: {e}", exc_info=True)
                await asyncio.sleep(60)  # Wait 1 minute before retrying

    async def _update_provider_metrics(self):
        """
        Update provider health metrics using data from model_health_monitor.

        This is the core function that:
        1. Gets health data from existing model_health_monitor
        2. Aggregates by provider
        3. Calculates availability, error rate, latency
        4. Updates Prometheus metrics
        """
        try:
            # Import here to avoid circular imports
            from src.services.model_health_monitor import health_monitor

            if not health_monitor.health_data:
                logger.debug("No health data available yet from model_health_monitor")
                return

            # Aggregate health data by provider
            provider_stats = self._aggregate_by_provider(health_monitor.health_data)

            if not provider_stats:
                logger.debug("No provider stats to update")
                return

            # Update Prometheus metrics for each provider
            for provider, stats in provider_stats.items():
                # 1. Provider Availability (1=available, 0=unavailable)
                # Consider provider available if at least one model is healthy
                availability = 1.0 if stats["healthy_count"] > 0 else 0.0
                provider_availability.labels(provider=provider).set(availability)

                # 2. Provider Error Rate (0.0 to 1.0)
                total_requests = stats["total_requests"]
                error_count = stats["error_count"]
                error_rate = error_count / total_requests if total_requests > 0 else 0.0
                provider_error_rate.labels(provider=provider).set(error_rate)

                # 3. Provider Response Time (observe latest average)
                # Only update if we have response time data
                if stats["avg_latency_ms"] > 0:
                    latency_seconds = stats["avg_latency_ms"] / 1000.0
                    provider_response_time.labels(provider=provider).observe(latency_seconds)

                # 4. Provider Health Score (composite metric)
                health_score = self._calculate_health_score(
                    availability=availability,
                    error_rate=error_rate,
                    avg_latency_ms=stats["avg_latency_ms"]
                )
                gatewayz_provider_health_score.labels(provider=provider).set(health_score)

            logger.debug(f"Updated provider health metrics for {len(provider_stats)} providers")

        except Exception as e:
            logger.error(f"Failed to update provider metrics: {e}", exc_info=True)

    def _aggregate_by_provider(self, health_data: dict[str, Any]) -> dict[str, Any]:
        """
        Aggregate model health data by provider.

        Args:
            health_data: Dict from model_health_monitor.health_data
                Format: {model_key: ModelHealthMetrics}

        Returns:
            Dict[provider_name, stats] with aggregated metrics:
            {
                "provider_name": {
                    "total_models": int,
                    "healthy_count": int,
                    "unhealthy_count": int,
                    "total_requests": int,
                    "error_count": int,
                    "response_times": [float],
                    "avg_latency_ms": float,
                }
            }
        """
        provider_stats: dict[str, Any] = {}

        for _model_key, health_metrics in health_data.items():
            provider = health_metrics.provider

            if provider not in provider_stats:
                provider_stats[provider] = {
                    "total_models": 0,
                    "healthy_count": 0,
                    "unhealthy_count": 0,
                    "total_requests": 0,
                    "error_count": 0,
                    "response_times": [],
                    "avg_latency_ms": 0.0,
                }

            stats = provider_stats[provider]
            stats["total_models"] += 1

            # Count healthy vs unhealthy
            # health_metrics.status is a HealthStatus enum with .value
            if health_metrics.status.value == "healthy":
                stats["healthy_count"] += 1
            else:
                stats["unhealthy_count"] += 1

            # Aggregate request counts
            stats["total_requests"] += health_metrics.total_requests
            stats["error_count"] += health_metrics.error_count

            # Collect response times for latency calculation
            if health_metrics.response_time_ms:
                stats["response_times"].append(health_metrics.response_time_ms)

        # Calculate average latencies
        for provider, stats in provider_stats.items():
            if stats["response_times"]:
                stats["avg_latency_ms"] = sum(stats["response_times"]) / len(stats["response_times"])

        return provider_stats

    def _calculate_health_score(
        self,
        availability: float,
        error_rate: float,
        avg_latency_ms: float
    ) -> float:
        """
        Calculate composite health score (0.0 to 1.0).

        Formula:
        - Availability: 40% weight
        - Error rate: 30% weight (inverted: 1 - error_rate)
        - Latency: 30% weight (normalized: good=2s, bad=10s)

        Args:
            availability: Provider availability (0 or 1)
            error_rate: Error rate (0.0 to 1.0)
            avg_latency_ms: Average latency in milliseconds

        Returns:
            Composite health score (0.0 to 1.0)
        """
        # Normalize latency (2s is good, 10s is bad)
        # Latency score = 1.0 if < 2s, 0.0 if > 10s, linear between
        latency_seconds = avg_latency_ms / 1000.0
        latency_score = max(0.0, 1.0 - (latency_seconds - 2.0) / 8.0)

        # Calculate weighted composite score
        health_score = (
            availability * 0.4 +
            (1.0 - error_rate) * 0.3 +
            latency_score * 0.3
        )

        return max(0.0, min(1.0, health_score))


# Global instance
provider_health_tracker = ProviderHealthTracker(update_interval=30)
