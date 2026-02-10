"""
AI-specific distributed tracing utilities for Gateway Z.

This module provides OpenTelemetry instrumentation specifically designed for
AI inference workloads, tracking:
- Model inference calls with provider/model attributes
- Token usage (input/output/total)
- Cost tracking
- Provider routing decisions
- Circuit breaker states
- Latency breakdowns (queue time, inference time, network time)

These traces integrate with:
- Tempo for distributed tracing visualization
- Langfuse for LLM-specific observability and analytics
- Loki logs and Prometheus metrics for correlation

Usage:
    from src.utils.ai_tracing import AITracer, trace_model_call

    # Using context manager
    async with AITracer.trace_inference("openrouter", "gpt-4") as span:
        response = await call_model(...)
        span.set_token_usage(input_tokens=100, output_tokens=50)
        span.set_cost(0.003)

    # Using decorator
    @trace_model_call(provider="anthropic", model="claude-3-opus")
    async def call_claude(prompt: str) -> str:
        ...
"""

import logging
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any, Callable, Optional

# Try to import OpenTelemetry - it's optional
try:
    from opentelemetry import trace
    from opentelemetry.trace import Span, SpanKind, Status, StatusCode

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    Span = None  # type: ignore
    SpanKind = None  # type: ignore
    Status = None  # type: ignore
    StatusCode = None  # type: ignore

# Try to import Langfuse - it's optional
try:
    from src.config.langfuse_config import (
        LangfuseConfig,
        LangfuseTracer,
        LangfuseGenerationContext,
    )

    LANGFUSE_AVAILABLE = True
except ImportError:
    LANGFUSE_AVAILABLE = False
    LangfuseConfig = None  # type: ignore
    LangfuseTracer = None  # type: ignore
    LangfuseGenerationContext = None  # type: ignore

logger = logging.getLogger(__name__)


class AIRequestType(str, Enum):
    """Types of AI requests for categorization in traces."""

    CHAT_COMPLETION = "chat_completion"
    TEXT_COMPLETION = "text_completion"
    EMBEDDING = "embedding"
    IMAGE_GENERATION = "image_generation"
    AUDIO_TRANSCRIPTION = "audio_transcription"
    AUDIO_GENERATION = "audio_generation"
    CODE_COMPLETION = "code_completion"
    FUNCTION_CALL = "function_call"


