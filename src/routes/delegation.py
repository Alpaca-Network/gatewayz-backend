"""Delegated staking API -- an inference allowance for stake delegated to
Gatewayz.

Users stake ETH from their own wallet into the Gatewayz StakeWise V3 vault,
or delegate ADA to the Gatewayz Cardano pool. The stake stays in their
control; its staking rewards reach us through the vault fee / pool margin,
and we grant inference credits at a rate WE set. That rate can change at any
time and is published as the current rate -- it is not a guaranteed or fixed
return, and nothing here may present it as one.

Endpoints:
    GET   /delegation/status                public: config + current rates
    GET   /delegation/rewards               the caller's positions + allowance
    GET   /admin/delegation/rates           rates + suggested rates (admin)
    PUT   /admin/delegation/rates           set rates (superadmin, audited)
    POST  /admin/delegation/run             run a job now (superadmin, audited)
    GET   /admin/delegation/reconciliation  revenue vs cost per asset (admin)
    POST  /admin/delegation/resume          un-pause an asset (superadmin, audited)

Numbers are strings, like the holdings API.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.config.config import Config
from src.db.audit import record_audit
from src.db.delegation import (
    deactivate_rate,
    get_active_rates,
    get_latest_measurements,
    list_accruals_for_wallet,
    list_recent_revenue,
    replace_active_rate,
    resume_accruals,
)
from src.db.user_wallets import get_wallets_for_user
from src.schemas.delegation import (
    ResumeDelegationRequest,
    RunDelegationRequest,
    UpdateDelegationRatesRequest,
)
from src.security.deps import get_current_user, require_admin_or_env_key, require_superadmin
from src.services.delegation import stakewise
from src.services.delegation.measurements import (
    cardano_pool_id,
    run_delegation_measurements_once,
)
from src.services.delegation.reconciliation import (
    reconciliation_view,
    run_delegation_reconciliation_once,
)
from src.services.delegation.rewards import (
    DelegationMeasurementsMissingError,
    asset_configured,
    daily_cap,
    estimate_daily_credits,
    rate_for,
    run_delegation_accruals_once,
    suggested_rate,
)

logger = logging.getLogger(__name__)

router = APIRouter()

DISCLAIMER = "Rates are set by Gatewayz, can change at any time, and are not a guaranteed return."

_HISTORY_DAYS = 30
_TOTALS_ROW_LIMIT = 1000
_NATIVE_DECIMALS = {"eth": 18, "ada": 6}
# The allowance is shown as "99% off up to $X/month" -- X is 30 days of the
# daily estimate, at 1 credit = $1.
_MONTH_DAYS = 30


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def _num(value: Any) -> str:
    return format(_decimal(value), "f")


def _native_amount(asset: str, raw: Any) -> str:
    decimals = _NATIVE_DECIMALS.get(asset, 0)
    value = _decimal(raw) / (Decimal(10) ** decimals)
    return format(value.quantize(Decimal(1).scaleb(-decimals)), "f")


def _public_rates() -> list[dict[str, str]]:
    if not Config.DELEGATED_STAKING_ENABLED:
        return []
    rates = get_active_rates()
    return [
        {"asset": asset, "credits_per_1k_usd_per_day": _num(rate_for(rates, asset))}
        for asset in ("eth", "ada")
        if asset in rates and asset_configured(asset)
    ]


def _fee_percent() -> str | None:
    if not Config.DELEGATED_STAKING_ENABLED:
        return None
    bps = stakewise.cached_fee_percent_bps()
    if bps is None:
        return None
    return format(Decimal(bps) / Decimal(100), ".2f")


@router.get("/delegation/status", tags=["delegation"])
async def get_delegation_status() -> dict[str, Any]:
    """Public: is delegated staking on, where to stake, the current
    allowance rates, and the disclaimer that goes with them."""
    return {
        "success": True,
        "data": {
            "enabled": bool(Config.DELEGATED_STAKING_ENABLED),
            "eth": {
                "vault_address": stakewise.vault_address(),
                "chain_id": int(Config.STAKEWISE_VAULT_CHAIN_ID),
                "fee_percent": _fee_percent(),
            },
            "cardano": {"pool_id": cardano_pool_id()},
            "allowance_rates": _public_rates(),
            "disclaimer": DISCLAIMER,
        },
    }


@router.get("/delegation/rewards", tags=["delegation"])
async def get_delegation_rewards(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """The caller's delegated positions (latest measurement per wallet and
    asset), the allowance they would earn per day at the current rates, and
    their accrual totals and last 30 days. The estimate uses the LATEST
    measurement; what is paid uses the day's lowest."""
    rates = get_active_rates() if Config.DELEGATED_STAKING_ENABLED else {}
    positions: list[dict[str, Any]] = []
    estimate_inputs: list[tuple[str, Decimal]] = []
    accruals: list[dict[str, Any]] = []

    for wallet in get_wallets_for_user(user["id"]):
        address = str(wallet.get("wallet_address") or "")
        if not address:
            continue
        for row in get_latest_measurements(address):
            asset = str(row.get("asset"))
            usd = _decimal(row.get("usd_value"))
            positions.append(
                {
                    "asset": asset,
                    "wallet_address": address,
                    "amount": _native_amount(asset, row.get("amount_raw")),
                    "usd_value": _num(usd),
                    "measured_at": row.get("taken_at"),
                }
            )
            if asset_configured(asset):
                estimate_inputs.append((asset, usd))
        accruals.extend(list_accruals_for_wallet(address, limit=_TOTALS_ROW_LIMIT))

    per_day = estimate_daily_credits(estimate_inputs, rates)
    pending = sum(
        (_decimal(a.get("credits")) for a in accruals if a.get("status") == "pending"), Decimal(0)
    )
    paid = sum(
        (_decimal(a.get("credits")) for a in accruals if a.get("status") == "paid"), Decimal(0)
    )
    cutoff = (datetime.now(UTC).date() - timedelta(days=_HISTORY_DAYS)).isoformat()
    history = sorted(
        (a for a in accruals if str(a.get("reward_date")) >= cutoff),
        key=lambda a: (str(a.get("reward_date")), str(a.get("asset"))),
        reverse=True,
    )

    return {
        "success": True,
        "data": {
            "enabled": bool(Config.DELEGATED_STAKING_ENABLED),
            "positions": positions,
            "allowance": {
                "credits_per_day_estimate": _num(per_day),
                "month_estimate_usd": _num(per_day * _MONTH_DAYS),
                "daily_cap_credits": _num(daily_cap()),
            },
            "totals": {"pending": _num(pending), "paid": _num(paid)},
            "history": [
                {
                    "reward_date": a.get("reward_date"),
                    "asset": a.get("asset"),
                    "wallet_address": a.get("wallet_address"),
                    "usd_basis": _num(a.get("usd_basis")),
                    "credits": _num(a.get("credits")),
                    "status": a.get("status"),
                }
                for a in history
            ],
            "disclaimer": DISCLAIMER,
        },
    }


