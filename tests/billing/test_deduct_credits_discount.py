"""The delegated-staking inference discount at billing time
(src/services/delegation/discount.py, hooked into src/db/users.py::
deduct_credits and credit_handler.refund_credits).

Invariants:
* flags off -> deduct_credits is unchanged: same RPC call, same params, and
  no discount RPC / balance read at all;
* on -> d x cost comes out of the discount balance (discount_apply, keyed on
  the billing request_id) and only cost - applied is charged in credits;
* any discount error -> full price, the request never fails because of it;
* a retry with the same request_id never draws again or re-charges;
* a refused charge puts the discount back; a refund returns the discount to
  the discount balance and only the credits part as credits.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.services.delegation import discount

REQ = str(uuid.uuid4())


def _client(rpc_handler, balance=5.0, existing_tx=None):
    client = MagicMock()

    def table(name):
        t = MagicMock()
        res = MagicMock()
        if name == "api_keys_new":
            res.data = [{"user_id": 7}]
        elif name == "credit_transactions":
            res.data = [existing_tx] if existing_tx else []
        else:
            res.data = [
                {"id": 7, "subscription_allowance": 0, "purchased_credits": balance, "tier": "pro"}
            ]
        t.select.return_value.eq.return_value.execute.return_value = res
        return t

    client.table.side_effect = table
    calls: list[tuple[str, dict]] = []

    def rpc(name, params):
        calls.append((name, params))
        out = MagicMock()
        out.execute.return_value.data = rpc_handler(name, params)
        return out

    client.rpc.side_effect = rpc
    client.calls = calls
    return client


def _ok_deduct(name, params):
    if name == "atomic_deduct_credits":
        return {
            "success": True,
            "transaction_id": 1,
            "new_allowance": 0,
            "new_purchased": 4,
            "new_balance": 4,
        }
    raise AssertionError(f"unexpected rpc {name}")


@pytest.fixture
def flags(monkeypatch):
    def set_(enabled=True, staking=True, percent="0.99"):
        monkeypatch.setattr(discount.Config, "DELEGATION_DISCOUNT_ENABLED", enabled)
        monkeypatch.setattr(discount.Config, "DELEGATED_STAKING_ENABLED", staking)
        monkeypatch.setattr(discount.Config, "DELEGATION_DISCOUNT_PERCENT", Decimal(percent))

    return set_


def _deduct(client, cost=2.0, request_id=REQ, metadata=None):
    from src.db.users import deduct_credits

    with (
        patch("src.db.users.get_supabase_client", return_value=client),
        patch("src.db.inference_discount.get_supabase_client", return_value=client),
        patch("src.db.plans.is_admin_tier_user", return_value=False),
        patch("src.services.daily_usage_limiter.enforce_daily_usage_limit"),
        patch("src.db.users.invalidate_user_cache"),
        patch(
            "src.db.credit_transactions.get_supabase_client",
            return_value=client,
            create=True,
        ),
    ):
        deduct_credits(
            "key",
            cost,
            "API usage - m",
            metadata if metadata is not None else {"model": "m", "request_id": request_id},
            request_id,
        )


def _atomic(client):
    return [p for n, p in client.calls if n == "atomic_deduct_credits"]


class TestFlagOff:
    @pytest.mark.parametrize("enabled,staking", [(False, False), (False, True), (True, False)])
    def test_unchanged_and_no_discount_db_call(self, flags, enabled, staking):
        flags(enabled=enabled, staking=staking)
        client = _client(_ok_deduct)
        with (
            patch("src.db.inference_discount.apply_discount") as apply,
            patch("src.db.inference_discount.get_balance") as balance,
        ):
            _deduct(client)
        apply.assert_not_called()
        balance.assert_not_called()
        assert [n for n, _ in client.calls] == ["atomic_deduct_credits"]
        params = _atomic(client)[0]
        assert params["p_tokens_amount"] == 2.0
        # Metadata byte-for-byte: no discount keys.
        assert params["p_metadata"] == {"model": "m", "request_id": REQ}

    def test_precheck_helpers_are_identity_without_db(self, flags):
        flags(enabled=False)
        with patch("src.db.inference_discount.get_balance") as balance:
            assert discount.discount_aware_credits(7, 1.5) == 1.5
            assert asyncio.run(discount.discount_aware_credits_async(7, 1.5)) == 1.5
            assert discount.apply_for_request(7, Decimal(1), REQ) == Decimal(0)
        balance.assert_not_called()


class TestFlagOn:
    def test_partial_balance_coverage(self, flags):
        flags()

        def handler(name, params):
            if name == "discount_apply":
                assert params["p_request_id"] == REQ
                assert params["p_discount_percent"] == "0.99"
                assert params["p_user_id"] == 7
                return 0.5  # the balance only covered $0.50 of the $1.98
            return _ok_deduct(name, params)

        client = _client(handler)
        _deduct(client, cost=2.0)
        assert [n for n, _ in client.calls] == ["discount_apply", "atomic_deduct_credits"]
        params = _atomic(client)[0]
        assert params["p_tokens_amount"] == pytest.approx(1.5)
        assert params["p_from_allowance"] + params["p_from_purchased"] == pytest.approx(1.5)
        assert params["p_metadata"]["cost_list_usd"] == "2.00000000"
        assert params["p_metadata"]["discount_usd"] == "0.500000"
        assert params["p_metadata"]["charged_usd"] == "1.50000000"

    def test_full_discount_charges_one_minus_d(self, flags):
        flags()
        client = _client(lambda n, p: "1.980000" if n == "discount_apply" else _ok_deduct(n, p))
        _deduct(client, cost=2.0)
        assert _atomic(client)[0]["p_tokens_amount"] == pytest.approx(0.02)

    def test_rpc_error_charges_full_price_and_never_fails(self, flags):
        flags()

        def handler(name, params):
            if name == "discount_apply":
                raise ConnectionError("https://secret.supabase.co?apikey=sk_live boom")
            return _ok_deduct(name, params)

        client = _client(handler)
        with patch("src.services.delegation.discount.logger") as log:
            _deduct(client, cost=2.0)
        assert _atomic(client)[0]["p_tokens_amount"] == 2.0
        logged = " ".join(str(a) for c in log.warning.call_args_list for a in c.args)
        assert "secret" not in logged and "sk_live" not in logged

    @pytest.mark.parametrize("bad", [None, "", "not-a-number", {"a": 1, "b": 2}, -1, True])
    def test_unreadable_rpc_result_charges_full_price(self, flags, bad):
        flags()
        client = _client(lambda n, p: bad if n == "discount_apply" else _ok_deduct(n, p))
        _deduct(client, cost=2.0)
        assert _atomic(client)[0]["p_tokens_amount"] == 2.0

    def test_applied_is_never_more_than_cost(self, flags):
        flags()
        client = _client(lambda n, p: 999 if n == "discount_apply" else _ok_deduct(n, p))
        _deduct(client, cost=2.0)
        # Fully covered -> nothing charged in credits, never a negative charge.
        assert _atomic(client) == []

    def test_fully_covered_writes_no_credit_charge(self, flags):
        flags(percent="1")
        client = _client(lambda n, p: 2.0 if n == "discount_apply" else _ok_deduct(n, p))
        _deduct(client, cost=2.0)
        assert [n for n, _ in client.calls] == ["discount_apply"]

    def test_non_uuid_request_id_gets_no_discount(self, flags):
        flags()
        client = _client(_ok_deduct)
        _deduct(client, cost=2.0, request_id="route-1", metadata={"model": "m", "request_id": "x"})
        assert [n for n, _ in client.calls] == ["atomic_deduct_credits"]

    def test_retry_with_same_request_id_never_recharges(self, flags):
        """The first attempt charged; the retry finds the ledger row and
        returns before touching the discount or the balance."""
        flags()
        client = _client(_ok_deduct, existing_tx={"id": 1, "amount": -0.02})
        _deduct(client, cost=2.0)
        assert client.calls == []

    def test_retry_after_transient_failure_reuses_the_same_discount(self, flags):
        """discount_apply is idempotent on request_id (the SQL returns the
        original amount), so a retry after a transient failure charges the
        same cost - applied, and the failure does not put the discount back."""
        flags()
        drawn: list[str] = []
        state = {"fail": True}

        def handler(name, params):
            if name == "discount_apply":
                # Mirror the SQL: the first call draws, a repeat returns it.
                drawn.append(params["p_request_id"])
                return "1.980000"
            if state["fail"]:
                raise ConnectionError("transient")
            return _ok_deduct(name, params)

        client = _client(handler)
        with (
            patch("src.db.inference_discount.reverse_discount") as reverse,
            patch("src.db.credit_transactions.log_credit_transaction", return_value=None),
        ):
            with pytest.raises(RuntimeError):
                _deduct(client, cost=2.0)  # atomic RPC down, legacy ledger write fails
            reverse.assert_not_called()
            state["fail"] = False
            _deduct(client, cost=2.0)
        assert drawn == [REQ, REQ]
        assert _atomic(client)[-1]["p_tokens_amount"] == pytest.approx(0.02)

    def test_refused_charge_puts_the_discount_back(self, flags):
        flags()

        def handler(name, params):
            if name == "discount_apply":
                return 1.98
            if name == "discount_reverse":
                assert params == {"p_request_id": REQ}
                return 1.98
            return {"success": False, "error": "insufficient_credits", "new_balance": 0}

        client = _client(handler, balance=0.0)
        with pytest.raises(RuntimeError, match="Insufficient credits"):
            _deduct(client, cost=2.0)
        assert [n for n, _ in client.calls] == [
            "discount_apply",
            "atomic_deduct_credits",
            "discount_reverse",
        ]


class TestCapacity:
    @pytest.mark.parametrize(
        "credits,balance,percent,expected",
        [
            (1.0, 1000, "0.99", 100.0),  # credits bind: 1 / 0.01
            (1.0, 5, "0.99", 6.0),  # balance binds: 1 + 5
            (1.0, 0, "0.99", 1.0),  # no balance: unchanged
            (2.0, 3, "1", 5.0),  # d = 1: credits + balance
            (0.0, 100, "0.99", 0.0),  # no credits: still nothing (1 - d is paid in credits)
        ],
    )
    def test_formula(self, flags, credits, balance, percent, expected):
        flags(percent=percent)
        with patch(
            "src.db.inference_discount.get_balance", return_value={"balance_usd": str(balance)}
        ):
            assert discount.discount_aware_credits(7, credits) == pytest.approx(expected)

    def test_lookup_error_is_identity(self, flags):
        flags()
        with patch("src.db.inference_discount.get_balance", side_effect=RuntimeError("x")):
            assert discount.discount_aware_credits(7, 3.0) == 3.0

    def test_non_int_user_is_identity(self, flags):
        flags()
        with patch("src.db.inference_discount.get_balance") as balance:
            assert discount.discount_aware_credits(None, 3.0) == 3.0
        balance.assert_not_called()


class TestRefund:
    def _refund(self, amount, restored):
        from src.services.billing import credit_handler

        with (
            patch("src.services.delegation.discount.reverse_for_request", return_value=restored),
            patch("src.db.users.add_credits_to_user") as add,
        ):
            ok = asyncio.run(
                credit_handler.refund_credits(
                    user_id=7,
                    api_key="k",
                    amount=amount,
                    reason="provider_error",
                    original_request_id=REQ,
                )
            )
        return ok, add

    def test_refund_returns_discount_to_balance_and_only_credits_as_credits(self, flags):
        flags()
        ok, add = self._refund(2.0, Decimal("1.98"))
        assert ok is True
        assert add.call_args.kwargs["credits"] == pytest.approx(0.02)
        assert add.call_args.kwargs["metadata"]["discount_restored_usd"] == "1.98"

    def test_fully_discounted_refund_writes_no_credits(self, flags):
        flags(percent="1")
        ok, add = self._refund(2.0, Decimal("2.0"))
        assert ok is True
        add.assert_not_called()

    def test_flag_off_refund_unchanged(self, flags):
        flags(enabled=False)
        with patch("src.services.delegation.discount.reverse_for_request") as reverse:
            from src.services.billing import credit_handler

            with patch("src.db.users.add_credits_to_user") as add:
                asyncio.run(
                    credit_handler.refund_credits(
                        user_id=7,
                        api_key="k",
                        amount=2.0,
                        reason="provider_error",
                        original_request_id=REQ,
                    )
                )
        reverse.assert_not_called()
        assert add.call_args.kwargs["credits"] == 2.0
