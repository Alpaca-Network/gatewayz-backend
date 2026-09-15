"""Holdings rewards API -- inference credits for tokens a user holds.

We read balances in a wallet the user has proven they control and pay
inference credits for what they hold. We take no custody, we never accept a
deposit, and we promise no return: the credits are a usage grant this
platform funds, not a payment from anyone else's capital, and the rate can
change or stop at any time.

Endpoints:
    GET   /holdings/rewards                 the caller's own view
    GET   /admin/holdings/rates             read the tier table
    PUT   /admin/holdings/rates             replace it (superadmin, audited)
    GET   /admin/holdings/tokens            read the asset registry
    POST  /admin/holdings/tokens            register an asset (superadmin)
    PATCH /admin/holdings/tokens/{id}       amend one (superadmin)
    POST  /admin/holdings/rewards/run       run the accrual now (superadmin)
    GET   /admin/holdings/rewards/summary   global totals + last run

Read endpoints accept an admin API key or the ADMIN_API_KEY env key;
everything that mutates a rate, the registry, or credits is superadmin-only
and audited, matching src/routes/admin_staff.py's convention.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.config.config import Config
from src.db.audit import record_audit
from src.db.holdings import (
    create_token,
    get_active_holdings_rates,
    get_latest_snapshot_usd,
    list_all_tokens,
    list_holdings_accruals_for_wallet,
    list_holdings_accruals_since,
    replace_active_holdings_rates,
    select_holdings_rate,
    update_token,
)
from src.db.user_wallets import get_wallets_for_user
from src.schemas.holdings import (
    CreateHoldingsTokenRequest,
    RunHoldingsRewardsRequest,
    UpdateHoldingsRatesRequest,
    UpdateHoldingsTokenRequest,
)
from src.security.deps import get_current_user, require_admin_or_env_key, require_superadmin
from src.services.holdings.rewards import (
    HoldingsSnapshotsMissingError,
    estimate_daily_credits,
    rate_table_view,
    run_holdings_rewards_once,
)
from src.services.ops.job_runs import get_job_runs

logger = logging.getLogger(__name__)

router = APIRouter()

_HISTORY_LIMIT = 30
_TOTALS_WINDOW_DAYS = 30
_SUMMARY_WINDOW_DAYS = 30


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


def _invalid_request(message: str, code: str, parameter_value: Any = None) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "error": {
                "message": message,
                "type": "invalid_request_error",
                "code": code,
                "context": {"parameter_value": parameter_value},
            }
        },
    )


def _validate_rate_set(rates: list[Any]) -> None:
    """Reject a tier set that cannot safely stand alone as the active table:

    - no tier floored at 0 -- a wallet below every other floor would match
      no tier at all (select_holdings_rate returns None) and be silently
      skipped instead of earning at the base rate.
    - duplicate floors -- ambiguous which tier applies. (Field(ge=0) on
      HoldingsRateInput already rejects negative floors and rates through
      FastAPI's own 422 path.)
    """
    floors = [r.min_usd for r in rates]

    if Decimal(0) not in floors:
        raise _invalid_request(
            "Rate set must include a tier with min_usd == 0.",
            "missing_zero_floor_tier",
            None,
        )

    seen: set[Decimal] = set()
    duplicates: set[Decimal] = set()
    for floor in floors:
        if floor in seen:
            duplicates.add(floor)
        seen.add(floor)
    if duplicates:
        raise _invalid_request(
            "Rate set has duplicate min_usd floors.",
            "duplicate_rate_floor",
            sorted(str(d) for d in duplicates),
        )


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def _totals_from_rows(rows: list[dict[str, Any]]) -> dict[str, str]:
    cutoff = (datetime.now(UTC).date() - timedelta(days=_TOTALS_WINDOW_DAYS)).isoformat()
    paid_30d = Decimal(0)
    paid_all = Decimal(0)
    pending = Decimal(0)
    for row in rows:
        credits = _decimal(row.get("credits"))
        status = row.get("status")
        if status == "paid":
            paid_all += credits
            if str(row.get("reward_date")) >= cutoff:
                paid_30d += credits
        elif status == "pending":
            pending += credits
    return {
        "credits_paid_30d": str(paid_30d),
        "credits_paid_all": str(paid_all),
        "pending_credits": str(pending),
    }


def _history_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda r: str(r.get("reward_date")), reverse=True)
    return [
        {
            "reward_date": row.get("reward_date"),
            "wallet_address": row.get("wallet_address"),
            "usd_basis": str(row.get("usd_basis")),
            "credits": str(row.get("credits")),
            "status": row.get("status"),
        }
        for row in ordered[:_HISTORY_LIMIT]
    ]


@router.get("/holdings/rewards", tags=["holdings"])
async def get_holdings_rewards(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """This account's holdings-rewards view: whether the feature is live,
    the current tier table, each proven wallet's most recently observed
    value with its tier and daily estimate, running totals, and recent
    accruals.

    The per-wallet value shown here is the LATEST observation. What actually
    gets paid is computed from the day's LOWEST observation, so a wallet
    whose balance moved during the day earns on less than this figure -- the
    estimate is what today's value would be worth if it held.
    """
    rates = get_active_holdings_rates()

    wallets_out: list[dict[str, Any]] = []
    accrual_rows: list[dict[str, Any]] = []
    total_usd = Decimal(0)

    for wallet in get_wallets_for_user(user["id"]):
        address = str(wallet.get("wallet_address") or "")
        if not address:
            continue
        observed = get_latest_snapshot_usd(address)
        value = observed if observed is not None else Decimal(0)
        total_usd += value
        tier = select_holdings_rate(rates, value)
        wallets_out.append(
            {
                "address": address,
                "usd_value": str(value),
                "observed": observed is not None,
                "tier_min_usd": str(tier["min_usd"]) if tier else None,
                **estimate_daily_credits(value, rates),
            }
        )
        accrual_rows.extend(list_holdings_accruals_for_wallet(address, limit=_HISTORY_LIMIT))

    return {
        "success": True,
        "data": {
            "enabled": Config.HOLDINGS_REWARDS_ENABLED,
            "rate_table": rate_table_view(rates),
            "daily_cap_credits": str(Config.HOLDINGS_DAILY_CAP_CREDITS),
            "total_usd_value": str(total_usd),
            "account_estimate": estimate_daily_credits(total_usd, rates),
            "wallets": wallets_out,
            "totals": _totals_from_rows(accrual_rows),
            "history": _history_from_rows(accrual_rows),
        },
    }


@router.get("/admin/holdings/rates", tags=["admin", "holdings"])
async def get_holdings_rates(
    _admin_user: dict[str, Any] = Depends(require_admin_or_env_key),
) -> dict[str, Any]:
    return {"success": True, "data": {"rates": get_active_holdings_rates()}}


@router.put("/admin/holdings/rates", tags=["admin", "holdings"])
async def update_holdings_rates(
    body: UpdateHoldingsRatesRequest,
    request: Request,
    admin_user: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    """Replace the active tier set. Existing tier rows are never mutated in
    place -- a tier an accrual was already computed against must not change
    out from under it -- so this deactivates the old set and inserts a new
    one."""
    _validate_rate_set(body.rates)

    new_rates = replace_active_holdings_rates(
        [
            {
                "min_usd": r.min_usd,
                "credits_per_1k_usd_per_day": r.credits_per_1k_usd_per_day,
                "note": r.note,
            }
            for r in body.rates
        ]
    )
    if new_rates is None:
        raise HTTPException(status_code=500, detail="Failed to update holdings rates")

    record_audit(
        admin_user,
        action="holdings.rates.update",
        target_type="holdings_reward_rates",
        target_id=None,
        request=request,
        metadata={"rates": [r.model_dump(mode="json") for r in body.rates]},
    )
    return {"success": True, "data": {"rates": new_rates}}


@router.get("/admin/holdings/tokens", tags=["admin", "holdings"])
async def get_holdings_tokens(
    _admin_user: dict[str, Any] = Depends(require_admin_or_env_key),
) -> dict[str, Any]:
    """The whole asset registry, disabled rows included."""
    return {"success": True, "data": {"tokens": list_all_tokens()}}


@router.post("/admin/holdings/tokens", tags=["admin", "holdings"])
async def create_holdings_token(
    body: CreateHoldingsTokenRequest,
    request: Request,
    admin_user: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    """Register one asset. A 409 means the (chain, contract) pair is already
    registered -- amend it with PATCH rather than adding a second row, since
    two rows for one asset would value the same balance twice."""
    created = create_token(
        chain_id=body.chain_id,
        contract_address=body.contract_address,
        symbol=body.symbol,
        decimals=body.decimals,
        price_id=body.price_id,
        is_enabled=body.is_enabled,
    )
    if created is None:
        raise _conflict(
            "Could not register this asset; it may already be in the registry.",
            "token_already_registered",
            {"chain_id": body.chain_id, "contract_address": body.contract_address},
        )

    record_audit(
        admin_user,
        action="holdings.tokens.create",
        target_type="holdings_tokens",
        target_id=str(created.get("id")),
        request=request,
        metadata=body.model_dump(mode="json"),
    )
    return {"success": True, "data": {"token": created}}


@router.patch("/admin/holdings/tokens/{token_id}", tags=["admin", "holdings"])
async def patch_holdings_token(
    token_id: int,
    body: UpdateHoldingsTokenRequest,
    request: Request,
    admin_user: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    """Amend one registered asset -- most often to disable it, which stops
    it being observed or valued from the next sweep on without deleting the
    snapshots already taken against it."""
    updates = body.model_dump(exclude_unset=True)
    updated = update_token(token_id, updates)
    if updated is None:
        raise HTTPException(status_code=404, detail="Registered asset not found")

    record_audit(
        admin_user,
        action="holdings.tokens.update",
        target_type="holdings_tokens",
        target_id=str(token_id),
        request=request,
        metadata=body.model_dump(mode="json", exclude_unset=True),
    )
    return {"success": True, "data": {"token": updated}}


@router.post("/admin/holdings/rewards/run", tags=["admin", "holdings"])
async def run_holdings_rewards_now(
    body: RunHoldingsRewardsRequest,
    request: Request,
    admin_user: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    """Run the daily accrual now, for `reward_date` (default: yesterday
    UTC). Idempotent and safe to call any number of times, exactly like the
    scheduled run -- used to force a run without waiting for the schedule,
    e.g. right after enabling the feature."""
    try:
        summary = run_holdings_rewards_once(body.reward_date)
    except HoldingsSnapshotsMissingError as e:
        raise _conflict(
            "That date has no observed balances to pay on.",
            "no_observations_for_date",
            str(e),
        ) from e

    record_audit(
        admin_user,
        action="holdings.rewards.run",
        target_type="holdings_reward_accruals",
        target_id=summary.get("reward_date"),
        request=request,
        metadata=summary,
    )
    return {"success": True, "data": summary}


@router.get("/admin/holdings/rewards/summary", tags=["admin", "holdings"])
async def get_holdings_rewards_summary(
    _admin_user: dict[str, Any] = Depends(require_admin_or_env_key),
) -> dict[str, Any]:
    """Global totals across every account, plus the last run of each of the
    two jobs -- the observation sweep and the daily accrual. The sweep's
    health matters here: no observations means no accruals, however healthy
    the accrual job itself looks."""
    runs = get_job_runs(["holdings_snapshots", "holdings_rewards"])
    cutoff = (datetime.now(UTC).date() - timedelta(days=_SUMMARY_WINDOW_DAYS)).isoformat()
    rows = list_holdings_accruals_since(cutoff)
    return {
        "success": True,
        "data": {
            "enabled": Config.HOLDINGS_REWARDS_ENABLED,
            "last_run": runs.get("holdings_rewards"),
            "last_snapshot_run": runs.get("holdings_snapshots"),
            "accruals": len(rows),
            "totals": _totals_from_rows(rows),
        },
    }
