"""Delegated staking API -- an inference allowance for stake delegated to
Gatewayz.

Users stake ETH from their own wallet into the Gatewayz StakeWise V3 vault,
or delegate ADA to the Gatewayz Cardano pool. The stake stays in their
control; its staking rewards reach us through the vault fee / pool margin.

Two payout models (DELEGATION_PAYOUT_MODEL):

* reward_share (v3, default) -- a share (DELEGATION_REWARD_SHARE) of the
  rewards each wallet's stake actually earned us is granted as an inference
  DISCOUNT balance; a billed request then pays (1 - d) of list price in
  credits until the balance runs out. Never more than we received.
* allowance (v2, rollback) -- inference credits at a rate WE set.

Either way the amount can change at any time and is not a guaranteed or fixed
return, and nothing here may present it as one.

Endpoints:
    GET   /delegation/status                     public: config + current rates
    GET   /delegation/rewards                    the caller's positions, allowance, discount
    GET   /admin/delegation/rates                rates + suggested rates (admin)
    PUT   /admin/delegation/rates                set rates (superadmin, audited)
    POST  /admin/delegation/run                  run a job now (superadmin, audited)
    GET   /admin/delegation/reconciliation       revenue vs cost per asset (admin)
    POST  /admin/delegation/resume               un-pause an asset (superadmin, audited)
    POST  /admin/delegation/pause                pause an asset (superadmin, audited)
    GET   /admin/delegation/overview             per-asset stake, revenue, discount (admin)
    GET   /admin/delegation/accounts             accounts with discount / stake (admin)
    GET   /admin/delegation/accounts/{user_id}   one account in detail (admin)
    POST  /admin/delegation/accounts/{user_id}/adjust
                                                 correct a discount balance (superadmin,
                                                 audited, Idempotency-Key)

Numbers are strings, like the holdings API.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from src.config.config import Config
from src.db.audit import record_audit
from src.db.credit_transactions import reward_request_uuid
from src.db.delegation import (
    ASSETS,
    deactivate_rate,
    get_active_rates,
    get_controls,
    get_latest_measurements,
    get_latest_sweep_rows,
    get_user_emails,
    list_accruals_for_wallet,
    list_attribution_user_ids,
    list_attributions_for_account,
    list_recent_revenue,
    pause_accruals,
    replace_active_rate,
    resume_accruals,
    sum_attributed_discount,
    sum_revenue_usd,
)
from src.db.inference_discount import (
    adjust_discount,
    get_balance,
    list_balances,
    list_ledger,
)
from src.db.user_wallets import get_wallets_for_user, is_cardano_wallet, list_all_wallets
from src.db.users import get_user_by_id
from src.schemas.delegation import (
    AdjustDiscountRequest,
    DelegationEligibilityRequest,
    PauseDelegationRequest,
    ResumeDelegationRequest,
    RunDelegationRequest,
    UpdateDelegationRatesRequest,
)
from src.security.deps import get_current_user, require_admin_or_env_key, require_superadmin
from src.services.delegation import stakewise
from src.services.delegation.attribution import run_delegation_attribution_once
from src.services.delegation.discount import (
    discount_enabled,
    discount_percent,
    payout_model,
    reward_share,
)
from src.services.delegation.eligibility import eligibility_view, require_eligible
from src.services.delegation.measurements import (
    cardano_pool_id,
    run_delegation_measurements_once,
)
from src.services.delegation.reconciliation import (
    discount_liability,
    reconciliation_view,
    run_delegation_reconciliation_once,
)
from src.services.delegation.rewards import (
    DelegationMeasurementsMissingError,
    asset_configured,
    daily_cap,
    estimate_daily_credits,
    paused_assets,
    rate_for,
    run_delegation_accruals_once,
    suggested_rate,
)

logger = logging.getLogger(__name__)

router = APIRouter()

DISCLAIMER = "Rates are set by Gatewayz, can change at any time, and are not a guaranteed return."
# The reward_share model has no rate: say what the discount actually is.
REWARD_SHARE_DISCLAIMER = (
    "Discounts are a share of staking rewards Gatewayz actually received, can change at any "
    "time, and are not a guaranteed return."
)

_HISTORY_DAYS = 30
_TOTALS_ROW_LIMIT = 1000
_NATIVE_DECIMALS = {"eth": 18, "ada": 6}
# The allowance is shown as "99% off up to $X/month" -- X is 30 days of the
# daily estimate, at 1 credit = $1.
_MONTH_DAYS = 30
_ATTRIBUTION_LIMIT = 90
_DISCOUNT_HISTORY_LIMIT = 50
_REVENUE_RECENT_DAYS = 30


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


def _disclaimer() -> str:
    return REWARD_SHARE_DISCLAIMER if payout_model() == "reward_share" else DISCLAIMER


def _usd(value: Any) -> str:
    """A discount amount (6 dp) as a string."""
    return format(_decimal(value).quantize(Decimal("0.000001")), "f")


def _usd_or_none(value: Decimal | None) -> str | None:
    return None if value is None else _usd(value)


def _native_decimal(asset: str, value: Any) -> str:
    """A native-unit amount that is already in ETH / ADA (not wei/lovelace)."""
    decimals = _NATIVE_DECIMALS.get(asset, 0)
    return format(_decimal(value).quantize(Decimal(1).scaleb(-decimals)), "f")


def _discount_config() -> dict[str, Any]:
    return {
        "payout_model": payout_model(),
        "reward_share": _num(reward_share()),
        "discount_percent": _num(discount_percent()),
        "discount_enabled": discount_enabled(),
    }


# User-facing ledger refs: the stored ref of a use/reverse/adjust row is a
# billing request id or an admin's free-text reason -- neither is shown.
_USER_REF = {"use": "inference", "reverse": "refund", "adjust": "adjustment"}


def _history_item(row: dict[str, Any], *, admin: bool = False) -> dict[str, Any]:
    kind = str(row.get("kind") or "")
    item = {
        "created_at": row.get("created_at"),
        "kind": kind,
        "amount_usd": _usd(row.get("amount_usd")),
        "balance_after": _usd(row.get("balance_after")),
        "ref": row.get("ref") if admin or kind == "grant" else _USER_REF.get(kind, kind),
    }
    if admin:
        item["id"] = row.get("id")
        item["request_id"] = row.get("request_id")
        item["metadata"] = row.get("metadata") or {}
    return item


def _attribution_item(row: dict[str, Any], *, admin: bool = False) -> dict[str, Any]:
    asset = str(row.get("asset") or "")
    item = {
        "asset": asset,
        "period_key": row.get("period_key"),
        "wallet_address": row.get("wallet_address"),
        "stake_native": _native_decimal(asset, row.get("stake_native")),
        "attributed_usd": _usd(row.get("attributed_usd")),
        "discount_usd": _usd(row.get("discount_usd")),
        "status": row.get("status"),
        "granted_at": row.get("granted_at"),
    }
    if admin:
        item.update(
            {
                "id": row.get("id"),
                "reward_date": row.get("reward_date"),
                "user_id": row.get("user_id"),
                "stake_total_native": _native_decimal(asset, row.get("stake_total_native")),
                "revenue_native": _native_decimal(asset, row.get("revenue_native")),
                "revenue_usd": _usd(row.get("revenue_usd")),
                "reward_share": _num(row.get("reward_share")),
                "created_at": row.get("created_at"),
            }
        )
    return item


def _balance_view(row: dict[str, Any] | None) -> dict[str, str]:
    row = row or {}
    return {
        "balance_usd": _usd(row.get("balance_usd")),
        "lifetime_granted_usd": _usd(row.get("lifetime_granted_usd")),
        "lifetime_used_usd": _usd(row.get("lifetime_used_usd")),
    }


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
            **_discount_config(),
            "disclaimer": _disclaimer(),
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

    linked: list[dict[str, str]] = []
    wallets = get_wallets_for_user(user["id"])
    for wallet in wallets:
        address = str(wallet.get("wallet_address") or "")
        if not address:
            continue
        # Linked right away, before the first measurement sweep sees it.
        linked.append(
            {"asset": "ada" if is_cardano_wallet(wallet) else "eth", "wallet_address": address}
        )
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
    # "claimed" = reserved for this account, payment in flight.
    pending = sum(
        (_decimal(a.get("credits")) for a in accruals if a.get("status") in ("pending", "claimed")),
        Decimal(0),
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
            "linked_wallets": linked,
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
            **_discount_block(user["id"], wallets),
            "eligibility": eligibility_view(user["id"]),
            "disclaimer": _disclaimer(),
        },
    }


@router.post("/delegation/eligibility", tags=["delegation"])
async def attest_delegation_eligibility(
    body: DelegationEligibilityRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Record the caller's attestation that they are not a US or Canadian
    person -- required before any reward is granted (ETH vault deposits go
    straight on-chain, so this is the only step that can carry it). 451
    region_restricted / attestation_required otherwise."""
    require_eligible(user["id"], request, body.not_us_ca_person)
    return {"success": True, "data": {"eligibility": eligibility_view(user["id"])}}


