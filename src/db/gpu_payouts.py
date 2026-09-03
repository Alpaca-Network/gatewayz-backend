"""DB access for community-GPU verification + WAYZ payouts
(gatewayz-backend#2265, #2266; m4/spec.md §2, §5).

Mirrors src/db/wallet_stakes.py's try/except + logger.warning + safe-default
convention: every read returns an empty/None safe default on error, every
write returns bool, so a DB hiccup degrades a scheduled job's throughput
for one run rather than crashing it.

Ownership split (m4/_standing.md, WB-payouts.md): W-A1 owns the migration
(all tables below) and src/db/gpu.py (get_provider, list_providers,
set_node_status, ...). This module is built in parallel, before that
lands, so provider/node lookups below try importing from src.db.gpu first
and fall back to a direct query against the tables documented in
m4/spec.md §2 -- functionally complete either way, and free of duplicate
logic once src.db.gpu exists (the fallback branch simply stops being
exercised). adjust_health_score has no W-A1 equivalent planned per
WB-payouts.md, so it lives here permanently.
"""

from __future__ import annotations

import logging

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

_RATES_TABLE = "provider_payout_rates"
_WORK_TABLE = "provider_work"
_EARNINGS_TABLE = "provider_earnings"
_SETTLEMENTS_TABLE = "provider_settlements"
_NODES_TABLE = "gpu_nodes"
_PROVIDERS_TABLE = "gpu_providers"

# Row caps -- testnet scale, but real limits rather than unbounded selects.
_WORK_QUERY_ROW_CAP = 2000
_EARNINGS_LIST_ROW_CAP = 50
_PROVIDERS_ROW_CAP = 1000


# ---------------------------------------------------------------------------
# Payout rates
# ---------------------------------------------------------------------------


def get_payout_rate_wei_per_1k(model_class: str) -> int | None:
    """wayz_per_1k_tokens (wei) for a model class, or None if unseeded/on error."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_RATES_TABLE)
            .select("wayz_per_1k_tokens")
            .eq("model_class", model_class)
            .execute()
        )
        if not result.data:
            return None
        return int(result.data[0]["wayz_per_1k_tokens"])
    except Exception as e:
        logger.warning(f"provider_payout_rates lookup failed for {model_class}: {e}")
        return None


# ---------------------------------------------------------------------------
# provider_work / verification
# ---------------------------------------------------------------------------


def list_sampled_pending_work(since_iso: str) -> list[dict]:
    """provider_work rows selected for spot-check (verification='sampled')
    created at or after since_iso, oldest first. These are the candidates
    the verifier job replays each run."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_WORK_TABLE)
            .select("*")
            .eq("verification", "sampled")
            .gte("created_at", since_iso)
            .order("created_at", desc=False)
            .limit(_WORK_QUERY_ROW_CAP)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"provider_work sampled-pending lookup failed: {e}")
        return []


def list_agable_pending_work(older_than_iso: str) -> list[dict]:
    """provider_work rows never selected for spot-check (verification still
    'pending') and older than older_than_iso -- the 24h aging path that
    resolves them to verified/skipped without ever being replayed."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_WORK_TABLE)
            .select("*")
            .eq("verification", "pending")
            .lt("created_at", older_than_iso)
            .order("created_at", desc=False)
            .limit(_WORK_QUERY_ROW_CAP)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"provider_work agable-pending lookup failed: {e}")
        return []


def get_work(work_id: int) -> dict | None:
    try:
        client = get_supabase_client()
        result = client.table(_WORK_TABLE).select("*").eq("id", work_id).execute()
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"provider_work lookup failed for {work_id}: {e}")
        return None


def set_verification(work_id: int, state: str) -> bool:
    """Update provider_work.verification. Caller is responsible for only
    passing a value in the 'pending'/'sampled'/'verified'/'failed'/'skipped'
    CHECK set -- this module doesn't re-validate it."""
    try:
        client = get_supabase_client()
        client.table(_WORK_TABLE).update({"verification": state}).eq("id", work_id).execute()
        return True
    except Exception as e:
        logger.warning(f"provider_work verification update failed for {work_id}: {e}")
        return False


