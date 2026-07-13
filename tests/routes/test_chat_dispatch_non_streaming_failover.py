"""Regression tests: authenticated non-streaming dispatch must fail over across
provider_chain, matching dispatch_streaming's behavior. Previously it attempted
exactly one provider (the stale, pre-smart-router `provider` value) and never
consulted provider_chain at all.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException

from src.handlers.chat_handler import ChatInferenceHandler
from src.routes.chat_dispatch import dispatch_non_streaming
from src.schemas.internal.chat import InternalChatResponse, InternalUsage


def _make_response(provider_used: str, model: str) -> InternalChatResponse:
    return InternalChatResponse(
        id="resp-1",
        model=model,
        content="OK",
        usage=InternalUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        finish_reason="stop",
        provider_used=provider_used,
        cost_usd=0.0,
        input_cost_usd=0.0,
        output_cost_usd=0.0,
    )


def _kwargs(**overrides):
    base = dict(
        is_anonymous=False,
        provider_chain=["deepinfra", "openrouter"],
        messages=[{"role": "user", "content": "hi"}],
        original_model="allenai/Olmo-3.1-32B-Instruct",
        optional={},
        model="allenai/Olmo-3.1-32B-Instruct",
        provider="openrouter",  # stale pre-routing value — must NOT be trusted anymore
        api_key="test-key",
        background_tasks=BackgroundTasks(),
        request=None,
        user={"id": 1},
        trial={"is_trial": False},
    )
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_fails_over_to_next_provider_on_failover_eligible_error():
    """First provider raises a 401 (failover-eligible) -> second provider serves it."""
    calls = []

    async def fake_process(self, internal_request):
        calls.append(internal_request.provider)
        if internal_request.provider == "deepinfra":
            raise HTTPException(status_code=401, detail="dead key")
        return _make_response("openrouter", internal_request.model)

    with patch.object(ChatInferenceHandler, "process", new=fake_process):
        processed, provider, model = await dispatch_non_streaming(**_kwargs())

    assert calls == ["deepinfra", "openrouter"]
    assert provider == "openrouter"
    assert processed["choices"][0]["message"]["content"] == "OK"


@pytest.mark.asyncio
async def test_succeeds_on_first_provider_without_trying_others():
    calls = []

    async def fake_process(self, internal_request):
        calls.append(internal_request.provider)
        return _make_response("deepinfra", internal_request.model)

    with patch.object(ChatInferenceHandler, "process", new=fake_process):
        processed, provider, model = await dispatch_non_streaming(**_kwargs())

    assert calls == ["deepinfra"]
    assert provider == "deepinfra"


@pytest.mark.asyncio
async def test_raises_after_exhausting_all_providers():
    async def fake_process(self, internal_request):
        raise HTTPException(status_code=401, detail="dead key")

    with patch.object(ChatInferenceHandler, "process", new=fake_process):
        with pytest.raises(HTTPException) as exc_info:
            await dispatch_non_streaming(**_kwargs())

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_does_not_fail_over_on_non_failover_eligible_error():
    """A 400 (bad request) must not trigger failover to the next provider."""
    calls = []

    async def fake_process(self, internal_request):
        calls.append(internal_request.provider)
        raise HTTPException(status_code=400, detail="bad request")

    with patch.object(ChatInferenceHandler, "process", new=fake_process):
        with pytest.raises(HTTPException) as exc_info:
            await dispatch_non_streaming(**_kwargs())

    assert calls == ["deepinfra"]
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_raises_502_when_provider_chain_is_empty():
    with pytest.raises(HTTPException) as exc_info:
        await dispatch_non_streaming(**_kwargs(provider_chain=[]))
    assert exc_info.value.status_code == 502
