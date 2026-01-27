"""
Pricing Sync Distributed Lock Service

Provides distributed locking mechanism to prevent concurrent pricing sync operations.
Uses database-backed locks with automatic expiry for reliability.

Usage:
    async with PricingSyncLock(lock_id="global_sync", timeout=300):
        # Perform pricing sync
        await sync_pricing()
"""

import uuid
from datetime import datetime, timedelta
from typing import Optional
from contextlib import asynccontextmanager

from src.config.supabase_config import get_supabase_client
from fastapi import HTTPException
import logging

logger = logging.getLogger(__name__)


class PricingSyncLockError(Exception):
    """Base exception for pricing sync lock errors"""
    pass


class LockAcquisitionError(PricingSyncLockError):
    """Raised when lock cannot be acquired"""
    pass


class PricingSyncLock:
    """
    Distributed lock for pricing sync operations.

    Prevents concurrent pricing syncs that would cause 502/504 errors.
    Uses database-backed locks with automatic expiry.
    """

    def __init__(
        self,
        lock_key: str = "pricing_sync_global",
        timeout_seconds: int = 300,  # 5 minutes default
        request_id: Optional[str] = None
    ):
        """
        Initialize pricing sync lock.

        Args:
            lock_key: Unique identifier for the lock
            timeout_seconds: How long the lock is valid (auto-expires)
            request_id: Identifier for who acquired the lock
        """
        self.lock_key = lock_key
        self.timeout_seconds = timeout_seconds
        self.request_id = request_id or f"request_{uuid.uuid4().hex[:8]}"
        self.lock_id: Optional[int] = None
        self.supabase = get_supabase_client()

    async def acquire(self) -> bool:
        """
        Attempt to acquire the distributed lock.

        Returns:
            True if lock acquired, False otherwise

        Raises:
            LockAcquisitionError: If lock is held by another process
        """
        try:
            # Clean up any expired locks first
            await self._cleanup_expired_locks()

            # Check if lock already exists
            existing_lock = self.supabase.table("pricing_sync_lock").select("*").eq(
                "lock_key", self.lock_key
            ).execute()

            if existing_lock.data:
                lock = existing_lock.data[0]
                locked_by = lock.get("locked_by")
                expires_at = lock.get("expires_at")

                # If lock exists and hasn't expired, raise error
                if expires_at:
                    expiry_time = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                    if expiry_time > datetime.now(expiry_time.tzinfo):
                        logger.warning(
                            f"Pricing sync already in progress. "
                            f"Locked by: {locked_by}, expires: {expires_at}"
                        )
                        raise LockAcquisitionError(
                            f"Pricing sync already in progress (locked by {locked_by}). "
                            f"Please wait for current sync to complete."
                        )

            # Acquire the lock
            expires_at = datetime.utcnow() + timedelta(seconds=self.timeout_seconds)

            lock_data = {
                "lock_key": self.lock_key,
                "locked_by": self.request_id,
                "locked_at": datetime.utcnow().isoformat(),
                "expires_at": expires_at.isoformat()
            }

            result = self.supabase.table("pricing_sync_lock").upsert(
                lock_data,
                on_conflict="lock_key"
            ).execute()

            if result.data:
                self.lock_id = result.data[0].get("id")
                logger.info(
                    f"Acquired pricing sync lock: {self.lock_key} "
                    f"(id: {self.lock_id}, expires in {self.timeout_seconds}s)"
                )
                return True

            return False

        except LockAcquisitionError:
            raise
        except Exception as e:
            logger.error(f"Error acquiring pricing sync lock: {str(e)}")
            raise PricingSyncLockError(f"Failed to acquire lock: {str(e)}")

    async def release(self) -> bool:
        """
        Release the distributed lock.

        Returns:
            True if lock released successfully
        """
        try:
            if not self.lock_id:
                logger.warning("Attempted to release lock without lock_id")
                return False

            # Delete the lock
            result = self.supabase.table("pricing_sync_lock").delete().eq(
                "id", self.lock_id
            ).eq(
                "locked_by", self.request_id
            ).execute()

            if result.data:
                logger.info(f"Released pricing sync lock: {self.lock_key} (id: {self.lock_id})")
                self.lock_id = None
                return True

            logger.warning(f"Lock {self.lock_id} not found or already released")
            return False

        except Exception as e:
            logger.error(f"Error releasing pricing sync lock: {str(e)}")
            # Don't raise - lock will expire automatically
            return False

    async def _cleanup_expired_locks(self):
        """Clean up expired locks to prevent stale lock buildup"""
        try:
            result = self.supabase.rpc("cleanup_expired_pricing_locks").execute()
            if result.data and result.data > 0:
                logger.info(f"Cleaned up {result.data} expired pricing sync locks")
        except Exception as e:
            logger.warning(f"Failed to cleanup expired locks: {str(e)}")
            # Non-critical, continue

    async def __aenter__(self):
        """Context manager entry - acquire lock"""
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - release lock"""
        await self.release()
        return False


@asynccontextmanager
async def pricing_sync_lock(
    lock_key: str = "pricing_sync_global",
    timeout_seconds: int = 300,
    request_id: Optional[str] = None
):
    """
    Context manager for pricing sync distributed lock.

    Usage:
        async with pricing_sync_lock():
            # Perform pricing sync
            await sync_pricing()

    Args:
        lock_key: Unique identifier for the lock
        timeout_seconds: Lock timeout in seconds
        request_id: Request identifier

    Raises:
        LockAcquisitionError: If lock cannot be acquired (sync already in progress)
    """
    lock = PricingSyncLock(
        lock_key=lock_key,
        timeout_seconds=timeout_seconds,
        request_id=request_id
    )

    try:
        await lock.acquire()
        yield lock
    finally:
        await lock.release()


async def is_pricing_sync_in_progress(lock_key: str = "pricing_sync_global") -> bool:
    """
    Check if a pricing sync is currently in progress.

    Args:
        lock_key: Lock identifier to check

    Returns:
        True if sync is in progress, False otherwise
    """
    try:
        supabase = get_supabase_client()

        # Clean up expired locks
        await PricingSyncLock(lock_key)._cleanup_expired_locks()

        result = supabase.table("pricing_sync_lock").select("*").eq(
            "lock_key", lock_key
        ).execute()

        if not result.data:
            return False

        lock = result.data[0]
        expires_at = lock.get("expires_at")

        if expires_at:
            expiry_time = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            return expiry_time > datetime.now(expiry_time.tzinfo)

        return False

    except Exception as e:
        logger.error(f"Error checking sync lock status: {str(e)}")
        return False


async def get_current_sync_lock_info(lock_key: str = "pricing_sync_global") -> Optional[dict]:
    """
    Get information about the current sync lock.

    Args:
        lock_key: Lock identifier to query

    Returns:
        Dict with lock info or None if no active lock
    """
    try:
        supabase = get_supabase_client()

        result = supabase.table("pricing_sync_lock").select("*").eq(
            "lock_key", lock_key
        ).execute()

        if not result.data:
            return None

        lock = result.data[0]
        expires_at = lock.get("expires_at")

        if expires_at:
            expiry_time = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            if expiry_time <= datetime.now(expiry_time.tzinfo):
                # Lock expired
                return None

        return {
            "lock_key": lock.get("lock_key"),
            "locked_by": lock.get("locked_by"),
            "locked_at": lock.get("locked_at"),
            "expires_at": lock.get("expires_at"),
            "is_active": True
        }

    except Exception as e:
        logger.error(f"Error getting sync lock info: {str(e)}")
        return None
