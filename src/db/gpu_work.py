"""DB access for ``provider_work`` -- community-adapter call receipts
(gatewayz-backend#2262 #2265, spec §2/§4).

Mirrors ``src/db/faucet.py``'s try/except + logger.warning + safe-default
convention: a failed insert returns ``None`` rather than raising, so a DB
hiccup degrades to "no receipt recorded" instead of failing the user's
chat request (the receipt is for verification/payout bookkeeping, not for
serving the response).

The ``provider_work`` table itself (migration ``20260903200000_gpu_marketplace.sql``)
is owned by W-A1 -- this module only reads/writes rows via the Supabase
client, same as every other ``src/db/*`` module. NEVER pass prompt/response
content here -- only hashes (threat model G3).
"""

from __future__ import annotations

import logging
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

_TABLE = "provider_work"


def record_work(
    *,
    billing_ref: str | None,
    node_id: Any,
    provider_id: Any,
    model: str,
    prompt_hash: str,
    response_hash: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    status: str,
) -> dict | None:
    """Insert one ``provider_work`` row. Returns the inserted row, or ``None``
    on failure (including a duplicate ``billing_ref`` -- the unique
    constraint's expected race outcome, treated like ``faucet.create_pending_claim``).

    ``status`` must be ``'completed'`` or ``'failed'`` (DB CHECK constraint).
    """
    if not billing_ref:
        logger.warning("record_work called without a billing_ref; skipping receipt")
        return None
    try:
        client = get_supabase_client()
        result = (
            client.table(_TABLE)
            .insert(
                {
                    "billing_ref": billing_ref,
                    "node_id": node_id,
                    "provider_id": provider_id,
                    "model": model,
                    "prompt_hash": prompt_hash,
                    "response_hash": response_hash,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "latency_ms": latency_ms,
                    "status": status,
                }
            )
            .execute()
        )
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning("provider_work insert failed for billing_ref=%s: %s", billing_ref, e)
        return None


def mark_attested(work_id: Any, signature: str) -> bool:
    """Mark a ``provider_work`` row ``attested=true`` with its signature.

    Returns False (never raises) on any DB failure -- attestation is a bonus
    signal for W-B's spot-checker, not required for the request to succeed.
    """
    try:
        client = get_supabase_client()
        result = (
            client.table(_TABLE)
            .update({"attested": True, "attestation_sig": signature})
            .eq("id", work_id)
            .execute()
        )
        return bool(result.data)
    except Exception as e:
        logger.warning("provider_work attestation update failed for id=%s: %s", work_id, e)
        return False