class CircuitState(str, Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, rejecting requests
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class AISpanContext:
    """
    Context object for AI trace spans with helper methods.

    Provides a clean interface for setting AI-specific attributes
    on OpenTelemetry spans and Langfuse generations.
    """

    span: Optional[Span] = None
    start_time: float = field(default_factory=time.time)
    provider: str = ""
    model: str = ""
    # Langfuse context for LLM-specific observability
    langfuse_ctx: Optional["LangfuseGenerationContext"] = None
    # Track input/output for Langfuse generation logging
    _input_data: Any = None
    _output_data: Any = None
    _usage_data: Optional[dict] = None
    _cost_usd: Optional[float] = None

    def set_token_usage(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: Optional[int] = None,
    ) -> "AISpanContext":
        """Set token usage attributes on the span.

        Sets both custom ai.* attributes (for backward compatibility) and
        standardized gen_ai.* semantic conventions (for observability tools).
        Also updates Langfuse generation if available.
        """
        total = total_tokens or (input_tokens + output_tokens)

        # Store for Langfuse
        self._usage_data = {
            "input": input_tokens,
            "output": output_tokens,
            "total": total,
        }

        # OpenTelemetry attributes
        if self.span and OTEL_AVAILABLE:
            # Custom attributes (backward compatibility)
            self.span.set_attribute("ai.tokens.input", input_tokens)
            self.span.set_attribute("ai.tokens.output", output_tokens)
            self.span.set_attribute("ai.tokens.total", total)
            # Standardized gen_ai.* semantic conventions (OpenLLMetry compatible)
            self.span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
            self.span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
            self.span.set_attribute("gen_ai.usage.total_tokens", total)

        # Langfuse generation
        if self.langfuse_ctx:
            self.langfuse_ctx.set_usage(input_tokens, output_tokens, total)

        return self

    def set_cost(self, cost_usd: float) -> "AISpanContext":
        """Set the cost of this inference call in USD."""
        # Store for Langfuse
        self._cost_usd = cost_usd

        # OpenTelemetry attributes
        if self.span and OTEL_AVAILABLE:
            self.span.set_attribute("ai.cost.usd", cost_usd)
            # Also set gen_ai.* convention for compatibility
            self.span.set_attribute("gen_ai.usage.cost", cost_usd)

        # Langfuse generation
        if self.langfuse_ctx:
            self.langfuse_ctx.set_cost(cost_usd)

        return self

    def set_input(self, input_data: Any) -> "AISpanContext":
        """Set the input data for this inference call (for Langfuse)."""
        self._input_data = input_data
        if self.langfuse_ctx:
            self.langfuse_ctx.set_input(input_data)
        return self

    def set_output(self, output_data: Any) -> "AISpanContext":
        """Set the output data for this inference call (for Langfuse)."""
        self._output_data = output_data
        if self.langfuse_ctx:
            self.langfuse_ctx.set_output(output_data)
        return self

    def set_response_model(
        self,
        response_model: str,
        finish_reason: Optional[str] = None,
        response_id: Optional[str] = None,
    ) -> "AISpanContext":
        """Set the actual model returned by the provider.

        This captures the response model which may differ from the requested model
        (e.g., requesting "gpt-4" but getting "gpt-4-0613").

        Args:
            response_model: The actual model name/ID from the provider response
            finish_reason: Why the generation stopped (stop, length, end_turn, etc.)
            response_id: The provider's response/completion ID
        """
        if self.span and OTEL_AVAILABLE:
            # Standardized gen_ai.* semantic conventions
            self.span.set_attribute("gen_ai.response.model", response_model)
            if finish_reason:
                self.span.set_attribute("gen_ai.response.finish_reason", finish_reason)
            if response_id:
                self.span.set_attribute("gen_ai.response.id", response_id)
            # Also set custom attribute for backward compatibility
            self.span.set_attribute("ai.response.model", response_model)
        return self

    def set_model_parameters(
        self,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        frequency_penalty: Optional[float] = None,
        presence_penalty: Optional[float] = None,
    ) -> "AISpanContext":
        """Set model generation parameters.

        Sets both custom ai.params.* attributes (for backward compatibility) and
        standardized gen_ai.request.* semantic conventions (for observability tools).
        """
        if self.span and OTEL_AVAILABLE:
            if temperature is not None:
                self.span.set_attribute("ai.params.temperature", temperature)
                self.span.set_attribute("gen_ai.request.temperature", temperature)
            if max_tokens is not None:
                self.span.set_attribute("ai.params.max_tokens", max_tokens)
                self.span.set_attribute("gen_ai.request.max_tokens", max_tokens)
            if top_p is not None:
                self.span.set_attribute("ai.params.top_p", top_p)
                self.span.set_attribute("gen_ai.request.top_p", top_p)
            if frequency_penalty is not None:
                self.span.set_attribute("ai.params.frequency_penalty", frequency_penalty)
                self.span.set_attribute("gen_ai.request.frequency_penalty", frequency_penalty)
            if presence_penalty is not None:
                self.span.set_attribute("ai.params.presence_penalty", presence_penalty)
                self.span.set_attribute("gen_ai.request.presence_penalty", presence_penalty)
        return self

    def set_routing_info(
        self,
        original_model: Optional[str] = None,
        fallback_used: bool = False,
        routing_strategy: Optional[str] = None,
    ) -> "AISpanContext":
        """Set routing decision information."""
        if self.span and OTEL_AVAILABLE:
            if original_model:
                self.span.set_attribute("ai.routing.original_model", original_model)
            self.span.set_attribute("ai.routing.fallback_used", fallback_used)
            if routing_strategy:
                self.span.set_attribute("ai.routing.strategy", routing_strategy)
        return self

    def set_circuit_breaker(
        self,
        state: CircuitState,
        failure_count: int = 0,
        success_count: int = 0,
    ) -> "AISpanContext":
        """Set circuit breaker state information."""
        if self.span and OTEL_AVAILABLE:
            self.span.set_attribute("ai.circuit.state", state.value)
            self.span.set_attribute("ai.circuit.failure_count", failure_count)
            self.span.set_attribute("ai.circuit.success_count", success_count)
        return self

    def set_cache_info(
        self,
        cache_hit: bool,
        cache_key: Optional[str] = None,
    ) -> "AISpanContext":
        """Set cache hit/miss information."""
        if self.span and OTEL_AVAILABLE:
            self.span.set_attribute("ai.cache.hit", cache_hit)
            if cache_key:
                self.span.set_attribute("ai.cache.key", cache_key)
        return self

    def set_user_info(
        self,
        user_id: Optional[str] = None,
        api_key_hash: Optional[str] = None,
        tier: Optional[str] = None,
    ) -> "AISpanContext":
        """Set user context information (redacted for privacy).

        Sets both custom user.* attributes and customer.id for model popularity tracking.
        """
        if self.span and OTEL_AVAILABLE:
            if user_id:
                self.span.set_attribute("user.id", user_id)
                # Also set customer.id for popularity tracking (OpenLLMetry convention)
                self.span.set_attribute("customer.id", user_id)
            if api_key_hash:
                self.span.set_attribute("user.api_key_hash", api_key_hash)
            if tier:
                self.span.set_attribute("user.tier", tier)
        return self

    def set_error(
        self,
        error: Exception,
        error_type: Optional[str] = None,
    ) -> "AISpanContext":
        """Record an error on the span."""
        if self.span and OTEL_AVAILABLE:
            self.span.set_status(Status(StatusCode.ERROR, str(error)))
            self.span.record_exception(error)
            self.span.set_attribute("ai.error.type", error_type or type(error).__name__)
            self.span.set_attribute("ai.error.message", str(error)[:500])  # Truncate
        return self

    def set_latency_breakdown(
        self,
        queue_time_ms: Optional[float] = None,
        inference_time_ms: Optional[float] = None,
        network_time_ms: Optional[float] = None,
    ) -> "AISpanContext":
        """Set latency breakdown for detailed performance analysis."""
        if self.span and OTEL_AVAILABLE:
            if queue_time_ms is not None:
                self.span.set_attribute("ai.latency.queue_ms", queue_time_ms)
            if inference_time_ms is not None:
                self.span.set_attribute("ai.latency.inference_ms", inference_time_ms)
            if network_time_ms is not None:
                self.span.set_attribute("ai.latency.network_ms", network_time_ms)
        return self

    def add_event(self, name: str, attributes: Optional[dict] = None) -> "AISpanContext":
        """Add an event to the span timeline."""
        if self.span and OTEL_AVAILABLE:
            self.span.add_event(name, attributes=attributes or {})
        return self


class AITracer:
    """
    AI-specific tracer for Gateway Z inference requests.

    Provides context managers and utilities for tracing AI model calls
    with rich semantic attributes for observability.
    """

    _tracer = None

    @classmethod
    def _get_tracer(cls):
        """Get or create the OpenTelemetry tracer."""
        if not OTEL_AVAILABLE:
            logger.warning("AITracer: OTEL_AVAILABLE is False - OpenTelemetry not installed")
            return None
        if cls._tracer is None:
            cls._tracer = trace.get_tracer("gatewayz.ai", "2.0.3")
            # Log tracer info for debugging
            provider = trace.get_tracer_provider()
            logger.info(f"AITracer: Created tracer with provider: {type(provider).__name__}")
        return cls._tracer

    @classmethod
    @asynccontextmanager
    async def trace_inference(
        cls,
        provider: str,
        model: str,
        request_type: AIRequestType = AIRequestType.CHAT_COMPLETION,
        operation_name: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ):
        """
        Async context manager for tracing AI inference calls.

        Traces are sent to both OpenTelemetry (Tempo) and Langfuse (if enabled).

        Args:
            provider: AI provider name (e.g., "openrouter", "anthropic")
            model: Model identifier (e.g., "gpt-4", "claude-3-opus")
            request_type: Type of AI request
            operation_name: Optional custom operation name for the span
            user_id: Optional user ID for Langfuse user-level analytics
            session_id: Optional session ID for Langfuse session tracking
            metadata: Optional metadata dict for both OTel and Langfuse

        Yields:
            AISpanContext: Context object for setting additional attributes

        Example:
            async with AITracer.trace_inference("openrouter", "gpt-4") as ctx:
                response = await call_model(prompt)
                ctx.set_token_usage(input_tokens=100, output_tokens=50)
                ctx.set_cost(0.003)
        """
        tracer = cls._get_tracer()
        span_name = operation_name or f"{provider}/{model}"

        # Create Langfuse context if available
        langfuse_ctx = None
        langfuse_cm = None
        if LANGFUSE_AVAILABLE and LangfuseConfig and LangfuseConfig.is_initialized():
            try:
                # Use the async context manager from LangfuseTracer
                langfuse_cm = LangfuseTracer.trace_generation(
                    provider=provider,
                    model=model,
                    user_id=user_id,
                    session_id=session_id,
                    metadata=metadata,
                )
                langfuse_ctx = await langfuse_cm.__aenter__()
            except Exception as e:
                logger.debug(f"AITracer: Failed to create Langfuse context: {e}")
                langfuse_ctx = None
                langfuse_cm = None

        # Track exception info for proper Langfuse error level propagation
        exc_info: tuple = (None, None, None)

        # Wrap entire tracing logic in try/finally to ensure Langfuse context is always closed
        # This prevents context leaks if OTel span creation fails
        try:
            if tracer:
                logger.debug(f"AITracer: Creating span '{span_name}' with gen_ai.system={provider}")
                with tracer.start_as_current_span(
                    span_name,
                    kind=SpanKind.CLIENT,
                    attributes={
                        # Custom attributes (backward compatibility)
                        "ai.provider": provider,
                        "ai.model": model,
                        "ai.request_type": request_type.value,
                        "service.operation": "model_inference",
                        # Standardized gen_ai.* semantic conventions (OpenLLMetry)
                        "gen_ai.system": provider,
                        "gen_ai.request.model": model,
                        "gen_ai.operation.name": request_type.value,
                    },
                ) as span:
                    ctx = AISpanContext(
                        span=span,
                        provider=provider,
                        model=model,
                        langfuse_ctx=langfuse_ctx,
                    )
                    logger.debug(f"AITracer: Span created, span_id={span.get_span_context().span_id if span.get_span_context().is_valid else 'invalid'}")
                    try:
                        yield ctx
                        # Set success status if no error
                        span.set_status(Status(StatusCode.OK))
                        # Record total duration
                        duration_ms = (time.time() - ctx.start_time) * 1000
                        span.set_attribute("ai.duration_ms", duration_ms)
                        logger.debug(f"AITracer: Span completed successfully, duration={duration_ms:.2f}ms")
                    except Exception as e:
                        ctx.set_error(e)
                        if langfuse_ctx:
                            langfuse_ctx.set_error(e)
                        # Capture exception info for __aexit__
                        import sys
                        exc_info = sys.exc_info()
                        raise
            else:
                logger.warning(f"AITracer: No tracer available for '{span_name}' - tracing disabled")
                # OpenTelemetry not available, yield context with Langfuse only
                ctx = AISpanContext(provider=provider, model=model, langfuse_ctx=langfuse_ctx)
                try:
                    yield ctx
                except Exception as e:
                    if langfuse_ctx:
                        langfuse_ctx.set_error(e)
                    # Capture exception info for __aexit__
                    import sys
                    exc_info = sys.exc_info()
                    raise
        finally:
            # Always close Langfuse context to prevent leaks
            # Pass exception info to __aexit__ for proper error level
            if langfuse_cm:
                try:
                    await langfuse_cm.__aexit__(*exc_info)
                except Exception as e:
                    logger.debug(f"AITracer: Error closing Langfuse context: {e}")

    @classmethod
    @contextmanager
    def trace_inference_sync(
        cls,
        provider: str,
        model: str,
        request_type: AIRequestType = AIRequestType.CHAT_COMPLETION,
        operation_name: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ):
        """
        Synchronous context manager for tracing AI inference calls.

        Same as trace_inference but for synchronous code.
        Traces are sent to both OpenTelemetry (Tempo) and Langfuse (if enabled).
        """
        tracer = cls._get_tracer()
        span_name = operation_name or f"{provider}/{model}"

        # Create Langfuse context if available
        langfuse_ctx = None
        langfuse_cm = None
        if LANGFUSE_AVAILABLE and LangfuseConfig and LangfuseConfig.is_initialized():
            try:
                langfuse_cm = LangfuseTracer.trace_generation_sync(
                    provider=provider,
                    model=model,
                    user_id=user_id,
                    session_id=session_id,
                    metadata=metadata,
                )
                langfuse_ctx = langfuse_cm.__enter__()
            except Exception as e:
                logger.debug(f"AITracer: Failed to create Langfuse context: {e}")
                langfuse_ctx = None
                langfuse_cm = None

        # Track exception info for proper Langfuse error level propagation
        exc_info: tuple = (None, None, None)

        # Wrap entire tracing logic in try/finally to ensure Langfuse context is always closed
        # This prevents context leaks if OTel span creation fails
        try:
            if tracer:
                with tracer.start_as_current_span(
                    span_name,
                    kind=SpanKind.CLIENT,
                    attributes={
                        # Custom attributes (backward compatibility)
                        "ai.provider": provider,
                        "ai.model": model,
                        "ai.request_type": request_type.value,
                        "service.operation": "model_inference",
                        # Standardized gen_ai.* semantic conventions (OpenLLMetry)
                        "gen_ai.system": provider,
                        "gen_ai.request.model": model,
                        "gen_ai.operation.name": request_type.value,
                    },
                ) as span:
                    ctx = AISpanContext(
                        span=span,
                        provider=provider,
                        model=model,
                        langfuse_ctx=langfuse_ctx,
                    )
                    try:
                        yield ctx
                        span.set_status(Status(StatusCode.OK))
                        duration_ms = (time.time() - ctx.start_time) * 1000
                        span.set_attribute("ai.duration_ms", duration_ms)
                    except Exception as e:
                        ctx.set_error(e)
                        if langfuse_ctx:
                            langfuse_ctx.set_error(e)
                        # Capture exception info for __exit__
                        import sys
                        exc_info = sys.exc_info()
                        raise
            else:
                ctx = AISpanContext(provider=provider, model=model, langfuse_ctx=langfuse_ctx)
                try:
                    yield ctx
                except Exception as e:
                    if langfuse_ctx:
                        langfuse_ctx.set_error(e)
                    # Capture exception info for __exit__
                    import sys
                    exc_info = sys.exc_info()
                    raise
        finally:
            # Always close Langfuse context to prevent leaks
            # Pass exception info to __exit__ for proper error level
            if langfuse_cm:
                try:
                    langfuse_cm.__exit__(*exc_info)
                except Exception as e:
                    logger.debug(f"AITracer: Error closing Langfuse context: {e}")

    @classmethod
    @asynccontextmanager
    async def trace_routing(cls, strategy: str):
        """
        Trace the model routing/selection process.

        Args:
            strategy: Routing strategy name (e.g., "cost_optimized", "latency_optimized")
        """
        tracer = cls._get_tracer()

        if tracer:
            with tracer.start_as_current_span(
                "route_model",
                kind=SpanKind.INTERNAL,
                attributes={
                    "ai.routing.strategy": strategy,
                    "service.operation": "route_selection",
                },
            ) as span:
                ctx = AISpanContext(span=span)
                try:
                    yield ctx
                    span.set_status(Status(StatusCode.OK))
                except Exception as e:
                    ctx.set_error(e)
                    raise
        else:
            yield AISpanContext()

    @classmethod
    @asynccontextmanager
    async def trace_provider_call(cls, provider: str, endpoint: str):
        """
        Trace an HTTP call to an AI provider's API.

        Args:
            provider: Provider name
            endpoint: API endpoint being called
        """
        tracer = cls._get_tracer()

        if tracer:
            with tracer.start_as_current_span(
                f"provider_call/{provider}",
                kind=SpanKind.CLIENT,
                attributes={
                    "ai.provider": provider,
                    "http.url": endpoint,
                    "service.operation": "provider_api_call",
                },
            ) as span:
                ctx = AISpanContext(span=span, provider=provider)
                try:
                    yield ctx
                    span.set_status(Status(StatusCode.OK))
                except Exception as e:
                    ctx.set_error(e)
                    raise
        else:
            yield AISpanContext(provider=provider)


def trace_model_call(
    provider: str,
    model: str,
    request_type: AIRequestType = AIRequestType.CHAT_COMPLETION,
):
    """
    Decorator for tracing async model call functions.

    Args:
        provider: AI provider name
        model: Model identifier
        request_type: Type of AI request

    Example:
        @trace_model_call(provider="anthropic", model="claude-3-opus")
        async def call_claude(prompt: str) -> str:
            ...
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            async with AITracer.trace_inference(
                provider=provider,
                model=model,
                request_type=request_type,
                operation_name=func.__name__,
            ) as ctx:
                result = await func(*args, **kwargs)
                # If the result has token usage info, set it automatically
                if hasattr(result, "usage"):
                    usage = result.usage
                    ctx.set_token_usage(
                        input_tokens=getattr(usage, "prompt_tokens", 0),
                        output_tokens=getattr(usage, "completion_tokens", 0),
                        total_tokens=getattr(usage, "total_tokens", None),
                    )
                return result

        return wrapper

    return decorator


def get_current_trace_context() -> dict:
    """
    Get current trace context for log correlation.

    Returns:
        dict: Contains trace_id and span_id if available, empty dict otherwise
    """
    if not OTEL_AVAILABLE:
        return {}

    try:
        span = trace.get_current_span()
        span_context = span.get_span_context()
        if span_context.is_valid:
            return {
                "trace_id": format(span_context.trace_id, "032x"),
                "span_id": format(span_context.span_id, "016x"),
            }
    except Exception:
        pass
    return {}


def inject_trace_context_to_headers(headers: dict) -> dict:
    """
    Inject W3C trace context into HTTP headers for propagation.

    Args:
        headers: Existing headers dict

    Returns:
        dict: Headers with trace context added
    """
    if not OTEL_AVAILABLE:
        return headers

    try:
        from opentelemetry.propagate import inject

        inject(headers)
    except Exception as e:
        logger.debug(f"Failed to inject trace context: {e}")

    return headers
