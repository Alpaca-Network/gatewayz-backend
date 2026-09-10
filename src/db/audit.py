"""Unified admin/superadmin audit log (Phase A3,
docs/superpowers/specs/2026-09-10-unified-admin-identity-design.md §3).

This is distinct from src/db/activity.py's ``activity_log`` table, which is
user-facing (a user's own API usage history). ``audit_log`` is the internal,
service-role-only record of privileged actions -- staff changes, GPU
provider approve/suspend, role changes, key revocations -- and is never
exposed to the user it's about.

``record_audit`` is called from every admin/superadmin mutation and is
written to never raise: an audit-log failure must not block or fail the
action it's recording, only be logged locally so it isn't silently lost.
"""

from __future__ import annotations

import logging
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

# Mirrors the audit_log.actor_auth CHECK constraint in
# supabase/migrations/20260911000001_audit_log_and_staff.sql.
_VALID_ACTOR_AUTH = {"api_key", "env_key", "system"}


def _actor_auth(actor: dict[str, Any] | None) -> str:
    """Derive actor_auth from the dependency-injected actor dict.

    require_admin_or_env_key's env-key branch returns
    {"role": "admin", "auth": "env_key", "is_admin": True} -- no "id".
    Every other admin dependency returns a real `users` row authenticated
    by API key.
    """
    if not actor:
        return "system"
    auth = actor.get("auth")
    if auth in _VALID_ACTOR_AUTH:
        return auth
    return "api_key" if actor.get("id") is not None else "system"


def _client_ip(request: Any) -> str | None:
    """First hop of X-Forwarded-For, falling back to the direct connection --
    matches src/middleware/security_middleware.py's convention. A
    downstream proxy (Railway) prepends the real client IP."""
    if request is None:
        return None
    try:
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip() or None
        return request.client.host if request.client else None
    except Exception:
        return None


def _user_agent(request: Any) -> str | None:
    if request is None:
        return None
    try:
        return request.headers.get("user-agent")
    except Exception:
        return None


def record_audit(
    actor: dict[str, Any] | None,
    action: str,
    target_type: str | None = None,
    target_id: str | int | None = None,
    request: Any = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append one row to audit_log. Never raises -- a failure here must not
    break the admin action it's recording.

    Args:
        actor: The dict returned by require_admin/require_superadmin/
            require_admin_or_env_key (a `users` row, or the env-key
            sentinel dict).
        action: Short, stable action identifier, e.g. "staff.role_changed",
            "gpu.provider_approved", "api_key.revoked".
        target_type: What kind of thing was acted on, e.g. "user", "gpu_provider".
        target_id: The target's id (stringified -- ids across tables are a
            mix of int/uuid).
        request: The FastAPI Request, if available, for ip/user_agent.
        metadata: Arbitrary structured detail about the action.
    """
    try:
        client = get_supabase_client()
        row = {
            "actor_user_id": (actor or {}).get("id"),
            "actor_email": (actor or {}).get("email"),
            "actor_auth": _actor_auth(actor),
            "action": action,
            "target_type": target_type,
            "target_id": str(target_id) if target_id is not None else None,
            "ip": _client_ip(request),
            "user_agent": _user_agent(request),
            "metadata": metadata or {},
        }
        client.table("audit_log").insert(row).execute()
    except Exception as e:
        logger.warning("record_audit failed for action=%s: %s", action, e)


def list_audit(
    limit: int = 100,
    action: str | None = None,
    actor_user_id: int | None = None,
    before: str | None = None,
) -> list[dict[str, Any]]:
    """List audit_log entries, newest first.

    Args:
        limit: Max rows to return (capped at 500).
        action: Optional exact-match filter on action.
        actor_user_id: Optional filter to one actor.
        before: Optional ISO timestamp -- only entries created strictly
            before this (for cursor-style pagination).
    """
    try:
        client = get_supabase_client()
        query = client.table("audit_log").select("*")

        if action:
            query = query.eq("action", action)
        if actor_user_id is not None:
            query = query.eq("actor_user_id", actor_user_id)
        if before:
            query = query.lt("created_at", before)

        capped_limit = max(1, min(limit, 500))
        result = query.order("created_at", desc=True).limit(capped_limit).execute()
        return result.data if result.data else []
    except Exception as e:
        logger.error("list_audit failed: %s", e)
        return []