def _discount_block(user_id: int, wallets: list[dict[str, Any]]) -> dict[str, Any]:
    """The v3 reward-share discount for /delegation/rewards. No DB read while
    delegated staking is off -- zeros and empty lists, same shape."""
    config = {
        "enabled": discount_enabled(),
        "discount_percent": _num(discount_percent()),
        "reward_share": _num(reward_share()),
    }
    if not Config.DELEGATED_STAKING_ENABLED:
        return {
            "discount": {**config, **_balance_view(None)},
            "attributions": [],
            "discount_history": [],
        }
    addresses = [str(w.get("wallet_address") or "") for w in wallets]
    return {
        "discount": {**config, **_balance_view(get_balance(user_id))},
        "attributions": [
            _attribution_item(r)
            for r in list_attributions_for_account(user_id, addresses, limit=_ATTRIBUTION_LIMIT)
        ],
        "discount_history": [
            _history_item(r) for r in list_ledger(user_id, limit=_DISCOUNT_HISTORY_LIMIT)
        ],
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
    elif body.job == "attribute":
        summary = run_delegation_attribution_once(body.reward_date)
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


@router.post("/admin/delegation/pause", tags=["admin", "delegation"])
async def pause_delegation_asset(
    body: PauseDelegationRequest,
    request: Request,
    admin_user: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    """Pause an asset's new accruals / attributions and pending grants now
    (the same switch reconciliation flips on overspend). Resume with POST
    /admin/delegation/resume."""
    actor = str(admin_user.get("email") or admin_user.get("id") or "admin")
    reason = f"admin ({actor}): {body.reason}"
    if not pause_accruals(body.asset, reason):
        raise _admin_error(500, "pause_failed", "Failed to pause delegation accruals")
    record_audit(
        admin_user,
        action="delegation.pause",
        target_type="delegation_controls",
        target_id=body.asset,
        request=request,
        metadata={"asset": body.asset, "reason": body.reason},
    )
    return {
        "success": True,
        "data": {"asset": body.asset, "paused": True, "paused_reason": reason[:500]},
    }


# -- admin staking view (v3) ----------------------------------------------------------


def _wallet_asset(row: dict[str, Any]) -> str:
    return "ada" if is_cardano_wallet(row) else "eth"


def _latest_stakes() -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]] | None]]:
    """({wallet: {asset, amount_raw, usd_value}} from each asset's latest
    sweep, {asset: sweep rows or None when unreadable})."""
    by_wallet: dict[str, dict[str, Any]] = {}
    sweeps: dict[str, list[dict[str, Any]] | None] = {}
    for asset in ASSETS:
        rows = get_latest_sweep_rows(asset)
        sweeps[asset] = rows
        for row in rows or []:
            by_wallet[str(row.get("wallet_address") or "").lower()] = {
                "asset": asset,
                "amount_raw": row.get("amount_raw"),
                "usd_value": row.get("usd_value"),
            }
    return by_wallet, sweeps


