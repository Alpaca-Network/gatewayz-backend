"""Request/response schemas for the staff management API (Phase A2,
src/routes/admin_staff.py)."""

from pydantic import BaseModel, EmailStr, field_validator

from src.db.staff import STAFF_ROLES


class InviteStaffRequest(BaseModel):
    email: EmailStr
    role: str

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str) -> str:
        if v not in STAFF_ROLES:
            raise ValueError(f"role must be one of {STAFF_ROLES}")
        return v


class UpdateStaffRoleRequest(BaseModel):
    role: str

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str) -> str:
        if v not in STAFF_ROLES:
            raise ValueError(f"role must be one of {STAFF_ROLES}")
        return v


class AcceptInviteRequest(BaseModel):
    token: str
