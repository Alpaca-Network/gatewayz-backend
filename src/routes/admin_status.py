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
