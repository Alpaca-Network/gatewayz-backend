"""Staff management (Phase A2,
docs/superpowers/specs/2026-09-10-unified-admin-identity-design.md §3).

"Staff" means a `users` row with role in ('admin', 'superadmin') -- there is
no separate staff table for identity; `admin_invites` only tracks pending
invitations before a `users` row exists (or before an existing user has been
granted the role).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

STAFF_ROLES = ("admin", "superadmin")
INVITE_TTL_HOURS = 72


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def list_staff() -> list[dict[str, Any]]:
    """All users with role in ('admin', 'superadmin')."""
    try:
        client = get_supabase_client()
        result = (
            client.table("users")
            .select("id, email, username, role, is_active, last_login, created_at, privy_user_id")
            .in_("role", list(STAFF_ROLES))
            .execute()
        )
        rows = result.data if result.data else []
        for row in rows:
            row["has_privy_link"] = bool(row.pop("privy_user_id", None))
        return rows
    except Exception as e:
        logger.error("list_staff failed: %s", e)
        return []


def count_active_superadmins() -> int:
    """Count of users with role='superadmin' and is_active=true -- used to
    guard against demoting/removing the last one."""
    try:
        client = get_supabase_client()
        result = (
            client.table("users")
            .select("id", count="exact")
            .eq("role", "superadmin")
            .eq("is_active", True)
            .execute()
        )
        return result.count if result.count is not None else len(result.data or [])
    except Exception as e:
        logger.error("count_active_superadmins failed: %s", e)
        # Fail closed: an unknown count must not look like "safe to demote".
        return 0


def set_role(user_id: int, role: str, actor: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Set a user's role. Returns the updated row, or None if the user
    doesn't exist. Invalidates the in-memory user cache -- role gates
    (require_admin/require_superadmin) must see the change immediately,
    not up to 60s later."""
    # Lazy import: src.db.users imports src.db.api_keys, avoid any cycle risk.
    from src.db.users import invalidate_user_cache_by_id

    try:
        client = get_supabase_client()
        result = (
            client.table("users")
            .update({"role": role, "updated_at": datetime.now(UTC).isoformat()})
            .eq("id", user_id)
            .execute()
        )
        if not result.data:
            return None

        invalidate_user_cache_by_id(user_id)

        try:
            client.table("role_audit_log").insert(
                {
                    "user_id": user_id,
                    "new_role": role,
                    "changed_by": (actor or {}).get("id"),
                    "metadata": {"via": "admin_staff"},
                }
            ).execute()
        except Exception as e:
            # role_audit_log is the pre-existing, narrower trail (role
            # changes only); audit_log (src/db/audit.py) is the record of
            # truth for this endpoint and is written by the caller. Losing
            # this secondary trail must not fail the role change itself.
            logger.warning("role_audit_log insert failed for user %s: %s", user_id, e)

        return result.data[0]
    except Exception as e:
        logger.error("set_role failed for user %s: %s", user_id, e)
        return None


def create_invite(
    email: str, role: str, invited_by: int | None
) -> tuple[dict[str, Any] | None, str | None]:
    """Create a pending staff invite. Returns (row, raw_token) -- the raw
    token is returned exactly once and never stored (only its sha256 hash
    is persisted in admin_invites.token_hash)."""
    try:
        client = get_supabase_client()
        raw_token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(UTC) + timedelta(hours=INVITE_TTL_HOURS)).isoformat()

        result = (
            client.table("admin_invites")
            .insert(
                {
                    "email": email.strip().lower(),
                    "role": role,
                    "token_hash": _hash_token(raw_token),
                    "invited_by": invited_by,
                    "expires_at": expires_at,
                }
            )
            .execute()
        )
        if not result.data:
            return None, None
        return result.data[0], raw_token
    except Exception as e:
        logger.error("create_invite failed for %s: %s", email, e)
        return None, None


def get_invite_by_token(raw_token: str) -> dict[str, Any] | None:
    """Look up a pending (unaccepted, unexpired) invite by its raw token."""
    try:
        client = get_supabase_client()
        result = (
            client.table("admin_invites")
            .select("*")
            .eq("token_hash", _hash_token(raw_token))
            .is_("accepted_at", "null")
            .execute()
        )
        if not result.data:
            return None
        invite = result.data[0]
        expires_at = invite.get("expires_at")
        if expires_at:
            try:
                expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if expiry < datetime.now(UTC):
                    return None
            except ValueError:
                pass
        return invite
    except Exception as e:
        logger.error("get_invite_by_token failed: %s", e)
        return None


def accept_invite(raw_token: str, user_id: int, user_email: str) -> dict[str, Any] | None:
    """Claim a pending invite as the logged-in user identified by user_id,
    provided their verified email matches the invite's email
    (case-insensitive). Sets the user's role and marks the invite accepted.

    Returns the updated user row, or None if the token is invalid/expired/
    already accepted, or the email doesn't match.
    """
    invite = get_invite_by_token(raw_token)
    if invite is None:
        return None

    if invite["email"].strip().lower() != (user_email or "").strip().lower():
        logger.warning("accept_invite email mismatch for invite %s", invite.get("id"))
        return None

    updated = set_role(user_id, invite["role"], actor=None)
    if updated is None:
        return None

    try:
        client = get_supabase_client()
        client.table("admin_invites").update({"accepted_at": datetime.now(UTC).isoformat()}).eq(
            "id", invite["id"]
        ).execute()
    except Exception as e:
        # The role is already granted -- don't undo it over a bookkeeping
        # failure, but do log so a stale-looking invite can be investigated.
        logger.warning("Failed to mark invite %s accepted: %s", invite.get("id"), e)

    return updated


def revoke_user_keys(user_id: int) -> int:
    """Deactivate every api_keys_new row for a user (e.g. on staff removal).
    Returns the number of keys deactivated."""
    from src.db.users import invalidate_user_cache

    try:
        client = get_supabase_client()
        result = (
            client.table("api_keys_new")
            .select("api_key")
            .eq("user_id", user_id)
            .eq("is_active", True)
            .execute()
        )
        keys = [row["api_key"] for row in (result.data or [])]
        if not keys:
            return 0

        client.table("api_keys_new").update(
            {"is_active": False, "updated_at": datetime.now(UTC).isoformat()}
        ).eq("user_id", user_id).eq("is_active", True).execute()

        for api_key in keys:
            invalidate_user_cache(api_key)

        return len(keys)
    except Exception as e:
        logger.error("revoke_user_keys failed for user %s: %s", user_id, e)
        return 0
