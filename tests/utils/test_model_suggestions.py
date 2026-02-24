"""
Unit tests for model name fuzzy matching and suggestions.

Tests the find_similar_models function to ensure it correctly suggests
similar model names for typos and variations.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.utils.model_suggestions import find_similar_models, get_available_models


@pytest.mark.asyncio
class TestModelSuggestions:
    """Test suite for model fuzzy matching."""

    async def test_exact_match(self):
        """Test exact model name returns as suggestion."""
        available = ["gpt-4", "gpt-3.5-turbo", "claude-2"]
        similar = await find_similar_models("gpt-4", available)

        assert "gpt-4" in similar
        assert similar[0] == "gpt-4"  # Exact match should be first

    async def test_typo_correction_gpt(self):
        """Test fuzzy matching for GPT model typos."""
        available = ["gpt-4", "gpt-4-turbo", "gpt-3.5-turbo"]
        similar = await find_similar_models("gpt-5", available)

        # Should suggest gpt-4 or gpt-4-turbo as they're similar
        assert "gpt-4" in similar or "gpt-4-turbo" in similar
        assert len(similar) > 0

    async def test_typo_correction_claude(self):
        """Test fuzzy matching for Claude model typos."""
        available = ["claude-2", "claude-3-opus", "claude-3-sonnet"]
        similar = await find_similar_models("claud-3", available)

        # Should suggest claude-3 variants
        assert any("claude-3" in model for model in similar)

    async def test_case_insensitive_matching(self):
        """Test case-insensitive matching."""
        available = ["GPT-4", "Claude-2", "Llama-2"]
        similar = await find_similar_models("gpt-4", available)

        assert len(similar) > 0
        assert "GPT-4" in similar

    async def test_case_insensitive_mixed(self):
        """Test case-insensitive with mixed case input."""
        available = ["gpt-4", "claude-2", "llama-2"]
        similar = await find_similar_models("GPT-4", available)

        assert len(similar) > 0
        assert "gpt-4" in similar

    async def test_no_matches(self):
        """Test when no similar models exist."""
        available = ["gpt-4", "claude-2"]
        similar = await find_similar_models("completely-different-model-12345-xyz", available)

        # Should return empty or very few matches
        assert len(similar) <= 1

    async def test_max_suggestions_limit(self):
        """Test max suggestions parameter."""
        available = [f"model-{i}" for i in range(100)]
        similar = await find_similar_models("model-1", available, max_suggestions=3)

        assert len(similar) <= 3

    async def test_max_suggestions_default(self):
        """Test default max suggestions is 5."""
        available = [f"gpt-4-variant-{i}" for i in range(20)]
        similar = await find_similar_models("gpt-4", available)

        # Default max is 5
        assert len(similar) <= 5

    async def test_cutoff_threshold(self):
        """Test similarity cutoff threshold."""
        available = ["gpt-4", "gpt-3.5-turbo", "completely-different"]
        similar = await find_similar_models("gpt-5", available, cutoff=0.8)

        # With high cutoff, should only return very similar matches
        assert "completely-different" not in similar

    async def test_cutoff_low_threshold(self):
        """Test low similarity cutoff allows more matches."""
        available = ["gpt-4", "gpt-3", "model-x"]
        similar = await find_similar_models("gpt", available, cutoff=0.3)

        # Low cutoff should return more matches
        assert len(similar) >= 2

    async def test_provider_prefix_matching(self):
        """Test matching models with provider prefixes."""
        available = [
            "openrouter/gpt-4",
            "openrouter/gpt-4-turbo",
            "openrouter/claude-2",
        ]
        similar = await find_similar_models("openrouter/gpt-5", available)

        # Should match similar openrouter models
        assert any("gpt-4" in model for model in similar)

    async def test_slash_separated_models(self):
        """Test models with slash separators."""
        available = [
            "provider/model-a",
            "provider/model-b",
            "other-provider/model-a",
        ]
        similar = await find_similar_models("provider/model-c", available)

        # Should prefer same provider
        assert any("provider/" in model for model in similar)

    async def test_version_number_matching(self):
        """Test matching models with version numbers."""
        available = ["gpt-3.5-turbo", "gpt-4", "gpt-4-turbo"]
        similar = await find_similar_models("gpt-3", available)

        assert "gpt-3.5-turbo" in similar

    async def test_hyphen_variations(self):
        """Test matching with hyphen variations."""
        available = ["gpt-4-turbo", "gpt-4-vision", "gpt-4-32k"]
        similar = await find_similar_models("gpt4turbo", available)

        # Should still match despite missing hyphens
        assert any("turbo" in model for model in similar)

    async def test_empty_available_models(self):
        """Test with empty available models list."""
        similar = await find_similar_models("gpt-4", [])

        assert len(similar) == 0

    async def test_empty_requested_model(self):
        """Test with empty requested model string."""
        available = ["gpt-4", "claude-2"]
        similar = await find_similar_models("", available)

        # Should return empty or no good matches
        assert len(similar) <= 1

    async def test_special_characters(self):
        """Test models with special characters."""
        available = ["gpt-4", "gpt-4-0613", "gpt-4-1106-preview"]
        similar = await find_similar_models("gpt-4-0614", available)

        # Should match gpt-4-0613 as very similar
        assert "gpt-4-0613" in similar or "gpt-4" in similar

    async def test_unicode_model_names(self):
        """Test with unicode characters in model names."""
        available = ["model-α", "model-β", "model-γ"]
        similar = await find_similar_models("model-a", available)

        # Basic fuzzy matching should still work
        assert len(similar) >= 0  # Should not crash

    async def test_very_long_model_names(self):
        """Test with very long model names."""
        long_name = "very-long-model-name-" + "-".join([str(i) for i in range(50)])
        available = [long_name, "gpt-4", "claude-2"]
        similar = await find_similar_models(long_name[:20], available)

        # Should handle long names without crashing
        assert isinstance(similar, list)

    async def test_similar_prefixes(self):
        """Test models with similar prefixes."""
        available = [
            "llama-2-7b",
            "llama-2-13b",
            "llama-2-70b",
            "llama-3-8b",
        ]
        similar = await find_similar_models("llama-2", available)

        # Should return all llama-2 variants
        llama_2_matches = [m for m in similar if "llama-2" in m]
        assert len(llama_2_matches) >= 2

    async def test_common_typos(self):
        """Test common typos are matched correctly."""
        test_cases = [
            ("gtp-4", "gpt-4"),  # Letter swap
            ("gpt-4o-mini", "gpt-4o-mini"),  # Exact match
            ("claud-3", "claude-3"),  # Missing letter
            ("lama-2", "llama-2"),  # Missing double letter
        ]

        for typo, correct in test_cases:
            available = [correct, "other-model"]
            similar = await find_similar_models(typo, available)
            assert correct in similar, f"Failed to match {typo} to {correct}"

    @patch("src.utils.model_suggestions.get_available_models")
    async def test_uses_catalog_when_not_provided(self, mock_get_models):
        """Test that it fetches from catalog when available_models not provided."""
        mock_get_models.return_value = ["gpt-4", "claude-2"]

        similar = await find_similar_models("gpt-5")

        # Should have called get_available_models
        mock_get_models.assert_called_once()
        assert isinstance(similar, list)

    async def test_preserves_original_case_in_results(self):
        """Test that original case is preserved in results."""
        available = ["GPT-4-Turbo", "Claude-3-Opus", "LLaMA-2"]
        similar = await find_similar_models("gpt-4", available)

        # Should return with original case
        if len(similar) > 0:
            assert similar[0] == "GPT-4-Turbo" or "Turbo" in similar[0]

    async def test_multiple_exact_matches(self):
        """Test behavior with multiple exact matches (different case)."""
        available = ["gpt-4", "GPT-4", "Gpt-4"]
        similar = await find_similar_models("gpt-4", available)

        # Should return at least one match
        assert len(similar) >= 1

    async def test_performance_with_large_catalog(self):
        """Test performance with large model catalog."""
        # Create 1000 model names
        available = [f"model-{i}-variant-{j}" for i in range(50) for j in range(20)]

        # Should complete quickly even with large catalog
        import time

        start = time.time()
        similar = await find_similar_models("model-25", available, max_suggestions=5)
        elapsed = time.time() - start

        assert elapsed < 1.0  # Should complete in under 1 second
        assert len(similar) <= 5

    async def test_suggestion_ordering(self):
        """Test that suggestions are ordered by similarity."""
        available = ["gpt-4", "gpt-4-turbo", "gpt-3.5-turbo", "claude-2"]
        similar = await find_similar_models("gpt-4-turb", available)

        # gpt-4-turbo should be first as it's most similar
        if len(similar) > 0:
            assert "turbo" in similar[0].lower()

    async def test_partial_name_matching(self):
        """Test matching with partial model names."""
        available = [
            "anthropic/claude-3-opus",
            "anthropic/claude-3-sonnet",
            "openai/gpt-4",
        ]
        similar = await find_similar_models("claude-3", available)

        # Should match claude-3 variants
        claude_matches = [m for m in similar if "claude-3" in m]
        assert len(claude_matches) >= 1


class TestGetAvailableModels:
    """Test suite for get_available_models helper."""

    @pytest.mark.asyncio
    @patch("src.utils.model_suggestions.get_all_models")
    async def test_fetches_from_catalog(self, mock_get_all_models):
        """Test that it fetches models from the catalog."""
        mock_get_all_models.return_value = [
            {"id": "gpt-4"},
            {"id": "claude-2"},
            {"id": "llama-2"},
        ]

        models = await get_available_models()

        assert "gpt-4" in models
        assert "claude-2" in models
        assert "llama-2" in models

    @pytest.mark.asyncio
    @patch("src.utils.model_suggestions.get_all_models")
    async def test_handles_empty_catalog(self, mock_get_all_models):
        """Test handling of empty model catalog."""
        mock_get_all_models.return_value = []

        models = await get_available_models()

        assert isinstance(models, list)
        assert len(models) == 0

    @pytest.mark.asyncio
    @patch("src.utils.model_suggestions.get_all_models")
    async def test_handles_catalog_error(self, mock_get_all_models):
        """Test handling of catalog fetch error."""
        mock_get_all_models.side_effect = Exception("Database error")

        # Should return empty list or fallback
        models = await get_available_models()

        assert isinstance(models, list)
        # Should not crash, returns empty or fallback list
