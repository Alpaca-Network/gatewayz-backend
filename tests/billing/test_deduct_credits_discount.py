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

REQ = "6f9b1f0e-3c2a-4d5e-9b8a-1c2d3e4f5a6b"


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
        with patch("src.db.inference_discount.hold_discount") as hold:
            assert discount.discount_aware_credits(7, 1.5, 1.0, REQ) == 1.5
            assert asyncio.run(discount.discount_aware_credits_async(7, 1.5, 1.0, REQ)) == 1.5
            assert discount.apply_for_request(7, Decimal(1), REQ) == Decimal(0)
        hold.assert_not_called()


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

    def test_applied_above_its_ceiling_is_reversed_and_full_price(self, flags):
        flags()
        client = _client(
            lambda n, p: 999 if n in ("discount_apply", "discount_reverse") else _ok_deduct(n, p)
        )
        _deduct(client, cost=2.0)
        assert [n for n, _ in client.calls] == [
            "discount_apply",
            "discount_reverse",
            "atomic_deduct_credits",
        ]
        assert _atomic(client)[0]["p_tokens_amount"] == 2.0

    @pytest.mark.parametrize("configured", ["1", "1.5", "0.999"])
    def test_d_is_capped_so_every_request_pays_writes_and_is_limited(self, flags, configured):
        """Security review: d = 1 made a request free, with no credit row and
        no daily-limit check. d is capped at 0.99 in Python and SQL, so the
        charge is always positive and goes through both."""
        flags(percent=configured)
        seen = {}

        def handler(name, params):
            if name == "discount_apply":
                seen["percent"] = params["p_discount_percent"]
                return "1.980000"
            return _ok_deduct(name, params)

        client = _client(handler)
        with patch("src.services.daily_usage_limiter.enforce_daily_usage_limit") as limit:
            from src.db.users import deduct_credits

            with (
                patch("src.db.users.get_supabase_client", return_value=client),
                patch("src.db.inference_discount.get_supabase_client", return_value=client),
                patch("src.db.plans.is_admin_tier_user", return_value=False),
                patch("src.db.users.invalidate_user_cache"),
                patch("src.db.credit_transactions.get_supabase_client", return_value=client),
            ):
                deduct_credits("key", 2.0, "d", {"request_id": REQ}, REQ)
        assert seen["percent"] == "0.99"
        assert _atomic(client)[0]["p_tokens_amount"] == pytest.approx(0.02)
        assert limit.call_args.args[1] == pytest.approx(0.02)
        assert discount.discount_percent() == Decimal("0.99")

    def test_tiny_cost_still_charges(self, flags):
        flags()
        client = _client(lambda n, p: "0" if n == "discount_apply" else _ok_deduct(n, p))
        _deduct(client, cost=1e-8)
        assert _atomic(client)[0]["p_tokens_amount"] > 0

    def test_no_reversal_when_the_charge_succeeded(self, flags):
        flags()
        client = _client(lambda n, p: "1.98" if n == "discount_apply" else _ok_deduct(n, p))
        _deduct(client, cost=2.0)
        assert "discount_reverse" not in [n for n, _ in client.calls]

    def test_refused_twice_reverses_once(self, flags):
        """credit_handler retries a wrapped refusal: the first attempt reverses,
        the second finds the request reversed (SQL returns 0) and has nothing
        to put back."""
        flags()
        state = {"applied": False, "reversed": False}

        def handler(name, params):
            if name == "discount_apply":
                if state["reversed"]:
                    return 0
                state["applied"] = True
                return "1.98"
            if name == "discount_reverse":
                state["reversed"] = True
                return "1.98"
            return {"success": False, "error": "insufficient_credits", "new_balance": 0}

        client = _client(handler, balance=0.0)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                _deduct(client, cost=2.0)
        assert [n for n, _ in client.calls].count("discount_reverse") == 1
        assert _atomic(client)[-1]["p_tokens_amount"] == 2.0

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
    """The pre-check counts this request's HOLD, never the whole balance."""

    @pytest.mark.parametrize(
        "credits,held,percent,expected",
        [
            (1.0, 1000, "0.99", 100.0),  # credits bind: 1 / 0.01
            (1.0, 5, "0.99", 6.0),  # the hold binds: 1 + 5
            (1.0, 0, "0.99", 1.0),  # nothing held: unchanged
            (2.0, 1000, "1", 200.0),  # d = 1 is capped at 0.99: never unbounded
            (0.0, 100, "0.99", 0.0),  # no credits: still nothing (1 - d is paid in credits)
        ],
    )
    def test_formula(self, flags, credits, held, percent, expected):
        flags(percent=percent)
        with patch("src.db.inference_discount.hold_discount", return_value=Decimal(held)) as hold:
            assert discount.discount_aware_credits(7, credits, 3.0, REQ) == pytest.approx(expected)
        if credits > 0:
            user_id, estimate, d, key, ttl = hold.call_args.args
            assert (user_id, estimate, key) == (7, Decimal("3.0"), REQ)
            assert d == Decimal("0.99") and 60 <= ttl <= 3600

    def test_hold_error_is_identity(self, flags):
        flags()
        with patch("src.db.inference_discount.hold_discount", side_effect=RuntimeError("x")):
            assert discount.discount_aware_credits(7, 3.0, 1.0, REQ) == 3.0

    @pytest.mark.parametrize(
        "user_id,estimate,key", [(None, 1.0, REQ), (7, 1.0, None), (7, 1.0, "nope"), (7, 0, REQ)]
    )
    def test_no_user_key_or_estimate_holds_nothing(self, flags, user_id, estimate, key):
        flags()
        with patch("src.db.inference_discount.hold_discount") as hold:
            assert discount.discount_aware_credits(user_id, 3.0, estimate, key) == 3.0
        hold.assert_not_called()

    def test_precheck_passes_the_billing_ref_through(self, flags):
        from src.services.billing import simple_metering

        flags()
        user = {"id": 7, "subscription_allowance": 0, "purchased_credits": 0.01}
        with (
            patch.object(simple_metering.users_module, "get_user", return_value=user),
            patch("src.db.inference_discount.hold_discount", return_value=Decimal("0.99")) as h,
        ):
            assert asyncio.run(simple_metering.precheck_credits("k", 1.0, REQ)) is user
        assert h.call_args.args[3] == REQ

    def test_precheck_without_billing_ref_counts_credits_only(self, flags):
        from fastapi import HTTPException

        from src.services.billing import simple_metering

        flags()
        user = {"id": 7, "subscription_allowance": 0, "purchased_credits": 0.01}
        with (
            patch.object(simple_metering.users_module, "get_user", return_value=user),
            patch("src.db.inference_discount.hold_discount") as h,
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(simple_metering.precheck_credits("k", 1.0))
        assert exc.value.status_code == 402
        h.assert_not_called()


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

    def test_refund_never_goes_negative(self, flags):
        flags()
        ok, add = self._refund(2.0, Decimal("5"))
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
