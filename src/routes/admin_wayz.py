"""Admin WAYZ ops status endpoint (gatewayz-backend admin ops).

``GET /admin/wayz/status`` aggregates the health of every WAYZ/GPU
scheduled job (via ``src/services/ops/job_runs.py``), on-chain/config
state, and live counters (staking, faucet, wallets, GPU marketplace) into
one response the admin-panel's WAYZ Ops page polls. Never cached -- ops
pages need live data, not a stale snapshot.

Every sub-block is computed independently and wrapped in its own
try/except: a failure in any one of them degrades to
``{"error": "<ExceptionClassName>"}`` for that block only, and the
endpoint still returns 200. An ops status page that 500s because one
counter's query broke is worse than useless -- it hides every OTHER
counter that's still fine.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends

from src.config.config import Config
from src.db.faucet import get_claim_stats
from src.db.gpu import count_nodes_by_status, count_providers_by_status, list_nodes, list_providers
from src.db.gpu_payouts import earnings_totals_all, get_last_settlement_overall, work_24h_stats
from src.db.user_wallets import count_all_wallets, count_wallets_by_source
from src.db.wallet_stakes import get_stake_totals, get_sync_cursor_row
from src.security.deps import require_admin_or_env_key
from src.security.privy_token import privy_verification_mode
from src.services.ops.job_runs import get_job_runs

logger = logging.getLogger(__name__)

router = APIRouter()

# Job name -> configured interval in minutes. Mirrors exactly the jobs
# wrapped in src/services/scheduled_sync.py and src/services/gpu/rollup.py
# (see job_runs.record_job_run call sites). gpu_settlement_reconcile has no
# entry -- it runs inline as part of gpu_settlement (no separate scheduled
# pass exists) and is folded into that job's own summary.
_JOB_INTERVAL_MINUTES: dict[str, int] = {
    "model_sync": Config.MODEL_SYNC_INTERVAL_MINUTES,
    "price_refresh": Config.PRICE_REFRESH_INTERVAL_MINUTES,
    "ledger_reconciliation": Config.LEDGER_RECONCILIATION_INTERVAL_MINUTES,
    "retention_cleanup": Config.RETENTION_CLEANUP_INTERVAL_HOURS * 60,
    "wayz_staking_sync": Config.WAYZ_STAKING_SYNC_INTERVAL_MINUTES,
    "gpu_spot_check": Config.COMMUNITY_SPOTCHECK_INTERVAL_MINUTES,
    "gpu_settlement": Config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS * 60,
    "pricing_drift": Config.PRICING_DRIFT_INTERVAL_MINUTES,
    "gpu_liveness_sweep": Config.GPU_LIVENESS_SWEEP_INTERVAL_MINUTES,
    "gpu_rollup": 60,
}

# WAYZ token/staking contracts are deployed on Avalanche Fuji testnet.
# There is no dedicated Config field for this today (SIWE_ALLOWED_CHAIN_IDS
# is a set of *accepted* chain ids for wallet auth, not this), so it's a
# literal here rather than invented Config plumbing for a single constant.
_WAYZ_CHAIN_ID = 43113

_RPC_TIMEOUT_SECONDS = 2


def _safe_block(fn: Any, block_name: str) -> dict[str, Any]:
    """Run one sub-block builder, degrading to {"error": ExceptionClassName}
    on any failure so one broken block never takes down the whole response."""
    try:
        return fn()
    except Exception as e:
        logger.warning(f"admin/wayz/status: '{block_name}' block failed: {e}")
        return {"error": type(e).__name__}


def _build_config_block() -> dict[str, Any]:
    return {
        "chain_id": _WAYZ_CHAIN_ID,
        "token_address": Config.WAYZ_TOKEN_CONTRACT_ADDRESS,
        "staking_address": Config.WAYZ_STAKING_CONTRACT_ADDRESS,
        "deploy_block": Config.WAYZ_STAKING_DEPLOY_BLOCK,
        "faucet_configured": bool(Config.WAYZ_FAUCET_MINTER_PRIVATE_KEY),
        "rewards_pool_configured": bool(Config.WAYZ_REWARDS_POOL_PRIVATE_KEY),
        "privy_verification_mode": privy_verification_mode(),
        "community_routing_enabled": bool(Config.COMMUNITY_ROUTING_ENABLED),
        "upstream_pseudonym_enabled": bool(Config.UPSTREAM_ABUSE_PSEUDONYM),
        "spotcheck_reference_provider": Config.COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER,
    }


def _build_jobs_block() -> dict[str, Any]:
    names = list(_JOB_INTERVAL_MINUTES.keys())
    records = get_job_runs(names)
    now = datetime.now(UTC)

    jobs: dict[str, Any] = {}
    for name in names:
        interval_minutes = _JOB_INTERVAL_MINUTES[name]
        record = records.get(name)

        if record is None:
            jobs[name] = {
                "ok": None,
                "ran_at": None,
                "duration_ms": None,
                "interval_minutes": interval_minutes,
                "stale": True,
                "summary": None,
                "error": None,
            }
            continue

        ran_at_raw = record.get("ran_at")
        stale = True
        if ran_at_raw:
            try:
                ran_at = datetime.fromisoformat(ran_at_raw.replace("Z", "+00:00"))
                stale = (now - ran_at).total_seconds() > (2 * interval_minutes * 60)
            except (TypeError, ValueError):
                stale = True

        jobs[name] = {
            "ok": record.get("ok"),
            "ran_at": ran_at_raw,
            "duration_ms": record.get("duration_ms"),
            "interval_minutes": interval_minutes,
            "stale": stale,
            "summary": record.get("summary"),
            "error": record.get("error"),
        }

    return jobs


def _rpc_latest_block() -> int | None:
    """Current block number from the Fuji RPC, 2s timeout. Bare Web3 call
    (not WayzStakingClient) -- block_number needs no contract instance."""
    from web3 import Web3

    w3 = Web3(
        Web3.HTTPProvider(
            Config.AVALANCHE_FUJI_RPC_URL,
            request_kwargs={"timeout": _RPC_TIMEOUT_SECONDS},
        )
    )
    return w3.eth.block_number


def _build_staking_block() -> dict[str, Any]:
    total_staked_wei, wallets = get_stake_totals()

    cursor_block: int | None = None
    last_synced_at: str | None = None
    if Config.WAYZ_STAKING_CONTRACT_ADDRESS:
        cursor_row = get_sync_cursor_row(Config.WAYZ_STAKING_CONTRACT_ADDRESS)
        if cursor_row:
            cursor_block = cursor_row.get("last_synced_block")
            last_synced_at = cursor_row.get("updated_at")

    rpc_latest_block: int | None = None
    lag_blocks: int | None = None
    if Config.WAYZ_STAKING_CONTRACT_ADDRESS:
        try:
            rpc_latest_block = _rpc_latest_block()
            if cursor_block is not None:
                lag_blocks = max(0, rpc_latest_block - cursor_block)
        except Exception as e:
            logger.info(f"admin/wayz/status: RPC latest-block lookup failed/timed out: {e}")

    return {
        "wallets": wallets,
        "total_staked_wei": total_staked_wei,
        "cursor_block": cursor_block,
        "last_synced_at": last_synced_at,
        "rpc_latest_block": rpc_latest_block,
        "lag_blocks": lag_blocks,
    }


def _build_faucet_block() -> dict[str, Any]:
    stats = get_claim_stats()
    return {
        "claims_pending": stats.get("pending", 0),
        "claims_sent": stats.get("sent", 0),
        "claims_failed": stats.get("failed", 0),
        "last_claim_at": stats.get("last_claim_at"),
    }


def _build_wallets_block() -> dict[str, Any]:
    by_source_raw = count_wallets_by_source()
    by_source = {
        "privy": by_source_raw.get("privy", 0),
        "siwe": by_source_raw.get("siwe", 0),
    }
    for source, count in by_source_raw.items():
        if source not in by_source:
            by_source[source] = count

    return {
        "linked_total": count_all_wallets(),
        "by_source": by_source,
    }


def _build_gpu_block(jobs_block: dict[str, Any]) -> dict[str, Any]:
    earnings = earnings_totals_all()

    last_settlement_row = get_last_settlement_overall()
    last_settlement = None
    if last_settlement_row:
        last_settlement = {
            "provider_id": last_settlement_row.get("provider_id"),
            "amount_wei": str(last_settlement_row.get("amount_wei", "0")),
            "status": last_settlement_row.get("status"),
            "tx_hash": last_settlement_row.get("tx_hash"),
            "created_at": last_settlement_row.get("created_at"),
        }

    rollup_job = jobs_block.get("gpu_rollup") if isinstance(jobs_block, dict) else None
    rollup_last_hour = rollup_job.get("ran_at") if isinstance(rollup_job, dict) else None

    return {
        "providers": count_providers_by_status(),
        "nodes": count_nodes_by_status(),
        "work_24h": work_24h_stats(),
        "earnings": {
            "accrued_wei": str(earnings.get("accrued", 0)),
            "settling_wei": str(earnings.get("settling", 0)),
            "settled_wei": str(earnings.get("settled", 0)),
            "void_wei": str(earnings.get("void", 0)),
        },
        "last_settlement": last_settlement,
        "rollup_last_hour": rollup_last_hour,
    }


def _build_pending_approvals() -> list[dict[str, Any]]:
    pending = list_providers(status="pending")
    result = []
    for provider in pending:
        provider_id = provider.get("id")
        node_count = len(list_nodes(provider_id)) if provider_id is not None else 0
        result.append(
            {
                "id": provider_id,
                "display_name": provider.get("display_name"),
                "created_at": provider.get("created_at"),
                "payout_wallet_address": provider.get("payout_wallet_address"),
                "user_id": provider.get("user_id"),
                "nodes": node_count,
            }
        )
    return result


@router.get("/admin/wayz/status", tags=["admin"])
async def get_wayz_ops_status(
    _admin_user: dict[str, Any] = Depends(require_admin_or_env_key),
) -> dict[str, Any]:
    jobs_block = _safe_block(_build_jobs_block, "jobs")

    data = {
        "generated_at": datetime.now(UTC).isoformat(),
        "config": _safe_block(_build_config_block, "config"),
        "jobs": jobs_block,
        "staking": _safe_block(_build_staking_block, "staking"),
        "faucet": _safe_block(_build_faucet_block, "faucet"),
        "wallets": _safe_block(_build_wallets_block, "wallets"),
        "gpu": _safe_block(lambda: _build_gpu_block(jobs_block), "gpu"),
        "pending_approvals": _safe_block(_build_pending_approvals, "pending_approvals"),
    }

    return {"success": True, "data": data}
