"""
Health Snapshots Service for Prompt Router.

Pre-computes healthy model lists and writes them to Redis as single keys.
Router does ONE read per request, not N awaits per model.

This is critical for meeting the < 2ms latency budget.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from src.config.redis_config import get_redis_client

logger = logging.getLogger(__name__)

# Redis key prefixes for router health snapshots
ROUTER_HEALTHY_MODELS_KEY = "router:healthy_models"
ROUTER_HEALTHY_SMALL_KEY = "router:healthy_models:small"
ROUTER_HEALTHY_MEDIUM_KEY = "router:healthy_models:medium"
ROUTER_HEALTH_TIMESTAMP_KEY = "router:health_timestamp"

# TTL for health snapshots (60 seconds, refreshed every 30 seconds)
HEALTH_SNAPSHOT_TTL = 60

# Model pools for each tier (cheap models for small tier)
SMALL_TIER_POOL = [
    # Primary cheap-fast options
    "openai/gpt-4o-mini",
    "anthropic/claude-3-haiku",
    "google/gemini-flash-1.5",
    "deepseek/deepseek-chat",
    "mistral/mistral-small",
    "meta-llama/llama-3.1-8b-instant",
    # ONE quality escape hatch (only selected if cheap options fail capability gate)
    "openai/gpt-4o",
]

MEDIUM_TIER_POOL = [
    # All small tier models
    *SMALL_TIER_POOL,
    # Additional balanced models
    "anthropic/claude-3.5-sonnet",
    "anthropic/claude-3-sonnet",
    "google/gemini-pro-1.5",
    "meta-llama/llama-3.1-70b",
    "meta-llama/llama-3.1-405b",
    "mistral/mistral-large",
    "cohere/command-r-plus",
    "deepseek/deepseek-coder",
]

# Cooldown period after failure (seconds)
FAILURE_COOLDOWN_SECONDS = 60


class HealthSnapshotService:
    """
    Service for managing pre-computed healthy model snapshots.

    Background monitor writes snapshots every 30 seconds.
    Router reads ONE key per request.
    """

    def __init__(self):
        self._redis = get_redis_client()
        # In-memory fallback if Redis unavailable
        self._fallback_cache: dict[str, list[str]] = {
            "small": SMALL_TIER_POOL.copy(),
            "medium": MEDIUM_TIER_POOL.copy(),
            "all": MEDIUM_TIER_POOL.copy(),
        }
        self._fallback_timestamp: datetime | None = None

    async def get_healthy_models(self, tier: str = "small") -> list[str]:
        """
        Get healthy models for a tier. Single Redis GET.

        Target latency: < 0.5ms

        Args:
            tier: "small", "medium", or "all"

        Returns:
            List of healthy model IDs
        """
        key = self._get_key_for_tier(tier)

        try:
            if self._redis:
                data = self._redis.get(key)
                if data:
                    return json.loads(data)
        except Exception as e:
            logger.warning(f"Redis read failed for {key}: {e}")

        # Fail open: return fallback pool
        logger.debug(f"Using fallback pool for tier {tier}")
        return self._fallback_cache.get(tier, SMALL_TIER_POOL.copy())

    def get_healthy_models_sync(self, tier: str = "small") -> list[str]:
        """
        Synchronous version for non-async contexts.
        Same as async version but without await.
        """
        key = self._get_key_for_tier(tier)

        try:
            if self._redis:
                data = self._redis.get(key)
                if data:
                    return json.loads(data)
        except Exception as e:
            logger.warning(f"Redis read failed for {key}: {e}")

        return self._fallback_cache.get(tier, SMALL_TIER_POOL.copy())

    def _get_key_for_tier(self, tier: str) -> str:
        """Get Redis key for a tier."""
        if tier == "small":
            return ROUTER_HEALTHY_SMALL_KEY
        elif tier == "medium":
            return ROUTER_HEALTHY_MEDIUM_KEY
        else:
            return ROUTER_HEALTHY_MODELS_KEY

    async def update_health_snapshot(
        self,
        model_health_data: dict[str, dict[str, Any]],
    ) -> None:
        """
        Update healthy model snapshots in Redis.

        Called by background monitor every 30 seconds.

        Args:
            model_health_data: Dict mapping model_id -> health info
                Expected format:
                {
                    "model_id": {
                        "health_score": 85.0,
                        "consecutive_failures": 0,
                        "last_failure_at": "2025-01-22T00:00:00Z" or None,
                        "last_updated": "2025-01-22T00:00:00Z"
                    }
                }
        """
        now = datetime.now(UTC)

        # Filter to healthy models
        all_healthy = []
        for model_id, health in model_health_data.items():
            if self._is_model_healthy(model_id, health, now):
                all_healthy.append(model_id)

        # Compute tier-specific lists
        small_healthy = [m for m in all_healthy if m in SMALL_TIER_POOL]
        medium_healthy = [m for m in all_healthy if m in MEDIUM_TIER_POOL]

        # Write to Redis (single pipeline for atomicity)
        try:
            if self._redis:
                pipe = self._redis.pipeline()
                pipe.setex(
                    ROUTER_HEALTHY_MODELS_KEY,
                    HEALTH_SNAPSHOT_TTL,
                    json.dumps(all_healthy),
                )
                pipe.setex(
                    ROUTER_HEALTHY_SMALL_KEY,
                    HEALTH_SNAPSHOT_TTL,
                    json.dumps(small_healthy),
                )
                pipe.setex(
                    ROUTER_HEALTHY_MEDIUM_KEY,
                    HEALTH_SNAPSHOT_TTL,
                    json.dumps(medium_healthy),
                )
                pipe.setex(
                    ROUTER_HEALTH_TIMESTAMP_KEY,
                    HEALTH_SNAPSHOT_TTL,
                    now.isoformat(),
                )
                pipe.execute()

                logger.debug(
                    f"Updated health snapshots: small={len(small_healthy)}, "
                    f"medium={len(medium_healthy)}, all={len(all_healthy)}"
                )
        except Exception as e:
            logger.error(f"Failed to update health snapshots: {e}")

        # Also update in-memory fallback
        self._fallback_cache["all"] = all_healthy
        self._fallback_cache["small"] = small_healthy
        self._fallback_cache["medium"] = medium_healthy
        self._fallback_timestamp = now

    def _is_model_healthy(
        self,
        model_id: str,
        health: dict[str, Any],
        now: datetime,
    ) -> bool:
        """
        Determine if a model should be considered healthy.

        Checks:
        1. Health score >= 50
        2. Consecutive failures < 3
        3. Not in cooldown period after recent failure
        4. Health data is not stale (< 5 minutes old)
        """
        # Check health score
        health_score = health.get("health_score", 0)
        if health_score < 50:
            return False

        # Check consecutive failures
        consecutive_failures = health.get("consecutive_failures", 0)
        if consecutive_failures >= 3:
            return False

        # Check cooldown
        last_failure = health.get("last_failure_at")
        if last_failure:
            if isinstance(last_failure, str):
                try:
                    last_failure = datetime.fromisoformat(last_failure.replace("Z", "+00:00"))
                except ValueError:
                    last_failure = None

            if last_failure:
                seconds_since_failure = (now - last_failure).total_seconds()
                if seconds_since_failure < FAILURE_COOLDOWN_SECONDS:
                    logger.debug(
                        f"Model {model_id} in cooldown ({seconds_since_failure:.0f}s since failure)"
                    )
                    return False

        # Check staleness
        last_updated = health.get("last_updated")
        if last_updated:
            if isinstance(last_updated, str):
                try:
                    last_updated = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
                except ValueError:
                    last_updated = None

            if last_updated:
                age_seconds = (now - last_updated).total_seconds()
                if age_seconds > 300:  # 5 minutes
                    logger.debug(f"Model {model_id} has stale health data ({age_seconds:.0f}s old)")
                    return False

        return True

    def update_health_snapshot_sync(
        self,
        model_health_data: dict[str, dict[str, Any]],
    ) -> None:
        """Synchronous version of update_health_snapshot."""
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're in an async context, create a task
                asyncio.create_task(self.update_health_snapshot(model_health_data))
            else:
                loop.run_until_complete(self.update_health_snapshot(model_health_data))
        except RuntimeError:
            # No event loop, run synchronously
            asyncio.run(self.update_health_snapshot(model_health_data))

    def get_snapshot_timestamp(self) -> datetime | None:
        """Get timestamp of last health snapshot update."""
        try:
            if self._redis:
                data = self._redis.get(ROUTER_HEALTH_TIMESTAMP_KEY)
                if data:
                    return datetime.fromisoformat(data.replace("Z", "+00:00"))
        except Exception as e:
            logger.warning(f"Failed to get snapshot timestamp: {e}")

        return self._fallback_timestamp

    def is_snapshot_fresh(self, max_age_seconds: int = 120) -> bool:
        """Check if health snapshot is fresh enough to use."""
        timestamp = self.get_snapshot_timestamp()
        if not timestamp:
            return False

        age = (datetime.now(UTC) - timestamp).total_seconds()
        return age < max_age_seconds


# Global instance
_health_snapshot_service: HealthSnapshotService | None = None


def get_health_snapshot_service() -> HealthSnapshotService:
    """Get global health snapshot service instance."""
    global _health_snapshot_service
    if _health_snapshot_service is None:
        _health_snapshot_service = HealthSnapshotService()
    return _health_snapshot_service


async def get_healthy_models(tier: str = "small") -> list[str]:
    """Convenience function to get healthy models."""
    service = get_health_snapshot_service()
    return await service.get_healthy_models(tier)


def get_healthy_models_sync(tier: str = "small") -> list[str]:
    """Convenience function to get healthy models (sync)."""
    service = get_health_snapshot_service()
    return service.get_healthy_models_sync(tier)
