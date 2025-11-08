"""
Integration tests for multi-provider routing functionality.

These tests verify that models can route through multiple providers with
automatic failover and that the canonical registry works correctly.
"""

import pytest
from src.services.canonical_model_registry import (
    CanonicalModel,
    CanonicalModelRegistry,
    get_canonical_registry,
)
from src.services.multi_provider_registry import ProviderConfig
from src.services.registry_router import (
    get_provider_chain_for_model,
    should_attempt_failover,
    get_model_info,
)
from src.services.model_transformations import (
    transform_model_id,
    detect_provider_from_model_id,
)
from fastapi import HTTPException


class TestCanonicalModelRegistry:
    """Test the CanonicalModelRegistry functionality"""

    def test_register_and_retrieve_model(self):
        """Test registering and retrieving a model from the registry"""
        registry = CanonicalModelRegistry()

        model = CanonicalModel(
            id="test-model-1",
            name="Test Model 1",
            description="A test model",
            providers=[
                ProviderConfig(
                    name="provider-a",
                    model_id="provider-a/test-model-1",
                    priority=1,
                    cost_per_1k_input=1.0,
                    cost_per_1k_output=2.0,
                ),
                ProviderConfig(
                    name="provider-b",
                    model_id="provider-b/test-model-1",
                    priority=2,
                    cost_per_1k_input=1.5,
                    cost_per_1k_output=2.5,
                ),
            ],
        )

        registry.register_model(model)

        # Test retrieval
        retrieved = registry.get_model("test-model-1")
        assert retrieved is not None
        assert retrieved.id == "test-model-1"
        assert len(retrieved.providers) == 2
        assert retrieved.primary_provider == "provider-a"

    def test_get_models_by_provider(self):
        """Test filtering models by provider"""
        registry = CanonicalModelRegistry()

        model1 = CanonicalModel(
            id="model-1",
            name="Model 1",
            providers=[
                ProviderConfig(name="openrouter", model_id="or/model-1", priority=1),
            ],
        )

        model2 = CanonicalModel(
            id="model-2",
            name="Model 2",
            providers=[
                ProviderConfig(name="fireworks", model_id="fw/model-2", priority=1),
            ],
        )

        model3 = CanonicalModel(
            id="model-3",
            name="Model 3",
            providers=[
                ProviderConfig(name="openrouter", model_id="or/model-3", priority=1),
                ProviderConfig(name="fireworks", model_id="fw/model-3", priority=2),
            ],
        )

        registry.register_models([model1, model2, model3])

        # Get models available on openrouter
        openrouter_models = registry.get_models_by_provider("openrouter")
        assert len(openrouter_models) == 2
        assert "model-1" in [m.id for m in openrouter_models]
        assert "model-3" in [m.id for m in openrouter_models]

        # Get models available on fireworks
        fireworks_models = registry.get_models_by_provider("fireworks")
        assert len(fireworks_models) == 2
        assert "model-2" in [m.id for m in fireworks_models]
        assert "model-3" in [m.id for m in fireworks_models]

    def test_search_models(self):
        """Test searching models with various criteria"""
        registry = CanonicalModelRegistry()

        model1 = CanonicalModel(
            id="gpt-4o",
            name="GPT-4o",
            description="OpenAI's advanced model",
            context_length=128000,
            providers=[
                ProviderConfig(
                    name="openrouter",
                    model_id="openai/gpt-4o",
                    priority=1,
                    features=["streaming", "function_calling"],
                ),
            ],
        )

        model2 = CanonicalModel(
            id="claude-sonnet-4.5",
            name="Claude Sonnet 4.5",
            description="Anthropic's latest model",
            context_length=200000,
            providers=[
                ProviderConfig(
                    name="openrouter",
                    model_id="anthropic/claude-sonnet-4.5",
                    priority=1,
                    features=["streaming"],
                ),
            ],
        )

        registry.register_models([model1, model2])

        # Search by query
        results = registry.search_models(query="gpt")
        assert len(results) == 1
        assert results[0].id == "gpt-4o"

        # Search by minimum context length
        results = registry.search_models(min_context_length=150000)
        assert len(results) == 1
        assert results[0].id == "claude-sonnet-4.5"

        # Search by provider
        results = registry.search_models(provider="openrouter")
        assert len(results) == 2

    def test_get_multi_provider_models(self):
        """Test getting models with multiple providers"""
        registry = CanonicalModelRegistry()

        single_provider = CanonicalModel(
            id="single",
            name="Single Provider Model",
            providers=[ProviderConfig(name="provider-a", model_id="a/single", priority=1)],
        )

        multi_provider = CanonicalModel(
            id="multi",
            name="Multi Provider Model",
            providers=[
                ProviderConfig(name="provider-a", model_id="a/multi", priority=1),
                ProviderConfig(name="provider-b", model_id="b/multi", priority=2),
                ProviderConfig(name="provider-c", model_id="c/multi", priority=3),
            ],
        )

        registry.register_models([single_provider, multi_provider])

        multi_models = registry.get_multi_provider_models()
        assert len(multi_models) == 1
        assert multi_models[0].id == "multi"

    def test_provider_priority_ordering(self):
        """Test that providers are ordered by priority"""
        model = CanonicalModel(
            id="priority-test",
            name="Priority Test Model",
            providers=[
                ProviderConfig(name="low-priority", model_id="lp/model", priority=3),
                ProviderConfig(name="high-priority", model_id="hp/model", priority=1),
                ProviderConfig(name="mid-priority", model_id="mp/model", priority=2),
            ],
        )

        enabled = model.get_enabled_providers()
        assert len(enabled) == 3
        assert enabled[0].name == "high-priority"
        assert enabled[1].name == "mid-priority"
        assert enabled[2].name == "low-priority"


