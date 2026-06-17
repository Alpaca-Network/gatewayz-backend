"""Persistence + shadow-recording for the Phase 3 credit ledger (Gatewayz One).

Bridges the pure ledger math (:mod:`src.services.credit_ledger`) to the
append-only ``public.credit_ledger`` table. Used in SHADOW mode: alongside the
authoritative ``subscription_allowance``/``purchased_credits`` deduction it
records a settled double-entry, so the ledger can be reconciled against the live
system *before* any cutover to ledger-authoritative billing.

Contract for the shadow path:
  * **Non-blocking** — :func:`record_shadow_settlement` never raises; a failure
    here must never affect billing or the request. It returns ``True`` on write,
    ``False`` on any skip/failure (logged at WARNING).
  * **Idempotent** — keyed on ``ref`` (the request id). If the ref already has
    ledger rows, the write is skipped (client retries won't double-record).
  * **Allowance-first** — mirrors how ``deduct_credits`` spends balances, via
    :func:`src.services.credit_ledger.split_charge`.

The table is created by ``supabase/migrations/20260617000001_gatewayz_one_phase3_credit_ledger.sql``;
until that migration is applied (and ``CREDIT_LEDGER_SHADOW_ENABLED=true``) this
module is dormant.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

from src.services.credit_ledger import (
    ALLOWANCE,
    PURCHASED,
    REVENUE,
    EntryState,
    LedgerEntry,
    split_charge,
)

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")


def build_settled_entries(ref: str, cost, allowance, purchased) -> list[LedgerEntry]:
    """Build a balanced, SETTLED double-entry for a completed charge.

    Spends ``allowance`` before ``purchased`` (matching ``deduct_credits``) and
    credits ``revenue`` for the total. Raises ``ValueError`` /
    ``InsufficientBalance`` from :func:`split_charge` for a bad/oversized charge —
    callers in the shadow path swallow those.
    """
    split = split_charge(cost, allowance, purchased)
    entries: list[LedgerEntry] = []
    if split.from_allowance > 0:
        entries.append(LedgerEntry(ref, ALLOWANCE, split.from_allowance, _ZERO, EntryState.SETTLED))
    if split.from_purchased > 0:
        entries.append(LedgerEntry(ref, PURCHASED, split.from_purchased, _ZERO, EntryState.SETTLED))
    if split.total > 0:
        entries.append(LedgerEntry(ref, REVENUE, _ZERO, split.total, EntryState.SETTLED))
    return entries


def _entries_to_rows(entries: list[LedgerEntry], user_id) -> list[dict]:
    """Serialize ledger entries to ``credit_ledger`` insert rows (Decimals as str)."""
    return [
        {
            "ref": e.ref,
            "user_id": user_id,
            "account": e.account,
            "debit": str(e.debit),
            "credit": str(e.credit),
            "state": e.state.value,
        }
        for e in entries
    ]


def _record_shadow_settlement_sync(ref, user_id, cost, allowance, purchased) -> bool:
    """Synchronous core of :func:`record_shadow_settlement`. Never raises."""
    try:
        if not ref:
            return False

        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # Idempotency: skip if this ref already has ledger rows (retry-safe).
        existing = client.table("credit_ledger").select("id").eq("ref", ref).limit(1).execute()
        if getattr(existing, "data", None):
            return False

        entries = build_settled_entries(ref, cost, allowance, purchased)
        if not entries:
            return False

        client.table("credit_ledger").insert(_entries_to_rows(entries, user_id)).execute()
        return True
    except Exception as e:  # never let a shadow write affect billing
        logger.warning("credit_ledger shadow write skipped (ref=%s): %s", ref, e)
        return False


async def record_shadow_settlement(ref, user_id, cost, allowance, purchased) -> bool:
    """Record a SETTLED double-entry mirroring a completed deduction (shadow mode).

    Idempotent by ``ref`` and fully non-blocking — returns ``True`` if rows were
    written, ``False`` on any skip/failure. Runs the blocking Supabase I/O in a
    thread so it doesn't stall the event loop.
    """
    return await asyncio.to_thread(
        _record_shadow_settlement_sync, ref, user_id, cost, allowance, purchased
    )
