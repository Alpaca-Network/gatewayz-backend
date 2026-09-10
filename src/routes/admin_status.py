"""Unified admin status endpoint (gatewayz-backend Phase A, A4).

``GET /admin/status`` generalizes ``GET /admin/wayz/status`` (see
``src/routes/admin_wayz.py``) into the broader operational surface the
admin panel needs: job health (reused, not copied, from admin_wayz's own
block builders), external integration health
(``src/services/integrations_health.py``), and secrets *presence* -- never
values -- for a fixed allow-list of env vars. ``GET /admin/wayz/status``
keeps working unchanged; this route is additive.

Same degradation contract as admin_wayz: every sub-block is computed
independently and wrapped in its own try/except, so one broken block never
takes the rest of the page down. Never cached -- ops pages need live data.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends

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
from src.services.integrations_health import check_all

logger = logging.getLogger(__name__)

router = APIRouter()

# Fixed allow-list of secret env vars to report presence for. Never add a
# name here without also confirming _build_secrets_block below can only
# ever report {present, source} for it -- never its value or length.
_SECRET_ALLOWLIST: list[str] = [
    "ADMIN_API_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "RESEND_API_KEY",
    "PRIVY_APP_ID",
    "PRIVY_VERIFICATION_KEY",
    "WAYZ_FAUCET_MINTER_PRIVATE_KEY",
    "WAYZ_REWARDS_POOL_PRIVATE_KEY",
    "STRIPE_SECRET_KEY",
    "SENTRY_DSN",
]


def _build_secrets_block() -> dict[str, Any]:
    """{name: {present, source}} for the fixed allow-list above.

    Deliberately reports only a boolean -- never the value, never its
    length, never a hash. Anything more specific than "is it set" is a
    stronger leak than this block exists to prevent.
    """
    return {
        name: {"present": bool(os.environ.get(name)), "source": "env"} for name in _SECRET_ALLOWLIST
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
    }

    return {"success": True, "data": data}
