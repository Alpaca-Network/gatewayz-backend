"""Admin audit log read API (Phase A3,
docs/superpowers/specs/2026-09-10-unified-admin-identity-design.md §3).

Read-only: audit_log rows are written only by src/db/audit.py::record_audit,
called from admin/superadmin mutation routes. Both admin and superadmin can
read the full log -- see the ('admin', 'audit', 'read') and
('superadmin', 'audit', 'read') role_permissions rows in
20260911000001_audit_log_and_staff.sql.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query

from src.db.audit import list_audit
from src.security.deps import require_admin

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/admin/audit", tags=["admin", "audit"])
async def get_audit_log(
    limit: int = Query(default=100, ge=1, le=500),
    action: str | None = None,
    actor_user_id: int | None = None,
    before: str | None = None,
    _admin_user: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """List audit_log entries, newest first. Admin or superadmin."""
    entries = list_audit(limit=limit, action=action, actor_user_id=actor_user_id, before=before)
    return {"success": True, "data": {"entries": entries}}
