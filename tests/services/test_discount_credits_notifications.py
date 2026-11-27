"""Tests for src/services/discount_credits/notifications.py -- one email when
billing falls back to the paid balance because discount credits ran out or
the daily cap was reached.

The email provider is mocked in every test: an autouse guard makes any HTTP
call from src/services/email.py fail the test, and the send function itself
is replaced with a mock. Redis is an in-memory fake; the balance and the
account are mocked. Nothing here sends an email or calls a provider."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.config.config import Config
from src.services import email as email_service
from src.services.discount_credits import billing
from src.services.discount_credits import notifications as notify
from src.services.email import EmailResult

USER_ID = 7
TODAY = date(2026, 11, 27)
REQ = "6f9b1f0e-3c2a-4d5e-9b8a-1c2d3e4f5a6b"
USER = {"id": USER_ID, "email": "someone@example.com"}


class FakeRedis:
    """The two calls the module makes: SET NX EX and EXISTS."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        self.ttls[key] = ex
        return True

    def exists(self, key):
        return 1 if key in self.store else 0


def _row(**fields):
    base = {
        "user_id": USER_ID,
        "balance_usd": "2.000000",
        "lifetime_granted_usd": "5.000000",
        "daily_cap_usd": "50.000000",
        "cap_day": TODAY.isoformat(),
        "cap_used_usd": "3.000000",
    }
    return {**base, **fields}


DEPLETED = _row(balance_usd="0.000000")
CAPPED = _row(daily_cap_usd="10.000000", cap_used_usd="10.000000")


@pytest.fixture(autouse=True)
def _no_real_email(monkeypatch):
    """Any real HTTP call to the email provider fails the test."""

    def _boom(*args, **kwargs):
        raise AssertionError("a test tried to call the real email provider")

    monkeypatch.setattr(email_service.httpx, "post", _boom)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    notify._quiet_until.clear()
    yield
    notify._quiet_until.clear()


@pytest.fixture
def enabled():
    with patch.object(Config, "VAULT_DISCOUNT_NOTIFY_EMAIL_ENABLED", True):
        yield


@pytest.fixture
def env():
    """Redis, the balance, the account and the send function, all mocked."""
    redis_client = FakeRedis()
    send = MagicMock(return_value=EmailResult(sent=True, id="test-id"))
    balance = MagicMock(return_value=DEPLETED)
    user = MagicMock(return_value=dict(USER))
    with (
        patch("src.config.redis_config.get_redis_client", return_value=redis_client),
        patch("src.db.inference_discount.get_balance", balance),
        patch("src.db.users.get_user_by_id", user),
        patch("src.services.email.send_email", send),
        patch.object(notify, "_today", return_value=TODAY),
    ):
        yield {"redis": redis_client, "send": send, "balance": balance, "user": user}


# --- classify -----------------------------------------------------------


class TestClassify:
    def test_empty_balance_is_depleted(self):
        assert notify.classify(DEPLETED, TODAY) == notify.EVENT_DEPLETED

    def test_cap_used_up_today_is_cap_reached(self):
        assert notify.classify(CAPPED, TODAY) == notify.EVENT_DAILY_CAP

    def test_use_above_a_lowered_cap_is_cap_reached(self):
        row = _row(daily_cap_usd="5", cap_used_usd="8")
        assert notify.classify(row, TODAY) == notify.EVENT_DAILY_CAP

    def test_empty_balance_wins_over_the_cap(self):
        row = _row(balance_usd="0", daily_cap_usd="10", cap_used_usd="10")
        assert notify.classify(row, TODAY) == notify.EVENT_DEPLETED

    def test_use_counted_on_an_earlier_day_is_not_today(self):
        row = _row(daily_cap_usd="10", cap_used_usd="10", cap_day="2026-11-26")
        assert notify.classify(row, TODAY) is None

    def test_room_left_on_both_is_not_an_event(self):
        # A short draw caused by other in-flight requests' holds.
        assert notify.classify(_row(), TODAY) is None

    def test_a_cap_of_zero_is_the_users_choice_not_an_event(self):
        row = _row(daily_cap_usd="0", cap_used_usd="0")
        assert notify.classify(row, TODAY) is None

    @pytest.mark.parametrize("row", [None, {}, _row(lifetime_granted_usd="0", balance_usd="0")])
    def test_no_row_or_never_granted_is_not_an_event(self, row):
        assert notify.classify(row, TODAY) is None


# --- process ------------------------------------------------------------


