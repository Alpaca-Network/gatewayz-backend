"""
Request prioritization system for chat completions.

This module provides a priority queue and request classification system to
fast-track high-priority chat completion requests for improved streaming performance.

Includes low-latency model routing based on actual production metrics.
"""

import logging
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# LOW-LATENCY MODEL CONFIGURATION
# Based on actual production metrics from model_health_tracking table
# Updated: 2025-12-12
# =============================================================================

# Models with sub-100ms average response time (ultra-fast)
ULTRA_LOW_LATENCY_MODELS: set[str] = {
    "groq/moonshotai/kimi-k2-instruct-0905",  # 29ms
    "groq/openai/gpt-oss-120b",  # 74ms
}

# Models with sub-500ms average response time (fast)
LOW_LATENCY_MODELS: set[str] = {
    # Groq models (fastest provider)
    "groq/moonshotai/kimi-k2-instruct-0905",  # 29ms
    "groq/openai/gpt-oss-120b",  # 74ms
    "groq/llama-3.3-70b-versatile",  # 492ms
    "groq/llama-3.1-70b-versatile",
    "groq/llama-3.1-8b-instant",
    "groq/mixtral-8x7b-32768",
    "groq/gemma2-9b-it",
    # OpenRouter fast models
    "arcee-ai/trinity-mini:free",  # 214ms
    "switchpoint/router",  # 217ms
    "anthropic/claude-3.5-sonnet",  # 283ms
    "google/gemma-2-9b-it",  # 368ms
    "google/gemini-2.0-flash-001",  # 415ms
    "google/gemini-2.0-flash-exp:free",
    "mistralai/ministral-3b-2512",  # 421ms
    # Fireworks fast models
    "fireworks/accounts/fireworks/models/deepseek-v3-0324",  # 326ms
}

# Provider latency tiers based on production data (lower = faster)
# Tier 1: <100ms typical, Tier 2: 100-500ms typical, Tier 3: 500ms+ typical
PROVIDER_LATENCY_TIERS: dict[str, int] = {
    # Tier 1 - Ultra-fast (specialized inference hardware)
    "groq": 1,
    "cerebras": 1,
    # Tier 2 - Fast (optimized infrastructure)
    "fireworks": 2,
    "together": 2,
    "cloudflare-workers-ai": 2,
    # Tier 3 - Standard (good general performance)
    "openrouter": 3,
    "deepinfra": 3,
    "google-vertex": 3,
    # Tier 4 - Variable (depends on model/load)
    "huggingface": 4,
    "featherless": 4,
    "near": 4,
    "alibaba-cloud": 4,
}

# Default tier for unknown providers
DEFAULT_PROVIDER_TIER = 3


class RequestPriority(IntEnum):
    """Priority levels for requests (lower number = higher priority)"""

    CRITICAL = 0  # System-critical requests
    HIGH = 1  # Premium users, paid plans
    MEDIUM = 2  # Standard users
    LOW = 3  # Free tier, trial users
    BACKGROUND = 4  # Background/batch processing


@dataclass
class PriorityRequest:
    """Container for prioritized requests"""

    priority: RequestPriority
    request_id: str
    user_id: str | None
    timestamp: float
    model: str
    stream: bool
    metadata: dict[str, Any]

    def __lt__(self, other):
        """Compare requests for priority queue ordering"""
        if self.priority != other.priority:
            return self.priority < other.priority
        # If same priority, older requests go first (FIFO within priority)
        return self.timestamp < other.timestamp


