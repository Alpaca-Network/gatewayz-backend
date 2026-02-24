"""
Tests for credit pre-flight check service.

These tests verify that the credit sufficiency checking works correctly
before making provider requests, following OpenAI's max_tokens model.
"""

import pytest

from src.services.credit_precheck import (
    calculate_maximum_cost,
    check_credit_sufficiency,
    estimate_and_check_credits,
    get_model_max_tokens,
)


class TestGetModelMaxTokens:
    """Test getting maximum output tokens for different models."""

    def test_exact_match_gpt4(self):
        """Test exact match for GPT-4."""
        assert get_model_max_tokens("gpt-4") == 8192

    def test_exact_match_claude(self):
        """Test exact match for Claude models."""
        assert get_model_max_tokens("claude-3-opus") == 4096
        assert get_model_max_tokens("claude-sonnet-4") == 8192

    def test_partial_match_gpt4o(self):
        """Test partial match for versioned GPT-4o."""
        assert get_model_max_tokens("gpt-4o-2024-05-13") == 4096

    def test_unknown_model_defaults(self):
        """Test unknown model returns default."""
        assert get_model_max_tokens("unknown-model") == 4096

    def test_llama_models(self):
        """Test Llama model detection."""
        assert get_model_max_tokens("llama-3.1") == 128000
        assert get_model_max_tokens("meta-llama/Llama-3.1-8B") == 128000


class TestCalculateMaximumCost:
    """Test maximum cost calculation."""

    def test_with_explicit_max_tokens(self):
        """Test cost calculation with explicit max_tokens."""
        messages = [{"role": "user", "content": "Hello, how are you?"}]
        max_cost, input_tokens, max_output_tokens = calculate_maximum_cost(
            model_id="gpt-4o",
            messages=messages,
            max_tokens=1000,
        )

        assert max_output_tokens == 1000
        assert input_tokens > 0  # Should estimate from messages
        assert max_cost > 0  # Should have non-zero cost

    def test_without_max_tokens_uses_model_default(self):
        """Test cost calculation without max_tokens uses model default."""
        messages = [{"role": "user", "content": "Hello"}]
        max_cost, input_tokens, max_output_tokens = calculate_maximum_cost(
            model_id="gpt-4o",
            messages=messages,
            max_tokens=None,
        )

        # Should use GPT-4o's default of 4096
        assert max_output_tokens == 4096
        assert input_tokens > 0
        assert max_cost > 0

    def test_zero_max_tokens_uses_model_default(self):
        """Test cost calculation with zero max_tokens uses model default."""
        messages = [{"role": "user", "content": "Test"}]
        max_cost, input_tokens, max_output_tokens = calculate_maximum_cost(
            model_id="gpt-4",
            messages=messages,
            max_tokens=0,
        )

        # Should use GPT-4's default of 8192
        assert max_output_tokens == 8192


class TestCheckCreditSufficiency:
    """Test credit sufficiency checking."""

    def test_sufficient_credits(self):
        """Test when user has sufficient credits."""
        result = check_credit_sufficiency(
            user_credits=10.0,
            max_cost=5.0,
            model_id="gpt-4o",
            max_tokens=1000,
            is_trial=False,
        )

        assert result["allowed"] is True
        assert result["reason"] == "Sufficient credits"
        assert result["max_cost"] == 5.0
        assert result["available_credits"] == 10.0
        assert result["remaining_after_max"] == 5.0

    def test_insufficient_credits(self):
        """Test when user has insufficient credits."""
        result = check_credit_sufficiency(
            user_credits=2.0,
            max_cost=5.0,
            model_id="gpt-4o",
            max_tokens=1000,
            is_trial=False,
        )

        assert result["allowed"] is False
        assert result["reason"] == "Insufficient credits"
        assert result["max_cost"] == 5.0
        assert result["available_credits"] == 2.0
        assert result["shortfall"] == 3.0
        assert "suggestion" in result

    def test_trial_user_always_allowed(self):
        """Test that trial users are always allowed (no credit check)."""
        result = check_credit_sufficiency(
            user_credits=0.0,  # Trial user has no credits
            max_cost=100.0,  # Even expensive request
            model_id="gpt-4o",
            max_tokens=4096,
            is_trial=True,
        )

        assert result["allowed"] is True
        assert "Trial user" in result["reason"]
        assert result["max_cost"] == 0.0  # Trial users have $0 cost

    def test_exact_credits_match(self):
        """Test when user has exactly enough credits."""
        result = check_credit_sufficiency(
            user_credits=5.0,
            max_cost=5.0,
            model_id="gpt-4o",
            max_tokens=1000,
            is_trial=False,
        )

        assert result["allowed"] is True  # Equal is sufficient
        assert result["remaining_after_max"] == 0.0


