"""A 429 is not a model failure, and failure must never speed the prober up.

Production on 2026-09-15: 439,162 probes, 419,542 recorded "errors" (95%), and a
public GET /v1/status reporting major_outage at 30.3% uptime while customer
inference was fine. Two bugs compounded:

  1. ``is_success = status == SUCCESS`` — a 429 counted as an error, bumped
     consecutive_failures, and after 8 of them opened the circuit breaker, which
     the status page publishes as "offline".
  2. ``if not is_success and consecutive_failures > 1: interval = min(interval, 300)``
     — a throttled model got probed MORE often, producing more 429s. A feedback
     loop, not a monitor.

Every test here pins one half of that, plus the property that keeps the fix
honest: a REAL failure must still mark a model unhealthy.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from src.services.monitoring import intelligent_health_monitor as ihm
from src.services.monitoring.intelligent_health_monitor import (
    CircuitBreakerState,
    HealthCheckResult,
    HealthCheckStatus,
    IntelligentHealthMonitor,
    MonitoringTier,
    is_unmeasured_status,
    uptime_from_history,
)


@pytest.fixture
def monitor():
    return IntelligentHealthMonitor(redis_coordination=False)


@pytest.fixture
def no_jitter(monkeypatch):
    """Switch jitter off for assertions about the underlying interval rule.

    Jitter exists to make adjacent intervals deliberately incomparable, so a
    test that compares two of them is testing the randomness, not the rule.
    """
    from src.config.config import Config

    monkeypatch.setattr(Config, "HEALTH_PROBE_JITTER_FRACTION", 0.0)


def _result(status, provider="openai", model="openai/gpt-4.1", http=None, error=None):
    return HealthCheckResult(
        provider=provider,
        model=model,
        gateway=provider,
        status=status,
        response_time_ms=12.0,
        error_message=error,
        http_status_code=http,
        checked_at=datetime.now(UTC),
    )


class _Tracking:
    """Minimal supabase double that captures the row written back.

    Records the upsert payload for model_health_tracking and remembers whether
    an incident was ever written, which is the other thing a 429 must not do.
    """

    def __init__(self, current: dict):
        self.current = current
        self.upserted: dict | None = None
        self.incident_writes = 0
        self.history: list[dict] = []

    def table(self, name):
        outer = self

        class _Q:
            def __init__(self):
                self.name = name

            def select(self, *a, **k):
                return self

            def eq(self, *a, **k):
                return self

            def order(self, *a, **k):
                return self

            def limit(self, *a, **k):
                return self

            def maybe_single(self):
                return self

            def update(self, payload):
                if self.name == "model_health_incidents":
                    outer.incident_writes += 1
                return self

            def insert(self, payload):
                if self.name == "model_health_history":
                    outer.history.append(payload)
                if self.name == "model_health_incidents":
                    outer.incident_writes += 1
                return self

            def upsert(self, payload, **k):
                if self.name == "model_health_tracking":
                    outer.upserted = payload
                return self

            def execute(self):
                if self.name == "model_health_tracking":
                    return MagicMock(data=dict(outer.current))
                if self.name == "model_health_incidents":
                    return MagicMock(data=None)
                return MagicMock(data=[])

        return _Q()


async def _process(monkeypatch, monitor, result, current):
    client = _Tracking(current)
    monkeypatch.setattr("src.config.supabase_config.supabase", client)
    await monitor._process_health_check_result(result)
    return client


# ---------------------------------------------------------------------------
# 1. A 429 is not a model failure
# ---------------------------------------------------------------------------


class TestRateLimitIsNotFailure:
    @pytest.mark.asyncio
    async def test_429_does_not_increment_error_count(self, monitor, monkeypatch):
        client = await _process(
            monkeypatch,
            monitor,
            _result(HealthCheckStatus.RATE_LIMITED, http=429, error="Rate limit exceeded"),
            {"call_count": 10, "error_count": 3, "success_count": 7},
        )
        assert client.upserted["error_count"] == 3, "a 429 must not count as a model error"
        # The probe still happened, so the request volume stays visible.
        assert client.upserted["call_count"] == 11

    @pytest.mark.asyncio
    async def test_429_does_not_bump_the_failure_streak(self, monitor, monkeypatch):
        client = await _process(
            monkeypatch,
            monitor,
            _result(HealthCheckStatus.RATE_LIMITED, http=429),
            {"consecutive_failures": 2, "consecutive_successes": 0},
        )
        assert client.upserted["consecutive_failures"] == 2

    @pytest.mark.asyncio
    async def test_429_does_not_open_the_circuit_breaker(self, monitor, monkeypatch):
        """One short of the threshold, a 429 must not be the straw that trips it."""
        client = await _process(
            monkeypatch,
            monitor,
            _result(HealthCheckStatus.RATE_LIMITED, http=429),
            {"consecutive_failures": 7, "circuit_breaker_state": "closed"},
        )
        assert client.upserted["circuit_breaker_state"] == CircuitBreakerState.CLOSED.value

    @pytest.mark.asyncio
    async def test_429_does_not_open_an_incident(self, monitor, monkeypatch):
        client = await _process(
            monkeypatch,
            monitor,
            _result(HealthCheckStatus.RATE_LIMITED, http=429),
            {"consecutive_failures": 5},
        )
        assert client.incident_writes == 0

    @pytest.mark.asyncio
    async def test_429_does_not_move_last_failure_at(self, monitor, monkeypatch):
        client = await _process(
            monkeypatch,
            monitor,
            _result(HealthCheckStatus.RATE_LIMITED, http=429),
            {"last_failure_at": "2020-01-01T00:00:00+00:00"},
        )
        assert client.upserted["last_failure_at"] == "2020-01-01T00:00:00+00:00"

    @pytest.mark.asyncio
    async def test_429_is_still_recorded_verbatim(self, monitor, monkeypatch):
        """Not-a-failure is not the same as not-recorded. The condition must
        stay diagnosable, which is what lets the status page report it."""
        client = await _process(
            monkeypatch,
            monitor,
            _result(HealthCheckStatus.RATE_LIMITED, http=429, error="Rate limit exceeded"),
            {},
        )
        assert client.upserted["last_status"] == "rate_limited"
        assert client.upserted["last_error_message"] == "Rate limit exceeded"

    @pytest.mark.asyncio
    async def test_429_cannot_heal_an_open_breaker_either(self, monitor, monkeypatch):
        """Unmeasured is symmetric: it is not evidence of health, so it must not
        walk an OPEN breaker toward recovery."""
        client = await _process(
            monkeypatch,
            monitor,
            _result(HealthCheckStatus.RATE_LIMITED, http=429),
            {"circuit_breaker_state": "open", "consecutive_failures": 9},
        )
        assert client.upserted["circuit_breaker_state"] == CircuitBreakerState.OPEN.value


class TestUnauthorizedIsSurfaced:
    @pytest.mark.asyncio
    async def test_401_is_unmeasured_not_an_outage(self, monitor, monkeypatch):
        client = await _process(
            monkeypatch,
            monitor,
            _result(HealthCheckStatus.UNAUTHORIZED, http=401, error="Authentication failed"),
            {"error_count": 1, "consecutive_failures": 7},
        )
        assert client.upserted["error_count"] == 1
        assert client.upserted["consecutive_failures"] == 7
        assert client.upserted["circuit_breaker_state"] == CircuitBreakerState.CLOSED.value

    @pytest.mark.asyncio
    async def test_401_is_logged_distinctly_as_a_credential_problem(
        self, monitor, monkeypatch, caplog
    ):
        """It usually means a missing provider key. It must be findable in logs
        as that, not buried in a generic failure count."""
        with caplog.at_level("WARNING"):
            await _process(
                monkeypatch,
                monitor,
                _result(HealthCheckStatus.UNAUTHORIZED, http=401),
                {},
            )
        assert any(
            "UNAUTHORIZED" in r.message and "credential" in r.message for r in caplog.records
        ), "an auth failure must be reported as a gateway credential problem"


class TestRealFailuresStillCount:
    """The over-correction guard. If these go green-by-doing-nothing the fix has
    turned the monitor into a machine that can never report an outage."""

    @pytest.mark.asyncio
    async def test_5xx_still_increments_errors_and_the_streak(self, monitor, monkeypatch):
        client = await _process(
            monkeypatch,
            monitor,
            _result(HealthCheckStatus.ERROR, http=500, error="HTTP 500: boom"),
            {"error_count": 3, "consecutive_failures": 2},
        )
        assert client.upserted["error_count"] == 4
        assert client.upserted["consecutive_failures"] == 3

    @pytest.mark.asyncio
    async def test_repeated_real_failures_still_open_the_breaker(self, monitor, monkeypatch):
        client = await _process(
            monkeypatch,
            monitor,
            _result(HealthCheckStatus.ERROR, http=500, error="HTTP 500: boom"),
            {"consecutive_failures": 7, "circuit_breaker_state": "closed"},
        )
        assert client.upserted["circuit_breaker_state"] == CircuitBreakerState.OPEN.value

    @pytest.mark.asyncio
    async def test_timeout_still_counts_as_a_failure(self, monitor, monkeypatch):
        client = await _process(
            monkeypatch,
            monitor,
            _result(HealthCheckStatus.TIMEOUT, error="Request timeout after 60s"),
            {"error_count": 0, "consecutive_failures": 0},
        )
        assert client.upserted["error_count"] == 1
        assert client.upserted["consecutive_failures"] == 1

    @pytest.mark.asyncio
    async def test_not_found_still_counts_as_a_failure(self, monitor, monkeypatch):
        """A 404 for a model we DO serve is real evidence it is gone."""
        client = await _process(
            monkeypatch,
            monitor,
            _result(HealthCheckStatus.NOT_FOUND, http=404, error="Model not found"),
            {"error_count": 0, "consecutive_failures": 0},
        )
        assert client.upserted["error_count"] == 1
        assert client.upserted["consecutive_failures"] == 1

    @pytest.mark.asyncio
    async def test_a_real_failure_still_opens_an_incident(self, monitor, monkeypatch):
        client = await _process(
            monkeypatch,
            monitor,
            _result(HealthCheckStatus.ERROR, http=502, error="HTTP 502"),
            {"consecutive_failures": 3},
        )
        assert client.incident_writes >= 1


# ---------------------------------------------------------------------------
# 2. Back off — never accelerate
# ---------------------------------------------------------------------------


class TestBackoff:
    def test_backoff_grows_with_repeated_rate_limits(self, monitor):
        result = _result(HealthCheckStatus.RATE_LIMITED, http=429)
        intervals = [
            monitor._next_check_interval(MonitoringTier.CRITICAL, result) for _ in range(5)
        ]
        # Jitter can reorder two ADJACENT strikes (that is the point of jitter),
        # so the claim is growth across the run, not step-by-step monotonicity.
        assert intervals[-1] > intervals[0] * 2, f"repeated 429s must back off: {intervals}"

    def test_the_underlying_backoff_curve_is_monotonic(self, monitor):
        """Jitter-free, so this is the real shape of the curve."""
        curve = [monitor._backoff_seconds(n) for n in range(1, 10)]
        assert curve == sorted(curve)
        assert curve[0] < curve[-1]

    def test_failure_never_shortens_the_interval(self, monitor, no_jitter):
        """The exact inversion of the old min(interval, 300) rule.

        Jitter is switched off here: it exists precisely to make adjacent
        intervals incomparable, and this assertion is about the underlying rule.
        """
        rl = _result(HealthCheckStatus.RATE_LIMITED, http=429)
        for tier in MonitoringTier:
            m = IntelligentHealthMonitor(redis_coordination=False)
            ok = m._next_check_interval(tier, _result(HealthCheckStatus.SUCCESS))
            for _ in range(6):
                assert m._next_check_interval(tier, rl) >= ok

    def test_success_resets_the_backoff(self, monitor):
        rl = _result(HealthCheckStatus.RATE_LIMITED, http=429)
        for _ in range(4):
            monitor._next_check_interval(MonitoringTier.CRITICAL, rl)
        monitor._next_check_interval(MonitoringTier.CRITICAL, _result(HealthCheckStatus.SUCCESS))
        after = monitor._next_check_interval(MonitoringTier.CRITICAL, rl)
        first = IntelligentHealthMonitor(redis_coordination=False)._next_check_interval(
            MonitoringTier.CRITICAL, rl
        )
        assert after == pytest.approx(first, rel=0.5)

    def test_backoff_stays_inside_the_24h_measurement_window(self, monitor):
        """src/routes/status_page.py drops a measurement older than 24h. If
        backoff could exceed that, a throttled model would silently age out of
        "monitored" and the page would lose coverage instead of reporting it."""
        assert monitor._backoff_seconds(50) * 1.25 < 24 * 3600

    def test_every_tier_gets_at_least_one_probe_per_window(self, monitor):
        """Even at maximum backoff, every tier re-probes well inside 24h."""
        rl = _result(HealthCheckStatus.RATE_LIMITED, http=429)
        for tier in MonitoringTier:
            m = IntelligentHealthMonitor(redis_coordination=False)
            worst = max(m._next_check_interval(tier, rl) for _ in range(30))
            assert worst < 24 * 3600, f"{tier} can exceed the measurement window"

    def test_jitter_spreads_retries(self, monitor):
        """Without jitter every model behind one quota retries in lockstep and
        reproduces the burst that caused the throttling."""
        ok = _result(HealthCheckStatus.SUCCESS)
        values = {monitor._next_check_interval(MonitoringTier.STANDARD, ok) for _ in range(20)}
        assert len(values) > 1

    def test_minimum_interval_floor_applies_to_the_fastest_tier(self, monitor):
        """CRITICAL's raw 300s is below the floor; the floor must win."""
        from src.config.config import Config

        got = monitor._next_check_interval(
            MonitoringTier.CRITICAL, _result(HealthCheckStatus.SUCCESS)
        )
        assert got >= Config.HEALTH_PROBE_MIN_INTERVAL_SECONDS


