"""Who discount credits billing applies to (VAULT_DISCOUNT_PILOT_USER_IDS).

With the switch on, billing draws on discount credits only for the accounts
the setting names ("*" for every account). Every other account must take
exactly the switch-off path, with no discount DB call at all: on the charge,
on the pre-request check, and on a refund."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from src.config.config import Config
from src.services.discount_credits import billing

ROOT = Path(__file__).resolve().parents[2]
REQ = "6f9b1f0e-3c2a-4d5e-9b8a-1c2d3e4f5a6b"
PILOT = 10
OTHER = 11
APPLY = "src.db.inference_discount.apply_discount"
HOLD = "src.db.inference_discount.hold_discount"


def _scope(ids: frozenset[int], everyone: bool = False):
    return (
        patch.object(Config, "VAULT_DISCOUNT_PILOT_USER_IDS", ids),
        patch.object(Config, "VAULT_DISCOUNT_ALL_ACCOUNTS", everyone),
    )


@pytest.fixture
def switched_on():
    with patch.object(Config, "VAULT_DISCOUNT_BILLING_ENABLED", True):
        yield


@pytest.fixture
def pilot_only(switched_on):
    ids, everyone = _scope(frozenset({PILOT}))
    with ids, everyone:
        yield


class TestWhoIsIn:
    def test_an_empty_setting_admits_nobody(self, switched_on):
        """A missing or mistyped setting must never open billing to everyone."""
        ids, everyone = _scope(frozenset())
        with ids, everyone:
            assert not billing.account_in_pilot(PILOT)
            assert not billing.discount_enabled_for(PILOT)

    def test_a_star_admits_everyone(self, switched_on):
        ids, everyone = _scope(frozenset(), everyone=True)
        with ids, everyone:
            assert billing.discount_enabled_for(PILOT)
            assert billing.discount_enabled_for(OTHER)

    def test_a_list_admits_only_its_accounts(self, pilot_only):
        assert billing.discount_enabled_for(PILOT)
        assert not billing.discount_enabled_for(OTHER)

    @pytest.mark.parametrize("not_an_account", [None, "10", 10.0, True, [10]])
    def test_only_a_whole_number_id_can_match(self, pilot_only, not_an_account):
        assert not billing.account_in_pilot(not_an_account)

    def test_the_list_never_overrides_the_switch(self):
        ids, everyone = _scope(frozenset({PILOT}), everyone=True)
        with patch.object(Config, "VAULT_DISCOUNT_BILLING_ENABLED", False), ids, everyone:
            assert not billing.discount_enabled_for(PILOT)

    def test_default_admits_nobody(self):
        assert Config.VAULT_DISCOUNT_PILOT_USER_IDS == frozenset()
        assert Config.VAULT_DISCOUNT_ALL_ACCOUNTS is False


class TestOutsideThePilot:
    """An account that is not named: nothing is drawn, nothing is held, and
    the database is never asked."""

    def test_apply_draws_nothing_and_makes_no_db_call(self, pilot_only):
        with patch(APPLY) as apply:
            assert billing.apply_for_request(OTHER, Decimal("2"), REQ) == Decimal(0)
        apply.assert_not_called()

    def test_precheck_counts_paid_credits_only_and_places_no_hold(self, pilot_only):
        with patch(HOLD) as hold:
            assert billing.discount_aware_credits(OTHER, 5.0, 2.0, REQ) == 5.0
            via_async = asyncio.run(billing.discount_aware_credits_async(OTHER, 5.0, 2.0, REQ))
            assert via_async == 5.0
        hold.assert_not_called()

    def test_a_refund_makes_no_discount_call(self, pilot_only):
        from src.services.billing import credit_handler

        with (
            patch.object(billing, "reverse_or_none") as reverse,
            patch.object(billing, "charged_credits_for") as charged,
            patch("src.db.users.add_credits_to_user") as add,
        ):
            asyncio.run(
                credit_handler.refund_credits(
                    user_id=OTHER,
                    api_key="gw_test_key",
                    amount=1.0,
                    reason=sorted(credit_handler.REFUNDABLE_ERROR_TYPES)[0],
                    original_request_id=REQ,
                )
            )
        reverse.assert_not_called()
        charged.assert_not_called()
        assert add.called

    @pytest.mark.parametrize(
        "path",
        [
            "src/db/users.py",
            "src/routes/chat.py",
            "src/handlers/chat_handler.py",
            "src/services/billing/credit_handler.py",
        ],
    )
    def test_no_billing_call_site_checks_the_switch_alone(self, path):
        """Each call site must ask about the account, not only the switch."""
        source = (ROOT / path).read_text()
        assert "discount_enabled_for(" in source
        assert "discount_enabled()" not in source


class TestInsideThePilot:
    def test_apply_draws_for_a_named_account(self, pilot_only):
        with patch(APPLY, return_value=Decimal("1.98")) as apply:
            assert billing.apply_for_request(PILOT, Decimal("2"), REQ) == Decimal("1.98")
        apply.assert_called_once()

    def test_precheck_places_a_hold_for_a_named_account(self, pilot_only):
        with patch(HOLD, return_value=Decimal("1.98")) as hold:
            assert billing.discount_aware_credits(PILOT, 5.0, 2.0, REQ) > 5.0
        hold.assert_called_once()

    def test_a_refund_restores_the_discount_for_a_named_account(self, pilot_only):
        from src.services.billing import credit_handler

        with (
            patch.object(billing, "reverse_or_none", return_value=Decimal("0.99")) as reverse,
            patch.object(billing, "charged_credits_for", return_value=Decimal("0.01")),
            patch("src.db.users.add_credits_to_user"),
        ):
            asyncio.run(
                credit_handler.refund_credits(
                    user_id=PILOT,
                    api_key="gw_test_key",
                    amount=1.0,
                    reason=sorted(credit_handler.REFUNDABLE_ERROR_TYPES)[0],
                    original_request_id=REQ,
                )
            )
        reverse.assert_called_once()
