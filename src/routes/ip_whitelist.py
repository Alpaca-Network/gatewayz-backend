"""
IP Whitelist Management Routes

Admin endpoints for managing IP whitelists that bypass rate limiting.
"""

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, validator

from src.db.ip_whitelist import (
    create_whitelist_entry,
    delete_whitelist_entry,
    get_all_whitelist_entries,
    get_whitelist_entries,
    get_whitelist_entry_by_id,
    is_ip_whitelisted,
    update_whitelist_entry,
)
from src.db.users import get_user_by_id
from src.security.deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


# --- Request/Response Schemas ---


class CreateWhitelistRequest(BaseModel):
    """Request to create a new IP whitelist entry"""

    ip_address: str = Field(
        ...,
        description="IP address or CIDR range (e.g., '203.0.113.5' or '203.0.113.0/24')",
        example="182.160.0.40",
    )
    reason: str = Field(
        ...,
        description="Reason for whitelisting this IP",
        example="Known good client experiencing false positives",
    )
    user_id: Optional[str] = Field(
        None,
        description="Optional user ID to associate with (null = global whitelist)",
    )
    expires_at: Optional[datetime] = Field(
        None,
        description="Optional expiration datetime (null = never expires)",
    )
    metadata: Optional[dict[str, Any]] = Field(
        default_factory=dict,
        description="Additional context data",
    )

    @validator("ip_address")
    def validate_ip_address(cls, v):
        """Validate IP address or CIDR range format"""
        import ipaddress

        try:
            ipaddress.ip_network(v, strict=False)
            return v
        except ValueError as e:
            raise ValueError(f"Invalid IP address or CIDR range: {e}")


class UpdateWhitelistRequest(BaseModel):
    """Request to update an IP whitelist entry"""

    enabled: Optional[bool] = Field(None, description="Enable or disable this entry")
    reason: Optional[str] = Field(None, description="Update the reason")
    expires_at: Optional[datetime] = Field(None, description="Update expiration datetime")
    metadata: Optional[dict[str, Any]] = Field(None, description="Update metadata")


class WhitelistEntryResponse(BaseModel):
    """IP whitelist entry response"""

    id: str
    ip_address: str
    user_id: Optional[str]
    reason: str
    created_by: str
    enabled: bool
    expires_at: Optional[datetime]
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class CheckIPWhitelistRequest(BaseModel):
    """Request to check if an IP is whitelisted"""

    ip_address: str = Field(..., description="IP address to check", example="182.160.0.40")
    user_id: Optional[str] = Field(
        None,
        description="Optional user ID to check user-specific whitelists",
    )


class CheckIPWhitelistResponse(BaseModel):
    """Response for IP whitelist check"""

    ip_address: str
    is_whitelisted: bool
    user_id: Optional[str] = None


