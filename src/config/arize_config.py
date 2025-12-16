"""
Arize AI observability configuration for LLM tracing and monitoring.

This module configures the Arize OTEL integration to send traces
for AI/LLM observability, including:
- Request/response tracing for LLM calls
- Token usage tracking
- Latency monitoring
- Model performance analytics

Note: Arize OTEL is optional. If not installed or configured, tracing will be gracefully disabled.
"""

import logging

from src.config.config import Config

logger = logging.getLogger(__name__)

# Try to import arize-otel - it's optional
try:
    from arize.otel import register

    ARIZE_OTEL_AVAILABLE = True
except ImportError:
    ARIZE_OTEL_AVAILABLE = False
    register = None  # type: ignore

# Try to import OpenAI instrumentation - it's optional
try:
    from openinference.instrumentation.openai import OpenAIInstrumentor

    OPENAI_INSTRUMENTOR_AVAILABLE = True
except ImportError:
    OPENAI_INSTRUMENTOR_AVAILABLE = False
    OpenAIInstrumentor = None  # type: ignore


class ArizeConfig:
    """
    Arize AI observability configuration and setup.

    This class handles initialization of the Arize OTEL tracer provider
    for LLM observability and monitoring.
    """

    _initialized = False
    _tracer_provider = None

    @classmethod
    def initialize(cls) -> bool:
        """
        Initialize Arize OTEL tracing if enabled and configured.

        Returns:
            bool: True if initialization succeeded, False if disabled or failed
        """
        if cls._initialized:
            logger.debug("Arize OTEL already initialized")
            return True

        if not ARIZE_OTEL_AVAILABLE:
            logger.info("Arize OTEL not available (arize-otel package not installed)")
            return False

        if not Config.ARIZE_ENABLED:
            logger.info("Arize OTEL tracing disabled (ARIZE_ENABLED=false)")
            return False

        # Validate required configuration
        if not Config.ARIZE_SPACE_ID:
            logger.warning("Arize OTEL disabled: ARIZE_SPACE_ID not configured")
            return False

        if not Config.ARIZE_API_KEY:
            logger.warning("Arize OTEL disabled: ARIZE_API_KEY not configured")
            return False

        try:
            logger.info("Initializing Arize OTEL tracing...")
            logger.info(f"   Project: {Config.ARIZE_PROJECT_NAME}")

            # Register the Arize tracer provider
            cls._tracer_provider = register(
                space_id=Config.ARIZE_SPACE_ID,
                api_key=Config.ARIZE_API_KEY,
                project_name=Config.ARIZE_PROJECT_NAME,
            )

            # Instrument OpenAI client for automatic LLM tracing
            if OPENAI_INSTRUMENTOR_AVAILABLE:
                OpenAIInstrumentor().instrument(tracer_provider=cls._tracer_provider)
                logger.info("   OpenAI instrumentation enabled")
            else:
                logger.debug("   OpenAI instrumentation not available (package not installed)")

            cls._initialized = True
            logger.info("Arize OTEL tracing initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Arize OTEL: {e}", exc_info=True)
            return False

    @classmethod
    def get_tracer_provider(cls):
        """
        Get the Arize tracer provider instance.

        Returns:
            The tracer provider if initialized, None otherwise
        """
        return cls._tracer_provider if cls._initialized else None

    @classmethod
    def is_initialized(cls) -> bool:
        """
        Check if Arize OTEL is initialized.

        Returns:
            bool: True if initialized, False otherwise
        """
        return cls._initialized

    @classmethod
    def shutdown(cls) -> None:
        """
        Gracefully shutdown Arize OTEL and flush any pending traces.

        Should be called during application shutdown to ensure all traces
        are exported before the application exits.
        """
        if not cls._initialized:
            return

        try:
            logger.info("Shutting down Arize OTEL...")
            if cls._tracer_provider and hasattr(cls._tracer_provider, "shutdown"):
                cls._tracer_provider.shutdown()
            logger.info("Arize OTEL shutdown complete")
        except Exception as e:
            logger.error(f"Error during Arize OTEL shutdown: {e}", exc_info=True)
        finally:
            cls._initialized = False
            cls._tracer_provider = None


def init_arize_otel() -> bool:
    """
    Convenience function to initialize Arize OTEL tracing.

    Returns:
        bool: True if initialization succeeded, False otherwise
    """
    return ArizeConfig.initialize()


def shutdown_arize_otel() -> None:
    """
    Convenience function to shutdown Arize OTEL tracing.
    """
    ArizeConfig.shutdown()
