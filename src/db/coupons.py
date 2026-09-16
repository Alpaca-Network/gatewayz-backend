"""Coupon persistence for the admin coupons API (src/routes/admin_coupons.py).

Two tables, both verified against prod ``ynleroehyrmaafkgjgmr`` on 2026-09-16:

``public.coupons``            -- the 16 columns in COUPON_COLUMNS, 1 live row.
``public.coupon_redemptions`` -- id, coupon_id, user_id, redeemed_at,
                                 value_applied, user_balance_before,
                                 user_balance_after, ip_address, user_agent.
                                 Currently empty.

Two conventions this module is built around, both from
"Gatewayz - Phantom Column Failures, Admin Dashboard Batch 1 (Sep 15, 2026)":

1. **Never swallow a query failure into an empty result.** Every read raises
   on error. An empty coupon list and a broken query must not render as the
   same calm, healthy-looking page -- that is precisely how a broken staff
   roster hid for weeks.

2. **PostgREST caps responses at 1000 rows** (``db-max-rows``), so a select
   that looks like "aggregate the whole table" silently aggregates an
   arbitrary 1000-row slice. Aggregate functions are disabled on this
   instance (``PGRST123``), so redemption totals are computed by explicitly
   paging with ``.range()`` and refusing -- loudly -- past a hard cap rather
   than returning a number that is quietly short.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

# Every column of public.coupons, in migration order. Selected explicitly and
# never as "*": an explicit list is what makes a dropped column fail in review
# instead of at runtime, and it keeps the response shape stable if a column is
# added later.
COUPON_COLUMNS = (
    "id, code, description, coupon_type, coupon_scope, value_usd, "
    "assigned_to_user_id, max_uses, times_used, valid_from, valid_until, "
    "is_active, created_by, created_by_type, created_at, updated_at"
)

# PostgREST's own ceiling. Paging uses exactly this so each request returns a
# full page and the loop terminates on a short page.
_PAGE_SIZE = 1000

# Refuse rather than under-report. 50k redemptions across a single coupon (or
# the whole table, for the overview) is far beyond anything the current data
# suggests; if it is ever hit, the fix is a Postgres-side aggregate RPC, not a
# bigger cap here.
MAX_REDEMPTION_SCAN = 50_000


class RedemptionScanTooLarge(RuntimeError):
    """Redemption rows exceeded MAX_REDEMPTION_SCAN.

    Raised instead of returning a truncated sum, so the caller answers 503
    rather than showing a dollar figure that is quietly wrong.
    """


def _sanitize_search(raw: str) -> str:
    """Make a search term safe to embed in a PostgREST ``or=`` filter.

    ``or_()`` takes a comma-separated list of filters, and ``*`` is the
    wildcard, so an unescaped comma or parenthesis in user input is filter
    injection, not just a bad match. Everything outside a conservative
    allowlist is dropped.
    """
    cleaned = "".join(c for c in (raw or "") if c.isalnum() or c in " _-")
    return cleaned.strip()[:100]


def _row_or_none(result: Any) -> dict[str, Any] | None:
    data = getattr(result, "data", None)
    return data[0] if data else None


def list_coupons(
    *,
    scope: str | None = None,
    coupon_type: str | None = None,
    is_active: bool | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Page of coupons plus the exact total matching the same filters.

    The total comes from PostgREST's ``count="exact"``, not from
    ``len(rows)`` -- the rows are a page, the count is the table.

    Returns:
        (rows, total_matching_filters)

    Raises:
        Exception: any database failure, deliberately unhandled.
    """
    try:
        client = get_supabase_client()
        query = client.table("coupons").select(COUPON_COLUMNS, count="exact")

        if scope is not None:
            query = query.eq("coupon_scope", scope)
        if coupon_type is not None:
            query = query.eq("coupon_type", coupon_type)
        if is_active is not None:
            query = query.eq("is_active", is_active)

        term = _sanitize_search(search) if search else ""
        if term:
            query = query.or_(f"code.ilike.*{term}*,description.ilike.*{term}*")

        result = (
            query.order("created_at", desc=True).range(offset, offset + max(limit, 1) - 1).execute()
        )
    except Exception:
        logger.error("list_coupons query failed", exc_info=True)
        raise

    rows = result.data or []
    total = result.count if result.count is not None else len(rows)
    return rows, total


def get_coupon(coupon_id: int) -> dict[str, Any] | None:
    """One coupon by id, or None if it genuinely does not exist.

    None means "no such row". A database failure raises -- the two must stay
    distinguishable, otherwise an outage renders as a 404.
    """
    try:
        client = get_supabase_client()
        result = (
            client.table("coupons").select(COUPON_COLUMNS).eq("id", coupon_id).limit(1).execute()
        )
    except Exception:
        logger.error("get_coupon(%s) query failed", coupon_id, exc_info=True)
        raise
    return _row_or_none(result)


def get_coupon_by_code(code: str, *, exclude_id: int | None = None) -> dict[str, Any] | None:
    """Find a coupon by code, case-insensitively.

    Used for the uniqueness pre-check. The table's UNIQUE(code) is
    case-sensitive, but ``is_coupon_redeemable()`` matches on UPPER(code), so
    'welcome' and 'WELCOME' would be two rows that redeem as one coupon --
    a second, unintended grant of the same money.
    """
    try:
        client = get_supabase_client()
        query = client.table("coupons").select(COUPON_COLUMNS).ilike("code", code)
        if exclude_id is not None:
            query = query.neq("id", exclude_id)
        result = query.limit(1).execute()
    except Exception:
        logger.error("get_coupon_by_code query failed", exc_info=True)
        raise
    return _row_or_none(result)