class TestProviderCooldown:
    @pytest.mark.asyncio
    async def test_a_429_pauses_every_model_on_that_provider(self, monitor, monkeypatch):
        """The quota is shared, so probing a sibling model spends another slice
        of the same exhausted budget."""
        await _process(monkeypatch, monitor, _result(HealthCheckStatus.RATE_LIMITED, http=429), {})
        assert monitor._provider_is_cooling_down("openai")
        assert not monitor._provider_is_cooling_down("anthropic")

    @pytest.mark.asyncio
    async def test_cooled_down_providers_are_not_selected(self, monitor, monkeypatch):
        monitor._note_provider_throttled("openai")
        rows = [
            {"provider": "openai", "model": "openai/gpt-4.1", "gateway": "openai"},
            {"provider": "anthropic", "model": "anthropic/claude-sonnet-5", "gateway": "anthropic"},
        ]
        monkeypatch.setattr("src.config.supabase_config.supabase", _tracking_client(rows))
        monkeypatch.setattr("src.utils.provider_filter.is_provider_enabled", lambda s: True)
        monkeypatch.setattr(ihm, "_get_servable_model_ids", set)

        selected = await monitor._get_models_for_checking()

        assert [m["provider"] for m in selected] == ["anthropic"]

    @pytest.mark.asyncio
    async def test_a_success_clears_the_cooldown(self, monitor, monkeypatch):
        monitor._note_provider_throttled("openai")
        await _process(monkeypatch, monitor, _result(HealthCheckStatus.SUCCESS), {})
        assert not monitor._provider_is_cooling_down("openai")


