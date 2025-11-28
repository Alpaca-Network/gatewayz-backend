import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, TypeVar

from httpx import RemoteProtocolError, ConnectError, ReadTimeout
from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

T = TypeVar("T")


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
    
    Args:
        operation: The operation to execute
        operation_name: Name of the operation for logging
        max_retries: Maximum number of retry attempts (default: 3)
        initial_delay: Initial delay in seconds before first retry (default: 0.1)
        
    Returns:
        The result of the operation
        
    Raises:
        The last exception encountered if all retries fail
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
            # For non-transient errors, fail immediately
            logger.error(f"{operation_name} failed with non-retryable error: {e}")
            raise
    
    # If we get here, all retries failed
    if last_exception:
        raise last_exception
    raise RuntimeError(f"{operation_name} failed without exception details")


def create_chat_session(user_id: int, title: str = None, model: str = None) -> dict[str, Any]:
    """Create a new chat session for a user"""
    try:
        client = get_supabase_client()

        # Generate title if not provided
        if not title:
            title = f"Chat {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}"

        session_data = {
            "user_id": user_id,
            "title": title,
            "model": model or "openai/gpt-3.5-turbo",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "is_active": True,
        }

        def insert_session():
            return client.table("chat_sessions").insert(session_data).execute()
        
        result = _execute_with_connection_retry(
            insert_session,
            f"create_chat_session(user={user_id})"
        )

        if not result.data:
            raise ValueError("Failed to create chat session")

        session = result.data[0]
        logger.info(f"Created chat session {session['id']} for user {user_id}")
        return session

    except Exception as e:
        logger.error(f"Failed to create chat session: {e}")
        raise RuntimeError(f"Failed to create chat session: {e}") from e


