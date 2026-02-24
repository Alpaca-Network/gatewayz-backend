"""
Comprehensive tests for Request Prioritization service
"""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest


class TestRequestPrioritization:
    """Test Request Prioritization service functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        import src.services.request_prioritization

        assert src.services.request_prioritization is not None

    def test_module_has_expected_attributes(self):
        """Test module exports"""
        from src.services import request_prioritization

        assert hasattr(request_prioritization, "__name__")


class TestLowLatencyModels:
    """Tests for low-latency model configuration and routing"""

    def test_ultra_low_latency_models_defined(self):
        """Test that ultra-low-latency models are defined"""
        from src.services.request_prioritization import ULTRA_LOW_LATENCY_MODELS

        assert ULTRA_LOW_LATENCY_MODELS is not None
        assert len(ULTRA_LOW_LATENCY_MODELS) > 0

    def test_low_latency_models_defined(self):
        """Test that low-latency models are defined"""
        from src.services.request_prioritization import LOW_LATENCY_MODELS

        assert LOW_LATENCY_MODELS is not None
        assert len(LOW_LATENCY_MODELS) > 0

    def test_ultra_low_latency_subset_of_low_latency(self):
        """Test that ultra-low-latency models are included in low-latency set"""
        from src.services.request_prioritization import (
            LOW_LATENCY_MODELS,
            ULTRA_LOW_LATENCY_MODELS,
        )

        for model in ULTRA_LOW_LATENCY_MODELS:
            assert model in LOW_LATENCY_MODELS, f"{model} should be in LOW_LATENCY_MODELS"

    def test_is_low_latency_model(self):
        """Test is_low_latency_model function"""
        from src.services.request_prioritization import is_low_latency_model

        # Known low-latency models
        assert is_low_latency_model("groq/llama-3.3-70b-versatile") is True
        assert is_low_latency_model("groq/moonshotai/kimi-k2-instruct-0905") is True

        # Case insensitivity
        assert is_low_latency_model("GROQ/LLAMA-3.3-70B-VERSATILE") is True

        # Non-low-latency model
        assert is_low_latency_model("anthropic/claude-sonnet-4.5") is False

        # Edge cases
        assert is_low_latency_model(None) is False
        assert is_low_latency_model("") is False

    def test_is_ultra_low_latency_model(self):
        """Test is_ultra_low_latency_model function"""
        from src.services.request_prioritization import is_ultra_low_latency_model

        # Known ultra-low-latency models
        assert is_ultra_low_latency_model("groq/moonshotai/kimi-k2-instruct-0905") is True
        assert is_ultra_low_latency_model("groq/openai/gpt-oss-120b") is True

        # Low-latency but not ultra-low-latency
        assert is_ultra_low_latency_model("groq/llama-3.3-70b-versatile") is False

        # Edge cases
        assert is_ultra_low_latency_model(None) is False
        assert is_ultra_low_latency_model("") is False

    def test_get_low_latency_models(self):
        """Test get_low_latency_models returns sorted list"""
        from src.services.request_prioritization import get_low_latency_models

        models = get_low_latency_models()
        assert isinstance(models, list)
        assert len(models) > 0
        # Check it's sorted
        assert models == sorted(models)

    def test_get_ultra_low_latency_models(self):
        """Test get_ultra_low_latency_models returns sorted list"""
        from src.services.request_prioritization import get_ultra_low_latency_models

        models = get_ultra_low_latency_models()
        assert isinstance(models, list)
        assert len(models) > 0
        # Check it's sorted
        assert models == sorted(models)


class TestProviderLatencyTiers:
    """Tests for provider latency tier configuration"""

    def test_provider_latency_tiers_defined(self):
        """Test that provider latency tiers are defined"""
        from src.services.request_prioritization import PROVIDER_LATENCY_TIERS

        assert PROVIDER_LATENCY_TIERS is not None
        assert len(PROVIDER_LATENCY_TIERS) > 0

    def test_groq_is_tier_1(self):
        """Test that Groq is classified as tier 1 (fastest)"""
        from src.services.request_prioritization import PROVIDER_LATENCY_TIERS

        assert PROVIDER_LATENCY_TIERS.get("groq") == 1

    def test_cerebras_is_tier_1(self):
        """Test that Cerebras is classified as tier 1 (fastest)"""
        from src.services.request_prioritization import PROVIDER_LATENCY_TIERS

        assert PROVIDER_LATENCY_TIERS.get("cerebras") == 1

    def test_fireworks_is_tier_2(self):
        """Test that Fireworks is classified as tier 2"""
        from src.services.request_prioritization import PROVIDER_LATENCY_TIERS

        assert PROVIDER_LATENCY_TIERS.get("fireworks") == 2

    def test_get_provider_latency_tier(self):
        """Test get_provider_latency_tier function"""
        from src.services.request_prioritization import (
            DEFAULT_PROVIDER_TIER,
            get_provider_latency_tier,
        )

        assert get_provider_latency_tier("groq") == 1
        assert get_provider_latency_tier("GROQ") == 1  # Case insensitivity
        assert get_provider_latency_tier("fireworks") == 2
        assert get_provider_latency_tier("unknown-provider") == DEFAULT_PROVIDER_TIER

    def test_get_fastest_providers(self):
        """Test get_fastest_providers returns sorted list"""
        from src.services.request_prioritization import get_fastest_providers

        providers = get_fastest_providers()
        assert isinstance(providers, list)
        assert len(providers) > 0
        # Groq should be first (tier 1)
        assert "groq" in providers[:2]  # Should be in first two (tier 1)


class TestLowLatencyAlternatives:
    """Tests for low-latency alternative suggestions"""

    def test_suggest_low_latency_alternative_for_claude(self):
        """Test suggesting alternatives for Claude models"""
        from src.services.request_prioritization import suggest_low_latency_alternative

        alternative = suggest_low_latency_alternative("anthropic/claude-sonnet-4.5")
        assert alternative is not None
        assert "groq" in alternative.lower() or "fireworks" in alternative.lower()

    def test_suggest_low_latency_alternative_for_gpt(self):
        """Test suggesting alternatives for GPT models"""
        from src.services.request_prioritization import suggest_low_latency_alternative

        alternative = suggest_low_latency_alternative("openai/gpt-4o")
        assert alternative is not None
        assert "groq" in alternative.lower()

    def test_suggest_low_latency_alternative_for_reasoning_model(self):
        """Test suggesting alternatives for reasoning models"""
        from src.services.request_prioritization import suggest_low_latency_alternative

        alternative = suggest_low_latency_alternative("deepseek/deepseek-r1")
        assert alternative is not None
        # R1 should map to fast alternative like deepseek-v3
        assert "deepseek" in alternative.lower() or "groq" in alternative.lower()

    def test_suggest_low_latency_alternative_for_unknown(self):
        """Test suggesting alternatives for unknown models"""
        from src.services.request_prioritization import suggest_low_latency_alternative

        alternative = suggest_low_latency_alternative("unknown/random-model")
        # Should return None when no pattern matches
        assert alternative is None


class TestProviderSelection:
    """Tests for provider selection based on priority"""

    def test_high_priority_gets_fastest_first(self):
        """Test that high priority requests get fastest providers first"""
        from src.services.request_prioritization import (
            RequestPriority,
            get_preferred_providers_for_priority,
        )

        available = ["openrouter", "groq", "huggingface", "fireworks"]
        ordered = get_preferred_providers_for_priority(RequestPriority.HIGH, available)

        # Groq (tier 1) should be before openrouter (tier 3)
        groq_idx = ordered.index("groq")
        openrouter_idx = ordered.index("openrouter")
        assert groq_idx < openrouter_idx

    def test_low_priority_gets_slower_first(self):
        """Test that low priority requests get slower providers first"""
        from src.services.request_prioritization import (
            RequestPriority,
            get_preferred_providers_for_priority,
        )

        available = ["openrouter", "groq", "huggingface", "fireworks"]
        ordered = get_preferred_providers_for_priority(RequestPriority.LOW, available)

        # For low priority, tier 3/4 should come before tier 1
        openrouter_idx = ordered.index("openrouter")
        groq_idx = ordered.index("groq")
        assert openrouter_idx < groq_idx

    def test_all_providers_included(self):
        """Test that all available providers are included in result"""
        from src.services.request_prioritization import (
            RequestPriority,
            get_preferred_providers_for_priority,
        )

        available = ["openrouter", "groq", "unknown-new-provider", "fireworks"]
        ordered = get_preferred_providers_for_priority(RequestPriority.MEDIUM, available)

        assert set(ordered) == set(available)

    def test_empty_providers_list(self):
        """Test handling of empty providers list"""
        from src.services.request_prioritization import (
            RequestPriority,
            get_preferred_providers_for_priority,
        )

        ordered = get_preferred_providers_for_priority(RequestPriority.HIGH, [])
        assert ordered == []
