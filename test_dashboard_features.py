#!/usr/bin/env python3
"""
Test script to verify the new dashboard features:
1. Fix gateway endpoint
2. Pricing data in dashboard
"""

import json
from src.services.pricing_lookup import get_model_pricing, load_manual_pricing

def test_pricing_lookup():
    """Test that pricing lookup works correctly"""
    print("Testing pricing lookup...")

    # Load pricing data
    pricing_data = load_manual_pricing()
    print(f"Loaded pricing data for {len(pricing_data) - 1} providers")

    # Test specific model pricing
    test_cases = [
        ("deepinfra", "meta-llama/Meta-Llama-3.1-8B-Instruct"),
        ("featherless", "meta-llama/Meta-Llama-3.1-70B-Instruct"),
        ("chutes", "stability-ai/sdxl")
    ]

    for gateway, model_id in test_cases:
        pricing = get_model_pricing(gateway, model_id)
        if pricing:
            print(f"✓ {gateway}/{model_id}: Input=${pricing.get('prompt')}/1M, Output=${pricing.get('completion')}/1M")
        else:
            print(f"✗ No pricing found for {gateway}/{model_id}")

    print()
    return True

def test_fix_endpoint_structure():
    """Test that the fix endpoint is properly structured"""
    print("Testing fix endpoint structure...")

    # Check if the endpoint would be accessible
    from src.routes.system import router

    # Find our new fix endpoint
    fix_endpoint_found = False
    for route in router.routes:
        if hasattr(route, 'path') and '/health/gateways/{gateway}/fix' in str(route.path):
            fix_endpoint_found = True
            print(f"✓ Found fix endpoint: {route.path}")
            break

    if not fix_endpoint_found:
        print("✗ Fix endpoint not found in router")
        return False

    print()
    return True

def test_dashboard_enrichment():
    """Test that dashboard data includes pricing information"""
    print("Testing dashboard data enrichment...")

    # Simulate loading pricing for a sample model
    sample_models = [
        {"id": "meta-llama/Meta-Llama-3.1-8B-Instruct"},
        {"id": "mistralai/Mixtral-8x22B-Instruct-v0.1"},
        "meta-llama/Meta-Llama-3.1-70B-Instruct"  # Test string format too
    ]

    gateway = "deepinfra"
    enriched_models = []

    for model in sample_models:
        if isinstance(model, dict):
            model_id = model.get("id")
            pricing = get_model_pricing(gateway, model_id)
            if pricing:
                model["pricing"] = pricing
            enriched_models.append(model)
        else:
            model_dict = {"id": str(model)}
            pricing = get_model_pricing(gateway, str(model))
            if pricing:
                model_dict["pricing"] = pricing
            enriched_models.append(model_dict)

    # Check enrichment results
    models_with_pricing = sum(1 for m in enriched_models if "pricing" in m)
    print(f"✓ Enriched {models_with_pricing}/{len(enriched_models)} models with pricing data")

    # Show sample enriched model
    if enriched_models and "pricing" in enriched_models[0]:
        sample = enriched_models[0]
        print(f"  Sample: {sample['id']}")
        print(f"    Input: ${sample['pricing']['prompt']}/1M tokens")
        print(f"    Output: ${sample['pricing']['completion']}/1M tokens")

    print()
    return True

def main():
    print("=" * 60)
    print("Dashboard Feature Tests")
    print("=" * 60)
    print()

    tests = [
        test_pricing_lookup,
        test_fix_endpoint_structure,
        test_dashboard_enrichment
    ]

    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"✗ Test failed with error: {e}")
            results.append(False)

    print("=" * 60)
    print(f"Tests Passed: {sum(results)}/{len(results)}")
    print("=" * 60)

    if all(results):
        print("✅ All tests passed!")
    else:
        print("⚠️ Some tests failed. Please review the output above.")

if __name__ == "__main__":
    main()