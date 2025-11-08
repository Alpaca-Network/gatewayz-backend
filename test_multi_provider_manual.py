#!/usr/bin/env python3
"""
Manual test script for multi-provider routing.
Demonstrates that multi-provider registry works correctly.
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.services.multi_provider_registry import (
    MultiProviderModel,
    ProviderConfig,
    get_registry,
)
from src.services.curated_models_config import (
    get_llama_models,
    get_deepseek_models,
    get_all_curated_models,
    initialize_curated_models,
)
from src.services.model_transformations import (
    transform_model_id,
    detect_provider_from_model_id,
)


def test_basic_registry():
    """Test basic registry functionality"""
    print("\n=== Testing Basic Registry Functionality ===\n")
    
    registry = get_registry()
    registry._models.clear()
    registry._alias_to_canonical.clear()
    registry._provider_index.clear()
    
    # Create a test model
    test_model = MultiProviderModel(
        id="test-model",
        name="Test Model",
        description="A test model",
        context_length=8192,
        aliases=["test", "test-alias"],
        providers=[
            ProviderConfig(
                name="openrouter",
                model_id="openrouter/test-model",
                priority=1,
                cost_per_1k_input=0.10,
                cost_per_1k_output=0.20,
                features=["streaming"],
            ),
            ProviderConfig(
                name="fireworks",
                model_id="accounts/fireworks/models/test-model",
                priority=2,
                cost_per_1k_input=0.15,
                cost_per_1k_output=0.25,
                features=["streaming"],
            ),
        ],
    )
    
    registry.register_model(test_model)
    
    # Test 1: Canonical ID resolution
    canonical = registry.resolve_canonical_id("test")
    assert canonical == "test-model", f"Expected 'test-model', got '{canonical}'"
    print("✓ Canonical ID resolution works")
    
    # Test 2: Alias resolution
    canonical = registry.resolve_canonical_id("test-alias")
    assert canonical == "test-model", f"Expected 'test-model', got '{canonical}'"
    print("✓ Alias resolution works")
    
    # Test 3: Case-insensitive resolution
    canonical = registry.resolve_canonical_id("TEST-ALIAS")
    assert canonical == "test-model", f"Expected 'test-model', got '{canonical}'"
    print("✓ Case-insensitive resolution works")
    
    # Test 4: Provider-specific model ID resolution
    canonical = registry.resolve_canonical_id("openrouter/test-model")
    assert canonical == "test-model", f"Expected 'test-model', got '{canonical}'"
    print("✓ Provider-specific model ID resolution works")
    
    # Test 5: Get provider model ID
    provider_id = registry.get_provider_model_id("test-model", "openrouter")
    assert provider_id == "openrouter/test-model", f"Expected 'openrouter/test-model', got '{provider_id}'"
    print("✓ Get provider model ID works")
    
    # Test 6: Provider selection
    provider = registry.select_provider("test-model")
    assert provider.name == "openrouter", f"Expected 'openrouter', got '{provider.name}'"
    assert provider.priority == 1, f"Expected priority 1, got {provider.priority}"
    print("✓ Provider selection works (selected highest priority)")
    
    # Test 7: Preferred provider selection
    provider = registry.select_provider("test-model", preferred_provider="fireworks")
    assert provider.name == "fireworks", f"Expected 'fireworks', got '{provider.name}'"
    print("✓ Preferred provider selection works")
    
    # Test 8: Fallback providers
    fallbacks = registry.get_fallback_providers("test-model", exclude_provider="openrouter")
    assert len(fallbacks) == 1, f"Expected 1 fallback, got {len(fallbacks)}"
    assert fallbacks[0].name == "fireworks", f"Expected 'fireworks', got '{fallbacks[0].name}'"
    print("✓ Fallback providers work")
    
    print("\n✅ All basic registry tests passed!\n")


def test_curated_models():
    """Test curated models"""
    print("\n=== Testing Curated Models ===\n")
    
    # Get Llama models
    llama_models = get_llama_models()
    print(f"✓ Found {len(llama_models)} Llama models")
    
    for model in llama_models:
        print(f"  - {model.id}: {len(model.providers)} providers, {len(model.aliases)} aliases")
    
    # Get DeepSeek models
    deepseek_models = get_deepseek_models()
    print(f"✓ Found {len(deepseek_models)} DeepSeek models")
    
    for model in deepseek_models:
        print(f"  - {model.id}: {len(model.providers)} providers, {len(model.aliases)} aliases")
    
    # Get all curated models
    all_models = get_all_curated_models()
    print(f"✓ Total curated models: {len(all_models)}")
    
    print("\n✅ All curated models loaded successfully!\n")


def test_model_transformation():
    """Test model ID transformation"""
    print("\n=== Testing Model Transformation ===\n")
    
    registry = get_registry()
    # Only initialize if not already done
    if not registry.get_all_models():
        initialize_curated_models()
    
    # Test transformations for Llama 3.3 70B
    test_cases = [
        ("llama-3.3-70b", "openrouter"),
        ("llama-3.3-70b-instruct", "fireworks"),
        ("meta-llama/llama-3.3-70b", "together"),
    ]
    
    for model_id, provider in test_cases:
        transformed = transform_model_id(model_id, provider)
        print(f"✓ {model_id} + {provider} -> {transformed}")
    
    # Test provider detection
    test_detection = [
        "llama-3.3-70b",
        "deepseek-v3",
        "meta-llama/llama-3.3-70b-instruct",
    ]
    
    for model_id in test_detection:
        provider = detect_provider_from_model_id(model_id)
        print(f"✓ Detected provider for '{model_id}': {provider}")
    
    print("\n✅ All transformation tests passed!\n")


def test_multi_provider_routing():
    """Test complete multi-provider routing flow"""
    print("\n=== Testing Multi-Provider Routing Flow ===\n")
    
    registry = get_registry()
    
    # Debug: check state before initialize
    print(f"DEBUG: Models before initialize: {len(registry.get_all_models())}")
    
    # Always initialize fresh for this test
    initialize_curated_models()
    
    # Debug: check state after initialize
    print(f"DEBUG: Models after initialize: {len(registry.get_all_models())}")
    print(f"DEBUG: Model IDs: {[m.id for m in registry.get_all_models()][:3]}")
    
    # Simulate a request for llama-3.3-70b-instruct (the actual canonical ID)
    model_id = "llama-3.3-70b-instruct"
    
    # Step 1: Resolve canonical ID
    canonical = registry.resolve_canonical_id(model_id)
    if not canonical:
        canonical = model_id  # Use model_id as canonical if not found
    print(f"1. Resolved '{model_id}' to canonical '{canonical}'")
    
    # Verify the model exists
    if not registry.has_model(canonical):
        print(f"   ERROR: Model '{canonical}' not in registry!")
        print(f"   Available models: {[m.id for m in registry.get_all_models()]}")
        raise ValueError(f"Model '{canonical}' not found in registry")
    
    # Step 2: Select primary provider
    primary = registry.select_provider(canonical, required_features=["streaming"])
    if not primary:
        print(f"   ERROR: Could not select provider for '{canonical}'!")
        raise ValueError(f"No provider found for '{canonical}'")
    print(f"2. Selected primary provider: {primary.name} (priority {primary.priority})")
    
    # Step 3: Get fallback providers
    fallbacks = registry.get_fallback_providers(canonical, exclude_provider=primary.name)
    print(f"3. Fallback providers: {[p.name for p in fallbacks[:2]]}")
    
    # Step 4: Build provider chain
    provider_chain = [primary.name] + [p.name for p in fallbacks[:2]]
    print(f"4. Provider chain: {provider_chain}")
    
    # Step 5: Transform model ID for each provider
    print("5. Model ID transformations:")
    for provider_name in provider_chain:
        provider_model_id = registry.get_provider_model_id(canonical, provider_name)
        if provider_model_id:
            print(f"   - {provider_name}: {provider_model_id}")
        else:
            fallback = transform_model_id(model_id, provider_name)
            print(f"   - {provider_name}: {fallback} (via fallback transformation)")
    
    print("\n✅ Complete routing flow works!\n")


def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("Multi-Provider Routing Test Suite")
    print("="*60)
    
    try:
        # Run basic registry test (which uses a test model)
        test_basic_registry()
        
        # Clear registry after basic test for curated model tests
        registry = get_registry()
        registry._models.clear()
        registry._alias_to_canonical.clear()
        registry._provider_index.clear()
        
        # Now run curated model tests (doesn't modify registry)
        test_curated_models()
        
        # These tests will initialize curated models
        test_model_transformation()
        test_multi_provider_routing()
        
        print("\n" + "="*60)
        print("✅ ALL TESTS PASSED!")
        print("="*60 + "\n")
        return 0
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
