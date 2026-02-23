import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, UTC
from typing import Any, TypeVar

from httpx import ConnectError, ReadTimeout, RemoteProtocolError

from src.config.supabase_config import get_supabase_client
from src.utils.retry import with_retry

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _execute_with_connection_retry(  # noqa: UP047
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


@with_retry(
    max_attempts=3,
    initial_delay=0.1,
    max_delay=2.0,
    exceptions=(Exception,)
)
def create_chat_session(user_id: int, title: str = None, model: str = None) -> dict[str, Any]:
    """
    Create a new chat session for a user.
    
    This function is decorated with retry logic to handle transient connection errors.
    """
    try:
        client = get_supabase_client()

        # Generate title if not provided
        if not title:
            title = f"Chat {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')}"

        session_data = {
            "user_id": user_id,
            "title": title,
            "model": model or "openai/gpt-3.5-turbo",
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
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


@with_retry(
    max_attempts=3,
    initial_delay=0.1,
    max_delay=2.0,
    exceptions=(Exception,)
)
def save_chat_message(
    session_id: int,
    role: str,
    content: str,
    model: str = None,
    tokens: int = 0,
    user_id: int = None,
    skip_duplicate_check: bool = False,
) -> dict[str, Any]:
    """
    Save a chat message to a session and update session's updated_at timestamp.

    This function is decorated with retry logic to handle transient connection errors
    that may occur when called from background tasks after HTTP responses are sent.

    Args:
        session_id: The chat session ID
        role: Message role ('user' or 'assistant')
        content: Message content
        model: Model name (optional)
        tokens: Token count (default: 0)
        user_id: User ID for additional validation (optional)
        skip_duplicate_check: If True, skips duplicate detection (default: False)

    Returns:
        The saved message dict

    Note:
        By default, this function checks for duplicate messages within the last 5 minutes
        to prevent accidentally saving the same content multiple times (e.g., on retries).
    """
    try:
        client = get_supabase_client()

        # Duplicate detection: Check if identical message was saved recently
        if not skip_duplicate_check and content:
            try:
                five_minutes_ago = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()

                def check_duplicate():
                    return (
                        client.table("chat_messages")
                        .select("*")  # Select all fields to return complete message object
                        .eq("session_id", session_id)
                        .eq("role", role)
                        .eq("content", content)
                        .gte("created_at", five_minutes_ago)
                        .order("created_at", desc=True)
                        .limit(1)
                        .execute()
                    )

                duplicate_result = _execute_with_connection_retry(
                    check_duplicate,
                    f"check_duplicate_message(session={session_id}, role={role})"
                )

                if duplicate_result.data:
                    existing = duplicate_result.data[0]
                    logger.warning(
                        f"Duplicate message detected for session {session_id}, role={role}. "
                        f"Returning existing message {existing['id']} instead of creating duplicate. "
                        f"Content preview: {content[:50]}..."
                    )
                    # Return existing message instead of creating duplicate
                    # This ensures consistent return value with all expected fields
                    return existing

            except Exception as e:
                # If duplicate check fails, log but continue with save
                # (better to potentially save a duplicate than fail the request)
                logger.warning(f"Duplicate check failed, proceeding with save: {e}")

        message_data = {
            "session_id": session_id,
            "role": role,  # 'user' or 'assistant'
            "content": content,
            "model": model,
            "tokens": tokens,
            "created_at": datetime.now(UTC).isoformat(),
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
        update_time = datetime.now(UTC).isoformat()
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


@with_retry(
    max_attempts=3,
    initial_delay=0.1,
    max_delay=2.0,
    exceptions=(Exception,)
)
def update_chat_session(
    session_id: int, user_id: int, title: str = None, model: str = None
) -> bool:
    """
    Update a chat session.
    
    This function is decorated with retry logic to handle transient connection errors.
    """
    try:
        client = get_supabase_client()

        update_data = {"updated_at": datetime.now(UTC).isoformat()}

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


@with_retry(
    max_attempts=3,
    initial_delay=0.1,
    max_delay=2.0,
    exceptions=(Exception,)
)
def delete_chat_session(session_id: int, user_id: int) -> bool:
    """
    Delete a chat session (soft delete).
    
    This function is decorated with retry logic to handle transient connection errors.
    """
    try:
        client = get_supabase_client()

        # Soft delete - mark as inactive
        def soft_delete_session():
            return (
                client.table("chat_sessions")
                .update({"is_active": False, "updated_at": datetime.now(UTC).isoformat()})
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


def validate_message_ownership(message_id: int, user_id: int, session_id: int = None) -> bool:
    """
    Validate that a message belongs to the user (via their session).

    Args:
        message_id: The message ID to validate
        user_id: The user ID to check ownership against
        session_id: Optional session ID - if provided, also validates message belongs to this session

    Returns:
        True if the message belongs to the user's session, False otherwise
    """
    try:
        client = get_supabase_client()

        # Query the message and join with session to verify ownership
        def query_message():
            query = (
                client.table("chat_messages")
                .select("id, session_id, chat_sessions!inner(id, user_id)")
                .eq("id", message_id)
                .eq("chat_sessions.user_id", user_id)
            )
            if session_id is not None:
                query = query.eq("session_id", session_id)
            return query.execute()

        result = _execute_with_connection_retry(
            query_message,
            f"validate_message_ownership(message={message_id}, user={user_id})"
        )

        return bool(result.data)

    except Exception as e:
        logger.error(f"Failed to validate message ownership: {e}")
        return False


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
