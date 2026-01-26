"""
Pricing Sync Cleanup Service

Automatically cleans up stuck pricing sync records that failed to update their status.
This prevents database pollution from syncs that crashed or timed out.

This should run as a scheduled job (e.g., every 15 minutes).
"""

import logging
from datetime import datetime, timedelta, timezone

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)


async def cleanup_stuck_syncs(timeout_minutes: int = 10) -> dict:
    """
    Find and mark stuck syncs as failed.

    A sync is considered "stuck" if:
    - Status is 'in_progress' or 'queued'
    - Started more than timeout_minutes ago
    - No completion timestamp

    Args:
        timeout_minutes: How many minutes before considering a sync stuck

    Returns:
        Dict with cleanup stats: {stuck_syncs_found: int, syncs_cleaned: int}
    """
    supabase = get_supabase_client()

    try:
        # Calculate cutoff time
        cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)
        cutoff_str = cutoff_time.isoformat()

        logger.info(f"Looking for stuck syncs (started before {cutoff_str})")

        # Find stuck syncs (jobs that are queued/running for too long)
        response = (
            supabase.table('pricing_sync_jobs')
            .select('id, job_id, triggered_at, triggered_by')
            .in_('status', ['queued', 'running'])
            .lt('triggered_at', cutoff_str)
            .is_('completed_at', 'null')
            .execute()
        )

        stuck_syncs = response.data

        if not stuck_syncs:
            logger.info("✅ No stuck syncs found")
            return {'stuck_syncs_found': 0, 'syncs_cleaned': 0}

        logger.warning(f"Found {len(stuck_syncs)} stuck syncs")

        # Mark each as failed
        cleaned_count = 0
        for sync in stuck_syncs:
            try:
                logger.warning(
                    f"Cleaning stuck sync: job_id={sync['job_id']}, "
                    f"triggered_at={sync['triggered_at']}, "
                    f"triggered_by={sync.get('triggered_by', 'unknown')}"
                )

                supabase.table('pricing_sync_jobs').update({
                    'status': 'failed',
                    'completed_at': datetime.now(timezone.utc).isoformat(),
                    'error_message': f'Sync timeout - auto-cleaned after {timeout_minutes} minutes'
                }).eq('job_id', sync['job_id']).execute()

                cleaned_count += 1

            except Exception as e:
                logger.error(f"Failed to clean stuck sync {sync['job_id']}: {e}")

        logger.info(f"✅ Cleaned {cleaned_count}/{len(stuck_syncs)} stuck syncs")

        return {
            'stuck_syncs_found': len(stuck_syncs),
            'syncs_cleaned': cleaned_count
        }

    except Exception as e:
        logger.error(f"Error during stuck sync cleanup: {e}", exc_info=True)
        return {
            'stuck_syncs_found': 0,
            'syncs_cleaned': 0,
            'error': str(e)
        }


async def run_cleanup_job():
    """
    Run cleanup job (can be called from scheduler or cron).

    Returns:
        Cleanup result dict
    """
    logger.info("🧹 Running pricing sync cleanup job")
    result = await cleanup_stuck_syncs(timeout_minutes=10)
    logger.info(f"Cleanup complete: {result}")
    return result


# CLI entry point for manual testing/cron
if __name__ == "__main__":
    import asyncio

    async def main():
        print("Running stuck sync cleanup...")
        result = await cleanup_stuck_syncs(timeout_minutes=5)
        print(f"Result: {result}")

    asyncio.run(main())
