"""Streams are billed on the provider's usage, not the len/4 estimate.

Regression: usage was read only from chunks that carried choices. OpenAI's
include_usage chunk is `choices: []`, so it was dropped; and include_usage was
never requested, so most streams carried no usage at all.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.handlers import chat_handler
from src.handlers.chat_handler import ChatInferenceHandler
from src.schemas.internal.chat import InternalChatRequest, InternalMessage


def _chunk(content=None, finish=None):
    delta = SimpleNamespace(content=content, role=None, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)], usage=None)


def _usage_only(prompt, completion, **extra):
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=extra.pop("total_tokens", prompt + completion),
            **extra,
        ),
    )


def _req(stream_options=None):
    return InternalChatRequest(
        messages=[InternalMessage(role="user", content="x" * 400)],
        model="openai/gpt-4o",
        stream=True,
        stream_options=stream_options,
    )


class _Env:
    def __init__(self, provider, chunks):
        self.provider = provider
        self.chunks = chunks
        self.kwargs = None
        self.cost = MagicMock(
            side_effect=lambda m, pm, p, c, *a, **k: ((p + c) * 1e-5, p * 1e-5, c * 1e-5)
        )

    def __enter__(self):
        h = ChatInferenceHandler(api_key="gw", background_tasks=None, request=None)
        h.user = {"id": 1, "key_id": 9}
        h.is_anonymous = False
        h._billing_ref = lambda: "ref-1"
        self.h = h
        env = self

        async def _noop(*a, **k):
            return None

        async def _credit(*a, **k):
            return 100

        def _stream(provider, model, messages, **kwargs):
            env.kwargs = kwargs

            async def gen():
                for c in env.chunks:
                    yield c

            return gen()

        self._patches = [
            patch.object(h, "_initialize_user_context", _noop),
            patch.object(h, "_check_credit_sufficiency", _credit),
            patch.object(h, "_call_provider_stream", _stream),
            patch(
                "src.handlers.chat_handler.get_selector",
                return_value=MagicMock(registry=MagicMock(get_model=MagicMock(return_value=None))),
            ),
            patch("src.handlers.chat_handler._loss_proof_cost_split", self.cost),
            patch(
                "src.services.model_transformations.detect_provider_from_model_id",
                return_value=self.provider,
            ),
            patch(
                "src.services.model_transformations.transform_model_id", side_effect=lambda m, p: m
            ),
            patch("src.handlers.chat_handler.deduct_credits"),
            patch("src.handlers.chat_handler.record_usage"),
            patch("src.handlers.chat_handler.save_chat_completion_request_with_cost"),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()

    async def run(self, req):
        out = [c async for c in self.h.process_stream(req)]
        pending = list(chat_handler._BILLING_TASKS)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return out

    @property
    def billed(self):
        _, _, prompt, completion = self.cost.call_args.args[:4]
        return prompt, completion


@pytest.mark.asyncio
async def test_usage_only_final_chunk_is_billed_not_estimated():
    chunks = [_chunk("hi"), _chunk(None, finish="stop"), _usage_only(120, 7)]
    with _Env("openai", chunks) as env:
        await env.run(_req())
    assert env.billed == (120, 7)  # the estimate would have been (100, 1)


@pytest.mark.asyncio
async def test_include_usage_is_requested_from_verified_providers():
    with _Env("openai", [_chunk("hi", finish="stop")]) as env:
        await env.run(_req())
    assert env.kwargs["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_client_stream_options_are_kept_and_extended():
    with _Env("openai", [_chunk("hi", finish="stop")]) as env:
        await env.run(_req(stream_options={"include_usage": False, "other": 1}))
    assert env.kwargs["stream_options"] == {"include_usage": True, "other": 1}


@pytest.mark.asyncio
async def test_unverified_provider_is_not_sent_stream_options():
    with _Env("groq", [_chunk("hi", finish="stop")]) as env:
        await env.run(_req())
    assert "stream_options" not in env.kwargs


@pytest.mark.asyncio
async def test_client_without_include_usage_sees_no_extra_chunk():
    chunks = [_chunk("hi", finish="stop"), _usage_only(120, 7)]
    with _Env("openai", chunks) as env:
        out = await env.run(_req())
    assert len(out) == 1


@pytest.mark.asyncio
async def test_client_with_include_usage_gets_the_usage_chunk():
    chunks = [_chunk("hi", finish="stop"), _usage_only(120, 7)]
    with _Env("openai", chunks) as env:
        out = await env.run(_req(stream_options={"include_usage": True}))
    assert out[-1].usage.prompt_tokens == 120 and out[-1].usage.completion_tokens == 7


@pytest.mark.asyncio
async def test_xai_reasoning_outside_completion_is_billed():
    chunks = [
        _chunk("ok", finish="stop"),
        _usage_only(10, 62, reasoning_tokens=121, total_tokens=193),
    ]
    with _Env("xai", chunks) as env:
        await env.run(_req())
    assert env.billed == (10, 183)


@pytest.mark.asyncio
async def test_openai_reasoning_inside_completion_is_not_double_billed():
    chunks = [
        _chunk("ok", finish="stop"),
        _usage_only(
            30,
            1290,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=1280),
        ),
    ]
    with _Env("openai", chunks) as env:
        await env.run(_req())
    assert env.billed == (30, 1290)


def _source_count(provider, path, source):
    from src.services.metrics.prometheus_metrics import billing_usage_source

    return billing_usage_source.labels(provider=provider, path=path, source=source)._value.get()


@pytest.mark.asyncio
async def test_usage_source_metric_counts_provider_vs_estimate():
    before_p = _source_count("deepinfra", "stream", "provider")
    before_e = _source_count("deepinfra", "stream", "estimated")
    with _Env("deepinfra", [_chunk("hi", finish="stop"), _usage_only(120, 7)]) as env:
        await env.run(_req())
    with _Env("deepinfra", [_chunk("hi", finish="stop")]) as env:
        await env.run(_req())
    assert _source_count("deepinfra", "stream", "provider") == before_p + 1
    assert _source_count("deepinfra", "stream", "estimated") == before_e + 1
