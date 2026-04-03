"""
ProviderSpanEnricher — OpenTelemetry SpanProcessor that adds peer.service
to outbound HTTP spans targeting known AI providers.

WHY THIS IS NEEDED
──────────────────
Tempo's metrics_generator service-graph processor builds topology edges by
looking for traces that contain CLIENT spans with a ``peer.service`` attribute.
When ``peer.service = "openai"`` is present on a child span of the incoming
FastAPI request span (service.name = "gatewayz-backend"), Tempo generates the
edge gatewayz-backend → openai and emits:

    traces_service_graph_request_total{client="gatewayz-backend",server="openai"}
    traces_service_graph_request_failed_total{...}
    traces_service_graph_request_duration_seconds_bucket{...}

These metrics are what the Grafana "Service Graph & Topology" section reads.

Without peer.service the AI providers are external HTTP endpoints that don't
propagate traceparent back, so Tempo sees only one service in each trace and
draws no edges — the dependency map stays blank.

HOW IT WORKS
────────────
opentelemetry-instrumentation-httpx is already installed and activated in
opentelemetry_config.py. Every outbound HTTPX call already produces a CLIENT
span.  This SpanProcessor intercepts spans on_end, checks whether the URL host
matches a known AI provider, and writes peer.service into the span attributes.
Because SpanProcessor.on_end receives a ReadableSpan we use the internal
_attributes dict (same approach used by OTel contrib processors).
"""

import logging
from urllib.parse import urlparse

try:
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult  # noqa: F401
    from opentelemetry.trace import SpanKind

    OPENTELEMETRY_AVAILABLE = True
except ImportError:
    OPENTELEMETRY_AVAILABLE = False

logger = logging.getLogger(__name__)

# Maps lowercase hostname (or hostname suffix) → canonical peer.service name.
# Tempo will use this value as the "server" label in service-graph metrics and
# as the node label in the Live Service Dependency Map panel.
# Static infrastructure hosts (not AI providers — always kept in code)
_INFRA_HOST_MAP: dict[str, str] = {
    "upstash.io": "upstash-redis",
    "supabase.co": "supabase",
    "api.stripe.com": "stripe",
    "sentry.io": "sentry",
}

# Static external AI providers (not in our gateway registry)
_EXTERNAL_HOST_MAP: dict[str, str] = {
    "api.openai.azure.com": "azure-openai",
    "api.mistral.ai": "mistral",
    "api.perplexity.ai": "perplexity",
    "api.cohere.com": "cohere",
    "api.cohere.ai": "cohere",
    "api.deepseek.com": "deepseek",
    "api.hyperbolic.xyz": "hyperbolic",
    "api.portkey.ai": "portkey",
    "api.replicate.com": "replicate",
    "api.ai21.com": "ai21",
}

# Fallback map — used when the gateway registry is unavailable
_FALLBACK_PROVIDER_HOST_MAP: dict[str, str] = {
    "api.openai.com": "openai",
    "api.openai.azure.com": "azure-openai",
    "api.anthropic.com": "anthropic",
    "openrouter.ai": "openrouter",
    "api.openrouter.ai": "openrouter",
    "api.groq.com": "groq",
    "api.mistral.ai": "mistral",
    "api.together.ai": "together",
    "api.together.xyz": "together",
    "api.perplexity.ai": "perplexity",
    "api.cohere.com": "cohere",
    "api.cohere.ai": "cohere",
    "generativelanguage.googleapis.com": "google-gemini",
    "us-central1-aiplatform.googleapis.com": "google-vertex",
    "api.fireworks.ai": "fireworks",
    "api.deepseek.com": "deepseek",
    "api.x.ai": "xai",
    "api.cerebras.ai": "cerebras",
    "inference.featherless.ai": "featherless",
    "api.hyperbolic.xyz": "hyperbolic",
    "api.novita.ai": "novita",
    "api.huggingface.co": "huggingface",
    "huggingface.co": "huggingface",
    "api.portkey.ai": "portkey",
    "api.replicate.com": "replicate",
    "api.ai21.com": "ai21",
    "upstash.io": "upstash-redis",
    "supabase.co": "supabase",
    "api.stripe.com": "stripe",
    "sentry.io": "sentry",
}

