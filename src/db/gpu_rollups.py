"""DB access for gpu_utilization_hourly and the read paths behind the public
GPU transparency endpoints (gatewayz-backend#2263 #2264, spec §6).

Mirrors src/db/wallet_stakes.py's try/except + logger.warning + safe-default
convention exactly -- every lookup here backs a public, no-auth endpoint or a
background rollup job, neither of which may ever hard-fail the caller.

gpu_utilization_hourly, gpu_nodes, and gpu_providers are owned by W-A1's
migration (20260903200000_gpu_marketplace.sql, merged to main as 48ace7ef).
This module only reads/writes rows shaped per spec §2; every function below
still degrades to its safe default (empty list / zeroed summary) via the
same try/except path a missing/unreachable table would hit, so nothing
here needs to special-case "table doesn't exist" as a distinct case from
"DB unreachable".

get_summary()'s active-node count reuses src.db.gpu.list_active_nodes()
(W-A1) rather than re-implementing the active-status + approved-provider
join here -- see that function's own docstring. get_public_nodes() does
NOT reuse it: the public node listing intentionally shows nodes of every
status (registered/active/degraded/offline/disabled) for transparency, not
just 'active' ones (that's the whole reason `status` is a field in the
response), and list_active_nodes() hardcodes status='active' server-side.
No equivalent "all statuses, approved providers only" helper exists in
src/db/gpu.py, so _fetch_approved_nodes() below stays a local
implementation for that one call site.

active_nodes semantics (a deliberate deviation from a literal reading of
spec §6, recorded here per the standing instructions): the spec text says
"nodes with a heartbeat in that hour". gpu_nodes only stores the single most
recent heartbeat timestamp, not a history, so that literal definition cannot
be computed for the 7-day backfill (only the just-completed hour would ever
be non-zero; all backfilled hours would read active_nodes=0, which is worse
than not showing them at all on a transparency dashboard). Instead,
active_nodes here means "nodes that completed at least one provider_work
row in that hour" -- a proxy with real historical fidelity for both the
live and backfilled paths. See docs/gpu/PUBLIC_FEED.md.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.db.client import get_db
from src.db.gpu import list_active_nodes

logger = logging.getLogger(__name__)

_UTIL_TABLE = "gpu_utilization_hourly"
_WORK_TABLE = "provider_work"
_NODES_TABLE = "gpu_nodes"
_PROVIDERS_TABLE = "gpu_providers"

# Caps on rows scanned per query -- fine for testnet scale, but a real limit
# rather than an unbounded select (same rationale as wallet_stakes.py's
# _STAKE_TOTALS_ROW_CAP).
_WORK_ROW_CAP = 50_000
_UTIL_ROW_CAP = 10_000

_WINDOW_HOURS = {"24h": 24, "7d": 24 * 7}
_UPTIME_LOOKBACK_HOURS = 24


# ---------------------------------------------------------------------------
# Pure aggregation math (no DB) -- the part covered by fixture-row tests.
# ---------------------------------------------------------------------------


def compute_hourly_aggregates(hour: datetime, work_rows: list[dict]) -> list[dict]:
    """Group provider_work-shaped rows (each already carrying a resolved
    'region' key -- see aggregate_hour) into gpu_utilization_hourly rows for
    the given hour bucket. Pure function: no I/O, safe to unit test with
    fixture data.
    """
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for row in work_rows:
        key = (row.get("region") or "unknown", row["model"])
        bucket = buckets.setdefault(
            key,
            {
                "requests": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "latency_sum": 0,
                "latency_count": 0,
                "failed": 0,
                "nodes": set(),
            },
        )
        bucket["requests"] += 1
        bucket["prompt_tokens"] += row.get("prompt_tokens") or 0
        bucket["completion_tokens"] += row.get("completion_tokens") or 0
        if row.get("latency_ms") is not None:
            bucket["latency_sum"] += row["latency_ms"]
            bucket["latency_count"] += 1
        if row.get("status") == "failed":
            bucket["failed"] += 1
        if row.get("node_id") is not None:
            bucket["nodes"].add(row["node_id"])

    rows: list[dict] = []
    for (region, model), bucket in buckets.items():
        avg_latency = (
            int(round(bucket["latency_sum"] / bucket["latency_count"]))
            if bucket["latency_count"]
            else 0
        )
        error_rate = round(bucket["failed"] / bucket["requests"], 4) if bucket["requests"] else 0.0
        rows.append(
            {
                "hour": hour.isoformat(),
                "region": region,
                "model": model,
                "requests": bucket["requests"],
                "prompt_tokens": bucket["prompt_tokens"],
                "completion_tokens": bucket["completion_tokens"],
                "avg_latency_ms": avg_latency,
                "error_rate": error_rate,
                "active_nodes": len(bucket["nodes"]),
            }
        )
    return rows


def _regroup(rows: list[dict], group: str) -> list[dict]:
    """Re-aggregate gpu_utilization_hourly rows (already grouped by
    (hour, region, model)) into (hour, key) buckets where key is the
    requested region or model. active_nodes is taken as the max across the
    collapsed sub-groups, not summed -- a node serving two models in the
    same hour must not be double-counted when grouping by region.
    """
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["hour"], row[group])
        bucket = buckets.setdefault(
            key,
            {
                "requests": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "latency_weighted": 0.0,
                "failed_weighted": 0.0,
                "active_nodes": 0,
            },
        )
        requests = row.get("requests") or 0
        bucket["requests"] += requests
        bucket["prompt_tokens"] += row.get("prompt_tokens") or 0
        bucket["completion_tokens"] += row.get("completion_tokens") or 0
        bucket["latency_weighted"] += (row.get("avg_latency_ms") or 0) * requests
        bucket["failed_weighted"] += (row.get("error_rate") or 0) * requests
        bucket["active_nodes"] = max(bucket["active_nodes"], row.get("active_nodes") or 0)

    out: list[dict] = []
    for (hour, key_value), bucket in sorted(buckets.items()):
        requests = bucket["requests"]
        avg_latency = int(round(bucket["latency_weighted"] / requests)) if requests else 0
        error_rate = round(bucket["failed_weighted"] / requests, 4) if requests else 0.0
        out.append(
            {
                "hour": hour,
                "key": key_value,
                "requests": requests,
                "prompt_tokens": bucket["prompt_tokens"],
                "completion_tokens": bucket["completion_tokens"],
                "avg_latency_ms": avg_latency,
                "error_rate": error_rate,
                "active_nodes": bucket["active_nodes"],
            }
        )
    return out


def _estimate_uptime_pct(region: str | None, model_ids: list[str], rows: list[dict]) -> float:
    """Approximation documented in spec §6 / docs/gpu/PUBLIC_FEED.md: no
    per-node heartbeat history is persisted, only the hourly (region, model)
    rollup. A node's uptime is estimated as the share of the last 24 hourly
    buckets in which ANY node serving the same (region, model) pair recorded
    active_nodes >= 1 -- a group-level proxy, not a per-node measurement.
    Nodes serving multiple models take the most generous (max) match.
    """
    if not model_ids:
        return 0.0
    up_hours = {
        row["hour"]
        for row in rows
        if row.get("region") == region
        and row.get("model") in model_ids
        and (row.get("active_nodes") or 0) >= 1
    }
    return round(min(1.0, len(up_hours) / _UPTIME_LOOKBACK_HOURS) * 100, 2)


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


def upsert_hourly_rows(rows: list[dict]) -> bool:
    """Idempotent upsert on the (hour, region, model) primary key."""
    if not rows:
        return True
    try:
        client = get_db()
        client.table(_UTIL_TABLE).upsert(rows, on_conflict="hour,region,model").execute()
        return True
    except Exception as e:
        logger.warning(f"gpu_utilization_hourly upsert failed for {len(rows)} rows: {e}")
        return False


def is_utilization_empty() -> bool:
    """Whether gpu_utilization_hourly has zero rows -- used to decide
    whether to run the one-time 7-day backfill. Defaults to False (not
    empty) on error so a transient DB hiccup never triggers a spurious
    168-hour backfill.
    """
    try:
        client = get_db()
        result = client.table(_UTIL_TABLE).select("hour", count="exact").limit(1).execute()
        return (result.count or 0) == 0
    except Exception as e:
        logger.warning(f"gpu_utilization_hourly emptiness check failed: {e}")
        return False


def aggregate_hour(hour: datetime) -> list[dict]:
    """Compute (but do not write) gpu_utilization_hourly rows for one UTC
    hour bucket, from provider_work joined to gpu_nodes for region. Only
    the columns the aggregation needs are selected -- no billing_ref,
    prompt_hash, response_hash, or provider_id (defense in depth for the
    aggregate-only guarantee, on top of compute_hourly_aggregates never
    emitting them).
    """
    hour = hour.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    start = hour.isoformat()
    end = (hour + timedelta(hours=1)).isoformat()
    try:
        client = get_db()
        work_result = (
            client.table(_WORK_TABLE)
            .select("model,status,prompt_tokens,completion_tokens,latency_ms,node_id")
            .gte("created_at", start)
            .lt("created_at", end)
            .limit(_WORK_ROW_CAP)
            .execute()
        )
        work_rows = work_result.data or []
        if len(work_rows) >= _WORK_ROW_CAP:
            logger.warning(
                f"aggregate_hour hit the {_WORK_ROW_CAP}-row cap for hour={start}; "
                "aggregates for this hour may be incomplete"
            )

        node_ids = sorted({r["node_id"] for r in work_rows if r.get("node_id") is not None})
        region_by_node: dict[str, str] = {}
        if node_ids:
            nodes_result = (
                client.table(_NODES_TABLE).select("id,region").in_("id", node_ids).execute()
            )
            region_by_node = {
                n["id"]: n.get("region") or "unknown" for n in (nodes_result.data or [])
            }

        enriched = [
            {**row, "region": region_by_node.get(row.get("node_id"), "unknown")}
            for row in work_rows
        ]
        return compute_hourly_aggregates(hour, enriched)
    except Exception as e:
        logger.warning(f"gpu_utilization_hourly aggregation failed for hour={start}: {e}")
        return []


# ---------------------------------------------------------------------------
# Public read paths
# ---------------------------------------------------------------------------


def get_utilization(window: str, group: str) -> list[dict]:
    """Hourly series from gpu_utilization_hourly, re-grouped by region or
    model. Returns [] on any lookup error or if the window has no data yet.
    """
    hours = _WINDOW_HOURS.get(window)
    if hours is None:
        raise ValueError(
            f"unsupported window: {window!r} (expected one of {sorted(_WINDOW_HOURS)})"
        )
    if group not in ("region", "model"):
        raise ValueError(f"unsupported group: {group!r} (expected 'region' or 'model')")

    since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    try:
        client = get_db()
        result = (
            client.table(_UTIL_TABLE)
            .select("*")
            .gte("hour", since)
            .order("hour")
            .limit(_UTIL_ROW_CAP)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.warning(f"gpu_utilization_hourly lookup failed: {e}")
        return []

    return _regroup(rows, group)


def _fetch_approved_nodes() -> list[dict]:
    """gpu_nodes rows for nodes whose provider is approved, joined via
    gpu_providers!inner(status) so the filter runs server-side. Selects
    only public-safe columns plus the join key -- no wallet, endpoint, node
    token, or provider identity ever leaves this function.

    Deliberately NOT src.db.gpu.list_active_nodes(): that helper hardcodes
    status='active' server-side, but get_public_nodes() (the only caller of
    this function -- get_summary() uses list_active_nodes() directly)
    intentionally lists nodes of every status for public transparency.
    """
    try:
        client = get_db()
        result = (
            client.table(_NODES_TABLE)
            .select("name,region,gpu_model,vram_gb,status,models,gpu_providers!inner(status)")
            .eq("gpu_providers.status", "approved")
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"gpu_nodes public lookup failed: {e}")
        return []


def _recent_hourly_rows(hours: int) -> list[dict]:
    since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    try:
        client = get_db()
        result = (
            client.table(_UTIL_TABLE)
            .select("hour,region,model,active_nodes")
            .gte("hour", since)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"gpu_utilization_hourly recent-rows lookup failed: {e}")
        return []


def _model_ids(node: dict) -> list[str]:
    return [m.get("id") for m in (node.get("models") or []) if isinstance(m, dict) and m.get("id")]


def _format_public_node(node: dict, recent_rows: list[dict]) -> dict:
    model_ids = _model_ids(node)
    return {
        "name": node.get("name"),
        "region": node.get("region"),
        "gpu_model": node.get("gpu_model"),
        "vram_gb": node.get("vram_gb") or 0,
        "status": node.get("status"),
        "uptime_24h_pct": _estimate_uptime_pct(node.get("region"), model_ids, recent_rows),
        "models": model_ids,
    }


def get_public_nodes() -> list[dict]:
    """Public node listing: name, region, gpu_model, vram_gb, status,
    uptime_24h_pct, models -- deliberately excludes wallet, endpoint, node
    token, and provider identity (see
    tests/security/test_gpu_public_aggregate_only.py).
    """
    nodes = _fetch_approved_nodes()
    if not nodes:
        return []
    recent_rows = _recent_hourly_rows(_UPTIME_LOOKBACK_HOURS)
    return [_format_public_node(n, recent_rows) for n in nodes]


def _last_hour_summary() -> dict:
    hour_start = (datetime.now(UTC) - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    try:
        client = get_db()
        result = (
            client.table(_UTIL_TABLE)
            .select("requests,prompt_tokens,completion_tokens,avg_latency_ms,error_rate")
            .eq("hour", hour_start.isoformat())
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.warning(f"gpu_utilization_hourly last-hour lookup failed: {e}")
        rows = []

    requests = sum(r.get("requests") or 0 for r in rows)
    tokens = sum((r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0) for r in rows)
    if requests:
        avg_latency = int(
            round(
                sum((r.get("avg_latency_ms") or 0) * (r.get("requests") or 0) for r in rows)
                / requests
            )
        )
        error_rate = round(
            sum((r.get("error_rate") or 0) * (r.get("requests") or 0) for r in rows) / requests, 4
        )
    else:
        avg_latency = 0
        error_rate = 0.0

    return {
        "requests": requests,
        "tokens": tokens,
        "avg_latency_ms": avg_latency,
        "error_rate": error_rate,
    }


def get_summary() -> dict:
    """Protocol-wide public summary. Every sub-lookup degrades to a safe
    zeroed default independently, so a failure in one (e.g. gpu_providers
    unreachable) doesn't blank out the rest.
    """
    # list_active_nodes() (src/db/gpu.py, W-A1) already does exactly the
    # active-status + approved-provider join this needs -- reused here
    # instead of re-implementing it via _fetch_approved_nodes() + a
    # status=='active' filter.
    active_nodes = list_active_nodes()

    try:
        client = get_db()
        providers_result = (
            client.table(_PROVIDERS_TABLE)
            .select("id", count="exact")
            .eq("status", "approved")
            .execute()
        )
        approved_providers = providers_result.count or 0
    except Exception as e:
        logger.warning(f"gpu_providers summary lookup failed: {e}")
        approved_providers = 0

    regions: dict[str, int] = {}
    models: dict[str, int] = {}
    for node in active_nodes:
        region = node.get("region") or "unknown"
        regions[region] = regions.get(region, 0) + 1
        for model_id in _model_ids(node):
            models[model_id] = models.get(model_id, 0) + 1

    return {
        "active_nodes": len(active_nodes),
        "approved_providers": approved_providers,
        "regions": [{"region": r, "nodes": c} for r, c in sorted(regions.items())],
        "models": [{"id": m, "nodes": c} for m, c in sorted(models.items())],
        "last_hour": _last_hour_summary(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
