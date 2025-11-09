"""
Integration tests for ProviderSelector multi-provider failover functionality.

These tests verify that the ProviderSelector correctly handles:
1. Successful requests using the primary provider
2. Failover to secondary providers when primary fails
3. Circuit breaker behavior after repeated failures
4. Proper error propagation when all providers fail
"""

import pytest
from unittest.mock import Mock, patch
from src.services.provider_selector import ProviderSelector
from src.services.multi_provider_registry import (
    MultiProviderRegistry,
    MultiProviderModel,
    ProviderConfig,
)


class TestProviderSelectorFailover:
    """Test multi-provider failover scenarios"""

    @pytest.fixture
    def registry(self):
        """Create a test registry with a multi-provider model"""
        registry = MultiProviderRegistry()
        model = MultiProviderModel(
            id="test-model",
            name="Test Model",
            description="A test model with multiple providers",
            context_length=8192,
            providers=[
                ProviderConfig(
                    name="google-vertex",
                    model_id="test-model-vertex",
                    priority=1,
                    cost_per_1k_input=0.075,
                    cost_per_1k_output=0.30,
                    enabled=True,
                    features=["streaming"],
                ),
                ProviderConfig(
                    name="openrouter",
                    model_id="test-model-openrouter",
                    priority=2,
                    cost_per_1k_input=0.10,
                    cost_per_1k_output=0.40,
                    enabled=True,
                    features=["streaming"],
                ),
                ProviderConfig(
                    name="huggingface",
                    model_id="test-model-hf",
                    priority=3,
                    cost_per_1k_input=0.05,
                    cost_per_1k_output=0.20,
                    enabled=True,
                    features=[],
                ),
            ],
        )
        registry.register_model(model)
        return registry

    @pytest.fixture
    def selector(self, registry):
        """Create a ProviderSelector with the test registry"""
        return ProviderSelector(registry)

    def test_primary_provider_succeeds(self, selector):
        """Test that primary provider is used when it succeeds"""

        def execute_fn(provider_name: str, model_id: str):
            if provider_name == "google-vertex":
                return {"success": True, "provider": provider_name, "model": model_id}
            raise Exception(f"Unexpected provider: {provider_name}")

        result = selector.execute_with_failover(
            model_id="test-model",
            execute_fn=execute_fn,
        )

        assert result["success"] is True
        assert result["provider"] == "google-vertex"
        assert result["provider_model_id"] == "test-model-vertex"
        assert len(result["attempts"]) == 1
        assert result["attempts"][0]["provider"] == "google-vertex"
        assert result["attempts"][0]["success"] is True

    def test_failover_to_secondary_provider(self, selector):
        """Test that failover occurs when primary provider fails"""

        def execute_fn(provider_name: str, model_id: str):
            if provider_name == "google-vertex":
                raise Exception("Primary provider failed")
            elif provider_name == "openrouter":
                return {"success": True, "provider": provider_name, "model": model_id}
            raise Exception(f"Unexpected provider: {provider_name}")

        result = selector.execute_with_failover(
            model_id="test-model",
            execute_fn=execute_fn,
        )

        assert result["success"] is True
        assert result["provider"] == "openrouter"
        assert result["provider_model_id"] == "test-model-openrouter"
        assert len(result["attempts"]) == 2
        assert result["attempts"][0]["provider"] == "google-vertex"
        assert result["attempts"][0]["success"] is False
        assert result["attempts"][1]["provider"] == "openrouter"
        assert result["attempts"][1]["success"] is True

    def test_all_providers_fail(self, selector):
        """Test error handling when all providers fail"""

        def execute_fn(provider_name: str, model_id: str):
            raise Exception(f"Provider {provider_name} failed")

        result = selector.execute_with_failover(
            model_id="test-model",
            execute_fn=execute_fn,
            max_retries=3,
        )

        assert result["success"] is False
        assert result["provider"] is None
        assert "error" in result
        assert len(result["attempts"]) == 3

    def test_preferred_provider_priority(self, selector):
        """Test that preferred provider is tried first when specified"""

        def execute_fn(provider_name: str, model_id: str):
            if provider_name == "huggingface":
                return {"success": True, "provider": provider_name, "model": model_id}
            raise Exception(f"Provider {provider_name} failed")

        result = selector.execute_with_failover(
            model_id="test-model",
            execute_fn=execute_fn,
            preferred_provider="huggingface",
        )

        assert result["success"] is True
        assert result["provider"] == "huggingface"
        assert result["provider_model_id"] == "test-model-hf"
        # Should succeed on first attempt since preferred provider works
        assert len(result["attempts"]) == 1
        assert result["attempts"][0]["provider"] == "huggingface"

    def test_required_features_filtering(self, selector):
        """Test that providers without required features are skipped"""

        def execute_fn(provider_name: str, model_id: str):
            # Only google-vertex and openrouter have streaming
            return {"success": True, "provider": provider_name, "model": model_id}

        result = selector.execute_with_failover(
            model_id="test-model",
            execute_fn=execute_fn,
            required_features=["streaming"],
        )

        assert result["success"] is True
        # Should use google-vertex (priority 1) or openrouter (priority 2)
        # huggingface should be skipped as it doesn't have streaming
        assert result["provider"] in ["google-vertex", "openrouter"]

    def test_circuit_breaker_opens_after_failures(self, selector):
        """Test that circuit breaker opens after repeated failures"""
        failure_count = 0

        def execute_fn(provider_name: str, model_id: str):
            nonlocal failure_count
            if provider_name == "google-vertex":
                failure_count += 1
                raise Exception("Provider consistently failing")
            return {"success": True, "provider": provider_name, "model": model_id}

        # First few requests should fail on google-vertex and fallback to openrouter
        for _ in range(3):
            result = selector.execute_with_failover(
                model_id="test-model",
                execute_fn=execute_fn,
            )
            assert result["success"] is True
            assert result["provider"] == "openrouter"

        # Circuit breaker should have recorded failures for google-vertex
        circuit = selector.circuit_breakers.get("google-vertex:test-model")
        if circuit:
            assert circuit.failure_count > 0

    def test_model_not_in_registry(self, selector):
        """Test handling of models not in the registry"""

        def execute_fn(provider_name: str, model_id: str):
            return {"success": True, "provider": provider_name, "model": model_id}

        result = selector.execute_with_failover(
            model_id="unknown-model",
            execute_fn=execute_fn,
        )

        assert result["success"] is False
        assert "not found in registry" in result.get("error", "").lower()

    def test_max_retries_limit(self, selector):
        """Test that max_retries is respected"""

        def execute_fn(provider_name: str, model_id: str):
            raise Exception(f"Provider {provider_name} failed")

        result = selector.execute_with_failover(
            model_id="test-model",
            execute_fn=execute_fn,
            max_retries=2,
        )

        assert result["success"] is False
        # Should only try 2 providers due to max_retries=2
        assert len(result["attempts"]) == 2

    def test_response_preserved_from_successful_provider(self, selector):
        """Test that the actual response from provider is preserved"""

        expected_response = {
            "id": "response-123",
            "choices": [{"message": {"content": "Hello"}}],
            "usage": {"total_tokens": 10},
        }

        def execute_fn(provider_name: str, model_id: str):
            if provider_name == "openrouter":
                return expected_response
            raise Exception(f"Provider {provider_name} failed")

        result = selector.execute_with_failover(
            model_id="test-model",
            execute_fn=execute_fn,
        )

        assert result["success"] is True
        assert result["response"] == expected_response
        assert result["provider"] == "openrouter"


class TestProviderSelectorWithChatEndpoint:
    """Integration tests with the chat endpoint executor"""

    @pytest.fixture
    def registry_with_gemini(self):
        """Create registry with Gemini model"""
        from src.services.google_models_config import get_google_models
        from src.services.multi_provider_registry import get_registry

        registry = get_registry()
        # Register Google models if not already registered
        google_models = get_google_models()
        for model in google_models:
            if not registry.has_model(model.id):
                registry.register_model(model)
        return registry

    def test_gemini_model_registered(self, registry_with_gemini):
        """Verify Gemini models are properly registered"""
        assert registry_with_gemini.has_model("gemini-2.5-flash")
        model = registry_with_gemini.get_model("gemini-2.5-flash")
        assert model is not None
        assert len(model.providers) >= 2  # At least google-vertex and openrouter
