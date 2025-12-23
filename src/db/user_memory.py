"""
Database operations for user memories (cross-session AI memory).

This module provides CRUD operations for storing and retrieving user facts
and preferences extracted from chat conversations. These memories are used
to provide personalized context in future chat sessions.
"""

import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, TypeVar

from httpx import RemoteProtocolError, ConnectError, ReadTimeout
from src.config.supabase_config import get_supabase_client
from src.utils.retry import with_retry

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Valid memory categories
MEMORY_CATEGORIES = frozenset([
    "preference",   # User preferences (e.g., "prefers TypeScript")
    "context",      # Professional/personal context (e.g., "works at startup")
    "instruction",  # Explicit instructions (e.g., "always explain step by step")
    "fact",         # Factual information (e.g., "uses PostgreSQL")
    "name",         # Names mentioned (e.g., "my name is Alex")
    "project",      # Project details (e.g., "building e-commerce app")
    "general",      # General information
])


def _execute_with_connection_retry(
    operation: Callable[[], T],
    operation_name: str,
    max_retries: int = 3,
    initial_delay: float = 0.1,
) -> T:
    """
    Execute a Supabase operation with retry logic for transient connection errors.

    Handles HTTP/2 connection resets, server disconnects, and other transient network issues
    that can occur when reusing connections in high-concurrency scenarios.
    """
    last_exception = None
    delay = initial_delay

    for attempt in range(max_retries + 1):
        try:
            return operation()
        except (RemoteProtocolError, ConnectError, ReadTimeout) as e:
            last_exception = e
            error_type = type(e).__name__

            if attempt < max_retries:
                logger.warning(
                    f"{operation_name} failed with {error_type}: {e}. "
                    f"Retrying in {delay:.2f}s (attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(delay)
                delay *= 2  # Exponential backoff
            else:
                logger.error(
                    f"{operation_name} failed after {max_retries} retries with {error_type}: {e}"
                )
        except Exception as e:
            logger.error(f"{operation_name} failed with non-retryable error: {e}")
            raise

    if last_exception:
        raise last_exception
    raise RuntimeError(f"{operation_name} failed without exception details")


@with_retry(
    max_attempts=3,
    initial_delay=0.1,
    max_delay=2.0,
    exceptions=(Exception,)
)
def create_user_memory(
    user_id: int,
    category: str,
    content: str,
    source_session_id: int = None,
    confidence: float = 0.80,
) -> dict[str, Any]:
    """
    Create a new user memory entry.

    Args:
        user_id: The user's ID
        category: Memory category (preference, context, instruction, fact, name, project, general)
        content: The memory content text
        source_session_id: Optional ID of the chat session this memory was extracted from
        confidence: Confidence score 0.0-1.0 (default: 0.80)

    Returns:
        The created memory dict

    Raises:
        ValueError: If category is invalid
        RuntimeError: If creation fails
    """
    if category not in MEMORY_CATEGORIES:
        raise ValueError(f"Invalid category '{category}'. Must be one of: {MEMORY_CATEGORIES}")

    if not content or not content.strip():
        raise ValueError("Memory content cannot be empty")

    confidence = max(0.0, min(1.0, confidence))  # Clamp to 0.0-1.0

    try:
        client = get_supabase_client()

        memory_data = {
            "user_id": user_id,
            "category": category,
            "content": content.strip(),
            "source_session_id": source_session_id,
            "confidence": confidence,
            "is_active": True,
            "access_count": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        def insert_memory():
            return client.table("user_memories").insert(memory_data).execute()

        result = _execute_with_connection_retry(
            insert_memory,
            f"create_user_memory(user={user_id}, category={category})"
        )

        if not result.data:
            raise ValueError("Failed to create user memory")

        memory = result.data[0]
        logger.info(f"Created memory {memory['id']} for user {user_id}: {category}")
        return memory

    except ValueError:
        raise
    except Exception as e:
        logger.error(f"Failed to create user memory: {e}")
        raise RuntimeError(f"Failed to create user memory: {e}") from e


def get_user_memories(
    user_id: int,
    category: str = None,
    limit: int = 20,
    offset: int = 0,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    """
    Get user memories, optionally filtered by category.

    Args:
        user_id: The user's ID
        category: Optional category filter
        limit: Maximum number of memories to return (default: 20)
        offset: Offset for pagination (default: 0)
        active_only: If True, only return active memories (default: True)

    Returns:
        List of memory dicts, ordered by last_accessed_at (most recent first),
        then by created_at (most recent first)
    """
    try:
        client = get_supabase_client()

        def query_memories():
            query = (
                client.table("user_memories")
                .select("*")
                .eq("user_id", user_id)
            )

            if active_only:
                query = query.eq("is_active", True)

            if category:
                if category not in MEMORY_CATEGORIES:
                    raise ValueError(f"Invalid category '{category}'")
                query = query.eq("category", category)

            # Order by access time (most used first), then creation time
            query = (
                query
                .order("last_accessed_at", desc=True, nullsfirst=False)
                .order("created_at", desc=True)
                .range(offset, offset + limit - 1)
            )

            return query.execute()

        result = _execute_with_connection_retry(
            query_memories,
            f"get_user_memories(user={user_id}, category={category})"
        )

        memories = result.data or []
        logger.info(f"Retrieved {len(memories)} memories for user {user_id}")
        return memories

    except ValueError:
        raise
    except Exception as e:
        logger.error(f"Failed to get user memories: {e}")
        raise RuntimeError(f"Failed to get user memories: {e}") from e


def get_memory_by_id(memory_id: int, user_id: int) -> dict[str, Any] | None:
    """
    Get a specific memory by ID.

    Args:
        memory_id: The memory ID
        user_id: The user's ID (for ownership verification)

    Returns:
        The memory dict, or None if not found
    """
    try:
        client = get_supabase_client()

        def query_memory():
            return (
                client.table("user_memories")
                .select("*")
                .eq("id", memory_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = _execute_with_connection_retry(
            query_memory,
            f"get_memory_by_id(memory={memory_id}, user={user_id})"
        )

        if not result.data:
            return None

        return result.data[0]

    except Exception as e:
        logger.error(f"Failed to get memory {memory_id}: {e}")
        raise RuntimeError(f"Failed to get memory: {e}") from e


@with_retry(
    max_attempts=3,
    initial_delay=0.1,
    max_delay=2.0,
    exceptions=(Exception,)
)
def update_memory_access(memory_id: int) -> bool:
    """
    Update access count and last_accessed_at for a memory.
    Called when a memory is used in chat context.

    Args:
        memory_id: The memory ID

    Returns:
        True if updated successfully, False otherwise
    """
    try:
        client = get_supabase_client()

        # First get current access_count
        def get_current():
            return (
                client.table("user_memories")
                .select("access_count")
                .eq("id", memory_id)
                .execute()
            )

        current_result = _execute_with_connection_retry(
            get_current,
            f"get_memory_access_count(memory={memory_id})"
        )

        if not current_result.data:
            return False

        current_count = current_result.data[0].get("access_count", 0) or 0

        def update_access():
            return (
                client.table("user_memories")
                .update({
                    "access_count": current_count + 1,
                    "last_accessed_at": datetime.now(timezone.utc).isoformat(),
                })
                .eq("id", memory_id)
                .execute()
            )

        result = _execute_with_connection_retry(
            update_access,
            f"update_memory_access(memory={memory_id})"
        )

        return bool(result.data)

    except Exception as e:
        logger.warning(f"Failed to update memory access: {e}")
        return False


@with_retry(
    max_attempts=3,
    initial_delay=0.1,
    max_delay=2.0,
    exceptions=(Exception,)
)
def delete_user_memory(memory_id: int, user_id: int) -> bool:
    """
    Soft delete a memory (set is_active=False).

    Args:
        memory_id: The memory ID
        user_id: The user's ID (for ownership verification)

    Returns:
        True if deleted successfully, False otherwise
    """
    try:
        client = get_supabase_client()

        def soft_delete():
            return (
                client.table("user_memories")
                .update({
                    "is_active": False,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                })
                .eq("id", memory_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = _execute_with_connection_retry(
            soft_delete,
            f"delete_user_memory(memory={memory_id}, user={user_id})"
        )

        if not result.data:
            logger.warning(f"Failed to delete memory {memory_id}")
            return False

        logger.info(f"Soft deleted memory {memory_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to delete user memory: {e}")
        raise RuntimeError(f"Failed to delete user memory: {e}") from e


@with_retry(
    max_attempts=3,
    initial_delay=0.1,
    max_delay=2.0,
    exceptions=(Exception,)
)
def hard_delete_user_memory(memory_id: int, user_id: int) -> bool:
    """
    Permanently delete a memory from the database.

    Args:
        memory_id: The memory ID
        user_id: The user's ID (for ownership verification)

    Returns:
        True if deleted successfully, False otherwise
    """
    try:
        client = get_supabase_client()

        def hard_delete():
            return (
                client.table("user_memories")
                .delete()
                .eq("id", memory_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = _execute_with_connection_retry(
            hard_delete,
            f"hard_delete_user_memory(memory={memory_id}, user={user_id})"
        )

        if not result.data:
            logger.warning(f"Failed to hard delete memory {memory_id}")
            return False

        logger.info(f"Hard deleted memory {memory_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to hard delete user memory: {e}")
        raise RuntimeError(f"Failed to hard delete user memory: {e}") from e


@with_retry(
    max_attempts=3,
    initial_delay=0.1,
    max_delay=2.0,
    exceptions=(Exception,)
)
def delete_all_user_memories(user_id: int, hard_delete: bool = False) -> int:
    """
    Delete all memories for a user.

    Args:
        user_id: The user's ID
        hard_delete: If True, permanently delete; otherwise soft delete

    Returns:
        Number of memories deleted
    """
    try:
        client = get_supabase_client()

        if hard_delete:
            def delete_all():
                return (
                    client.table("user_memories")
                    .delete()
                    .eq("user_id", user_id)
                    .execute()
                )
        else:
            def delete_all():
                return (
                    client.table("user_memories")
                    .update({
                        "is_active": False,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    })
                    .eq("user_id", user_id)
                    .eq("is_active", True)
                    .execute()
                )

        result = _execute_with_connection_retry(
            delete_all,
            f"delete_all_user_memories(user={user_id}, hard={hard_delete})"
        )

        deleted_count = len(result.data) if result.data else 0
        logger.info(f"Deleted {deleted_count} memories for user {user_id} (hard={hard_delete})")
        return deleted_count

    except Exception as e:
        logger.error(f"Failed to delete all user memories: {e}")
        raise RuntimeError(f"Failed to delete all user memories: {e}") from e


def get_user_memory_stats(user_id: int) -> dict[str, Any]:
    """
    Get memory statistics for a user.

    Args:
        user_id: The user's ID

    Returns:
        Dict with statistics: total_memories, by_category, oldest_memory, newest_memory
    """
    try:
        client = get_supabase_client()

        def query_stats():
            return (
                client.table("user_memories")
                .select("id, category, created_at")
                .eq("user_id", user_id)
                .eq("is_active", True)
                .order("created_at", desc=False)
                .execute()
            )

        result = _execute_with_connection_retry(
            query_stats,
            f"get_user_memory_stats(user={user_id})"
        )

        memories = result.data or []

        # Count by category
        by_category = {}
        for memory in memories:
            cat = memory.get("category", "general")
            by_category[cat] = by_category.get(cat, 0) + 1

        stats = {
            "total_memories": len(memories),
            "by_category": by_category,
            "oldest_memory": memories[0]["created_at"] if memories else None,
            "newest_memory": memories[-1]["created_at"] if memories else None,
        }

        logger.info(f"Retrieved memory stats for user {user_id}: {stats['total_memories']} total")
        return stats

    except Exception as e:
        logger.error(f"Failed to get user memory stats: {e}")
        raise RuntimeError(f"Failed to get user memory stats: {e}") from e


def search_user_memories(user_id: int, query: str, limit: int = 10) -> list[dict[str, Any]]:
    """
    Search memories by content (case-insensitive).

    Args:
        user_id: The user's ID
        query: Search query string
        limit: Maximum results to return

    Returns:
        List of matching memories
    """
    try:
        client = get_supabase_client()

        def search():
            return (
                client.table("user_memories")
                .select("*")
                .eq("user_id", user_id)
                .eq("is_active", True)
                .ilike("content", f"%{query}%")
                .order("last_accessed_at", desc=True, nullsfirst=False)
                .limit(limit)
                .execute()
            )

        result = _execute_with_connection_retry(
            search,
            f"search_user_memories(user={user_id}, query={query[:20]}...)"
        )

        memories = result.data or []
        logger.info(f"Found {len(memories)} memories matching '{query}' for user {user_id}")
        return memories

    except Exception as e:
        logger.error(f"Failed to search user memories: {e}")
        raise RuntimeError(f"Failed to search user memories: {e}") from e


def check_duplicate_memory(user_id: int, content: str) -> dict[str, Any] | None:
    """
    Check if a similar memory already exists for the user.

    Args:
        user_id: The user's ID
        content: The memory content to check

    Returns:
        Existing memory if duplicate found, None otherwise
    """
    try:
        client = get_supabase_client()

        # Normalize content for comparison
        normalized = content.strip().lower()

        def check():
            return (
                client.table("user_memories")
                .select("*")
                .eq("user_id", user_id)
                .eq("is_active", True)
                .execute()
            )

        result = _execute_with_connection_retry(
            check,
            f"check_duplicate_memory(user={user_id})"
        )

        if not result.data:
            return None

        # Check for similar content (simple approach)
        for memory in result.data:
            existing_normalized = memory.get("content", "").strip().lower()
            # Exact match or one is substring of other
            if normalized == existing_normalized:
                return memory
            if len(normalized) > 20 and len(existing_normalized) > 20:
                if normalized in existing_normalized or existing_normalized in normalized:
                    return memory

        return None

    except Exception as e:
        logger.warning(f"Failed to check duplicate memory: {e}")
        return None
