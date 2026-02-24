"""
Prometheus metrics collection and management for Gatewayz API Gateway.

This module initializes and exposes Prometheus metrics for monitoring:
- HTTP request metrics (count, duration, status codes)
- Model inference metrics (requests, tokens, latency by provider/model)
- Database metrics (queries, latency)
- Cache metrics (hits, misses, operations)
- Rate limiting metrics (blocked requests, current limits)
- Provider health metrics (availability, error rates)
- Business metrics (credits used, token consumption)
"""

import logging
import os
import time
from contextlib import contextmanager

from prometheus_client import REGISTRY, Counter, Gauge, Histogram, Info, Summary

logger = logging.getLogger(__name__)

# Get app name from environment or use default
APP_NAME = os.environ.get("APP_NAME", "gatewayz")


def get_trace_exemplar() -> dict[str, str] | None:
    """
    Get the current OpenTelemetry trace ID as a Prometheus exemplar.

    Returns {"trace_id": "<hex>"} when a valid trace context exists,
    or None when tracing is unavailable. Prometheus exemplars let you
    click from a metric datapoint directly to the corresponding trace
    in Tempo via Grafana.
    """
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.is_valid:
            return {"trace_id": format(ctx.trace_id, "032x")}
    except Exception:
        pass
    return None


# Clear any existing metrics from the registry to avoid duplication issues
# This is necessary because Prometheus uses a global registry that persists across imports
try:
    collectors = list(REGISTRY._collector_to_names.keys())
    for collector in collectors:
        try:
            REGISTRY.unregister(collector)
        except Exception:
            pass  # Ignore errors from default collectors
    logger.debug("Cleared Prometheus registry")
except Exception as e:
    logger.warning(f"Could not clear Prometheus registry: {e}")

# Re-register the built-in default collectors that expose process-level metrics.
# The clearing loop above removes ProcessCollector, PlatformCollector, and GCCollector
# along with custom metrics, causing process_cpu_seconds_total,
# process_resident_memory_bytes, process_open_fds, process_max_fds, and
# process_start_time_seconds to disappear from /metrics.
# These are required by the CPU / Saturation panels in Grafana.
try:
    from prometheus_client import GCCollector, PlatformCollector, ProcessCollector

    for collector_cls in (ProcessCollector, PlatformCollector, GCCollector):
        try:
            REGISTRY.register(collector_cls())
        except ValueError:
            pass  # Already registered (e.g. uvicorn --reload skipped unregister)
    logger.debug("Re-registered default process/platform/gc collectors")
except Exception as e:
    logger.warning(f"Could not re-register default collectors: {e}")


# Helper function to handle metric registration with --reload support
def get_or_create_metric(metric_class, name, *args, **kwargs):
    """
    Get existing metric or create new one.
    Handles duplicate registration errors when using uvicorn --reload
    """
    # IMPORTANT: Check for existing metric FIRST (before trying to create)
    # This prevents duplication errors during reload
    for collector in list(REGISTRY._collector_to_names.keys()):
        if hasattr(collector, "_name") and collector._name == name:
            logger.debug(f"Reusing existing metric: {name}")
            return collector

    # Metric doesn't exist, create it
    try:
        return metric_class(name, *args, **kwargs)
    except ValueError as e:
        # This shouldn't happen now that we check first, but keep as safety
        logger.warning(f"Unexpected duplicate metric error for {name}: {e}")
        raise


# ==================== Application Info ====================
# This metric helps Grafana dashboard populate the app_name variable dropdown
fastapi_app_info = get_or_create_metric(Info, "fastapi_app_info", "FastAPI application information")
# Set the app_name label value after creation (idempotent operation)
try:
    fastapi_app_info.info({"app_name": APP_NAME})
except Exception:
    pass  # Already set

# ==================== HTTP Request Metrics (Grafana Dashboard Compatible) ====================
# These metrics are compatible with Grafana FastAPI Observability Dashboard (ID: 16110)
fastapi_requests_total = get_or_create_metric(
    Counter,
    "fastapi_requests_total",
    "Total FastAPI requests",
    ["app_name", "method", "path", "status_code", "status_class"],
)

