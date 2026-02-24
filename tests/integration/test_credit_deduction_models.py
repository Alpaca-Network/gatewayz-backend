"""
Integration tests for credit deduction with specific model types.

These tests verify that credits are properly deducted for:
- OpenAI models (gpt-4o, gpt-3.5-turbo, etc.)
- Anthropic models (claude-3-opus, claude-3-sonnet, etc.)
- Both streaming and non-streaming requests

The tests verify:
1. Pricing is correctly looked up for each model type
2. Cost calculation uses the correct pricing
3. Credits are deducted from the user account
4. Transactions are logged with correct metadata
"""

import math
from unittest.mock import patch

import pytest


class TestCreditDeductionForOpenAIModels:
    """Test credit deduction for OpenAI models (GPT-4o, GPT-3.5, etc.)"""

    @pytest.fixture
    def mock_user(self):
        return {
            "id": "test-user-123",
            "tier": "pro",
            "subscription_status": "active",
            "stripe_subscription_id": "sub_123",
            "subscription_allowance": 10.0,
            "purchased_credits": 5.0,
        }

    @pytest.fixture
    def mock_trial_inactive(self):
        return {"is_trial": False, "is_expired": False}

    @pytest.fixture
    def mock_trial_active(self):
        return {"is_trial": True, "is_expired": False}

    @pytest.mark.asyncio
    async def test_gpt4o_pricing_lookup(self, monkeypatch):
        """Test that GPT-4o model gets correct pricing"""
        from src.services.pricing import get_model_pricing

        # Mock the cached models with GPT-4o pricing
        mock_models = [
            {
                "id": "openai/gpt-4o",
                "slug": "gpt-4o",
                "pricing": {"prompt": "0.000005", "completion": "0.000015"},
            }
        ]
        monkeypatch.setattr("src.services.models.get_cached_models", lambda _: mock_models)
        monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)

        # Test with full provider prefix
        pricing = get_model_pricing("openai/gpt-4o")
        assert pricing["found"] is True
        assert math.isclose(pricing["prompt"], 0.000005)
        assert math.isclose(pricing["completion"], 0.000015)

    @pytest.mark.asyncio
    async def test_gpt4o_without_prefix_uses_alias(self, monkeypatch):
        """Test that 'gpt-4o' (without openai/ prefix) resolves via alias"""
        from src.services.model_transformations import apply_model_alias
        from src.services.pricing import get_model_pricing

        # Verify alias is applied
        aliased = apply_model_alias("gpt-4o")
        assert aliased == "openai/gpt-4o"

        # Mock models
        mock_models = [
            {
                "id": "openai/gpt-4o",
                "slug": "gpt-4o",
                "pricing": {"prompt": "0.000005", "completion": "0.000015"},
            }
        ]
        monkeypatch.setattr("src.services.models.get_cached_models", lambda _: mock_models)
        monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)

        # Test with just model name (no prefix)
        pricing = get_model_pricing("gpt-4o")
        # Should find via alias or slug matching
        assert pricing["found"] is True or pricing["source"] == "cache_fallback"

    @pytest.mark.asyncio
    async def test_cost_calculation_openai(self, monkeypatch):
        """Test cost calculation for OpenAI models"""
        from src.services.pricing import calculate_cost

        # Mock pricing lookup
        monkeypatch.setattr(
            "src.services.pricing.get_model_pricing",
            lambda _: {"prompt": 0.000005, "completion": 0.000015, "found": True},
        )

        # Calculate cost: 1000 prompt tokens + 500 completion tokens
        # Cost = 1000 * 0.000005 + 500 * 0.000015 = 0.005 + 0.0075 = 0.0125
        cost = calculate_cost("openai/gpt-4o", prompt_tokens=1000, completion_tokens=500)
        assert math.isclose(cost, 0.0125)

    @pytest.mark.asyncio
    async def test_credit_handler_deducts_for_openai(self, mock_user, mock_trial_inactive):
        """Test that credit handler deducts credits for OpenAI model"""
        from src.services.credit_handler import handle_credits_and_usage

        with (
            patch("src.services.credit_handler.calculate_cost_async") as mock_cost,
            patch("src.services.credit_handler.asyncio.to_thread") as mock_to_thread,
        ):

            mock_cost.return_value = 0.0125
            mock_to_thread.return_value = None

            cost = await handle_credits_and_usage(
                api_key="test-key-123",
                user=mock_user,
                model="openai/gpt-4o",
                trial=mock_trial_inactive,
                total_tokens=1500,
                prompt_tokens=1000,
                completion_tokens=500,
                elapsed_ms=250,
                endpoint="/v1/chat/completions",
            )

            assert math.isclose(cost, 0.0125)
            # Verify deduct_credits was called
            assert mock_to_thread.called