class TestProcess:
    def test_depleted_sends_one_email(self, env):
        assert notify.process(USER_ID) == notify.EVENT_DEPLETED
        env["send"].assert_called_once()
        to, subject, html = env["send"].call_args.args
        assert to == USER["email"]
        assert subject == "You are out of discount credits"
        assert "discount credits" in html
        assert env["send"].call_args.kwargs["tags"] == [
            {"name": "category", "value": "discount_credits_credits_depleted"}
        ]

    def test_cap_reached_sends_one_email(self, env):
        env["balance"].return_value = CAPPED
        assert notify.process(USER_ID) == notify.EVENT_DAILY_CAP
        _, subject, html = env["send"].call_args.args
        assert subject == "You reached today's discount limit"
        assert "$10.00" in html

    def test_same_event_same_day_is_sent_once(self, env):
        notify.process(USER_ID)
        notify._quiet_until.clear()  # another worker, same day
        assert notify.process(USER_ID) is None
        assert env["send"].call_count == 1

    def test_cap_is_sent_again_on_the_next_day(self, env):
        env["balance"].return_value = CAPPED
        notify.process(USER_ID)
        tomorrow = date(2026, 11, 28)
        with patch.object(notify, "_today", return_value=tomorrow):
            env["balance"].return_value = _row(
                daily_cap_usd="10", cap_used_usd="10", cap_day=tomorrow.isoformat()
            )
            assert notify.process(USER_ID) == notify.EVENT_DAILY_CAP
        assert env["send"].call_count == 2

    def test_an_account_that_stays_empty_is_not_emailed_every_day(self, env):
        notify.process(USER_ID)
        with patch.object(notify, "_today", return_value=date(2026, 11, 28)):
            assert notify.process(USER_ID) is None
        assert env["send"].call_count == 1

    def test_a_new_grant_that_runs_out_is_a_new_event(self, env):
        notify.process(USER_ID)
        with patch.object(notify, "_today", return_value=date(2026, 12, 3)):
            env["balance"].return_value = _row(balance_usd="0", lifetime_granted_usd="9.5")
            assert notify.process(USER_ID) == notify.EVENT_DEPLETED
        assert env["send"].call_count == 2

    def test_a_new_grant_that_runs_out_the_same_day_is_not_sent_twice(self, env):
        notify.process(USER_ID)
        env["balance"].return_value = _row(balance_usd="0", lifetime_granted_usd="9.5")
        assert notify.process(USER_ID) is None
        assert env["send"].call_count == 1

    def test_dedupe_keys_expire(self, env):
        notify.process(USER_ID)
        ttls = env["redis"].ttls
        day_key = f"discount_credits:notify:credits_depleted:{USER_ID}:{TODAY.isoformat()}"
        assert ttls[day_key] == 2 * 24 * 3600
        assert all(ttl and ttl > 0 for ttl in ttls.values())

    def test_both_events_handled_today_skips_the_balance_read(self, env):
        for event in notify.EVENTS:
            env["redis"].set(f"discount_credits:notify:{event}:{USER_ID}:{TODAY.isoformat()}", "1")
        assert notify.process(USER_ID) is None
        env["balance"].assert_not_called()
        env["send"].assert_not_called()

    def test_no_event_sends_nothing_and_claims_nothing(self, env):
        env["balance"].return_value = _row()
        assert notify.process(USER_ID) is None
        env["send"].assert_not_called()
        assert env["redis"].store == {}

    def test_no_redis_sends_nothing(self, env):
        with patch("src.config.redis_config.get_redis_client", return_value=None):
            assert notify.process(USER_ID) is None
        env["send"].assert_not_called()

    def test_redis_error_sends_nothing_and_never_raises(self, env):
        broken = MagicMock()
        broken.exists.side_effect = ConnectionError("down")
        with patch("src.config.redis_config.get_redis_client", return_value=broken):
            assert notify.process(USER_ID) is None
        env["send"].assert_not_called()

    @pytest.mark.parametrize(
        "address", [None, "", "not-an-email", "x@privy.user", "noemail+1@privy.placeholder"]
    )
    def test_no_deliverable_address_sends_nothing(self, env, address):
        env["user"].return_value = {"id": USER_ID, "email": address}
        assert notify.process(USER_ID) is None
        env["send"].assert_not_called()

    def test_a_failed_send_is_logged_not_raised(self, env):
        env["send"].return_value = EmailResult(sent=False, error="not_configured")
        assert notify.process(USER_ID) is None
        env["send"].assert_called_once()


# --- notify_after_draw (the billing hook) ---------------------------------