class TestHourlyCap:
    @pytest.mark.asyncio
    async def test_a_model_is_not_probed_past_its_hourly_budget(self, monitor, monkeypatch):
        """A backstop independent of next_check_at: even if the schedule were
        reset to the past on every pass, the cap holds."""
        from src.config.config import Config

        for _ in range(Config.HEALTH_PROBE_MAX_PER_MODEL_PER_HOUR):
            monitor._record_probe_issued("openai", "openai/gpt-4.1")

        rows = [{"provider": "openai", "model": "openai/gpt-4.1", "gateway": "openai"}]
        monkeypatch.setattr("src.config.supabase_config.supabase", _tracking_client(rows))
        monkeypatch.setattr("src.utils.provider_filter.is_provider_enabled", lambda s: True)
        monkeypatch.setattr(ihm, "_get_servable_model_ids", set)

        assert await monitor._get_models_for_checking() == []

    @pytest.mark.asyncio
    async def test_a_model_under_budget_is_still_probed(self, monitor, monkeypatch):
        monitor._record_probe_issued("openai", "openai/gpt-4.1")
        rows = [{"provider": "openai", "model": "openai/gpt-4.1", "gateway": "openai"}]
        monkeypatch.setattr("src.config.supabase_config.supabase", _tracking_client(rows))
        monkeypatch.setattr("src.utils.provider_filter.is_provider_enabled", lambda s: True)
        monkeypatch.setattr(ihm, "_get_servable_model_ids", set)

        assert len(await monitor._get_models_for_checking()) == 1


