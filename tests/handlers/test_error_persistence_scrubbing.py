"""format_error_for_persistence must never let arbitrary exception text (which
could embed prompt content) reach chat_completion_requests.error_message —
threat model L6/G5."""

import httpx

from src.handlers.error_persistence import format_error_for_persistence

_CANARY = "canary prompt fragment 424242"


def test_generic_exception_never_echoes_message_text():
    """A bare exception (parsing/config-shaped) must not leak str(error)."""
    err = ValueError(f"invalid literal: {_CANARY}")
    result = format_error_for_persistence(err)
    assert _CANARY not in result
    assert result.startswith("ValueError:")


def test_key_error_never_echoes_message_text():
    err = KeyError(_CANARY)
    result = format_error_for_persistence(err)
    assert _CANARY not in result
    assert result.startswith("KeyError:")


def test_timeout_error_includes_sanitized_detail():
    """Provider/network-shaped errors may include a scrubbed, bounded detail."""
    err = TimeoutError("upstream timed out after 30s")
    result = format_error_for_persistence(err)
    assert result.startswith("TimeoutError:")
    assert "upstream timed out" in result


def test_http_status_error_strips_urls_and_secrets():
    request = httpx.Request("GET", "https://provider.example/v1/models")
    response = httpx.Response(
        402,
        request=request,
        text=(
            "Payment required, see https://dashboard.provider.example/billing?"
            "key=abcdef0123456789abcdef0123456789 for details"
        ),
    )
    err = httpx.HTTPStatusError("payment required", request=request, response=response)
    result = format_error_for_persistence(err)
    assert "dashboard.provider.example" not in result
    assert "abcdef0123456789abcdef0123456789" not in result
    assert result.startswith("HTTPStatusError:")


def test_result_is_bounded_length():
    err = TimeoutError("x" * 5000)
    result = format_error_for_persistence(err)
    assert len(result) < 250
