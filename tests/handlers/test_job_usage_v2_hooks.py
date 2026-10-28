"""v2 usage-line facts are captured on the real request paths (cost disputes).

Proves the handler records, for a job key: hidden reasoning split out of the
billed completion count, the provider's real finish_reason (streaming included),
a content hash over the client's ORIGINAL messages that the client can rebuild,
and the model whose rates priced the charge. Ordinary keys are untouched.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from starlette.requests import Request

from src.handlers import chat_handler
from src.handlers.chat_handler import ChatInferenceHandler, _loss_proof_cost_split
from src.schemas.internal.chat import InternalChatRequest, InternalMessage
from src.services import job_usage_v2 as v2

JOB = "0x" + "cd" * 32


@pytest.fixture(autouse=True)
def _v2_on(monkeypatch):
    from src.config.config import Config

    monkeypatch.setattr(Config, "JOB_USAGE_V2_ENABLED", True, raising=False)


def _starlette_request(body: bytes, path: str = "/v1/chat/completions") -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    req = Request(
        {"type": "http", "method": "POST", "path": path, "headers": []},
        receive,
    )

    async def connected():
        return False

    req.is_disconnected = connected  # type: ignore[method-assign]
    return req


def _client_hash(raw: bytes, output: str) -> str:
    """What a client computes from exactly what it sent and received."""
    bundle = {"messages": json.loads(raw)["messages"], "output": output}
    return (
        "0x"
        + hashlib.sha256(
            json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
    )


# The client sends fields the gateway normalises away (name=None is dropped,
# content parts are flattened...). The hash must cover what was SENT.
RAW = json.dumps(
    {
        "model": "openai/o4-mini",
        "messages": [
            {"role": "system", "content": "Réponds en français."},
            {"role": "user", "content": [{"type": "text", "text": "2+2 ?"}]},
        ],
    },
    ensure_ascii=False,
).encode()


def _handler(key_name=f"job:{JOB}", body=RAW, path="/v1/chat/completions"):
    h = ChatInferenceHandler(
        api_key="gw_key", background_tasks=None, request=_starlette_request(body, path)
    )
    h.user = {"id": 1, "key_id": 9, "key_name": key_name}
    h.is_anonymous = False
    h._billing_ref = lambda: "ref-1"
    return h


def _req(stream=False):
    return InternalChatRequest(
        messages=[
            InternalMessage(role="system", content="Réponds en français."),
            InternalMessage(role="user", content="2+2 ?"),
        ],
        model="openai/o4-mini",
        stream=stream,
    )


def _priced_split(m, pm, p, c, *a, priced=None, **k):
    if priced is not None:
        priced["model"] = "openai/o4-mini"
    return ((p + c) * 1e-5, p * 1e-5, c * 1e-5)


class _Env:
    def __init__(self, handler, response=None, chunks=None):
        self.h = handler
        self.response = response
        self.chunks = chunks or []
        self.rec = MagicMock()

    def _stream(self):
        async def gen():
            for c in self.chunks:
                yield c

        return gen()

    def __enter__(self):
        h = self.h

        async def _noop(*a, **k):
            return None

        async def _credit(*a, **k):
            return 100

        self._patches = [
            patch.object(h, "_initialize_user_context", _noop),
            patch.object(h, "_check_credit_sufficiency", _credit),
            patch.object(h, "_call_provider", lambda *a, **k: self.response),
            patch.object(h, "_call_provider_stream", lambda *a, **k: self._stream()),
            patch(
                "src.handlers.chat_handler.get_selector",
                return_value=MagicMock(registry=MagicMock(get_model=MagicMock(return_value=None))),
            ),
            patch("src.handlers.chat_handler._loss_proof_cost_split", side_effect=_priced_split),
            patch(
                "src.services.model_transformations.detect_provider_from_model_id",
                return_value="openai",
            ),
            patch(
                "src.services.model_transformations.transform_model_id",
                side_effect=lambda m, p: m,
            ),
            patch("src.handlers.chat_handler.deduct_credits"),
            patch("src.handlers.chat_handler.record_usage"),
            patch("src.handlers.chat_handler.save_chat_completion_request_with_cost"),
            patch("src.handlers.chat_handler.record_job_usage", self.rec),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


def _response(content, finish, prompt, completion, reasoning):
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "completion_tokens_details": {"reasoning_tokens": reasoning},
        },
    }


def _chunk(content=None, usage=None, finish=None):
    delta = SimpleNamespace(content=content, role=None, tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish)], usage=usage
    )


async def _drain():
    pending = list(chat_handler._BILLING_TASKS)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


# --------------------------------------------------------------------------- non-streaming


@pytest.mark.asyncio
async def test_reasoning_model_records_visible_and_reasoning_tokens():
    h = _handler()
    with _Env(h, response=_response("4", "stop", 30, 1290, 1280)) as env:
        await h.process(_req())
    env.rec.assert_called_once()
    user, model, provider, tin, tout, cost, ref = env.rec.call_args.args
    kw = env.rec.call_args.kwargs
    assert (tin, tout) == (30, 1290)  # v1 line: billed tokens, unchanged
    assert kw["tokens_reasoning"] == 1280  # visible = 10, close to the text
    assert kw["finish_reason"] == "stop"
    assert kw["priced_model"] == "openai/o4-mini"
    assert kw["usage_estimated"] is False
    assert kw["request_id"] == "ref-1"


@pytest.mark.asyncio
async def test_content_hash_matches_what_the_client_can_rebuild():
    h = _handler()
    with _Env(h, response=_response("Quatre.", "stop", 30, 5, 0)) as env:
        await h.process(_req())
    expected = _client_hash(RAW, "Quatre.")
    assert env.rec.call_args.kwargs["content_hash"] == expected
    assert h.request.state.gatewayz_content_hash == expected  # -> X-Gatewayz-Content-Hash
    # Not the normalised messages the provider saw.
    normalised = [
        {"role": "system", "content": "Réponds en français."},
        {"role": "user", "content": "2+2 ?"},
    ]
    assert expected != v2.content_hash(normalised, "Quatre.")


@pytest.mark.asyncio
async def test_length_finish_is_recorded_non_streaming():
    h = _handler()
    with _Env(h, response=_response("1, 2, 3", "length", 10, 16, 0)) as env:
        await h.process(_req())
    assert env.rec.call_args.kwargs["finish_reason"] == "length"


@pytest.mark.asyncio
async def test_estimated_usage_is_flagged():
    h = _handler()
    resp = {"choices": [{"message": {"content": "abcdefgh"}, "finish_reason": "stop"}]}
    with _Env(h, response=resp) as env:
        await h.process(_req())
    assert env.rec.call_args.kwargs["usage_estimated"] is True


@pytest.mark.asyncio
async def test_ordinary_key_gets_no_v2_fields_and_no_hash():
    h = _handler(key_name="prod")
    with _Env(h, response=_response("4", "stop", 30, 5, 0)) as env:
        await h.process(_req())
    env.rec.assert_not_called()  # ordinary keys record no job line at all
    assert h._client_messages is None
    assert not hasattr(h.request.state, "gatewayz_content_hash")


@pytest.mark.asyncio
async def test_body_without_messages_records_zero_hash():
    """/v1/completions sends `prompt`, not `messages`: metadata-only line."""
    h = _handler(body=b'{"model":"m","prompt":"hi"}')
    with _Env(h, response=_response("4", "stop", 3, 1, 0)) as env:
        await h.process(_req())
    assert env.rec.call_args.kwargs["content_hash"] == v2.ZERO_HASH
    assert not hasattr(h.request.state, "gatewayz_content_hash")


# --------------------------------------------------------------------------- streaming


@pytest.mark.asyncio
async def test_stream_that_hits_max_tokens_records_length():
    h = _handler()
    usage = SimpleNamespace(
        prompt_tokens=12,
        completion_tokens=200,
        total_tokens=212,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=150),
    )
    chunks = [_chunk("one "), _chunk("two", usage=usage, finish="length")]
    with _Env(h, chunks=chunks) as env:
        async for _ in h.process_stream(_req(stream=True)):
            pass
        await _drain()
    kw = env.rec.call_args.kwargs
    assert kw["finish_reason"] == "length"
    assert kw["tokens_reasoning"] == 150
    assert kw["content_hash"] == _client_hash(RAW, "one two")


@pytest.mark.asyncio
async def test_client_cancelled_stream_records_cancelled():
    h = _handler()
    with _Env(h, chunks=[_chunk("partial "), _chunk("more")]) as env:
        gen = h.process_stream(_req(stream=True))
        await gen.__anext__()
        await gen.aclose()  # client went away
        await _drain()
    assert env.rec.call_args.kwargs["finish_reason"] == "cancelled"


@pytest.mark.asyncio
async def test_stream_ending_without_a_finish_reason_is_not_called_stop():
    h = _handler()
    usage = SimpleNamespace(prompt_tokens=5, completion_tokens=3, total_tokens=8)
    with _Env(h, chunks=[_chunk("cut", usage=usage)]) as env:
        async for _ in h.process_stream(_req(stream=True)):
            pass
        await _drain()
    # None reaches record_job_usage, which writes "unknown" (never a made-up "stop").
    assert env.rec.call_args.kwargs["finish_reason"] is None


# --------------------------------------------------------------------------- pricing


def test_loss_proof_reports_the_model_that_priced_the_charge():
    with (
        patch.object(
            chat_handler,
            "_cache_aware_split",
            side_effect=lambda m, *a: (2.0, 1, 1) if m == "served/m" else (1.0, 0.5, 0.5),
        ),
        patch.object(
            chat_handler,
            "get_model_pricing",
            return_value={"source": "db", "prompt": 1, "completion": 1},
        ),
    ):
        priced: dict = {}
        assert _loss_proof_cost_split("req/m", "served/m", 1, 1, priced=priced)[0] == 2.0
        assert priced == {"model": "served/m"}
        priced = {}
        _loss_proof_cost_split("req/m", None, 1, 1, priced=priced)
        assert priced == {"model": "req/m"}


# --------------------------------------------------------------------------- routes


@pytest.mark.asyncio
async def test_dispatch_streaming_sends_the_hash_before_done():
    from src.routes import chat_dispatch

    request = SimpleNamespace(state=SimpleNamespace())
    h_hash = "0x" + "12" * 32

    async def fake_process_stream(self, internal_request):
        if False:
            yield None
        request.state.gatewayz_content_hash = h_hash  # set at billing, before [DONE]

    with patch.object(ChatInferenceHandler, "process_stream", new=fake_process_stream):
        resp = await chat_dispatch.dispatch_streaming(
            is_anonymous=False,
            provider_chain=["openai"],
            messages=[{"role": "user", "content": "hi"}],
            original_model="openai/gpt-5-mini",
            optional={},
            api_key="gw",
            api_key_id=1,
            background_tasks=None,
            request=request,
            request_id="r",
            rl_pre=None,
            tracker=None,
            user={"id": 1},
            trial={},
            environment_tag="live",
            session_id=None,
            rate_limit_mgr=None,
            client_ip=None,
        )
        out = [c async for c in resp.body_iterator]
    i = out.index(f": x-gatewayz-content-hash: {h_hash}\n\n")
    assert out[i + 1].startswith("data: [DONE]")


@pytest.mark.asyncio
async def test_anthropic_stream_passes_the_hash_comment_through():
    from src.routes import messages

    h_hash = "0x" + "34" * 32

    async def upstream():
        yield f": x-gatewayz-content-hash: {h_hash}\n\n"
        yield "data: [DONE]\n\n"

    out = [c async for c in messages._stream_anthropic_events_impl(upstream(), "m", "msg_1")]
    assert f": x-gatewayz-content-hash: {h_hash}\n\n" in out


@pytest.mark.asyncio
async def test_legacy_stream_post_processing_passes_the_real_finish_reason():
    from src.handlers import post_processing as pp

    user = {"id": 1, "key_name": f"job:{JOB}", "environment_tag": "live"}
    with (
        patch.object(pp, "record_job_usage") as rec,
        patch.object(pp, "_handle_credits_and_usage_with_fallback", return_value=(0.003, True)),
        patch.object(pp, "calculate_cost_async", return_value=0.003),
        patch.object(pp, "increment_api_key_usage"),
        patch.object(pp, "log_activity") as activity,
        patch.object(pp, "_record_inference_metrics_and_health"),
        patch.object(pp, "save_chat_completion_request_with_cost"),
    ):
        await pp._process_stream_completion_background(
            user=user,
            api_key="gw_x",
            model="openai/gpt-5-mini",
            trial={},
            environment_tag="live",
            session_id=None,
            messages=[],
            accumulated_content="1, 2, 3",
            prompt_tokens=20,
            completion_tokens=8,
            total_tokens=28,
            elapsed=1.0,
            provider="openai",
            request_id="req-1",
            api_key_id=9,
            finish_reason="length",
        )
    assert rec.call_args.kwargs["finish_reason"] == "length"
    assert activity.call_args.kwargs["finish_reason"] == "length"


def test_price_sheet_route_serves_exact_bytes():
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient

    from src.routes import price_sheets

    h, body = v2.price_sheet("openai/gpt-5-mini", 0.25e-6, 2e-6, 1.0)
    store = {h: body.decode()}
    app = FastAPI()
    v1 = APIRouter(prefix="/v1")
    v1.include_router(price_sheets.router)
    app.include_router(v1)
    app.dependency_overrides[price_sheets.price_sheet_rl] = lambda: None
    with patch.object(price_sheets, "get_price_sheet", side_effect=store.get):
        c = TestClient(app)
        r = c.get(f"/v1/price-sheets/{h}")
        assert r.status_code == 200 and r.content == body
        assert "0x" + hashlib.sha256(r.content).hexdigest() == h
        assert "immutable" in r.headers["cache-control"]
        assert c.get("/v1/price-sheets/0x" + "00" * 32).status_code == 404
        assert c.get("/v1/price-sheets/not-a-hash").status_code == 404
        store[h] = body.decode().replace("0.25", "0.01")  # tampered row
        assert c.get(f"/v1/price-sheets/{h}").status_code == 500


@pytest.mark.asyncio
async def test_flag_off_reads_no_body_and_returns_no_hash(monkeypatch):
    from src.config.config import Config

    monkeypatch.setattr(Config, "JOB_USAGE_V2_ENABLED", False)
    h = _handler()
    with _Env(h, response=_response("4", "stop", 30, 5, 0)) as env:
        await h.process(_req())
    assert env.rec.call_args.kwargs == {}
    assert h._client_messages is None
    assert not hasattr(h.request.state, "gatewayz_content_hash")


def test_flag_off_hides_price_sheets_and_v2_reads(monkeypatch):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient

    from src.config.config import Config
    from src.db import inference_jobs
    from src.routes import price_sheets

    monkeypatch.setattr(Config, "JOB_USAGE_V2_ENABLED", False)
    assert inference_jobs.list_usage_v2("0x" + "00" * 32) is None  # no DB call
    app = FastAPI()
    v1 = APIRouter(prefix="/v1")
    v1.include_router(price_sheets.router)
    app.include_router(v1)
    app.dependency_overrides[price_sheets.price_sheet_rl] = lambda: None
    with patch.object(price_sheets, "get_price_sheet") as get:
        assert TestClient(app).get("/v1/price-sheets/0x" + "ab" * 32).status_code == 404
    get.assert_not_called()


@pytest.mark.asyncio
async def test_anthropic_messages_hash_includes_the_system_prompt():
    raw = json.dumps(
        {
            "model": "anthropic/claude-x",
            "system": "Réponds en français.",
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "2+2 ?"}],
        },
        ensure_ascii=False,
    ).encode()
    h = _handler(body=raw, path="/v1/messages")
    with _Env(h, response=_response("Quatre.", "stop", 30, 5, 0)) as env:
        await h.process(_req())
    sent = json.loads(raw)
    bundle = {
        "messages": [{"role": "system", "content": sent["system"]}, *sent["messages"]],
        "output": "Quatre.",
    }
    expected = (
        "0x"
        + hashlib.sha256(
            json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
    )
    assert env.rec.call_args.kwargs["content_hash"] == expected


@pytest.mark.asyncio
async def test_xai_reasoning_is_billed_and_split_once():
    """#2417 bills xAI reasoning reported outside completion_tokens; the v2 line must
    then show it as reasoning inside the billed count, not double it or drop it."""
    h = _handler()
    resp = {
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 62,
            "total_tokens": 193,
            "reasoning_tokens": 121,
        },
    }
    with _Env(h, response=resp) as env:
        await h.process(_req())
    _, _, _, tin, tout, _, _ = env.rec.call_args.args
    assert (tin, tout) == (10, 183)  # billed: visible 62 + reasoning 121
    assert env.rec.call_args.kwargs["tokens_reasoning"] == 121