def node_verification_stats_since(node_id: int, since_iso: str) -> tuple[int, int]:
    """(failed_count, resolved_count) of a node's provider_work rows with a
    RESOLVED verification outcome (verified or failed; pending/sampled/
    skipped excluded) since since_iso. Used for both the 3-fails/24h
    disable rule and the unsampled-row daily-failure-rate threshold.
    Returns (0, 0) on error -- callers treat that as "no data yet", not a
    100% or 0% failure rate.
    """
    try:
        client = get_supabase_client()
        result = (
            client.table(_WORK_TABLE)
            .select("verification")
            .eq("node_id", node_id)
            .in_("verification", ["verified", "failed"])
            .gte("created_at", since_iso)
            .limit(_WORK_QUERY_ROW_CAP)
            .execute()
        )
        rows = result.data or []
        failed = sum(1 for row in rows if row["verification"] == "failed")
        return failed, len(rows)
    except Exception as e:
        logger.warning(f"provider_work node verification stats failed for node {node_id}: {e}")
        return 0, 0


def list_recent_work_for_provider(
    provider_id: int, limit: int = _EARNINGS_LIST_ROW_CAP
) -> list[dict]:
    """Most recent provider_work rows for a provider -- for the earnings
    endpoint's work history. Never includes prompt_hash/response_hash's
    plaintext (there is none to leak; only the hashes themselves, which
    the route strips per WB-payouts.md's endpoint contract)."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_WORK_TABLE)
            .select("*")
            .eq("provider_id", provider_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"provider_work recent-list failed for provider {provider_id}: {e}")
        return []


# ---------------------------------------------------------------------------
# gpu_nodes health (no W-A1 equivalent planned -- owned here permanently)
# ---------------------------------------------------------------------------


def adjust_health_score(node_id: int, delta: float) -> bool:
    """Add delta (may be negative) to a node's health_score, floored at 0.
    Reads-then-writes rather than an atomic SQL increment -- Supabase's
    REST layer has no server-side arithmetic update, and a lost update
    under rare concurrent spot-check failures for the same node is an
    acceptable tradeoff at testnet scale (worst case: one penalty is
    under-applied, not a correctness or security issue)."""
    try:
        client = get_supabase_client()
        result = client.table(_NODES_TABLE).select("health_score").eq("id", node_id).execute()
        if not result.data:
            logger.warning(f"adjust_health_score: node {node_id} not found")
            return False
        current = result.data[0].get("health_score")
        current = float(current) if current is not None else 100.0
        new_score = max(0.0, current + delta)
        client.table(_NODES_TABLE).update({"health_score": new_score}).eq("id", node_id).execute()
        return True
    except Exception as e:
        logger.warning(f"adjust_health_score failed for node {node_id}: {e}")
        return False


def get_node(node_id: int) -> dict | None:
    """A single gpu_nodes row. Tries src.db.gpu.get_node (W-A1) first and
    falls back to a direct query -- see module docstring."""
    try:
        from src.db.gpu import get_node as _get_node  # type: ignore[import-not-found]

        return _get_node(node_id)
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"src.db.gpu.get_node failed for {node_id}, falling back: {e}")

    try:
        client = get_supabase_client()
        result = client.table(_NODES_TABLE).select("*").eq("id", node_id).execute()
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"gpu_nodes lookup failed for {node_id}: {e}")
        return None


def disable_node(node_id: int) -> bool:
    """Set a node's status to 'disabled' (3-fails/24h penalty). Tries
    src.db.gpu.set_node_status (W-A1) first and falls back to a direct
    update -- see module docstring."""
    try:
        from src.db.gpu import set_node_status  # type: ignore[import-not-found]

        return set_node_status(node_id, "disabled")
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"src.db.gpu.set_node_status failed for {node_id}, falling back: {e}")

    try:
        client = get_supabase_client()
        client.table(_NODES_TABLE).update({"status": "disabled"}).eq("id", node_id).execute()
        return True
    except Exception as e:
        logger.warning(f"gpu_nodes disable failed for {node_id}: {e}")
        return False


# ---------------------------------------------------------------------------
# gpu_providers lookups
# ---------------------------------------------------------------------------


def get_provider_for_user(user_id: int) -> dict | None:
    """The caller's own gpu_providers row, or None. Tries src.db.gpu first
    (see module docstring) -- an OR-across-user lookup is never exposed
    here, mirroring src/routes/faucet.py's IDOR-safety pattern of always
    scoping provider-facing reads to the authenticated caller."""
    try:
        from src.db.gpu import (
            get_provider_for_user as _get_provider_for_user,  # type: ignore[import-not-found]
        )

        return _get_provider_for_user(user_id)
    except ImportError:
        pass
    except Exception as e:
        logger.warning(
            f"src.db.gpu.get_provider_for_user failed for user {user_id}, falling back: {e}"
        )

    try:
        client = get_supabase_client()
        result = client.table(_PROVIDERS_TABLE).select("*").eq("user_id", user_id).execute()
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"gpu_providers lookup failed for user {user_id}: {e}")
        return None


def list_approved_providers() -> list[dict]:
    """All gpu_providers rows with status='approved' -- the settlement
    job's per-provider iteration. Tries src.db.gpu first, see module
    docstring."""
    try:
        from src.db.gpu import list_providers  # type: ignore[import-not-found]

        return list_providers(status="approved")
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"src.db.gpu.list_providers failed, falling back: {e}")

    try:
        client = get_supabase_client()
        result = (
            client.table(_PROVIDERS_TABLE)
            .select("*")
            .eq("status", "approved")
            .limit(_PROVIDERS_ROW_CAP)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"gpu_providers approved-list failed: {e}")
        return []


# ---------------------------------------------------------------------------
# provider_earnings
# ---------------------------------------------------------------------------


def create_earning(provider_id: int, work_id: int, amount_wei: int) -> dict | None:
    """Insert an 'accrued' provider_earnings row. work_id is UNIQUE, so a
    duplicate call (e.g. a re-run after a crash) raises instead of
    double-paying -- caught and logged at info level (expected, not an
    error) and treated as "already recorded"."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_EARNINGS_TABLE)
            .insert(
                {
                    "provider_id": provider_id,
                    "work_id": work_id,
                    "amount_wei": str(amount_wei),
                    "status": "accrued",
                }
            )
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.info(f"provider_earnings insert skipped for work {work_id} (likely duplicate): {e}")
        return None


