"""
Traceloop SDK (OpenLLMetry) configuration for standardized LLM observability.

This module initializes the Traceloop SDK to provide:
- Automatic instrumentation of OpenAI, Anthropic, and other LLM SDKs
- Standardized gen_ai.* semantic conventions for LLM metrics
- OTLP export to existing Tempo/LGTM stack
- Customer/user ID tagging for popularity tracking

IMPORTANT: This module must be imported and initialized BEFORE any LLM SDK imports
to enable proper monkey-patching.

Environment Variables:
- TRACELOOP_BASE_URL: OTLP endpoint (falls back to TEMPO_OTLP_HTTP_ENDPOINT)
- TRACELOOP_HEADERS: Authentication headers for OTLP endpoint (JSON format)
- TRACELOOP_API_KEY: Optional Traceloop cloud API key
- OPENLLMETRY_ENABLED: Enable/disable Traceloop SDK (default: true if TEMPO_ENABLED)

Usage:
    # At the very start of main.py, BEFORE other imports:
    from src.config.traceloop_config import initialize_traceloop
    initialize_traceloop()
"""

import json
import logging
import os
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Track initialization state
_initialized = False


def _get_otlp_endpoint() -> str | None:
    """
    Get the OTLP endpoint for Traceloop SDK.

    Priority:
    1. TRACELOOP_BASE_URL (explicit Traceloop config)
    2. TEMPO_OTLP_HTTP_ENDPOINT (existing OTel config)

    Returns:
        str: OTLP endpoint URL without /v1/traces suffix
    """
    # Check for explicit Traceloop endpoint first
    traceloop_url = os.environ.get("TRACELOOP_BASE_URL")
    if traceloop_url:
        return traceloop_url.rstrip("/")

    # Fall back to existing Tempo endpoint
    tempo_url = os.environ.get("TEMPO_OTLP_HTTP_ENDPOINT")
    if tempo_url:
        # Remove /v1/traces suffix if present (Traceloop adds it automatically)
        url = tempo_url.rstrip("/")
        if url.endswith("/v1/traces"):
            url = url[:-10]  # Remove /v1/traces
        return url

    return None


def _normalize_railway_endpoint(endpoint: str) -> str:
    """
    Normalize Railway endpoints for proper OTLP connectivity.

    Railway internal DNS uses port 4318, public URLs use HTTPS.
    """
    if not endpoint:
        return endpoint

    # Railway internal DNS detection
    if ".railway.internal" in endpoint:
        # Ensure http:// and port 4318 for internal
        if not endpoint.startswith("http://"):
            endpoint = f"http://{endpoint}"
        parsed = urlparse(endpoint)
        if not parsed.port:
            endpoint = f"{parsed.scheme}://{parsed.hostname}:4318{parsed.path}"
        return endpoint

    # Railway public URL detection
    if ".railway.app" in endpoint or ".up.railway.app" in endpoint:
        # Remove port suffixes, use HTTPS
        endpoint = endpoint.replace(":4318", "").replace(":4317", "")
        if endpoint.startswith("http://"):
            endpoint = endpoint.replace("http://", "https://")
        elif not endpoint.startswith("https://"):
            endpoint = f"https://{endpoint}"
        return endpoint

    return endpoint


def _get_headers() -> dict:
    """
    Get authentication headers for OTLP endpoint.

    Supports:
    - TRACELOOP_HEADERS: JSON string of headers
    - GRAFANA_TEMPO_USERNAME + GRAFANA_TEMPO_API_KEY: Basic auth
    """
    headers = {}

    # Check for explicit Traceloop headers
    traceloop_headers = os.environ.get("TRACELOOP_HEADERS")
    if traceloop_headers:
        try:
            headers.update(json.loads(traceloop_headers))
        except json.JSONDecodeError:
            logger.warning("Failed to parse TRACELOOP_HEADERS as JSON")

    # Check for Grafana Cloud auth (basic auth for Tempo)
    grafana_user = os.environ.get("GRAFANA_TEMPO_USERNAME")
    grafana_key = os.environ.get("GRAFANA_TEMPO_API_KEY")
    if grafana_user and grafana_key:
        import base64

        auth = base64.b64encode(f"{grafana_user}:{grafana_key}".encode()).decode()
        headers["Authorization"] = f"Basic {auth}"

    return headers


def is_enabled() -> bool:
    """
    Check if OpenLLMetry/Traceloop SDK should be enabled.

    Returns True ONLY if OPENLLMETRY_ENABLED is explicitly set to "true".
    Disabled by default to prevent startup issues on deployments where
    traceloop-sdk is not yet configured.
    """
    return os.environ.get("OPENLLMETRY_ENABLED", "false").lower() == "true"