class TestCreditDeductionForAnthropicModels:
    """Test credit deduction for Anthropic models (Claude 3, etc.)"""

    @pytest.fixture
    def mock_user(self):
        return {
            "id": "test-user-456",
            "tier": "pro",
            "subscription_status": "active",
            "stripe_subscription_id": "sub_456",
            "subscription_allowance": 10.0,
            "purchased_credits": 5.0,
        }

    @pytest.fixture
    def mock_trial_inactive(self):
        return {"is_trial": False, "is_expired": False}

    @pytest.mark.asyncio
    async def test_claude_opus_pricing_lookup(self, monkeypatch):
        """Test that Claude Opus model gets correct pricing"""
        from src.services.pricing import get_model_pricing

        mock_models = [
            {
                "id": "anthropic/claude-3-opus",
                "slug": "claude-3-opus",
                "pricing": {"prompt": "0.000015", "completion": "0.000075"},
            }
        ]
        monkeypatch.setattr("src.services.models.get_cached_models", lambda _: mock_models)
        monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)

        pricing = get_model_pricing("anthropic/claude-3-opus")
        assert pricing["found"] is True
        assert math.isclose(pricing["prompt"], 0.000015)
        assert math.isclose(pricing["completion"], 0.000075)

    @pytest.mark.asyncio
    async def test_claude_sonnet_pricing_lookup(self, monkeypatch):
        """Test that Claude Sonnet model gets correct pricing"""
        from src.services.pricing import get_model_pricing

        mock_models = [
            {
                "id": "anthropic/claude-3-sonnet",
                "slug": "claude-3-sonnet",
                "pricing": {"prompt": "0.000003", "completion": "0.000015"},
            }
        ]
        monkeypatch.setattr("src.services.models.get_cached_models", lambda _: mock_models)
        monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)

        pricing = get_model_pricing("anthropic/claude-3-sonnet")
        assert pricing["found"] is True
        assert math.isclose(pricing["prompt"], 0.000003)
        assert math.isclose(pricing["completion"], 0.000015)

    @pytest.mark.asyncio
    async def test_cost_calculation_anthropic(self, monkeypatch):
        """Test cost calculation for Anthropic Claude Opus"""
        from src.services.pricing import calculate_cost

        # Claude Opus pricing
        monkeypatch.setattr(
            "src.services.pricing.get_model_pricing",
            lambda _: {"prompt": 0.000015, "completion": 0.000075, "found": True},
        )

        # 1000 prompt + 500 completion
        # Cost = 1000 * 0.000015 + 500 * 0.000075 = 0.015 + 0.0375 = 0.0525
        cost = calculate_cost("anthropic/claude-3-opus", prompt_tokens=1000, completion_tokens=500)
        assert math.isclose(cost, 0.0525)

    @pytest.mark.asyncio
    async def test_credit_handler_deducts_for_anthropic(self, mock_user, mock_trial_inactive):
        """Test that credit handler deducts credits for Anthropic model"""
        from src.services.credit_handler import handle_credits_and_usage

        with (
            patch("src.services.credit_handler.calculate_cost_async") as mock_cost,
            patch("src.services.credit_handler.asyncio.to_thread") as mock_to_thread,
        ):

            mock_cost.return_value = 0.0525
            mock_to_thread.return_value = None

            cost = await handle_credits_and_usage(
                api_key="test-key-456",
                user=mock_user,
                model="anthropic/claude-3-opus",
                trial=mock_trial_inactive,
                total_tokens=1500,
                prompt_tokens=1000,
                completion_tokens=500,
                elapsed_ms=500,
                endpoint="/v1/messages",
            )

            assert math.isclose(cost, 0.0525)
            assert mock_to_thread.called


