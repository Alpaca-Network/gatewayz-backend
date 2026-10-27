"""The synchronous provider call must not run on the event loop.

Production 2026-10-27 (one uvicorn worker): ChatInferenceHandler.process called
the sync provider client inline, so each upstream round trip blocked every other
request; 8 concurrent simple requests took ~8 s each.
"""

import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.handlers.chat_handler import ChatInferenceHandler
from src.schemas.internal.chat import InternalChatRequest, InternalMessage


class _Stop(Exception):
    pass


@pytest.mark.asyncio
async def test_provider_call_runs_in_a_worker_thread():
    request = SimpleNamespace(state=SimpleNamespace(billing_ref="ref"))
    handler = ChatInferenceHandler(api_key="gw_test_key", background_tasks=None, request=request)
    handler.user = {"id": 1, "key_id": 42}
    handler.is_anonymous = False
    seen = {}

    def fake_call_provider(*args, **kwargs):
        seen["thread"] = threading.current_thread()
        raise _Stop()

    with (
        patch.object(handler, "_initialize_user_context", new=AsyncMock()),
        patch.object(handler, "_capture_client_messages", new=AsyncMock()),
        patch.object(handler, "_check_credit_sufficiency", new=AsyncMock(return_value=None)),
        patch.object(handler, "_call_provider", side_effect=fake_call_provider),
        patch("src.handlers.chat_handler.save_chat_completion_request_with_cost"),
    ):
        with pytest.raises(_Stop):
            await handler.process(
                InternalChatRequest(
                    messages=[InternalMessage(role="user", content="hi")],
                    model="openai/gpt-4o-mini",
                    stream=False,
                )
            )

    assert "thread" in seen, "provider was never called"
    assert seen["thread"] is not threading.main_thread()
