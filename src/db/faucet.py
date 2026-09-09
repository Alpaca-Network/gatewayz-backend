"""DB access for faucet_claims and eligibility checks (gatewayz-backend#2245).

Mirrors src/db/routing_policies.py's try/except + logger.warning +
safe-default convention for reads. create_pending_claim is the exception:
a failed insert (including a unique-constraint violation, the expected
race outcome of a duplicate claim) returns None rather than raising --
callers treat None as "already claimed," not as a hard failure.
"""

from __future__ import annotations

import logging

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

_CLAIMS_TABLE = "faucet_claims"
_USAGE_TABLE = "usage_records"


def has_completed_at_least_one_request(user_id: int, min_requests: int = 1) -> bool:
    """True if this user has at least min_requests rows in usage_records."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_USAGE_TABLE)
            .select("id")
            .eq("user_id", user_id)
            .limit(min_requests)
            .execute()
        )
        return len(result.data or []) >= min_requests
    except Exception as e:
        logger.warning(f"usage_records eligibility check failed for user {user_id}: {e}")
        return False


def get_existing_claim(user_id: int, wallet_address: str) -> dict | None:
    """A faucet_claims row for this user OR this wallet, if either already claimed."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_CLAIMS_TABLE)
            .select("*")
            .or_(f"user_id.eq.{user_id},wallet_address.eq.{wallet_address}")
            .execute()
        )
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"faucet_claims lookup failed for user {user_id}: {e}")
        return None


def get_claim_for_user(user_id: int) -> dict | None:
    """A faucet_claims row for this user, if one exists. Used by GET
    /faucet/status when the caller doesn't supply a wallet_address --
    a plain user_id filter, not get_existing_claim's OR-across-wallet
    lookup (which needs a real wallet_address to be meaningful)."""
    try:
        client = get_supabase_client()
        result = client.table(_CLAIMS_TABLE).select("*").eq("user_id", user_id).execute()
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"faucet_claims lookup failed for user {user_id}: {e}")
        return None


def create_pending_claim(user_id: int, wallet_address: str, amount: int) -> dict | None:
    """Insert a pending claim row. Returns the row, or None on any failure
    (including the expected unique-constraint violation from a duplicate
    claim -- the caller treats None as "already claimed")."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_CLAIMS_TABLE)
            .insert(
                {
                    "user_id": user_id,
                    "wallet_address": wallet_address,
                    "amount": str(amount),
                    "status": "pending",
                }
            )
            .execute()
        )
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"faucet_claims insert failed for user {user_id}: {e}")
        return None


def mark_claim_sent(claim_id: int, tx_hash: str) -> None:
    try:
        client = get_supabase_client()
        client.table(_CLAIMS_TABLE).update({"status": "sent", "tx_hash": tx_hash}).eq(
            "id", claim_id
        ).execute()
    except Exception as e:
        logger.warning(f"faucet_claims mark-sent failed for claim {claim_id}: {e}")


def mark_claim_failed(claim_id: int, error: str) -> None:
    try:
        client = get_supabase_client()
        client.table(_CLAIMS_TABLE).update({"status": "failed", "error": error}).eq(
            "id", claim_id
        ).execute()
    except Exception as e:
        logger.warning(f"faucet_claims mark-failed failed for claim {claim_id}: {e}")


# Row cap for the admin ops status summary -- real limit rather than an
# unbounded select, same convention as src/db/wallet_stakes.py's
# _STAKE_TOTALS_ROW_CAP.
_CLAIM_STATS_ROW_CAP = 10000


def get_claim_stats() -> dict:
    """Counts of faucet_claims by status ('pending'/'sent'/'failed') plus
    the most recent claim's claimed_at, for the admin WAYZ ops status
    endpoint. Zeroed counts and last_claim_at=None on any lookup error --
    never raises."""
    stats = {"pending": 0, "sent": 0, "failed": 0, "last_claim_at": None}
    try:
        client = get_supabase_client()
        result = (
            client.table(_CLAIMS_TABLE)
            .select("status,claimed_at")
            .order("claimed_at", desc=True)
            .limit(_CLAIM_STATS_ROW_CAP)
            .execute()
        )
        rows = result.data or []
        for row in rows:
            status = row.get("status")
            if status in stats:
                stats[status] += 1
        if rows:
            stats["last_claim_at"] = rows[0].get("claimed_at")
        return stats
    except Exception as e:
        logger.warning(f"faucet_claims stats lookup failed: {e}")
        return stats
