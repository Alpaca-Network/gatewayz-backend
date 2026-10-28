"""The inference-escrow hooks are wired into the real request path.

Unit tests of job_usage prove the logic; these prove it is CALLED — by key
validation (cap enforcement) and by both billing paths (usage lines) — and that
ordinary keys are untouched.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.handlers.chat_handler import ChatInferenceHandler
from src.security.security import _validate_key_constraints
from src.services import job_usage as ju

JOB = "0x" + "cd" * 32


def _handler(key_name, background=None):
    h = ChatInferenceHandler(
        api_key=None,
        background_tasks=background,
        request=SimpleNamespace(headers={}, state=SimpleNamespace()),
    )
    h.user = {"id": 1, "key_id": 7, "key_name": key_name}
    h.is_anonymous = False
    return h


def _save(h, status="completed"):
    with (
        patch("src.handlers.chat_handler.save_chat_completion_request_with_cost"),
        patch("src.handlers.chat_handler.record_job_usage") as rec,
    ):
        h._save_request_record(
            model_name="openai/gpt-5-mini",
            provider_name="openai",
            input_tokens=11,
            output_tokens=4,
            status=status,
            cost_usd=0.002,
        )
        asyncio.run(h._flush_job_line())
    return rec


def test_completed_request_on_a_job_key_records_a_usage_line():
    rec = _save(_handler(f"job:{JOB}"))
    rec.assert_called_once()
    user, model, provider, tin, tout, cost, ref = rec.call_args.args
    assert (model, provider, tin, tout, cost) == ("openai/gpt-5-mini", "openai", 11, 4, 0.002)
    assert ref  # joins to the chat_completion_requests row


def test_failed_request_records_nothing():
    _save(_handler(f"job:{JOB}"), status="failed").assert_not_called()


def test_job_line_is_never_a_response_background_task():
    """Codex P1 on #2382: a background task runs after the client has its answer, so
    a client closing the job right away sealed a record missing the line. The line
    is held for _flush_job_line(), which the billing paths await before responding."""
    bg = MagicMock()
    h = _handler(f"job:{JOB}", background=bg)
    with (
        patch("src.handlers.chat_handler.save_chat_completion_request_with_cost"),
        patch("src.handlers.chat_handler.record_job_usage") as rec,
    ):
        h._save_request_record(
            model_name="m",
            provider_name="p",
            input_tokens=1,
            output_tokens=1,
            status="completed",
            cost_usd=0.001,
        )
        assert not any(c.args and c.args[0] is rec for c in bg.add_task.call_args_list)
        rec.assert_not_called()
        asyncio.run(h._flush_job_line())
        rec.assert_called_once()
        asyncio.run(h._flush_job_line())  # flushing twice writes once
        rec.assert_called_once()


def test_ordinary_key_holds_no_job_line():
    rec = _save(_handler("prod", background=MagicMock()))
    rec.assert_not_called()


def test_key_validation_enforces_the_job_cap():
    client = MagicMock()
    q = client.table.return_value.select.return_value.eq.return_value
    q.execute.return_value = MagicMock(
        data=[
            {
                "status": "running",
                "cap_usd": "1",
                "spent_usd": "1",
                "deadline": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            }
        ]
    )
    key = {"id": 9, "key_name": f"job:{JOB}", "is_active": True}
    with pytest.raises(ValueError, match=ju.JOB_CAP_REACHED):
        _validate_key_constraints(key, None, None, "api_keys_new", client)


def _running_job_client():
    client = MagicMock()
    q = client.table.return_value.select.return_value.eq.return_value
    q.execute.return_value = MagicMock(
        data=[
            {
                "status": "running",
                "cap_usd": "1",
                "spent_usd": "0",
                "deadline": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            }
        ]
    )
    return client


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/v1/embeddings"),
        ("POST", "/v1/audio/transcriptions"),
        ("POST", "/v1/tools/execute"),
        ("POST", "/v1/images/generations"),
    ],
)
def test_job_key_is_refused_on_routes_that_record_no_usage(method, path):
    """Codex P1 on #2382: those routes bill the account but never append a line,
    so a job key could spend there past its cap and outside its sealed record."""
    key = {"id": 9, "key_name": f"job:{JOB}", "is_active": True}
    with pytest.raises(ValueError, match=ju.JOB_ROUTE_NOT_ALLOWED):
        _validate_key_constraints(
            key, None, None, "api_keys_new", _running_job_client(), method=method, path=path
        )


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/v1/chat/completions"),
        ("POST", "/v1/messages"),
        ("POST", "/v1/completions"),
        ("POST", "/v1/chat/completions/"),
        ("GET", "/v1/models"),
        (None, None),
    ],
)
def test_job_key_may_call_instrumented_and_read_routes(method, path):
    key = {"id": 9, "key_name": f"job:{JOB}", "is_active": True}
    _validate_key_constraints(
        key, None, None, "api_keys_new", _running_job_client(), method=method, path=path
    )


def test_route_refusal_is_a_403():
    from src.security.deps import ceiling_http_exception

    exc = ceiling_http_exception(ju.JOB_ROUTE_NOT_ALLOWED)
    assert exc.status_code == 403
    assert exc.detail["error"]["code"] == "not_allowed"


def test_ordinary_key_is_not_route_limited():
    client = MagicMock()
    _validate_key_constraints(
        {"id": 9, "key_name": "prod", "is_active": True},
        None,
        None,
        "api_keys_new",
        client,
        method="POST",
        path="/v1/embeddings",
    )


def test_key_validation_leaves_ordinary_keys_alone():
    client = MagicMock()
    _validate_key_constraints(
        {"id": 9, "key_name": "prod", "is_active": True}, None, None, "api_keys_new", client
    )
    for call in client.table.call_args_list:
        assert call.args[0] != "inference_jobs"


@pytest.mark.asyncio
async def test_streaming_post_processing_records_a_usage_line():
    from src.handlers import post_processing as pp

    user = {"id": 1, "key_name": f"job:{JOB}", "environment_tag": "live"}
    with (
        patch.object(pp, "record_job_usage") as rec,
        patch.object(pp, "_handle_credits_and_usage_with_fallback", return_value=(0.003, True)),
        patch.object(pp, "calculate_cost_async", return_value=0.003),
        patch.object(pp, "increment_api_key_usage"),
        patch.object(pp, "log_activity"),
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
            accumulated_content="",
            prompt_tokens=20,
            completion_tokens=8,
            total_tokens=28,
            elapsed=1.0,
            provider="openai",
            request_id="req-1",
            api_key_id=9,
        )
    rec.assert_called_once()
    assert rec.call_args.args[1:] == ("openai/gpt-5-mini", "openai", 20, 8, 0.003, "req-1")
