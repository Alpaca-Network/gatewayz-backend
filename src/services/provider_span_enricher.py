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
_PROVIDER_HOST_MAP: dict[str, str] = {
    # OpenAI family
    "api.openai.com": "openai",
    "api.openai.azure.com": "azure-openai",
    # Anthropic
    "api.anthropic.com": "anthropic",
    # OpenRouter (aggregator — single edge, not per-model)
    "openrouter.ai": "openrouter",
    "api.openrouter.ai": "openrouter",
    # Groq
    "api.groq.com": "groq",
    # Mistral
    "api.mistral.ai": "mistral",
    # Together AI
    "api.together.ai": "together-ai",
    "api.together.xyz": "together-ai",
    # Perplexity
    "api.perplexity.ai": "perplexity",
    # Cohere
    "api.cohere.com": "cohere",
    "api.cohere.ai": "cohere",
    # Google (Gemini / Vertex)
    "generativelanguage.googleapis.com": "google-gemini",
    "us-central1-aiplatform.googleapis.com": "google-vertex",
    # Fireworks AI
    "api.fireworks.ai": "fireworks",
    # DeepSeek
    "api.deepseek.com": "deepseek",
    # xAI (Grok)
    "api.x.ai": "xai",
    # Cerebras
    "api.cerebras.ai": "cerebras",
    # Featherless
    "inference.featherless.ai": "featherless",
    # Hyperbolic
    "api.hyperbolic.xyz": "hyperbolic",
    # Novita
    "api.novita.ai": "novita",
    # HuggingFace
    "api.huggingface.co": "huggingface",
    "huggingface.co": "huggingface",
    # Portkey (proxy/gateway)
    "api.portkey.ai": "portkey",
    # Replicate
    "api.replicate.com": "replicate",
    # AI21
    "api.ai21.com": "ai21",
    # Upstash (Redis)
    # Not an AI provider, but useful to see in topology
    "upstash.io": "upstash-redis",
    # Supabase (Postgres)
    "supabase.co": "supabase",
    # Stripe
    "api.stripe.com": "stripe",
    # Sentry
    "sentry.io": "sentry",
}


def _resolve_peer_service(host: str) -> str | None:
    """
    Return the canonical provider name for a given hostname, or None if
    the host is not a known external dependency.

    Checks exact match first, then suffix match (e.g. any *.supabase.co).
    """
    host = host.lower()
    if host in _PROVIDER_HOST_MAP:
        return _PROVIDER_HOST_MAP[host]
    # Suffix match — handles regional subdomains like us-east-1.supabase.co
    for pattern, service in _PROVIDER_HOST_MAP.items():
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
