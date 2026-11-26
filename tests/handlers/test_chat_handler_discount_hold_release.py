"""A discount credits hold placed at the pre-check is released when the
request ends without being billed (src/handlers/chat_handler.py).

The pre-check (``_check_credit_sufficiency``) records the billing ref its hold
was placed under; billing settles that hold; every other exit -- a provider
error, a refused or failed charge, nothing to charge, a cancel before the
provider call -- releases it. Releasing is best effort and never raises.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.handlers.chat_handler import ChatInferenceHandler
from src.schemas.internal.chat import InternalChatRequest, InternalMessage
from src.services.discount_credits import billing

RELEASE = "src.services.discount_credits.billing.release_for_request_async"
REF = "6f9b1f0e-3c2a-4d5e-9b8a-1c2d3e4f5a6b"


def _handler():
    h = ChatInferenceHandler(api_key="gw_key", background_tasks=None, request=None)
    h.user = {"id": 1, "key_id": 9}
    h.is_anonymous = False
    h._billing_ref = lambda: REF
    return h


def _req(stream: bool):
    return InternalChatRequest(
        messages=[InternalMessage(role="user", content="hello")],
        model="openai/gpt-4o",
        stream=stream,
    )


def _holding_precheck(h):
    """A pre-check that placed a hold, as the real one does when discount
    credits apply to the account."""

    async def _check(*args, **kwargs):
        h._discount_hold_ref = REF
        return kwargs.get("max_tokens")

    return _check


async def _noop(*args, **kwargs):
    return None


@pytest.mark.asyncio
async def test_the_precheck_records_the_ref_its_hold_was_placed_under():
    h = _handler()
    h.user.update({"subscription_allowance": 0, "purchased_credits": 5})
    with (
        patch.object(billing.Config, "VAULT_DISCOUNT_BILLING_ENABLED", True),
        patch.object(billing.Config, "VAULT_DISCOUNT_ALL_ACCOUNTS", True),
        patch(
            "src.services.billing.credit_precheck.calculate_maximum_cost",
            return_value=(1.0, 0, 0),
        ),
        patch(
            "src.services.discount_credits.billing.discount_aware_credits_async",
            AsyncMock(return_value=5.0),
        ) as hold,
        patch(
            "src.handlers.chat_handler.estimate_and_check_credits",
            return_value={"allowed": True, "max_cost": 1.0},
        ),
    ):
        await h._check_credit_sufficiency("openai/gpt-4o", [], 100)
    assert h._discount_hold_ref == REF
    assert hold.await_args.args[3] == REF


@pytest.mark.asyncio
async def test_no_hold_no_release():
    h = _handler()
    with patch(RELEASE, AsyncMock()) as release:
        await h._release_discount_hold()
    release.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_provider_error_on_a_non_streaming_request_releases_the_hold():
    h = _handler()
    with (
        patch.object(h, "_initialize_user_context", _noop),
        patch.object(h, "_capture_client_messages", _noop),
        patch.object(h, "_check_credit_sufficiency", _holding_precheck(h)),
        patch("src.handlers.chat_handler.get_selector", side_effect=RuntimeError("down")),
        patch.object(h, "_save_request_record"),
        patch(RELEASE, AsyncMock(return_value=Decimal("0.99"))) as release,
    ):
        with pytest.raises(RuntimeError):
            await h.process(_req(stream=False))
    release.assert_awaited_once_with(1, REF)
    assert h._discount_hold_ref is None


@pytest.mark.asyncio
async def test_a_provider_error_on_a_stream_releases_the_hold():
    h = _handler()

    def _broken_stream(*args, **kwargs):
        raise RuntimeError("provider refused the stream")

    with (
        patch.object(h, "_initialize_user_context", _noop),
        patch.object(h, "_capture_client_messages", _noop),
        patch.object(h, "_check_credit_sufficiency", _holding_precheck(h)),
        patch.object(h, "_call_provider_stream", _broken_stream),
        patch(
            "src.handlers.chat_handler.get_selector",
            return_value=MagicMock(registry=MagicMock(get_model=MagicMock(return_value=None))),
        ),
        patch(
            "src.services.model_transformations.detect_provider_from_model_id",
            return_value="openai",
        ),
        patch(
            "src.services.model_transformations.transform_model_id",
            side_effect=lambda m, p: m,
        ),
        patch.object(h, "_save_request_record"),
        patch(RELEASE, AsyncMock(return_value=Decimal("0.99"))) as release,
    ):
        with pytest.raises(RuntimeError):
            async for _ in h.process_stream(_req(stream=True)):
                pass
    release.assert_awaited_once_with(1, REF)


@pytest.mark.asyncio
async def test_a_stream_cancelled_before_the_provider_call_releases_the_hold():
    h = _handler()

    async def _cancelled(*args, **kwargs):
        h._discount_hold_ref = REF
        raise asyncio.CancelledError

    with (
        patch.object(h, "_initialize_user_context", _noop),
        patch.object(h, "_capture_client_messages", _noop),
        patch.object(h, "_check_credit_sufficiency", _cancelled),
        patch(RELEASE, AsyncMock(return_value=Decimal("0.99"))) as release,
    ):
        with pytest.raises(asyncio.CancelledError):
            async for _ in h.process_stream(_req(stream=True)):
                pass
        from src.handlers import chat_handler

        await asyncio.gather(*list(chat_handler._BILLING_TASKS), return_exceptions=True)
    release.assert_awaited_once_with(1, REF)


@pytest.mark.asyncio
async def test_nothing_to_charge_releases_the_hold():
    """deduct_credits returns at once for a zero cost (a free model, a BYOK
    request with no routing fee), so the hold would never be settled."""
    h = _handler()
    h._discount_hold_ref = REF
    with (
        patch("src.handlers.chat_handler.deduct_credits") as deduct,
        patch("src.handlers.chat_handler.record_usage"),
        patch(RELEASE, AsyncMock(return_value=Decimal("0.99"))) as release,
    ):
        await h._charge_user(0.0, "m", 0, 0)
    release.assert_awaited_once_with(1, REF)
    deduct.assert_called_once()
    assert h._discount_hold_ref is None


@pytest.mark.asyncio
async def test_a_byok_request_with_no_fee_releases_the_hold():
    h = _handler()
    h._discount_hold_ref = REF
    h.is_byok = True
    with (
        patch("src.services.byok.byok_routing_fee", return_value=0.0),
        patch("src.handlers.chat_handler.deduct_credits"),
        patch("src.handlers.chat_handler.record_usage"),
        patch(RELEASE, AsyncMock(return_value=Decimal("0.99"))) as release,
    ):
        await h._charge_user(h._apply_byok_fee(1.25), "m", 10, 10)
    release.assert_awaited_once_with(1, REF)


@pytest.mark.asyncio
async def test_a_billed_request_settles_its_hold_without_a_release():
    h = _handler()
    h._discount_hold_ref = REF
    with (
        patch("src.handlers.chat_handler.deduct_credits") as deduct,
        patch("src.handlers.chat_handler.record_usage"),
        patch(RELEASE, AsyncMock()) as release,
    ):
        await h._charge_user(0.5, "m", 10, 10)
    deduct.assert_called_once()
    release.assert_not_awaited()
    assert h._discount_hold_ref is None


@pytest.mark.asyncio
async def test_a_refused_charge_releases_the_hold():
    h = _handler()
    h._discount_hold_ref = REF
    with (
        patch(
            "src.handlers.chat_handler.deduct_credits",
            side_effect=RuntimeError("Failed to deduct credits: Insufficient credits"),
        ),
        patch(RELEASE, AsyncMock(return_value=Decimal(0))) as release,
    ):
        with pytest.raises(RuntimeError):
            await h._charge_user(0.5, "m", 10, 10)
    release.assert_awaited_once_with(1, REF)


@pytest.mark.asyncio
async def test_a_release_failure_never_raises():
    h = _handler()
    h._discount_hold_ref = REF
    with patch(RELEASE, AsyncMock(side_effect=RuntimeError("db down"))):
        await h._release_discount_hold()
    assert h._discount_hold_ref is None


def test_release_for_request_is_a_no_op_while_the_discount_is_off():
    with (
        patch.object(billing.Config, "VAULT_DISCOUNT_BILLING_ENABLED", False),
        patch("src.db.inference_discount.release_discount") as release,
    ):
        assert billing.release_for_request(1, REF) == Decimal(0)
        assert asyncio.run(billing.release_for_request_async(1, REF)) == Decimal(0)
    release.assert_not_called()


def test_release_for_request_calls_the_db_and_swallows_errors():
    with (
        patch.object(billing.Config, "VAULT_DISCOUNT_BILLING_ENABLED", True),
        patch.object(billing.Config, "VAULT_DISCOUNT_ALL_ACCOUNTS", True),
        patch(
            "src.db.inference_discount.release_discount", return_value=Decimal("0.99")
        ) as release,
    ):
        assert billing.release_for_request(1, REF) == Decimal("0.99")
        release.assert_called_once_with(REF, 1)
        release.side_effect = RuntimeError("boom")
        assert billing.release_for_request(1, REF) == Decimal(0)
        # No UUID request id: nothing could have been held.
        release.reset_mock()
        assert billing.release_for_request(1, "route-1") == Decimal(0)
        release.assert_not_called()


def test_release_discount_rpc_shape():
    from src.db import inference_discount

    client = MagicMock()
    client.rpc.return_value.execute.return_value = SimpleNamespace(data="0.990000")
    with patch.object(inference_discount, "get_supabase_client", return_value=client):
        assert inference_discount.release_discount(REF, 1) == Decimal("0.99")
    client.rpc.assert_called_once_with("discount_release", {"p_request_id": REF, "p_user_id": 1})
