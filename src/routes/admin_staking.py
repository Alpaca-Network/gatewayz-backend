"""Admin API for staking-rewards rate control and manual runs
(gatewayz-backend staking rewards -- boss's rule: WAYZ stakers are paid in
inference credits). See docs/staking/REWARDS.md.

GET endpoints accept either an admin API key or the ADMIN_API_KEY env key
(require_admin_or_env_key, matching src/routes/admin_wayz.py's ops-status
endpoint); mutating endpoints (rate changes, manual runs) are
superadmin-only and audited, matching src/routes/admin_staff.py's
convention.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.db.audit import record_audit
from src.db.staking_rewards import get_active_rates, replace_active_rates
from src.schemas.staking import RunRewardsRequest, UpdateRewardRatesRequest
from src.security.deps import require_admin_or_env_key, require_superadmin
from src.services.ops.job_runs import get_job_runs
from src.services.staking_rewards import (
    StakingRewardsStaleError,
    get_global_rewards_summary,
    run_staking_rewards_once,
)

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


@router.get("/admin/staking/reward-rates", tags=["admin", "staking"])
async def get_reward_rates(
    _admin_user: dict[str, Any] = Depends(require_admin_or_env_key),
) -> dict[str, Any]:
    return {"success": True, "data": {"rates": get_active_rates()}}


@router.put("/admin/staking/reward-rates", tags=["admin", "staking"])
async def update_reward_rates(
    body: UpdateRewardRatesRequest,
    request: Request,
    admin_user: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    """Replace the active rate set. Existing rate rows are never mutated in
    place (a rate already referenced by a paid accrual must not change out
    from under it) -- this deactivates the old set and inserts a new one."""
    new_rates = replace_active_rates([r.model_dump() for r in body.rates])
    if new_rates is None:
        raise HTTPException(status_code=500, detail="Failed to update reward rates")

    record_audit(
        admin_user,
        action="staking.rates.update",
        target_type="staking_reward_rates",
        target_id=None,
        request=request,
        metadata={"rates": [r.model_dump() for r in body.rates]},
    )
    return {"success": True, "data": {"rates": new_rates}}


@router.post("/admin/staking/rewards/run", tags=["admin", "staking"])
async def run_rewards_now(
    body: RunRewardsRequest,
    request: Request,
    admin_user: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    """Run the staking-rewards job now (for `reward_date`, default
    yesterday UTC) -- idempotent, safe to call any number of times, same as
    the scheduled run. Used to force a run without waiting for the
    schedule, e.g. right after enabling the feature or fixing a stale sync."""
    try:
        summary = run_staking_rewards_once(body.reward_date)
    except StakingRewardsStaleError as e:
        raise _conflict(
            "Stake sync is too stale to trust today's stake amounts.",
            "stake_sync_stale",
            str(e),
        ) from e

    record_audit(
        admin_user,
        action="staking.rewards.run",
        target_type="staking_reward_accruals",
        target_id=summary.get("reward_date"),
        request=request,
        metadata=summary,
    )
    return {"success": True, "data": summary}


@router.get("/admin/staking/rewards/summary", tags=["admin", "staking"])
async def get_rewards_summary(
    _admin_user: dict[str, Any] = Depends(require_admin_or_env_key),
) -> dict[str, Any]:
    last_run = get_job_runs(["staking_rewards"]).get("staking_rewards")
    summary = get_global_rewards_summary()
    return {"success": True, "data": {"last_run": last_run, **summary}}
