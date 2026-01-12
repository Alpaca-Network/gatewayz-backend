import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from src.config.supabase_config import get_supabase_client
from src.services.user_lookup_cache import get_user
from src.enhanced_notification_service import enhanced_notification_service
from src.schemas.notification import (
    NotificationChannel,
    NotificationPreferences,
    NotificationStats,
    NotificationType,
    SendNotificationRequest,
    UpdateNotificationPreferencesRequest,
)
from src.security.deps import get_api_key, require_admin
from src.services.notification import notification_service

logger = logging.getLogger(__name__)

router = APIRouter()

## Notification Endpoints


@router.get(
    "/user/notifications/preferences",
    response_model=NotificationPreferences,
    tags=["notifications"],
)
async def get_notification_preferences(api_key: str = Depends(get_api_key)):
    """Get user notification preferences"""
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        preferences = notification_service.get_user_preferences(user["id"])
        if not preferences:
            # Create default preferences if they don't exist
            preferences = notification_service.create_user_preferences(user["id"])

        return preferences
    except Exception as e:
        logger.error(f"Error getting notification preferences: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.put("/user/notifications/preferences", tags=["notifications"])
async def update_notification_preferences(
    request: UpdateNotificationPreferencesRequest, api_key: str = Depends(get_api_key)
):
    """Update user notification preferences"""
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Convert request to dict, excluding None values
        updates = {k: v for k, v in request.model_dump().items() if v is not None}

        if not updates:
            raise HTTPException(status_code=400, detail="No updates provided")

        success = notification_service.update_user_preferences(user["id"], updates)

        if not success:
            raise HTTPException(status_code=500, detail="Failed to update preferences")

        return {"status": "success", "message": "Notification preferences updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating notification preferences: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post("/user/notifications/test", tags=["notifications"])
async def test_notification(
    notification_type: NotificationType = Query(..., description="Type of notification to test"),
    api_key: str = Depends(get_api_key),
):
    """Send test notification to a user"""
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Create a test notification based on type
        if notification_type == NotificationType.LOW_BALANCE:
            subject = f"Test Low Balance Alert - {os.environ.get('APP_NAME', 'AI Gateway')}"
            content = f"""
            <html>
            <body>
                <h2>Test Low Balance Alert</h2>
                <p>Hello {user.get('username', 'User')},</p>
                <p>This is a test notification for low balance alerts.</p>
                <p>Current Credits: ${user.get('credits', 0):.2f}</p>
                <p>This is just a test - no action required.</p>
                <p>Best regards,<br>The {os.environ.get('APP_NAME', 'AI Gateway')} Team</p>
            </body>
            </html>
            """
        elif notification_type == NotificationType.TRIAL_EXPIRING:
            subject = f"Test Trial Expiry Alert - {os.environ.get('APP_NAME', 'AI Gateway')}"
            content = f"""
            <html>
            <body>
                <h2>Test Trial Expiry Alert</h2>
                <p>Hello {user.get('username', 'User')},</p>
                <p>This is a test notification for trial expiry alerts.</p>
                <p>This is just a test - no action required.</p>
                <p>Best regards,<br>The {os.environ.get('APP_NAME', 'AI Gateway')} Team</p>
            </body>
            </html>
            """
        elif notification_type == NotificationType.SUBSCRIPTION_EXPIRING:
            subject = f"Test Subscription Expiry Alert - {os.environ.get('APP_NAME', 'AI Gateway')}"
            content = f"""
            <html>
            <body>
                <h2>Test Subscription Expiry Alert</h2>
                <p>Hello {user.get('username', 'User')},</p>
                <p>This is a test notification for subscription expiry alerts.</p>
                <p>This is just a test - no action required.</p>
                <p>Best regards,<br>The {os.environ.get('APP_NAME', 'AI Gateway')} Team</p>
            </body>
            </html>
            """
        else:
            subject = f"Test Notification - {os.environ.get('APP_NAME', 'AI Gateway')}"
            content = f"""
            <html>
            <body>
                <h2>Test Notification</h2>
                <p>Hello {user.get('username', 'User')},</p>
                <p>This is a test notification.</p>
                <p>This is just a test - no action required.</p>
                <p>Best regards,<br>The {os.environ.get('APP_NAME', 'AI Gateway')} Team</p>
            </body>
            </html>
            """

        request = SendNotificationRequest(
            user_id=user["id"],
            type=notification_type,
            channel=NotificationChannel.EMAIL,
            subject=subject,
            content=content,
            metadata={"test": True},
        )

        success = notification_service.create_notification(request)

        return {
            "status": "success" if success else "failed",
            "message": (
                "Test notification sent successfully"
                if success
                else "Failed to send test notification"
            ),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending test notification: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post("/user/notifications/send-usage-report", tags=["notifications"])
async def send_usage_report(
    month: str = Query(..., description="Month to send report for (YYYY-MM)"),
    api_key: str = Depends(get_api_key),
):
    """Send monthly usage report email"""
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # This is a simplified example - you'd need to implement actual usage tracking
        usage_stats = {
            "total_requests": 1000,
            "tokens_used": 50000,
            "credits_spent": 5.00,
            "remaining_credits": user.get("credits", 0),
        }

        success = enhanced_notification_service.send_monthly_usage_report(
            user_id=user["id"],
            username=user["username"],
            email=user["email"],
            month=month,
            usage_stats=usage_stats,
        )

        if success:
            return {"message": "Usage report sent successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to send usage report")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending usage report: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/admin/notifications/stats", response_model=NotificationStats, tags=["admin"])
async def get_notification_stats(admin_user: dict = Depends(require_admin)):
    """Get notification statistics for admin"""
    try:
        client = get_supabase_client()

        # Check if notifications table exists
        try:
            # Get notification counts
            logger.info("Fetching notification counts...")
            result = client.table("notifications").select("status").execute()
            notifications = result.data if result.data else []
        except Exception as table_error:
            if "Could not find the table" in str(table_error):
                logger.warning("Notifications table does not exist yet. Returning empty stats.")
                return NotificationStats(
                    total_notifications=0,
                    sent_notifications=0,
                    failed_notifications=0,
                    pending_notifications=0,
                    delivery_rate=0.0,
                    recent_notifications=[],
                )
            else:
                raise table_error from table_error

        total_notifications = len(notifications)
        sent_notifications = len([n for n in notifications if n["status"] == "sent"])
        failed_notifications = len([n for n in notifications if n["status"] == "failed"])
        pending_notifications = len([n for n in notifications if n["status"] == "pending"])

        delivery_rate = (
            (sent_notifications / total_notifications * 100) if total_notifications > 0 else 0
        )

        # Get last 24-hour notifications - use a simpler approach
        logger.info("Fetching recent notifications...")
        try:
            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            recent_result = (
                client.table("notifications").select("id").gte("created_at", yesterday).execute()
            )
            last_24h_notifications = len(recent_result.data) if recent_result.data else 0
        except Exception as recent_error:
            logger.warning(f"Error fetching recent notifications: {recent_error}")
            # Fallback: get all notifications and filter in Python
            all_notifications = client.table("notifications").select("created_at").execute()
            if all_notifications.data:
                yesterday_dt = datetime.now(timezone.utc) - timedelta(days=1)
                last_24h_notifications = len(
                    [
                        n
                        for n in all_notifications.data
                        if datetime.fromisoformat(n["created_at"].replace("Z", "+00:00"))
                        >= yesterday_dt
                    ]
                )
            else:
                last_24h_notifications = 0

        logger.info(
            f"Notification stats calculated: total={total_notifications}, sent={sent_notifications}, failed={failed_notifications}, pending={pending_notifications}"
        )

        return NotificationStats(
            total_notifications=total_notifications,
            sent_notifications=sent_notifications,
            failed_notifications=failed_notifications,
            pending_notifications=pending_notifications,
            delivery_rate=round(delivery_rate, 2),
            last_24h_notifications=last_24h_notifications,
        )
    except Exception as e:
        logger.error(f"Error getting notification stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}") from e


@router.post("/admin/notifications/process", tags=["admin"])
async def process_notifications(admin_user: dict = Depends(require_admin)):
    """Process all pending notifications (admin only)"""
    try:
        stats = notification_service.process_notifications()

        return {
            "status": "success",
            "message": "Notifications processed successfully",
            "stats": stats,
        }
    except Exception as e:
        logger.error(f"Error processing notifications: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


# =============================================================================
# Admin Dashboard In-App Notifications
# =============================================================================

try:
    from src.models.notification_models import (
        NotificationActionResponse,
        NotificationListResponse,
        NotificationResponse,
        UnreadCountResponse,
    )
    from src.services.notification_service import notification_service as admin_notification_service

    @router.get("/notifications", response_model=NotificationListResponse, tags=["notifications"])
    async def get_admin_notifications(
        limit: int = Query(20, ge=1, le=100, description="Number of notifications to fetch"),
        offset: int = Query(0, ge=0, description="Offset for pagination"),
        is_read: bool | None = Query(None, description="Filter by read status"),
        category: str | None = Query(None, description="Filter by category"),
        admin_user: dict = Depends(require_admin),
    ):
        """Get admin dashboard notifications with filtering and pagination"""
        try:
            notifications = await admin_notification_service.get_notifications(
                user_id=admin_user["id"],
                limit=limit,
                offset=offset,
                is_read=is_read,
                category=category,
            )
            return notifications
        except Exception as e:
            logger.error(f"Error fetching admin notifications: {e}")
            raise HTTPException(status_code=500, detail="Failed to fetch notifications") from e

    @router.get("/notifications/unread-count", response_model=UnreadCountResponse, tags=["notifications"])
    async def get_unread_notification_count(admin_user: dict = Depends(require_admin)):
        """Get count of unread admin notifications"""
        try:
            count = await admin_notification_service.get_unread_count(admin_user["id"])
            return UnreadCountResponse(count=count, timestamp=datetime.now(timezone.utc))
        except Exception as e:
            logger.error(f"Error getting unread count: {e}")
            raise HTTPException(status_code=500, detail="Failed to get unread count") from e

    @router.patch("/notifications/{notification_id}/read", response_model=NotificationActionResponse, tags=["notifications"])
    async def mark_notification_read(
        notification_id: int,
        admin_user: dict = Depends(require_admin),
    ):
        """Mark a single notification as read"""
        try:
            success = await admin_notification_service.mark_as_read(
                notification_id=notification_id,
                user_id=admin_user["id"]
            )
            return NotificationActionResponse(
                success=success,
                message="Notification marked as read" if success else "Failed to mark notification as read",
                notification_id=notification_id
            )
        except Exception as e:
            logger.error(f"Error marking notification as read: {e}")
            raise HTTPException(status_code=500, detail="Failed to mark notification as read") from e

    @router.patch("/notifications/mark-all-read", response_model=NotificationActionResponse, tags=["notifications"])
    async def mark_all_notifications_read(admin_user: dict = Depends(require_admin)):
        """Mark all notifications as read for the current admin"""
        try:
            count = await admin_notification_service.mark_all_as_read(admin_user["id"])
            return NotificationActionResponse(
                success=True,
                message=f"Marked {count} notifications as read"
            )
        except Exception as e:
            logger.error(f"Error marking all notifications as read: {e}")
            raise HTTPException(status_code=500, detail="Failed to mark all notifications as read") from e

    @router.delete("/notifications/{notification_id}", response_model=NotificationActionResponse, tags=["notifications"])
    async def delete_admin_notification(
        notification_id: int,
        admin_user: dict = Depends(require_admin),
    ):
        """Delete a notification"""
        try:
            success = await admin_notification_service.delete_notification(
                notification_id=notification_id,
                user_id=admin_user["id"]
            )
            return NotificationActionResponse(
                success=success,
                message="Notification deleted" if success else "Failed to delete notification",
                notification_id=notification_id
            )
        except Exception as e:
            logger.error(f"Error deleting notification: {e}")
            raise HTTPException(status_code=500, detail="Failed to delete notification") from e

    @router.post("/notifications/test", response_model=NotificationActionResponse, tags=["notifications"])
    async def create_test_admin_notification(admin_user: dict = Depends(require_admin)):
        """Create a test notification for the current admin user"""
        try:
            from src.models.notification_models import NotificationCreate, NotificationType, NotificationCategory

            notification = NotificationCreate(
                user_id=admin_user["id"],
                title="Test Notification",
                message=f"This is a test notification created at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
                type=NotificationType.INFO,
                category=NotificationCategory.SYSTEM,
                link="/admin/dashboard",
                metadata={"test": True, "created_by": admin_user.get("email", "admin")},
                expires_in_days=1,
            )

            result = await admin_notification_service.create_notification(notification)
            return NotificationActionResponse(
                success=True,
                message="Test notification created successfully",
                notification_id=result.id
            )
        except Exception as e:
            logger.error(f"Error creating test notification: {e}")
            raise HTTPException(status_code=500, detail="Failed to create test notification") from e

    logger.info("✓ Admin dashboard notification endpoints loaded successfully")

except ImportError as e:
    logger.warning(f"Admin notification models not available: {e}. Admin notification endpoints not loaded.")
    # Silently skip if notification models don't exist yet
