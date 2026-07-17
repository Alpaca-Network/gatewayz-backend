"""Tests for provider-budget (402) handling: friendly message + no credential leak.

OpenRouter (and similar) return an upstream 402 when the gateway's API key hits its
spend limit, and the raw error embeds the key id in a dashboard URL. We must:
  - detect it (is_provider_budget_error),
  - never leak the URL/key to users (sanitize_provider_error_for_user),
  - map it to a clean 402 (map_provider_error) with no raw message.
"""

from fastapi import HTTPException

from src.services.provider_failover import map_provider_error
from src.utils.errors import (
    is_provider_budget_error,
    sanitize_provider_error_for_user,
)

# The real upstream error a user hit (key id redacted here only for the test literal length;
# the point is the sanitizer removes it).
REAL_402 = (
    "Error code: 402 - {'error': {'message': \"This request requires more credits, or fewer "
    "max_tokens. You requested up to 2000 tokens, but can only afford 897. To increase, visit "
    "https://openrouter.ai/workspaces/default/keys/"
    "f001429593544cd92610592c96fee5e341f53e759e3f07aa5089c82159c5ed03 and adjust the key's "
    "weekly limit\", 'code': 402}}"
)


def test_is_provider_budget_error_detects_402_budget():
    assert is_provider_budget_error(REAL_402) is True
    assert is_provider_budget_error("Error code: 402 - payment required") is True
    assert is_provider_budget_error("can only afford 12 tokens") is True
    assert is_provider_budget_error(None) is False
    assert is_provider_budget_error("Error code: 429 - rate limited") is False
    assert is_provider_budget_error("some random error") is False


def test_sanitizer_removes_url_and_key_hash():
    cleaned = sanitize_provider_error_for_user(REAL_402)
    assert "http" not in cleaned
    assert "openrouter.ai" not in cleaned
    # the 64-char key hash must be gone
    assert "f001429593544cd92610592c96fee5e341f53e759e3f07aa5089c82159c5ed03" not in cleaned
    assert "\n" not in cleaned


def test_sanitizer_truncates_and_handles_empty():
    assert sanitize_provider_error_for_user("") == ""
    assert sanitize_provider_error_for_user(None) == ""
    long = "x" * 500
    assert len(sanitize_provider_error_for_user(long, max_length=100)) <= 101  # + ellipsis


def test_map_provider_error_402_is_clean_no_leak():
    result = map_provider_error("openrouter", "anthropic/claude-haiku-4.5", Exception(REAL_402))
    assert isinstance(result, HTTPException)
    assert result.status_code == 402
    detail = str(result.detail)
    assert "openrouter.ai" not in detail
    assert "f001429593544cd92610592c96fee5e341f53e759e3f07aa5089c82159c5ed03" not in detail
    assert "capacity" in detail.lower()


def test_map_provider_error_generic_message_is_sanitized():
    exc = Exception("boom see https://openrouter.ai/keys/deadbeefdeadbeefdeadbeefdeadbeef00")
    result = map_provider_error("openrouter", "some/model", exc)
    detail = str(result.detail)
    assert "http" not in detail
    assert "deadbeefdeadbeefdeadbeefdeadbeef00" not in detail
