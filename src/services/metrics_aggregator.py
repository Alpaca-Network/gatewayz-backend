"""
Metrics aggregation service - Periodically aggregates Redis metrics to database.

This service runs hourly to:
1. Collect metrics from Redis (last hour)
2. Calculate aggregations (counts, percentiles, error rates)
3. Write to metrics_hourly_aggregates table
4. Clean up old Redis data

Designed to run as a background task or cron job.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.config.redis_config import get_redis_client
from src.config.supabase_config import get_supabase_client
from src.services.prometheus_metrics import track_database_query

logger = logging.getLogger(__name__)


class MetricsAggregator:
    """
    Aggregates metrics from Redis to database for long-term storage.
    """

    def __init__(self, redis_client=None, supabase_client=None):
        """Initialize aggregator with optional clients"""
        self.redis = redis_client or get_redis_client()
        self.supabase = supabase_client or get_supabase_client()
        self.enabled = self.redis is not None and self.supabase is not None

        if not self.enabled:
            logger.warning("Metrics aggregator disabled - Redis or Supabase not available")

    async def aggregate_hour(self, hour_timestamp: datetime) -> dict[str, Any]:
        """
        Aggregate metrics for a specific hour from Redis.

        Args:
            hour_timestamp: The hour to aggregate (rounded to hour start)

        Returns:
            Dictionary with aggregation statistics
        """
        if not self.enabled:
            return {"status": "disabled", "aggregated": 0}

        # Round to hour start
        hour = hour_timestamp.replace(minute=0, second=0, microsecond=0)
        hour_key = hour.strftime("%Y-%m-%d:%H")

        logger.info(f"Starting aggregation for hour: {hour_key}")

        aggregated_count = 0
        errors = []

        try:
            # Scan for all provider metrics for this hour
            pattern = f"metrics:*:{hour_key}"
            metrics_keys = list(self.redis.scan_iter(pattern))

            logger.info(f"Found {len(metrics_keys)} metric keys to aggregate")

            for metrics_key in metrics_keys:
                try:
                    # Parse provider from key (format: metrics:provider:YYYY-MM-DD:HH)
                    parts = metrics_key.split(":")
                    if len(parts) < 3:
                        logger.warning(f"Invalid metrics key format: {metrics_key}")
                        continue

                    provider = parts[1]

                    # Get metrics data
                    metrics_data = self.redis.hgetall(metrics_key)
                    if not metrics_data:
                        continue

                    total_requests = int(metrics_data.get("total_requests", 0))
                    successful_requests = int(metrics_data.get("successful_requests", 0))
                    failed_requests = int(metrics_data.get("failed_requests", 0))
                    tokens_input = int(metrics_data.get("tokens_input", 0))
                    tokens_output = int(metrics_data.get("tokens_output", 0))
                    total_cost = float(metrics_data.get("total_cost", 0.0))

                    # Calculate error rate
                    error_rate = failed_requests / total_requests if total_requests > 0 else 0.0

                    # Get model-specific metrics (we aggregate by provider for now)
                    # In a more detailed version, you could track per-model metrics
                    model = "all"  # Aggregate across all models for this provider

                    # Calculate latency percentiles
                    latency_stats = await self._calculate_latency_stats(provider, hour_key)

                    # Prepare aggregate record
                    aggregate = {
                        "hour": hour.isoformat(),
                        "provider": provider,
                        "model": model,
                        "total_requests": total_requests,
                        "successful_requests": successful_requests,
                        "failed_requests": failed_requests,
                        "total_tokens_input": tokens_input,
                        "total_tokens_output": tokens_output,
                        "total_cost_credits": total_cost,
                        "avg_latency_ms": latency_stats.get("avg"),
                        "p50_latency_ms": latency_stats.get("p50"),
                        "p95_latency_ms": latency_stats.get("p95"),
                        "p99_latency_ms": latency_stats.get("p99"),
                        "min_latency_ms": latency_stats.get("min"),
                        "max_latency_ms": latency_stats.get("max"),
                        "error_rate": error_rate,
                    }

                    # Insert or update in database
                    with track_database_query(
                        table="metrics_hourly_aggregates", operation="upsert"
                    ):
                        result = (
                            self.supabase.table("metrics_hourly_aggregates")
                            .upsert(aggregate, on_conflict="hour,provider,model")
                            .execute()
                        )

                    if result.data:
                        aggregated_count += 1
                        logger.debug(
                            f"Aggregated metrics for {provider} at {hour_key}: "
                            f"{total_requests} requests, ${total_cost:.4f}"
                        )
                    else:
                        error_msg = f"Failed to upsert metrics for {provider} at {hour_key}"
                        logger.error(error_msg)
                        errors.append(error_msg)

                except Exception as e:
                    error_msg = f"Error aggregating {metrics_key}: {e}"
                    logger.error(error_msg)
                    errors.append(error_msg)

            # Cleanup old Redis data (keep last 2 hours)
            await self._cleanup_old_redis_data(hours=2)

            logger.info(
                f"Aggregation complete for {hour_key}: "
                f"{aggregated_count} providers aggregated, {len(errors)} errors"
            )

            return {
                "status": "success",
                "hour": hour_key,
                "aggregated": aggregated_count,
                "errors": errors,
            }

        except Exception as e:
            logger.error(f"Fatal error during aggregation: {e}", exc_info=True)
            return {
                "status": "error",
                "hour": hour_key,
                "aggregated": aggregated_count,
                "error": str(e),
            }

    async def _calculate_latency_stats(
        self, provider: str, hour_key: str
    ) -> dict[str, float | None]:
        """
        Calculate latency statistics from Redis sorted sets.

        Args:
            provider: Provider name
            hour_key: Hour key (YYYY-MM-DD:HH)

        Returns:
            Dictionary with latency statistics
        """
        try:
            # Get all latency values for this provider (across all models)
            # In production, you might want to query per model
            latency_pattern = f"latency:{provider}:*"
            latency_keys = list(self.redis.scan_iter(latency_pattern))

            all_latencies = []
            for latency_key in latency_keys:
                latencies = self.redis.zrange(latency_key, 0, -1)
                all_latencies.extend([int(lat) for lat in latencies])

            if not all_latencies:
                return {
                    "avg": None,
                    "p50": None,
                    "p95": None,
                    "p99": None,
                    "min": None,
                    "max": None,
                }

            # Sort for percentile calculation
            sorted_latencies = sorted(all_latencies)
            n = len(sorted_latencies)

            # Calculate percentiles
            p50_idx = max(0, min(n - 1, int(0.50 * n)))
            p95_idx = max(0, min(n - 1, int(0.95 * n)))
            p99_idx = max(0, min(n - 1, int(0.99 * n)))

            return {
                "avg": sum(all_latencies) / n,
                "p50": sorted_latencies[p50_idx],
                "p95": sorted_latencies[p95_idx],
                "p99": sorted_latencies[p99_idx],
                "min": sorted_latencies[0],
                "max": sorted_latencies[-1],
            }

        except Exception as e:
            logger.warning(f"Failed to calculate latency stats for {provider}: {e}")
            return {
                "avg": None,
                "p50": None,
                "p95": None,
                "p99": None,
                "min": None,
                "max": None,
            }

    async def _cleanup_old_redis_data(self, hours: int = 2):
        """
        Clean up old metrics data from Redis.

        Args:
            hours: Keep data newer than this many hours
        """
        try:
            now = datetime.now(UTC)
            cutoff_time = now - timedelta(hours=hours)
            cutoff_hour = cutoff_time.replace(minute=0, second=0, microsecond=0)
            cutoff_key = cutoff_hour.strftime("%Y-%m-%d:%H")

            deleted_count = 0

            # Cleanup metrics keys
            for key in self.redis.scan_iter("metrics:*"):
                parts = key.split(":")
                if len(parts) >= 3:
                    hour_part = parts[2]
                    if hour_part < cutoff_key:
                        self.redis.delete(key)
                        deleted_count += 1

            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} old Redis metric keys")

        except Exception as e:
            logger.warning(f"Failed to cleanup old Redis data: {e}")

    async def aggregate_last_hour(self) -> dict[str, Any]:
        """
        Aggregate metrics for the last completed hour.

        Returns:
            Aggregation result dictionary
        """
        # Get last completed hour (current time - 1 hour, rounded)
        now = datetime.now(UTC)
        last_hour = (now - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

        return await self.aggregate_hour(last_hour)

    async def run_periodic_aggregation(self, interval_minutes: int = 60):
        """
        Run periodic aggregation in a loop.

        Args:
            interval_minutes: How often to run aggregation (default: 60 minutes)
        """
        logger.info(f"Starting periodic metrics aggregation (every {interval_minutes} minutes)")

        while True:
            try:
                result = await self.aggregate_last_hour()
                logger.info(f"Periodic aggregation result: {result}")

                # Refresh materialized view
                try:
                    self.supabase.rpc("refresh_provider_stats_24h").execute()
                    logger.info("Refreshed provider_stats_24h materialized view")
                except Exception as e:
                    logger.warning(f"Failed to refresh materialized view: {e}")

            except Exception as e:
                logger.error(f"Error in periodic aggregation: {e}", exc_info=True)

            # Sleep until next interval
            await asyncio.sleep(interval_minutes * 60)


# Global instance
_aggregator = None


def get_metrics_aggregator() -> MetricsAggregator:
    """Get global metrics aggregator instance"""
    global _aggregator
    if _aggregator is None:
        _aggregator = MetricsAggregator()
    return _aggregator


async def run_aggregation_job():
    """
    Standalone function to run aggregation once.

    Useful for cron jobs or one-off executions.
    """
    aggregator = get_metrics_aggregator()
    result = await aggregator.aggregate_last_hour()
    logger.info(f"Aggregation job complete: {result}")
    return result


if __name__ == "__main__":
    # Run aggregation when executed directly
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    if "--periodic" in sys.argv:
        # Run periodic aggregation
        aggregator = get_metrics_aggregator()
        asyncio.run(aggregator.run_periodic_aggregation())
    else:
        # Run once
        asyncio.run(run_aggregation_job())
