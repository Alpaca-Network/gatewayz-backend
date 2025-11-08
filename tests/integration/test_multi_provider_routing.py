"""
Integration tests for multi-provider model routing.

Tests that models can be routed through multiple providers with automatic
failover and correct model ID transformation.
"""

import pytest
from unittest.mock import patch, MagicMock

from src.services.multi_provider_registry import (
    MultiProviderModel,
    ProviderConfig,
    get_registry,
)
from src.services.model_transformations import (
    transform_model_id,
    detect_provider_from_model_id,
)


@pytest.fixture
def test_model():
    """Create a test multi-provider model"""
    model = MultiProviderModel(
        id="test-llama-70b",
        name="Test Llama 70B",
        description="Test model for multi-provider routing",
        context_length=8192,
        modalities=["text"],
        aliases=[
            "llama-70b",
            "meta-llama/llama-70b",
        ],
        providers=[
            ProviderConfig(
                name="openrouter",
                model_id="meta-llama/llama-70b-instruct",
                priority=1,
                cost_per_1k_input=0.18,
                cost_per_1k_output=0.18,
                features=["streaming"],
            ),
            ProviderConfig(
                name="fireworks",
                model_id="accounts/fireworks/models/llama-70b-instruct",
                priority=2,
                cost_per_1k_input=0.20,
                cost_per_1k_output=0.20,
                features=["streaming"],
            ),
        ],
    )
    return model


@pytest.fixture
def registry_with_test_model(test_model):
    """Get registry and register test model"""
    registry = get_registry()
    # Clear any existing models
    registry._models.clear()
    registry._alias_to_canonical.clear()
    registry._provider_index.clear()
    # Register test model
    registry.register_model(test_model)
    yield registry
    # Cleanup
    registry._models.clear()
    registry._alias_to_canonical.clear()
    registry._provider_index.clear()


def test_registry_alias_resolution(registry_with_test_model):
    """Test that aliases resolve to canonical IDs"""
    registry = registry_with_test_model
    
    # Test canonical ID resolves to itself
    assert registry.resolve_canonical_id("test-llama-70b") == "test-llama-70b"
    
    # Test aliases resolve to canonical
    assert registry.resolve_canonical_id("llama-70b") == "test-llama-70b"
    assert registry.resolve_canonical_id("meta-llama/llama-70b") == "test-llama-70b"
    
    # Test case-insensitive resolution
    assert registry.resolve_canonical_id("LLAMA-70B") == "test-llama-70b"
    assert registry.resolve_canonical_id("Meta-Llama/Llama-70B") == "test-llama-70b"
    
    # Test provider-specific model ID resolution
    assert registry.resolve_canonical_id("meta-llama/llama-70b-instruct") == "test-llama-70b"
    assert registry.resolve_canonical_id("accounts/fireworks/models/llama-70b-instruct") == "test-llama-70b"


def test_get_provider_model_id(registry_with_test_model):
    """Test retrieving provider-specific model IDs"""
    registry = registry_with_test_model
    
    # Test getting provider model IDs
    assert registry.get_provider_model_id("test-llama-70b", "openrouter") == "meta-llama/llama-70b-instruct"
    assert registry.get_provider_model_id("test-llama-70b", "fireworks") == "accounts/fireworks/models/llama-70b-instruct"
    
    # Test non-existent provider
    assert registry.get_provider_model_id("test-llama-70b", "nonexistent") is None
    
    # Test non-existent model
    assert registry.get_provider_model_id("nonexistent-model", "openrouter") is None


def test_find_provider_for_model_id(registry_with_test_model):
    """Test finding provider from provider-specific model ID"""
    registry = registry_with_test_model
    
    # Test finding provider from provider-specific model ID
    assert registry.find_provider_for_model_id("meta-llama/llama-70b-instruct") == "openrouter"
    assert registry.find_provider_for_model_id("accounts/fireworks/models/llama-70b-instruct") == "fireworks"
    
    # Test case-insensitive
    assert registry.find_provider_for_model_id("Meta-Llama/Llama-70B-Instruct") == "openrouter"
    
    # Test preferred provider
    assert registry.find_provider_for_model_id("meta-llama/llama-70b-instruct", preferred_provider="openrouter") == "openrouter"


def test_provider_selection(registry_with_test_model):
    """Test provider selection based on priority"""
    registry = registry_with_test_model
    
    # Test default selection (should pick highest priority = lowest number)
    provider = registry.select_provider("test-llama-70b")
    assert provider is not None
    assert provider.name == "openrouter"
    assert provider.priority == 1
    
    # Test preferred provider selection
    provider = registry.select_provider("test-llama-70b", preferred_provider="fireworks")
    assert provider is not None
    assert provider.name == "fireworks"
    
    # Test selection with required features
    provider = registry.select_provider("test-llama-70b", required_features=["streaming"])
    assert provider is not None
    assert "streaming" in provider.features


