"""
Redis-based metrics service for real-time monitoring and dashboards.

This service provides:
- Request counters (per provider, model, hour)
- Latency tracking (sorted sets with TTL)
- Error tracking (lists, last 100 errors per provider)
- Provider health scores (sorted set)
- Cost tracking (hash with hourly keys)
- Circuit breaker state sync

All data is stored in Redis with appropriate TTLs to prevent unbounded growth.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.config.redis_config import get_redis_client

logger = logging.getLogger(__name__)


@dataclass
class RequestMetrics:
    """Metrics for a single request"""
    provider: str
    model: str
    latency_ms: int
    success: bool
    cost: float
    tokens_input: int
    tokens_output: int
    timestamp: float
    error_message: str | None = None


@dataclass
class ProviderStats:
    """Aggregated statistics for a provider"""
    provider: str
    total_requests: int
    successful_requests: int
    failed_requests: int
    avg_latency_ms: float
    p95_latency_ms: float
    total_cost: float
    total_tokens: int
    health_score: float
    last_updated: float


class RedisMetrics:
    """
    Redis-based metrics service for real-time monitoring.

    Key patterns:
    - metrics:{provider}:{hour} -> Hash (request counts, costs)
    - latency:{provider}:{model} -> Sorted Set (timestamp:latency_ms)
    - errors:{provider} -> List (last 100 errors)
    - health:{provider} -> Float (health score 0-100)
    - circuit:{provider}:{model} -> String (circuit breaker state)
    """

    def __init__(self, redis_client=None):
        """Initialize Redis metrics service"""
        self.redis = redis_client or get_redis_client()
        self.enabled = self.redis is not None

        if not self.enabled:
            logger.warning("Redis not available - metrics service will operate in no-op mode")

    async def record_request(
        self,
        provider: str,
        model: str,
        latency_ms: int,
        success: bool,
        cost: float,
        tokens_input: int = 0,
        tokens_output: int = 0,
        error_message: str | None = None
    ):
        """
        Record a single request metrics to Redis.

        Args:
            provider: Provider name (e.g., "openrouter")
            model: Model ID
            latency_ms: Request latency in milliseconds
            success: Whether request succeeded
            cost: Cost in credits/USD
            tokens_input: Input tokens used
            tokens_output: Output tokens used
            error_message: Error message if failed
        """
        if not self.enabled:
            return

        try:
            # Get current hour key for aggregation
            now = datetime.now(timezone.utc)
            hour_key = now.strftime("%Y-%m-%d:%H")
            metrics_key = f"metrics:{provider}:{hour_key}"

            # Use pipeline for atomic operations
            pipe = self.redis.pipeline()

            # 1. Increment request counters
            pipe.hincrby(metrics_key, "total_requests", 1)
            if success:
                pipe.hincrby(metrics_key, "successful_requests", 1)
            else:
                pipe.hincrby(metrics_key, "failed_requests", 1)

            # 2. Track tokens
            pipe.hincrby(metrics_key, "tokens_input", tokens_input)
            pipe.hincrby(metrics_key, "tokens_output", tokens_output)

            # 3. Track cost
            pipe.hincrbyfloat(metrics_key, "total_cost", cost)

            # 4. Set expiry (2 hours TTL for hourly aggregates)
            pipe.expire(metrics_key, 7200)

            # 5. Track latency in sorted set (score = timestamp, value = latency_ms)
            latency_key = f"latency:{provider}:{model}"
            timestamp = time.time()
            pipe.zadd(latency_key, {str(latency_ms): timestamp})

            # Remove old latencies (keep last hour only)
            cutoff = timestamp - 3600
            pipe.zremrangebyscore(latency_key, 0, cutoff)
            pipe.expire(latency_key, 7200)

            # 6. Track errors
            if not success and error_message:
                error_key = f"errors:{provider}"
                error_data = json.dumps({
                    "model": model,
                    "error": error_message[:500],  # Limit error message length
                    "timestamp": timestamp,
                    "latency_ms": latency_ms
                })
                pipe.lpush(error_key, error_data)
                pipe.ltrim(error_key, 0, 99)  # Keep last 100 errors
                pipe.expire(error_key, 3600)  # 1 hour TTL

            # 7. Update health score
            await self._update_health_score_pipe(pipe, provider, success)

            # Execute all operations atomically in a thread to avoid blocking the event loop
            await asyncio.to_thread(pipe.execute)

            logger.debug(
                f"Recorded metrics: {provider}/{model} - "
                f"latency={latency_ms}ms, success={success}, cost=${cost:.4f}"
            )

        except Exception as e:
            # Never let metrics recording break the application
            logger.warning(f"Failed to record Redis metrics: {e}")

    async def _update_health_score_pipe(self, pipe, provider: str, success: bool):
        """Update provider health score (internal helper for pipeline)"""
        health_key = f"health:{provider}"

        # Adjust health score based on success/failure
        delta = 2 if success else -5

        # We can't easily get current value in a pipeline, so we'll do this separately
        # This is a minor inconsistency but acceptable for health scores
        try:
            current = await asyncio.to_thread(self.redis.zscore, "provider_health", provider)
            if current is None:
                current = 100.0

            new_score = max(0.0, min(100.0, current + delta))
            pipe.zadd("provider_health", {provider: new_score})
        except Exception:
            # If we can't get current score, just set to a reasonable default
            pipe.zadd("provider_health", {provider: 85.0 if success else 50.0})

    async def get_provider_health(self, provider: str) -> float:
        """
        Get current health score for a provider.

        Returns:
            Health score (0-100), or 100.0 if no data
        """
        if not self.enabled:
            return 100.0

        try:
            score = self.redis.zscore("provider_health", provider)
            return float(score) if score is not None else 100.0
        except Exception as e:
            logger.warning(f"Failed to get health score for {provider}: {e}")
            return 100.0

    async def get_recent_errors(self, provider: str, limit: int = 100) -> list[dict]:
        """
        Get recent errors for a provider.

        Args:
            provider: Provider name
            limit: Maximum number of errors to return

        Returns:
            List of error dictionaries
        """
        if not self.enabled:
            return []

        try:
            error_key = f"errors:{provider}"
            errors = self.redis.lrange(error_key, 0, limit - 1)

            return [json.loads(err) for err in errors]
        except Exception as e:
            logger.warning(f"Failed to get recent errors for {provider}: {e}")
            return []

    async def get_hourly_stats(self, provider: str, hours: int = 24) -> dict[str, Any]:
        """
        Get hourly statistics for a provider.

        Args:
            provider: Provider name
            hours: Number of hours to look back

        Returns:
            Dictionary with hourly statistics
        """
        if not self.enabled:
            return {}

        try:
            stats = {}
            now = datetime.now(timezone.utc)

            for hour_offset in range(hours):
                hour_time = (now - timedelta(hours=hour_offset)).replace(
                    minute=0,
                    second=0,
                    microsecond=0
                )
                hour_key = hour_time.strftime("%Y-%m-%d:%H")
                metrics_key = f"metrics:{provider}:{hour_key}"

                hour_data = self.redis.hgetall(metrics_key)
                if hour_data:
                    stats[hour_key] = {
                        "total_requests": int(hour_data.get("total_requests", 0)),
                        "successful_requests": int(hour_data.get("successful_requests", 0)),
                        "failed_requests": int(hour_data.get("failed_requests", 0)),
                        "tokens_input": int(hour_data.get("tokens_input", 0)),
                        "tokens_output": int(hour_data.get("tokens_output", 0)),
                        "total_cost": float(hour_data.get("total_cost", 0.0)),
                    }

            return stats
        except Exception as e:
            logger.warning(f"Failed to get hourly stats for {provider}: {e}")
            return {}

    async def get_latency_percentiles(
        self,
        provider: str,
        model: str,
        percentiles: list[int] = [50, 95, 99]
    ) -> dict[str, float]:
        """
        Calculate latency percentiles from recent data.

        Args:
            provider: Provider name
            model: Model ID
            percentiles: List of percentiles to calculate (e.g., [50, 95, 99])

        Returns:
            Dictionary mapping percentile to latency in ms
        """
        if not self.enabled:
            return {}

        try:
            latency_key = f"latency:{provider}:{model}"

            # Get all latencies from last hour
            latencies = self.redis.zrange(latency_key, 0, -1)

            if not latencies:
                return {}

            # Convert to integers and sort
            latency_values = sorted([int(lat) for lat in latencies])
            n = len(latency_values)

            result = {}
            for p in percentiles:
                idx = max(0, min(n - 1, int((p / 100.0) * n)))
                result[f"p{p}"] = float(latency_values[idx])

            result["count"] = n
            result["avg"] = sum(latency_values) / n if n > 0 else 0.0

            return result
        except Exception as e:
            logger.warning(f"Failed to calculate latency percentiles: {e}")
            return {}

    async def update_circuit_breaker(
        self,
        provider: str,
        model: str,
        state: str,
        failure_count: int = 0
    ):
        """
        Update circuit breaker state in Redis.

        Args:
            provider: Provider name
            model: Model ID
            state: Circuit state (CLOSED, OPEN, HALF_OPEN)
            failure_count: Number of consecutive failures
        """
        if not self.enabled:
            return

        try:
            circuit_key = f"circuit:{provider}:{model}"
            circuit_data = json.dumps({
                "state": state,
                "failure_count": failure_count,
                "updated_at": time.time()
            })

            self.redis.setex(circuit_key, 300, circuit_data)  # 5 min TTL

            logger.debug(f"Updated circuit breaker: {provider}/{model} -> {state}")
        except Exception as e:
            logger.warning(f"Failed to update circuit breaker state: {e}")

    async def get_all_provider_health(self) -> dict[str, float]:
        """
        Get health scores for all providers.

        Returns:
            Dictionary mapping provider name to health score
        """
        if not self.enabled:
            return {}

        try:
            # Get all providers from sorted set (high to low)
            providers = self.redis.zrevrange("provider_health", 0, -1, withscores=True)

            # Decode bytes to strings if needed
            return {
                provider.decode() if isinstance(provider, bytes) else provider: score
                for provider, score in providers
            }
        except Exception as e:
            logger.warning(f"Failed to get all provider health: {e}")
            return {}

    async def cleanup_old_data(self, hours: int = 2):
        """
        Cleanup old metrics data from Redis.

        This is called periodically to remove old hourly aggregates.

        Args:
            hours: Keep data newer than this many hours
        """
        if not self.enabled:
            return

        try:
            # Calculate cutoff time
            now = datetime.now(timezone.utc)
            cutoff_time = (now - timedelta(hours=hours)).replace(
                minute=0,
                second=0,
                microsecond=0
            )
            cutoff_key = cutoff_time.strftime("%Y-%m-%d:%H")

            # Scan for old metric keys
            deleted_count = 0
            for key in self.redis.scan_iter("metrics:*"):
                # Decode key if it's bytes
                key_str = key.decode() if isinstance(key, bytes) else key

                # Extract hour from key (format: metrics:provider:YYYY-MM-DD:HH)
                parts = key_str.split(":")
                if len(parts) >= 3:
                    hour_part = parts[2]
                    if hour_part < cutoff_key:
                        self.redis.delete(key)
                        deleted_count += 1

            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} old metric keys")

        except Exception as e:
            logger.warning(f"Failed to cleanup old Redis data: {e}")


# Global instance
_redis_metrics = None


def get_redis_metrics() -> RedisMetrics:
    """Get global Redis metrics instance"""
    global _redis_metrics
    if _redis_metrics is None:
        _redis_metrics = RedisMetrics()
    return _redis_metrics
