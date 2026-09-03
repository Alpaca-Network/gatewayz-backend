"""DB access for community-GPU verification + WAYZ payouts
(gatewayz-backend#2265, #2266; m4/spec.md §2, §5).

Mirrors src/db/wallet_stakes.py's try/except + logger.warning + safe-default
convention: every read returns an empty/None safe default on error, every
write returns bool, so a DB hiccup degrades a scheduled job's throughput
for one run rather than crashing it.

Ownership split (m4/_standing.md, WB-payouts.md): W-A1 owns the migration
(all tables below) and src/db/gpu.py (get_provider_by_user, list_providers,
set_node_status, get_node, ...) -- merged as of gatewayz-backend#2285
(commit 48ace7ef). The provider/node lookups below are now thin
re-exports of the real src.db.gpu helpers (kept under this module's own
names so every caller here only ever imports from gpu_payouts, not two
different db modules). adjust_health_score has no W-A1 equivalent, so it
lives here permanently -- it's the one function in this section that
isn't a re-export.

W-A2's community_adapter.get_node_adapter is a SEPARATE, still-unmerged
dependency (src/services/gpu/spot_check.py) -- that one still needs its
ImportError-fallback treatment; this module has nothing to do with it.
"""

from __future__ import annotations

import logging

from src.config.supabase_config import get_supabase_client
from src.db.gpu import get_node as _gpu_get_node
from src.db.gpu import get_provider_by_user as _gpu_get_provider_by_user
from src.db.gpu import list_providers as _gpu_list_providers
from src.db.gpu import set_node_status as _gpu_set_node_status

logger = logging.getLogger(__name__)

_RATES_TABLE = "provider_payout_rates"
_WORK_TABLE = "provider_work"
_EARNINGS_TABLE = "provider_earnings"
_SETTLEMENTS_TABLE = "provider_settlements"
_NODES_TABLE = "gpu_nodes"

# Row caps -- testnet scale, but real limits rather than unbounded selects.
_WORK_QUERY_ROW_CAP = 2000
_EARNINGS_LIST_ROW_CAP = 50


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
    """provider_work rows older than older_than_iso still awaiting a final
    outcome -- 'pending' (never selected for spot-check) OR 'sampled' (was
    selected, but the verifier job could never resolve it -- no stash, no
    node, no replay infra available -- so it never advanced past
    'sampled'). Both cases fall through to the same 24h aging resolution
    (src/services/gpu/spot_check.py's _resolve_aged_row) rather than
    getting stuck unresolved forever."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_WORK_TABLE)
            .select("*")
            .in_("verification", ["pending", "sampled"])
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
    """A single gpu_nodes row -- re-exports src.db.gpu.get_node (W-A1)."""
    return _gpu_get_node(node_id)


def disable_node(node_id: int) -> bool:
    """Set a node's status to 'disabled' (3-fails/24h penalty). Wraps
    src.db.gpu.set_node_status (W-A1), which returns the updated row (or
    None on failure) rather than a bool -- normalized to bool here so
    every write helper in this module has the same return contract."""
    return _gpu_set_node_status(node_id, "disabled") is not None


# ---------------------------------------------------------------------------
# gpu_providers lookups
# ---------------------------------------------------------------------------


def get_provider_for_user(user_id: int) -> dict | None:
    """The caller's own gpu_providers row, or None -- re-exports
    src.db.gpu.get_provider_by_user (W-A1). An OR-across-user lookup is
    never exposed here, mirroring src/routes/faucet.py's IDOR-safety
    pattern of always scoping provider-facing reads to the authenticated
    caller."""
    return _gpu_get_provider_by_user(user_id)


def list_approved_providers() -> list[dict]:
    """All gpu_providers rows with status='approved' -- the settlement
    job's per-provider iteration. Re-exports src.db.gpu.list_providers
    (W-A1)."""
    return _gpu_list_providers(status="approved")


# ---------------------------------------------------------------------------
# provider_earnings
# ---------------------------------------------------------------------------


def _is_duplicate_error(error: Exception) -> bool:
    """True if *error* is a Postgres unique-violation (duplicate work_id).
    Mirrors src/db/webhook_events.py's identical helper -- same problem,
    same convention."""
    message = str(error).lower()
    return (
        "23505" in message
        or "duplicate key" in message
        or "already exists" in message
        or "unique constraint" in message
    )


def create_earning(provider_id: int, work_id: int, amount_wei: int) -> tuple[dict | None, str]:
    """Insert an 'accrued' provider_earnings row. Returns (row_or_None, outcome):

    'created'   -- inserted successfully.
    'duplicate' -- work_id's UNIQUE constraint rejected it (e.g. a re-run
                   after a crash) -- a genuine no-op, logged at INFO.
    'db_error'  -- the insert failed for any OTHER reason (network blip,
                   RLS misconfig, malformed payload...) -- logged at
                   WARNING, not INFO, since this is a real, visible
                   failure to pay a verified work item. PR #2288 review
                   I1: the old version caught every exception as "likely
                   duplicate" and logged it at INFO, silently losing a
                   legitimate earning with no operator signal. Callers
                   must NOT assume 'db_error' means the work is unpaid
                   forever -- src/services/gpu/spot_check.py's
                   run_spot_check_verification retries these via its
                   reconciliation pass.
    """
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
        return (result.data[0] if result.data else None), "created"
    except Exception as e:
        if _is_duplicate_error(e):
            logger.info(f"provider_earnings insert skipped for work {work_id} (duplicate): {e}")
            return None, "duplicate"
        logger.warning(f"provider_earnings insert FAILED for work {work_id} (not a duplicate): {e}")
        return None, "db_error"


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


def mark_earnings_settling(provider_id: int, settlement_id: int) -> list[dict]:
    """Atomically flip a provider's 'accrued' earnings to 'settling',
    tagged with settlement_id, and return exactly the rows that were
    flipped (id + amount_wei). PR #2288 review I4 fix: this single
    UPDATE ... WHERE status='accrued' is the race-safety mechanism --
    Postgres applies it as one statement, so a concurrent
    void_earning_for_work (which also only ever matches status='accrued')
    can no longer touch a row after this has claimed it, and this can
    never claim a row a concurrent void got to first. The settlement job
    must sum ONLY the returned rows (not a separate list_accrued_earnings
    read) as the authoritative amount to transfer.

    Returns [] on any error -- callers must not treat that as "provider
    has zero accrued earnings," only as "couldn't claim any this attempt."
    """
    try:
        client = get_supabase_client()
        result = (
            client.table(_EARNINGS_TABLE)
            .update({"status": "settling", "settlement_id": settlement_id})
            .eq("provider_id", provider_id)
            .eq("status", "accrued")
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"provider_earnings settling-flip failed for provider {provider_id}: {e}")
        return []


def mark_earnings_settled(earning_ids: list[int], settlement_id: int) -> bool:
    """Flip a specific set of earning rows (by id) from 'settling' to
    'settled' -- the final confirmation after a successful transfer. Only
    'settling' rows tagged with this settlement_id match."""
    if not earning_ids:
        return True
    try:
        client = get_supabase_client()
        client.table(_EARNINGS_TABLE).update({"status": "settled"}).in_("id", earning_ids).eq(
            "status", "settling"
        ).eq("settlement_id", settlement_id).execute()
        return True
    except Exception as e:
        logger.warning(f"provider_earnings settle failed for settlement {settlement_id}: {e}")
        return False


def mark_earnings_accrued(earning_ids: list[int], settlement_id: int) -> bool:
    """Revert a specific set of earning rows (by id) from 'settling' back
    to 'accrued' and clear settlement_id -- used when a settlement fails
    (transfer error, threshold recheck after the atomic flip, or stuck-
    settlement reconciliation) so the earnings are picked up by a future
    settlement run instead of being stuck 'settling' forever."""
    if not earning_ids:
        return True
    try:
        client = get_supabase_client()
        client.table(_EARNINGS_TABLE).update({"status": "accrued", "settlement_id": None}).in_(
            "id", earning_ids
        ).eq("status", "settling").eq("settlement_id", settlement_id).execute()
        return True
    except Exception as e:
        logger.warning(
            f"provider_earnings revert-to-accrued failed for settlement {settlement_id}: {e}"
        )
        return False


def list_settling_earnings_for_settlement(settlement_id: int) -> list[dict]:
    """The earnings currently tagged 'settling' for one settlement --
    used by stuck-settlement reconciliation to know what to confirm/revert."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_EARNINGS_TABLE)
            .select("*")
            .eq("settlement_id", settlement_id)
            .eq("status", "settling")
            .limit(_WORK_QUERY_ROW_CAP)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(
            f"provider_earnings settling-list failed for settlement {settlement_id}: {e}"
        )
        return []