# ---------------------------------------------------------------------------
# 3. Don't probe models we don't serve
# ---------------------------------------------------------------------------


def _tracking_client(rows):
    q = MagicMock()
    for m in ("select", "eq", "lte", "order", "limit", "update"):
        getattr(q, m).return_value = q
    q.execute.return_value = MagicMock(data=rows)
    client = MagicMock()
    client.table.return_value = q
    return client


class TestCatalogScope:
    @pytest.mark.asyncio
    async def test_models_outside_the_catalog_are_not_probed(self, monitor, monkeypatch):
        rows = [
            {"provider": "openai", "model": "openai/gpt-4.1", "gateway": "openai"},
            {"provider": "openai", "model": "openai/gpt-4-turbo-2024-04-09", "gateway": "openai"},
        ]
        monkeypatch.setattr("src.config.supabase_config.supabase", _tracking_client(rows))
        monkeypatch.setattr("src.utils.provider_filter.is_provider_enabled", lambda s: True)
        monkeypatch.setattr(ihm, "_get_servable_model_ids", lambda: {"openai/gpt-4.1"})

        selected = await monitor._get_models_for_checking()

        assert [m["model"] for m in selected] == ["openai/gpt-4.1"]

    def test_a_bare_id_matches_its_prefixed_catalog_entry(self):
        """The two tables disagree about the gateway prefix. Exact-match-only
        let every mismatched row through — and those rows are exactly the 30
        that answered "Model not found" on every probe."""
        row = {"provider": "openai", "model": "gpt-4.1", "gateway": "openai"}
        assert ihm._is_in_catalog(row, {"openai/gpt-4.1"})

    def test_a_prefixed_id_matches_its_bare_catalog_entry(self):
        row = {"provider": "openai", "model": "openai/gpt-4.1", "gateway": "openai"}
        assert ihm._is_in_catalog(row, {"gpt-4.1"})

    def test_a_genuinely_absent_model_does_not_match(self):
        row = {"provider": "openai", "model": "openai/gpt-4-turbo", "gateway": "openai"}
        assert not ihm._is_in_catalog(row, {"openai/gpt-4.1"})