def void_earning_for_work(work_id: int) -> bool:
    """Flip an earning to 'void' (spot-check failure after it had already
    accrued). Only ever touches an 'accrued' row -- a 'settled' earning is
    money already sent and is never voided retroactively here."""
    try:
        client = get_supabase_client()
        client.table(_EARNINGS_TABLE).update({"status": "void"}).eq("work_id", work_id).eq(
            "status", "accrued"
        ).execute()
        return True
    except Exception as e:
        logger.warning(f"provider_earnings void failed for work {work_id}: {e}")
        return False


def list_accrued_earnings(provider_id: int) -> list[dict]:
    """All 'accrued' provider_earnings rows for a provider -- the
    settlement job snapshots this list (ids + amounts) once per run so the
    later mark-settled step touches exactly the rows it summed, not
    whatever happens to be 'accrued' by the time the transfer completes."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_EARNINGS_TABLE)
            .select("*")
            .eq("provider_id", provider_id)
            .eq("status", "accrued")
            .limit(_WORK_QUERY_ROW_CAP)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"provider_earnings accrued-list failed for provider {provider_id}: {e}")
        return []


def earnings_totals(provider_id: int) -> dict[str, int]:
    """{'accrued': wei, 'settled': wei, 'void': wei} totals for a provider.
    Zeroed out on error."""
    totals = {"accrued": 0, "settled": 0, "void": 0}
    try:
        client = get_supabase_client()
        result = (
            client.table(_EARNINGS_TABLE)
            .select("amount_wei,status")
            .eq("provider_id", provider_id)
            .limit(_WORK_QUERY_ROW_CAP)
            .execute()
        )
        for row in result.data or []:
            status = row.get("status")
            if status in totals:
                totals[status] += int(row["amount_wei"])
        return totals
    except Exception as e:
        logger.warning(f"provider_earnings totals failed for provider {provider_id}: {e}")
        return totals


def mark_earnings_settled(earning_ids: list[int], settlement_id: int) -> bool:
    """Flip a specific set of earning rows (by id) from 'accrued' to
    'settled', tagged with the settlement that paid them. Only 'accrued'
    rows match -- an id that's somehow already settled/void is a no-op,
    not an error."""
    if not earning_ids:
        return True
    try:
        client = get_supabase_client()
        client.table(_EARNINGS_TABLE).update(
            {"status": "settled", "settlement_id": settlement_id}
        ).in_("id", earning_ids).eq("status", "accrued").execute()
        return True
    except Exception as e:
        logger.warning(f"provider_earnings settle failed for settlement {settlement_id}: {e}")
        return False


# ---------------------------------------------------------------------------
# provider_settlements
# ---------------------------------------------------------------------------


def get_pending_settlement(provider_id: int) -> dict | None:
    """A provider's in-flight ('pending') settlement, if any -- the
    idempotency guard: a settlement stuck 'pending' (e.g. the job crashed
    between creating the row and sending the transfer) must never be
    duplicated by the next run."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_SETTLEMENTS_TABLE)
            .select("*")
            .eq("provider_id", provider_id)
            .eq("status", "pending")
            .execute()
        )
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(
            f"provider_settlements pending lookup failed for provider {provider_id}: {e}"
        )
        return None


