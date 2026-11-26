"""DB access for the wallet-link eligibility record (US / Canada block).

One row per account in ``delegation_eligibility`` -- the table keeps the name
it was created with (supabase/migrations); it now backs the gate applied when
a Cardano wallet is linked (src/services/wallet/eligibility.py).

try/except + logger.warning + a safe default: a failed read is None, which
callers treat as "not eligible".
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

_ELIGIBILITY_TABLE = "delegation_eligibility"


def get_eligibility(user_id: int) -> dict[str, Any] | None:
    """The account's eligibility row; {} when it has none. None on a failed
    read -- callers treat that as "not eligible"."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_ELIGIBILITY_TABLE).select("*").eq("user_id", user_id).limit(1).execute()
        )
        rows = result.data or []
        return rows[0] if rows else {}
    except Exception as e:
        logger.warning(f"wallet eligibility lookup failed: {e}")
        return None


def record_attestation(
    user_id: int, attestation_version: str, country: str | None
) -> dict[str, Any] | None:
    """Mark the account eligible after it attested. Never clears a
    'restricted' row: that one is returned as-is. None on any failure.

    The write is conditional so a concurrent mark_restricted cannot be
    overwritten: an existing row is updated only WHERE status is not
    'restricted', and a missing row is inserted without overwriting one
    that appeared in between. The row is read back afterwards, so the
    caller always sees what is stored."""
    existing = get_eligibility(user_id)
    if existing is None:
        return None
    if existing.get("status") == "restricted":
        return existing
    now = datetime.now(UTC).isoformat()
    fields = {
        "status": "eligible",
        "attested_at": now,
        "attestation_version": attestation_version,
        "country": country,
        "updated_at": now,
    }
    try:
        client = get_supabase_client()
        table = client.table(_ELIGIBILITY_TABLE)
        if existing.get("status"):
            table.update(fields).eq("user_id", user_id).neq("status", "restricted").execute()
        else:
            table.upsert(
                {"user_id": user_id, **fields}, on_conflict="user_id", ignore_duplicates=True
            ).execute()
    except Exception as e:
        logger.warning(f"wallet eligibility attestation write failed: {e}")
        return None
    return get_eligibility(user_id)


def mark_restricted(user_id: int, country: str | None, reason: str) -> bool:
    """Mark the account restricted (sticky). Keeps any earlier attestation
    fields for the record."""
    now = datetime.now(UTC).isoformat()
    try:
        client = get_supabase_client()
        client.table(_ELIGIBILITY_TABLE).upsert(
            {
                "user_id": user_id,
                "status": "restricted",
                "country": country,
                "restricted_reason": reason[:200],
                "updated_at": now,
            },
            on_conflict="user_id",
        ).execute()
        return True
    except Exception as e:
        logger.warning(f"wallet eligibility restrict write failed: {e}")
        return False