def create_coupon(values: dict[str, Any]) -> dict[str, Any]:
    """Insert one coupon and return the stored row.

    ``times_used`` is never part of ``values``: it is redemption state owned
    by the redemption path, and the column defaults to 0.
    """
    try:
        client = get_supabase_client()
        result = client.table("coupons").insert(values).execute()
    except Exception:
        logger.error("create_coupon insert failed", exc_info=True)
        raise

    row = _row_or_none(result)
    if row is None:
        raise RuntimeError("Coupon insert returned no row")
    return row


def update_coupon(coupon_id: int, patch: dict[str, Any]) -> dict[str, Any] | None:
    """Apply a patch to one coupon. Returns the updated row, or None if the
    coupon does not exist."""
    if not patch:
        return get_coupon(coupon_id)

    payload = dict(patch)
    payload["updated_at"] = datetime.now(UTC).isoformat()

    try:
        client = get_supabase_client()
        result = client.table("coupons").update(payload).eq("id", coupon_id).execute()
    except Exception:
        logger.error("update_coupon(%s) failed", coupon_id, exc_info=True)
        raise
    return _row_or_none(result)


def delete_coupon(coupon_id: int) -> bool:
    """Hard-delete one coupon row.

    ``coupon_redemptions.coupon_id`` is ON DELETE CASCADE, so this destroys
    the redemption history too. The route only reaches here for a coupon with
    no redemptions; everything else deactivates instead.
    """
    try:
        client = get_supabase_client()
        result = client.table("coupons").delete().eq("id", coupon_id).execute()
    except Exception:
        logger.error("delete_coupon(%s) failed", coupon_id, exc_info=True)
        raise
    return bool(getattr(result, "data", None))


def count_redemptions(coupon_id: int | None = None) -> int:
    """Exact redemption count, for one coupon or the whole table.

    Uses ``count="exact", head=True`` so no rows cross the wire and
    ``db-max-rows`` cannot truncate the answer.
    """
    try:
        client = get_supabase_client()
        query = client.table("coupon_redemptions").select("id", count="exact", head=True)
        if coupon_id is not None:
            query = query.eq("coupon_id", coupon_id)
        result = query.execute()
    except Exception:
        logger.error("count_redemptions(%s) failed", coupon_id, exc_info=True)
        raise
    return result.count or 0


def _scan_redemptions(coupon_id: int | None) -> list[dict[str, Any]]:
    """Every matching redemption's (user_id, value_applied), paged.

    PostgREST returns at most 1000 rows per request regardless of the
    requested range, so a single unbounded select would quietly aggregate a
    slice. This walks explicit pages until one comes back short.

    Raises:
        RedemptionScanTooLarge: past MAX_REDEMPTION_SCAN rows.
    """
    client = get_supabase_client()
    rows: list[dict[str, Any]] = []
    offset = 0

    while True:
        query = client.table("coupon_redemptions").select("user_id, value_applied")
        if coupon_id is not None:
            query = query.eq("coupon_id", coupon_id)
        result = query.order("id").range(offset, offset + _PAGE_SIZE - 1).execute()
        page = result.data or []
        rows.extend(page)

        if len(page) < _PAGE_SIZE:
            return rows

        offset += _PAGE_SIZE
        if offset >= MAX_REDEMPTION_SCAN:
            raise RedemptionScanTooLarge(
                f"coupon_redemptions scan exceeded {MAX_REDEMPTION_SCAN} rows "
                f"(coupon_id={coupon_id}); aggregate this in Postgres instead."
            )


def get_redemption_stats(coupon_id: int | None = None) -> dict[str, Any]:
    """Redemption totals for one coupon, or globally when coupon_id is None.

    Returns:
        {"total_redemptions": int, "unique_users": int,
         "total_value_distributed": float}

    Raises:
        RedemptionScanTooLarge: the scan would have been truncated.
        Exception: any database failure.
    """
    try:
        rows = _scan_redemptions(coupon_id)
    except RedemptionScanTooLarge:
        raise
    except Exception:
        logger.error("get_redemption_stats(%s) failed", coupon_id, exc_info=True)
        raise

    unique_users = {r.get("user_id") for r in rows if r.get("user_id") is not None}
    total_value = sum(float(r.get("value_applied") or 0) for r in rows)

    return {
        "total_redemptions": len(rows),
        "unique_users": len(unique_users),
        "total_value_distributed": round(total_value, 2),
    }


def get_coupon_counts() -> dict[str, int]:
    """Exact coupon counts by status and scope, via four head-only counts.

    Returns:
        {"total_coupons", "active_coupons", "global_coupons",
         "user_specific_coupons"}

    Raises:
        Exception: any database failure.
    """
    try:
        client = get_supabase_client()

        def _count(**filters: Any) -> int:
            query = client.table("coupons").select("id", count="exact", head=True)
            for column, value in filters.items():
                query = query.eq(column, value)
            return query.execute().count or 0

        return {
            "total_coupons": _count(),
            "active_coupons": _count(is_active=True),
            "global_coupons": _count(coupon_scope="global"),
            "user_specific_coupons": _count(coupon_scope="user_specific"),
        }
    except Exception:
        logger.error("get_coupon_counts failed", exc_info=True)
        raise
