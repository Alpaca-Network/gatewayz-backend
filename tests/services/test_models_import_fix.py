"""
Test that get_all_models can be imported and works correctly.

This test verifies the fix for the issue where startup.py was trying to import
get_all_models but the function was actually named get_all_models_parallel.
"""


class TestGetAllModelsImport:
    """Test suite for get_all_models import compatibility."""

    def test_get_all_models_can_be_imported(self):
        """Verify get_all_models can be imported from models module."""
        from src.services.models import get_all_models

        assert get_all_models is not None
        assert callable(get_all_models)

    def test_get_all_models_is_alias_of_parallel(self):
        """Verify get_all_models is an alias of get_all_models_parallel."""
        from src.services.models import get_all_models, get_all_models_parallel

        # They should be the same function
        assert get_all_models == get_all_models_parallel

    def test_get_all_models_returns_models_including_simplismart(self):
        """Verify get_all_models returns models including SimpliSmart."""
        from unittest.mock import patch

        from src.services.models import get_all_models

        # Mock the cache to make test deterministic
        mock_models = [
            {
                "id": "test-model-1",
                "name": "Test Model 1",
                "source_gateway": "simplismart",
                "provider": "simplismart",
            },
            {
                "id": "test-model-2",
                "name": "Test Model 2",
                "source_gateway": "openai",
                "provider": "openai",
            },
        ]

        with patch("src.services.models.get_cached_models", return_value=mock_models):
            # Call the function
            models = get_all_models()

            # Should return a list
            assert isinstance(models, list)

            # Check if we have SimpliSmart models
            simplismart_models = [m for m in models if m.get("source_gateway") == "simplismart"]
            assert len(simplismart_models) > 0

            # Verify SimpliSmart models have required fields
            for model in simplismart_models:
                assert "id" in model
                assert "source_gateway" in model
                assert model["source_gateway"] == "simplismart"
                assert "provider" in model

    def test_startup_can_import_get_all_models(self):
        """Verify the startup module can successfully import get_all_models."""
        # This simulates what startup.py does
        import importlib
        import sys

        # Dynamically import to test fresh import path
        if "src.services.models" in sys.modules:
            # Reload to ensure fresh import
            importlib.reload(sys.modules["src.services.models"])

        # This is what startup.py does
        from src.services.models import get_all_models

        assert get_all_models is not None
        assert callable(get_all_models)

    def test_all_provider_gateways_included(self):
        """Verify all key providers are supported by get_all_models_parallel."""
        from unittest.mock import MagicMock, patch

        from src.services.models import get_all_models_parallel, get_cached_models

        # Test that the function attempts to fetch models from key providers
        # by mocking get_cached_models and verifying it's called with expected gateways
        expected_providers = [
            "openrouter",
            "simplismart",
            "openai",
            "anthropic",
            "clarifai",
        ]

        mock_get_cached = MagicMock(return_value=[])

        with patch("src.services.models.get_cached_models", mock_get_cached):
            with patch("src.services.models.is_gateway_in_error_state", return_value=False):
                # Call the function
                get_all_models_parallel()

                # Verify key providers were requested
                called_gateways = [call[0][0] for call in mock_get_cached.call_args_list]

                for provider in expected_providers:
                    # Note: 'huggingface' is referenced as 'hug' in the code
                    if provider == "huggingface":
                        assert (
                            "hug" in called_gateways
                        ), "Provider 'hug' (huggingface) not found in gateway calls"
                    else:
                        assert (
                            provider in called_gateways
                        ), f"Provider {provider} not found in gateway calls"

    def test_gateway_registry_consistency(self):
        """Verify all gateways from GATEWAY_REGISTRY are included in parallel fetch."""
        from unittest.mock import MagicMock, patch

        from src.routes.catalog import GATEWAY_REGISTRY
        from src.services.models import get_all_models_parallel

        mock_get_cached = MagicMock(return_value=[])

        with patch("src.services.models.get_cached_models", mock_get_cached):
            with patch("src.services.models.is_gateway_in_error_state", return_value=False):
                get_all_models_parallel()

                called_gateways = [call[0][0] for call in mock_get_cached.call_args_list]

                # Check each gateway in the registry
                missing_gateways = []
                for gateway_id, config in GATEWAY_REGISTRY.items():
                    # Handle aliases (e.g., 'huggingface' -> 'hug')
                    aliases = config.get("aliases", [])
                    gateway_found = gateway_id in called_gateways or any(
                        alias in called_gateways for alias in aliases
                    )

                    # Skip 'alpaca' as it doesn't have a fetch function yet
                    if gateway_id == "alpaca":
                        continue

                    if not gateway_found:
                        missing_gateways.append(gateway_id)

                assert len(missing_gateways) == 0, (
                    f"Missing gateways in get_all_models_parallel: {missing_gateways}. "
                    "These gateways are registered but not fetched."
                )

    def test_morpheus_gateway_included(self):
        """Verify morpheus gateway is included in parallel fetch."""
        from unittest.mock import MagicMock, patch

        from src.services.models import get_all_models_parallel

        mock_get_cached = MagicMock(return_value=[])

        with patch("src.services.models.get_cached_models", mock_get_cached):
            with patch("src.services.models.is_gateway_in_error_state", return_value=False):
                get_all_models_parallel()

                called_gateways = [call[0][0] for call in mock_get_cached.call_args_list]
                assert "morpheus" in called_gateways, "morpheus gateway missing from parallel fetch"

    def test_vercel_ai_gateway_included(self):
        """Verify vercel-ai-gateway is included in parallel fetch."""
        from unittest.mock import MagicMock, patch

        from src.services.models import get_all_models_parallel

        mock_get_cached = MagicMock(return_value=[])

        with patch("src.services.models.get_cached_models", mock_get_cached):
            with patch("src.services.models.is_gateway_in_error_state", return_value=False):
                get_all_models_parallel()

                called_gateways = [call[0][0] for call in mock_get_cached.call_args_list]
                assert (
                    "vercel-ai-gateway" in called_gateways
                ), "vercel-ai-gateway missing from parallel fetch"

    def test_sybil_gateway_included(self):
        """Verify sybil gateway is included in parallel fetch."""
        from unittest.mock import MagicMock, patch

        from src.services.models import get_all_models_parallel

        mock_get_cached = MagicMock(return_value=[])

        with patch("src.services.models.get_cached_models", mock_get_cached):
            with patch("src.services.models.is_gateway_in_error_state", return_value=False):
                get_all_models_parallel()

                called_gateways = [call[0][0] for call in mock_get_cached.call_args_list]
                assert "sybil" in called_gateways, "sybil gateway missing from parallel fetch"

    def test_near_ai_fallback_models_are_correct(self):
        """Verify Near AI fallback models match the actual models available on the platform."""
        from unittest.mock import MagicMock, patch

        import httpx

        from src.config import Config
        from src.services.models import fetch_models_from_near

        # Expected models as of 2026-01 from https://cloud-api.near.ai/v1/model/list
        # Note: normalize_near_model() prefixes IDs with "near/"
        expected_model_ids = {
            "near/deepseek-ai/DeepSeek-V3.1",
            "near/openai/gpt-oss-120b",
            "near/Qwen/Qwen3-30B-A3B-Instruct-2507",
            "near/zai-org/GLM-4.6",
            "near/zai-org/GLM-4.7",
        }

        # Mock Config.NEAR_API_KEY and httpx.get to trigger fallback
        # Use httpx.RequestError which is caught by the inner exception handler
        with patch.object(Config, "NEAR_API_KEY", "test-key"):
            with patch("src.services.models.httpx.get") as mock_httpx_get:
                mock_httpx_get.side_effect = httpx.RequestError("API unavailable")

                # Fetch models - should use fallback due to API failure
                models = fetch_models_from_near()

                # Should return fallback models
                assert len(models) == 5, f"Expected 5 fallback models, got {len(models)}"

                # Verify all expected models are present
                model_ids = {m["id"] for m in models}
                assert model_ids == expected_model_ids, (
                    f"Near AI fallback models mismatch.\n"
                    f"Expected: {expected_model_ids}\n"
                    f"Got: {model_ids}"
                )

                # Verify pricing is set for all models
                for model in models:
                    assert "pricing" in model, f"Model {model['id']} missing pricing"
                    pricing = model["pricing"]
                    assert "prompt" in pricing, f"Model {model['id']} missing prompt pricing"
                    assert (
                        "completion" in pricing
                    ), f"Model {model['id']} missing completion pricing"
                    # Pricing values are strings that can be parsed to floats
                    assert (
                        float(pricing["prompt"]) > 0
                    ), f"Model {model['id']} has zero prompt pricing"
                    assert (
                        float(pricing["completion"]) > 0
                    ), f"Model {model['id']} has zero completion pricing"

    def test_get_fallback_models_from_db_function_exists(self):
        """Verify get_fallback_models_from_db function is available."""
        from src.services.models import get_fallback_models_from_db

        assert get_fallback_models_from_db is not None
        assert callable(get_fallback_models_from_db)

    def test_get_fallback_models_from_db_handles_missing_provider(self):
        """Verify get_fallback_models_from_db returns None for unknown provider."""
        from unittest.mock import MagicMock, patch

        from src.services.models import get_fallback_models_from_db

        # Mock the database function to return empty list
        with patch("src.db.models_catalog_db.get_models_by_provider_slug", return_value=[]):
            result = get_fallback_models_from_db("nonexistent-provider")
            assert result is None

    def test_get_fallback_models_from_db_converts_models_correctly(self):
        """Verify get_fallback_models_from_db converts database models to raw format."""
        from unittest.mock import patch

        from src.services.models import get_fallback_models_from_db

        # Mock database response
        mock_db_models = [
            {
                "provider_model_id": "test-model-1",
                "model_name": "Test Model 1",
                "description": "A test model",
                "context_length": 8192,
                "pricing_prompt": 0.000001,
                "pricing_completion": 0.000002,
                "metadata": {},
            }
        ]

        with patch(
            "src.db.models_catalog_db.get_models_by_provider_slug", return_value=mock_db_models
        ):
            result = get_fallback_models_from_db("test-provider")

            assert result is not None
            assert len(result) == 1
            assert result[0]["id"] == "test-model-1"
            assert result[0]["name"] == "Test Model 1"
            assert result[0]["context_length"] == 8192

    def test_get_fallback_models_from_db_near_pricing_format(self):
        """Verify Near AI models get correct pricing format with amount and scale."""
        from unittest.mock import patch

        from src.services.models import get_fallback_models_from_db

        # Mock database response for Near AI
        mock_db_models = [
            {
                "provider_model_id": "deepseek-ai/DeepSeek-V3.1",
                "model_name": "DeepSeek V3.1",
                "context_length": 128000,
                "pricing_prompt": 0.00000105,  # $1.05 per million tokens
                "pricing_completion": 0.0000031,  # $3.10 per million tokens
                "metadata": {"contextLength": 128000},
            }
        ]

        with patch(
            "src.db.models_catalog_db.get_models_by_provider_slug", return_value=mock_db_models
        ):
            result = get_fallback_models_from_db("near")

            assert result is not None
            assert len(result) == 1
            # Near AI expects inputCostPerToken/outputCostPerToken format
            assert "inputCostPerToken" in result[0]
            assert "outputCostPerToken" in result[0]
            assert result[0]["inputCostPerToken"]["scale"] == -6
