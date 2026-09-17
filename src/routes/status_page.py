"""
Public Status Page API Endpoints

Provides public-facing endpoints for status page display without authentication.
Optimized for performance with caching and pre-aggregated data.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.db.client import get_db
from src.db.model_health import is_unmeasured_status

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/status", tags=["status-page"])


# A health measurement older than this is history, not current state. The
# slowest monitoring tier (on_demand) re-probes every 4h, and a model in maximum
# backoff every 6h (Config.HEALTH_PROBE_BACKOFF_MAX_SECONDS, deliberately sized
# against this window), so 24h is several missed probe cycles — past it the
# prober is not watching.
MEASUREMENT_MAX_AGE = timedelta(hours=24)

_TRACKING_PAGE_SIZE = 1000
_TRACKING_MAX_PAGES = 50


def _parse_ts(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def _num(value: Any, default: float = 0.0) -> float:
    """
    A numeric column from ``model_status_current``, as a float.

    PostgREST serialises Postgres ``numeric`` as a JSON *string* ("0.0"), so
    ``round(row[col] or 0, 2)`` raised TypeError on every row that had a
    measurement — the whole endpoint 500'd while the data was fine.
    """
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# PostgREST answers a missing table with its OWN code and wording, not Postgres's:
#   {'code': 'PGRST205', 'message': "Could not find the table 'public.x' in the
#    schema cache"}
# The first cut of this check looked for 42P01 / "does not exist" — both plausible,
# neither what the client raises — so /status/uptime kept 500ing after the fix that
# was supposed to stop it. The strings below are copied from a real APIError.
_MISSING_TABLE_CODES = {"PGRST205", "PGRST200", "42P01"}
_MISSING_TABLE_PHRASES = ("could not find the table", "does not exist")


def _is_missing_table(error: Exception) -> bool:
    """True when the client reports the relation is absent, not merely unreachable."""
    if str(getattr(error, "code", "")) in _MISSING_TABLE_CODES:
        return True
    text = f"{getattr(error, 'message', '')} {error}".lower()
    if any(code.lower() in text for code in _MISSING_TABLE_CODES):
        return True
    return any(phrase in text for phrase in _MISSING_TABLE_PHRASES)


def _is_stale(row: dict[str, Any], now: datetime | None = None) -> bool:
    """
    True when this row's newest check is older than ``MEASUREMENT_MAX_AGE``.

    A row keeps its last verdict forever: since #2202 the prober skips models
    that left the served catalog, and nothing prunes tracking rows. Without this
    window ``/v1/status/models`` reported 40 of 100 models as ``operational`` at
    ``uptime_24h: 100.0`` whose newest check was **50 days old** — the same
    falsehood #2325 removed from the aggregate, still being served by the routes
    underneath it.
    """
    checked_at = _parse_ts(row.get("last_called_at"))
    if checked_at is None:
        return True
    return checked_at < (now or datetime.now(UTC)) - MEASUREMENT_MAX_AGE


def _format_model_status(model: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """
    One ``model_status_current`` row, shaped for the status page.

    Shared by the list and single-model routes so the two cannot drift: both
    previously read ``active_incidents_count``, a column the view does not
    have (it is ``active_incidents``), which is a KeyError the route turned
    into a blanket 500. Reads are ``.get()`` so a future view change degrades
    one field instead of the endpoint.

    A stale row is reported as ``status: "unknown"`` with ``monitored: false``
    and ``null`` uptimes rather than as a healthy model: nobody has measured it,
    which is not the same as it being up. The measured figures stay available
    under ``last_measured_*`` so a caller can still show the last known reading,
    dated.
    """
    if _is_stale(model, now):
        return {
            "model_id": model.get("model"),
            "provider": model.get("provider"),
            "gateway": model.get("gateway"),
            "status": "unknown",
            "monitored": False,
            "unmonitored_reason": (
                f"No health check in the last {int(MEASUREMENT_MAX_AGE.total_seconds() // 3600)}h."
            ),
            "tier": model.get("monitoring_tier"),
            "uptime_24h": None,
            "uptime_7d": None,
            "uptime_30d": None,
            "avg_response_time_ms": None,
            "last_checked": model.get("last_called_at"),
            "last_success": model.get("last_success_at"),
            "last_failure": model.get("last_failure_at"),
            "circuit_breaker_state": model.get("circuit_breaker_state"),
            "active_incidents": model.get("active_incidents"),
            "last_measured_uptime_24h": round(_num(model.get("uptime_percentage_24h")), 2),
            "last_measured_status": model.get("status_indicator"),
        }

    return {
        "monitored": True,
        "model_id": model.get("model"),
        "provider": model.get("provider"),
        "gateway": model.get("gateway"),
        "status": model.get("status_indicator"),
        "tier": model.get("monitoring_tier"),
        "uptime_24h": round(_num(model.get("uptime_percentage_24h")), 2),
        "uptime_7d": round(_num(model.get("uptime_percentage_7d")), 2),
        "uptime_30d": round(_num(model.get("uptime_percentage_30d")), 2),
        "avg_response_time_ms": round(_num(model.get("average_response_time_ms"))),
        "last_checked": model.get("last_called_at"),
        "last_success": model.get("last_success_at"),
        "last_failure": model.get("last_failure_at"),
        "circuit_breaker_state": model.get("circuit_breaker_state"),
        "active_incidents": model.get("active_incidents"),
    }


def _load_tracking_rows() -> list[dict[str, Any]]:
    """Every enabled ``model_health_tracking`` row.

    Paged with a total ORDER BY on the primary key: PostgREST caps a response at
    1000 rows, and ``range()`` without a stable order duplicates and drops rows
    between pages.
    """
    rows: list[dict[str, Any]] = []
    for page in range(_TRACKING_MAX_PAGES):
        start = page * _TRACKING_PAGE_SIZE
        response = (
            get_db()
            .table("model_health_tracking")
            .select("provider,model,gateway,last_status,last_called_at,circuit_breaker_state")
            .eq("is_enabled", True)
            .order("provider")
            .order("model")
            .range(start, start + _TRACKING_PAGE_SIZE - 1)
            .execute()
        )
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < _TRACKING_PAGE_SIZE:
            break
    return rows


def _latest_measurement_by_model(
    tracking_rows: list[dict[str, Any]], catalog_ids: set[str]
) -> tuple[dict[str, dict[str, Any]], int]:
    """Map catalog model id -> its most recent tracking row.

    Tracking rows store the gateway-prefixed catalog id ("openai/gpt-5.5");
    a bare id is matched as "<gateway>/<model>". Returns the map and the number
    of rows that match no catalog model (delisted models, disabled providers) —
    those describe nothing we serve and are excluded from every figure.
    """
    latest: dict[str, dict[str, Any]] = {}
    orphaned = 0
    for row in tracking_rows:
        model = row.get("model") or ""
        key = model if model in catalog_ids else None
        if key is None and "/" not in model:
            prefix = row.get("gateway") or row.get("provider") or ""
            candidate = f"{prefix}/{model}"
            key = candidate if candidate in catalog_ids else None
        if key is None:
            orphaned += 1
            continue
        current = latest.get(key)
        epoch = datetime.min.replace(tzinfo=UTC)
        row_ts = _parse_ts(row.get("last_called_at")) or epoch
        if current is None or row_ts > (_parse_ts(current.get("last_called_at")) or epoch):
            latest[key] = row
    return latest, orphaned


def _round_or_zero(value: Any, digits: int) -> float:
    """``round()`` that survives a NULL, a missing column or a non-numeric cell.

    ``model_status_current`` is a database VIEW. ``CREATE OR REPLACE VIEW``
    cannot reorder or retype existing columns, so a migration that tried to
    widen it fails and silently leaves the OLD definition in place. Indexing the
    row with ``row["col"]`` then raises KeyError for EVERY row, which is how
    ``GET /v1/status/models`` returned 500 in production while ``/v1/status/search``
    — reading a narrower subset of the same view — returned 200.
    """
    try:
        return round(float(value or 0), digits)
    except (TypeError, ValueError):
        return 0.0


def _warn_on_missing_columns(rows: list[dict[str, Any]], expected: set[str]) -> None:
    """Name the columns the view failed to supply, once per request.

    Without this the failure mode is a field that is quietly always null. The
    log line is what turns "the status page looks wrong" into a migration to run.
    """
    if not rows:
        return
    missing = sorted(expected - set(rows[0].keys()))
    if missing:
        logger.warning(
            "model_status_current is missing column(s) %s — the view is behind its "
            "definition. Apply "
            "supabase/staged-migrations/20260915000000_rebuild_model_status_views.sql. "
            "Affected fields are reported as null/0.",
            missing,
        )


_MODEL_STATUS_COLUMNS = {
    "model",
    "provider",
    "gateway",
    "status_indicator",
    "monitoring_tier",
    "uptime_percentage_24h",
    "uptime_percentage_7d",
    "uptime_percentage_30d",
    "average_response_time_ms",
    "last_called_at",
    "last_success_at",
    "last_failure_at",
    "circuit_breaker_state",
    "active_incidents_count",
}


def _gateway_of(model: dict[str, Any]) -> str:
    gateway = model.get("source_gateway") or model.get("provider_slug") or ""
    if not gateway:
        model_id = model.get("id") or ""
        gateway = model_id.split("/", 1)[0] if "/" in model_id else ""
    return str(gateway).strip().lower()


@router.get("/", response_model=dict[str, Any])
async def get_overall_status():
    """
    Get overall system status for status page

    Public endpoint - no authentication required. Reports only what is measured.

    Field meanings (changed 2026-09 — previously every figure came from the
    ``provider_health_current`` view, which counted every enabled
    ``model_health_tracking`` row: delisted models, never-refreshed rows and
    week-old verdicts all counted, so ``total_models`` disagreed with
    ``/v1/models`` and stale failures read as a live major outage):

    - ``total_models``: models ``GET /v1/models`` serves (same source and filters).
    - ``monitored_models``: catalog models with a health check in the last
      ``measurement_window_hours``. ``unmonitored_models`` = the rest; they are
      excluded from uptime and status and never treated as down.
    - ``healthy_models``: monitored models whose latest check succeeded.
    - ``offline_models``: monitored models whose circuit breaker is open.
    - ``degraded_models``: monitored, latest check failed, circuit not open.
    - ``rate_limited_models`` / ``unauthorized_models``: models whose latest
      probe was throttled (429) or rejected for auth. Those outcomes measure the
      PROBER's access, not the model, so they count as UNMONITORED — never as
      degraded or offline — and are reported here so the condition stays visible.
    - ``uptime_percentage``: healthy / monitored * 100 — a point-in-time share of
      models passing their latest check, not a time-weighted availability. ``null``
      when nothing is monitored, with ``uptime_reason`` saying why.
    - ``status``: from monitored models only; ``unknown`` when there are none.
    - ``total_gateways``: gateways in the catalog; ``healthy_gateways``: those with
      at least one healthy monitored model; ``gateway_health_percentage`` is over
      ``monitored_gateways`` and ``null`` when none are monitored.
    """
    try:
        from src.routes.catalog import get_public_catalog_models

        catalog = await asyncio.to_thread(get_public_catalog_models)
        now = datetime.now(UTC)
        window_hours = int(MEASUREMENT_MAX_AGE.total_seconds() // 3600)

        if not catalog:
            return {
                "status": "unknown",
                "status_message": "Model catalog unavailable",
                "message": "Status data not available",
                "uptime_percentage": None,
                "uptime_reason": "Model catalog unavailable; nothing to measure against.",
                "total_models": 0,
                "monitored_models": 0,
                "unmonitored_models": 0,
                "timestamp": now.isoformat(),
                "last_updated": now.isoformat(),
            }

        catalog_ids = {m["id"] for m in catalog if m.get("id")}
        tracking_rows = await asyncio.to_thread(_load_tracking_rows)
        latest, orphaned_rows = _latest_measurement_by_model(tracking_rows, catalog_ids)

        cutoff = now - MEASUREMENT_MAX_AGE
        gateways: dict[str, dict[str, bool]] = {}
        healthy = offline = degraded = monitored = 0
        rate_limited = unauthorized = 0
        for model in catalog:
            gateway = _gateway_of(model)
            gw = (
                gateways.setdefault(gateway, {"monitored": False, "healthy": False})
                if gateway
                else None
            )

            row = latest.get(model.get("id") or "")
            checked_at = _parse_ts(row.get("last_called_at")) if row else None
            if checked_at is None or checked_at < cutoff:
                continue  # unmonitored: no current measurement, so no verdict

            last_status = (row.get("last_status") or "").lower()

            if (row.get("circuit_breaker_state") or "").lower() == "open":
                # An open breaker is earned by real failures only (429s and auth
                # failures never move it), so it outranks a latest probe that
                # merely could not get an answer. Real outages still surface.
                monitored += 1
                if gw is not None:
                    gw["monitored"] = True
                offline += 1
                continue

            if is_unmeasured_status(last_status):
                # The prober was throttled or has no working key for this
                # gateway. That measures our access, not the model — counting it
                # as degraded is what turned a healthy catalog into a public
                # "major outage". Excluded from uptime, reported separately.
                if last_status == "rate_limited":
                    rate_limited += 1
                else:
                    unauthorized += 1
                continue

            monitored += 1
            if gw is not None:
                gw["monitored"] = True
            if last_status == "success":
                healthy += 1
                if gw is not None:
                    gw["healthy"] = True
            else:
                degraded += 1

        total_models = len(catalog)
        unmonitored = total_models - monitored

        if monitored == 0:
            status = "unknown"
            status_message = "No models monitored"
            uptime_percentage = None
            uptime_reason = (
                f"None of the {total_models} catalog models has a health check in the "
                f"last {window_hours}h."
            )
        else:
            if offline == 0:
                status = "operational"
                status_message = "All Systems Operational"
            elif offline < monitored * 0.1:
                status = "degraded"
                status_message = "Partial Service Degradation"
            else:
                status = "major_outage"
                status_message = "Major Service Disruption"
            uptime_percentage = round(healthy / monitored * 100, 2)
            uptime_reason = None

        incidents_response = (
            get_db()
            .table("model_health_incidents")
            .select("id", count="exact")
            .eq("status", "active")
            .execute()
        )
        active_incidents = incidents_response.count or 0

        total_gateways = len(gateways)
        monitored_gateways = sum(1 for g in gateways.values() if g["monitored"])
        healthy_gateways = sum(1 for g in gateways.values() if g["healthy"])
        gateway_health_percentage = (
            round(healthy_gateways / monitored_gateways * 100, 1) if monitored_gateways else None
        )
        providers = {
            str(m.get("provider_slug") or _gateway_of(m)).lower()
            for m in catalog
            if m.get("provider_slug") or _gateway_of(m)
        }

        return {
            "status": status,
            "status_message": status_message,
            "uptime_percentage": uptime_percentage,
            "uptime_reason": uptime_reason,
            "total_models": total_models,
            "monitored_models": monitored,
            "unmonitored_models": unmonitored,
            "monitoring_coverage_percentage": round(monitored / total_models * 100, 1),
            "healthy_models": healthy,
            "degraded_models": degraded,
            "offline_models": offline,
            # Unmeasured: the probe could not get an answer about the model.
            # Counted in unmonitored_models, never in uptime or status, but
            # reported so a throttled prober or a missing provider key is
            # visible instead of silently inflating "degraded".
            "rate_limited_models": rate_limited,
            "unauthorized_models": unauthorized,
            "measurement_window_hours": window_hours,
            "excluded_tracking_rows": orphaned_rows,
            "total_providers": len(providers),
            "total_gateways": total_gateways,
            "monitored_gateways": monitored_gateways,
            "healthy_gateways": healthy_gateways,
            "gateway_health_percentage": gateway_health_percentage,
            "active_incidents": active_incidents,
            "last_updated": now.isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to get overall status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve status") from e


@router.get("/providers", response_model=list[dict[str, Any]])
async def get_providers_status():
    """
    Get status for all providers

    Public endpoint - no authentication required.
    Returns health status for each provider/gateway combination.
    """
    try:
        response = get_db().table("provider_health_current").select("*").order("provider").execute()

        providers = response.data or []

        # Format for frontend display
        formatted = []
        for provider in providers:
            healthy = provider.get("healthy_models") or 0
            total = provider.get("total_models") or 0

            # Apply same data consistency check as main status endpoint
            if healthy > total:
                logger.warning(
                    f"Data inconsistency in provider {provider['provider']}/{provider['gateway']}: "
                    f"healthy_models ({healthy}) > total_models ({total}). Capping to total."
                )
                healthy = total

            formatted.append(
                {
                    "name": provider.get("provider"),
                    "gateway": provider.get("gateway"),
                    "status": provider.get("status_indicator"),
                    "uptime_24h": _round_or_zero(provider.get("avg_uptime_24h"), 2),
                    "uptime_7d": _round_or_zero(provider.get("avg_uptime_7d"), 2),
                    "total_models": total,
                    "healthy_models": healthy,
                    "offline_models": provider.get("offline_models") or 0,
                    "avg_response_time_ms": _round_or_zero(provider.get("avg_response_time_ms"), 0),
                    "last_checked": provider.get("last_checked_at"),
                }
            )

        return formatted

    except Exception as e:
        logger.error(f"Failed to get providers status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve provider status") from e


@router.get("/models", response_model=list[dict[str, Any]])
async def get_models_status(
    provider: str | None = Query(None, description="Filter by provider"),
    gateway: str | None = Query(None, description="Filter by gateway"),
    status: str | None = Query(None, description="Filter by status"),
    tier: str | None = Query(None, description="Filter by monitoring tier"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    """
    Get status for models

    Public endpoint - no authentication required.
    Supports filtering and pagination.
    """
    try:
        query = get_db().table("model_status_current").select("*")

        # Apply filters
        if provider:
            query = query.eq("provider", provider)
        if gateway:
            query = query.eq("gateway", gateway)
        if status:
            query = query.eq("status_indicator", status)
        if tier:
            query = query.eq("monitoring_tier", tier)

        # Ordered before the range, and with a tiebreaker: usage_count_24h alone
        # is not unique, so paging it duplicates and drops rows between pages.
        query = query.order("usage_count_24h", desc=True).order("provider").order("model")
        query = query.range(offset, offset + limit - 1)

        response = query.execute()
        models = response.data or []

        _warn_on_missing_columns(models, _MODEL_STATUS_COLUMNS)
        now = datetime.now(UTC)
        return [_format_model_status(model, now) for model in models]

    except Exception as e:
        logger.error(f"Failed to get models status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve model status") from e


@router.get("/models/{provider}/{model_id}", response_model=dict[str, Any])
async def get_model_status(provider: str, model_id: str, gateway: str | None = Query(None)):
    """
    Get status for a specific model

    Public endpoint - no authentication required.
    Returns detailed status information for a single model.
    """
    try:
        query = (
            get_db()
            .table("model_status_current")
            .select("*")
            .eq("provider", provider)
            .eq("model", model_id)
        )

        if gateway:
            query = query.eq("gateway", gateway)

        response = query.maybe_single().execute()

        # maybe_single() returns None (not a response with empty data) when
        # nothing matches, so `response.data` raised AttributeError and this
        # endpoint answered 500 for every unknown model instead of 404.
        if response is None or not response.data:
            raise HTTPException(status_code=404, detail="Model not found")

        model = response.data

        return {
            **_format_model_status(model),
            "consecutive_failures": model.get("consecutive_failures"),
            "usage_24h": model.get("usage_count_24h"),
            "is_enabled": model.get("is_enabled"),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get model status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve model status") from e


@router.get("/incidents", response_model=list[dict[str, Any]])
async def get_incidents(
    status: str | None = Query(None, description="Filter by status (active, resolved)"),
    severity: str | None = Query(None, description="Filter by severity"),
    provider: str | None = Query(None, description="Filter by provider"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """
    Get incidents

    Public endpoint - no authentication required.
    Returns recent incidents with filtering.
    """
    try:
        query = get_db().table("model_health_incidents").select("*")

        # Apply filters
        if status:
            query = query.eq("status", status)
        if severity:
            query = query.eq("severity", severity)
        if provider:
            query = query.eq("provider", provider)

        # Pagination
        query = query.range(offset, offset + limit - 1)
        query = query.order("started_at", desc=True)

        response = query.execute()
        incidents = response.data or []

        # Format for frontend
        formatted = []
        for incident in incidents:
            duration = None
            if incident["resolved_at"]:
                duration = incident["duration_seconds"]
            else:
                # Calculate current duration for active incidents
                started = datetime.fromisoformat(incident["started_at"])
                duration = int((datetime.now(UTC) - started).total_seconds())

            formatted.append(
                {
                    "id": incident["id"],
                    "provider": incident["provider"],
                    "model": incident["model"],
                    "gateway": incident["gateway"],
                    "type": incident["incident_type"],
                    "severity": incident["severity"],
                    "status": incident["status"],
                    "started_at": incident["started_at"],
                    "resolved_at": incident["resolved_at"],
                    "duration_seconds": duration,
                    "duration_human": _format_duration(duration) if duration else None,
                    "error_message": incident["error_message"],
                    "error_count": incident["error_count"],
                    "resolution_notes": incident.get("resolution_notes"),
                }
            )

        return formatted

    except Exception as e:
        logger.error(f"Failed to get incidents: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve incidents") from e


@router.get("/uptime/{provider}/{model_id}", response_model=dict[str, Any])
async def get_model_uptime_history(
    provider: str,
    model_id: str,
    gateway: str | None = Query(None),
    period: str = Query("24h", description="Time period: 24h, 7d, 30d"),
):
    """
    Get uptime history for a specific model

    Public endpoint - no authentication required.
    Returns time-series uptime data for charts.
    """
    try:
        # Determine time range
        now = datetime.now(UTC)
        if period == "24h":
            start_time = now - timedelta(hours=24)
            interval = "hour"
        elif period == "7d":
            start_time = now - timedelta(days=7)
            interval = "day"
        elif period == "30d":
            start_time = now - timedelta(days=30)
            interval = "day"
        else:
            raise HTTPException(status_code=400, detail="Invalid period")

        # NOTE: model_health_aggregates does not exist in the database (checked
        # 2026-09-15), so this route 500'd for every model since it was written.
        # A missing table is answered honestly below rather than as a server
        # error the caller should retry.
        query = (
            get_db()
            .table("model_health_aggregates")
            .select("*")
            .eq("provider", provider)
            .eq("model", model_id)
            .eq("aggregation_period", interval)
            .gte("period_start", start_time.isoformat())
        )

        if gateway:
            query = query.eq("gateway", gateway)

        try:
            response = query.order("period_start", desc=False).execute()
        except Exception as e:
            # 42P01 = undefined_table. No history is recorded anywhere, so this
            # is a permanent, knowable state: say so once instead of handing the
            # caller a 500 it will retry forever.
            if _is_missing_table(e):
                logger.warning(
                    "uptime history requested but model_health_aggregates does not exist"
                )
                return {
                    "provider": provider,
                    "model": model_id,
                    "period": period,
                    "data_points": [],
                    "history_available": False,
                    "reason": (
                        "No uptime history is recorded: the model_health_aggregates "
                        "table does not exist in this deployment."
                    ),
                }
            raise

        data = response.data or []

        points = [
            {
                "timestamp": point.get("period_start"),
                "uptime_percentage": round(_num(point.get("uptime_percentage")), 2),
                "avg_response_time_ms": round(_num(point.get("avg_response_time_ms"))),
                "total_checks": point.get("total_checks"),
                "successful_checks": point.get("successful_checks"),
                "failed_checks": point.get("failed_checks"),
            }
            for point in data
        ]

        return {
            "provider": provider,
            "model": model_id,
            "period": period,
            "data_points": points,
            "history_available": True,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get uptime history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve uptime history") from e


@router.get("/search", response_model=list[dict[str, Any]])
async def search_models(
    q: str = Query(..., min_length=2, description="Search query"),
    limit: int = Query(20, ge=1, le=100),
):
    """
    Search for models by name or provider

    Public endpoint - no authentication required.
    """
    try:
        # Sanitize search query to prevent PostgREST filter injection
        # Remove special characters that have meaning in PostgREST syntax
        sanitized_q = q.replace(",", "").replace("(", "").replace(")", "").replace(".", "")

        query = (
            get_db()
            .table("model_status_current")
            .select("*")
            .or_(f"model.ilike.%{sanitized_q}%,provider.ilike.%{sanitized_q}%")
            .limit(limit)
        )

        response = query.execute()
        models = response.data or []

        # Search served the same row shape with the same two faults as the list
        # route (numeric strings through round(), stale rows as healthy); it goes
        # through the shared formatter so it cannot drift again.
        now = datetime.now(UTC)
        formatted = [
            {
                k: v
                for k, v in _format_model_status(model, now).items()
                if k
                in {
                    "model_id",
                    "provider",
                    "gateway",
                    "status",
                    "tier",
                    "uptime_24h",
                    "monitored",
                }
            }
            for model in models
        ]

        return formatted

    except Exception as e:
        logger.error(f"Failed to search models: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to search models") from e


@router.get("/stats", response_model=dict[str, Any])
async def get_stats():
    """
    Get overall statistics for status page

    Public endpoint - no authentication required.
    Returns summary statistics and metrics.
    """
    try:
        # Get model counts by tier
        tier_counts_response = (
            get_db()
            .table("model_health_tracking")
            .select("monitoring_tier", count="exact")
            .eq("is_enabled", True)
            .execute()
        )

        tier_data = tier_counts_response.data or []
        tier_counts = {}
        for row in tier_data:
            tier = row.get("monitoring_tier", "unknown")
            tier_counts[tier] = tier_counts.get(tier, 0) + 1

        # Get incident statistics
        incidents_response = (
            get_db()
            .table("model_health_incidents")
            .select("severity,status", count="exact")
            .execute()
        )

        total_incidents = incidents_response.count or 0
        active_incidents = len([i for i in incidents_response.data if i.get("status") == "active"])

        # Get check statistics from last 24h
        yesterday = datetime.now(UTC) - timedelta(hours=24)
        checks_response = (
            get_db()
            .table("model_health_history")
            .select("status", count="exact")
            .gte("checked_at", yesterday.isoformat())
            .execute()
        )

        total_checks = checks_response.count or 0
        successful_checks = len([c for c in checks_response.data if c.get("status") == "success"])

        # total_models is the catalog GET /v1/models serves, as in `/v1/status`
        # (#2325). Summing enabled tracking rows reported 123 against 68 served:
        # delisted models keep their rows. tracking_rows_enabled keeps the raw
        # figure visible rather than quietly dropping it.
        from src.routes.catalog import get_public_catalog_models

        catalog = await asyncio.to_thread(get_public_catalog_models)

        return {
            "monitoring": {
                "total_models": len(catalog),
                "tracking_rows_enabled": sum(tier_counts.values()),
                "critical_tier": tier_counts.get("critical", 0),
                "popular_tier": tier_counts.get("popular", 0),
                "standard_tier": tier_counts.get("standard", 0),
                "on_demand_tier": tier_counts.get("on_demand", 0),
            },
            "incidents": {
                "total_all_time": total_incidents,
                "active": active_incidents,
                "resolved": total_incidents - active_incidents,
            },
            "checks_24h": {
                "total": total_checks,
                "successful": successful_checks,
                "failed": total_checks - successful_checks,
                # null, not 0, when there are no samples. This endpoint is public
                # and unauthenticated, so reporting 0 for "no data" tells every
                # reader the gateway failed 100% of its checks. monitoring_active
                # keeps "not measured" distinguishable from "measured and failing".
                "success_rate": (
                    round(successful_checks / total_checks * 100, 2) if total_checks > 0 else None
                ),
                "monitoring_active": total_checks > 0,
            },
            "last_updated": datetime.now(UTC).isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to get stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve statistics") from e


def _format_duration(seconds: int) -> str:
    """Format duration in human-readable format"""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}m"
    elif seconds < 86400:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m"
    else:
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        return f"{days}d {hours}h"