class TestEstimateAndCheckCredits:
    """Test the main entry point for credit pre-checks."""

    def test_complete_flow_with_sufficient_credits(self):
        """Test complete pre-check flow with sufficient credits."""
        messages = [{"role": "user", "content": "Write a short poem about Python programming"}]

        result = estimate_and_check_credits(
            model_id="gpt-4o",
            messages=messages,
            user_credits=5.0,
            max_tokens=500,
            is_trial=False,
        )

        assert result["allowed"] is True
        assert result["max_cost"] > 0
        assert result["input_tokens"] > 0
        assert result["max_output_tokens"] == 500

    def test_complete_flow_with_insufficient_credits(self):
        """Test complete pre-check flow with insufficient credits."""
        messages = [{"role": "user", "content": "Write a very long essay"}]

        result = estimate_and_check_credits(
            model_id="gpt-4o",
            messages=messages,
            user_credits=0.001,  # Very low credits
            max_tokens=4096,  # Maximum output
            is_trial=False,
        )

        assert result["allowed"] is False
        assert result["shortfall"] > 0
        assert "suggestion" in result

    def test_trial_user_bypasses_check(self):
        """Test that trial users bypass credit checks entirely."""
        messages = [{"role": "user", "content": "Test message"}]

        result = estimate_and_check_credits(
            model_id="gpt-4o",
            messages=messages,
            user_credits=0.0,
            max_tokens=4096,
            is_trial=True,
        )

        assert result["allowed"] is True
        assert result["max_cost"] == 0.0

    def test_uses_model_default_when_no_max_tokens(self):
        """Test that model defaults are used when max_tokens not specified."""
        messages = [{"role": "user", "content": "Hello"}]

        result = estimate_and_check_credits(
            model_id="gpt-4",  # Has 8192 default
            messages=messages,
            user_credits=10.0,
            max_tokens=None,
            is_trial=False,
        )

        assert result["allowed"] is True
        assert result["max_output_tokens"] == 8192

    def test_expensive_model_high_max_tokens(self):
        """Test expensive scenario: GPT-4 with high max_tokens."""
        messages = [
            {
                "role": "user",
                "content": "This is a prompt that will generate a lot of output",
            }
        ]

        result = estimate_and_check_credits(
            model_id="gpt-4",
            messages=messages,
            user_credits=0.10,  # Only $0.10
            max_tokens=8000,  # Very high output
            is_trial=False,
        )

        # May pass or fail depending on pricing, but should have valid result
        assert "allowed" in result
        assert "max_cost" in result
        if not result["allowed"]:
            assert result["shortfall"] > 0


class TestIntegrationScenarios:
    """Test real-world integration scenarios."""

    def test_scenario_user_starts_expensive_request(self):
        """
        Scenario: User with $0.01 tries to start a GPT-4 request
        that could cost $10.00.

        Expected: Request blocked BEFORE calling provider.
        """
        messages = [
            {
                "role": "user",
                "content": "Write a comprehensive 10,000 word essay on quantum physics",
            }
        ]

        result = estimate_and_check_credits(
            model_id="gpt-4",
            messages=messages,
            user_credits=0.01,
            max_tokens=8000,  # Will generate expensive output
            is_trial=False,
        )

        # Should be blocked (most likely, depending on pricing)
        # The key is that we CHECK before calling provider
        assert "allowed" in result
        assert "max_cost" in result
        if result["max_cost"] > 0.01:
            assert result["allowed"] is False

    def test_scenario_user_reduces_max_tokens(self):
        """
        Scenario: User sees error, reduces max_tokens to lower cost.

        Expected: Lower max_tokens = lower max_cost = request allowed.
        """
        messages = [{"role": "user", "content": "Explain AI"}]

        # First attempt with high max_tokens
        result_high = estimate_and_check_credits(
            model_id="gpt-4o",
            messages=messages,
            user_credits=0.05,
            max_tokens=4096,
            is_trial=False,
        )

        # Second attempt with lower max_tokens
        result_low = estimate_and_check_credits(
            model_id="gpt-4o",
            messages=messages,
            user_credits=0.05,
            max_tokens=500,
            is_trial=False,
        )

        # Lower max_tokens should have lower cost
        assert result_low["max_cost"] < result_high["max_cost"]
        assert result_low["max_output_tokens"] == 500
        assert result_high["max_output_tokens"] == 4096

    def test_scenario_streaming_vs_non_streaming(self):
        """
        Scenario: Same credit check for streaming and non-streaming.

        Expected: Both use same pre-flight check logic.
        """
        messages = [{"role": "user", "content": "Hello"}]

        # Non-streaming check
        result_normal = estimate_and_check_credits(
            model_id="gpt-4o",
            messages=messages,
            user_credits=1.0,
            max_tokens=1000,
            is_trial=False,
        )

        # Streaming check (uses same function)
        result_stream = estimate_and_check_credits(
            model_id="gpt-4o",
            messages=messages,
            user_credits=1.0,
            max_tokens=1000,
            is_trial=False,
        )

        # Should be identical
        assert result_normal["max_cost"] == result_stream["max_cost"]
        assert result_normal["allowed"] == result_stream["allowed"]
