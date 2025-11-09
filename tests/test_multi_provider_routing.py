"""
Integration test for multi-provider model routing.

This test verifies that models can be routed through multiple providers
and that failover works correctly.
"""

import sys
import os

# Add src to path so we can import our modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.services.multi_provider_registry import (
    MultiProviderModel,
    ProviderConfig,
    get_registry,
)
from src.services.provider_selector import get_selector


def test_model_registration():
    """Test that models can be registered and retrieved"""
    print("Testing model registration...")
    
    # Create a sample multi-provider model
    model = MultiProviderModel(
        id="gemini-1.5-flash",
        name="Gemini 1.5 Flash",
        description="Fast and efficient model for everyday tasks",
        context_length=1000000,
        modalities=["text", "image", "audio", "video"],
        categories=["chat", "multimodal"],
        capabilities=["streaming", "function_calling"],
        providers=[
            ProviderConfig(
                name="google-vertex",
                model_id="gemini-1.5-flash",
                priority=1,
                requires_credentials=True,
                cost_per_1k_input=0.075,
                cost_per_1k_output=0.30,
                max_tokens=8192,
                features=["streaming", "multimodal", "function_calling"],
                availability=True,
            ),
            ProviderConfig(
                name="openrouter",
                model_id="google/gemini-flash-1.5",
                priority=2,
                requires_credentials=False,
                cost_per_1k_input=0.10,
                cost_per_1k_output=0.40,
                max_tokens=8192,
                features=["streaming", "multimodal"],
                availability=True,
            ),
        ],
    )
    
    # Get registry and clear any existing models
    registry = get_registry()
    registry._models.clear()
    
    # Register our test model
    registry.register_model(model)
    
    # Check that model is registered
    assert registry.has_model("gemini-1.5-flash"), "Model should be registered"
    
    # Retrieve the model
    retrieved_model = registry.get_model("gemini-1.5-flash")
    assert retrieved_model is not None, "Model should be retrievable"
    assert retrieved_model.id == "gemini-1.5-flash", "Model ID should match"
    assert retrieved_model.name == "Gemini 1.5 Flash", "Model name should match"
    assert len(retrieved_model.providers) == 2, "Should have 2 providers"
    
    # Check providers
    providers = {p.name: p for p in retrieved_model.providers}
    assert "google-vertex" in providers, "Should have google-vertex provider"
    assert "openrouter" in providers, "Should have openrouter provider"
    
    # Check provider details
    vertex_provider = providers["google-vertex"]
    assert vertex_provider.model_id == "gemini-1.5-flash", "Vertex model ID should match"
    assert vertex_provider.priority == 1, "Vertex should have priority 1"
    assert vertex_provider.cost_per_1k_input == 0.075, "Vertex cost should match"
    
    openrouter_provider = providers["openrouter"]
    assert openrouter_provider.model_id == "google/gemini-flash-1.5", "OpenRouter model ID should match"
    assert openrouter_provider.priority == 2, "OpenRouter should have priority 2"
    assert openrouter_provider.cost_per_1k_input == 0.10, "OpenRouter cost should match"
    
    print("✓ Model registration test passed")


def test_provider_selection():
    """Test provider selection with different criteria"""
    print("Testing provider selection...")
    
    registry = get_registry()
    
    # Test primary provider selection (should select google-vertex due to priority=1)
    primary_provider = registry.select_provider("gemini-1.5-flash")
    assert primary_provider is not None, "Should select a provider"
    assert primary_provider.name == "google-vertex", "Should select vertex as primary"
    
    # Test with preferred provider
    preferred_provider = registry.select_provider(
        "gemini-1.5-flash", 
        preferred_provider="openrouter"
    )
    assert preferred_provider is not None, "Should select preferred provider"
    assert preferred_provider.name == "openrouter", "Should select openrouter when preferred"
    
    # Test with required features
    streaming_provider = registry.select_provider(
        "gemini-1.5-flash",
        required_features=["streaming"]
    )
    assert streaming_provider is not None, "Should select provider with streaming"
    assert streaming_provider.name == "google-vertex", "Should still select primary"
    
    # Test with cost constraint
    cheap_provider = registry.select_provider(
        "gemini-1.5-flash",
        max_cost=0.08  # Should exclude openrouter (0.10) but include vertex (0.075)
    )
    assert cheap_provider is not None, "Should select affordable provider"
    assert cheap_provider.name == "google-vertex", "Should select vertex (cheaper)"
    
    print("✓ Provider selection test passed")


def test_fallback_providers():
    """Test getting fallback providers"""
    print("Testing fallback providers...")
    
    registry = get_registry()
    
    # Get fallback providers (should exclude google-vertex since it's primary)
    fallbacks = registry.get_fallback_providers(
        "gemini-1.5-flash",
        exclude_provider="google-vertex"
    )
    
    assert len(fallbacks) == 1, "Should have 1 fallback"
    assert fallbacks[0].name == "openrouter", "Fallback should be openrouter"
    
    # Get all providers without exclusion
    all_providers = registry.get_fallback_providers("gemini-1.5-flash")
    assert len(all_providers) == 2, "Should have 2 providers total"
    # Should be ordered by priority
    assert all_providers[0].name == "google-vertex", "First should be vertex (priority 1)"
    assert all_providers[1].name == "openrouter", "Second should be openrouter (priority 2)"
    
    print("✓ Fallback providers test passed")


