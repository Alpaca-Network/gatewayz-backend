"""
Instrumentation and observability endpoints for Loki and Tempo.

This module provides endpoints for:
- Loki logging status and configuration
- Tempo tracing status and configuration
- Trace ID generation and correlation
- Instrumentation health checks
"""

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.config.config import Config
from src.config.opentelemetry_config import get_current_span_id, get_current_trace_id

router = APIRouter(prefix="/api/instrumentation", tags=["instrumentation"])
logger = logging.getLogger(__name__)

security = HTTPBearer()


def get_admin_key(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Validate admin API key"""
    admin_key = credentials.credentials
    expected_key = os.environ.get("ADMIN_API_KEY")

    if not expected_key:
        raise HTTPException(status_code=401, detail="Admin API key not configured")

    if admin_key != expected_key:
        raise HTTPException(status_code=401, detail="Invalid admin API key")

    return admin_key


@router.get("/health", tags=["instrumentation"])
async def instrumentation_health():
    """
    Get instrumentation health status.

    Returns:
        dict: Status of Loki and Tempo integration
    """
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "loki": {
            "enabled": Config.LOKI_ENABLED,
            "url": Config.LOKI_PUSH_URL if Config.LOKI_ENABLED else None,
            "service_name": Config.OTEL_SERVICE_NAME,
            "environment": Config.APP_ENV,
        },
        "tempo": {
            "enabled": Config.TEMPO_ENABLED,
            "endpoint": Config.TEMPO_OTLP_HTTP_ENDPOINT if Config.TEMPO_ENABLED else None,
            "service_name": Config.OTEL_SERVICE_NAME,
            "environment": Config.APP_ENV,
        },
    }


@router.get("/trace-context", tags=["instrumentation"])
async def get_trace_context():
    """
    Get current trace and span context.

    Returns:
        dict: Current trace ID and span ID for correlation
    """
    trace_id = get_current_trace_id()
    span_id = get_current_span_id()

    return {
        "trace_id": trace_id or "none",
        "span_id": span_id or "none",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/loki/status", tags=["instrumentation"])
async def loki_status(admin_key: str = Depends(get_admin_key)):
    """
    Get Loki logging status and configuration.

    Requires admin API key.

    Returns:
        dict: Loki configuration and status
    """
    return {
        "enabled": Config.LOKI_ENABLED,
        "push_url": Config.LOKI_PUSH_URL if Config.LOKI_ENABLED else None,
        "query_url": Config.LOKI_QUERY_URL if Config.LOKI_ENABLED else None,
        "service_name": Config.OTEL_SERVICE_NAME,
        "environment": Config.APP_ENV,
        "tags": {
            "app": Config.OTEL_SERVICE_NAME,
            "environment": Config.APP_ENV,
            "service": "gatewayz-api",
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/tempo/status", tags=["instrumentation"])
async def tempo_status(admin_key: str = Depends(get_admin_key)):
    """
    Get Tempo tracing status and configuration.

    Requires admin API key.

    Returns:
        dict: Tempo configuration and status
    """
    return {
        "enabled": Config.TEMPO_ENABLED,
        "otlp_http_endpoint": Config.TEMPO_OTLP_HTTP_ENDPOINT if Config.TEMPO_ENABLED else None,
        "otlp_grpc_endpoint": Config.TEMPO_OTLP_GRPC_ENDPOINT if Config.TEMPO_ENABLED else None,
        "service_name": Config.OTEL_SERVICE_NAME,
        "environment": Config.APP_ENV,
        "resource_attributes": {
            "service.name": Config.OTEL_SERVICE_NAME,
            "service.version": "2.0.3",
            "deployment.environment": Config.APP_ENV,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/config", tags=["instrumentation"])
async def instrumentation_config(admin_key: str = Depends(get_admin_key)):
    """
    Get complete instrumentation configuration.

    Requires admin API key.

    Returns:
        dict: Full instrumentation setup details
    """
    return {
        "service": {
            "name": Config.OTEL_SERVICE_NAME,
            "version": "2.0.3",
            "environment": Config.APP_ENV,
        },
        "loki": {
            "enabled": Config.LOKI_ENABLED,
            "push_url": Config.LOKI_PUSH_URL if Config.LOKI_ENABLED else None,
            "query_url": Config.LOKI_QUERY_URL if Config.LOKI_ENABLED else None,
            "labels": {
                "app": Config.OTEL_SERVICE_NAME,
                "environment": Config.APP_ENV,
                "service": "gatewayz-api",
            },
        },
        "tempo": {
            "enabled": Config.TEMPO_ENABLED,
            "otlp_http_endpoint": Config.TEMPO_OTLP_HTTP_ENDPOINT if Config.TEMPO_ENABLED else None,
            "otlp_grpc_endpoint": Config.TEMPO_OTLP_GRPC_ENDPOINT if Config.TEMPO_ENABLED else None,
        },
        "environment_variables": {
            "LOKI_ENABLED": Config.LOKI_ENABLED,
            "LOKI_PUSH_URL": "***" if Config.LOKI_ENABLED else None,
            "LOKI_QUERY_URL": "***" if Config.LOKI_ENABLED else None,
            "TEMPO_ENABLED": Config.TEMPO_ENABLED,
            "TEMPO_OTLP_HTTP_ENDPOINT": "***" if Config.TEMPO_ENABLED else None,
            "TEMPO_OTLP_GRPC_ENDPOINT": "***" if Config.TEMPO_ENABLED else None,
            "OTEL_SERVICE_NAME": Config.OTEL_SERVICE_NAME,
            "APP_ENV": Config.APP_ENV,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/test-trace", tags=["instrumentation"])
async def test_trace(admin_key: str = Depends(get_admin_key)):
    """
    Generate a test trace for verification.

    Requires admin API key.

    Returns:
        dict: Test trace information
    """
    from src.config.opentelemetry_config import OpenTelemetryConfig

    tracer = OpenTelemetryConfig.get_tracer(__name__)

    if not tracer:
        raise HTTPException(
            status_code=503,
            detail="OpenTelemetry tracing not available",
        )

    with tracer.start_as_current_span("test_trace") as span:
        span.set_attribute("test", True)
        span.set_attribute("timestamp", datetime.now(timezone.utc).isoformat())

        trace_id = get_current_trace_id()
        span_id = get_current_span_id()

        logger.info(
            f"Test trace generated",
            extra={
                "trace_id": trace_id,
                "span_id": span_id,
                "test": True,
            },
        )

        return {
            "status": "success",
            "trace_id": trace_id,
            "span_id": span_id,
            "message": "Test trace generated successfully. Check Tempo for trace details.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@router.post("/test-log", tags=["instrumentation"])
async def test_log(admin_key: str = Depends(get_admin_key)):
    """
    Generate a test log for verification.

    Requires admin API key.

    Returns:
        dict: Test log information
    """
    trace_id = get_current_trace_id()
    span_id = get_current_span_id()

    logger.info(
        "Test log generated",
        extra={
            "trace_id": trace_id,
            "span_id": span_id,
            "test": True,
            "endpoint": "/api/instrumentation/test-log",
        },
    )

    return {
        "status": "success",
        "trace_id": trace_id,
        "span_id": span_id,
        "message": "Test log generated successfully. Check Loki for log details.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/environment-variables", tags=["instrumentation"])
async def environment_variables(admin_key: str = Depends(get_admin_key)):
    """
    Get instrumentation-related environment variables.

    Requires admin API key.

    Returns:
        dict: Environment variables (sensitive values masked)
    """
    return {
        "loki": {
            "LOKI_ENABLED": os.environ.get("LOKI_ENABLED", "false"),
            "LOKI_URL": "***" if os.environ.get("LOKI_URL") else None,
            "LOKI_PUSH_URL": "***" if os.environ.get("LOKI_PUSH_URL") else None,
            "LOKI_QUERY_URL": "***" if os.environ.get("LOKI_QUERY_URL") else None,
        },
        "tempo": {
            "TEMPO_ENABLED": os.environ.get("TEMPO_ENABLED", "false"),
            "TEMPO_URL": "***" if os.environ.get("TEMPO_URL") else None,
            "TEMPO_OTLP_HTTP_ENDPOINT": "***" if os.environ.get("TEMPO_OTLP_HTTP_ENDPOINT") else None,
            "TEMPO_OTLP_GRPC_ENDPOINT": "***" if os.environ.get("TEMPO_OTLP_GRPC_ENDPOINT") else None,
        },
        "service": {
            "SERVICE_NAME": os.environ.get("SERVICE_NAME", "gatewayz-api"),
            "SERVICE_VERSION": os.environ.get("SERVICE_VERSION", "1.0.0"),
            "ENVIRONMENT": os.environ.get("ENVIRONMENT", "development"),
            "OTEL_SERVICE_NAME": os.environ.get("OTEL_SERVICE_NAME", "gatewayz-api"),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/otel/initialize", tags=["instrumentation"])
async def otel_initialize(admin_key: str = Depends(get_admin_key)):
    """
    Manually initialize or re-initialize OpenTelemetry.

    Useful for debugging when automatic initialization fails at startup
    due to timing issues (e.g., Tempo not ready when backend starts).

    Requires admin API key.

    Returns:
        dict: Initialization result with tracer provider status
    """
    from src.config.opentelemetry_config import OpenTelemetryConfig

    # Check if already initialized
    was_initialized = OpenTelemetryConfig._initialized

    if was_initialized:
        # Already initialized - return current status
        tracer = OpenTelemetryConfig.get_tracer(__name__)
        trace_id = None
        if tracer:
            with tracer.start_as_current_span("otel_initialize_check") as span:
                trace_id = get_current_trace_id()

        return {
            "status": "already_initialized",
            "initialized": True,
            "tracer_provider_exists": OpenTelemetryConfig._tracer_provider is not None,
            "endpoint": Config.TEMPO_OTLP_HTTP_ENDPOINT,
            "test_trace_id": trace_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # Try to initialize
    success = OpenTelemetryConfig.initialize()

    if success:
        # Generate a test trace to verify
        tracer = OpenTelemetryConfig.get_tracer(__name__)
        trace_id = None
        if tracer:
            with tracer.start_as_current_span("otel_manual_initialization") as span:
                span.set_attribute("manual_init", True)
                span.set_attribute("timestamp", datetime.now(timezone.utc).isoformat())
                trace_id = get_current_trace_id()

        logger.info(f"OpenTelemetry manually initialized. Test trace ID: {trace_id}")

        return {
            "status": "success",
            "initialized": True,
            "tracer_provider_exists": OpenTelemetryConfig._tracer_provider is not None,
            "endpoint": Config.TEMPO_OTLP_HTTP_ENDPOINT,
            "test_trace_id": trace_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    else:
        return {
            "status": "failed",
            "initialized": False,
            "tracer_provider_exists": False,
            "endpoint": Config.TEMPO_OTLP_HTTP_ENDPOINT,
            "message": "Initialization failed - check logs for details. Tempo may not be reachable.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@router.post("/otel/reinitialize", tags=["instrumentation"])
async def otel_reinitialize(admin_key: str = Depends(get_admin_key)):
    """
    Force re-initialize OpenTelemetry by shutting down existing provider first.

    Use this when tracing was initialized but is not working correctly,
    or when Tempo endpoint has changed.

    Requires admin API key.

    Returns:
        dict: Re-initialization result
    """
    from src.config.opentelemetry_config import OpenTelemetryConfig

    # Use the thread-safe shutdown method which handles locking internally
    # and resets the _initialized and _tracer_provider state
    if OpenTelemetryConfig._initialized:
        logger.info("Shutting down existing OpenTelemetry provider for re-initialization...")
        OpenTelemetryConfig.shutdown()

    # Re-initialize (thread-safe, handles locking internally)
    success = OpenTelemetryConfig.initialize()

    if success:
        tracer = OpenTelemetryConfig.get_tracer(__name__)
        trace_id = None
        if tracer:
            with tracer.start_as_current_span("otel_reinitialization") as span:
                span.set_attribute("reinitialized", True)
                span.set_attribute("timestamp", datetime.now(timezone.utc).isoformat())
                trace_id = get_current_trace_id()

        logger.info(f"OpenTelemetry re-initialized successfully. Test trace ID: {trace_id}")

        return {
            "status": "success",
            "reinitialized": True,
            "tracer_provider_exists": OpenTelemetryConfig._tracer_provider is not None,
            "endpoint": Config.TEMPO_OTLP_HTTP_ENDPOINT,
            "test_trace_id": trace_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    else:
        return {
            "status": "failed",
            "reinitialized": False,
            "tracer_provider_exists": False,
            "endpoint": Config.TEMPO_OTLP_HTTP_ENDPOINT,
            "message": "Re-initialization failed - check logs for details",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@router.get("/otel/status", tags=["instrumentation"])
async def otel_status(admin_key: str = Depends(get_admin_key)):
    """
    Get detailed OpenTelemetry status.

    Requires admin API key.

    Returns:
        dict: Current OTel initialization status and configuration
    """
    from src.config.opentelemetry_config import OpenTelemetryConfig

    # Check if we can create spans
    can_create_spans = False
    test_trace_id = None
    tracer = OpenTelemetryConfig.get_tracer(__name__)
    if tracer:
        try:
            with tracer.start_as_current_span("otel_status_check") as span:
                test_trace_id = get_current_trace_id()
                can_create_spans = test_trace_id is not None
        except Exception as e:
            logger.warning(f"Failed to create test span: {e}")

    return {
        "initialized": OpenTelemetryConfig._initialized,
        "tracer_provider_exists": OpenTelemetryConfig._tracer_provider is not None,
        "can_create_spans": can_create_spans,
        "test_trace_id": test_trace_id,
        "config": {
            "tempo_enabled": Config.TEMPO_ENABLED,
            "endpoint": Config.TEMPO_OTLP_HTTP_ENDPOINT,
            "service_name": Config.OTEL_SERVICE_NAME,
            "environment": Config.APP_ENV,
            "skip_reachability_check": Config.TEMPO_SKIP_REACHABILITY_CHECK,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