fastapi_requests_duration_seconds = get_or_create_metric(
    Histogram,
    "fastapi_requests_duration_seconds",
    "FastAPI request duration in seconds",
    ["app_name", "method", "path"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)

fastapi_requests_in_progress = get_or_create_metric(
    Gauge,
    "fastapi_requests_in_progress",
    "Number of HTTP requests currently being processed",
    ["app_name", "method", "path"],
)

# Legacy metrics for backward compatibility
http_request_count = get_or_create_metric(
    Counter,
    "http_requests_total",
    "Total HTTP requests by method, endpoint and status code",
    ["method", "endpoint", "status_code", "status_class"],
)

http_request_duration = get_or_create_metric(
    Histogram,
    "http_request_duration_seconds",
    "HTTP request duration in seconds by method and endpoint",
    ["method", "endpoint"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)

# Additional metrics for request/response size tracking
fastapi_request_size_bytes = get_or_create_metric(
    Histogram,
    "fastapi_request_size_bytes",
    "HTTP request body size in bytes",
    ["app_name", "method", "path"],
    buckets=(100, 1000, 10000, 100000, 1000000),
)

fastapi_response_size_bytes = get_or_create_metric(
    Histogram,
    "fastapi_response_size_bytes",
    "HTTP response body size in bytes",
    ["app_name", "method", "path"],
    buckets=(100, 1000, 10000, 100000, 1000000),
)

# Exception tracking for Grafana dashboard
fastapi_exceptions_total = get_or_create_metric(
    Counter,
    "fastapi_exceptions_total",
    "Total FastAPI exceptions",
    ["app_name", "exception_type"],
)

# ==================== Model Inference Metrics ====================
model_inference_requests = get_or_create_metric(
    Counter,
    "model_inference_requests_total",
    "Total model inference requests",
    ["provider", "model", "status"],
)

model_inference_duration = get_or_create_metric(
    Histogram,
    "model_inference_duration_seconds",
    "Model inference duration in seconds",
    ["provider", "model"],
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 25, 60),
)

tokens_used = get_or_create_metric(
    Counter,
    "tokens_used_total",
    "Total tokens used (input + output)",
    ["provider", "model", "token_type"],
)

credits_used = get_or_create_metric(
    Counter,
    "credits_used_total",
    "Total credits consumed",
    ["provider", "model"],
)

# ==================== Pricing Metrics ====================
# Track when default pricing is used (potential under-billing)
default_pricing_usage_counter = get_or_create_metric(
    Counter,
    "gatewayz_default_pricing_usage_total",
    "Count of requests using default pricing (pricing data not found). High values indicate missing pricing data.",
    ["model"],
)

# ==================== Cost Tracking Metrics ====================
# Track actual USD costs for billing and budget monitoring
api_cost_usd_total = get_or_create_metric(
    Counter,
    "gatewayz_api_cost_usd_total",
    "Total API cost in USD",
    ["provider", "model"],
)

api_cost_per_request = get_or_create_metric(
    Histogram,
    "gatewayz_api_cost_per_request_usd",
    "Cost per API request in USD",
    ["provider", "model"],
    buckets=(0.00001, 0.0001, 0.001, 0.01, 0.1, 1.0, 10.0, 100.0),
)

cost_per_1k_tokens = get_or_create_metric(
    Histogram,
    "gatewayz_cost_per_1k_tokens_usd",
    "Cost per 1000 tokens in USD",
    ["provider", "model", "token_type"],  # token_type: input, output
    buckets=(0.0001, 0.001, 0.01, 0.1, 1.0, 10.0),
)

# ==================== Catalog Cache Metrics ====================
# Track catalog response caching performance for monitoring cache effectiveness
catalog_cache_hits = get_or_create_metric(
    Counter,
    "catalog_cache_hits_total",
    "Total catalog cache hits (successful cache retrievals)",
    ["gateway"],  # gateway: openrouter, anthropic, groq, all, etc.
)

catalog_cache_misses = get_or_create_metric(
    Counter,
    "catalog_cache_misses_total",
    "Total catalog cache misses (cache not found, fetch required)",
    ["gateway"],
)

catalog_cache_size_bytes = get_or_create_metric(
    Gauge,
    "catalog_cache_size_bytes",
    "Size of catalog cache in bytes per gateway",
    ["gateway"],
)

catalog_cache_invalidations = get_or_create_metric(
    Counter,
    "catalog_cache_invalidations_total",
    "Total catalog cache invalidation operations",
    ["gateway", "reason"],  # reason: model_sync, manual, expired
)

# ==================== Read Replica Metrics ====================
# Track read replica usage for monitoring database load distribution
read_replica_queries_total = get_or_create_metric(
    Counter,
    "read_replica_queries_total",
    "Total queries routed to read replica",
    ["table", "status"],  # status: success, error, fallback_to_primary
)

read_replica_connection_errors = get_or_create_metric(
    Counter,
    "read_replica_connection_errors_total",
    "Total read replica connection errors (fallback to primary)",
)

daily_cost_estimate = get_or_create_metric(
    Gauge,
    "gatewayz_daily_cost_estimate_usd",
    "Estimated daily cost in USD (updated hourly)",
    ["provider"],
)

monthly_cost_estimate = get_or_create_metric(
    Gauge,
    "gatewayz_monthly_cost_estimate_usd",
    "Estimated monthly cost in USD (updated daily)",
    ["provider"],
)

# Cost savings from caching
cache_cost_savings_usd = get_or_create_metric(
    Counter,
    "gatewayz_cache_cost_savings_usd_total",
    "Total cost saved from cache hits in USD",
    ["provider", "model", "cache_type"],  # cache_type: butter, redis, local
)

# User-level cost tracking (aggregated by plan type for privacy)
user_cost_by_plan = get_or_create_metric(
    Counter,
    "gatewayz_user_cost_by_plan_usd_total",
    "Total user costs by plan type in USD",
    ["plan_type"],  # free, trial, starter, professional, enterprise
)

# ==================== Credit Deduction Metrics ====================
# Track credit deduction success/failure for billing reliability monitoring
credit_deduction_total = get_or_create_metric(
    Counter,
    "gatewayz_credit_deduction_total",
    "Total credit deduction attempts",
    ["status", "endpoint", "is_streaming"],  # status: success, failed, retried
)

credit_deduction_amount_usd = get_or_create_metric(
    Counter,
    "gatewayz_credit_deduction_amount_usd_total",
    "Total USD amount of credit deductions",
    ["status", "endpoint"],  # status: success, failed
)

credit_deduction_retry_count = get_or_create_metric(
    Counter,
    "gatewayz_credit_deduction_retry_total",
    "Total credit deduction retry attempts",
    ["attempt_number", "endpoint"],  # attempt_number: 1, 2, 3
)

credit_deduction_latency = get_or_create_metric(
    Histogram,
    "gatewayz_credit_deduction_latency_seconds",
    "Credit deduction operation latency",
    ["endpoint", "is_streaming"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

streaming_background_task_failures = get_or_create_metric(
    Counter,
    "gatewayz_streaming_background_task_failures_total",
    "Total streaming background task failures (potential missed credit deductions)",
    ["failure_type", "endpoint"],  # failure_type: credit_deduction, activity_logging, etc.
)

missed_credit_deductions_usd = get_or_create_metric(
    Counter,
    "gatewayz_missed_credit_deductions_usd_total",
    "Total USD amount of potentially missed credit deductions due to failures",
    ["reason"],  # reason: background_task_failure, retry_exhausted, etc.
)

# ==================== Token Estimation Metrics ====================
# Track when token counts are estimated vs provided by providers,
# and the accuracy of estimations for calibration purposes.

token_count_source_total = get_or_create_metric(
    Counter,
    "gatewayz_token_count_source_total",
    "Count of streaming requests by token count source (provider-reported vs estimated)",
    ["provider", "model", "source"],  # source: provider, tiktoken, word_heuristic
)

token_estimation_accuracy_ratio = get_or_create_metric(
    Histogram,
    "gatewayz_token_estimation_accuracy_ratio",
    "Ratio of estimated to actual token count when both are available (1.0 = perfect). "
    "Values >1 indicate over-estimation, <1 under-estimation.",
    ["provider", "estimation_method", "token_type"],  # token_type: prompt, completion, total
    buckets=(0.25, 0.5, 0.7, 0.8, 0.9, 0.95, 1.0, 1.05, 1.1, 1.2, 1.5, 2.0, 4.0),
)

token_estimation_delta = get_or_create_metric(
    Histogram,
    "gatewayz_token_estimation_delta",
    "Absolute delta between estimated and actual token count (estimated - actual). "
    "Positive values = over-estimation, negative = under-estimation.",
    ["provider", "estimation_method", "token_type"],
    buckets=(-1000, -500, -200, -100, -50, -20, -10, 0, 10, 20, 50, 100, 200, 500, 1000),
)


def record_token_count_source(provider: str, model: str, source: str):
    """Record whether token counts came from the provider or were estimated.

    Args:
        provider: Provider name (e.g. "openrouter", "chutes").
        model: Model ID.
        source: One of "provider", "tiktoken", "word_heuristic".
    """
    try:
        token_count_source_total.labels(provider=provider, model=model, source=source).inc()
    except Exception:
        pass  # Never break the main flow


def record_token_estimation_accuracy(
    provider: str,
    estimation_method: str,
    estimated_prompt: int,
    estimated_completion: int,
    actual_prompt: int,
    actual_completion: int,
):
    """Record accuracy metrics when both estimated and actual counts are available.

    This is called when a provider returns usage data *and* we also
    computed an estimate, allowing us to calibrate the estimation method.

    Args:
        provider: Provider name.
        estimation_method: "tiktoken" or "word_heuristic".
        estimated_prompt: Estimated prompt token count.
        estimated_completion: Estimated completion token count.
        actual_prompt: Provider-reported prompt token count.
        actual_completion: Provider-reported completion token count.
    """
    try:
        estimated_total = estimated_prompt + estimated_completion
        actual_total = actual_prompt + actual_completion

        for token_type, estimated, actual in [
            ("prompt", estimated_prompt, actual_prompt),
            ("completion", estimated_completion, actual_completion),
            ("total", estimated_total, actual_total),
        ]:
            if actual > 0:
                ratio = estimated / actual
                token_estimation_accuracy_ratio.labels(
                    provider=provider,
                    estimation_method=estimation_method,
                    token_type=token_type,
                ).observe(ratio)

            delta = estimated - actual
            token_estimation_delta.labels(
                provider=provider,
                estimation_method=estimation_method,
                token_type=token_type,
            ).observe(delta)
    except Exception:
        pass  # Never break the main flow


# ==================== Database Metrics ====================
database_query_count = get_or_create_metric(
    Counter,
    "database_queries_total",
    "Total database queries",
    ["table", "operation"],
)

database_query_duration = get_or_create_metric(
    Summary,
    "database_query_duration_seconds",
    "Database query duration in seconds",
    ["table"],
)

# ==================== Connection Pool Metrics ====================
connection_pool_size = get_or_create_metric(
    Gauge,
    "connection_pool_size",
    "Total number of connections in the pool",
    ["pool_name"],
)

connection_pool_active = get_or_create_metric(
    Gauge,
    "connection_pool_active_connections",
    "Number of active connections currently in use",
    ["pool_name"],
)

connection_pool_idle = get_or_create_metric(
    Gauge,
    "connection_pool_idle_connections",
    "Number of idle connections available for use",
    ["pool_name"],
)

connection_pool_utilization = get_or_create_metric(
    Gauge,
    "connection_pool_utilization_ratio",
    "Connection pool utilization ratio (active/total)",
    ["pool_name"],
)

connection_pool_errors = get_or_create_metric(
    Counter,
    "connection_pool_errors_total",
    "Total number of connection pool errors",
    ["pool_name", "error_type"],
)

# ==================== Cache Metrics ====================
cache_hits = get_or_create_metric(
    Counter,
    "cache_hits_total",
    "Total cache hits",
    ["cache_name"],
)

cache_misses = get_or_create_metric(
    Counter,
    "cache_misses_total",
    "Total cache misses",
    ["cache_name"],
)

# ==================== Butter.dev Cache Metrics ====================
# Metrics for tracking LLM response caching via Butter.dev
butter_cache_requests = get_or_create_metric(
    Counter,
    "butter_cache_requests_total",
    "Total requests routed through Butter.dev cache",
    ["provider", "model", "cache_result"],  # cache_result: hit, miss, error, skipped
)

butter_cache_savings_usd = get_or_create_metric(
    Counter,
    "butter_cache_savings_usd_total",
    "Total USD savings from Butter.dev cache hits",
    ["provider", "model"],
)

butter_cache_latency = get_or_create_metric(
    Histogram,
    "butter_cache_latency_seconds",
    "Butter.dev request latency in seconds",
    ["provider", "cache_result"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# ==================== Circuit Breaker Metrics ====================
# Metrics for tracking circuit breaker state and behavior
circuit_breaker_state_transitions = get_or_create_metric(
    Counter,
    "circuit_breaker_state_transitions_total",
    "Total circuit breaker state transitions",
    ["provider", "from_state", "to_state"],
)

circuit_breaker_failures = get_or_create_metric(
    Counter,
    "circuit_breaker_failures_total",
    "Total failures recorded by circuit breaker",
    ["provider", "state"],
)

circuit_breaker_successes = get_or_create_metric(
    Counter,
    "circuit_breaker_successes_total",
    "Total successes recorded by circuit breaker",
    ["provider", "state"],
)

circuit_breaker_rejected_requests = get_or_create_metric(
    Counter,
    "circuit_breaker_rejected_requests_total",
    "Total requests rejected by circuit breaker",
    ["provider"],
)

circuit_breaker_current_state = get_or_create_metric(
    Gauge,
    "circuit_breaker_current_state",
    "Current circuit breaker state (1=active state, 0=inactive state)",
    ["provider", "state"],
)

butter_cache_errors = get_or_create_metric(
    Counter,
    "butter_cache_errors_total",
    "Total Butter.dev errors (triggers fallback to direct provider)",
    ["provider", "error_type"],
)


def track_butter_cache_request(
    provider: str,
    model: str,
    cache_result: str,
    latency_seconds: float | None = None,
    savings_usd: float | None = None,
):
    """
    Track a Butter.dev cache request in Prometheus metrics.

    Args:
        provider: Provider slug (e.g., 'openrouter', 'fireworks')
        model: Model name
        cache_result: One of 'hit', 'miss', 'error', 'skipped'
        latency_seconds: Request latency (optional)
        savings_usd: Cost saved from cache hit (optional)
    """
    # Increment request counter
    butter_cache_requests.labels(
        provider=provider,
        model=model,
        cache_result=cache_result,
    ).inc()

    # Record latency if provided
    if latency_seconds is not None:
        butter_cache_latency.labels(
            provider=provider,
            cache_result=cache_result,
        ).observe(latency_seconds)

    # Track savings for cache hits
    if cache_result == "hit" and savings_usd is not None and savings_usd > 0:
        butter_cache_savings_usd.labels(
            provider=provider,
            model=model,
        ).inc(savings_usd)


def track_butter_cache_error(provider: str, error_type: str):
    """
    Track a Butter.dev error (e.g., timeout, connection error).

    Args:
        provider: Provider slug
        error_type: Type of error (e.g., 'timeout', 'connection_error', 'api_error')
    """
    butter_cache_errors.labels(
        provider=provider,
        error_type=error_type,
    ).inc()


cache_size = get_or_create_metric(
    Gauge,
    "cache_size_bytes",
    "Cache size in bytes",
    ["cache_name"],
)

# ==================== Rate Limiting Metrics ====================
rate_limited_requests = get_or_create_metric(
    Counter,
    "rate_limited_requests_total",
    "Total rate-limited requests",
    ["limit_type"],
)

current_rate_limit = get_or_create_metric(
    Gauge,
    "current_rate_limit",
    "Current rate limit status",
    ["limit_type"],
)

# ==================== Velocity Mode Metrics ====================
# Metrics for tracking velocity mode activations and system protection
velocity_mode_active = get_or_create_metric(
    Gauge,
    "velocity_mode_active",
    "Velocity mode status (1=active, 0=inactive)",
)

velocity_mode_activations_total = get_or_create_metric(
    Counter,
    "velocity_mode_activations_total",
    "Total velocity mode activations",
)

velocity_mode_duration_seconds = get_or_create_metric(
    Histogram,
    "velocity_mode_duration_seconds",
    "Duration of velocity mode activations in seconds",
    buckets=(10, 30, 60, 120, 180, 300, 600),
)

velocity_mode_error_rate = get_or_create_metric(
    Gauge,
    "velocity_mode_error_rate",
    "Error rate that triggered velocity mode (0-1)",
)

velocity_mode_trigger_request_count = get_or_create_metric(
    Gauge,
    "velocity_mode_trigger_request_count",
    "Number of requests in window when velocity mode triggered",
)

velocity_mode_trigger_error_count = get_or_create_metric(
    Gauge,
    "velocity_mode_trigger_error_count",
    "Number of errors in window when velocity mode triggered",
)

# ==================== Provider Health Metrics ====================
provider_availability = get_or_create_metric(
    Gauge,
    "provider_availability",
    "Provider availability status (1=available, 0=unavailable)",
    ["provider"],
)

provider_error_rate = get_or_create_metric(
    Gauge,
    "provider_error_rate",
    "Provider error rate (0-1)",
    ["provider"],
)

provider_response_time = get_or_create_metric(
    Histogram,
    "provider_response_time_seconds",
    "Provider response time in seconds",
    ["provider"],
    buckets=(0.1, 0.5, 1, 2.5, 5, 10),
)

# ==================== Provider Response Duration Metrics (Detailed Timing) ====================
# Fine-grained provider response timing with focus on slow requests (30-60s range)
provider_response_duration = get_or_create_metric(
    Histogram,
    "provider_response_duration_seconds",
    "Provider response duration in seconds (detailed buckets for slow request detection)",
    ["provider", "model", "status"],  # status: success, error
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 60, 90, 120),
)

provider_slow_requests_total = get_or_create_metric(
    Counter,
    "provider_slow_requests_total",
    "Total slow provider requests (>30s) by severity level",
    ["provider", "model", "severity"],  # severity: slow (30-45s), very_slow (>45s)
)

# ==================== Zero-Model Event Metrics ====================
# These metrics track when gateways/providers return zero models
# Critical for monitoring provider health and fallback activation

gateway_zero_model_events = get_or_create_metric(
    Counter,
    "gateway_zero_model_events_total",
    "Total zero-model events by gateway (when API returns 0 models)",
    ["gateway", "reason"],  # reason: api_empty, cache_empty, below_threshold, timeout
)

gateway_model_count = get_or_create_metric(
    Gauge,
    "gateway_model_count",
    "Current number of models available per gateway",
    ["gateway"],
)

gateway_fallback_activations = get_or_create_metric(
    Counter,
    "gateway_fallback_activations_total",
    "Total fallback activations when primary gateway returns zero models",
    ["primary_gateway", "fallback_source"],  # fallback_source: database, cache, static
)

gateway_recovery_events = get_or_create_metric(
    Counter,
    "gateway_recovery_events_total",
    "Total recovery events when gateway returns models after zero-model state",
    ["gateway"],
)

gateway_health_check_duration = get_or_create_metric(
    Histogram,
    "gateway_health_check_duration_seconds",
    "Duration of gateway health checks",
    ["gateway", "status"],  # status: healthy, unhealthy, timeout
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30),
)

gateway_auto_fix_attempts = get_or_create_metric(
    Counter,
    "gateway_auto_fix_attempts_total",
    "Total auto-fix attempts for failing gateways",
    ["gateway", "result"],  # result: success, failure
)

# ==================== API Key Tracking Metrics ====================
api_key_lookup_attempts = get_or_create_metric(
    Counter,
    "api_key_lookup_attempts_total",
    "Total API key lookup attempts",
    ["status"],  # success, failed, retry
)

api_key_tracking_success = get_or_create_metric(
    Counter,
    "api_key_tracking_success_total",
    "Chat requests with successfully tracked API key",
    ["request_type"],  # authenticated, anonymous
)

api_key_tracking_failures = get_or_create_metric(
    Counter,
    "api_key_tracking_failures_total",
    "Chat requests with failed API key tracking",
    ["reason"],  # lookup_failed, not_found, anonymous
)

api_key_tracking_rate = get_or_create_metric(
    Gauge,
    "api_key_tracking_rate",
    "Current API key tracking success rate (0-1)",
)


# ==================== Authentication & API Key Metrics ====================
api_key_usage = get_or_create_metric(
    Counter,
    "api_key_usage_total",
    "Total API key usage",
    ["status"],
)

active_api_keys = get_or_create_metric(
    Gauge,
    "active_api_keys",
    "Number of active API keys",
    ["status"],
)

# ==================== Business Metrics ====================
# Free model usage tracking for expired trials
free_model_usage = get_or_create_metric(
    Counter,
    "free_model_usage_total",
    "Total free model requests by user status (expired_trial, active_trial, paid)",
    ["user_status", "model"],
)

user_credit_balance = get_or_create_metric(
    Gauge,
    "user_credit_balance",
    "Total user credit balance aggregated by plan type",
    ["plan_type"],
)

trial_status = get_or_create_metric(
    Gauge,
    "trial_active",
    "Active trials count",
    ["status"],
)

subscription_count = get_or_create_metric(
    Gauge,
    "subscription_count",
    "Active subscriptions",
    ["plan_type", "billing_cycle"],
)

# ==================== System Metrics ====================
active_connections = get_or_create_metric(
    Gauge,
    "active_connections",
    "Number of active connections",
    ["connection_type"],
)

queue_size = get_or_create_metric(
    Gauge,
    "queue_size",
    "Queue size for prioritization",
    ["queue_name"],
)

# ==================== Redis INFO Metrics ====================
# Scraped from Redis INFO on every Prometheus /metrics request.
# Metric names match the standard redis_exporter convention so
# the Grafana Redis-Cache dashboard queries work out of the box.
#
# These are all Gauges (not Counters) because we read absolute values
# from Redis INFO. PromQL rate() works on monotonically-increasing
# Gauges the same way it works on Counters.

redis_up = get_or_create_metric(
    Gauge,
    "redis_up",
    "Whether Redis is reachable (1=UP, 0=DOWN)",
)

redis_memory_used_bytes = get_or_create_metric(
    Gauge,
    "redis_memory_used_bytes",
    "Total bytes allocated by Redis",
)

redis_memory_max_bytes = get_or_create_metric(
    Gauge,
    "redis_memory_max_bytes",
    "Maximum memory configured for Redis (maxmemory)",
)

redis_connected_clients = get_or_create_metric(
    Gauge,
    "redis_connected_clients",
    "Number of connected client connections",
)

redis_uptime_in_seconds = get_or_create_metric(
    Gauge,
    "redis_uptime_in_seconds",
    "Redis server uptime in seconds",
)

redis_commands_processed_total = get_or_create_metric(
    Gauge,
    "redis_commands_processed_total",
    "Total number of commands processed by Redis",
)

redis_keyspace_hits_total = get_or_create_metric(
    Gauge,
    "redis_keyspace_hits_total",
    "Total number of successful key lookups",
)

redis_keyspace_misses_total = get_or_create_metric(
    Gauge,
    "redis_keyspace_misses_total",
    "Total number of failed key lookups",
)

redis_expired_keys_total = get_or_create_metric(
    Gauge,
    "redis_expired_keys_total",
    "Total number of keys expired by TTL",
)

redis_evicted_keys_total = get_or_create_metric(
    Gauge,
    "redis_evicted_keys_total",
    "Total number of keys evicted due to maxmemory policy",
)

redis_total_connections_received_total = get_or_create_metric(
    Gauge,
    "redis_total_connections_received_total",
    "Total number of connections accepted by Redis",
)

redis_db_keys = get_or_create_metric(
    Gauge,
    "redis_db_keys",
    "Number of keys in a Redis database",
    ["db"],
)

# ==================== Performance Stage Metrics ====================
# Detailed stage breakdown metrics for performance profiling
backend_ttfb_seconds = get_or_create_metric(
    Histogram,
    "backend_ttfb_seconds",
    "Backend API time to first byte (TTFB) in seconds",
    ["provider", "model", "endpoint"],
    buckets=(0.1, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 5.0, 10.0),
)

streaming_duration_seconds = get_or_create_metric(
    Histogram,
    "streaming_duration_seconds",
    "Time spent streaming response to client in seconds",
    ["provider", "model", "endpoint"],
    buckets=(0.1, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 5.0, 10.0),
)

# TTFC (Time to First Chunk) - Critical metric for perceived streaming latency
# Measures time from stream_generator() entry to first SSE chunk yielded
time_to_first_chunk_seconds = get_or_create_metric(
    Histogram,
    "time_to_first_chunk_seconds",
    "Time from stream start to first SSE chunk sent to client (TTFC)",
    ["provider", "model"],
    buckets=(0.1, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 10.0, 15.0),
)

frontend_processing_seconds = get_or_create_metric(
    Histogram,
    "frontend_processing_seconds",
    "Frontend processing time (request parsing, auth, preparation) in seconds",
    ["endpoint"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5),
)

request_stage_duration_seconds = get_or_create_metric(
    Histogram,
    "request_stage_duration_seconds",
    "Duration of specific request processing stages in seconds",
    ["stage", "endpoint"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0),
)

# Stage breakdown percentages
stage_percentage = get_or_create_metric(
    Gauge,
    "stage_percentage",
    "Percentage of total request time spent in each stage",
    ["stage", "endpoint"],
)

# ==================== Context Managers & Helpers ====================


@contextmanager
def track_http_request(method: str, endpoint: str):
    """Context manager to track HTTP request metrics."""
    start_time = time.time()
    try:
        yield
    finally:
        duration = time.time() - start_time
        http_request_duration.labels(method=method, endpoint=endpoint).observe(duration)


def record_http_response(method: str, endpoint: str, status_code: int, app_name: str | None = None):
    """Record HTTP response metrics."""
    # Use provided app_name or fall back to environment variable
    app = app_name or APP_NAME

    # Derive a human-readable status class to distinguish success vs client vs server errors
    if 200 <= status_code < 300:
        status_class = "2xx"
    elif 400 <= status_code < 500:
        status_class = "4xx"
    elif 500 <= status_code < 600:
        status_class = "5xx"
    else:
        status_class = "other"

    # Record in new Grafana-compatible metrics
    fastapi_requests_total.labels(
        app_name=app,
        method=method,
        path=endpoint,
        status_code=status_code,
        status_class=status_class,
    ).inc()

    # Also record in legacy metrics for backward compatibility
    http_request_count.labels(
        method=method,
        endpoint=endpoint,
        status_code=status_code,
        status_class=status_class,
    ).inc()


@contextmanager
def track_model_inference(provider: str, model: str):
    """Context manager to track model inference metrics."""
    start_time = time.time()
    status = "success"
    try:
        yield
    except Exception:  # Intentionally catch all exceptions from yield block
        status = "error"
        logger.debug(f"Model inference completed with status: {status} for {provider}/{model}")
    finally:
        duration = time.time() - start_time
        exemplar = get_trace_exemplar()
        model_inference_duration.labels(provider=provider, model=model).observe(
            duration, exemplar=exemplar
        )
        model_inference_requests.labels(provider=provider, model=model, status=status).inc(
            1, exemplar=exemplar
        )


def record_tokens_used(provider: str, model: str, input_tokens: int, output_tokens: int):
    """Record token consumption metrics."""
    exemplar = get_trace_exemplar()
    tokens_used.labels(provider=provider, model=model, token_type="input").inc(
        input_tokens, exemplar=exemplar
    )
    tokens_used.labels(provider=provider, model=model, token_type="output").inc(
        output_tokens, exemplar=exemplar
    )


def record_credits_used(provider: str, model: str, user_id: str, credits: float):
    """Record credit consumption metrics."""
    # Note: user_id parameter kept for backwards compatibility but not used in labels
    # (avoid exposing PII in metric labels)
    credits_used.labels(provider=provider, model=model).inc(credits)


@contextmanager
def track_database_query(table: str, operation: str, query_description: str | None = None):
    """
    Context manager to track database query metrics with both Prometheus and Sentry.

    This creates:
    - Prometheus metrics for database query count and duration
    - Sentry spans for Queries Insights (https://docs.sentry.io/product/insights/backend/queries/)

    Args:
        table: Database table name
        operation: Operation type (select, insert, update, delete)
        query_description: Optional parameterized query string for Sentry Insights
    """
    # Import Sentry insights utilities (isolated try/except to avoid double yield)
    trace_supabase_query = None
    try:
        from src.utils.sentry_insights import trace_supabase_query as _trace_supabase_query

        trace_supabase_query = _trace_supabase_query
    except ImportError:
        pass  # Sentry insights not available

    start_time = time.time()

    if trace_supabase_query:
        # Build query description if not provided
        query_desc = query_description or f"{operation.upper()} FROM {table}"

        with trace_supabase_query(table, operation, query_description=query_desc):
            try:
                yield
            finally:
                duration = time.time() - start_time
                database_query_count.labels(table=table, operation=operation).inc()
                database_query_duration.labels(table=table).observe(duration)
    else:
        # Sentry insights not available, fall back to Prometheus only
        try:
            yield
        finally:
            duration = time.time() - start_time
            database_query_count.labels(table=table, operation=operation).inc()
            database_query_duration.labels(table=table).observe(duration)


def record_cache_hit(cache_name: str, key: str | None = None, item_size: int | None = None):
    """
    Record cache hit metric with Sentry Cache Insights.

    Args:
        cache_name: Name of the cache (e.g., "response_cache", "model_catalog")
        key: Optional cache key for Sentry Insights
        item_size: Optional size of the cached item in bytes
    """
    cache_hits.labels(cache_name=cache_name).inc()

    # Record in Sentry Cache Insights
    if key:
        try:
            from src.utils.sentry_insights import trace_cache_operation

            with trace_cache_operation(
                "cache.get",
                key,
                cache_hit=True,
                item_size=item_size,
                cache_system="redis" if "redis" in cache_name.lower() else "memory",
            ):
                pass  # Span is just for recording the hit
        except ImportError:
            pass  # Sentry insights not available


def record_cache_miss(cache_name: str, key: str | None = None):
    """
    Record cache miss metric with Sentry Cache Insights.

    Args:
        cache_name: Name of the cache (e.g., "response_cache", "model_catalog")
        key: Optional cache key for Sentry Insights
    """
    cache_misses.labels(cache_name=cache_name).inc()

    # Record in Sentry Cache Insights
    if key:
        try:
            from src.utils.sentry_insights import trace_cache_operation

            with trace_cache_operation(
                "cache.get",
                key,
                cache_hit=False,
                cache_system="redis" if "redis" in cache_name.lower() else "memory",
            ):
                pass  # Span is just for recording the miss
        except ImportError:
            pass  # Sentry insights not available


def record_cache_set(
    cache_name: str, key: str, item_size: int | None = None, ttl: int | None = None
):
    """
    Record cache set operation with Sentry Cache Insights.

    Args:
        cache_name: Name of the cache
        key: Cache key being set
        item_size: Size of the cached item in bytes
        ttl: Time-to-live in seconds
    """
    try:
        from src.utils.sentry_insights import trace_cache_operation

        with trace_cache_operation(
            "cache.put",
            key,
            item_size=item_size,
            ttl=ttl,
            cache_system="redis" if "redis" in cache_name.lower() else "memory",
        ):
            pass  # Span is just for recording the set
    except ImportError:
        pass  # Sentry insights not available


def record_cache_remove(cache_name: str, key: str):
    """
    Record cache remove operation with Sentry Cache Insights.

    Args:
        cache_name: Name of the cache
        key: Cache key being removed
    """
    try:
        from src.utils.sentry_insights import trace_cache_operation

        with trace_cache_operation(
            "cache.remove",
            key,
            cache_system="redis" if "redis" in cache_name.lower() else "memory",
        ):
            pass  # Span is just for recording the remove
    except ImportError:
        pass  # Sentry insights not available


def set_cache_size(cache_name: str, size_bytes: int):
    """Set cache size metric."""
    cache_size.labels(cache_name=cache_name).set(size_bytes)


def record_rate_limited_request(api_key: str, limit_type: str):
    """Record rate-limited request metric."""
    # Note: api_key parameter kept for backwards compatibility but not used in labels
    # (avoid exposing PII in metric labels)
    rate_limited_requests.labels(limit_type=limit_type).inc()


# ==================== Velocity Mode Helper Functions ====================


def set_velocity_mode_active(active: bool):
    """Set velocity mode active status.

    Args:
        active: True if velocity mode is active, False otherwise
    """
    velocity_mode_active.set(1 if active else 0)


def record_velocity_mode_activation(error_rate: float, total_requests: int, error_count: int):
    """Record a velocity mode activation event.

    Args:
        error_rate: Error rate that triggered activation (0-1)
        total_requests: Total requests in the window
        error_count: Number of errors in the window
    """
    velocity_mode_activations_total.inc()
    velocity_mode_error_rate.set(error_rate)
    velocity_mode_trigger_request_count.set(total_requests)
    velocity_mode_trigger_error_count.set(error_count)
    set_velocity_mode_active(True)


def record_velocity_mode_deactivation(duration_seconds: float):
    """Record velocity mode deactivation and duration.

    Args:
        duration_seconds: How long velocity mode was active
    """
    velocity_mode_duration_seconds.observe(duration_seconds)
    set_velocity_mode_active(False)
    # Reset trigger metrics
    velocity_mode_error_rate.set(0)
    velocity_mode_trigger_request_count.set(0)
    velocity_mode_trigger_error_count.set(0)


def set_provider_availability(provider: str, available: bool):
    """Set provider availability metric."""
    provider_availability.labels(provider=provider).set(1 if available else 0)


def set_provider_error_rate(provider: str, error_rate: float):
    """Set provider error rate metric."""
    provider_error_rate.labels(provider=provider).set(min(1.0, max(0.0, error_rate)))


def track_provider_response_time(provider: str, duration: float):
    """Track provider response time."""
    provider_response_time.labels(provider=provider).observe(duration)


# ==================== Zero-Model Event Helper Functions ====================


def record_zero_model_event(gateway: str, reason: str = "api_empty"):
    """
    Record a zero-model event for a gateway.

    Args:
        gateway: Gateway/provider name (e.g., "openrouter", "featherless")
        reason: Reason for zero models:
            - "api_empty": API returned empty response
            - "cache_empty": Cache was empty/expired
            - "below_threshold": Model count below minimum threshold
            - "timeout": Request timed out
            - "error": Other error occurred
    """
    gateway_zero_model_events.labels(gateway=gateway, reason=reason).inc()


def set_gateway_model_count(gateway: str, count: int):
    """
    Set the current model count for a gateway.

    Args:
        gateway: Gateway/provider name
        count: Number of models currently available
    """
    gateway_model_count.labels(gateway=gateway).set(count)


def record_fallback_activation(primary_gateway: str, fallback_source: str = "database"):
    """
    Record a fallback activation event.

    Args:
        primary_gateway: Gateway that triggered the fallback
        fallback_source: Source of fallback models:
            - "database": Models from database
            - "cache": Models from stale cache
            - "static": Static fallback model list
    """
    gateway_fallback_activations.labels(
        primary_gateway=primary_gateway, fallback_source=fallback_source
    ).inc()


def record_gateway_recovery(gateway: str):
    """
    Record a recovery event when a gateway returns models after being in zero-model state.

    Args:
        gateway: Gateway/provider name that recovered
    """
    gateway_recovery_events.labels(gateway=gateway).inc()


def track_gateway_health_check(gateway: str, status: str, duration: float):
    """
    Track gateway health check duration and status.

    Args:
        gateway: Gateway/provider name
        status: Health check result ("healthy", "unhealthy", "timeout")
        duration: Duration of the health check in seconds
    """
    gateway_health_check_duration.labels(gateway=gateway, status=status).observe(duration)


def record_auto_fix_attempt(gateway: str, success: bool):
    """
    Record an auto-fix attempt for a failing gateway.

    Args:
        gateway: Gateway/provider name
        success: Whether the auto-fix was successful
    """
    result = "success" if success else "failure"
    gateway_auto_fix_attempts.labels(gateway=gateway, result=result).inc()


def record_api_key_usage(api_key_id: str, status: str = "success"):
    """Record API key usage."""
    # Note: api_key_id parameter kept for backwards compatibility but not used in labels
    # (avoid exposing PII in metric labels)
    api_key_usage.labels(status=status).inc()


def set_active_api_keys(status: str, count: int):
    """Set active API keys count."""
    active_api_keys.labels(status=status).set(count)


def set_user_credit_balance(user_id: str, plan_type: str, balance: float):
    """Set total user credit balance aggregated by plan type."""
    # Note: user_id parameter kept for backwards compatibility but not used in labels
    # This aggregates total credit balance by plan type (avoid exposing PII in metric labels)
    user_credit_balance.labels(plan_type=plan_type).set(balance)


def set_trial_count(status: str, count: int):
    """Set trial count by status."""
    trial_status.labels(status=status).set(count)


def record_free_model_usage(user_status: str, model: str):
    """Record free model usage by user status.

    Args:
        user_status: One of "expired_trial", "active_trial", "paid", "anonymous"
        model: The model identifier (e.g., "google/gemini-2.0-flash-exp:free")
    """
    free_model_usage.labels(user_status=user_status, model=model).inc()


def set_subscription_count(plan_type: str, billing_cycle: str, count: int):
    """Set subscription count."""
    subscription_count.labels(plan_type=plan_type, billing_cycle=billing_cycle).set(count)


def set_active_connections(connection_type: str, count: int):
    """Set active connections count."""
    active_connections.labels(connection_type=connection_type).set(count)


def set_queue_size(queue_name: str, size: int):
    """Set queue size."""
    queue_size.labels(queue_name=queue_name).set(size)


# ==================== Queue Monitoring Functions ====================
# These functions integrate with Sentry Queue Monitoring
# (https://docs.sentry.io/product/insights/backend/queue-monitoring/)


@contextmanager
def track_queue_publish(
    destination: str,
    message_id: str | None = None,
    message_body_size: int | None = None,
    messaging_system: str = "custom",
):
    """
    Context manager to track queue publish operations with Sentry Queue Monitoring.

    This creates Sentry spans for producer operations and returns trace headers
    that should be included in the message for distributed tracing.

    Args:
        destination: Queue or topic name
        message_id: Unique message identifier
        message_body_size: Size of the message body in bytes
        messaging_system: Messaging system name (kafka, redis, aws_sqs, etc.)

    Yields:
        dict: Trace headers to include in the message for distributed tracing

    Example:
        with track_queue_publish("notifications", message_id="123") as headers:
            await queue.publish({
                "data": payload,
                "headers": headers  # Include for trace continuity
            })
    """
    # Import Sentry insights utilities (isolated try/except to avoid double yield)
    _trace_queue_publish = None
    try:
        from src.utils.sentry_insights import trace_queue_publish as _tqp

        _trace_queue_publish = _tqp
    except ImportError:
        pass  # Sentry insights not available

    if _trace_queue_publish:
        with _trace_queue_publish(
            destination,
            message_id=message_id,
            message_body_size=message_body_size,
            messaging_system=messaging_system,
        ) as (span, headers):
            yield headers
    else:
        # Sentry insights not available
        yield {}


@contextmanager
def track_queue_process(
    destination: str,
    message_id: str | None = None,
    message_body_size: int | None = None,
    retry_count: int | None = None,
    receive_latency_ms: float | None = None,
    messaging_system: str = "custom",
    trace_headers: dict[str, str] | None = None,
):
    """
    Context manager to track queue process (consume) operations with Sentry Queue Monitoring.

    This creates Sentry spans for consumer operations. If trace headers are provided
    from the producer, it will create a linked span for distributed tracing.

    Args:
        destination: Queue or topic name
        message_id: Unique message identifier
        message_body_size: Size of the message body in bytes
        retry_count: Number of processing attempts
        receive_latency_ms: Milliseconds between publishing and consumer receipt
        messaging_system: Messaging system name (kafka, redis, aws_sqs, etc.)
        trace_headers: Dict with sentry-trace and baggage headers from producer

    Yields:
        Sentry span object (or None if Sentry not available)

    Example:
        # Extract headers from message
        headers = message.get("headers", {})

        with track_queue_process(
            "notifications",
            message_id=message.get("id"),
            trace_headers=headers
        ) as span:
            await process_notification(message)
    """
    # Import Sentry insights utilities (isolated try/except to avoid double yield)
    _trace_queue_process = None
    try:
        from src.utils.sentry_insights import trace_queue_process as _tqp

        _trace_queue_process = _tqp
    except ImportError:
        pass  # Sentry insights not available

    if _trace_queue_process:
        with _trace_queue_process(
            destination,
            message_id=message_id,
            message_body_size=message_body_size,
            retry_count=retry_count,
            receive_latency_ms=receive_latency_ms,
            messaging_system=messaging_system,
            trace_headers=trace_headers,
        ) as span:
            yield span
    else:
        # Sentry insights not available
        yield None


# ==================== Performance Stage Tracking Functions ====================


def track_backend_ttfb(provider: str, model: str, endpoint: str, duration: float):
    """Track backend API time to first byte (TTFB)."""
    backend_ttfb_seconds.labels(provider=provider, model=model, endpoint=endpoint).observe(duration)


def track_streaming_duration(provider: str, model: str, endpoint: str, duration: float):
    """Track streaming response duration."""
    streaming_duration_seconds.labels(provider=provider, model=model, endpoint=endpoint).observe(
        duration
    )


def track_time_to_first_chunk(provider: str, model: str, ttfc: float):
    """Track Time to First Chunk (TTFC) - critical for perceived streaming latency.

    This metric measures the time from when stream_generator() starts iterating
    to when the first SSE chunk is yielded to the client. High TTFC values
    indicate the AI provider is slow to start generating tokens.

    Args:
        provider: The AI provider name (e.g., "openrouter", "fireworks")
        model: The model identifier
        ttfc: Time to first chunk in seconds
    """
    time_to_first_chunk_seconds.labels(provider=provider, model=model).observe(ttfc)


def track_frontend_processing(endpoint: str, duration: float):
    """Track frontend processing time (parsing, auth, preparation)."""
    frontend_processing_seconds.labels(endpoint=endpoint).observe(duration)


def track_request_stage(stage: str, endpoint: str, duration: float):
    """Track duration of a specific request processing stage.

    Stages:
    - request_parsing: Time to parse and validate request
    - auth_validation: Time to validate authentication
    - request_preparation: Time to prepare request for backend
    - backend_fetch: Time waiting for backend API response (TTFB)
    - stream_processing: Time spent streaming response to client
    """
    request_stage_duration_seconds.labels(stage=stage, endpoint=endpoint).observe(duration)


def record_stage_percentage(stage: str, endpoint: str, percentage: float):
    """Record percentage of total request time spent in a stage."""
    stage_percentage.labels(stage=stage, endpoint=endpoint).set(percentage)


def track_connection_pool_stats(pool_name: str, stats: dict):
    """Track connection pool statistics.

    Args:
        pool_name: Name of the connection pool (e.g., "supabase", "redis", "provider_http")
        stats: Dictionary containing pool statistics:
            - total_connections: Total number of connections
            - active_connections: Number of active connections
            - idle_connections: Number of idle connections
            - max_pool_size: Maximum pool size
            - connection_errors: Number of connection errors
            - connection_timeouts: Number of connection timeouts
    """
    try:
        total = stats.get("total_connections", 0)
        active = stats.get("active_connections", 0)
        idle = stats.get("idle_connections", 0)

        connection_pool_size.labels(pool_name=pool_name).set(total)
        connection_pool_active.labels(pool_name=pool_name).set(active)
        connection_pool_idle.labels(pool_name=pool_name).set(idle)

        # Calculate utilization ratio
        max_size = stats.get("max_pool_size", 0)
        if max_size > 0:
            utilization = active / max_size
            connection_pool_utilization.labels(pool_name=pool_name).set(utilization)

        # Track errors
        errors = stats.get("connection_errors", 0)
        timeouts = stats.get("connection_timeouts", 0)
        if errors > 0:
            connection_pool_errors.labels(pool_name=pool_name, error_type="error").inc(errors)
        if timeouts > 0:
            connection_pool_errors.labels(pool_name=pool_name, error_type="timeout").inc(timeouts)
    except Exception as e:
        logger.warning(f"Failed to track connection pool stats: {e}")


# ==================== Pricing Sync Metrics ====================
# Metrics for monitoring pricing sync scheduler operations

pricing_sync_duration_seconds = get_or_create_metric(
    Histogram,
    "pricing_sync_duration_seconds",
    "Duration of pricing sync operations by provider",
    ["provider", "status"],  # status: success, failed
    buckets=(1, 5, 10, 20, 30, 45, 60, 90, 120, 180),
)

pricing_sync_total = get_or_create_metric(
    Counter,
    "pricing_sync_total",
    "Total number of pricing sync operations",
    ["provider", "status", "triggered_by"],  # triggered_by: manual, scheduler, api
)

pricing_sync_models_updated_total = get_or_create_metric(
    Counter,
    "pricing_sync_models_updated_total",
    "Total number of models with updated pricing",
    ["provider"],
)

pricing_sync_models_skipped_total = get_or_create_metric(
    Counter,
    "pricing_sync_models_skipped_total",
    "Total number of models skipped during sync",
    ["provider", "reason"],  # reason: zero_pricing, dynamic_pricing, unchanged, error
)

pricing_sync_errors_total = get_or_create_metric(
    Counter,
    "pricing_sync_errors_total",
    "Total number of pricing sync errors",
    ["provider", "error_type"],  # error_type: api_fetch_failed, db_error, validation_error
)

pricing_sync_last_success_timestamp = get_or_create_metric(
    Gauge,
    "pricing_sync_last_success_timestamp",
    "Unix timestamp of last successful pricing sync",
    ["provider"],
)

pricing_sync_job_duration_seconds = get_or_create_metric(
    Histogram,
    "pricing_sync_job_duration_seconds",
    "Duration of background pricing sync jobs",
    ["status"],  # status: completed, failed
    buckets=(1, 5, 10, 20, 30, 45, 60, 90, 120, 180, 300),
)

pricing_sync_job_queue_size = get_or_create_metric(
    Gauge,
    "pricing_sync_job_queue_size",
    "Current number of pricing sync jobs in queue",
    ["status"],  # status: queued, running, completed, failed
)

pricing_sync_models_fetched_total = get_or_create_metric(
    Counter,
    "pricing_sync_models_fetched_total",
    "Total number of models fetched from provider APIs",
    ["provider"],
)

pricing_sync_price_changes_total = get_or_create_metric(
    Counter,
    "pricing_sync_price_changes_total",
    "Total number of detected price changes",
    ["provider"],
)

# Pricing validation metrics
pricing_validation_total = get_or_create_metric(
    Counter,
    "pricing_validation_total",
    "Total number of pricing validations performed",
    ["model"],
)

pricing_validation_failures = get_or_create_metric(
    Counter,
    "pricing_validation_failures",
    "Total number of pricing validation failures",
    ["model", "reason"],
)

pricing_spike_detected_total = get_or_create_metric(
    Counter,
    "pricing_spike_detected_total",
    "Total number of pricing spikes detected",
    ["model", "price_type"],
)

pricing_bounds_violations_total = get_or_create_metric(
    Counter,
    "pricing_bounds_violations_total",
    "Total number of pricing bounds violations",
    ["model", "violation_type"],
)

# Pricing health monitoring metrics
pricing_staleness_hours = get_or_create_metric(
    Gauge,
    "pricing_staleness_hours",
    "Hours since last pricing update",
    [],
)

models_using_default_pricing = get_or_create_metric(
    Gauge,
    "models_using_default_pricing",
    "Number of models currently using default pricing",
    [],
)

pricing_health_status = get_or_create_metric(
    Gauge,
    "pricing_health_status",
    "Overall pricing system health status (0=unknown, 1=healthy, 2=warning, 3=critical)",
    [],
)


# ==================== Helper Functions for Pricing Sync ====================


@contextmanager
def track_pricing_sync(provider: str, triggered_by: str = "scheduler"):
    """
    Context manager to track pricing sync operations.

    Usage:
        with track_pricing_sync("openrouter", triggered_by="manual"):
            # Perform sync
            result = sync_pricing()
    """
    start_time = time.time()
    status = "success"
    try:
        yield
    except Exception:
        status = "failed"
        raise
    finally:
        duration = time.time() - start_time
        pricing_sync_duration_seconds.labels(provider=provider, status=status).observe(duration)
        pricing_sync_total.labels(provider=provider, status=status, triggered_by=triggered_by).inc()

        if status == "success":
            pricing_sync_last_success_timestamp.labels(provider=provider).set(time.time())


def record_pricing_sync_models_updated(provider: str, count: int):
    """Record number of models updated during sync."""
    if count > 0:
        pricing_sync_models_updated_total.labels(provider=provider).inc(count)


def record_pricing_sync_models_skipped(provider: str, reason: str, count: int):
    """Record number of models skipped during sync."""
    if count > 0:
        pricing_sync_models_skipped_total.labels(provider=provider, reason=reason).inc(count)


def record_pricing_sync_error(provider: str, error_type: str):
    """Record pricing sync error."""
    pricing_sync_errors_total.labels(provider=provider, error_type=error_type).inc()


def record_pricing_sync_models_fetched(provider: str, count: int):
    """Record number of models fetched from provider API."""
    if count > 0:
        pricing_sync_models_fetched_total.labels(provider=provider).inc(count)


def record_pricing_sync_price_changes(provider: str, count: int):
    """Record number of price changes detected."""
    if count > 0:
        pricing_sync_price_changes_total.labels(provider=provider).inc(count)


def set_pricing_sync_job_queue_size(status: str, count: int):
    """Set current job queue size."""
    pricing_sync_job_queue_size.labels(status=status).set(count)


def track_pricing_sync_job(duration: float, status: str):
    """Track pricing sync background job duration."""
    pricing_sync_job_duration_seconds.labels(status=status).observe(duration)


# ==================== Code Router Metrics ====================
# Metrics for the code-optimized prompt router

code_router_requests_total = get_or_create_metric(
    Counter,
    "code_router_requests_total",
    "Total code routing requests",
    ["task_category", "complexity", "mode", "selected_model", "selected_tier"],
)

code_router_latency_seconds = get_or_create_metric(
    Histogram,
    "code_router_latency_seconds",
    "Code router decision latency in seconds (target: <2ms)",
    buckets=(0.0005, 0.001, 0.002, 0.005, 0.01, 0.025, 0.05, 0.1),
)

code_task_success_total = get_or_create_metric(
    Counter,
    "code_task_success_total",
    "Successful code task completions",
    ["task_category", "model", "tier"],
)

code_task_retry_total = get_or_create_metric(
    Counter,
    "code_task_retry_total",
    "Code tasks requiring retry/regeneration",
    ["task_category", "model", "tier", "retry_reason"],
)

code_router_savings_dollars = get_or_create_metric(
    Counter,
    "code_router_savings_dollars_total",
    "Dollars saved by code routing optimization",
    ["baseline", "task_category"],
)

code_router_tier_distribution = get_or_create_metric(
    Counter,
    "code_router_tier_distribution_total",
    "Distribution of tier selections by task category",
    ["task_category", "tier", "mode"],
)

code_router_quality_gate_triggered = get_or_create_metric(
    Counter,
    "code_router_quality_gate_triggered_total",
    "Times quality gate prevented tier downgrade",
    ["task_category", "requested_tier", "enforced_tier"],
)

code_router_classification_confidence = get_or_create_metric(
    Histogram,
    "code_router_classification_confidence",
    "Classification confidence distribution",
    ["task_category"],
    buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
)

code_router_fallback_total = get_or_create_metric(
    Counter,
    "code_router_fallback_total",
    "Times fallback model was used",
    ["reason"],
)


# ==================== General Router Metrics ====================

general_router_requests_total = get_or_create_metric(
    Counter,
    "general_router_requests_total",
    "Total general routing requests",
    ["mode", "selected_model", "provider"],
)

general_router_latency_seconds = get_or_create_metric(
    Histogram,
    "general_router_latency_seconds",
    "General router decision latency in seconds",
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2),
)

general_router_notdiamond_calls_total = get_or_create_metric(
    Counter,
    "general_router_notdiamond_calls_total",
    "NotDiamond API calls",
    ["status", "mode"],
)

general_router_notdiamond_latency_seconds = get_or_create_metric(
    Histogram,
    "general_router_notdiamond_latency_seconds",
    "NotDiamond API latency",
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1),
)

general_router_fallback_total = get_or_create_metric(
    Counter,
    "general_router_fallback_total",
    "Fallback model usage",
    ["reason", "mode"],
)

general_router_confidence = get_or_create_metric(
    Histogram,
    "general_router_confidence",
    "NotDiamond confidence scores",
    ["mode"],
    buckets=(0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 1.0),
)


# ==================== Code Router Helper Functions ====================


def track_code_routing_request(
    task_category: str,
    complexity: str,
    mode: str,
    selected_model: str,
    selected_tier: int,
    latency_seconds: float,
    confidence: float,
):
    """
    Track a code routing request in Prometheus metrics.

    Args:
        task_category: Classified task category
        complexity: Classified complexity
        mode: Routing mode used
        selected_model: Selected model ID
        selected_tier: Selected tier number
        latency_seconds: Routing latency in seconds
        confidence: Classification confidence
    """
    code_router_requests_total.labels(
        task_category=task_category,
        complexity=complexity,
        mode=mode,
        selected_model=selected_model,
        selected_tier=str(selected_tier),
    ).inc()

    code_router_latency_seconds.observe(latency_seconds)

    code_router_tier_distribution.labels(
        task_category=task_category,
        tier=str(selected_tier),
        mode=mode,
    ).inc()

    code_router_classification_confidence.labels(
        task_category=task_category,
    ).observe(confidence)


# ==================== General Router Helper Functions ====================


def track_general_router_request(
    mode: str,
    selected_model: str,
    provider: str,
    latency_seconds: float,
    confidence: float,
):
    """
    Track general router request.

    Args:
        mode: Routing mode (balanced, quality, cost, latency)
        selected_model: Selected model ID
        provider: Provider name
        latency_seconds: Routing latency in seconds
        confidence: NotDiamond confidence score
    """
    general_router_requests_total.labels(
        mode=mode,
        selected_model=selected_model,
        provider=provider,
    ).inc()

    general_router_latency_seconds.observe(latency_seconds)

    if confidence > 0:
        general_router_confidence.labels(mode=mode).observe(confidence)


def track_notdiamond_api_call(status: str, mode: str, latency_seconds: float):
    """
    Track NotDiamond API call.

    Args:
        status: Call status (success, error)
        mode: Routing mode
        latency_seconds: API call latency in seconds
    """
    general_router_notdiamond_calls_total.labels(
        status=status,
        mode=mode,
    ).inc()

    general_router_notdiamond_latency_seconds.observe(latency_seconds)


def track_general_router_fallback(reason: str, mode: str):
    """
    Track fallback usage.

    Args:
        reason: Fallback reason (disabled, model_unavailable, exception)
        mode: Routing mode
    """
    general_router_fallback_total.labels(
        reason=reason,
        mode=mode,
    ).inc()


def track_code_task_success(task_category: str, model: str, tier: int):
    """Track successful code task completion."""
    code_task_success_total.labels(
        task_category=task_category,
        model=model,
        tier=str(tier),
    ).inc()


def track_code_task_retry(task_category: str, model: str, tier: int, retry_reason: str):
    """Track code task retry/regeneration."""
    code_task_retry_total.labels(
        task_category=task_category,
        model=model,
        tier=str(tier),
        retry_reason=retry_reason,
    ).inc()


def track_code_router_savings(baseline: str, task_category: str, savings_usd: float):
    """Track cost savings from code routing."""
    if savings_usd > 0:
        code_router_savings_dollars.labels(
            baseline=baseline,
            task_category=task_category,
        ).inc(savings_usd)


def track_quality_gate_triggered(task_category: str, requested_tier: int, enforced_tier: int):
    """Track when quality gate prevents tier downgrade."""
    code_router_quality_gate_triggered.labels(
        task_category=task_category,
        requested_tier=str(requested_tier),
        enforced_tier=str(enforced_tier),
    ).inc()


def track_code_router_fallback(reason: str):
    """Track when fallback model is used."""
    code_router_fallback_total.labels(reason=reason).inc()


def get_metrics_summary() -> dict:
    """Get a summary of key metrics for monitoring."""
    # This function returns a summary of metrics collected.
    # In production, use the /metrics endpoint which exports all metrics in Prometheus format.
    # This summary is for diagnostic purposes only.
    try:
        summary = {
            "enabled": True,
            "metrics_endpoint": "/metrics",
            "message": "Use /metrics endpoint for Prometheus format metrics",
        }
        return summary
    except Exception as e:
        logger.warning(f"Could not retrieve metrics summary: {type(e).__name__}")
        return {"enabled": False}


def collect_redis_info():
    """Scrape Redis INFO and update Prometheus gauges.

    Called automatically before each /metrics response via the
    metrics endpoint in main.py. Uses the existing Redis client
    from redis_config — no extra connections needed.

    Exports all metrics needed by the Grafana Redis-Cache dashboard:
    - Health: redis_up
    - Memory: redis_memory_used_bytes, redis_memory_max_bytes
    - Clients: redis_connected_clients
    - Server: redis_uptime_in_seconds
    - Stats: redis_commands_processed_total, redis_keyspace_hits_total,
             redis_keyspace_misses_total, redis_expired_keys_total,
             redis_evicted_keys_total, redis_total_connections_received_total
    - Keyspace: redis_db_keys (per-database key count)
    """
    try:
        from src.config.redis_config import get_redis_client

        client = get_redis_client()
        if not client:
            redis_up.set(0)
            return

        client.ping()
        redis_up.set(1)

        # Fetch all INFO sections at once
        info = client.info()

        # Memory
        redis_memory_used_bytes.set(info.get("used_memory", 0))
        maxmemory = info.get("maxmemory", 0)
        # Upstash and some configs report maxmemory=0 (unlimited) — use used_memory
        # as a floor so Memory Usage % gauge doesn't divide by zero
        if maxmemory and maxmemory > 0:
            redis_memory_max_bytes.set(maxmemory)
        else:
            # For unlimited configs, report a sentinel so the gauge renders
            # (dashboard handles 0 gracefully)
            redis_memory_max_bytes.set(0)

        # Clients
        redis_connected_clients.set(info.get("connected_clients", 0))

        # Server
        redis_uptime_in_seconds.set(info.get("uptime_in_seconds", 0))

        # Stats (monotonically increasing — PromQL rate() works on these)
        redis_commands_processed_total.set(info.get("total_commands_processed", 0))
        redis_keyspace_hits_total.set(info.get("keyspace_hits", 0))
        redis_keyspace_misses_total.set(info.get("keyspace_misses", 0))
        redis_expired_keys_total.set(info.get("expired_keys", 0))
        redis_evicted_keys_total.set(info.get("evicted_keys", 0))
        redis_total_connections_received_total.set(info.get("total_connections_received", 0))

        # Keyspace — per-database key counts (db0, db1, ...)
        for key, value in info.items():
            if key.startswith("db") and isinstance(value, dict):
                redis_db_keys.labels(db=key).set(value.get("keys", 0))

    except Exception as e:
        redis_up.set(0)
        logger.warning(f"Redis INFO collection failed: {type(e).__name__}: {e}")
