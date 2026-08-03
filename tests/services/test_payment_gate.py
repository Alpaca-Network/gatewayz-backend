"""Tests for the live-API-key payment gate.

Two properties matter: a bot with no payment cannot mint a live key, and a real
paying customer is never locked out by an infrastructure hiccup.
"""

from unittest.mock import patch

import pytest

from src.services import payment_gate
from src.services.payment_gate import (
    check_live_key_allowed,
    gate_error_detail,
    has_payment_signal,
    is_gate_enabled,
)


class TestGateToggle:
    def test_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("REQUIRE_PAYMENT_FOR_LIVE_KEYS", raising=False)
        assert is_gate_enabled() is True

    @pytest.mark.parametrize("value", ["false", "FALSE", "0", "no"])
    def test_disabled_by_env(self, monkeypatch, value):
        monkeypatch.setenv("REQUIRE_PAYMENT_FOR_LIVE_KEYS", value)
        assert is_gate_enabled() is False


class TestPaymentSignal:
    def test_settled_payment_above_minimum_passes(self):
        with patch(
            "src.db.payments.get_user_payments",
            return_value=[{"amount_usd": 10.0, "status": "succeeded"}],
        ):
            allowed, reason = has_payment_signal({"id": 1})
        assert allowed is True
        assert "lifetime_payments" in reason

    def test_no_payments_and_no_credits_fails(self):
        with patch("src.db.payments.get_user_payments", return_value=[]):
            allowed, reason = has_payment_signal({"id": 1, "credits": 0})
        assert allowed is False
        assert reason == "no_payment_signal"

    def test_credit_balance_counts_as_a_signal(self):
        """Covers admin grants, coupons and partner trials, all human-gated."""
        with patch("src.db.payments.get_user_payments", return_value=[]):
            allowed, reason = has_payment_signal({"id": 1, "credits": 5.0})
        assert allowed is True
        assert "credit_balance" in reason

    def test_failed_payments_do_not_count(self):
        with patch(
            "src.db.payments.get_user_payments",
            return_value=[{"amount_usd": 100.0, "status": "failed"}],
        ):
            allowed, _ = has_payment_signal({"id": 1, "credits": 0})
        assert allowed is False

    def test_payment_lookup_failure_falls_through_to_credits(self):
        """Infrastructure trouble must not lock out a paying customer."""
        with patch("src.db.payments.get_user_payments", side_effect=RuntimeError("db down")):
            allowed, reason = has_payment_signal({"id": 1, "credits": 20.0})
        assert allowed is True
        assert "credit_balance" in reason

    def test_cents_only_payment_row_normalised(self):
        with patch(
            "src.db.payments.get_user_payments",
            return_value=[{"amount": 500, "status": "paid"}],
        ):
            allowed, _ = has_payment_signal({"id": 1, "credits": 0})
        assert allowed is True

    def test_below_minimum_does_not_pass(self, monkeypatch):
        monkeypatch.setattr(payment_gate, "MIN_TOPUP_USD", 5.0)
        with patch(
            "src.db.payments.get_user_payments",
            return_value=[{"amount_usd": 1.0, "status": "succeeded"}],
        ):
            allowed, _ = has_payment_signal({"id": 1, "credits": 0})
        assert allowed is False


class TestEnvironmentGating:
    def test_test_environment_is_free(self):
        """Evaluation must not require a card."""
        allowed, reason = check_live_key_allowed({"id": 1, "credits": 0}, "test")
        assert allowed is True
        assert reason == "ungated_environment"

    def test_development_environment_is_free(self):
        allowed, _ = check_live_key_allowed({"id": 1, "credits": 0}, "development")
        assert allowed is True

    def test_live_environment_blocked_without_payment(self):
        with patch("src.db.payments.get_user_payments", return_value=[]):
            allowed, _ = check_live_key_allowed({"id": 1, "credits": 0}, "live")
        assert allowed is False

    def test_live_environment_allowed_with_payment(self):
        with patch(
            "src.db.payments.get_user_payments",
            return_value=[{"amount_usd": 10.0, "status": "succeeded"}],
        ):
            allowed, _ = check_live_key_allowed({"id": 1}, "live")
        assert allowed is True

    def test_case_insensitive_environment(self):
        with patch("src.db.payments.get_user_payments", return_value=[]):
            allowed, _ = check_live_key_allowed({"id": 1, "credits": 0}, "LIVE")
        assert allowed is False

    def test_disabled_gate_allows_everything(self, monkeypatch):
        monkeypatch.setenv("REQUIRE_PAYMENT_FOR_LIVE_KEYS", "false")
        allowed, reason = check_live_key_allowed({"id": 1, "credits": 0}, "live")
        assert allowed is True
        assert reason == "gate_disabled"


class TestResolveKeyEnvironment:
    """The signup path auto-creates keys and cannot 402, so it downgrades.

    Regression cover for a hole found by running the flow end to end: the gate
    originally guarded only POST /user/api-keys, so registering through /auth
    handed out a live key to a brand-new zero-credit account — the exact
    farming vector the gate exists to close.
    """

    def test_new_unpaid_account_is_downgraded_to_test(self):
        with patch("src.db.payments.get_user_payments", return_value=[]):
            env, downgraded = payment_gate.resolve_key_environment(
                {"id": 1, "credits": 0}, "live"
            )
        assert env == "test"
        assert downgraded is True

    def test_paid_account_keeps_live(self):
        with patch(
            "src.db.payments.get_user_payments",
            return_value=[{"amount_usd": 10.0, "status": "succeeded"}],
        ):
            env, downgraded = payment_gate.resolve_key_environment({"id": 1}, "live")
        assert env == "live"
        assert downgraded is False

    def test_test_environment_is_never_downgraded(self):
        with patch("src.db.payments.get_user_payments", return_value=[]):
            env, downgraded = payment_gate.resolve_key_environment(
                {"id": 1, "credits": 0}, "test"
            )
        assert env == "test"
        assert downgraded is False

    def test_missing_request_defaults_to_live_then_downgrades(self):
        with patch("src.db.payments.get_user_payments", return_value=[]):
            env, downgraded = payment_gate.resolve_key_environment({"id": 1, "credits": 0}, None)
        assert env == "test"
        assert downgraded is True

    def test_kill_switch_restores_live(self, monkeypatch):
        monkeypatch.setenv("REQUIRE_PAYMENT_FOR_LIVE_KEYS", "false")
        env, downgraded = payment_gate.resolve_key_environment({"id": 1, "credits": 0}, "live")
        assert env == "live"
        assert downgraded is False

    def test_account_with_granted_credits_keeps_live(self):
        """Admin grants and coupons are human-gated, so they still qualify."""
        with patch("src.db.payments.get_user_payments", return_value=[]):
            env, _ = payment_gate.resolve_key_environment({"id": 1, "credits": 25.0}, "live")
        assert env == "live"

    def test_downgrade_notice_tells_the_user_how_to_unlock(self):
        notice = payment_gate.downgrade_notice("live")
        assert "test" in notice
        assert "Top up" in notice


class TestErrorDetail:
    def test_tells_the_user_how_to_unblock_themselves(self):
        detail = gate_error_detail("live")
        assert detail["error"] == "payment_required"
        assert detail["free_alternative_environment"] == "test"
        assert len(detail["how_to_resolve"]) >= 2

    def test_message_names_the_environment(self):
        assert "live" in gate_error_detail("live")["message"]
