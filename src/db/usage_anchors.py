"""DB access for daily usage anchoring (docs/usage-anchor/README.md).

Two concerns:
- `usage_anchors`: one row per (chain, contract, UTC day) tracking the
  anchored Merkle root and its transaction.
- the leaf source: `credit_transactions` rows with transaction_type
  'api_usage' -- the authoritative per-request debit written atomically by
  atomic_deduct_credits (src/db/users.py). Read-only here.

Unlike the read-mostly WAYZ indexer modules, these functions RAISE on DB
errors: the anchoring job must never mistake "lookup failed" for "no rows"
(that would anchor a wrong root) or proceed after failing to record a tx
hash. Callers catch and record the failure.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

_ANCHORS_TABLE = "usage_anchors"
_LEDGER_TABLE = "credit_transactions"
_USAGE_TYPE = "api_usage"
# PostgREST caps a response at 1000 rows; page explicitly.
_PAGE_SIZE = 1000
_LEDGER_COLUMNS = "id,user_id,created_at,amount,metadata"


def _day_bounds(day: date) -> tuple[str, str]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


def fetch_usage_rows_for_day(day: date) -> list[dict[str, Any]]:
    """Every api_usage ledger row created on `day` (UTC). Raises on error.
    Callers sort; the DB order here is only for stable paging."""
    start, end = _day_bounds(day)
    client = get_supabase_client()
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        result = (
            client.table(_LEDGER_TABLE)
            .select(_LEDGER_COLUMNS)
            .eq("transaction_type", _USAGE_TYPE)
            .gte("created_at", start)
            .lt("created_at", end)
            .order("created_at")
            .order("id")
            .range(offset, offset + _PAGE_SIZE - 1)
            .execute()
        )
        page = result.data or []
        rows.extend(page)
        if len(page) < _PAGE_SIZE:
            return rows
        offset += _PAGE_SIZE


def get_usage_row(ledger_id: int) -> dict[str, Any] | None:
    """One api_usage ledger row by id, or None. Raises on error."""
    client = get_supabase_client()
    result = (
        client.table(_LEDGER_TABLE)
        .select(_LEDGER_COLUMNS)
        .eq("id", ledger_id)
        .eq("transaction_type", _USAGE_TYPE)
        .limit(1)
        .execute()
    )
    data = result.data or []
    return data[0] if data else None


def get_anchor(chain_id: int, contract_address: str, day: date) -> dict[str, Any] | None:
    client = get_supabase_client()
    result = (
        client.table(_ANCHORS_TABLE)
        .select("*")
        .eq("chain_id", chain_id)
        .eq("contract_address", contract_address.lower())
        .eq("day", day.isoformat())
        .limit(1)
        .execute()
    )
    data = result.data or []
    return data[0] if data else None


def upsert_anchor(chain_id: int, contract_address: str, day: date, **fields: Any) -> None:
    """Create or update the anchor row for `day`. Raises on error."""
    row = {
        "chain_id": chain_id,
        "contract_address": contract_address.lower(),
        "day": day.isoformat(),
        "day_index": (day - date(1970, 1, 1)).days,
        "updated_at": datetime.now(UTC).isoformat(),
        **fields,
    }
    client = get_supabase_client()
    client.table(_ANCHORS_TABLE).upsert(row, on_conflict="chain_id,contract_address,day").execute()


def list_anchors(
    chain_id: int, contract_address: str, limit: int = 30, statuses: list[str] | None = None
) -> list[dict[str, Any]]:
    client = get_supabase_client()
    query = (
        client.table(_ANCHORS_TABLE)
        .select("*")
        .eq("chain_id", chain_id)
        .eq("contract_address", contract_address.lower())
    )
    if statuses:
        query = query.in_("status", statuses)
    result = query.order("day", desc=True).limit(limit).execute()
    return result.data or []
