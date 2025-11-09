#!/usr/bin/env python3
"""
Simple demo of the multi-provider model registry.

This script demonstrates the core functionality of the multi-provider system
without requiring any external dependencies or configuration.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '.'))

from src.services.multi_provider_registry import MultiProviderModel, ProviderConfig, get_registry
from src.services.provider_selector import get_selector


def demo_multi_provider_system():
    """
    Demonstrate the multi-provider model routing system.
    """
    print("=== Multi-Provider Model Registry Demo ===\n")
    
    # Create sample multi-provider models
    print("1. Creating multi-provider models...")
    
    gemini_model = MultiProviderModel(
        id="gemini-1.5-flash",
        name="Gemini 1.5 Flash",
        description="Fast multimodal model",
        context_length=1000000,
        modalities=["text", "image"],
        categories=["chat", "multimodal"],
        capabilities=["streaming", "function_calling"],
        providers=[
            ProviderConfig(
                name="google-vertex",
                model_id="gemini-1.5-flash",
                priority=1,
                cost_per_1k_input=0.075,
                cost_per_1k_output=0.30,
                features=["streaming", "multimodal", "function_calling"],
                availability=True,
            ),
            ProviderConfig(
                name="openrouter",
                model_id="google/gemini-flash-1.5",
                priority=2,
                cost_per_1k_input=0.10,
                cost_per_1k_output=0.40,
                features=["streaming", "multimodal"],
                availability=True,
            ),
        ],
    )
    
    gpt_model = MultiProviderModel(
        id="gpt-4-turbo",
        name="GPT-4 Turbo",
        description="High-intelligence model",
        context_length=128000,
        modalities=["text"],
        categories=["chat", "reasoning"],
        capabilities=["function_calling", "streaming"],
        providers=[
            ProviderConfig(
                name="openai",
                model_id="gpt-4-turbo",
                priority=1,
                cost_per_1k_input=0.01,
                cost_per_1k_output=0.03,
                features=["streaming", "function_calling"],
                availability=True,
            ),
            ProviderConfig(
                name="openrouter",
                model_id="openai/gpt-4-turbo",
                priority=2,
                cost_per_1k_input=0.015,
                cost_per_1k_output=0.045,
                features=["streaming", "function_calling"],
                availability=True,
            ),
        ],
    )
    
    print("✓ Created sample models")
    
    # Register models
    print("\n2. Registering models...")
    registry = get_registry()
    registry.register_model(gemini_model)
    registry.register_model(gpt_model)
    print("✓ Registered models in the registry")
    
    # Show registered models
    print("\n3. Registered models:")
    models = registry.get_all_models()
    for model in models:
        print(f"  - {model.id}: {model.name}")
        for provider in model.providers:
            print(f"    └── {provider.name} ({provider.model_id}) [priority: {provider.priority}]")
    
    # Demonstrate provider selection
    print("\n4. Provider selection examples:")
    
    # Select primary provider (based on priority)
    provider = registry.select_provider("gemini-1.5-flash")
    print(f"  Primary provider for gemini-1.5-flash: {provider.name}")
    
    # Select with cost constraint
    cheap_provider = registry.select_provider("gemini-1.5-flash", max_cost=0.08)
    print(f"  Cheapest provider for gemini-1.5-flash: {cheap_provider.name}")
    
    # Select with preferred provider
    preferred_provider = registry.select_provider("gpt-4-turbo", preferred_provider="openrouter")
    print(f"  Preferred provider for gpt-4-turbo: {preferred_provider.name}")
    
    # Get fallback providers
    fallbacks = registry.get_fallback_providers("gemini-1.5-flash", exclude_provider="google-vertex")
    print(f"  Fallback providers for gemini-1.5-flash: {[p.name for p in fallbacks]}")
    
    # Demonstrate failover
    print("\n5. Failover demonstration:")
    selector = get_selector()
    
    # Mock execution function that fails on first provider
    call_count = 0
    def mock_execute(provider_name, model_id):
        nonlocal call_count
        call_count += 1
        print(f"    Attempt {call_count}: Calling {provider_name} for {model_id}")
        
        if call_count == 1:
            print(f"    ❌ {provider_name} failed!")
            raise Exception("Provider unavailable")
        else:
            print(f"    ✅ {provider_name} succeeded!")
            return {"result": "Success from " + provider_name}
    
    # Execute with failover
    result = selector.execute_with_failover(
        model_id="gemini-1.5-flash",
        execute_fn=mock_execute,
        max_retries=3
    )
    
    print(f"  Final result: {'Success' if result['success'] else 'Failed'}")
    if result['success']:
        print(f"  Provider used: {result['provider']}")
        print(f"  Response: {result['response']}")
    
    # Demonstrate circuit breaker
    print("\n6. Circuit breaker demonstration:")
    
    # Check initial health
    health = selector.check_provider_health("gemini-1.5-flash", "google-vertex")
    print(f"  Initial health of google-vertex: {health['available']}")
    
    # Record multiple failures to trigger circuit breaker
    for i in range(5):
        selector.health_tracker.record_failure("gemini-1.5-flash", "google-vertex")
        print(f"  Recorded failure {i+1}/5")
    
    # Check health after failures
    health = selector.check_provider_health("gemini-1.5-flash", "google-vertex")
    print(f"  Health after 5 failures: {health['available']}")
    print(f"  Reason: {health['reason']}")
    
    # Show that selection now avoids the failed provider
    provider = registry.select_provider("gemini-1.5-flash")
    print(f"  Provider selected after failures: {provider.name}")
    
    print("\n🎉 Demo completed successfully!")


if __name__ == "__main__":
    demo_multi_provider_system()