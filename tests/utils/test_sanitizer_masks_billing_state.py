"""Our billing state must not reach a customer through any error path.

`map_provider_error`'s 402 arm already swaps budget failures for
`PROVIDER_CAPACITY_MESSAGE`. But that arm is keyed on the *mapped status*, and
an upstream failure arriving as a different exception type lands in an arm that
passes a sanitized message through instead. Sanitizing strips URLs and key
hashes; it did nothing to the words "credit balance is too low".

So on 2026-09-16, while the Anthropic account sat unfunded and 57 of ~65 models
were down, those paths were telling end users which of our accounts had run dry.

These tests pin the masking at the sanitizer, which is the one place all 16 call
sites share.
"""

from __future__ import annotations

import pytest

from src.utils.errors import (
    PROVIDER_CAPACITY_MESSAGE,
    is_provider_budget_error,
    sanitize_provider_error_for_user,
)

# Real upstream text, as each provider actually phrases it.
BUDGET_ERRORS = [
    "Your credit balance is too low to access the Anthropic API.",
    "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
    "'message': 'Your credit balance is too low to access the Anthropic API.'}}",
    "You exceeded your current quota: insufficient_quota",
    "402 Payment Required",
    "This request requires more credits, or fewer max_tokens",
    "Key limit exceeded: weekly limit reached",
]


@pytest.mark.parametrize("raw", BUDGET_ERRORS)
def test_a_budget_error_never_reaches_the_user_verbatim(raw: str):
    out = sanitize_provider_error_for_user(raw)
    assert out == PROVIDER_CAPACITY_MESSAGE
    lowered = out.lower()
    for tell in ("credit balance", "quota", "payment required", "weekly limit", "more credits"):
        assert tell not in lowered, f"billing state leaked via {tell!r}: {out}"


@pytest.mark.parametrize("raw", BUDGET_ERRORS)
def test_the_detector_and_the_masking_agree(raw: str):
    """Anything the detector calls a budget error must also be masked.

    These are two functions that must not drift: a detector that recognises a
    condition the sanitizer then prints verbatim is worse than neither.
    """
    assert is_provider_budget_error(raw)
    assert sanitize_provider_error_for_user(raw) == PROVIDER_CAPACITY_MESSAGE


def test_ordinary_provider_errors_are_still_passed_through():
    """Masking must not swallow errors a user can actually act on."""
    raw = "model 'gpt-5-turbo' does not exist"
    out = sanitize_provider_error_for_user(raw)
    assert out == raw
    assert out != PROVIDER_CAPACITY_MESSAGE


def test_url_and_secret_stripping_still_applies_to_non_budget_errors():
    raw = (
        "upstream failed, see https://example.com/keys/a3f5b2c1d4e6f7a8b9c0d1e2f3a4b5c6 for detail"
    )
    out = sanitize_provider_error_for_user(raw)
    assert "https://" not in out
    assert "a3f5b2c1d4e6f7a8b9c0d1e2f3a4b5c6" not in out
    assert "[link removed]" in out


def test_empty_input_is_unchanged():
    assert sanitize_provider_error_for_user(None) == ""
    assert sanitize_provider_error_for_user("") == ""


def test_masking_ignores_max_length():
    """The replacement is a constant, not a truncated upstream string."""
    out = sanitize_provider_error_for_user(BUDGET_ERRORS[0], max_length=10)
    assert out == PROVIDER_CAPACITY_MESSAGE
    assert not out.endswith("…")
