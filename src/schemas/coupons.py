"""Request schemas and redemption invariants for the admin coupons API
(src/routes/admin_coupons.py).

Every bound in here mirrors a constraint that already exists on
``public.coupons`` (supabase/migrations/20251009040000_add_coupon_system.sql,
verified against prod ``ynleroehyrmaafkgjgmr`` on 2026-09-16). Duplicating
them at the boundary is deliberate: a coupon is money, and a violation that
reaches Postgres comes back as an opaque 23514 CHECK error that the admin
panel renders as a raw string. Validating first turns each one into a named,
machine-readable 422.

The DB CHECKs being mirrored:
  value_usd_range              value_usd > 0 AND value_usd <= 1000
  max_uses                     max_uses > 0
  times_used_within_limit      times_used <= max_uses
  valid_date_range             valid_until > valid_from
  user_specific_must_have_user user_specific => assigned_to_user_id NOT NULL
                               global        => assigned_to_user_id IS NULL
  user_specific_max_uses       user_specific => max_uses = 1
  code                         VARCHAR(50) UNIQUE
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

COUPON_SCOPES = ("global", "user_specific")
COUPON_TYPES = ("promotional", "referral", "compensation", "partnership")

MAX_CODE_LENGTH = 50
MAX_VALUE_USD = 1000.0

# Redemption codes are typed by hand off a screenshot or an email. Restricting
# the alphabet keeps "GATEWAYZ" and "WELCOME-50" working while refusing
# whitespace and punctuation that make two codes look identical in a support
# ticket. Codes are stored upper-cased; the table's uniqueness is
# case-sensitive but idx_coupons_code_upper and is_coupon_redeemable() both
# match on UPPER(code), so a lower-case twin would be a redeemable duplicate.
_CODE_RE = re.compile(r"^[A-Z0-9_-]+$")


class CouponValidationError(ValueError):
    """An invariant violation with a machine-readable code.

    Carries `code` so the route can return a stable
    ``error.code`` instead of the panel substring-matching prose.
    """

    def __init__(self, message: str, code: str, parameter_name: str | None = None):
        super().__init__(message)
        self.code = code
        self.parameter_name = parameter_name


def normalize_code(raw: str) -> str:
    """Trim + upper-case a coupon code, rejecting anything unusable."""
    code = (raw or "").strip().upper()
    if not code:
        raise CouponValidationError("Coupon code is required.", "code_required", "code")
    if len(code) > MAX_CODE_LENGTH:
        raise CouponValidationError(
            f"Coupon code must be at most {MAX_CODE_LENGTH} characters.",
            "code_too_long",
            "code",
        )
    if not _CODE_RE.match(code):
        raise CouponValidationError(
            "Coupon code may only contain letters, digits, hyphens and underscores.",
            "code_invalid_characters",
            "code",
        )
    return code


class CreateCouponRequest(BaseModel):
    """POST /admin/coupons body.

    ``extra="forbid"`` on purpose: ``times_used`` is redemption state, not
    configuration, and a caller that thinks it can set it should get a loud
    422 rather than have the field silently dropped.
    """

    model_config = ConfigDict(extra="forbid")

    code: str
    value_usd: float = Field(gt=0, le=MAX_VALUE_USD)
    max_uses: int = Field(gt=0)
    coupon_scope: str = "global"
    coupon_type: str = "promotional"
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    assigned_to_user_id: int | None = None
    description: str | None = None

    @field_validator("coupon_scope")
    @classmethod
    def _validate_scope(cls, v: str) -> str:
        if v not in COUPON_SCOPES:
            raise ValueError(f"coupon_scope must be one of {list(COUPON_SCOPES)}")
        return v

    @field_validator("coupon_type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        if v not in COUPON_TYPES:
            raise ValueError(f"coupon_type must be one of {list(COUPON_TYPES)}")
        return v


class UpdateCouponRequest(BaseModel):
    """PATCH/PUT /admin/coupons/{coupon_id} body -- every field optional.

    The panel sends the whole form on edit and ``{"is_active": true}`` alone
    on reactivate, so both a full and a one-key body have to work. Cross-field
    invariants are checked against the stored row merged with this patch, in
    ``validate_coupon_invariants``.
    """

    model_config = ConfigDict(extra="forbid")

    code: str | None = None
    value_usd: float | None = Field(default=None, gt=0, le=MAX_VALUE_USD)
    max_uses: int | None = Field(default=None, gt=0)
    coupon_scope: str | None = None
    coupon_type: str | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    assigned_to_user_id: int | None = None
    description: str | None = None
    is_active: bool | None = None

    @field_validator("coupon_scope")
    @classmethod
    def _validate_scope(cls, v: str | None) -> str | None:
        if v is not None and v not in COUPON_SCOPES:
            raise ValueError(f"coupon_scope must be one of {list(COUPON_SCOPES)}")
        return v

    @field_validator("coupon_type")
    @classmethod
    def _validate_type(cls, v: str | None) -> str | None:
        if v is not None and v not in COUPON_TYPES:
            raise ValueError(f"coupon_type must be one of {list(COUPON_TYPES)}")
        return v


def _as_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        # Supabase hands back "+00:00" offsets; fromisoformat handles those,
        # and a trailing "Z" only on 3.11+ -- normalise it either way.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise CouponValidationError(
        "Timestamps must be ISO-8601 strings.", "invalid_timestamp", "valid_from"
    )


def validate_coupon_invariants(coupon: dict[str, Any]) -> None:
    """Check every redemption invariant on a fully-merged coupon dict.

    Call this with the complete post-write state (for an update: the stored
    row overlaid with the patch), never with the patch alone -- lowering
    ``max_uses`` below the stored ``times_used`` is only visible when both
    are in hand.

    Raises:
        CouponValidationError: on the first violated invariant.
    """
    scope = coupon.get("coupon_scope")
    assigned = coupon.get("assigned_to_user_id")
    max_uses = coupon.get("max_uses")
    times_used = coupon.get("times_used") or 0
    value_usd = coupon.get("value_usd")

    if value_usd is None or float(value_usd) <= 0:
        raise CouponValidationError(
            "value_usd must be greater than 0.", "value_usd_out_of_range", "value_usd"
        )
    if float(value_usd) > MAX_VALUE_USD:
        raise CouponValidationError(
            f"value_usd must be at most {MAX_VALUE_USD:.2f}.",
            "value_usd_out_of_range",
            "value_usd",
        )

    if max_uses is None or int(max_uses) <= 0:
        raise CouponValidationError(
            "max_uses must be greater than 0.", "max_uses_out_of_range", "max_uses"
        )

    # The one that costs real money: lowering max_uses under the redemptions
    # already granted, or any path that lets times_used run past its ceiling.
    if int(times_used) > int(max_uses):
        raise CouponValidationError(
            f"max_uses ({max_uses}) cannot be lower than times_used ({times_used}); "
            "this coupon has already been redeemed that many times.",
            "max_uses_below_times_used",
            "max_uses",
        )

    if scope not in COUPON_SCOPES:
        raise CouponValidationError(
            f"coupon_scope must be one of {list(COUPON_SCOPES)}.",
            "invalid_coupon_scope",
            "coupon_scope",
        )

    if scope == "user_specific":
        if assigned is None:
            raise CouponValidationError(
                "A user_specific coupon must name assigned_to_user_id.",
                "assigned_user_required",
                "assigned_to_user_id",
            )
        if int(max_uses) != 1:
            raise CouponValidationError(
                "A user_specific coupon must have max_uses = 1.",
                "user_specific_max_uses",
                "max_uses",
            )
    elif assigned is not None:
        raise CouponValidationError(
            "A global coupon must not name assigned_to_user_id.",
            "assigned_user_not_allowed",
            "assigned_to_user_id",
        )

    valid_from = _as_datetime(coupon.get("valid_from"))
    valid_until = _as_datetime(coupon.get("valid_until"))

    # Both columns are NOT NULL in prod; an open-ended coupon is not
    # representable, so refuse it here rather than let Postgres 23502 it.
    if valid_from is None:
        raise CouponValidationError("valid_from is required.", "valid_from_required", "valid_from")
    if valid_until is None:
        raise CouponValidationError(
            "valid_until is required -- the coupons table has no open-ended validity.",
            "valid_until_required",
            "valid_until",
        )
    if valid_until <= valid_from:
        raise CouponValidationError(
            "valid_until must be strictly after valid_from.",
            "invalid_validity_window",
            "valid_until",
        )
