"""
Message Feedback Database Operations

This module handles CRUD operations for message feedback data,
allowing users to provide feedback (thumbs up, thumbs down, etc.)
on assistant responses.
"""

import logging
import time
from datetime import datetime, timedelta, UTC
from typing import Any, TypeVar

from httpx import ConnectError, ReadTimeout, RemoteProtocolError

from src.config.supabase_config import get_supabase_client
from src.utils.retry import with_retry

logger = logging.getLogger(__name__)

T = TypeVar("T")


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
            # For non-transient errors, fail immediately
            logger.error(f"{operation_name} failed with non-retryable error: {e}")
            raise

    # If we get here, all retries failed
    if last_exception:
        raise last_exception
    raise RuntimeError(f"{operation_name} failed without exception details")


# Valid feedback types
VALID_FEEDBACK_TYPES = {"thumbs_up", "thumbs_down", "regenerate"}


@with_retry(
    max_attempts=3,
    initial_delay=0.1,
    max_delay=2.0,
    exceptions=(Exception,),
)
def save_message_feedback(
    user_id: int,
    feedback_type: str,
    session_id: int | None = None,
    message_id: int | None = None,
    rating: int | None = None,
    comment: str | None = None,
    model: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Save message feedback from a user.

    Args:
        user_id: The user providing feedback
        feedback_type: Type of feedback ('thumbs_up', 'thumbs_down', 'regenerate')
        session_id: Optional chat session ID
        message_id: Optional message ID being rated
        rating: Optional 1-5 star rating
        comment: Optional text comment
        model: Optional model name that generated the response
        metadata: Optional additional context (response content, prompt, etc.)

    Returns:
        The saved feedback record

    Raises:
        ValueError: If feedback_type is invalid or rating is out of range
        RuntimeError: If database operation fails
    """
    # Validate feedback type
    if feedback_type not in VALID_FEEDBACK_TYPES:
        raise ValueError(
            f"Invalid feedback_type '{feedback_type}'. "
            f"Must be one of: {', '.join(VALID_FEEDBACK_TYPES)}"
        )

    # Validate rating if provided
    if rating is not None and (rating < 1 or rating > 5):
        raise ValueError("Rating must be between 1 and 5")

    try:
        client = get_supabase_client()

        feedback_data = {
            "user_id": user_id,
            "feedback_type": feedback_type,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }

        # Add optional fields if provided
        if session_id is not None:
            feedback_data["session_id"] = session_id
        if message_id is not None:
            feedback_data["message_id"] = message_id
        if rating is not None:
            feedback_data["rating"] = rating
        if comment is not None:
            feedback_data["comment"] = comment
        if model is not None:
            feedback_data["model"] = model
        if metadata is not None:
            feedback_data["metadata"] = metadata

        def insert_feedback():
            return client.table("message_feedback").insert(feedback_data).execute()

        result = _execute_with_connection_retry(
            insert_feedback, f"save_message_feedback(user={user_id}, type={feedback_type})"
        )

        if not result.data:
            raise ValueError("Failed to save message feedback")

        feedback = result.data[0]
        logger.info(
            f"Saved {feedback_type} feedback {feedback['id']} for user {user_id}"
            + (f" on message {message_id}" if message_id else "")
        )
        return feedback

    except ValueError:
        raise
    except Exception as e:
        logger.error(f"Failed to save message feedback: {e}")
        raise RuntimeError(f"Failed to save message feedback: {e}") from e


def get_user_feedback(
    user_id: int,
    feedback_type: str | None = None,
    session_id: int | None = None,
    model: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """
    Get feedback records for a user with optional filters.

    Args:
        user_id: The user ID
        feedback_type: Optional filter by feedback type
        session_id: Optional filter by session
        model: Optional filter by model name
        limit: Maximum records to return (default 50)
        offset: Pagination offset

    Returns:
        List of feedback records
    """
    try:
        client = get_supabase_client()

        def query_feedback():
            query = (
                client.table("message_feedback")
                .select("*")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
            )

            if feedback_type:
                query = query.eq("feedback_type", feedback_type)
            if session_id is not None:
                query = query.eq("session_id", session_id)
            if model:
                query = query.eq("model", model)

            return query.range(offset, offset + limit - 1).execute()

        result = _execute_with_connection_retry(
            query_feedback, f"get_user_feedback(user={user_id})"
        )

        feedback_list = result.data or []
        logger.info(f"Retrieved {len(feedback_list)} feedback records for user {user_id}")
        return feedback_list

    except Exception as e:
        logger.error(f"Failed to get user feedback: {e}")
        raise RuntimeError(f"Failed to get user feedback: {e}") from e


def get_feedback_by_message(message_id: int, user_id: int | None = None) -> list[dict[str, Any]]:
    """
    Get all feedback for a specific message.

    Args:
        message_id: The message ID
        user_id: Optional filter by user (for authorization)

    Returns:
        List of feedback records for the message
    """
    try:
        client = get_supabase_client()

        def query_feedback():
            query = (
                client.table("message_feedback")
                .select("*")
                .eq("message_id", message_id)
                .order("created_at", desc=True)
            )

            if user_id is not None:
                query = query.eq("user_id", user_id)

            return query.execute()

        result = _execute_with_connection_retry(
            query_feedback, f"get_feedback_by_message(message={message_id})"
        )

        return result.data or []

    except Exception as e:
        logger.error(f"Failed to get feedback for message {message_id}: {e}")
        raise RuntimeError(f"Failed to get feedback for message: {e}") from e


def get_feedback_by_session(session_id: int, user_id: int) -> list[dict[str, Any]]:
    """
    Get all feedback for a specific chat session.

    Args:
        session_id: The session ID
        user_id: The user ID (for authorization)

    Returns:
        List of feedback records for the session
    """
    try:
        client = get_supabase_client()

        def query_feedback():
            return (
                client.table("message_feedback")
                .select("*")
                .eq("session_id", session_id)
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .execute()
            )

        result = _execute_with_connection_retry(
            query_feedback, f"get_feedback_by_session(session={session_id})"
        )

        return result.data or []

    except Exception as e:
        logger.error(f"Failed to get feedback for session {session_id}: {e}")
        raise RuntimeError(f"Failed to get feedback for session: {e}") from e


@with_retry(
    max_attempts=3,
    initial_delay=0.1,
    max_delay=2.0,
    exceptions=(Exception,),
)
def update_feedback(
    feedback_id: int,
    user_id: int,
    feedback_type: str | None = None,
    rating: int | None = None,
    comment: str | None = None,
) -> dict[str, Any] | None:
    """
    Update an existing feedback record.

    Args:
        feedback_id: The feedback record ID
        user_id: The user ID (for authorization)
        feedback_type: Optional new feedback type
        rating: Optional new rating
        comment: Optional new comment

    Returns:
        The updated feedback record, or None if not found
    """
    # Validate feedback type if provided
    if feedback_type is not None and feedback_type not in VALID_FEEDBACK_TYPES:
        raise ValueError(
            f"Invalid feedback_type '{feedback_type}'. "
            f"Must be one of: {', '.join(VALID_FEEDBACK_TYPES)}"
        )

    # Validate rating if provided
    if rating is not None and (rating < 1 or rating > 5):
        raise ValueError("Rating must be between 1 and 5")

    try:
        client = get_supabase_client()

        update_data = {"updated_at": datetime.now(UTC).isoformat()}

        if feedback_type is not None:
            update_data["feedback_type"] = feedback_type
        if rating is not None:
            update_data["rating"] = rating
        if comment is not None:
            update_data["comment"] = comment

        def update_feedback_record():
            return (
                client.table("message_feedback")
                .update(update_data)
                .eq("id", feedback_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = _execute_with_connection_retry(
            update_feedback_record, f"update_feedback(id={feedback_id})"
        )

        if not result.data:
            logger.warning(f"Feedback {feedback_id} not found for user {user_id}")
            return None

        logger.info(f"Updated feedback {feedback_id} for user {user_id}")
        return result.data[0]

    except ValueError:
        raise
    except Exception as e:
        logger.error(f"Failed to update feedback: {e}")
        raise RuntimeError(f"Failed to update feedback: {e}") from e


@with_retry(
    max_attempts=3,
    initial_delay=0.1,
    max_delay=2.0,
    exceptions=(Exception,),
)
def delete_feedback(feedback_id: int, user_id: int) -> bool:
    """
    Delete a feedback record.

    Args:
        feedback_id: The feedback record ID
        user_id: The user ID (for authorization)

    Returns:
        True if deleted, False if not found
    """
    try:
        client = get_supabase_client()

        def delete_feedback_record():
            return (
                client.table("message_feedback")
                .delete()
                .eq("id", feedback_id)
                .eq("user_id", user_id)
                .execute()
            )

        result = _execute_with_connection_retry(
            delete_feedback_record, f"delete_feedback(id={feedback_id})"
        )

        if not result.data:
            logger.warning(f"Feedback {feedback_id} not found for user {user_id}")
            return False

        logger.info(f"Deleted feedback {feedback_id} for user {user_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to delete feedback: {e}")
        raise RuntimeError(f"Failed to delete feedback: {e}") from e


def get_feedback_stats(
    user_id: int | None = None,
    model: str | None = None,
    days: int = 30,
) -> dict[str, Any]:
    """
    Get aggregated feedback statistics.

    Args:
        user_id: Optional filter by user
        model: Optional filter by model
        days: Number of days to aggregate (default 30)

    Returns:
        Dictionary with feedback statistics
    """
    try:
        client = get_supabase_client()

        # Calculate date filter
        from_date = datetime.now(UTC) - timedelta(days=days)
        from_date = from_date.replace(hour=0, minute=0, second=0, microsecond=0)

        def query_all_feedback():
            query = (
                client.table("message_feedback")
                .select("feedback_type, rating, model, created_at")
                .gte("created_at", from_date.isoformat())
            )

            if user_id is not None:
                query = query.eq("user_id", user_id)
            if model is not None:
                query = query.eq("model", model)

            return query.execute()

        result = _execute_with_connection_retry(
            query_all_feedback,
            f"get_feedback_stats(user={user_id}, model={model})",
        )

        feedback_list = result.data or []

        # Calculate statistics
        total = len(feedback_list)
        thumbs_up = sum(1 for f in feedback_list if f["feedback_type"] == "thumbs_up")
        thumbs_down = sum(1 for f in feedback_list if f["feedback_type"] == "thumbs_down")
        regenerate = sum(1 for f in feedback_list if f["feedback_type"] == "regenerate")

        # Calculate average rating
        ratings = [f["rating"] for f in feedback_list if f.get("rating") is not None]
        avg_rating = sum(ratings) / len(ratings) if ratings else None

        # Get feedback by model
        by_model: dict[str, dict[str, int]] = {}
        for f in feedback_list:
            m = f.get("model") or "unknown"
            if m not in by_model:
                by_model[m] = {"thumbs_up": 0, "thumbs_down": 0, "regenerate": 0, "total": 0}
            by_model[m][f["feedback_type"]] += 1
            by_model[m]["total"] += 1

        stats = {
            "total_feedback": total,
            "thumbs_up": thumbs_up,
            "thumbs_down": thumbs_down,
            "regenerate": regenerate,
            "thumbs_up_rate": round(thumbs_up / total * 100, 2) if total > 0 else 0,
            "thumbs_down_rate": round(thumbs_down / total * 100, 2) if total > 0 else 0,
            "average_rating": round(avg_rating, 2) if avg_rating else None,
            "by_model": by_model,
            "period_days": days,
        }

        logger.info(f"Calculated feedback stats: {total} total feedback items")
        return stats

    except Exception as e:
        logger.error(f"Failed to get feedback stats: {e}")
        raise RuntimeError(f"Failed to get feedback stats: {e}") from e