class TestOrphanPrune:
    @pytest.mark.asyncio
    async def test_orphaned_rows_are_disabled(self, monitor, monkeypatch):
        rows = [
            {"provider": "openai", "model": "openai/gpt-4.1", "gateway": "openai"},
            {"provider": "openai", "model": "openai/gpt-4-turbo", "gateway": "openai"},
        ]
        client = _tracking_client(rows)
        monkeypatch.setattr("src.config.supabase_config.supabase", client)
        monkeypatch.setattr(ihm, "_get_servable_model_ids", lambda: {"openai/gpt-4.1"})

        summary = await monitor.prune_orphaned_tracking_rows()

        assert summary["scanned"] == 2
        assert summary["orphaned"] == 1
        assert summary["disabled"] == 1

    @pytest.mark.asyncio
    async def test_dry_run_writes_nothing(self, monitor, monkeypatch):
        rows = [{"provider": "openai", "model": "openai/gpt-4-turbo", "gateway": "openai"}]
        client = _tracking_client(rows)
        monkeypatch.setattr("src.config.supabase_config.supabase", client)
        monkeypatch.setattr(ihm, "_get_servable_model_ids", lambda: {"openai/gpt-4.1"})

        summary = await monitor.prune_orphaned_tracking_rows(dry_run=True)

        assert summary["orphaned"] == 1
        assert summary["disabled"] == 0

    @pytest.mark.asyncio
    async def test_an_unavailable_catalog_prunes_nothing(self, monitor, monkeypatch):
        """An empty scope means the lookup failed, not that we sell nothing.
        Disabling every row on that reading would stop all monitoring."""
        rows = [{"provider": "openai", "model": "openai/gpt-4.1", "gateway": "openai"}]
        monkeypatch.setattr("src.config.supabase_config.supabase", _tracking_client(rows))
        monkeypatch.setattr(ihm, "_get_servable_model_ids", set)

        summary = await monitor.prune_orphaned_tracking_rows()

        assert summary["disabled"] == 0
        assert summary["skipped_reason"] == "catalog_scope_unavailable"


