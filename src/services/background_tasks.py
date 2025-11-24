"""
Background task management for non-blocking operations
Handles activity logging and other I/O operations in the background
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from src.db.activity import log_activity as db_log_activity

logger = logging.getLogger(__name__)

# Queue for background tasks
_background_tasks = []
_task_lock = asyncio.Lock() if hasattr(asyncio, 'Lock') else None


async def log_activity_async(
    user_id: int,
    model: str,
    provider: str,
    tokens: int,
    cost: float,
    speed: float = 0.0,
    finish_reason: str = "stop",
    app: str = "API",
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Log activity asynchronously in the background
    Non-blocking activity logging to improve session creation performance

    Args:
        user_id: User ID
        model: Model name
        provider: Provider name
        tokens: Tokens used
        cost: Cost in dollars
        speed: Tokens per second
        finish_reason: Completion reason
        app: Application name
        metadata: Additional metadata
    """
    try:
        # Run database operation in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            db_log_activity,
            user_id,
            model,
            provider,
            tokens,
            cost,
            speed,
            finish_reason,
            app,
            metadata,
        )
    except Exception as e:
        logger.error(
            f"Failed to log activity in background for user {user_id}: {e}",
            exc_info=True,
        )
        # Don't raise - background tasks should not affect main flow


def log_activity_background(
    user_id: int,
    model: str,
    provider: str,
    tokens: int,
    cost: float,
    speed: float = 0.0,
    finish_reason: str = "stop",
    app: str = "API",
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Queue activity logging as a background task (non-awaitable)

    This is useful when you can't use async/await and need to fire-and-forget
    activity logging. Use this in synchronous contexts.

    Args:
        user_id: User ID
        model: Model name
        provider: Provider name
        tokens: Tokens used
        cost: Cost in dollars
        speed: Tokens per second
        finish_reason: Completion reason
        app: Application name
        metadata: Additional metadata
    """
    try:
        # Try to create a task if in async context
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Schedule the coroutine as a task
                task = loop.create_task(
                    log_activity_async(
                        user_id=user_id,
                        model=model,
                        provider=provider,
                        tokens=tokens,
                        cost=cost,
                        speed=speed,
                        finish_reason=finish_reason,
                        app=app,
                        metadata=metadata,
                    )
                )
                logger.debug(f"Queued background activity logging task for user {user_id}")
                return
        except RuntimeError:
            # No event loop running, fall through to sync call
            pass

        # Fall back to synchronous logging (will block slightly)
        db_log_activity(
            user_id=user_id,
            model=model,
            provider=provider,
            tokens=tokens,
            cost=cost,
            speed=speed,
            finish_reason=finish_reason,
            app=app,
            metadata=metadata,
        )

    except Exception as e:
        logger.error(
            f"Failed to queue background activity logging for user {user_id}: {e}",
            exc_info=True,
        )


def get_pending_tasks_count() -> int:
    """Get count of pending background tasks (for monitoring)"""
    try:
        loop = asyncio.get_event_loop()
        tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
        return len(tasks)
    except Exception:
        return 0