def save_chat_message(
    session_id: int,
    role: str,
    content: str,
    model: str = None,
    tokens: int = 0,
    user_id: int = None,
) -> dict[str, Any]:
    """Save a chat message to a session and update session's updated_at timestamp"""
    try:
        client = get_supabase_client()

        message_data = {
            "session_id": session_id,
            "role": role,  # 'user' or 'assistant'
            "content": content,
            "model": model,
            "tokens": tokens,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # Insert message with retry logic for connection errors
        def insert_message():
            return client.table("chat_messages").insert(message_data).execute()
        
        result = _execute_with_connection_retry(
            insert_message,
            f"save_chat_message(session={session_id}, role={role})"
        )

        if not result.data:
            raise ValueError("Failed to save chat message")

        message = result.data[0]

        # Update session's updated_at timestamp to reflect latest activity
        update_time = datetime.now(timezone.utc).isoformat()
        update_data = {"updated_at": update_time}

        # If model is provided, also update session model
        if model:
            update_data["model"] = model

        # Update session with retry logic for connection errors
        def update_session():
            session_update_query = (
                client.table("chat_sessions").update(update_data).eq("id", session_id)
            )
            
            # Add user_id check if provided for additional security
            if user_id is not None:
                session_update_query = session_update_query.eq("user_id", user_id)
            
            return session_update_query.execute()
        
        session_update_result = _execute_with_connection_retry(
            update_session,
            f"update_chat_session_timestamp(session={session_id})"
        )

        if not session_update_result.data:
            logger.warning(f"Failed to update session {session_id} timestamp after saving message")

        logger.info(
            f"Saved message {message['id']} to session {session_id} and updated session timestamp"
        )
        return message

    except Exception as e:
        logger.error(f"Failed to save chat message: {e}")
        raise RuntimeError(f"Failed to save chat message: {e}") from e


def get_user_chat_sessions(user_id: int, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    """Get all chat sessions for a user"""
    try:
        client = get_supabase_client()

        def query_sessions():
            return (
                client.table("chat_sessions")
                .select("*")
                .eq("user_id", user_id)
                .eq("is_active", True)
                .order("updated_at", desc=True)
                .range(offset, offset + limit - 1)
                .execute()
            )
        
        result = _execute_with_connection_retry(
            query_sessions,
            f"get_user_chat_sessions(user={user_id})"
        )

        sessions = result.data or []
        logger.info(f"Retrieved {len(sessions)} chat sessions for user {user_id}")
        return sessions

    except Exception as e:
        logger.error(f"Failed to get chat sessions: {e}")
        raise RuntimeError(f"Failed to get chat sessions: {e}") from e


def get_chat_session(session_id: int, user_id: int) -> dict[str, Any] | None:
    """Get a specific chat session with messages"""
    try:
        client = get_supabase_client()

        # Get session
        def query_session():
            return (
                client.table("chat_sessions")
                .select("*")
                .eq("id", session_id)
                .eq("user_id", user_id)
                .eq("is_active", True)
                .execute()
            )
        
        session_result = _execute_with_connection_retry(
            query_session,
            f"get_chat_session(session={session_id}, user={user_id})"
        )

        if not session_result.data:
            logger.warning(f"Chat session {session_id} not found for user {user_id}")
            return None

        session = session_result.data[0]

        # Get messages for this session
        def query_messages():
            return (
                client.table("chat_messages")
                .select("*")
                .eq("session_id", session_id)
                .order("created_at", desc=False)
                .execute()
            )
        
        messages_result = _execute_with_connection_retry(
            query_messages,
            f"get_chat_messages(session={session_id})"
        )

        session["messages"] = messages_result.data or []
        logger.info(f"Retrieved session {session_id} with {len(session['messages'])} messages")
        return session

    except Exception as e:
        logger.error(f"Failed to get chat session: {e}")
        raise RuntimeError(f"Failed to get chat session: {e}") from e


def update_chat_session(
    session_id: int, user_id: int, title: str = None, model: str = None
) -> bool:
    """Update a chat session"""
    try:
        client = get_supabase_client()

        update_data = {"updated_at": datetime.now(timezone.utc).isoformat()}

        if title:
            update_data["title"] = title
        if model:
            update_data["model"] = model

        def update_session():
            return (
                client.table("chat_sessions")
                .update(update_data)
                .eq("id", session_id)
                .eq("user_id", user_id)
                .execute()
            )
        
        result = _execute_with_connection_retry(
            update_session,
            f"update_chat_session(session={session_id})"
        )

        if not result.data:
            logger.warning(f"Failed to update chat session {session_id}")
            return False

        logger.info(f"Updated chat session {session_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to update chat session: {e}")
        raise RuntimeError(f"Failed to update chat session: {e}") from e


def delete_chat_session(session_id: int, user_id: int) -> bool:
    """Delete a chat session (soft delete)"""
    try:
        client = get_supabase_client()

        # Soft delete - mark as inactive
        def soft_delete_session():
            return (
                client.table("chat_sessions")
                .update({"is_active": False, "updated_at": datetime.now(timezone.utc).isoformat()})
                .eq("id", session_id)
                .eq("user_id", user_id)
                .execute()
            )
        
        result = _execute_with_connection_retry(
            soft_delete_session,
            f"delete_chat_session(session={session_id})"
        )

        if not result.data:
            logger.warning(f"Failed to delete chat session {session_id}")
            return False

        logger.info(f"Deleted chat session {session_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to delete chat session: {e}")
        raise RuntimeError(f"Failed to delete chat session: {e}") from e


def get_chat_session_stats(user_id: int) -> dict[str, Any]:
    """Get chat session statistics for a user"""
    try:
        client = get_supabase_client()

        # Get total sessions
        def query_sessions_count():
            return (
                client.table("chat_sessions")
                .select("id")
                .eq("user_id", user_id)
                .eq("is_active", True)
                .execute()
            )
        
        sessions_result = _execute_with_connection_retry(
            query_sessions_count,
            f"get_chat_session_stats_sessions(user={user_id})"
        )
        total_sessions = len(sessions_result.data) if sessions_result.data else 0

        # Get total messages
        def query_messages_count():
            return (
                client.table("chat_messages")
                .select("id")
                .join("chat_sessions", "session_id", "id")
                .eq("chat_sessions.user_id", user_id)
                .eq("chat_sessions.is_active", True)
                .execute()
            )
        
        messages_result = _execute_with_connection_retry(
            query_messages_count,
            f"get_chat_session_stats_messages(user={user_id})"
        )
        total_messages = len(messages_result.data) if messages_result.data else 0

        # Get total tokens
        def query_tokens():
            return (
                client.table("chat_messages")
                .select("tokens")
                .join("chat_sessions", "session_id", "id")
                .eq("chat_sessions.user_id", user_id)
                .eq("chat_sessions.is_active", True)
                .execute()
            )
        
        tokens_result = _execute_with_connection_retry(
            query_tokens,
            f"get_chat_session_stats_tokens(user={user_id})"
        )
        total_tokens = (
            sum(msg.get("tokens", 0) for msg in tokens_result.data) if tokens_result.data else 0
        )

        stats = {
            "total_sessions": total_sessions,
            "total_messages": total_messages,
            "total_tokens": total_tokens,
        }

        logger.info(f"Retrieved chat stats for user {user_id}: {stats}")
        return stats

    except Exception as e:
        logger.error(f"Failed to get chat session stats: {e}")
        raise RuntimeError(f"Failed to get chat session stats: {e}") from e


def search_chat_sessions(user_id: int, query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search chat sessions by title or message content"""
    try:
        client = get_supabase_client()

        # Search in session titles
        def search_titles():
            return (
                client.table("chat_sessions")
                .select("*")
                .eq("user_id", user_id)
                .eq("is_active", True)
                .ilike("title", f"%{query}%")
                .execute()
            )
        
        title_result = _execute_with_connection_retry(
            search_titles,
            f"search_chat_sessions_titles(user={user_id})"
        )

        # Search in message content
        def search_messages():
            return (
                client.table("chat_messages")
                .select("session_id")
                .ilike("content", f"%{query}%")
                .execute()
            )
        
        message_result = _execute_with_connection_retry(
            search_messages,
            f"search_chat_sessions_messages(user={user_id})"
        )

        session_ids = set()
        if message_result.data:
            session_ids.update(msg["session_id"] for msg in message_result.data)

        # Get sessions from message search
        message_sessions = []
        if session_ids:
            def query_message_sessions():
                return (
                    client.table("chat_sessions")
                    .select("*")
                    .eq("user_id", user_id)
                    .eq("is_active", True)
                    .in_("id", list(session_ids))
                    .execute()
                )
            
            message_sessions_result = _execute_with_connection_retry(
                query_message_sessions,
                f"search_chat_sessions_by_message_ids(user={user_id})"
            )
            message_sessions = message_sessions_result.data or []

        # Combine and deduplicate results
        all_sessions = (title_result.data or []) + message_sessions
        unique_sessions = {session["id"]: session for session in all_sessions}.values()

        # Sort by updated_at and limit
        sorted_sessions = sorted(unique_sessions, key=lambda x: x["updated_at"], reverse=True)[
            :limit
        ]

        logger.info(
            f"Found {len(sorted_sessions)} sessions matching query '{query}' for user {user_id}"
        )
        return list(sorted_sessions)

    except Exception as e:
        logger.error(f"Failed to search chat sessions: {e}")
        raise RuntimeError(f"Failed to search chat sessions: {e}") from e