class RequestPrioritizer:
    """
    Manages request prioritization for chat completions.

    Uses priority levels to ensure premium users and streaming requests
    get faster processing.
    """

    def __init__(self):
        self._priority_weights = {
            RequestPriority.CRITICAL: 1.0,
            RequestPriority.HIGH: 0.9,
            RequestPriority.MEDIUM: 0.7,
            RequestPriority.LOW: 0.5,
            RequestPriority.BACKGROUND: 0.3,
        }
        self._request_counts: dict[RequestPriority, int] = dict.fromkeys(RequestPriority, 0)
        self._total_requests = 0

    def determine_priority(
        self,
        user_tier: str | None = None,
        is_streaming: bool = False,
        model: str | None = None,
        is_trial: bool = False,
    ) -> RequestPriority:
        """
        Determine request priority based on user tier and request characteristics.

        Args:
            user_tier: User subscription tier (e.g., 'premium', 'pro', 'free')
            is_streaming: Whether this is a streaming request
            model: Model being requested
            is_trial: Whether user is on trial

        Returns:
            RequestPriority level
        """
        # Trial users get low priority
        if is_trial:
            return RequestPriority.LOW

        # Determine base priority from user tier
        if user_tier in ("enterprise", "premium", "pro"):
            base_priority = RequestPriority.HIGH
        elif user_tier in ("standard", "plus"):
            base_priority = RequestPriority.MEDIUM
        elif user_tier == "free":
            base_priority = RequestPriority.LOW
        else:
            # Default for unknown tier
            base_priority = RequestPriority.MEDIUM

        # Streaming requests get slight boost (one level higher priority)
        if is_streaming and base_priority > RequestPriority.CRITICAL:
            base_priority = RequestPriority(base_priority - 1)

        # Fast models (like GPT-3.5-turbo) can be slightly deprioritized
        # since they're already fast
        if model and any(
            fast_model in model.lower()
            for fast_model in ["3.5-turbo", "gpt-3.5", "llama-3-8b", "mistral-7b"]
        ):
            if base_priority < RequestPriority.BACKGROUND:
                base_priority = RequestPriority(base_priority + 1)

        return base_priority

    def track_request(self, priority: RequestPriority):
        """Track a request for metrics"""
        self._request_counts[priority] += 1
        self._total_requests += 1

    def get_priority_stats(self) -> dict[str, Any]:
        """Get statistics about request prioritization"""
        if self._total_requests == 0:
            return {
                "total_requests": 0,
                "priority_distribution": {},
            }

        distribution = {
            priority.name: {
                "count": self._request_counts[priority],
                "percentage": (self._request_counts[priority] / self._total_requests) * 100,
            }
            for priority in RequestPriority
        }

        return {
            "total_requests": self._total_requests,
            "priority_distribution": distribution,
        }

    def should_fast_track(self, priority: RequestPriority) -> bool:
        """Determine if a request should be fast-tracked"""
        return priority <= RequestPriority.HIGH

    def get_timeout_multiplier(self, priority: RequestPriority) -> float:
        """Get timeout multiplier based on priority (higher priority = longer timeout)"""
        return self._priority_weights[priority] * 2.0 + 0.5  # Range: 1.1x to 2.5x


# Global prioritizer instance
_prioritizer = RequestPrioritizer()


def get_request_priority(
    user_tier: str | None = None,
    is_streaming: bool = False,
    model: str | None = None,
    is_trial: bool = False,
) -> RequestPriority:
    """
    Get priority for a request.

    Args:
        user_tier: User subscription tier
        is_streaming: Whether this is a streaming request
        model: Model being requested
        is_trial: Whether user is on trial

    Returns:
        RequestPriority level
    """
    priority = _prioritizer.determine_priority(
        user_tier=user_tier,
        is_streaming=is_streaming,
        model=model,
        is_trial=is_trial,
    )
    _prioritizer.track_request(priority)
    return priority


def should_fast_track(priority: RequestPriority) -> bool:
    """Check if request should be fast-tracked"""
    return _prioritizer.should_fast_track(priority)


def get_timeout_for_priority(
    base_timeout: float,
    priority: RequestPriority,
) -> float:
    """
    Get adjusted timeout based on priority.

    Args:
        base_timeout: Base timeout in seconds
        priority: Request priority level

    Returns:
        Adjusted timeout in seconds
    """
    multiplier = _prioritizer.get_timeout_multiplier(priority)
    return base_timeout * multiplier


def get_priority_stats() -> dict[str, Any]:
    """Get current prioritization statistics"""
    return _prioritizer.get_priority_stats()


def log_request_priority(
    request_id: str,
    priority: RequestPriority,
    user_tier: str | None = None,
    model: str | None = None,
):
    """
    Log request priority information for monitoring.

    Args:
        request_id: Unique request identifier
        priority: Assigned priority level
        user_tier: User subscription tier
        model: Model being requested
    """
    logger.info(
        f"Request {request_id} assigned priority {priority.name} "
        f"(tier={user_tier}, model={model})"
    )


