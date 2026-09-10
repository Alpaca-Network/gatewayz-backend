"""Staff management API (Phase A2,
docs/superpowers/specs/2026-09-10-unified-admin-identity-design.md §3).

All routes are superadmin-only except GET /admin/staff (any admin can view
the roster). POST /auth/accept-invite lives here rather than in
src/routes/auth.py because that module belongs to a different in-flight
PR (phase-a-status) -- FastAPI routes are addressed by path, not by the
module they're defined in, so mounting it on this router changes nothing
about the URL a client calls.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.constants import FRONTEND_BETA_URL
from src.db.audit import record_audit
from src.db.staff import (
    accept_invite,
    count_active_superadmins,
    create_invite,
    list_staff,
    revoke_user_keys,
    set_role,
)
from src.db.users import get_user_by_email_ci, get_user_by_id
from src.schemas.staff import AcceptInviteRequest, InviteStaffRequest, UpdateStaffRoleRequest
from src.security.deps import get_current_user, require_admin, require_superadmin

logger = logging.getLogger(__name__)

router = APIRouter()


def _conflict(message: str, code: str, parameter_value: Any = None) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "error": {
                "message": message,
                "type": "conflict_error",
                "code": code,
                "context": {"parameter_value": parameter_value},
            }
        },
    )


def _guard_not_self(admin_user: dict[str, Any], user_id: int) -> None:
    if admin_user.get("id") == user_id:
        raise _conflict("You cannot change your own staff role.", "cannot_change_own_role", user_id)


def _guard_not_last_superadmin(target_user: dict[str, Any], new_role: str) -> None:
    """A superadmin being demoted (role changing away from 'superadmin')
    must not be the last active one -- otherwise nobody could manage staff
    at all."""
    if target_user.get("role") != "superadmin" or new_role == "superadmin":
        return
    if count_active_superadmins() <= 1:
        raise _conflict(
            "Cannot remove the last active superadmin.",
            "last_superadmin",
            target_user.get("id"),
        )


@router.get("/admin/staff", tags=["admin", "staff"])
async def get_staff(_admin_user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """List every user with role in (admin, superadmin). Any admin can view."""
    return {"success": True, "data": {"staff": list_staff()}}


@router.post("/admin/staff/invite", tags=["admin", "staff"])
async def invite_staff(
    body: InviteStaffRequest,
    request: Request,
    admin_user: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    """Grant staff role to an existing Gatewayz account by email, or create
    a pending invite (and email it) for one that doesn't exist yet."""
    email = body.email.strip().lower()

    existing_user = get_user_by_email_ci(email)
    if existing_user is not None:
        updated = set_role(existing_user["id"], body.role, actor=admin_user)
        if updated is None:
            raise HTTPException(status_code=500, detail="Failed to update role")
        record_audit(
            admin_user,
            action="staff.role_granted",
            target_type="user",
            target_id=updated["id"],
            request=request,
            metadata={"role": body.role, "email": email, "invited": False},
        )
        return {"success": True, "data": {"user": updated, "invited": False}}

    invite, raw_token = create_invite(email, body.role, invited_by=admin_user.get("id"))
    if invite is None:
        raise HTTPException(status_code=500, detail="Failed to create invite")

    record_audit(
        admin_user,
        action="staff.invited",
        target_type="admin_invite",
        target_id=invite["id"],
        request=request,
        metadata={"role": body.role, "email": email},
    )

    invite_link = f"{FRONTEND_BETA_URL}/admin/accept-invite?token={raw_token}"

    # Lazy import: src/services/email.py lands in a different, in-flight PR
    # (phase-a-status). Treat any failure to import or send as "email not
    # sent" and hand the link back to the caller instead -- a superadmin can
    # relay it manually rather than the invite silently going nowhere.
    email_sent = False
    try:
        from src.services.email import send_staff_invite

        send_staff_invite(email=email, role=body.role, invite_link=invite_link)
        email_sent = True
    except ImportError:
        logger.info("send_staff_invite not available yet; returning invite_link instead")
    except Exception as e:
        logger.warning("send_staff_invite failed for %s: %s", email, e)

    response_data: dict[str, Any] = {
        "invite": {
            "id": invite["id"],
            "email": invite["email"],
            "role": invite["role"],
            "expires_at": invite["expires_at"],
        },
    }
    if not email_sent:
        response_data["invite_link"] = invite_link

    return {"success": True, "data": response_data}


@router.patch("/admin/staff/{user_id}", tags=["admin", "staff"])
async def update_staff_role(
    user_id: int,
    body: UpdateStaffRoleRequest,
    request: Request,
    admin_user: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    _guard_not_self(admin_user, user_id)

    target_user = get_user_by_id(user_id)
    if target_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    _guard_not_last_superadmin(target_user, body.role)

    updated = set_role(user_id, body.role, actor=admin_user)
    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to update role")

    record_audit(
        admin_user,
        action="staff.role_changed",
        target_type="user",
        target_id=user_id,
        request=request,
        metadata={"previous_role": target_user.get("role"), "new_role": body.role},
    )
    return {"success": True, "data": {"user": updated}}


@router.delete("/admin/staff/{user_id}", tags=["admin", "staff"])
async def remove_staff(
    user_id: int,
    request: Request,
    admin_user: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    """Remove staff access: sets role back to 'user'. Does not delete the
    account or its data."""
    _guard_not_self(admin_user, user_id)

    target_user = get_user_by_id(user_id)
    if target_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    _guard_not_last_superadmin(target_user, "user")

    updated = set_role(user_id, "user", actor=admin_user)
    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to update role")

    record_audit(
        admin_user,
        action="staff.removed",
        target_type="user",
        target_id=user_id,
        request=request,
        metadata={"previous_role": target_user.get("role")},
    )
    return {"success": True, "data": {"user": updated}}


@router.post("/admin/staff/{user_id}/revoke-keys", tags=["admin", "staff"])
async def revoke_staff_keys(
    user_id: int,
    request: Request,
    admin_user: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    target_user = get_user_by_id(user_id)
    if target_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    revoked_count = revoke_user_keys(user_id)

    record_audit(
        admin_user,
        action="staff.keys_revoked",
        target_type="user",
        target_id=user_id,
        request=request,
        metadata={"revoked_count": revoked_count},
    )
    return {"success": True, "data": {"revoked_count": revoked_count}}


@router.post("/auth/accept-invite", tags=["authentication", "staff"])
async def accept_staff_invite(
    body: AcceptInviteRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """The logged-in Gatewayz user claims a pending staff invite -- their
    verified account email must match the invite's email."""
    updated = accept_invite(body.token, user["id"], user.get("email", ""))
    if updated is None:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "message": "Invite is invalid, expired, already accepted, or the email doesn't match your account.",
                    "type": "invalid_request_error",
                    "code": "invite_not_claimable",
                    "context": {"parameter_value": None},
                }
            },
        )

    record_audit(
        {"id": user["id"], "email": user.get("email")},
        action="staff.invite_accepted",
        target_type="user",
        target_id=user["id"],
        request=request,
        metadata={"role": updated.get("role")},
    )
    return {"success": True, "data": {"user": updated}}
