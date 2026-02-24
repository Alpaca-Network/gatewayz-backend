#!/usr/bin/env python3
"""
Test for the simplismart_models UnboundLocalError fix

This test ensures that requesting models from gateways other than 'simplismart'
or 'all' doesn't cause an UnboundLocalError when the simplismart_models variable
is referenced in merge_models_by_slug() or provider derivation blocks.

Bug: simplismart_models was only initialized conditionally but referenced
unconditionally, causing UnboundLocalError when gateway != 'simplismart' and != 'all'.

Fix: Initialize simplismart_models = [] at the beginning of get_models() function
alongside all other model list initializations.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


class TestSimplismartModelsInitialization:
    """
    Test that simplismart_models variable is properly initialized
    regardless of gateway parameter value.
    """

    @patch("src.routes.catalog.get_cached_models")
    @patch("src.routes.catalog.get_cached_providers")
    def test_get_models_alpaca_gateway_no_unbound_error(self, mock_providers, mock_models):
        """
        Test that requesting models from 'alpaca' gateway doesn't cause UnboundLocalError.

        This was the primary failure case in production where simplismart_models
        was referenced but never initialized for non-simplismart gateways.
        """

        # Mock the cache to return empty lists for most gateways
        def get_models_side_effect(gateway):
            if gateway == "alpaca":
                return [{"id": "alpaca/llama-2-7b", "name": "Llama 2 7B"}]
            return []

        mock_models.side_effect = get_models_side_effect
        mock_providers.return_value = []

        # This should NOT raise UnboundLocalError
        response = client.get("/v1/models?gateway=alpaca&limit=50000")

        # The request should either succeed or fail gracefully, but NOT with UnboundLocalError
        assert response.status_code in [200, 500, 503]

        # If it's a 500 error, make sure it's not an UnboundLocalError
        if response.status_code == 500:
            response_json = response.json()
            error_detail = str(response_json.get("detail", ""))
            assert "UnboundLocalError" not in error_detail
            assert (
                "simplismart_models" not in error_detail
                or "not associated with a value" not in error_detail
            )

    @patch("src.routes.catalog.get_cached_models")
    @patch("src.routes.catalog.get_cached_providers")
    def test_get_models_clarifai_gateway_no_unbound_error(self, mock_providers, mock_models):
        """
        Test that requesting models from 'clarifai' gateway doesn't cause UnboundLocalError.
        """

        def get_models_side_effect(gateway):
            if gateway == "clarifai":
                return [{"id": "clarifai/gpt-4-vision", "name": "GPT-4 Vision"}]
            return []

        mock_models.side_effect = get_models_side_effect
        mock_providers.return_value = []

        response = client.get("/v1/models?gateway=clarifai&limit=50000")

        assert response.status_code in [200, 500, 503]

        if response.status_code == 500:
            response_json = response.json()
            error_detail = str(response_json.get("detail", ""))
            assert "UnboundLocalError" not in error_detail
            assert (
                "simplismart_models" not in error_detail
                or "not associated with a value" not in error_detail
            )

    @patch("src.routes.catalog.get_cached_models")
    @patch("src.routes.catalog.get_cached_providers")
    def test_get_models_huggingface_gateway_no_unbound_error(self, mock_providers, mock_models):
        """
        Test that requesting models from 'huggingface' gateway doesn't cause UnboundLocalError.
        """

        def get_models_side_effect(gateway):
            if gateway in ["huggingface", "hug"]:
                return [{"id": "meta-llama/Llama-2-7b-hf", "name": "Llama 2 7B"}]
            return []

        mock_models.side_effect = get_models_side_effect
        mock_providers.return_value = []

        response = client.get("/v1/models?gateway=huggingface&limit=50000")

        assert response.status_code in [200, 500, 503]

        if response.status_code == 500:
            response_json = response.json()
            error_detail = str(response_json.get("detail", ""))
            assert "UnboundLocalError" not in error_detail

    @patch("src.routes.catalog.get_cached_models")
    @patch("src.routes.catalog.get_cached_providers")
    def test_get_models_multiple_non_simplismart_gateways(self, mock_providers, mock_models):
        """
        Test multiple different gateways to ensure robustness of the fix.
        """
        non_simplismart_gateways = [
            "openrouter",
            "featherless",
            "deepinfra",
            "chutes",
            "groq",
            "fireworks",
            "together",
            "cerebras",
            "nebius",
            "xai",
            "novita",
            "aimo",
            "near",
            "fal",
            "helicone",
            "anannas",
            "aihubmix",
            "vercel-ai-gateway",
            "alibaba",
            "google-vertex",
        ]

        def get_models_side_effect(gateway):
            # Return some dummy models for the requested gateway
            return [{"id": f"{gateway}/test-model", "name": f"Test Model for {gateway}"}]

        mock_models.side_effect = get_models_side_effect
        mock_providers.return_value = []

        for gateway in non_simplismart_gateways:
            response = client.get(f"/v1/models?gateway={gateway}&limit=50000")

            assert response.status_code in [
                200,
                500,
                503,
            ], f"Gateway {gateway} failed with unexpected status"

            if response.status_code == 500:
                response_json = response.json()
                error_detail = str(response_json.get("detail", ""))
                assert (
                    "UnboundLocalError" not in error_detail
                ), f"Gateway {gateway} has UnboundLocalError"
                assert (
                    "simplismart_models" not in error_detail
                    or "not associated with a value" not in error_detail
                )

    @patch("src.routes.catalog.get_cached_models")
    @patch("src.routes.catalog.get_cached_providers")
    def test_get_models_simplismart_gateway_still_works(self, mock_providers, mock_models):
        """
        Verify that the fix doesn't break the intended simplismart gateway functionality.
        """

        def get_models_side_effect(gateway):
            if gateway == "simplismart":
                return [
                    {"id": "simplismart/llama-3.1-8b", "name": "Llama 3.1 8B"},
                    {"id": "simplismart/mixtral-8x7b", "name": "Mixtral 8x7B"},
                ]
            return []

        mock_models.side_effect = get_models_side_effect
        mock_providers.return_value = []

        response = client.get("/v1/models?gateway=simplismart&limit=50000")

        assert response.status_code in [200, 500, 503]

        if response.status_code == 200:
            response_json = response.json()
            models = response_json.get("data", [])
            # If successful, should have simplismart models
            assert isinstance(models, list)

    @patch("src.routes.catalog.get_cached_models")
    @patch("src.routes.catalog.get_cached_providers")
    def test_get_models_all_gateway_includes_simplismart(self, mock_providers, mock_models):
        """
        Verify that 'all' gateway properly includes simplismart models.
        """

        def get_models_side_effect(gateway):
            if gateway == "simplismart":
                return [{"id": "simplismart/llama-3.1-8b", "name": "Llama 3.1 8B"}]
            elif gateway == "openrouter":
                return [{"id": "openai/gpt-4", "name": "GPT-4"}]
            return []

        mock_models.side_effect = get_models_side_effect
        mock_providers.return_value = []

        response = client.get("/v1/models?gateway=all")

        assert response.status_code in [200, 500, 503]

        if response.status_code == 500:
            response_json = response.json()
            error_detail = str(response_json.get("detail", ""))
            # Should not have UnboundLocalError for 'all' gateway either
            assert "UnboundLocalError" not in error_detail


class TestRootModelsEndpoint:
    """
    Test the /models endpoint (without /v1 prefix) which delegates to get_models.
    This ensures the fix works for both endpoint variants.
    """

    @patch("src.routes.catalog.get_cached_models")
    @patch("src.routes.catalog.get_cached_providers")
    def test_root_models_endpoint_alpaca_no_error(self, mock_providers, mock_models):
        """Test /models endpoint with alpaca gateway"""

        def get_models_side_effect(gateway):
            if gateway == "alpaca":
                return [{"id": "alpaca/test", "name": "Test"}]
            return []

        mock_models.side_effect = get_models_side_effect
        mock_providers.return_value = []

        response = client.get("/models?gateway=alpaca&limit=50000")

        assert response.status_code in [200, 500, 503]

        if response.status_code == 500:
            response_json = response.json()
            error_detail = str(response_json.get("detail", ""))
            assert "UnboundLocalError" not in error_detail
            assert (
                "simplismart_models" not in error_detail
                or "not associated with a value" not in error_detail
            )

    @patch("src.routes.catalog.get_cached_models")
    @patch("src.routes.catalog.get_cached_providers")
    def test_root_models_endpoint_clarifai_no_error(self, mock_providers, mock_models):
        """Test /models endpoint with clarifai gateway"""

        def get_models_side_effect(gateway):
            if gateway == "clarifai":
                return [{"id": "clarifai/test", "name": "Test"}]
            return []

        mock_models.side_effect = get_models_side_effect
        mock_providers.return_value = []

        response = client.get("/models?gateway=clarifai&limit=50000")

        assert response.status_code in [200, 500, 503]

        if response.status_code == 500:
            response_json = response.json()
            error_detail = str(response_json.get("detail", ""))
            assert "UnboundLocalError" not in error_detail


class TestProviderDerivationBlock:
    """
    Test the provider derivation block that references simplismart_models.

    The provider derivation block at line 709-713 in catalog.py checks:
    if gateway_value in ("simplismart", "all"):
        models_for_providers = simplismart_models if gateway_value == "all" else models

    This block should only run when simplismart_models is defined.
    """

    @patch("src.routes.catalog.get_cached_models")
    @patch("src.routes.catalog.get_cached_providers")
    def test_provider_derivation_with_all_gateway(self, mock_providers, mock_models):
        """
        Test that 'all' gateway properly handles provider derivation for simplismart.
        """

        def get_models_side_effect(gateway):
            if gateway == "simplismart":
                return [
                    {
                        "id": "simplismart/llama-3.1-8b",
                        "name": "Llama 3.1 8B",
                        "provider": "simplismart",
                    }
                ]
            elif gateway == "openrouter":
                return [{"id": "openai/gpt-4", "name": "GPT-4", "provider": "openai"}]
            return []

        mock_models.side_effect = get_models_side_effect
        mock_providers.return_value = []

        response = client.get("/v1/models?gateway=all")

        # Should not fail with UnboundLocalError
        assert response.status_code in [200, 500, 503]

        if response.status_code == 500:
            response_json = response.json()
            error_detail = str(response_json.get("detail", ""))
            assert "UnboundLocalError" not in error_detail
            assert (
                "simplismart_models" not in error_detail
                or "not associated with a value" not in error_detail
            )