def create_settlement(
    provider_id: int, period_start_iso: str, period_end_iso: str, amount_wei: int
) -> dict | None:
    try:
        client = get_supabase_client()
        result = (
            client.table(_SETTLEMENTS_TABLE)
            .insert(
                {
                    "provider_id": provider_id,
                    "period_start": period_start_iso,
                    "period_end": period_end_iso,
                    "amount_wei": str(amount_wei),
                    "status": "pending",
                }
            )
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.warning(f"provider_settlements insert failed for provider {provider_id}: {e}")
        return None


def mark_settlement_sent(settlement_id: int, tx_hash: str) -> bool:
    try:
        client = get_supabase_client()
        client.table(_SETTLEMENTS_TABLE).update({"status": "sent", "tx_hash": tx_hash}).eq(
            "id", settlement_id
        ).execute()
        return True
    except Exception as e:
        logger.warning(f"provider_settlements mark-sent failed for {settlement_id}: {e}")
        return False


def mark_settlement_failed(settlement_id: int, error: str) -> bool:
    try:
        client = get_supabase_client()
        client.table(_SETTLEMENTS_TABLE).update({"status": "failed", "error": error}).eq(
            "id", settlement_id
        ).execute()
        return True
    except Exception as e:
        logger.warning(f"provider_settlements mark-failed failed for {settlement_id}: {e}")
        return False


def list_settlements_for_provider(
    provider_id: int, limit: int = _EARNINGS_LIST_ROW_CAP
) -> list[dict]:
    try:
        client = get_supabase_client()
        result = (
            client.table(_SETTLEMENTS_TABLE)
            .select("*")
            .eq("provider_id", provider_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"provider_settlements list failed for provider {provider_id}: {e}")
        return []
