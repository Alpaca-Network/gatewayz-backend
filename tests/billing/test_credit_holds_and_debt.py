"""Credit holds and unpaid balances (src/services/billing/credit_holds.py,
src/db/credit_holds.py, src/db/users.py::deduct_credits and the chat
handler's pre-check).

* Switches off (the default): no hold, no debt lookup, no new DB call, and
  deduct_credits makes exactly the calls it made before.
* CREDIT_HOLDS_ENABLED: the pre-check holds the request's maximum paid cost;
  a refused hold is a 402; a request that ends without being billed releases
  its hold.
* CREDIT_DEBT_ENABLED: a charge after delivery that exceeds the balance
  charges the balance and records the rest; an account with an unpaid balance
  gets 402 ``outstanding_balance``.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.handlers.chat_handler import ChatInferenceHandler
from src.schemas.internal.chat import InternalChatRequest, InternalMessage
from src.services.billing import credit_holds

REF = "6f9b1f0e-3c2a-4d5e-9b8a-1c2d3e4f5a6b"
HOLD = "src.db.credit_holds.hold_credits"
RELEASE = "src.db.credit_holds.release_credits"
DEBT_LOOKUP = "src.db.credit_holds.has_unpaid_debt"
CHARGE_WITH_DEBT = "src.db.credit_holds.charge_with_debt"


@pytest.fixture(autouse=True)
def _switches(monkeypatch):
    monkeypatch.setattr(credit_holds.Config, "CREDIT_HOLDS_ENABLED", False, raising=False)
    monkeypatch.setattr(credit_holds.Config, "CREDIT_DEBT_ENABLED", False, raising=False)
    credit_holds._debt_cache.clear()
    yield
    credit_holds._debt_cache.clear()


def _on(monkeypatch, holds=True, debt=True):
    monkeypatch.setattr(credit_holds.Config, "CREDIT_HOLDS_ENABLED", holds, raising=False)
    monkeypatch.setattr(credit_holds.Config, "CREDIT_DEBT_ENABLED", debt, raising=False)


def _handler(credits=5.0):
    h = ChatInferenceHandler(api_key="gw_key", background_tasks=None, request=None)
    h.user = {"id": 1, "key_id": 9, "subscription_allowance": 0, "purchased_credits": credits}
    h.is_anonymous = False
    h._billing_ref = lambda: REF
    return h


def _allowed(max_cost=1.0):
    return {
        "allowed": True,
        "max_cost": max_cost,
        "input_tokens": 1234,
        "max_output_tokens": 100,
    }


# --- Service layer ------------------------------------------------------------


@pytest.mark.asyncio
async def test_switches_off_make_no_db_call():
    with (
        patch(HOLD) as hold,
        patch(RELEASE) as release,
        patch(DEBT_LOOKUP) as lookup,
    ):
        assert await credit_holds.place_hold_async(1, 2.0, REF) is True
        assert await credit_holds.release_hold_async(1, REF) == 0
        assert await credit_holds.has_unpaid_debt_async(1) is False
    hold.assert_not_called()
    release.assert_not_called()
    lookup.assert_not_called()


def test_a_hold_is_refused_only_when_the_balance_is_held(monkeypatch):
    _on(monkeypatch)
    with patch(HOLD, return_value=Decimal(0)):
        assert credit_holds.place_hold(1, 2.0, REF) is False
    with patch(HOLD, return_value=Decimal("2")) as hold:
        assert credit_holds.place_hold(1, 2.0, REF) is True
    assert hold.call_args.args[:3] == (1, Decimal("2.0"), REF)
    assert hold.call_args.args[3] == 900  # CREDIT_HOLD_TTL_SECONDS default
    # A hold that cannot be placed because of an error never fails the request.
    with patch(HOLD, side_effect=RuntimeError("down")):
        assert credit_holds.place_hold(1, 2.0, REF) is True
    # A request without a UUID billing ref is not held.
    with patch(HOLD) as hold:
        assert credit_holds.place_hold(1, 2.0, "not-a-uuid") is True
    hold.assert_not_called()


def test_the_unpaid_balance_answer_is_cached_briefly(monkeypatch):
    _on(monkeypatch)
    with patch(DEBT_LOOKUP, return_value=True) as lookup:
        assert credit_holds.has_unpaid_debt(1) is True
        assert credit_holds.has_unpaid_debt(1) is True
    assert lookup.call_count == 1
    credit_holds.forget_debt_lookup(1)
    with patch(DEBT_LOOKUP, return_value=False):
        assert credit_holds.has_unpaid_debt(1) is False


def test_the_paid_part_excludes_the_discount_credits_share():
    assert credit_holds.paid_part(10.0, 5.0, 5.0) == 10.0
    with patch(
        "src.services.discount_credits.billing.discount_percent", return_value=Decimal("0.5")
    ):
        # Hold of 2 discount credits limits the discount: paid part 10 - 2.
        assert credit_holds.paid_part(10.0, 5.0, 7.0) == pytest.approx(8.0)
        # Plenty of discount: paid part (1 - d) x cost.
        assert credit_holds.paid_part(4.0, 5.0, 10.0) == pytest.approx(2.0)


# --- Handler pre-check --------------------------------------------------------


@pytest.mark.asyncio
async def test_precheck_with_switches_off_makes_no_new_db_call():
    h = _handler()
    with (
        patch("src.handlers.chat_handler.estimate_and_check_credits", return_value=_allowed()),
        patch(HOLD) as hold,
        patch(DEBT_LOOKUP) as lookup,
    ):
        assert await h._check_credit_sufficiency("openai/gpt-4o", [], 100) == 100
    hold.assert_not_called()
    lookup.assert_not_called()
    assert h._credit_hold_ref is None


@pytest.mark.asyncio
async def test_precheck_passes_tools_and_tool_choice_to_the_estimate():
    h = _handler()
    tools = [{"type": "function", "function": {"name": "t", "parameters": {}}}]
    with patch(
        "src.handlers.chat_handler.estimate_and_check_credits", return_value=_allowed()
    ) as check:
        await h._check_credit_sufficiency("openai/gpt-4o", [], 100, tools, "required")
    assert check.call_args.kwargs["tools"] == tools
    assert check.call_args.kwargs["tool_choice"] == "required"
    assert h._precheck_input_tokens == 1234


@pytest.mark.asyncio
async def test_the_handler_passes_the_request_tools_to_its_precheck():
    h = _handler()
    tools = [{"type": "function", "function": {"name": "t", "parameters": {}}}]
    req = InternalChatRequest(
        messages=[InternalMessage(role="user", content="hi")],
        model="openai/gpt-4o",
        stream=False,
        tools=tools,
    )
    seen = {}

    async def _check(*args, **kwargs):
        seen.update(kwargs)
        raise HTTPException(status_code=402, detail="stop here")

    with (
        patch.object(h, "_initialize_user_context", AsyncMock()),
        patch.object(h, "_check_credit_sufficiency", _check),
        patch.object(h, "_save_request_record"),
    ):
        with pytest.raises(HTTPException):
            await h.process(req)
    assert seen["tools"] == tools


@pytest.mark.asyncio
async def test_precheck_holds_the_maximum_paid_cost(monkeypatch):
    _on(monkeypatch, debt=False)
    h = _handler()
    with (
        patch("src.handlers.chat_handler.estimate_and_check_credits", return_value=_allowed(2.5)),
        patch(HOLD, return_value=Decimal("2.5")) as hold,
    ):
        assert await h._check_credit_sufficiency("openai/gpt-4o", [], 100) == 100
    assert hold.call_args.args[0] == 1
    assert hold.call_args.args[1] == Decimal("2.5")
    assert hold.call_args.args[2] == REF
    assert h._credit_hold_ref == REF


@pytest.mark.asyncio
async def test_a_refused_hold_is_a_402(monkeypatch):
    _on(monkeypatch, debt=False)
    h = _handler()
    with (
        patch("src.handlers.chat_handler.estimate_and_check_credits", return_value=_allowed()),
        patch(HOLD, return_value=Decimal(0)),
    ):
        with pytest.raises(HTTPException) as exc:
            await h._check_credit_sufficiency("openai/gpt-4o", [], 100)
    assert exc.value.status_code == 402
    assert h._credit_hold_ref is None


@pytest.mark.asyncio
async def test_a_request_that_fails_after_the_hold_releases_it(monkeypatch):
    _on(monkeypatch, debt=False)
    h = _handler()
    req = InternalChatRequest(
        messages=[InternalMessage(role="user", content="hi")], model="openai/gpt-4o"
    )

    async def _check(*args, **kwargs):
        h._credit_hold_ref = REF
        return 100

    with (
        patch.object(h, "_initialize_user_context", AsyncMock()),
        patch.object(h, "_check_credit_sufficiency", _check),
        patch(
            "src.handlers.chat_handler.get_selector",
            side_effect=RuntimeError("provider down"),
        ),
        patch.object(h, "_save_request_record"),
        patch(RELEASE, return_value=Decimal("1")) as release,
    ):
        with pytest.raises(RuntimeError):
            await h.process(req)
    release.assert_called_once_with(REF, 1)
    assert h._credit_hold_ref is None


@pytest.mark.asyncio
async def test_an_account_with_an_unpaid_balance_gets_outstanding_balance(monkeypatch):
    _on(monkeypatch, holds=False)
    h = _handler()
    with (
        patch(DEBT_LOOKUP, return_value=True),
        patch("src.handlers.chat_handler.estimate_and_check_credits") as check,
    ):
        with pytest.raises(HTTPException) as exc:
            await h._check_credit_sufficiency("openai/gpt-4o", [], 100)
    assert exc.value.status_code == 402
    assert exc.value.detail["error"]["code"] == "outstanding_balance"
    assert "unpaid balance" in exc.value.detail["error"]["message"]
    check.assert_not_called()


@pytest.mark.asyncio
async def test_free_models_are_not_refused_for_an_unpaid_balance(monkeypatch):
    _on(monkeypatch)
    h = _handler()
    with (
        patch("src.routes.chat_helpers.is_free_model", return_value=True),
        patch(DEBT_LOOKUP, return_value=True) as lookup,
    ):
        assert await h._check_credit_sufficiency("x/free:free", [], 100) == 100
    lookup.assert_not_called()


# --- Charging after delivery -------------------------------------------------


@pytest.mark.asyncio
async def test_the_handler_asks_for_debt_only_with_the_switch_on(monkeypatch):
    h = _handler()
    with (
        patch("src.handlers.chat_handler.deduct_credits") as deduct,
        patch("src.handlers.chat_handler.record_usage"),
    ):
        await h._charge_user(0.5, "m", 1, 1)
        assert "allow_debt" not in deduct.call_args.kwargs
        _on(monkeypatch)
        await h._charge_user(0.5, "m", 1, 1)
        assert deduct.call_args.kwargs["allow_debt"] is True


def _client(rpc_error="insufficient_credits", balance=0.4):
    client = MagicMock()

    def table(name):
        t = MagicMock()
        res = MagicMock()
        if name == "api_keys_new":
            res.data = [{"user_id": 7}]
        elif name == "credit_transactions":
            res.data = []
        else:
            res.data = [
                {"id": 7, "subscription_allowance": 0, "purchased_credits": balance, "tier": "pro"}
            ]
        t.select.return_value.eq.return_value.execute.return_value = res
        return t

    client.table.side_effect = table
    calls = []

    def rpc(name, params):
        calls.append((name, params))
        out = MagicMock()
        out.execute.return_value.data = {"success": False, "error": rpc_error, "new_balance": 0.4}
        return out

    client.rpc.side_effect = rpc
    client.calls = calls
    return client


def _deduct(client, **kwargs):
    from src.db.users import deduct_credits

    with (
        patch("src.db.users.get_supabase_client", return_value=client),
        patch("src.db.plans.is_admin_tier_user", return_value=False),
        patch("src.services.daily_usage_limiter.enforce_daily_usage_limit"),
        patch("src.db.users.invalidate_user_cache"),
        patch("src.db.credit_transactions.get_supabase_client", return_value=client, create=True),
    ):
        deduct_credits("key", 2.0, "Chat completion - m", {"request_id": REF}, REF, **kwargs)


def test_a_shortfall_after_delivery_is_charged_and_recorded_as_debt(monkeypatch):
    _on(monkeypatch, holds=False)
    client = _client()
    with patch(
        CHARGE_WITH_DEBT,
        return_value={"success": True, "charged_usd": 0.4, "debt_usd": 1.6},
    ) as charge:
        _deduct(client, allow_debt=True)
    args = charge.call_args.args
    assert args[0] == 7 and args[1] == 2.0 and args[5] == REF
    assert credit_holds._debt_cache[7][1] is True


def test_with_the_switch_off_a_shortfall_is_refused_as_before():
    client = _client()
    with patch(CHARGE_WITH_DEBT) as charge:
        with pytest.raises(RuntimeError, match="Insufficient credits"):
            _deduct(client, allow_debt=True)
    charge.assert_not_called()
    assert [name for name, _ in client.calls] == ["atomic_deduct_credits"]


def test_without_allow_debt_a_shortfall_is_refused_even_with_the_switch_on(monkeypatch):
    _on(monkeypatch, holds=False)
    client = _client()
    with patch(CHARGE_WITH_DEBT) as charge:
        with pytest.raises(RuntimeError, match="Insufficient credits"):
            _deduct(client)
    charge.assert_not_called()


def test_charge_with_debt_wrapper_raises_on_a_refusal():
    from src.db import credit_holds as db

    client = MagicMock()
    client.rpc.return_value.execute.return_value.data = {"success": False, "error": "x"}
    with patch("src.db.credit_holds.get_supabase_client", return_value=client):
        with pytest.raises(db.CreditHoldUnavailableError):
            db.charge_with_debt(1, 1.0, "api_usage", "d", {}, REF)
