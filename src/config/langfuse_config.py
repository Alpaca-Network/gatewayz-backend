"""
Langfuse LLM observability configuration for tracing and analytics.

This module configures the Langfuse SDK for LLM observability, including:
- Request/response tracing for LLM calls
- Token usage and cost tracking
- Model performance analytics
- Scoring and feedback collection
- User session tracking

Langfuse provides:
- Cloud-hosted: https://cloud.langfuse.com
- Self-hosted: https://github.com/langfuse/langfuse

Note: Langfuse is optional. If not installed or configured, tracing will be gracefully disabled.
"""

import logging
import threading
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, UTC
from typing import Any, Optional

from src.config.config import Config

logger = logging.getLogger(__name__)

# Try to import Langfuse - it's optional
try:
    from langfuse import Langfuse

    LANGFUSE_AVAILABLE = True
except ImportError:
    LANGFUSE_AVAILABLE = False
    Langfuse = None  # type: ignore


class LangfuseConfig:
    """
    Langfuse LLM observability configuration and setup.

    This class handles initialization of the Langfuse client
    for LLM tracing, scoring, and analytics.

    Thread-safe: Uses a lock to guard state transitions in initialize() and shutdown().
    """

    _initialized = False
    _client: Optional["Langfuse"] = None
    _lock = threading.Lock()

    @classmethod
    def initialize(cls) -> bool:
        """
        Initialize Langfuse client if enabled and configured.

        Thread-safe: Uses double-checked locking pattern.

        Returns:
            bool: True if initialization succeeded, False if disabled or failed
        """
        # Fast path: already initialized
        if cls._initialized:
            return True

        with cls._lock:
            # Double-check after acquiring lock
            if cls._initialized:
                logger.debug("Langfuse already initialized")
                return True

            if not LANGFUSE_AVAILABLE:
                logger.info("Langfuse not available (langfuse package not installed)")
                return False

            if not Config.LANGFUSE_ENABLED:
                logger.info("Langfuse tracing disabled (LANGFUSE_ENABLED=false)")
                return False

            # Validate required configuration
            if not Config.LANGFUSE_PUBLIC_KEY:
                logger.warning("Langfuse disabled: LANGFUSE_PUBLIC_KEY not configured")
                return False

            if not Config.LANGFUSE_SECRET_KEY:
                logger.warning("Langfuse disabled: LANGFUSE_SECRET_KEY not configured")
                return False

            try:
                logger.info("Initializing Langfuse LLM observability...")
                logger.info(f"   Host: {Config.LANGFUSE_HOST}")
                logger.info(f"   Debug: {Config.LANGFUSE_DEBUG}")

                # Initialize Langfuse client
                cls._client = Langfuse(
                    public_key=Config.LANGFUSE_PUBLIC_KEY,
                    secret_key=Config.LANGFUSE_SECRET_KEY,
                    host=Config.LANGFUSE_HOST,
                    debug=Config.LANGFUSE_DEBUG,
                    flush_interval=Config.LANGFUSE_FLUSH_INTERVAL,
                )

                # Verify connection by checking auth
                # Note: Langfuse SDK batches requests, so this just validates config
                logger.debug("Langfuse client created, connection will be verified on first trace")

                cls._initialized = True
                logger.info("Langfuse LLM observability initialized successfully")
                return True

            except Exception as e:
                logger.error(f"Failed to initialize Langfuse: {e}", exc_info=True)
                return False

    @classmethod
    def get_client(cls) -> Optional["Langfuse"]:
        """
        Get the Langfuse client instance.

        Returns:
            The Langfuse client if initialized, None otherwise
        """
        return cls._client if cls._initialized else None

    @classmethod
    def is_initialized(cls) -> bool:
        """
        Check if Langfuse is initialized.

        Returns:
            bool: True if initialized, False otherwise
        """
        return cls._initialized

    @classmethod
    def flush(cls) -> None:
        """
        Flush any pending traces to Langfuse.

        Call this to ensure all traces are sent before shutdown
        or when you need immediate trace visibility.
        """
        if not cls._initialized or not cls._client:
            return

        try:
            cls._client.flush()
            logger.debug("Langfuse traces flushed")
        except Exception as e:
            logger.warning(f"Failed to flush Langfuse traces: {e}")

    @classmethod
    def shutdown(cls) -> None:
        """
        Gracefully shutdown Langfuse and flush any pending traces.

        Thread-safe: Uses lock to prevent concurrent shutdown/initialize races.

        Should be called during application shutdown to ensure all traces
        are exported before the application exits.
        """
        with cls._lock:
            if not cls._initialized:
                return

            try:
                logger.info("Shutting down Langfuse...")
                if cls._client:
                    # Flush pending traces before shutdown
                    cls._client.flush()
                    # Shutdown the client
                    cls._client.shutdown()
                logger.info("Langfuse shutdown complete")
            except Exception as e:
                logger.error(f"Error during Langfuse shutdown: {e}", exc_info=True)
            finally:
                cls._initialized = False
                cls._client = None

    @classmethod
    def create_trace(
        cls,
        name: str,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict | None = None,
        tags: list[str] | None = None,
        **kwargs,
    ):
        """
        Create a new Langfuse trace for tracking an LLM operation.

        Args:
            name: Name of the trace (e.g., "chat_completion", "embedding")
            user_id: Optional user identifier for user-level analytics
            session_id: Optional session ID for grouping related traces
            metadata: Optional metadata dict for the trace
            tags: Optional list of tags for filtering

        Returns:
            Langfuse trace object or None if not initialized
        """
        if not cls._initialized or not cls._client:
            return None

        try:
            return cls._client.trace(
                name=name,
                user_id=user_id,
                session_id=session_id,
                metadata=metadata or {},
                tags=tags or [],
                **kwargs,
            )
        except Exception as e:
            logger.warning(f"Failed to create Langfuse trace: {e}")
            return None

    @classmethod
    def create_generation(
        cls,
        trace,
        name: str,
        model: str,
        input: Any = None,
        output: Any = None,
        usage: dict | None = None,
        metadata: dict | None = None,
        **kwargs,
    ):
        """
        Create a generation span within a trace for an LLM call.

        Args:
            trace: Parent trace object
            name: Name of the generation (e.g., model name)
            model: Model identifier
            input: Input to the model (messages, prompt, etc.)
            output: Output from the model
            usage: Token usage dict with prompt_tokens, completion_tokens, total_tokens
            metadata: Additional metadata

        Returns:
            Langfuse generation object or None if trace is None
        """
        if not trace:
            return None

        try:
            return trace.generation(
                name=name,
                model=model,
                input=input,
                output=output,
                usage=usage,
                metadata=metadata or {},
                **kwargs,
            )
        except Exception as e:
            logger.warning(f"Failed to create Langfuse generation: {e}")
            return None