def test_provider_failover_execution():
    """Test provider failover execution"""
    print("Testing provider failover execution...")
    
    registry = get_registry()
    selector = get_selector()
    
    # Mock execution function that succeeds on second attempt
    call_count = 0
    
    def mock_execute(provider_name, model_id):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First provider fails
            raise Exception("First provider unavailable")
        else:
            # Second provider succeeds
            return {"result": f"Success from {provider_name}"}
    
    # Execute with failover
    result = selector.execute_with_failover(
        model_id="gemini-1.5-flash",
        execute_fn=mock_execute,
        max_retries=3
    )
    
    # Should succeed on second attempt
    assert result["success"] is True, "Should succeed with failover"
    assert result["provider"] == "openrouter", "Should succeed with second provider"
    assert call_count == 2, "Should have called execute function twice"
    
    print("✓ Provider failover execution test passed")


def test_all_providers_fail():
    """Test behavior when all providers fail"""
    print("Testing all providers fail...")
    
    registry = get_registry()
    selector = get_selector()
    
    # Mock execution function that always fails
    def mock_execute(provider_name, model_id):
        raise Exception("All providers unavailable")
    
    # Execute with failover
    result = selector.execute_with_failover(
        model_id="gemini-1.5-flash",
        execute_fn=mock_execute,
        max_retries=3
    )
    
    # Should fail
    assert result["success"] is False, "Should fail when all providers fail"
    assert "All providers failed" in result["error"], "Error message should indicate all failed"
    
    print("✓ All providers fail test passed")


def test_model_categorization():
    """Test model categorization and filtering"""
    print("Testing model categorization...")
    
    registry = get_registry()
    
    # Verify model exists first
    model = registry.get_model("gemini-1.5-flash")
    assert model is not None, "Model should exist"
    
    # Test category filtering
    chat_models = registry.get_models_by_category("chat")
    assert len(chat_models) == 1, "Should find 1 chat model"
    assert chat_models[0].id == "gemini-1.5-flash", "Should find our test model"
    
    # Test capability filtering
    function_calling_models = registry.get_models_by_capability("function_calling")
    assert len(function_calling_models) == 1, "Should find 1 function calling model"
    assert function_calling_models[0].id == "gemini-1.5-flash", "Should find our test model"
    
    # Test provider filtering
    vertex_models = registry.get_models_by_provider("google-vertex")
    assert len(vertex_models) == 1, "Should find 1 vertex model"
    assert vertex_models[0].id == "gemini-1.5-flash", "Should find our test model"
    
    print("✓ Model categorization test passed")


def test_provider_health_tracking():
    """Test provider health tracking and circuit breaker"""
    print("Testing provider health tracking...")
    
    registry = get_registry()
    selector = get_selector()
    
    # Verify model exists first
    model = registry.get_model("gemini-1.5-flash")
    assert model is not None, "Model should exist"
    
    # Test initial health status
    health = selector.check_provider_health("gemini-1.5-flash", "google-vertex")
    assert health["available"] is True, "Provider should be initially available"
    assert health["reason"] == "Provider healthy", "Should report as healthy"
    
    # Simulate failures to trigger circuit breaker
    for i in range(5):  # Default threshold is 5
        selector.health_tracker.record_failure("gemini-1.5-flash", "google-vertex")
    
    # Check that provider is now disabled
    health = selector.check_provider_health("gemini-1.5-flash", "google-vertex")
    assert health["available"] is False, "Provider should be disabled after 5 failures"
    assert "circuit breaker" in health["reason"], "Should report circuit breaker"
    
    # Test that provider is excluded from selection
    provider = registry.select_provider("gemini-1.5-flash")
    # Should select openrouter instead of google-vertex
    assert provider.name == "openrouter", "Should fallback to openrouter"
    
    print("✓ Provider health tracking test passed")


def test_registry_updates():
    """Test registry update functionality"""
    print("Testing registry updates...")
    
    registry = get_registry()
    
    # Verify model exists first
    original_model = registry.get_model("gemini-1.5-flash")
    assert original_model is not None, "Model should exist"
    
    # Update model information
    updates = {
        "description": "Updated description",
        "context_length": 2000000
    }
    
    success = registry.update_model("gemini-1.5-flash", updates)
    assert success is True, "Update should succeed"
    
    # Verify updates
    updated_model = registry.get_model("gemini-1.5-flash")
    assert updated_model.description == "Updated description", "Description should be updated"
    assert updated_model.context_length == 2000000, "Context length should be updated"
    # Check that updated_at was changed
    assert updated_model.updated_at != original_model.updated_at, "Updated timestamp should change"
    
    print("✓ Registry updates test passed")


def run_all_tests():
    """Run all tests"""
    print("Running multi-provider routing tests...\n")
    
    try:
        test_model_registration()
        test_provider_selection()
        test_fallback_providers()
        test_provider_failover_execution()
        test_all_providers_fail()
        test_model_categorization()
        test_provider_health_tracking()
        test_registry_updates()
        
        print("\n🎉 All tests passed!")
        return True
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)