# ---------------------------------------------------------------------------
# 4. Uptime arithmetic
# ---------------------------------------------------------------------------


class TestUptimeExcludesUnmeasured:
    def test_throttled_probes_leave_the_denominator(self):
        """Nine 429s and one success is 100% of what was measured, not 10%."""
        rows = [{"status": "rate_limited"}] * 9 + [{"status": "success"}]
        assert uptime_from_history(rows) == 100.0

    def test_real_failures_still_drag_uptime_down(self):
        rows = [{"status": "error"}] * 3 + [{"status": "success"}]
        assert uptime_from_history(rows) == 25.0

    def test_nothing_measured_is_not_zero_percent(self):
        assert uptime_from_history([{"status": "rate_limited"}]) == 100.0

    def test_empty_history(self):
        assert uptime_from_history([]) == 100.0
        assert uptime_from_history(None) == 100.0


class TestSweepWriterAgrees:
    """``model_health_tracking`` has TWO writers: the live prober, and
    ``record_model_call`` (the 6-hourly sweep in .github/workflows/
    model-health-sweep.yml, plus the passive monitor). Both had the same
    ``status != "success" -> error`` bug. A column that means "errors" from one
    writer and "errors plus throttling" from the other is the drift that made
    419,542 throttled probes read as a 95% model error rate."""

    def _client(self, existing):
        captured = {}

        class _Q:
            def select(self, *a, **k):
                return self

            def eq(self, *a, **k):
                return self

            def upsert(self, payload, **k):
                captured["payload"] = payload
                return self

            def execute(self):
                return MagicMock(data=existing)

        client = MagicMock()
        client.table.return_value = _Q()
        return client, captured

    def _record(self, monkeypatch, status, existing):
        from src.db import model_health as mh

        client, captured = self._client(existing)
        monkeypatch.setattr(mh, "get_supabase_client", lambda: client)
        mh.record_model_call("openai", "openai/gpt-4.1", 10.0, status)
        return captured.get("payload", {})

    @pytest.mark.parametrize("status", ["rate_limited", "unauthorized"])
    def test_unmeasured_statuses_are_not_errors(self, monkeypatch, status):
        existing = [
            {
                "call_count": 5,
                "success_count": 2,
                "error_count": 3,
                "average_response_time_ms": 10.0,
            }
        ]
        payload = self._record(monkeypatch, status, existing)
        assert payload["error_count"] == 3
        assert payload["call_count"] == 6

    @pytest.mark.parametrize("status", ["error", "timeout", "not_found", "provider_error"])
    def test_real_failures_are_still_errors(self, monkeypatch, status):
        existing = [
            {
                "call_count": 5,
                "success_count": 2,
                "error_count": 3,
                "average_response_time_ms": 10.0,
            }
        ]
        payload = self._record(monkeypatch, status, existing)
        assert payload["error_count"] == 4

    def test_first_ever_row_agrees_too(self, monkeypatch):
        """The new-record branch had its own copy of the same expression."""
        assert self._record(monkeypatch, "rate_limited", [])["error_count"] == 0
        assert self._record(monkeypatch, "error", [])["error_count"] == 1
        assert self._record(monkeypatch, "success", [])["error_count"] == 0


class TestIsUnmeasuredStatus:
    @pytest.mark.parametrize("status", ["rate_limited", "unauthorized"])
    def test_unmeasured(self, status):
        assert is_unmeasured_status(status)

    @pytest.mark.parametrize("status", ["success", "error", "timeout", "not_found", "", None])
    def test_measured(self, status):
        assert not is_unmeasured_status(status)

    def test_accepts_the_enum_too(self):
        assert is_unmeasured_status(HealthCheckStatus.RATE_LIMITED)
        assert not is_unmeasured_status(HealthCheckStatus.ERROR)
