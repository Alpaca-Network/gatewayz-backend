"""
Notification service for managing admin dashboard notifications
"""

import logging
from datetime import UTC, datetime, timedelta

from ..config.redis_config import get_redis_client
from ..config.supabase_config import get_supabase_client
from ..models.notification_models import (
    NotificationCreate,
    NotificationResponse,
)

logger = logging.getLogger(__name__)


class NotificationService:
    """Service for managing admin dashboard notifications"""

    CACHE_PREFIX = "admin_notif:"
    UNREAD_COUNT_TTL = 60  # 1 minute cache for unread count

    async def create_notification(
        self, notification: NotificationCreate
    ) -> NotificationResponse | None:
        """
        Create a new notification

        Args:
            notification: Notification data

        Returns:
            NotificationResponse if successful, None otherwise
        """
        try:
            supabase = get_supabase_client()

            # Calculate expiration timestamp
            expires_at = None
            if notification.expires_in_days:
                expires_at = datetime.now(UTC) + timedelta(days=notification.expires_in_days)

            # Prepare data for insertion
            data = {
                "user_id": notification.user_id,
                "title": notification.title,
                "message": notification.message,
                "type": notification.type.value,
                "category": notification.category.value if notification.category else None,
                "link": notification.link,
                "metadata": notification.metadata or {},
                "expires_at": expires_at.isoformat() if expires_at else None,
            }

            # Insert into database
            response = supabase.table("admin_notifications").insert(data).execute()

            if response.data and len(response.data) > 0:
                # Invalidate user's cache
                await self._invalidate_user_cache(notification.user_id)

                logger.info(
                    f"Created notification {response.data[0]['id']} for user {notification.user_id}"
                )
                return NotificationResponse(**response.data[0])

            logger.error("Failed to create notification: No data returned")
            return None

        except Exception as e:
            logger.error(f"Failed to create notification: {e}", exc_info=True)
            return None

    async def get_notifications(
        self,
        user_id: int,
        limit: int = 20,
        offset: int = 0,
        is_read: bool | None = None,
        category: str | None = None,
    ) -> list[NotificationResponse]:
        """
        Get notifications for a user with optional filtering

        Args:
            user_id: User ID
            limit: Maximum number of notifications to return
            offset: Offset for pagination
            is_read: Filter by read status (optional)
            category: Filter by category (optional)

        Returns:
            List of notifications
        """
        try:
            supabase = get_supabase_client()

            # Build query
            query = supabase.table("admin_notifications").select("*").eq("user_id", user_id)

            # Apply filters
            if is_read is not None:
                query = query.eq("is_read", is_read)

            if category:
                query = query.eq("category", category)

            # Execute query with pagination
            response = (
                query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
            )

            if response.data:
                return [NotificationResponse(**n) for n in response.data]

            return []

        except Exception as e:
            logger.error(f"Failed to get notifications for user {user_id}: {e}")
            return []

    async def get_unread_count(self, user_id: int) -> int:
        """
        Get unread notification count for a user (with Redis caching)

        Args:
            user_id: User ID

        Returns:
            Number of unread notifications
        """
        try:
            # Try cache first
            redis_client = get_redis_client()
            cache_key = f"{self.CACHE_PREFIX}unread:{user_id}"

            if redis_client:
                cached = redis_client.get(cache_key)
                if cached is not None:
                    return int(cached)

            # Query database if cache miss
            supabase = get_supabase_client()
            response = (
                supabase.table("admin_notifications")
                .select("*", count="exact", head=True)
                .eq("user_id", user_id)
                .eq("is_read", False)
                .execute()
            )

            count = response.count or 0

            # Cache the result
            if redis_client:
                redis_client.setex(cache_key, self.UNREAD_COUNT_TTL, count)

            return count

        except Exception as e:
            logger.error(f"Failed to get unread count for user {user_id}: {e}")
            return 0

    async def mark_as_read(self, notification_id: int, user_id: int) -> bool:
        """
        Mark a notification as read

        Args:
            notification_id: Notification ID
            user_id: User ID (for authorization)

        Returns:
            True if successful, False otherwise
        """
        try:
            supabase = get_supabase_client()

            response = (
                supabase.table("admin_notifications")
                .update({"is_read": True, "read_at": datetime.now(UTC).isoformat()})
                .eq("id", notification_id)
                .eq("user_id", user_id)  # Ensure user owns this notification
                .execute()
            )

            if response.data and len(response.data) > 0:
                await self._invalidate_user_cache(user_id)
                logger.debug(f"Marked notification {notification_id} as read for user {user_id}")
                return True

            logger.warning(
                f"Failed to mark notification {notification_id} as read for user {user_id}"
            )
            return False

        except Exception as e:
            logger.error(
                f"Failed to mark notification {notification_id} as read for user {user_id}: {e}"
            )
            return False

    async def mark_all_read(self, user_id: int) -> bool:
        """
        Mark all notifications as read for a user

        Args:
            user_id: User ID

        Returns:
            True if successful, False otherwise
        """
        try:
            supabase = get_supabase_client()

            response = (  # noqa: F841
                supabase.table("admin_notifications")
                .update({"is_read": True, "read_at": datetime.now(UTC).isoformat()})
                .eq("user_id", user_id)
                .eq("is_read", False)
                .execute()
            )

            await self._invalidate_user_cache(user_id)
            logger.info(f"Marked all notifications as read for user {user_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to mark all notifications as read for user {user_id}: {e}")
            return False

    async def delete_notification(self, notification_id: int, user_id: int) -> bool:
        """
        Delete a notification

        Args:
            notification_id: Notification ID
            user_id: User ID (for authorization)

        Returns:
            True if successful, False otherwise
        """
        try:
            supabase = get_supabase_client()

            response = (
                supabase.table("admin_notifications")
                .delete()
                .eq("id", notification_id)
                .eq("user_id", user_id)  # Ensure user owns this notification
                .execute()
            )

            if response.data is not None:  # Delete returns empty list on success
                await self._invalidate_user_cache(user_id)
                logger.debug(f"Deleted notification {notification_id} for user {user_id}")
                return True

            logger.warning(f"Failed to delete notification {notification_id} for user {user_id}")
            return False

        except Exception as e:
            logger.error(f"Failed to delete notification {notification_id} for user {user_id}: {e}")
            return False

    async def _invalidate_user_cache(self, user_id: int) -> None:
        """
        Invalidate cached data for a user

        Args:
            user_id: User ID
        """
        try:
            redis_client = get_redis_client()
            if redis_client:
                cache_key = f"{self.CACHE_PREFIX}unread:{user_id}"
                redis_client.delete(cache_key)
                logger.debug(f"Invalidated notification cache for user {user_id}")
        except Exception as e:
            logger.debug(f"Failed to invalidate cache for user {user_id}: {e}")


# Global instance
notification_service = NotificationService()
