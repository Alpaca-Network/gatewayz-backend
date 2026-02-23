"""
Data models for admin dashboard notifications
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class NotificationType(str, Enum):  # noqa: UP042
    """Notification type enumeration"""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    SUCCESS = "success"


class NotificationCategory(str, Enum):  # noqa: UP042
    """Notification category enumeration"""

    USER = "user"
    PAYMENT = "payment"
    HEALTH = "health"
    SYSTEM = "system"
    SECURITY = "security"


class NotificationBase(BaseModel):
    """Base notification model with common fields"""

    title: str = Field(..., max_length=255, description="Short notification title")
    message: str = Field(..., description="Detailed notification message")
    type: NotificationType = Field(NotificationType.INFO, description="Visual type")
    category: NotificationCategory | None = Field(
        None, description="Notification category for filtering"
    )
    link: str | None = Field(None, max_length=255, description="Optional navigation URL")
    metadata: dict | None = Field(None, description="Additional context data")


class NotificationCreate(NotificationBase):
    """Schema for creating a new notification"""

    user_id: int = Field(..., description="ID of the user to notify")
    expires_in_days: int | None = Field(
        7, ge=1, le=365, description="Days until notification expires (default: 7)"
    )


class NotificationUpdate(BaseModel):
    """Schema for updating a notification"""

    is_read: bool | None = Field(None, description="Mark as read/unread")


class NotificationResponse(NotificationBase):
    """Schema for notification response"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Notification ID")
    user_id: int = Field(..., description="User ID")
    is_read: bool = Field(..., description="Read status")
    created_at: datetime = Field(..., description="Creation timestamp")
    read_at: datetime | None = Field(None, description="Read timestamp")
    expires_at: datetime | None = Field(None, description="Expiration timestamp")


class UnreadCountResponse(BaseModel):
    """Schema for unread count response"""

    count: int = Field(..., ge=0, description="Number of unread notifications")
    timestamp: datetime = Field(..., description="Response timestamp")


class NotificationListResponse(BaseModel):
    """Schema for list of notifications"""

    notifications: list[NotificationResponse] = Field(
        default_factory=list, description="List of notifications"
    )
    total: int = Field(..., ge=0, description="Total count")
    limit: int = Field(..., ge=1, description="Limit per page")
    offset: int = Field(..., ge=0, description="Offset for pagination")


class NotificationActionResponse(BaseModel):
    """Schema for notification action responses"""

    success: bool = Field(..., description="Whether the action succeeded")
    message: str = Field(..., description="Response message")
    notification_id: int | None = Field(None, description="Affected notification ID")
