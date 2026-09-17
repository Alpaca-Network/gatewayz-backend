"""Tests for src/db/coupon_redemptions.py.

Under tests/unit/ rather than tests/db/ for the same reason as
tests/unit/test_coupons_persistence.py: tests/conftest.py's autouse
skip_if_no_database skips anything whose path contains "db" when no Supabase is
reachable, and everything here mocks the client outright. The filename avoids
the substring "db" too.

The behaviours pinned here are the ones where a wrong choice costs money or
lies to a user:

* a transport failure is NEVER turned into a refusal -- "your coupon is
  invalid" for what was actually an outage is both wrong and unrecoverable
  from the user's side, since they now believe their good code is dead;
* there is NO fallback path. src/db/users.py degrades to a read-modify-write
  when its RPC is missing; doing that here would reopen the exact TOCTOU the
  RPC exists to close;
* an unrecognised verdict is refused, not optimistically passed through.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.db.coupon_redemptions import (
    REDEMPTION_COLUMNS,
    REDEMPTION_ERROR_CODES,
    RedemptionUnavailable,
    get_user_redemptions,
    redeem_coupon,
)

# public.coupon_redemptions as it exists in prod (ynleroehyrmaafkgjgmr,
# 2026-09-16), pulled from PostgREST's OpenAPI document rather than written
# from memory -- a hand-written column list threw five false positives in the
# sibling guard on 2026-09-16.
PROD_REDEMPTION_COLUMNS = {
    "id",
    "coupon_id",
    "user_id",
    "redeemed_at",
    "value_applied",
    "user_balance_before",
    "user_balance_after",
    "ip_address",
    "user_agent",
}

SUCCESS = {
    "success": True,
    "error_code": None,
    "error_message": None,
    "debug": None,
    "coupon_id": 19,
    "code": "GATEWAYZ",
    "value_applied": 20.0,
    "balance_before": 5.0,
    "balance_after": 25.0,
    "redemption_id": 7,
    "transaction_id": 4242,
}


def _client(execute_return=None, execute_raises=None):
    query = MagicMock()
    for method in ("select", "eq", "order", "limit", "range"):
        getattr(query, method).return_value = query

    rpc = MagicMock()
    if execute_raises is not None:
        rpc.execute.side_effect = execute_raises
        query.execute.side_effect = execute_raises
    else:
        rpc.execute.return_value = execute_return
        query.execute.return_value = execute_return

    client = MagicMock()
    client.rpc.return_value = rpc
    client.table.return_value = query
    return client


def _patch(client):
    return patch("src.db.coupon_redemptions.get_supabase_client", return_value=client)


class TestSelectedColumnsExist:
    def test_no_phantom_columns_in_redemption_select(self):
        """Every column named in REDEMPTION_COLUMNS exists in prod.

        This is the defect class behind seven production failures in Sep 2026:
        code SELECTs a column the database dropped, PostgREST answers 42703,
        and a broad except turns it into a plausible empty result.
        """
        named = {c.strip() for c in REDEMPTION_COLUMNS.split(",")}
        assert named <= PROD_REDEMPTION_COLUMNS, named - PROD_REDEMPTION_COLUMNS

    def test_select_is_explicit_not_star(self):
        assert "*" not in REDEMPTION_COLUMNS


class TestRedeemCoupon:
    def test_passes_code_user_and_client_fingerprint_to_the_rpc(self):
        client = _client(SimpleNamespace(data=SUCCESS))
        with _patch(client):
            result = redeem_coupon(
                code="GATEWAYZ", user_id=34, ip_address="203.0.113.9", user_agent="curl/8"
            )

        assert result == SUCCESS
        name, params = client.rpc.call_args[0]
        assert name == "redeem_coupon"
        assert params == {
            "p_coupon_code": "GATEWAYZ",
            "p_user_id": 34,
            "p_ip_address": "203.0.113.9",
            "p_user_agent": "curl/8",
        }

    def test_calls_the_rpc_exactly_once(self):
        """No client-side retry. A retry on an ambiguous outcome is how one
        redemption becomes two rows; the RPC is idempotent per (coupon, user),
        but only the caller can decide to re-ask, and it must be the user."""
        client = _client(SimpleNamespace(data=SUCCESS))
        with _patch(client):
            redeem_coupon(code="GATEWAYZ", user_id=34)
        assert client.rpc.call_count == 1
        assert client.rpc.return_value.execute.call_count == 1

    def test_unwraps_a_single_row_list(self):
        """PostgREST returns a scalar function bare, but the client has
        historically wrapped single rows in a list. Accept both."""
        client = _client(SimpleNamespace(data=[SUCCESS]))
        with _patch(client):
            assert redeem_coupon(code="GATEWAYZ", user_id=34) == SUCCESS

    @pytest.mark.parametrize("error_code", sorted(REDEMPTION_ERROR_CODES))
    def test_every_known_refusal_is_returned_not_raised(self, error_code):
        """A refusal is an answer. Only an unknown outcome is an exception."""
        verdict = {"success": False, "error_code": error_code, "error_message": "nope"}
        client = _client(SimpleNamespace(data=verdict))
        with _patch(client):
            assert redeem_coupon(code="X", user_id=1) == verdict

    def test_refusals_stay_distinguishable(self):
        """The wrapper must not collapse reasons; the route maps each to its
        own status and error.code."""
        for code in ("COUPON_EXPIRED", "MAX_USES_EXCEEDED", "COUPON_NOT_ASSIGNED"):
            client = _client(SimpleNamespace(data={"success": False, "error_code": code}))
            with _patch(client):
                assert redeem_coupon(code="X", user_id=1)["error_code"] == code

    def test_transport_failure_raises_rather_than_refusing(self):
        client = _client(execute_raises=RuntimeError("connection reset"))
        with _patch(client), pytest.raises(RedemptionUnavailable):
            redeem_coupon(code="GATEWAYZ", user_id=34)

    def test_missing_rpc_raises_and_does_not_fall_back(self):
        """PGRST202 means the migration is not applied. src/db/users.py falls
        back to a read-modify-write in this situation; here that would be the
        double-grant race, so it must stay a hard failure."""
        client = _client(execute_raises=Exception("PGRST202: function not found"))
        with _patch(client), pytest.raises(RedemptionUnavailable):
            redeem_coupon(code="GATEWAYZ", user_id=34)

        # Nothing was written by another route in the process.
        client.table.assert_not_called()

    @pytest.mark.parametrize("payload", [None, [], "granted", 17, {"nope": 1}])
    def test_unrecognised_payload_raises(self, payload):
        """An unparseable answer must not read as success OR as a refusal."""
        client = _client(SimpleNamespace(data=payload))
        with _patch(client), pytest.raises(RedemptionUnavailable):
            redeem_coupon(code="GATEWAYZ", user_id=34)

    def test_unknown_error_code_raises(self):
        """An error_code this code has never heard of means the deployed
        function is not the one we were written against."""
        client = _client(SimpleNamespace(data={"success": False, "error_code": "SOMETHING_NEW"}))
        with _patch(client), pytest.raises(RedemptionUnavailable):
            redeem_coupon(code="GATEWAYZ", user_id=34)

    def test_missing_success_flag_is_not_treated_as_success(self):
        client = _client(SimpleNamespace(data={"error_code": "COUPON_EXPIRED"}))
        with _patch(client):
            assert redeem_coupon(code="X", user_id=1).get("success") is not True

    def test_debug_text_is_logged_not_returned_to_the_caller_untouched(self, caplog):
        """`debug` can carry SQLSTATE/SQLERRM. It belongs in the server log;
        the route is what must not echo it, and it logs it here."""
        verdict = {
            "success": False,
            "error_code": "REDEMPTION_FAILED",
            "error_message": "Could not redeem this coupon. Please try again.",
            "debug": "22003: numeric field overflow",
        }
        client = _client(SimpleNamespace(data=verdict))
        with _patch(client), caplog.at_level("WARNING"):
            redeem_coupon(code="GATEWAYZ", user_id=34)
        assert "numeric field overflow" in caplog.text


class TestGetUserRedemptions:
    def test_scopes_to_the_requesting_user(self):
        """A user's history must be filtered by user_id -- an unfiltered read
        would hand one user another's redemption record."""
        client = _client(SimpleNamespace(data=[]))
        with _patch(client):
            get_user_redemptions(34)
        client.table.return_value.eq.assert_called_once_with("user_id", 34)

    def test_query_failure_raises_instead_of_returning_empty(self):
        """An empty history and a broken query must not render the same --
        the whole lesson of the Sep 15 phantom-column incident."""
        client = _client(execute_raises=RuntimeError("42703"))
        with _patch(client), pytest.raises(RuntimeError):
            get_user_redemptions(34)
