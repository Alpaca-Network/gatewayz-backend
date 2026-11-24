"""Tests for src.services.holdings.alerts -- deduped ops-alert delivery
(``_send``), which the delegated-staking alerts reuse. Redis and email are
faked; nothing here sends mail or touches a network."""

import logging
from unittest.mock import MagicMock, patch

import pytest

import src.services.holdings.alerts as alerts
from src.config.config import Config


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
    monkeypatch.setattr(Config, "HOLDINGS_ALERT_COOLDOWN_HOURS", 12.0)
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


CONDITION = "test_condition"


def _alert() -> bool:
    return alerts._send(CONDITION, "[Gatewayz] test alert", ["line one", "line two"])


class TestDedupe:
    def test_redis_claim_sends_once_per_cooldown(self, redis, mailer):
        assert _alert() is True
        assert _alert() is False
        assert len(mailer) == 1
        key = f"ops:alert:holdings:{CONDITION}"
        assert redis.ttls[key] == 12 * 3600

    def test_in_process_fallback_when_redis_is_down(self, no_redis, mailer):
        _alert()
        _alert()
        assert len(mailer) == 1

    def test_redis_errors_fall_back_rather_than_spam(self, mailer):
        broken = MagicMock()
        broken.set.side_effect = RuntimeError("redis down")
        with patch("src.services.holdings.alerts.get_redis_client", return_value=broken):
            _alert()
            _alert()
        assert len(mailer) == 1

    def test_the_cooldown_expires(self, no_redis, mailer):
        _alert()
        with patch("src.services.holdings.alerts.time.time", return_value=10**12):
            _alert()
        assert len(mailer) == 2

    def test_conditions_are_deduped_independently(self, redis, mailer):
        alerts._send("condition_a", "a", ["x"])
        alerts._send("condition_b", "b", ["y"])
        assert [m["subject"] for m in mailer] == ["a", "b"]

    def test_a_failed_delivery_releases_the_claim(self, redis):
        with (
            patch(
                "src.services.provider_alerting.resolve_ops_recipients",
                return_value=(["ops@example.com"], "env"),
            ),
            patch("src.services.email.send_email", return_value=MagicMock(sent=False)) as send,
        ):
            _alert()
            _alert()
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
            assert _alert() is True
            _alert()  # deduped

        send.assert_not_called()
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("OPS_ALERT_EMAIL" in r.getMessage() for r in errors)
        # One alert (its ERROR + the no-recipient ERROR), not one per call.
        assert len(errors) == 2
