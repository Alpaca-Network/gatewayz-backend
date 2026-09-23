"""The sweep runs on a schedule, and writes nothing until told to.

#2367. `/v1/models` hides models marked `health_status == 'down'` and the gate
is enabled by default -- but the only thing that could SET that flag was an
admin endpoint behind an opt-in query param. The gate was armed and nothing
ever pulled the trigger.

Measured 2026-09-23: `health_status` was `unknown` -- never evaluated -- on 30
of the 34 advertised models our own status surface published as down, and 8
models were advertised as servable with pricing while measuring 0% uptime.

Hiding a model asserts it does not exist, and that has been got wrong before:
nine live flagship models were hidden after a single bad window. So the
properties pinned here are mostly about NOT acting:

* both flags default off
* enabled + write-off runs the sweep and writes nothing
* a missing API key refuses to run and says so, rather than reporting a clean
  sweep of zero models
* nothing it does can raise
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import src.services.monitoring.scheduled_health_sweep as sweep


class _Result:
    def __init__(self, model_id, status, status_code=None, error=None):
        self.model_id = model_id
        self.status = status
        self.status_code = status_code
        self.error = error
        self.gateway = "openai"
        self.provider = "openai"
        self.latency_ms = 10


def _patch_probe(results):
    """Stand in for the catalog fetch and the per-model probe."""
    models = [{"id": r.model_id, "provider_slug": "openai"} for r in results]

    async def _fetch(*_a, **_k):
        return models

    async def _test(client, model, *_a, **_k):
        return next(r for r in results if r.model_id == model["id"])

    return (
        patch("src.routes.live_model_test._fetch_catalog", _fetch),
        patch("src.routes.live_model_test._test_single_model", _test),
    )


RESULTS = [
    _Result("openai/gpt-4o", "pass"),
    _Result("openai/gpt-5-codex", "fail", 404, "Model 'gpt-5-codex' not found"),
    _Result("openai/gpt-4-turbo", "fail", 502, "Provider returned an error"),
]


def test_both_flags_default_off():
    # Read from the environment default rather than a live Config instance, so
    # this pins the SHIPPED default and not whatever the test env exports.
    import os

    assert os.environ.get("MODEL_HEALTH_SWEEP_ENABLED", "false").lower() == "false"
    assert os.environ.get("MODEL_HEALTH_SWEEP_WRITE", "false").lower() == "false"


@pytest.mark.asyncio
async def test_a_missing_key_refuses_to_run_rather_than_reporting_zero():
    # A sweep that silently does nothing is worse than no sweep: it looks
    # scheduled and reports success.
    with patch.object(sweep, "_sweep_api_key", lambda: None):
        out = await sweep.run_scheduled_health_sweep()
    assert out["ran"] is False
    assert out["reason"] == "no_api_key"


@pytest.mark.asyncio
async def test_record_only_writes_nothing():
    p1, p2 = _patch_probe(RESULTS)
    with (
        patch.object(sweep, "_sweep_api_key", lambda: "k"),
        p1,
        p2,
        patch(
            "src.services.monitoring.model_health_sweep.persist_sweep_results",
            new=AsyncMock(),
        ) as persist,
    ):
        out = await sweep.run_scheduled_health_sweep()
    assert out["mode"] == "record_only"
    assert not persist.called, "record-only mode wrote to the catalog"


@pytest.mark.asyncio
async def test_record_only_still_reports_what_it_would_have_marked():
    # The whole point of the mode: evidence before the decision.
    p1, p2 = _patch_probe(RESULTS)
    with patch.object(sweep, "_sweep_api_key", lambda: "k"), p1, p2:
        out = await sweep.run_scheduled_health_sweep()
    would = out["would_mark_down"]
    assert "openai/gpt-5-codex" in would, "a 404 is hard evidence and must be reported"
    assert "openai/gpt-4-turbo" not in would, "a 5xx is NEVER proof a model is dead"
    assert "openai/gpt-4o" not in would


@pytest.mark.asyncio
async def test_write_mode_persists():
    p1, p2 = _patch_probe(RESULTS)
    with (
        patch.object(sweep, "_sweep_api_key", lambda: "k"),
        p1,
        p2,
        patch(
            "src.services.monitoring.model_health_sweep.persist_sweep_results",
            new=AsyncMock(return_value={"marked_down": 1}),
        ) as persist,
    ):
        with patch("src.config.config.Config.MODEL_HEALTH_SWEEP_WRITE", True):
            out = await sweep.run_scheduled_health_sweep()
    assert persist.called
    assert out["mode"] == "write"


@pytest.mark.asyncio
async def test_an_empty_catalog_does_not_read_as_everything_being_dead():
    async def _empty(*_a, **_k):
        return []

    with (
        patch.object(sweep, "_sweep_api_key", lambda: "k"),
        patch("src.routes.live_model_test._fetch_catalog", _empty),
    ):
        out = await sweep.run_scheduled_health_sweep()
    assert out["ran"] is False
    assert out["reason"] == "empty_catalog"


@pytest.mark.asyncio
async def test_it_never_raises():
    # It runs on the event loop from app startup. A monitoring job must not be
    # able to take the app with it.
    async def _boom(*_a, **_k):
        raise RuntimeError("catalog exploded")

    with (
        patch.object(sweep, "_sweep_api_key", lambda: "k"),
        patch("src.routes.live_model_test._fetch_catalog", _boom),
    ):
        out = await sweep.run_scheduled_health_sweep()
    assert out["ran"] is False
    assert "error" in out


def test_the_scheduler_is_a_no_op_while_disabled():
    from src.services import scheduled_sync

    with patch("src.config.config.Config.MODEL_HEALTH_SWEEP_ENABLED", False):
        scheduled_sync.start_model_health_sweep_scheduler()
    assert scheduled_sync._model_health_sweep_scheduler is None