class TestRegistryRouter:
    """Test registry-driven routing functionality"""

    def test_get_provider_chain_from_registry(self):
        """Test getting provider chain from canonical registry"""
        registry = CanonicalModelRegistry()

        model = CanonicalModel(
            id="test-routing",
            name="Test Routing Model",
            providers=[
                ProviderConfig(name="primary", model_id="p1/test", priority=1),
                ProviderConfig(name="secondary", model_id="p2/test", priority=2),
                ProviderConfig(name="tertiary", model_id="p3/test", priority=3),
            ],
        )

        registry.register_model(model)

        # Get provider chain
        chain = get_provider_chain_for_model("test-routing", use_registry=True)

        assert len(chain) == 3
        assert chain[0]["provider"] == "primary"
        assert chain[0]["model_id"] == "p1/test"
        assert chain[0]["from_registry"] is True

        assert chain[1]["provider"] == "secondary"
        assert chain[2]["provider"] == "tertiary"

    def test_get_provider_chain_with_preferred_provider(self):
        """Test provider chain with preferred provider first"""
        registry = CanonicalModelRegistry()

        model = CanonicalModel(
            id="test-preferred",
            name="Test Preferred Provider",
            providers=[
                ProviderConfig(name="provider-a", model_id="a/test", priority=1),
                ProviderConfig(name="provider-b", model_id="b/test", priority=2),
                ProviderConfig(name="provider-c", model_id="c/test", priority=3),
            ],
        )

        registry.register_model(model)

        # Request with preferred provider
        chain = get_provider_chain_for_model(
            "test-preferred",
            initial_provider="provider-c",
            use_registry=True,
        )

        # Provider-c should be first even though it has lower priority
        assert chain[0]["provider"] == "provider-c"
        assert chain[1]["provider"] == "provider-a"  # Then by priority
        assert chain[2]["provider"] == "provider-b"

    def test_fallback_to_legacy_chain(self):
        """Test fallback to legacy chain when model not in registry"""
        # This model is not in the registry
        chain = get_provider_chain_for_model(
            "unknown-model",
            initial_provider="openrouter",
            use_registry=True,
        )

        # Should fall back to legacy failover chain
        assert len(chain) > 0
        assert chain[0]["from_registry"] is False
        assert chain[0]["provider"] == "openrouter"

    def test_model_info_retrieval(self):
        """Test getting model information"""
        registry = CanonicalModelRegistry()

        model = CanonicalModel(
            id="info-test",
            name="Info Test Model",
            description="Testing info retrieval",
            providers=[
                ProviderConfig(
                    name="provider-a",
                    model_id="a/info",
                    priority=1,
                    features=["streaming", "function_calling"],
                ),
            ],
        )

        registry.register_model(model)

        info = get_model_info("info-test")

        assert info["in_registry"] is True
        assert info["id"] == "info-test"
        assert info["name"] == "Info Test Model"
        assert "provider-a" in info["providers"]
        assert info["primary_provider"] == "provider-a"