def _wallet_items(
    wallets: list[dict[str, Any]], stakes: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    items = []
    for w in wallets:
        address = str(w.get("wallet_address") or "").lower()
        if not address:
            continue
        asset = _wallet_asset(w)
        stake = stakes.get(address) or {}
        items.append(
            {
                "address": address,
                "asset": asset,
                "stake_native": _native_amount(asset, stake.get("amount_raw") or 0),
                "stake_usd": _usd(stake.get("usd_value") or 0),
            }
        )
    return items


def _account_item(
    user_id: int,
    email: str | None,
    wallets: list[dict[str, Any]],
    stakes: dict[str, dict[str, Any]],
    balance: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "email": email,
        "wallets": _wallet_items(wallets, stakes),
        **_balance_view(balance),
        "last_grant_at": (balance or {}).get("last_grant_at"),
    }


@router.get("/admin/delegation/overview", tags=["admin", "delegation"])
async def get_delegation_overview(
    _admin_user: dict[str, Any] = Depends(require_admin_or_env_key),
) -> dict[str, Any]:
    """Per-asset configuration, pause state, linked stake (latest sweep),
    revenue and discount granted, plus the discount used / outstanding across
    assets. A total that cannot be read is null, never 0."""
    controls = get_controls()
    paused = paused_assets(controls)
    wallets = list_all_wallets()
    _, sweeps = _latest_stakes()
    since = (datetime.now(UTC).date() - timedelta(days=_REVENUE_RECENT_DAYS)).isoformat()

    assets: dict[str, Any] = {}
    for asset in ASSETS:
        rows = sweeps.get(asset)
        control = (controls or {}).get(asset) or {}
        raw = sum((_decimal(r.get("amount_raw")) for r in rows or []), Decimal(0))
        usd = sum((_decimal(r.get("usd_value")) for r in rows or []), Decimal(0))
        assets[asset] = {
            "configured": asset_configured(asset),
            "paused": asset in paused,
            "paused_reason": control.get("paused_reason"),
            "stake_native": None if rows is None else _native_amount(asset, raw),
            "stake_usd": None if rows is None else _usd(usd),
            "linked_wallets": sum(1 for w in wallets if _wallet_asset(w) == asset),
            "revenue_usd_30d": _usd_or_none(sum_revenue_usd(asset, since=since)),
            "revenue_usd_total": _usd_or_none(sum_revenue_usd(asset)),
            "discount_granted_usd_total": _usd_or_none(
                sum_attributed_discount(asset, ("granted",))
            ),
        }

    liability = discount_liability()
    known = liability.get("status") == "ok"
    return {
        "success": True,
        "data": {
            "enabled": bool(Config.DELEGATED_STAKING_ENABLED),
            **_discount_config(),
            "assets": assets,
            "discount_used_usd_total": (_usd(liability["discount_used_usd"]) if known else None),
            "outstanding_balance_usd": (
                _usd(liability["outstanding_balance_usd"]) if known else None
            ),
            "accounts_with_balance": liability.get("accounts_with_balance") if known else None,
        },
    }


@router.get("/admin/delegation/accounts", tags=["admin", "delegation"])
async def list_delegation_accounts(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None, max_length=200),
    _admin_user: dict[str, Any] = Depends(require_admin_or_env_key),
) -> dict[str, Any]:
    """Every account with a discount balance, an attribution, or stake in the
    latest sweep -- largest balance first. `q` matches a user id, an email
    (substring, case-insensitive) or a wallet address (substring)."""
    stakes, _ = _latest_stakes()
    wallets_by_user: dict[int, list[dict[str, Any]]] = {}
    for w in list_all_wallets():
        uid = w.get("user_id")
        if isinstance(uid, int) and not isinstance(uid, bool):
            wallets_by_user.setdefault(uid, []).append(w)

    balance_rows = list_balances() or []
    balances = {int(r["user_id"]): r for r in balance_rows if r.get("user_id") is not None}
    candidates: set[int] = set(balances) | (list_attribution_user_ids() or set())
    for uid, ws in wallets_by_user.items():
        if any(
            _decimal(
                (stakes.get(str(w.get("wallet_address") or "").lower()) or {}).get("amount_raw")
            )
            > 0
            for w in ws
        ):
            candidates.add(uid)

    emails = get_user_emails(sorted(candidates))
    needle = (q or "").strip().lower()

    def matches(uid: int) -> bool:
        if not needle:
            return True
        if needle.isdigit() and int(needle) == uid:
            return True
        if needle in str(emails.get(uid) or "").lower():
            return True
        return any(
            needle in str(w.get("wallet_address") or "").lower()
            for w in wallets_by_user.get(uid, [])
        )

    selected = sorted(
        (uid for uid in candidates if matches(uid)),
        key=lambda uid: (-_decimal((balances.get(uid) or {}).get("balance_usd")), uid),
    )
    page = selected[offset : offset + limit]
    return {
        "success": True,
        "data": {
            "items": [
                _account_item(
                    uid, emails.get(uid), wallets_by_user.get(uid, []), stakes, balances.get(uid)
                )
                for uid in page
            ],
            "total": len(selected),
            "limit": limit,
            "offset": offset,
        },
    }


