"""Streaming billing must survive a client disconnect / task cancel.

Before the fix, ChatInferenceHandler.process_stream charged only after the
provider loop ran to completion. CancelledError / GeneratorExit are not
``Exception`` subclasses, so an aborted stream skipped billing entirely while
the provider had already generated (and we had already paid for) the tokens.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.handlers.chat_handler import ChatInferenceHandler
from src.schemas.internal.chat import InternalChatRequest, InternalMessage


def _chunk(content=None, usage=None, finish=None):
    delta = SimpleNamespace(content=content, role=None, tool_calls=None)
    choice = SimpleNamespace(delta=delta, finish_reason=finish)
    return SimpleNamespace(choices=[choice], usage=usage)


def _handler():
    request = SimpleNamespace(state=SimpleNamespace(billing_ref="ref-1"), is_disconnected=None)
    h = ChatInferenceHandler(api_key="gw_key", background_tasks=None, request=None)
    h.request = None
    h.user = {"id": 1, "key_id": 9}
    h.is_anonymous = False
    h._billing_ref = lambda: "ref-1"
    return h


def _req():
    return InternalChatRequest(
        messages=[InternalMessage(role="user", content="a prompt of some length here")],
        model="openai/gpt-4o",
        stream=True,
    )


async def _drain_billing():
    from src.handlers import chat_handler

    pending = list(getattr(chat_handler, "_BILLING_TASKS", ()))
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


class _Env:
    """Patch every collaborator of process_stream except the billing path."""

    def __init__(self, handler, chunks, hang_after=False):
        self.handler = handler
        self.chunks = chunks
        self.hang_after = hang_after
        self.deduct = MagicMock()
        self.save = MagicMock()
        self.started = asyncio.Event()

    def _stream(self):
        env = self

        async def gen():
            for c in env.chunks:
                yield c
            env.started.set()
            if env.hang_after:
                await asyncio.sleep(3600)

        return gen()

    def __enter__(self):
        h = self.handler

        async def _noop(*a, **k):
            return None

        async def _credit(*a, **k):
            return 100

        self._patches = [
            patch.object(h, "_initialize_user_context", _noop),
            patch.object(h, "_check_credit_sufficiency", _credit),
            patch.object(h, "_call_provider_stream", lambda *a, **k: self._stream()),
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
            patch("src.handlers.chat_handler.deduct_credits", self.deduct),
            patch("src.handlers.chat_handler.record_usage"),
            patch("src.handlers.chat_handler.save_chat_completion_request_with_cost", self.save),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


@pytest.mark.asyncio
async def test_normal_completion_bills_exactly_once():
    h = _handler()
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
    with _Env(h, [_chunk("hello "), _chunk("world", usage=usage, finish="stop")]) as env:
        async for _ in h.process_stream(_req()):
            pass
        await _drain_billing()
    assert env.deduct.call_count == 1
    assert env.save.call_args.kwargs["status"] == "completed"
    assert "cancelled" not in (env.save.call_args.kwargs.get("metadata") or {})


@pytest.mark.asyncio
async def test_generator_close_mid_stream_bills_once_with_estimate():
    h = _handler()
    content = "x" * 400  # ~100 tokens, no provider usage chunk ever arrives
    with _Env(h, [_chunk(content), _chunk("never seen")]) as env:
        agen = h.process_stream(_req())
        await agen.__anext__()  # client received first chunk...
        await agen.aclose()  # ...then disconnects (GeneratorExit at the yield)
        await _drain_billing()

    assert env.deduct.call_count == 1
    args = env.deduct.call_args.args
    assert args[4] == "ref-1"  # idempotency key
    assert args[3]["completion_tokens"] == 100  # estimated from streamed content
    assert args[3]["prompt_tokens"] >= 1
    kwargs = env.save.call_args.kwargs
    assert kwargs["status"] == "completed"
    assert kwargs["metadata"]["cancelled"] is True
    assert kwargs["output_tokens"] == 100


@pytest.mark.asyncio
async def test_task_cancel_mid_stream_bills_once():
    h = _handler()
    with _Env(h, [_chunk("y" * 80)], hang_after=True) as env:

        async def consume():
            async for _ in h.process_stream(_req()):
                pass

        task = asyncio.create_task(consume())
        await asyncio.wait_for(env.started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await _drain_billing()

    assert env.deduct.call_count == 1
    assert env.deduct.call_args.args[3]["completion_tokens"] == 20
    assert env.save.call_args.kwargs["metadata"]["cancelled"] is True


@pytest.mark.asyncio
async def test_cancel_uses_provider_usage_when_already_received():
    h = _handler()
    usage = SimpleNamespace(prompt_tokens=42, completion_tokens=7)
    with _Env(h, [_chunk("hi", usage=usage), _chunk("more")]) as env:
        agen = h.process_stream(_req())
        await agen.__anext__()
        await agen.aclose()
        await _drain_billing()
    assert env.deduct.call_count == 1
    assert env.deduct.call_args.args[3]["prompt_tokens"] == 42
    assert env.deduct.call_args.args[3]["completion_tokens"] == 7


@pytest.mark.asyncio
async def test_cancel_during_charge_still_completes_single_deduction():
    """Cancellation landing while the normal-path charge is in flight must not
    abort it (shielded) and must not trigger a second charge."""
    h = _handler()
    release = asyncio.Event()
    entered = asyncio.Event()
    loop = asyncio.get_running_loop()
    calls = []

    def slow_deduct(*a, **k):
        calls.append(a)
        loop.call_soon_threadsafe(entered.set)
        import time

        time.sleep(0.2)

    with _Env(h, [_chunk("hello")]) as env:
        env.deduct.side_effect = slow_deduct

        async def consume():
            async for _ in h.process_stream(_req()):
                pass

        task = asyncio.create_task(consume())
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await _drain_billing()
        release.set()

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_anonymous_cancel_is_not_charged():
    h = _handler()
    h.is_anonymous = True
    with _Env(h, [_chunk("hello"), _chunk("more")]) as env:
        agen = h.process_stream(_req())
        await agen.__anext__()
        await agen.aclose()
        await _drain_billing()
    assert env.deduct.call_count == 0
