"""Billed completion tokens across provider usage conventions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.usage_normalization import (
    billed_completion_tokens,
    client_wants_usage,
    reasoning_outside_completion,
)


@pytest.mark.parametrize(
    "usage,prompt,completion,billed",
    [
        # OpenAI / OpenRouter / DeepSeek: reasoning inside completion_tokens.
        (
            {"completion_tokens_details": {"reasoning_tokens": 1280}, "total_tokens": 1320},
            30,
            1290,
            1290,
        ),
        # xAI: visible-only completion_tokens, total = prompt + completion + reasoning.
        ({"reasoning_tokens": 121, "total_tokens": 193}, 10, 62, 183),
        (
            SimpleNamespace(
                completion_tokens_details=SimpleNamespace(reasoning_tokens=121), total_tokens=193
            ),
            10,
            62,
            183,
        ),
        # Reasoning larger than completion cannot be a breakdown of it.
        ({"reasoning_tokens": 556}, 5, 30, 586),
        # A total inflated by something else is not taken as unbilled reasoning.
        ({"completion_tokens_details": {"reasoning_tokens": 10}, "total_tokens": 999}, 10, 50, 50),
        # No reasoning reported.
        ({"total_tokens": 15}, 10, 5, 5),
        (None, 0, 7, 7),
    ],
)
def test_billed_completion_tokens(usage, prompt, completion, billed):
    assert billed_completion_tokens(usage, prompt, completion) == billed


def test_bool_total_is_not_a_count():
    usage = {"completion_tokens_details": {"reasoning_tokens": 1}, "total_tokens": True}
    assert not reasoning_outside_completion(usage, 0, 5)
    assert billed_completion_tokens(usage, 0, 5) == 5


@pytest.mark.parametrize(
    "so,wants",
    [
        (None, False),
        ({}, False),
        ({"include_usage": True}, True),
        (SimpleNamespace(include_usage=True), True),
    ],
)
def test_client_wants_usage(so, wants):
    assert client_wants_usage(so) is wants


def test_vertex_rest_folds_thinking_into_billed_output():
    from src.services.providers.google_vertex_client import _normalize_vertex_candidate_to_openai

    out = _normalize_vertex_candidate_to_openai(
        {
            "content": {"parts": [{"text": "ok"}]},
            "finishReason": "STOP",
            "usageMetadata": {
                "promptTokenCount": 12,
                "candidatesTokenCount": 26,
                "thoughtsTokenCount": 443,
            },
        },
        "google/gemini-2.5-pro",
    )
    assert out["usage"]["completion_tokens"] == 469
    assert out["usage"]["completion_tokens_details"] == {"reasoning_tokens": 443}
    # Inside completion now, so it is billed exactly once.
    assert billed_completion_tokens(out["usage"], 12, 469) == 469


def test_vertex_sdk_folds_thinking_into_billed_output():
    from src.services.providers.google_vertex_client import _process_google_vertex_sdk_response

    response = SimpleNamespace(
        text="ok",
        candidates=[
            SimpleNamespace(
                finish_reason=1, content=SimpleNamespace(parts=[SimpleNamespace(text="ok")])
            )
        ],
        usage_metadata=SimpleNamespace(
            prompt_token_count=12, candidates_token_count=26, thoughts_token_count=443
        ),
    )
    out = _process_google_vertex_sdk_response(response, "google/gemini-2.5-pro")
    assert out["usage"]["completion_tokens"] == 469


def test_vertex_stream_carries_usage_on_the_final_chunk():
    from unittest.mock import patch

    from src.services.providers import google_vertex_client as gv

    full = {
        "id": "v1",
        "created": 1,
        "model": "google/gemini-2.5-pro",
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 469, "total_tokens": 481},
    }
    with patch.object(gv, "make_google_vertex_request_openai", return_value=full):
        chunks = list(gv.make_google_vertex_request_openai_stream([], "google/gemini-2.5-pro"))
    assert chunks[-1]["usage"]["completion_tokens"] == 469
