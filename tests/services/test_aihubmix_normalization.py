#!/usr/bin/env python3
"""
Tests for AiHubMix model normalization with direct pricing

Tests cover:
- Model normalization with pricing from AiHubMix API
- Price conversion from per 1K tokens to per-token format (matching OpenRouter)
- Filtering of zero-priced models
- Logging behavior for models missing ID fields

Note: AiHubMix returns pricing per 1K tokens, which is converted to per-token
format by dividing by 1000. This matches the format used by OpenRouter and
expected by the calculate_cost() function for billing.
Example: $1.25/1K tokens -> stored as 0.00125 (per-token)
"""

import logging
from unittest.mock import patch

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
                "output": 10,  # $10 per 1K tokens
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

        # Verify pricing is converted from per 1K to per-token format
        # This matches OpenRouter format and what calculate_cost() expects
        # $1.25 per 1K tokens = $0.00125 per token
        assert normalized["pricing"]["prompt"] == "0.00125"
        # $10 per 1K tokens = $0.01 per token
        assert normalized["pricing"]["completion"] == "0.01"
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

        import math

        assert normalized is not None
        # $0.0001 per 1K tokens = $0.0000001 per token
        # Using math.isclose due to floating point precision
        assert math.isclose(float(normalized["pricing"]["prompt"]), 1e-07)
        # $0.0003 per 1K tokens = $0.0000003 per token
        assert math.isclose(float(normalized["pricing"]["completion"]), 3e-07)

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
        # $0.2 per 1K tokens = $0.0002 per token
        assert normalized["pricing"]["prompt"] == "0.0002"
        assert normalized["pricing"]["completion"] == "0.0002"
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

    def test_missing_id_logs_debug_not_warning(self, caplog):
        """Test that models missing both 'id' and 'model_id' log at DEBUG level, not WARNING

        This prevents excessive logging during catalog refresh and avoids hitting
        Railway's 500 logs/second rate limit.
        """
        aihubmix_model = {
            "name": "Model without ID",
            "pricing": {
                "input": 1.0,
                "output": 2.0,
            },
        }

        with caplog.at_level(logging.DEBUG):
            normalized = normalize_aihubmix_model_with_pricing(aihubmix_model)

        # Should return None
        assert normalized is None

        # Should log at DEBUG level, not WARNING
        assert any(
            "missing both 'id' and 'model_id' fields" in record.message
            and record.levelname == "DEBUG"
            for record in caplog.records
        ), "Expected DEBUG log message about missing ID fields"

        # Should NOT log at WARNING level
        assert not any(
            record.levelname == "WARNING" for record in caplog.records
        ), "Should not log at WARNING level to avoid excessive logging"

    def test_model_id_field_works_without_warnings(self, caplog):
        """Test that models with 'model_id' field are processed without warnings

        Ensures the fix properly handles the common case where AiHubMix API
        returns 'model_id' instead of 'id', which was causing hundreds of
        warning logs during catalog refresh.
        """
        aihubmix_model = {
            "model_id": "llama-3.1-405b-instruct",  # Using model_id, not id
            "developer_id": 11,
            "desc": "Meta's Llama model",
            "pricing": {
                "input": 4,  # $4 per 1K tokens
                "output": 4,  # $4 per 1K tokens
            },
        }

        with caplog.at_level(logging.WARNING):
            normalized = normalize_aihubmix_model_with_pricing(aihubmix_model)

        # Should successfully normalize
        assert normalized is not None
        assert normalized["id"] == "llama-3.1-405b-instruct"
        # $4 per 1K tokens = $0.004 per token
        assert normalized["pricing"]["prompt"] == "0.004"
        assert normalized["pricing"]["completion"] == "0.004"

        # Should NOT produce any warnings
        assert len(caplog.records) == 0, "Should not log warnings for valid model_id field"
