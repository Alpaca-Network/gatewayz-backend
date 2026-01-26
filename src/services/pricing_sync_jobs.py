"""
Pricing Sync Background Job Management

Tracks and manages async pricing sync background jobs.
Provides job creation, status updates, and querying.

Usage:
    # Create new job
    job = await create_pricing_sync_job(triggered_by="admin@example.com")

    # Update job status
    await update_job_status(job_id, "running")

    # Get job status
    status = await get_job_status(job_id)

    # Complete job with results
    await complete_job(job_id, results)
"""

import uuid
import json
from datetime import datetime
from typing import Optional, Dict, Any, List
import logging

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)


class PricingSyncJobError(Exception):
    """Base exception for pricing sync job errors"""
    pass


class JobNotFoundError(PricingSyncJobError):
    """Raised when job is not found"""
    pass


async def create_pricing_sync_job(triggered_by: str) -> str:
    """
    Create a new pricing sync background job.

    Args:
        triggered_by: User email or identifier who triggered the job

    Returns:
        job_id: Unique job identifier (UUID string)

    Raises:
        PricingSyncJobError: If job creation fails
    """
    try:
        supabase = get_supabase_client()
        job_id = str(uuid.uuid4())

        job_data = {
            "job_id": job_id,
            "status": "queued",
            "triggered_by": triggered_by,
            "triggered_at": datetime.utcnow().isoformat()
        }

        result = supabase.table("pricing_sync_jobs").insert(job_data).execute()

        if result.data:
            logger.info(f"Created pricing sync job: {job_id} (triggered by: {triggered_by})")
            return job_id

        raise PricingSyncJobError("Failed to create job - no data returned")

    except Exception as e:
        logger.error(f"Error creating pricing sync job: {str(e)}")
        raise PricingSyncJobError(f"Failed to create job: {str(e)}")


async def update_job_status(
    job_id: str,
    status: str,
    error_message: Optional[str] = None
) -> bool:
    """
    Update job status.

    Args:
        job_id: Job identifier
        status: New status (queued, running, completed, failed)
        error_message: Optional error message for failed jobs

    Returns:
        True if updated successfully

    Raises:
        JobNotFoundError: If job not found
    """
    try:
        supabase = get_supabase_client()

        update_data = {"status": status}

        # Set timestamps based on status
        if status == "running":
            update_data["started_at"] = datetime.utcnow().isoformat()
        elif status in ("completed", "failed"):
            update_data["completed_at"] = datetime.utcnow().isoformat()

        if error_message:
            update_data["error_message"] = error_message

        result = supabase.table("pricing_sync_jobs").update(
            update_data
        ).eq("job_id", job_id).execute()

        if not result.data:
            raise JobNotFoundError(f"Job not found: {job_id}")

        logger.info(f"Updated job {job_id} status to: {status}")
        return True

    except JobNotFoundError:
        raise
    except Exception as e:
        logger.error(f"Error updating job status: {str(e)}")
        return False


async def complete_job(
    job_id: str,
    results: Dict[str, Any],
    success: bool = True
) -> bool:
    """
    Mark job as completed and store results.

    Args:
        job_id: Job identifier
        results: Sync results dictionary
        success: Whether job succeeded

    Returns:
        True if updated successfully
    """
    try:
        supabase = get_supabase_client()

        status = "completed" if success else "failed"

        update_data = {
            "status": status,
            "completed_at": datetime.utcnow().isoformat(),
            "providers_synced": results.get("providers_synced", 0),
            "models_updated": results.get("total_models_updated", 0),
            "models_skipped": results.get("total_models_skipped", 0),
            "total_errors": results.get("total_errors", 0),
            "result_data": json.dumps(results)
        }

        if not success:
            update_data["error_message"] = results.get("error_message", "Unknown error")

        result = supabase.table("pricing_sync_jobs").update(
            update_data
        ).eq("job_id", job_id).execute()

        if not result.data:
            raise JobNotFoundError(f"Job not found: {job_id}")

        logger.info(
            f"Completed job {job_id}: {status}, "
            f"{results.get('total_models_updated', 0)} models updated"
        )
        return True

    except JobNotFoundError:
        raise
    except Exception as e:
        logger.error(f"Error completing job: {str(e)}")
        return False


