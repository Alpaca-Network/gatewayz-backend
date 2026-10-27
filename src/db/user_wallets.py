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
from datetime import datetime
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

_TABLE = "user_wallets"

# user_wallets.chain_namespace values (CAIP-2 namespaces). Every wallet linked
# before delegated staking is an EVM wallet; Cardano stake addresses linked
# with a CIP-30 signData proof carry "cip34" (src/routes/wallet_auth_cardano.py).
EVM_NAMESPACE = "eip155"
CARDANO_NAMESPACE = "cip34"


def wallet_namespace(row: dict[str, Any]) -> str:
    """The row's chain namespace. A row (or a test double) without the
    column predates it and is EVM, which is also the column default."""
    return str(row.get("chain_namespace") or EVM_NAMESPACE)


def is_evm_wallet(row: dict[str, Any]) -> bool:
    """True for an EVM (0x...) wallet. Jobs that read EVM balances must skip
    every other namespace: a Cardano stake address is not an EVM address."""
    return wallet_namespace(row) == EVM_NAMESPACE


def is_cardano_wallet(row: dict[str, Any]) -> bool:
    return wallet_namespace(row) == CARDANO_NAMESPACE


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
    chain_namespace: str = EVM_NAMESPACE,
) -> dict[str, Any] | None:
    """Link a wallet to a user. Returns the created row, or None on any
    failure -- including the wallet_address UNIQUE conflict (address
    already linked to some user, possibly this one). Callers that need to
    distinguish "already linked to me" (idempotent success) from "linked to
    someone else" (409) must call get_wallet(address) first and branch on
    that, since a unique-violation and a transient DB error both collapse
    to None here (same safe-default convention as every other DB module).

    `chain_namespace` is only written when it is not the column default
    ("eip155"), so an EVM link inserts exactly the row it always did.
    """
    row = {
        "user_id": user_id,
        "wallet_address": address.lower(),
        "source": source,
        "wallet_client_type": wallet_client_type,
        "is_primary": make_primary,
    }
    if chain_namespace != EVM_NAMESPACE:
        row["chain_namespace"] = chain_namespace
    try:
        client = get_supabase_client()
        result = client.table(_TABLE).insert(row).execute()
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"user_wallets link failed for user {user_id} / {address}: {e}")
        return None


def count_all_wallets() -> int:
    """Total number of linked wallets across all users, for the admin WAYZ
    ops status endpoint. 0 on any lookup error."""
    try:
        client = get_supabase_client()
        result = client.table(_TABLE).select("id", count="exact").execute()
        return result.count or 0
    except Exception as e:
        logger.warning(f"user_wallets total count failed: {e}")
        return 0


def count_wallets_linked_before(cutoff: datetime) -> int | None:
    """How many EVM wallets were linked at or before ``cutoff`` -- the
    wallets old enough for the holdings sweep to consider (it reads EVM
    balances only, so a Cardano link is never eligible). A count, not a
    list, for the sweep watchdog in src/services/holdings/alerts.py. None
    (not 0) on a lookup error, so a failed read is never mistaken for "no
    eligible wallets"."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_TABLE)
            .select("id", count="exact")
            .eq("chain_namespace", EVM_NAMESPACE)
            .lte("created_at", cutoff.isoformat())
            .limit(1)
            .execute()
        )
        return result.count or 0
    except Exception as e:
        logger.warning(f"user_wallets eligible count failed: {e}")
        return None


_ALL_WALLETS_ROW_CAP = 10000


def list_all_wallets() -> list[dict[str, Any]]:
    """Every linked wallet, oldest-linked first, for jobs that sweep the
    whole set (the holdings-rewards observation sweep in
    src/services/holdings/snapshots.py). Ordered by created_at so the sweep
    visits wallets in a stable order across runs, and row-capped like every
    other bulk read here. Empty list on any lookup error -- a sweep that
    sees no wallets records nothing, which is the safe outcome."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_TABLE)
            .select("*")
            .order("created_at", desc=False)
            .limit(_ALL_WALLETS_ROW_CAP)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"user_wallets full list failed: {e}")
        return []


_BY_SOURCE_ROW_CAP = 10000


def count_wallets_by_source() -> dict[str, int]:
    """{'privy': n, 'siwe': n, ...} counts of linked wallets grouped by
    `source`, for the admin WAYZ ops status endpoint. Empty dict on any
    lookup error -- never raises. Row-capped like every other summary read
    in this codebase (see src/db/wallet_stakes.py's _STAKE_TOTALS_ROW_CAP)."""
    try:
        client = get_supabase_client()
        result = client.table(_TABLE).select("source").limit(_BY_SOURCE_ROW_CAP).execute()
        rows = result.data or []
        if len(rows) >= _BY_SOURCE_ROW_CAP:
            logger.warning(
                f"count_wallets_by_source hit the {_BY_SOURCE_ROW_CAP}-row cap; "
                "counts may be incomplete"
            )
        counts: dict[str, int] = {}
        for row in rows:
            source = row.get("source") or "unknown"
            counts[source] = counts.get(source, 0) + 1
        return counts
    except Exception as e:
        logger.warning(f"user_wallets by-source count failed: {e}")
        return {}


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
