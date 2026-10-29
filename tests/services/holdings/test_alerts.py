"""Tests for src.services.holdings.alerts -- ops alerts for the holdings
observation sweep. Redis, email and the DB are all faked; nothing here
sends mail or touches a network."""

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

import src.services.holdings.alerts as alerts
from src.config.config import Config
from src.db.holdings import HoldingsLookupError

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
WALLET = "0x742d35cc6634c0532925a3b844bc454e4438f44e"


class FakeRedis:
    """Just enough of redis-py for SET NX EX dedupe."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        self.ttls[key] = ex
        return True

    def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    alerts._reset_for_tests()
    monkeypatch.setattr(Config, "HOLDINGS_REWARDS_ENABLED", True)
    monkeypatch.setattr(Config, "HOLDINGS_SWEEP_STALE_HOURS", 8.0)
    monkeypatch.setattr(Config, "HOLDINGS_ALERT_COOLDOWN_HOURS", 12.0)
    monkeypatch.setattr(Config, "HOLDINGS_MIN_WALLET_AGE_DAYS", 3)
    yield
    alerts._reset_for_tests()


@pytest.fixture
def no_redis():
    with patch("src.services.holdings.alerts.get_redis_client", return_value=None):
        yield


@pytest.fixture
def redis():
    fake = FakeRedis()
    with patch("src.services.holdings.alerts.get_redis_client", return_value=fake):
        yield fake


@pytest.fixture
def mailer():
    """Records every email; one configured recipient."""
    sent: list[dict] = []

    def _send_email(to, subject, html, text=None, tags=None):  # noqa: ARG001
        sent.append({"to": to, "subject": subject, "html": html, "text": text})
        return MagicMock(sent=True)

    with (
        patch(
            "src.services.provider_alerting.resolve_ops_recipients",
            return_value=(["ops@example.com"], "env"),
        ),
        patch("src.services.email.send_email", side_effect=_send_email),
    ):
        yield sent


def _failed_sweep(**overrides):
    summary = {
        "taken_at": NOW.isoformat(),
        "tokens": 30,
        "wallets_considered": 5,
        "sweeps_recorded": 0,
        "skipped": {"too_new": 1, "incomplete_read": 5, "missing_price": 0, "error": 0},
        "failed_chains": {"137": 5},
    }
    summary.update(overrides)
    return summary


class TestRecordedNothing:
    def test_alerts_with_counts_and_chain_names(self, no_redis, mailer):
        assert alerts.alert_if_sweep_recorded_nothing(_failed_sweep()) is True

        assert len(mailer) == 1
        body = mailer[0]["text"]
        assert "considered 5 eligible wallet(s) and recorded none" in body
        assert "incomplete_read=5" in body
        assert "polygon (137)=5" in body
        assert "missing_price" not in body  # zero counts are noise

    def test_alert_never_carries_a_wallet_address_or_rpc_url(self, no_redis, mailer):
        alerts.alert_if_sweep_recorded_nothing(_failed_sweep(wallet=WALLET))
        sent = mailer[0]["text"] + mailer[0]["html"]
        assert WALLET not in sent
        assert "http" not in sent

    @pytest.mark.parametrize(
        "summary",
        [
            _failed_sweep(sweeps_recorded=1),
            _failed_sweep(wallets_considered=0),
            {"skipped": "disabled"},
            {"skipped": "no_tokens"},
            {},
        ],
        ids=["recorded-some", "nothing-eligible", "disabled", "no-tokens", "empty"],
    )
    def test_no_alert_when_the_sweep_did_its_job(self, no_redis, mailer, summary):
        assert alerts.alert_if_sweep_recorded_nothing(summary) is False
        assert mailer == []

    def test_no_alert_while_the_feature_is_off(self, no_redis, mailer, monkeypatch):
        monkeypatch.setattr(Config, "HOLDINGS_REWARDS_ENABLED", False)
        assert alerts.alert_if_sweep_recorded_nothing(_failed_sweep()) is False
        assert mailer == []


class TestDedupe:
    def test_redis_claim_sends_once_per_cooldown(self, redis, mailer):
        assert alerts.alert_if_sweep_recorded_nothing(_failed_sweep()) is True
        assert alerts.alert_if_sweep_recorded_nothing(_failed_sweep()) is False
        assert len(mailer) == 1
        key = "ops:alert:holdings:sweep_recorded_nothing"
        assert redis.ttls[key] == 12 * 3600

    def test_in_process_fallback_when_redis_is_down(self, no_redis, mailer):
        alerts.alert_if_sweep_recorded_nothing(_failed_sweep())
        alerts.alert_if_sweep_recorded_nothing(_failed_sweep())
        assert len(mailer) == 1

    def test_redis_errors_fall_back_rather_than_spam(self, mailer):
        broken = MagicMock()
        broken.set.side_effect = RuntimeError("redis down")
        with patch("src.services.holdings.alerts.get_redis_client", return_value=broken):
            alerts.alert_if_sweep_recorded_nothing(_failed_sweep())
            alerts.alert_if_sweep_recorded_nothing(_failed_sweep())
        assert len(mailer) == 1

    def test_the_cooldown_expires(self, no_redis, mailer):
        alerts.alert_if_sweep_recorded_nothing(_failed_sweep())
        with patch("src.services.holdings.alerts.time.time", return_value=10**12):
            alerts.alert_if_sweep_recorded_nothing(_failed_sweep())
        assert len(mailer) == 2

    def test_conditions_are_deduped_independently(self, redis, mailer):
        alerts._send(alerts.CONDITION_RECORDED_NOTHING, "a", ["x"])
        alerts._send(alerts.CONDITION_STALE, "b", ["y"])
        assert [m["subject"] for m in mailer] == ["a", "b"]

    def test_a_failed_delivery_releases_the_claim(self, redis):
        with (
            patch(
                "src.services.provider_alerting.resolve_ops_recipients",
                return_value=(["ops@example.com"], "env"),
            ),
            patch("src.services.email.send_email", return_value=MagicMock(sent=False)) as send,
        ):
            alerts.alert_if_sweep_recorded_nothing(_failed_sweep())
            alerts.alert_if_sweep_recorded_nothing(_failed_sweep())
        assert send.call_count == 2  # retried, not suppressed


class TestNoRecipient:
    def test_logs_one_error_instead_of_silence(self, no_redis, caplog):
        with (
            patch(
                "src.services.provider_alerting.resolve_ops_recipients", return_value=([], "none")
            ),
            patch("src.services.email.send_email") as send,
            caplog.at_level(logging.ERROR, logger="src.services.holdings.alerts"),
        ):
            assert alerts.alert_if_sweep_recorded_nothing(_failed_sweep()) is True
            alerts.alert_if_sweep_recorded_nothing(_failed_sweep())  # deduped

        send.assert_not_called()
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("OPS_ALERT_EMAIL" in r.getMessage() for r in errors)
        # One alert (its ERROR + the no-recipient ERROR), not one per call.
        assert len(errors) == 2


def _staleness(last_recorded, eligible=4, now=NOW):
    latest = (
        patch("src.db.holdings.get_latest_sweep_taken_at", side_effect=last_recorded)
        if isinstance(last_recorded, Exception)
        else patch("src.db.holdings.get_latest_sweep_taken_at", return_value=last_recorded)
    )
    with latest, patch("src.db.user_wallets.count_wallets_linked_before", return_value=eligible):
        return alerts.check_sweep_staleness(now)


class TestStaleness:
    def test_alerts_after_the_threshold(self, no_redis, mailer):
        status = _staleness(NOW - timedelta(hours=9))
        assert status["stale"] is True
        assert status["alerted"] is True
        assert status["hours_since_last_recorded"] == 9.0
        assert "9.0h" in mailer[0]["subject"]
        assert "4 eligible" in mailer[0]["text"]

    def test_quiet_inside_the_threshold(self, no_redis, mailer):
        status = _staleness(NOW - timedelta(hours=7))
        assert status["stale"] is False
        assert mailer == []

    def test_no_eligible_wallets_is_not_stale(self, no_redis, mailer):
        status = _staleness(NOW - timedelta(days=3), eligible=0)
        assert status["stale"] is False
        assert mailer == []

    def test_never_recorded_is_measured_from_when_the_watch_started(self, no_redis, mailer):
        """A freshly enabled feature must not alert before its first sweep
        had a chance -- but must alert if it never records anything."""
        first = _staleness(None, now=NOW)
        assert first["stale"] is False

        later = _staleness(None, now=NOW + timedelta(hours=9))
        assert later["stale"] is True
        assert "never" in mailer[0]["text"]

    def test_existing_staleness_alerts_on_the_first_check(self, no_redis, mailer):
        """The Oct 2026 incident: three days of nothing, then a deploy. The
        restart must not reset the clock when the DB already shows it."""
        status = _staleness(NOW - timedelta(days=3))
        assert status["stale"] is True
        assert len(mailer) == 1

    def test_a_failed_lookup_is_unknown_not_stale(self, no_redis, mailer):
        status = _staleness(HoldingsLookupError("db down"))
        assert status["stale"] is None
        assert mailer == []

    def test_a_failed_wallet_count_is_unknown_not_stale(self, no_redis, mailer):
        status = _staleness(NOW - timedelta(days=3), eligible=None)
        assert status["stale"] is None
        assert mailer == []

    def test_disabled_feature_never_checks(self, no_redis, mailer, monkeypatch):
        monkeypatch.setattr(Config, "HOLDINGS_REWARDS_ENABLED", False)
        with patch("src.db.holdings.get_latest_sweep_taken_at") as latest:
            status = alerts.check_sweep_staleness(NOW)
        latest.assert_not_called()
        assert status["stale"] is False
        assert mailer == []

    def test_repeat_checks_alert_once_per_cooldown(self, redis, mailer):
        _staleness(NOW - timedelta(hours=9))
        _staleness(NOW - timedelta(hours=10))
        assert len(mailer) == 1

    def test_eligibility_uses_the_wallet_age_gate(self, no_redis, mailer):
        with (
            patch("src.db.holdings.get_latest_sweep_taken_at", return_value=NOW),
            patch("src.db.user_wallets.count_wallets_linked_before", return_value=1) as count,
        ):
            alerts.check_sweep_staleness(NOW)
        count.assert_called_once_with(NOW - timedelta(days=3))


class TestAdminHealth:
    def test_reports_the_last_sweep_and_degraded(self, no_redis):
        record = {"ran_at": NOW.isoformat(), "summary": _failed_sweep()}
        with (
            patch("src.db.holdings.get_latest_sweep_taken_at", return_value=datetime.now(UTC)),
            patch("src.db.user_wallets.count_wallets_linked_before", return_value=5),
            patch(
                "src.services.ops.job_runs.get_job_runs",
                return_value={"holdings_snapshots": record},
            ),
        ):
            health = alerts.holdings_sweep_health()

        assert health["stale"] is False
        assert health["last_sweep"]["recorded_nothing"] is True
        assert health["last_sweep"]["failed_chains"] == {"137": 5}
        assert health["degraded"] is True

    def test_healthy_when_the_last_sweep_recorded(self, no_redis):
        record = {"ran_at": NOW.isoformat(), "summary": _failed_sweep(sweeps_recorded=5)}
        with (
            patch("src.db.holdings.get_latest_sweep_taken_at", return_value=datetime.now(UTC)),
            patch("src.db.user_wallets.count_wallets_linked_before", return_value=5),
            patch(
                "src.services.ops.job_runs.get_job_runs",
                return_value={"holdings_snapshots": record},
            ),
        ):
            health = alerts.holdings_sweep_health()
        assert health["degraded"] is False
