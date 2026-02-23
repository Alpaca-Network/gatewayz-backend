"""Database functions for shared chat links."""

import logging
import secrets
import time
from datetime import datetime, UTC
from typing import Any

from httpx import ConnectError, ReadTimeout, RemoteProtocolError

from src.config.supabase_config import get_supabase_client
from src.utils.retry import with_retry

logger = logging.getLogger(__name__)


def _execute_with_connection_retry(
    operation,
    operation_name: str,
    max_retries: int = 3,
    initial_delay: float = 0.1,
):
    """
    Execute a Supabase operation with retry logic for transient connection errors.
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


def generate_share_token() -> str:
    """Generate a cryptographically secure share token."""
    return secrets.token_urlsafe(32)


@with_retry(
    max_attempts=3,
    initial_delay=0.1,
    max_delay=2.0,
    exceptions=(Exception,),
)
def create_shared_chat(
    session_id: int,
    user_id: int,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    """
    Create a shared chat link for a session.

    Args:
        session_id: The chat session ID to share
        user_id: The user creating the share link
        expires_at: Optional expiration date

    Returns:
        The created shared_chat record
    """
    try:
        client = get_supabase_client()
        share_token = generate_share_token()

        share_data = {
            "session_id": session_id,
            "share_token": share_token,
            "created_by_user_id": user_id,
            "created_at": datetime.now(UTC).isoformat(),
            "expires_at": expires_at.isoformat() if expires_at else None,
            "view_count": 0,
            "is_active": True,
        }

        def insert_share():
            return client.table("shared_chats").insert(share_data).execute()

        result = _execute_with_connection_retry(
            insert_share, f"create_shared_chat(session={session_id}, user={user_id})"
        )

        if not result.data:
            raise ValueError("Failed to create shared chat link")

        share = result.data[0]
        logger.info(
            f"Created shared chat link {share['id']} for session {session_id} by user {user_id}"
        )
        return share

    except Exception as e:
        logger.error(f"Failed to create shared chat link: {e}")
        raise RuntimeError(f"Failed to create shared chat link: {e}") from e


def get_shared_chat_by_token(token: str) -> dict[str, Any] | None:
    """
    Get a shared chat by its token (public endpoint).

    Returns the shared chat with session data and messages if found and valid.
    """
    try:
        client = get_supabase_client()

        # Get the share record
        def query_share():
            return (
                client.table("shared_chats")
                .select("*")
                .eq("share_token", token)
                .eq("is_active", True)
                .execute()
            )

        share_result = _execute_with_connection_retry(
            query_share, f"get_shared_chat_by_token(token={token[:8]}...)"
        )

        if not share_result.data:
            logger.warning(f"Shared chat not found for token {token[:8]}...")
            return None

        share = share_result.data[0]

        # Check if expired
        if share.get("expires_at"):
            expires_at = datetime.fromisoformat(share["expires_at"].replace("Z", "+00:00"))
            if expires_at < datetime.now(UTC):
                logger.warning(f"Shared chat {share['id']} has expired")
                return None

        # Get the session data
        def query_session():
            return (
                client.table("chat_sessions")
                .select("*")
                .eq("id", share["session_id"])
                .eq("is_active", True)
                .execute()
            )

        session_result = _execute_with_connection_retry(
            query_session, f"get_session_for_share(session={share['session_id']})"
        )

        if not session_result.data:
            logger.warning(f"Session {share['session_id']} not found for shared chat")
            return None

        session = session_result.data[0]

        # Get the messages
        def query_messages():
            return (
                client.table("chat_messages")
                .select("*")
                .eq("session_id", share["session_id"])
                .order("created_at", desc=False)
                .execute()
            )

        messages_result = _execute_with_connection_retry(
            query_messages, f"get_messages_for_share(session={share['session_id']})"
        )

        messages = messages_result.data or []

        # Update view count
        try:
            def update_view_count():
                return (
                    client.table("shared_chats")
                    .update({
                        "view_count": share["view_count"] + 1,
                        "last_viewed_at": datetime.now(UTC).isoformat(),
                    })
                    .eq("id", share["id"])
                    .execute()
                )

            _execute_with_connection_retry(
                update_view_count, f"update_view_count(share={share['id']})"
            )
        except Exception as e:
            # Don't fail the request if view count update fails
            logger.warning(f"Failed to update view count for share {share['id']}: {e}")

        logger.info(
            f"Retrieved shared chat {share['id']} for session {share['session_id']} "
            f"with {len(messages)} messages"
        )

        return {
            "session_id": session["id"],
            "title": session["title"],
            "model": session["model"],
            "created_at": session["created_at"],
            "messages": messages,
        }

    except Exception as e:
        logger.error(f"Failed to get shared chat by token: {e}")
        raise RuntimeError(f"Failed to get shared chat by token: {e}") from e


def get_user_shared_chats(
    user_id: int, limit: int = 50, offset: int = 0
) -> list[dict[str, Any]]:
    """Get all share links created by a user."""
    try:
        client = get_supabase_client()

        def query_shares():
            return (
                client.table("shared_chats")
                .select("*")
                .eq("created_by_user_id", user_id)
                .eq("is_active", True)
                .order("created_at", desc=True)
                .range(offset, offset + limit - 1)
                .execute()
            )

        result = _execute_with_connection_retry(
            query_shares, f"get_user_shared_chats(user={user_id})"
        )

        shares = result.data or []
        logger.info(f"Retrieved {len(shares)} shared chats for user {user_id}")
        return shares

    except Exception as e:
        logger.error(f"Failed to get user shared chats: {e}")
        raise RuntimeError(f"Failed to get user shared chats: {e}") from e


@with_retry(
    max_attempts=3,
    initial_delay=0.1,
    max_delay=2.0,
    exceptions=(Exception,),
)
def delete_shared_chat(token: str, user_id: int) -> bool:
    """
    Delete (soft delete) a shared chat by token.

    Only the user who created the share can delete it.
    """
    try:
        client = get_supabase_client()

        def soft_delete_share():
            return (
                client.table("shared_chats")
                .update({
                    "is_active": False,
                })
                .eq("share_token", token)
                .eq("created_by_user_id", user_id)
                .execute()
            )

        result = _execute_with_connection_retry(
            soft_delete_share, f"delete_shared_chat(token={token[:8]}..., user={user_id})"
        )

        if not result.data:
            logger.warning(f"Shared chat not found or not owned by user {user_id}")
            return False

        logger.info(f"Deleted shared chat with token {token[:8]}... by user {user_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to delete shared chat: {e}")
        raise RuntimeError(f"Failed to delete shared chat: {e}") from e


def verify_session_ownership(session_id: int, user_id: int) -> bool:
    """Verify that a session belongs to a user."""
    try:
        client = get_supabase_client()

        def query_session():
            return (
                client.table("chat_sessions")
                .select("id")
                .eq("id", session_id)
                .eq("user_id", user_id)
                .eq("is_active", True)
                .execute()
            )

        result = _execute_with_connection_retry(
            query_session, f"verify_session_ownership(session={session_id}, user={user_id})"
        )

        return bool(result.data)

    except Exception as e:
        logger.error(f"Failed to verify session ownership: {e}")
        return False


def check_share_rate_limit(user_id: int, max_shares_per_hour: int = 10) -> bool:
    """
    Check if user has exceeded the share rate limit.

    Returns True if user can create more shares, False if rate limited.
    """
    try:
        client = get_supabase_client()
        one_hour_ago = datetime.now(UTC).replace(
            minute=0, second=0, microsecond=0
        ).isoformat()

        def count_recent_shares():
            return (
                client.table("shared_chats")
                .select("id")
                .eq("created_by_user_id", user_id)
                .gte("created_at", one_hour_ago)
                .execute()
            )

        result = _execute_with_connection_retry(
            count_recent_shares, f"check_share_rate_limit(user={user_id})"
        )

        count = len(result.data) if result.data else 0
        within_limit = count < max_shares_per_hour

        if not within_limit:
            logger.warning(
                f"User {user_id} rate limited for shares: {count}/{max_shares_per_hour} in last hour"
            )

        return within_limit

    except Exception as e:
        logger.error(f"Failed to check share rate limit: {e}")
        # On error, allow the share (fail open)
        return True
