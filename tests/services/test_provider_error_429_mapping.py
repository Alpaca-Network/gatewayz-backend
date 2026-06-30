"""Regression tests for upstream 429 mapping (free-tier models looked "broken").

Rate-limited provider responses (e.g. OpenRouter free-tier) reach the chat
handler as plain exceptions whose text preserves the upstream status — the
OpenAI SDK renders `RateLimitError` as ``"Error code: 429 - {...}"``. The
handler used to wrap *every* provider exception as a generic 502, so a 429
(retryable) was surfaced to clients as a 502 (looks like a gateway failure),
which made free models appear permanently broken in the model selector.

`map_provider_error` must extract a 429 from such stringified errors and return
a retryable 429 with Retry-After. Genuine non-status errors still map to 502.
"""

import pytest
from fastapi import HTTPException

from src.services.provider_failover import map_provider_error


def test_stringified_429_maps_to_429_with_retry_after():
    exc = Exception(
        "Error code: 429 - {'error': {'message': 'Provider returned error', 'code': 429}}"
    )
    result = map_provider_error("openrouter", "openai/gpt-oss-120b:free", exc)
    assert isinstance(result, HTTPException)
    assert result.status_code == 429
    assert result.headers and "Retry-After" in result.headers


@pytest.mark.parametrize(
    "message",
    [
        "Provider returned a rate limit error",
        "429 Too Many Requests",
        "Error code: 429 - rate limited",
    ],
)
def test_rate_limit_phrasings_map_to_429(message):
    result = map_provider_error("openrouter", "some/model:free", Exception(message))
    assert result.status_code == 429


def test_stringified_404_maps_to_404():
    exc = Exception("Error code: 404 - {'error': {'message': 'No endpoints found'}}")
    result = map_provider_error("openrouter", "google/gemma-3-27b-it:free", exc)
    assert result.status_code == 404


def test_unknown_provider_error_still_maps_to_502():
    exc = Exception("something unexpected blew up")
    result = map_provider_error("openrouter", "some/model", exc)
    assert result.status_code == 502