class LangfuseTracer:
    """
    Langfuse-specific tracer for Gateway Z inference requests.

    Provides context managers for tracing AI model calls
    with Langfuse's generation tracking.
    """

    @classmethod
    @asynccontextmanager
    async def trace_generation(
        cls,
        provider: str,
        model: str,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict | None = None,
    ):
        """
        Async context manager for tracing LLM generations with Langfuse.

        Args:
            provider: AI provider name (e.g., "openrouter", "anthropic")
            model: Model identifier (e.g., "gpt-4", "claude-3-opus")
            user_id: Optional user identifier
            session_id: Optional session ID
            metadata: Optional metadata dict

        Yields:
            LangfuseGenerationContext: Context object for setting generation data

        Example:
            async with LangfuseTracer.trace_generation("openrouter", "gpt-4") as ctx:
                response = await call_model(messages)
                ctx.set_output(response)
                ctx.set_usage(input_tokens=100, output_tokens=50)
        """
        client = LangfuseConfig.get_client()

        if client:
            trace = None
            generation = None
            start_time = datetime.now(UTC)

            try:
                # Create trace
                trace = client.trace(
                    name=f"{provider}/{model}",
                    user_id=user_id,
                    session_id=session_id,
                    metadata={
                        "provider": provider,
                        "model": model,
                        **(metadata or {}),
                    },
                    tags=[provider, "chat_completion"],
                )

                # Create generation span
                generation = trace.generation(
                    name=model,
                    model=model,
                    start_time=start_time,
                    metadata={"provider": provider},
                )

                ctx = LangfuseGenerationContext(
                    trace=trace,
                    generation=generation,
                    provider=provider,
                    model=model,
                    start_time=start_time,
                )

                yield ctx

                # End generation with success
                if generation:
                    generation.end(
                        output=ctx._output,
                        usage=ctx._usage,
                        metadata=ctx._metadata,
                        level="DEFAULT",
                    )

            except Exception as e:
                # Record error
                if generation:
                    generation.end(
                        output={"error": str(e)},
                        level="ERROR",
                        status_message=str(e),
                    )
                raise
        else:
            # Langfuse not available, yield dummy context
            yield LangfuseGenerationContext(
                trace=None,
                generation=None,
                provider=provider,
                model=model,
            )

    @classmethod
    @contextmanager
    def trace_generation_sync(
        cls,
        provider: str,
        model: str,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict | None = None,
    ):
        """
        Synchronous context manager for tracing LLM generations with Langfuse.

        Same as trace_generation but for synchronous code.
        """
        client = LangfuseConfig.get_client()

        if client:
            trace = None
            generation = None
            start_time = datetime.now(UTC)

            try:
                trace = client.trace(
                    name=f"{provider}/{model}",
                    user_id=user_id,
                    session_id=session_id,
                    metadata={
                        "provider": provider,
                        "model": model,
                        **(metadata or {}),
                    },
                    tags=[provider, "chat_completion"],
                )

                generation = trace.generation(
                    name=model,
                    model=model,
                    start_time=start_time,
                    metadata={"provider": provider},
                )

                ctx = LangfuseGenerationContext(
                    trace=trace,
                    generation=generation,
                    provider=provider,
                    model=model,
                    start_time=start_time,
                )

                yield ctx

                if generation:
                    generation.end(
                        output=ctx._output,
                        usage=ctx._usage,
                        metadata=ctx._metadata,
                        level="DEFAULT",
                    )

            except Exception as e:
                if generation:
                    generation.end(
                        output={"error": str(e)},
                        level="ERROR",
                        status_message=str(e),
                    )
                raise
        else:
            yield LangfuseGenerationContext(
                trace=None,
                generation=None,
                provider=provider,
                model=model,
            )