class TestModelTransformations:
    """Test model ID transformations with canonical registry"""

    def test_transform_from_canonical_registry(self):
        """Test that transform_model_id uses canonical registry"""
        registry = CanonicalModelRegistry()

        model = CanonicalModel(
            id="transform-test",
            name="Transform Test",
            providers=[
                ProviderConfig(
                    name="fireworks",
                    model_id="accounts/fireworks/models/transform-test",
                    priority=1,
                ),
                ProviderConfig(
                    name="openrouter",
                    model_id="test/transform-test",
                    priority=2,
                ),
            ],
        )

        registry.register_model(model)

        # Transform for fireworks
        fireworks_id = transform_model_id("transform-test", "fireworks")
        assert "accounts/fireworks/models/transform-test" in fireworks_id.lower()

        # Transform for openrouter
        openrouter_id = transform_model_id("transform-test", "openrouter")
        assert "test/transform-test" in openrouter_id.lower()

    def test_detect_provider_from_canonical_registry(self):
        """Test provider detection from canonical registry"""
        registry = CanonicalModelRegistry()

        model = CanonicalModel(
            id="detect-test",
            name="Detect Test",
            providers=[
                ProviderConfig(name="primary-provider", model_id="pp/detect", priority=1),
                ProviderConfig(name="secondary-provider", model_id="sp/detect", priority=2),
            ],
        )

        registry.register_model(model)

        # Should detect primary provider
        detected = detect_provider_from_model_id("detect-test")
        assert detected == "primary-provider"

        # Should respect preferred provider if available
        detected = detect_provider_from_model_id("detect-test", preferred_provider="secondary-provider")
        assert detected == "secondary-provider"


class TestFailoverBehavior:
    """Test failover and error handling"""

    def test_should_attempt_failover(self):
        """Test failover decision logic"""
        # Should failover on retryable errors
        http_exc = HTTPException(status_code=503, detail="Service unavailable")
        assert should_attempt_failover(http_exc, attempt_number=1, total_attempts=3)

        # Should not failover on last attempt
        assert not should_attempt_failover(http_exc, attempt_number=3, total_attempts=3)

        # Should failover on other retryable status codes
        for status in [401, 403, 404, 502, 504]:
            exc = HTTPException(status_code=status, detail="Error")
            assert should_attempt_failover(exc, attempt_number=1, total_attempts=3)


# Pytest fixtures
@pytest.fixture(autouse=True)
def clean_registry():
    """Clean the registry before each test"""
    # Note: In a real scenario, you'd want to use a fresh registry instance
    # This is a simplified approach for testing
    yield
    # Cleanup after test if needed


def test_end_to_end_multi_provider_routing():
    """
    End-to-end test: Register a model with multiple providers and verify
    routing works correctly through the full stack.
    """
    registry = CanonicalModelRegistry()

    # Register a model similar to llama-3.3-70b with multiple providers
    llama_model = CanonicalModel(
        id="llama-3.3-70b",
        name="Llama 3.3 70B",
        description="Meta's latest 70B model",
        context_length=128000,
        providers=[
            ProviderConfig(
                name="fireworks",
                model_id="accounts/fireworks/models/llama-v3p3-70b-instruct",
                priority=1,
                cost_per_1k_input=0.90,
                cost_per_1k_output=0.90,
                features=["streaming", "function_calling"],
            ),
            ProviderConfig(
                name="together",
                model_id="meta-llama/Llama-3.3-70B-Instruct",
                priority=2,
                cost_per_1k_input=0.88,
                cost_per_1k_output=0.88,
                features=["streaming"],
            ),
            ProviderConfig(
                name="huggingface",
                model_id="meta-llama/Llama-3.3-70B-Instruct",
                priority=3,
                cost_per_1k_input=0.70,
                cost_per_1k_output=0.70,
                features=["streaming"],
            ),
        ],
    )

    registry.register_model(llama_model)

    # Test 1: Provider detection
    detected = detect_provider_from_model_id("llama-3.3-70b")
    assert detected == "fireworks"  # Should select highest priority

    # Test 2: Provider chain
    chain = get_provider_chain_for_model("llama-3.3-70b")
    assert len(chain) == 3
    assert chain[0]["provider"] == "fireworks"
    assert chain[1]["provider"] == "together"
    assert chain[2]["provider"] == "huggingface"

    # Test 3: Model ID transformation
    fw_id = transform_model_id("llama-3.3-70b", "fireworks")
    assert "llama-v3p3-70b-instruct" in fw_id.lower()

    tg_id = transform_model_id("llama-3.3-70b", "together")
    assert "llama-3.3-70b-instruct" in tg_id.lower()

    # Test 4: Model info
    info = get_model_info("llama-3.3-70b")
    assert info["in_registry"] is True
    assert len(info["providers"]) == 3
    assert info["supports_streaming"] is True
    assert info["supports_function_calling"] is True

    print("✓ End-to-end multi-provider routing test passed!")