# Provider selection helpers based on priority
def get_preferred_providers_for_priority(
    priority: RequestPriority,
    available_providers: list[str],
) -> list[str]:
    """
    Get preferred providers ordered by priority.

    Higher priority requests get routed to faster/more reliable providers first.
    Uses PROVIDER_LATENCY_TIERS based on actual production metrics.

    Args:
        priority: Request priority level
        available_providers: List of available provider names

    Returns:
        Ordered list of provider names
    """

    # Sort providers by their latency tier
    def get_tier(provider: str) -> int:
        return PROVIDER_LATENCY_TIERS.get(provider.lower(), DEFAULT_PROVIDER_TIER)

    # Group providers by tier
    tier_1 = [p for p in available_providers if get_tier(p) == 1]  # Ultra-fast
    tier_2 = [p for p in available_providers if get_tier(p) == 2]  # Fast
    tier_3 = [p for p in available_providers if get_tier(p) == 3]  # Standard
    tier_4 = [p for p in available_providers if get_tier(p) >= 4]  # Variable

    ordered = []

    # High priority gets fastest providers first
    if priority <= RequestPriority.HIGH:
        ordered.extend(tier_1)
        ordered.extend(tier_2)
        ordered.extend(tier_3)
        ordered.extend(tier_4)
    # Medium priority balances speed and availability
    elif priority == RequestPriority.MEDIUM:
        ordered.extend(tier_2)
        ordered.extend(tier_1)
        ordered.extend(tier_3)
        ordered.extend(tier_4)
    # Low priority can use any provider (prefer higher availability)
    else:
        ordered.extend(tier_3)
        ordered.extend(tier_4)
        ordered.extend(tier_2)
        ordered.extend(tier_1)

    # Add any remaining providers not in our tiers
    for provider in available_providers:
        if provider not in ordered:
            ordered.append(provider)

    return ordered


def is_low_latency_model(model_id: str) -> bool:
    """
    Check if a model is classified as low-latency.

    Args:
        model_id: The model identifier

    Returns:
        True if the model is in the LOW_LATENCY_MODELS set
    """
    if not model_id:
        return False
    return model_id.lower() in {m.lower() for m in LOW_LATENCY_MODELS}


def is_ultra_low_latency_model(model_id: str) -> bool:
    """
    Check if a model is classified as ultra-low-latency (<100ms).

    Args:
        model_id: The model identifier

    Returns:
        True if the model is in the ULTRA_LOW_LATENCY_MODELS set
    """
    if not model_id:
        return False
    return model_id.lower() in {m.lower() for m in ULTRA_LOW_LATENCY_MODELS}


def get_provider_latency_tier(provider: str) -> int:
    """
    Get the latency tier for a provider.

    Args:
        provider: Provider name

    Returns:
        Tier number (1=fastest, 4=variable)
    """
    return PROVIDER_LATENCY_TIERS.get(provider.lower(), DEFAULT_PROVIDER_TIER)


def get_low_latency_models() -> list[str]:
    """
    Get the list of all low-latency models.

    Returns:
        List of model IDs with sub-500ms response times
    """
    return sorted(LOW_LATENCY_MODELS)


def get_ultra_low_latency_models() -> list[str]:
    """
    Get the list of ultra-low-latency models.

    Returns:
        List of model IDs with sub-100ms response times
    """
    return sorted(ULTRA_LOW_LATENCY_MODELS)


def get_fastest_providers() -> list[str]:
    """
    Get providers sorted by latency tier (fastest first).

    Returns:
        List of provider names sorted by speed
    """
    sorted_providers = sorted(PROVIDER_LATENCY_TIERS.items(), key=lambda x: x[1])
    return [provider for provider, _ in sorted_providers]


def suggest_low_latency_alternative(model_id: str) -> str | None:
    """
    Suggest a low-latency alternative for a given model.

    Maps common model types to their fastest equivalents.

    Args:
        model_id: The original model ID

    Returns:
        A suggested low-latency alternative, or None if no suggestion
    """
    model_lower = (model_id or "").lower()

    # Map model families to fast alternatives
    alternatives = {
        # For Claude/Anthropic models
        "claude": "groq/llama-3.3-70b-versatile",
        "anthropic": "groq/llama-3.3-70b-versatile",
        # For GPT models
        "gpt-4": "groq/llama-3.3-70b-versatile",
        "gpt-3.5": "groq/llama-3.1-8b-instant",
        "openai": "groq/llama-3.3-70b-versatile",
        # For reasoning models (slower by nature)
        "deepseek-r1": "fireworks/accounts/fireworks/models/deepseek-v3-0324",
        "o1": "groq/llama-3.3-70b-versatile",
        "o3": "groq/llama-3.3-70b-versatile",
        # For general chat
        "llama": "groq/llama-3.3-70b-versatile",
        "mistral": "groq/mixtral-8x7b-32768",
        "gemini": "google/gemini-2.0-flash-001",
        "gemma": "groq/gemma2-9b-it",
    }

    for pattern, alternative in alternatives.items():
        if pattern in model_lower:
            return alternative

    return None