async def get_job_status(job_id: str) -> Dict[str, Any]:
    """
    Get current job status and details.

    Args:
        job_id: Job identifier

    Returns:
        Job status dictionary

    Raises:
        JobNotFoundError: If job not found
    """
    try:
        supabase = get_supabase_client()

        result = supabase.table("pricing_sync_jobs").select("*").eq(
            "job_id", job_id
        ).execute()

        if not result.data:
            raise JobNotFoundError(f"Job not found: {job_id}")

        job = result.data[0]

        # Parse result_data if available
        if job.get("result_data"):
            try:
                job["result_data"] = json.loads(job["result_data"])
            except json.JSONDecodeError:
                pass

        return {
            "job_id": job["job_id"],
            "status": job["status"],
            "triggered_by": job.get("triggered_by"),
            "triggered_at": job.get("triggered_at"),
            "started_at": job.get("started_at"),
            "completed_at": job.get("completed_at"),
            "duration_seconds": float(job["duration_seconds"]) if job.get("duration_seconds") else None,
            "providers_synced": job.get("providers_synced", 0),
            "models_updated": job.get("models_updated", 0),
            "models_skipped": job.get("models_skipped", 0),
            "total_errors": job.get("total_errors", 0),
            "error_message": job.get("error_message"),
            "result_data": job.get("result_data")
        }

    except JobNotFoundError:
        raise
    except Exception as e:
        logger.error(f"Error getting job status: {str(e)}")
        raise PricingSyncJobError(f"Failed to get job status: {str(e)}")


async def list_recent_jobs(limit: int = 50, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    List recent pricing sync jobs.

    Args:
        limit: Maximum number of jobs to return
        status: Optional status filter (queued, running, completed, failed)

    Returns:
        List of job status dictionaries
    """
    try:
        supabase = get_supabase_client()

        query = supabase.table("pricing_sync_jobs").select("*")

        if status:
            query = query.eq("status", status)

        result = query.order("triggered_at", desc=True).limit(limit).execute()

        jobs = []
        for job in result.data:
            # Parse result_data if available
            if job.get("result_data"):
                try:
                    job["result_data"] = json.loads(job["result_data"])
                except json.JSONDecodeError:
                    pass

            jobs.append({
                "job_id": job["job_id"],
                "status": job["status"],
                "triggered_by": job.get("triggered_by"),
                "triggered_at": job.get("triggered_at"),
                "duration_seconds": float(job["duration_seconds"]) if job.get("duration_seconds") else None,
                "models_updated": job.get("models_updated", 0),
                "total_errors": job.get("total_errors", 0)
            })

        return jobs

    except Exception as e:
        logger.error(f"Error listing jobs: {str(e)}")
        return []


async def cleanup_old_jobs() -> int:
    """
    Clean up jobs older than 30 days.

    Returns:
        Number of jobs deleted
    """
    try:
        supabase = get_supabase_client()
        result = supabase.rpc("cleanup_old_pricing_jobs").execute()

        if result.data:
            logger.info(f"Cleaned up {result.data} old pricing sync jobs")
            return result.data

        return 0

    except Exception as e:
        logger.warning(f"Failed to cleanup old jobs: {str(e)}")
        return 0


async def get_active_jobs() -> List[Dict[str, Any]]:
    """
    Get all currently active (queued or running) jobs.

    Returns:
        List of active jobs
    """
    try:
        supabase = get_supabase_client()

        result = supabase.table("pricing_sync_jobs").select("*").in_(
            "status", ["queued", "running"]
        ).order("triggered_at", desc=False).execute()

        jobs = []
        for job in result.data:
            jobs.append({
                "job_id": job["job_id"],
                "status": job["status"],
                "triggered_by": job.get("triggered_by"),
                "triggered_at": job.get("triggered_at"),
                "started_at": job.get("started_at")
            })

        return jobs

    except Exception as e:
        logger.error(f"Error getting active jobs: {str(e)}")
        return []