# Module-level cached host map (rebuilt when gateway registry refreshes)
_host_map_cache: dict[str, str] | None = None
_host_map_cache_ts: float = 0.0
_HOST_MAP_TTL = 300  # 5 minutes


def _build_provider_host_map() -> dict[str, str]:
    """Build the hostname-to-provider map from gateway registry + static maps."""
    global _host_map_cache, _host_map_cache_ts
    import time

    now = time.monotonic()
    if _host_map_cache is not None and (now - _host_map_cache_ts) < _HOST_MAP_TTL:
        return _host_map_cache

    host_map: dict[str, str] = {}
    # 1. Static infrastructure hosts
    host_map.update(_INFRA_HOST_MAP)
    # 2. External AI providers (not in our registry)
    host_map.update(_EXTERNAL_HOST_MAP)
    # 3. Provider hostnames from DB registry
    try:
        from src.services.gateway_registry import get_gateway_registry

        registry = get_gateway_registry()
        for slug, entry in registry.items():
            for hostname in entry.get("hostnames", []):
                host_map[hostname] = slug
    except Exception:
        # Fall back to static map
        host_map.update(_FALLBACK_PROVIDER_HOST_MAP)
        return host_map

    _host_map_cache = host_map
    _host_map_cache_ts = now
    return host_map


# Backward-compat alias — static fallback only; use _build_provider_host_map()
# for the live DB-driven map.
_PROVIDER_HOST_MAP = _FALLBACK_PROVIDER_HOST_MAP


def _resolve_peer_service(host: str) -> str | None:
    """
    Return the canonical provider name for a given hostname, or None if
    the host is not a known external dependency.

    Checks exact match first, then suffix match (e.g. any *.supabase.co).
    """
    host = host.lower()
    host_map = _build_provider_host_map()
    if host in host_map:
        return host_map[host]
    # Suffix match — handles regional subdomains like us-east-1.supabase.co
    for pattern, service in host_map.items():
        if host.endswith("." + pattern) or host == pattern:
            return service
    return None


class ProviderSpanEnricher:
    """
    OpenTelemetry SpanProcessor that annotates outbound HTTP CLIENT spans with
    ``peer.service`` so Tempo's service-graph processor can draw edges between
    gatewayz-backend and each AI provider it calls.

    Usage (in opentelemetry_config.py):
        tracer_provider.add_span_processor(ProviderSpanEnricher())
    """

    if OPENTELEMETRY_AVAILABLE:
        from opentelemetry.sdk.trace import Span as _SdkSpan

        def on_start(self, span: "_SdkSpan", parent_context=None) -> None:  # noqa: F811
            pass  # Nothing to do on start

        def on_end(self, span: "ReadableSpan") -> None:  # noqa: F811
            try:
                # Only enrich CLIENT spans (outbound HTTP requests)
                if span.kind != SpanKind.CLIENT:
                    return

                attrs = span.attributes or {}

                # Skip spans that already have peer.service
                if attrs.get("peer.service"):
                    return

                # Extract host from OTel HTTP semantic conventions
                # OTel 1.x: http.url  |  OTel 2.x: url.full + server.address
                host: str | None = None

                url = attrs.get("http.url") or attrs.get("url.full")
                if url:
                    try:
                        host = urlparse(str(url)).hostname
                    except Exception:
                        pass

                if not host:
                    host = attrs.get("server.address") or attrs.get("net.peer.name")
                    if host:
                        host = str(host)

                if not host:
                    return

                peer_service = _resolve_peer_service(host)
                if peer_service:
                    # SpanProcessor.on_end receives a ReadableSpan whose internal
                    # _attributes dict is still mutable at this stage (before export).
                    span._attributes["peer.service"] = peer_service
                    logger.debug(
                        "ProviderSpanEnricher: %s → peer.service=%s",
                        host,
                        peer_service,
                    )
            except Exception:
                pass  # Never break tracing for enrichment failures

        def shutdown(self) -> None:
            pass

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return True

    else:
        # OTel not available — no-op stubs so the class can always be imported
        def on_start(self, span, parent_context=None) -> None:
            pass

        def on_end(self, span) -> None:
            pass

        def shutdown(self) -> None:
            pass

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return True