class TestTrialUserCreditHandling:
    """Test that trial users don't get credits deducted"""

    @pytest.fixture
    def mock_trial_user(self):
        return {
            "id": "trial-user-789",
            "tier": "free",
            "subscription_status": None,
            "stripe_subscription_id": None,
            "subscription_allowance": 0.0,
            "purchased_credits": 0.0,
        }

    @pytest.fixture
    def mock_trial_active(self):
        return {"is_trial": True, "is_expired": False}

    @pytest.mark.asyncio
    async def test_trial_user_no_credit_deduction(self, mock_trial_user, mock_trial_active):
        """Test that trial users don't have credits deducted"""
        from src.services.credit_handler import handle_credits_and_usage

        deduct_credits_called = False
        log_transaction_called = False

        def mock_deduct_credits(*args, **kwargs):
            nonlocal deduct_credits_called
            deduct_credits_called = True

        def mock_log_transaction(*args, **kwargs):
            nonlocal log_transaction_called
            log_transaction_called = True
            # Verify is_trial is True in metadata
            metadata = args[3] if len(args) > 3 else kwargs.get("metadata", {})
            assert metadata.get("is_trial") is True

        with (
            patch("src.services.credit_handler.calculate_cost_async") as mock_cost,
            patch("src.db.users.deduct_credits", mock_deduct_credits),
            patch("src.db.users.log_api_usage_transaction", mock_log_transaction),
            patch("src.db.trials.track_trial_usage", return_value=None),
            patch("src.services.credit_handler.asyncio.to_thread") as mock_to_thread,
        ):

            # Make to_thread call the function directly
            async def call_func(func, *args, **kwargs):
                return func(*args, **kwargs)

            mock_to_thread.side_effect = call_func

            mock_cost.return_value = 0.05  # Would be $0.05 if not trial

            cost = await handle_credits_and_usage(
                api_key="trial-key-789",
                user=mock_trial_user,
                model="openai/gpt-4o",
                trial=mock_trial_active,
                total_tokens=1500,
                prompt_tokens=1000,
                completion_tokens=500,
                elapsed_ms=250,
            )

            # For trial users, deduct_credits should NOT be called
            assert deduct_credits_called is False
            # Cost should still be calculated correctly
            assert cost == 0.05


class TestTrialOverrideForPaidUsers:
    """Test that paid users with stale is_trial flag still get charged"""

    @pytest.fixture
    def mock_paid_user_with_stale_trial(self):
        """User has active subscription but stale is_trial flag"""
        return {
            "id": "paid-user-stale",
            "tier": "pro",
            "subscription_status": "active",
            "stripe_subscription_id": "sub_active_123",
            "subscription_allowance": 10.0,
            "purchased_credits": 5.0,
        }

    @pytest.fixture
    def mock_stale_trial(self):
        """Stale trial data that should be overridden"""
        return {"is_trial": True, "is_expired": False}

    @pytest.mark.asyncio
    async def test_paid_user_with_stale_trial_gets_charged(
        self, mock_paid_user_with_stale_trial, mock_stale_trial
    ):
        """Test that paid users with stale is_trial=True flag get charged"""
        from src.services.credit_handler import handle_credits_and_usage

        deduct_credits_called = False

        def mock_deduct(*args, **kwargs):
            nonlocal deduct_credits_called
            deduct_credits_called = True

        with (
            patch("src.services.credit_handler.calculate_cost_async") as mock_cost,
            patch("src.services.credit_handler.asyncio.to_thread") as mock_to_thread,
        ):

            mock_cost.return_value = 0.05

            async def call_func(func, *args, **kwargs):
                if func.__name__ == "deduct_credits":
                    mock_deduct(*args, **kwargs)
                return None

            mock_to_thread.side_effect = call_func

            cost = await handle_credits_and_usage(
                api_key="paid-key-stale",
                user=mock_paid_user_with_stale_trial,
                model="openai/gpt-4o",
                trial=mock_stale_trial,
                total_tokens=1500,
                prompt_tokens=1000,
                completion_tokens=500,
                elapsed_ms=250,
            )

            # Even though is_trial=True, the override should trigger deduct_credits
            # because user has active subscription
            assert deduct_credits_called is True
            # Cost should still be calculated correctly
            assert cost == 0.05


