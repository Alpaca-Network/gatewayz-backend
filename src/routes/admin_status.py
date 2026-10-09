"""Unified admin status endpoint (gatewayz-backend Phase A, A4).

``GET /admin/status`` generalizes ``GET /admin/wayz/status`` (see
``src/routes/admin_wayz.py``) into the broader operational surface the
admin panel needs: job health (reused, not copied, from admin_wayz's own
block builders), external integration health
(``src/services/integrations_health.py``), secrets *presence* -- never
values -- for a fixed allow-list of env vars, and provider budget
exhaustion (``src/services/provider_budget_alerts.py``), and holdings sweep
coverage (``src/services/holdings/alerts.py``), and delegated staking
(``src/services/delegation/``). ``GET /admin/wayz/status``
keeps working unchanged; this route is additive.

Same degradation contract as admin_wayz: every sub-block is computed
independently and wrapped in its own try/except, so one broken block never
takes the rest of the page down. Never cached -- ops pages need live data.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends

from src.config import Config
from src.routes.admin_wayz import (
    _build_config_block,
    _build_faucet_block,
    _build_gpu_block,
    _build_jobs_block,
    _build_pending_approvals,
    _build_staking_block,
    _build_wallets_block,
    _safe_block,
)
from src.security.deps import require_admin_or_env_key
from src.services.holdings.alerts import holdings_sweep_health
from src.services.integrations_health import check_all
from src.services.privy_migration import migration_counts
from src.services.provider_alerting import ops_alerts_status
from src.services.provider_budget_alerts import provider_budget_status
from src.services.secrets_registry import SECRET_NAMES, secret_ages

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_secrets_block() -> dict[str, Any]:
    """{name: {present, source, first_seen_at, age_days, rotate_due,
    fingerprint_known}} for the fixed allow-list in
    src/services/secrets_registry.py (SECRET_NAMES).

    Deliberately reports only presence, a fixed source label, and a
    fingerprint-derived age -- never the value, never its length, never the
    fingerprint itself. Anything more specific than "is it set, and how long
    has it looked the same" is a stronger leak than this block exists to
    prevent (see secrets_registry.secret_ages()).
    """
    ages = secret_ages()
    return {name: {"source": "env", **ages[name]} for name in SECRET_NAMES}


def _build_migration_block() -> dict[str, Any]:
    """Privy app-migration progress (docs/PRIVY_MIGRATION.md): how many
    accounts still carry a legacy Privy app id vs. how many have been
    adopted onto the current one, plus whether adoption is currently on."""
    counts = migration_counts()
    return {
        "legacy_users": counts["legacy_users"],
        "migrated_users": counts["migrated_users"],
        "adopt_mode": Config.PRIVY_MIGRATION_MODE == "adopt",
    }


def _build_provider_budget_block() -> dict[str, Any]:
    """Which of *our* provider accounts have run out of money, from
    src/services/provider_budget_alerts.py.

    The counterpart to PROVIDER_CAPACITY_MESSAGE. End users are told only that a
    model is "temporarily unavailable due to a capacity limit on our side", which is
    right -- our billing state is not theirs to see -- but until this block existed
    nobody else was told anything either, so an unfunded Anthropic key that took all
    11 of its models down read as transient and users retried forever.

    This is an admin-only surface, so it carries more than "degraded": provider,
    which budget condition, since when, how often, and one affected model. All of
    those are fields the gateway owns. What it deliberately does NOT carry is any
    slice of the upstream error text, which embeds key ids and dashboard URLs --
    ``reason`` is a constant from PROVIDER_BUDGET_REASONS, not a quote. That is the
    line: more specific than the user-facing message, without reopening the leak the
    sanitizer closes.
    """
    return provider_budget_status()


def _build_delegation_block() -> dict[str, Any]:
    """Delegated staking at a glance: on/off, which assets are configured,
    which are paused by reconciliation (fail closed), and the last run of
    each of its three jobs -- the reconciliation run's summary carries the
    per-asset cost vs revenue. Cheap reads only; the full ledger comparison
    is GET /admin/delegation/reconciliation."""
    from src.db.delegation import ASSETS, get_controls, get_latest_measurement_taken_at
    from src.services.delegation.rewards import asset_configured
    from src.services.ops.job_runs import get_job_runs

    controls = get_controls()
    latest = get_latest_measurement_taken_at()
    return {
        "enabled": bool(Config.DELEGATED_STAKING_ENABLED),
        "assets": {
            asset: {
                "configured": asset_configured(asset),
                # An unreadable controls table means "treated as paused".
                "paused": (
                    True
                    if controls is None
                    else bool((controls.get(asset) or {}).get("accruals_paused"))
                ),
                "paused_reason": ((controls or {}).get(asset) or {}).get("paused_reason"),
            }
            for asset in ASSETS
        },
        "last_measurement_at": latest.isoformat() if latest else None,
        "jobs": get_job_runs(
            ["delegation_measurements", "delegation_accruals", "delegation_reconciliation"]
        ),
    }


def _build_wayz_block(jobs_block: dict[str, Any]) -> dict[str, Any]:
    """The same payload as GET /admin/wayz/status, minus jobs (reported at
    the top level of this response instead, alongside integrations/secrets).
    Reuses admin_wayz's own block builders rather than recomputing them."""
    return {
        "config": _safe_block(_build_config_block, "config"),
        "staking": _safe_block(_build_staking_block, "staking"),
        "faucet": _safe_block(_build_faucet_block, "faucet"),
        "wallets": _safe_block(_build_wallets_block, "wallets"),
        "gpu": _safe_block(lambda: _build_gpu_block(jobs_block), "gpu"),
        "pending_approvals": _safe_block(_build_pending_approvals, "pending_approvals"),
    }


@router.get("/admin/status", tags=["admin"])
async def get_admin_status(
    _admin_user: dict[str, Any] = Depends(require_admin_or_env_key),
) -> dict[str, Any]:
    jobs_block = _safe_block(_build_jobs_block, "jobs")

    data = {
        "generated_at": datetime.now(UTC).isoformat(),
        "jobs": jobs_block,
        "integrations": _safe_block(check_all, "integrations"),
        "secrets": _safe_block(_build_secrets_block, "secrets"),
        "wayz": _safe_block(lambda: _build_wayz_block(jobs_block), "wayz"),
        "migration": _safe_block(_build_migration_block, "migration"),
        "provider_budget": _safe_block(_build_provider_budget_block, "provider_budget"),
        "ops_alerts": _safe_block(ops_alerts_status, "ops_alerts"),
        # Is the holdings sweep actually recording wallets? A job that runs
        # "ok" while skipping every wallet looks healthy in `jobs`.
        "holdings_sweeps": _safe_block(holdings_sweep_health, "holdings_sweeps"),
        # Delegated staking: configured / paused (fail closed) / job runs.
        "delegation": _safe_block(_build_delegation_block, "delegation"),
    }

    return {"success": True, "data": data}
