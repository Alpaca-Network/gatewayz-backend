"""Anonymous-dispatch upstream identity firewall (gatewayz-backend#2262 #2265
fix round 1, docs/security/ANONYMITY_THREAT_MODEL.md G1).

chat_dispatch.py's "anonymous users: keep existing provider routing logic"
branches (both streaming and non-streaming) built their own provider kwargs
(`optional`, sourced from chat_request.py's prepare_upstream_request, which
forwards the client's `user` field verbatim) and called
`PROVIDER_ROUTING[provider]["request"/"stream"]` directly with them --
`scrub_upstream_kwargs` was never applied on this path, unlike the
authenticated path (chat_handler.py). These tests drive `dispatch_streaming`/
`dispatch_non_streaming` directly with `is_anonymous=True` (the established
convention in this test directory -- see
tests/routes/test_chat_dispatch_non_streaming_failover.py's docstring -- a
full TestClient hit of the anonymous provider-dispatch path needs the entire
legacy anonymous rate-limit/credit/stream_generator machinery, which nothing
else in this suite drives either) and a fake `PROVIDER_ROUTING` entry that
captures exactly the kwargs it was called with.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.routes import chat_dispatch
from src.routes.chat_dispatch import dispatch_non_streaming, dispatch_streaming

SENTINEL_USER = "canary-end-user"


def _fake_provider_routing(capture: list[dict]) -> dict:
    def request_func(messages, model, **kwargs):
        capture.append(kwargs)
        return object()

    def stream_func(messages, model, **kwargs):
        capture.append(kwargs)
        return iter([])

    def process_func(raw):
        return {
            "id": "id-1",
            "object": "chat.completion",
            "created": 1,
            "model": "some-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    return {"request": request_func, "stream": stream_func, "process": process_func}


def _fake_request(billing_ref: str | None):
    return SimpleNamespace(state=SimpleNamespace(billing_ref=billing_ref))


def _non_streaming_kwargs(**overrides):
    base = {
        "is_anonymous": True,
        "provider_chain": ["deepinfra"],
        "messages": [{"role": "user", "content": "hi"}],
        "original_model": "deepinfra/some-model",
        "optional": {"temperature": 0.1, "user": SENTINEL_USER},
        "model": "deepinfra/some-model",
        "provider": "deepinfra",
        "api_key": None,
        "background_tasks": None,
        "request": _fake_request(None),
        "user": None,
        "trial": {"is_trial": False},
    }
    base.update(overrides)
    return base


def _streaming_kwargs(**overrides):
    base = {
        "is_anonymous": True,
        "provider_chain": ["deepinfra"],
        "messages": [{"role": "user", "content": "hi"}],
        "original_model": "deepinfra/some-model",
        "optional": {"temperature": 0.1, "user": SENTINEL_USER},
        "api_key": None,
        "api_key_id": None,
        "background_tasks": None,
        "request": _fake_request(None),
        "request_id": "req-1",
        "rl_pre": None,
        "tracker": None,
        "user": None,
        "trial": {"is_trial": False},
        "environment_tag": "live",
        "session_id": None,
        "rate_limit_mgr": None,
        "client_ip": "127.0.0.1",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_anonymous_non_streaming_scrubs_user_field():
    capture: list[dict] = []
    fake_routing = {"deepinfra": _fake_provider_routing(capture)}
    with (
        patch.object(chat_dispatch, "PROVIDER_ROUTING", fake_routing),
        patch.object(chat_dispatch, "calculate_cost_async", AsyncMock(return_value=0.0)),
    ):
        await dispatch_non_streaming(**_non_streaming_kwargs())

    assert len(capture) == 1
    assert "user" not in capture[0]
    assert SENTINEL_USER not in str(capture[0])


@pytest.mark.asyncio
async def test_anonymous_non_streaming_community_gets_billing_ref_deepinfra_does_not():
    capture: list[dict] = []
    request = _fake_request("server-minted-ref-123")

    with (
        patch.object(
            chat_dispatch, "PROVIDER_ROUTING", {"community": _fake_provider_routing(capture)}
        ),
        patch.object(chat_dispatch, "calculate_cost_async", AsyncMock(return_value=0.0)),
    ):
        await dispatch_non_streaming(
            **_non_streaming_kwargs(
                provider_chain=["community"],
                original_model="community/some-model",
                model="community/some-model",
                provider="community",
                request=request,
            )
        )
    assert capture[0]["_gatewayz_billing_ref"] == "server-minted-ref-123"

    capture.clear()
    with (
        patch.object(
            chat_dispatch, "PROVIDER_ROUTING", {"deepinfra": _fake_provider_routing(capture)}
        ),
        patch.object(chat_dispatch, "calculate_cost_async", AsyncMock(return_value=0.0)),
    ):
        await dispatch_non_streaming(**_non_streaming_kwargs(request=request))
    assert "_gatewayz_billing_ref" not in capture[0]


@pytest.mark.asyncio
async def test_anonymous_streaming_scrubs_user_field():
    capture: list[dict] = []
    fake_routing = {"deepinfra": _fake_provider_routing(capture)}
    with patch.object(chat_dispatch, "PROVIDER_ROUTING", fake_routing):
        await dispatch_streaming(**_streaming_kwargs())

    assert len(capture) == 1
    assert "user" not in capture[0]
    assert SENTINEL_USER not in str(capture[0])


@pytest.mark.asyncio
async def test_anonymous_streaming_injects_billing_ref_for_community():
    capture: list[dict] = []
    request = _fake_request("server-minted-ref-456")
    with patch.object(
        chat_dispatch, "PROVIDER_ROUTING", {"community": _fake_provider_routing(capture)}
    ):
        await dispatch_streaming(
            **_streaming_kwargs(
                provider_chain=["community"],
                original_model="community/some-model",
                request=request,
            )
        )

    assert capture[0]["_gatewayz_billing_ref"] == "server-minted-ref-456"
    assert "user" not in capture[0]
