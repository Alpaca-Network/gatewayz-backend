"""DB access for user_wallets -- wallet-to-account linkage (Milestone 2,
gatewayz-backend#2249 #2250 #2251 #2252).

Mirrors src/db/wallet_stakes.py's try/except + logger.warning +
safe-default convention: callers must treat a lookup failure as "no data,"
never as a hard failure, matching every other DB module in this session.
See docs/superpowers/specs/2026-09-03-wallet-identity-auth-design.md
section 4.5.
"""

from __future__ import annotations

import logging
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

_TABLE = "user_wallets"


def get_wallets_for_user(user_id: int) -> list[dict[str, Any]]:
    """All wallets linked to a user, most-recently-linked first. Empty list
    on any lookup error."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_TABLE)
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"user_wallets lookup failed for user {user_id}: {e}")
        return []


def get_wallet(address: str) -> dict[str, Any] | None:
    """The user_wallets row for a single address, or None if unlinked (or
    on error)."""
    try:
        client = get_supabase_client()
        result = client.table(_TABLE).select("*").eq("wallet_address", address.lower()).execute()
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"user_wallets lookup failed for {address}: {e}")
        return None


def count_wallets(user_id: int) -> int:
    """Number of wallets linked to a user. 0 on any lookup error -- callers
    that gate a destructive action (e.g. unlink) on this must not treat a
    transient DB error as "safe to proceed"; see unlink call sites."""
    try:
        client = get_supabase_client()
        result = client.table(_TABLE).select("id").eq("user_id", user_id).execute()
        return len(result.data or [])
    except Exception as e:
        logger.warning(f"user_wallets count failed for user {user_id}: {e}")
        return 0


def link_wallet(
    user_id: int,
    address: str,
    source: str,
    wallet_client_type: str | None = None,
    make_primary: bool = False,
) -> dict[str, Any] | None:
    """Link a wallet to a user. Returns the created row, or None on any
    failure -- including the wallet_address UNIQUE conflict (address
    already linked to some user, possibly this one). Callers that need to
    distinguish "already linked to me" (idempotent success) from "linked to
    someone else" (409) must call get_wallet(address) first and branch on
    that, since a unique-violation and a transient DB error both collapse
    to None here (same safe-default convention as every other DB module).
    """
    try:
        client = get_supabase_client()
        result = (
            client.table(_TABLE)
            .insert(
                {
                    "user_id": user_id,
                    "wallet_address": address.lower(),
                    "source": source,
                    "wallet_client_type": wallet_client_type,
                    "is_primary": make_primary,
                }
            )
            .execute()
        )
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"user_wallets link failed for user {user_id} / {address}: {e}")
        return None


def unlink_wallet(user_id: int, address: str) -> bool:
    """Remove a wallet link owned by this user. Returns True iff a row was
    deleted; False on no match (wrong owner / not linked) or any error."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_TABLE)
            .delete()
            .eq("user_id", user_id)
            .eq("wallet_address", address.lower())
            .execute()
        )
        return bool(result.data)
    except Exception as e:
        logger.warning(f"user_wallets unlink failed for user {user_id} / {address}: {e}")
        return False