def list_verified_work_since(since_iso: str) -> list[dict]:
    """provider_work rows with verification='verified' created at or after
    since_iso -- the earnings-reconciliation pass's input (PR #2288 review
    I1): record_earning_for_verified_work is idempotent (create_earning's
    UNIQUE(work_id) constraint), so simply re-attempting it for every
    recently-verified row is a safe, cheap way to recover any that failed
    for a non-duplicate DB reason on their first attempt, without needing
    a NOT EXISTS / anti-join query."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_WORK_TABLE)
            .select("*")
            .eq("verification", "verified")
            .gte("created_at", since_iso)
            .limit(_WORK_QUERY_ROW_CAP)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"provider_work verified-since lookup failed: {e}")
        return []


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


def list_stuck_pending_settlements(older_than_iso: str) -> list[dict]:
    """'pending' provider_settlements rows older than older_than_iso --
    PR #2288 review I3: the reconciliation input for settlements stuck
    since before a crash. See src/services/gpu/settlement.py's
    reconcile_stuck_settlements and docs/gpu/VERIFICATION_AND_PAYOUTS.md's
    runbook."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_SETTLEMENTS_TABLE)
            .select("*")
            .eq("status", "pending")
            .lt("created_at", older_than_iso)
            .limit(_WORK_QUERY_ROW_CAP)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"provider_settlements stuck-pending lookup failed: {e}")
        return []


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


def update_settlement_amount(settlement_id: int, amount_wei: int) -> bool:
    """Persist the authoritative amount_wei once it's known from the
    atomic mark_earnings_settling() flip -- the row is created with a
    provisional 0 before that flip happens (a settlement_id is needed to
    tag the flipped earnings, so the row must exist first)."""
    try:
        client = get_supabase_client()
        client.table(_SETTLEMENTS_TABLE).update({"amount_wei": str(amount_wei)}).eq(
            "id", settlement_id
        ).execute()
        return True
    except Exception as e:
        logger.warning(f"provider_settlements amount update failed for {settlement_id}: {e}")
        return False


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
