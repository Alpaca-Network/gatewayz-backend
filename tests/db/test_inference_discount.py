"""Tests for src/db/inference_discount.py -- the discount credits balance.
The billing calls must RAISE on failure (billing then charges full price);
reads and the grant return a safe default. The Supabase client is mocked."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

import src.db.inference_discount as idb

REQ = "6f9b1f0e-3c2a-4d5e-9b8a-1c2d3e4f5a6b"


@pytest.fixture
def sb():
    """Opt out of conftest's skip_if_no_database: the client is mocked."""
    return None


def _broken():
    client = MagicMock()
    client.table.side_effect = RuntimeError("db down")
    client.rpc.side_effect = RuntimeError("db down")
    return client


def _rpc_client(data):
    client = MagicMock()
    client.rpc.return_value.execute.return_value.data = data
    return client


class TestBillingCalls:
    def test_apply_raises_on_rpc_failure_and_garbage(self, sb):
        client = MagicMock()
        client.rpc.side_effect = RuntimeError("https://x.supabase.co?key=secret")
        with patch.object(idb, "get_supabase_client", return_value=client):
            with pytest.raises(idb.DiscountUnavailableError) as exc:
                idb.apply_discount(7, Decimal(1), Decimal("0.99"), "u")
            assert "secret" not in str(exc.value)
        for bad in (None, "x", -1, True, [], {"a": 1, "b": 2}, "inf"):
            with patch.object(idb, "get_supabase_client", return_value=_rpc_client(bad)):
                with pytest.raises(idb.DiscountUnavailableError):
                    idb.apply_discount(7, Decimal(1), Decimal("0.99"), "u")

    @pytest.mark.parametrize("data", [1.98, "1.98", [1.98], [{"discount_apply": 1.98}]])
    def test_apply_parses_scalar_shapes(self, sb, data):
        client = _rpc_client(data)
        with patch.object(idb, "get_supabase_client", return_value=client):
            assert idb.apply_discount(7, Decimal(2), Decimal("0.99"), "u") == Decimal("1.98")
        params = client.rpc.call_args.args[1]
        assert params["p_cost"] == "2" and params["p_discount_percent"] == "0.99"


class TestReads:
    def test_reads_have_safe_defaults(self, sb):
        with patch.object(idb, "get_supabase_client", return_value=_broken()):
            assert idb.get_balance(7) is None
            assert idb.list_ledger(7) == []


class TestGrantDiscount:
    def test_calls_discount_grant_with_the_expected_payload(self, sb):
        result = {"status": "granted", "amount_usd": 12.5, "balance_after": 12.5}
        client = _rpc_client(result)
        with patch.object(idb, "get_supabase_client", return_value=client):
            assert idb.grant_discount(7, Decimal("12.5"), REQ, "test:grant") == result
        client.rpc.assert_called_once_with(
            "discount_grant",
            {
                "p_user_id": 7,
                "p_amount": "12.5",
                "p_request_id": REQ,
                "p_ref": "test:grant",
                "p_metadata": {},
            },
        )

    def test_the_idempotency_key_is_passed_through_and_a_repeat_adds_nothing(self, sb):
        """Idempotency lives in the SQL (one ledger row per request_id): the
        wrapper must hand the same key over unchanged on every call and
        report the SQL's 'exists' answer as-is."""
        first = {"status": "granted", "amount_usd": 5, "balance_after": 5}
        repeat = {"status": "exists", "amount_usd": 5, "balance_after": 5}
        client = MagicMock()
        client.rpc.return_value.execute.side_effect = [
            MagicMock(data=first),
            MagicMock(data=repeat),
        ]
        with patch.object(idb, "get_supabase_client", return_value=client):
            assert idb.grant_discount(7, Decimal(5), REQ, "test:grant") == first
            assert idb.grant_discount(7, Decimal(5), REQ, "test:grant") == repeat
        keys = [call.args[1]["p_request_id"] for call in client.rpc.call_args_list]
        assert keys == [REQ, REQ]
        assert {call.args[0] for call in client.rpc.call_args_list} == {"discount_grant"}

    @pytest.mark.parametrize(
        "data", [[{"status": "granted"}], {"discount_grant": {"status": "granted"}}]
    )
    def test_parses_wrapped_jsonb_returns(self, sb, data):
        with patch.object(idb, "get_supabase_client", return_value=_rpc_client(data)):
            assert idb.grant_discount(7, Decimal(1), REQ, "r") == {"status": "granted"}

    def test_invalid_amount_is_reported_not_raised(self, sb):
        with patch.object(
            idb, "get_supabase_client", return_value=_rpc_client({"status": "invalid"})
        ):
            assert idb.grant_discount(7, Decimal(0), REQ, "r") == {"status": "invalid"}

    def test_failure_is_none_and_leaks_nothing(self, sb, caplog):
        client = MagicMock()
        client.rpc.side_effect = RuntimeError("https://x.supabase.co?key=secret")
        with patch.object(idb, "get_supabase_client", return_value=client):
            assert idb.grant_discount(7, Decimal(1), REQ, "r") is None
        assert "secret" not in caplog.text

    def test_docstring_states_who_may_grant(self):
        doc = " ".join((idb.grant_discount.__doc__ or "").split())
        assert (
            "In production, discount credits may only be granted by the vault mirror job "
            "from on-chain mints. Tests and a developer-run script may call this directly."
        ) in doc

    def test_the_retired_admin_adjust_wrapper_is_gone(self):
        assert not hasattr(idb, "adjust_discount")
