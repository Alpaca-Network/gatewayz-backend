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
        from src.services.models import get_all_models
        from unittest.mock import patch

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
        from src.services.models import get_all_models_parallel, get_cached_models
        from unittest.mock import patch, MagicMock

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
                        assert "hug" in called_gateways, "Provider 'hug' (huggingface) not found in gateway calls"
                    else:
                        assert provider in called_gateways, f"Provider {provider} not found in gateway calls"
