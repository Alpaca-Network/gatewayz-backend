"""Regression tests for review round 1 on PR #2282 (M3 W-C): the dominant
authenticated /v1/chat/completions path runs through ChatInferenceHandler, not
chat.py directly — G4 (billing idempotency) and G5 (error_message scrubbing)
must hold here too, not just in chat.py.

See docs/security/ANONYMITY_THREAT_MODEL.md G4/G5 and the WC-review findings.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.handlers.chat_handler import ChatInferenceHandler
from src.schemas.internal.chat import InternalChatRequest, InternalMessage

SENTINEL_PROMPT_FRAGMENT = "canary prompt fragment 424242"


def _make_handler(billing_ref="billing-ref-canary"):
    request = SimpleNamespace(state=SimpleNamespace(billing_ref=billing_ref))
    handler = ChatInferenceHandler(api_key="gw_test_key", background_tasks=None, request=request)
    handler.user = {"id": 1, "key_id": 42}
    handler.is_anonymous = False
    return handler


def _make_request(stream: bool = False) -> InternalChatRequest:
    return InternalChatRequest(
        messages=[InternalMessage(role="user", content="hi")],
        model="openai/gpt-4o",
        stream=stream,
    )


# ============================================================================
# G4: deduct_credits must receive the billing_ref as its idempotency key.
# ============================================================================


class TestChargeUserBillingRef:
    @pytest.mark.asyncio
    async def test_deduct_credits_receives_billing_ref_as_idempotency_key(self):
        handler = _make_handler(billing_ref="billing-ref-canary")

        with (
            patch("src.handlers.chat_handler.deduct_credits") as mock_deduct,
            patch("src.handlers.chat_handler.record_usage"),
        ):
            await handler._charge_user(
                cost=0.01, model_name="gpt-4o", prompt_tokens=10, completion_tokens=5
            )

        # deduct_credits(api_key, cost, description, metadata, request_id) — positional.
        args, _ = mock_deduct.call_args
        assert args[4] == "billing-ref-canary"
        assert args[3]["request_id"] == "billing-ref-canary"

    @pytest.mark.asyncio
    async def test_falls_back_to_instance_request_id_when_no_billing_ref(self):
        """request.state.billing_ref missing (e.g. handler used outside the
        normal middleware stack) must still pass SOME id, never None — an
        idempotency key of None disables the guard entirely."""
        handler = _make_handler(billing_ref=None)

        with (
            patch("src.handlers.chat_handler.deduct_credits") as mock_deduct,
            patch("src.handlers.chat_handler.record_usage"),
        ):
            await handler._charge_user(
                cost=0.01, model_name="gpt-4o", prompt_tokens=10, completion_tokens=5
            )

        args, _ = mock_deduct.call_args
        assert args[4] == handler.request_id
        assert args[4] is not None

    @pytest.mark.asyncio
    async def test_idempotency_same_billing_ref_twice_yields_one_deduction(self):
        """End-to-end through ChatInferenceHandler (not chat.py): the real
        deduct_credits (src/db/users.py, unmocked) short-circuits via
        get_transaction_by_request_id before touching the DB when a
        transaction already exists for this handler's billing_ref — proving
        G4 actually holds on the dominant authenticated billing path."""
        handler = _make_handler(billing_ref="billing-ref-canary")
        existing_transaction = {"id": 1, "amount": -0.01}

        with (
            patch(
                "src.db.credit_transactions.get_transaction_by_request_id",
                return_value=existing_transaction,
            ),
            patch("src.db.users.get_supabase_client") as mock_get_client,
            patch("src.handlers.chat_handler.record_usage"),
        ):
            await handler._charge_user(
                cost=0.01, model_name="gpt-4o", prompt_tokens=10, completion_tokens=5
            )

        mock_get_client.assert_not_called()


class TestSaveRequestRecordBillingRef:
    def test_request_id_is_billing_ref_not_instance_uuid(self):
        handler = _make_handler(billing_ref="billing-ref-canary")
        assert handler.request_id != "billing-ref-canary"  # sanity: genuinely different values

        with patch("src.handlers.chat_handler.save_chat_completion_request_with_cost") as mock_save:
            handler._save_request_record(
                model_name="gpt-4o",
                provider_name="openai",
                input_tokens=10,
                output_tokens=5,
                status="completed",
            )

        assert mock_save.call_args.kwargs["request_id"] == "billing-ref-canary"

    def test_falls_back_to_instance_request_id_when_no_billing_ref(self):
        handler = _make_handler(billing_ref=None)

        with patch("src.handlers.chat_handler.save_chat_completion_request_with_cost") as mock_save:
            handler._save_request_record(
                model_name="gpt-4o", provider_name="openai", input_tokens=10, output_tokens=5
            )

        assert mock_save.call_args.kwargs["request_id"] == handler.request_id


# ============================================================================
# G5: error_message must never echo raw exception text on either path.
# ============================================================================


class TestErrorMessageScrubbingBothPaths:
    @pytest.mark.asyncio
    async def test_non_streaming_process_scrubs_sentinel_on_failure(self):
        handler = _make_handler()

        with (
            patch.object(
                handler,
                "_initialize_user_context",
                new=AsyncMock(side_effect=ValueError(f"boom: {SENTINEL_PROMPT_FRAGMENT}")),
            ),
            patch("src.handlers.chat_handler.save_chat_completion_request_with_cost") as mock_save,
        ):
            with pytest.raises(ValueError):
                await handler.process(_make_request(stream=False))

        persisted = mock_save.call_args.kwargs["error_message"]
        assert SENTINEL_PROMPT_FRAGMENT not in persisted
        assert persisted.startswith("ValueError:")

    @pytest.mark.asyncio
    async def test_streaming_process_stream_scrubs_sentinel_on_failure(self):
        handler = _make_handler()

        with (
            patch.object(
                handler,
                "_initialize_user_context",
                new=AsyncMock(side_effect=ValueError(f"boom: {SENTINEL_PROMPT_FRAGMENT}")),
            ),
            patch("src.handlers.chat_handler.save_chat_completion_request_with_cost") as mock_save,
        ):
            with pytest.raises(ValueError):
                async for _ in handler.process_stream(_make_request(stream=True)):
                    pass

        persisted = mock_save.call_args.kwargs["error_message"]
        assert SENTINEL_PROMPT_FRAGMENT not in persisted
        assert persisted.startswith("ValueError:")
