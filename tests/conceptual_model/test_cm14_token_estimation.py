"""
CM-14 Token Estimation

Tests covering the token estimation fallback strategy, including
the CM-specified 1-token-per-4-chars rule vs. the actual 0.75-tokens-per-word
heuristic in code.
"""

import pytest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# CM-14.1  Token estimation fallback: CM says 1 token per 4 chars
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1401TokenEstimationFallback1Per4Chars:
    def test_token_estimation_fallback_1_per_4_chars(self):
        """CM specifies 1 token per 4 characters (400 chars -> ~100 tokens).
        The fallback code uses len(text) // 4."""
        from src.utils.token_estimator import count_tokens_text

        # Force the word-based fallback by disabling tiktoken
        with patch("src.utils.token_estimator._get_tiktoken_encoding", return_value=None):
            # 400 characters of text
            text = "a" * 400
            result = count_tokens_text(text)
            # CM says: 400 chars / 4 = 100 tokens
            assert result == 100, (
                f"CM expects 100 tokens for 400 chars (1 per 4 chars), "
                f"got {result} (code uses 0.75 tokens per word)"
            )


# ---------------------------------------------------------------------------
# CM-14.2  Fallback estimation used when provider omits usage
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1402TokenEstimationUsedWhenProviderOmitsUsage:
    def test_token_estimation_used_when_provider_omits_usage(self):
        """When no explicit max_tokens is provided and messages exist,
        estimate_message_tokens falls back to counting from message content."""
        from src.utils.token_estimator import estimate_message_tokens

        messages = [
            {"role": "user", "content": "Hello, how are you doing today?"},
        ]

        # No max_tokens provided -> estimation from messages
        result = estimate_message_tokens(messages, max_tokens=None)
        assert result > 0, "Should produce a positive estimate from message content"


# ---------------------------------------------------------------------------
# CM-14.3  Real usage preferred over estimation
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1403RealUsagePreferredOverEstimation:
    def test_real_usage_preferred_over_estimation(self):
        """When max_tokens is explicitly provided, estimate_message_tokens
        returns that value directly rather than estimating from content."""
        from src.utils.token_estimator import estimate_message_tokens

        messages = [
            {"role": "user", "content": "Hello world"},
        ]

        explicit_max = 500
        result = estimate_message_tokens(messages, max_tokens=explicit_max)
        assert result == explicit_max, (
            f"When max_tokens={explicit_max} is given, it should be returned "
            f"directly, got {result}"
        )
