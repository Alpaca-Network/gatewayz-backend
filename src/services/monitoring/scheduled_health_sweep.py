"""Run the model health sweep on a schedule instead of when a human remembers.

Why this exists (#2367)
-----------------------
``/v1/models`` hides models marked ``health_status == 'down'``
(``_apply_health_gating``), and ``HEALTH_GATING_ENABLED`` defaults to true. The
gate works. The only thing that can set ``health_status = 'down'`` is
``persist_sweep_results``, and its only caller was an **admin** endpoint behind
an opt-in ``?persist=true``.

So the gate was armed and nothing ever pulled the trigger. Measured on
2026-09-23: ``health_status`` was ``unknown`` — never evaluated — on 30 of the
34 advertised models our own status surface published as down, and **8 models
were advertised as servable with pricing while measuring 0% uptime**, two of
them independently confirmed dead by direct call.

Safety, which is the whole design
---------------------------------
Hiding a model asserts it does not exist, and getting that wrong is expensive:
nine live flagship models were once hidden after a single bad window. So:

* **Two flags, two decisions.** ``MODEL_HEALTH_SWEEP_ENABLED`` runs the sweep.
  ``MODEL_HEALTH_SWEEP_WRITE`` lets it write verdicts. Both default **off**.
  Running and acting are not the same choice.
* **Record-only by default.** With the sweep on and writing off, it reports
  exactly what it *would* have marked down, and changes nothing. That produces
  real evidence about the decision before anybody lives with it.
* The judgement itself is unchanged and already conservative: only a 404 or an
  explicit not-found body is hard evidence, a 5xx is **never** proof a model is
  dead, and ``HARD_FAIL_THRESHOLD`` consecutive hard failures are required.
* Never raises. A monitoring job must not be able to take down startup or the
  event loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

logger = logging.getLogger(__name__)

# Cap one run so a pathological catalog cannot spend unbounded money or time.
MAX_MODELS_PER_RUN = 500
DEFAULT_CONCURRENCY = 5
DEFAULT_TIMEOUT_S = 45.0


def _sweep_api_key() -> str | None:
    """The key the sweep calls through.

    Falls back to ADMIN_API_KEY, which is what the admin route already used, so
    enabling this needs no new secret unless an operator wants a dedicated one.
    """
    return (
        os.getenv("MODEL_HEALTH_SWEEP_API_KEY") or os.getenv("ADMIN_API_KEY") or ""
    ).strip() or None


async def run_scheduled_health_sweep() -> dict:
    """One sweep. Returns a summary dict; never raises."""
    summary: dict = {"ran": False}
    started = time.monotonic()
    try:
        import httpx

        from src.config.config import Config
        from src.routes.live_model_test import _fetch_catalog, _test_single_model

        key = _sweep_api_key()
        if not key:
            # A sweep that silently does nothing is worse than no sweep: it
            # looks scheduled and reports success.
            logger.warning(
                "model health sweep: no MODEL_HEALTH_SWEEP_API_KEY or ADMIN_API_KEY — "
                "NOT RUNNING. This is a configuration gap, not a healthy catalog."
            )
            return {"ran": False, "reason": "no_api_key"}

        models = await _fetch_catalog(None, None)
        if not models:
            logger.warning("model health sweep: catalog returned no models — not running")
            return {"ran": False, "reason": "empty_catalog"}
        models = models[:MAX_MODELS_PER_RUN]

        port = os.environ.get("PORT", "8000")
        # Same reasoning as the admin route: never BASE_URL. That is the public
        # URL and routes back through the CDN, which turns a self-call into a
        # circular 502.
        base_url = os.environ.get("LIVE_TEST_BASE_URL") or f"http://localhost:{port}"

        semaphore = asyncio.Semaphore(
            int(getattr(Config, "MODEL_HEALTH_SWEEP_CONCURRENCY", DEFAULT_CONCURRENCY))
        )
        headers = {"Authorization": f"Bearer {key}", "X-Internal-Source": "live-test"}

        async with httpx.AsyncClient(base_url=base_url, headers=headers) as client:
            raw = await asyncio.gather(
                *[
                    _test_single_model(client, m, DEFAULT_TIMEOUT_S, semaphore, True)
                    for m in models
                ],
                return_exceptions=True,
            )

        results = [r for r in raw if not isinstance(r, BaseException)]
        crashed = len(raw) - len(results)

        summary = {
            "ran": True,
            "models": len(models),
            "probed": len(results),
            "crashed": crashed,
            "duration_s": round(time.monotonic() - started, 1),
        }

        write = bool(getattr(Config, "MODEL_HEALTH_SWEEP_WRITE", False))
        if write:
            from src.services.monitoring.model_health_sweep import persist_sweep_results

            summary["persistence"] = await persist_sweep_results(results)
            summary["mode"] = "write"
            logger.info("model health sweep (WRITE): %s", summary)
        else:
            summary["mode"] = "record_only"
            summary["would_mark_down"] = _dry_run_verdicts(results)
            logger.info(
                "model health sweep (RECORD-ONLY, nothing written): %s. "
                "Set MODEL_HEALTH_SWEEP_WRITE=true once these verdicts look right.",
                summary,
            )
        return summary

    except Exception as exc:  # noqa: BLE001 - monitoring must never break the app
        logger.error("model health sweep failed: %s", exc, exc_info=True)
        return {"ran": False, "error": str(exc)[:200]}


def _dry_run_verdicts(results: list) -> list[str]:
    """Which models this run classified as HARD failures, without writing.

    Deliberately reports the per-run classification rather than simulating the
    consecutive-failure streak: the streak lives in the database, and a
    record-only run must not touch it. So this is "hard-failed today", which is
    a *superset* of what would be marked down — it is the list worth reading
    before turning writes on, and it overstates rather than understates.
    """
    try:
        from src.services.monitoring.model_health_sweep import (
            _field,
            classify_probe_result,
        )
    except Exception:  # noqa: BLE001
        return []

    hard = []
    for r in results or []:
        try:
            outcome = classify_probe_result(
                _field(r, "status", ""), _field(r, "status_code"), _field(r, "error")
            )
            if outcome == "hard_fail":
                mid = _field(r, "model_id")
                if mid:
                    hard.append(str(mid))
        except Exception:  # noqa: BLE001
            continue
    return sorted(hard)
