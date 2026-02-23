"""
Prometheus exposition format exporter for GatewayZ metrics.

Converts collected metrics to Prometheus text exposition format (TYPE 0.0.4).
This format is compatible with Prometheus scraping and includes:
- Counters (monotonically increasing values)
- Gauges (values that can go up and down)
- Histograms (latency distributions)
"""

from typing import Any

from src.services.metrics_instrumentation import get_metrics_collector


class PrometheusExporter:
    """Exports metrics in Prometheus exposition format."""

    def __init__(self):
        """Initialize the exporter."""
        self.collector = get_metrics_collector()

    def export_metrics(self) -> str:
        """
        Export all metrics in Prometheus exposition format.

        Returns:
            Metrics in Prometheus text exposition format
        """
        lines = []

        # Get current metrics snapshot
        metrics = self.collector.get_metrics_snapshot()

        # Export request latency histogram metrics
        lines.extend(self._export_latency_metrics(metrics))

        # Export request count metrics
        lines.extend(self._export_request_count_metrics(metrics))

        # Export error count metrics
        lines.extend(self._export_error_metrics(metrics))

        # Export status code metrics
        lines.extend(self._export_status_code_metrics(metrics))

        # Export provider metrics
        lines.extend(self._export_provider_metrics(metrics))

        # Export model metrics
        lines.extend(self._export_model_metrics(metrics))

        # Export cache metrics
        lines.extend(self._export_cache_metrics(metrics))

        # Export database metrics
        lines.extend(self._export_database_metrics(metrics))

        # Export external API metrics
        lines.extend(self._export_external_api_metrics(metrics))

        # Export uptime metric
        lines.extend(self._export_uptime_metric(metrics))

        return "\n".join(lines) + "\n"

    def _export_latency_metrics(self, metrics: dict[str, Any]) -> list:
        """Export request latency histogram metrics."""
        lines = [
            "# HELP http_request_latency_seconds HTTP request latency in seconds",
            "# TYPE http_request_latency_seconds histogram",
        ]

        latency_data = metrics.get("latency", {})
        for endpoint, latency_values in latency_data.items():
            if latency_values["avg"] is not None:
                # Export as gauge metrics for simplicity
                lines.append(
                    f'http_request_latency_avg_seconds{{endpoint="{endpoint}"}} {latency_values["avg"]}'
                )
            if latency_values["p50"] is not None:
                lines.append(
                    f'http_request_latency_p50_seconds{{endpoint="{endpoint}"}} {latency_values["p50"]}'
                )
            if latency_values["p95"] is not None:
                lines.append(
                    f'http_request_latency_p95_seconds{{endpoint="{endpoint}"}} {latency_values["p95"]}'
                )
            if latency_values["p99"] is not None:
                lines.append(
                    f'http_request_latency_p99_seconds{{endpoint="{endpoint}"}} {latency_values["p99"]}'
                )

        return lines

    def _export_request_count_metrics(self, metrics: dict[str, Any]) -> list:
        """Export request count metrics."""
        lines = [
            "# HELP http_requests_total Total HTTP requests",
            "# TYPE http_requests_total counter",
        ]

        request_counts = metrics.get("requests", {})
        for endpoint, methods in request_counts.items():
            for method, count in methods.items():
                lines.append(
                    f'http_requests_total{{endpoint="{endpoint}",method="{method}"}} {count}'
                )

        return lines

    def _export_error_metrics(self, metrics: dict[str, Any]) -> list:
        """Export error count metrics."""
        lines = [
            "# HELP http_request_errors_total Total HTTP request errors",
            "# TYPE http_request_errors_total counter",
        ]

        error_counts = metrics.get("errors", {})
        for endpoint, methods in error_counts.items():
            for method, count in methods.items():
                lines.append(
                    f'http_request_errors_total{{endpoint="{endpoint}",method="{method}"}} {count}'
                )

        return lines

    def _export_status_code_metrics(self, metrics: dict[str, Any]) -> list:
        """Export HTTP status code metrics."""
        lines = [
            "# HELP http_response_status_total Total HTTP responses by status code",
            "# TYPE http_response_status_total counter",
        ]

        status_codes = metrics.get("status_codes", {})
        for endpoint, statuses in status_codes.items():
            for status, count in statuses.items():
                lines.append(
                    f'http_response_status_total{{endpoint="{endpoint}",status="{status}"}} {count}'
                )

        return lines

    def _export_provider_metrics(self, metrics: dict[str, Any]) -> list:
        """Export provider-specific metrics."""
        lines = [
            "# HELP provider_requests_total Total requests to provider",
            "# TYPE provider_requests_total counter",
            "# HELP provider_errors_total Total errors from provider",
            "# TYPE provider_errors_total counter",
            "# HELP provider_error_rate Error rate for provider",
            "# TYPE provider_error_rate gauge",
            "# HELP provider_latency_avg_seconds Average latency for provider",
            "# TYPE provider_latency_avg_seconds gauge",
            "# HELP provider_latency_min_seconds Minimum latency for provider",
            "# TYPE provider_latency_min_seconds gauge",
            "# HELP provider_latency_max_seconds Maximum latency for provider",
            "# TYPE provider_latency_max_seconds gauge",
        ]

        providers = metrics.get("providers", {})
        for provider, provider_metrics in providers.items():
            lines.append(
                f'provider_requests_total{{provider="{provider}"}} {provider_metrics["requests"]}'
            )
            lines.append(
                f'provider_errors_total{{provider="{provider}"}} {provider_metrics["errors"]}'
            )
            lines.append(
                f'provider_error_rate{{provider="{provider}"}} {provider_metrics["error_rate"]}'
            )
            lines.append(
                f'provider_latency_avg_seconds{{provider="{provider}"}} {provider_metrics["avg_latency"]}'
            )
            if provider_metrics["min_latency"] is not None:
                lines.append(
                    f'provider_latency_min_seconds{{provider="{provider}"}} {provider_metrics["min_latency"]}'
                )
            lines.append(
                f'provider_latency_max_seconds{{provider="{provider}"}} {provider_metrics["max_latency"]}'
            )

        return lines

    def _export_model_metrics(self, metrics: dict[str, Any]) -> list:
        """Export model-specific metrics."""
        lines = [
            "# HELP model_requests_total Total requests for model",
            "# TYPE model_requests_total counter",
            "# HELP model_errors_total Total errors for model",
            "# TYPE model_errors_total counter",
            "# HELP model_error_rate Error rate for model",
            "# TYPE model_error_rate gauge",
            "# HELP model_latency_avg_seconds Average latency for model",
            "# TYPE model_latency_avg_seconds gauge",
        ]

        models = metrics.get("models", {})
        for model, model_metrics in models.items():
            lines.append(
                f'model_requests_total{{model="{model}"}} {model_metrics["requests"]}'
            )
            lines.append(
                f'model_errors_total{{model="{model}"}} {model_metrics["errors"]}'
            )
            lines.append(
                f'model_error_rate{{model="{model}"}} {model_metrics["error_rate"]}'
            )
            lines.append(
                f'model_latency_avg_seconds{{model="{model}"}} {model_metrics["avg_latency"]}'
            )

        return lines

    def _export_cache_metrics(self, metrics: dict[str, Any]) -> list:
        """Export cache performance metrics."""
        lines = [
            "# HELP cache_hits_total Total cache hits",
            "# TYPE cache_hits_total counter",
            "# HELP cache_misses_total Total cache misses",
            "# TYPE cache_misses_total counter",
            "# HELP cache_hit_rate Cache hit rate",
            "# TYPE cache_hit_rate gauge",
        ]

        cache = metrics.get("cache", {})
        lines.append(f'cache_hits_total {cache.get("hits", 0)}')
        lines.append(f'cache_misses_total {cache.get("misses", 0)}')
        lines.append(f'cache_hit_rate {cache.get("hit_rate", 0)}')

        return lines

    def _export_database_metrics(self, metrics: dict[str, Any]) -> list:
        """Export database query metrics."""
        lines = [
            "# HELP db_queries_total Total database queries",
            "# TYPE db_queries_total counter",
            "# HELP db_query_latency_avg_seconds Average database query latency",
            "# TYPE db_query_latency_avg_seconds gauge",
        ]

        database = metrics.get("database", {})
        lines.append(f'db_queries_total {database.get("queries", 0)}')
        lines.append(f'db_query_latency_avg_seconds {database.get("avg_latency", 0)}')

        return lines

    def _export_external_api_metrics(self, metrics: dict[str, Any]) -> list:
        """Export external API call metrics."""
        lines = [
            "# HELP external_api_calls_total Total external API calls",
            "# TYPE external_api_calls_total counter",
            "# HELP external_api_errors_total Total external API errors",
            "# TYPE external_api_errors_total counter",
        ]

        external_apis = metrics.get("external_apis", {})
        calls = external_apis.get("calls", {})
        errors = external_apis.get("errors", {})

        for service, count in calls.items():
            lines.append(f'external_api_calls_total{{service="{service}"}} {count}')

        for service, count in errors.items():
            lines.append(f'external_api_errors_total{{service="{service}"}} {count}')

        return lines

    def _export_uptime_metric(self, metrics: dict[str, Any]) -> list:
        """Export application uptime metric."""
        lines = [
            "# HELP gatewayz_uptime_seconds GatewayZ API uptime in seconds",
            "# TYPE gatewayz_uptime_seconds gauge",
        ]

        uptime = metrics.get("uptime_seconds", 0)
        lines.append(f"gatewayz_uptime_seconds {uptime}")

        return lines
