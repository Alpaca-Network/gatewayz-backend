"""/v1/messages streaming: a client disconnect must still bill exactly once.

The Messages route re-wraps the chat pipeline's SSE iterator. A disconnect
lands at the wrapper's ``yield``; it must close the inner generator chain
(dispatch -> adapter -> ChatInferenceHandler.process_stream) so billing runs.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.handlers import chat_handler
from src.handlers.chat_handler import ChatInferenceHandler
from src.adapters.chat.openai import OpenAIChatAdapter
from src.routes import messages as messages_route
from src.routes.chat_dispatch import _aclose_quiet
from src.schemas.internal.chat import InternalChatRequest, InternalMessage


def _chunk(content):
    delta = SimpleNamespace(content=content, role=None, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=None)], usage=None)


async def _drain():
    pending = list(getattr(chat_handler, "_BILLING_TASKS", ()))
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def _build_chain(handler):
    """Same nesting chat_dispatch builds for an authenticated stream."""
    req = InternalChatRequest(
        messages=[InternalMessage(role="user", content="prompt text")],
        model="openai/gpt-4o",
        stream=True,
    )
    adapter = OpenAIChatAdapter()
    internal = handler.process_stream(req)
    sse = adapter.from_internal_stream(internal)

    async def dispatch_like():
        try:
            async for c in sse:
                yield c
        finally:
            await _aclose_quiet(sse)
            await _aclose_quiet(internal)

    return dispatch_like()


@pytest.mark.asyncio
async def test_messages_stream_disconnect_bills_exactly_once():
    h = ChatInferenceHandler(api_key="gw_key", background_tasks=None, request=None)
    h.user = {"id": 1, "key_id": 9}
    h.is_anonymous = False
    h._billing_ref = lambda: "ref-msg"

    async def provider_stream():
        yield _chunk("z" * 200)
        yield _chunk("never delivered")

    async def _noop(*a, **k):
        return None

    async def _credit(*a, **k):
        return 100

    deduct = MagicMock()
    with (
        patch.object(h, "_initialize_user_context", _noop),
        patch.object(h, "_check_credit_sufficiency", _credit),
        patch.object(h, "_call_provider_stream", lambda *a, **k: provider_stream()),
        patch(
            "src.handlers.chat_handler.get_selector",
            return_value=MagicMock(registry=MagicMock(get_model=MagicMock(return_value=None))),
        ),
        patch(
            "src.handlers.chat_handler._loss_proof_cost_split",
            side_effect=lambda m, pm, p, c, *a, **k: ((p + c) * 1e-5, p * 1e-5, c * 1e-5),
        ),
        patch(
            "src.services.model_transformations.detect_provider_from_model_id",
            return_value="openai",
        ),
        patch(
            "src.services.model_transformations.transform_model_id",
            side_effect=lambda m, p: m,
        ),
        patch("src.handlers.chat_handler.deduct_credits", deduct),
        patch("src.handlers.chat_handler.record_usage"),
        patch("src.handlers.chat_handler.save_chat_completion_request_with_cost"),
    ):
        events = messages_route._stream_anthropic_events(_build_chain(h), "openai/gpt-4o", "msg_1")
        seen = []
        async for ev in events:
            seen.append(ev)
            if "content_block_delta" in ev:
                break  # client disconnects after receiving text
        await events.aclose()
        await _drain()

    assert any("content_block_delta" in e for e in seen)
    assert deduct.call_count == 1
    assert deduct.call_args.args[4] == "ref-msg"
    assert deduct.call_args.args[3]["completion_tokens"] == 50
