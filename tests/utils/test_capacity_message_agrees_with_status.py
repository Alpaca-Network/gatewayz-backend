"""The words must agree with the status.

#2355 moved provider budget exhaustion from a retryable 503 to a terminal 402.
Verified live the same day, after that deploy:

    HTTP/2 402
    {"type":"error","error":{"type":"invalid_request_error","message":
     "... This model is temporarily unavailable due to a capacity limit on our
     side. Please try a different model or try again shortly."}}

The status said terminal; the sentence said retry. An SDK reads the status, a
human reads the sentence, and they were being told opposite things about one
condition. Half a fix reads as a whole one right up until someone acts on the
other half.

The mid-stream event had the same problem through its own door: `capacity_error`
is emitted from exactly one place -- chat_streaming's provider-budget branch --
and was mapped onto Anthropic's `overloaded_error`, the one type whose
documented advice is to back off and retry.

These tests pin the PROPERTY (the message never invites a retry; the mapped
type is not a retry-advising one), not the wording, so a later copy edit is
free and a later regression is not.
"""

from __future__ import annotations

from src.routes.messages import _STREAM_ERROR_TYPE_MAP, _anthropic_error_type
from src.utils.errors import PROVIDER_CAPACITY_MESSAGE

# Anthropic error types whose documented handling is "wait, then retry".
RETRY_ADVISING_TYPES = {"overloaded_error", "rate_limit_error", "api_error"}

RETRY_INVITING_PHRASES = (
    "temporarily",
    "temporary",
    "try again shortly",
    "try again later",
    "try again in",
    "please retry",
)


def test_the_capacity_message_never_invites_a_retry():
    text = PROVIDER_CAPACITY_MESSAGE.lower()
    for phrase in RETRY_INVITING_PHRASES:
        assert phrase not in text, f"{phrase!r} in a message that rides on a terminal 402"


def test_the_capacity_message_says_what_the_caller_can_do_instead():
    # A terminal error that names no alternative just moves the dead end.
    text = PROVIDER_CAPACITY_MESSAGE.lower()
    assert "different model" in text or "support" in text


def test_the_capacity_message_leaks_no_credential_or_url():
    # The raw upstream text embeds a key id in a dashboard URL; that is the whole
    # reason this constant exists.
    text = PROVIDER_CAPACITY_MESSAGE.lower()
    for leak in ("http", "://", "key", "$"):
        assert leak not in text, leak


def test_the_402_envelope_type_is_terminal():
    # The non-stream half, pinned alongside the message it carries.
    assert _anthropic_error_type(402, None) not in RETRY_ADVISING_TYPES


def test_the_mid_stream_capacity_event_is_terminal_too():
    mapped = _STREAM_ERROR_TYPE_MAP["capacity_error"]
    assert mapped not in RETRY_ADVISING_TYPES, (
        f"capacity_error -> {mapped}: the identical cause the non-stream path "
        "now calls terminal, still telling a streaming SDK to retry"
    )


def test_a_genuine_upstream_overload_is_still_retryable():
    # The carve-out. 503 must keep meaning "real capacity, retrying is the right
    # advice", or this trades one wrong status for another.
    assert _anthropic_error_type(503, None) == "overloaded_error"
    assert _anthropic_error_type(429, None) == "rate_limit_error"
