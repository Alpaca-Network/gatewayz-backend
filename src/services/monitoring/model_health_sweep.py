"""
Model health sweep — classify live-probe results and persist FRESH per-model
health verdicts.

This module turns the raw pass/fail/timeout/error results produced by the admin
live-model-test sweep (``src/routes/live_model_test.py``) into:

1. A precise per-model call record in ``model_health_tracking`` (via
   ``record_model_call``) so soft failures (429 / timeout / auth) stay
   distinguishable from hard failures (404 / 5xx / dead model).
2. A conservative catalog verdict written to the ``models`` table
   (``health_status`` column) via ``update_model_health``.

Safety property
---------------
The pipeline NEVER hides a model that merely rate-limits (429), times out, or has
auth problems — those recover or are config issues. Only models that *consistently
hard-fail* (dead / 404 / 5xx) reach ``health_status='down'``, and only after
``HARD_FAIL_THRESHOLD`` consecutive hard failures. Everything is wrapped
defensively: this runs from an admin endpoint / cron and must never raise.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Number of consecutive hard failures required before a model is marked "down".
HARD_FAIL_THRESHOLD = 3

# Substrings (checked case-insensitively) that indicate a model genuinely does not
# exist / has no serving endpoint — i.e. a HARD failure worth hiding.
_NOT_FOUND_PATTERNS = (
    "not found",
    "no endpoints",
    "no allowed providers",
    "does not exist",
)
# Substrings that indicate a transient rate-limit — a SOFT failure (never hide).
_RATE_LIMIT_PATTERNS = ("rate limit", "too many requests", "rate_limited")
# Substrings that indicate an auth/config problem — a SOFT failure (never hide).
_AUTH_PATTERNS = ("unauthorized", "authentication", "api key")


def classify_probe_result(status: str, status_code: int | None, error: str | None) -> str:
    """Classify a single live-probe result into a health outcome.

    Args:
        status: The probe status ("pass", "fail", "timeout", "error", "skip").
        status_code: HTTP status code from the probe, if any.
        error: Error text from the probe, if any.

    Returns:
        One of "healthy", "hard_fail", "soft", "skip".

    The default for any ambiguous failure is "soft" — we never hide a model on
    uncertain evidence.
    """
    s = (status or "").strip().lower()
    err = (error or "").lower()

    if s == "pass":
        return "healthy"
    if s == "skip":
        return "skip"
    if s == "timeout":
        return "soft"

    if s == "fail":
        if status_code == 404 or any(p in err for p in _NOT_FOUND_PATTERNS):
            return "hard_fail"
        if status_code == 429 or any(p in err for p in _RATE_LIMIT_PATTERNS):
            return "soft"
        if status_code in (401, 403) or any(p in err for p in _AUTH_PATTERNS):
            return "soft"
        if isinstance(status_code, int) and status_code >= 500:
            return "hard_fail"
        # Anything else (400, unknown) → soft: never hide on ambiguous evidence.
        return "soft"

    if s == "error":
        # Transport/exception errors are soft unless the text clearly says the
        # model does not exist (e.g. "No endpoints found for model X").
        if any(p in err for p in _NOT_FOUND_PATTERNS):
            return "hard_fail"
        return "soft"

    # Unknown status → soft (safe default).
    return "soft"


def _precise_status(outcome: str, status_code: int | None, error: str | None) -> str:
    """Map an outcome to the precise ``model_health_tracking.last_status`` value.

    Keeping this precise (rather than a blanket "error") is what makes soft
    failures distinguishable from hard ones on later reads.
    """
    err = (error or "").lower()
    if outcome == "healthy":
        return "success"
    if outcome == "hard_fail":
        if status_code == 404 or any(p in err for p in _NOT_FOUND_PATTERNS):
            return "not_found"
        return "provider_error"
    # soft
    if status_code == 429 or any(p in err for p in _RATE_LIMIT_PATTERNS):
        return "rate_limited"
    if status_code in (401, 403) or any(p in err for p in _AUTH_PATTERNS):
        return "unauthorized"
    return "error"


def _field(result: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from a ModelTestResult-like object OR a plain dict."""
    if isinstance(result, dict):
        return result.get(name, default)
    return getattr(result, name, default)


def _build_model_pk_map(model_ids: list[str]) -> dict[str, int]:
    """Batch-resolve string catalog ids → integer ``models`` primary keys.

    The catalog id is ``provider_model_id`` (falling back to ``model_name``), so we
    look up both columns. Tolerant of misses — unresolved ids are simply absent
    from the returned map.
    """
    mapping: dict[str, int] = {}
    unique_ids = [mid for mid in dict.fromkeys(model_ids) if mid]
    if not unique_ids:
        return mapping

    try:
        from src.config.supabase_config import get_client_for_query

        supabase = get_client_for_query(read_only=True)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("model_health_sweep: cannot get supabase client for PK lookup: %s", exc)
        return mapping

    chunk_size = 200
    for i in range(0, len(unique_ids), chunk_size):
        chunk = unique_ids[i : i + chunk_size]
        chunk_set = set(chunk)
        for column in ("provider_model_id", "model_name"):
            try:
                resp = (
                    supabase.table("models")
                    .select("id, model_name, provider_model_id")
                    .in_(column, chunk)
                    .execute()
                )
            except Exception as exc:
                logger.warning("model_health_sweep: PK lookup by %s failed: %s", column, exc)
                continue
            for row in resp.data or []:
                pk = row.get("id")
                if pk is None:
                    continue
                for key in (row.get("provider_model_id"), row.get("model_name")):
                    if key and key in chunk_set and key not in mapping:
                        mapping[key] = pk
    return mapping


