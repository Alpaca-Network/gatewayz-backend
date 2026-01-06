#!/usr/bin/env python3
"""
Tests for AiHubMix model normalization with direct pricing

Tests cover:
- Model normalization with pricing from AiHubMix API
- Price conversion from per 1K tokens to per 1M tokens
- Filtering of zero-priced models
"""

from src.services.models import normalize_aihubmix_model_with_pricing


class TestAiHubMixNormalizationWithPricing:
    """Test AiHubMix model normalization with pricing data"""

    def test_normalize_model_with_pricing(self):
        """Test normalizing a model with pricing from AiHubMix API"""
        # Sample AiHubMix model data with pricing (per 1K tokens)
        aihubmix_model = {
            "id": "gpt-5",
            "name": "GPT-5",
            "description": "OpenAI GPT-5 model",
            "context_length": 128000,
            "pricing": {
                "input": 1.25,  # $1.25 per 1K tokens
                "output": 10,   # $10 per 1K tokens
            },
        }

        normalized = normalize_aihubmix_model_with_pricing(aihubmix_model)

        # Verify basic fields
        assert normalized is not None
        assert normalized["id"] == "gpt-5"
        assert normalized["slug"] == "aihubmix/gpt-5"
        assert normalized["provider_slug"] == "aihubmix"
        assert normalized["source_gateway"] == "aihubmix"
        assert normalized["pricing_source"] == "aihubmix-api"

        # Verify pricing is converted from per 1K to per 1M tokens
        # $1.25 per 1K = $1250 per 1M tokens
        assert normalized["pricing"]["prompt"] == "1250.0"
        # $10 per 1K = $10000 per 1M tokens
        assert normalized["pricing"]["completion"] == "10000.0"
        assert normalized["pricing"]["request"] == "0"
        assert normalized["pricing"]["image"] == "0"

    def test_normalize_model_filters_zero_pricing(self):
        """Test that models with zero pricing are filtered out"""
        # Sample AiHubMix model with zero pricing (free model)
        aihubmix_model = {
            "id": "free-model",
            "name": "Free Model",
            "pricing": {
                "input": 0,
                "output": 0,
            },
        }

        normalized = normalize_aihubmix_model_with_pricing(aihubmix_model)

        # Should return None for free models to prevent credit drain
        assert normalized is None

    def test_normalize_model_filters_empty_pricing(self):
        """Test that models with empty pricing are filtered out"""
        aihubmix_model = {
            "id": "no-price-model",
            "name": "No Price Model",
            "pricing": {},
        }

        normalized = normalize_aihubmix_model_with_pricing(aihubmix_model)

        # Should return None for models without pricing
        assert normalized is None

    def test_normalize_model_missing_id(self):
        """Test that models without ID are filtered out"""
        aihubmix_model = {
            "name": "No ID Model",
            "pricing": {
                "input": 1.0,
                "output": 2.0,
            },
        }

        normalized = normalize_aihubmix_model_with_pricing(aihubmix_model)

        # Should return None for models without ID
        assert normalized is None

    def test_normalize_model_with_small_pricing(self):
        """Test normalizing a model with small pricing values"""
        # Sample AiHubMix model with small pricing
        aihubmix_model = {
            "id": "cheap-model",
            "name": "Cheap Model",
            "pricing": {
                "input": 0.0001,  # $0.0001 per 1K tokens
                "output": 0.0003,  # $0.0003 per 1K tokens
            },
        }

        normalized = normalize_aihubmix_model_with_pricing(aihubmix_model)

        assert normalized is not None
        # $0.0001 per 1K = $0.1 per 1M tokens
        assert normalized["pricing"]["prompt"] == "0.1"
        # $0.0003 per 1K = $0.3 per 1M tokens
        assert normalized["pricing"]["completion"] == "0.3"

    def test_normalize_model_context_length(self):
        """Test that context length is properly extracted"""
        aihubmix_model = {
            "id": "test-model",
            "context_length": 200000,
            "pricing": {
                "input": 1.0,
                "output": 2.0,
            },
        }

        normalized = normalize_aihubmix_model_with_pricing(aihubmix_model)

        assert normalized is not None
        assert normalized["context_length"] == 200000

    def test_normalize_model_default_context_length(self):
        """Test that default context length is used when not provided"""
        aihubmix_model = {
            "id": "test-model",
            "pricing": {
                "input": 1.0,
                "output": 2.0,
            },
        }

        normalized = normalize_aihubmix_model_with_pricing(aihubmix_model)

        assert normalized is not None
        assert normalized["context_length"] == 4096  # Default value

    def test_normalize_model_with_model_id_field(self):
        """Test normalizing a model with 'model_id' instead of 'id' field"""
        # Some AiHubMix API responses use 'model_id' instead of 'id'
        aihubmix_model = {
            "model_id": "llama-3.2-11b-vision-preview",
            "developer_id": 11,
            "desc": "Vision model from Meta",
            "pricing": {
                "input": 0.2,
                "output": 0.2,
            },
            "input_modalities": "text,image",
        }

        normalized = normalize_aihubmix_model_with_pricing(aihubmix_model)

        # Verify model is normalized correctly with model_id field
        assert normalized is not None
        assert normalized["id"] == "llama-3.2-11b-vision-preview"
        assert normalized["slug"] == "aihubmix/llama-3.2-11b-vision-preview"
        assert normalized["provider_slug"] == "aihubmix"
        # $0.2 per 1K = $200 per 1M tokens
        assert normalized["pricing"]["prompt"] == "200.0"
        assert normalized["pricing"]["completion"] == "200.0"
        # Description from 'desc' field
        assert normalized["description"] == "Vision model from Meta"
        # Input modalities should include image
        assert "image" in normalized["architecture"]["input_modalities"]

    def test_normalize_model_with_desc_field(self):
        """Test normalizing a model with 'desc' instead of 'description' field"""
        aihubmix_model = {
            "id": "test-model",
            "desc": "Short description",
            "pricing": {
                "input": 1.0,
                "output": 2.0,
            },
        }

        normalized = normalize_aihubmix_model_with_pricing(aihubmix_model)

        assert normalized is not None
        assert normalized["description"] == "Short description"
