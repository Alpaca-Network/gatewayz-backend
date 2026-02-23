"""
Share Chat API endpoints.

Provides endpoints for creating and managing shareable chat links.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from src.db.shared_chats import (
    check_share_rate_limit,
    create_shared_chat,
    delete_shared_chat,
    get_shared_chat_by_token,
    get_user_shared_chats,
    verify_session_ownership,
)
from src.schemas.share import (
    CreateShareLinkRequest,
    CreateShareLinkResponse,
    SharedChatPublicView,
    ShareLinksListResponse,
)
from src.security.deps import get_api_key
from src.services.user_lookup_cache import get_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/chat/share", tags=["chat-share"])


@router.post("", response_model=CreateShareLinkResponse)
async def create_share_link(
    request: CreateShareLinkRequest,
    api_key: str = Depends(get_api_key),
):
    """
    Create a shareable link for a chat session.

    The link can be shared publicly and allows anyone with the link
    to view the entire conversation.

    Rate limited to 10 shares per hour per user.
    """
    try:
        # Get authenticated user
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        user_id = user["id"]

        # Check rate limit
        if not check_share_rate_limit(user_id):
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Maximum 10 shares per hour.",
            )

        # Verify user owns the session
        if not verify_session_ownership(request.session_id, user_id):
            raise HTTPException(
                status_code=404,
                detail="Chat session not found or access denied",
            )

        # Parse expires_at if provided
        expires_at = None
        if request.expires_at:
            try:
                expires_at = datetime.fromisoformat(request.expires_at.replace("Z", "+00:00"))
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid expires_at format. Use ISO 8601 format.",
                )

        # Create the share link
        share = create_shared_chat(
            session_id=request.session_id,
            user_id=user_id,
            expires_at=expires_at,
        )

        logger.info(f"Created share link {share['id']} for session {request.session_id} by user {user_id}")

        return CreateShareLinkResponse(
            success=True,
            id=share["id"],
            session_id=share["session_id"],
            share_token=share["share_token"],
            created_by_user_id=share["created_by_user_id"],
            created_at=share["created_at"],
            expires_at=share.get("expires_at"),
            view_count=share["view_count"],
            last_viewed_at=share.get("last_viewed_at"),
            is_active=share["is_active"],
            message="Share link created successfully",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create share link: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create share link: {str(e)}",
        ) from e


@router.get("", response_model=ShareLinksListResponse)
async def get_my_share_links(
    api_key: str = Depends(get_api_key),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """
    Get all share links created by the authenticated user.
    """
    try:
        # Get authenticated user
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        shares = get_user_shared_chats(
            user_id=user["id"],
            limit=limit,
            offset=offset,
        )

        logger.info(f"Retrieved {len(shares)} share links for user {user['id']}")

        return ShareLinksListResponse(
            success=True,
            data=shares,
            count=len(shares),
            message=f"Retrieved {len(shares)} share links",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get share links: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get share links: {str(e)}",
        ) from e


@router.get("/{token}", response_model=SharedChatPublicView)
async def get_shared_chat(
    token: str,
):
    """
    Get a shared chat by its token (public endpoint).

    This endpoint does not require authentication and returns the
    chat conversation including all messages.
    """
    try:
        shared_chat = get_shared_chat_by_token(token)

        if not shared_chat:
            raise HTTPException(
                status_code=404,
                detail="Shared chat not found or has expired",
            )

        logger.info(f"Retrieved shared chat for token {token[:8]}...")

        return SharedChatPublicView(
            success=True,
            session_id=shared_chat["session_id"],
            title=shared_chat["title"],
            model=shared_chat["model"],
            created_at=shared_chat["created_at"],
            messages=shared_chat["messages"],
            message="Shared chat retrieved successfully",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get shared chat: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get shared chat: {str(e)}",
        ) from e


@router.delete("/{token}")
async def delete_share_link(
    token: str,
    api_key: str = Depends(get_api_key),
):
    """
    Delete a share link by its token.

    Only the user who created the share link can delete it.
    """
    try:
        # Get authenticated user
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        success = delete_shared_chat(token, user["id"])

        if not success:
            raise HTTPException(
                status_code=404,
                detail="Share link not found or access denied",
            )

        logger.info(f"Deleted share link {token[:8]}... by user {user['id']}")

        return {
            "success": True,
            "message": "Share link deleted successfully",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete share link: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete share link: {str(e)}",
        ) from e