def test_get_fallback_providers(registry_with_test_model):
    """Test getting fallback providers"""
    registry = registry_with_test_model
    
    # Test getting all providers
    fallbacks = registry.get_fallback_providers("test-llama-70b")
    assert len(fallbacks) == 2
    assert fallbacks[0].name == "openrouter"
    assert fallbacks[1].name == "fireworks"
    
    # Test excluding primary provider
    fallbacks = registry.get_fallback_providers("test-llama-70b", exclude_provider="openrouter")
    assert len(fallbacks) == 1
    assert fallbacks[0].name == "fireworks"


def test_transform_model_id_with_registry(registry_with_test_model):
    """Test model ID transformation using registry"""
    
    # Test canonical ID transformation
    assert transform_model_id("test-llama-70b", "openrouter") == "meta-llama/llama-70b-instruct"
    assert transform_model_id("test-llama-70b", "fireworks") == "accounts/fireworks/models/llama-70b-instruct"
    
    # Test alias transformation
    assert transform_model_id("llama-70b", "openrouter") == "meta-llama/llama-70b-instruct"
    assert transform_model_id("meta-llama/llama-70b", "fireworks") == "accounts/fireworks/models/llama-70b-instruct"
    
    # Test case-insensitive transformation
    assert transform_model_id("LLAMA-70B", "openrouter") == "meta-llama/llama-70b-instruct"


def test_detect_provider_from_model_id_with_registry(registry_with_test_model):
    """Test provider detection using registry"""
    
    # Test canonical ID detection
    provider = detect_provider_from_model_id("test-llama-70b")
    assert provider == "openrouter"  # Should select highest priority
    
    # Test alias detection
    provider = detect_provider_from_model_id("llama-70b")
    assert provider == "openrouter"
    
    # Test provider-specific model ID detection
    provider = detect_provider_from_model_id("meta-llama/llama-70b-instruct")
    assert provider == "openrouter"
    
    provider = detect_provider_from_model_id("accounts/fireworks/models/llama-70b-instruct")
    assert provider == "fireworks"
    
    # Test preferred provider
    provider = detect_provider_from_model_id("test-llama-70b", preferred_provider="fireworks")
    assert provider == "fireworks"


def test_curated_models_initialization():
    """Test that curated models are properly initialized"""
    from src.services.curated_models_config import (
        get_llama_models,
        get_deepseek_models,
        get_qwen_models,
        get_all_curated_models,
    )
    
    # Test Llama models
    llama_models = get_llama_models()
    assert len(llama_models) >= 2  # At least 3.1 and 3.3
    
    # Find Llama 3.3 70B
    llama_33 = next((m for m in llama_models if "3.3" in m.id and "70b" in m.id), None)
    assert llama_33 is not None
    assert len(llama_33.providers) >= 3  # OpenRouter, Fireworks, Together, etc.
    assert len(llama_33.aliases) > 0
    
    # Test DeepSeek models
    deepseek_models = get_deepseek_models()
    assert len(deepseek_models) >= 2  # V3 and R1
    
    # Find DeepSeek V3
    deepseek_v3 = next((m for m in deepseek_models if "v3" in m.id.lower()), None)
    assert deepseek_v3 is not None
    assert len(deepseek_v3.providers) >= 3  # Multiple providers
    
    # Test all curated models
    all_models = get_all_curated_models()
    assert len(all_models) >= 8  # Llama + DeepSeek + Qwen + Mistral + Gemma


def test_multi_provider_failover_chain():
    """Test that failover chain works with multi-provider models"""
    from src.services.curated_models_config import get_llama_models
    
    registry = get_registry()
    registry._models.clear()
    registry._alias_to_canonical.clear()
    registry._provider_index.clear()
    
    # Register Llama 3.3 70B
    llama_models = get_llama_models()
    llama_33 = next((m for m in llama_models if "3.3" in m.id and "70b" in m.id), None)
    if llama_33:
        registry.register_model(llama_33)
        
        # Test provider chain
        primary = registry.select_provider(llama_33.id)
        assert primary is not None
        
        fallbacks = registry.get_fallback_providers(llama_33.id, exclude_provider=primary.name)
        assert len(fallbacks) >= 2  # Should have multiple fallback options
        
        # Verify order by priority
        for i in range(len(fallbacks) - 1):
            assert fallbacks[i].priority <= fallbacks[i + 1].priority


def test_provider_model_normalization():
    """Test that provider model IDs are properly normalized"""
    from src.services.curated_models_config import get_deepseek_models
    
    registry = get_registry()
    registry._models.clear()
    registry._alias_to_canonical.clear()
    registry._provider_index.clear()
    
    deepseek_models = get_deepseek_models()
    deepseek_v3 = next((m for m in deepseek_models if "v3" in m.id.lower()), None)
    
    if deepseek_v3:
        registry.register_model(deepseek_v3)
        
        # Test various input formats resolve to same canonical ID
        test_inputs = [
            "deepseek-v3",
            "deepseek-ai/deepseek-v3",
            "DEEPSEEK-V3",
            "DeepSeek-AI/DeepSeek-V3",
        ]
        
        for input_id in test_inputs:
            canonical = registry.resolve_canonical_id(input_id)
            assert canonical == deepseek_v3.id, f"Failed to resolve '{input_id}' to canonical ID"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
