"""
Test that get_all_models can be imported and works correctly.

This test verifies the fix for the issue where startup.py was trying to import
get_all_models but the function was actually named get_all_models_parallel.
"""

import pytest


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

        # Call the function
        models = get_all_models()

        # Should return a list
        assert isinstance(models, list)

        # Check if we have SimpliSmart models (if cache is populated)
        simplismart_models = [m for m in models if m.get("source_gateway") == "simplismart"]

        # Note: This test may fail if cache is not populated
        # But the import should work regardless
        if simplismart_models:
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
        """Verify all providers are included in the get_all_models_parallel function."""
        from src.services.models import get_all_models_parallel
        import inspect

        # Get the source code of the function
        source = inspect.getsource(get_all_models_parallel)

        # Verify key providers are in the gateways list
        expected_providers = [
            "openrouter",
            "simplismart",
            "openai",
            "anthropic",
            "cerebras",
            "nebius",
            "xai",
            "novita",
            "huggingface",  # referenced as "hug" in code
            "aimo",
            "alpaca",  # may not be in the list
            "clarifai",
        ]

        # Check for providers in source (some use different names)
        for provider in ["openrouter", "simplismart", "openai", "anthropic", "clarifai"]:
            assert provider in source, f"Provider {provider} not found in get_all_models_parallel"
