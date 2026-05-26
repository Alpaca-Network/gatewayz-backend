"""
Gatewayz Health Monitoring Service — one-shot cron entrypoint.

Previously ran as an always-on FastAPI/Railway container that drove the
intelligent health monitor's background loops. Now runs as a single tiered
health-check pass and exits, intended to be invoked by GitHub Actions on a
30-minute cron (see .github/workflows/health-monitor.yml).

Each invocation:
  1. Loads config and Redis.
  2. Asks the IntelligentHealthMonitor for the next batch of models due for
     checking (priority + Redis-lock filtered).
  3. Runs concurrent health checks bounded by the monitor's semaphore.
  4. Persists results and publishes the fresh snapshot to Redis.
  5. Exits.

The monitoring loop, tier-update loop, metric-aggregation loop, and
incident-resolution loop that the always-on service used to run continuously
are not started here — they can be scheduled separately if needed.
"""

import asyncio
import logging
import os
import sys

# Add parent directory to path to import shared modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config import Config
from src.config.logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)


async def run_once() -> int:
    """Run a single tiered health-check pass and exit.

    Returns process exit code (0 success, 1 on fatal error).
    """
    logger.info("=" * 60)
    logger.info("Gatewayz Health Monitor — one-shot run")
    logger.info("=" * 60)

    is_valid, missing_vars = Config.validate_critical_env_vars()
    if not is_valid:
        logger.warning(f"Missing env vars (continuing): {missing_vars}")

    try:
        from src.config.redis_config import get_redis_client

        if get_redis_client() is None:
            logger.warning("Redis not available — proceeding without coordination")
    except Exception as e:
        logger.warning(f"Redis init warning: {e}")

    try:
        from src.services.monitoring.intelligent_health_monitor import (
            intelligent_health_monitor,
        )
    except Exception as e:
        logger.error(f"Failed to import intelligent_health_monitor: {e}", exc_info=True)
        return 1

    monitor = intelligent_health_monitor

    try:
        models = await monitor._get_models_for_checking()
    except Exception as e:
        logger.error(f"Failed to fetch models for checking: {e}", exc_info=True)
        return 1

    if not models:
        logger.info("No models due for checking — publishing cache snapshot and exiting")
        try:
            await monitor._publish_health_to_cache()
        except Exception as e:
            logger.warning(f"Cache publish warning: {e}")
        return 0

    logger.info(f"Checking {len(models)} models in one pass")

    results = await asyncio.gather(
        *[monitor._check_model_health_with_limit(m) for m in models],
        return_exceptions=True,
    )

    for model, result in zip(models, results, strict=False):
        if isinstance(result, Exception):
            logger.error(f"Health check failed for {model.get('model')}: {result}")
            continue
        if result:
            try:
                await monitor._process_health_check_result(result)
            except Exception as e:
                logger.error(
                    f"Failed to process result for {model.get('model')}: {e}",
                    exc_info=True,
                )

    try:
        await monitor._publish_health_to_cache()
    except Exception as e:
        logger.warning(f"Cache publish warning: {e}")

    try:
        from src.config.supabase_config import cleanup_supabase_client

        cleanup_supabase_client()
    except Exception as e:
        logger.warning(f"Supabase cleanup warning: {e}")

    logger.info("Health Monitor one-shot run complete")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run_once()))
