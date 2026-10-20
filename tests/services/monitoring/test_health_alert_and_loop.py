"""Health alert denominator/dedupe, and the monitor's DB calls staying off the loop.

Production (2026-10-19): the alert divided 56 healthy models by the 451-row
legacy openrouter_models table and reported "12.4% healthy" 542 times in 17
hours, while the monitor's synchronous Supabase calls stalled the API's event
loop for up to 108 s.
"""

import threading
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from src.services.monitoring import intelligent_health_monitor as ihm
from src.services.monitoring.intelligent_health_monitor import (
    HealthCheckResult,
    HealthCheckStatus,
    IntelligentHealthMonitor,
)


@pytest.fixture
def monitor():
    return IntelligentHealthMonitor(redis_coordination=False)


@pytest.fixture
def sentry(monkeypatch):
    capture = MagicMock()
    monkeypatch.setattr("sentry_sdk.capture_message", capture)
    return capture


def _chain_client(execute):
    """A Supabase-like client where every builder method returns the query."""
    q = MagicMock()
    for m in (
        "select",
        "eq",
        "lte",
        "gte",
        "order",
        "limit",
        "maybe_single",
        "upsert",
        "insert",
        "update",
    ):
        getattr(q, m).return_value = q
    q.execute.side_effect = execute
    client = MagicMock()
    client.table.return_value = q
    client.rpc.return_value = q
    return client


class TestAlertDedupe:
    @pytest.mark.asyncio
    async def test_unchanged_alert_is_sent_once(self, monitor, sentry):
        for _ in range(5):
            await monitor._check_health_threshold_and_alert(87, 56, 31, 64.4)
        assert sentry.call_count == 1
        message = sentry.call_args.args[0]
        assert "56/87" in message
        assert "#1094" not in message

    @pytest.mark.asyncio
    async def test_realerts_when_health_moves(self, monitor, sentry):
        await monitor._check_health_threshold_and_alert(100, 60, 40, 60.0)
        await monitor._check_health_threshold_and_alert(100, 62, 38, 62.0)  # +2 pts: quiet
        await monitor._check_health_threshold_and_alert(100, 50, 50, 50.0)  # -10 pts: alert
        assert sentry.call_count == 2

    @pytest.mark.asyncio
    async def test_realerts_when_severity_changes(self, monitor, sentry):
        await monitor._check_health_threshold_and_alert(100, 87, 13, 87.0)  # error
        await monitor._check_health_threshold_and_alert(100, 84, 16, 84.0)  # critical
        assert [c.kwargs["level"] for c in sentry.call_args_list] == ["error", "critical"]

    @pytest.mark.asyncio
    async def test_repeats_after_an_hour(self, monitor, sentry, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(ihm.time, "monotonic", lambda: clock[0])
        await monitor._check_health_threshold_and_alert(100, 50, 50, 50.0)
        clock[0] += ihm.HEALTH_ALERT_REPEAT_SECONDS - 1
        await monitor._check_health_threshold_and_alert(100, 50, 50, 50.0)
        clock[0] += 2
        await monitor._check_health_threshold_and_alert(100, 50, 50, 50.0)
        assert sentry.call_count == 2

    @pytest.mark.asyncio
    async def test_recovery_resets_so_next_degradation_alerts(self, monitor, sentry):
        await monitor._check_health_threshold_and_alert(100, 50, 50, 50.0)
        await monitor._check_health_threshold_and_alert(100, 95, 5, 95.0)  # healthy
        await monitor._check_health_threshold_and_alert(100, 50, 50, 50.0)
        assert sentry.call_count == 2


class TestAlertDenominator:
    @pytest.mark.asyncio
    async def test_alert_uses_measured_models_not_catalog_rows(self, monitor, monkeypatch):
        tracked = [
            {"provider": "openai", "model": "openai/a", "last_status": "success"},
            {"provider": "openai", "model": "openai/b", "last_status": "error"},
        ]

        def execute():
            # Every count query (openrouter_models, providers) says 451.
            return MagicMock(data=tracked, count=451)

        monkeypatch.setattr("src.config.supabase_config.supabase", _chain_client(execute))
        monkeypatch.setattr("src.services.simple_health_cache.simple_health_cache", MagicMock())
        # Skip the untracked-gateway section: it looks up live catalogs.
        monkeypatch.setattr("src.services.monitoring.gateway_health_service.GATEWAY_CONFIG", {})
        seen = {}

        async def fake_alert(**kwargs):
            seen.update(kwargs)

        monkeypatch.setattr(monitor, "_check_health_threshold_and_alert", fake_alert)

        await monitor._publish_health_to_cache()

        assert seen["total_models"] == 2
        assert seen["healthy_models"] == 1
        assert seen["unhealthy_models"] == 1


class TestDbCallsOffTheLoop:
    @pytest.mark.asyncio
    async def test_process_result_db_calls_run_in_threads(self, monitor, monkeypatch):
        loop_thread = threading.get_ident()
        call_threads: list[int] = []

        def execute():
            call_threads.append(threading.get_ident())
            return MagicMock(data={}, count=0)

        monkeypatch.setattr("src.config.supabase_config.supabase", _chain_client(execute))
        result = HealthCheckResult(
            provider="openai",
            model="openai/a",
            gateway="openai",
            status=HealthCheckStatus.SUCCESS,
            response_time_ms=120.0,
            error_message=None,
            http_status_code=200,
            checked_at=datetime.now(UTC),
        )

        await monitor._process_health_check_result(result)

        # read + upsert + history insert + incident resolution, at least
        assert len(call_threads) >= 3
        assert loop_thread not in call_threads

    @pytest.mark.asyncio
    async def test_uptime_aggregation_runs_in_threads(self, monitor, monkeypatch):
        loop_thread = threading.get_ident()
        call_threads: list[int] = []
        rows = [{"provider": "openai", "model": "openai/a", "gateway": "openai"}]

        def execute():
            call_threads.append(threading.get_ident())
            return MagicMock(data=rows, count=1)

        monkeypatch.setattr("src.config.supabase_config.supabase", _chain_client(execute))

        await monitor._aggregate_hourly_metrics()

        # tracked list + 3 history windows + 1 update
        assert len(call_threads) == 5
        assert loop_thread not in call_threads