@router.get("/admin/delegation/accounts/{user_id}", tags=["admin", "delegation"])
async def get_delegation_account(
    user_id: int,
    _admin_user: dict[str, Any] = Depends(require_admin_or_env_key),
) -> dict[str, Any]:
    user = get_user_by_id(user_id)
    if not isinstance(user, dict):
        raise _admin_error(404, "user_not_found", "No account with that id")
    stakes, _ = _latest_stakes()
    wallets = get_wallets_for_user(user_id)
    addresses = [str(w.get("wallet_address") or "") for w in wallets]
    return {
        "success": True,
        "data": {
            "account": _account_item(
                user_id, user.get("email"), wallets, stakes, get_balance(user_id)
            ),
            "attributions": [
                _attribution_item(r, admin=True)
                for r in list_attributions_for_account(user_id, addresses, limit=_ATTRIBUTION_LIMIT)
            ],
            "discount_history": [
                _history_item(r, admin=True)
                for r in list_ledger(user_id, limit=_DISCOUNT_HISTORY_LIMIT)
            ],
        },
    }


def _admin_error(status_code: int, code: str, message: str) -> HTTPException:
    """An error the admin panel can read: the global handler passes a detail
    that is already {"error": {...}} through as-is (a plain string detail is
    replaced by a generic message for most status codes)."""
    return HTTPException(
        status_code=status_code,
        detail={"error": {"type": code, "code": code, "message": message, "status": status_code}},
    )


