"""Connection pool monitoring for database performance diagnostics.

This module provides utilities to monitor and diagnose connection pool issues
that may cause authentication timeouts.
"""

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class ConnectionPoolStats:
    """Statistics about connection pool usage."""

    def __init__(self):
        self.total_connections = 0
        self.active_connections = 0
        self.idle_connections = 0
        self.max_pool_size = 0
        self.connection_errors = 0
        self.connection_timeouts = 0
        self.last_checked = time.time()

    def to_dict(self) -> dict[str, Any]:
        """Convert stats to dictionary."""
        return {
            "total_connections": self.total_connections,
            "active_connections": self.active_connections,
            "idle_connections": self.idle_connections,
            "max_pool_size": self.max_pool_size,
            "connection_errors": self.connection_errors,
            "connection_timeouts": self.connection_timeouts,
            "last_checked": self.last_checked,
            "utilization_percent": (
                (self.active_connections / self.max_pool_size * 100)
                if self.max_pool_size > 0
                else 0
            ),
        }

    def is_healthy(self, warning_threshold: float = 0.8) -> bool:
        """Check if connection pool is healthy.

        Args:
            warning_threshold: Utilization percentage above which pool is considered stressed

        Returns:
            True if pool utilization is below threshold, False otherwise
        """
        if self.max_pool_size == 0:
            return True

        utilization = self.active_connections / self.max_pool_size
        return utilization < warning_threshold

    def get_health_status(self) -> str:
        """Get human-readable health status."""
        if self.max_pool_size == 0:
            return "UNKNOWN"

        utilization = self.active_connections / self.max_pool_size

        if utilization < 0.5:
            return "HEALTHY"
        elif utilization < 0.8:
            return "NORMAL"
        elif utilization < 0.95:
            return "WARNING"
        else:
            return "CRITICAL"


def get_supabase_pool_stats() -> ConnectionPoolStats | None:
    """Get connection pool statistics from Supabase client.

    Returns:
        ConnectionPoolStats if available, None if unable to retrieve
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # Supabase Python client doesn't directly expose pool stats,
        # but we can check the underlying httpx pool if available
        stats = ConnectionPoolStats()

        # Try to access the underlying HTTP client
        if hasattr(client, "_client") and hasattr(client._client, "_pool"):
            pool = client._client._pool
            if hasattr(pool, "_connections"):
                stats.total_connections = len(pool._connections)
            if hasattr(pool, "_active"):
                stats.active_connections = len(pool._active)
            if hasattr(pool, "_idle"):
                stats.idle_connections = len(pool._idle)
            if hasattr(pool, "_max_pool_size"):
                stats.max_pool_size = pool._max_pool_size

        logger.debug(f"Connection pool stats: {stats.to_dict()}")
        return stats

    except Exception as e:
        logger.warning(f"Failed to retrieve connection pool stats: {e}")
        return None


def log_pool_diagnostics():
    """Log diagnostic information about connection pool.

    This is useful for debugging connection pool issues.
    Also exports metrics to Prometheus for monitoring.
    """
    try:
        stats = get_supabase_pool_stats()
        if stats:
            logger.info(
                f"Connection pool diagnostics: "
                f"total={stats.total_connections}, "
                f"active={stats.active_connections}, "
                f"idle={stats.idle_connections}, "
                f"max={stats.max_pool_size}, "
                f"utilization={stats.to_dict()['utilization_percent']:.1f}%, "
                f"health={stats.get_health_status()}"
            )

            # Export to Prometheus metrics
            try:
                from src.services.prometheus_metrics import track_connection_pool_stats

                track_connection_pool_stats("supabase", stats.to_dict())
            except Exception as prom_error:
                logger.debug(
                    f"Failed to export connection pool metrics to Prometheus: {prom_error}"
                )

            if not stats.is_healthy():
                logger.warning("Connection pool is under stress")
        else:
            logger.debug("Unable to retrieve connection pool stats")
    except Exception as e:
        logger.error(f"Error logging pool diagnostics: {e}")


def check_pool_health_and_warn() -> bool:
    """Check connection pool health and log warnings if stressed.

    Returns:
        True if pool is healthy, False if under stress
    """
    try:
        stats = get_supabase_pool_stats()
        if not stats:
            return True

        if not stats.is_healthy():
            logger.warning(
                f"Connection pool is stressed: "
                f"utilization={stats.to_dict()['utilization_percent']:.1f}%, "
                f"status={stats.get_health_status()}"
            )
            return False

        return True
    except Exception as e:
        logger.error(f"Error checking pool health: {e}")
        return True


async def periodic_pool_health_check(check_interval_seconds: int = 60):
    """Periodically check pool health (for use in lifespan startup).

    Args:
        check_interval_seconds: How often to check pool health
    """
    import asyncio

    try:
        while True:
            await asyncio.sleep(check_interval_seconds)
            log_pool_diagnostics()
            check_pool_health_and_warn()
    except asyncio.CancelledError:
        logger.info("Pool health check task cancelled")
    except Exception as e:
        logger.error(f"Error in periodic pool health check: {e}")