class LangfuseGenerationContext:
    """
    Context object for Langfuse generation spans with helper methods.

    Provides a clean interface for setting generation-specific attributes.
    """

    def __init__(
        self,
        trace=None,
        generation=None,
        provider: str = "",
        model: str = "",
        start_time: datetime | None = None,
    ):
        self.trace = trace
        self.generation = generation
        self.provider = provider
        self.model = model
        self.start_time = start_time or datetime.now(UTC)
        self._output: Any = None
        self._usage: dict | None = None
        self._metadata: dict = {}

    def set_input(self, input_data: Any) -> "LangfuseGenerationContext":
        """Set the input data for the generation."""
        if self.generation:
            try:
                self.generation.update(input=input_data)
            except Exception as e:
                logger.debug(f"Failed to set Langfuse input: {e}")
        return self

    def set_output(self, output_data: Any) -> "LangfuseGenerationContext":
        """Set the output data for the generation."""
        self._output = output_data
        return self

    def set_usage(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int | None = None,
    ) -> "LangfuseGenerationContext":
        """Set token usage for the generation.

        Uses the generic Langfuse format (input, output, total).
        Langfuse SDK v2 internally maps OpenAI-style keys if needed.
        """
        self._usage = {
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens or (input_tokens + output_tokens),
        }
        return self

    def set_cost(self, cost_usd: float) -> "LangfuseGenerationContext":
        """Set the cost of this generation in USD."""
        self._metadata["cost_usd"] = cost_usd
        return self

    def set_model_parameters(
        self,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        **kwargs,
    ) -> "LangfuseGenerationContext":
        """Set model generation parameters."""
        params = {}
        if temperature is not None:
            params["temperature"] = temperature
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        if top_p is not None:
            params["top_p"] = top_p
        params.update(kwargs)

        if params and self.generation:
            try:
                self.generation.update(model_parameters=params)
            except Exception as e:
                logger.debug(f"Failed to set Langfuse model parameters: {e}")
        return self

    def add_metadata(self, key: str, value: Any) -> "LangfuseGenerationContext":
        """Add metadata to the generation."""
        self._metadata[key] = value
        return self

    def set_error(self, error: Exception) -> "LangfuseGenerationContext":
        """Record an error on the generation.

        Merges error info into existing output rather than overwriting,
        so any partial response data is preserved alongside the error.
        """
        # Merge error into existing output if it's a dict, otherwise create new
        if isinstance(self._output, dict):
            self._output["error"] = str(error)
            self._output["error_type"] = type(error).__name__
        else:
            # Preserve previous output in a separate field if it exists
            prev_output = self._output
            self._output = {"error": str(error), "error_type": type(error).__name__}
            if prev_output is not None:
                self._output["partial_output"] = prev_output
        self._metadata["error"] = True
        return self

    def score(
        self,
        name: str,
        value: float,
        comment: str | None = None,
    ) -> "LangfuseGenerationContext":
        """
        Add a score to the trace for quality evaluation.

        Args:
            name: Score name (e.g., "accuracy", "relevance", "user_rating")
            value: Score value (typically 0-1 or 1-5)
            comment: Optional comment explaining the score
        """
        if self.trace:
            try:
                self.trace.score(
                    name=name,
                    value=value,
                    comment=comment,
                )
            except Exception as e:
                logger.debug(f"Failed to add Langfuse score: {e}")
        return self


# Convenience functions for module-level usage
def init_langfuse() -> bool:
    """
    Convenience function to initialize Langfuse.

    Returns:
        bool: True if initialization succeeded, False otherwise
    """
    return LangfuseConfig.initialize()


def shutdown_langfuse() -> None:
    """
    Convenience function to shutdown Langfuse.
    """
    LangfuseConfig.shutdown()


def get_langfuse_client() -> Optional["Langfuse"]:
    """
    Get the Langfuse client instance.

    Returns:
        Langfuse client or None if not initialized
    """
    return LangfuseConfig.get_client()


def flush_langfuse() -> None:
    """
    Flush pending Langfuse traces.
    """
    LangfuseConfig.flush()