def initialize_traceloop() -> bool:
    """
    Initialize the Traceloop SDK for OpenLLMetry instrumentation.

    This MUST be called at application startup BEFORE importing any LLM SDKs
    (OpenAI, Anthropic, etc.) to enable automatic instrumentation.

    Returns:
        bool: True if initialization succeeded, False otherwise
    """
    global _initialized

    if _initialized:
        logger.debug("Traceloop SDK already initialized")
        return True

    if not is_enabled():
        logger.info("⏭️  Traceloop SDK disabled (OPENLLMETRY_ENABLED=false or TEMPO_ENABLED=false)")
        return False

    try:
        from traceloop.sdk import Traceloop
    except ImportError:
        logger.info("⏭️  Traceloop SDK not available (package not installed)")
        return False

    endpoint = _get_otlp_endpoint()
    if not endpoint:
        logger.warning(
            "⏭️  Traceloop SDK: No OTLP endpoint configured. "
            "Set TRACELOOP_BASE_URL or TEMPO_OTLP_HTTP_ENDPOINT"
        )
        return False

    # Normalize Railway endpoints
    endpoint = _normalize_railway_endpoint(endpoint)

    try:
        logger.info("🔭 Initializing Traceloop SDK (OpenLLMetry)...")
        logger.info(f"   OTLP endpoint: {endpoint}")

        # Get service name from existing config
        service_name = os.environ.get("OTEL_SERVICE_NAME", "gatewayz-api")
        app_env = os.environ.get("APP_ENV", "development")

        # Check for Traceloop Cloud API key
        api_key = os.environ.get("TRACELOOP_API_KEY")
        if api_key:
            logger.info("   Using Traceloop Cloud API key")

        # Get authentication headers
        headers = _get_headers()

        # Build exporter config
        exporter_config = {}
        if headers:
            exporter_config["headers"] = headers
            logger.info(f"   Custom headers configured: {list(headers.keys())}")

        # Initialize Traceloop SDK
        # The SDK auto-instruments OpenAI, Anthropic, Cohere, and other LLM libraries
        Traceloop.init(
            app_name=service_name,
            api_endpoint=endpoint,
            api_key=api_key,  # Optional: for Traceloop Cloud
            disable_batch=False,  # Use batching for performance
            exporter="otlp_http",  # Use OTLP HTTP exporter (compatible with Tempo)
            resource_attributes={
                "service.name": service_name,
                "service.version": "2.0.3",
                "deployment.environment": app_env,
                "telemetry.sdk.name": "openllmetry",
            },
            headers=headers if headers else None,
        )

        _initialized = True
        logger.info("✅ Traceloop SDK initialized successfully")
        logger.info("   Auto-instrumentation enabled for: OpenAI, Anthropic, Cohere, etc.")
        logger.info("   Using gen_ai.* semantic conventions")

        return True

    except Exception as e:
        logger.error(f"❌ Failed to initialize Traceloop SDK: {e}", exc_info=True)
        return False


def set_association_properties(
    user_id: str | None = None,
    api_key_id: str | None = None,
    session_id: str | None = None,
    **custom_properties,
) -> None:
    """
    Set association properties for the current trace context.

    These properties are attached to all subsequent LLM spans in the current
    context, enabling tracking of metrics by user/customer.

    Args:
        user_id: Customer/user identifier for popularity tracking
        api_key_id: API key identifier (hashed for privacy)
        session_id: Session identifier for grouping related requests
        **custom_properties: Additional custom properties

    Usage:
        # In the /chat/completions endpoint, after authentication:
        from src.config.traceloop_config import set_association_properties
        set_association_properties(
            user_id=user.id,
            api_key_id=api_key_hash,
            session_id=request.headers.get("X-Session-ID"),
        )
    """
    if not _initialized:
        return

    try:
        from traceloop.sdk import Traceloop

        properties = {}
        if user_id:
            properties["customer.id"] = user_id
            properties["user.id"] = user_id
        if api_key_id:
            properties["api_key.id"] = api_key_id
        if session_id:
            properties["session.id"] = session_id
        properties.update(custom_properties)

        if properties:
            Traceloop.set_association_properties(properties)

    except Exception as e:
        logger.debug(f"Failed to set Traceloop association properties: {e}")


def set_prompt_tracing(
    prompt_name: str | None = None,
    prompt_version: str | None = None,
) -> None:
    """
    Associate the current trace with a prompt template for tracking.

    Args:
        prompt_name: Name of the prompt template
        prompt_version: Version of the prompt template
    """
    if not _initialized:
        return

    try:
        from traceloop.sdk.decorators import set_prompt

        if prompt_name:
            set_prompt(name=prompt_name, version=prompt_version)

    except Exception as e:
        logger.debug(f"Failed to set Traceloop prompt: {e}")


def flush() -> None:
    """
    Flush any pending spans to the OTLP endpoint.

    Call this during application shutdown to ensure all spans are exported.
    """
    if not _initialized:
        return

    try:
        from traceloop.sdk import Traceloop

        Traceloop.flush()
        logger.info("   Traceloop spans flushed")

    except Exception as e:
        logger.debug(f"Failed to flush Traceloop spans: {e}")


def shutdown() -> None:
    """
    Gracefully shutdown the Traceloop SDK.

    Flushes pending spans and releases resources.
    """
    global _initialized

    if not _initialized:
        return

    try:
        flush()
        logger.info("✅ Traceloop SDK shutdown complete")
    except Exception as e:
        logger.warning(f"Error during Traceloop shutdown: {e}")
    finally:
        _initialized = False


def is_initialized() -> bool:
    """Check if Traceloop SDK is initialized."""
    return _initialized