def _update_hard_fail_streak(
    provider: str, model: str, gateway: str | None, consecutive_failures: int
) -> None:
    """Persist the hard-failure streak counter on ``model_health_tracking``.

    ``record_model_call`` maintains call/success/error counts and ``last_status``
    but does NOT touch ``consecutive_failures``; we maintain that counter here so
    the "consistently hard-failing" verdict actually works. Best-effort only.
    """
    try:
        from src.config.supabase_config import get_supabase_client

        supabase = get_supabase_client()
        payload = {
            "provider": provider,
            "model": model,
            "gateway": gateway or provider,
            "consecutive_failures": consecutive_failures,
            "consecutive_successes": 0 if consecutive_failures > 0 else 1,
        }
        supabase.table("model_health_tracking").upsert(
            payload, on_conflict="provider,model"
        ).execute()
    except Exception as exc:
        logger.warning(
            "model_health_sweep: failed to update hard-fail streak for %s/%s: %s",
            provider,
            model,
            exc,
        )


async def persist_sweep_results(results: list) -> dict:
    """Persist classified sweep results and compute per-model health verdicts.

    For each result (``ModelTestResult``-like object or dict):
      * classify → outcome; skip "skip" outcomes entirely.
      * record a precise call in ``model_health_tracking`` via ``record_model_call``.
      * maintain the consecutive hard-failure streak.
      * write a conservative verdict to the ``models`` table:
          - "healthy"   → health_status='healthy' (recovery)
          - "hard_fail" → health_status='down' ONLY once the streak reaches
                          HARD_FAIL_THRESHOLD consecutive hard failures.
          - "soft"      → no change (neutral: 429 / timeout / auth never hide).

    Never raises. Returns a summary dict of counts.
    """
    summary = {
        "processed": 0,
        "healthy": 0,
        "hard_fail": 0,
        "soft": 0,
        "skipped": 0,
        "marked_down": 0,
        "recovered": 0,
        "errors": 0,
    }

    # Deferred imports keep this module import-light and avoid import cycles.
    try:
        from src.db.model_health import get_model_health, record_model_call
        from src.db.models_catalog_db import update_model_health
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("model_health_sweep: cannot import DB helpers: %s", exc)
        return summary

    # First pass: record calls + maintain streaks, collect verdicts to write.
    verdicts: dict[str, str] = {}  # model_id -> "down" | "healthy"
    for result in results or []:
        try:
            status = _field(result, "status", "")
            status_code = _field(result, "status_code")
            error = _field(result, "error")
            outcome = classify_probe_result(status, status_code, error)

            if outcome == "skip":
                summary["skipped"] += 1
                continue

            summary["processed"] += 1
            summary[outcome] = summary.get(outcome, 0) + 1

            model_id = _field(result, "model_id") or ""
            provider = _field(result, "provider") or _field(result, "gateway") or "unknown"
            gateway = _field(result, "gateway")
            latency = _field(result, "latency_ms") or 0

            if not model_id:
                continue

            precise = _precise_status(outcome, status_code, error)

            # Read the current streak BEFORE recording (record_model_call preserves
            # consecutive_failures, so this reflects the prior state).
            prev = get_model_health(provider, model_id) or {}
            prev_streak = int(prev.get("consecutive_failures") or 0)

            record_model_call(
                provider,
                model=model_id,
                response_time_ms=latency,
                status=precise,
                error_message=error,
                gateway=gateway,
            )

            if outcome == "healthy":
                new_streak = 0
            elif outcome == "hard_fail":
                new_streak = prev_streak + 1
            else:  # soft — neutral, leave streak unchanged
                new_streak = prev_streak

            # Only write the streak when it changes (healthy reset or hard bump).
            if outcome in ("healthy", "hard_fail"):
                _update_hard_fail_streak(provider, model_id, gateway, new_streak)

            if outcome == "healthy":
                verdicts[model_id] = "healthy"
            elif outcome == "hard_fail" and new_streak >= HARD_FAIL_THRESHOLD:
                verdicts[model_id] = "down"
        except Exception as exc:
            summary["errors"] += 1
            logger.warning("model_health_sweep: error processing a result: %s", exc)

    # Second pass: resolve PKs in one batch and write catalog verdicts.
    if verdicts:
        try:
            pk_map = _build_model_pk_map(list(verdicts.keys()))
        except Exception as exc:
            logger.warning("model_health_sweep: PK batch lookup failed: %s", exc)
            pk_map = {}

        for model_id, verdict in verdicts.items():
            pk = pk_map.get(model_id)
            if pk is None:
                logger.debug(
                    "model_health_sweep: no models PK for '%s'; skipping verdict '%s'",
                    model_id,
                    verdict,
                )
                continue
            try:
                update_model_health(pk, verdict)
                if verdict == "down":
                    summary["marked_down"] += 1
                    logger.info("model_health_sweep: marked '%s' (pk=%s) down", model_id, pk)
                else:
                    summary["recovered"] += 1
            except Exception as exc:
                summary["errors"] += 1
                logger.warning(
                    "model_health_sweep: failed writing verdict for %s: %s", model_id, exc
                )

    logger.info("model_health_sweep: persistence summary %s", summary)
    return summary
