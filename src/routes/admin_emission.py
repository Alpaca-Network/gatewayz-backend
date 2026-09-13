"""Admin API for Chutes-style WAYZ emission rewards (gatewayz-backend
tokenomics -- boss asks: split WAYZ rewards between stakers and GPU
providers the way Chutes/Bittensor does). See docs/tokenomics/EMISSION.md.

GET endpoints accept either an admin API key or the ADMIN_API_KEY env key
(require_admin_or_env_key, matching src/routes/admin_wayz.py's ops-status
endpoint); mutating endpoints (manual epoch runs, config changes) are
superadmin-only and audited, matching src/routes/admin_staking.py's
convention.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.config.config import Config
from src.db.audit import record_audit
from src.db.emission import list_epochs, list_provider_scores_for_epoch
from src.security.deps import require_admin_or_env_key, require_superadmin
from src.services.emission.epoch import is_emission_job_disabled, run_emission_epoch
from src.services.staking_rewards import StakingRewardsStaleError

logger = logging.getLogger(__name__)

router = APIRouter()

_DEFAULT_EPOCHS_LIMIT = 30
_MAX_EPOCHS_LIMIT = 90


class RunEmissionEpochRequest(BaseModel):
    epoch_date: date | None = None


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


@router.get("/admin/emission/epochs", tags=["admin", "emission"])
async def get_emission_epochs(
    limit: int = _DEFAULT_EPOCHS_LIMIT,
    _admin_user: dict[str, Any] = Depends(require_admin_or_env_key),
) -> dict[str, Any]:
    """Most recent emission_epochs rows, newest first."""
    bounded_limit = max(1, min(limit, _MAX_EPOCHS_LIMIT))
    return {"success": True, "data": {"epochs": list_epochs(limit=bounded_limit)}}


@router.get("/admin/emission/epochs/{epoch_date}", tags=["admin", "emission"])
async def get_emission_epoch_scores(
    epoch_date: str,
    _admin_user: dict[str, Any] = Depends(require_admin_or_env_key),
) -> dict[str, Any]:
    """Every provider_scores row for one epoch, highest share first."""
    scores = list_provider_scores_for_epoch(epoch_date)
    return {"success": True, "data": {"epoch_date": epoch_date, "scores": scores}}


@router.post("/admin/emission/run", tags=["admin", "emission"])
async def run_emission_epoch_now(
    body: RunEmissionEpochRequest,
    request: Request,
    admin_user: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    """Run the emission_epoch job now (for `epoch_date`, default yesterday
    UTC) -- idempotent, safe to call any number of times, same as the
    scheduled run. Used to force a run without waiting for the schedule,
    e.g. right after flipping REWARDS_MODE to 'emission'."""
    try:
        summary = run_emission_epoch(body.epoch_date)
    except StakingRewardsStaleError as e:
        raise _conflict(
            "Stake sync is too stale to trust today's staker split.",
            "stake_sync_stale",
            str(e),
        ) from e

    record_audit(
        admin_user,
        action="emission.epoch.run",
        target_type="emission_epochs",
        target_id=summary.get("epoch_date"),
        request=request,
        metadata=summary,
    )
    return {"success": True, "data": summary}


def _config_view() -> dict[str, Any]:
    return {
        "rewards_mode": Config.REWARDS_MODE,
        "daily_emission_wayz": str(Config.WAYZ_DAILY_EMISSION),
        "split_bps": {
            "providers": Config.EMISSION_SPLIT_PROVIDERS_BPS,
            "stakers": Config.EMISSION_SPLIT_STAKERS_BPS,
            "treasury": Config.EMISSION_SPLIT_TREASURY_BPS,
        },
        "score_weights_bps": Config.emission_score_weights_bps(),
        "exponent_above_median": str(Config.PROVIDER_SCORE_EXPONENT_ABOVE_MEDIAN),
        "staker_reward_asset": Config.STAKER_REWARD_ASSET,
        "wayz_credit_rate": str(Config.WAYZ_CREDIT_RATE),
        "cron_utc": f"{Config.EMISSION_EPOCH_CRON_HOUR_UTC:02d}:{Config.EMISSION_EPOCH_CRON_MINUTE_UTC:02d}",
        "disabled_reason": is_emission_job_disabled(),
    }


@router.get("/admin/emission/config", tags=["admin", "emission"])
async def get_emission_config(
    _admin_user: dict[str, Any] = Depends(require_admin_or_env_key),
) -> dict[str, Any]:
    """The effective emission config (env-derived) and whether the startup
    bps-sum check disabled the job."""
    return {"success": True, "data": _config_view()}


@router.put("/admin/emission/config", tags=["admin", "emission"])
async def update_emission_config(
    request: Request,
    admin_user: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    """Not implemented yet: emission config is env-only today (no `kv`
    store backs a runtime override the way staking_reward_rates does for
    the per_unit rate table). Returns 501 with a clear message rather than
    silently no-op'ing or pretending to persist a change that won't
    survive a restart -- change the env vars and restart, or call
    POST /admin/emission/run to test a value without changing prod config."""
    record_audit(
        admin_user,
        action="emission.config.update_attempted",
        target_type="emission_config",
        target_id=None,
        request=request,
        metadata={"note": "not implemented -- config is env-only"},
    )
    raise HTTPException(
        status_code=501,
        detail=(
            "Emission config is env-only today -- there is no kv store backing a runtime "
            "override (unlike staking_reward_rates for the per_unit path). Change the "
            "EMISSION_SPLIT_*_BPS / WAYZ_DAILY_EMISSION / PROVIDER_SCORE_WEIGHT_*_BPS env "
            "vars and restart, or use POST /admin/emission/run to test a one-off value."
        ),
    )