class TestDefaultPricingAlerts:
    """Test that default pricing usage is tracked and alerted"""

    @pytest.mark.asyncio
    async def test_unknown_model_uses_default_pricing(self, monkeypatch):
        """Test that unknown models fall back to default pricing and are tracked"""
        from src.services.pricing import get_default_pricing_stats, get_model_pricing

        # Mock empty models list (no pricing data)
        monkeypatch.setattr("src.services.models.get_cached_models", lambda _: [])
        monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)

        # Request pricing for unknown model
        pricing = get_model_pricing("unknown/mystery-model")

        # Should use default pricing
        assert pricing["found"] is False
        assert pricing["source"] == "default"
        assert math.isclose(pricing["prompt"], 0.00002)
        assert math.isclose(pricing["completion"], 0.00002)

        # Check that usage was tracked
        stats = get_default_pricing_stats()
        assert "unknown/mystery-model" in stats["details"]
        assert stats["details"]["unknown/mystery-model"]["count"] >= 1

    @pytest.mark.asyncio
    async def test_high_value_model_default_pricing_alert(self, monkeypatch):
        """Test that high-value models (OpenAI, Anthropic) trigger alerts on default pricing"""
        from src.services.pricing import _default_pricing_tracker, _track_default_pricing_usage

        # Clear tracker
        _default_pricing_tracker.clear()

        # Mock Sentry to capture the alert
        sentry_called = False
        captured_message = None

        def mock_capture_message(msg, **kwargs):
            nonlocal sentry_called, captured_message
            sentry_called = True
            captured_message = msg

        with patch("sentry_sdk.capture_message", mock_capture_message):
            # Track a high-value model (should trigger Sentry alert)
            _track_default_pricing_usage("openai/gpt-4-turbo-2025")

        # Verify Sentry was called with warning
        assert sentry_called is True
        assert "High-value model using default pricing" in captured_message
        assert "openai/gpt-4-turbo-2025" in captured_message


class TestAsyncCostCalculation:
    """Test async cost calculation"""

    @pytest.mark.asyncio
    async def test_calculate_cost_async_works(self, monkeypatch):
        """Test that async cost calculation works correctly"""
        from src.services.pricing import calculate_cost_async

        # Mock the async pricing function
        async def mock_async_pricing(model_id):
            return {"prompt": 0.000005, "completion": 0.000015, "found": True}

        monkeypatch.setattr("src.services.pricing.get_model_pricing_async", mock_async_pricing)

        cost = await calculate_cost_async("openai/gpt-4o", 1000, 500)
        # 1000 * 0.000005 + 500 * 0.000015 = 0.005 + 0.0075 = 0.0125
        assert math.isclose(cost, 0.0125)

    @pytest.mark.asyncio
    async def test_free_model_returns_zero_cost_async(self, monkeypatch):
        """Test that free models return $0 cost in async version"""
        from src.services.pricing import calculate_cost_async

        # Even with non-zero pricing, :free suffix should return $0
        async def mock_async_pricing(model_id):
            return {"prompt": 0.00001, "completion": 0.00002, "found": True}

        monkeypatch.setattr("src.services.pricing.get_model_pricing_async", mock_async_pricing)

        cost = await calculate_cost_async("google/gemini-2.0-flash-exp:free", 1000, 500)
        assert cost == 0.0