# --- Helper Functions ---


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """Dependency to require admin role"""
    user_role = current_user.get("role", "user")
    if user_role not in ["admin", "superadmin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


# --- Routes ---


@router.post(
    "/api/admin/ip-whitelist",
    response_model=WhitelistEntryResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Admin - IP Whitelist"],
    summary="Create IP whitelist entry",
    description="Create a new IP whitelist entry to bypass rate limiting",
)
async def create_ip_whitelist(
    request: CreateWhitelistRequest,
    current_user: dict = Depends(require_admin),
):
    """Create a new IP whitelist entry"""
    try:
        # Verify user exists if user_id provided
        if request.user_id:
            user = get_user_by_id(request.user_id)
            if not user:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"User not found: {request.user_id}",
                )

        # Create whitelist entry
        entry = create_whitelist_entry(
            ip_address=request.ip_address,
            reason=request.reason,
            created_by=current_user["id"],
            user_id=request.user_id,
            expires_at=request.expires_at,
            metadata=request.metadata,
        )

        if not entry:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create IP whitelist entry",
            )

        logger.info(
            f"Admin {current_user['email']} created IP whitelist entry: "
            f"{request.ip_address} (user_id: {request.user_id or 'global'})"
        )

        return entry

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating IP whitelist entry: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.get(
    "/api/admin/ip-whitelist",
    response_model=list[WhitelistEntryResponse],
    tags=["Admin - IP Whitelist"],
    summary="List IP whitelist entries",
    description="Get all IP whitelist entries",
)
async def list_ip_whitelists(
    user_id: Optional[str] = None,
    enabled_only: bool = True,
    include_expired: bool = False,
    current_user: dict = Depends(require_admin),
):
    """List IP whitelist entries"""
    try:
        if user_id:
            # Get entries for specific user
            entries = get_whitelist_entries(
                user_id=user_id,
                enabled_only=enabled_only,
                include_expired=include_expired,
            )
        else:
            # Get all entries
            entries = get_all_whitelist_entries(
                enabled_only=enabled_only,
                include_expired=include_expired,
            )

        return entries

    except Exception as e:
        logger.error(f"Error listing IP whitelist entries: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.get(
    "/api/admin/ip-whitelist/{entry_id}",
    response_model=WhitelistEntryResponse,
    tags=["Admin - IP Whitelist"],
    summary="Get IP whitelist entry",
    description="Get a specific IP whitelist entry by ID",
)
async def get_ip_whitelist(
    entry_id: str,
    current_user: dict = Depends(require_admin),
):
    """Get a specific IP whitelist entry"""
    try:
        entry = get_whitelist_entry_by_id(entry_id)

        if not entry:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"IP whitelist entry not found: {entry_id}",
            )

        return entry

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting IP whitelist entry: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.patch(
    "/api/admin/ip-whitelist/{entry_id}",
    response_model=WhitelistEntryResponse,
    tags=["Admin - IP Whitelist"],
    summary="Update IP whitelist entry",
    description="Update an IP whitelist entry (enable/disable, reason, expiration)",
)
async def update_ip_whitelist(
    entry_id: str,
    request: UpdateWhitelistRequest,
    current_user: dict = Depends(require_admin),
):
    """Update an IP whitelist entry"""
    try:
        # Check entry exists
        existing = get_whitelist_entry_by_id(entry_id)
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"IP whitelist entry not found: {entry_id}",
            )

        # Update entry
        entry = update_whitelist_entry(
            entry_id=entry_id,
            enabled=request.enabled,
            reason=request.reason,
            expires_at=request.expires_at,
            metadata=request.metadata,
        )

        if not entry:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update IP whitelist entry",
            )

        logger.info(
            f"Admin {current_user['email']} updated IP whitelist entry: {entry_id} "
            f"({existing['ip_address']})"
        )

        return entry

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating IP whitelist entry: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.delete(
    "/api/admin/ip-whitelist/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Admin - IP Whitelist"],
    summary="Delete IP whitelist entry",
    description="Delete an IP whitelist entry",
)
async def delete_ip_whitelist(
    entry_id: str,
    current_user: dict = Depends(require_admin),
):
    """Delete an IP whitelist entry"""
    try:
        # Check entry exists
        existing = get_whitelist_entry_by_id(entry_id)
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"IP whitelist entry not found: {entry_id}",
            )

        # Delete entry
        success = delete_whitelist_entry(entry_id)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete IP whitelist entry",
            )

        logger.info(
            f"Admin {current_user['email']} deleted IP whitelist entry: {entry_id} "
            f"({existing['ip_address']})"
        )

        return None

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting IP whitelist entry: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.post(
    "/api/admin/ip-whitelist/check",
    response_model=CheckIPWhitelistResponse,
    tags=["Admin - IP Whitelist"],
    summary="Check if IP is whitelisted",
    description="Check if a specific IP address is whitelisted",
)
async def check_ip_whitelist(
    request: CheckIPWhitelistRequest,
    current_user: dict = Depends(require_admin),
):
    """Check if an IP address is whitelisted"""
    try:
        is_whitelisted = is_ip_whitelisted(
            ip_address=request.ip_address,
            user_id=request.user_id,
        )

        return {
            "ip_address": request.ip_address,
            "is_whitelisted": is_whitelisted,
            "user_id": request.user_id,
        }

    except Exception as e:
        logger.error(f"Error checking IP whitelist: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