def _adjust_request_id(user_id: int, idempotency_key: str) -> str:
    """The discount ledger's UUID request_id for an admin adjustment: the
    Idempotency-Key itself when it is a UUID, else a UUIDv5 of it scoped to
    the account (so the same key on two accounts is two adjustments)."""
    try:
        return str(uuid.UUID(idempotency_key))
    except ValueError:
        return reward_request_uuid(f"discount_adjust:{user_id}:{idempotency_key}")


@router.post("/admin/delegation/accounts/{user_id}/adjust", tags=["admin", "delegation"])
async def adjust_delegation_discount(
    user_id: int,
    body: AdjustDiscountRequest,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key", max_length=200),
    admin_user: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    """Correct an account's discount balance by a signed amount. Requires a
    reason and an Idempotency-Key header (a retried click applies once);
    refuses to take the balance below zero."""
    key = (idempotency_key or "").strip()
    if not key:
        raise _admin_error(400, "idempotency_key_required", "Send an Idempotency-Key header")
    request_id = _adjust_request_id(user_id, key)
    admin_id = admin_user.get("id")
    result = adjust_discount(
        user_id,
        body.amount_usd,
        request_id,
        body.reason,
        admin_id if isinstance(admin_id, int) and not isinstance(admin_id, bool) else None,
    )
    status = (result or {}).get("status")
    if status == "user_not_found":
        raise _admin_error(404, "user_not_found", "No account with that id")
    if status == "insufficient_balance":
        raise _admin_error(
            422, "would_go_negative", "The adjustment would take the balance below zero"
        )
    if status == "invalid":
        raise _admin_error(422, "invalid_adjustment", "Amount must be non-zero with a reason")
    if status not in ("adjusted", "exists"):
        raise _admin_error(500, "adjust_failed", "Failed to adjust the discount balance")
    if status == "adjusted":
        record_audit(
            admin_user,
            action="delegation.discount.adjust",
            target_type="inference_discount_balances",
            target_id=user_id,
            request=request,
            metadata={
                "amount_usd": str(body.amount_usd),
                "reason": body.reason,
                "request_id": request_id,
            },
        )
    return {
        "success": True,
        "data": {
            "user_id": user_id,
            "status": status,
            "amount_usd": _usd(result.get("amount_usd")),
            "balance_after": _usd(result.get("balance_after")),
            "request_id": request_id,
        },
    }