class TestNotifyAfterDraw:
    def test_off_by_default(self):
        assert Config.VAULT_DISCOUNT_NOTIFY_EMAIL_ENABLED is False

    def test_off_does_nothing(self):
        with (
            patch.object(Config, "VAULT_DISCOUNT_NOTIFY_EMAIL_ENABLED", False),
            patch.object(notify, "_dispatch") as dispatch,
            patch("src.config.redis_config.get_redis_client") as redis_client,
        ):
            notify.notify_after_draw(USER_ID, Decimal("0.99"), Decimal("0"))
        dispatch.assert_not_called()
        redis_client.assert_not_called()

    def test_a_full_draw_is_not_a_fallback(self, enabled):
        with patch.object(notify, "_dispatch") as dispatch:
            notify.notify_after_draw(USER_ID, Decimal("0.99"), Decimal("0.99"))
        dispatch.assert_not_called()

    def test_a_short_draw_starts_one_check(self, enabled):
        with patch.object(notify, "_dispatch") as dispatch:
            notify.notify_after_draw(USER_ID, Decimal("0.99"), Decimal("0.5"))
        dispatch.assert_called_once_with(USER_ID)

    def test_a_burst_of_short_draws_starts_one_check(self, enabled):
        with patch.object(notify, "_dispatch") as dispatch:
            for _ in range(20):
                notify.notify_after_draw(USER_ID, Decimal("0.99"), Decimal("0"))
        dispatch.assert_called_once_with(USER_ID)

    @pytest.mark.parametrize("user_id", [None, "7", True])
    def test_a_bad_user_id_does_nothing(self, enabled, user_id):
        with patch.object(notify, "_dispatch") as dispatch:
            notify.notify_after_draw(user_id, Decimal("0.99"), Decimal("0"))
        dispatch.assert_not_called()

    def test_never_raises(self, enabled):
        with patch.object(notify, "_dispatch", side_effect=RuntimeError("no threads")):
            notify.notify_after_draw(USER_ID, Decimal("0.99"), Decimal("0"))

    def test_end_to_end_on_a_thread_sends_once(self, enabled, env):
        started = []

        def run_inline(user_id):
            started.append(user_id)
            notify.process(user_id)

        with patch.object(notify, "_dispatch", side_effect=run_inline):
            notify.notify_after_draw(USER_ID, Decimal("0.99"), Decimal("0"))
            notify.notify_after_draw(USER_ID, Decimal("0.99"), Decimal("0"))
        assert started == [USER_ID]
        env["send"].assert_called_once()

    def test_dispatch_runs_process_on_a_daemon_thread(self):
        with patch.object(notify.threading, "Thread") as thread:
            notify._dispatch(USER_ID)
        kwargs = thread.call_args.kwargs
        assert kwargs["target"] is notify.process
        assert kwargs["args"] == (USER_ID,)
        assert kwargs["daemon"] is True
        thread.return_value.start.assert_called_once()


# --- the hook in billing --------------------------------------------------


class TestBillingHook:
    @pytest.fixture(autouse=True)
    def _billing_on(self):
        with (
            patch.object(Config, "VAULT_DISCOUNT_BILLING_ENABLED", True),
            patch.object(Config, "VAULT_DISCOUNT_PILOT_USER_IDS", frozenset({USER_ID})),
            patch.object(Config, "VAULT_DISCOUNT_ALL_ACCOUNTS", False),
        ):
            yield

    def test_apply_reports_the_full_share_and_what_was_drawn(self):
        with (
            patch("src.db.inference_discount.apply_discount", return_value=Decimal("0.25")),
            patch.object(notify, "notify_after_draw") as hook,
        ):
            applied = billing.apply_for_request(USER_ID, Decimal("1.00"), REQ)
        assert applied == Decimal("0.25")
        hook.assert_called_once_with(USER_ID, Decimal("0.990000"), Decimal("0.250000"))

    def test_a_failed_draw_does_not_notify(self):
        with (
            patch("src.db.inference_discount.apply_discount", side_effect=RuntimeError("x")),
            patch("src.db.inference_discount.reverse_discount", return_value=Decimal(0)),
            patch.object(notify, "notify_after_draw") as hook,
        ):
            assert billing.apply_for_request(USER_ID, Decimal("1.00"), REQ) == Decimal(0)
        hook.assert_not_called()

    def test_the_hook_never_changes_the_amount_charged(self, enabled):
        with (
            patch("src.db.inference_discount.apply_discount", return_value=Decimal("0")),
            patch.object(notify, "_dispatch", side_effect=RuntimeError("boom")),
        ):
            assert billing.apply_for_request(USER_ID, Decimal("1.00"), REQ) == Decimal(0)


# --- wording ----------------------------------------------------------------


@pytest.mark.parametrize(
    "event,row", [(notify.EVENT_DEPLETED, DEPLETED), (notify.EVENT_DAILY_CAP, CAPPED)]
)
def test_emails_say_discount_credits_and_never_the_retired_names(event, row):
    subject, html, text = notify.build_email(event, row)
    assert "discount" in subject.lower()
    for body in (html, text):
        lowered = body.lower()
        assert "discount credits" in lowered
        assert "paid balance" in lowered
        assert "/settings/credits" in lowered
        for banned in ("inference credits", "free inference", "reward"):
            assert banned not in lowered
