"""
NotDiamond Client Service

Wraps NotDiamond Python SDK with:
- Retry logic (tenacity)
- Error handling (Sentry)
- Metrics tracking (Prometheus)
- Model mapping (NotDiamond IDs → Gatewayz IDs)
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Literal

from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

# Try to import NotDiamond SDK
try:
    from notdiamond import NotDiamond

    NOTDIAMOND_AVAILABLE = True
except ImportError:
    NOTDIAMOND_AVAILABLE = False
    logger.warning(
        "NotDiamond SDK not installed. General router will use fallback mode. "
        "Install with: pip install notdiamond"
    )

OptimizationMode = Literal["quality", "cost", "latency", "balanced"]


class NotDiamondClient:
    """NotDiamond API client with Gatewayz integration."""

    def __init__(self, api_key: str | None = None):
        """
        Initialize NotDiamond client.

        Args:
            api_key: NotDiamond API key (optional, falls back to config)
        """
        if not NOTDIAMOND_AVAILABLE:
            self.enabled = False
            logger.info("NotDiamond SDK not available, client disabled")
            return

        from src.config.config import Config

        self.api_key = api_key or Config.NOTDIAMOND_API_KEY
        self.timeout = Config.NOTDIAMOND_TIMEOUT
        self.enabled = Config.NOTDIAMOND_ENABLED and bool(self.api_key)

        if not self.api_key:
            logger.warning(
                "NOTDIAMOND_API_KEY not configured. General router will use fallback mode."
            )
            self.enabled = False
            return

        if self.enabled:
            try:
                self.client = NotDiamond(api_key=self.api_key)
                self.model_mappings = self._load_model_mappings()
                logger.info("NotDiamond client initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize NotDiamond client: {e}")
                self.enabled = False

    def _load_model_mappings(self) -> dict:
        """Load NotDiamond → Gatewayz model mappings from JSON file."""
        try:
            mapping_file = Path(__file__).parent / "notdiamond_model_mappings.json"
            with open(mapping_file) as f:
                mappings = json.load(f)
            logger.info(
                f"Loaded {len(mappings.get('mappings', {}))} NotDiamond model mappings"
            )
            return mappings
        except Exception as e:
            logger.error(f"Failed to load model mappings: {e}")
            return {"mappings": {}, "candidate_models": [], "fallback_mappings": {}}

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def select_model(
        self,
        messages: list[dict],
        mode: OptimizationMode = "quality",
        candidate_models: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Select optimal model via NotDiamond API.

        Args:
            messages: List of message dicts with 'role' and 'content'
            mode: Optimization mode (quality, cost, latency, balanced)
            candidate_models: Optional list of NotDiamond model IDs to consider

        Returns:
            {
                "model_id": "openai/gpt-4o",  # Gatewayz format
                "provider": "openai",
                "notdiamond_model": "gpt-4o",  # Original ND format
                "session_id": "nd_xxx",
                "confidence": 0.95,
                "latency_ms": 45.2,
                "mode": "quality"
            }

        Raises:
            Exception: If NotDiamond API call fails after retries
        """
        if not self.enabled:
            raise RuntimeError("NotDiamond client is not enabled")

        start_time = time.perf_counter()

        # Use default candidates if none provided
        if not candidate_models:
            candidate_models = self.model_mappings.get("candidate_models", [])

        # Map mode to NotDiamond preference parameter
        # NotDiamond API uses 'preference' for optimization target
        preference_map = {
            "quality": "quality",
            "cost": "cost",
            "latency": "latency",
            "balanced": "quality",  # Default balanced to quality
        }
        preference = preference_map.get(mode, "quality")

        try:
            # Call NotDiamond model_select API
            # Note: NotDiamond SDK might use model_select or create method
            # depending on version. Check SDK docs for exact API.
            result = await self._call_notdiamond_api(
                messages=messages,
                candidate_models=candidate_models,
                preference=preference,
            )

            latency_ms = (time.perf_counter() - start_time) * 1000

            # Extract result
            nd_model = result.get("model") or result.get("provider")
            session_id = result.get("session_id", "")
            confidence = result.get("confidence", 0.9)

            # Validate nd_model is not None or empty
            if not nd_model:
                raise ValueError(
                    "NotDiamond API returned empty model selection. "
                    f"Response: {result}"
                )

            # Map to Gatewayz format
            gatewayz_id, provider = self.map_notdiamond_to_gatewayz(nd_model)

            # Track metrics (optional - Prometheus may not be installed)
            try:
                from src.services.prometheus_metrics import track_notdiamond_api_call

                track_notdiamond_api_call(
                    status="success", mode=mode, latency_seconds=latency_ms / 1000
                )
            except ImportError:
                # Prometheus metrics are optional; skip tracking if not available
                logger.debug("Prometheus metrics not available, skipping NotDiamond tracking")

            return {
                "model_id": gatewayz_id,
                "provider": provider,
                "notdiamond_model": nd_model,
                "session_id": session_id,
                "confidence": confidence,
                "latency_ms": latency_ms,
                "mode": mode,
            }

        except Exception as e:
            latency_ms = (time.perf_counter() - start_time) * 1000

            # Track failure (optional - Prometheus may not be installed)
            try:
                from src.services.prometheus_metrics import track_notdiamond_api_call

                track_notdiamond_api_call(
                    status="error", mode=mode, latency_seconds=latency_ms / 1000
                )
            except ImportError:
                # Prometheus metrics are optional; skip tracking if not available
                logger.debug("Prometheus metrics not available, skipping error tracking")

            # Capture to Sentry (optional - Sentry may not be configured)
            try:
                from src.utils.sentry_context import capture_error

                capture_error(
                    e,
                    context_type="notdiamond_client",
                    context_data={"mode": mode, "message_count": len(messages)},
                    level="warning",
                )
            except ImportError:
                # Sentry integration is optional; skip error capture if not available
                logger.debug("Sentry not available, skipping error capture")

            logger.error(f"NotDiamond API call failed: {e}")
            raise

    async def _call_notdiamond_api(
        self,
        messages: list[dict],
        candidate_models: list[str],
        preference: str,
    ) -> dict[str, Any]:
        """
        Call NotDiamond API (abstracted for easier testing/mocking).

        Args:
            messages: Chat messages
            candidate_models: List of model IDs
            preference: Optimization preference (quality/cost/latency)

        Returns:
            API response dict
        """
        # NotDiamond SDK call
        # Use asyncio.to_thread to avoid blocking the event loop
        # since the NotDiamond SDK uses synchronous HTTP calls
        import asyncio

        try:
            # Wrap synchronous SDK call in thread pool to avoid blocking
            result = await asyncio.to_thread(
                self.client.model_select,
                messages=messages,
                model=candidate_models,
                preference=preference,
                timeout=self.timeout,
            )

            # Convert result to dict format
            # NotDiamond SDK might return an object, extract relevant fields
            if hasattr(result, "model"):
                return {
                    "model": result.model,
                    "session_id": getattr(result, "session_id", ""),
                    "confidence": getattr(result, "confidence", 0.9),
                }
            else:
                return result

        except Exception as e:
            logger.error(f"NotDiamond API error: {e}")
            raise

    def map_notdiamond_to_gatewayz(self, nd_model: str) -> tuple[str, str]:
        """
        Map NotDiamond model ID to Gatewayz format.

        Args:
            nd_model: NotDiamond model identifier

        Returns:
            Tuple of (gatewayz_id, provider)

        Examples:
            "gpt-4o" → ("openai/gpt-4o", "openai")
            "claude-sonnet-4-5" → ("anthropic/claude-sonnet-4-5", "anthropic")
        """
        mappings = self.model_mappings.get("mappings", {})

        # Direct mapping lookup
        if nd_model in mappings:
            mapping = mappings[nd_model]
            return (mapping["gatewayz_id"], mapping["provider"])

        # Fallback: try to infer from model name using fallback patterns
        logger.warning(f"No direct mapping for NotDiamond model: {nd_model}")

        fallback_mappings = self.model_mappings.get("fallback_mappings", {})
        nd_model_lower = nd_model.lower()

        for keyword, provider in fallback_mappings.items():
            if keyword in nd_model_lower:
                # Construct ID based on provider convention
                gatewayz_id = f"{provider}/{nd_model}"
                logger.info(
                    f"Using fallback mapping: {nd_model} → {gatewayz_id} (provider: {provider})"
                )
                return (gatewayz_id, provider)

        # Ultimate fallback: use OpenRouter as aggregator
        logger.warning(f"No fallback pattern matched for {nd_model}, using OpenRouter")
        return (nd_model, "openrouter")


# Singleton instance
_client: NotDiamondClient | None = None


def get_notdiamond_client() -> NotDiamondClient:
    """
    Get singleton NotDiamond client instance.

    Returns:
        Initialized NotDiamondClient
    """
    global _client
    if _client is None:
        _client = NotDiamondClient()
    return _client


def is_notdiamond_available() -> bool:
    """
    Check if NotDiamond is available and enabled.

    Returns:
        True if NotDiamond can be used, False otherwise
    """
    client = get_notdiamond_client()
    return client.enabled
