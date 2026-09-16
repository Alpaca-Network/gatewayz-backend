"""Admin coupons API.

The admin panel has shipped a complete Coupons page, proxy routes and service
layer against these paths for months; the backend half never existed (see
"Gatewayz - Phantom Column Failures, Admin Dashboard Batch 1 (Sep 15, 2026)",
"Left open deliberately"). The request/response shapes here are therefore not
a new design -- they are fixed by
``admin-panel/src/services/coupons-api.ts`` and the proxy routes under
``admin-panel/src/app/api/proxy/admin/coupons/``. Responses are bare objects,
not the ``{"success", "data"}`` envelope used by src/routes/admin_staff.py,
because that is what the panel's existing service parses.

Auth, and why:
  GET  (list, detail, analytics, stats)  require_admin
  POST / PATCH / PUT                     require_admin -- consistent with
      src/routes/credits.py, where granting credits (the same money, by a
      more direct route) is an admin action; every write is audited.
  DELETE                                 require_admin to deactivate,
      require_superadmin to hard-delete. The panel's "Deactivate" button
      calls DELETE and then expects the row to still be there for its
      "Reactivate" button, so a plain DELETE is a soft deactivation. A real
      row delete needs ``?hard=true``, superadmin, and no redemption history
      -- ``coupon_redemptions.coupon_id`` is ON DELETE CASCADE, so hard-
      deleting a redeemed coupon destroys the financial record of the money
      it granted.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.db.audit import record_audit
from src.db.coupons import (
    RedemptionScanTooLarge,
    count_redemptions,
    create_coupon,
    delete_coupon,
    get_coupon,
    get_coupon_by_code,
    get_coupon_counts,
    get_redemption_stats,
    list_coupons,
    update_coupon,
)
from src.schemas.coupons import (
    COUPON_SCOPES,
    COUPON_TYPES,
    CouponValidationError,
    CreateCouponRequest,
    UpdateCouponRequest,
    normalize_code,
    validate_coupon_invariants,
)
from src.security.deps import require_admin

logger = logging.getLogger(__name__)

router = APIRouter()

# Fields a caller may set. `times_used` is absent on purpose: it is redemption
# state, and a coupon that can be redeemed more times than intended is a
# direct financial loss. Nothing in this module writes it.
_WRITABLE_FIELDS = (
    "code",
    "description",
    "coupon_type",
    "coupon_scope",
    "value_usd",
    "assigned_to_user_id",
    "max_uses",
    "valid_from",
    "valid_until",
    "is_active",
)


def _error(status: int, message: str, code: str, error_type: str, value: Any = None):
    return HTTPException(
        status_code=status,
        detail={
            "error": {
                "message": message,
                "type": error_type,
                "code": code,
                "context": {"parameter_value": value},
            }
        },
    )


def _unavailable(code: str, message: str) -> HTTPException:
    """503 for a failed query.

    Never return an empty list or a zero instead: an empty result and a broken
    query must stay distinguishable, which is the whole lesson of the Sep 15
    phantom-column incident.
    """
    return _error(503, message, code, "service_unavailable")


def _invalid(exc: CouponValidationError) -> HTTPException:
    return _error(422, str(exc), exc.code, "invalid_request_error", exc.parameter_name)


def _not_found(coupon_id: int) -> HTTPException:
    return _error(
        404, f"Coupon {coupon_id} not found.", "coupon_not_found", "not_found_error", coupon_id
    )


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce a stored row into the shape the panel's `Coupon` type declares.

    Supabase returns NUMERIC as a string in some driver versions and as a
    float in others; the panel does arithmetic on ``value_usd`` and formats
    it as currency, so the coercion happens here rather than in the browser.
    """
    return {
        "id": int(row["id"]),
        "code": row.get("code"),
        "description": row.get("description"),
        "coupon_type": row.get("coupon_type"),
        "coupon_scope": row.get("coupon_scope"),
        "value_usd": float(row.get("value_usd") or 0),
        "assigned_to_user_id": (
            int(row["assigned_to_user_id"]) if row.get("assigned_to_user_id") is not None else None
        ),
        "max_uses": int(row.get("max_uses") or 0),
        "times_used": int(row.get("times_used") or 0),
        "valid_from": _iso(row.get("valid_from")),
        "valid_until": _iso(row.get("valid_until")),
        "is_active": bool(row.get("is_active")),
        "created_by": int(row["created_by"]) if row.get("created_by") is not None else None,
        "created_by_type": row.get("created_by_type"),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def _load_coupon_or_404(coupon_id: int) -> dict[str, Any]:
    try:
        row = get_coupon(coupon_id)
    except Exception as e:
        logger.error("get_coupon(%s) failed: %s", coupon_id, e, exc_info=True)
        raise _unavailable("coupon_lookup_unavailable", "Coupon lookup is temporarily unavailable.")
    if row is None:
        raise _not_found(coupon_id)
    return row


def _assert_code_available(code: str, *, exclude_id: int | None = None) -> None:
    try:
        clash = get_coupon_by_code(code, exclude_id=exclude_id)
    except Exception as e:
        logger.error("coupon code uniqueness check failed: %s", e, exc_info=True)
        raise _unavailable("coupon_lookup_unavailable", "Coupon lookup is temporarily unavailable.")
    if clash is not None:
        raise _error(
            409,
            f"Coupon code '{code}' already exists.",
            "coupon_code_taken",
            "conflict_error",
            code,
        )


@router.get("/admin/coupons", tags=["admin", "coupons"])
async def list_admin_coupons(
    scope: str | None = Query(None, description="global | user_specific"),
    coupon_type: str | None = Query(None, description="promotional | referral | ..."),
    is_active: bool | None = Query(None),
    search: str | None = Query(None, description="Substring of code or description"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _admin_user: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Paginated coupon list. Query parameters match what the panel's
    ``listCoupons`` builds."""
    if scope is not None and scope not in COUPON_SCOPES:
        raise _error(
            422,
            f"scope must be one of {list(COUPON_SCOPES)}.",
            "invalid_coupon_scope",
            "invalid_request_error",
            scope,
        )
    if coupon_type is not None and coupon_type not in COUPON_TYPES:
        raise _error(
            422,
            f"coupon_type must be one of {list(COUPON_TYPES)}.",
            "invalid_coupon_type",
            "invalid_request_error",
            coupon_type,
        )

    try:
        rows, total = list_coupons(
            scope=scope,
            coupon_type=coupon_type,
            is_active=is_active,
            search=search,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        logger.error("GET /admin/coupons failed: %s", e, exc_info=True)
        raise _unavailable("coupons_unavailable", "Coupons are temporarily unavailable.")

    return {
        "coupons": [_serialize(row) for row in rows],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/admin/coupons/stats/overview", tags=["admin", "coupons"])
async def coupon_stats_overview(
    _admin_user: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Catalog-wide coupon counters and redemption totals.

    Declared before ``/admin/coupons/{coupon_id}`` deliberately: FastAPI
    matches in declaration order, and "stats" would otherwise be parsed as an
    int path parameter and 422 before ever reaching this handler.

    Redemption figures come from ``coupon_redemptions`` -- the ledger -- not
    from summing ``coupons.times_used``.
    """
    try:
        counts = get_coupon_counts()
    except Exception as e:
        logger.error("coupon counts failed: %s", e, exc_info=True)
        raise _unavailable("coupon_stats_unavailable", "Coupon stats are temporarily unavailable.")

    try:
        redemptions = get_redemption_stats(None)
    except RedemptionScanTooLarge as e:
        logger.error("coupon overview redemption scan too large: %s", e)
        raise _unavailable(
            "coupon_stats_unavailable",
            "Redemption history is too large to aggregate through the API.",
        )
    except Exception as e:
        logger.error("coupon overview redemption stats failed: %s", e, exc_info=True)
        raise _unavailable("coupon_stats_unavailable", "Coupon stats are temporarily unavailable.")

    total_redemptions = redemptions["total_redemptions"]
    total_value = redemptions["total_value_distributed"]
    average = round(total_value / total_redemptions, 2) if total_redemptions else 0.0

    return {
        "total_coupons": counts["total_coupons"],
        "active_coupons": counts["active_coupons"],
        "user_specific_coupons": counts["user_specific_coupons"],
        "global_coupons": counts["global_coupons"],
        "total_redemptions": total_redemptions,
        "unique_redeemers": redemptions["unique_users"],
        "total_value_distributed": total_value,
        "average_redemption_value": average,
    }


@router.get("/admin/coupons/{coupon_id}", tags=["admin", "coupons"])
async def get_admin_coupon(
    coupon_id: int,
    _admin_user: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    return _serialize(_load_coupon_or_404(coupon_id))


@router.get("/admin/coupons/{coupon_id}/analytics", tags=["admin", "coupons"])
async def get_admin_coupon_analytics(
    coupon_id: int,
    _admin_user: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Per-coupon redemption metrics.

    ``remaining_uses`` and ``redemption_rate`` are derived from
    ``coupons.times_used`` -- the counter the redemption ceiling is actually
    enforced against -- while the redemption totals come from the
    ``coupon_redemptions`` ledger. They are separate numbers on purpose: if
    they ever disagree, the page shows both rather than one invented
    reconciliation.
    """
    coupon = _load_coupon_or_404(coupon_id)

    try:
        stats = get_redemption_stats(coupon_id)
    except RedemptionScanTooLarge as e:
        logger.error("coupon %s redemption scan too large: %s", coupon_id, e)
        raise _unavailable(
            "coupon_analytics_unavailable",
            "Redemption history is too large to aggregate through the API.",
        )
    except Exception as e:
        logger.error("coupon %s analytics failed: %s", coupon_id, e, exc_info=True)
        raise _unavailable(
            "coupon_analytics_unavailable", "Coupon analytics are temporarily unavailable."
        )

    serialized = _serialize(coupon)
    max_uses = serialized["max_uses"]
    times_used = serialized["times_used"]
    remaining = max(max_uses - times_used, 0)
    rate = round((times_used / max_uses) * 100, 2) if max_uses else 0.0

    valid_until = serialized["valid_until"]
    is_expired = False
    if valid_until:
        try:
            is_expired = datetime.fromisoformat(valid_until.replace("Z", "+00:00")) < datetime.now(
                UTC
            )
        except ValueError:
            logger.warning("coupon %s has unparseable valid_until %r", coupon_id, valid_until)

    return {
        "coupon": serialized,
        "total_redemptions": stats["total_redemptions"],
        "unique_users": stats["unique_users"],
        "total_value_distributed": stats["total_value_distributed"],
        "redemption_rate": rate,
        "remaining_uses": remaining,
        "is_expired": is_expired,
    }


@router.post("/admin/coupons", status_code=201, tags=["admin", "coupons"])
async def create_admin_coupon(
    body: CreateCouponRequest,
    request: Request,
    admin_user: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Create a coupon. Every redemption invariant is checked before the
    insert so a violation is a named 422, not an opaque Postgres 23514."""
    try:
        code = normalize_code(body.code)
    except CouponValidationError as e:
        raise _invalid(e)

    now = datetime.now(UTC)
    candidate: dict[str, Any] = {
        "code": code,
        "description": body.description,
        "coupon_type": body.coupon_type,
        "coupon_scope": body.coupon_scope,
        "value_usd": body.value_usd,
        "assigned_to_user_id": body.assigned_to_user_id,
        "max_uses": body.max_uses,
        "times_used": 0,
        "valid_from": body.valid_from or now,
        "valid_until": body.valid_until,
        "is_active": True,
    }

    try:
        validate_coupon_invariants(candidate)
    except CouponValidationError as e:
        raise _invalid(e)

    _assert_code_available(code)

    values = dict(candidate)
    values.pop("times_used")  # column default; never written by this API
    values["valid_from"] = _iso(values["valid_from"])
    values["valid_until"] = _iso(values["valid_until"])
    # The env-key admin sentinel has no "id"; created_by is a FK to users(id),
    # so leave it NULL rather than inventing an actor.
    values["created_by"] = admin_user.get("id")
    values["created_by_type"] = "admin"

    try:
        row = create_coupon(values)
    except Exception as e:
        logger.error("POST /admin/coupons failed: %s", e, exc_info=True)
        raise _unavailable("coupon_create_failed", "Could not create the coupon.")

    serialized = _serialize(row)
    record_audit(
        admin_user,
        action="coupon.created",
        target_type="coupon",
        target_id=serialized["id"],
        request=request,
        metadata={
            "code": serialized["code"],
            "value_usd": serialized["value_usd"],
            "max_uses": serialized["max_uses"],
            "coupon_scope": serialized["coupon_scope"],
            "coupon_type": serialized["coupon_type"],
            "assigned_to_user_id": serialized["assigned_to_user_id"],
        },
    )
    return serialized


@router.patch("/admin/coupons/{coupon_id}", tags=["admin", "coupons"])
@router.put("/admin/coupons/{coupon_id}", tags=["admin", "coupons"])
async def update_admin_coupon(
    coupon_id: int,
    body: UpdateCouponRequest,
    request: Request,
    admin_user: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Update a coupon.

    PATCH is what the panel sends (both the full edit form and the one-key
    ``{"is_active": true}`` reactivation); PUT is mounted on the same handler
    so either verb works.

    Invariants are checked against the stored row merged with the patch --
    lowering ``max_uses`` below the ``times_used`` already granted is only
    visible with both in hand.
    """
    existing = _load_coupon_or_404(coupon_id)

    patch = body.model_dump(exclude_unset=True)
    patch = {k: v for k, v in patch.items() if k in _WRITABLE_FIELDS}

    if "code" in patch:
        try:
            patch["code"] = normalize_code(patch["code"])
        except CouponValidationError as e:
            raise _invalid(e)

    merged = dict(existing)
    merged.update(patch)

    try:
        validate_coupon_invariants(merged)
    except CouponValidationError as e:
        raise _invalid(e)

    if "code" in patch and patch["code"] != (existing.get("code") or "").upper():
        _assert_code_available(patch["code"], exclude_id=coupon_id)

    write = dict(patch)
    for key in ("valid_from", "valid_until"):
        if key in write:
            write[key] = _iso(write[key])

    try:
        row = update_coupon(coupon_id, write)
    except Exception as e:
        logger.error("PATCH /admin/coupons/%s failed: %s", coupon_id, e, exc_info=True)
        raise _unavailable("coupon_update_failed", "Could not update the coupon.")

    if row is None:
        raise _not_found(coupon_id)

    serialized = _serialize(row)
    record_audit(
        admin_user,
        action="coupon.updated",
        target_type="coupon",
        target_id=coupon_id,
        request=request,
        metadata={
            "code": serialized["code"],
            "changed_fields": sorted(patch.keys()),
            "previous": {k: _iso(existing.get(k)) for k in patch},
        },
    )
    return serialized


@router.delete("/admin/coupons/{coupon_id}", tags=["admin", "coupons"])
async def delete_admin_coupon(
    coupon_id: int,
    request: Request,
    hard: bool = Query(
        False,
        description="Permanently delete the row instead of deactivating it. "
        "Superadmin only, and refused once the coupon has been redeemed.",
    ),
    admin_user: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """Deactivate a coupon (default), or hard-delete it with ``?hard=true``.

    The panel's "Deactivate" button calls DELETE and then offers
    "Reactivate", which PATCHes ``is_active`` back to true -- so the default
    has to leave the row in place. ``?hard=true`` is the real delete: it needs
    superadmin, and it is refused for any coupon with redemption history,
    because ``coupon_redemptions.coupon_id`` is ON DELETE CASCADE and the
    record of money already granted must not vanish with the coupon.
    """
    existing = _load_coupon_or_404(coupon_id)

    if not hard:
        try:
            row = update_coupon(coupon_id, {"is_active": False})
        except Exception as e:
            logger.error("DELETE /admin/coupons/%s failed: %s", coupon_id, e, exc_info=True)
            raise _unavailable("coupon_delete_failed", "Could not deactivate the coupon.")
        if row is None:
            raise _not_found(coupon_id)

        record_audit(
            admin_user,
            action="coupon.deactivated",
            target_type="coupon",
            target_id=coupon_id,
            request=request,
            metadata={"code": existing.get("code"), "times_used": existing.get("times_used")},
        )
        return {
            "success": True,
            "message": f"Coupon {existing.get('code')} deactivated.",
            "coupon": _serialize(row),
        }

    if admin_user.get("role") != "superadmin":
        raise _error(
            403,
            "Permanently deleting a coupon requires superadmin privileges.",
            "superadmin_required",
            "permission_error",
            coupon_id,
        )

    try:
        redemptions = count_redemptions(coupon_id)
    except Exception as e:
        logger.error("redemption count for coupon %s failed: %s", coupon_id, e, exc_info=True)
        # Fail closed: an unknown redemption count must not read as "safe to
        # cascade-delete the ledger".
        raise _unavailable(
            "coupon_delete_failed", "Could not verify redemption history before deleting."
        )

    times_used = int(existing.get("times_used") or 0)
    if redemptions > 0 or times_used > 0:
        raise _error(
            409,
            f"Coupon {existing.get('code')} has been redeemed "
            f"({times_used} use(s), {redemptions} ledger entries) and cannot be deleted. "
            "Deactivate it instead.",
            "coupon_has_redemptions",
            "conflict_error",
            coupon_id,
        )

    try:
        deleted = delete_coupon(coupon_id)
    except Exception as e:
        logger.error("hard delete of coupon %s failed: %s", coupon_id, e, exc_info=True)
        raise _unavailable("coupon_delete_failed", "Could not delete the coupon.")

    if not deleted:
        raise _not_found(coupon_id)

    record_audit(
        admin_user,
        action="coupon.deleted",
        target_type="coupon",
        target_id=coupon_id,
        request=request,
        metadata={
            "code": existing.get("code"),
            "value_usd": float(existing.get("value_usd") or 0),
            "max_uses": existing.get("max_uses"),
        },
    )
    return {"success": True, "message": f"Coupon {existing.get('code')} permanently deleted."}
