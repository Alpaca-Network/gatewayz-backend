"""Public GPU transparency feed (gatewayz-backend#2263 #2264, spec §6).

No auth, no envelope (mirrors src/routes/status_page.py's public-endpoint
precedent, not the `{success, data}` wrapper the authenticated /gpu/*
routes use). Every response carries `Cache-Control: public, max-age=30`
and is rate-limited 60/min/IP.

Rate limiting: reuses sliding_window_check from src.services.rate_limiting
directly (the same primitive src.services.auth_rate_limiting's IP limiter
for /auth/login etc. is built on -- see that module's AuthRateLimiter),
keyed by IP via the same get_client_ip helper auth endpoints use, rather
than going through AuthRateLimitType (a closed enum of auth-specific
actions) or create_endpoint_rate_limit (src/services/endpoint_rate_limiter.py,
keyed by API key -- these endpoints have none).

Caching: Redis with a 30s TTL when available, an in-process dict fallback
(also 30s) when it is not -- mirrors src.config.redis_config's own
is_available()-gated fallback pattern used throughout the codebase.

Aggregate-only guarantee: every payload here is built exclusively from
src.db.gpu_rollups's read helpers, which themselves only ever select
public-safe columns (see that module's docstrings). Enforced by
tests/security/test_gpu_public_aggregate_only.py.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from src.config.redis_config import get_redis_config
from src.db.gpu_rollups import get_public_nodes, get_summary, get_utilization
from src.schemas.gpu_public import (
    GpuPublicNodesResponse,
    GpuPublicSummary,
    GpuPublicUtilizationResponse,
)
from src.services.auth_rate_limiting import get_client_ip
from src.services.rate_limiting import sliding_window_check

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/gpu/public", tags=["gpu-public"])

_CACHE_TTL_SECONDS = 30
_RATE_LIMIT_MAX_REQUESTS = 60
_RATE_LIMIT_WINDOW_SECONDS = 60

# In-process fallback cache: key -> (written_at_monotonic, value). Only used
# when Redis is unavailable; per-process, so it doesn't help across Railway
# replicas, but it's strictly a fallback for a 30s cache anyway.
_local_cache: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str) -> Any | None:
    redis_cfg = get_redis_config()
    if redis_cfg.is_available():
        raw = redis_cfg.get_cache(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            logger.warning(f"gpu_public cache: unparseable cached value for {key}")
            return None

    entry = _local_cache.get(key)
    if entry is None:
        return None
    written_at, value = entry
    if time.monotonic() - written_at >= _CACHE_TTL_SECONDS:
        return None
    return value


def _cache_set(key: str, value: Any) -> None:
    redis_cfg = get_redis_config()
    if redis_cfg.is_available():
        redis_cfg.set_cache(key, json.dumps(value), ttl=_CACHE_TTL_SECONDS)
        return
    _local_cache[key] = (time.monotonic(), value)


async def _rate_limit(request: Request) -> None:
    ip = get_client_ip(request)
    allowed, _remaining, retry_after = sliding_window_check(
        f"gpu_public:{ip}", _RATE_LIMIT_MAX_REQUESTS, _RATE_LIMIT_WINDOW_SECONDS
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "message": (
                        f"Rate limit exceeded. Maximum {_RATE_LIMIT_MAX_REQUESTS} "
                        f"requests per {_RATE_LIMIT_WINDOW_SECONDS} seconds."
                    ),
                    "type": "gpu_public_rate_limit",
                    "code": 429,
                }
            },
            headers={"Retry-After": str(retry_after or _RATE_LIMIT_WINDOW_SECONDS)},
        )


def _set_cache_headers(response: Response) -> None:
    response.headers["Cache-Control"] = f"public, max-age={_CACHE_TTL_SECONDS}"


@router.get("/summary", response_model=GpuPublicSummary, dependencies=[Depends(_rate_limit)])
async def public_summary(response: Response) -> dict:
    """Protocol-wide public summary: node/provider counts, region and model
    breakdown, and last-hour aggregate utilization."""
    cache_key = "gpu_public:summary"
    data = _cache_get(cache_key)
    if data is None:
        data = get_summary()
        _cache_set(cache_key, data)
    _set_cache_headers(response)
    return data


@router.get("/nodes", response_model=GpuPublicNodesResponse, dependencies=[Depends(_rate_limit)])
async def public_nodes(response: Response) -> list[dict]:
    """Public node listing -- name, region, gpu_model, vram_gb, status,
    uptime_24h_pct, models. Never wallet, endpoint, node token, or provider
    identity."""
    cache_key = "gpu_public:nodes"
    data = _cache_get(cache_key)
    if data is None:
        data = get_public_nodes()
        _cache_set(cache_key, data)
    _set_cache_headers(response)
    return data


@router.get(
    "/utilization",
    response_model=GpuPublicUtilizationResponse,
    dependencies=[Depends(_rate_limit)],
)
async def public_utilization(
    response: Response,
    window: str = Query("24h", pattern="^(24h|7d)$"),
    group: str = Query("region", pattern="^(region|model)$"),
) -> dict:
    """Hourly utilization series from the gpu_utilization_hourly rollup."""
    cache_key = f"gpu_public:utilization:{window}:{group}"
    data = _cache_get(cache_key)
    if data is None:
        series = get_utilization(window, group)
        data = {"window": window, "group": group, "series": series}
        _cache_set(cache_key, data)
    _set_cache_headers(response)
    return data


def build_public_feed_schema() -> dict:
    """JSON Schema (draft 2020-12) for the three payloads above, generated
    from the Pydantic response models so it cannot drift from the live
    responses. Also committed at docs/gpu/public-feed.schema.json (kept in
    sync by tests/routes/test_gpu_public.py::test_schema_matches_committed_file
    and scripts/generate_gpu_public_schema.py).
    """
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://gatewayz.ai/schemas/gpu-public-feed.json",
        "title": "Gatewayz GPU Public Transparency Feed",
        "definitions": {
            "summary": GpuPublicSummary.model_json_schema(),
            "nodes": GpuPublicNodesResponse.model_json_schema(),
            "utilization": GpuPublicUtilizationResponse.model_json_schema(),
        },
    }


@router.get("/schema", dependencies=[Depends(_rate_limit)])
async def public_schema(response: Response) -> dict:
    """JSON Schema for summary/nodes/utilization (draft 2020-12)."""
    cache_key = "gpu_public:schema"
    data = _cache_get(cache_key)
    if data is None:
        data = build_public_feed_schema()
        _cache_set(cache_key, data)
    _set_cache_headers(response)
    return data
