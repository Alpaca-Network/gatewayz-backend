"""Schema definitions for share chat functionality."""

from typing import Any

from pydantic import BaseModel


class SharedChatMessage(BaseModel):
    """A message in a shared chat."""

    id: int
    session_id: int
    role: str  # 'user' or 'assistant'
    content: str
    model: str | None = None
    tokens: int | None = 0
    created_at: str | None = None


class CreateShareLinkRequest(BaseModel):
    """Request to create a share link."""

    session_id: int
    expires_at: str | None = None  # ISO 8601 datetime string


class CreateShareLinkResponse(BaseModel):
    """Response from creating a share link."""

    success: bool
    id: int | None = None
    session_id: int | None = None
    share_token: str | None = None
    created_by_user_id: int | None = None
    created_at: str | None = None
    expires_at: str | None = None
    view_count: int | None = None
    last_viewed_at: str | None = None
    is_active: bool | None = None
    message: str | None = None


class ShareLinkData(BaseModel):
    """Data for a single share link."""

    id: int
    session_id: int
    share_token: str
    created_by_user_id: int
    created_at: str | None = None
    expires_at: str | None = None
    view_count: int = 0
    last_viewed_at: str | None = None
    is_active: bool = True


class ShareLinksListResponse(BaseModel):
    """Response for listing share links."""

    success: bool
    data: list[dict[str, Any]]
    count: int
    message: str | None = None


class SharedChatPublicView(BaseModel):
    """Public view of a shared chat."""

    success: bool
    session_id: int | None = None
    title: str | None = None
    model: str | None = None
    created_at: str | None = None
    messages: list[dict[str, Any]] | None = None
    message: str | None = None
    error: str | None = None