def _rates_view() -> dict[str, Any]:
    rates = get_active_rates()
    return {
        "rates": [
            {
                "asset": asset,
                "credits_per_1k_usd_per_day": _num(rate_for(rates, asset)),
                "is_active": asset in rates,
                "note": (rates.get(asset) or {}).get("note"),
                "configured": asset_configured(asset),
            }
            for asset in ("eth", "ada")
        ],
        # 1000 x R / (1 - m) from the configured expected revenue R; what
        # the rate would be if it spent exactly the expected revenue.
        "suggested": {
            "eth": _num(suggested_rate(Config.DELEGATION_EXPECTED_DAILY_REVENUE_PER_USD_ETH)),
            "ada": _num(suggested_rate(Config.DELEGATION_EXPECTED_DAILY_REVENUE_PER_USD_ADA)),
        },
        "inference_margin": _num(Config.DELEGATION_INFERENCE_MARGIN),
    }


@router.get("/admin/delegation/rates", tags=["admin", "delegation"])
async def get_delegation_rates(
    _admin_user: dict[str, Any] = Depends(require_admin_or_env_key),
) -> dict[str, Any]:
    return {"success": True, "data": _rates_view()}


@router.put("/admin/delegation/rates", tags=["admin", "delegation"])
async def update_delegation_rates(
    body: UpdateDelegationRatesRequest,
    request: Request,
    admin_user: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    """Set (or deactivate) each listed asset's allowance rate. A rate row is
    never edited in place: the old one is deactivated and a new one inserted.
    A positive rate cannot be activated for an asset whose vault / pool is
    not configured."""
    for rate in body.rates:
        if (
            rate.is_active
            and rate.credits_per_1k_usd_per_day > 0
            and not asset_configured(rate.asset)
        ):
            raise HTTPException(status_code=422, detail=f"asset_not_configured:{rate.asset}")

    for rate in body.rates:
        ok = (
            replace_active_rate(rate.asset, rate.credits_per_1k_usd_per_day, rate.note) is not None
            if rate.is_active
            else deactivate_rate(rate.asset)
        )
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to update delegation rates")

    record_audit(
        admin_user,
        action="delegation.rates.update",
        target_type="delegation_allowance_rates",
        target_id=None,
        request=request,
        metadata={"rates": [r.model_dump(mode="json") for r in body.rates]},
    )
    return {"success": True, "data": _rates_view()}


@router.post("/admin/delegation/run", tags=["admin", "delegation"])
async def run_delegation_job(
    body: RunDelegationRequest,
    request: Request,
    admin_user: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    """Run one delegated-staking job now: the measurement sweep, the accrual
    (for `reward_date`, default yesterday UTC), or reconciliation. Each is
    idempotent, exactly like its scheduled run."""
    if body.job == "measure":
        summary = run_delegation_measurements_once()
    elif body.job == "accrue":
        try:
            summary = run_delegation_accruals_once(body.reward_date)
        except DelegationMeasurementsMissingError as e:
            raise HTTPException(status_code=409, detail="no_measurements_for_date") from e
    else:
        summary = run_delegation_reconciliation_once()

    record_audit(
        admin_user,
        action=f"delegation.run.{body.job}",
        target_type="delegation",
        target_id=body.reward_date.isoformat() if body.reward_date else None,
        request=request,
        metadata=summary,
    )
    return {"success": True, "data": summary}


@router.get("/admin/delegation/reconciliation", tags=["admin", "delegation"])
async def get_delegation_reconciliation(
    _admin_user: dict[str, Any] = Depends(require_admin_or_env_key),
) -> dict[str, Any]:
    view = reconciliation_view()
    view["recent_revenue"] = [
        {
            "revenue_date": r.get("revenue_date"),
            "asset": r.get("asset"),
            "period_key": r.get("period_key"),
            "revenue_native": _num(r.get("revenue_native")),
            "revenue_usd": _num(r.get("revenue_usd")),
            "source": r.get("source"),
        }
        for r in list_recent_revenue()
    ]
    return {"success": True, "data": view}


@router.post("/admin/delegation/resume", tags=["admin", "delegation"])
async def resume_delegation_asset(
    body: ResumeDelegationRequest,
    request: Request,
    admin_user: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    """Un-pause an asset that reconciliation paused. The next reconciliation
    pauses it again if the overspend is still there -- fix the rate (or the
    revenue recording) first."""
    actor = str(admin_user.get("email") or admin_user.get("id") or "admin")
    row = resume_accruals(body.asset, actor)
    if row is None:
        raise HTTPException(status_code=500, detail="Failed to resume delegation accruals")
    record_audit(
        admin_user,
        action="delegation.resume",
        target_type="delegation_controls",
        target_id=body.asset,
        request=request,
        metadata={"asset": body.asset},
    )
    return {"success": True, "data": {"asset": body.asset, "paused": False}}
