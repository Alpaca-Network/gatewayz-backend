"""
Prometheus metrics instrumentation service for GatewayZ API.

This module provides comprehensive metrics collection for:
- Request latency (histogram with percentiles)
- Request counts by endpoint, method, and status
- Error rates and error types
- Provider-specific metrics
- Model-specific metrics
- Cache performance metrics
- Database query metrics
- External API call metrics
"""

import logging
import time
from collections import defaultdict
from datetime import datetime, UTC
from functools import wraps
from typing import Any, Callable

logger = logging.getLogger(__name__)


class MetricsCollector:
    """Collects and aggregates Prometheus metrics."""

    def __init__(self):
        """Initialize the metrics collector."""
        self.request_latencies = defaultdict(list)  # {endpoint: [latencies]}
        self.request_counts = defaultdict(lambda: defaultdict(int))  # {endpoint: {method: count}}
        self.error_counts = defaultdict(lambda: defaultdict(int))  # {endpoint: {method: count}}
        self.status_codes = defaultdict(lambda: defaultdict(int))  # {endpoint: {status: count}}
        self.provider_metrics = defaultdict(lambda: {
            "requests": 0,
            "errors": 0,
            "total_latency": 0.0,
            "min_latency": float("inf"),
            "max_latency": 0.0,
        })
        self.model_metrics = defaultdict(lambda: {
            "requests": 0,
            "errors": 0,
            "total_latency": 0.0,
        })
        self.cache_hits = 0
        self.cache_misses = 0
        self.db_queries = 0
        self.db_query_latency = 0.0
        self.external_api_calls = defaultdict(int)  # {service: count}
        self.external_api_errors = defaultdict(int)  # {service: count}
        self.start_time = datetime.now(UTC)

    def record_request(
        self,
        endpoint: str,
        method: str,
        latency_seconds: float,
        status_code: int,
        error: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        """
        Record a request metric.

        Args:
            endpoint: API endpoint path
            method: HTTP method (GET, POST, etc.)
            latency_seconds: Request latency in seconds
            status_code: HTTP status code
            error: Error message if request failed
            provider: Provider name if applicable
            model: Model name if applicable
        """
        # Record latency
        self.request_latencies[endpoint].append(latency_seconds)

        # Record request count
        self.request_counts[endpoint][method] += 1

        # Record status code
        self.status_codes[endpoint][status_code] += 1

        # Record error if present
        if error or status_code >= 400:
            self.error_counts[endpoint][method] += 1

        # Record provider metrics
        if provider:
            self.provider_metrics[provider]["requests"] += 1
            self.provider_metrics[provider]["total_latency"] += latency_seconds
            self.provider_metrics[provider]["min_latency"] = min(
                self.provider_metrics[provider]["min_latency"], latency_seconds
            )
            self.provider_metrics[provider]["max_latency"] = max(
                self.provider_metrics[provider]["max_latency"], latency_seconds
            )
            if error or status_code >= 400:
                self.provider_metrics[provider]["errors"] += 1

        # Record model metrics
        if model:
            self.model_metrics[model]["requests"] += 1
            self.model_metrics[model]["total_latency"] += latency_seconds
            if error or status_code >= 400:
                self.model_metrics[model]["errors"] += 1

    def record_cache_hit(self) -> None:
        """Record a cache hit."""
        self.cache_hits += 1

    def record_cache_miss(self) -> None:
        """Record a cache miss."""
        self.cache_misses += 1

    def record_db_query(self, latency_seconds: float) -> None:
        """Record a database query."""
        self.db_queries += 1
        self.db_query_latency += latency_seconds

    def record_external_api_call(self, service: str, error: bool = False) -> None:
        """Record an external API call."""
        self.external_api_calls[service] += 1
        if error:
            self.external_api_errors[service] += 1

    def get_percentile(self, endpoint: str, percentile: float) -> float | None:
        """
        Calculate latency percentile for an endpoint.

        Args:
            endpoint: API endpoint
            percentile: Percentile to calculate (0.0-1.0)

        Returns:
            Percentile value in seconds or None if no data
        """
        latencies = self.request_latencies.get(endpoint, [])
        if not latencies:
            return None

        sorted_latencies = sorted(latencies)
        index = int(len(sorted_latencies) * percentile)
        return sorted_latencies[min(index, len(sorted_latencies) - 1)]

    def get_average_latency(self, endpoint: str) -> float | None:
        """Get average latency for an endpoint."""
        latencies = self.request_latencies.get(endpoint, [])
        if not latencies:
            return None
        return sum(latencies) / len(latencies)

    def get_metrics_snapshot(self) -> dict[str, Any]:
        """
        Get a snapshot of all current metrics.

        Returns:
            Dictionary containing all metrics
        """
        endpoints = set(self.request_counts.keys()) | set(self.request_latencies.keys())

        latency_metrics = {}
        for endpoint in endpoints:
            latency_metrics[endpoint] = {
                "avg": self.get_average_latency(endpoint),
                "p50": self.get_percentile(endpoint, 0.50),
                "p95": self.get_percentile(endpoint, 0.95),
                "p99": self.get_percentile(endpoint, 0.99),
            }

        provider_metrics = {}
        for provider, metrics in self.provider_metrics.items():
            avg_latency = (
                metrics["total_latency"] / metrics["requests"]
                if metrics["requests"] > 0
                else 0
            )
            provider_metrics[provider] = {
                "requests": metrics["requests"],
                "errors": metrics["errors"],
                "error_rate": (
                    metrics["errors"] / metrics["requests"]
                    if metrics["requests"] > 0
                    else 0
                ),
                "avg_latency": avg_latency,
                "min_latency": (
                    metrics["min_latency"]
                    if metrics["min_latency"] != float("inf")
                    else None
                ),
                "max_latency": metrics["max_latency"],
            }

        model_metrics = {}
        for model, metrics in self.model_metrics.items():
            avg_latency = (
                metrics["total_latency"] / metrics["requests"]
                if metrics["requests"] > 0
                else 0
            )
            model_metrics[model] = {
                "requests": metrics["requests"],
                "errors": metrics["errors"],
                "error_rate": (
                    metrics["errors"] / metrics["requests"]
                    if metrics["requests"] > 0
                    else 0
                ),
                "avg_latency": avg_latency,
            }

        cache_hit_rate = (
            self.cache_hits / (self.cache_hits + self.cache_misses)
            if (self.cache_hits + self.cache_misses) > 0
            else 0
        )

        db_avg_latency = (
            self.db_query_latency / self.db_queries if self.db_queries > 0 else 0
        )

        uptime_seconds = (datetime.now(UTC) - self.start_time).total_seconds()

        return {
            "latency": latency_metrics,
            "requests": dict(self.request_counts),
            "errors": dict(self.error_counts),
            "status_codes": dict(self.status_codes),
            "providers": provider_metrics,
            "models": model_metrics,
            "cache": {
                "hits": self.cache_hits,
                "misses": self.cache_misses,
                "hit_rate": cache_hit_rate,
            },
            "database": {
                "queries": self.db_queries,
                "avg_latency": db_avg_latency,
            },
            "external_apis": {
                "calls": dict(self.external_api_calls),
                "errors": dict(self.external_api_errors),
            },
            "uptime_seconds": uptime_seconds,
        }

    def reset(self) -> None:
        """Reset all metrics."""
        self.request_latencies.clear()
        self.request_counts.clear()
        self.error_counts.clear()
        self.status_codes.clear()
        self.provider_metrics.clear()
        self.model_metrics.clear()
        self.cache_hits = 0
        self.cache_misses = 0
        self.db_queries = 0
        self.db_query_latency = 0.0
        self.external_api_calls.clear()
        self.external_api_errors.clear()
        self.start_time = datetime.now(UTC)


# Global metrics collector instance
_collector: MetricsCollector | None = None


def get_metrics_collector() -> MetricsCollector:
    """Get or create the global metrics collector instance."""
    global _collector
    if _collector is None:
        _collector = MetricsCollector()
    return _collector


def track_request(
    endpoint: str,
    method: str = "GET",
    provider: str | None = None,
    model: str | None = None,
) -> Callable:
    """
    Decorator to track request metrics.

    Args:
        endpoint: API endpoint path
        method: HTTP method
        provider: Provider name if applicable
        model: Model name if applicable

    Returns:
        Decorated function that tracks metrics
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            status_code = 200
            error = None

            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                error = str(e)
                status_code = 500
                raise
            finally:
                latency = time.time() - start_time
                collector = get_metrics_collector()
                collector.record_request(
                    endpoint=endpoint,
                    method=method,
                    latency_seconds=latency,
                    status_code=status_code,
                    error=error,
                    provider=provider,
                    model=model,
                )

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            status_code = 200
            error = None

            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                error = str(e)
                status_code = 500
                raise
            finally:
                latency = time.time() - start_time
                collector = get_metrics_collector()
                collector.record_request(
                    endpoint=endpoint,
                    method=method,
                    latency_seconds=latency,
                    status_code=status_code,
                    error=error,
                    provider=provider,
                    model=model,
                )

        # Return appropriate wrapper based on function type
        if hasattr(func, "__await__"):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator
