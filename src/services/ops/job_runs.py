"""Job-run registry for every scheduled job (gatewayz-backend admin ops).

Every scheduled job in src/services/scheduled_sync.py, src/services/gpu/
{rollup,spot_check,settlement}.py, src/services/chain/wayz_staking_sync.py,
and src/db/retention.py already tracks its own ad-hoc "last run" status dict
(``_last_sync_status``, ``_last_price_refresh_status``, etc) for its own
health-monitoring surface. This module is a second, UNIFORM surface those
same call sites also write to -- so the admin ops page (``GET
/admin/wayz/status``) can render one jobs table instead of reading nine
differently-shaped module-level dicts.

Redis-backed (key ``ops:job:{name}``, 7-day TTL, JSON value) with an
in-process dict fallback when Redis is unavailable, mirroring the
try/except + safe-default convention every ``src/db/*`` module in this
codebase already uses. ``record_job_run`` must NEVER raise -- a failure to
record must never mask, delay, or crash the job's own success/failure path,
since it is always called from the tail of a job that has already done its
real work.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from src.config.redis_config import get_redis_client

logger = logging.getLogger(__name__)

_KEY_PREFIX = "ops:job:"
_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days

# In-process fallback store, used whenever Redis is unavailable (e.g. local
# dev with no REDIS_URL configured). Not shared across processes/instances --
# same limitation as every other in-memory fallback in this codebase (see
# src/config/redis_config.py's own docstring). Module-level so it survives
# across calls within one process but never needs explicit teardown.
_fallback_store: dict[str, dict[str, Any]] = {}


def record_job_run(
    name: str,
    ok: bool,
    summary: dict[str, Any] | None = None,
    error: str | None = None,
    duration_ms: int | None = None,
) -> None:
    """Record the outcome of one job run under ``name``.

    Called from the tail of a scheduled job's own try/except -- on BOTH the
    success and failure paths -- so ``ok=False`` runs are visible too, not
    just successes. Never raises: any Redis or serialization failure is
    caught and logged, falling back to the in-process store rather than
    propagating into the caller's own job logic.
    """
    record = {
        "name": name,
        "ok": ok,
        "ran_at": datetime.now(UTC).isoformat(),
        "duration_ms": duration_ms,
        "summary": summary,
        "error": error,
    }

    client = None
    try:
        client = get_redis_client()
    except Exception as e:
        logger.debug(f"record_job_run: Redis client unavailable for '{name}': {e}")

    if client is not None:
        try:
            client.setex(f"{_KEY_PREFIX}{name}", _TTL_SECONDS, json.dumps(record))
            return
        except Exception as e:
            logger.warning(
                f"record_job_run: Redis write failed for '{name}', "
                f"falling back to in-process store: {e}"
            )

    _fallback_store[name] = record


def get_job_runs(names: list[str]) -> dict[str, dict[str, Any] | None]:
    """``{name: record_or_None}`` for each requested job name.

    A ``None`` value means the job has never recorded a run in this process
    (or via Redis), or its 7-day TTL expired -- callers (the ops status
    route) treat that the same as any other "no data yet" case, never as an
    error. Never raises.
    """
    results: dict[str, dict[str, Any] | None] = {}

    client = None
    try:
        client = get_redis_client()
    except Exception as e:
        logger.debug(f"get_job_runs: Redis client unavailable: {e}")

    for name in names:
        record: dict[str, Any] | None = None
        if client is not None:
            try:
                raw = client.get(f"{_KEY_PREFIX}{name}")
                if raw:
                    record = json.loads(raw)
            except Exception as e:
                logger.warning(f"get_job_runs: Redis read failed for '{name}': {e}")
        if record is None:
            record = _fallback_store.get(name)
        results[name] = record

    return results
